"""LanceDB 向量索引（VectorIndex）。按 chunk_id + 文本和日期元数据哈希跳过未变化的块。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import lancedb
import pyarrow as pa

from datumoj.core.contracts import Embedder, IndexResult, ModelMismatch, ScoredChunk
from datumoj.core.models import Chunk

_TABLE = "chunks"
_META = "_meta.json"


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _chunk_hash(chunk: Chunk) -> str:
    metadata = [chunk.text, chunk.published_at.isoformat() if chunk.published_at else None,
                chunk.effective_at.isoformat() if chunk.effective_at else None]
    return hashlib.sha256(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class LanceVectorIndex:
    """``path`` 是索引目录。目录里记录建索引时的模型名和维度，换模型或维度打开会抛 ModelMismatch。

    add 每嵌入完一批（``batch_size`` 块）就落盘，中途失败后重跑会跳过已完成的块。
    """

    def __init__(self, path: str | Path, embedder: Embedder, batch_size: int = 64) -> None:
        if batch_size < 1:
            raise ValueError("batch_size 必须 ≥ 1")
        self.embedder = embedder
        self.batch_size = batch_size
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        self._check_meta(root / _META)
        self._schema = pa.schema([
            pa.field("chunk_id", pa.string()),
            pa.field("doc_key", pa.string()),
            pa.field("source_id", pa.string()),
            pa.field("text_hash", pa.string()),
            pa.field("published_at", pa.string()),
            pa.field("effective_at", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), embedder.dim)),
        ])
        self._db = lancedb.connect(str(root))
        if _TABLE in self._db.table_names():
            self._table = self._db.open_table(_TABLE)
        else:
            self._table = self._db.create_table(_TABLE, schema=self._schema)
        self._has_date_fields = {"published_at", "effective_at"} <= set(self._table.schema.names)

    def _check_meta(self, meta_path: Path) -> None:
        now = {"model": self.embedder.model, "dim": self.embedder.dim}
        if not meta_path.exists():
            meta_path.write_text(json.dumps(now), encoding="utf-8")
            return
        old = json.loads(meta_path.read_text(encoding="utf-8"))
        if old != now:
            raise ModelMismatch(f"索引由 {old['model']}（{old['dim']} 维）建立，不能用 {now['model']}（{now['dim']} 维）打开")

    def _existing_hashes(self) -> dict[str, str]:
        t = self._table.to_arrow().select(["chunk_id", "text_hash"])
        return dict(zip(t["chunk_id"].to_pylist(), t["text_hash"].to_pylist(), strict=True))

    def add(self, chunks: Sequence[Chunk]) -> int:
        existing = self._existing_hashes()
        todo = {c.chunk_id: c for c in chunks}  # 同一 chunk_id 重复时取最后一个
        todo = {i: c for i, c in todo.items() if existing.get(i) != _chunk_hash(c)}
        pending = list(todo.values())
        for i in range(0, len(pending), self.batch_size):
            batch = pending[i : i + self.batch_size]
            vectors = self.embedder.embed([c.text for c in batch])
            if len(vectors) != len(batch):
                raise ValueError("嵌入结果数量和输入不一致")
            ids = ", ".join(_lit(c.chunk_id) for c in batch)
            self._table.delete(f"chunk_id IN ({ids})")
            rows = []
            for c, v in zip(batch, vectors, strict=True):
                row = {"chunk_id": c.chunk_id, "doc_key": c.doc_key, "source_id": c.source_id,
                       "text_hash": _chunk_hash(c), "vector": v}
                if self._has_date_fields:
                    row.update(
                        published_at=c.published_at.isoformat() if c.published_at else None,
                        effective_at=c.effective_at.isoformat() if c.effective_at else None,
                    )
                rows.append(row)
            self._table.add(rows)
        return len(pending)

    def delete(self, doc_key: str) -> None:
        self._table.delete(f"doc_key = {_lit(doc_key)}")

    def clear(self) -> None:
        self._db.drop_table(_TABLE)
        self._table = self._db.create_table(_TABLE, schema=self._schema)
        self._has_date_fields = True

    def search(self, query: str, k: int = 10, source_ids: Sequence[str] | None = None) -> IndexResult:
        return self._search(query, k=k, source_ids=source_ids)

    def search_filtered(
        self,
        query: str,
        k: int = 10,
        source_ids: Sequence[str] | None = None,
        *,
        date_range: tuple[date | None, date | None] | None = None,
        doc_keys: Sequence[str] | None = None,
    ) -> IndexResult:
        return self._search(query, k=k, source_ids=source_ids, date_range=date_range, doc_keys=doc_keys)

    def _search(
        self,
        query: str,
        k: int = 10,
        source_ids: Sequence[str] | None = None,
        *,
        date_range: tuple[date | None, date | None] | None = None,
        doc_keys: Sequence[str] | None = None,
    ) -> IndexResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("查询不能为空白")
        if k < 1:
            raise ValueError("k 必须 ≥ 1")
        if (source_ids is not None and not source_ids) or (doc_keys is not None and not doc_keys):
            return IndexResult()
        vector = self.embedder.embed([query])[0]
        q = self._table.search(vector).metric("cosine").limit(k)
        if source_ids is not None:
            q = q.where(f"source_id IN ({', '.join(_lit(s) for s in source_ids)})", prefilter=True)
        if doc_keys is not None:
            q = q.where(f"doc_key IN ({', '.join(_lit(key) for key in doc_keys)})", prefilter=True)
        if date_range is not None and self._has_date_fields:
            lo, hi = date_range
            clauses = []
            if lo is not None and hi is not None:
                clauses.extend([
                    f"published_at BETWEEN {_lit(lo.isoformat())} AND {_lit(hi.isoformat())}",
                    f"effective_at BETWEEN {_lit(lo.isoformat())} AND {_lit(hi.isoformat())}",
                ])
            elif lo is not None:
                clauses.extend([f"published_at >= {_lit(lo.isoformat())}", f"effective_at >= {_lit(lo.isoformat())}"])
            elif hi is not None:
                clauses.extend([f"published_at <= {_lit(hi.isoformat())}", f"effective_at <= {_lit(hi.isoformat())}"])
            if clauses:
                clauses.append("(published_at IS NULL AND effective_at IS NULL)")
                q = q.where("(" + " OR ".join(clauses) + ")", prefilter=True)
        rows = q.to_list()
        return IndexResult(items=[
            ScoredChunk(chunk_id=r["chunk_id"], doc_key=r["doc_key"], score=1.0 - float(r["_distance"]))
            for r in rows
        ])
