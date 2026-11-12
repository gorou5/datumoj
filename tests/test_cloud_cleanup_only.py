"""Cleanup-only rounds against synthetic published state, without network access."""

import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, cloud
from datumoj.rikolto.core.lease import LEASE_KEY, LeaseManager
from datumoj.rikolto.core.state import RETENTION_WINDOW, StateManager
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.testing.samples import T0
from tests.regressions.test_audit_131 import DAY, publish, raw_key, record
from tests.regressions.test_audit_131 import archive as archive  # noqa: PLC0414 -- Shared synthetic fixture.


def objects(store):
    return {key: store.get(key) for key in store.list("") if key != LEASE_KEY}


@pytest.fixture
def cleanup_world(archive, monkeypatch):
    old = record(archive, "expired")
    young = record(archive, "young-mark")
    feed = record(archive, "protected", resource_type="feed")
    publish(archive, T0, monkeypatch)
    fresh = record(archive, "unexpired", when=T0 + 5 * DAY)
    now = T0 + 10 * DAY
    for snap, marked_at in ((old, T0 + 4 * DAY), (young, now - RETENTION_WINDOW + timedelta(hours=1)),
                            (fresh, T0 + 7 * DAY)):
        # A prior, shorter retention policy may have marked fresh before expiry.
        archive.deps.manifest.mark_raw_optional(snap.sha256, marked_at)
    generation, _ = publish(archive, T0 + 10 * DAY, monkeypatch)
    archive.store.put("copies/sha256/unreferenced", b"synthetic unused copy")
    monkeypatch.setenv("R2_CLEANUP", "on")
    monkeypatch.setenv("R2_RAW_CLEANUP", "on")
    monkeypatch.setattr(cloud, "utcnow", lambda: T0 + 10 * DAY)
    managers, writes, deletes = [], [], []

    def build_state(root):
        manager = StateManager(archive.store, root)
        managers.append(manager)
        return manager

    monkeypatch.setattr(cloud.wiring, "build_state", build_state)
    monkeypatch.setattr(cloud, "load_registry", lambda path: FakeRegistry(archive.specs))
    for name in ("put", "put_if_absent", "put_if_match"):
        original = getattr(archive.store, name)

        def tracked(key, *args, original=original):
            writes.append(key)
            return original(key, *args)

        monkeypatch.setattr(archive.store, name, tracked)
    original_delete = archive.store.delete_if_match

    def delete(key, etag):
        deletes.append(key)
        return original_delete(key, etag)

    monkeypatch.setattr(archive.store, "delete_if_match", delete)

    def forbidden(*args, **kwargs):
        pytest.fail("cleanup-only must never initialize the pipeline, process, mark or publish")

    for name in ("push", "mark_raw"):
        monkeypatch.setattr(StateManager, name, forbidden)
    monkeypatch.setattr(cloud.wiring, "build", forbidden)
    for name in ("_source", "_backfill", "maintain_vectors", "publish_previous_week_report"):
        monkeypatch.setattr(cloud, name, forbidden)
    return SimpleNamespace(archive=archive, old=old, young=young, fresh=fresh, feed=feed,
                           generation=generation.generation, writes=writes, deletes=deletes, managers=managers,
                           root=archive.root, store=archive.store)


def invoke_cleanup(world, name="cleanup", *options):
    response = CliRunner().invoke(cli.app, [
        "cloud", "cleanup", "--max-minutes", "1", "--work-dir", str(world.root / name), *options,
    ])
    result = json.loads(response.output)
    assert response.exit_code == result["exit_status"], response.output
    assert set(world.writes) <= {LEASE_KEY}
    return result


def test_default_dry_run_reports_exact_backlog_and_never_deletes(cleanup_world):
    world = cleanup_world
    before = objects(world.store)
    current = world.store.get("current.json")
    result = invoke_cleanup(world)
    report = result["cleanup"]
    assert result["status"] == "completed" and result["exit_status"] == 0
    assert result["reason"] is None and result["phase"] == "cleanup"
    assert result["mode"] == "dry_run" and result["generation"] == world.generation
    assert result["restore_timings"]["total"] >= 0
    assert report["enabled"] is False and report["raw_enabled"] is True
    assert report["reclaimable_raw"] == 1
    assert report["reclaimable_raw_bytes"] == len(before[raw_key(world.old)][0])
    retained = {copy.key for copy in world.archive.manager.list()[0].stores}
    unused = [value[0] for key, value in before.items() if key.startswith("copies/") and key not in retained]
    assert report["reclaimable_copies"] == len(unused)
    assert report["reclaimable_copies_bytes"] == sum(map(len, unused))
    assert report["reclaimable_generations"] == 1
    assert report["raw_marked"] == report["raw_deleted"] == report["copies_deleted"] == 0
    assert report["generations_deleted"] == 0 and world.deletes == []
    assert objects(world.store) == before and world.store.get("current.json") == current
    with (sqlite3.connect(world.root / "cleanup" / "manifest.sqlite") as restored,
          sqlite3.connect(world.archive.manager.data_dir / "manifest.sqlite") as original):
        assert restored.execute("SELECT * FROM raw_allowed_missing ORDER BY 1").fetchall() == original.execute(
            "SELECT * FROM raw_allowed_missing ORDER BY 1").fetchall()
    deleted = invoke_cleanup(world, "delete", "--delete")["cleanup"]
    for kind in ("generations", "copies", "raw"):
        assert report[f"reclaimable_{kind}"] == deleted[f"{kind}_deleted"]
    for kind in ("copies", "raw"):
        assert report[f"reclaimable_{kind}_bytes"] == deleted[f"{kind}_deleted_bytes"]


def test_delete_only_expired_and_mature_raw_and_keeps_current_restorable(cleanup_world):
    world = cleanup_world
    current = world.store.get("current.json")
    generation = world.store.get(f"generations/{world.generation}.json")
    result = invoke_cleanup(world, "delete", "--delete")
    assert result["mode"] == "delete" and result["exit_status"] == 0
    assert result["cleanup"]["raw_deleted"] == 1 and result["cleanup"]["raw_marked"] == 0
    assert not world.store.exists(raw_key(world.old))
    assert all(world.store.exists(raw_key(snap)) for snap in (world.young, world.fresh, world.feed))
    assert world.store.get("current.json") == current
    assert world.store.get(f"generations/{world.generation}.json") == generation
    assert world.store.list("generations/") == [f"generations/{world.generation}.json"]
    for mode in ("local", "remote"):
        report = world.archive.manager.restore(world.root / f"verify-{mode}", raw=mode)
        assert report["verified"] and report["generation"] == world.generation
    assert LeaseManager(world.store).status() == {"held": False}


@pytest.mark.parametrize("cleanup,raw,raw_deleted", [("off", "on", 0), ("on", "off", 0), ("on", "on", 1)])
def test_delete_respects_both_switches(cleanup_world, monkeypatch, cleanup, raw, raw_deleted):
    monkeypatch.setenv("R2_CLEANUP", cleanup)
    monkeypatch.setenv("R2_RAW_CLEANUP", raw)
    result = invoke_cleanup(cleanup_world, "switches", "--delete")
    assert result["exit_status"] == 0 and result["cleanup"]["raw_deleted"] == raw_deleted
    assert bool(result["cleanup"]["copies_deleted"]) == (cleanup == "on")
    if cleanup == "off":
        assert cleanup_world.deletes == []


@pytest.mark.parametrize("delete", [False, True])
def test_orphan_refused_without_restore_publish_or_deletion(cleanup_world, monkeypatch, delete):
    world = cleanup_world
    world.store.put(f"generations/{world.generation + 1}.json", b"unpublished orphan")
    world.writes.clear()
    before = objects(world.store)

    def forbidden(*args, **kwargs):
        pytest.fail("orphan must be refused before restore")

    monkeypatch.setattr(StateManager, "restore", forbidden)
    result = invoke_cleanup(world, "orphan", *(("--delete",) if delete else ()))
    assert result["status"] == "failed" and result["exit_status"] == 1
    assert result["reason"] == "orphan_generation" and result["generation"] == world.generation
    assert world.deletes == [] and objects(world.store) == before


def test_busy_lease_skips_without_probe_or_deletion(cleanup_world):
    world = cleanup_world
    leases = LeaseManager(world.store)
    lease = leases.acquire("actions/synthetic-other-writer")
    before = world.store.get(LEASE_KEY)
    world.writes.clear()
    result = invoke_cleanup(world, "busy", "--delete")
    assert result["status"] == "skipped" and result["exit_status"] == 0
    assert result["reason"] == "跳过：写入租约已占用" and result["phase"] == "lease_acquire"
    assert world.writes == world.deletes == [] and world.store.get(LEASE_KEY) == before
    leases.release(lease.holder, lease.token)


def test_cli_passes_default_and_delete_and_propagates_exit_status(monkeypatch):
    calls = []

    def cleanup(**kwargs):
        calls.append(kwargs)
        return {"exit_status": 1, "status": "failed", "reason": "cleanup_failed"}

    monkeypatch.setattr(cloud, "cleanup_only", cleanup)
    for options, delete in (([], False), (["--delete"], True)):
        response = CliRunner().invoke(cli.app, ["cloud", "cleanup", "--max-minutes", "2.5", *options])
        assert response.exit_code == 1 and json.loads(response.output)["reason"] == "cleanup_failed"
        assert calls[-1] == {"work_dir": None, "max_minutes": 2.5, "delete": delete}
