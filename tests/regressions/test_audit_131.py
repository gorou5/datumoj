"""Leased retention across publications; synthetic data and clocks only."""

from contextlib import closing
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core import state as state_module
from datumoj.rikolto.core.contracts import CrawlContext, FetchResult, FetchTask
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import FetchOutcome, PublishState
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.crawlers.jma_feed import FEED_URL, JmaFeedCrawler
from datumoj.rikolto.testing.fakes import FakeParser, FakeRegistry, FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec

DAY = timedelta(days=1)
ENTRY = "https://www.data.jma.go.jp/entry.xml"
FEED = (f'<feed xmlns="http://www.w3.org/2005/Atom"><updated>2026-01-01T00:00:00Z</updated>'
        f'<entry><id>{ENTRY}</id><link type="application/xml" href="{ENTRY}"/></entry></feed>').encode()


@pytest.fixture
def archive(tmp_path):
    spec = make_spec("jp-jma", scope={"raw_retention_days": 7, "raw_expire_latest_types": ["entry"]},
                     base_url=FEED_URL, allowed_hosts=["www.data.jma.go.jp"])
    deps = wiring.build(tmp_path / "data", {"registry": FakeRegistry([spec]), "vectors": None,
                                          "parsers": {("fake", "text/plain"): FakeParser}})
    store = FakeStateObjectStore()
    manager = StateManager(store, tmp_path / "data")
    world = SimpleNamespace(spec=spec, specs=[spec], deps=deps, store=store, manager=manager, root=tmp_path)
    yield world
    for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
        getattr(deps, name).close()


def record(world, name, when=T0, *, resource_type="entry", source_id="jp-jma", data=None, url=None, parsed=True):
    body = data if data is not None else f"Synthetic {name}\n\narchive regression".encode()
    snap = make_snapshot(source_id, url=url or f"https://www.data.jma.go.jp/{name}.xml", data=body,
                         fetched_at=when, snapshot_id=name)
    world.deps.raw.put(body)
    world.deps.manifest.record_snapshot(snap, {"resource_type": resource_type, "entry_id": snap.resource_url})
    if parsed:
        # Exercise real parse/publish metadata, not a hand-built candidate list.
        summary = world.deps.parse_pending(world.deps.pipeline_deps)
        assert not summary.failed
    return snap


def publish(world, when, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    monkeypatch.setattr(state_module, "datetime", Clock)
    marked = world.manager.mark_raw(world.specs, when)
    generation = world.manager.push()
    with world.manager._temporary_stage(prefix="test-verify-") as stage:
        world.manager._materialize(generation, stage, raw="remote")
    return generation, marked


def cleanup(world, when, **kw):
    return world.manager.cleanup(world.specs, now=when, check=lambda: None, **kw)


def raw_key(snap):
    return f"raw/{snap.sha256[:2]}/{snap.sha256}"


def test_ten_days_bound_copies_expire_entries_and_restore_every_survivor(archive, monkeypatch):
    world = archive
    entry = record(world, "entry", url=ENTRY)
    feed = record(world, "feed", resource_type="feed", data=FEED, url=FEED_URL)
    counts = []
    for day in range(11):
        when = T0 + day * DAY
        if day:
            record(world, f"entry-{day}", when=when)
        # Real parse/publish updates change manifests and index blocks every day.
        (world.manager.data_dir / "control.json").write_text(f'{{"day":{day}}}')
        _, marked = publish(world, when, monkeypatch)
        report = cleanup(world, when, raw_enabled=True, marked=marked)
        assert report["failures"] == 0
        counts.append(len(world.store.list("copies/sha256/")))
        assert world.store.exists(raw_key(entry)) == (day < 7)
        assert world.store.exists(raw_key(feed))
        # Cloud rounds restore remote raw; no local cache resurrects deleted bytes.
        for path in (world.manager.data_dir / "raw").glob("*/*"):
            if not world.store.exists(f"raw/{path.parent.name}/{path.name}"):
                path.unlink()
    assert len(world.manager.list()) == 4  # inclusive 72-hour boundary
    assert max(counts[7:]) <= counts[4] + 1
    assert report["generations_deleted"] == 1
    assert report["total_bytes"] == sum(obj.size_bytes for obj in world.store.list_objects(""))
    for generation in world.manager.list():
        for mode in ("remote", "local"):
            target = world.root / f"restored-{generation.generation}-{mode}"
            assert world.manager.restore(target, generation.generation, raw=mode)["verified"]
            with closing(SqliteManifest(target / "manifest.sqlite")) as manifest:
                assert manifest.get_snapshot(entry.snapshot_id) == entry
                assert entry.sha256 in manifest.raw_markers()
            if mode == "local":
                assert (target / raw_key(feed)).read_bytes() == FEED
    # jma_feed reads the previous feed, and ignores already-seen entry raw.
    jma_spec = world.spec.model_copy(update={"kind": "jma_feed", "scope": {"feeds": [FEED_URL]}})
    restored = world.root / "crawler-raw"
    world.manager.restore(restored, raw="local")
    from datumoj.rikolto.core.rawstore import FileRawStore

    ctx = CrawlContext(jma_spec, world.deps.manifest, FileRawStore(restored), T0 + 11 * DAY)
    result = FetchResult(url=FEED_URL, fetched_at=ctx.now, outcome=FetchOutcome.ok, content=FEED)
    assert list(JmaFeedCrawler().after_fetch(FetchTask(url=FEED_URL, meta={"resource_type": "feed"}), result, ctx)) == []


def test_marking_72h_boundary_and_unmarked_retained_generation_veto(archive, monkeypatch):
    snap = record(archive, "old-entry")
    publish(archive, T0 + DAY, monkeypatch)  # a retained, unmarked manifest
    _, marked = publish(archive, T0 + 4 * DAY, monkeypatch)
    assert marked == 1
    assert cleanup(archive, T0 + 7 * DAY - timedelta(microseconds=1), raw_enabled=True)["raw_deleted"] == 0
    # At exactly seven days the mark is 72h old, and all retained manifests carry it.
    publish(archive, T0 + 7 * DAY, monkeypatch)
    assert cleanup(archive, T0 + 7 * DAY, raw_enabled=True)["raw_deleted"] == 1
    assert not archive.store.exists(raw_key(snap))


def test_old_current_and_boundary_generation_keep_shared_blocks(archive, monkeypatch):
    record(archive, "entry")
    first, _ = publish(archive, T0, monkeypatch)
    second, _ = publish(archive, T0 + DAY, monkeypatch)
    first_blocks = {copy.key for copy in first.stores}
    second_blocks = {copy.key for copy in second.stores}
    assert first_blocks & second_blocks
    assert cleanup(archive, T0 + 3 * DAY)["generations_deleted"] == 0
    report = cleanup(archive, T0 + 20 * DAY)
    assert report["generations_deleted"] == 1
    assert [g.generation for g in archive.manager.list()] == [2]
    assert all(archive.store.exists(key) for key in second_blocks)
    assert archive.store.exists("current.json")
    assert archive.manager.restore(archive.root / "old-current")["verified"]


@pytest.mark.parametrize("enabled,raw_enabled", [(False, False), (False, True), (True, False), (True, True)])
def test_switches_report_backlog_and_reenable(archive, monkeypatch, enabled, raw_enabled):
    snap = record(archive, "entry")
    publish(archive, T0, monkeypatch)
    publish(archive, T0 + 4 * DAY, monkeypatch)
    publish(archive, T0 + 10 * DAY, monkeypatch)
    archive.store.put("copies/sha256/orphan", b"orphan")
    before = archive.store.list_objects("")
    report = cleanup(archive, T0 + 10 * DAY, enabled=enabled, raw_enabled=raw_enabled)
    assert report["reclaimable_raw"] == 1
    assert report["reclaimable_generations"] == 2
    assert archive.store.exists(raw_key(snap)) == (not (enabled and raw_enabled))
    if not enabled:
        assert archive.store.list_objects("") == before
    final = cleanup(archive, T0 + 10 * DAY, raw_enabled=True)
    assert final["failures"] == 0
    assert not archive.store.exists(raw_key(snap))
    assert not archive.store.exists("copies/sha256/orphan")


def test_policy_latest_pending_staged_and_multiple_sources(archive):
    world = archive
    protected = record(world, "feed", resource_type="feed")
    entry = record(world, "entry")
    staged = record(world, "staged")
    pending = record(world, "pending", parsed=False)
    world.deps.manifest.record_publish_state(staged.snapshot_id, PublishState.staged)
    assert set(world.manager.raw_retention(world.specs, T0 + 40 * DAY)) == {entry.sha256}
    other = make_spec("jp-other", scope={"raw_expire_latest_types": ["entry"]})
    world.specs.append(other)
    # Same raw: older source requires 30d, newest fetch is from the 7d source.
    record(world, "other-ref", when=T0 - 10 * DAY, data=world.deps.raw.get(entry.sha256), source_id="jp-other", parsed=False)
    world.deps.manifest.record_publish_state("other-ref", PublishState.published)
    record(world, "recent-ref", when=T0 + 2 * DAY, data=world.deps.raw.get(entry.sha256), parsed=False)
    world.deps.manifest.record_publish_state("recent-ref", PublishState.published)
    expiry = world.manager.raw_retention(world.specs, T0 + 40 * DAY)
    assert expiry[entry.sha256] == T0 + 32 * DAY
    assert all(snap.sha256 not in expiry for snap in (protected, pending, staged))
    # One source's latest protection vetoes expiration of a shared hash.
    world.specs[-1] = make_spec("jp-other")
    assert entry.sha256 not in world.manager.raw_retention(world.specs, T0 + 40 * DAY)


def test_cleanup_failure_and_lease_loss_stop_deletions(archive, monkeypatch):
    record(archive, "entry")
    publish(archive, T0, monkeypatch)
    publish(archive, T0 + 10 * DAY, monkeypatch)
    archive.store.put("copies/sha256/orphan-a", b"a")
    archive.store.put("copies/sha256/orphan-b", b"b")
    original = archive.store.delete_if_match
    calls = []

    def fail(key, etag):
        calls.append(key)
        if key.startswith("generations/"):
            raise OSError("synthetic private detail")
        original(key, etag)

    monkeypatch.setattr(archive.store, "delete_if_match", fail)
    assert cleanup(archive, T0 + 10 * DAY)["failures"] == 1
    assert calls == ["generations/1.json"]
    assert archive.manager.restore(archive.root / "after-failure")["verified"]
    monkeypatch.setattr(archive.store, "delete_if_match", original)
    checks = []

    def lease():
        checks.append(1)
        if len(checks) == 3:
            raise OSError("lease lost")

    report = archive.manager.cleanup(archive.specs, now=T0 + 10 * DAY, check=lease, raw_enabled=True)
    assert report["failures"] == 1 and report["generations_deleted"] == 1
    assert report["copies_deleted"] == report["raw_deleted"] == 0


def test_archived_parse_skips_and_cite_keeps_metadata(archive, monkeypatch):
    snap = record(archive, "entry")
    publish(archive, T0 + 4 * DAY, monkeypatch)
    publish(archive, T0 + 8 * DAY, monkeypatch)
    assert cleanup(archive, T0 + 8 * DAY, raw_enabled=True)["raw_deleted"] == 1
    # Mimic the next round's remote raw backend, rather than the test's local cache.
    path = archive.manager.data_dir / raw_key(snap)
    path.unlink()
    summary = archive.deps.parse_pending(archive.deps.pipeline_deps)
    assert summary.skipped == 1 and not summary.failed
    tools = AnalysisTools(registry=archive.deps.registry, manifest=archive.deps.manifest,
                          raw=archive.deps.raw, chunks=archive.deps.chunks,
                          retriever=archive.deps.retriever, catalog=archive.deps.catalog,
                          observations=archive.deps.observations)
    citation = tools.cite(snap.sha256, "para=1", snapshot_id=snap.snapshot_id)
    assert citation["data"]["status"] == "archived"
    assert citation["data"]["resource_url"] == snap.resource_url
    assert "原文已归档，云端不再保留" in citation["warnings"]


@pytest.mark.parametrize("scope,field", [
    ({"raw_retention_days": 0}, "raw_retention_days"),
    ({"raw_retention_days": -1}, "raw_retention_days"),
    ({"raw_retention_days": True}, "raw_retention_days"),
    ({"raw_retention_days": 1.5}, "raw_retention_days"),
    ({"raw_retention_days": "7"}, "raw_retention_days"),
    ({"raw_expire_latest_types": "entry"}, "raw_expire_latest_types"),
    ({"raw_expire_latest_types": [1]}, "raw_expire_latest_types"),
])
def test_retention_configuration_is_validated(scope, field):
    with pytest.raises(ValueError, match=field):
        make_spec(scope=scope)


def test_default_retention_and_short_retention_marking(archive):
    archive.specs = [make_spec("jp-jma")]
    old = record(archive, "old", url=ENTRY)
    latest = record(archive, "latest", when=T0 + DAY, url=ENTRY)
    eligible = archive.manager.raw_retention(archive.specs, T0 + 40 * DAY)
    assert eligible == {old.sha256: T0 + 30 * DAY}
    assert latest.sha256 not in eligible
    assert archive.manager.mark_raw(archive.specs, T0 + 27 * DAY - timedelta(microseconds=1)) == 0
    assert archive.manager.mark_raw(archive.specs, T0 + 27 * DAY) == 1
    short = make_spec("jp-jma", scope={"raw_retention_days": 1, "raw_expire_latest_types": ["entry"]})
    archive.specs = [short]
    assert archive.manager.mark_raw(archive.specs, T0 + DAY) == 1
    assert archive.deps.manifest.raw_markers()[latest.sha256] == T0 + DAY


def test_retained_unmarked_manifest_blocks_deletion_even_with_aged_marker(archive, monkeypatch):
    snap = record(archive, "entry")
    # An older reader/writer published a manifest without the new additive table.
    publish(archive, T0, monkeypatch)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return T0 + 6 * DAY

    monkeypatch.setattr(state_module, "datetime", Clock)
    archive.manager.push()
    archive.deps.manifest.mark_raw_optional(snap.sha256, T0 + 4 * DAY)
    publish(archive, T0 + 7 * DAY, monkeypatch)
    report = cleanup(archive, T0 + 7 * DAY, raw_enabled=True)
    assert report["failures"] == 0 and report["raw_deleted"] == 0
    assert archive.store.exists(raw_key(snap))
    publish(archive, T0 + 10 * DAY, monkeypatch)
    assert cleanup(archive, T0 + 10 * DAY, raw_enabled=True)["raw_deleted"] == 1


def test_post_publish_verification_failure_prevents_cleanup(archive, monkeypatch):
    record(archive, "entry")
    publish(archive, T0, monkeypatch)
    archive.store.put("copies/sha256/orphan", b"orphan")
    before = archive.store.list_objects("")

    def bad_copy(*args, **kwargs):
        raise OSError("synthetic verification failure")

    monkeypatch.setattr(archive.manager, "_materialize", bad_copy)
    report = cleanup(archive, T0 + 10 * DAY, raw_enabled=True)
    assert report["failures"] == 1
    assert report["generations_deleted"] == report["copies_deleted"] == report["raw_deleted"] == 0
    assert archive.store.list_objects("") == before


@pytest.mark.parametrize("extra_bytes,over", [(0, False), (7_000_000_000, True)])
def test_inventory_total_and_seven_gb_signal(archive, monkeypatch, extra_bytes, over):
    from dataclasses import replace

    record(archive, "feed", resource_type="feed")
    publish(archive, T0, monkeypatch)
    inventory = archive.store.list_objects("")
    inventory[0] = replace(inventory[0], size_bytes=inventory[0].size_bytes + extra_bytes)
    monkeypatch.setattr(archive.store, "list_objects", lambda prefix: inventory)
    report = cleanup(archive, T0 + DAY, enabled=False)
    assert report["failures"] == 0
    assert report["total_bytes"] == sum(obj.size_bytes for obj in inventory)
    assert report["over_7gb"] is over


def test_reusable_parse_does_not_unprotect_pending_observation(archive):
    old = record(archive, "published", url=ENTRY)
    pending = record(archive, "pending-repeat", when=T0 + DAY, url=ENTRY,
                     data=archive.deps.raw.get(old.sha256), parsed=False)
    assert archive.deps.manifest.publish_state(old.snapshot_id) is PublishState.published
    assert archive.deps.manifest.publish_state(pending.snapshot_id) is None
    assert old.sha256 == pending.sha256
    assert old.sha256 not in archive.manager.raw_retention(archive.specs, T0 + 40 * DAY)
    assert archive.manager.mark_raw(archive.specs, T0 + 40 * DAY) == 0
    assert archive.deps.parse_pending(archive.deps.pipeline_deps).failed == {}
    assert archive.deps.manifest.publish_state(pending.snapshot_id) is PublishState.published
    assert old.sha256 in archive.manager.raw_retention(archive.specs, T0 + 40 * DAY)
