"""KATABAMI-114: a retention run that fails half-way is finished by the next one.

Each step of ``db_retention.run`` commits on its own, so the tests make one step fail and run again.
"""

import sqlite3
from contextlib import closing

import pytest

from datumoj.rikolto import cloud, db_retention
from datumoj.rikolto.core.state import StateError, _check_schema
from datumoj.rikolto.index.fts import SqliteKeywordIndex
from datumoj.rikolto.index.vector import LanceVectorIndex
from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.testing.fakes import FakeEmbedder
from datumoj.rikolto.testing.samples import make_chunk, make_document
from tests.test_cloud_db_retention import count, expiring, restored
from tests.test_cloud_run import invoke, released
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture (cloud tests only).
from tests.test_db_retention import World, ago, sha_of

EXPIRED_KEY, EXPIRED_VECTOR = "jp-jma:expired", "jp-jma:expired#row=1"
STALE_ONLY_VECTOR, SHARED_VECTOR = "jp-test:stale#row=2", "jp-test:stale#row=1"


class Boom(OSError):
    pass


def boom(*args, **kwargs):
    raise Boom("injected")


@pytest.fixture
def local(tmp_path):
    w = World(tmp_path / "data")
    # Superseded 72 h ago (row=2 exists only in the old version), unknown-time old version, expired bulletin.
    w.version("jp-test", "stale", "old", ago(24 * 30), current=False, locators=("row=1", "row=2"))
    w.version("jp-test", "stale", "new", ago(72))
    w.version("jp-test", "mystery", "old", ago(24 * 40), current=False, record=False)
    w.version("jp-test", "mystery", "new", ago(100))
    w.version("jp-jma", "expired", "only", ago(72))
    w.version("jp-jma", "recent", "only", ago(1))
    return w


def go(w):
    return w.run(execute=True, raw_present=lambda sha: False)


def pending(root):
    """Rows of the pending table; None when the table does not exist."""
    with closing(sqlite3.connect(root / "chunks.sqlite")) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='retention_pending'").fetchone():
            return None
        return set(db.execute("SELECT kind, key FROM retention_pending"))


def keyword_docs(root):
    with closing(sqlite3.connect(root / "keyword.sqlite")) as db:
        return {row[0] for row in db.execute("SELECT doc_key FROM entries")}


def vector_ids(root):
    return {item.chunk_id for item in LanceVectorIndex(root / "vectors", FakeEmbedder()).search("bulletin", k=100).items}


def snapshot_rows(root, sha):
    with closing(sqlite3.connect(root / "manifest.sqlite")) as db:
        return db.execute("SELECT count(*) FROM snapshots WHERE sha256=?", (sha,)).fetchone()[0]


def parsed_rows(root, sha):
    with closing(sqlite3.connect(root / "manifest.sqlite")) as db:
        return db.execute("SELECT count(*) FROM parsed WHERE sha256=?", (sha,)).fetchone()[0]


def test_setup_has_the_vectors_and_keywords_the_tests_expect(local):
    local.close()
    assert {EXPIRED_VECTOR, STALE_ONLY_VECTOR, SHARED_VECTOR} <= vector_ids(local.root)
    assert EXPIRED_KEY in keyword_docs(local.root)


def test_keyword_failure_after_block_delete_is_finished_next_round(local, monkeypatch):
    root = local.root
    monkeypatch.setattr(SqliteKeywordIndex, "delete", boom)
    with pytest.raises(Boom):
        go(local)
    # The blocks are gone and recorded for cleanup; the keyword index still names a removed document,
    # exactly the state publication refuses.
    assert pending(root) == {("keyword", EXPIRED_KEY), ("vector", EXPIRED_VECTOR), ("vector", STALE_ONLY_VECTOR)}
    assert EXPIRED_KEY in keyword_docs(root)
    with pytest.raises(StateError, match="关键词索引包含未发布的块"):
        _check_schema(root)

    monkeypatch.undo()
    report = go(local)
    assert EXPIRED_KEY not in keyword_docs(root) and "jp-jma:recent" in keyword_docs(root)
    found = vector_ids(root)
    assert EXPIRED_VECTOR not in found and STALE_ONLY_VECTOR not in found
    assert {SHARED_VECTOR, "jp-jma:recent#row=1"} <= found  # still referenced by current versions
    assert report["vectors"]["deleted"] == 2
    assert pending(root) is None  # all done: the record (and its table) is gone
    _check_schema(root)  # the state check now passes


def test_vector_failure_after_block_delete_is_finished_next_round(local, monkeypatch):
    root = local.root
    monkeypatch.setattr(db_retention, "delete_vectors", boom)
    with pytest.raises(Boom):
        go(local)
    # Keyword entries are already clean, but the record stays until the vectors are clean too.
    assert EXPIRED_KEY not in keyword_docs(root)
    assert pending(root) == {("keyword", EXPIRED_KEY), ("vector", EXPIRED_VECTOR), ("vector", STALE_ONLY_VECTOR)}
    assert {EXPIRED_VECTOR, STALE_ONLY_VECTOR} <= vector_ids(root)

    monkeypatch.undo()
    report = go(local)
    found = vector_ids(root)
    assert EXPIRED_VECTOR not in found and STALE_ONLY_VECTOR not in found and SHARED_VECTOR in found
    assert report["vectors"]["deleted"] == 2 and pending(root) is None
    _check_schema(root)


def test_a_chunk_published_again_in_between_is_not_deleted_by_the_leftover_cleanup(local, monkeypatch):
    root = local.root
    monkeypatch.setattr(SqliteKeywordIndex, "delete", boom)
    with pytest.raises(Boom):
        go(local)
    monkeypatch.undo()
    # The expired bulletin comes back as a current document with the same document key and chunk id.
    chunk = make_chunk(EXPIRED_KEY, sha_of("again"), "row=1", "expired again row=1")
    with closing(SqliteChunkStore(root / "chunks.sqlite")) as chunks, closing(
        SqliteKeywordIndex(root / "keyword.sqlite")
    ) as keyword:
        chunks.put_document(make_document(EXPIRED_KEY, sha_of("again")), [chunk], publish=True)
        keyword.add([chunk])
    go(local)
    assert EXPIRED_KEY in keyword_docs(root) and EXPIRED_VECTOR in vector_ids(root)
    assert STALE_ONLY_VECTOR not in vector_ids(root) and pending(root) is None


def test_failure_before_the_block_delete_keeps_the_age_basis_of_old_versions(local, monkeypatch):
    root = local.root
    old_doc, old_sha, _ = local.docs["stale", "old"]
    monkeypatch.setattr(SqliteChunkStore, "delete_versions", boom)
    with pytest.raises(Boom):
        go(local)
    # Nothing was deleted, in particular not the snapshot rows that date the old version ...
    assert snapshot_rows(root, old_sha) == 1
    with closing(SqliteChunkStore(root / "chunks.sqlite")) as chunks:
        assert chunks.get_document(old_doc, old_sha) is not None
    assert pending(root) is None
    # ... nor its parse record, which goes with the snapshot rows after the blocks.
    assert parsed_rows(root, old_sha) == 1

    monkeypatch.undo()
    report = go(local)
    assert report["superseded"]["unknown_superseded"] == 1  # only "mystery", never the old stale version
    assert report["superseded"]["sources"]["jp-test"]["versions"] == 1
    with closing(SqliteChunkStore(root / "chunks.sqlite")) as chunks:
        assert chunks.get_document(old_doc, old_sha) is None
    assert snapshot_rows(root, old_sha) == 0 and parsed_rows(root, old_sha) == 0  # dropped after the blocks went
    assert STALE_ONLY_VECTOR not in vector_ids(root)


def test_without_deletions_no_pending_table_is_created(tmp_path):
    w = World(tmp_path / "quiet")
    w.version("jp-test", "fresh", "old", ago(24 * 30), current=False)
    w.version("jp-test", "fresh", "new", ago(10))  # superseded only 10 h ago: nothing is due
    report = go(w)
    assert report["superseded"]["waiting_superseded"] == 1 and report["vectors"] == {"deleted": 0}
    assert pending(w.root) is None


def test_switch_off_never_creates_the_pending_table_in_a_cloud_round(world, monkeypatch):
    monkeypatch.delenv("R2_DB_RETENTION", raising=False)
    expiring(world)
    invoke(world, "first")
    invoke(world, "second")
    kept = restored(world, "kept")
    with closing(sqlite3.connect(kept / "chunks.sqlite")) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='retention_pending'").fetchone()
    assert count(kept, "documents") == 3
    released(world)


def test_switch_on_cloud_round_leaves_no_record_behind_and_usage_still_reads_it(world, monkeypatch):
    expiring(world)
    invoke(world, "first")
    monkeypatch.setenv("R2_DB_RETENTION", "on")
    result, _ = invoke(world, "second")
    assert result["status"] == "completed"
    gen2 = restored(world, "gen2")
    assert count(gen2, "documents") == 0
    assert pending(gen2) is None  # db_usage accepts only its known tables, so no leftover (empty) table
    assert "db" in cloud.usage(db=True, work_dir=world.tmp / "usage-after")
    released(world)
