"""Retention against small real stores: nothing here talks to a network or a bucket."""

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from datumoj.rikolto import db_retention, db_usage
from datumoj.rikolto.core.contracts import FetchTask, ParseRecord, ParseStatus
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import FetchOutcome, PublishState, parse_context_fingerprint
from datumoj.rikolto.core.state import _manifest_raw_references, _raw_policy, _raw_refs, raw_retention
from datumoj.rikolto.crawlers.jma_feed import JmaFeedCrawler
from datumoj.rikolto.index.fts import SqliteKeywordIndex
from datumoj.rikolto.index.vector import LanceVectorIndex
from datumoj.rikolto.pipeline import PipelineDeps, parse_pending
from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeEmbedder,
    FakeKeywordIndex,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
)
from datumoj.rikolto.testing.samples import make_chunk, make_document, make_snapshot, make_spec

NOW = datetime(2026, 11, 18, 12, tzinfo=UTC)
SPECS = [make_spec("jp-test"), make_spec("jp-jma", scope={"urls": ["https://example.go.jp/a.txt"],
                                                          "index_retention_hours": 48})]


def ago(hours):
    return NOW - timedelta(hours=hours)


def sha_of(text):
    return hashlib.sha256(text.encode()).hexdigest()


class World:
    """Real manifest and chunk store, a keyword index and a vector index over the same chunks."""

    def __init__(self, root, specs=SPECS):
        root.mkdir(exist_ok=True)
        self.root, self.specs, self.counter = root, specs, 0
        self.manifest = SqliteManifest(root / "manifest.sqlite")
        self.chunks = SqliteChunkStore(root / "chunks.sqlite")
        self.keyword = SqliteKeywordIndex(root / "keyword.sqlite")
        self.vectors = LanceVectorIndex(root / "vectors", FakeEmbedder())
        self.docs = {}

    def close(self):
        for store in (self.manifest, self.chunks, self.keyword):
            store.close()

    def fetch(self, source, url, text, when, *, outcome=FetchOutcome.ok, state=PublishState.published, meta=None):
        self.counter += 1
        snap = make_snapshot(source, url, text.encode(), fetched_at=when, outcome=outcome,
                             snapshot_id=f"snap-{self.counter}")
        self.manifest.record_snapshot(snap, meta or {})
        if state is not None and snap.sha256 is not None:
            self.manifest.record_publish_state(snap.snapshot_id, state)
        return snap

    def version(self, source, name, label, when, *, current=True, record=True, locators=("row=1",)):
        """One stored version of a document; ``record=False`` leaves no snapshot (unknown time)."""
        doc_key = f"{source}:{name}"
        text = f"{name}-{label} bulletin"
        if record:
            sha = self.fetch(source, f"https://example.go.jp/{name}", text, when).sha256
            fingerprint = parse_context_fingerprint(self.manifest.get_snapshot(f"snap-{self.counter}"), {})
            self.manifest.record_parse(ParseRecord(sha256=sha, parser="p", version="1", status=ParseStatus.ok,
                                                   context_fingerprint=fingerprint, doc_keys=[doc_key]))
        else:
            sha = sha_of(text)
        chunks = [make_chunk(doc_key, sha, locator, f"{name}-{label} {locator}") for locator in locators]
        self.chunks.put_document(make_document(doc_key, sha), chunks, publish=current)
        if current:
            self.keyword.add(chunks)
        self.vectors.add(chunks)
        self.docs[name, label] = (doc_key, sha, [c.chunk_id for c in chunks])
        return sha

    def run(self, **kw):
        self.close()
        kw = {"execute": False, "observations": set(), **kw}
        return db_retention.run(self.root, NOW, self.specs, **kw)

    def rows(self, table, column):
        with closing(sqlite3.connect(self.root / ("manifest.sqlite" if table in MANIFEST else "chunks.sqlite"))) as db:
            return {r[0] for r in db.execute(f"SELECT {column} FROM {table}")}


MANIFEST = {"snapshots", "parsed", "parse_writers", "publish_states", "raw_allowed_missing"}


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path / "data")
    # Superseded 72 h ago, still superseded-too-recently, unknown time, and a shared chunk id.
    w.version("jp-test", "stale", "old", ago(24 * 30), current=False, locators=("row=1", "row=2"))
    w.version("jp-test", "stale", "new", ago(72))
    w.version("jp-test", "fresh", "old", ago(24 * 30), current=False)
    w.version("jp-test", "fresh", "new", ago(10))
    w.version("jp-test", "mystery", "old", ago(24 * 40), current=False, record=False)
    w.version("jp-test", "mystery", "new", ago(100))
    # Bulletins: expired, fresh, unknown fetch time.
    w.version("jp-jma", "expired", "only", ago(72))
    w.version("jp-jma", "recent", "only", ago(1))
    w.version("jp-jma", "unknown", "only", ago(500), record=False)
    return w


def current_ids(root):
    with closing(SqliteChunkStore(root / "chunks.sqlite")) as store:
        return {c.chunk_id for c in store.iter_chunks()}


def test_dry_run_changes_nothing_and_reports_counts_only(world):
    world.close()
    before = {name: (world.root / name).read_bytes() for name in ("manifest.sqlite", "chunks.sqlite")}
    report = db_retention.run(world.root, NOW, SPECS, observations=set())
    assert report["mode"] == "dry_run"
    assert {name: (world.root / name).read_bytes() for name in before} == before
    assert report["superseded"]["sources"] == {"jp-test": {"versions": 1, "chunks": 2, "bytes": report[
        "superseded"]["sources"]["jp-test"]["bytes"]}}
    assert report["index_expired"]["sources"]["jp-jma"]["documents"] == 1
    assert "https://" not in json.dumps(report) and "bulletin" not in json.dumps(report)


def test_old_versions_and_expired_bulletins_go_only_when_due(world):
    kept_before = world.docs
    report = world.run(execute=True)
    assert world.rows("documents", "doc_key || '@' || snapshot_sha256") == {
        f"{kept_before[key][0]}@{kept_before[key][1]}" for key in (
            ("stale", "new"), ("fresh", "old"), ("fresh", "new"), ("mystery", "old"), ("mystery", "new"),
            ("recent", "only"), ("unknown", "only"))}
    assert report["superseded"]["waiting_superseded"] == 1  # fresh/old: superseded only 10 h ago
    assert report["superseded"]["unknown_superseded"] == 1  # mystery/old: no time, never deleted
    assert report["index_expired"]["unknown_index_age"] == 1  # unknown bulletin: no time, never deleted
    assert world.rows("current_versions", "doc_key") == {k for k, _, _ in kept_before.values()} - {"jp-jma:expired"}
    assert world.rows("published_orders", "doc_key") == set()
    survivors = current_ids(world.root)
    assert "jp-jma:expired#row=1" not in survivors and "jp-jma:recent#row=1" in survivors
    assert "jp-test:stale#row=1" in survivors  # same chunk id in the old and the current version


def test_index_and_vectors_never_return_deleted_documents(world):
    # Before: the old version's own vector (row=2) and the shared one (row=1) are both findable.
    first = LanceVectorIndex(world.root / "vectors", FakeEmbedder())
    present = {item.chunk_id for item in first.search("bulletin", k=100).items}
    assert {"jp-test:stale#row=1", "jp-test:stale#row=2"} <= present
    world.run(execute=True)
    with closing(SqliteKeywordIndex(world.root / "keyword.sqlite")) as keyword:
        hits = {item.doc_key for item in keyword.search("expired-only bulletin", k=100).items}
        assert "jp-jma:expired" not in hits and "jp-jma:recent" in hits
    vectors = LanceVectorIndex(world.root / "vectors", FakeEmbedder())
    found = {item.chunk_id for item in vectors.search("bulletin", k=100).items}
    assert "jp-jma:expired#row=1" not in found and "jp-test:stale#row=2" not in found  # old version's own vector
    assert {"jp-test:stale#row=1", "jp-jma:recent#row=1", "jp-test:fresh#row=1"} <= found
    assert vectors._table.count_rows() == len(found)


def test_planned_old_version_counts_match_db_usage_estimate(world):
    world.close()
    copies = world.root.parent / "copy"
    copies.mkdir()
    for name in ("manifest", "chunks"):
        with closing(sqlite3.connect(world.root / f"{name}.sqlite")) as source, closing(
            sqlite3.connect(copies / f"{name}.sqlite")
        ) as target:
            source.backup(target)
    estimate = db_usage.measure_usage(manifest=copies / "manifest.sqlite", chunks=copies / "chunks.sqlite",
                                      now=NOW)["estimates"]["A"]
    planned = db_retention.run(world.root, NOW, [SPECS[0]], observations=set())["superseded"]["sources"]["jp-test"]
    assert estimate == {"documents": planned["versions"], "chunks": planned["chunks"], "bytes": planned["bytes"]}


def test_index_expiry_compares_whole_elapsed_hours_and_survives_huge_settings(tmp_path):
    w = World(tmp_path / "edge")
    w.version("jp-jma", "exact", "only", ago(48))  # fetched exactly 48 h ago: due
    w.version("jp-jma", "short", "only", ago(48) + timedelta(seconds=1))  # one second short: not yet
    w.close()
    huge = [SPECS[0], make_spec("jp-jma", scope={"urls": ["https://example.go.jp/a.txt"],
                                                 "index_retention_hours": 24_000_000_000})]
    report = db_retention.run(w.root, NOW, huge, observations=set())  # no OverflowError
    assert report["index_expired"] == {"sources": {}, "unknown_index_age": 0}
    due = db_retention.run(w.root, NOW, SPECS, execute=True, observations=set())
    assert due["index_expired"]["sources"]["jp-jma"]["documents"] == 1
    assert w.rows("documents", "doc_key") == {"jp-jma:short"}


def test_disabled_index_retention_deletes_no_bulletin(world):
    world.close()
    plain = [make_spec("jp-test"), make_spec("jp-jma", scope={"urls": ["https://example.go.jp/a.txt"]})]
    report = db_retention.run(world.root, NOW, plain, observations=set())
    assert report["index_expired"] == {"sources": {}, "unknown_index_age": 0}


# ---- manifest rules -------------------------------------------------------------------------


def manifest_world(tmp_path):
    w = World(tmp_path / "m")
    url = "https://example.go.jp/r1"
    names = {}

    def row(name, text, hours, **kw):
        names[name] = w.fetch("jp-test", kw.pop("url", url), text, ago(hours), **kw)

    row("old_ok", "x1", 200)
    row("mid_nm", "x1", 150, outcome=FetchOutcome.not_modified)
    row("old_fail", "x1", 120, outcome=FetchOutcome.network_error)
    row("new_ok", "x2", 100)
    row("latest_nm", "x2", 90, outcome=FetchOutcome.not_modified)
    row("latest_fail", "x2", 80, outcome=FetchOutcome.network_error)
    other = "https://example.go.jp/r2"
    row("recent_ok", "y1", 10, url=other)
    row("raw_present_old", "y0", 100, url=other)
    row("pending_old", "p0", 120, url="https://example.go.jp/r3", state=None)
    row("pending_new", "p1", 60, url="https://example.go.jp/r3")
    row("staged_old", "s0", 120, url="https://example.go.jp/r4", state=PublishState.staged)
    row("staged_new", "s1", 60, url="https://example.go.jp/r4")
    row("observed_old", "o0", 120, url="https://example.go.jp/r5")
    row("observed_new", "o1", 60, url="https://example.go.jp/r5")
    for name, snap in names.items():
        if snap.sha256 is not None:
            w.manifest.record_parse(ParseRecord(
                sha256=snap.sha256, parser="p", version="1", status=ParseStatus.ok,
                context_fingerprint=parse_context_fingerprint(snap, {}), doc_keys=[f"jp-test:{name}"]))
    for snap in names.values():
        if snap.sha256 is not None:
            w.manifest.mark_raw_optional(snap.sha256, ago(300))
    w.close()
    return w, names


def queries(path, now):
    with closing(SqliteManifest(path)) as manifest:
        urls = [f"https://example.go.jp/r{i}" for i in range(1, 6)]
        return {
            "latest": {u: getattr(manifest.latest_snapshot(u), "snapshot_id", None) for u in urls},
            "success": {u: getattr(manifest.last_success(u), "snapshot_id", None) for u in urls},
            "freshness": manifest.freshness("jp-test"),
        }


def test_manifest_keeps_the_rule_classes_and_queries_do_not_change(tmp_path):
    w, names = manifest_world(tmp_path)
    present = {names["raw_present_old"].sha256}
    observed = {names["observed_old"].sha256}
    before = queries(w.root / "manifest.sqlite", NOW)
    protected_before = _raw_policy(w.root / "manifest.sqlite", SPECS)[1]
    expiry_before = raw_retention(w.root / "manifest.sqlite", SPECS, NOW)
    refs_before = _raw_refs(w.root, specs=SPECS)
    hashes_before = _manifest_raw_references(w.root / "manifest.sqlite")
    report = db_retention.run(w.root, NOW, SPECS, execute=True, raw_present=lambda sha: sha in present,
                              observations=observed)
    kept = w.rows("snapshots", "snapshot_id")
    dropped = {"old_ok", "mid_nm", "old_fail"}
    assert kept == {snap.snapshot_id for name, snap in names.items() if name not in dropped}
    assert report["manifest"]["snapshots_dropped"] == 3 and report["manifest"]["snapshots_kept"] == len(kept)
    # Incremental decisions, freshness, raw protection, raw expiry of what still exists: unchanged.
    assert queries(w.root / "manifest.sqlite", NOW) == before
    assert _raw_policy(w.root / "manifest.sqlite", SPECS)[1] == protected_before
    # cleanup's required raw set is unchanged; the manifest only stops naming a hash whose raw is already
    # gone, so no raw object that still exists can turn into an orphan.
    hashes_after = _manifest_raw_references(w.root / "manifest.sqlite")
    assert _raw_refs(w.root, specs=SPECS) == refs_before
    assert hashes_before - hashes_after == {names["old_ok"].sha256} and present <= hashes_after
    after_expiry = raw_retention(w.root / "manifest.sqlite", SPECS, NOW)
    assert {sha: at for sha, at in after_expiry.items() if sha in present} == {
        sha: at for sha, at in expiry_before.items() if sha in present}
    # Dependent rows follow the hash: no parse record, writer or marker survives for a hash with no row.
    gone = {names["old_ok"].sha256}  # x1: all three rows dropped
    assert gone.isdisjoint(w.rows("parsed", "sha256") | w.rows("raw_allowed_missing", "sha256"))
    assert w.rows("publish_states", "snapshot_id") == {
        s.snapshot_id for n, s in names.items() if n not in dropped | {"pending_old"} and s.sha256 is not None}
    assert names["new_ok"].sha256 in w.rows("parsed", "sha256")


def test_hash_bearing_rows_are_kept_when_raw_may_still_exist(tmp_path):
    w, names = manifest_world(tmp_path)
    report = db_retention.run(w.root, NOW, SPECS, execute=True, raw_present=None, observations=set())
    # Without a raw inventory only rows without a hash can go (old failure, not_modified).
    assert w.rows("snapshots", "snapshot_id") >= {names["old_ok"].snapshot_id}
    assert report["manifest"]["snapshots_dropped"] == 2


def test_observation_hashes_come_from_real_parquet_and_keep_their_snapshot_rows(tmp_path):
    from datumoj.rikolto.store.observations import DuckDBObservationStore
    from datumoj.rikolto.testing.samples import make_observation

    w, names = manifest_world(tmp_path)
    cited = names["observed_old"].sha256
    store = DuckDBObservationStore(w.root)
    store.write_table("jp-test", "t1", [make_observation("o1", "1", source_id="jp-test", dataset_key="jp-test:d",
                                                         table_key="t1", snapshot_sha256=cited)])
    assert db_retention.observation_shas(w.root) == {cited}
    assert db_retention.observation_shas(tmp_path / "nowhere") == set()
    # Without passing the set explicitly the run finds it by itself and keeps the cited row.
    db_retention.run(w.root, NOW, SPECS, execute=True, raw_present=lambda sha: False)
    assert names["observed_old"].snapshot_id in w.rows("snapshots", "snapshot_id")
    assert names["old_ok"].snapshot_id not in w.rows("snapshots", "snapshot_id")


# ---- replay -----------------------------------------------------------------------------------


def jma_pipeline(tmp_path):
    root = tmp_path / "replay"
    root.mkdir()
    spec = make_spec("jp-jma", scope={"urls": ["https://example.go.jp/a.txt"], "index_retention_hours": 48})
    manifest = SqliteManifest(root / "manifest.sqlite")
    chunks = SqliteChunkStore(root / "chunks.sqlite")
    raw = FakeRawStore()
    deps = PipelineDeps(manifest=manifest, raw=raw, registry=FakeRegistry([spec]), chunks=chunks,
                        keyword=FakeKeywordIndex(), observations=FakeObservationStore(),
                        catalog=FakeCatalogStore(), parsers={("fake", "text/plain"): FakeParser})
    for name, hours in (("old1", 100), ("old2", 60), ("new", 1)):
        data = f"{name}\n\nbody of {name}".encode()
        raw.put(data)
        manifest.record_snapshot(make_snapshot("jp-jma", f"https://www.data.jma.go.jp/{name}", data,
                                               fetched_at=ago(hours), snapshot_id=name),
                                 {"resource_type": "entry", "entry_id": f"https://www.data.jma.go.jp/{name}"})
    return root, spec, deps


def test_expired_bulletins_are_not_parsed_or_indexed_again_when_the_feed_replays(tmp_path):
    root, spec, deps = jma_pipeline(tmp_path)
    assert len(parse_pending(deps).parsed) == 3
    deps.manifest.close(), deps.chunks.close()
    # The worst case for re-ingestion: raw objects are still there for every snapshot.
    report = db_retention.run(root, NOW, [spec], execute=True, raw_present=lambda sha: True, observations=set())
    assert report["index_expired"]["sources"]["jp-jma"]["documents"] == 2
    deps.manifest = SqliteManifest(root / "manifest.sqlite")
    deps.chunks = SqliteChunkStore(root / "chunks.sqlite")
    replay = parse_pending(deps)
    assert replay.parsed == [] and replay.skipped == 3 and not replay.failed
    assert {c.doc_key for c in deps.chunks.iter_chunks()} == {"jp-jma:new"}
    assert deps.chunks.get_document("jp-jma:old1") is None
    # The crawler's "already saved" test reads the kept snapshots: the same feed schedules nothing.
    entries = "".join(
        f'<entry><id>https://www.data.jma.go.jp/{n}</id><updated>2026-11-17T00:00:00+09:00</updated>'
        f'<link type="application/xml" href="https://www.data.jma.go.jp/{n}"/></entry>' for n in ("old1", "old2", "new"))
    feed = f'<feed xmlns="http://www.w3.org/2005/Atom"><updated>2026-11-18T00:00:00+09:00</updated>{entries}</feed>'
    feed_spec = make_spec("jp-jma", kind="jma_feed", allowed_hosts=["www.data.jma.go.jp"],
                          scope={"feeds": ["https://www.data.jma.go.jp/feed.xml"], "feed_lookback_days": 7})
    ctx = SimpleNamespace(spec=feed_spec, manifest=deps.manifest, raw=deps.raw, limit=None)
    task = FetchTask(url="https://www.data.jma.go.jp/feed.xml", meta={"resource_type": "feed"})
    result = SimpleNamespace(outcome=FetchOutcome.ok, content=feed.encode())
    assert list(JmaFeedCrawler().after_fetch(task, result, ctx)) == []


# ---- file maintenance --------------------------------------------------------------------------


def autovac(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("PRAGMA auto_vacuum").fetchone()[0]


def test_one_off_vacuum_needs_the_gate_then_only_capped_incremental_maintenance(world):
    asked = []

    def refuse(estimated):
        asked.append(estimated)
        return {"allowed": False, "reason": "capacity_freeze"}

    first = world.run(execute=True, gate=refuse)
    assert first["vacuum"]["one_off"]["skipped_reason"] == "capacity_freeze"
    assert first["vacuum"]["one_off"]["done"] == [] and len(asked) == 1 and asked[0] > 0
    assert autovac(world.root / "chunks.sqlite") == 0 and autovac(world.root / "manifest.sqlite") == 0

    allowed = db_retention.run(world.root, NOW, world.specs, execute=True, observations=set(),
                               gate=lambda estimated: {"allowed": True, "reason": "capacity_normal"})
    assert allowed["vacuum"]["one_off"]["done"] == ["manifest", "chunks"]
    assert autovac(world.root / "chunks.sqlite") == autovac(world.root / "manifest.sqlite") == 2
    # Incremental mode adds pointer-map pages, so a tiny file may even grow; the report just has both sizes.
    assert allowed["vacuum"]["files"]["chunks"]["after_bytes"] > 0 < allowed["vacuum"]["files"]["chunks"]["before_bytes"]

    with closing(sqlite3.connect(world.root / "chunks.sqlite")) as db:
        db.execute("CREATE TABLE junk(x)")
        db.executemany("INSERT INTO junk VALUES(?)", [("x" * 3000,)] * 300)
        db.commit()
        db.execute("DROP TABLE junk")
        db.commit()
        assert db.execute("PRAGMA freelist_count").fetchone()[0] > 50
    never = []
    later = db_retention.run(world.root, NOW, world.specs, execute=True, observations=set(), incremental_pages=7,
                             gate=lambda estimated: never.append(estimated))
    assert never == [] and later["vacuum"]["one_off"]["pending"] == []
    assert later["vacuum"]["incremental"]["chunks"]["pages"] == 7
    assert later["vacuum"]["incremental"]["manifest"]["pages"] <= 7


def test_settings_default_to_off_and_cap_the_page_count():
    assert db_retention.settings({}) == {"enabled": False, "incremental_pages": db_retention.DEFAULT_INCREMENTAL_PAGES}
    on = db_retention.settings({"R2_DB_RETENTION": "on", "R2_DB_INCREMENTAL_PAGES": "999999999"})
    assert on == {"enabled": True, "incremental_pages": db_retention.MAX_INCREMENTAL_PAGES}
    assert db_retention.settings({"R2_DB_INCREMENTAL_PAGES": "bad"})["incremental_pages"] == 5000
