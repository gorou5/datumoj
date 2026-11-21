"""Stop-tier recovery: mark due raw and publish only the manifest, then let cleanup reclaim it."""

import hashlib
import json
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.capacity import MARK_ONLY_WRITES
from datumoj.rikolto.core import state as state_module
from datumoj.rikolto.core.contracts import PreconditionFailed, StateObject
from datumoj.rikolto.core.lease import LEASE_KEY
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.core.state_s3 import S3StateObjectStore
from datumoj.rikolto.testing.fakes import FakeRegistry
from tests.test_cloud_run import BODY, invoke, released, remote
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Offline moto fixture.

SHA = hashlib.sha256(BODY).hexdigest()
RAW = f"raw/{SHA[:2]}/{SHA}"
RAW_SYNTHETIC_BYTES = 700_000_000  # Deleting this raw lowers the synthetic total below 8.5 GB.
HOUR = timedelta(hours=1)


def pad_inventory(monkeypatch, total):
    """Account the first body as 700 MB and pad the bucket once to ``total`` bytes."""
    original = cloud._usage_objects
    padding = []

    def inventory(store):
        objects, other = original(store)
        objects = [replace(obj, size_bytes=obj.size_bytes + RAW_SYNTHETIC_BYTES) if obj.key == RAW else obj
                   for obj in objects]
        if not padding:
            padding.append(total - sum(obj.size_bytes for obj in [*objects, *other]))
        return objects, [*other, StateObject("outside/synthetic", padding[0], '"synthetic"')]

    monkeypatch.setattr(cloud, "_usage_objects", inventory)


def track_writes(monkeypatch):
    puts, deletes = [], []
    put, delete_if_match, delete_objects = (S3StateObjectStore._put, S3StateObjectStore.delete_if_match,
                                            S3StateObjectStore.delete_objects)

    def tracked_put(store, key, data, **conditions):
        puts.append(key)
        return put(store, key, data, **conditions)

    def tracked_delete(store, key, etag):
        deletes.append(key)
        return delete_if_match(store, key, etag)

    def tracked_delete_many(store, keys):
        deletes.extend(keys)
        return delete_objects(store, keys)

    monkeypatch.setattr(S3StateObjectStore, "_put", tracked_put)
    monkeypatch.setattr(S3StateObjectStore, "delete_if_match", tracked_delete)
    monkeypatch.setattr(S3StateObjectStore, "delete_objects", tracked_delete_many)
    return puts, deletes


def forbid_work(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("a stopped bucket must not crawl, embed, report or publish a full state")

    for name in ("_source", "_recover", "_backfill", "maintain_vectors", "publish_previous_week_report"):
        monkeypatch.setattr(cloud, name, forbidden)
    monkeypatch.setattr(StateManager, "push", forbidden)


@pytest.fixture
def unmarked(world, monkeypatch):
    """Two legacy publications without raw markers; the first shared body is then superseded.

    The daily source always serves its own body, so only the two hourly sources share
    (and later replace) the first body, which becomes unprotected and expirable.
    """
    suffix = {"hourly": b"", "daily": b"\n\ndaily"}
    fetcher = world.overrides["fetcher"]
    fetch = fetcher.fetch

    def changed(url, *args, **kwargs):
        result = fetch(url, *args, **kwargs)
        if not result.content:
            return result
        extra = suffix["daily" if url.endswith("/source1.txt") else "hourly"]
        return result.model_copy(update={"content": result.content + extra})

    monkeypatch.setattr(fetcher, "fetch", changed)
    monkeypatch.setenv("R2_RAW_CLEANUP", "on")
    with monkeypatch.context() as legacy:
        legacy.setattr(StateManager, "mark_raw", lambda self, specs, now: 0)
        assert invoke(world, "first")[0]["generation"] == 1
        suffix["hourly"] = b"\n\nchanged"
        world.clock[0] += 61 * timedelta(minutes=1)  # Hourly sources are due; the daily one is not.
        second, _ = invoke(world, "second")
        assert second["generation"] == 2 and second["new"] == 2, {k: second[k] for k in ("status", "reason")}
    assert remote(world).exists(RAW) and world.managers[-1].list()[0].generation == 2
    # The stop path loads the registry itself; give it the test sources.
    monkeypatch.setattr(cloud, "load_registry", lambda path: FakeRegistry(world.specs))
    # Stopped rounds run a day later; the first body was fetched about 24 hours before that.
    world.clock[0] = datetime.now(UTC) + 24 * HOUR
    return suffix


def stores_except_manifest(generation):
    return [(copy.name, copy.key) for copy in generation.stores if not copy.name.startswith("manifest.sqlite")]


def test_mark_only_then_cleanup_then_normal_publication(world, unmarked, monkeypatch):
    store = remote(world)
    second = world.managers[-1].list()[0]
    assert second.generation == 2
    pad_inventory(monkeypatch, 9_100_000_000)
    puts, deletes = track_writes(monkeypatch)
    components = len(world.components)
    raw_before = set(store.list("raw/"))
    world.clock[0] += HOUR
    marked_at = world.clock[0]

    with monkeypatch.context() as stopped:
        forbid_work(stopped)
        first, _ = invoke(world, "mark")
    capacity = first["capacity"]
    assert first["status"] == "skipped" and first["reason"] == "capacity_stop" and first["exit_status"] == 0
    # Synthetic padding is fixed; real objects (lease, descriptors) add a few hundred bytes.
    assert capacity["tier"] == "stop" and 0 <= capacity["current_bytes"] - 9_100_000_000 < 100_000
    mark_only = capacity["mark_only"]
    assert mark_only["marked"] == 1 and mark_only["published_generation"] == 3
    assert set(mark_only) == {"marked", "published_generation"}
    writes = capacity["mark_only_writes"]
    assert writes["writes"] == list(MARK_ONLY_WRITES)
    assert 0 < writes["uploaded_bytes"] <= writes["estimated_bytes"]
    assert set(writes) == {"writes", "estimated_bytes", "uploaded_bytes"}
    assert first["generation"] is None and first["cleanup"]["failures"] == 0
    assert len(world.components) == components  # No pipeline was built: nothing crawled or written.
    # Only the lease, the new manifest blocks, the descriptor and the pointer were written.
    third = world.managers[-1].list()[0]
    assert third.generation == 3 and third.previous_generation == 2
    manifest_keys = {copy.key for copy in third.stores if copy.name.startswith("manifest.sqlite")}
    assert LEASE_KEY in puts and "generations/3.json" in puts and "current.json" in puts
    assert set(puts) <= {LEASE_KEY, "current.json", "generations/3.json"} | manifest_keys
    assert not [key for key in deletes if key.startswith("raw/")] and set(store.list("raw/")) == raw_before
    assert all(key.startswith(("generations/", "copies/sha256/")) for key in deletes)
    # Every database, index and vector copy is reused byte for byte; only the manifest changed.
    assert stores_except_manifest(third) == stores_except_manifest(second)
    assert ({copy.key for copy in third.stores if copy.name == "manifest.sqlite"}
            != {copy.key for copy in second.stores if copy.name == "manifest.sqlite"})
    assert third.vector_coverage == second.vector_coverage
    assert third.last_successful_runs == second.last_successful_runs
    restored = world.managers[-1].restore(world.tmp / "verify", raw="remote")
    assert restored["verified"] and restored["generation"] == 3
    with closing(SqliteManifest(world.tmp / "verify" / "manifest.sqlite")) as manifest:
        assert manifest.raw_markers()[SHA] == marked_at
    released(world)

    # Inside the protection period nothing is deleted and no further generation is written.
    puts.clear()
    deletes.clear()
    world.clock[0] += 10 * HOUR
    young, _ = invoke(world, "young")
    assert young["cleanup"]["raw_deleted"] == 0 and young["capacity"]["mark_only"]["marked"] == 0
    assert store.exists(RAW) and set(puts) == {LEASE_KEY} and store.get("generations/4.json") is None

    # After the protection period the same stopped round first deletes the marked raw.
    puts.clear()
    deletes.clear()
    world.clock[0] += 15 * HOUR
    with monkeypatch.context() as stopped:
        forbid_work(stopped)
        reclaimed, _ = invoke(world, "reclaim")
    assert reclaimed["capacity"]["tier"] == "stop" and reclaimed["capacity"]["current_bytes"] > 9_000_000_000 - 500_000_000
    assert reclaimed["cleanup"]["raw_deleted"] == 1 and reclaimed["cleanup"]["raw_deleted_by_age"] == 1
    assert not store.exists(RAW)
    assert reclaimed["capacity"]["mark_only"]["marked"] == 0
    assert reclaimed["capacity"]["mark_only"]["published_generation"] is None
    assert set(puts) == {LEASE_KEY} and store.get("generations/4.json") is None
    assert json.loads(store.get("current.json")[0])["generation"] == 3
    assert RAW in deletes and all(key.startswith(("generations/", "copies/sha256/", "raw/")) for key in deletes)
    released(world)

    # The synthetic total is now 8.4 GB: freeze tier, where a crawl and publication still run.
    puts.clear()
    unmarked.update(hourly=b"\n\nagain", daily=b"\n\ndaily-again")

    class Clock(datetime):  # A real round at a future synthetic time needs matching generation times.
        @classmethod
        def now(cls, tz=None):
            return world.clock[0]

    monkeypatch.setattr(state_module, "datetime", Clock)
    world.clock[0] += 2 * HOUR
    recovered, _ = invoke(world, "recover")
    assert recovered["status"] == "completed" and recovered["generation"] == 4 and recovered["new"] == 3
    assert recovered["capacity"]["tier"] == "freeze" and recovered["capacity"]["allowed"]
    assert recovered["capacity"]["skipped_steps"] == ["backfill", "weekly_report"]
    assert "mark_only" not in recovered["capacity"]
    assert len(world.components) == components + 1
    released(world)


def test_nothing_to_mark_publishes_nothing(world, monkeypatch):
    assert invoke(world, "first")[0]["generation"] == 1  # A normal round already marked everything.
    store = remote(world)
    before = store.get("current.json")
    pad_inventory(monkeypatch, 9_100_000_000)
    puts, _ = track_writes(monkeypatch)
    world.clock[0] += HOUR
    result, _ = invoke(world, "stopped")
    assert result["reason"] == "capacity_stop" and result["status"] == "skipped"
    assert result["capacity"]["mark_only"] == {"marked": 0, "published_generation": None}
    assert result["capacity"]["mark_only_writes"] == {"writes": [], "estimated_bytes": 0, "uploaded_bytes": 0}
    assert set(puts) == {LEASE_KEY} and store.get("current.json") == before
    assert store.get("generations/2.json") is None
    released(world)


@pytest.mark.parametrize("switch", ["env", "flag"])
def test_cleanup_switch_disables_mark_only(world, unmarked, monkeypatch, switch):
    pad_inventory(monkeypatch, 9_100_000_000)
    puts, _ = track_writes(monkeypatch)
    if switch == "env":
        monkeypatch.setenv("R2_CLEANUP", "off")
    world.clock[0] += HOUR
    result, _ = invoke(world, "off", *(["--no-cleanup"] if switch == "flag" else []))
    assert result["reason"] == "capacity_stop" and "mark_only" not in result["capacity"]
    assert set(puts) == {LEASE_KEY} and remote(world).get("generations/3.json") is None


def test_failed_cleanup_skips_mark_only(world, unmarked, monkeypatch):
    pad_inventory(monkeypatch, 9_100_000_000)
    cleanup = StateManager.cleanup
    monkeypatch.setattr(StateManager, "cleanup", lambda manager, *a, **kw: {**cleanup(manager, *a, **kw),
                                                                           "failures": 1})
    puts, _ = track_writes(monkeypatch)
    world.clock[0] += HOUR
    result, _ = invoke(world, "failed")
    assert result["reason"] == "capacity_stop_cleanup_failed" and "mark_only" not in result["capacity"]
    assert set(puts) == {LEASE_KEY}


def test_publication_failure_is_a_fixed_code_and_leaves_current_unchanged(world, unmarked, monkeypatch):
    pad_inventory(monkeypatch, 9_100_000_000)

    def broken(*args, **kwargs):
        raise OSError("synthetic private storage detail")

    monkeypatch.setattr(StateManager, "publish_marked_manifest", broken)
    world.clock[0] += HOUR
    result, _ = invoke(world, "broken")
    assert result["status"] == "failed" and result["exit_status"] == 1
    assert result["reason"] == "capacity_stop_mark_only_failed"
    assert result["capacity"]["mark_only"] == {"marked": 1, "published_generation": None}
    assert "synthetic private storage detail" not in json.dumps(result)
    assert json.loads(remote(world).get("current.json")[0])["generation"] == 2
    released(world)


def test_lost_pointer_cas_leaves_an_orphan_that_the_next_stopped_round_resumes(world, unmarked, monkeypatch):
    pad_inventory(monkeypatch, 9_100_000_000)
    publish = StateManager._publish

    def lost_cas(*args):
        raise PreconditionFailed("synthetic lost CAS")

    monkeypatch.setattr(StateManager, "_publish", lost_cas)
    world.clock[0] += HOUR
    failed, _ = invoke(world, "lost")
    assert failed["reason"] == "capacity_stop_mark_only_failed"
    store = remote(world)
    assert store.get("generations/3.json") is not None
    assert json.loads(store.get("current.json")[0])["generation"] == 2
    monkeypatch.setattr(StateManager, "_publish", publish)
    puts, _ = track_writes(monkeypatch)
    world.clock[0] += HOUR
    resumed, _ = invoke(world, "resumed")
    assert resumed["reason"] == "capacity_stop" and resumed["orphan_resumed"] is True
    assert json.loads(store.get("current.json")[0])["generation"] == 3
    assert resumed["capacity"]["mark_only"]["marked"] == 0
    assert set(puts) == {LEASE_KEY, "current.json"}
    released(world)
