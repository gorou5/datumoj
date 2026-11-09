"""MAPLE-145: sparse lexical collisions, cited versions, and search_docs hit@10.

The comparison replays trusted local git sources from 89e8763, never HTTP.
Both versions use the same recorded corpora and FakeEmbedder(dim=64).
hit@10 counts questions with at least one expected doc/locator among ten hits;
only positive expect.hits enter the denominator. Catalog/SQL/change gates are
not text retrieval metrics; changes.yaml therefore has no hit@10 denominator.
"""

import subprocess
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.contracts import citation_for_chunk
from datumoj.rikolto.core.models import Freshness, GapState, Hit, MatchedBy, SourceText, ToolResult
from datumoj.rikolto.eval import load_questions
from datumoj.rikolto.index.fts import SqliteKeywordIndex
from datumoj.rikolto.index.hybrid import HybridRetriever
from datumoj.rikolto.testing.fakes import FakeCatalogStore, FakeEmbedder, FakeRawStore, FakeVectorIndex
from datumoj.rikolto.testing.samples import make_chunk, make_dataset, make_document, make_snapshot

pytest_plugins = (
    "tests.integration.test_maple_eval",
    "tests.integration.test_tachibana_m3",
    "tests.regressions.test_audit_144",
)

ROOT = Path(__file__).resolve().parents[2]
BASELINE_COMMIT = "89e8763"
# Measured through search_docs after MAPLE-145 merged at dfdfb65.
MAPLE_145_MULTILINGUAL_HITS = (6, 6)
# Measured with 89e8763's AnalysisTools.search_docs (before MAPLE-144)
# on the corpus expanded by MAPLE-149's production catalog/row projections.
# (questions hitting an expected doc/locator at k=10, positive questions).
BASELINE_HITS = {
    "behavior.yaml": (12, 12),
    "catalog.yaml": (10, 10),
    "changes.yaml": (0, 0),
    "gov_web.yaml": (11, 12),
    "law.yaml": (20, 20),
    "maple_multilingual.yaml": (6, 6),
    "stats.yaml": (9, 10),
    "tachibana_gaps.yaml": (6, 6),
}


@pytest.mark.parametrize("query", [
    "月面補助金", "月面基地建設補助金", "宇宙港建設補助金",
    "月面补助金", "月面基地建设补助金", "宇宙港建设补助金",
    "lunar subsidy", "lunar base construction subsidy", "spaceport construction subsidy",
])
@pytest.mark.parametrize("vectors", [False, True])
def test_sparse_common_trigram_cannot_support_unknown_words(corpus, query, vectors):
    chunks = list(corpus.tools.chunks.iter_chunks())
    assert sum("補助金" in " ".join([*c.title_path, c.text]) for c in chunks) <= len(chunks) / 5
    # This is a real candidate at normal IDF, not a corpus saturated with 補助金.
    assert corpus.keyword.search("月面補助金").items
    if not vectors:
        corpus.retriever.vector = None
    found = corpus.tools.search_docs(query, sources="jp-test")
    assert found["gap_state"] == "zero_hits"
    assert found["data"]["hits"] == []
    if "subsidy" in query or "补助金" in query:
        assert "補助金" in found["data"]["queries"]


@pytest.mark.parametrize(("query", "doc_key"), [
    ("補助金", "jp-test:2"), ("スタートアップ", "jp-test:3"),
])
def test_true_single_words_still_hit(corpus, query, doc_key):
    found = corpus.tools.search_docs(query)
    assert found["gap_state"] == "ok"
    assert [hit["doc_key"] for hit in found["data"]["hits"]] == [doc_key]


@pytest.mark.parametrize(("query", "body", "supported"), [
    ("月面補助金", "補助金の案内", False),
    ("月面基地建設補助金申請", "補助金申請の案内", False),  # Three overlapping trigrams.
    ("abcdefghij", "abc efg hij", False),  # Disconnected fragments do not add up.
    ("abcd", "abc", False),
    ("スタートアップ支援", "スタートアップの案内", True),
    ("subsidy", "補助金の案内", True),
    ("AI", "ＡＩの案内", True),
    ("ベンチャー", "ベンチャーの案内", True),
])
def test_single_word_requires_a_contiguous_substantial_fragment(query, body, supported):
    chunk = make_chunk("jp-test:word", "ab" * 32, "section=1", body)
    assert AnalysisTools._supports_query(query, chunk) is supported


@pytest.mark.parametrize(("query", "title", "locator"), [
    ("沖縄地方 地方一か月予報 対象期間", "沖縄地方１か月予報", "section=1"),
    ("創業支援 最終更新日 対象となる方", "スタートアップ支援事業 対象となる方", "section=1"),
    ("人口推計 都道府県別人口 2000年 2020年", "都道府県別人口", "sheet=総人口（2000年～2015年）;cell=A1"),
])
def test_partial_phrases_aliases_and_sheet_titles_support_real_queries(query, title, locator):
    chunk = make_chunk("jp-test:context", "ab" * 32, locator, title)
    assert AnalysisTools._supports_query(query, chunk)


@pytest.mark.parametrize("same_snapshot", [False, True])
def test_catalog_context_must_belong_to_cited_snapshot(corpus, same_snapshot):
    corpus.retriever.vector = None
    survey = "平成28年経済センサス"
    snapshot = make_snapshot(url="https://example.go.jp/table", data=b"table")
    key = "jp-test:table"
    chunk = make_chunk(key, snapshot.sha256, "cell=H2;row=2", "従業者数")
    corpus.tools.manifest.record_snapshot(snapshot)
    corpus.tools.chunks.put_document(make_document(key, snapshot.sha256), [chunk])
    corpus.keyword.add([chunk])
    corpus.tools.catalog.upsert([make_dataset(
        key, "従業者数", description=survey,
        snapshot_sha256=snapshot.sha256 if same_snapshot else "cd" * 32,
    )])
    found = corpus.tools.search_docs([survey, "東京都 従業者数"])
    assert found["gap_state"] == ("ok" if same_snapshot else "zero_hits")
    assert [h["doc_key"] for h in found["data"]["hits"]] == ([key] if same_snapshot else [])


@pytest.mark.parametrize("historical_supports", [False, True])
def test_relevance_uses_cited_snapshot_in_both_directions(corpus, historical_supports):
    query = "スタートアップ"
    texts = ["気象観測資料。", "スタートアップの案内。"]
    old_text, current_text = texts[historical_supports], texts[not historical_supports]
    store = corpus.tools.chunks
    old_snapshot = make_snapshot(data=old_text.encode(), snapshot_id="historical-version")
    current_snapshot = make_snapshot(data=current_text.encode(), snapshot_id="current-version")
    key = "jp-test:versions"
    old = make_chunk(key, old_snapshot.sha256, "section=1", old_text)
    current = make_chunk(key, current_snapshot.sha256, "section=1", current_text)
    for snapshot, chunk in ((old_snapshot, old), (current_snapshot, current)):
        corpus.tools.manifest.record_snapshot(snapshot)
        store.put_document(make_document(key, snapshot.sha256), [chunk])
    assert old.chunk_id == current.chunk_id
    assert store.get_chunk(old.chunk_id).snapshot_sha256 == current_snapshot.sha256
    hit = Hit(
        chunk_id=old.chunk_id, doc_key=key, locator=old.locator,
        citation=citation_for_chunk(old, old_snapshot), score=1.0,
        matched_by=[MatchedBy.keyword], snippet=SourceText(content=old.text),
    )
    result = ToolResult(data=[hit], gap_state=GapState.ok, freshness=Freshness())
    corpus.tools.retriever = SimpleNamespace(search=lambda *args, **kwargs: result)
    found = corpus.tools.search_docs(query)
    assert found["gap_state"] == ("ok" if historical_supports else "zero_hits")
    assert bool(found["data"]["hits"]) is historical_supports
    if historical_supports:
        assert found["data"]["hits"][0]["citation"]["snapshot_sha256"] == old_snapshot.sha256


def _baseline_classes():
    """Load the actual baseline implementations without checking out/mutating files."""
    classes = []
    for module, name in (("index/fts", "SqliteKeywordIndex"),
                         ("index/hybrid", "HybridRetriever"), ("analysis/tools", "AnalysisTools")):
        path = f"src/datumoj/rikolto/{module}.py"
        source = subprocess.run(
            ["git", "show", f"{BASELINE_COMMIT}:{path}"], cwd=ROOT,
            check=True, capture_output=True, text=True,
        ).stdout
        namespace = {"__name__": f"audit_145_baseline_{name}", "__file__": str(ROOT / path)}
        # Trusted, pinned repository code, not government source text.
        exec(compile(source, f"{BASELINE_COMMIT}:{path}", "exec"), namespace)  # noqa: S102
        classes.append(namespace[name])
    return classes


def _measure(deps, questions, classes):
    index_class, retriever_class, tools_class = classes
    index = index_class(":memory:")
    try:
        chunks = list(deps.chunks.iter_chunks())
        index.add(chunks)
        vectors = FakeVectorIndex(FakeEmbedder(dim=64))
        vectors.add(chunks)
        tools = tools_class(
            retriever=retriever_class(deps.chunks, deps.manifest, deps.registry, index, vectors),
            catalog=deps.catalog, observations=deps.observations, chunks=deps.chunks,
            manifest=deps.manifest, raw=deps.raw, registry=deps.registry,
            parsers={}, data_dir=deps.data_dir,
        )
        rows = []
        for question in questions:
            # Change questions have no search formulation; still exercise the
            # tool on their text, but do not invent document expectations.
            found = tools.search_docs(question.queries or question.question, k=10, sources=question.source_ids)
            hits = found["data"]["hits"]
            recalled = any(
                hit["doc_key"] == expected.doc_key
                and (expected.locator_prefix is None or hit["locator"] == expected.locator_prefix
                     or hit["locator"].startswith(expected.locator_prefix + ";"))
                for expected in question.expect.hits or [] for hit in hits
            )
            rows.append((question, recalled))
        return rows
    finally:
        index.close()


def _scores(rows):
    banks = defaultdict(list)
    for question, recalled in rows:
        bank = Path(question._source_file).name
        if question.expect.hits:
            banks[bank].append(recalled)
        else:
            banks.setdefault(bank, [])
    return {bank: (sum(values), len(values)) for bank, values in banks.items()}


def _display(score):
    hits, total = score
    return f"{hits}/{total} ({hits / total:.2%})" if total else "N/A (0 positive questions)"


def test_search_docs_scores_against_pre_144_baseline(m3, maple_corpus, tmp_path, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("search_docs comparison must stay offline")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    baseline_classes = _baseline_classes()
    current_classes = (SqliteKeywordIndex, HybridRetriever, AnalysisTools)
    questions = load_questions(ROOT / "eval/questions")
    ivy_tachibana = [q for q in questions if not Path(q._source_file).name.startswith("maple_")]
    multilingual = [q for q in questions if Path(q._source_file).name == "maple_multilingual.yaml"]
    maple_retriever = maple_corpus["retriever"]
    maple = SimpleNamespace(
        chunks=maple_corpus["chunks"], manifest=maple_corpus["manifest"],
        registry=maple_retriever.registry, observations=maple_corpus["observations"],
        catalog=FakeCatalogStore(), raw=FakeRawStore(tmp_path), data_dir=tmp_path,
    )
    baseline_rows, current_rows = [], []
    for deps, bank_questions in ((m3["deps"], ivy_tachibana), (maple, multilingual)):
        baseline_rows.extend(_measure(deps, bank_questions, baseline_classes))
        current_rows.extend(_measure(deps, bank_questions, current_classes))
    baseline, current = _scores(baseline_rows), _scores(current_rows)
    baseline_ids = {q.id for q, recalled in baseline_rows if recalled}
    current_ids = {q.id for q, recalled in current_rows if recalled}
    with capsys.disabled():
        print(f"\nsearch_docs hit@10 — baseline {BASELINE_COMMIT}; FakeEmbedder(dim=64)")
        print("| 题库 | 基线 | 现值 |")
        print("| --- | --- | --- |")
        for bank in sorted(baseline):
            print(f"| {bank} | {_display(baseline[bank])} | {_display(current[bank])} |")
        print("Declined questions:", ", ".join(sorted(baseline_ids - current_ids)) or "none")
    assert baseline == BASELINE_HITS
    for bank, (hits, total) in current.items():
        if bank != "maple_multilingual.yaml":
            assert total == BASELINE_HITS[bank][1]
            assert hits >= BASELINE_HITS[bank][0], (bank, baseline[bank], current[bank])
    hits, total = current["maple_multilingual.yaml"]
    assert total == MAPLE_145_MULTILINGUAL_HITS[1]
    assert hits >= MAPLE_145_MULTILINGUAL_HITS[0]
