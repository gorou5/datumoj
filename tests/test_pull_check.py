"""Offline published-generation checks: moto storage, synthetic text and fake embeddings."""

import importlib.util
import json
import logging
import os
import runpy
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from datumoj.rikolto import wiring
from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.models import StateWriter
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.testing.fakes import FakeEmbedder, FakeParser, FakeRegistry
from datumoj.rikolto.testing.samples import make_observation, make_snapshot, make_spec

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/pull_check.py"
SPEC = importlib.util.spec_from_file_location("pull_check", SCRIPT)
pull_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pull_check)

ENV = {
    "R2_ACCESS_KEY_ID": "synthetic-check-access",
    "R2_SECRET_ACCESS_KEY": "synthetic-check-secret",
    "R2_ENDPOINT": "https://s3.amazonaws.com",
    "DATUMOJ_S3_BUCKET": "synthetic-check-bucket",
    "DATUMOJ_S3_PREFIX": "synthetic/check",
    "DATUMOJ_S3_REGION": "us-east-1",
    "DATUMOJ_RAW_BACKEND": "s3",
}
BODY = "気象 警報についての合成資料。公開ログには出さない本文。"


def close(deps):
    for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
        getattr(deps, name).close()


@pytest.fixture
def remote(tmp_path, monkeypatch):
    for name in ("DATUMOJ_S3_ENDPOINT", "DATUMOJ_S3_ACCESS_KEY_ID", "DATUMOJ_S3_SECRET_ACCESS_KEY",
                 "DATUMOJ_STATE_BACKEND", "DATUMOJ_STATE_DIR", "DATUMOJ_VECTORS"):
        monkeypatch.delenv(name, raising=False)
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing",
                              aws_secret_access_key="testing")
        client.create_bucket(Bucket=ENV["DATUMOJ_S3_BUCKET"])
        manager = wiring.build_state(tmp_path / "source")
        yield manager.store
        manager.store._client.close()
        client.close()


@pytest.fixture
def published(remote, tmp_path, monkeypatch):
    registry = FakeRegistry([make_spec("jp-pull"), make_spec("jp-empty")])
    embedder = FakeEmbedder()
    original_build = wiring.build
    overrides = {"registry": registry, "parsers": {("fake", "text/plain"): FakeParser}, "embedder": embedder}
    root = tmp_path / "source"
    # Publication uses local raw files; the reader still gets its real S3 configuration.
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    deps = original_build(root, overrides)
    try:
        body = BODY.encode()
        snap = make_snapshot("jp-pull", data=body)
        deps.raw.put(body)
        deps.manifest.record_snapshot(snap)
        assert deps.parse_pending(deps.pipeline_deps).parsed == [snap.snapshot_id]
        assert embedder.embedded > 0
        deps.observations.write_table("jp-pull", "t1", [make_observation(
            "synthetic-obs", "3", source_id="jp-pull", dataset_key="jp-pull:pop", snapshot_sha256=snap.sha256,
        )], order=deps.manifest.snapshot_order(snap.snapshot_id))
        generation = StateManager(remote, root, writer=StateWriter(kind="actions", run_id="synthetic")).push()
    finally:
        close(deps)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "s3")

    def build(target):
        assert target != root
        assert os.environ["DATUMOJ_VECTORS"] == "off"
        result = original_build(target, overrides)
        assert result.vectors is None
        return result

    monkeypatch.setattr(wiring, "build", build)
    return generation


def invoke(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), str(tmp_path / "replica")])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(captured.out.splitlines()) == 1
    assert all(value not in captured.out for value in (*ENV.values(), BODY, str(tmp_path)))
    result = json.loads(captured.out)
    assert result["exit_status"] == exit_info.value.code
    assert set(result) == {"generation", "pull_bytes", "pull_elapsed_s", "source_snapshots", "search_hits",
                           "search_source_ids", "cite_restored", "sql_rows", "exit_status", "phase"}
    return result


def test_published_generation_checks_same_tools_without_remote_writes(
    remote, published, tmp_path, monkeypatch, capsys,
):
    before = {key: remote.get(key) for key in remote.list()}
    calls, sizes = [], []
    tools_called = []
    events = boto3.DEFAULT_SESSION.events

    def observe(model, **kwargs):
        calls.append(model.name)

    def downloaded(parsed, **kwargs):
        sizes.append(parsed.get("ContentLength", 0))

    for name in ("version", "coverage", "search_docs", "cite", "query_sql"):
        original = getattr(AnalysisTools, name)

        def record(self, *args, _name=name, _original=original, **kwargs):
            tools_called.append(_name)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(AnalysisTools, name, record)
    events.register("before-call.s3", observe)
    events.register("after-call.s3.GetObject", downloaded)
    try:
        result = invoke(tmp_path, monkeypatch, capsys)
    finally:
        events.unregister("before-call.s3", observe)
        events.unregister("after-call.s3.GetObject", downloaded)
    assert result["exit_status"] == 0 and result["phase"] == "complete", result
    assert result["generation"] == published.generation
    assert result["pull_bytes"] == sum(sizes) > 0
    assert result["pull_elapsed_s"] >= 0
    assert result["source_snapshots"] == {"jp-pull": 1, "jp-empty": 0}
    assert result["search_hits"] == 1 and result["search_source_ids"] == ["jp-pull"]
    assert result["cite_restored"] is True and result["sql_rows"] == 1
    assert tools_called == ["version", "coverage", "search_docs", "cite", "query_sql"]
    assert calls and set(calls) <= {"GetObject", "HeadObject", "ListObjects", "ListObjectsV2"}
    assert {key: remote.get(key) for key in remote.list()} == before
    assert any(copy.name.startswith("vectors/") for copy in published.stores)
    assert (tmp_path / "replica" / "vectors").is_dir()


@pytest.mark.parametrize("corrupt_pointer", [False, True])
def test_no_valid_generation_fails_safely(remote, tmp_path, monkeypatch, capsys, corrupt_pointer):
    if corrupt_pointer:
        remote.put("current.json", b'{"generation":1}')
    result = invoke(tmp_path, monkeypatch, capsys)
    assert result["exit_status"] == 1 and result["phase"] == "pull"
    assert result["generation"] is None and result["cite_restored"] is False


@pytest.mark.parametrize("method", ["version", "coverage", "search_docs", "cite", "query_sql"])
def test_every_tool_failure_is_nonzero_and_hides_diagnostics(
    published, tmp_path, monkeypatch, capsys, caplog, method,
):
    def fail(*args, **kwargs):
        text = " ".join([*ENV.values(), BODY])
        print(text)
        print(text, file=sys.stderr)
        logging.getLogger("synthetic").critical(text)
        raise RuntimeError(text)

    monkeypatch.setattr(AnalysisTools, method, fail)
    result = invoke(tmp_path, monkeypatch, capsys)
    assert result["exit_status"] == 1 and result["phase"] == method
    assert all(value not in caplog.text for value in (*ENV.values(), BODY))


@pytest.mark.parametrize("method,response", [
    ("search_docs", {"data": {"hits": []}}),
    ("cite", {"data": {"status": "not_found", "passages": []}}),
    ("coverage", {"data": {"sources": [{"source_id": BODY, "snapshots": 1}]}}),
    ("query_sql", {"data": {"rows": [[BODY]]}}),
])
def test_unsuccessful_or_unsafe_results_fail_closed(published, tmp_path, monkeypatch, capsys, method, response):
    monkeypatch.setattr(AnalysisTools, method, lambda *args, **kwargs: response)
    result = invoke(tmp_path, monkeypatch, capsys)
    assert result["exit_status"] == 1 and result["phase"] == method


def test_read_meter_refuses_remote_mutations(remote, tmp_path):
    with pull_check._read_meter(), pytest.raises(ValueError, match="read_only"):
        # A new client inherits the session's read-only event guard.
        manager = wiring.build_state(tmp_path / "unused")
        try:
            manager.store.put("forbidden", b"x")
        finally:
            manager.store._client.close()
    assert remote.list() == []
