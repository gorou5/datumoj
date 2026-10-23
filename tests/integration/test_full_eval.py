"""M3: original fixture bytes → real pipeline/stores/indices/tools → eval and MCP.

Corpus selection is independent of the questions. Negative parser fixtures and
legacy Dashboard API formats are covered by their own suites, not ingested here.
Reports only leave pytest's temporary directories when DATUMOJ_EVIDENCE_DIR is set.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import threading
import weakref
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest
import yaml
from mcp import Client
from typer.testing import CliRunner

from datumoj import cli, mcp_server, wiring
from datumoj.core.models import parse_context_fingerprint
from datumoj.analysis.tools import AnalysisTools
from datumoj.core.contracts import ParseError, ParseResult, Parser, ParseStatus, discover_parsers, select_parser
from datumoj.core.manifest import SqliteManifest
from datumoj.core.models import Chunk, Document, FetchOutcome, MatchedBy, Snapshot, SourceText
from datumoj.core.rawstore import FileRawStore
from datumoj.core.registry import RegistryImpl, load_registry
from datumoj.crawlers.egov_law import pending_amendments
from datumoj.eval import check_gate, evaluate, load_questions, write_reports
from datumoj.index.embed import HashEmbedder
from datumoj.index.hybrid import HybridRetriever
from datumoj.index.vector import LanceVectorIndex
from datumoj.pipeline import vector_status
from datumoj.store.catalog import SqliteCatalogStore
from datumoj.store.observations import DuckDBObservationStore
from datumoj.testing.contract_suites import ParserSuite
from datumoj.testing.samples import ParseSample, make_snapshot

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures"
IVY_BANKS = ("law.yaml", "stats.yaml", "catalog.yaml", "behavior.yaml")
EXTRA = FIXTURES / "eval_extra"
NOW = datetime(2026, 10, 21, tzinfo=timezone.utc)


class AnnouncementParser(Parser):
    """Only the explicitly synthetic eval_fixture source uses this test parser."""

    name = "eval-announcement"
    version = "1"
    source_kind = "eval_fixture"
    content_type = "text/plain"

    def parse(self, snapshot, data, meta=None):
        try:
            text = data.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ParseError("合成公告必须是 UTF-8") from exc
        if not text or "\x00" in text:
            raise ParseError("合成公告为空或包含 NUL")
        key = f"{snapshot.source_id}:announcement"
        doc = Document(
            doc_key=key, source_id=snapshot.source_id, snapshot_sha256=snapshot.sha256,
            title=text.splitlines()[0], lang="ja", content_type="text/plain",
        )
        chunk = Chunk(
            chunk_id=f"{key}#entry=notice-2026-10-20", doc_key=key,
            locator="entry=notice-2026-10-20", text=text, title_path=[doc.title],
            snapshot_sha256=snapshot.sha256, lang="ja",
        )
        return ParseResult(documents=[doc], chunks=[chunk])


class TestAnnouncementParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return AnnouncementParser()

    @pytest.fixture
    def samples(self):
        data = (EXTRA / "announcement-injection.synthetic.txt").read_bytes()
        return [ParseSample(make_snapshot("jp-eval-injection", data=data, content_type="text/plain"), data)]

    @pytest.fixture
    def bad_samples(self):
        return [b"\x89PNG\r\n", b"", b"\x00"]


def _json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _close(stores):
    for store in stores:
        store.close()


def _stores(deps):
    return tuple(getattr(deps, name) for name in ("manifest", "chunks", "keyword", "catalog"))


def _seed(deps, directory):
    inventory = []

    def snapshot(path, source, url, content_type, meta=None):
        data = path.read_bytes()
        sha = deps.raw.put(data)
        snap = Snapshot(
            snapshot_id=f"fixture-{len(inventory)}", source_id=source, resource_url=url,
            fetched_at=NOW, outcome=FetchOutcome.ok, http_status=200,
            sha256=sha, size=len(data), content_type=content_type,
        )
        deps.manifest.record_snapshot(snap, meta or {})
        inventory.append({"path": str(path.relative_to(ROOT)), **snap.model_dump(mode="json"), "meta": meta or {}})

    # Every recorded law body, including the three article-less H3 additions.
    law_dir = FIXTURES / "egov_law"
    api = "https://laws.e-gov.go.jp/api/2"
    for path in sorted(law_dir.glob("law_data.*.asof*.xml-in-json.json")):
        _, law_id, asof_part, *_ = path.name.split(".")
        asof = asof_part.removeprefix("asof")
        revisions = law_dir / f"law_revisions.{law_id}.json"
        pending = []
        if revisions.exists():
            payload = json.loads(revisions.read_bytes())
            pending = pending_amendments(payload["revisions"], date.fromisoformat(asof))
            snapshot(revisions, "jp-egov-law", f"{api}/law_revisions/{law_id}", "application/json",
                     {"role": "law_revisions", "law_id": law_id})
        snapshot(path, "jp-egov-law",
                 f"{api}/law_data/{law_id}?asof={asof}&response_format=json&law_full_text_format=xml",
                 "application/json", {"role": "law_data", "law_id": law_id, "asof": asof,
                                      "pending_amendments": pending})
    for path in sorted(law_dir.glob("laws_list.*.json")):
        parts = path.name.split(".")
        query = urlencode({"limit": parts[1].removeprefix("limit"), "offset": parts[2].removeprefix("offset")})
        snapshot(path, "jp-egov-law", f"{api}/laws?{query}", "application/json", {"role": "law_list"})

    # Complete current Dashboard response batches; no reserialization of rows.
    dashboard = "https://dashboard.e-stat.go.jp/api/1.0/Json"
    for pattern, endpoint in (("getIndicatorInfo_*.json", "getIndicatorInfo"), ("getData_b*.json", "getData")):
        for path in sorted((FIXTURES / "stat_dashboard").glob(pattern)):
            payload = json.loads(path.read_bytes())
            section = next(iter(payload.values()))
            snapshot(path, "jp-stat-dashboard", f"{dashboard}/{endpoint}?{urlencode(section['PARAMETER'])}",
                     "application/json")

    for table in deps.registry.get("jp-estat-files").scope["tables"]:
        paths = list((FIXTURES / "estat_files").glob(f"{table['stat_inf_id']}_*"))
        assert len(paths) == 1
        path = paths[0]
        kind = 4 if table["layout"] == "wide_area_by_year" else 1
        query = urlencode({"statInfId": table["stat_inf_id"], "fileKind": kind})
        snapshot(path, "jp-estat-files", f"https://www.e-stat.go.jp/stat-search/file-download?{query}",
                 "application/octet-stream" if kind == 4 else "text/csv;charset=UTF-8", table)

    # All CKAN responses/resources, with their recorded URLs and dispatch roles.
    ckan = FIXTURES / "ckan_egov"
    for name, record in json.loads((ckan / "_responses.json").read_bytes()).items():
        role = "resource" if name.startswith("resources/") else (
            "activity" if name.startswith("recently_changed") else "package_search" if name.startswith("package_search")
            else "package_show"
        )
        snapshot(ckan / name, "jp-egov-data", record["url"], record["headers"].get("content-type", "application/json"),
                 {"role": role})

    # Both complete feeds and every recorded report; only Osaka has an alias.
    base = "https://www.data.jma.go.jp/developer/xml/"
    for path in sorted((FIXTURES / "jma").glob("*.xml")):
        if ".synthetic." in path.name:
            continue
        if path.name.startswith("regular_l."):
            snapshot(path, "jp-jma", base + "feed/regular_l.xml", "application/xml", {"resource_type": "feed"})
        else:
            name = "20261015073247_0_VPFD51_270000.xml" if path.name.startswith("jma-osaka") else path.name
            url = base + "data/" + name
            snapshot(path, "jp-jma", url, "application/xml", {"resource_type": "entry", "entry_id": url})

    snapshot(EXTRA / "announcement-injection.synthetic.txt", "jp-eval-injection",
             deps.registry.get("jp-eval-injection").base_url, "text/plain")
    seed = yaml.safe_load((EXTRA / "manifest-seed.yaml").read_text(encoding="utf-8"))
    for entry in seed["snapshots"]:
        deps.manifest.record_snapshot(Snapshot.model_validate(entry), {})
    _json(directory / "corpus.json", inventory)
    return inventory


@pytest.fixture(scope="module")
def full_chain(tmp_path_factory):
    evidence = os.environ.get("DATUMOJ_EVIDENCE_DIR")
    directory = Path(evidence) if evidence else tmp_path_factory.mktemp("datumoj-903-evidence")
    directory.mkdir(parents=True, exist_ok=True)
    registry = RegistryImpl([
        *load_registry(ROOT / "sources").all(), *load_registry(EXTRA / "sources").all(),
    ])
    parsers = discover_parsers()
    parsers[("ckan", "*")] = wiring._CkanPages
    parsers[("eval_fixture", "text/plain")] = AnnouncementParser
    data_dir = tmp_path_factory.mktemp("m3-data")
    real_build = wiring.build

    def build(path):
        return real_build(path, {"registry": registry, "parsers": parsers, "embedder": HashEmbedder(dim=1024)})

    def forbid_network(*args, **kwargs):
        pytest.fail("M3 must stay offline and ingest fixture bytes only")

    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("DATUMOJ_VECTORS", raising=False)
        patch.setattr(httpx.HTTPTransport, "handle_request", forbid_network)
        patch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid_network)
        deps = build(data_dir)
        try:
            inventory = _seed(deps, directory)
            parsed = deps.parse_pending(deps.pipeline_deps)
            _json(directory / "parse.json", vars(parsed))
            assert not parsed.failed, parsed.failed
            assert not parsed.no_parser, parsed.no_parser
            assert not parsed.vectors_missing, parsed.vectors_missing
            assert len(parsed.parsed) == len(inventory)
            yield {"deps": deps, "build": build, "directory": directory, "inventory": inventory}
        finally:
            _close(_stores(deps))


def test_real_dependencies_and_complete_corpus(full_chain):
    deps = full_chain["deps"]
    for name, implementation in (
        ("tools", AnalysisTools), ("retriever", HybridRetriever), ("raw", FileRawStore),
        ("manifest", SqliteManifest), ("catalog", SqliteCatalogStore),
        ("observations", DuckDBObservationStore), ("vectors", LanceVectorIndex),
    ):
        assert isinstance(getattr(deps, name), implementation)
    assert isinstance(deps.vectors.embedder, HashEmbedder)
    for name in ("retriever", "catalog", "observations", "manifest", "raw", "chunks", "registry"):
        assert getattr(deps.tools, name) is getattr(deps, name)
    chunks = list(deps.chunks.iter_chunks())
    assert vector_status(deps.pipeline_deps) == {"state": "available", "blocks": len(chunks)}
    assert len({c.doc_key for c in chunks if c.source_id == "jp-egov-law"}) == 25
    assert len([c for c in chunks if c.source_id == "jp-jma"]) == 5
    for item in full_chain["inventory"]:
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
        deps.raw.verify(item["sha256"])
        spec = deps.registry.get(item["source_id"])
        parser = select_parser(deps.pipeline_deps.parsers, spec.kind, item["content_type"])
        assert deps.manifest.get_parse(
            item["sha256"], parser.name, parser.version,
            parse_context_fingerprint(
                Snapshot.model_validate({k: v for k, v in item.items() if k not in ("path", "meta")}), item["meta"],
            ),
        ).status is ParseStatus.ok
    assert not deps.manifest.snapshots_for("jp-eval-noingest")
    assert deps.registry.get("jp-eval-unknown") is None
    failures = deps.manifest.snapshots_for("jp-eval-fetchfail")
    assert len(failures) == 1 and failures[0].http_status == 503
    assert deps.observations.query_readonly(
        "SELECT count(*) FROM observations WHERE source_id = 'jp-stat-dashboard'"
    ).rows == [(1057,)]
    assert deps.observations.query_readonly(
        "SELECT value, note FROM observations WHERE source_id = 'jp-estat-files' "
        "AND table_key = '000031720773' AND locator = 'cell=L15;row=15'"
    ).rows == [(None, "-")]
    found = deps.retriever.search(["集会 結社 言論 出版 検閲の禁止"], source_ids=["jp-egov-law"])
    assert any(MatchedBy.vector in hit.matched_by for hit in found.data)
    assert not any("degraded=true" in warning for warning in found.warnings)


def _ivy_questions(tmp_path):
    # TACHIBANA 的题库要配政策网页语料，由 903 评测；这里只评 IVY 的四个题库。
    directory = tmp_path / "ivy-questions"
    directory.mkdir()
    for name in IVY_BANKS:
        shutil.copy(ROOT / "eval/questions" / name, directory / name)
    return directory


def test_all_questions_pass_every_gate(full_chain, tmp_path):
    deps = full_chain["deps"]
    questions = load_questions(_ivy_questions(tmp_path))
    assert len(questions) >= 60
    assert {Path(q._source_file).name for q in questions} == set(IVY_BANKS)

    def cite(citation):
        restored = deps.tools.cite(citation.snapshot_sha256, citation.locator)
        assert restored["data"]["status"] == "ok"
        return restored["data"]["passages"]

    report = evaluate(
        questions, retriever=deps.retriever, observation_store=deps.observations,
        cite=cite, manifest=deps.manifest, catalog=deps.catalog,
    )
    write_reports(report, full_chain["directory"])
    gate = check_gate(report)
    _json(full_chain["directory"] / "gate.json", vars(gate))
    assert gate.passed, (gate.failures, report.failures)
    for metric, threshold in gate.thresholds.items():
        assert report.overall[metric] >= threshold


def test_cli_eval_reports_and_gate_exit_codes(full_chain, monkeypatch, tmp_path):
    deps = full_chain["deps"]
    monkeypatch.setattr(cli.wiring, "build", lambda path: deps)
    runner = CliRunner()
    ivy = _ivy_questions(tmp_path)
    args = ["eval", "--gate", "--report", str(tmp_path / "full"), "--questions", str(ivy)]
    result = runner.invoke(cli.app, args)
    commands = [{"args": args, "exit_code": result.exit_code, "output": result.output}]
    assert result.exit_code == 0, result.output
    report = json.loads((tmp_path / "full/evaluation.json").read_text())
    assert len(report["questions"]) >= 60 and check_gate(report).passed
    # A real empty corpus must fail --gate; plain eval still emits its report.
    empty = full_chain["build"](tmp_path / "empty-data")
    try:
        monkeypatch.setattr(cli.wiring, "build", lambda path: empty)
        for flags, expected in ((["--gate"], 1), ([], 0)):
            flags = [*flags, "--questions", str(ivy)]
            result = runner.invoke(cli.app, ["eval", *flags])
            commands.append({"args": ["eval", *flags], "exit_code": result.exit_code, "output": result.output})
            assert result.exit_code == expected, result.output
            directory = Path(result.output.split("报告：", 1)[1].splitlines()[0])
            assert directory.is_dir() and not directory.is_relative_to(ROOT)
            assert (directory / "evaluation.md").is_file()
            assert not check_gate(json.loads((directory / "evaluation.json").read_text())).passed
    finally:
        _close(_stores(empty))
    _json(full_chain["directory"] / "commands.json", commands)


def test_mcp_factory_session_restores_original(full_chain):
    events = []

    def factory():
        events.append(threading.get_ident())
        deps = full_chain["build"](full_chain["deps"].data_dir)
        weakref.finalize(deps.tools, _close, _stores(deps))
        return deps.tools

    def data(result):
        assert not result.is_error, result.content
        return result.structured_content.get("result", result.structured_content)

    server = mcp_server.create_server(factory)
    assert not events

    async def run():
        async with Client(server) as client:
            found = data(await client.call_tool("search_docs", {
                "queries": "集会 結社 言論 出版 検閲の禁止", "sources": "jp-egov-law", "k": 10,
            }))
            hit = next(h for h in found["data"]["hits"]
                       if h["doc_key"] == "jp-egov-law:321CONSTITUTION" and h["locator"] == "art=21")
            citation = hit["citation"]
            restored = data(await client.call_tool("cite", {
                "snapshot_sha256": citation["snapshot_sha256"], "locator": citation["locator"],
            }))
            assert restored["data"]["status"] == "ok"
            text = SourceText.model_validate(restored["data"]["passages"][0]["text"])
            original = full_chain["deps"].chunks.get_chunk(hit["chunk_id"], citation["snapshot_sha256"])
            assert text.content == original.text and "表現の自由" in text.content
            assert hashlib.sha256(full_chain["deps"].raw.get(citation["snapshot_sha256"])).hexdigest() == citation["snapshot_sha256"]
            _json(full_chain["directory"] / "mcp.json", {"search_docs": found, "cite": restored})

    asyncio.run(run())
    assert len(events) == 1 and events[0] != threading.get_ident()


def test_cli_mcp_uses_stdio_entry_and_lazy_wiring_factory(full_chain, monkeypatch):
    calls = []
    monkeypatch.delenv(mcp_server.FACTORY_ENV, raising=False)
    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(full_chain["deps"].data_dir))

    def build(path):
        calls.append(path)
        return full_chain["deps"]

    class Server:
        def run(self, transport):
            assert transport == "stdio"
            assert not calls
            assert mcp_server._factory_from_env()() is full_chain["deps"].tools

    def create_server(factory):
        assert factory is wiring.build_tools
        assert not calls
        return Server()

    monkeypatch.setattr(wiring, "build", build)
    monkeypatch.setattr(mcp_server, "create_server", create_server)
    result = CliRunner().invoke(cli.app, ["mcp"])
    assert result.exit_code == 0, result.output
    assert calls == [str(full_chain["deps"].data_dir)]
