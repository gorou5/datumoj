"""MAPLE-144: unrelated lexical fragments and vector neighbours are not answers."""

from datetime import date
from types import SimpleNamespace

import pytest

from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.models import DocKind, MatchedBy
from datumoj.rikolto.index.fts import SqliteKeywordIndex
from datumoj.rikolto.index.hybrid import HybridRetriever
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeEmbedder,
    FakeManifest,
    FakeObservationStore,
    FakeRawStore,
    FakeRegistry,
    FakeVectorIndex,
)
from datumoj.rikolto.testing.samples import make_chunk, make_document, make_snapshot, make_spec


class CollidingEmbedder(FakeEmbedder):
    """Even a score of 1 cannot prove relevance without keyword support."""

    def embed(self, texts):
        return [[1.0] + [0.0] * (self.dim - 1) for _ in texts]


@pytest.fixture
def corpus(tmp_path):
    chunks, manifest = FakeChunkStore(), FakeManifest()
    registry = FakeRegistry([make_spec()])
    keyword = SqliteKeywordIndex(":memory:")
    vector = FakeVectorIndex(CollidingEmbedder())
    bodies = [
        ("建設", "旧法令 第32条 建設に関する規定。"),
        ("補助金", "大阪市 補助金の申請書様式。"),
        ("支援策", "大阪市 AI スタートアップ 支援策。起業相談を受け付けます。"),
        ("助成金", "大阪市 助成金の申請書。"),
        ("天気", "明日は晴れ。週間天気予報を公表します。"),
        ("交通", "鉄道の運行予定と道路の通行情報。"),
        ("教育", "学校の入学手続きと授業日程。"),
        ("環境", "森林の保全と河川の水質調査。"),
        ("保健", "定期健診の予約受付と医療機関の案内。"),
        ("文化", "図書館の開館時間と文化施設の催し。"),
    ]
    for number, (title, body) in enumerate(bodies, 1):
        snapshot = make_snapshot(url=f"https://example.go.jp/{number}", data=body.encode())
        document = make_document(f"jp-test:{number}", snapshot.sha256, title, kind=DocKind.html_page)
        chunk = make_chunk(document.doc_key, snapshot.sha256, "section=1", body,
                           title_path=[title], published_at=date(2026, 1, 1))
        manifest.record_snapshot(snapshot)
        chunks.put_document(document, [chunk])
        keyword.add([chunk])
        vector.add([chunk])
    retriever = HybridRetriever(chunks, manifest, registry, keyword, vector)
    tools = AnalysisTools(
        retriever=retriever, catalog=FakeCatalogStore(), observations=FakeObservationStore(),
        chunks=chunks, manifest=manifest, raw=FakeRawStore(tmp_path), registry=registry,
        parsers={}, data_dir=tmp_path,
    )
    yield SimpleNamespace(tools=tools, keyword=keyword, retriever=retriever, vector=vector)
    keyword.close()


@pytest.mark.parametrize("query", [
    "月面基地 建設 補助金",
    "月面基地建設補助金",
    "lunar base construction subsidy",
    "lunar base construction",
])
@pytest.mark.parametrize("vectors", [False, True])
def test_original_out_of_corpus_queries_are_zero_hits(corpus, query, vectors):
    if not vectors:
        corpus.retriever.vector = None
    result = corpus.tools.search_docs(query, sources="jp-test")
    assert result["gap_state"] == "zero_hits"
    assert result["data"]["hits"] == []
    assert "相关性门槛" in result["gap_detail"]
    if query == "lunar base construction subsidy":
        assert "補助金" in result["data"]["queries"]
        assert "助成金" in result["data"]["queries"]


def test_vector_only_match_cannot_support_an_answer(corpus):
    query = "宇宙港の運用計画"
    assert corpus.keyword.search(query).items == []
    assert corpus.vector.search(query).items[0].score == 1.0
    result = corpus.tools.search_docs(query, sources="jp-test")
    assert result["gap_state"] == "zero_hits"
    assert result["data"]["hits"] == []
    assert "纯向量" in result["gap_detail"]


@pytest.mark.parametrize("filters", [{}, {"since": "2025-01-01"}, {"kinds": ["html_page"]}])
def test_short_in_corpus_query_keeps_correct_chunk_only(corpus, filters):
    result = corpus.tools.search_docs("大阪市 AI スタートアップ 支援策", sources="jp-test", **filters)
    assert result["gap_state"] == "ok"
    assert [hit["doc_key"] for hit in result["data"]["hits"]] == ["jp-test:3"]
    assert MatchedBy.keyword in result["data"]["hits"][0]["matched_by"]
    assert any("LIKE" in warning for warning in result["warnings"])


@pytest.mark.parametrize("query", ["補助金", "subsidy"])
def test_glossary_aliases_still_support_in_corpus_queries(corpus, query):
    result = corpus.tools.search_docs(query, sources="jp-test")
    assert result["gap_state"] == "ok"
    expected = {"jp-test:2", "jp-test:4"} if query == "subsidy" else {"jp-test:2"}
    assert {hit["doc_key"] for hit in result["data"]["hits"]} == expected


@pytest.mark.parametrize("query", ["月面基地 建設 補助金", "月 基地 建設"])
def test_like_path_rejects_low_coverage(corpus, query):
    assert corpus.keyword.search(query).items == []


def test_zero_hits_does_not_override_missing_source(corpus):
    result = corpus.tools.search_docs("lunar base construction subsidy", sources="jp-absent")
    assert result["gap_state"] == "out_of_scope"
    assert result["data"]["hits"] == []


def test_kind_filtered_zero_hits_explains_relevance_gate(corpus):
    result = corpus.tools.search_docs("lunar base construction subsidy", kinds=["html_page"])
    assert result["gap_state"] == "zero_hits"
    assert result["data"]["hits"] == []
    assert "相关性门槛" in result["gap_detail"]


def test_filtered_vector_hits_do_not_hide_upstream_truncation(corpus):
    result = corpus.retriever.search(["宇宙港の運用計画"])
    result.warnings.append("truncated=true：候选补取达到上限，结果可能不完整")
    corpus.tools.retriever = SimpleNamespace(search=lambda *args, **kwargs: result)
    found = corpus.tools.search_docs("宇宙港の運用計画")
    assert found["gap_state"] == "ok"
    assert found["data"]["hits"] == []
    assert found["gap_detail"] is None
    assert any(w.startswith("truncated=true") for w in found["warnings"])
