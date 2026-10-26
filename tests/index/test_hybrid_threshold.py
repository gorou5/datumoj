from datumoj.rikolto.core.contracts import IndexResult, ScoredChunk
from datumoj.rikolto.core.models import GapState, MatchedBy
from datumoj.rikolto.index.hybrid import DEFAULT_MIN_VECTOR_SCORE, HybridRetriever
from datumoj.rikolto.testing.contract_suites import retrieval_corpus
from datumoj.rikolto.testing.fakes import (
    FakeChunkStore,
    FakeEmbedder,
    FakeKeywordIndex,
    FakeManifest,
    FakeRegistry,
    FakeVectorIndex,
)


class SpecifiedVectorIndex(FakeVectorIndex):
    """基于向量假实现返回测试指定的相似度分数。"""

    def __init__(self, items):
        super().__init__(FakeEmbedder())
        self.items = items

    def search(self, query, k=10, source_ids=None):
        items = [
            item for item in self.items
            if source_ids is None or item.doc_key.split(":", 1)[0] in source_ids
        ]
        return IndexResult(items=items[:k])


def build(items, *, min_vector_score=DEFAULT_MIN_VECTOR_SCORE):
    corpus = retrieval_corpus()
    chunks = FakeChunkStore()
    manifest = FakeManifest()
    keyword = FakeKeywordIndex()
    for snapshot in corpus.snapshots:
        manifest.record_snapshot(snapshot)
    for document, doc_chunks in corpus.documents:
        chunks.put_document(document, doc_chunks)
        keyword.add(doc_chunks)
    retriever = HybridRetriever(
        chunks,
        manifest,
        FakeRegistry(corpus.specs),
        keyword,
        SpecifiedVectorIndex(items),
        min_vector_score=min_vector_score,
    )
    return retriever, [chunk for _, doc_chunks in corpus.documents for chunk in doc_chunks]


def scored(chunk, score):
    return ScoredChunk(chunk_id=chunk.chunk_id, doc_key=chunk.doc_key, score=score)


def test_only_low_score_vector_results_are_zero_hits_and_warned():
    corpus_chunks = [chunk for _, doc_chunks in retrieval_corpus().documents for chunk in doc_chunks]
    candidate = next(c for c in corpus_chunks if c.locator == "art=27;para=1")
    retriever, _ = build([scored(candidate, 0.1)])

    result = retriever.search(["木星地表積雪量"], source_ids=["jp-law"])

    assert result.data == []
    assert result.gap_state is GapState.zero_hits
    assert "向量近邻相似度过低，已忽略" in result.warnings


def test_high_score_vector_result_is_kept():
    corpus_chunks = [chunk for _, doc_chunks in retrieval_corpus().documents for chunk in doc_chunks]
    candidate = next(c for c in corpus_chunks if c.locator == "art=27;para=1")
    retriever, _ = build([scored(candidate, 0.9)])

    result = retriever.search(["木星地表積雪量"], source_ids=["jp-law"])

    assert [hit.chunk_id for hit in result.data] == [candidate.chunk_id]
    assert result.data[0].matched_by == [MatchedBy.vector]
    assert result.gap_state is GapState.ok


def test_keyword_hit_survives_when_low_score_vector_result_is_discarded():
    corpus_chunks = [chunk for _, doc_chunks in retrieval_corpus().documents for chunk in doc_chunks]
    keyword_chunk = next(c for c in corpus_chunks if c.locator == "art=27;para=1")
    vector_chunk = next(c for c in corpus_chunks if c.locator == "art=78")
    retriever, _ = build([scored(vector_chunk, 0.1)])

    result = retriever.search(["第三者に提供"], source_ids=["jp-law"])

    assert [hit.chunk_id for hit in result.data] == [keyword_chunk.chunk_id]
    assert result.data[0].matched_by == [MatchedBy.keyword]
    assert "向量近邻相似度过低，已忽略" not in result.warnings


def test_min_vector_score_is_configurable():
    corpus_chunks = [chunk for _, doc_chunks in retrieval_corpus().documents for chunk in doc_chunks]
    candidate = next(c for c in corpus_chunks if c.locator == "art=27;para=1")
    retriever, _ = build([scored(candidate, 0.9)], min_vector_score=0.95)

    result = retriever.search(["木星地表積雪量"], source_ids=["jp-law"])

    assert result.data == []
    assert result.gap_state is GapState.zero_hits

