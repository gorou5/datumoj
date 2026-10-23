"""TACHIBANA m2: real wiring, fixture HTTP, keyword gates and loopback moto.

All generated discovery and reports stay in temporary directories by default.
BODIK offsets 15..60 are explicitly synthetic pagination scaffolding: only the
17 recorded facility records are evidence about the actual dataset.
"""

from __future__ import annotations

import csv
import json
import os
import runpy
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import boto3
import httpx
import openpyxl
import pytest
from moto.server import ThreadedMotoServer
from typer.testing import CliRunner

from datumoj import cli, wiring
from datumoj.core.models import parse_context_fingerprint
from datumoj.core.contracts import ParseRecord, ParseResult, ParseStatus
from datumoj.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.core.models import Citation, GapState, SourceText
from datumoj.core.rawstore import FileRawStore
from datumoj.core.rawstore_s3 import S3RawStore
from datumoj.core.registry import RegistryImpl, load_registry
from datumoj.core.runner import CrawlDeps
from datumoj.eval import check_gate, evaluate, load_questions, write_reports
from datumoj.testing.contract_suites import ParserSuite
from datumoj.testing.samples import ParseSample, make_snapshot
from tests.integration.test_full_eval import AnnouncementParser, _ivy_questions, _seed

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures"
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
RESOURCE = "a6e50db4-8984-43b2-b477-99de98f8efab"


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _close(deps):
    for name in ("manifest", "chunks", "keyword", "catalog"):
        getattr(deps, name).close()
    if isinstance(deps.raw, S3RawStore):
        deps.raw._client.close()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Government HTTP must use ReplayTransport")

    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)


@pytest.fixture(scope="module")
def evidence(tmp_path_factory):
    base = os.environ.get("DATUMOJ_EVIDENCE_DIR")
    directory = Path(base) / "tachibana-m2" if base else tmp_path_factory.mktemp("tachibana-m2-evidence")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _crawl(deps, source, routes, *, limit=None):
    spec = deps.registry.get(source)
    transport = ReplayTransport(routes)
    with PoliteHttpFetcher.from_spec(
        spec, transport=transport, now=lambda: NOW, clock=lambda: 0.0, sleep=lambda seconds: None,
    ) as fetcher:
        report = deps.run_crawl(spec, deps.crawler_classes[spec.kind](), CrawlDeps(
            raw=deps.raw, manifest=deps.manifest, fetcher=fetcher,
            now=lambda: NOW, clock=lambda: 0.0, sleep=lambda seconds: None,
        ), limit=limit)
    assert not report.failures, report
    assert report.stop_reason is None, report
    return transport.requests


def _parse_index(deps, source):
    report = deps.parse_pending(deps.pipeline_deps, source)
    assert not report.failed, report
    assert not report.no_parser, report
    deps.reindex(deps.pipeline_deps)
    return report


def _restore(deps, item):
    citation = Citation.model_validate(item["citation"])
    deps.raw.verify(citation.snapshot_sha256)
    assert citation.fetched_at == NOW
    restored = deps.tools.cite(citation.snapshot_sha256, citation.locator)["data"]
    assert restored["status"] == "ok", restored
    assert restored["passages"]
    return citation, restored["passages"]


def test_bodik_catalog_and_datastore_snapshots(tmp_path, evidence):
    original = load_registry(ROOT / "sources")
    spec = original.get("jp-bodik")
    # Directory replay retains both full original package pages. The second run
    # projects discovery to the sole resource with recorded DataStore responses.
    spec = spec.model_copy(update={"scope": {
        **spec.scope, "formats": [], "datastore": False,
    }})
    registry = RegistryImpl([spec])
    deps = wiring.build(tmp_path / "bodik", {"registry": registry})
    base = spec.base_url.rstrip("/")
    routes = {"https://data.bodik.jp/robots.txt": Recorded(status=404)}
    packages = []
    for start, name in ((0, "p1"), (3, "p2")):
        path = FIXTURES / f"bodik/package_search-{name}.json"
        packages.extend(json.loads(path.read_bytes())["result"]["results"])
        query = urlencode({"q": spec.scope["q"], "rows": 3, "start": start, "sort": "id asc"}, quote_via=quote)
        routes[f"{base}/package_search?{query}"] = Recorded(path)
    query = urlencode({"q": spec.scope["q"], "rows": 3, "start": 6, "sort": "id asc"}, quote_via=quote)
    routes[f"{base}/package_search?{query}"] = Recorded(_json(tmp_path / "empty.synthetic.json", {
        "success": True, "result": {"results": []},
    }))
    try:
        requests = _crawl(deps, spec.id, routes)
        _parse_index(deps, spec.id)
        found = deps.tools.search_catalog("", sources=spec.id)["data"]["datasets"]
        assert {item["dataset_key"] for item in found} == {f"{spec.id}:{p['id']}" for p in packages}
        for item in found:
            citation, passages = _restore(deps, item)
            package = json.loads(SourceText.model_validate(passages[0]["text"]).content)
            assert item["dataset_key"] == f"{spec.id}:{package['id']}"
            assert citation.locator.startswith("entry=")
        _json(evidence / "bodik-catalog.json", {"requests": requests, "results": found})
    finally:
        _close(deps)

    spec = spec.model_copy(update={"scope": {**spec.scope, "datastore": True, "datastore_limit": 5}})
    deps = wiring.build(tmp_path / "datastore", {"registry": RegistryImpl([spec])})
    projected = json.loads((FIXTURES / "bodik/package_search-p1.json").read_bytes())
    selected = projected["result"]["results"][0]
    selected["resources"] = [selected["resources"][0]]
    projected["result"]["results"] = [selected]
    query = urlencode({"q": spec.scope["q"], "rows": 3, "start": 0, "sort": "id asc"}, quote_via=quote)
    routes[f"{base}/package_search?{query}"] = Recorded(_json(tmp_path / "selected.synthetic.json", projected))
    recorded = {0: "p1", 5: "p2", 10: "p3", 65: "last", 67: "end"}
    for offset in [*range(0, 66, 5), 67]:
        if offset in recorded:
            path = FIXTURES / f"bodik/datastore_search-{recorded[offset]}.json"
        else:
            # No invented government facility names, addresses or numbers.
            path = _json(tmp_path / f"offset-{offset}.synthetic.json", {
                "success": True, "result": {"total": 67, "offset": offset, "limit": 5,
                    "records": [{"_id": i, "synthetic": True} for i in range(offset + 1, offset + 6)]},
            })
        query = urlencode({"resource_id": RESOURCE, "limit": 5, "sort": "_id asc", "offset": offset}, quote_via=quote)
        routes[f"{base}/datastore_search?{query}"] = Recorded(path)
    try:
        requests = _crawl(deps, spec.id, routes, limit=1)
        offsets = [int(parse_qs(urlsplit(url).query)["offset"][0]) for url in requests if "datastore_search?" in url]
        assert offsets == list(range(0, 66, 5))  # 65 is short; neither 67 nor _links.next is requested.
        pages = [s for s in deps.manifest.snapshots_for(spec.id)
                 if deps.manifest.snapshot_meta(s.snapshot_id).get("role") == "datastore"]
        assert len(pages) == 14
        by_offset = {deps.manifest.snapshot_meta(s.snapshot_id)["offset"]: s for s in pages}
        for offset, name in recorded.items():
            if offset == 67:
                continue  # Deliberately routed but unreachable after the short terminal page.
            snap = by_offset[offset]
            assert deps.raw.get(snap.sha256) == (FIXTURES / f"bodik/datastore_search-{name}.json").read_bytes()
            assert deps.manifest.snapshot_meta(snap.snapshot_id)["license"] == "cc-by-40-intl"
        # A failed pre-fix page is retried without changing the parser version.
        failed = by_offset[0]
        deps.manifest.record_parse(ParseRecord(
            sha256=failed.sha256, parser=wiring._CkanPages.name, version=wiring._CkanPages.version,
            status=ParseStatus.failed, error="pre-fix: CKAN package 缺少 id/name",
            context_fingerprint=parse_context_fingerprint(failed, deps.manifest.snapshot_meta(failed.snapshot_id)),
        ))
        parsed = _parse_index(deps, spec.id)
        assert failed.snapshot_id in parsed.retried
        for snap in pages:
            record = deps.manifest.get_parse(
                snap.sha256, wiring._CkanPages.name, wiring._CkanPages.version,
                parse_context_fingerprint(snap, deps.manifest.snapshot_meta(snap.snapshot_id)),
            )
            assert record.status is ParseStatus.ok
        assert [h.dataset.dataset_key for h in deps.catalog.search("")] == [f"{spec.id}:{selected['id']}"]
        _json(evidence / "bodik-datastore.json", {
            "requests": requests, "recorded_offsets": [0, 5, 10, 65],
            "synthetic_offsets": list(range(15, 65, 5)), "snapshots": [s.model_dump(mode="json") for s in pages],
        })
    finally:
        _close(deps)


class TestConnectedCkanParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return wiring._CkanPages()

    @pytest.fixture
    def samples(self):
        samples = []
        for name in ("p1", "p2"):
            data = (FIXTURES / f"bodik/package_search-{name}.json").read_bytes()
            samples.append(ParseSample(make_snapshot("jp-bodik", data=data), data, {"role": "package_search"}))
        return samples

    @pytest.fixture
    def bad_samples(self):
        return [b"not JSON", b'{"success":false,"result":{"results":[]}}']


@pytest.mark.parametrize("role", ["datastore", "resource", "activity"])
def test_ckan_non_catalog_roles_do_not_create_datasets(role):
    data = (FIXTURES / "bodik/datastore_search-p1.json").read_bytes()
    snap = make_snapshot("jp-bodik", data=data)
    assert wiring._CkanPages().parse(snap, data, {"role": role}) == ParseResult()


@pytest.fixture
def table_chain(tmp_path):
    registry = load_registry(ROOT / "sources")
    routes = {}
    for source in ("jp-egov-data", "jp-estat-files"):
        host = httpx.URL(registry.get(source).base_url).host
        routes[f"https://{host}/robots.txt"] = Recorded(status=404)
    spec = registry.get("jp-egov-data")
    spec = spec.model_copy(update={"scope": {**spec.scope, "rows": 5, "formats": []}})
    registry = RegistryImpl([s if s.id != spec.id else spec for s in registry.all()])
    deps = wiring.build(tmp_path / "tables", {"registry": registry})
    for start, suffix in ((0, "start0"), (5, "start5"), (10, "start100000_empty")):
        query = urlencode({"q": spec.scope["q"], "rows": 5, "start": start, "sort": "id asc"}, quote_via=quote)
        routes[f"{spec.base_url.rstrip('/')}/package_search?{query}"] = Recorded(
            FIXTURES / f"ckan_egov/package_search_q-jinko_rows5_{suffix}.json")
    for table in registry.get("jp-estat-files").scope["tables"]:
        path, = (FIXTURES / "estat_files").glob(f"{table['stat_inf_id']}_*")
        kind = 4 if path.suffix == ".xlsx" else 1
        query = urlencode({"statInfId": table["stat_inf_id"], "fileKind": kind})
        routes[f"https://www.e-stat.go.jp/stat-search/file-download?{query}"] = Recorded(
            path, headers={"content-type": "application/octet-stream" if kind == 4 else "text/csv;charset=UTF-8"})
    try:
        for source in ("jp-egov-data", "jp-estat-files"):
            _crawl(deps, source, routes)
            _parse_index(deps, source)
        yield deps
    finally:
        _close(deps)


def test_egov_directory_every_result_restores_original_entry(table_chain, evidence):
    deps = table_chain
    items = deps.tools.search_catalog("", sources="jp-egov-data")["data"]["datasets"]
    assert len(items) == 10
    for item in items:
        citation, passages = _restore(deps, item)
        original = json.loads(deps.raw.get(citation.snapshot_sha256))["result"]["results"][int(citation.locator[6:])]
        assert json.loads(SourceText.model_validate(passages[0]["text"]).content) == original
        assert item["title"]["content"] == original["title"]
    _json(evidence / "egov-catalog.json", items)


def test_estat_titles_and_sql_match_original_cells(table_chain, evidence):
    deps = table_chain
    checked = []
    for table in deps.registry.get("jp-estat-files").scope["tables"]:
        key = f"jp-estat-files:{table['stat_inf_id']}"
        items = deps.tools.search_catalog(table["title"], sources="jp-estat-files")["data"]["datasets"]
        item = next(item for item in items if item["dataset_key"] == key)
        citation, passages = _restore(deps, item)
        assert any(p["kind"] == "chunk" for p in passages)
        assert item["state"] == "observations_loaded"
        path, = (FIXTURES / "estat_files").glob(f"{table['stat_inf_id']}_*")
        if path.suffix == ".xlsx":
            book = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                expected = Decimal(str(book.worksheets[0]["E24"].value))
                locator = f"sheet={book.worksheets[0].title};cell=E24"
            finally:
                book.close()
        else:
            expected = Decimal(list(csv.reader(path.read_bytes().decode("cp932").splitlines()))[976][8])
            locator = "cell=I977;row=977"
        result = deps.tools.query_sql(
            f"SELECT obs_id, value FROM observations WHERE table_key = '{table['stat_inf_id']}' AND locator = '{locator}'"
        )["data"]
        assert len(result["rows"]) == len(result["provenance"]) == 1
        assert Decimal(result["rows"][0][1]) == expected
        _, passages = _restore(deps, result["provenance"][0])
        assert any(p["kind"] == "observation" and Decimal(p["value"]) == expected for p in passages)
        checked.append({"table": key, "title_citation": citation.model_dump(mode="json"),
                        "value": expected, "locator": locator})
    missing = deps.tools.query_sql(
        "SELECT obs_id, value, note FROM observations WHERE table_key = '000031720773' AND locator = 'cell=L15;row=15'"
    )["data"]
    assert missing["rows"][0][1:] == [None, {"content": "-", "content_role": "untrusted_source_text"}]
    _json(evidence / "estat-values.json", checked)


def test_keyword_only_ivy_gates_and_behavior(tmp_path, evidence):
    registry = RegistryImpl([
        *load_registry(ROOT / "sources").all(),
        *load_registry(FIXTURES / "eval_extra/sources").all(),
    ])
    parsers = wiring._connected_parsers()
    parsers[("eval_fixture", "text/plain")] = AnnouncementParser
    deps = wiring.build(tmp_path / "keyword", {"registry": registry, "parsers": parsers})
    try:
        assert deps.vectors is None and deps.retriever.vector is None
        _seed(deps, evidence)
        _parse_index(deps, None)
        questions = load_questions(_ivy_questions(tmp_path))
        assert len(questions) >= 60

        def cite(citation):
            restored = deps.tools.cite(citation.snapshot_sha256, citation.locator)["data"]
            assert restored["status"] == "ok"
            return restored["passages"]

        report = evaluate(questions, retriever=deps.retriever, observation_store=deps.observations,
                          cite=cite, manifest=deps.manifest, catalog=deps.catalog)
        write_reports(report, evidence / "keyword")
        gate = check_gate(report)
        _json(evidence / "keyword-gate.json", vars(gate))
        assert gate.passed, (gate.failures, report.failures)
        for metric, threshold in gate.thresholds.items():
            assert report.overall[metric] >= threshold
        zero = [q for q in questions if Path(q._source_file).name == "behavior.yaml" and q.expect.gap_state is GapState.zero_hits]
        assert len(zero) == 2
        for question in zero:
            found = deps.tools.search_docs(question.queries, sources=question.source_ids)
            assert found["gap_state"] == "zero_hits" and not found["data"]["hits"]
    finally:
        _close(deps)


def test_s3_law_chain_matches_local_raw(tmp_path, monkeypatch, evidence):
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    endpoint = f"http://{host}:{port}"
    # All boto traffic, including retries, must stay at this exact moto endpoint.
    import botocore.httpsession
    send = botocore.httpsession.URLLib3Session.send

    def local_only(session, request):
        assert urlsplit(request.url).netloc == urlsplit(endpoint).netloc
        return send(session, request)

    monkeypatch.setattr(botocore.httpsession.URLLib3Session, "send", local_only)
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "synthetic-test-id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "synthetic-test-secret")
    monkeypatch.setenv("R2_ENDPOINT", endpoint)
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", "tachibana-m2")
    monkeypatch.setenv("DATUMOJ_S3_REGION", "us-east-1")
    client = boto3.client("s3", endpoint_url=endpoint, region_name="us-east-1",
                          aws_access_key_id="synthetic-test-id", aws_secret_access_key="synthetic-test-secret")
    try:
        client.create_bucket(Bucket="tachibana-m2")
        api = "https://laws.e-gov.go.jp/api/2"
        body = FIXTURES / "egov_law/law_data.321CONSTITUTION.asof2026-10-20.xml-in-json.json"
        payload = json.loads(body.read_bytes())
        discovery = _json(tmp_path / "laws.synthetic.json", {
            "total_count": 1, "count": 1, "laws": [{"law_info": payload["law_info"],
                "revision_info": payload["revision_info"], "current_revision_info": payload["revision_info"]}],
        })
        routes = {
            "https://laws.e-gov.go.jp/robots.txt": Recorded(status=404),
            f"{api}/laws?limit=100&offset=0": Recorded(discovery),
            f"{api}/law_revisions/321CONSTITUTION": Recorded(FIXTURES / "egov_law/law_revisions.321CONSTITUTION.json"),
            f"{api}/law_data/321CONSTITUTION?asof=2026-10-20&response_format=json&law_full_text_format=xml": Recorded(body),
        }
        outputs = []
        for backend, store_type in (("local", FileRawStore), ("s3", S3RawStore)):
            monkeypatch.setenv("DATUMOJ_RAW_BACKEND", backend)
            deps = wiring.build(tmp_path / backend)
            try:
                assert isinstance(deps.raw, store_type)
                requests = _crawl(deps, "jp-egov-law", routes)
                _parse_index(deps, "jp-egov-law")
                found = deps.tools.search_docs("表現の自由", sources="jp-egov-law")
                assert found["data"]["hits"]
                hit = next(h for h in found["data"]["hits"] if h["locator"] == "art=21")
                _, passages = _restore(deps, hit)
                assert "表現の自由" in passages[0]["text"]["content"]
                for snap in deps.manifest.snapshots_for("jp-egov-law"):
                    deps.raw.verify(snap.sha256)
                    if backend == "s3":
                        assert client.get_object(Bucket="tachibana-m2", Key=f"raw/{snap.sha256[:2]}/{snap.sha256}")["Body"].read() == deps.raw.get(snap.sha256)
                outputs.append({"requests": requests, "search": found, "passages": passages})
                if backend == "s3":
                    assert not (deps.data_dir / "raw").exists()
            finally:
                _close(deps)
        assert outputs[0] == outputs[1]
        _json(evidence / "s3-local-parity.json", {"endpoint": endpoint, "result": outputs[1]})
    finally:
        client.close()
        server.stop()


def test_registry_cli_export_matches_committed_catalog(tmp_path, evidence):
    out = tmp_path / "catalog"
    result = CliRunner().invoke(cli.app, ["registry", "export", "--out", str(out)])
    assert result.exit_code == 0, result.output
    expected = {p.name: p.read_bytes() for p in (ROOT / "catalog").glob("*.json")}
    assert {p.name: p.read_bytes() for p in out.glob("*.json")} == expected
    # Run the repository validator itself; it reads the committed catalog whose
    # exact bytes we have just matched, without exporting into tracked paths.
    runpy.run_path(str(ROOT / "tests/validate_schemas.py"), run_name="__main__")
    _json(evidence / "registry-export.json", {"output": result.output, "files": sorted(expected)})
