from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import ContentState, ParseResult
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import Dataset, FetchOutcome, Resource, Snapshot, parse_context_fingerprint
from datumoj.rikolto.core.rawstore import FileRawStore
from datumoj.rikolto.pipeline import PipelineDeps, parse_pending, reindex
from datumoj.rikolto.store.catalog import SqliteCatalogStore
from datumoj.rikolto.testing.fakes import (
    FakeChunkStore,
    FakeKeywordIndex,
    FakeObservationStore,
    FakeRegistry,
)
from datumoj.rikolto.testing.samples import T0, make_spec

SOURCE_ID = "jp-egov-data"
FIXTURES = Path(__file__).parent / "fixtures" / "ckan_egov"


class CkanFixtureParser:
    name = "ckan-fixture"
    version = "1"
    source_kind = "ckan"
    content_type = "*"

    def parse(self, snapshot, data, meta=None):
        if meta and meta.get("role") == "resource":
            return ParseResult()

        payload = json.loads(data)
        page = payload.get("result", {})
        packages = page.get("results") if isinstance(page, dict) else None
        if not isinstance(packages, list):
            packages = [page] if isinstance(page, dict) else []

        datasets, resources = [], []
        for package in packages:
            dataset_id = package.get("id")
            if not isinstance(dataset_id, str):
                continue
            dataset_key = f"{snapshot.source_id}:{dataset_id}"
            datasets.append(Dataset(
                dataset_key=dataset_key,
                source_id=snapshot.source_id,
                title=package.get("title") or dataset_id,
                kind=package.get("type") or "dataset",
            ))
            for item in package.get("resources", []):
                if not isinstance(item, dict):
                    continue
                url = item.get("url") or item.get("download_url") or item.get("access_url")
                resource_id = item.get("id") or item.get("name")
                if not isinstance(url, str) or not isinstance(resource_id, str):
                    continue
                resources.append(Resource(
                    resource_id=resource_id,
                    dataset_key=dataset_key,
                    source_id=snapshot.source_id,
                    url=url,
                    name=item.get("name"),
                    format=item.get("format"),
                    media_type=item.get("mimetype"),
                ))
        return ParseResult(datasets=datasets, resources=resources)


@pytest.fixture
def deps(tmp_path):
    manifest = SqliteManifest(tmp_path / "manifest.sqlite")
    catalog = SqliteCatalogStore(tmp_path / "catalog.sqlite")
    value = PipelineDeps(
        manifest=manifest,
        raw=FileRawStore(tmp_path / "raw"),
        registry=FakeRegistry([make_spec(SOURCE_ID, kind="ckan")]),
        chunks=FakeChunkStore(),
        keyword=FakeKeywordIndex(),
        observations=FakeObservationStore(),
        catalog=catalog,
        parsers={("ckan", "*"): CkanFixtureParser},
    )
    try:
        yield value
    finally:
        manifest.close()
        catalog.close()


def _resource_package() -> dict:
    payload = json.loads((FIXTURES / "package_show_npa_keibigyo.json").read_text(encoding="utf-8"))
    return payload["result"]


def _page_data() -> bytes:
    payload = json.loads((FIXTURES / "package_search_q-jinko_rows5_start0.json").read_text(encoding="utf-8"))
    payload["result"]["results"].append(_resource_package())
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _record_ok(deps, snapshot_id: str, url: str, data: bytes, fetched_at, meta=None) -> Snapshot:
    sha256 = deps.raw.put(data)
    snapshot = Snapshot(
        snapshot_id=snapshot_id,
        source_id=SOURCE_ID,
        resource_url=url,
        fetched_at=fetched_at,
        outcome=FetchOutcome.ok,
        sha256=sha256,
        size=len(data),
        http_status=200,
        content_type="application/json" if "page" in snapshot_id else "text/csv",
    )
    deps.manifest.record_snapshot(snapshot, meta)
    return snapshot


def _record_page(deps, fetched_at=T0) -> Snapshot:
    return _record_ok(
        deps,
        "page-snapshot",
        "https://data.e-gov.go.jp/data/api/action/package_search?q=%E4%BA%BA%E5%8F%A3",
        _page_data(),
        fetched_at,
        {"role": "package_search"},
    )


def _record_resource(deps, fetched_at=T0, *, success=True) -> Snapshot:
    package = _resource_package()
    resource = package["resources"][0]
    url = resource["url"] or resource["download_url"]
    if not success:
        snapshot = Snapshot(
            snapshot_id="resource-failed",
            source_id=SOURCE_ID,
            resource_url=url,
            fetched_at=fetched_at,
            outcome=FetchOutcome.network_error,
            error="offline fixture failure",
        )
        deps.manifest.record_snapshot(snapshot, {"role": "resource", "dataset_id": package["id"]})
        return snapshot
    data = (FIXTURES / "resources" / "npa_keibigyo_reiwa7data.csv").read_bytes()
    return _record_ok(
        deps,
        "resource-snapshot",
        url,
        data,
        fetched_at,
        {"role": "resource", "dataset_id": package["id"]},
    )


def _resource_entry(deps):
    package = _resource_package()
    return deps.catalog.resources_for(f"{SOURCE_ID}:{package['id']}")[0]


@pytest.mark.parametrize("resource_first", [True, False])
def test_successful_resource_and_directory_page_order_promote_content_state(deps, resource_first):
    if resource_first:
        _record_resource(deps, T0)
        _record_page(deps, T0 + timedelta(seconds=1))
    else:
        _record_page(deps, T0)
        _record_resource(deps, T0 + timedelta(seconds=1))

    first = parse_pending(deps, SOURCE_ID)
    assert len(first.parsed) == 2
    assert _resource_entry(deps).state is ContentState.content_fetched

    second = parse_pending(deps, SOURCE_ID)
    assert second.parsed == [] and second.skipped == 2
    assert _resource_entry(deps).state is ContentState.content_fetched


def test_failed_resource_stays_catalog_only(deps):
    _record_page(deps)
    _record_resource(deps, T0 + timedelta(seconds=1), success=False)

    summary = parse_pending(deps, SOURCE_ID)

    assert summary.parsed == ["page-snapshot"]
    assert _resource_entry(deps).state is ContentState.catalog_only


def test_successful_resource_does_not_downgrade_observations_loaded(deps):
    _record_page(deps)
    parse_pending(deps, SOURCE_ID)
    entry = _resource_entry(deps)
    deps.catalog.set_content_state(entry.resource.url, ContentState.observations_loaded)
    _record_resource(deps, T0 + timedelta(seconds=1))

    parse_pending(deps, SOURCE_ID)

    assert _resource_entry(deps).state is ContentState.observations_loaded


def test_reindex_reconciles_successful_resource_without_reparsing(deps):
    _record_page(deps)
    parse_pending(deps, SOURCE_ID)
    resource_snapshot = _record_resource(deps, T0 + timedelta(seconds=1))
    assert _resource_entry(deps).state is ContentState.catalog_only

    assert reindex(deps, SOURCE_ID) == 0

    assert _resource_entry(deps).state is ContentState.content_fetched
    assert deps.manifest.get_parse(
        resource_snapshot.sha256, CkanFixtureParser.name, CkanFixtureParser.version,
        parse_context_fingerprint(resource_snapshot, deps.manifest.snapshot_meta(resource_snapshot.snapshot_id)),
    ) is None
