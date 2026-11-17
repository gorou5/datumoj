from contextlib import closing

import pytest

from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.testing.samples import make_chunk, make_document, sha


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


def test_read_only_view_cannot_delete(tmp_path):
    with closing(SqliteChunkStore(tmp_path / "c.sqlite")) as store:
        put(store, "jp-test:c", "x", ("row=1",), publish=True)
    with closing(SqliteChunkStore.open_readonly(tmp_path / "c.sqlite")) as view, pytest.raises(PermissionError):
        view.delete_versions([("jp-test:c", sha(b"x"))])
