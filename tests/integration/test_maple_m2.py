"""MAPLE m2: five production Japanese plugins in one offline data directory.

The e-Stat success response is derived from the recorded first page: only its
pagination envelope changes (as in test_estat_api_e2e). The untouched page is
also crawled and must fail parsing without publishing observations. NDB's
untouched extracted workbook is wrapped in a ZIP with its original cp932 name.
It has no suppressed cells; a second, explicitly synthetic copy changes C6 to
"-" and is fetched through the same directory to exercise NULL preservation.
Evidence stays under tmp_path unless DATUMOJ_EVIDENCE_DIR explicitly opts in.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
from decimal import Decimal
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest
from mcp import Client
from openpyxl import load_workbook
from typer.testing import CliRunner

from datumoj.rikolto import cli, mcp_server, wiring
from datumoj.rikolto.core.contracts import CrawlContext
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.models import Citation, parse_locator
from datumoj.rikolto.core.registry import RegistryImpl, load_registry
from datumoj.rikolto.core.runner import CrawlDeps
from datumoj.rikolto.core.schedule import read_control
from datumoj.rikolto.testing.samples import T0

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures"
SOURCES = ("jp-estat-api", "jp-ksj", "jp-census", "jp-mhlw", "jp-abr")
FILES = {
    "jp-estat-api": "estat_api/getStatsData_0003443838_start1_limit10.json",
    "jp-ksj": "ksj/P05-22_41_GML.zip",
    "jp-census": "census2024/c1_001_1.xlsx",
    "jp-mhlw": "ndb/ndb11_kenshin_shitsumon01_pref_sex_age.xlsx",
    "jp-abr": "abr/mt_town_city134023.csv.zip",
}
IDENTITY = "source_id, table_key, obs_id, snapshot_sha256, release_id"


def _close(deps):
    for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
        getattr(deps, name).close()


def _rows(answer):
    data = answer["data"]
    return [dict(zip(data["columns"], map(_text, row), strict=True)) for row in data["rows"]]


def _text(value):
    return value["content"] if isinstance(value, dict) else value


@pytest.fixture
def offline(tmp_path, monkeypatch, caplog):
    def forbidden(*args, **kwargs):
        pytest.fail("MAPLE m2 HTTP must use ReplayTransport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(tmp_path / "data"))
    # Always replace the inherited credential before planning any e-Stat task.
    key = "synthetic-maple-" + "m2-credential"
    monkeypatch.setenv("ESTAT_APP_ID", key)
    caplog.set_level(logging.INFO)
    yield
    assert key not in caplog.text
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert key.encode() not in path.read_bytes(), path.relative_to(tmp_path)


def _inputs(tmp_path, specs):
    paths = {sid: FIXTURES / name for sid, name in FILES.items()}
    payload = json.loads(paths["jp-estat-api"].read_bytes())
    info = payload["GET_STATS_DATA"]["STATISTICAL_DATA"]["RESULT_INF"]
    assert info.pop("NEXT_KEY") and int(info["TOTAL_NUMBER"]) > int(info["TO_NUMBER"])
    info["TOTAL_NUMBER"] = info["TO_NUMBER"]
    paths["jp-estat-api"] = tmp_path / "estat-single-page.synthetic.json"
    paths["jp-estat-api"].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    payload = paths["jp-mhlw"].read_bytes()
    paths["jp-mhlw"] = tmp_path / "ndb.synthetic.zip"
    _ndb_archive(paths["jp-mhlw"], payload, specs["jp-mhlw"].scope["member"])
    return paths


def _ndb_archive(path, payload, member):
    member = member.encode("cp932")
    placeholder = b"a" * len(member)
    output = io.BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(placeholder.decode(), payload)
    path.write_bytes(output.getvalue().replace(placeholder, member))


def _crawl(deps, spec, path):
    crawler = deps.crawler_classes[spec.kind]()
    task, = crawler.plan(CrawlContext(spec, deps.manifest, deps.raw, T0))
    transport = ReplayTransport({
        f"https://{spec.allowed_hosts[0]}/robots.txt": Recorded(status=404),
        task.url: Recorded(path, headers={"content-type": "text/html; charset=ascii"}),
    })
    with PoliteHttpFetcher.from_spec(spec, transport=transport, now=lambda: T0,
                                    clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
        report = deps.run_crawl(spec, crawler, CrawlDeps(
            raw=deps.raw, manifest=deps.manifest, fetcher=fetcher,
            now=lambda: T0, clock=lambda: 0.0, sleep=lambda _: None,
        ))
    assert not report.failures and report.stop_reason is None and report.new == 1
    assert len(transport.requests) == 2
    snapshot, = [s for s in deps.manifest.snapshots_for(spec.id)
                 if s.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()]
    assert deps.raw.get(snapshot.sha256) == path.read_bytes()
    return snapshot


async def _mcp_checks(data_dir, registry, snapshots, generation, suppressed_workbook):
    def factory():
        return wiring.build(data_dir, {"registry": registry, "vectors": None}).tools

    answers = {}
    async with Client(mcp_server.create_server(factory)) as client:
        async def call(name, **args):
            result = await client.call_tool(name, args)
            assert not result.is_error, result.content
            return result.structured_content.get("result", result.structured_content)

        async def restore(value, source):
            citation = Citation.model_validate(value)
            assert citation.source_id == source
            assert citation.snapshot_sha256 == snapshots[source].sha256
            assert citation.snapshot_id == snapshots[source].snapshot_id
            assert citation.resource_url == snapshots[source].resource_url
            assert citation.fetched_at == T0
            if source == "jp-estat-api":
                assert "appId=REDACTED" in citation.resource_url
            restored = await call("cite", snapshot_sha256=citation.snapshot_sha256,
                                  locator=citation.locator, snapshot_id=citation.snapshot_id,
                                  resource_url=citation.resource_url)
            assert restored["data"]["status"] == "ok"
            assert restored["data"]["passages"]
            return restored["data"]["passages"]

        answers["version"] = await call("version")
        assert answers["version"]["data"]["data_generation"] == generation.generation
        assert answers["version"]["data"]["data_generated_at"] == generation.created_at.isoformat()

        for sid, query in (("jp-ksj", "佐賀市 公民館"), ("jp-census", "従業者数"),
                           ("jp-mhlw", "10未満"), ("jp-abr", "134023")):
            answer = await call("search_docs", queries=query, sources=[sid], k=50)
            hits = answer["data"]["hits"]
            assert answer["gap_state"] == "ok" and hits, sid
            passages = []
            for hit in hits:
                passages.extend(await restore(hit["citation"], sid))
            if sid == "jp-ksj":
                assert any("嘉瀬公民館" in h["snippet"]["content"]
                           and "佐賀市嘉瀬町中原1690" in h["snippet"]["content"] for h in hits)
            if sid == "jp-mhlw":
                assert all(parse_locator(h["locator"])["cell"] == "A1" for h in hits)
                assert "10未満" in hits[0]["snippet"]["content"]
                assert "標準的な質問票（質問項目１）" in parse_locator(hits[0]["locator"])["member"]
            if sid == "jp-abr":
                records = [json.loads(p["text"]["content"].split("\n", 1)[1]) for p in passages]
                assert all(r["lg_code"] == "134023" for r in records)
                assert any(r["machiaza_id"] == "0003000" and r["oaza_cho"] == "休戸郷" for r in records)
            answers[sid] = answer

        answers["catalog"] = await call("search_catalog", query="概算値", sources=["jp-estat-api"])
        items = answers["catalog"]["data"]["datasets"]
        assert items and any(i["dataset_key"] == "jp-estat-api:0003443838" for i in items)
        for item in items:
            assert "月別人口" in item["title"]["content"]
            await restore(item["citation"], "jp-estat-api")

        # The cp932 KSJ metadata is searchable alongside its UTF-8 feature attributes.
        answers["ksj_metadata"] = await call("search_catalog", query="国土数値情報", sources=["jp-ksj"])
        for item in answers["ksj_metadata"]["data"]["datasets"]:
            assert parse_locator(item["citation"]["locator"])["member"] == "KS-META-P05-22_41.xml"
            await restore(item["citation"], "jp-ksj")
        assert answers["ksj_metadata"]["data"]["datasets"]

        sql = (f"SELECT {IDENTITY}, value, unit, time_label, area_code FROM observations WHERE "
               "(source_id='jp-estat-api' AND time_label='2021年12月') OR "
               "(source_id='jp-census' AND area_code='41201' AND indicator='従業者数') ORDER BY source_id")
        answers["cross_source_sql"] = await call("query_sql", sql=sql)
        rows = _rows(answers["cross_source_sql"])
        assert answers["cross_source_sql"]["evidence_state"] == "complete"
        assert len(rows) == 2 and {r["source_id"] for r in rows} == {"jp-estat-api", "jp-census"}
        for row in rows:
            expected = 12547 if row["source_id"] == "jp-estat-api" else 108887
            assert Decimal(row["value"]) == expected
            if row["source_id"] == "jp-estat-api":
                assert _text(row["unit"]) == "万人" and _text(row["time_label"]) == "2021年12月"
            provenance = [p for p in answers["cross_source_sql"]["data"]["provenance"]
                          if (p["source_id"], p["table_key"], p["obs_id"])
                          == (row["source_id"], row["table_key"], row["obs_id"])]
            item, = provenance
            passages = await restore(item["citation"], row["source_id"])
            assert any(p["kind"] == "observation" and Decimal(p["value"]) == expected for p in passages)
            if row["source_id"] == "jp-census":
                assert item["citation"]["locator"] == "sheet=c1_001_1;cell=E1745"

        answers["suppressed"] = await call("query_sql", sql=(
            f"SELECT {IDENTITY}, value, note, unit FROM observations "
            "WHERE source_id='jp-mhlw' AND value IS NULL ORDER BY obs_id LIMIT 1"))
        row, = _rows(answers["suppressed"])
        assert row["value"] is None and _text(row["note"]).startswith("-;")
        assert _text(row["unit"]) == "人数"
        item, = answers["suppressed"]["data"]["provenance"]
        passages = await restore(item["citation"], "jp-mhlw")
        assert any(p["kind"] == "observation" and p["value"] is None
                   and _text(p["note"]) == _text(row["note"]) for p in passages)
        # The marker belongs to the synthetic copy, never to the recorded fixture.
        book = load_workbook(suppressed_workbook, read_only=True, data_only=False)
        try:
            assert book["質問項目"][parse_locator(item["citation"]["locator"])["cell"]].value == "-"
        finally:
            book.close()
    return answers


def test_five_sources_share_pipeline_and_mcp(tmp_path, monkeypatch, offline):
    data_dir = tmp_path / "data"
    registry = load_registry(ROOT / "sources")
    specs = {sid: registry.get(sid) for sid in SOURCES}
    # One selected population table; the existing fixtures do not contain
    # complete labour/CPI responses or a complete catalog crawl.
    estat = specs["jp-estat-api"]
    specs[estat.id] = estat.model_copy(update={"scope": {
        **estat.scope, "tables": estat.scope["tables"][:1], "stats_codes": [],
    }})
    registry = RegistryImpl(list(specs.values()))
    inputs = _inputs(tmp_path, specs)
    deps = wiring.build(data_dir, {"registry": registry, "vectors": None})
    snapshots, reports = {}, {}
    suppressed_workbook = tmp_path / "ndb-suppressed.synthetic.xlsx"
    try:
        for sid, spec in specs.items():
            snapshot = _crawl(deps, spec, inputs[sid])
            snapshots[sid] = snapshot
            reports[sid] = {"requests": 2, "new": 1,
                            "snapshot_sha256": snapshot.sha256, "url": snapshot.resource_url}

        parsed = deps.parse_pending(deps.pipeline_deps)
        assert len(parsed.parsed) == 5 and not parsed.failed and not parsed.no_parser
        # 27 body blocks + 3 catalog entries + 10 API / 5886 census / 1536 NDB rows.
        assert deps.reindex(deps.pipeline_deps) == 7462
        assert {s for s in SOURCES if deps.manifest.snapshots_for(s)} == set(SOURCES)
        control = read_control(data_dir)
        assert control["enabled"] is False
        assert all(not control["sources"].get(s, {}).get("enabled", False) for s in SOURCES)

        # First prove that the untouched NDB numeric cell is preserved. Then
        # exercise suppression on a second snapshot, not on the fixture itself.
        original = deps.tools.query_sql(f"SELECT {IDENTITY}, value FROM observations "
                                       "WHERE obs_id='ndb11-q1:北海道:男:40～44歳:はい'")
        row, = _rows(original)
        book = load_workbook(FIXTURES / FILES["jp-mhlw"])
        try:
            assert Decimal(row["value"]) == book["質問項目"]["C6"].value == 4688
            book["質問項目"]["C6"] = "-"
            book.save(suppressed_workbook)
        finally:
            book.close()
        path = tmp_path / "ndb-suppressed.synthetic.zip"
        _ndb_archive(path, suppressed_workbook.read_bytes(), specs["jp-mhlw"].scope["member"])
        snapshots["jp-mhlw"] = _crawl(deps, specs["jp-mhlw"], path)
        parsed = deps.parse_pending(deps.pipeline_deps, "jp-mhlw")
        assert len(parsed.parsed) == 1 and not parsed.failed
        assert deps.reindex(deps.pipeline_deps) == 7462
        reports["jp-mhlw"]["suppression_synthetic_sha256"] = snapshots["jp-mhlw"].sha256
    finally:
        _close(deps)

    # Publish/restore the same common directory using a real local state store,
    # so MCP version must report a real current generation rather than a stub.
    monkeypatch.setenv("DATUMOJ_STATE_BACKEND", "dir")
    monkeypatch.setenv("DATUMOJ_STATE_DIR", str(tmp_path / "state"))
    generation = wiring.build_state(data_dir).push()
    restored = cli.pull_state(data_dir)
    assert restored["verified"] and restored["generation"] == generation.generation
    answers = asyncio.run(_mcp_checks(data_dir, registry, snapshots, generation, suppressed_workbook))
    # version contains a local path for host diagnostics; omit it from evidence.
    answers["version"]["data"].pop("data_dir")
    evidence = {"crawl": reports, "mcp": answers, "fixtures": [
        {"path": name, "sha256": hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()}
        for name in FILES.values()
    ]}
    output = Path(os.environ.get("DATUMOJ_EVIDENCE_DIR") or tmp_path / "evidence")
    output.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    assert str(tmp_path) not in rendered and os.environ["ESTAT_APP_ID"] not in rendered
    (output / "maple-m2.json").write_text(rendered, encoding="utf-8")


def test_registry_export_matches_committed_catalog(tmp_path, offline):
    output = tmp_path / "catalog"
    result = CliRunner().invoke(cli.app, ["registry", "export", "--out", str(output)])
    assert result.exit_code == 0, result.output
    expected = {p.name: p.read_bytes() for p in (ROOT / "catalog").glob("*.json")}
    assert {p.name: p.read_bytes() for p in output.glob("*.json")} == expected
    assert all(f"{sid}.json" in expected for sid in SOURCES)


@pytest.mark.parametrize("page", ["1", "11"])
def test_incomplete_estat_page_never_publishes_observations(tmp_path, offline, page):
    spec = load_registry(ROOT / "sources").get("jp-estat-api")
    spec = spec.model_copy(update={"scope": {
        **spec.scope, "tables": spec.scope["tables"][:1], "stats_codes": [],
    }})
    deps = wiring.build(tmp_path / "data", {"registry": RegistryImpl([spec]), "vectors": None})
    try:
        crawler = deps.crawler_classes[spec.kind]()
        task, = crawler.plan(CrawlContext(spec, deps.manifest, deps.raw, T0))
        path = FIXTURES / f"estat_api/getStatsData_0003443838_start{page}_limit10.json"
        transport = ReplayTransport({
            "https://api.e-stat.go.jp/robots.txt": Recorded(status=404), task.url: Recorded(path),
        })
        with PoliteHttpFetcher.from_spec(spec, transport=transport, now=lambda: T0,
                                        clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            report = deps.run_crawl(spec, crawler, CrawlDeps(
                raw=deps.raw, manifest=deps.manifest, fetcher=fetcher,
                now=lambda: T0, clock=lambda: 0.0, sleep=lambda _: None,
            ))
        assert not report.failures and report.new == 1
        parsed = deps.parse_pending(deps.pipeline_deps, spec.id)
        assert len(parsed.failed) == 1 and not parsed.parsed
        assert set(parsed.failed.values()) == {"ParseError"}
        result = deps.tools.query_sql("SELECT source_id, table_key, obs_id FROM observations")
        assert not result["data"]["rows"] and not result["data"]["provenance"]
        assert not list(deps.chunks.iter_chunks([spec.id]))
    finally:
        _close(deps)
