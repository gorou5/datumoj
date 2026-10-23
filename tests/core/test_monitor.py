from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from datumoj.core.contracts import ParseRecord, ParseStatus
from datumoj.core.models import FetchOutcome
from datumoj.core.monitor import evaluate_alerts
from datumoj.core.schedule import Scheduler, set_enabled
from datumoj.testing.fakes import FakeManifest
from datumoj.testing.samples import T0, make_snapshot, make_spec


def _run(source_id: str, at, *, success: bool) -> dict:
    return {
        "source_id": source_id,
        "started_at": at.isoformat(),
        "ended_at": (at + timedelta(minutes=1)).isoformat(),
        "status": "completed" if success else "failed",
        "exit_status": 0 if success else 1,
    }


def _manifest_with_failures() -> FakeManifest:
    manifest = FakeManifest()
    manifest.record_snapshot(make_snapshot(
        "jp-test", "https://example.go.jp/network", outcome=FetchOutcome.network_error,
        fetched_at=T0 - timedelta(minutes=2),
    ))
    manifest.record_snapshot(make_snapshot(
        "jp-test", "https://example.go.jp/rate", outcome=FetchOutcome.http_error,
        fetched_at=T0 - timedelta(minutes=1), http_status=429, error="HTTP 429",
    ))
    return manifest


def test_consecutive_failures_triggers_at_exact_limit_and_classifies_recent_failures():
    manifest = _manifest_with_failures()
    runs = [_run("jp-test", T0 - timedelta(hours=3 - i), success=False) for i in range(3)]

    alerts = evaluate_alerts(make_spec("jp-test"), runs, manifest, T0, enabled=False)

    alert, = alerts
    assert alert.rule == "consecutive_failures" and alert.severity == "error"
    assert alert.since == T0 - timedelta(hours=3)
    recent = alert.details["recent_failure_summary"]
    assert recent["counts_by_kind"] == {"rate_limited": 1, "source_unavailable": 1}
    assert recent["retryability_by_kind"] == {
        "rate_limited": {"retryable": 1, "not_retryable": 0},
        "source_unavailable": {"retryable": 1, "not_retryable": 0},
    }


@pytest.mark.parametrize("successes", [[False, False], [False, True, False]])
def test_consecutive_failures_does_not_trigger_with_too_few_or_a_success(successes):
    runs = [_run("jp-test", T0 - timedelta(hours=len(successes) - i), success=value)
            for i, value in enumerate(successes)]
    assert evaluate_alerts(make_spec("jp-test"), runs, None, T0, enabled=False) == []


def _add_parse_result(manifest: FakeManifest, index: int, fetched_at, status: ParseStatus) -> None:
    snapshot = make_snapshot(
        "jp-test", f"https://example.go.jp/{index}", data=f"body {index}".encode(), fetched_at=fetched_at,
    )
    manifest.record_snapshot(snapshot)
    manifest.record_parse(ParseRecord(
        sha256=snapshot.sha256, parser="fake", version="1", status=status,
        error="parse failed" if status is ParseStatus.failed else None,
    ))


def test_parse_failure_rate_compares_recent_day_with_prior_week():
    manifest = FakeManifest()
    for index in range(20):
        status = ParseStatus.failed if index < 2 else ParseStatus.ok
        _add_parse_result(manifest, index, T0 - timedelta(days=3, minutes=index), status)
    for index in range(20, 30):
        status = ParseStatus.failed if index < 28 else ParseStatus.ok
        _add_parse_result(manifest, index, T0 - timedelta(hours=12, minutes=index), status)

    alerts = evaluate_alerts(make_spec("jp-test"), [], manifest, T0, enabled=False)

    alert, = alerts
    assert alert.rule == "parse_failure_rate" and alert.severity == "warning"
    assert alert.details["recent_snapshots"] == 10
    assert alert.details["recent_rate"] == 0.8
    assert alert.details["baseline_rate"] == 0.1
    assert alert.details["increase"] == pytest.approx(0.7)


def test_parse_failure_rate_ignores_fewer_than_minimum_recent_snapshots():
    manifest = FakeManifest()
    for index in range(20):
        _add_parse_result(
            manifest, index, T0 - timedelta(days=3, minutes=index),
            ParseStatus.failed if index < 2 else ParseStatus.ok,
        )
    for index in range(20, 29):
        _add_parse_result(
            manifest, index, T0 - timedelta(hours=12, minutes=index),
            ParseStatus.failed if index < 28 else ParseStatus.ok,
        )

    assert evaluate_alerts(make_spec("jp-test"), [], manifest, T0, enabled=False) == []


def test_overdue_requires_enabled_source_and_uses_first_failed_run_without_success():
    spec = make_spec("jp-test", schedule="hourly")
    first = _run("jp-test", T0 - timedelta(hours=3), success=False)

    disabled = evaluate_alerts(spec, [first], None, T0, enabled=False)
    enabled = evaluate_alerts(spec, [first], None, T0, enabled=True)

    assert disabled == []
    overdue, = enabled
    assert overdue.rule == "overdue" and overdue.severity == "warning"
    assert overdue.since == T0 - timedelta(hours=3)


@pytest.mark.parametrize(
    "schedule,elapsed",
    [("hourly", timedelta(hours=2)), ("daily", timedelta(hours=48))],
)
def test_overdue_does_not_trigger_at_exactly_two_cycles(schedule, elapsed):
    run = _run("jp-test", T0 - elapsed - timedelta(minutes=1), success=True)
    run["ended_at"] = (T0 - elapsed).isoformat()
    assert evaluate_alerts(
        make_spec("jp-test", schedule=schedule), [run], None, T0, enabled=True,
    ) == []


def test_short_retention_gap_reports_endpoints_and_exact_six_hours_is_not_a_gap():
    spec = make_spec("jp-test", scope={"short_retention": True})
    old = _run("jp-test", T0 - timedelta(hours=7, minutes=1), success=True)
    old["ended_at"] = (T0 - timedelta(hours=7)).isoformat()
    alerts = evaluate_alerts(spec, [old], None, T0, enabled=True)
    gap, = alerts
    assert gap.rule == "retention_gap" and gap.severity == "error"
    assert gap.details["gap_start"] == (T0 - timedelta(hours=7)).isoformat()
    assert gap.details["gap_end"] == T0.isoformat()
    assert T0.isoformat() in gap.message

    exact = _run("jp-test", T0 - timedelta(hours=6, minutes=1), success=True)
    exact["ended_at"] = (T0 - timedelta(hours=6)).isoformat()
    assert evaluate_alerts(spec, [exact], None, T0, enabled=True) == []


def test_short_retention_scope_flag_detects_gap_between_successes():
    spec = make_spec("jp-test", scope={"short_retention": True})
    first = _run("jp-test", T0 - timedelta(hours=20, minutes=1), success=True)
    first["ended_at"] = (T0 - timedelta(hours=20)).isoformat()
    second = _run("jp-test", T0 - timedelta(hours=10, minutes=1), success=True)
    second["ended_at"] = (T0 - timedelta(hours=10)).isoformat()

    alerts = evaluate_alerts(spec, [first, second], None, T0, enabled=True)

    assert len(alerts) == 2
    assert all(alert.rule == "retention_gap" for alert in alerts)
    assert alerts[0].details["gap_start"] == (T0 - timedelta(hours=20)).isoformat()
    assert alerts[0].details["gap_end"] == (T0 - timedelta(hours=10)).isoformat()
    assert alerts[1].details["gap_start"] == (T0 - timedelta(hours=10)).isoformat()
    assert alerts[1].details["gap_end"] == T0.isoformat()


def test_retention_note_alone_does_not_mark_short_retention():
    spec = make_spec("jp-test", retention_note="约 7 天滑动窗口")
    run = _run("jp-test", T0 - timedelta(hours=7), success=True)
    run["ended_at"] = (T0 - timedelta(hours=7)).isoformat()
    assert evaluate_alerts(spec, [run], None, T0, enabled=True) == []


def test_disabled_short_retention_source_reports_history_gap_but_not_tail_gap():
    spec = make_spec("jp-test", scope={"short_retention": True})
    first = _run("jp-test", T0 - timedelta(hours=20, minutes=1), success=True)
    first["ended_at"] = (T0 - timedelta(hours=20)).isoformat()
    second = _run("jp-test", T0 - timedelta(hours=10, minutes=1), success=True)
    second["ended_at"] = (T0 - timedelta(hours=10)).isoformat()

    alerts = evaluate_alerts(spec, [first, second], None, T0, enabled=False)

    gap, = alerts
    assert gap.rule == "retention_gap"
    assert gap.details["gap_start"] == (T0 - timedelta(hours=20)).isoformat()
    assert gap.details["gap_end"] == (T0 - timedelta(hours=10)).isoformat()


def test_scheduler_status_serializes_source_alerts_and_total_count(tmp_path):
    spec = make_spec("jp-test", schedule="manual")
    scheduler = Scheduler(
        tmp_path, [spec],
        lambda _: SimpleNamespace(new=0, unchanged=0, failures={"http_error": 1}, stop_reason=None),
        now=lambda: T0, manifest=FakeManifest(),
    )
    set_enabled(tmp_path, True)
    set_enabled(tmp_path, True, [spec.id])
    for _ in range(3):
        scheduler.run_now(spec.id)

    status = scheduler.status()

    assert status["alert_count"] == 1
    assert status["sources"][0]["alerts"][0]["rule"] == "consecutive_failures"
    json.dumps(status)
