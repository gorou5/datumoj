"""Bounded cleanup without content downloads; only synthetic objects and clocks."""

import io
import json
import sqlite3
import sys
from contextlib import closing, contextmanager
from datetime import timedelta

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.core import state as state_module
from datumoj.rikolto.core.contracts import NO_PARSER, ParseRecord, ParseStatus
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import FetchOutcome, PublishState, parse_context_fingerprint
from datumoj.rikolto.core.state import RETENTION_WINDOW, StateManager
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec
from tests.regressions.test_audit_131 import DAY, cleanup, publish, raw_key, record
from tests.regressions.test_audit_131 import archive as archive  # noqa: PLC0414 -- Shared offline fixture.
from tests.test_cloud_run import invoke, released
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.


class CountingStore(FakeStateObjectStore):
    def __init__(self):
        super().__init__()
        self.gets = []
        self.inventories = 0

    def get(self, key):
        self.gets.append(key)
        return super().get(key)

    def list_objects(self, prefix):
        self.inventories += 1
        return super().list_objects(prefix)


@pytest.mark.parametrize("count", [72, 144])
def test_cleanup_downloads_no_content_with_many_generations(tmp_path, monkeypatch, count):
    root = tmp_path / "data"
    root.mkdir()
    store = CountingStore()
    spec = make_spec(scope={"raw_retention_days": 2, "raw_expire_latest_types": ["entry"]})
    old = make_snapshot(snapshot_id="old", fetched_at=T0 - 10 * DAY)
    needed = make_snapshot(snapshot_id="needed", data=b"synthetic protected feed")
    with closing(SqliteManifest(root / "manifest.sqlite")) as manifest:
        for snap, kind in ((old, "entry"), (needed, "feed")):
            manifest.record_snapshot(snap, {"resource_type": kind})
            manifest.record_publish_state(snap.snapshot_id, PublishState.published)
            path = root / raw_key(snap)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic protected feed" if snap == needed else b"hello")
        manifest.mark_raw_optional(old.sha256, T0 - 4 * DAY)
    # Use the sample's actual bytes, without relying on a fixture's implicit body.
    from datumoj.rikolto.testing.samples import sha

    assert sha((root / raw_key(old)).read_bytes()) == old.sha256
    manager = StateManager(store, root)
    base = manager.push()
    # Descriptors share real, verified stores. All retained publications postdate the mark.
    for number in range(1, count + 1):
        generation = base.model_copy(update={
            "generation": number, "previous_generation": number - 1 or None,
            "created_at": T0 - RETENTION_WINDOW * (count - number) / count,
        })
        store.put(f"generations/{number}.json", generation.model_dump_json().encode())
    store.put("current.json", json.dumps({"generation": count}).encode())
    store.put("copies/sha256/orphan", b"orphan")
    store.gets.clear()
    store.inventories = 0

    def no_restore(*args, **kwargs):
        pytest.fail("cleanup must not materialize any generation")

    with monkeypatch.context() as patch:
        patch.setattr(manager, "_materialize", no_restore)
        report = manager.cleanup([spec], now=T0, check=lambda: None, raw_enabled=True)
    assert report["failures"] == 0 and report["raw_deleted"] == report["copies_deleted"] == 1
    assert [key for key in store.gets if key.startswith(("copies/", "raw/"))] == []
    assert store.inventories == 1  # Content reads and inventory calls stay constant when history doubles.
    assert len(store.gets) == count + 2  # Small descriptors, current pointer and the fixed receipt key only.
    assert not store.exists(raw_key(old)) and store.exists(raw_key(needed))
    for number in range(1, count + 1):
        for mode in ("remote", "local"):
            target = tmp_path / f"restore-{number}-{mode}"
            assert manager.restore(target, number, raw=mode)["verified"]
            if mode == "local":
                assert (target / raw_key(needed)).read_bytes() == b"synthetic protected feed"


def legacy_retention(manager, specs):
    """The previous per-snapshot algorithm, kept as an independent result oracle."""
    policies = {spec.id: spec for spec in specs}
    path = manager.data_dir / "manifest.sqlite"
    with closing(SqliteManifest(path)) as manifest, closing(sqlite3.connect(path)) as db:
        source_ids = [row[0] for row in db.execute("SELECT DISTINCT source_id FROM snapshots")]
        no_parser = set(db.execute("SELECT sha256,context_fingerprint FROM parsed WHERE status='no_parser'"))
        fetched, retention, protected = {}, {}, set()
        for source_id in source_ids:
            spec = policies.get(source_id)
            scope = spec.scope if spec else {}
            days = 2  # Uniform 48-hour lifetime, including unknown-source policies.
            snapshots = manifest.snapshots_for(source_id)
            latest = {snap.resource_url: snap.snapshot_id for snap in snapshots if snap.outcome is FetchOutcome.ok}
            for snap in snapshots:
                if snap.sha256 is None:
                    continue
                sha = snap.sha256
                fetched[sha] = max(fetched.get(sha, snap.fetched_at), snap.fetched_at)
                retention[sha] = max(retention.get(sha, days), days)
                meta = manifest.snapshot_meta(snap.snapshot_id)
                state = manifest.publish_state(snap.snapshot_id)
                pending = (snap.outcome is FetchOutcome.ok and state is not PublishState.published
                           and (sha, parse_context_fingerprint(snap, meta)) not in no_parser)
                if (state in {PublishState.staged, PublishState.failed} or pending
                        or (latest.get(snap.resource_url) == snap.snapshot_id
                            and meta.get("resource_type") not in scope.get("raw_expire_latest_types", []))):
                    protected.add(sha)
        return {sha: when + timedelta(days=retention[sha]) for sha, when in fetched.items() if sha not in protected}


def test_retention_matches_legacy_and_uses_two_queries(archive, monkeypatch):
    world = archive
    world.specs += [make_spec("jp-other"), make_spec("jp-expire", scope={"raw_expire_latest_types": ["entry"]})]
    shared = record(world, "shared")
    for i in range(10):
        record(world, f"old-{i}", url="https://www.data.jma.go.jp/repeated", parsed=False)
        world.deps.manifest.record_publish_state(f"old-{i}", PublishState.published)
    record(world, "latest", when=T0 + DAY, url="https://www.data.jma.go.jp/repeated")
    record(world, "other", source_id="jp-other", when=T0 - DAY, data=world.deps.raw.get(shared.sha256))
    record(world, "expiring-latest", source_id="jp-expire")
    record(world, "unknown-policy", source_id="jp-unknown")
    staged = record(world, "staged")
    failed = record(world, "failed")
    world.deps.manifest.record_publish_state(staged.snapshot_id, PublishState.staged)
    world.deps.manifest.record_publish_state(failed.snapshot_id, PublishState.failed)
    pending = record(world, "pending", parsed=False)
    no_parser = record(world, "no-parser", parsed=False)
    meta = world.deps.manifest.snapshot_meta(no_parser.snapshot_id)
    world.deps.manifest.record_parse(ParseRecord(
        sha256=no_parser.sha256, parser=NO_PARSER, version="0", status=ParseStatus.no_parser,
        context_fingerprint=parse_context_fingerprint(no_parser, meta),
    ))
    context_mismatch = record(world, "different-context", data=world.deps.raw.get(no_parser.sha256),
                              resource_type="different", parsed=False)
    terminal = record(world, "terminal-no-parser", parsed=False)
    world.deps.manifest.record_parse(ParseRecord(
        sha256=terminal.sha256, parser=NO_PARSER, version="0", status=ParseStatus.no_parser,
        context_fingerprint=parse_context_fingerprint(terminal, world.deps.manifest.snapshot_meta(terminal.snapshot_id)),
    ))
    expected = legacy_retention(world.manager, world.specs)
    assert terminal.sha256 in expected
    assert all(snap.sha256 not in expected for snap in (pending, staged, failed, shared, context_mismatch))
    queries = []
    original = state_module._ro

    @contextmanager
    def traced(path):
        with original(path) as db:
            db.set_trace_callback(queries.append)
            yield db

    monkeypatch.setattr(state_module, "_ro", traced)
    assert world.manager.raw_retention(world.specs, T0 + 40 * DAY) == expected
    assert len(queries) == 2


def test_rollback_removes_marker_and_remarking_restarts_protection(archive, monkeypatch):
    snap = record(archive, "entry")
    original = archive.manager.push()  # Simulate an unmarked legacy generation.
    publish(archive, T0 + 7 * DAY - RETENTION_WINDOW, monkeypatch)
    # Restore the unmarked manifest exactly as a rollback would, then publish forward.
    target = archive.root / "rollback"
    archive.manager.restore(target, original.generation, raw="remote")
    with closing(sqlite3.connect(target / "manifest.sqlite")) as source:
        source.backup(archive.deps.manifest._db)
    assert not archive.deps.manifest.raw_markers()
    assert cleanup(archive, T0 + 10 * DAY, raw_enabled=True)["raw_deleted"] == 0
    _, marked = publish(archive, T0 + 10 * DAY, monkeypatch)
    assert marked == 1
    assert cleanup(archive, T0 + 10 * DAY + RETENTION_WINDOW - timedelta(microseconds=1), raw_enabled=True)["raw_deleted"] == 0
    publish(archive, T0 + 10 * DAY + RETENTION_WINDOW, monkeypatch)
    assert cleanup(archive, T0 + 10 * DAY + RETENTION_WINDOW, raw_enabled=True)["raw_deleted"] == 1
    assert not archive.store.exists(raw_key(snap))


def test_aged_marker_still_waits_for_retention_expiry(archive, monkeypatch):
    snap = record(archive, "entry")
    archive.deps.manifest.mark_raw_optional(snap.sha256, T0)
    publish(archive, T0, monkeypatch)
    publish(archive, T0 + DAY, monkeypatch)
    assert cleanup(archive, T0 + DAY, raw_enabled=True)["raw_deleted"] == 0
    assert archive.store.exists(raw_key(snap))
    publish(archive, T0 + 2 * DAY, monkeypatch)
    assert cleanup(archive, T0 + 2 * DAY, raw_enabled=True)["raw_deleted"] == 1


@pytest.mark.parametrize("phase", ["planning", "descriptors", "generations", "copies", "raw", "lease"])
def test_timeout_stops_safely_and_next_round_continues(archive, monkeypatch, phase):
    snap = record(archive, "entry")
    second = record(archive, "second")
    publish(archive, T0, monkeypatch)
    publish(archive, T0 + 7 * DAY - RETENTION_WINDOW, monkeypatch)
    publish(archive, T0 + 10 * DAY, monkeypatch)
    for name in ("orphan-a", "orphan-b"):
        archive.store.put(f"copies/sha256/{name}", b"orphan")
    timer = [0.0]
    delete = archive.store.delete_if_match
    calls = []

    def slow_delete(key, etag):
        calls.append(key)
        delete(key, etag)
        if key.startswith(phase + "/"):
            timer[0] = 600

    def check():
        if phase == "lease" and calls == []:
            # The first check starts planning; the second precedes the first delete.
            checks.append(1)
            if len(checks) == 2:
                timer[0] = 600

    checks = []
    monkeypatch.setattr(archive.store, "delete_if_match", slow_delete)
    inventory = archive.store.list_objects

    def slow_inventory(prefix):
        result = inventory(prefix)
        if phase == "planning":
            timer[0] = 600
        return result

    monkeypatch.setattr(archive.store, "list_objects", slow_inventory)
    get = archive.store.get

    def slow_get(key):
        result = get(key)
        if phase == "descriptors" and key.startswith("generations/"):
            timer[0] = 600
        return result

    monkeypatch.setattr(archive.store, "get", slow_get)
    report = archive.manager.cleanup(archive.specs, now=T0 + 10 * DAY, check=check,
                                     raw_enabled=True, clock=lambda: timer[0])
    assert report["timed_out"] is True
    assert report["failures"] == 0 and report["elapsed_s"] == 600
    if phase in {"planning", "descriptors", "lease"}:
        assert calls == []
    elif phase != "raw":
        assert len(calls) >= 1 and not any(key.startswith("raw/") for key in calls)
    monkeypatch.setattr(archive.store, "list_objects", inventory)
    monkeypatch.setattr(archive.store, "get", get)
    monkeypatch.setattr(archive.store, "delete_if_match", delete)
    final = cleanup(archive, T0 + 10 * DAY, raw_enabled=True)
    assert final["failures"] == 0 and final["timed_out"] is False
    assert not archive.store.exists(raw_key(snap)) and not archive.store.exists(raw_key(second))
    for generation in archive.manager.list():
        for mode in ("remote", "local"):
            assert archive.manager.restore(archive.root / f"after-{generation.generation}-{mode}",
                                           generation.generation, raw=mode)["verified"]


def test_insufficient_job_time_skips_all_planning(archive, monkeypatch):
    monkeypatch.setattr(archive.store, "get", lambda *_: pytest.fail("must skip before GET"))
    monkeypatch.setattr(archive.store, "list_objects", lambda *_: pytest.fail("must skip before LIST"))
    report = archive.manager.cleanup(archive.specs, now=T0, check=lambda: pytest.fail("must skip before lease"),
                                     clock=lambda: 100, deadline=699.999)
    assert report["timed_out"] is True and report["elapsed_s"] == report["failures"] == 0


def test_cloud_timeout_preserves_publication_and_progress_whitelist(world, monkeypatch):
    timer = [0.0]
    original = StateManager.cleanup
    stream = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", stream)

    def cleanup(manager, specs, **kw):
        # The job has used its full configured window by the end of publication.
        assert kw["deadline"] == 3600
        return original(manager, specs, **{**kw, "clock": lambda: 3600})

    monkeypatch.setattr(cloud.time, "monotonic", lambda: timer[0])
    monkeypatch.setattr(StateManager, "cleanup", cleanup)
    result, _ = invoke(world)
    assert result["generation"] == 1 and result["status"] == "completed" and result["exit_status"] == 0
    assert result["cleanup"]["timed_out"] is True and result["cleanup"]["failures"] == 0
    assert set(result["cleanup"]) == {
        "enabled", "raw_enabled", "generations_deleted", "copies_deleted", "copies_deleted_bytes", "raw_marked",
        "raw_deleted", "raw_deleted_bytes", "reclaimable_generations", "reclaimable_copies", "reclaimable_copies_bytes",
        "reclaimable_raw", "reclaimable_raw_bytes", "total_bytes", "over_7gb", "failures", "elapsed_s", "timed_out",
        "receipt_status", "raw_deleted_by_receipt", "raw_deleted_by_receipt_bytes",
        "raw_deleted_by_age", "raw_deleted_by_age_bytes",
    }
    events = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [row["event"] for row in events][-3:] == ["publish_end", "cleanup_end", "round_end"]
    assert set(events[-2]) == {"event", "source_id", "elapsed_s", "new", "unchanged", "parsed", "failures",
                               "stop_reason", "publish_timings", "restore_timings"}
    released(world)
