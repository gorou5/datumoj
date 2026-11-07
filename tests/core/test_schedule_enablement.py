"""Durable enablement clocks, independent of runs and generation publication."""

import json
from datetime import timedelta

import pytest

from datumoj.rikolto.core.monitor import attach_cloud_status, evaluate_cloud_alerts
from datumoj.rikolto.core.schedule import Scheduler, read_control, set_enabled, write_control
from datumoj.rikolto.testing.samples import T0, make_spec


def test_switches_preserve_clock_until_effectively_disabled(tmp_path):
    source = "jp-clock"
    set_enabled(tmp_path, True, [source], now=T0)
    assert "enabled_since" not in read_control(tmp_path)["sources"][source]
    set_enabled(tmp_path, True, now=T0)
    set_enabled(tmp_path, True, [source], now=T0 + timedelta(hours=2))
    write_control(tmp_path, {"enabled": True, "sources": {source: {"enabled": True}}},
                  now=T0 + timedelta(hours=3))
    assert read_control(tmp_path)["sources"][source]["enabled_since"] == T0.isoformat()
    for targets in ([source], None):
        set_enabled(tmp_path, False, targets, now=T0 + timedelta(days=1))
        assert "enabled_since" not in read_control(tmp_path)["sources"][source]
        set_enabled(tmp_path, True, targets, now=T0 + timedelta(days=2))
        assert read_control(tmp_path)["sources"][source]["enabled_since"] == (T0 + timedelta(days=2)).isoformat()
    assert not (tmp_path / "runs.jsonl").exists()


def test_observe_legacy_control_once_without_inventing_runs(tmp_path):
    specs = [make_spec("jp-new", schedule="hourly"), make_spec("jp-old", schedule="hourly")]
    (tmp_path / "control.json").write_text(json.dumps({
        "enabled": True, "sources": {spec.id: {"enabled": True} for spec in specs},
    }))
    scheduler = Scheduler(tmp_path, specs, lambda _: None, now=lambda: T0)
    scheduler.append_run({"source_id": "jp-old", "started_at": (T0 - timedelta(days=3)).isoformat(),
                          "status": "failed", "exit_status": 1})
    ledger = (tmp_path / "runs.jsonl").read_bytes()
    scheduler.observe_enabled()
    state = read_control(tmp_path)["sources"]
    assert state["jp-new"]["enabled_since"] == T0.isoformat()
    assert state["jp-old"]["enabled_since"] == (T0 - timedelta(days=3)).isoformat()
    scheduler.now = lambda: T0 + timedelta(days=4)
    scheduler.observe_enabled()
    assert read_control(tmp_path)["sources"] == state
    assert (tmp_path / "runs.jsonl").read_bytes() == ledger


@pytest.mark.parametrize("stamp", ["not-a-date", "2026-10-20T00:00:00", 123, None])
def test_invalid_enablement_clock_is_rejected(tmp_path, stamp):
    (tmp_path / "control.json").write_text(json.dumps({
        "enabled": True, "sources": {"jp-clock": {"enabled": True, "enabled_since": stamp}},
    }))
    with pytest.raises(ValueError, match="enabled_since"):
        read_control(tmp_path)


def test_reenabled_source_gets_grace_period_despite_old_success_or_attempt():
    spec = make_spec("jp-clock", schedule="hourly")
    old = (T0 - timedelta(days=3)).isoformat()
    for successes in ({}, {spec.id: {"run_id": "old", "finished_at": old}}):
        assert evaluate_cloud_alerts(
            [spec], {spec.id}, successes, T0, T0 + timedelta(hours=1),
            first_attempts={spec.id: old}, enabled_since={spec.id: T0.isoformat()},
        ) == []
        [alert] = evaluate_cloud_alerts(
            [spec], {spec.id}, successes, T0 + timedelta(hours=1), T0 + timedelta(hours=2),
            first_attempts={spec.id: old}, enabled_since={spec.id: T0.isoformat()},
        )
        assert alert.since == T0
        assert alert.details["last_success_at"] == (old if successes else None)
        assert alert.details["enabled_since"] == T0.isoformat()


def test_readonly_replica_uses_enablement_clock_without_changing_control(tmp_path):
    spec = make_spec("jp-clock", schedule="hourly")
    write_control(tmp_path, {"enabled": True, "sources": {spec.id: {"enabled": True}}}, now=T0)
    control = (tmp_path / "control.json").read_bytes()
    current = T0 + timedelta(days=2)
    (tmp_path / "state-pull.json").write_text(json.dumps({
        "generation": 73, "created_at": current.isoformat(), "pulled_at": current.isoformat(),
        "writer": {"kind": "nas"}, "last_successful_runs": {},
    }))
    scheduler = Scheduler(tmp_path, [spec], lambda _: None, now=lambda: current)
    status = attach_cloud_status(scheduler.status(), tmp_path, [spec], [], current)
    [alert] = status["sources"][0]["alerts"]
    assert alert["rule"] == "cloud_run_missing" and alert["since"] == T0.isoformat()
    assert (tmp_path / "control.json").read_bytes() == control
    assert not (tmp_path / "runs.jsonl").exists()


@pytest.mark.parametrize("operation", ["source", "master", "other", "replace"])
def test_legacy_clock_is_not_reset_by_idempotent_or_unrelated_writes(tmp_path, operation):
    spec = make_spec("jp-legacy", schedule="hourly")
    control = {"enabled": True, "sources": {spec.id: {"enabled": True}}}
    (tmp_path / "control.json").write_text(json.dumps(control))
    scheduler = Scheduler(tmp_path, [spec], lambda _: None, now=lambda: T0)
    oldest = T0 - timedelta(days=3)
    scheduler.append_run({"source_id": spec.id, "started_at": oldest.isoformat(),
                          "status": "failed", "exit_status": 1})
    if operation == "replace":
        write_control(tmp_path, control, now=T0)
    else:
        targets = {"source": [spec.id], "master": None, "other": ["jp-other"]}[operation]
        set_enabled(tmp_path, True, targets, now=T0)
    scheduler.observe_enabled()
    assert read_control(tmp_path)["sources"][spec.id]["enabled_since"] == oldest.isoformat()
