from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from datumoj.rikolto.core.contracts import CrawlContext, discover_crawlers
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.crawlers.eurostat import EurostatCrawler
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse, drive_crawler
from datumoj.rikolto.testing.samples import T0
from tests.parsers.test_eurostat import BASE, GDP, GOV, ROOT, data_bytes, dsd_bytes

SPEC = load_registry(ROOT / "sources").get("eu-eurostat")
assert SPEC is not None


def routes():
    result = {}
    for entry in SPEC.scope["datasets"]:
        code = entry["id"]
        result[f"{BASE}datastructure/ESTAT/{code.upper()}?references=all"] = FakeResponse(
            200, dsd_bytes(code), {"content-type": "application/xml", "etag": code})
        result[f"{BASE}data/{code}/{entry['query']}?startPeriod={entry['startPeriod']}&endPeriod={entry['endPeriod']}"] = FakeResponse(
            200, data_bytes(code), {"content-type": "application/vnd.sdmx.genericdata+xml;version=2.1"})
    return result


class TestEurostatCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return EurostatCrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher(routes(), now=lambda: T0)


def context(spec=SPEC, limit=None):
    return CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0, limit=limit)


def test_requests_use_each_datasets_filters_and_official_host():
    assert discover_crawlers()["eurostat_sdmx"] is EurostatCrawler
    steps = drive_crawler(EurostatCrawler(), SPEC, FakeHttpFetcher(routes()), now=T0)
    assert len(steps) == 6
    for step in steps:
        parts = urlsplit(step.task.url)
        assert parts.hostname == "ec.europa.eu"
        assert "format=JSON" not in step.task.url
        if step.task.meta["request_type"] == "dsd":
            assert parts.path.endswith(f"/datastructure/ESTAT/{step.task.meta['id'].upper()}")
            assert parse_qs(parts.query) == {"references": ["all"]}
        else:
            entry = next(entry for entry in SPEC.scope["datasets"] if entry["id"] == step.task.meta["id"])
            assert parts.path.endswith(f"/data/{entry['id']}/{entry['query']}")
            assert parse_qs(parts.query) == {"startPeriod": [entry["startPeriod"]], "endPeriod": [entry["endPeriod"]]}
            assert step.task.meta["structure"]["id"] == entry["id"]
            assert len(step.task.meta["dsd_sha256"]) == 64


@pytest.mark.parametrize("key", ["A.X/EL", "A.X?x=1", "A.%2f", "A.X#fragment", "A.X EL", "A.日本", "A.X\n", "", None])
def test_invalid_key_fails_before_first_request(key):
    entries = [dict(entry) for entry in SPEC.scope["datasets"]]
    entries[-1]["query"] = key
    spec = SPEC.model_copy(update={"scope": {"datasets": entries}})
    with pytest.raises(ValueError, match="key"):
        next(iter(EurostatCrawler().plan(context(spec))))


def test_limit_and_304_dsd_still_fetch_data():
    assert len(list(EurostatCrawler().plan(context(limit=1)))) == 1
    assert not list(EurostatCrawler().plan(context(limit=0)))
    manifest, raw = FakeManifest(), FakeRawStore()
    fetcher = FakeHttpFetcher(routes(), now=lambda: T0)
    for day in range(2):
        steps = drive_crawler(EurostatCrawler(), SPEC, fetcher, manifest=manifest, raw=raw, now=T0 + timedelta(days=day))
        assert len(steps) == 6
        assert len([s for s in steps if s.task.meta["request_type"] == "data"]) == 3
    assert all(s.result.outcome is FetchOutcome.not_modified for s in steps if s.task.meta["request_type"] == "dsd")


def test_failed_dsd_does_not_schedule_its_data():
    responses = routes()
    responses[f"{BASE}datastructure/ESTAT/{GDP.upper()}?references=all"] = FakeResponse(503)
    steps = drive_crawler(EurostatCrawler(), SPEC, FakeHttpFetcher(responses), now=T0)
    assert len(steps) == 5
    assert not any(s.task.meta["id"] == GDP and s.task.meta["request_type"] == "data" for s in steps)
    assert any(s.task.meta["id"] == GOV and s.task.meta["request_type"] == "data" for s in steps)
