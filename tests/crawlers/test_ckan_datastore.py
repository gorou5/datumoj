from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

import yaml

from datumoj.rikolto.core.contracts import CrawlContext, FetchResult
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.crawlers.ckan import CkanCrawler
from datumoj.rikolto.testing.fakes import FakeManifest, FakeRawStore, drive_crawler
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "bodik"
SEARCH_PATH = "/api/3/action/package_search"
DATASTORE_PATH = "/api/3/action/datastore_search"
DATASTORE_RESOURCE = "a6e50db4-8984-43b2-b477-99de98f8efab"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _json(data: bytes) -> dict:
    return json.loads(data)


def _expect(httpserver, path: str, query: dict[str, str], body: bytes) -> None:
    httpserver.expect_request(path, query_string=query).respond_with_data(body, content_type="application/json")


class _LocalServerFetcher:
    """HTTPFetcher adapter for pytest-httpserver; all traffic stays on the local test server."""

    def __init__(self) -> None:
        self.requests: list[str] = []

    def fetch(self, url: str, etag: str | None = None, last_modified: str | None = None) -> FetchResult:
        self.requests.append(url)
        with urlopen(url, timeout=5) as response:
            return FetchResult(
                url=url, outcome=FetchOutcome.ok, fetched_at=T0, http_status=response.status,
                content=response.read(), content_type=response.headers.get("Content-Type"),
                etag=response.headers.get("ETag"), last_modified=response.headers.get("Last-Modified"),
            )


def _spec(base_url: str, *, rows: int = 3, **scope):
    return make_spec(
        source_id="jp-bodik-test", kind="ckan", base_url=base_url, allowed_hosts=["127.0.0.1", "localhost"],
        max_total_bytes=10_000_000, max_files=200,
        scope={"q": "公共施設", "rows": rows, "incremental": False, "formats": [], **scope},
    )


def _search_query(start: int, rows: int = 3) -> dict[str, str]:
    return {"q": "公共施設", "rows": str(rows), "start": str(start), "sort": "id asc"}


def _datastore_query(offset: int, limit: int = 5) -> dict[str, str]:
    return {"resource_id": DATASTORE_RESOURCE, "limit": str(limit), "sort": "_id asc", "offset": str(offset)}


def _selected_dataset_search() -> tuple[bytes, str, str]:
    data = _json(_fixture("package_search-p1.json"))
    package = data["result"]["results"][0]
    resource = package["resources"][0]
    package["resources"] = [resource]
    data["result"]["results"] = [package]
    return json.dumps(data).encode(), package["id"], resource["url"]


def _configure_dataset(httpserver) -> tuple[str, str]:
    body, dataset_id, resource_url = _selected_dataset_search()
    _expect(httpserver, SEARCH_PATH, _search_query(0), body)
    return dataset_id, resource_url


def test_bodik_source_configuration_is_valid_and_keeps_unverified_license():
    path = ROOT / "sources" / "jp-bodik.yaml"
    source = yaml.safe_load(path.read_text(encoding="utf-8"))
    spec = make_spec(**source)
    assert spec.kind == "ckan"
    assert spec.base_url == "https://data.bodik.jp/api/3/action/"
    assert spec.rate_per_sec == 0.5 and spec.schedule == "daily"
    assert spec.license.redistribution == "unverified" and spec.license.usage_scope == "internal_only"
    assert spec.max_total_bytes == 10 * 1024**3 and spec.scope["max_file_bytes"] == 200 * 1024**2
    assert spec.scope["datastore"] is True


def test_catalog_pagination_uses_fixture_pages_and_datastore_is_disabled_by_default(httpserver):
    base_url = httpserver.url_for("/api/3/action")
    _expect(httpserver, SEARCH_PATH, _search_query(0), _fixture("package_search-p1.json"))
    _expect(httpserver, SEARCH_PATH, _search_query(3), _fixture("package_search-p2.json"))
    empty = json.dumps({"success": True, "result": {"count": 1621, "results": []}}).encode()
    _expect(httpserver, SEARCH_PATH, _search_query(6), empty)
    fetcher = _LocalServerFetcher()

    steps = drive_crawler(CkanCrawler(), _spec(base_url), fetcher, now=T0)

    assert [parse_qs(urlsplit(url).query)["start"][0] for url in fetcher.requests] == ["0", "3", "6"]
    assert all(step.task.meta["role"] == "package_search" for step in steps)
    assert all("datastore_search" not in url for url in fetcher.requests)


def test_datastore_concatenates_67_records_and_stores_dataset_license_meta(httpserver):
    base_url = httpserver.url_for("/api/3/action")
    dataset_id, resource_url = _configure_dataset(httpserver)
    _expect(httpserver, DATASTORE_PATH, _datastore_query(0), _fixture("datastore_search-p1.json"))
    _expect(httpserver, DATASTORE_PATH, _datastore_query(5), _fixture("datastore_search-p2.json"))
    _expect(httpserver, DATASTORE_PATH, _datastore_query(10), _fixture("datastore_search-p3.json"))

    sample = _json(_fixture("datastore_search-p1.json"))["result"]
    for offset in range(15, 65, 5):
        page = dict(sample, offset=offset, total=67)
        page["records"] = [dict(record, _id=offset + index + 1) for index, record in enumerate(sample["records"])]
        _expect(httpserver, DATASTORE_PATH, _datastore_query(offset), json.dumps({"success": True, "result": page}).encode())
    _expect(httpserver, DATASTORE_PATH, _datastore_query(65), _fixture("datastore_search-last.json"))

    fetcher, manifest, raw = _LocalServerFetcher(), FakeManifest(), FakeRawStore()
    steps = drive_crawler(CkanCrawler(), _spec(base_url, datastore=True, datastore_limit=5), fetcher,
                          manifest=manifest, raw=raw, limit=1, now=T0)

    pages = [step for step in steps if step.task.meta["role"] == "datastore"]
    records = [record for page in pages for record in _json(page.result.content)["result"]["records"]]
    assert len(records) == 67
    assert [record["_id"] for record in records] == list(range(1, 68))
    assert [parse_qs(urlsplit(url).query)["offset"][0] for url in fetcher.requests if DATASTORE_PATH in url] == [
        str(offset) for offset in range(0, 67, 5)
    ]
    assert all(page.task.meta["dataset_id"] == dataset_id for page in pages)
    assert all(page.task.meta["resource_url"] == resource_url for page in pages)
    assert all(manifest.snapshot_meta(page.snapshot.snapshot_id)["license"] == "cc-by-40-intl" for page in pages)
    assert all(raw.get(page.snapshot.sha256) == page.result.content for page in pages)


def test_empty_datastore_page_stops_without_following_links_next(httpserver):
    base_url = httpserver.url_for("/api/3/action")
    _configure_dataset(httpserver)
    empty_page = _json(_fixture("datastore_search-end.json"))["result"]
    empty_page["offset"] = 0
    _expect(httpserver, DATASTORE_PATH, _datastore_query(0), json.dumps({"success": True, "result": empty_page}).encode())
    fetcher = _LocalServerFetcher()

    steps = drive_crawler(CkanCrawler(), _spec(base_url, datastore=True, datastore_limit=5), fetcher,
                          limit=1, now=T0)

    datastore_requests = [url for url in fetcher.requests if DATASTORE_PATH in url]
    assert len(datastore_requests) == 1
    assert _json(steps[-1].result.content)["result"]["records"] == []


def test_short_page_continues_when_total_says_more_records_remain(httpserver):
    base_url = httpserver.url_for("/api/3/action")
    _configure_dataset(httpserver)
    sample = _json(_fixture("datastore_search-p1.json"))["result"]
    first = dict(sample, offset=0, total=4, records=sample["records"][:2])
    second = dict(sample, offset=2, total=4, records=[dict(record, _id=index + 6) for index, record in enumerate(sample["records"][:2])])
    _expect(httpserver, DATASTORE_PATH, _datastore_query(0, 3), json.dumps({"success": True, "result": first}).encode())
    _expect(httpserver, DATASTORE_PATH, _datastore_query(2, 3), json.dumps({"success": True, "result": second}).encode())
    fetcher = _LocalServerFetcher()

    steps = drive_crawler(CkanCrawler(), _spec(base_url, datastore=True, datastore_limit=3), fetcher,
                          limit=1, now=T0)

    pages = [step for step in steps if step.task.meta["role"] == "datastore"]
    assert [page.task.meta["offset"] for page in pages] == [0, 2]
    assert sum(len(_json(page.result.content)["result"]["records"]) for page in pages) == 4


def test_datastore_max_rows_stops_and_logs_warning(httpserver, caplog):
    base_url = httpserver.url_for("/api/3/action")
    _configure_dataset(httpserver)
    first = _json(_fixture("datastore_search-p1.json"))["result"]
    capped = dict(first, offset=5, limit=2, total=67,
                  records=[dict(record, _id=index + 6) for index, record in enumerate(first["records"][:2])])
    _expect(httpserver, DATASTORE_PATH, _datastore_query(0, 5), _fixture("datastore_search-p1.json"))
    _expect(httpserver, DATASTORE_PATH, _datastore_query(5, 2), json.dumps({"success": True, "result": capped}).encode())
    fetcher = _LocalServerFetcher()

    with caplog.at_level(logging.WARNING, logger="datumoj.rikolto.crawlers.ckan"):
        steps = drive_crawler(CkanCrawler(), _spec(base_url, datastore=True, datastore_limit=5, datastore_max_rows=7),
                              fetcher, limit=1, now=T0)

    offsets = [parse_qs(urlsplit(url).query)["offset"][0] for url in fetcher.requests if DATASTORE_PATH in url]
    assert offsets == ["0", "5"]
    assert "scope.datastore_max_rows=7" in caplog.text
    assert sum(len(_json(step.result.content)["result"]["records"]) for step in steps
               if step.task.meta["role"] == "datastore") == 7


def test_not_modified_datastore_page_reuses_raw_to_continue_pagination():
    body, _, _ = _selected_dataset_search()
    crawler = CkanCrawler()
    spec = _spec("https://data.bodik.jp/api/3/action", datastore=True, datastore_limit=5)
    manifest, raw = FakeManifest(), FakeRawStore()
    ctx = CrawlContext(spec=spec, manifest=manifest, raw=raw, now=T0, limit=1)
    search = next(iter(crawler.plan(ctx)))
    search_result = FetchResult(url=search.url, outcome=FetchOutcome.ok, fetched_at=T0, http_status=200, content=body)
    datastore_task = next(task for task in crawler.after_fetch(search, search_result, ctx)
                          if task.meta.get("role") == "datastore")
    page = _json(_fixture("datastore_search-p1.json"))
    page["result"]["total"] = 67
    page_body = json.dumps(page).encode()
    sha = raw.put(page_body)
    old_snapshot = make_snapshot(source_id=spec.id, url=datastore_task.url, data=page_body, snapshot_id="old-datastore-page")
    manifest.record_snapshot(old_snapshot, datastore_task.meta)

    unchanged = FetchResult(url=datastore_task.url, outcome=FetchOutcome.not_modified, fetched_at=T0, http_status=304)
    next_tasks = crawler.after_fetch(datastore_task, unchanged, ctx)

    next_task, = next_tasks
    assert next_task.meta["offset"] == 5
    assert raw.get(sha) == page_body


def test_datastore_page_defaults_are_one_thousand_rows_and_one_hundred_thousand_max():
    body, _, _ = _selected_dataset_search()
    crawler = CkanCrawler()
    spec = _spec("https://data.bodik.jp/api/3/action", datastore=True)
    ctx = CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)
    search = next(iter(crawler.plan(ctx)))
    response = FetchResult(url=search.url, outcome=FetchOutcome.ok, fetched_at=T0, http_status=200, content=body)

    task, = (item for item in crawler.after_fetch(search, response, ctx) if item.meta.get("role") == "datastore")

    assert task.meta["limit"] == 1000 and task.meta["max_rows"] == 100_000
    assert parse_qs(urlsplit(task.url).query)["sort"] == ["_id asc"]
