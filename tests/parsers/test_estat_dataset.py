from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.contracts import ContentState
from datumoj.rikolto.parsers.estat_tables import EstatTableParser
from datumoj.rikolto.testing.contract_suites import ParserSuite
from datumoj.rikolto.testing.fakes import FakeCatalogStore, FakeManifest, FakeObservationStore, FakeRawStore, FakeRegistry
from datumoj.rikolto.testing.samples import ParseSample, make_snapshot, make_spec

FIXTURES = Path(__file__).parents[1] / "fixtures" / "estat_files"
DOWNLOAD = "https://www.e-stat.go.jp/stat-search/file-download?statInfId={}&fileKind={}"

TABLES = [
    {
        "stat_inf_id": "000013168605",
        "survey": "人口推計",
        "title": "第5表 都道府県別人口(各年10月1日現在)－ 総人口、日本人",
        "layout": "wide_area_by_year",
        "expected_area_level": "prefecture",
        "filename": "000013168605_jinsui_pref_population_2000-2020.xlsx",
        "file_kind": 4,
        "format": "xlsx",
        "media_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "time_labels": {"earliest": "2000年", "latest": "2020年"},
    },
    {
        "stat_inf_id": "000031720773",
        "survey": "平成28年経済センサス‐活動調査",
        "title": "第２表　産業（細分類）、経営組織（４区分）別民営事業所数、男女別従業者数及び１事業所当たり従業者数―全国、都道府県",
        "layout": "census_area_by_measure",
        "expected_area_level": "prefecture",
        "area_code": "13000",
        "filename": "000031720773_ecensus2016_t02_tokyo.csv",
        "file_kind": 1,
        "format": "csv",
        "media_type": "text/csv",
        "time_labels": {"earliest": "平成28年", "latest": "平成28年"},
    },
    {
        "stat_inf_id": "000031721271",
        "survey": "平成28年経済センサス‐活動調査",
        "title": "第２表　産業（細分類）、経営組織（４区分）別民営事業所数、男女別従業者数及び１事業所当たり従業者数―全国、都道府県",
        "layout": "census_area_by_measure",
        "expected_area_level": "prefecture",
        "area_code": "27000",
        "filename": "000031721271_ecensus2016_t02_osaka.csv",
        "file_kind": 1,
        "format": "csv",
        "media_type": "text/csv",
        "time_labels": {"earliest": "平成28年", "latest": "平成28年"},
    },
]


def _sample(table: dict[str, object]) -> ParseSample:
    data = (FIXTURES / str(table["filename"])).read_bytes()
    url = DOWNLOAD.format(table["stat_inf_id"], table["file_kind"])
    snapshot = make_snapshot(
        source_id="jp-estat-files",
        url=url,
        data=data,
        content_type="application/octet-stream" if table["format"] == "xlsx" else "text/csv",
    )
    meta = {key: value for key, value in table.items() if key not in {
        "filename", "file_kind", "format", "media_type", "time_labels",
    }}
    return ParseSample(snapshot, data, meta)


def _normalize_title(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).replace("、", ",")


class TestEstatDatasetParserContract(ParserSuite):
    @pytest.fixture
    def parser(self):
        return EstatTableParser()

    @pytest.fixture
    def samples(self):
        return [_sample(table) for table in TABLES]

    @pytest.fixture
    def bad_samples(self):
        return [b"<html><body>error</body></html>"]


@pytest.mark.parametrize("table", TABLES, ids=lambda table: str(table["stat_inf_id"]))
def test_dataset_resource_and_title_citation_are_stable(table):
    sample = _sample(table)
    parser = EstatTableParser()
    first = parser.parse(sample.snapshot, sample.data, sample.meta)
    second = parser.parse(sample.snapshot, sample.data, sample.meta)

    assert len(first.datasets) == 1
    dataset = first.datasets[0]
    dataset_key = f"jp-estat-files:{table['stat_inf_id']}"
    assert dataset.dataset_key == dataset_key
    assert dataset.kind == "stat_table"
    assert dataset.title == table["title"]
    assert dataset.description is not None
    assert all(text in dataset.description for text in (
        str(table["survey"]),
        str(table["time_labels"]["earliest"]),
        str(table["time_labels"]["latest"]),
        str(table["expected_area_level"]),
    ))
    assert dataset.publisher is None
    assert dataset.url == sample.snapshot.resource_url
    assert dataset.tags == [table["survey"]]
    assert dataset.extras == {
        "stat_inf_id": table["stat_inf_id"],
        "survey": table["survey"],
        "time_labels": table["time_labels"],
        "area_level": table["expected_area_level"],
    }
    assert dataset.snapshot_sha256 == sample.snapshot.sha256

    if table["format"] == "xlsx":
        assert dataset.locator.startswith("sheet=") and ";cell=" in dataset.locator
    else:
        assert dataset.locator.startswith("cell=") and ";row=" in dataset.locator
    # cite resolves exact locators from parsed chunks; the title chunk is the table cell named by Dataset.locator.
    matching_chunks = [chunk for chunk in first.chunks if chunk.locator == dataset.locator]
    assert len(matching_chunks) == 1
    assert _normalize_title(str(table["title"])) in _normalize_title(matching_chunks[0].text)

    assert len(first.resources) == 1
    resource = first.resources[0]
    assert resource.dataset_key == dataset_key
    assert resource.url == sample.snapshot.resource_url
    assert resource.format == table["format"]
    assert resource.media_type == table["media_type"]
    assert first.datasets == second.datasets
    assert first.resources == second.resources
    assert first.observations == second.observations
    assert first.observations and all(obs.dataset_key == dataset_key for obs in first.observations)


def test_publisher_comes_from_the_table_entry_when_present():
    sample = _sample(TABLES[0])
    meta = {**sample.meta, "publisher": "テスト用機関"}
    result = EstatTableParser().parse(sample.snapshot, sample.data, meta)
    assert result.datasets[0].publisher == "テスト用機関"


def test_catalog_cite_and_sql_find_the_parsed_table():
    sample = _sample(TABLES[0])
    parsed = EstatTableParser().parse(sample.snapshot, sample.data, sample.meta)
    manifest, raw = FakeManifest(), FakeRawStore()
    manifest.record_snapshot(sample.snapshot, sample.meta)
    raw.put(sample.data)
    catalog = FakeCatalogStore()
    catalog.upsert(parsed.datasets, parsed.resources)
    catalog.set_content_state(sample.snapshot.resource_url, ContentState.observations_loaded)
    observations = FakeObservationStore()
    observations.write_table(sample.snapshot.source_id, str(TABLES[0]["stat_inf_id"]), parsed.observations)
    registry = FakeRegistry([make_spec(
        "jp-estat-files",
        kind="estat_files",
        base_url="https://www.e-stat.go.jp/",
        allowed_hosts=["www.e-stat.go.jp"],
        scope={"urls": [sample.snapshot.resource_url]},
    )])
    tools = AnalysisTools(
        retriever=None,
        catalog=catalog,
        observations=observations,
        chunks=None,
        manifest=manifest,
        raw=raw,
        registry=registry,
        parsers={("estat_files", "*"): EstatTableParser},
    )

    for query in (str(TABLES[0]["title"]), str(TABLES[0]["survey"])):
        found = tools.search_catalog(query, sources="jp-estat-files")["data"]["datasets"]
        assert [item["dataset_key"] for item in found] == [parsed.datasets[0].dataset_key]
        assert found[0]["state"] == ContentState.observations_loaded

    citation = tools.cite(sample.snapshot.sha256, parsed.datasets[0].locator)["data"]
    assert citation["status"] == "ok"
    assert any(passage["locator"] == parsed.datasets[0].locator for passage in citation["passages"])

    query = (
        "SELECT obs_id, value FROM observations "
        f"WHERE table_key = '{TABLES[0]['stat_inf_id']}' LIMIT 1"
    )
    sql_result = tools.query_sql(query)["data"]
    assert sql_result["rows"]
    assert sql_result["rows"][0][1] in ("126926", 126926)
