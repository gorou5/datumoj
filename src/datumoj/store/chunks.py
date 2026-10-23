"""SQLite 文档与检索块存储（ChunkStore）。同一 doc_key 的新快照成为当前版本，旧快照保留。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from datumoj.core.contracts import NotFoundError
from datumoj.core.models import Chunk, Document, SnapshotOrder

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
    doc_key TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    document_json TEXT NOT NULL,
    PRIMARY KEY (doc_key, snapshot_sha256)
);
CREATE TABLE IF NOT EXISTS current_versions (
    doc_key TEXT PRIMARY KEY,
    snapshot_sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS published_orders (
    doc_key TEXT PRIMARY KEY,
    order_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    doc_key TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    ord INTEGER NOT NULL,
    source_id TEXT NOT NULL,
    chunk_json TEXT NOT NULL,
    PRIMARY KEY (doc_key, snapshot_sha256, chunk_id)
);
CREATE INDEX IF NOT EXISTS chunks_chunk_id ON chunks(chunk_id);
"""


class SqliteChunkStore:
    """``path`` 是 SQLite 文件路径，或 ``:memory:``。"""

    def __init__(self, path: str | Path, timeout: float = 30.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout 必须 > 0")
        db_path = str(path)
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, timeout=timeout, isolation_level=None)
        try:
            self._init_schema()
        except BaseException:
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    def _init_schema(self) -> None:
        with self._tx() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            row = db.execute("SELECT version FROM schema_version").fetchone() if "schema_version" in tables else None
            if tables and (row is None or row[0] not in (1, SCHEMA_VERSION)):
                found = "没有版本记录" if row is None else f"版本 {row[0]}"
                raise ValueError(f"chunk store schema 版本不符：代码要求 {SCHEMA_VERSION}，库里{found}")
            for stmt in _SCHEMA.split(";\n"):
                if stmt.strip():
                    db.execute(stmt)
            if not tables:
                db.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
            elif row[0] == 1:
                db.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        else:
            self._db.execute("COMMIT")

    def put_document(self, document: Document, chunks: Sequence[Chunk], *, publish: bool = True) -> None:
        ids: set[str] = set()
        for c in chunks:
            if c.doc_key != document.doc_key or c.snapshot_sha256 != document.snapshot_sha256:
                raise ValueError(f"块 {c.chunk_id} 不属于文档 {document.doc_key} 的这个快照")
            if c.chunk_id in ids:
                raise ValueError(f"chunk_id 重复：{c.chunk_id}")
            ids.add(c.chunk_id)
        key = (document.doc_key, document.snapshot_sha256)
        with self._tx() as db:
            db.execute("DELETE FROM chunks WHERE doc_key=? AND snapshot_sha256=?", key)
            db.execute(
                "INSERT INTO documents(doc_key, snapshot_sha256, document_json) VALUES(?,?,?) "
                "ON CONFLICT(doc_key, snapshot_sha256) DO UPDATE SET document_json=excluded.document_json",
                (*key, document.model_dump_json()),
            )
            db.executemany(
                "INSERT INTO chunks(doc_key, snapshot_sha256, chunk_id, ord, source_id, chunk_json) VALUES(?,?,?,?,?,?)",
                [(*key, c.chunk_id, i, c.source_id, c.model_dump_json()) for i, c in enumerate(chunks)],
            )
            if publish:
                db.execute(
                    "INSERT INTO current_versions(doc_key, snapshot_sha256) VALUES(?,?) "
                    "ON CONFLICT(doc_key) DO UPDATE SET snapshot_sha256=excluded.snapshot_sha256",
                    key,
                )

    def current_order(self, doc_key: str) -> SnapshotOrder | None:
        row = self._db.execute("SELECT order_json FROM published_orders WHERE doc_key=?", (doc_key,)).fetchone()
        return SnapshotOrder.model_validate_json(row[0]) if row else None

    def publish_current(self, doc_key: str, snapshot_sha256: str, order: SnapshotOrder) -> bool:
        with self._tx() as db:
            if self.get_document(doc_key, snapshot_sha256) is None:
                raise NotFoundError(f"没有文档版本 {doc_key} / {snapshot_sha256}")
            current = self.current_order(doc_key)
            if current is not None and order <= current:
                return False
            db.execute(
                "INSERT INTO current_versions(doc_key, snapshot_sha256) VALUES(?,?) "
                "ON CONFLICT(doc_key) DO UPDATE SET snapshot_sha256=excluded.snapshot_sha256",
                (doc_key, snapshot_sha256),
            )
            db.execute(
                "INSERT INTO published_orders(doc_key, order_json) VALUES(?,?) "
                "ON CONFLICT(doc_key) DO UPDATE SET order_json=excluded.order_json",
                (doc_key, order.model_dump_json()),
            )
            return True

    def get_document(self, doc_key: str, snapshot_sha256: str | None = None) -> Document | None:
        if snapshot_sha256 is None:
            row = self._db.execute(
                "SELECT d.document_json FROM documents d JOIN current_versions v "
                "ON v.doc_key=d.doc_key AND v.snapshot_sha256=d.snapshot_sha256 WHERE d.doc_key=?",
                (doc_key,),
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT document_json FROM documents WHERE doc_key=? AND snapshot_sha256=?", (doc_key, snapshot_sha256)
            ).fetchone()
        return Document.model_validate_json(row[0]) if row else None

    def get_chunk(self, chunk_id: str, snapshot_sha256: str | None = None) -> Chunk | None:
        if snapshot_sha256 is None:
            row = self._db.execute(
                "SELECT c.chunk_json FROM chunks c JOIN current_versions v "
                "ON v.doc_key=c.doc_key AND v.snapshot_sha256=c.snapshot_sha256 WHERE c.chunk_id=?",
                (chunk_id,),
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT chunk_json FROM chunks WHERE chunk_id=? AND snapshot_sha256=?", (chunk_id, snapshot_sha256)
            ).fetchone()
        return Chunk.model_validate_json(row[0]) if row else None

    def chunks_for(self, doc_key: str, snapshot_sha256: str | None = None) -> list[Chunk]:
        if snapshot_sha256 is None:
            row = self._db.execute("SELECT snapshot_sha256 FROM current_versions WHERE doc_key=?", (doc_key,)).fetchone()
            if row is None:
                return []
            snapshot_sha256 = row[0]
        rows = self._db.execute(
            "SELECT chunk_json FROM chunks WHERE doc_key=? AND snapshot_sha256=? ORDER BY ord", (doc_key, snapshot_sha256)
        )
        return [Chunk.model_validate_json(r[0]) for r in rows]

    def iter_chunks(self, source_ids: Sequence[str] | None = None) -> Iterator[Chunk]:
        sql = (
            "SELECT c.chunk_json FROM chunks c JOIN current_versions v "
            "ON v.doc_key=c.doc_key AND v.snapshot_sha256=c.snapshot_sha256"
        )
        params: list[str] = []
        if source_ids is not None:
            sql += f" WHERE c.source_id IN ({','.join('?' * len(source_ids))})"
            params = list(source_ids)
        for (blob,) in self._db.execute(sql + " ORDER BY c.doc_key, c.ord", params).fetchall():
            yield Chunk.model_validate_json(blob)
