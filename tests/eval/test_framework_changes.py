"""Change questions dispatch to list_changes; failures cannot masquerade as zero changes."""

import json
from pathlib import Path

import pytest

from datumoj.rikolto.eval import Question, check_gate, evaluate, load_questions, write_reports


def question(expected=None, **updates):
    data = {
        "id": "change-test", "question": "変更は？", "lang": "ja", "kind": "change",
        "source_ids": ["jp-test"],
        "change_filter": {"since": "2026-10-20T00:00:00Z", "until": "2026-10-22T00:00:00Z",
                          "kinds": ["modified"], "limit": 20},
        "expect": {"changes": expected if expected is not None else [
            {"kind": "modified", "source_id": "jp-test", "doc_key_prefix": "jp-test:page:"},
        ]},
    }
    data.update(updates)
    return Question.model_validate(data)


def run(q, callback=None):
    # A change question must not touch retrieval, SQL or citation services.
    return evaluate([q], retriever=None, observation_store=None, cite=None, list_changes=callback)


def test_change_dispatch_filters_metrics_and_reports(tmp_path):
    calls = []

    def changes(**kwargs):
        calls.append(kwargs)
        return {"gap_state": "ok", "data": [
            {"kind": "modified", "source_id": "jp-test", "doc_key": "jp-test:page:123"},
        ]}

    q = question()
    report = run(q, changes)
    assert calls == [{**q.change_filter.model_dump(), "sources": ["jp-test"]}]
    assert report.overall["change_hit_rate"] == 1
    assert report.overall["recall_at_10"] is None
    assert report.by_source["jp-test"]["change_hit_rate"] == 1
    assert report.by_language["ja"]["change_hit_rate"] == 1
    assert not report.failures
    write_reports(report, tmp_path)
    assert json.loads((tmp_path / "evaluation.json").read_text())["overall"]["change_hit_rate"] == 1
    assert "change_hit_rate | 1.000" in (tmp_path / "evaluation.md").read_text()


@pytest.mark.parametrize("row", [
    {"kind": "added", "source_id": "jp-test", "doc_key": "jp-test:page:1"},
    {"kind": "modified", "source_id": "jp-other", "doc_key": "jp-test:page:1"},
    {"kind": "modified", "source_id": "jp-test", "doc_key": "jp-test:other:1"},
    {"kind": "modified", "source_id": "jp-test", "doc_key": None},
])
def test_kind_source_and_prefix_all_have_to_match(row):
    report = run(question(), lambda **kw: {"gap_state": "ok", "data": [row]})
    assert report.overall["change_hit_rate"] == 0 and report.failures
    assert "change_hit_rate" in " ".join(check_gate(report).failures)


def test_partial_hit_rate_and_failure_without_document_key():
    q = question([
        {"kind": "fetch_failed", "source_id": "jp-test"},
        {"kind": "removed", "source_id": "jp-test", "doc_key_prefix": "jp-test:page:"},
    ])
    report = run(q, lambda **kw: {"gap_state": "ok", "data": [
        {"kind": "fetch_failed", "source_id": "jp-test", "doc_key": None},
    ]})
    assert report.overall["change_hit_rate"] == 0.5


def test_change_hit_rate_is_scoped_to_each_expected_source():
    q = question([
        {"kind": "added", "source_id": "jp-test"},
        {"kind": "added", "source_id": "jp-other"},
    ], source_ids=["jp-test", "jp-other"])
    report = run(q, lambda **kw: {"gap_state": "ok", "data": [
        {"kind": "added", "source_id": "jp-test"},
    ]})
    assert report.overall["change_hit_rate"] == 0.5
    assert report.by_source["jp-test"]["change_hit_rate"] == 1
    assert report.by_source["jp-other"]["change_hit_rate"] == 0


@pytest.mark.parametrize("response,score", [
    ({"gap_state": "zero_hits", "data": []}, 1),
    ({"gap_state": "not_ingested", "data": []}, 0),
    ({"gap_state": "ok", "data": [{"kind": "added"}]}, 0),
    ({"gap_state": "ok", "data": []}, 0),
    ({"gap_state": "zero_hits", "data": {}}, 0),
    ({"gap_state": "zero_hits", "data": [{"kind": "added"}]}, 0),
])
def test_zero_changes_requires_successful_empty_result(response, score):
    assert run(question([]), lambda **kw: response).overall["change_hit_rate"] == score


def test_missing_or_failed_callback_is_a_failure_even_for_zero_changes():
    def broken(**kw):
        raise RuntimeError("log unavailable")

    for callback in (None, broken):
        report = run(question([]), callback)
        assert report.overall["change_hit_rate"] == 0 and report.failures


@pytest.mark.parametrize("updates", [
    {"expect": {}}, {"change_filter": None},
    {"expect": {"changes": [], "hits": []}},
    {"expect": {"changes": [{"kind": "bogus", "source_id": "jp-test"}]}},
    {"expect": {"changes": [{"kind": "added", "source_id": "jp-test", "doc_key_prefix": "jp-other:"}]}},
    {"change_filter": {"since": "2026-10-23T00:00:00Z", "until": "2026-10-22T00:00:00Z"}},
    {"change_filter": {"since": "2026-10-20T00:00:00", "until": "2026-10-22T00:00:00Z"}},
])
def test_invalid_change_questions_are_rejected(updates):
    with pytest.raises(ValueError):
        question(**updates)


def test_change_gate_is_required_only_for_change_questions():
    metrics = {"recall_at_10": 1, "numeric_precision": 1, "numeric_occurrence": 1,
               "gap_accuracy": 1, "citation_recoverability": 1}
    assert check_gate({"overall": metrics}).passed
    payload = {"overall": metrics, "questions": [{"kind": "change"}]}
    assert not check_gate(payload).passed
    metrics["change_hit_rate"] = 1
    assert check_gate(payload).passed


def test_committed_change_bank_has_five_cases():
    questions = [q for q in load_questions(Path(__file__).parents[2] / "eval/questions")
                 if Path(q._source_file).name == "changes.yaml"]
    assert len(questions) == 5 and all(q.kind == "change" for q in questions)
    assert {item.kind.value for q in questions for item in q.expect.changes} == {
        "added", "modified", "removed", "fetch_failed",
    }
    assert sum(q.expect.changes == [] for q in questions) == 1
