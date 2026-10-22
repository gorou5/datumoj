"""TACHIBANA m1: real components, with HTTP confined to pytest-httpserver.

Recorded bytes remain unchanged. The synthetic site exercises crawl boundaries;
the recorded CAO page and its 36-page PDF exercise retrieval and provenance.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import date, datetime, timezone
from functools import partial
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner
from werkzeug.wrappers import Response

from datumoj import cli, wiring
from datumoj.core.contracts import ParseStatus, select_parser
from datumoj.core.http import PoliteHttpFetcher
from datumoj.core.models import Citation, DocKind, FetchOutcome, SourceText, TimeBasis, parent_ref
from datumoj.core.registry import RegistryImpl, load_registry
from datumoj.core.runner import CrawlDeps
from datumoj.core.schedule import read_control
from datumoj.crawlers.gov_web import GovWebCrawler
from datumoj.index.hybrid import HybridRetriever
from datumoj.testing.contract_suites import ParserSuite
from datumoj.testing.samples import ParseSample, make_snapshot

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/gov_web"
SITE = FIXTURES / "html/synthetic/site"
SOURCE = "jp-gov-web"
BASE = "https://gov-web.test"
PREFIX = "/policy/startup/"
SEED = PREFIX + "index.html"
DETAIL = PREFIX + "detail-a.html"
DEEPER = PREFIX + "deeper/detail-b.html"
GUIDE = PREFIX + "guide.pdf"
CAO = "/cstp/openinnovation/procurement/index.html"
PDF = "/cstp/openinnovation/procurement/guideline.pdf"
DATED = "/dated/index.html"
NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
LAST_MODIFIED = "Wed, 21 Oct 2026 00:00:00 GMT"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _spec(seeds, **scope):
    original = load_registry(ROOT / "sources").get(SOURCE)
    return original.model_copy(update={
        "base_url": BASE, "allowed_hosts": ["gov-web.test"], "rate_per_sec": 1000,
        "scope": {**original.scope, "seeds": seeds, **scope},
    })


def _seed(path):
    return {"url": BASE + path, "label": "Offline fixture"}


@pytest.fixture
def chain_factory(httpserver, tmp_path, monkeypatch):
    """Only transport, clocks, source URLs and optional vectors are injected."""
    real_build = wiring.build
    handle_request = httpx.HTTPTransport.handle_request
    local_url = httpx.URL(httpserver.url_for("/"))

    def local_only(transport, request):
        assert (request.url.scheme, request.url.host, request.url.port) == (
            local_url.scheme, local_url.host, local_url.port,
        ), "integration attempted an external HTTP request"
        return handle_request(transport, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", local_only)
    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    opened = []
    with httpx.Client(trust_env=False, timeout=5) as local:
        def start(name, spec, routes):
            evidence = Path(os.environ.get("DATUMOJ_EVIDENCE_DIR", str(tmp_path / "evidence"))) / tmp_path.name / name
            evidence.mkdir(parents=True, exist_ok=True)
            requests, commands = [], []
            for path, (body, media) in routes.items():
                def respond(request, body=body, media=media, path=path):
                    headers = {"ETag": '"' + _sha(body) + '"', "Last-Modified": LAST_MODIFIED}
                    cached = request.headers.get("If-None-Match") == headers["ETag"]
                    requests.append({
                        "path": path, "headers": dict(request.headers),
                        "status": 304 if cached else 200, "body_bytes": 0 if cached else len(body),
                    })
                    return Response(status=304, headers=headers) if cached else Response(
                        body, headers=headers, content_type=media,
                    )

                httpserver.expect_request(path).respond_with_handler(respond)

            def forward(request):
                assert request.url.host == "gov-web.test"
                assert request.url.path in routes, f"unregistered fixture URL: {request.url}"
                assert not request.url.query, f"unnormalized fixture URL: {request.url}"
                response = local.get(httpserver.url_for(request.url.path), headers={
                    key: value for key, value in request.headers.items() if key != "host"
                })
                return httpx.Response(response.status_code, headers=response.headers,
                                      stream=httpx.ByteStream(response.content))

            class LocalFetcherFactory:
                @staticmethod
                def from_spec(spec):
                    return PoliteHttpFetcher.from_spec(
                        spec, transport=httpx.MockTransport(forward), now=lambda: NOW,
                        clock=lambda: 0.0, sleep=lambda _: None,
                    )

            def build(path):
                deps = real_build(path, {
                    "registry": RegistryImpl([spec]), "fetcher": LocalFetcherFactory,
                    "crawl_deps_type": partial(CrawlDeps, now=lambda: NOW, clock=lambda: 0.0, sleep=lambda _: None),
                    "vectors": None,
                })
                opened.append(deps)
                return deps

            monkeypatch.setattr(cli.wiring, "build", build)
            runner = CliRunner()

            def invoke(*args):
                result = runner.invoke(cli.app, list(args))
                commands.append({"args": args, "exit_code": result.exit_code, "output": result.output})
                (evidence / "commands.json").write_text(
                    json.dumps(commands, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                )
                (evidence / "requests.json").write_text(
                    json.dumps(requests, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                )
                return result

            return build(tmp_path / "data"), invoke, requests

        yield start
    for deps in opened:
        for name in ("manifest", "chunks", "keyword", "catalog"):
            getattr(deps, name).close()
    httpserver.check_assertions()


def _success(invoke, *args):
    result = invoke(*args)
    assert result.exit_code == 0, result.output
    return result


@pytest.fixture
def policy_chain(chain_factory):
    routes = {
        "/robots.txt": (b"User-agent: *\nDisallow:\n", "text/plain"),
        # Deliberately incorrect headers: the bytes must choose the parser.
        CAO: ((FIXTURES / "html/cao/detail-1.html").read_bytes(), "application/pdf"),
        PDF: ((FIXTURES / "pdf/text.pdf").read_bytes(), "text/html"),
        DATED: ((FIXTURES / "html/synthetic/dated-wareki.html").read_bytes(), "application/octet-stream"),
    }
    deps, invoke, requests = chain_factory("policy", _spec([_seed(CAO), _seed(DATED)], max_depth=0), routes)
    first = _success(invoke, "crawl", SOURCE)
    assert "新增 3，未变化 0，失败 0" in first.output
    _success(invoke, "parse", SOURCE)
    _success(invoke, "index", "--rebuild")
    return deps, invoke, requests


def test_registry_kind_and_default_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    deps = wiring.build(tmp_path, {"parsers": None})
    try:
        spec = deps.registry.get(SOURCE)
        assert spec.kind == GovWebCrawler.kind
        assert deps.crawler_classes[spec.kind] is GovWebCrawler
        assert select_parser(deps.tools.parsers, spec.kind, "application/pdf") is wiring._GovWebContent
        control = read_control(tmp_path)
        assert control["enabled"] is False
        assert control["sources"].get(SOURCE, {}).get("enabled", False) is False
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog"):
            getattr(deps, name).close()


def test_page_sections_pdf_pages_and_restorable_citations(policy_chain):
    deps, invoke, _ = policy_chain
    assert isinstance(deps.retriever, HybridRetriever)
    assert deps.pipeline_deps.parsers is None  # No injected parser or fake storage/index.
    chunks = list(deps.chunks.iter_chunks([SOURCE]))
    documents = {chunk.doc_key: deps.chunks.get_document(chunk.doc_key) for chunk in chunks}
    pdf, = [doc for doc in documents.values() if doc.kind is DocKind.pdf]
    page_chunks = deps.chunks.chunks_for(pdf.doc_key)
    assert pdf.metadata["pages"] == "36" and pdf.metadata["needs_ocr_pages"] == "1"
    assert [chunk.locator for chunk in page_chunks] == [f"page={page}" for page in range(1, 37) if page != 25]
    parent = deps.manifest.last_success(BASE + CAO)
    assert parent_ref(pdf.metadata) == (BASE + CAO, parent.sha256)
    pdf_snapshot = deps.manifest.last_success(BASE + PDF)
    assert parent_ref(deps.manifest.snapshot_meta(pdf_snapshot.snapshot_id)) == parent_ref(pdf.metadata)
    for snap in deps.manifest.snapshots_for(SOURCE):
        assert snap.outcome is FetchOutcome.ok
        deps.raw.verify(snap.sha256)
        record = deps.manifest.get_parse(snap.sha256, wiring._GovWebContent.name, wiring._GovWebContent.version)
        assert record.status is ParseStatus.ok

    cases = [
        ("スタートアップからの公共調達", CAO,
         "section=スタートアップによる公共調達の促進や研究開発の活用に係る取組", TimeBasis.no_data_date, None),
        ("補助対象経費", DATED, "section=スタートアップ支援事業のご案内 > 事業の概要 > 補助額",
         TimeBasis.published, date(2025, 4, 1)),
        ("適正なアプローチに基づく連携の促進", PDF, "page=1", TimeBasis.no_data_date, None),
    ]
    for query, path, locator, basis, published in cases:
        result = deps.tools.search_docs(query, sources=SOURCE, k=20)
        hit = next(h for h in result["data"]["hits"]
                   if h["citation"]["resource_url"] == BASE + path and h["locator"] == locator)
        citation = Citation.model_validate(hit["citation"])
        assert citation.source_id == SOURCE and citation.fetched_at == NOW
        assert citation.time_basis is basis and citation.published_at == published
        assert citation.effective_at is None and citation.period is None
        assert citation.usage_scope == deps.registry.get(SOURCE).license.usage_scope
        assert _sha(deps.raw.get(citation.snapshot_sha256)) == citation.snapshot_sha256
        restored = deps.tools.cite(citation.snapshot_sha256, citation.locator)
        assert restored["data"]["status"] == "ok"
        passage, = restored["data"]["passages"]
        assert query in "".join(SourceText.model_validate(passage["text"]).content.split())
        assert SourceText.model_validate(hit["snippet"]).content
        human = _success(invoke, "search", query, "--source", SOURCE, "-k", "20")
        assert citation.render() in human.output


def _versions(deps):
    with sqlite3.connect(deps.data_dir / "chunks.sqlite") as db:
        return db.execute("SELECT doc_key, snapshot_sha256, document_json FROM documents ORDER BY doc_key").fetchall()


def test_conditional_rerun_downloads_no_bodies_or_document_versions(policy_chain):
    deps, invoke, requests = policy_chain
    before = _versions(deps)
    assert len(before) == 3
    raw_before = {p.relative_to(deps.data_dir): p.stat().st_mtime_ns
                  for p in (deps.data_dir / "raw").rglob("*") if p.is_file()}
    requests.clear()
    second = _success(invoke, "crawl", SOURCE)
    assert "新增 0，未变化 2，失败 0" in second.output
    downloads = [r for r in requests if r["path"] != "/robots.txt"]
    assert {r["path"] for r in downloads} == {CAO, DATED}
    assert len(downloads) == 2
    assert all(
        deps.manifest.latest_snapshot(BASE + path).outcome is FetchOutcome.not_modified
        for path in (CAO, DATED)
    )
    parsed = _success(invoke, "parse", SOURCE)
    assert "成功 0" in parsed.output
    _success(invoke, "index", "--rebuild")
    assert _versions(deps) == before
    assert {p.relative_to(deps.data_dir): p.stat().st_mtime_ns
            for p in (deps.data_dir / "raw").rglob("*") if p.is_file()} == raw_before
    assert len(deps.manifest.snapshots_for(SOURCE)) == 5


def test_crawl_boundaries_and_failures_are_not_successes(chain_factory):
    routes = {"/" + path.relative_to(SITE).as_posix(): (path.read_bytes(), "text/html")
              for path in SITE.rglob("*") if path.is_file()}
    routes["/spa/index.html"] = ((FIXTURES / "html/spa/shell.html").read_bytes(), "text/html")
    routes["/empty/index.html"] = (b"", "text/html")
    spec = _spec([_seed(SEED), _seed("/spa/index.html"), _seed("/empty/index.html")])
    deps, invoke, requests = chain_factory("boundaries", spec, routes)
    result = invoke("crawl", SOURCE)
    assert result.exit_code == 1, result.output
    assert "新增 4，未变化 0，失败 3" in result.output
    paths = [r["path"] for r in requests]
    assert set(paths) == {"/robots.txt", SEED, DETAIL, DEEPER, GUIDE, "/spa/index.html", "/empty/index.html"}
    assert paths.count(DETAIL) == paths.count(GUIDE) == 1  # fragment/tracking/PDF duplicates
    snapshots = deps.manifest.snapshots_for(SOURCE)
    failures = {s.resource_url: s.outcome for s in snapshots if s.outcome is not FetchOutcome.ok}
    assert failures == {
        BASE + PREFIX + "private/internal.html": FetchOutcome.blocked_by_robots,
        BASE + "/spa/index.html": FetchOutcome.spa_shell,
        BASE + "/empty/index.html": FetchOutcome.empty_body,
    }
    # These short synthetic detail pages pass crawl classification (chrome is long)
    # but have <100 body characters after HTML chrome removal. Parsing must fail.
    parsed = invoke("parse", SOURCE)
    assert parsed.exit_code == 1 and "empty_content" in parsed.output
    for path in (DETAIL, DEEPER):
        snap = deps.manifest.last_success(BASE + path)
        record = deps.manifest.get_parse(snap.sha256, wiring._GovWebContent.name, wiring._GovWebContent.version)
        assert record.status is ParseStatus.failed
    assert len(_versions(deps)) == 2  # Only seed HTML and its PDF entered the store.
    indexed = invoke("index", "--rebuild")
    assert indexed.exit_code == 1 and "解析失败" in indexed.output
    for query in ("内部資料", "申請要領"):
        hits = deps.tools.search_docs(query, sources=SOURCE)["data"]["hits"]
        assert all(hit["citation"]["resource_url"] in {BASE + SEED, BASE + GUIDE} for hit in hits)


class TestGovWebContentContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return wiring._GovWebContent()

    @pytest.fixture
    def samples(self):
        return [ParseSample(make_snapshot(source_id=SOURCE, url=BASE + "/download", data=data,
                                         content_type="application/octet-stream"), data)
                for data in ((FIXTURES / "html/soumu/detail-1.html").read_bytes(),
                             (FIXTURES / "pdf/text.pdf").read_bytes())]


@pytest.mark.parametrize("media", ["text/html", "application/pdf", "application/octet-stream", None])
def test_content_dispatch_ignores_headers_and_extension(media):
    parsers = wiring._connected_parsers()
    parser = select_parser(parsers, "gov_web", media)()
    for path, kind in (("html/cao/detail-1.html", DocKind.html_page), ("pdf/text.pdf", DocKind.pdf)):
        data = (FIXTURES / path).read_bytes()
        snap = make_snapshot(source_id=SOURCE, url=BASE + "/misleading.txt", data=data, content_type=media)
        parsed = parser.parse(snap, data)
        assert parsed.documents[0].kind is kind
        assert parsed.chunks
