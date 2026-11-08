"""MAPLE-146: unseen concepts outweigh common lexical collisions at any size."""

import math

import pytest

from datumoj.rikolto.analysis import tools as tools_module
from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.models import DocKind, MatchedBy
from datumoj.rikolto.index.fts import SqliteKeywordIndex, keyword_coverage
from datumoj.rikolto.testing.samples import make_chunk, make_document, make_snapshot

pytest_plugins = ("tests.regressions.test_audit_144",)

UNKNOWN_QUERIES = (
    "月面基地 建設 補助金",
    "lunar base construction subsidy",
    "月球基地 建设 补贴",
    "月面基地建設補助金",
    "月面補助金",
)
OSAKA_QUERY = "Osaka City support startups advanced financial markets subsidy"


@pytest.fixture(params=[40, 600], ids=["dozens", "over-candidate-cap"])
def weighted_corpus(corpus, request):
    bodies = [
        ("port-law", "art=32", DocKind.law, "港湾埋立法", "第32条 港湾埋立の建設に係る補助金。"),
        ("osaka-finance", "page=12", DocKind.pdf, "Financial incentives", (
            "Osaka City provides support for startups in financial markets. "
            "Subsidy for establishing a business Base."
        )),
        *((f"common-{i}", "section=1", DocKind.html_page, "事業案内", (
            "公共施設の建設に係る補助金の申請。Application support and subsidy guidelines."
        )) for i in range(request.param)),
    ]
    chunks = []
    for key, locator, kind, title, body in bodies:
        snapshot = make_snapshot(url=f"https://example.go.jp/{key}", data=body.encode())
        document = make_document(f"jp-test:{key}", snapshot.sha256, title, kind=kind)
        chunk = make_chunk(document.doc_key, snapshot.sha256, locator, body, title_path=[title])
        corpus.tools.manifest.record_snapshot(snapshot)
        corpus.tools.chunks.put_document(document, [chunk])
        chunks.append(chunk)
    corpus.keyword.add(chunks)
    corpus.vector.add(chunks)
    return corpus


@pytest.mark.parametrize("vectors", [False, True])
@pytest.mark.parametrize("query", UNKNOWN_QUERIES)
def test_unseen_core_cannot_be_replaced_by_common_terms(weighted_corpus, query, vectors):
    if not vectors:
        weighted_corpus.retriever.vector = None
    found = weighted_corpus.tools.search_docs(query, sources="jp-test")
    assert found["gap_state"] == "zero_hits"
    assert found["data"]["hits"] == []
    assert "相关性门槛" in found["gap_detail"]
    assert not any(w.startswith("truncated=true") for w in found["warnings"])
    # Gate the actual question; translations remain ordinary recall queries.
    assert found["data"]["queries"][0] == query


@pytest.mark.parametrize("vectors", [False, True])
def test_rare_osaka_concepts_rank_the_correct_pdf_first(weighted_corpus, vectors):
    if not vectors:
        weighted_corpus.retriever.vector = None
    found = weighted_corpus.tools.search_docs(OSAKA_QUERY, sources="jp-test")
    assert found["gap_state"] == "ok"
    hit = found["data"]["hits"][0]
    assert (hit["doc_key"], hit["locator"]) == ("jp-test:osaka-finance", "page=12")


def test_idf_uses_live_union_frequency_and_handles_short_literal_aliases():
    index = SqliteKeywordIndex(":memory:")
    chunks = [
        make_chunk("jp-test:one", "ab" * 32, "page=1", "補助金 助成金 subsidy AI 50% a_b"),
        make_chunk("jp-test:two", "ab" * 32, "page=1", "助成金 ＡＩ"),
        make_chunk("jp-test:three", "ab" * 32, "page=1", "construction"),
    ]
    try:
        concepts = [("補助金", "助成金", "subsidy"), ("AI",), ("lunar",), ("%",), ("_",)]
        assert index.concept_weights(concepts) == [1.0] * 5  # Empty index is safe.
        index.add(chunks)
        weights = index.concept_weights(concepts)
        assert weights == pytest.approx([
            1 + math.log(4 / 3), 1 + math.log(4 / 3), 1 + math.log(4),
            1 + math.log(2), 1 + math.log(2),
        ])
        index.delete("jp-test:two")
        assert index.concept_weights(concepts)[0] == pytest.approx(1 + math.log(3 / 2))
        index.add([make_chunk("jp-test:one", "ab" * 32, "page=1", "lunar")])
        assert index.concept_weights(concepts)[0] > index.concept_weights(concepts)[2]
        index.clear()
        assert index.concept_weights(concepts) == [1.0] * 5
    finally:
        index.close()


def test_weighted_coverage_keeps_unseen_terms_in_denominator(weighted_corpus):
    terms = ("lunar", "base", "construction", "subsidy")
    weights = dict(zip(terms, weighted_corpus.keyword.concept_weights([(t,) for t in terms]), strict=True))
    texts = ["Base Subsidy"]
    assert keyword_coverage(" ".join(terms), texts) == 0.5
    assert keyword_coverage(" ".join(terms), texts, weights=weights) < 0.5


def test_alias_frequency_is_for_the_whole_concept(weighted_corpus):
    received = []

    def weights_for(concepts):
        received.extend(concepts)
        return weighted_corpus.keyword.concept_weights(concepts)

    chunk = make_chunk("jp-test:alias", "ab" * 32, "section=1", "助成金")
    assert AnalysisTools._supports_query("subsidy", chunk, weights_for=weights_for)
    subsidy = next(concept for concept in received if "subsidy" in concept)
    assert {"補助金", "助成金", "补贴", "subsidy", "grants"} <= set(subsidy)
    assert weighted_corpus.keyword.concept_weights([subsidy])[0] < (
        weighted_corpus.keyword.concept_weights([("補助金",)])[0]
    )


def test_full_rejected_pool_is_zero_hits_with_one_bounded_retry(weighted_corpus, monkeypatch):
    monkeypatch.setattr(tools_module, "MAX_FILTERED_CANDIDATES", 20)
    original_search = weighted_corpus.retriever.search_filtered
    sizes = []

    def search(*args, **kwargs):
        result = original_search(*args, **kwargs)
        sizes.append(len(result.data))
        if len(sizes) == 1:
            result.warnings.append("first-pass warning")
        return result

    monkeypatch.setattr(weighted_corpus.retriever, "search_filtered", search)
    found = weighted_corpus.tools.search_docs(UNKNOWN_QUERIES[0])
    assert sizes == [10, 20]  # One retry; a saturated rejected pool is still zero_hits.
    assert found["gap_state"] == "zero_hits"
    assert found["data"]["hits"] == []
    assert "first-pass warning" in found["warnings"]
    assert not any(w.startswith("truncated=true") for w in found["warnings"])


def test_supported_head_keeps_its_ranking_without_retry(weighted_corpus, monkeypatch):
    original_search = weighted_corpus.retriever.search_filtered
    expected = [
        hit.chunk_id for hit in original_search(["補助金"], k=10).data
        if MatchedBy.keyword in hit.matched_by
    ]
    assert 0 < len(expected) < 10  # Vector-only neighbours leave a partial head.
    limits = []

    def search(*args, **kwargs):
        limits.append(kwargs["k"])
        return original_search(*args, **kwargs)

    monkeypatch.setattr(weighted_corpus.retriever, "search_filtered", search)
    found = weighted_corpus.tools.search_docs("補助金")
    assert found["gap_state"] == "ok"
    assert limits == [10]
    assert [hit["chunk_id"] for hit in found["data"]["hits"]] == expected


def test_rare_core_also_outweighs_common_terms(weighted_corpus):
    keyword = weighted_corpus.keyword
    keyword.add([make_chunk("jp-test:rare", "ab" * 32, "section=1", "希少制度の統計")])
    chunk = make_chunk("jp-test:common", "ab" * 32, "art=32", "建設に係る補助金")
    assert not AnalysisTools._supports_query(
        "希少制度 建設 補助金", chunk, weights_for=keyword.concept_weights,
    )
