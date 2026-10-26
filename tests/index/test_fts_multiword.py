import pytest

from datumoj.rikolto.index.fts import SqliteKeywordIndex
from datumoj.rikolto.testing.samples import make_chunk

SHA = "ab" * 32


@pytest.fixture
def index():
    result = SqliteKeywordIndex(":memory:")
    result.add([
        make_chunk(
            "jp-law:all", SHA, "art=1",
            "個人情報を適切に扱う。別の箇所で第三者提供を制限し、さらに本人の同意を要する。",
        ),
        make_chunk(
            "jp-news:two", SHA, "entry=1",
            "個人情報を管理する。別条に第三者提供の記録を定める。",
        ),
        make_chunk("jp-law:consent", SHA, "art=1", "本人の同意を得た場合に限る。" * 8),
        make_chunk("jp-law:personal", SHA, "art=1", "個人情報に関する規定。" * 8),
        make_chunk("jp-news:third-party", SHA, "entry=1", "第三者提供を記録する。"),
        make_chunk("jp-law:noise-1", SHA, "art=1", "行政手続の記録を保存する。"),
        make_chunk("jp-law:noise-2", SHA, "art=1", "申請書の提出先を定める。"),
        make_chunk("jp-law:noise-3", SHA, "art=1", "事業者への通知を行う。"),
        make_chunk("jp-law:noise-4", SHA, "art=1", "情報の管理方法を定める。"),
        make_chunk("jp-law:noise-5", SHA, "art=1", "関係機関と連携する。"),
        make_chunk("jp-law:noise-6", SHA, "art=1", "必要な書類を保管する。"),
        make_chunk("jp-law:noise-7", SHA, "art=1", "規則で定める手続きを行う。"),
        make_chunk("jp-law:literal", SHA, "art=1", '対象語"引用" AND * 記号を含む。'),
    ])
    yield result
    result.close()


def test_multiword_matches_nonadjacent_partial_terms_and_ranks_more_matches_first(index):
    result = index.search("個人情報 第三者提供 本人の同意", k=20)
    ids = [hit.chunk_id for hit in result.items]

    assert ids[:2] == ["jp-law:all#art=1", "jp-news:two#entry=1"]
    assert {
        "jp-law:consent#art=1",
        "jp-law:personal#art=1",
        "jp-news:third-party#entry=1",
    } <= set(ids)
    assert [hit.score for hit in result.items] == sorted(
        (hit.score for hit in result.items), reverse=True
    )


def test_fullwidth_space_separates_query_terms(index):
    ids = {hit.chunk_id for hit in index.search("個人情報　第三者提供", k=20).items}
    assert {"jp-law:all#art=1", "jp-news:two#entry=1", "jp-law:personal#art=1"} <= ids


def test_short_word_fallback_merges_with_fts_and_respects_source_filter(index):
    index.add([
        make_chunk("jp-law:long", SHA, "art=1", "申請書を受け付ける。"),
        make_chunk("jp-news:short", SHA, "entry=1", "本人確認を行う。"),
        make_chunk("jp-law:both", SHA, "art=1", "申請書の本人確認を行う。"),
    ])

    all_ids = {hit.chunk_id for hit in index.search("申請書 確認", k=20).items}
    filtered = index.search("申請書 確認", k=20, source_ids=["jp-law"])

    assert {"jp-law:long#art=1", "jp-news:short#entry=1", "jp-law:both#art=1"} <= all_ids
    assert all(hit.chunk_id.startswith("jp-law:") for hit in filtered.items)
    assert any("LIKE" in warning for warning in filtered.warnings)


def test_all_short_words_use_like_and_rank_chunks_matching_more_terms_first(index):
    index.add([make_chunk("jp-law:one-short", SHA, "art=1", "本人確認を行う。" * 8)])

    result = index.search("本人 同意", k=20)
    ids = [hit.chunk_id for hit in result.items]

    assert "jp-law:one-short#art=1" in ids
    assert ids.index("jp-law:all#art=1") < ids.index("jp-law:one-short#art=1")
    assert any("LIKE" in warning for warning in result.warnings)


def test_fts_syntax_characters_are_literal(index):
    literal_id = "jp-law:literal#art=1"

    assert [hit.chunk_id for hit in index.search('"引用"').items] == [literal_id]
    assert [hit.chunk_id for hit in index.search("AND").items] == [literal_id]
    assert [hit.chunk_id for hit in index.search("*").items] == [literal_id]
    assert literal_id in {hit.chunk_id for hit in index.search("未存在 AND *", k=20).items}

