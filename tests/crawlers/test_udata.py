import json
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import CrawlContext, FetchResult, discover_crawlers
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.crawlers.udata import UdataCrawler
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, drive_crawler
from datumoj.rikolto.testing.samples import T0

ROOT = Path(__file__).parents[2]
FIX = ROOT / "tests/fixtures/udata_fr"
SPEC = load_registry(ROOT / "sources").get("fr-data-gouv").model_copy(update={"scope": {
    "organization": "534fff81a3a7292c64a77e5c", "page_size": 2, "download_resources": False,
}})


def context(spec=SPEC):
    return CrawlContext(spec, FakeManifest(), FakeRawStore(), T0)


def pages():
    first = (FIX / "datasets_org-insee_page1_size2.json").read_bytes()
    second = json.loads((FIX / "datasets_org-insee_page2_size2.json").read_bytes())
    second["next_page"] = None
    return first, json.dumps(second).encode()


def routes():
    crawler = UdataCrawler()
    first = next(iter(crawler.plan(context())))
    bodies = pages()
    result = FetchResult(url=first.url, outcome=FetchOutcome.ok, fetched_at=T0, content=bodies[0])
    second = next(iter(crawler.after_fetch(first, result, context())))
    return {first.url: bodies[0], second.url: bodies[1]}


class TestUdataCrawler(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return UdataCrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher(routes(), now=lambda: T0)


def test_pages_and_invalid_organization():
    assert discover_crawlers()["udata"] is UdataCrawler
    steps = drive_crawler(UdataCrawler(), SPEC, FakeHttpFetcher(routes(), now=lambda: T0), now=T0)
    assert [step.task.meta["page"] for step in steps] == [1, 2]
    bad = SPEC.model_copy(update={"scope": {**SPEC.scope, "organization": "insee"}})
    with pytest.raises(ValueError):
        list(UdataCrawler().plan(context(bad)))


def test_page_cap_logs_reason(caplog):
    crawler = UdataCrawler()
    task = next(iter(crawler.plan(context()))).model_copy(update={"meta": {
        "organization": SPEC.scope["organization"], "page_size": 2, "page": 3, "page_cap": 3,
    }})
    result = FetchResult(url=task.url, outcome=FetchOutcome.ok, fetched_at=T0, content=pages()[0])
    assert list(crawler.after_fetch(task, result, context())) == []
    assert "page cap" in caplog.text
