"""WISTERIA-104: private exception text never enters persisted/shared failures."""

import json
import logging
import sqlite3
import threading
from types import SimpleNamespace

import httpx
import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from datumoj.rikolto import cli, mcp_server, pipeline, wiring
from datumoj.rikolto.admin import web
from datumoj.rikolto.core.budget import BudgetExceeded
from datumoj.rikolto.core.contracts import ParseError, ParseStatus
from datumoj.rikolto.core.errors import MAX_REASON_LENGTH, safe_reason
from datumoj.rikolto.core.http import PoliteHttpFetcher
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import PublishState, parse_context_fingerprint
from datumoj.rikolto.core.runner import CrawlDeps, run_crawl
from datumoj.rikolto.core.schedule import Scheduler, set_enabled
from datumoj.rikolto.pipeline import PipelineDeps, parse_pending, vector_status
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeCrawler,
    FakeEmbedder,
    FakeHttpFetcher,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
    FakeVectorIndex,
)
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec

# Synthetic markers, never actual machine paths or source text.
PRIVATE = "private-dir/document.pdf SOURCE_TEXT_SENTINEL_104\n\x1b[31m"


@pytest.mark.parametrize("exc", [OSError(PRIVATE), RuntimeError(PRIVATE), ParseError(PRIVATE)])
def test_unknown_messages_are_not_rendered(exc):
    assert safe_reason(exc) == type(exc).__name__


def test_safe_reason_avoids_custom_str_and_bounds_and_cleans_type_name():
    class UntrustedError(Exception):
        def __str__(self):
            raise AssertionError("safe_reason must not render arbitrary exception text")

    assert safe_reason(UntrustedError(PRIVATE)) == "UntrustedError"
    odd_type = type("X\n\x1b\u2028" + "a" * MAX_REASON_LENGTH, (Exception,), {})
    reason = safe_reason(odd_type(PRIVATE))
    assert reason == "X" + "a" * (MAX_REASON_LENGTH - 1)
    assert reason.isprintable()


@pytest.mark.parametrize("item", ["pages", "text_chars", "output_chunks", "duration_seconds"])
def test_budget_whitelist_keeps_original_stop_code(item):
    assert safe_reason(BudgetExceeded(item)) == f"budget_exceeded:{item}"


def test_whitelist_requires_exact_type_and_exact_message():
    class DerivedBudget(BudgetExceeded):
        pass

    assert safe_reason(BudgetExceeded(PRIVATE)) == "BudgetExceeded"
    assert safe_reason(DerivedBudget("pages")) == "DerivedBudget"
    assert safe_reason(RuntimeError("budget_exceeded:pages")) == "RuntimeError"
    assert safe_reason(ParseError("PDF 没有页面")) == "ParseError: PDF 没有页面"
    assert safe_reason(ParseError("PDF 没有页面" + PRIVATE)) == "ParseError"
    assert safe_reason(ParseError("empty_content: HTML 页面正文少于 100 字")) == (
        "ParseError: empty_content: HTML 页面正文少于 100 字"
    )


@pytest.fixture
def deps(tmp_path):
    manifest = SqliteManifest(tmp_path / "manifest.sqlite")
    result = PipelineDeps(
        manifest=manifest, raw=FakeRawStore(), registry=FakeRegistry([make_spec()]),
        chunks=FakeChunkStore(), keyword=FakeKeywordIndex(), observations=FakeObservationStore(),
        catalog=FakeCatalogStore(), parsers={("fake", "text/plain"): FakeParser},
        vector_state_path=tmp_path / "vectors.json",
    )
    yield result
    manifest.close()


def add_snapshot(deps, sid):
    data = f"Title {sid}\n\nBody {sid}".encode()
    deps.raw.put(data)
    snap = make_snapshot(data=data, snapshot_id=sid, url=f"https://example.go.jp/{sid}.txt")
    deps.manifest.record_snapshot(snap)
    return snap


def test_parse_failure_is_persisted_safely_and_other_snapshot_still_publishes(deps, tmp_path, monkeypatch, caplog):
    class SelectiveParser(FakeParser):
        def parse(self, snapshot, data, meta=None):
            if snapshot.snapshot_id == "bad":
                raise RuntimeError(PRIVATE)
            return super().parse(snapshot, data, meta)

    bad = add_snapshot(deps, "bad")
    good = add_snapshot(deps, "good")
    deps.parsers = {("fake", "text/plain"): SelectiveParser}

    def broken_change_detection(*_):
        raise OSError(PRIVATE)

    deps.change_log = object()
    monkeypatch.setattr(pipeline, "_record_fetch_changes", broken_change_detection)
    monkeypatch.setattr(cli.wiring, "build", lambda _: wiring.Wiring(
        data_dir=tmp_path, pipeline_deps=deps, parse_pending=parse_pending,
    ))
    with caplog.at_level(logging.WARNING):
        result = CliRunner().invoke(cli.app, ["--data-dir", str(tmp_path), "parse"])
    assert result.exit_code == 1, result.output
    assert "RuntimeError" in result.output
    assert "成功 1" in result.output
    assert deps.manifest.publish_state(bad.snapshot_id) is PublishState.failed
    assert deps.manifest.publish_state(good.snapshot_id) is PublishState.published
    record = deps.manifest.get_parse(
        bad.sha256, FakeParser.name, FakeParser.version,
        parse_context_fingerprint(bad, deps.manifest.snapshot_meta(bad.snapshot_id)),
    )
    assert record.status is ParseStatus.failed and record.error == "RuntimeError"
    with sqlite3.connect(tmp_path / "manifest.sqlite") as db:
        stored = "\n".join(db.iterdump())
    for text in (stored, result.output, caplog.text):
        assert "private-dir/document.pdf" not in text
        assert "SOURCE_TEXT_SENTINEL_104" not in text
    assert "OSError" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_vector_state_and_cli_status_use_safe_reason(deps, tmp_path, monkeypatch):
    class BrokenVectors(FakeVectorIndex):
        def add(self, chunks):
            raise OSError(PRIVATE)

    add_snapshot(deps, "vector")
    deps.vectors = BrokenVectors(FakeEmbedder())
    summary = parse_pending(deps)
    assert summary.vectors_missing == {"vector": "OSError"}
    assert vector_status(deps)["error"] == "OSError"
    stored = deps.vector_state_path.read_text()
    assert json.loads(stored)["last_error"] == "OSError"
    assert "SOURCE_TEXT_SENTINEL_104" not in stored
    monkeypatch.setattr(cli.wiring, "build", lambda _: wiring.Wiring(
        data_dir=tmp_path, pipeline_deps=deps, vectors=deps.vectors,
        tools=type("Tools", (), {"coverage": lambda _: {"data": {"sources": []}, "warnings": []}})(),
    ))
    result = CliRunner().invoke(cli.app, ["--data-dir", str(tmp_path), "status"])
    assert result.exit_code == 0, result.output
    assert "OSError" in result.output
    assert "SOURCE_TEXT_SENTINEL_104" not in result.output


def test_scheduler_ledger_and_plugin_stop_reason_hide_private_text(tmp_path):
    spec = make_spec(schedule="daily")

    def fail(_):
        raise OSError(PRIVATE)

    scheduler = Scheduler(tmp_path, [spec], fail, now=lambda: T0)
    set_enabled(tmp_path, True)
    set_enabled(tmp_path, True, [spec.id])
    assert scheduler.run_now(spec.id)["stop_reason"] == "OSError"
    ledger = (tmp_path / "runs.jsonl").read_text()
    assert "SOURCE_TEXT_SENTINEL_104" not in ledger
    assert "private-dir/document.pdf" not in ledger

    class BrokenCrawler(FakeCrawler):
        def plan(self, ctx):
            raise RuntimeError(PRIVATE)

    report = run_crawl(spec, BrokenCrawler(), CrawlDeps(
        raw=FakeRawStore(), manifest=scheduler.manifest, fetcher=FakeHttpFetcher({}),
    ))
    assert report.stop_reason == "plan 失败：RuntimeError"


def test_http_transport_failure_and_mcp_boundary_hide_private_text():
    def transport(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectError(PRIVATE)

    with PoliteHttpFetcher(["example.go.jp"], transport=httpx.MockTransport(transport),
                           max_retries=0, sleep=lambda _: None) as fetcher:
        result = fetcher.fetch("https://example.go.jp/data")
    assert "ConnectError" in result.error
    assert "SOURCE_TEXT_SENTINEL_104" not in result.error

    @mcp_server._guarded
    def fail():
        raise RuntimeError(PRIVATE)

    with pytest.raises(mcp_server.ToolError, match="^RuntimeError$"):
        fail()


def test_admin_worker_start_failure_hides_private_text(tmp_path, monkeypatch):
    class BrokenThread:
        def __init__(self, **_):
            raise RuntimeError(PRIVATE)

    spec = make_spec(schedule="manual")
    deps = SimpleNamespace(registry=FakeRegistry([spec]), manifest=FakeManifest(), raw=FakeRawStore())
    scheduler = Scheduler(tmp_path, [spec], lambda _: None, now=lambda: T0)
    set_enabled(tmp_path, True)
    set_enabled(tmp_path, True, [spec.id])
    monkeypatch.setattr(web, "threading", SimpleNamespace(Thread=BrokenThread, Event=threading.Event))
    app = web.create_app(tmp_path, deps, scheduler, token="test-token")
    with TestClient(app) as client:
        response = client.post("/", data={"csrf": "test-token", "action": "run", "source_id": spec.id})
    assert response.status_code == 500
    assert "后台运行失败：RuntimeError" in response.text
    assert "SOURCE_TEXT_SENTINEL_104" not in response.text
    assert "private-dir/document.pdf" not in response.text
