import pytest

from datumoj.core.contracts import IndexResult, ScoredChunk
from datumoj.core.models import GapState
from datumoj.index.fts import SqliteKeywordIndex
from datumoj.index.hybrid import HybridRetriever
from datumoj.testing.contract_suites import KeywordIndexSuite
from datumoj.testing.fakes import FakeChunkStore, FakeEmbedder, FakeManifest, FakeRegistry
from datumoj.testing.samples import make_chunk, make_document, make_snapshot, make_spec

SHA = "ab" * 32


def _common_index(**kwargs):
    index = SqliteKeywordIndex(":memory:", **kwargs)
    chunks = [
        make_chunk(
            "jp-test:common", SHA, f"art={i}", "統計資料には人口動態の記録があります。",
        )
        for i in range(20)
    ]
    index.add(chunks)
    return index, chunks


class TestSqliteKeywordRelevance(KeywordIndexSuite):
    @pytest.fixture
    def index(self):
        idx = SqliteKeywordIndex(":memory:")
        yield idx
        idx.close()

    def test_filters_common_word_with_low_coverage_and_bm25(self):
        index, _ = _common_index()
        try:
            assert index.search("統計資料 木星 積雪量", k=30).items == []
        finally:
            index.close()

    def test_normal_query_keeps_relevant_matches(self):
        index, chunks = _common_index()
        try:
            found = index.search("統計資料。人口動態", k=30).items
            assert {hit.chunk_id for hit in found} == {chunk.chunk_id for chunk in chunks}
        finally:
            index.close()

    @pytest.mark.parametrize("thresholds", [
        {"min_keyword_coverage": 0.3, "min_keyword_bm25": 1_000_000},
        {"min_keyword_coverage": 0.5, "min_keyword_bm25": 0.0},
    ])
    def test_thresholds_can_be_overridden(self, thresholds):
        index, _ = _common_index(**thresholds)
        try:
            assert index.search("統計資料 木星 積雪量", k=30).items
        finally:
            index.close()

    def test_unspaced_long_query_uses_trigram_coverage(self):
        index = SqliteKeywordIndex(
            ":memory:", min_keyword_coverage=0.5, min_keyword_bm25=1_000_000,
        )
        index.add([make_chunk("jp-test:tokyo", SHA, "art=1", "東京都の人口統計資料。")])
        try:
            assert [hit.chunk_id for hit in index.search("東京都人口統計", k=10).items] == [
                "jp-test:tokyo#art=1",
            ]
        finally:
            index.close()


class _LowScoreVector:
    embedder = FakeEmbedder()

    def __init__(self, chunks):
        self.items = [ScoredChunk(chunk_id=c.chunk_id, doc_key=c.doc_key, score=0.2) for c in chunks]

    def search(self, query, k=10, source_ids=None):
        return IndexResult(items=self.items[:k])


def test_low_keyword_and_vector_matches_produce_zero_hits():
    snapshot = make_snapshot("jp-test", data=b"indexed")
    document = make_document("jp-test:common", snapshot.sha256)
    chunks = [
        make_chunk(document.doc_key, snapshot.sha256, f"art={i}", "統計資料には人口動態の記録があります。")
        for i in range(20)
    ]
    chunk_store, manifest = FakeChunkStore(), FakeManifest()
    chunk_store.put_document(document, chunks)
    manifest.record_snapshot(snapshot)
    keyword = SqliteKeywordIndex(":memory:")
    keyword.add(chunks)
    retriever = HybridRetriever(
        chunk_store, manifest, FakeRegistry([make_spec("jp-test")]), keyword,
        _LowScoreVector(chunks), min_vector_score=0.6,
    )
    try:
        result = retriever.search(["統計資料 木星 積雪量"], source_ids=["jp-test"])
        assert result.data == [] and result.gap_state is GapState.zero_hits
    finally:
        keyword.close()
