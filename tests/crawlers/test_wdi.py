from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from datumoj.rikolto.core.contracts import CrawlContext
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.crawlers.wdi import WDICrawler
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse
from datumoj.rikolto.testing.samples import T0

SPEC = load_registry(Path(__file__).parents[2] / "sources").get("intl-wdi")


def context(spec=SPEC):
    return CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)


class TestCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return WDICrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        routes = {task.url: FakeResponse(200, b'[]') for task in WDICrawler().plan(context())}
        return lambda: FakeHttpFetcher(routes, now=lambda: T0)


def test_requests_and_scope():
    tasks = list(WDICrawler().plan(context()))
    assert len(tasks) == 8
    for task in tasks:
        params = parse_qs(urlsplit(task.url).query)
        assert params['source'] == ['2'] and params['format'] == ['json']
        if task.meta['request_type'] == 'data':
            assert params['per_page'] == ['1000'] and params['date'] == ['2000:2024']
    for changes in ({'countries': ['WLD']}, {'start_year': 1000}):
        scope = {**SPEC.scope, **changes}
        with pytest.raises(ValueError):
            list(WDICrawler().plan(context(SPEC.model_copy(update={'scope': scope}))))
