"""LanceDB 向量索引（VectorIndex）。按 chunk_id + 文本和日期元数据哈希跳过未变化的块。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path

import lancedb
import pyarrow as pa

from datumoj.rikolto.core.contracts import Embedder, IndexResult, ModelMismatch, ScoredChunk
from datumoj.rikolto.core.errors import safe_reason
from datumoj.rikolto.core.models import Chunk

_TABLE = "chunks"
_META = "_meta.json"
_MAX_FILTER_IDS = 100
_COPY_BATCH_SIZE = 500
_LOG = logging.getLogger(__name__)


def maintain_vectors(path: str | Path, retain: timedelta | None = None) -> dict:
    """维护本地表，不需要嵌入服务。每张代次表独立清理，保留所有表及其最新版本。"""
    if retain is None:
        retain = timedelta(hours=float(os.environ.get("DATUMOJ_VECTOR_RETAIN_HOURS", "1")))
    if retain < timedelta(0):
        raise ValueError("向量版本保留期不能为负")
    root = Path(path)
    empty = {"versions": 0, "data_files": 0, "bytes": 0}
    if not root.exists():
        return {"before": empty.copy(), "after": empty.copy()}
    if root.is_symlink() or any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("向量目录不能包含符号链接")
    db = lancedb.connect(str(root))
    tables = [db.open_table(name) for name in db.list_tables().tables
              if re.fullmatch(r"chunks(?:_g\d+)?", name)]

    def stats():
        files = [p for p in root.rglob("*") if p.is_file()]
        return {"versions": sum(len(t.list_versions()) for t in tables),
                "data_files": sum(p.suffix == ".lance" and p.parent.name == "data" for p in files),
                "bytes": sum(p.stat().st_size for p in files)}

    before = stats()
    for table in tables:
        # Keep Lance's in-flight file protection (delete_unverified=False).
        table.optimize(cleanup_older_than=retain)
    return {"before": before, "after": stats()}


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
        self._meta_path = root / _META
        self._meta_was_missing = not self._meta_path.exists()
        self._check_meta(self._meta_path)
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
        table_name = self._table_name(self._current_generation)
        if table_name in self._db.list_tables().tables:
            self._table = self._db.open_table(table_name)
        elif self._meta_was_missing:
            self._table = self._db.create_table(table_name, schema=self._schema)
        else:
            raise ValueError(f"向量索引元数据指向的代不存在：{table_name}")
        self._has_date_fields = {"published_at", "effective_at"} <= set(self._table.schema.names)
        self._candidate_table = None
        self._candidate_generation = None
        self._candidate_has_date_fields = False

    def _check_meta(self, meta_path: Path) -> None:
        now = {"model": self.embedder.model, "dim": self.embedder.dim}
        if not meta_path.exists():
            self._meta = now
            self._save_meta(0)
            return
        old = json.loads(meta_path.read_text(encoding="utf-8"))
        if old.get("model") != now["model"] or old.get("dim") != now["dim"]:
            raise ModelMismatch(
                f"索引由 {old.get('model')}（{old.get('dim')} 维）建立，不能用 {now['model']}（{now['dim']} 维）打开"
            )
        generation = old.get("generation", 0)
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("向量索引元数据中的 generation 必须是非负整数")
        self._meta = old
        self._current_generation = generation

    @staticmethod
    def _table_name(generation: int) -> str:
        return _TABLE if generation == 0 else f"{_TABLE}_g{generation}"

    @property
    def _write_table(self):
        return self._candidate_table if self._candidate_table is not None else self._table

    @property
    def _write_has_date_fields(self) -> bool:
        return self._candidate_has_date_fields if self._candidate_table is not None else self._has_date_fields

    def _save_meta(self, generation: int) -> None:
        data = {"model": self.embedder.model, "dim": self.embedder.dim, "generation": generation}
        temporary = self._meta_path.with_name(self._meta_path.name + ".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.replace(self._meta_path)
        self._meta = data
        self._current_generation = generation

    def _existing_hashes(self, chunk_ids: Sequence[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        table = self._write_table
        for offset in range(0, len(chunk_ids), _MAX_FILTER_IDS):
            ids = chunk_ids[offset : offset + _MAX_FILTER_IDS]
            condition = ", ".join(_lit(chunk_id) for chunk_id in ids)
            batches = table.search().where(f"chunk_id IN ({condition})").select(
                ["chunk_id", "text_hash"]
            ).to_batches(batch_size=_MAX_FILTER_IDS)
            for batch in batches:
                rows = batch.to_pylist()
                found.update((row["chunk_id"], row["text_hash"]) for row in rows)
        return found

    def add(self, chunks: Sequence[Chunk]) -> int:
        todo = {c.chunk_id: c for c in chunks}  # 同一 chunk_id 重复时取最后一个
        existing = self._existing_hashes(list(todo))
        todo = {i: c for i, c in todo.items() if existing.get(i) != _chunk_hash(c)}
        pending = list(todo.values())
        batch_size = min(self.batch_size, _MAX_FILTER_IDS)
        for i in range(0, len(pending), batch_size):
            batch = pending[i : i + batch_size]
            vectors = self.embedder.embed([c.text for c in batch])
            if len(vectors) != len(batch):
                raise ValueError("嵌入结果数量和输入不一致")
            ids = ", ".join(_lit(c.chunk_id) for c in batch)
            table = self._write_table
            table.delete(f"chunk_id IN ({ids})")
            rows = []
            for c, v in zip(batch, vectors, strict=True):
                row = {"chunk_id": c.chunk_id, "doc_key": c.doc_key, "source_id": c.source_id,
                       "text_hash": _chunk_hash(c), "vector": v}
                if self._write_has_date_fields:
                    row.update(
                        published_at=c.published_at.isoformat() if c.published_at else None,
                        effective_at=c.effective_at.isoformat() if c.effective_at else None,
                    )
                rows.append(row)
            table.add(rows)
        return len(pending)

    def delete(self, doc_key: str) -> None:
        self._write_table.delete(f"doc_key = {_lit(doc_key)}")

    def clear(self) -> None:
        if self._candidate_table is not None:
            self.abort_generation()
        self._table.delete("chunk_id IS NOT NULL")

    def maintain(self, retain: timedelta | None = None) -> dict:
        if self._candidate_table is not None:
            raise RuntimeError("不能维护未完成的向量代")
        result = maintain_vectors(self._meta_path.parent, retain)
        self._table.checkout_latest()
        return result

    def count_rows(self) -> int:
        """当前写入代的覆盖行数，供有上限的部分重建校验。"""
        return self._write_table.count_rows()

    def begin_generation(self, *, copy_current: bool = False) -> None:
        """准备候选代；查询仍指向当前代，写入只进入候选代。"""
        if self._candidate_table is not None:
            raise RuntimeError("向量索引已有未完成的重建代")
        self._cleanup_generations()
        generation = self._current_generation + 1
        name = self._table_name(generation)
        if name in self._db.list_tables().tables:
            self._db.drop_table(name)
        schema = self._table.schema if copy_current else self._schema
        candidate = self._db.create_table(name, schema=schema)
        self._candidate_table = candidate
        self._candidate_generation = generation
        self._candidate_has_date_fields = {"published_at", "effective_at"} <= set(schema.names)
        if not copy_current:
            return
        try:
            batches = self._table.search().select(self._table.schema.names).to_batches(batch_size=_COPY_BATCH_SIZE)
            for batch in batches:
                candidate.add(batch)
        except Exception:  # 复制候选代失败须先撤销候选代，再原样抛出。
            self.abort_generation()
            raise

    def finish_generation(self, expected_count: int) -> None:
        """校验候选代后，以元数据原子切换，再保留当前代和上一代。"""
        if self._candidate_table is None or self._candidate_generation is None:
            raise RuntimeError("没有待提交的向量索引代")
        if expected_count < 0:
            raise ValueError("expected_count 必须非负")
        table = self._candidate_table
        if self._meta.get("model") != self.embedder.model or self._meta.get("dim") != self.embedder.dim:
            raise ModelMismatch("候选向量代的模型或维度和当前 embedder 不一致")
        vector_type = table.schema.field("vector").type
        if not pa.types.is_fixed_size_list(vector_type) or vector_type.list_size != self.embedder.dim:
            raise ModelMismatch(f"候选向量代维度和模型不一致：期望 {self.embedder.dim}")
        row_count = table.count_rows()
        searchable_count = sum(
            batch.num_rows
            for batch in table.search().select(["chunk_id"]).to_batches(batch_size=_COPY_BATCH_SIZE)
        )
        if row_count != expected_count or searchable_count != expected_count:
            raise ValueError(
                f"候选向量代校验失败：行数 {row_count}、可检索块数 {searchable_count}、期望 {expected_count}"
            )
        generation = self._candidate_generation
        self._save_meta(generation)
        self._table = table
        self._has_date_fields = self._candidate_has_date_fields
        self._candidate_table = None
        self._candidate_generation = None
        self._candidate_has_date_fields = False
        try:
            self._cleanup_generations()
        except Exception as exc:  # noqa: BLE001 -- generation switch is already committed
            _LOG.warning("向量代已切换，但旧代清理失败：%s", safe_reason(exc))

    def abort_generation(self) -> None:
        if self._candidate_table is None:
            return
        name = self._candidate_table.name
        self._candidate_table = None
        self._candidate_generation = None
        self._candidate_has_date_fields = False
        if name in self._db.list_tables().tables:
            self._db.drop_table(name)

    def _cleanup_generations(self) -> None:
        generations = []
        for name in self._db.list_tables().tables:
            match = re.fullmatch(r"chunks(?:_g(\d+))?", name)
            if match:
                generations.append((int(match.group(1) or 0), name))
        previous = max((generation for generation, _ in generations if generation < self._current_generation), default=None)
        keep = {self._current_generation}
        if previous is not None:
            keep.add(previous)
        for generation, name in generations:
            if generation not in keep:
                self._db.drop_table(name)

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
