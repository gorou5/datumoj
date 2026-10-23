"""Due selection shares scheduler history; remote contention is a clean skip."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from datumoj import cli
from datumoj.core.lease import LeaseManager
from datumoj.core.schedule import Scheduler, set_enabled
from datumoj.testing.fakes import FakeRegistry, FakeStateObjectStore
from datumoj.testing.samples import T0, make_spec


REMOTE_ENV = {
    "R2_ACCESS_KEY_ID": "synthetic-schedule-id",
    "R2_SECRET_ACCESS_KEY": "synthetic-schedule-secret",
    "R2_ENDPOINT": "https://synthetic-schedule.invalid",
    "DATUMOJ_S3_BUCKET": "synthetic-schedule-bucket",
}


@pytest.fixture(autouse=True)
def no_remote_environment(monkeypatch):
    for name in (*REMOTE_ENV, "DATUMOJ_S3_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)


def setup(tmp_path, schedules=("hourly", "daily", "monthly", "manual", None)):
    specs = [make_spec(f"jp-due{i}", schedule=schedule) for i, schedule in enumerate(schedules)]
    calls = []
    clock = [T0]

    def execute(spec):
        calls.append(spec.id)
        return SimpleNamespace(new=1, unchanged=0, failures={}, stop_reason=None)

    scheduler = Scheduler(tmp_path, specs, execute, now=lambda: clock[0])
    return scheduler, calls, clock


def enable(scheduler):
    set_enabled(scheduler.data_dir, True)
    set_enabled(scheduler.data_dir, True, scheduler.sources)


def test_due_switches_and_existing_frequency_rules(tmp_path):
    scheduler, calls, clock = setup(tmp_path)
    assert scheduler.due() == []
    set_enabled(tmp_path, True, scheduler.sources)
    assert scheduler.due() == []
    set_enabled(tmp_path, True)
    assert scheduler.due() == ["jp-due0", "jp-due1", "jp-due2"]
    scheduler.tick()
    assert calls == ["jp-due0", "jp-due1", "jp-due2"]
    assert scheduler.due() == []
    clock[0] += timedelta(hours=1)
    assert scheduler.due() == ["jp-due0"]
    clock[0] = T0 + timedelta(days=1)
    assert scheduler.due() == ["jp-due0", "jp-due1"]
    clock[0] = T0 + timedelta(days=30)
    set_enabled(tmp_path, False, ["jp-due1"])
    assert scheduler.due() == ["jp-due0", "jp-due2"]
    set_enabled(tmp_path, False)
    assert scheduler.due() == []


def test_due_uses_legacy_failed_interrupted_history_and_warnings(tmp_path):
    scheduler, _, clock = setup(tmp_path, ("hourly", "hourly", "hourly"))
    enable(scheduler)
    scheduler._append({"source_id": "jp-due0", "started_at": T0.isoformat(), "status": "failed", "exit_status": 1})
    scheduler._append({"source_id": "jp-due1", "started_at": T0.isoformat(), "status": "running", "exit_status": 0,
                       "run_id": "interrupted-run", "state": "running"})
    with (tmp_path / "runs.jsonl").open("ab") as stream:
        stream.write(b"broken\n{}\n\xff\n")
    assert scheduler.due() == ["jp-due2"]
    assert len(scheduler.status()["ledger_warnings"]) == 3
    clock[0] += timedelta(hours=1)
    assert scheduler.due() == list(scheduler.sources)


def test_due_cli_data_dir_json(tmp_path, monkeypatch):
    scheduler, calls, _ = setup(tmp_path)
    enable(scheduler)
    roots = []

    def factory(root, *, read_only=False):
        assert read_only
        roots.append(str(root))
        return None, scheduler

    monkeypatch.setattr(cli, "_scheduler_at", factory)
    runner = CliRunner()
    for args in [
        ["schedule", "due", "--data-dir", str(tmp_path), "--json"],
        ["schedule", "--data-dir", str(tmp_path), "due", "--json"],
        ["--data-dir", str(tmp_path), "schedule", "due", "--json"],
    ]:
        result = runner.invoke(cli.app, args)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == ["jp-due0", "jp-due1", "jp-due2"]
    assert roots == [str(tmp_path)] * 3 and calls == []


@pytest.mark.parametrize("manual", [False, True])
def test_remote_competitor_skips_with_ledger_and_no_due_delay(tmp_path, monkeypatch, caplog, manual):
    scheduler, calls, _ = setup(tmp_path, ("hourly",))
    enable(scheduler)
    leases = LeaseManager(FakeStateObjectStore(), now=scheduler.now)
    owner = leases.acquire("actions/other")
    for name, value in REMOTE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(scheduler, "_remote_lease", lambda: leases)
    monkeypatch.setattr(cli, "_scheduler", lambda: scheduler)
    result = CliRunner().invoke(cli.app, ["schedule", "run-now", "jp-due0"] if manual else ["schedule", "tick"])
    assert result.exit_code == 0, result.output
    row = json.loads(result.output)
    if not manual:
        row = row[0]
    assert row["status"] == "skipped" and row["reason_code"] == "write_lease_unavailable"
    assert not calls
    assert json.loads((tmp_path / "runs.jsonl").read_text()) == row
    assert scheduler.due() == ["jp-due0"]
    assert scheduler.status()["sources"][0]["next_due"] == T0.isoformat()
    leases.check(owner)
    output = result.output + (tmp_path / "runs.jsonl").read_text() + caplog.text
    assert all(secret not in output for secret in (*REMOTE_ENV.values(), owner.token))


def test_remote_local_owner_held_during_execution_and_released(tmp_path, monkeypatch):
    scheduler, _, _ = setup(tmp_path, ("hourly", "hourly"))
    enable(scheduler)
    leases = LeaseManager(FakeStateObjectStore(), now=scheduler.now)
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", REMOTE_ENV["DATUMOJ_S3_BUCKET"])
    monkeypatch.setattr(scheduler, "_remote_lease", lambda: leases)
    holders = []

    def execute(spec):
        lease = leases._read()[0]
        assert lease.holder.startswith("local/")
        leases.check(lease)
        holders.append(lease.token)
        return SimpleNamespace(new=1, unchanged=0, failures={}, stop_reason=None)

    scheduler.execute = execute
    assert all(row["exit_status"] == 0 for row in scheduler.tick())
    assert len(set(holders)) == 2 and leases.status() == {"held": False}


def test_remote_error_sanitized_and_release_even_on_execution_failure(tmp_path, monkeypatch, caplog):
    scheduler, _, _ = setup(tmp_path, ("hourly",))
    enable(scheduler)
    leases = LeaseManager(FakeStateObjectStore(), now=scheduler.now)
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", REMOTE_ENV["DATUMOJ_S3_BUCKET"])
    monkeypatch.setattr(scheduler, "_remote_lease", lambda: leases)

    def fail(spec):
        raise RuntimeError(" ".join(REMOTE_ENV.values()))

    scheduler.execute = fail
    result = scheduler.run_now("jp-due0")
    assert result["exit_status"] == 1 and leases.status() == {"held": False}
    output = json.dumps(result) + (tmp_path / "runs.jsonl").read_text() + caplog.text
    assert all(secret not in output for secret in REMOTE_ENV.values())


def test_remote_expiry_during_execution_retains_successor(tmp_path, monkeypatch):
    scheduler, _, clock = setup(tmp_path, ("hourly",))
    enable(scheduler)
    leases = LeaseManager(FakeStateObjectStore(), now=scheduler.now)
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", REMOTE_ENV["DATUMOJ_S3_BUCKET"])
    monkeypatch.setattr(scheduler, "_remote_lease", lambda: leases)

    def execute(spec):
        clock[0] += leases.ttl
        leases.acquire("actions/successor", force=True)
        return SimpleNamespace(new=1, unchanged=0, failures={}, stop_reason=None)

    scheduler.execute = execute
    result = scheduler.run_now("jp-due0")
    assert result["exit_status"] == 1
    assert leases._read()[0].holder == "actions/successor"


def test_partial_configuration_fails_closed_without_sdk_call(tmp_path, monkeypatch):
    scheduler, calls, _ = setup(tmp_path, ("hourly",))
    enable(scheduler)
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", REMOTE_ENV["DATUMOJ_S3_BUCKET"])
    # No endpoint/credentials: constructor rejects before any network request.
    row = scheduler.tick()[0]
    assert row["reason_code"] == "write_lease_unavailable" and not calls


def test_unconfigured_remote_never_constructed(tmp_path, monkeypatch):
    scheduler, calls, _ = setup(tmp_path, ("hourly",))
    enable(scheduler)
    monkeypatch.setattr(scheduler, "_remote_lease", lambda: pytest.fail("local scheduling must stay offline"))
    assert scheduler.tick()[0]["exit_status"] == 0 and calls == ["jp-due0"]


def test_cli_due_and_contended_tick_never_open_stores(tmp_path, monkeypatch):
    import datumoj.core.registry as registry_module

    scheduler, _, _ = setup(tmp_path, ("hourly",))
    enable(scheduler)
    registry = FakeRegistry(list(scheduler.sources.values()))
    leases = LeaseManager(FakeStateObjectStore(), now=scheduler.now)
    owner = leases.acquire("actions/other")
    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", REMOTE_ENV["DATUMOJ_S3_BUCKET"])
    monkeypatch.setattr(registry_module, "load_registry", lambda _: registry)
    monkeypatch.setattr(Scheduler, "_remote_lease", lambda _: leases)
    builds = []

    def build(root):
        assert leases.status()["active"] and leases._read()[0].holder.startswith("local/")
        builds.append(root)
        return object()

    monkeypatch.setattr(cli.wiring, "build", build)
    monkeypatch.setattr(cli, "_crawl_report", lambda *a: SimpleNamespace(new=1, unchanged=0, failures={}, stop_reason=None))
    runner = CliRunner()
    assert runner.invoke(cli.app, ["schedule", "due", "--json"]).exit_code == 0
    assert runner.invoke(cli.app, ["schedule", "tick"]).exit_code == 0
    assert builds == [] and not list(tmp_path.glob("*.sqlite"))
    leases.release(owner.holder, owner.token)
    result = runner.invoke(cli.app, ["schedule", "run-now", "jp-due0"])
    assert result.exit_code == 0, result.output
    assert len(builds) == 1 and leases.status() == {"held": False}
