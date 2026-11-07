"""Failure, lease and time boundaries for cleanup-only; all storage is synthetic."""

import json
import logging
import sys

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, cloud
from datumoj.rikolto.core.lease import LEASE_KEY, LeaseError, LeaseManager
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from tests.core.test_state_s3 import state_store as state_store  # noqa: PLC0414 -- Offline moto fixture.
from tests.test_cloud_cleanup_only import archive as archive  # noqa: PLC0414 -- Fixture dependency.
from tests.test_cloud_cleanup_only import cleanup_world as cleanup_world  # noqa: PLC0414 -- Synthetic fixture.
from tests.test_cloud_cleanup_only import invoke_cleanup, objects


def test_slow_restore_still_reserves_full_cleanup_window(cleanup_world, monkeypatch):
    original_restore = StateManager.restore
    original_cleanup = StateManager.cleanup
    monotonic = cloud.time.monotonic
    delay = [0]
    windows = []

    def restore(manager, *args, **kwargs):
        result = original_restore(manager, *args, **kwargs)
        delay[0] += 7200  # Far longer than the configured 1 minute; no real wait.
        return result

    def cleanup(manager, specs, **kwargs):
        windows.append(kwargs["deadline"] - cloud.time.monotonic())
        return original_cleanup(manager, specs, **kwargs)

    monkeypatch.setattr(cloud.time, "monotonic", lambda: monotonic() + delay[0])
    monkeypatch.setattr(StateManager, "restore", restore)
    monkeypatch.setattr(StateManager, "cleanup", cleanup)
    result = invoke_cleanup(cleanup_world)
    assert windows[0] >= 600 and result["cleanup"]["timed_out"] is False
    assert result["cleanup"]["reclaimable_raw"] == 1 and result["exit_status"] == 0


@pytest.mark.parametrize("failure", ["delete_error", "lease_lost", "lease_stolen"])
def test_deletion_stops_at_first_failure_or_lease_loss(cleanup_world, monkeypatch, failure):
    world = cleanup_world
    original_delete = world.store.delete_if_match
    original_check = LeaseManager.check
    calls = []

    def delete(key, etag):
        calls.append(key)
        if failure == "delete_error":
            raise OSError("synthetic private exception payload")
        original_delete(key, etag)
        if failure == "lease_stolen":
            old = json.loads(world.store.get(LEASE_KEY)[0])
            world.store.put(LEASE_KEY, json.dumps({**old, "holder": "actions/new-writer", "token": "new-token"}).encode())

    def check(manager, lease):
        if calls and failure == "lease_lost":
            raise LeaseError("synthetic private lease payload")
        original_check(manager, lease)

    monkeypatch.setattr(world.store, "delete_if_match", delete)
    monkeypatch.setattr(LeaseManager, "check", check)
    result = invoke_cleanup(world, "interrupted", "--delete")
    assert len(calls) == 1 and calls[0].startswith("generations/")
    assert world.store.exists(raw_key := f"raw/{world.old.sha256[:2]}/{world.old.sha256}")
    assert raw_key not in calls
    assert result["cleanup"]["failures"] == 1 and result["exit_status"] == 1
    assert result["reason"] == ("lease_release_failed" if failure == "lease_stolen" else "cleanup_failed")
    if failure == "lease_stolen":
        assert json.loads(world.store.get(LEASE_KEY)[0])["holder"] == "actions/new-writer"


def test_cleanup_timeout_is_a_failed_command(cleanup_world, monkeypatch):
    cleanup = StateManager.cleanup

    def timed_out(manager, specs, **kwargs):
        report = cleanup(manager, specs, **kwargs)
        return {**report, "timed_out": True}

    monkeypatch.setattr(StateManager, "cleanup", timed_out)
    result = invoke_cleanup(cleanup_world)
    assert result["exit_status"] == 1 and result["status"] == "failed"
    assert result["reason"] == "cleanup_timed_out" and result["phase"] == "cleanup"
    assert cleanup_world.deletes == []


@pytest.mark.parametrize("phase", ["configuration", "restore", "initialization", "cleanup", "lease_release"])
def test_exception_output_and_logs_are_sanitized(cleanup_world, monkeypatch, caplog, capfd, phase):
    private = "synthetic-secret-value-and-private-exception"
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", private)

    def fail(*args, **kwargs):
        print(private)
        print(private, file=sys.stderr)
        logging.getLogger("synthetic-sdk").error(private)
        raise RuntimeError(private)

    target, name = {
        "configuration": (cloud.wiring, "build_state"), "restore": (StateManager, "restore"),
        "initialization": (cloud, "load_registry"), "cleanup": (StateManager, "cleanup"),
        "lease_release": (LeaseManager, "release"),
    }[phase]
    monkeypatch.setattr(target, name, fail)
    response = CliRunner().invoke(cli.app, ["cloud", "cleanup", "--max-minutes", "1"])
    result = json.loads(response.output)
    captured = capfd.readouterr()
    assert response.exit_code == result["exit_status"] == 1
    assert result["reason"] == f"{phase}_failed" and result["phase"] == phase
    public = response.output + captured.out + captured.err + caplog.text
    assert private not in public and "RuntimeError" not in public and "Traceback" not in public
    assert str(cleanup_world.root) not in public
    assert cleanup_world.deletes == []


@pytest.mark.parametrize("broken", ["put_if_absent", "put_if_match"])
def test_condition_ignoring_backend_is_refused_without_deletion(cleanup_world, monkeypatch, broken):
    world = cleanup_world
    before = objects(world.store)

    def unconditional(key, data, *args):
        world.store.put(key, data)
        return world.store.get(key)[1]

    monkeypatch.setattr(world.store, broken, unconditional)
    result = invoke_cleanup(world, "unsupported", "--delete")
    assert result["exit_status"] == 1 and result["phase"] == "storage_probe"
    assert result["reason"] == "storage_conditional_unsupported"
    assert objects(world.store) == before and world.deletes == []
    assert LeaseManager(world.store).status() == {"held": False}


@pytest.mark.parametrize("budget", [-1, float("nan"), float("inf"), "private", True, None])
def test_invalid_budget_fails_before_remote_access(cleanup_world, budget):
    result = cloud.cleanup_only(max_minutes=budget)
    assert result["exit_status"] == 1 and result["reason"] == "configuration_failed"
    assert cleanup_world.writes == cleanup_world.deletes == []


@pytest.mark.parametrize("kind", ["nonempty", "file", "symlink"])
def test_unsafe_work_directory_is_refused_before_remote_access(cleanup_world, kind):
    root = cleanup_world.root / "unsafe"
    if kind == "file":
        root.write_text("synthetic existing state")
    elif kind == "symlink":
        root.symlink_to(cleanup_world.archive.manager.data_dir, target_is_directory=True)
    else:
        root.mkdir()
        (root / "existing").touch()
    result = cloud.cleanup_only(work_dir=root, max_minutes=1)
    assert result["reason"] == "work_directory_failed" and result["exit_status"] == 1
    assert cleanup_world.writes == cleanup_world.deletes == []


def test_first_unpublished_generation_is_also_an_orphan(tmp_path, monkeypatch):
    store = FakeStateObjectStore()
    store.put("generations/1.json", b"unpublished synthetic orphan")
    monkeypatch.setattr(cloud.wiring, "build_state", lambda root: StateManager(store, root))
    result = cloud.cleanup_only(work_dir=tmp_path / "work", max_minutes=1, delete=True)
    assert result["reason"] == "orphan_generation" and result["generation"] == 0
    assert store.list("") == ["generations/1.json", LEASE_KEY]
    assert store.get("current.json") is None


def test_temporary_directory_cleanup_error_is_sanitized(cleanup_world, monkeypatch, caplog):
    cleanup = cloud.tempfile.TemporaryDirectory.cleanup

    def fail(directory):
        cleanup(directory)
        raise OSError("synthetic private temporary-directory path")

    monkeypatch.setattr(cloud.tempfile.TemporaryDirectory, "cleanup", fail)
    result = cloud.cleanup_only(max_minutes=1)
    assert result["exit_status"] == 1
    assert result["reason"] == "work_directory_cleanup_failed" and result["phase"] == "work_directory_cleanup"
    assert "synthetic private" not in json.dumps(result) + caplog.text
    assert cleanup_world.deletes == []


@pytest.mark.parametrize("delete", [False, True])
def test_s3_cleanup_writes_only_lease_and_dry_run_sends_no_delete(cleanup_world, state_store, monkeypatch, delete):
    world = cleanup_world
    for key, (data, _) in objects(world.store).items():
        state_store.put(key, data)
    current = state_store.get("current.json")
    writes, deletes = [], []
    client = state_store._client
    put = client.put_object
    delete_one, delete_many = client.delete_object, client.delete_objects

    def tracked_put(**kwargs):
        writes.append(kwargs["Key"])
        return put(**kwargs)

    def tracked_delete(**kwargs):
        deletes.append(kwargs["Key"])
        return delete_one(**kwargs)

    def tracked_delete_many(**kwargs):
        deletes.extend(obj["Key"] for obj in kwargs["Delete"]["Objects"])
        return delete_many(**kwargs)

    monkeypatch.setattr(client, "put_object", tracked_put)
    monkeypatch.setattr(client, "delete_object", tracked_delete)
    monkeypatch.setattr(client, "delete_objects", tracked_delete_many)
    monkeypatch.setattr(cloud.wiring, "build_state", lambda root: StateManager(state_store, root))
    result = cloud.cleanup_only(work_dir=world.root / "s3-cleanup", max_minutes=0, delete=delete)
    assert result["exit_status"] == 0 and result["cleanup"]["raw_deleted"] == int(delete)
    assert writes and all(key == state_store._key(LEASE_KEY) for key in writes)
    assert bool(deletes) == delete and state_store.get("current.json") == current
    assert not state_store.exists(f"generations/{world.generation + 1}.json")
