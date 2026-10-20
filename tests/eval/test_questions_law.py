import base64
import json
import re
from collections import Counter
from pathlib import Path

import pytest
from lxml import etree

from datumoj.core.models import parse_locator
from datumoj.eval import load_questions

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "egov_law"
QUESTIONS = load_questions(ROOT / "eval" / "questions")
LAW_QUESTIONS = [q for q in QUESTIONS if q.id.startswith("law-")]
JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


def _main_articles(law_id: str) -> set[str]:
    (path,) = FIXTURES.glob(f"law_data.{law_id}.asof*.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    root = etree.fromstring(base64.b64decode(payload["law_full_text"], validate=True))
    return {a.get("Num") for a in root.find(".//MainProvision").iter("Article")}


def test_loads_and_counts():
    assert len(LAW_QUESTIONS) == 20
    langs = Counter(q.lang for q in LAW_QUESTIONS)
    assert langs["ja"] >= 12 and langs["zh"] >= 4 and langs["en"] >= 4


@pytest.mark.parametrize("q", LAW_QUESTIONS, ids=lambda q: q.id)
def test_expected_articles_exist(q):
    assert q.source_ids == ["jp-egov-law"]
    for hit in q.expect.hits:
        source, law_id = hit.doc_key.split(":", 1)
        assert source == "jp-egov-law"
        loc = parse_locator(hit.locator_prefix)
        assert list(loc) == ["art"], "期望必须写到条（art=）"
        assert loc["art"] in _main_articles(law_id), f"{law_id} 没有第 {loc['art']} 条"
    assert q.notes


@pytest.mark.parametrize("q", LAW_QUESTIONS, ids=lambda q: q.id)
def test_queries_are_japanese_rewrites(q):
    assert all(JAPANESE.search(query) for query in q.queries)
    assert all(query != q.question for query in q.queries)
