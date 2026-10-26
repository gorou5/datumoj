"""Offline monitoring of generation metadata stored by datumoj pull."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

from typer.testing import CliRunner

from datumoj import cli
from datumoj.admin.web import _page
from datumoj.core.monitor import attach_cloud_status, evaluate_cloud_alerts
from datumoj.testing.fakes import FakeManifest, FakeRegistry
from datumoj.testing.samples import T0, make_spec


def _success(run_id: str, finished_at) -> dict[str, str]:
    return {"run_id": run_id, "finished_at": finished_at.isoformat()}


def test_cloud_alerts_use_one_source_cycle_and_two_cycles_for_generation_stall():
    hourly = make_spec("jp-hourly", schedule="hourly")
    daily = make_spec("jp-daily", schedule="daily")
    runs = {hourly.id: _success("hourly-ok", T0 - timedelta(minutes=30))}

    on_time = evaluate_cloud_alerts(
        [hourly, daily], {hourly.id}, runs, T0 - timedelta(hours=1), T0,
    )
    assert on_time == []

    missed_runs = {hourly.id: _success("hourly-ok", T0 - timedelta(hours=1, minutes=1))}
    missed_cycle = evaluate_cloud_alerts(
        [hourly, daily], {hourly.id}, missed_runs, T0 - timedelta(hours=1), T0,
    )
    assert [alert.rule for alert in missed_cycle] == ["cloud_run_missing"]
    assert missed_cycle[0].source_id == hourly.id
    assert missed_cycle[0].since == T0 - timedelta(hours=1, minutes=1)

    stalled = evaluate_cloud_alerts(
        [hourly, daily], {hourly.id}, missed_runs, T0 - timedelta(hours=3), T0,
        generation=12,
    )
    assert {alert.rule for alert in stalled} == {"cloud_run_missing", "cloud_stalled"}
    generation_alert = next(alert for alert in stalled if alert.rule == "cloud_stalled")
    assert generation_alert.details["generation"] == 12

    daily_stall = evaluate_cloud_alerts(
        [daily], {daily.id}, {}, T0 - timedelta(hours=3), T0,
    )
    assert [alert.rule for alert in daily_stall] == ["cloud_stalled"]
    no_sources_stall = evaluate_cloud_alerts(
        [daily], set(), {}, T0 - timedelta(hours=3), T0,
    )
    assert [alert.rule for alert in no_sources_stall] == ["cloud_stalled"]


def test_weekly_cloud_alert_starts_only_after_seven_days_without_success():
    weekly = make_spec("jp-weekly", schedule="weekly")
    created_at = T0 - timedelta(hours=1)
    on_time = evaluate_cloud_alerts(
        [weekly], {weekly.id}, {weekly.id: _success("weekly-ok", T0 - timedelta(days=7))},
        created_at, T0,
    )
    assert on_time == []

    overdue = evaluate_cloud_alerts(
        [weekly], {weekly.id}, {weekly.id: _success("weekly-ok", T0 - timedelta(days=7, seconds=1))},
        created_at, T0,
    )
    assert [alert.rule for alert in overdue] == ["cloud_run_missing"]
    assert overdue[0].details["expected_after_seconds"] == 7 * 24 * 60 * 60


def test_existing_cloud_schedule_intervals_are_unchanged():
    for schedule, interval in (
        ("hourly", timedelta(hours=1)),
        ("daily", timedelta(days=1)),
        ("monthly", timedelta(days=30)),
    ):
        spec = make_spec(f"jp-{schedule}", schedule=schedule)
        created_at = T0 - timedelta(minutes=30)
        on_time = evaluate_cloud_alerts(
            [spec], {spec.id}, {spec.id: _success("ok", T0 - interval)}, created_at, T0,
        )
        assert on_time == []
        overdue = evaluate_cloud_alerts(
            [spec], {spec.id}, {spec.id: _success("ok", T0 - interval - timedelta(seconds=1))},
            created_at, T0,
        )
        assert [alert.rule for alert in overdue] == ["cloud_run_missing"]


def test_pulled_status_includes_generation_writer_successes_and_alerts(tmp_path):
    spec = make_spec("jp-test", schedule="hourly")
    created_at = T0 - timedelta(hours=3)
    (tmp_path / "state-pull.json").write_text(json.dumps({
        "generation": 7,
        "created_at": created_at.isoformat(),
        "pulled_at": (T0 - timedelta(minutes=10)).isoformat(),
        "writer": {"kind": "nas"},
        "last_successful_runs": {spec.id: _success("run-6", T0 - timedelta(hours=2))},
    }), encoding="utf-8")
    status = {
        "enabled": True,
        "sources": [{
            "source_id": spec.id, "effective_enabled": True, "alerts": [],
        }],
    }

    result = attach_cloud_status(status, tmp_path, [spec], [], T0)

    assert result["cloud"] == {
        "generation": 7,
        "created_at": created_at.isoformat(),
        "pulled_at": (T0 - timedelta(minutes=10)).isoformat(),
        "status_basis": "以上次 pull 时的远端状态为准",
        "writer": {"kind": "nas"},
        "last_successful_runs": {spec.id: {"finished_at": (T0 - timedelta(hours=2)).isoformat()}},
    }
    assert result["sources"][0]["last_successful_run"] == {
        "finished_at": (T0 - timedelta(hours=2)).isoformat(),
    }
    assert [row["rule"] for row in result["sources"][0]["alerts"]] == ["cloud_run_missing"]
    assert [row["rule"] for row in result["cloud_alerts"]] == ["cloud_stalled"]


def test_failed_source_without_prior_success_uses_its_first_ledger_attempt(tmp_path):
    spec = make_spec("jp-never-succeeded", schedule="hourly")
    created_at = T0 - timedelta(minutes=10)
    first_attempt = T0 - timedelta(hours=2)
    (tmp_path / "state-pull.json").write_text(json.dumps({
        "generation": 8,
        "created_at": created_at.isoformat(),
        "pulled_at": (T0 - timedelta(minutes=10)).isoformat(),
        "writer": {"kind": "nas"},
        "last_successful_runs": {},
    }), encoding="utf-8")
    run = {
        "source_id": spec.id, "run_id": "failed-run", "started_at": first_attempt.isoformat(),
        "ended_at": (first_attempt + timedelta(minutes=1)).isoformat(),
        "state": "final", "status": "failed", "exit_status": 1,
    }
    status = {
        "enabled": True,
        "sources": [{"source_id": spec.id, "effective_enabled": True, "alerts": []}],
    }

    result = attach_cloud_status(status, tmp_path, [spec], [run], T0)

    alert = next(row for row in result["sources"][0]["alerts"] if row["rule"] == "cloud_run_missing")
    assert alert["since"] == first_attempt.isoformat()
    assert alert["details"]["last_success_at"] is None
    assert alert["details"]["first_attempt_at"] == first_attempt.isoformat()


def test_local_status_is_unchanged_without_pull_marker(tmp_path):
    spec = make_spec("jp-local", schedule="hourly")
    status = {
        "enabled": False,
        "sources": [{"source_id": spec.id, "effective_enabled": False, "alerts": []}],
        "alert_count": 0,
    }
    assert attach_cloud_status(status, tmp_path, [spec], [], T0) == status
    assert "cloud" not in status
    scheduler = SimpleNamespace(
        data_dir=tmp_path, sources={spec.id: spec}, now=lambda: T0,
        status=lambda: json.loads(json.dumps(status)), _read_history=lambda: ([], []),
    )
    deps = SimpleNamespace(
        data_dir=tmp_path, registry=FakeRegistry([spec]), manifest=FakeManifest(),
        chunks=None, raw=None, tools=None,
    )
    page = _page(deps, scheduler, "csrf-token")
    assert "最后成功运行" not in page and "无成功记录" not in page


def test_stale_replica_reports_only_replica_stale_for_cloud(tmp_path):
    spec = make_spec("jp-hourly", schedule="hourly")
    (tmp_path / "state-pull.json").write_text(json.dumps({
        "generation": 9,
        "created_at": (T0 - timedelta(minutes=10)).isoformat(),
        "pulled_at": (T0 - timedelta(hours=3)).isoformat(),
        "writer": {"kind": "nas"},
        "last_successful_runs": {spec.id: _success("run-8", T0 - timedelta(minutes=5))},
    }), encoding="utf-8")
    status = {
        "enabled": True,
        "sources": [{"source_id": spec.id, "effective_enabled": True, "alerts": []}],
    }

    result = attach_cloud_status(status, tmp_path, [spec], [], T0)

    assert [alert["rule"] for alert in result["cloud_alerts"]] == ["replica_stale"]


def test_schedule_status_and_admin_page_show_cloud_generation(tmp_path, monkeypatch):
    spec = make_spec("jp-test", schedule="hourly")
    created_at = T0 - timedelta(hours=3)
    (tmp_path / "state-pull.json").write_text(json.dumps({
        "generation": 9,
        "created_at": created_at.isoformat(),
        "pulled_at": (T0 - timedelta(minutes=5)).isoformat(),
        "writer": {"kind": "actions"},
        "last_successful_runs": {spec.id: _success("source-run-8", T0 - timedelta(minutes=30))},
    }), encoding="utf-8")
    base_status = {
        "enabled": True,
        "sources": [{
            "source_id": spec.id, "enabled": True, "effective_enabled": True,
            "schedule": "hourly", "last_run": None, "next_due": None,
            "running": False, "alerts": [],
        }],
        "alert_count": 0,
    }
    scheduler = SimpleNamespace(
        data_dir=tmp_path, sources={spec.id: spec}, now=lambda: T0,
        status=lambda: json.loads(json.dumps(base_status)),
        _read_history=lambda: ([], []),
    )
    monkeypatch.setattr(cli, "_scheduler", lambda: scheduler)

    output = CliRunner().invoke(cli.app, ["schedule", "status"])

    assert output.exit_code == 0, output.output
    assert '"generation": 9' in output.output
    assert '"kind": "actions"' in output.output
    assert '"rule": "cloud_stalled"' in output.output

    deps = SimpleNamespace(
        data_dir=tmp_path, registry=FakeRegistry([spec]), manifest=FakeManifest(),
        chunks=None, raw=None, tools=None,
    )
    page = _page(deps, scheduler, "csrf-token")
    assert "云端代次" in page and "Actions" in page
    assert "最后成功运行" in page and (T0 - timedelta(minutes=30)).isoformat() in page
    assert "cloud_stalled" in page
