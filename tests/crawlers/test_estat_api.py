import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from datumoj.rikolto.core.contracts import CrawlContext
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.crawlers.estat_api import EstatAPICrawler
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse
from datumoj.rikolto.testing.samples import T0

SPEC = load_registry(Path(__file__).parents[2] / 'sources').get('jp-estat-api')
FIX = Path(__file__).parents[1] / 'fixtures/estat_api'


def context(spec=SPEC):
    return CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)


class TestCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self, monkeypatch):
        monkeypatch.setenv('ESTAT_APP_ID', 'fake-key')
        return EstatAPICrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self, monkeypatch):
        monkeypatch.setenv('ESTAT_APP_ID', 'fake-key')
        routes = {t.url: FakeResponse(200, b'{"GET_STATS_LIST":{"RESULT":{"STATUS":0},"DATALIST_INF":{"RESULT_INF":{}}}}') for t in EstatAPICrawler().plan(context())}
        return lambda: FakeHttpFetcher(routes, now=lambda: T0)


def test_plan_and_pagination(monkeypatch):
    monkeypatch.delenv('ESTAT_APP_ID', raising=False)
    with pytest.raises(ValueError, match='ESTAT_APP_ID'):
        EstatAPICrawler().plan(context())
    monkeypatch.setenv('ESTAT_APP_ID', 'fake-key')
    crawler = EstatAPICrawler()
    tasks = crawler.plan(context())
    params = parse_qs(urlsplit(tasks[0].url).query)
    assert params['limit'] == ['100000'] and params['cdCat03'] == ['01000']
    assert 'fake-key' not in json.dumps(tasks[0].meta)
    data = (FIX / 'getStatsList_00200524_limit5.json').read_bytes()
    fetcher = FakeHttpFetcher({tasks[3].url: FakeResponse(200, data)}, now=lambda: T0)
    next_task, = crawler.after_fetch(tasks[3], fetcher.fetch(tasks[3].url), context())
    assert parse_qs(urlsplit(next_task.url).query)['startPosition'] == ['6']
    for field, value in [('stats_data_id', '12'), ('filters', {'cdTimeFrom': '1'})]:
        table = {**SPEC.scope['tables'][0], field: value}
        scope = {**SPEC.scope, 'tables': [table]}
        with pytest.raises(ValueError):
            crawler.plan(context(SPEC.model_copy(update={'scope': scope})))
