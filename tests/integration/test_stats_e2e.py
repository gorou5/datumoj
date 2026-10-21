"""M2: real CLI/crawlers/parsers/stores with offline, fixture-derived discovery.

CKAN is metadata-only. Dashboard responses are rebatched without changing rows.
JMA discovery is projected onto messages whose original XML was recorded: the
second projected feed exposes two more recorded messages. Full-feed delta is
checked separately; unrecorded message bodies are never fabricated.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from functools import partial
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
import openpyxl
import pytest
import yaml
from lxml import etree
from typer.testing import CliRunner

from datumoj import cli, wiring
from datumoj.core.contracts import ParseError, ParseStatus, discover_parsers, select_parser
from datumoj.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.core.models import Citation, FetchOutcome, SourceText, TimeBasis
from datumoj.core.registry import load_registry
from datumoj.core.runner import CrawlDeps
from datumoj.crawlers.ckan import CkanCrawler
from datumoj.crawlers.estat_files import EstatFilesCrawler
from datumoj.crawlers.jma_feed import FEED_URL, JmaFeedCrawler
from datumoj.crawlers.stat_dashboard import StatDashboardCrawler, plan_batches
from datumoj.eval import evaluate, load_questions, write_reports
from datumoj.testing.contract_suites import CatalogStoreSuite, ParserSuite
from datumoj.testing.samples import ParseSample, make_snapshot

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures"
NOW = datetime(2026, 10, 21, 0, tzinfo=timezone.utc)
ATOM = "http://www.w3.org/2005/Atom"
SOURCES = {
    "jp-egov-data": CkanCrawler,
    "jp-stat-dashboard": StatDashboardCrawler,
    "jp-estat-files": EstatFilesCrawler,
    "jp-jma": JmaFeedCrawler,
}


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def _read(path):
    return json.loads(path.read_bytes())


def _routes(directory, registry):
    """Only request envelopes/discovery are synthetic; source records stay intact."""
    routes, used = {}, set()

    def fixture(path, content_type):
        used.add(path)
        return Recorded(path, headers={"content-type": content_type})

    def synthetic(name, payload):
        return Recorded(_json(directory / name, payload), headers={"content-type": "application/json"})

    for spec in registry.all():
        host = httpx.URL(spec.base_url).host
        routes[f"https://{host}/robots.txt"] = Recorded(status=404)

    # Keep both original pages and all ten packages, independent of questions.
    spec = registry.get("jp-egov-data")
    for start in (0, 5):
        path = FIXTURES / f"ckan_egov/package_search_q-jinko_rows5_start{start}.json"
        params = {"q": spec.scope["q"], "rows": 5, "start": start, "sort": "id asc"}
        routes[f"{spec.base_url}/package_search?{urlencode(params, quote_via=quote)}"] = fixture(path, "application/json")
    params["start"] = 10
    routes[f"{spec.base_url}/package_search?{urlencode(params, quote_via=quote)}"] = fixture(
        FIXTURES / "ckan_egov/package_search_q-jinko_rows5_start100000_empty.json", "application/json",
    )

    # The production crawler batches the configured indicators differently from
    # the recording. Merge class metadata and select whole, unmodified DATA_OBJ.
    spec = registry.get("jp-stat-dashboard")
    catalogs = []
    for path in sorted((FIXTURES / "stat_dashboard").glob("getIndicatorInfo_*.json")):
        used.add(path)
        catalogs.append(_read(path))
    catalog = deepcopy(catalogs[0])
    records = {}
    for payload in catalogs:
        for record in payload["GET_META_INDICATOR_INF"]["METADATA_INF"]["CLASS_INF"]["CLASS_OBJ"]:
            if record["@code"] in records:
                assert records[record["@code"]] == record
            records[record["@code"]] = record
    catalog["GET_META_INDICATOR_INF"]["METADATA_INF"]["CLASS_INF"]["CLASS_OBJ"] = list(records.values())
    base = spec.base_url.rstrip("/")
    routes[f"{base}/Json/getIndicatorInfo?Lang=JP"] = synthetic("dashboard-catalog.synthetic.json", catalog)
    payloads = []
    for path in sorted((FIXTURES / "stat_dashboard").glob("getData_b*.json")):
        used.add(path)
        payloads.append(_read(path))
    classes, surveys, rows = {}, {}, []
    for payload in payloads:
        data = payload["GET_STATS"]["STATISTICAL_DATA"]
        rows.extend(data["DATA_INF"]["DATA_OBJ"])
        for group in data["CLASS_INF"]["CLASS_OBJ"]:
            target = classes.setdefault(group["@id"], deepcopy(group))
            by_code = {entry["@code"]: entry for entry in target["CLASS"]}
            by_code.update({entry["@code"]: entry for entry in group["CLASS"]})
            target["CLASS"] = list(by_code.values())
        for survey in data["TABLE_INF"]["STAT_NAME"]:
            surveys[survey["@code"]] = survey
    assert len(rows) == 1057
    regions = ["00000", *(f"{n:02d}000" for n in range(1, 48))]
    selected = []
    for index, (indicators, batch_regions) in enumerate(plan_batches(spec.scope["indicators"], regions)):
        params = {
            "Lang": "JP", "IndicatorCode": ",".join(indicators), "RegionCode": ",".join(batch_regions),
            "IsSeasonalAdjustment": "1", "MetaGetFlg": "Y", "SectionHeaderFlg": "1",
        }
        payload = deepcopy(payloads[0])
        data = payload["GET_STATS"]["STATISTICAL_DATA"]
        data["CLASS_INF"]["CLASS_OBJ"] = list(classes.values())
        data["TABLE_INF"]["STAT_NAME"] = list(surveys.values())
        batch = [row for row in rows if row["VALUE"]["@indicator"] in indicators]
        selected.extend(batch)
        data["DATA_INF"]["DATA_OBJ"] = batch
        routes[f"{base}/Json/getData?{urlencode(params)}"] = synthetic(f"dashboard-{index}.synthetic.json", payload)
    assert len(selected) == len(rows)  # No lost or duplicated observations.

    for path in sorted((FIXTURES / "estat_files").glob("*.*")):
        if path.suffix not in {".csv", ".xlsx"}:
            continue
        kind = 4 if path.suffix == ".xlsx" else 1
        url = "https://www.e-stat.go.jp/stat-search/file-download?" + urlencode({
            "statInfId": path.name.split("_")[0], "fileKind": kind,
        })
        # Deliberately retain the recorded, wrong UTF-8 charset for cp932 CSV.
        routes[url] = fixture(path, "text/csv;charset=UTF-8" if kind == 1 else "application/octet-stream")

    names = [
        "20261020074609_0_VPFG50_460100.xml", "20261020085325_0_VPFD51_240000.xml",
        "20261020112204_0_VPWP50_050000.xml", "20261015051031_0_VPCK50_471000.xml",
        "20261015073247_0_VPFD51_270000.xml",
    ]
    entry_base = "https://www.data.jma.go.jp/developer/xml/data/"
    ids = [set(), set()]
    for index in (0, 1):
        path = FIXTURES / f"jma/regular_l.snapshot{index + 1}.xml"
        used.add(path)
        root = etree.fromstring(path.read_bytes())
        entries = root.findall(f"{{{ATOM}}}entry")
        ids[index] = {entry.findtext(f"{{{ATOM}}}id") for entry in entries}
        keep = {entry_base + name for name in names[:3 if index == 0 else 5]}
        for entry in entries:
            if entry.findtext(f"{{{ATOM}}}id") not in keep:
                root.remove(entry)
        assert len(root.findall(f"{{{ATOM}}}entry")) == len(keep)
        target = directory / f"jma-feed-{index + 1}.synthetic.xml"
        target.write_bytes(etree.tostring(root))
    assert len(ids[0]) == len(ids[1]) == 9383
    assert len(ids[0] & ids[1]) == 9377
    assert len(ids[1] - ids[0]) == 6
    _json(directory / "jma-full-feed-delta.json", {"new_ids": sorted(ids[1] - ids[0]), "overlap": 9377})
    for name in names:
        path = FIXTURES / "jma" / ("jma-osaka-forecast-20261015.xml" if "270000" in name else name)
        routes[entry_base + name] = fixture(path, "application/xml")
    routes[FEED_URL] = Recorded(directory / "jma-feed-1.synthetic.xml", headers={"content-type": "application/xml"})
    _json(directory / "corpus.json", [
        {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(used)
    ])
    return routes, {entry_base + name for name in names[3:]}


def _close(deps):
    for name in ("manifest", "chunks", "keyword", "catalog"):
        getattr(deps, name).close()


@pytest.fixture(scope="module")
def stats_chain(tmp_path_factory):
    # No module-import side effects or repository writes in the default run.
    evidence = os.environ.get("DATUMOJ_EVIDENCE_DIR")
    directory = Path(evidence) if evidence else tmp_path_factory.mktemp("datumoj-902-evidence")
    directory.mkdir(parents=True, exist_ok=True)
    sources = tmp_path_factory.mktemp("m2-sources")
    for path in (ROOT / "sources").glob("*.yaml"):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        if spec["id"] == "jp-egov-data":
            spec["scope"].update(rows=5, formats=[])  # This checkpoint measures metadata retrieval.
        (sources / path.name).write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    registry = load_registry(sources)
    routes, new_entries = _routes(directory, registry)
    data_dir = tmp_path_factory.mktemp("m2-data")
    builds, transports, commands = [], [], []
    real_build = wiring.build
    jma_runs = 0

    class ReplayFetcherFactory:
        @staticmethod
        def from_spec(spec):
            nonlocal jma_runs
            current = dict(routes)
            if spec.id == "jp-jma":
                jma_runs += 1
                current[FEED_URL] = Recorded(
                    directory / f"jma-feed-{min(jma_runs, 2)}.synthetic.xml",
                    headers={"content-type": "application/xml"},
                )
            transport = ReplayTransport(current)
            transports.append((spec.id, transport))
            return PoliteHttpFetcher.from_spec(
                spec, transport=transport, now=lambda: NOW, clock=lambda: 0.0, sleep=lambda seconds: None,
            )

    def build(path):
        deps = real_build(path, {
            "registry": registry, "fetcher": ReplayFetcherFactory, "vectors": None,
            "crawl_deps_type": partial(CrawlDeps, now=lambda: NOW, clock=lambda: 0.0, sleep=lambda seconds: None),
        })
        builds.append(deps)
        return deps

    def forbid_network(*args, **kwargs):
        pytest.fail("M2 must use ReplayTransport for every HTTP request")

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATUMOJ_DATA_DIR", str(data_dir))
        patch.setattr(cli.wiring, "build", build)
        patch.setattr(httpx.HTTPTransport, "handle_request", forbid_network)
        runner = CliRunner()

        def invoke(args):
            before = len(builds)
            result = runner.invoke(cli.app, args)
            commands.append({"args": args, "exit_code": result.exit_code, "output": result.output})
            _json(directory / "commands.json", commands)
            for deps in builds[before:]:
                _close(deps)
            assert result.exit_code == 0, result.output
            return result

        for source in SOURCES:
            invoke(["crawl", source])
            invoke(["parse", source])
        second = invoke(["crawl", "jp-jma"])
        invoke(["parse", "jp-jma"])
        third = invoke(["crawl", "jp-jma"])
        invoke(["index", "--rebuild"])
        _json(directory / "requests.json", [{"source": source, "requests": t.requests} for source, t in transports])
        deps = build(data_dir)
        try:
            yield {
                "deps": deps, "directory": directory, "invoke": invoke, "transports": transports,
                "new_entries": new_entries, "second": second, "third": third,
            }
        finally:
            _close(deps)


def test_source_configs_and_real_parser_dispatch(stats_chain):
    deps = stats_chain["deps"]
    original = load_registry(ROOT / "sources")
    parsers = discover_parsers()
    for source, crawler in SOURCES.items():
        spec = original.get(source)
        assert spec is not None and deps.crawler_classes[spec.kind] is crawler
        snapshots = deps.manifest.snapshots_for(source)
        assert snapshots
        for snap in snapshots:
            assert snap.outcome is FetchOutcome.ok
            deps.raw.verify(snap.sha256)
            parser = wiring._CkanPages if spec.kind == "ckan" else select_parser(parsers, spec.kind, snap.content_type)
            assert parser is not None
            record = deps.manifest.get_parse(snap.sha256, parser.name, parser.version)
            assert record is not None and record.status is ParseStatus.ok
    assert deps.observations.query_readonly(
        "SELECT count(*) FROM observations WHERE source_id = 'jp-stat-dashboard'"
    ).rows == [(1057,)]


def test_catalog_questions_recall_at_10(stats_chain):
    deps = stats_chain["deps"]
    questions = [q for q in load_questions(ROOT / "eval/questions") if Path(q._source_file).name == "catalog.yaml"]
    assert len(questions) == 10
    results = []
    for question in questions:
        found = deps.tools.search_catalog(question.queries[0], k=10, sources=question.source_ids)
        keys = [item["dataset_key"] for item in found["data"]["datasets"]]
        expected = {hit.doc_key for hit in question.expect.hits}
        recall = len(expected & set(keys)) / len(expected)
        results.append({"id": question.id, "recall_at_10": recall, "response": found})
    recall = sum(row["recall_at_10"] for row in results) / len(results)
    _json(stats_chain["directory"] / "catalog-evaluation.json", {"recall_at_10": recall, "questions": results})
    assert recall >= 0.85, [(row["id"], row["recall_at_10"]) for row in results]


def _assert_citation(deps, item):
    citation = Citation.model_validate(item["citation"])
    assert citation.time_basis is TimeBasis.period
    assert citation.fetched_at == NOW
    assert citation.usage_scope == deps.registry.get(citation.source_id).license.usage_scope
    assert any(s.resource_url == citation.resource_url for s in deps.manifest.snapshots_by_sha256(citation.snapshot_sha256))
    deps.raw.verify(citation.snapshot_sha256)
    restored = deps.tools.cite(citation.snapshot_sha256, citation.locator)
    assert restored["data"]["status"] == "ok"
    passages = restored["data"]["passages"]
    assert len(passages) == 1
    assert passages[0]["kind"] == "observation"
    assert SourceText.model_validate(passages[0]["indicator"]).content
    assert passages[0]["value"] == item["value"]
    return citation


def test_stats_questions_numeric_precision_and_cli_citations(stats_chain):
    deps = stats_chain["deps"]
    questions = [q for q in load_questions(ROOT / "eval/questions") if Path(q._source_file).name == "stats.yaml"]
    assert len(questions) == 10
    report = evaluate(questions, retriever=deps.retriever, observation_store=deps.observations, cite=None, k=10)
    write_reports(report, stats_chain["directory"] / "stats")
    assert report.overall["numeric_precision"] == 1.0, report.failures
    checked = []
    for question in questions:
        # Execute the original question SQL, then select obs_id as required by
        # ObservationStore to restore evidence. Predicates remain unchanged.
        result = stats_chain["invoke"](["sql", question.expect.value.sql, "--json"])
        payload = json.loads(result.output)["data"]
        # 题库 SQL 自 707 起是 SELECT obs_id, value：按列名取 value，不依赖列顺序。
        value_index = [c if isinstance(c, str) else c.get("content") for c in payload["columns"]].index("value")
        assert Decimal(payload["rows"][0][value_index]) == question.expect.value.equals
        sql = question.expect.value.sql  # 707 起题库 SQL 已带 obs_id
        result = stats_chain["invoke"](["sql", sql, "--json"])
        data = json.loads(result.output)["data"]
        assert len(data["rows"]) == len(data["provenance"]) == 1
        item = data["provenance"][0]
        assert Decimal(item["value"]) == question.expect.value.equals
        citation = _assert_citation(deps, item)
        raw = deps.raw.get(citation.snapshot_sha256)
        if citation.source_id == "jp-stat-dashboard":
            row = int(citation.locator.removeprefix("row="))
            original = json.loads(raw)["GET_STATS"]["STATISTICAL_DATA"]["DATA_INF"]["DATA_OBJ"][row - 1]["VALUE"]
            assert Decimal(original["$"]) == question.expect.value.equals
        else:
            assert citation.locator == "cell=I977;row=977"
            original = list(csv.reader(raw.decode("cp932").splitlines()))[976][8]
            assert Decimal(original) == question.expect.value.equals
        human = stats_chain["invoke"](["sql", sql])
        assert citation.render() in human.output
        checked.append({
            "id": question.id, "sql": question.expect.value.sql, "value": item["value"],
            "citation": citation.model_dump(mode="json"), "original_cell_matches": True,
        })
    _json(stats_chain["directory"] / "numeric-verification.json", {"numeric_precision": 1.0, "questions": checked})


def test_prefecture_population_matches_xlsx_and_missing_notes(stats_chain):
    deps = stats_chain["deps"]
    path = FIXTURES / "estat_files/000013168605_jinsui_pref_population_2000-2020.xlsx"
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    checked = []
    try:
        for area, year, cell in [("13000", 2000, "E24"), ("27000", 2010, "O38"), ("47000", 2015, "T58")]:
            sql = (
                "SELECT value, obs_id FROM observations WHERE source_id = 'jp-estat-files' "
                f"AND table_key = '000013168605' AND area_code = '{area}' "
                f"AND time_start = '{year}-10-01' AND indicator = '総人口'"
            )
            result = stats_chain["invoke"](["sql", sql, "--json"])
            data = json.loads(result.output)["data"]
            assert len(data["rows"]) == 1
            assert Decimal(data["rows"][0][0]) == Decimal(str(book.worksheets[0][cell].value))
            assert data["provenance"][0]["unit"]["content"] == "千人"
            citation = _assert_citation(deps, data["provenance"][0])
            assert citation.period.start.isoformat() == f"{year}-10-01"
            checked.append({
                "area_code": area, "year": year, "original_cell": cell, "value": data["rows"][0][0],
                "unit": "千人", "citation": citation.model_dump(mode="json"),
            })
    finally:
        book.close()
    missing = deps.observations.query_readonly(
        "SELECT value, note FROM observations WHERE source_id = 'jp-estat-files' "
        "AND table_key = '000031720773' AND locator = 'cell=L15;row=15'"
    )
    assert missing.rows == [(None, "-")]
    _json(stats_chain["directory"] / "population-verification.json", checked)


def test_jma_second_feed_downloads_only_new_projected_entries(stats_chain):
    deps = stats_chain["deps"]
    transports = [t for source, t in stats_chain["transports"] if source == "jp-jma"]
    assert len(transports) == 3
    assert len(transports[0].requests) == 5  # robots, feed, three messages
    assert set(transports[1].requests[2:]) == stats_chain["new_entries"]
    assert transports[2].requests == ["https://www.data.jma.go.jp/robots.txt", FEED_URL]
    assert "新增 3，未变化 0，失败 0" in stats_chain["second"].output
    assert "新增 0，未变化 1，失败 0" in stats_chain["third"].output
    snapshots = deps.manifest.snapshots_for("jp-jma")
    entries = [s for s in snapshots if deps.manifest.snapshot_meta(s.snapshot_id).get("resource_type") == "entry"]
    assert len(entries) == len({s.resource_url for s in entries}) == 5
    chunks = list(deps.chunks.iter_chunks(["jp-jma"]))
    assert len(chunks) == 5
    for chunk in chunks:
        restored = deps.tools.cite(chunk.snapshot_sha256, chunk.locator)
        assert restored["data"]["status"] == "ok"
    _json(stats_chain["directory"] / "jma-verification.json", {
        "projected_first_entries": 3, "projected_second_entries": 5,
        "second_downloads": sorted(stats_chain["new_entries"]), "third_downloads": [],
    })


@pytest.mark.parametrize("payload", [
    b"not json", b'{"success":true,"result":{"results":{}}}',
    b'{"success":false,"result":{"results":[]}}',
    b'{"success":true,"result":{"results":[{}]}}',
])
def test_ckan_page_dispatch_preserves_validation(stats_chain, payload):
    snapshot = stats_chain["deps"].manifest.snapshots_for("jp-egov-data")[0]
    with pytest.raises(ParseError):
        wiring._CkanPages().parse(snapshot, payload, {"role": "package_search"})


class TestCkanPageContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return wiring._CkanPages()

    @pytest.fixture
    def samples(self):
        result = []
        for name in ("package_search_q-jinko_rows5_start0.json", "package_show_cao_syukujitsu.json"):
            data = (FIXTURES / "ckan_egov" / name).read_bytes()
            snapshot = make_snapshot("jp-egov-data", data=data, content_type="application/json")
            result.append(ParseSample(snapshot, data))
        return result


class TestCatalogQueryContract(CatalogStoreSuite):
    @pytest.fixture
    def store(self, tmp_path):
        store = wiring._CatalogQueries(tmp_path / "catalog.sqlite")
        try:
            yield store
        finally:
            store.close()


def test_catalog_multi_term_queries_keep_source_filter_and_limit(stats_chain):
    tools = stats_chain["deps"].tools
    assert len(tools.search_catalog("福岡市 国勢調査", sources="jp-egov-data", k=1)["data"]["datasets"]) == 1
    assert tools.search_catalog("福岡市 国勢調査", sources="jp-jma")["data"]["datasets"] == []


@pytest.mark.parametrize("role", ["resource", "activity"])
def test_ckan_non_package_snapshots_are_not_sent_to_package_parser(stats_chain, role):
    snapshot = stats_chain["deps"].manifest.snapshots_for("jp-egov-data")[0]
    parsed = wiring._CkanPages().parse(snapshot, b"not a package", {"role": role})
    assert not (parsed.datasets or parsed.resources or parsed.documents or parsed.chunks or parsed.observations)
