"""Offline pagination, incremental coverage and host boundaries."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import yaml
from werkzeug.wrappers import Response

from datumoj.rikolto.core.contracts import CrawlContext, FetchResult, SourceSpec, discover_crawlers
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.crawlers.fedreg import FedregCrawler
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse, drive_crawler
from datumoj.rikolto.testing.samples import T0

ROOT = Path(__file__).parents[2]
SPEC = SourceSpec.model_validate(yaml.safe_load((ROOT / "sources/us-fedreg.yaml").read_text()))
API = SPEC.base_url
FIXTURES = ROOT / "tests/fixtures/fedreg"
DETAIL = json.loads((FIXTURES / "document_2026-20122.json").read_bytes())
XML = (FIXTURES / "full_text_2026-20122.xml").read_bytes()


def _ctx(manifest=None, raw=None, now=T0, spec=SPEC):
    return CrawlContext(spec, manifest or FakeManifest(), raw or FakeRawStore(), now)


def _query(task):
    return parse_qs(urlsplit(task.url).query)


def _empty():
    return {"count": 0, "total_pages": 0, "results": []}


class TestFedregCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return FedregCrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        root = next(iter(FedregCrawler().plan(_ctx())))
        later = root.model_copy(update={"url": root.url.replace("gte%5D=2026-10-01", "gte%5D=2026-10-13")})
        response = FakeResponse(200, json.dumps(_empty()).encode())
        return lambda: FakeHttpFetcher({root.url: response, later.url: response})


@pytest.fixture
def local_fetcher(httpserver):
    """Map all planned URLs to a local server; an unregistered path fails offline."""
    seen = []

    class LocalFetcher:
        def fetch(self, url, **_kwargs):
            parts = urlsplit(url)
            assert parts.hostname == "www.federalregister.gov"
            seen.append(url)
            with httpx.Client(trust_env=False) as client:
                response = client.get(httpserver.url_for(parts.path), params=parse_qs(parts.query))
            return FetchResult(
                url=url, fetched_at=T0, outcome=FetchOutcome.ok,
                content=response.content, http_status=response.status_code,
            )

    return LocalFetcher(), seen


@pytest.mark.parametrize(("count", "pages"), [(10001, 6), (10000, 5), (9800, 50)])
def test_bisects_windows_before_pagination(httpserver, local_fetcher, count, pages):
    fetcher, seen = local_fetcher
    windows = []

    def search(request):
        start = request.args["conditions[publication_date][gte]"]
        end = request.args["conditions[publication_date][lte]"]
        page = int(request.args["page"])
        assert page <= 50
        assert request.args.getlist("conditions[type][]") == ["RULE", "PRORULE", "NOTICE"]
        assert "PRESDOCU" not in request.query_string.decode()
        windows.append((start, end, page))
        payload = {"count": count, "total_pages": pages, "results": []} if start != end else _empty()
        return Response(json.dumps(payload), content_type="application/json")

    httpserver.expect_request("/api/v1/documents.json").respond_with_handler(search)
    drive_crawler(FedregCrawler(), SPEC, fetcher, now=T0)
    assert windows[0] == ("2026-10-01", "2026-10-20", 1)
    leaves = [(start, end) for start, end, _ in windows if start == end]
    assert sorted(leaves) == [(f"2026-10-{day:02}", f"2026-10-{day:02}") for day in range(1, 21)]
    assert len(seen) == 39
    httpserver.check_assertions()


def test_single_day_overflow_fails_without_requesting_page_51(httpserver, local_fetcher):
    fetcher, seen = local_fetcher
    httpserver.expect_request("/api/v1/documents.json").respond_with_json({
        "count": 10000, "total_pages": 50, "results": [],
    })
    with pytest.raises(ValueError, match="single day 2026-10-01"):
        drive_crawler(FedregCrawler(), SPEC, fetcher, now=T0.replace(day=1))
    assert len(seen) == 1 and parse_qs(urlsplit(seen[0]).query)["page"] == ["1"]


def _routes(ctx, payload, detail=DETAIL):
    task = next(iter(FedregCrawler().plan(ctx)))
    return {
        task.url: FakeResponse(200, json.dumps(payload).encode()),
        API + "documents/2026-20122.json": FakeResponse(200, json.dumps(detail).encode()),
        DETAIL["full_text_xml_url"]: FakeResponse(200, XML),
    }


def _one():
    return {"count": 1, "total_pages": 1, "results": [DETAIL]}


def test_incremental_uses_complete_run_coverage_and_ignores_partial_later_run():
    manifest, raw = FakeManifest(), FakeRawStore()
    ctx = _ctx(manifest, raw)
    drive_crawler(FedregCrawler(), SPEC, FakeHttpFetcher(_routes(ctx, _one())), manifest=manifest, raw=raw, now=T0)
    tomorrow = T0 + timedelta(days=1)
    task = next(iter(FedregCrawler().plan(_ctx(manifest, raw, tomorrow))))
    assert _query(task)["conditions[publication_date][gte]"] == ["2026-10-13"]
    # A successful search with an unfetched detail must not advance coverage.
    limited = SPEC.model_copy(update={"max_files": 1})
    drive_crawler(FedregCrawler(), limited, FakeHttpFetcher(_routes(_ctx(manifest, raw, tomorrow), _one())),
                  manifest=manifest, raw=raw, now=tomorrow)
    later = next(iter(FedregCrawler().plan(_ctx(manifest, raw, T0 + timedelta(days=4)))))
    assert _query(later)["conditions[publication_date][gte]"] == ["2026-10-13"]


def test_first_run_and_lookback_never_cross_start_date():
    manifest, raw = FakeManifest(), FakeRawStore()
    now = T0.replace(day=3)
    ctx = _ctx(manifest, raw, now)
    drive_crawler(FedregCrawler(), SPEC, FakeHttpFetcher(_routes(ctx, _empty())), manifest=manifest, raw=raw, now=now)
    task = next(iter(FedregCrawler().plan(_ctx(manifest, raw, T0))))
    assert _query(task)["conditions[publication_date][gte]"] == ["2026-10-01"]


def test_off_host_full_text_is_skipped_with_reason(caplog):
    detail = {**DETAIL, "full_text_xml_url": "https://www.govinfo.gov/forbidden.xml"}
    steps = drive_crawler(FedregCrawler(), SPEC, FakeHttpFetcher(_routes(_ctx(), _one(), detail)), now=T0)
    assert len(steps) == 2
    assert "outside allowed_hosts" in caplog.text
    assert all(urlsplit(step.task.url).hostname == "www.federalregister.gov" for step in steps)


def test_pagination_stops_at_declared_last_page_and_ignores_next_page_url(httpserver, local_fetcher):
    fetcher, seen = local_fetcher

    def search(request):
        page = int(request.args["page"])
        assert 1 <= page <= 49
        return Response(json.dumps({
            "count": 49, "total_pages": 49, "results": [DETAIL],
            "next_page_url": "https://evil.invalid/?page=51",
        }), content_type="application/json")

    httpserver.expect_request("/api/v1/documents.json").respond_with_handler(search)
    httpserver.expect_request("/api/v1/documents/2026-20122.json").respond_with_json(DETAIL)
    httpserver.expect_request(urlsplit(DETAIL["full_text_xml_url"]).path).respond_with_data(XML)
    steps = drive_crawler(FedregCrawler(), SPEC, fetcher, now=T0)
    assert [int(parse_qs(urlsplit(url).query)["page"][0]) for url in seen if "documents.json?" in url] == list(range(1, 50))
    assert len(steps) == 51
    httpserver.check_assertions()


def test_plugin_discovery():
    assert discover_crawlers()["fedreg"] is FedregCrawler


@pytest.mark.parametrize("failed_role", ["detail", "xml"])
def test_failed_document_fetch_does_not_advance_coverage(failed_role):
    manifest, raw = FakeManifest(), FakeRawStore()
    routes = _routes(_ctx(), _one())
    failed_url = API + "documents/2026-20122.json" if failed_role == "detail" else DETAIL["full_text_xml_url"]
    routes[failed_url] = FakeResponse(503)
    steps = drive_crawler(FedregCrawler(), SPEC, FakeHttpFetcher(routes), manifest=manifest, raw=raw, now=T0)
    assert steps[-1].result.outcome is FetchOutcome.http_error
    task = next(iter(FedregCrawler().plan(_ctx(manifest, raw, T0 + timedelta(days=3)))))
    assert _query(task)["conditions[publication_date][gte]"] == ["2026-10-01"]


def test_304_discovery_still_schedules_details_and_full_text():
    manifest, raw = FakeManifest(), FakeRawStore()
    routes = _routes(_ctx(), _one())
    routes = {url: FakeResponse(200, response.body, {"ETag": '"recorded"'}) for url, response in routes.items()}
    drive_crawler(FedregCrawler(), SPEC, FakeHttpFetcher(routes), manifest=manifest, raw=raw, now=T0)
    later = T0 + timedelta(hours=1)
    later_ctx = _ctx(manifest, raw, later)
    root = next(iter(FedregCrawler().plan(later_ctx)))
    cached_root = FetchResult(url=root.url, content=json.dumps(_one()).encode(), outcome=FetchOutcome.ok, fetched_at=T0)
    raw.put(cached_root.content)
    manifest.record_snapshot(cached_root.to_snapshot("cached-incremental-search", SPEC.id))
    routes[root.url] = FakeResponse(304)
    steps = drive_crawler(FedregCrawler(), SPEC, FakeHttpFetcher(routes), manifest=manifest, raw=raw, now=later)
    assert [step.task.meta["role"] for step in steps] == ["search", "detail", "xml"]
    assert all(step.result.outcome is FetchOutcome.not_modified for step in steps)
    next_root = next(iter(FedregCrawler().plan(_ctx(manifest, raw, T0 + timedelta(days=2)))))
    assert _query(next_root)["conditions[publication_date][gte]"] == ["2026-10-13"]
