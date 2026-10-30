"""Regression coverage for NAS writer identity and pull-based cloud monitoring."""

import json
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, cloud, wiring
from datumoj.rikolto.core.lease import LeaseError, LeaseManager
from datumoj.rikolto.core.models import StateWriter
from datumoj.rikolto.core.monitor import attach_cloud_status
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0, make_spec


def _monitor_status(specs):
    return {
        "enabled": True,
        "sources": [
            {"source_id": spec.id, "effective_enabled": True, "alerts": []}
            for spec in specs
        ],
        "alert_count": 0,
    }


def _attach(root, specs, created_at, pulled_at, successes, now=T0):
    marker = {
        "generation": 17,
        "created_at": created_at.isoformat(),
        "pulled_at": pulled_at.isoformat(),
        "writer": {"kind": "nas", "run_id": "synthetic-run"},
        "last_successful_runs": {
            source_id: {"run_id": f"run-{source_id}", "finished_at": finished.isoformat()}
            for source_id, finished in successes.items()
        },
    }
    (root / "state-pull.json").write_text(json.dumps(marker), encoding="utf-8")
    return attach_cloud_status(_monitor_status(specs), root, specs, [], now)


def test_writer_kind_override_fallback_and_validation(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "synthetic-actions")
    monkeypatch.delenv("DATUMOJ_WRITER_KIND", raising=False)
    assert wiring.state_writer_kind() == "actions"

    monkeypatch.setenv("DATUMOJ_WRITER_KIND", "nas")
    assert wiring.state_writer_kind() == "nas"
    monkeypatch.setenv("DATUMOJ_WRITER_KIND", "local")
    assert wiring.state_writer_kind() == "local"
    monkeypatch.setenv("DATUMOJ_WRITER_KIND", "not-a-writer")
    with pytest.raises(ValueError, match="DATUMOJ_WRITER_KIND"):
        wiring.state_writer_kind()

    monkeypatch.delenv("DATUMOJ_WRITER_KIND")
    monkeypatch.delenv("GITHUB_RUN_ID")
    assert wiring.state_writer_kind() == "local"
    assert StateWriter(kind="actions", run_id="old-actions").kind == "actions"
    assert StateWriter(kind="local", run_id="old-local").kind == "local"


def test_nas_lease_prefix_is_accepted_and_invalid_holder_rules_remain():
    manager = LeaseManager(FakeStateObjectStore(), now=lambda: T0)
    lease = manager.acquire("nas/synthetic-run")
    assert lease.holder == "nas/synthetic-run"
    for holder in ("nas/", "other/run", "nas/run\x01bad"):
        with pytest.raises(LeaseError):
            manager.acquire(holder)


def test_cloud_run_lease_uses_writer_kind(tmp_path, monkeypatch):
    acquired = []

    class Manager:
        writer = StateWriter(kind="nas", run_id="synthetic-run")
        store = object()

        @staticmethod
        def list():
            raise RuntimeError("stop after lease acquisition")

    class Leases:
        def __init__(self, _store):
            pass

        def acquire(self, holder, *, force=False):
            acquired.append((holder, force))
            return SimpleNamespace(holder=holder, token="synthetic-token")

        @contextmanager
        def maintain(self, _lease):
            yield lambda: None

        @staticmethod
        def release(_holder, _token):
            pass

    monkeypatch.setattr(cloud.wiring, "build_state", lambda _root: Manager())
    monkeypatch.setattr(cloud, "probe_conditional_writes", lambda _store: {"supported": True})
    monkeypatch.setattr(cloud, "LeaseManager", Leases)

    result = cloud.run(work_dir=tmp_path / "work")

    assert acquired and acquired[0][0].startswith("nas/")
    assert acquired[0][1] is True
    assert result["reason"] == "restore_failed"


def test_nas_generation_outage_source_gap_and_recovery(tmp_path):
    hourly = make_spec("jp-hourly", schedule="hourly")
    daily = make_spec("jp-daily", schedule="daily")
    pulled_at = T0 - timedelta(minutes=5)

    offline = _attach(
        tmp_path, [hourly], T0 - timedelta(hours=3), pulled_at,
        {hourly.id: T0 - timedelta(minutes=40)},
    )
    assert offline["cloud"]["writer"]["kind"] == "nas"
    assert [alert["rule"] for alert in offline["cloud_alerts"]] == ["cloud_stalled"]

    running = _attach(
        tmp_path, [hourly, daily], T0 - timedelta(minutes=5), pulled_at,
        {hourly.id: T0 - timedelta(hours=1, minutes=5), daily.id: T0 - timedelta(hours=23)},
    )
    assert [alert["rule"] for alert in running["sources"][0]["alerts"]] == ["cloud_run_missing"]
    assert running["sources"][0]["source_id"] == hourly.id
    assert running["sources"][1]["alerts"] == []
    assert running["cloud_alerts"] == []

    recovered = _attach(
        tmp_path, [hourly, daily], T0 - timedelta(minutes=1), T0 - timedelta(minutes=1),
        {hourly.id: T0 - timedelta(minutes=10), daily.id: T0 - timedelta(hours=23)},
    )
    assert recovered["cloud_alerts"] == []
    assert all(not row["alerts"] for row in recovered["sources"])


def test_old_local_pull_only_reports_replica_stale(tmp_path):
    hourly = make_spec("jp-hourly", schedule="hourly")
    result = _attach(
        tmp_path, [hourly], T0 - timedelta(minutes=10), T0 - timedelta(hours=3),
        {hourly.id: T0 - timedelta(minutes=5)},
    )
    assert [alert["rule"] for alert in result["cloud_alerts"]] == ["replica_stale"]
    assert result["cloud_alerts"][0]["severity"] == "warning"
    assert result["cloud"]["pulled_at"] == (T0 - timedelta(hours=3)).isoformat()
    assert result["cloud"]["status_basis"] == "以上次 pull 时的远端状态为准"


def test_pull_output_includes_cloud_fields_and_evaluates_remote_generation(tmp_path, monkeypatch):
    root = tmp_path / "replica"
    hourly = make_spec("jp-hourly", schedule="hourly")
    daily = make_spec("jp-daily", schedule="daily")
    pulled_at = T0
    created_at = T0 - timedelta(hours=3)
    last_success = T0 - timedelta(hours=2)

    def pull_state(data_dir, _generation):
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "state-pull.json").write_text(json.dumps({
            "generation": 17,
            "created_at": created_at.isoformat(),
            "pulled_at": pulled_at.isoformat(),
            "writer": {"kind": "nas", "run_id": "synthetic-writer"},
            "last_successful_runs": {
                hourly.id: {"run_id": "synthetic-source-run", "finished_at": last_success.isoformat()},
            },
        }), encoding="utf-8")
        return {"generation": 17, "created_at": created_at.isoformat(), "pulled_at": pulled_at.isoformat(),
                "verified": True}

    scheduler = SimpleNamespace(
        data_dir=root, sources={hourly.id: hourly, daily.id: daily}, now=lambda: pulled_at,
        status=lambda: _monitor_status([hourly, daily]), _read_history=lambda: ([], []),
    )
    monkeypatch.setattr(cli, "pull_state", pull_state)
    monkeypatch.setattr(cli, "_scheduler_at", lambda *_args, **_kwargs: (None, scheduler))

    response = CliRunner().invoke(cli.app, ["pull", "--data-dir", str(root)])

    assert response.exit_code == 0, response.output
    payload = json.loads(response.output)
    cloud_summary = payload["cloud"]
    assert cloud_summary["generation"] == 17
    assert cloud_summary["created_at"] == created_at.isoformat()
    assert cloud_summary["pulled_at"] == pulled_at.isoformat()
    assert cloud_summary["writer"] == {"kind": "nas"}
    assert cloud_summary["last_successful_runs"] == {
        hourly.id: {"finished_at": last_success.isoformat()}, daily.id: None,
    }
    assert {(alert["source_id"], alert["rule"]) for alert in cloud_summary["alerts"]} == {
        ("cloud", "cloud_stalled"), (hourly.id, "cloud_run_missing"),
    }
    assert all(set(alert) <= {"source_id", "rule", "since"} for alert in cloud_summary["alerts"])
