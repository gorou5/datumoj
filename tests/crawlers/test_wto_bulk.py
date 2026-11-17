from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlsplit

import pytest

from datumoj.rikolto.core.contracts import CrawlContext, discover_crawlers
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.crawlers.wto_bulk import WTOBulkCrawler
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse, drive_crawler
from datumoj.rikolto.testing.samples import T0
from tests.parsers.test_wto_bulk import ETAG, LAST_MODIFIED, SPEC, URL, ZIP

HEADERS = {"content-type": "application/x-zip-compressed", "etag": ETAG, "last-modified": LAST_MODIFIED}


def routes():
    return {URL: FakeResponse(200, ZIP.read_bytes(), HEADERS)}


class TestWTOBulkCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return WTOBulkCrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher(routes(), now=lambda: T0)


def context(spec=SPEC, limit=None):
    return CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0, limit=limit)


def test_one_request_to_stats_wto_bulk_path_only():
    assert discover_crawlers()["wto_bulk"] is WTOBulkCrawler
    (step,) = drive_crawler(WTOBulkCrawler(), SPEC, FakeHttpFetcher(routes()), now=T0)
    parts = urlsplit(step.task.url)
    assert (parts.scheme, parts.hostname) == ("https", "stats.wto.org")
    assert parts.path == "/assets/UserGuide/merchandise_values_annual_dataset.zip" and not parts.query
    assert step.task.meta["reporters"] == ["840", "156", "276", "250", "392", "410"]


def test_second_run_is_conditional_and_304_adds_nothing():
    manifest, raw = FakeManifest(), FakeRawStore()
    fetcher = FakeHttpFetcher(routes(), now=lambda: T0)
    (first,) = drive_crawler(WTOBulkCrawler(), SPEC, fetcher, manifest=manifest, raw=raw, now=T0)
    assert first.result.outcome is FetchOutcome.ok
    (second,) = drive_crawler(WTOBulkCrawler(), SPEC, fetcher, manifest=manifest, raw=raw, now=T0 + timedelta(days=7))
    assert second.result.outcome is FetchOutcome.not_modified and second.result.content is None
    assert len([s for s in manifest.snapshots_for(SPEC.id) if s.outcome is FetchOutcome.ok]) == 1


@pytest.mark.parametrize("key,value", [("path", "../x.zip"), ("path", "https://data.wto.org/a.zip"), ("path", None),
                                       ("reporters", []), ("reporters", ["USA"]), ("indicators", "ITS_MTV_AX"),
                                       ("start_year", 2025), ("partner", None)])
def test_invalid_scope_fails_before_request(key, value):
    spec = SPEC.model_copy(update={"scope": {**SPEC.scope, key: value}})
    with pytest.raises(ValueError):
        list(WTOBulkCrawler().plan(context(spec)))


def test_limit_zero_plans_nothing():
    assert not list(WTOBulkCrawler().plan(context(limit=0)))
