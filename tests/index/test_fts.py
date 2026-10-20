import sqlite3

import pytest

from datumoj.index.fts import SqliteKeywordIndex
from datumoj.store.chunks import SqliteChunkStore
from datumoj.testing.contract_suites import ChunkStoreSuite, KeywordIndexSuite
from datumoj.testing.samples import make_chunk

SHA = "ab" * 32

TOPICS = [
    "国勢調査", "住民基本台帳", "個人情報保護", "地方交付税", "介護保険", "雇用保険", "固定資産税", "道路交通法",
    "建築基準法", "水道事業", "下水道整備", "防災計画", "気象警報", "地震情報", "津波注意報", "火山噴火予報",
    "農業振興", "漁業協同組合", "森林整備", "観光振興", "文化財保護", "学校教育", "生涯学習", "児童手当",
    "生活保護", "障害者支援", "感染症対策", "予防接種", "医療費助成", "年金制度", "環境基本計画", "再生可能エネルギー",
    "公共交通", "空港整備",
]


def corpus():
    chunks = []
    for i, topic in enumerate(TOPICS):
        source = "jp-law" if i % 2 == 0 else "jp-news"
        chunks.append(make_chunk(
            f"{source}:d{i}", SHA, "art=1", f"本件は{topic}に関する規定であり、詳細は別に定める。",
            title_path=["総則", f"第{i + 1}条"],
        ))
    return chunks


class TestSqliteChunkStore(ChunkStoreSuite):
    @pytest.fixture
    def store(self, tmp_path):
        s = SqliteChunkStore(tmp_path / "chunks.db")
        yield s
        s.close()

    def test_persists_across_reopen(self, tmp_path):
        from datumoj.testing.contract_suites import _doc_v

        path = tmp_path / "c.db"
        doc, chunks = _doc_v("cd" * 32, ["永続化"])
        s = SqliteChunkStore(path)
        s.put_document(doc, chunks)
        s.close()
        s = SqliteChunkStore(path)
        assert s.chunks_for(doc.doc_key) == chunks
        s.close()


class TestSqliteKeywordIndex(KeywordIndexSuite):
    @pytest.fixture
    def index(self):
        i = SqliteKeywordIndex(":memory:")
        yield i
        i.close()


@pytest.fixture
def big():
    i = SqliteKeywordIndex(":memory:")
    i.add(corpus())
    yield i
    i.close()


def test_corpus_size():
    assert len(corpus()) >= 30


@pytest.mark.parametrize("topic", TOPICS)
def test_every_chunk_hit_by_its_keyword(big, topic):
    ids = [h.chunk_id for h in big.search(topic, k=50).items]
    assert len(ids) == 1
    assert big.search(topic).warnings == []


def test_title_hit_ranks_first():
    i = SqliteKeywordIndex(":memory:")
    i.add([
        make_chunk("jp-law:a", SHA, "art=1", "この条文は統計調査の実施について定める。", title_path=["総則"]),
        make_chunk("jp-law:b", SHA, "art=1", "調査に関する事項。", title_path=["統計調査法"]),
        make_chunk("jp-law:c", SHA, "art=1", "統計調査の結果の公表。統計調査の方法。", title_path=["雑則"]),
    ])
    ids = [h.chunk_id for h in i.search("統計調査").items]
    assert ids[0] == "jp-law:b#art=1" and set(ids) == {"jp-law:a#art=1", "jp-law:b#art=1", "jp-law:c#art=1"}
    i.close()


def test_source_filter_and_empty_filter(big):
    ids = {h.chunk_id for h in big.search("に関する規定", k=100, source_ids=["jp-news"]).items}
    assert len(ids) == len(TOPICS) // 2 and all(x.startswith("jp-news:") for x in ids)
    assert big.search("に関する規定", source_ids=[]).items == []


def test_replace_document(big):
    big.delete("jp-law:d0")
    assert big.search("国勢調査").items == []
    big.add([make_chunk("jp-law:d0", "cd" * 32, "art=1", "新しい版の国勢調査規定。")])
    assert [h.chunk_id for h in big.search("国勢調査").items] == ["jp-law:d0#art=1"]


def test_quotes_and_symbols_are_literal(big):
    big.add([make_chunk("jp-law:q", SHA, "art=1", '「甲」は"乙"をいう。100%達成。')])
    assert [h.chunk_id for h in big.search('"乙"をい').items] == ["jp-law:q#art=1"]
    assert [h.chunk_id for h in big.search("%").items] == ["jp-law:q#art=1"]
    assert big.search("_").items == []


def test_short_query_warns(big):
    r = big.search("税")
    assert any("LIKE" in w for w in r.warnings)
    assert {h.chunk_id for h in r.items} == {"jp-law:d6#art=1", "jp-news:d3#art=1"}


def test_short_query_result_cap():
    i = SqliteKeywordIndex(":memory:", like_limit=3)
    i.add([make_chunk("jp-law:x", SHA, f"art={n}", f"条文{n}の内容") for n in range(10)])
    r = i.search("条", k=10)
    assert len(r.items) == 3 and any("不完整" in w for w in r.warnings)
    i.close()


def test_short_query_timeout():
    i = SqliteKeywordIndex(":memory:", like_timeout=1e-9)
    i.add([make_chunk("jp-law:x", SHA, f"art={n}", f"条文{n}の内容" * 20) for n in range(3000)])
    r = i.search("ず", k=10)
    assert any("超时" in w for w in r.warnings)
    i.close()


def test_requires_trigram(monkeypatch):
    class Conn:
        def execute(self, *a, **kw):
            raise sqlite3.OperationalError("no such tokenizer: trigram")

    idx = SqliteKeywordIndex.__new__(SqliteKeywordIndex)
    idx._db = Conn()
    with pytest.raises(RuntimeError, match="trigram"):
        idx._require_trigram()
