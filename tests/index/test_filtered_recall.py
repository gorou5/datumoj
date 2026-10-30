from datetime import date, timedelta

from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.models import DocKind, GapState
from datumoj.rikolto.index.fts import SqliteKeywordIndex
from datumoj.rikolto.index.hybrid import MAX_RECALL_CANDIDATES, HybridRetriever
from datumoj.rikolto.index.vector import LanceVectorIndex
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeEmbedder,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeRawStore,
    FakeRegistry,
)
from datumoj.rikolto.testing.samples import T0, make_chunk, make_document, make_snapshot, make_spec


def add_document(chunks, manifest, index, number, text, *, published_at=None, kind=None, title_path=None):
    doc_key = f"jp-test:doc-{number}"
    body = f"{number}:{text}".encode()
    snapshot = make_snapshot(
        "jp-test", f"https://example.go.jp/{number}.txt", body, fetched_at=T0 + timedelta(seconds=number),
    )
    manifest.record_snapshot(snapshot)
    document = make_document(doc_key, snapshot.sha256, kind=kind)
    chunk = make_chunk(
        doc_key, snapshot.sha256, "section=1", text, published_at=published_at, title_path=title_path or [],
    )
    chunks.put_document(document, [chunk])
    index.add([chunk])
    return chunk


def build_tools(chunks, manifest, index, registry, tmp_path):
    retriever = HybridRetriever(chunks, manifest, registry, index)
    return AnalysisTools(
        retriever=retriever,
        catalog=FakeCatalogStore(),
        observations=FakeObservationStore(),
        chunks=chunks,
        manifest=manifest,
        raw=FakeRawStore(tmp_path),
        registry=registry,
        parsers={},
        data_dir=tmp_path,
    )


def test_date_filtered_fts_finds_a_match_beyond_top_k(tmp_path):
    chunks, manifest = FakeChunkStore(), FakeManifest()
    index = SqliteKeywordIndex(":memory:")
    registry = FakeRegistry([make_spec("jp-test")])
    old = date(2025, 1, 1)
    target = add_document(
        chunks, manifest, index, 0, "政府監査の手続を定める。", published_at=date(2026, 4, 1),
    )
    for number in range(1, 4):
        add_document(
            chunks, manifest, index, number, "政府監査の手続を定める。", published_at=old,
            title_path=["政府監査に関する概要"],
        )

    baseline = index.search("政府監査", k=1)
    assert baseline.items[0].chunk_id != target.chunk_id
    retriever = HybridRetriever(chunks, manifest, registry, index)
    result = retriever.search(["政府監査"], k=1, date_range=(date(2026, 1, 1), None))

    assert [hit.chunk_id for hit in result.data] == [target.chunk_id]
    assert result.gap_state is GapState.ok
    index.close()


def test_kind_filter_finds_a_match_beyond_top_k(tmp_path):
    chunks, manifest = FakeChunkStore(), FakeManifest()
    index = SqliteKeywordIndex(":memory:")
    registry = FakeRegistry([make_spec("jp-test")])
    target = add_document(
        chunks, manifest, index, 0, "政府監査の手続を定める。", kind=DocKind.pdf,
    )
    for number in range(1, 4):
        add_document(
            chunks, manifest, index, number, "政府監査の手続を定める。", kind=DocKind.law,
            title_path=["政府監査に関する概要"],
        )
    tools = build_tools(chunks, manifest, index, registry, tmp_path)

    result = tools.search_docs("政府監査", k=1, kinds=["pdf"])

    assert [hit["chunk_id"] for hit in result["data"]["hits"]] == [target.chunk_id]
    assert result["gap_state"] == GapState.ok
    index.close()


def test_vector_index_applies_date_and_document_key_where_filters(tmp_path):
    index = LanceVectorIndex(tmp_path / "vectors", FakeEmbedder())
    dated = make_chunk(
        "jp-test:dated", "ab" * 32, "section=1", "unrelated text",
        published_at=date(2026, 2, 1),
    )
    old = make_chunk(
        "jp-test:old", "cd" * 32, "section=1", "query text",
        published_at=date(2025, 2, 1),
    )
    index.add([old, dated])

    result = index.search_filtered(
        "query text", k=1, date_range=(date(2026, 1, 1), None), doc_keys=["jp-test:dated"],
    )

    assert [hit.chunk_id for hit in result.items] == [dated.chunk_id]

    index.clear()
    index.add([old, dated])
    rebuilt = index.search_filtered(
        "query text", k=1, date_range=(date(2026, 1, 1), None), doc_keys=["jp-test:dated"],
    )
    assert [hit.chunk_id for hit in rebuilt.items] == [dated.chunk_id]


def test_candidate_budget_returns_truncated_instead_of_zero_hits():
    chunks, manifest = FakeChunkStore(), FakeManifest()
    index = FakeKeywordIndex()
    registry = FakeRegistry([make_spec("jp-test")])
    doc_key = "jp-test:budget"
    snapshot = make_snapshot("jp-test", "https://example.go.jp/budget.txt", b"candidate budget", fetched_at=T0)
    manifest.record_snapshot(snapshot)
    doc_chunks = [
        make_chunk(
            doc_key, snapshot.sha256, f"page={number}", "budgetneedle budgetneedle",
            published_at=date(2025, 1, 1),
        )
        for number in range(MAX_RECALL_CANDIDATES)
    ]
    target = make_chunk(
        doc_key, snapshot.sha256, f"page={MAX_RECALL_CANDIDATES}", "budgetneedle",
        published_at=date(2026, 1, 1),
    )
    doc_chunks.append(target)
    chunks.put_document(make_document(doc_key, snapshot.sha256), doc_chunks)
    index.add(doc_chunks)
    retriever = HybridRetriever(chunks, manifest, registry, index)

    result = retriever.search(["budgetneedle"], k=1, date_range=(date(2026, 1, 1), None))

    assert result.data == []
    assert result.gap_state is not GapState.zero_hits
    assert any("truncated=true" in warning for warning in result.warnings)


def test_unfiltered_hybrid_order_matches_keyword_index():
    chunks, manifest = FakeChunkStore(), FakeManifest()
    index = SqliteKeywordIndex(":memory:")
    registry = FakeRegistry([make_spec("jp-test")])
    for number, title in enumerate(("概要", "政府監査の概要", "政府監査に関する概要")):
        add_document(
            chunks, manifest, index, number, "政府監査の手続を定める。", title_path=[title],
        )

    expected = index.search("政府監査", k=2).items
    result = HybridRetriever(chunks, manifest, registry, index).search(["政府監査"], k=2)

    assert [hit.chunk_id for hit in result.data] == [item.chunk_id for item in expected]
    assert [hit.score for hit in result.data] == [1 / 61, 1 / 62]
    index.close()
