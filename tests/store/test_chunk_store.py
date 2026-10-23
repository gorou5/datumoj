"""SQLite ChunkStore 的合格测试、持久化和原子发布。"""

import sqlite3

import pytest

from datumoj.core.models import SnapshotOrder
from datumoj.store.chunks import SqliteChunkStore
from datumoj.testing.contract_suites import ChunkStoreSuite, _doc_v
from datumoj.testing.samples import T0, sha


class TestSqliteChunkStore(ChunkStoreSuite):
    @pytest.fixture
    def store(self, tmp_path):
        self.path = tmp_path / "chunks.db"
        self.opened = []
        store = SqliteChunkStore(self.path)
        self.opened.append(store)
        yield store
        for connection in self.opened:
            connection.close()

    def reopen_store(self, store):
        store.close()
        reopened = SqliteChunkStore(self.path)
        self.opened.append(reopened)
        return reopened


def test_publication_compares_persisted_watermark_across_connections(tmp_path):
    path = tmp_path / "shared.db"
    first, second = SqliteChunkStore(path), SqliteChunkStore(path)
    old_doc, old = _doc_v(sha(b"old"), ["旧"])
    new_doc, new = _doc_v(sha(b"new"), ["新"])
    first.put_document(old_doc, old, publish=False)
    second.put_document(new_doc, new, publish=False)
    newer = SnapshotOrder(fetched_at=T0, seq=2)
    assert second.publish_current(new_doc.doc_key, new_doc.snapshot_sha256, newer)
    assert not first.publish_current(old_doc.doc_key, old_doc.snapshot_sha256, SnapshotOrder(fetched_at=T0, seq=1))
    assert first.get_document(old_doc.doc_key) == new_doc
    assert first.current_order(old_doc.doc_key) == newer
    first.close()
    second.close()


def test_failed_watermark_write_rolls_back_current_switch(tmp_path):
    path = tmp_path / "atomic.db"
    store = SqliteChunkStore(path)
    old_doc, old = _doc_v(sha(b"old"), ["旧"])
    new_doc, new = _doc_v(sha(b"new"), ["新"])
    store.put_document(old_doc, old, publish=False)
    store.put_document(new_doc, new, publish=False)
    original = SnapshotOrder(fetched_at=T0, seq=1)
    store.publish_current(old_doc.doc_key, old_doc.snapshot_sha256, original)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER reject_watermark BEFORE INSERT ON published_orders "
                   "BEGIN SELECT RAISE(ABORT, 'publish failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="publish failed"):
        store.publish_current(new_doc.doc_key, new_doc.snapshot_sha256, SnapshotOrder(fetched_at=T0, seq=2))
    assert store.get_document(old_doc.doc_key) == old_doc
    assert store.current_order(old_doc.doc_key) == original
    assert store.chunks_for(new_doc.doc_key, new_doc.snapshot_sha256) == new
    store.close()
