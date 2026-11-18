"""Offline QA runner: published synthetic generation, fake embeddings and moto S3."""

import hashlib
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
import yaml

from datumoj.rikolto import wiring
from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.models import StateWriter
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.testing.fakes import FakeChunkStore, FakeEmbedder, FakeParser, FakeRegistry
from datumoj.rikolto.testing.samples import make_chunk, make_document, make_snapshot, make_spec

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/cloud_qa.py"
SPEC = importlib.util.spec_from_file_location("cloud_qa", SCRIPT)
qa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qa)
PULL_SPEC = importlib.util.spec_from_file_location("qa_pull_tests", ROOT / "tests/test_pull_check.py")
pull_tests = importlib.util.module_from_spec(PULL_SPEC)
PULL_SPEC.loader.exec_module(pull_tests)
ENV, close, remote = pull_tests.ENV, pull_tests.close, pull_tests.remote
BODY = "気象 警報についての合成資料。答案正文 must only appear in artifacts."


@pytest.fixture(autouse=True)
def script_import_path(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))


@pytest.fixture
def published_qa(remote, tmp_path, monkeypatch):
    monkeypatch.setenv("DATUMOJ_VECTORS", "on")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    overrides = {"registry": FakeRegistry([make_spec("jp-qa"), make_spec("jp-jma")]),
                 "parsers": {("fake", "text/plain"): FakeParser}, "embedder": FakeEmbedder()}
    original_build = wiring.build
    root = tmp_path / "source"
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    deps = original_build(root, overrides)
    try:
        body = BODY.encode()
        snap = make_snapshot("jp-qa", data=body)
        deps.raw.put(body)
        deps.manifest.record_snapshot(snap)
        assert deps.parse_pending(deps.pipeline_deps).parsed == [snap.snapshot_id]
        assert overrides["embedder"].embedded > 0
        for index, timestamp in enumerate(["2026-11-10T00:00:00Z", "2026-11-11T14:00:00Z",
                                           "2026-11-13T19:04:00Z", "2026-11-15T00:00:00Z"]):
            body = f"synthetic JMA {index}".encode()
            jma_snap = make_snapshot("jp-jma", url=f"https://example.go.jp/{index}", data=body)
            deps.raw.put(body)
            deps.manifest.record_snapshot(jma_snap)
            parsed = FakeParser().parse(jma_snap, body)
            doc = parsed.documents[0].model_copy(update={"metadata": {"published_at": timestamp}})
            deps.chunks.put_document(doc, parsed.chunks)
        generation = StateManager(remote, root, writer=StateWriter(kind="actions", run_id="synthetic-qa")).push()
    finally:
        close(deps)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "s3")

    def build(target):
        assert target != root
        assert os.environ["DATUMOJ_VECTORS"] == "on"
        result = original_build(target, overrides)
        assert result.vectors is not None
        return result

    monkeypatch.setattr(wiring, "build", build)
    return generation


@pytest.fixture
def question_path(tmp_path):
    questions = [
        {"id": "synthetic-ja", "lang": "ja", "question": "合成資料の気象警報は？", "calls": [
            {"tool": "search_docs", "args": {"queries": "気象 警報", "sources": ["jp-qa"], "k": 5}}]},
        {"id": "synthetic-error", "lang": "zh", "question": "保留错误后继续", "calls": [
            {"tool": "query_sql", "args": {"sql": "DROP TABLE observations"}}]},
        {"id": "synthetic-en", "lang": "en", "question": "Which sources?", "calls": [
            {"tool": "coverage", "args": {}}, {"tool": "version", "args": {}}]},
    ]
    path = tmp_path / "questions.yaml"
    path.write_text(yaml.safe_dump({"questions": questions}, allow_unicode=True))
    return path


def invoke(tmp_path, question_path, capfd):
    """Run the script; phase lines are written to the real fd 1, so capture at the descriptor level."""
    status = qa.main([str(tmp_path / "replica"), str(question_path), str(tmp_path / "answers")])
    captured = capfd.readouterr()
    assert captured.err == ""
    assert all(value not in captured.out for value in (*ENV.values(), BODY, str(tmp_path), "diagnostic-secret"))
    *phase_lines, last = captured.out.splitlines()
    summary = json.loads(last)
    assert summary["exit_status"] == status
    assert set(summary) == {"generation", "questions", "question_hits", "error_questions", "jma_hours", "phase",
                            "error_code", "exit_status"}
    phases = [json.loads(line) for line in phase_lines]
    for line in phases:  # Only a phase name and numbers.
        assert set(line) <= {"phase", "elapsed_s", "done"}
        assert isinstance(line["phase"], str) and all(type(v) in {int, float} for k, v in line.items() if k != "phase")
    (tmp_path / "public-log.jsonl").write_text(captured.out)
    return summary


def phases_of(tmp_path):
    return [json.loads(line) for line in (tmp_path / "public-log.jsonl").read_text().splitlines()[:-1]]


def test_full_list_artifacts_read_only_and_error_isolation(published_qa, remote, tmp_path, question_path, monkeypatch, capfd):
    before = {key: remote.get(key) for key in remote.list()}
    operations, tool_calls, gets, raw_listings = [], [], [], []

    def observe(model, **kwargs):
        operations.append(model.name)

    def got(params, **kwargs):
        gets.append(params["Key"])

    def listed(params, **kwargs):
        if params.get("Prefix", "").endswith("raw/"):
            raw_listings.append(params["Prefix"])

    for name in qa.ALLOWED_TOOLS:
        original = getattr(AnalysisTools, name)

        def record(self, *args, _name=name, _original=original, **kwargs):
            tool_calls.append((_name, args, kwargs))
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(AnalysisTools, name, record)
    events = boto3.DEFAULT_SESSION.events
    events.register("before-call.s3", observe)
    events.register("before-parameter-build.s3.GetObject", got)
    for name in ("ListObjects", "ListObjectsV2"):
        events.register(f"before-parameter-build.s3.{name}", listed)
    try:
        summary = invoke(tmp_path, question_path, capfd)
    finally:
        events.unregister("before-call.s3", observe)
        events.unregister("before-parameter-build.s3.GetObject", got)
        for name in ("ListObjects", "ListObjectsV2"):
            events.unregister(f"before-parameter-build.s3.{name}", listed)
    assert summary == {"generation": published_qa.generation, "questions": 3, "question_hits": [1, 0, 0],
                       "error_questions": 1, "jma_hours": 120, "phase": "complete", "error_code": None,
                       "exit_status": 0}
    assert operations and set(operations) <= {"GetObject", "HeadObject", "ListObjects", "ListObjectsV2"}
    assert {key: remote.get(key) for key in remote.list()} == before
    # Restore lists raw/ once and downloads no raw; only the cited snapshot's raw is read, on demand.
    published_raw = [key for key in remote.list() if key.startswith("raw/")]
    cited_sha = hashlib.sha256(BODY.encode()).hexdigest()
    raw_gets = [key for key in gets if qa.RAW_KEY.search(key)]
    assert len(published_raw) == 5 and len(raw_listings) == 1
    assert raw_gets and {key.split("/raw/", 1)[1] for key in raw_gets} == {f"{cited_sha[:2]}/{cited_sha}"}
    assert phases_of(tmp_path)[0]["phase"] == "restore" and [p["phase"] for p in phases_of(tmp_path)] == [
        "restore", "wiring", "questions", "questions", "questions", "jma", "artifacts"]
    assert [p.get("done") for p in phases_of(tmp_path) if p["phase"] == "questions"] == [0, 1, 2]
    report = json.loads((tmp_path / "answers/answers.json").read_text())
    assert report["metadata"]["raw_read"] == {"objects": len(raw_gets), "bytes": len(raw_gets) * len(BODY.encode())}
    assert report["metadata"]["generation"] == published_qa.generation
    assert report["metadata"]["generated_at"] == published_qa.created_at.isoformat()
    assert report["metadata"]["commit_sha"] == "a" * 40
    assert report["metadata"]["argument_adjustments"] == []
    first, error, last = report["questions"]
    assert [call[0] for call in tool_calls] == ["search_docs", "cite", "query_sql", "coverage", "version"]
    requests = yaml.safe_load(question_path.read_text())["questions"]
    for answer, request in zip(report["questions"], requests, strict=True):
        assert {key: answer[key] for key in ("id", "lang", "question")} == {key: request[key] for key in ("id", "lang", "question")}
        assert [(call["tool"], call["args"]) for call in answer["calls"]] == [(call["tool"], call["args"]) for call in request["calls"]]
    search = first["calls"][0]
    assert search["error_code"] is None and search["elapsed_s"] >= 0 and search["gap_state"]
    hit = search["top_hits"][0]
    assert hit["source_id"] == "jp-qa" and hit["title"]["content"] == BODY
    assert hit["locator"] and hit["snippet"]["content"] and hit["citation"]["snapshot_id"]
    assert search["first_hit_cite"]["restored"] is True
    assert error["error_code"] == "question_failed" and error["calls"][0]["error_code"] == "tool_failed"
    assert last["error_code"] is None and last["calls"][1]["data"]["data_generation"] == published_qa.generation
    markdown = (tmp_path / "answers/answers.md").read_text()
    assert BODY in markdown and "JMA hourly counts" in markdown and "snapshot_sha256" in markdown
    assert all(value not in markdown for value in ENV.values())


@pytest.mark.parametrize("method", sorted(qa.ALLOWED_TOOLS))
def test_sdk_diagnostics_and_exceptions_never_escape(published_qa, tmp_path, question_path, monkeypatch, capfd, caplog, method):
    def fail(*args, **kwargs):
        text = "diagnostic-secret " + BODY + " ".join(ENV.values())
        print(text)
        print(text, file=sys.stderr)
        os.write(2, text.encode())
        logging.getLogger("synthetic").critical(text)
        raise RuntimeError(text)

    monkeypatch.setattr(AnalysisTools, method, fail)
    question_path.write_text(yaml.safe_dump({"questions": [
        {"id": "failure", "lang": "en", "question": "Synthetic failure", "calls": [{"tool": method, "args": {}}]},
        {"id": "after", "lang": "en", "question": "Continue", "calls": [{"tool": "version", "args": {}}]},
    ]}))
    summary = invoke(tmp_path, question_path, capfd)
    assert summary["exit_status"] == 0 and summary["questions"] == 2 and summary["error_questions"] >= 1
    artifact = (tmp_path / "answers/answers.json").read_text()
    assert "diagnostic-secret" not in artifact + caplog.text
    assert json.loads(artifact)["questions"][0]["error_code"] == "question_failed"


@pytest.mark.parametrize("failure,phase,code", [
    ("missing", "restore", "restore_failed"), ("unverified", "restore", "unverified_generation"),
    ("vectors", "wiring", "vectors_unavailable"),
])
def test_restore_or_vector_failure_is_nonzero(
    remote, tmp_path, question_path, monkeypatch, capfd, request, failure, phase, code,
):
    if failure == "vectors":
        request.getfixturevalue("published_qa")  # A real generation is needed to reach wiring.
    if failure == "unverified":
        monkeypatch.setattr(wiring._ReleaseStateManager, "restore", lambda *args, **kwargs: {"generation": 1, "verified": False})
    if failure == "vectors":
        original = wiring.build

        def without_vectors(target):
            deps = original(target)
            deps.vectors = None
            return deps

        monkeypatch.setattr(wiring, "build", without_vectors)
    summary = invoke(tmp_path, question_path, capfd)
    assert summary["exit_status"] == 1 and summary["phase"] == phase and summary["error_code"] == code
    assert not (tmp_path / "answers/answers.json").exists()


def test_restored_replica_answers_sql_and_version_for_the_restored_generation(published_qa, tmp_path, question_path, capfd):
    question_path.write_text(yaml.safe_dump({"questions": [
        {"id": "sql", "lang": "en", "question": "Count", "calls": [
            {"tool": "query_sql", "args": {"sql": "SELECT COUNT(*) AS n FROM observations"}}]},
    ]}))
    assert invoke(tmp_path, question_path, capfd)["error_questions"] == 0
    call = json.loads((tmp_path / "answers/answers.json").read_text())["questions"][0]["calls"][0]
    assert call["error_code"] is None and call["data"]["rows"] == [[0]]


def test_generation_mismatch_stops_at_restore(published_qa, tmp_path, question_path, monkeypatch, capfd):
    from datumoj.rikolto.core import pull

    monkeypatch.setattr(pull, "data_state", lambda root: {"data_generation": 999, "data_generated_at": "synthetic"})
    summary = invoke(tmp_path, question_path, capfd)
    assert summary["exit_status"] == 1 and summary["phase"] == "restore" and summary["error_code"] == "generation_mismatch"
    assert not (tmp_path / "answers/answers.json").exists()


def test_missing_raw_fails_restore_with_only_phase_and_code(published_qa, remote, tmp_path, question_path, capfd):
    key = "synthetic/check/" + next(key for key in sorted(remote.list()) if key.startswith("raw/"))
    client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
    client.delete_object(Bucket=ENV["DATUMOJ_S3_BUCKET"], Key=key)
    client.close()
    summary = invoke(tmp_path, question_path, capfd)
    assert summary == {"generation": None, "questions": 3, "question_hits": [], "error_questions": 0, "jma_hours": 0,
                       "phase": "restore", "error_code": "restore_failed", "exit_status": 1}
    assert [p["phase"] for p in phases_of(tmp_path)] == ["restore"]
    assert not (tmp_path / "answers/answers.json").exists()


@pytest.mark.parametrize("phase", ["restore", "tools"])
def test_read_guard_blocks_mutations_throughout_run(remote, tmp_path, question_path, published_qa, monkeypatch, capfd, phase):
    def write(*args, **kwargs):
        manager = wiring.build_state(tmp_path / "unused")
        try:
            manager.store.put("forbidden", b"x")
        finally:
            manager.store._client.close()

    before = remote.list()
    if phase == "restore":
        monkeypatch.setattr(wiring._ReleaseStateManager, "restore", write)
    else:
        monkeypatch.setattr(AnalysisTools, "search_docs", write)
    summary = invoke(tmp_path, question_path, capfd)
    assert summary["exit_status"] == (1 if phase == "restore" else 0)
    if phase == "tools":
        assert summary["error_questions"] == 2
    assert remote.list() == before and remote.get("forbidden") is None


def test_only_allowed_tools_are_invoked(published_qa, tmp_path, question_path, monkeypatch, capfd):
    def forbidden(*args, **kwargs):
        pytest.fail("forbidden tool was invoked")

    monkeypatch.setattr(AnalysisTools, "drop_everything", forbidden, raising=False)
    question_path.write_text(yaml.safe_dump({"questions": [
        {"id": "forbidden", "lang": "en", "question": "Do not execute", "calls": [{"tool": "drop_everything", "args": {}}]},
        {"id": "after", "lang": "en", "question": "Continue", "calls": [{"tool": "version", "args": {}}]},
    ]}))
    assert invoke(tmp_path, question_path, capfd)["error_questions"] == 1
    assert json.loads((tmp_path / "answers/answers.json").read_text())["questions"][0]["calls"][0]["error_code"] == "tool_not_allowed"


def test_hourly_boundaries_zero_hours_and_exact_pause_means():
    chunks = FakeChunkStore()
    times = ["2026-11-09T23:59:59Z", "2026-11-10T09:00:00+09:00", "2026-11-14T23:59:59Z",
             "2026-11-15T00:00:00Z", "2026-11-11T13:59:59Z", "2026-11-11T14:00:00Z",
             "2026-11-13T19:03:59Z", "2026-11-13T19:04:00Z", None, "2026-11-10", "invalid"]
    for index, timestamp in enumerate(times):
        key, sha = f"jp-jma:{index}", "a" * 64
        doc = make_document(key, sha, metadata={} if timestamp is None else {"published_at": timestamp})
        chunks.put_document(doc, [make_chunk(key, sha, f"section={part}", "synthetic") for part in (1, 2)])
    report = qa.jma_hourly(chunks)
    rows = report["hours"]
    assert len(rows) == 120 and rows[0]["count"] == rows[-1]["count"] == 1 and rows[1]["count"] == 0
    assert sum(row["count"] for row in rows) == 6
    assert report["outside_window"] == 2 and report["missing_or_invalid_timestamp"] == 3
    assert report["current_documents"] == len(times)
    assert report["pause_mean"]["entries"] == 2
    assert report["pause_mean"]["hours"] == pytest.approx(53 + 4 / 60)
    assert report["pause_mean"]["entries_per_hour"] == pytest.approx(2 / (53 + 4 / 60))
    assert report["outside_pause_mean"]["entries"] == 4
    assert report["outside_pause_mean"]["entries_per_hour"] == pytest.approx(4 / (120 - 53 - 4 / 60))
    assert report["field"] == "Document.metadata.published_at"


def test_h5_list_is_unscored_and_runs_all_questions(published_qa, tmp_path, capfd):
    path = ROOT / "eval/acceptance/v0.3.0-maple-h5.yaml"
    questions = qa.load_questions(path)
    assert len(questions) == 12
    assert [q["lang"] for q in questions] == ["ja"] * 4 + ["zh"] * 4 + ["en"] * 4
    assert all(set(q) == {"id", "lang", "question", "calls"} for q in questions)
    summary = invoke(tmp_path, path, capfd)
    assert summary["exit_status"] == 0 and summary["questions"] == 12 and summary["error_questions"] == 0
    answers = json.loads((tmp_path / "answers/answers.json").read_text())["questions"]
    for question, answer in zip(questions, answers, strict=True):
        assert question["question"] == answer["question"]
        assert question["calls"] == [{"tool": call["tool"], "args": call["args"]} for call in answer["calls"]]


@pytest.mark.parametrize("citation_state", ["restored", "archived", "missing", "error"])
def test_catalog_first_hit_citation_and_five_hit_preview(tmp_path, citation_state):
    calls = []
    citation = {"snapshot_sha256": "a" * 64, "snapshot_id": "synthetic", "locator": "entry=0"}
    datasets = [{"title": {"content": f"Synthetic dataset {index}", "content_role": "untrusted_source_text"},
                 "source_id": "jp-qa", "citation": None if citation_state == "missing" else citation}
                for index in range(7)]

    def search(**args):
        calls.append(("search_catalog", args))
        return {"data": {"datasets": datasets}, "gap_state": "ok"}

    def cite(**args):
        calls.append(("cite", args))
        if citation_state == "error":
            raise RuntimeError("diagnostic-secret")
        return {"data": {"status": "archived" if citation_state == "archived" else "ok",
                         "passages": [] if citation_state == "archived" else [{"text": {"content": "Synthetic"}}]},
                "gap_state": "ok"}

    request = {"tool": "search_catalog", "args": {"query": "失业率", "sources": ["jp-qa"], "k": 7}}
    question = {"id": "catalog", "lang": "zh", "question": "原题不改写", "calls": [request]}
    answer = qa.run_question(question, SimpleNamespace(tools=SimpleNamespace(search_catalog=search, cite=cite)), tmp_path)
    call = answer["calls"][0]
    assert call["data"]["datasets"] == datasets and len(call["top_hits"]) == 5
    assert calls[0] == ("search_catalog", request["args"])
    assert calls[1:] == ([] if citation_state == "missing" else [("cite", citation)])
    assert call["first_hit_cite"]["restored"] is (citation_state == "restored")
    assert (answer["error_code"] is not None) is (citation_state in {"missing", "error"})


def test_raw_meter_counts_only_successful_raw_reads():
    from moto import mock_aws

    sha = hashlib.sha256(b"raw-body").hexdigest()
    missing = hashlib.sha256(b"missing").hexdigest()
    with mock_aws():
        boto3.setup_default_session(region_name="us-east-1")
        with qa.raw_meter() as meter:
            client = boto3.client("s3", region_name="us-east-1")
            client.create_bucket(Bucket="qa-meter")
            client.put_object(Bucket="qa-meter", Key=f"p/raw/{sha[:2]}/{sha}", Body=b"raw-body")
            client.get_object(Bucket="qa-meter", Key=f"p/raw/{sha[:2]}/{sha}")
            with pytest.raises(client.exceptions.NoSuchKey):
                client.get_object(Bucket="qa-meter", Key=f"p/raw/{missing[:2]}/{missing}")
    assert meter == {"objects": 1, "bytes": len(b"raw-body")}
