from pathlib import Path

import pytest
import yaml

from datumoj.rikolto.core.contracts import CrawlContext, SourceSpec, discover_crawlers, discover_parsers
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.crawlers.abr import AbrCrawler
from datumoj.rikolto.parsers.abr import MUNICIPALITY_CODE, AbrParser
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse
from datumoj.rikolto.testing.samples import T0

ROOT = Path(__file__).parents[2]
SPEC = SourceSpec.model_validate(yaml.safe_load((ROOT / 'sources/jp-abr.yaml').read_text()))
URL = SPEC.scope['url']


class TestAbrCrawler(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return AbrCrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher({URL: FakeResponse(200, b'archive bytes')}, now=lambda: T0)


def test_discovery_scope_limits_and_conservative_license():
    assert discover_crawlers()['abr_town'] is AbrCrawler
    assert discover_parsers()[('abr_town', '*')] is AbrParser
    assert SPEC.schedule == 'weekly' and SPEC.rate_per_sec == 0.2
    assert SPEC.max_total_bytes == 1073741824 and SPEC.max_files == 10000
    assert SPEC.license.usage_scope.value == 'internal_only'
    assert SPEC.license.redistribution.value == 'unverified' and SPEC.license.verified_at is None
    ctx = CrawlContext(SPEC, FakeManifest(), FakeRawStore(), T0)
    task, = AbrCrawler().plan(ctx)
    assert task.url == URL and task.meta == {'municipality_code': MUNICIPALITY_CODE}
    assert list(AbrCrawler().plan(CrawlContext(SPEC, ctx.manifest, ctx.raw, T0, limit=0))) == []


@pytest.mark.parametrize('url', [None, 42, 'https://evil.example/file',
                                 URL.replace('https:', 'http:'), URL.replace('https://', 'https://user@'),
                                 URL.replace('.go.jp/', '.go.jp:444/')])
def test_invalid_urls_are_rejected(url):
    spec = SPEC.model_copy(update={'scope': {**SPEC.scope, 'url': url}})
    with pytest.raises(ValueError):
        list(AbrCrawler().plan(CrawlContext(spec, FakeManifest(), FakeRawStore(), T0)))


def test_scope_and_host_must_match():
    for spec in (SPEC.model_copy(update={'scope': {**SPEC.scope, 'municipality_code': 'wrong'}}),
                 SPEC.model_copy(update={'allowed_hosts': ['evil.example']})):
        with pytest.raises(ValueError):
            list(AbrCrawler().plan(CrawlContext(spec, FakeManifest(), FakeRawStore(), T0)))


def test_single_file_cap():
    task, = AbrCrawler().plan(CrawlContext(SPEC, FakeManifest(), FakeRawStore(), T0))
    fetcher = FakeHttpFetcher({URL: FakeResponse(200, b'a' * (25 * 1024 * 1024 + 1))}, now=lambda: T0)
    assert AbrCrawler().classify(task, fetcher.fetch(URL)) is FetchOutcome.too_large
