from __future__ import annotations

import json

import pytest
from lancedb.query import LanceQueryBuilder
from lancedb.table import LanceTable

from datumoj.core.contracts import EmbeddingError
from datumoj.index.embed import HashEmbedder
from datumoj.index.vector import LanceVectorIndex
from datumoj.pipeline import PipelineDeps, reindex
from datumoj.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeRawStore,
    FakeRegistry,
)
from datumoj.testing.samples import make_chunk, make_document

SHA = "ab" * 32
SHA2 = "cd" * 32


class SwitchableEmbedder(HashEmbedder):
    def __init__(self):
        super().__init__()
        self.fail = False

    def embed(self, texts):
        if self.fail:
            raise EmbeddingError("嵌入失败")
        return super().embed(texts)


def _chunks(doc_key: str, sha: str, prefix: str, count: int = 1):
    return [
        make_chunk(doc_key, sha, f"art={i + 1}", f"{prefix} 条文 {i + 1} の本文。")
        for i in range(count)
    ]


def _deps(tmp_path, index, chunks):
    store = FakeChunkStore()
    by_doc: dict[str, list] = {}
    for chunk in chunks:
        by_doc.setdefault(chunk.doc_key, []).append(chunk)
    for doc_key, doc_chunks in by_doc.items():
        store.put_document(make_document(doc_key, doc_chunks[0].snapshot_sha256), doc_chunks)
    return PipelineDeps(
        manifest=FakeManifest(),
        raw=FakeRawStore(tmp_path),
        registry=FakeRegistry(),
        chunks=store,
        keyword=FakeKeywordIndex(),
        observations=FakeObservationStore(),
        catalog=FakeCatalogStore(),
        vectors=index,
    )


def _replace_doc(deps, doc_key: str, sha: str, chunks):
    deps.chunks.put_document(make_document(doc_key, sha), chunks)


def test_add_queries_only_bounded_chunk_ids_without_reading_the_table(tmp_path, monkeypatch):
    index = LanceVectorIndex(tmp_path / "vectors", HashEmbedder(), batch_size=500)
    chunks = _chunks("jp-test:many", SHA, "批量", 225)
    query_sizes = []
    full_table_reads = 0
    original_where = LanceQueryBuilder.where
    original_to_arrow = LanceTable.to_arrow

    def track_where(builder, where, prefilter=True):
        if isinstance(where, str) and where.startswith("chunk_id IN ("):
            query_sizes.append(len(where.partition("(")[2].rstrip(")").split(", ")))
        return original_where(builder, where, prefilter=prefilter)

    def track_table_to_arrow(table):
        nonlocal full_table_reads
        full_table_reads += 1
        return original_to_arrow(table)

    monkeypatch.setattr(LanceQueryBuilder, "where", track_where)
    monkeypatch.setattr(LanceTable, "to_arrow", track_table_to_arrow)

    assert index.add(chunks) == len(chunks)
    assert query_sizes == [100, 100, 25]
    assert full_table_reads == 0


def test_reindex_commits_a_complete_new_generation_and_keeps_one_previous(tmp_path):
    index = LanceVectorIndex(tmp_path / "vectors", HashEmbedder())
    doc_key = "jp-test:doc"
    old = _chunks(doc_key, SHA, "旧索引", 2)
    assert index.add(old) == 2
    deps = _deps(tmp_path, index, old)
    new = _chunks(doc_key, SHA2, "新索引", 3)
    _replace_doc(deps, doc_key, SHA2, new)

    assert reindex(deps) == 3

    metadata = json.loads((tmp_path / "vectors" / "_meta.json").read_text(encoding="utf-8"))
    assert metadata["generation"] == 1
    assert index._table.count_rows() == 3
    assert {"chunks", "chunks_g1"} <= set(index._db.list_tables().tables)
    assert {hit.chunk_id for hit in index.search("新索引 条文").items} == {chunk.chunk_id for chunk in new}

    assert reindex(deps) == 3
    assert {"chunks_g1", "chunks_g2"} <= set(index._db.list_tables().tables)
    assert "chunks" not in index._db.list_tables().tables


def test_failed_reindex_keeps_the_previous_generation_queryable(tmp_path):
    embedder = SwitchableEmbedder()
    index = LanceVectorIndex(tmp_path / "vectors", embedder)
    doc_key = "jp-test:doc"
    old = _chunks(doc_key, SHA, "旧本文")
    assert index.add(old) == 1
    deps = _deps(tmp_path, index, old)
    new = _chunks(doc_key, SHA2, "新本文")
    _replace_doc(deps, doc_key, SHA2, new)
    embedder.fail = True

    with pytest.raises(EmbeddingError, match="嵌入失败"):
        reindex(deps)

    embedder.fail = False
    metadata = json.loads((tmp_path / "vectors" / "_meta.json").read_text(encoding="utf-8"))
    assert metadata["generation"] == 0
    assert {hit.chunk_id for hit in index.search("旧本文").items} == {old[0].chunk_id}
    assert "chunks_g1" not in index._db.list_tables().tables


def test_vectors_only_copies_current_generation_and_replaces_missing_document(tmp_path):
    index = LanceVectorIndex(tmp_path / "vectors", HashEmbedder())
    missing_doc = "jp-test:missing"
    intact_doc = "jp-test:intact"
    old_missing = _chunks(missing_doc, SHA, "旧暂缺")
    intact = _chunks(intact_doc, SHA, "保留内容")
    assert index.add(old_missing + intact) == 2
    deps = _deps(tmp_path, index, old_missing + intact)
    new_missing = _chunks(missing_doc, SHA2, "补齐内容")
    _replace_doc(deps, missing_doc, SHA2, new_missing)
    deps.vector_state["missing"][missing_doc] = {"snapshot_id": None, "chunks": 1, "error": "之前缺失"}
    deps.vector_state["last_error"] = "之前缺失"

    assert reindex(deps, vectors_only=True) == 1

    assert index._table.count_rows() == 2
    ids = {hit.chunk_id for hit in index.search("内容").items}
    assert ids == {new_missing[0].chunk_id, intact[0].chunk_id}
    assert deps.vector_state["missing"] == {}


def test_reindex_removes_a_residual_uncommitted_generation(tmp_path):
    path = tmp_path / "vectors"
    index = LanceVectorIndex(path, HashEmbedder())
    old = _chunks("jp-test:doc", SHA, "旧内容")
    assert index.add(old) == 1
    residue = index._db.create_table("chunks_g1", schema=index._schema)
    residue.add([{
        "chunk_id": "stale#art=1",
        "doc_key": "stale:doc",
        "source_id": "stale",
        "text_hash": "unused",
        "published_at": None,
        "effective_at": None,
        "vector": [0.0] * index.embedder.dim,
    }])

    reopened = LanceVectorIndex(path, HashEmbedder())
    reopened.begin_generation()
    assert reopened._candidate_table.count_rows() == 0
    replacement = _chunks("jp-test:doc", SHA, "新内容")
    assert reopened.add(replacement) == 1
    reopened.finish_generation(expected_count=1)

    assert reopened._current_generation == 1
    assert reopened._table.count_rows() == 1


def test_legacy_metadata_without_generation_opens_as_generation_zero(tmp_path):
    path = tmp_path / "vectors"
    index = LanceVectorIndex(path, HashEmbedder())
    chunks = _chunks("jp-test:legacy", SHA, "旧格式")
    assert index.add(chunks) == 1
    (path / "_meta.json").write_text(json.dumps({"model": "hash-v1", "dim": 64}), encoding="utf-8")

    reopened = LanceVectorIndex(path, HashEmbedder())

    assert reopened._current_generation == 0
    assert reopened.add(chunks) == 0
    assert [hit.chunk_id for hit in reopened.search("旧格式").items] == [chunks[0].chunk_id]
