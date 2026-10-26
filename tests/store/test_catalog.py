import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import CatalogFilter, ContentState, ParseError
from datumoj.rikolto.parsers.ckan_meta import CkanMetaParser
from datumoj.rikolto.store.catalog import SqliteCatalogStore
from datumoj.rikolto.testing.contract_suites import CatalogStoreSuite, ParserSuite
from datumoj.rikolto.testing.samples import ParseSample, make_dataset, make_resource, make_snapshot


FIXTURES = Path(__file__).parents[1] / "fixtures" / "ckan_egov"
PARSER_SOURCE = "jp-ckan-egov"


def _parse_sample(filename: str) -> ParseSample:
    data = (FIXTURES / filename).read_bytes()
    return ParseSample(
        snapshot=make_snapshot(
            source_id=PARSER_SOURCE,
            url="https://data.e-gov.go.jp/data/api/action/package_show",
            data=data,
            content_type="application/json",
        ),
        data=data,
    )


class TestSqliteCatalogStore(CatalogStoreSuite):
    @pytest.fixture
    def store(self, tmp_path):
        store = SqliteCatalogStore(tmp_path / "catalog.sqlite")
        yield store
        store.close()


class TestCkanMetaParser(ParserSuite):
    @pytest.fixture
    def parser(self):
        return CkanMetaParser()

    @pytest.fixture
    def samples(self):
        return [
            _parse_sample("package_show_cao_syukujitsu.json"),
            _parse_sample("package_show_fukuoka_census_h22_1-6.json"),
            _parse_sample("package_show_jftc_nenpou_r7.json"),
        ]


def test_ckan_parser_maps_source_fields_and_keeps_both_license_levels():
    parser = CkanMetaParser()
    fukuoka = _parse_sample("package_show_fukuoka_census_h22_1-6.json")
    result = parser.parse(fukuoka.snapshot, fukuoka.data)
    dataset = result.datasets[0]

    assert dataset.dataset_key == f"{PARSER_SOURCE}:275043f1-d8a4-418f-96df-1aba07f1eaa2"
    assert dataset.title.startswith("平成２２年")
    assert dataset.description.startswith("人口")
    assert dataset.publisher == "福岡市"
    assert dataset.tags == ["統計"]
    assert dataset.license is None
    assert dataset.extras["local_government"] == "福岡県福岡市"
    assert len(result.resources) == 30
    assert result.resources[0].license == "cc-by"
    assert result.resources[0].format == "XLSX"
    assert result.resources[0].url.startswith("https://data.bodik.jp/")
    assert result.resources[0].size == 31078

    caof = _parse_sample("package_show_cao_syukujitsu.json")
    cao = parser.parse(caof.snapshot, caof.data)
    assert cao.datasets[0].extras["frequency_of_update"] == "1nen "
    assert cao.resources[0].size == 19000  # source declaration is retained as advisory metadata

    jftc = _parse_sample("package_show_jftc_nenpou_r7.json")
    jftc_result = parser.parse(jftc.snapshot, jftc.data)
    assert jftc_result.datasets[0].license is None
    assert jftc_result.resources[0].license == ""


def test_parser_uses_utc_for_ckan_timestamps_without_zone():
    sample = _parse_sample("package_show_cao_syukujitsu.json")
    parsed = CkanMetaParser().parse(sample.snapshot, sample.data)
    assert parsed.datasets[0].modified_at.tzinfo is UTC
    assert parsed.datasets[0].modified_at == datetime.fromisoformat("2026-10-19T13:04:21.553323+00:00")


def test_catalog_keeps_messy_format_values_and_filters_by_normalized_values(tmp_path):
    store = SqliteCatalogStore(tmp_path / "catalog.sqlite")
    dataset = make_dataset("jp-data:formats", "格式一覧", publisher=" 公開元 ")
    resources = [
        make_resource(dataset.dataset_key, "wide", "https://example.go.jp/wide", format="ＣＳＶ", license="CC-BY"),
        make_resource(dataset.dataset_key, "dot", "https://example.go.jp/dot", format=".CSV", license="cc-by"),
        make_resource(dataset.dataset_key, "zero", "https://example.go.jp/zero", format="0"),
        make_resource(dataset.dataset_key, "blank", "https://example.go.jp/blank", format=" ", license=" "),
        make_resource(dataset.dataset_key, "empty", "https://example.go.jp/empty", format="", license=""),
        make_resource(dataset.dataset_key, "legacy", "https://example.go.jp/legacy", license="gjstu"),
        make_resource(dataset.dataset_key, "jp-terms", "https://example.go.jp/jp-terms",
                      license="政府標準利用規約（第 2.0 版）"),
    ]
    unknown_dataset = make_dataset("jp-data:unknown", "未知形式")
    unknown_resources = [
        make_resource(unknown_dataset.dataset_key, "blank", "https://example.go.jp/unknown-blank", format=" "),
        make_resource(unknown_dataset.dataset_key, "empty", "https://example.go.jp/unknown-empty", format=""),
    ]
    zero_dataset = make_dataset("jp-data:zero-format", "0 形式")
    zero_resource = make_resource(zero_dataset.dataset_key, "zero", "https://example.go.jp/zero-only", format="0")
    store.upsert([dataset, unknown_dataset, zero_dataset], [*resources, *unknown_resources, zero_resource])

    hit, = store.search("", CatalogFilter(formats=["csv"]))
    assert [entry.resource.format for entry in hit.resources[:2]] == ["ＣＳＶ", ".CSV"]
    unknown_hits = store.search("", CatalogFilter(formats=["未知"]))
    assert {hit.dataset.dataset_key for hit in unknown_hits} == {dataset.dataset_key, unknown_dataset.dataset_key}
    unknown = next(hit for hit in unknown_hits if hit.dataset.dataset_key == unknown_dataset.dataset_key)
    assert unknown.dataset.dataset_key == unknown_dataset.dataset_key
    assert [entry.resource.format for entry in unknown.resources] == [" ", ""]
    zero_hits = store.search("", CatalogFilter(formats=["0"]))
    assert {hit.dataset.dataset_key for hit in zero_hits} == {dataset.dataset_key, zero_dataset.dataset_key}
    assert store.search("", CatalogFilter(formats=[])) == []
    assert {entry.resource.license for entry in store.resources_for(dataset.dataset_key)} == {
        "CC-BY", "cc-by", " ", "", "gjstu", "政府標準利用規約（第 2.0 版）", None,
    }
    store.close()


def test_parser_preserves_raw_resource_format_and_license_values():
    package = {
        "id": "raw-values",
        "title": "raw values",
        "license_id": None,
        "license_title": "dataset display license",
        "resources": [
            {"id": "fullwidth", "url": "https://example.go.jp/fullwidth", "format": "ＣＳＶ", "license_id": "cc-by"},
            {"id": "dot", "url": "https://example.go.jp/dot", "format": ".CSV", "license_id": "CC-BY"},
            {"id": "zero", "url": "https://example.go.jp/zero", "format": "0", "license_id": "gjstu"},
            {"id": "blank", "url": "https://example.go.jp/blank", "format": " ", "license_id": " "},
            {"id": "empty", "url": "https://example.go.jp/empty", "format": "", "license_id": ""},
            {"id": "japanese", "url": "https://example.go.jp/japanese", "license_id": "政府標準利用規約（第 2.0 版）"},
        ],
    }
    data = json.dumps({"success": True, "result": package}, ensure_ascii=False).encode()
    snapshot = make_snapshot(PARSER_SOURCE, "https://data.e-gov.go.jp/package_show", data)
    parsed = CkanMetaParser().parse(snapshot, data)
    assert parsed.datasets[0].license is None
    assert parsed.datasets[0].extras["license_title"] == "dataset display license"
    assert [resource.format for resource in parsed.resources] == ["ＣＳＶ", ".CSV", "0", " ", "", None]
    assert [resource.license for resource in parsed.resources] == [
        "cc-by", "CC-BY", "gjstu", " ", "", "政府標準利用規約（第 2.0 版）",
    ]


def test_catalog_short_query_and_status_are_returned(tmp_path):
    store = SqliteCatalogStore(tmp_path / "catalog.sqlite")
    dataset = make_dataset("jp-data:population", "人口統計", description="都道府県別", tags=["国勢調査"])
    resource = make_resource(dataset.dataset_key, "csv", "https://example.go.jp/pop.csv", format="CSV")
    store.upsert([dataset], [resource])

    hit, = store.search("人口")
    assert hit.dataset == dataset and hit.state is ContentState.catalog_only
    assert store.set_content_state(resource.url, ContentState.content_fetched) == 1
    hit, = store.search("人口統計")
    assert hit.state is ContentState.content_fetched
    assert hit.resources[0].state is ContentState.content_fetched
    assert store.set_content_state(resource.url, ContentState.observations_loaded) == 1
    assert store.search("人口統計")[0].state is ContentState.observations_loaded
    store.close()


def test_upsert_is_atomic_and_reopen_preserves_catalog_and_state(tmp_path):
    path = tmp_path / "catalog.sqlite"
    store = SqliteCatalogStore(path)
    dataset = make_dataset("jp-data:one", "人口統計")
    resource = make_resource(dataset.dataset_key, "csv", "https://example.go.jp/pop.csv", format="ＣＳＶ")
    store.upsert([dataset], [resource])
    store.set_content_state(resource.url, ContentState.content_fetched)
    with pytest.raises(ValueError):
        store.upsert(resources=[make_resource("jp-data:missing", "bad", "https://example.go.jp/bad")])
    store.close()

    reopened = SqliteCatalogStore(path)
    assert reopened.get_dataset(dataset.dataset_key) == dataset
    assert reopened.resources_for(dataset.dataset_key)[0].state is ContentState.content_fetched
    assert reopened.search("人口")[0].dataset == dataset
    reopened.close()


def test_parser_rejects_non_package_json_as_parse_error():
    snapshot = make_snapshot(
        source_id=PARSER_SOURCE,
        url="https://data.e-gov.go.jp/data/api/action/package_show",
        data=b"{}",
    )
    with pytest.raises(ParseError):
        CkanMetaParser().parse(snapshot, b"not-json")

