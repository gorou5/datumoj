from __future__ import annotations

import base64
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import yaml

from datumoj.rikolto.core.contracts import SourceSpec
from datumoj.rikolto.core.models import Snapshot, format_locator
from datumoj.rikolto.eval import load_questions

ROOT = Path(__file__).parents[2]
EVAL_EXTRA = ROOT / "tests" / "fixtures" / "eval_extra"
QUESTIONS = load_questions(ROOT / "eval" / "questions")
BEHAVIOR_QUESTIONS = [q for q in QUESTIONS if q.id.startswith("behavior-")]
GAP_QUESTIONS = [q for q in BEHAVIOR_QUESTIONS if q.kind == "gap"]
SPECIAL_GAP_SOURCES = {
    "not_ingested": "jp-eval-noingest",
    "fetch_failed": "jp-eval-fetchfail",
    "out_of_scope": "jp-eval-unknown",
}


def _load_specs(directory: Path) -> dict[str, SourceSpec]:
    specs = {}
    for path in sorted(directory.glob("*.yaml")):
        spec = SourceSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        assert path.stem == spec.id
        assert spec.id not in specs
        specs[spec.id] = spec
    return specs


def _feed_ids(path: Path) -> set[str]:
    ids = set()
    for _event, element in ET.iterparse(path, events=("end",)):
        if element.tag.endswith("}entry"):
            entry_id = next((child.text for child in element if child.tag.endswith("}id")), None)
            if entry_id:
                ids.add(entry_id)
            element.clear()
    return ids


def test_behavior_bank_loads_and_total_reaches_sixty():
    assert len(BEHAVIOR_QUESTIONS) >= 20
    assert len(QUESTIONS) >= 60

    kinds = Counter(q.kind for q in BEHAVIOR_QUESTIONS)
    assert kinds["gap"] >= 6
    assert kinds["time"] >= 4
    assert kinds["injection"] >= 2
    assert kinds["cross_source"] >= 2
    assert sum(q.lang in {"en", "zh"} for q in BEHAVIOR_QUESTIONS) >= 6


def test_gap_state_sources_match_registry_and_manifest_state():
    root_specs = _load_specs(ROOT / "sources")
    fixture_specs = _load_specs(EVAL_EXTRA / "sources")
    all_registered = root_specs | fixture_specs

    assert {"jp-eval-noingest", "jp-eval-fetchfail", "jp-eval-injection"} <= fixture_specs.keys()
    assert "jp-eval-unknown" not in all_registered
    assert not (ROOT / "sources" / "jp-eval-unknown.yaml").exists()
    assert not (EVAL_EXTRA / "sources" / "jp-eval-unknown.yaml").exists()

    seed = yaml.safe_load((EVAL_EXTRA / "manifest-seed.yaml").read_text(encoding="utf-8"))
    assert len(seed["snapshots"]) == 1
    snapshot = Snapshot.model_validate(seed["snapshots"][0])
    assert snapshot.source_id == "jp-eval-fetchfail"
    assert snapshot.snapshot_id == "eval-fetchfail-http-503"
    assert snapshot.resource_url == "https://example.invalid/eval/fetchfail/notice"
    assert snapshot.fetched_at.isoformat() == "2026-10-20T12:00:00+00:00"
    assert snapshot.outcome.value == "http_error"
    assert snapshot.http_status == 503
    assert snapshot.error == "HTTP 503 Service Unavailable"
    assert all(row["source_id"] != "jp-eval-noingest" for row in seed["snapshots"])

    by_state = Counter(q.expect.gap_state.value for q in GAP_QUESTIONS)
    assert by_state["zero_hits"] >= 2
    assert by_state["not_ingested"] >= 2
    assert by_state["fetch_failed"] >= 2
    assert by_state["out_of_scope"] >= 2

    for q in GAP_QUESTIONS:
        state = q.expect.gap_state.value
        assert len(q.source_ids) == 1
        source_id = q.source_ids[0]
        assert q.expect.hits in (None, [])
        if state == "zero_hits":
            assert source_id in {"jp-jma", "jp-stat-dashboard"}
            assert source_id in root_specs
        else:
            assert source_id == SPECIAL_GAP_SOURCES[state]
    assert (ROOT / "tests" / "fixtures" / "jma" / "regular_l.snapshot1.xml").is_file()
    assert (ROOT / "tests" / "fixtures" / "stat_dashboard" / "getData_b1_pop5x48_2021-2023CY.json").is_file()


def test_jma_question_keys_and_locators_match_real_feed_entries():
    feed_ids = _feed_ids(ROOT / "tests" / "fixtures" / "jma" / "regular_l.snapshot1.xml")
    jma_hits = [
        hit
        for q in BEHAVIOR_QUESTIONS
        for hit in (q.expect.hits or [])
        if hit.doc_key.startswith("jp-jma:")
    ]
    assert jma_hits
    for hit in jma_hits:
        entry_id = hit.doc_key.removeprefix("jp-jma:")
        assert entry_id.startswith("https://www.data.jma.go.jp/developer/xml/data/")
        assert entry_id in feed_ids
        assert hit.locator_prefix == format_locator({"entry": entry_id})


def test_cross_source_hits_cover_law_and_statistics_documents():
    questions = [q for q in BEHAVIOR_QUESTIONS if q.kind == "cross_source"]
    assert len(questions) >= 2
    for q in questions:
        source_ids = {hit.doc_key.split(":", 1)[0] for hit in q.expect.hits}
        assert "jp-egov-law" in source_ids
        assert source_ids & {"jp-stat-dashboard", "jp-estat-files"}
        law_hit = next(hit for hit in q.expect.hits if hit.doc_key.startswith("jp-egov-law:"))
        assert law_hit.doc_key == "jp-egov-law:419AC0000000053"
        assert law_hit.locator_prefix == "art=1"


def test_expected_fixture_keys_and_injection_text_are_present():
    law_path = ROOT / "tests" / "fixtures" / "egov_law" / "law_data.419AC0000000053.asof2026-10-20.xml-in-json.json"
    law_payload = json.loads(law_path.read_text(encoding="utf-8"))
    law_xml = ET.fromstring(base64.b64decode(law_payload["law_full_text"], validate=True))
    law_articles = {node.get("Num") for node in law_xml.iter() if node.tag.endswith("Article")}
    assert "1" in law_articles

    estat_path = ROOT / "tests" / "fixtures" / "estat_files" / "000013168605_jinsui_pref_population_2000-2020.xlsx"
    assert estat_path.is_file()
    estat_spec = _load_specs(ROOT / "sources")["jp-estat-files"]
    assert any(item["stat_inf_id"] == "000013168605" for item in estat_spec.scope["tables"])

    dashboard_path = ROOT / "tests" / "fixtures" / "stat_dashboard" / "getData_b1_pop5x48_2021-2023CY.json"
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    indicators = {
        row["VALUE"]["@indicator"]
        for row in dashboard["GET_STATS"]["STATISTICAL_DATA"]["DATA_INF"]["DATA_OBJ"]
    }
    assert "0201010000000010000" in indicators

    injection_fixture = EVAL_EXTRA / "announcement-injection.synthetic.txt"
    body = injection_fixture.read_text(encoding="utf-8")
    injection_questions = [q for q in BEHAVIOR_QUESTIONS if q.kind == "injection"]
    assert len(injection_questions) >= 2
    for q in injection_questions:
        assert q.expect.hits[0].doc_key == "jp-eval-injection:announcement"
        assert q.expect.hits[0].locator_prefix == "entry=notice-2026-10-20"
        assert all(text in body for text in q.must_not)


def test_non_japanese_questions_are_rewritten_as_japanese_subqueries():
    japanese = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
    for q in BEHAVIOR_QUESTIONS:
        if q.lang in {"en", "zh"}:
            assert len(q.queries) >= 2
            assert all(japanese.search(query) for query in q.queries)


def test_skill_has_seven_rules_safety_boundary_and_three_real_examples():
    skill = (ROOT / "skills" / "rikolto" / "SKILL.md").read_text(encoding="utf-8")
    rules = [line for line in skill.splitlines() if line[:1].isdigit() and line[1:3] == ". "]
    assert len(rules) == 7
    for phrase in (
        "先检索再回答",
        "query_sql",
        "每个结论都附出处",
        "统计期间",
        "发布日期",
        "施行日",
        "抓取日",
        "无数据日期",
        "署名",
        "仅供内部使用",
        "施行状态",
        "未施行",
        "缺口",
        "最近的可查内容",
        "改写成日文",
        "子查询",
        "SourceText.content",
        "个人个案的法律问题",
        "补贴资格",
    ):
        assert phrase in skill
    assert skill.count("### 示例") == 3
    assert "jp-estat-files:000031720773" in skill
    assert "出典：政府統計の総合窓口（e-Stat）" in skill
    assert "jp-egov-law:415AC0000000057" in skill
    assert "20XX" not in skill
    assert "当天" not in skill
