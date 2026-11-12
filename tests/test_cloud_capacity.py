"""Cloud gates against synthetic inventories; no large objects or real R2 access."""

import json
from datetime import timedelta

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.core.contracts import PreconditionFailed, StateObject
from datumoj.rikolto.core.lease import LEASE_KEY
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.core.state_s3 import S3StateObjectStore
from tests import test_cloud_project
from tests.regressions.test_audit_131 import archive as archive  # noqa: PLC0414 -- Fixture dependency.
from tests.test_cloud_cleanup_only import cleanup_world as cleanup_world  # noqa: PLC0414 -- Offline fixture.
from tests.test_cloud_cleanup_only import invoke_cleanup
from tests.test_cloud_embed import invoke_embed, seed
from tests.test_cloud_run import invoke, released, remote
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Offline moto fixture.

TOTALS = [6_000_000_000, 7_500_000_000, 8_100_000_000, 8_700_000_000, 9_100_000_000]
projection_world = test_cloud_project.world


def capacity_inventory(monkeypatch, total):
    original = cloud._usage_objects

    def inventory(store):
        objects, other = original(store)
        # Metadata-only unrelated data: exercise the same namespace + outside
        # accounting as usage, without allocating gigabytes of test payloads.
        padding = total - sum(obj.size_bytes for obj in [*objects, *other])
        return objects, [*other, StateObject("outside/synthetic", padding, '"synthetic"')]

    monkeypatch.setattr(cloud, "_usage_objects", inventory)


@pytest.mark.parametrize("total,tier", list(zip(TOTALS, ["normal", "report", "freeze", "stop", "stop"], strict=True)))
def test_run_allowance_table(world, monkeypatch, total, tier):
    capacity_inventory(monkeypatch, total)
    writes = []
    put = S3StateObjectStore._put

    def tracked(store, key, data, **conditions):
        writes.append(key)
        return put(store, key, data, **conditions)

    monkeypatch.setattr(S3StateObjectStore, "_put", tracked)
    if tier in {"freeze", "stop"}:
        def forbidden(*args, **kwargs):
            pytest.fail("optional work passed the capacity gate")

        monkeypatch.setattr(cloud, "_backfill", forbidden)
        monkeypatch.setattr(cloud, "publish_previous_week_report", forbidden)
        if tier == "stop":
            for name in ("_source", "_recover", "maintain_vectors"):
                monkeypatch.setattr(cloud, name, forbidden)
            monkeypatch.setattr(StateManager, "push", forbidden)
            monkeypatch.setattr(StateManager, "mark_raw", forbidden)
    result, _ = invoke(world)
    assert result["capacity"]["tier"] == tier
    assert result["capacity"]["current_bytes"] == total
    assert result["capacity"]["projected_bytes"] == total + 500_000_000
    if tier == "stop":
        # This empty bucket has no manifest to clean; its existing cleanup
        # failure must now be visible rather than reported as a successful skip.
        assert result["status"] == "failed" and result["reason"] == "capacity_stop_cleanup_failed"
        assert result["exit_status"] != 0 and result["cleanup"]["failures"]
        assert not result["capacity"]["allowed"] and result["generation"] is None
        assert set(writes) == {LEASE_KEY}  # No disposable probes, raw or generation writes.
        assert remote(world).get("current.json") is None and not world.components
        assert "crawl" in result["capacity"]["skipped_steps"]
    else:
        assert result["status"] == "completed" and result["generation"] == 1
        assert result["new"] == result["parsed"] == 3
        assert result["cleanup"]["enabled"] is True
        assert result["capacity"]["allowed"]
        if tier == "freeze":
            assert result["capacity"]["skipped_steps"] == ["backfill", "weekly_report"]
            assert result["weekly_report_key"] is None
        else:
            assert result["weekly_report_key"] is not None
    released(world)


@pytest.mark.parametrize("total,tier", list(zip(TOTALS, ["normal", "report", "report", "freeze", "stop"], strict=True)))
def test_embed_skips_before_probe_or_lease(world, monkeypatch, total, tier):
    seed(world)
    before = remote(world).get("current.json")
    tokens = len(world.tokens)
    capacity_inventory(monkeypatch, total)
    if tier in {"freeze", "stop"}:
        def forbidden(*args, **kwargs):
            pytest.fail("blocked embedding attempted a probe, lease or backfill")

        monkeypatch.setattr(cloud, "probe_conditional_writes", forbidden)
        monkeypatch.setattr(cloud.LeaseManager, "acquire", forbidden)
        monkeypatch.setattr(cloud, "_backfill", forbidden)
    result = invoke_embed(world)
    assert result["capacity"]["tier"] == tier
    if tier in {"freeze", "stop"}:
        assert result["status"] == "skipped" and result["generation"] is None
        assert len(world.tokens) == tokens and remote(world).get("current.json") == before
        assert result["capacity"]["skipped_steps"] == ["backfill", "publish"]
        assert not (world.tmp / "embed").exists()
    else:
        assert result["status"] == "completed" and result["generation"] == 2
        assert result["embedded"] == 6
    released(world)


@pytest.mark.parametrize("total,tier", list(zip(TOTALS, ["normal", "report", "report", "freeze", "stop"], strict=True)))
def test_project_allowance_table(projection_world, total, tier):
    world = projection_world
    actual = sum(obj.size_bytes for obj in world.store.list_objects())
    world.store.extra_bytes = total - actual
    result = cloud.project(work_dir=world.tmp / "gated", write=True)
    assert result["capacity"]["tier"] == tier
    if tier in {"freeze", "stop"}:
        assert result["reason"] == "storage_limit_exceeded" and not world.store.writes
        assert result["capacity"]["skipped_steps"] == ["project"]
    else:
        assert result["generation"] == 2 and result["added_chunks"] == 11


@pytest.mark.parametrize("total", TOTALS)
def test_cleanup_always_runs(cleanup_world, monkeypatch, total):
    objects = cleanup_world.store.list_objects

    def inventory(prefix=""):
        rows = objects(prefix)
        if prefix == "":
            rows.append(StateObject("other/synthetic-capacity", total - sum(obj.size_bytes for obj in rows),
                                    '"synthetic"'))
        return rows

    monkeypatch.setattr(cleanup_world.store, "list_objects", inventory)
    result = invoke_cleanup(cleanup_world, "reclaim", "--delete")
    assert result["status"] == "completed" and result["cleanup"]["raw_deleted"] == 1
    assert result["cleanup"]["over_7gb"] is (total > 7_000_000_000)


@pytest.mark.parametrize("mode", ["run", "embed"])
def test_historical_generation_plus_orphan_resumes_into_empty_root(world, monkeypatch, mode):
    # Exactly the rejected sequence: published #1, failed CAS leaves #2, retry
    # must publish #2 and then #3, even though root already contains restored #1.
    if mode == "embed":
        seed(world)
        call = lambda name: invoke_embed(world, name, "--max-embed", "1")
    else:
        first, _ = invoke(world, "first")
        assert first["generation"] == 1
        call = lambda name: invoke(world, name)[0]
    publish = StateManager._publish

    def lost_cas(*args):
        raise PreconditionFailed("synthetic lost CAS")

    monkeypatch.setattr(StateManager, "_publish", lost_cas)
    world.clock[0] += timedelta(hours=1)
    failed = call("orphan")
    assert failed["status"] == "failed"
    assert remote(world).get("generations/2.json") is not None
    assert json.loads(remote(world).get("current.json")[0])["generation"] == 1
    monkeypatch.setattr(StateManager, "_publish", publish)
    world.clock[0] += timedelta(hours=1)
    result = call("resumed")
    assert result["status"] == "completed" and result["generation"] == 3
    assert json.loads(remote(world).get("current.json")[0])["generation"] == 3
    if mode == "embed":
        assert result["embedded"] == 1 and result["vector_pending"] == 4
    released(world)


@pytest.mark.parametrize("mode", ["run", "embed"])
def test_unknown_usage_fails_closed(world, monkeypatch, mode):
    def unavailable(store):
        raise OSError("synthetic private capacity error")

    monkeypatch.setattr(cloud, "_usage_objects", unavailable)
    result = invoke_embed(world) if mode == "embed" else invoke(world)[0]
    assert result["status"] == "failed" and result["reason"] == "capacity_unknown"
    assert result["exit_status"] != 0
    assert result["capacity"]["reason"] == "capacity_unknown"
    assert result["generation"] is None and not world.components
    if mode == "embed":
        assert not world.tokens


@pytest.mark.parametrize("failure", ["estimate", "list"])
def test_project_unknown_capacity_never_writes(projection_world, monkeypatch, failure):
    def unavailable(*args):
        raise OSError("synthetic private estimate error")

    monkeypatch.setattr(cloud, "_projection_estimate" if failure == "estimate" else "_project_storage_bytes",
                        unavailable)
    result = cloud.project(work_dir=projection_world.tmp / "unknown", write=True)
    assert result["capacity"]["reason"] == "capacity_unknown"
    assert not result["capacity"]["allowed"] and not projection_world.store.writes


def test_cleanup_high_total_marks_next_round(world, monkeypatch):
    cleanup = StateManager.cleanup

    def high_total(manager, *args, **kwargs):
        return {**cleanup(manager, *args, **kwargs), "total_bytes": 9_000_000_000}

    monkeypatch.setattr(StateManager, "cleanup", high_total)
    result, _ = invoke(world)
    assert result["generation"] == 1
    assert result["capacity"]["next_round"] == "cleanup_only"
    assert result["weekly_report_key"] is None
    assert result["capacity"]["skipped_steps"] == ["weekly_report"]


def test_embedding_estimate_failure_skips_before_lease(world, monkeypatch):
    def unavailable(manager):
        raise OSError("synthetic private estimate error")

    monkeypatch.setattr(StateManager, "list", unavailable)
    result = invoke_embed(world)
    assert result["reason"] == "capacity_unknown" and not world.tokens
    assert result["capacity"]["estimated_new_bytes"] is None


def test_stop_after_slow_restore_has_full_cleanup_window(world, monkeypatch):
    first, _ = invoke(world, "first")
    assert first["generation"] == 1
    capacity_inventory(monkeypatch, 9_100_000_000)
    restore, cleanup = StateManager.restore, StateManager.cleanup
    monotonic = cloud.time.monotonic
    delay = [0]
    windows = []

    def slow_restore(manager, *args, **kwargs):
        restored = restore(manager, *args, **kwargs)
        delay[0] += 7200
        return restored

    def timed_cleanup(manager, *args, **kwargs):
        windows.append(kwargs["deadline"] - cloud.time.monotonic())
        return cleanup(manager, *args, **kwargs)

    monkeypatch.setattr(cloud.time, "monotonic", lambda: monotonic() + delay[0])
    monkeypatch.setattr(StateManager, "restore", slow_restore)
    monkeypatch.setattr(StateManager, "cleanup", timed_cleanup)
    result, _ = invoke(world, "stopped", "--max-minutes", "1")
    assert result["reason"] == "capacity_stop" and windows[0] >= 600
    assert not result["cleanup"]["timed_out"]


@pytest.mark.parametrize("no_cleanup", [False, True])
def test_stop_respects_cleanup_switch(world, monkeypatch, no_cleanup):
    assert invoke(world, "first")[0]["generation"] == 1
    capacity_inventory(monkeypatch, 9_100_000_000)
    result, _ = invoke(world, "stopped", *(["--no-cleanup"] if no_cleanup else []))
    assert result["reason"] == "capacity_stop"
    assert result["cleanup"]["enabled"] is (not no_cleanup)


def test_stop_resumes_existing_orphan(world, monkeypatch):
    publish = StateManager._publish
    monkeypatch.setattr(StateManager, "_publish", lambda *args: (_ for _ in ()).throw(PreconditionFailed()))
    invoke(world, "orphan")
    monkeypatch.setattr(StateManager, "_publish", publish)
    capacity_inventory(monkeypatch, 9_100_000_000)
    result, _ = invoke(world, "blocked")
    assert result["reason"] == "capacity_stop"
    assert result["orphan_resumed"] is True
    assert result["cleanup"]["failures"] == 0
    assert json.loads(remote(world).get("current.json")[0])["generation"] == 1
