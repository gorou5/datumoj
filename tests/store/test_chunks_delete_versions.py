import sqlite3
from contextlib import closing

import pytest

from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.testing.samples import make_chunk, make_document, sha


def has_pending_table(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("SELECT 1 FROM sqlite_master WHERE name='retention_pending'").fetchone() is not None


def put(store, key, label, locators, *, publish):
    digest = sha(label.encode())
    store.put_document(make_document(key, digest),
                       [make_chunk(key, digest, loc, f"{label} {loc}") for loc in locators], publish=publish)
    return digest


def test_delete_versions_returns_only_chunk_ids_no_longer_current(tmp_path):
    with closing(SqliteChunkStore(tmp_path / "c.sqlite")) as store:
        old = put(store, "jp-test:a", "old", ("row=1", "row=2"), publish=False)
        new = put(store, "jp-test:a", "new", ("row=1",), publish=True)
        gone = store.delete_versions([("jp-test:a", old)])
        assert gone == ["jp-test:a#row=2"]  # row=1 is still a current chunk id
        assert store.get_document("jp-test:a", old) is None and store.get_document("jp-test:a").snapshot_sha256 == new
        assert [c.chunk_id for c in store.iter_chunks()] == ["jp-test:a#row=1"]


def test_expired_keys_lose_current_and_order_rows(tmp_path):
    with closing(SqliteChunkStore(tmp_path / "c.sqlite")) as store:
        digest = put(store, "jp-test:b", "only", ("row=1",), publish=True)
        assert store.delete_versions([("jp-test:b", digest)], ["jp-test:b"]) == ["jp-test:b#row=1"]
        assert store.get_document("jp-test:b") is None and store.current_order("jp-test:b") is None
        assert list(store.iter_chunks()) == []


def test_current_version_outside_expired_keys_is_refused_without_deleting_anything(tmp_path):
    with closing(SqliteChunkStore(tmp_path / "c.sqlite")) as store:
        old = put(store, "jp-test:d", "old", ("row=1", "row=2"), publish=False)
        new = put(store, "jp-test:d", "new", ("row=3",), publish=True)
        with pytest.raises(ValueError, match="current_version_not_expired"):
            store.delete_versions([("jp-test:d", old), ("jp-test:d", new)])  # the old one first: still untouched
        assert store.get_document("jp-test:d", old) is not None and store.get_document("jp-test:d") is not None
        assert [c.chunk_id for c in store.chunks_for("jp-test:d", old)] == ["jp-test:d#row=1", "jp-test:d#row=2"]
        assert store.pending_cleanup() == ([], [])
        # Naming the key as expired makes deleting its current version legitimate.
        assert store.delete_versions([("jp-test:d", old), ("jp-test:d", new)], ["jp-test:d"]) == [
            "jp-test:d#row=1", "jp-test:d#row=2", "jp-test:d#row=3"]


def test_pending_cleanup_survives_reopening_and_is_cleared_only_on_request(tmp_path):
    path = tmp_path / "c.sqlite"
    with closing(SqliteChunkStore(path)) as store:
        old = put(store, "jp-test:e", "old", ("row=1", "row=2"), publish=False)
        put(store, "jp-test:e", "new", ("row=1",), publish=True)
        put(store, "jp-test:f", "only", ("row=1",), publish=True)
        assert store.pending_cleanup() == ([], [])  # no table yet, and reading does not create one
        assert store.delete_versions([("jp-test:e", old), ("jp-test:f", sha(b"only"))], ["jp-test:f"]) == [
            "jp-test:e#row=2", "jp-test:f#row=1"]
    with closing(SqliteChunkStore(path)) as store:  # a later round
        assert store.pending_cleanup() == (["jp-test:f"], ["jp-test:e#row=2", "jp-test:f#row=1"])
        store.clear_pending(["jp-test:f"], ["jp-test:e#row=2"])
        assert store.pending_cleanup() == ([], ["jp-test:f#row=1"])
        assert has_pending_table(path)
        store.clear_pending(chunk_ids=["jp-test:f#row=1"])
        assert not has_pending_table(path)  # empty again: the table goes, as db_usage knows no such table


def test_vector_entries_that_are_current_again_are_dropped_but_keyword_entries_wait_for_the_caller(tmp_path):
    path = tmp_path / "c.sqlite"
    with closing(SqliteChunkStore(path)) as store:
        digest = put(store, "jp-test:g", "only", ("row=1",), publish=True)
        store.delete_versions([("jp-test:g", digest)], ["jp-test:g"])
        assert has_pending_table(path)
        put(store, "jp-test:g", "again", ("row=1",), publish=True)  # re-published under the same ids
        # The vector is current again and never returned; the keyword record stays until the caller has
        # rewritten the keyword entries (publication replaces them after committing the version).
        assert store.pending_cleanup() == (["jp-test:g"], [])
        assert store.pending_cleanup() == (["jp-test:g"], []) and has_pending_table(path)
        store.clear_pending(["jp-test:g"], ["jp-test:g#row=1"])
        assert not has_pending_table(path)
        store.clear_pending(["jp-test:g"], ["jp-test:g#row=1"])  # clearing what is gone is harmless


def test_pending_table_is_created_by_deleting_not_by_opening(tmp_path):
    path = tmp_path / "c.sqlite"
    with closing(SqliteChunkStore(path)) as store:
        put(store, "jp-test:h", "x", ("row=1",), publish=True)
    assert not has_pending_table(path)


def test_read_only_view_cannot_delete(tmp_path):
    with closing(SqliteChunkStore(tmp_path / "c.sqlite")) as store:
        put(store, "jp-test:c", "x", ("row=1",), publish=True)
    with closing(SqliteChunkStore.open_readonly(tmp_path / "c.sqlite")) as view, pytest.raises(PermissionError):
        view.delete_versions([("jp-test:c", sha(b"x"))])
