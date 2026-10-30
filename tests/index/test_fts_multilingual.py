"""Latin stop words must not change CJK splitting or threshold overrides."""

import pytest

from datumoj.rikolto.index import fts
from datumoj.rikolto.index.fts import SqliteKeywordIndex, _query_terms
from datumoj.rikolto.testing.samples import make_chunk

SHA = "ab" * 32


@pytest.mark.parametrize(("query", "expected"), [
    ("the population of the Iris", ["population", "Iris"]),
    ("LA population DE la France", ["population", "France"]),
    ("l'économie et l’âge des ménages", ["économie", "âge", "ménages"]),
    ("aux données du recensement", ["données", "recensement"]),
    ("the of in", []),
    ("de la les et", []),
    ("AND", ["AND"]),  # Existing literal FTS syntax queries remain supported.
    ("population", ["population"]),
    ("165.T09-1271", ["165", "T09", "1271"]),
])
def test_latin_stop_words(query, expected):
    assert _query_terms(query) == expected


@pytest.mark.parametrize(("query", "expected"), [
    ("個人情報 第三者提供 本人の同意", ["個人情報", "第三者提供", "本人の同意"]),
    ("個人情報　第三者提供、本人の同意。個人情報", ["個人情報", "第三者提供", "本人の同意"]),
    ("東京都人口統計", ["東京都人口統計"]),
    ("统计资料，人口动态；统计资料", ["统计资料", "人口动态"]),
    ("北京市人口统计", ["北京市人口统计"]),
    ("the 個人情報 de la", ["the", "個人情報", "de", "la"]),
    ("la 中文的统计 the", ["la", "中文的统计", "the"]),
])
def test_cjk_and_mixed_script_splitting_is_unchanged(query, expected):
    assert _query_terms(query) == expected


def test_cjk_trigram_search_is_unchanged():
    index = SqliteKeywordIndex(":memory:", min_keyword_bm25=1_000_000)
    index.add([
        make_chunk("jp-test:tokyo", SHA, "art=1", "東京都の人口統計資料。"),
        make_chunk("cn-test:beijing", SHA, "art=1", "北京市的人口统计资料。"),
    ])
    try:
        assert [h.doc_key for h in index.search("東京都人口統計").items] == ["jp-test:tokyo"]
        assert [h.doc_key for h in index.search("北京市人口统计").items] == ["cn-test:beijing"]
    finally:
        index.close()


@pytest.mark.parametrize("query", ["the of in", "DE LA LES", "l’à de la"])
def test_stop_words_only_cannot_reintroduce_hits(query):
    index = SqliteKeywordIndex(":memory:", min_keyword_coverage=0, min_keyword_bm25=0)
    index.add([make_chunk("fr-test:common", SHA, "entry=1", "the of in de la les l à")])
    try:
        assert index.search(query).items == []
    finally:
        index.close()


@pytest.mark.parametrize(("thresholds", "kept"), [
    ({}, False),
    ({"min_keyword_coverage": 0.3}, True),
    ({"min_keyword_bm25": 0}, True),
    ({"min_keyword_coverage": 1, "min_keyword_bm25": 1_000_000}, False),
])
def test_threshold_parameters_still_override_defaults(thresholds, kept):
    index = SqliteKeywordIndex(":memory:", **thresholds)
    index.add([make_chunk("en-test:common", SHA, f"entry={i}", "population census report") for i in range(20)])
    try:
        assert bool(index.search("the population nebula quasar").items) is kept
    finally:
        index.close()


def test_stop_words_do_not_count_towards_coverage():
    index = SqliteKeywordIndex(":memory:", min_keyword_coverage=1, min_keyword_bm25=1_000_000)
    index.add([make_chunk("fr-test:population", SHA, "entry=1", "population ménages")])
    try:
        assert index.search("de la population et des ménages").items
        assert index.search("de la population et des galaxies").items == []
        assert fts.MIN_KEYWORD_COVERAGE == 0.5
        assert fts.MIN_KEYWORD_BM25 == 0.01
    finally:
        index.close()


@pytest.mark.parametrize("thresholds", [
    {"min_keyword_coverage": -0.1}, {"min_keyword_coverage": 1.1}, {"min_keyword_bm25": -1},
])
def test_invalid_threshold_parameters_are_still_rejected(thresholds):
    with pytest.raises(ValueError, match="关键词覆盖率"):
        SqliteKeywordIndex(":memory:", **thresholds)
