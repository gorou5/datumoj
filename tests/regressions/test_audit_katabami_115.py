"""KATABAMI-115: the five GitHub review comments on the database retention (#194).

Fake stores and temporary sqlite only; every test names the comment it covers.
"""

import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from datumoj.rikolto import cloud, db_retention
from datumoj.rikolto.core.contracts import FetchTask, ParseRecord, ParseStatus
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import FetchOutcome, parse_context_fingerprint
from datumoj.rikolto.core.state import StateError, _check_schema
from datumoj.rikolto.crawlers.jma_feed import JmaFeedCrawler
from datumoj.rikolto.index.fts import SqliteKeywordIndex
from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.testing.samples import make_chunk, make_document, make_spec
from tests.test_cloud_db_retention import count, expiring, restored
from tests.test_cloud_run import invoke, released
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture (cloud tests only).
from tests.test_db_retention import NOW, World, ago, sha_of

HOST = "https://www.data.jma.go.jp/"
GB = 1_000_000_000


def go(w):
    return w.run(execute=True, raw_present=lambda sha: False)


def pending(root):
    with closing(sqlite3.connect(root / "chunks.sqlite")) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='retention_pending'").fetchone():
            return None
        return set(db.execute("SELECT kind, key FROM retention_pending"))


def keyword_chunks(root, doc_key):
    with closing(sqlite3.connect(root / "keyword.sqlite")) as db:
        return {r[0] for r in db.execute("SELECT chunk_id FROM entries WHERE doc_key=?", (doc_key,))}


def entry_doc(w, doc_key, text, url, hours, meta=None):
    """One stored bulletin: snapshot (with meta), parse record, current document."""
    sha = w.fetch(w.specs[0].id, url, text, ago(hours), meta=meta).sha256
    snap = w.manifest.get_snapshot(f"snap-{w.counter}")
    w.manifest.record_parse(ParseRecord(sha256=sha, parser="p", version="1", status=ParseStatus.ok,
                                        context_fingerprint=parse_context_fingerprint(snap, meta or {}),
                                        doc_keys=[doc_key]))
    w.chunks.put_document(make_document(doc_key, sha), [make_chunk(doc_key, sha, "row=1", text)])
    return sha


def snapshot_ids(root):
    with closing(sqlite3.connect(root / "manifest.sqlite")) as db:
        return {r[0] for r in db.execute("SELECT snapshot_id FROM snapshots")}


# ---- 1. JMA entries keep one successful record per entry_id --------------------------------------


def test_two_entries_on_one_url_both_stay_saved_inside_the_lookback_window(tmp_path):
    spec = make_spec("jp-jma", kind="jma_feed", allowed_hosts=["www.data.jma.go.jp"],
                     scope={"feeds": [HOST + "feed.xml"], "feed_lookback_days": 7, "index_retention_hours": 48})
    w = World(tmp_path / "data", [spec])
    url = HOST + "same.xml"
    for entry, hours in (("e1", 100), ("e2", 60)):
        entry_doc(w, f"jp-jma:{entry}", f"{entry} body", url, hours,
                  {"resource_type": "entry", "entry_id": HOST + entry})
    report = go(w)
    assert report["index_expired"]["sources"]["jp-jma"]["documents"] == 2  # the index still goes after 48 h

    entries = "".join(f'<entry><id>{HOST}{n}</id><updated>2026-11-17T00:00:00+09:00</updated>'
                      f'<link type="application/xml" href="{url}"/></entry>' for n in ("e1", "e2"))
    feed = f'<feed xmlns="http://www.w3.org/2005/Atom"><updated>2026-11-18T00:00:00+09:00</updated>{entries}</feed>'
    with closing(SqliteManifest(w.root / "manifest.sqlite")) as manifest:
        ctx = SimpleNamespace(spec=spec, manifest=manifest, raw=None, limit=None)
        task = FetchTask(url=HOST + "feed.xml", meta={"resource_type": "feed"})
        result = SimpleNamespace(outcome=FetchOutcome.ok, content=feed.encode())
        assert list(JmaFeedCrawler().after_fetch(task, result, ctx)) == []  # nothing is fetched again


def test_entry_outside_the_lookback_window_is_not_protected(tmp_path):
    spec = make_spec("jp-jma", kind="jma_feed", allowed_hosts=["www.data.jma.go.jp"],
                     scope={"feeds": [HOST + "feed.xml"], "feed_lookback_days": 7, "index_retention_hours": 48})
    w = World(tmp_path / "data", [spec])
    url = HOST + "same.xml"
    entry_doc(w, "jp-jma:old", "old body", url, 24 * 10, {"resource_type": "entry", "entry_id": HOST + "old"})
    entry_doc(w, "jp-jma:new", "new body", url, 60, {"resource_type": "entry", "entry_id": HOST + "new"})
    go(w)
    assert snapshot_ids(w.root) == {"snap-2"}  # ten days old: past the 7-day window, dropped as before


# ---- 2. dropping a middle version does not age the newest one ------------------------------------


def test_removing_version_b_keeps_the_start_of_the_newest_a(tmp_path):
    spec = make_spec("jp-jma", scope={"urls": [HOST + "a"], "index_retention_hours": 48})
    w = World(tmp_path / "data", [spec])
    url = HOST + "dl.xml"
    sha_a = entry_doc(w, "jp-jma:first", "A", url, 140)
    entry_doc(w, "jp-jma:middle", "B", url, 100)
    entry_doc(w, "jp-jma:latest", "A", url, 1)
    w.close()

    def start_of_a():
        with closing(sqlite3.connect(w.root / "manifest.sqlite")) as db:
            return db_retention._fetch_times(db)[0]["jp-jma", sha_a]

    before = start_of_a()
    assert before == ago(1)
    for _ in range(2):  # round 1 removes B; round 2 has nothing new to fetch
        report = go(w)
        assert start_of_a() == before
    assert report["index_expired"]["sources"] == {}  # round 2 expires nothing
    with closing(SqliteChunkStore(w.root / "chunks.sqlite")) as store:
        assert store.get_document("jp-jma:latest") is not None
        assert store.get_document("jp-jma:middle") is None  # B's document did expire in round 1
    assert {"snap-1", "snap-3"} <= snapshot_ids(w.root)


@pytest.mark.parametrize(("hashes", "keeps", "revived"), [
    ("ABA", "101", {1}),          # the separator comes back
    ("ABCA", "1001", {1}),        # only the first different row: B, C stays dropped
    ("AABA", "1001", {2}),        # a dropped row of the same hash is not a separator
    ("ABB", "100", set()),        # nothing kept after it: no run to protect
    ("ABA", "111", set()),        # nothing dropped
    ("ABC", "101", set()),        # different hashes on both sides
])
def test_run_boundary_rule_on_synthetic_rows(hashes, keeps, revived):
    rows = [(i, f"s{i}", "src", "url", None, "ok", h, None) for i, h in enumerate(hashes)]
    flags = [c == "1" for c in keeps]
    db_retention._keep_run_boundaries(rows, flags)
    assert {i for i, flag in enumerate(flags) if flag} == {i for i, c in enumerate(keeps) if c == "1"} | revived


# ---- 3. vector maintenance first, the record afterwards ------------------------------------------


@pytest.fixture
def due(tmp_path):
    w = World(tmp_path / "data")
    w.version("jp-jma", "expired", "only", ago(72))
    w.version("jp-jma", "recent", "only", ago(1))
    return w


def test_failed_vector_maintenance_keeps_the_record_and_is_retried_even_without_new_deletions(due, monkeypatch):
    calls = []
    real = db_retention.maintain_vectors

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("injected")
        return real(*args, **kwargs)

    monkeypatch.setattr(db_retention, "maintain_vectors", flaky)
    with pytest.raises(OSError):
        go(due)
    assert ("vector", "jp-jma:expired#row=1") in pending(due.root)  # the record survived the failure

    original = db_retention.delete_vectors
    monkeypatch.setattr(db_retention, "delete_vectors", lambda *a: (original(*a), 0)[1])  # deleted nothing new
    report = go(due)
    assert len(calls) == 2 and report["vectors"]["deleted"] == 0
    assert pending(due.root) is None


# ---- 4. keyword records go only after the keyword index holds the current version ------------------


def test_keyword_record_of_a_current_document_is_rewritten_not_dropped(tmp_path, monkeypatch):
    w = World(tmp_path / "data")
    w.version("jp-jma", "expired", "only", ago(72), locators=("row=1", "row=2"))
    w.version("jp-jma", "recent", "only", ago(1))
    root, key = w.root, "jp-jma:expired"

    def boom(*args, **kwargs):
        raise OSError("injected")

    monkeypatch.setattr(SqliteKeywordIndex, "delete", boom)
    with pytest.raises(OSError):
        go(w)
    monkeypatch.undo()
    assert ("keyword", key) in pending(root)
    # The document is published again (one chunk) but the keyword replacement fails: row=2 stays indexed.
    chunk = make_chunk(key, sha_of("again"), "row=1", "again row=1")
    with closing(SqliteChunkStore(root / "chunks.sqlite")) as chunks:
        chunks.put_document(make_document(key, sha_of("again")), [chunk], publish=True)
    with pytest.raises(StateError, match="关键词索引包含未发布的块"):
        _check_schema(root)

    go(w)
    assert keyword_chunks(root, key) == {f"{key}#row=1"} and pending(root) is None
    _check_schema(root)  # publication is no longer refused


# ---- 5. the one-off VACUUM is decided on a fresh whole-bucket listing ----------------------------


def build(tmp_path, monkeypatch, listing):
    """cloud._db_retention over a real local directory; ``listing`` stands in for the bucket total."""
    w = World(tmp_path / "data")
    w.version("jp-jma", "expired", "only", ago(72))
    w.close()
    calls = []

    def total(store):
        calls.append(store)
        if isinstance(listing, Exception):
            raise listing
        return listing

    monkeypatch.setattr(cloud, "_project_storage_bytes", total)
    manager = SimpleNamespace(store=SimpleNamespace(list=lambda prefix: []))
    deps = SimpleNamespace(registry=SimpleNamespace(all=lambda: w.specs))
    return w, manager, deps, calls


def captured_gate(monkeypatch, tmp_path, listing):
    seen = {}
    w, manager, deps, calls = build(tmp_path, monkeypatch, listing)
    monkeypatch.setattr(db_retention, "run", lambda *a, **kw: seen.update(gate=kw["gate"]))
    cloud._db_retention(manager, deps, w.root, 5000)
    return seen["gate"], calls


def test_vacuum_gate_uses_a_fresh_listing_without_adding_uploads(tmp_path, monkeypatch):
    # Round start 8.30 GB, another prefix wrote 0.65 GB meanwhile, nothing uploaded by this round.
    gate, calls = captured_gate(monkeypatch, tmp_path, int(8.95 * GB))
    decision = gate(int(0.10 * GB))
    assert decision["allowed"] is False and decision["reason"] == "capacity_stop"
    assert decision["current_bytes"] == int(8.95 * GB) and len(calls) == 1
    # The same estimate at the old total is fine, and the listing is read again at every decision.
    assert cloud.gate(int(8.30 * GB), int(0.10 * GB), "optional")["allowed"] is True


def test_vacuum_gate_does_not_count_uploaded_raw_twice(tmp_path, monkeypatch):
    # The fresh listing already holds this round's uploads: 8.30 GB in total admits a 0.10 GB VACUUM.
    gate, _ = captured_gate(monkeypatch, tmp_path, int(8.30 * GB))
    assert gate(int(0.10 * GB))["allowed"] is True


def test_failed_listing_skips_only_the_vacuum(tmp_path, monkeypatch):
    w, manager, deps, calls = build(tmp_path, monkeypatch, OSError("https://synthetic.invalid/?key=secret"))
    with closing(sqlite3.connect(w.root / "chunks.sqlite")) as db:
        before = db.execute("SELECT count(*) FROM documents").fetchone()[0]
    report = cloud._db_retention(manager, deps, w.root, 5000)
    one_off = report["vacuum"]["one_off"]
    assert one_off["skipped_reason"] == "capacity_unknown" and one_off["done"] == [] and len(calls) == 1
    assert "synthetic" not in str(report)
    with closing(sqlite3.connect(w.root / "chunks.sqlite")) as db:
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == before - 1  # deletion went on
        assert db.execute("PRAGMA auto_vacuum").fetchone()[0] == 0


def test_incremental_maintenance_never_lists_the_bucket(tmp_path, monkeypatch):
    w, manager, deps, calls = build(tmp_path, monkeypatch, int(1 * GB))
    first = cloud._db_retention(manager, deps, w.root, 5000)
    assert first["vacuum"]["one_off"]["done"] == ["manifest", "chunks"] and len(calls) == 1
    again = cloud._db_retention(manager, deps, w.root, 5000)
    assert again["vacuum"]["one_off"]["pending"] == [] and len(calls) == 1  # no second listing


# ---- switch off: nothing of the above runs ---------------------------------------------------------


def test_switch_off_changes_nothing_in_a_cloud_round(world, monkeypatch):
    monkeypatch.delenv("R2_DB_RETENTION", raising=False)
    expiring(world)
    monkeypatch.setattr(cloud, "_db_retention", lambda *a, **k: pytest.fail("retention ran with the switch off"))
    for name in ("first", "second"):
        result, _ = invoke(world, name)
        assert result["status"] == "completed" and "db_retention" not in result
    kept = restored(world, "kept")
    assert count(kept, "documents") == 3 and count(kept, "current_versions") == 3
    assert pending(kept) is None
    released(world)


def test_dry_run_and_pending_read_create_nothing(due):
    due.close()
    before = {n: (due.root / n).read_bytes() for n in ("manifest.sqlite", "chunks.sqlite")}
    db_retention.run(due.root, NOW, due.specs, observations=set())
    assert {n: (due.root / n).read_bytes() for n in before} == before
