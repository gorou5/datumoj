"""LanceDB 向量索引（VectorIndex）。按 chunk_id + 文本哈希只嵌入新的或变了的块。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import lancedb
import pyarrow as pa

from datumoj.core.contracts import Embedder, IndexResult, ModelMismatch, ScoredChunk
from datumoj.core.models import Chunk

_TABLE = "chunks"
_META = "_meta.json"


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


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
            pa.field("vector", pa.list_(pa.float32(), embedder.dim)),
        ])
        self._db = lancedb.connect(str(root))
        if _TABLE in self._db.table_names():
            self._table = self._db.open_table(_TABLE)
        else:
            self._table = self._db.create_table(_TABLE, schema=self._schema)

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
        todo = {i: c for i, c in todo.items() if existing.get(i) != _text_hash(c.text)}
        pending = list(todo.values())
        for i in range(0, len(pending), self.batch_size):
            batch = pending[i : i + self.batch_size]
            vectors = self.embedder.embed([c.text for c in batch])
            if len(vectors) != len(batch):
                raise ValueError("嵌入结果数量和输入不一致")
            ids = ", ".join(_lit(c.chunk_id) for c in batch)
            self._table.delete(f"chunk_id IN ({ids})")
            self._table.add([
                {"chunk_id": c.chunk_id, "doc_key": c.doc_key, "source_id": c.source_id,
                 "text_hash": _text_hash(c.text), "vector": v}
                for c, v in zip(batch, vectors, strict=True)
            ])
        return len(pending)

    def delete(self, doc_key: str) -> None:
        self._table.delete(f"doc_key = {_lit(doc_key)}")

    def clear(self) -> None:
        self._db.drop_table(_TABLE)
        self._table = self._db.create_table(_TABLE, schema=self._schema)

    def search(self, query: str, k: int = 10, source_ids: Sequence[str] | None = None) -> IndexResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("查询不能为空白")
        if k < 1:
            raise ValueError("k 必须 ≥ 1")
        if source_ids is not None and not source_ids:
            return IndexResult()
        vector = self.embedder.embed([query])[0]
        q = self._table.search(vector).metric("cosine").limit(k)
        if source_ids is not None:
            q = q.where(f"source_id IN ({', '.join(_lit(s) for s in source_ids)})", prefilter=True)
        rows = q.to_list()
        return IndexResult(items=[
            ScoredChunk(chunk_id=r["chunk_id"], doc_key=r["doc_key"], score=1.0 - float(r["_distance"]))
            for r in rows
        ])
