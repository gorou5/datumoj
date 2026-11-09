"""Restore catalog and observation citations by reparsing offline recordings."""

import io
import json
from collections import defaultdict
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
import yaml

from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.contracts import ParseError, SourceSpec
from datumoj.rikolto.parsers.data_europa import DataEuropaParser
from datumoj.rikolto.parsers.estat_api import EstatAPIParser
from datumoj.rikolto.parsers.estat_tables import EstatTableParser
from datumoj.rikolto.parsers.eurostat import EurostatParser, read_structure
from datumoj.rikolto.parsers.ksj import KsjParser
from datumoj.rikolto.parsers.mhlw_ndb import MEMBER, MhlwNdbParser
from datumoj.rikolto.parsers.stat_dashboard import StatDashboardParser
from datumoj.rikolto.parsers.udata import UdataParser
from datumoj.rikolto.parsers.wdi import WDIParser
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeManifest,
    FakeObservationStore,
    FakeRawStore,
    FakeRegistry,
)
from datumoj.rikolto.testing.samples import make_snapshot
from datumoj.rikolto.wiring import _CkanPages

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures"
ROLE = "untrusted_source_text"
CASES = [
    ("eu-data-europa", "data_europa/repo_dataset_combined-nomenclature-2023.jsonld", DataEuropaParser, "entry=40"),
    ("fr-data-gouv", "udata_fr/datasets_org-insee_page1_size2.json", UdataParser, "entry=0"),
    ("eu-eurostat", "eurostat/nama_10_gdp_DE-FR-NL_2020-2024.xml", EurostatParser, "row=1"),
    ("intl-wdi", "wdi/gdp_cd_cn_JPN-USA_2020-2024_p1.json", WDIParser, "row=1"),
    # The recorded census is XLSX. Its real parser emits sheet/cell, not member/row.
    ("jp-census", "census2024/c1_001_1.xlsx", EstatTableParser, "sheet=c1_001_1;cell=C8"),
    ("jp-bodik", "bodik/package_search-p1.json", _CkanPages, "entry=0"),
    ("jp-egov-data", "ckan_egov/package_search_q-jinko_rows5_start0.json", _CkanPages, "entry=0"),
    ("jp-estat-api", "estat_api/getStatsList_00200524_limit5.json", EstatAPIParser, "row=1"),
    ("jp-estat-files", "estat_files/000013168605_jinsui_pref_population_2000-2020.xlsx",
     EstatTableParser, "sheet=総人口（2000年～2015年）;cell=E11"),
    ("jp-stat-dashboard", "stat_dashboard/getData_b1_pop5x48_2021-2023CY.json", StatDashboardParser, "row=1"),
    ("jp-ksj", "ksj/P05-22_41_GML.zip", KsjParser, "member=KS-META-P05-22_41.xml"),
    ("jp-mhlw", "ndb/ndb11_kenshin_shitsumon01_pref_sex_age.xlsx", MhlwNdbParser,
     f"member={MEMBER};sheet=質問項目;cell=C6"),
]


@pytest.fixture(params=CASES, ids=[case[0] for case in CASES])
def ingested(request, tmp_path):
    source_id, filename, parser, locator = request.param
    spec = SourceSpec.model_validate(yaml.safe_load((ROOT / "sources" / f"{source_id}.yaml").read_text()))
    data = (FIXTURES / filename).read_bytes()
    meta = {}
    url = spec.base_url
    if spec.kind == "ckan":
        url = spec.base_url.rstrip("/") + "/package_search"
        meta = {"role": "package_search"}
    if spec.kind == "estat_files":
        meta = spec.scope["tables"][0]
        url = f"https://www.e-stat.go.jp/stat-search/file-download?statInfId={meta['stat_inf_id']}"
    if source_id == "eu-eurostat":
        structure = (FIXTURES / "eurostat/datastructure_NAMA_10_GDP.xml").read_bytes()
        meta = {"structure": read_structure(structure, "nama_10_gdp")}
    if source_id == "intl-wdi":
        # The parser requires a complete response. Combine the two captured pages
        # exactly as the existing offline parser tests do, preserving every row.
        rows = json.loads(data)[1]
        rows.extend(json.loads((FIXTURES / "wdi/gdp_cd_cn_JPN-USA_2020-2024_p2.json").read_bytes())[1])
        data = json.dumps([{"page": 1, "pages": 1, "total": len(rows)}, rows]).encode()
        meta = {"request_type": "data", "indicators": {"NY.GDP.MKTP.CD": "current US$",
                "NY.GDP.MKTP.CN": "current LCU"}, "countries": ["JPN", "USA"],
                "start_year": 2020, "end_year": 2024}
    if source_id == "jp-mhlw":
        # The fixture is the unchanged workbook extracted from the source ZIP.
        archive = io.BytesIO()
        with ZipFile(archive, "w") as zipped:
            zipped.writestr(ZipInfo(MEMBER), data, compress_type=ZIP_DEFLATED)
        data = archive.getvalue()
        meta = {"member": MEMBER}

    snapshot = make_snapshot(source_id, url, data, content_type="application/octet-stream")
    raw, manifest = FakeRawStore(tmp_path), FakeManifest()
    raw.put(data)
    manifest.record_snapshot(snapshot, meta)
    parsed = parser().parse(snapshot, data, meta)
    parsed.check_against(snapshot)
    catalog, observations, chunks = FakeCatalogStore(), FakeObservationStore(), FakeChunkStore()
    catalog.upsert(parsed.datasets, parsed.resources)
    by_table = defaultdict(list)
    for observation in parsed.observations:
        by_table[observation.table_key].append(observation)
    for table_key, rows in by_table.items():
        observations.write_table(source_id, table_key, rows)
    for document in parsed.documents:
        chunks.put_document(document, [chunk for chunk in parsed.chunks if chunk.doc_key == document.doc_key])
    tools = AnalysisTools(
        retriever=None, catalog=catalog, observations=observations, chunks=chunks,
        raw=raw, manifest=manifest, registry=FakeRegistry([spec]),
        parsers={(spec.kind, "*"): parser}, data_dir=tmp_path,
    )
    return tools, snapshot, parsed, locator, parser


def test_recorded_citation_round_trip(ingested, monkeypatch):
    tools, snapshot, parsed, locator, parser = ingested
    matching_datasets = [dataset for dataset in parsed.datasets if dataset.locator == locator]
    if matching_datasets:
        hits = tools.search_catalog(sources=snapshot.source_id)["data"]["datasets"]
        citation = next(hit["citation"] for hit in hits if hit["dataset_key"] == matching_datasets[0].dataset_key)
        assert citation["snapshot_sha256"] == snapshot.sha256 and citation["locator"] == locator

    calls = []
    original_parse = parser.parse

    def reparse(self, snap, raw, meta):
        calls.append((snap, raw, meta))
        return original_parse(self, snap, raw, meta)

    monkeypatch.setattr(parser, "parse", reparse)
    # Disallow all reads of indexed content after ingestion and catalog search.
    tools.catalog = tools.observations = tools.chunks = None
    restored = tools.cite(snapshot.sha256, locator)
    assert restored["data"]["status"] == "ok" and restored["gap_state"] == "ok"
    assert len(calls) == (0 if parser is _CkanPages else 1)
    if calls:
        assert calls[0] == (snapshot, tools.raw.get(snapshot.sha256), tools.manifest.snapshot_meta(snapshot.snapshot_id))
    (passage,) = restored["data"]["passages"]
    assert passage["text"]["content_role"] == ROLE
    text = passage["text"]["content"]
    matching_chunks = [chunk for chunk in parsed.chunks if chunk.locator == locator]
    matching_observations = [obs for obs in parsed.observations if obs.locator == locator]
    if parser is _CkanPages:
        record = json.loads(tools.raw.get(snapshot.sha256))["result"]["results"][0]
        assert passage["kind"] == "catalog_entry" and json.loads(text) == record
        assert r"\u" in text  # Keep the original CKAN JSON spelling.
    elif matching_chunks:
        assert passage["kind"] == "chunk" and text == matching_chunks[0].text
    elif matching_observations:
        assert passage["kind"] == "observation"
        assert json.loads(text) == matching_observations[0].model_dump(mode="json")
        assert passage["citation"]["snapshot_id"] == snapshot.snapshot_id
    else:
        dataset = matching_datasets[0]
        assert passage["kind"] == "catalog_entry" and dataset.title in text
        assert text == "\n".join(t for t in [dataset.title, dataset.description, *dataset.tags] if t is not None)
    assert restored["freshness"]["as_of"] == snapshot.fetched_at.isoformat()


def test_nonexistent_index_has_reason(ingested):
    tools, snapshot, _, locator, _ = ingested
    missing = locator.rsplit("=", 1)[0] + "=999999999"
    restored = tools.cite(snapshot.sha256, missing)
    assert restored["data"]["status"] == "not_found"
    assert restored["data"]["passages"] == [] and restored["gap_state"] == "zero_hits"
    assert restored["gap_detail"]


@pytest.mark.parametrize("failure", ["corrupt", "missing", "parse"])
def test_raw_and_parser_failures_keep_their_reasons(ingested, monkeypatch, failure):
    tools, snapshot, _, locator, parser = ingested
    if failure == "corrupt":
        tools.raw.blobs[snapshot.sha256] = b"tampered"
        reason = "sha256 不一致"
    elif failure == "missing":
        tools.raw.blobs.clear()
        reason = "原始字节"
    else:
        def fail(*args, **kwargs):
            raise ParseError("source text must not escape")

        monkeypatch.setattr(parser, "parse", fail)
        if parser is _CkanPages:
            # The CKAN entry path intentionally does not call a parser.
            assert tools.cite(snapshot.sha256, locator)["data"]["status"] == "ok"
            return
        reason = "无法解析"
    restored = tools.cite(snapshot.sha256, locator)
    assert restored["data"]["status"] == "not_found" and reason in restored["gap_detail"]
    assert restored["data"]["passages"] == []


def test_shared_bytes_stay_ambiguous_until_resource_is_selected(ingested):
    tools, snapshot, _, locator, _ = ingested
    other = snapshot.model_copy(update={"snapshot_id": "other-resource", "resource_url": snapshot.resource_url + "/other"})
    tools.manifest.record_snapshot(other, tools.manifest.snapshot_meta(snapshot.snapshot_id))
    restored = tools.cite(snapshot.sha256, locator)
    assert restored["data"]["status"] == "ambiguous" and restored["gap_detail"]
    assert {candidate["snapshot_id"] for candidate in restored["data"]["candidates"]} == {
        snapshot.snapshot_id, other.snapshot_id,
    }
    for identity in ({"snapshot_id": snapshot.snapshot_id}, {"resource_url": snapshot.resource_url}):
        assert tools.cite(snapshot.sha256, locator, **identity)["data"]["status"] == "ok"
