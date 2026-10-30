import json
from datetime import date
from pathlib import Path

from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.models import LicenseInfo, Redistribution, UsageScope
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeManifest,
    FakeObservationStore,
    FakeRawStore,
    FakeRegistry,
    FakeRetriever,
)
from datumoj.rikolto.testing.samples import T0, make_dataset, make_snapshot, make_spec
from datumoj.rikolto.wiring import _CkanPages

FIXTURES = Path(__file__).parents[1] / "fixtures" / "ckan_egov"
SOURCE_ID = "jp-ckan-egov"
SEARCH_URL = "https://data.e-gov.go.jp/data/api/action/package_search?q=%E4%BA%BA%E5%8F%A3"


def _tools(tmp_path):
    data = (FIXTURES / "package_search_q-jinko_rows5_start0.json").read_bytes()
    raw = FakeRawStore(tmp_path)
    manifest = FakeManifest()
    catalog = FakeCatalogStore()
    chunks = FakeChunkStore()
    observations = FakeObservationStore()
    license = LicenseInfo(
        name="CC BY 4.0", url="https://example.go.jp/license", attribution="出典：e-Gov",
        usage_scope=UsageScope.redistributable, redistribution=Redistribution.allowed, outflow="raw_ok",
        verified_at=date(2026, 10, 20), notes="テスト用の検証根拠",
    )
    registry = FakeRegistry([
        make_spec(SOURCE_ID, kind="ckan", license=license),
        make_spec("jp-estat", kind="estat"),
    ])
    snapshot = make_snapshot(SOURCE_ID, SEARCH_URL, data, fetched_at=T0, content_type="application/json")
    raw.put(data)
    manifest.record_snapshot(snapshot, {"role": "package_search"})
    parsed = _CkanPages().parse(snapshot, data, {"role": "package_search"})

    estat_data = b"e-Stat table source"
    estat_snapshot = make_snapshot(
        "jp-estat", "https://api.e-stat.go.jp/rest/3.0/app/json/getStatsData", estat_data,
        fetched_at=T0, content_type="application/json",
    )
    raw.put(estat_data)
    manifest.record_snapshot(estat_snapshot)
    estat_dataset = make_dataset(
        "jp-estat:table-1", "e-Stat table", snapshot_sha256=estat_snapshot.sha256, locator="cell=A1",
    )
    old_dataset = make_dataset("jp-ckan-egov:old-record", "legacy CKAN record")
    catalog.upsert([*parsed.datasets, estat_dataset, old_dataset], parsed.resources)
    tools = AnalysisTools(
        retriever=FakeRetriever(chunks, manifest, registry), catalog=catalog, observations=observations,
        chunks=chunks, manifest=manifest, raw=raw, registry=registry,
        parsers={("ckan", "*"): _CkanPages}, data_dir=tmp_path,
    )
    page = json.loads(data)
    return tools, snapshot, page["result"]["results"], estat_dataset.dataset_key, old_dataset.dataset_key


def test_search_catalog_citation_can_restore_ckan_entry_and_keep_cell_locator(tmp_path):
    tools, snapshot, records, estat_key, old_key = _tools(tmp_path)
    result = tools.search_catalog()
    datasets = {item["dataset_key"]: item for item in result["data"]["datasets"]}

    first = next(item for item in datasets.values() if item["citation"] is not None and item["citation"]["locator"] == "entry=0")
    citation = first["citation"]
    assert citation["snapshot_sha256"] == snapshot.sha256
    assert citation["resource_url"] == snapshot.resource_url
    assert citation["fetched_at"] == snapshot.fetched_at.isoformat()
    assert citation["published_at"] == first["provenance"]["modified_at"][:10]
    assert citation["time_basis"] == "published"
    assert citation["license"] == "CC BY 4.0"
    assert citation["attribution"] == "出典：e-Gov"
    assert citation["usage_scope"] == "redistributable"

    restored = tools.cite(citation["snapshot_sha256"], citation["locator"])
    passage = restored["data"]["passages"][0]
    assert restored["data"]["status"] == "ok" and passage["kind"] == "catalog_entry"
    record_text = passage["text"]["content"]
    assert passage["text"]["content_role"] == "untrusted_source_text"
    assert json.loads(record_text) == records[0]
    assert r"\u" in record_text  # cite returns the original JSON spelling from the recorded page

    estat = datasets[estat_key]["citation"]
    assert estat["locator"] == "cell=A1" and estat["time_basis"] == "no_data_date"
    assert estat["published_at"] is None
    assert datasets[old_key]["citation"] is None
    assert "该记录缺少快照引用，重新解析后可还原" in result["warnings"]

    out_of_range = tools.cite(snapshot.sha256, "entry=999")
    assert out_of_range["data"]["status"] == "not_found"
    assert "超出 results 数组范围" in out_of_range["gap_detail"]


def test_entry_locator_rejects_non_package_search_snapshot(tmp_path):
    tools, _, _, _, _ = _tools(tmp_path)
    data = (FIXTURES / "package_show_cao_syukujitsu.json").read_bytes()
    snapshot = make_snapshot(
        SOURCE_ID, "https://data.e-gov.go.jp/data/api/action/package_show?id=sample", data,
        fetched_at=T0, content_type="application/json",
    )
    tools.raw.put(data)
    tools.manifest.record_snapshot(snapshot)

    result = tools.cite(snapshot.sha256, "entry=0")
    assert result["data"]["status"] == "not_found"
    assert "只适用于 CKAN package_search 快照" in result["gap_detail"]
