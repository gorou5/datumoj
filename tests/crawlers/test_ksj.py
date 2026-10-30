from pathlib import Path

import pytest
import yaml

from datumoj.rikolto.core.contracts import CrawlContext, SourceSpec, discover_crawlers, discover_parsers
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.crawlers.ksj import KsjCrawler
from datumoj.rikolto.parsers.ksj import KsjParser
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse
from datumoj.rikolto.testing.samples import T0

ROOT = Path(__file__).parents[2]
SPEC = SourceSpec.model_validate(yaml.safe_load((ROOT / "sources/jp-ksj.yaml").read_text()))
URL = SPEC.scope["files"][0]
DATA = (ROOT / "tests/fixtures/ksj/P05-22_41_GML.zip").read_bytes()


class TestKsjCrawler(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return KsjCrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher({URL: FakeResponse(200, DATA)}, now=lambda: T0)


def test_plugins_and_plan_limits():
    assert discover_crawlers()["ksj_file"] is KsjCrawler
    assert discover_parsers()[("ksj_file", "*")] is KsjParser
    assert SPEC.schedule == "weekly" and SPEC.rate_per_sec == 0.2
    ctx = CrawlContext(SPEC, FakeManifest(), FakeRawStore(), T0, limit=0)
    assert list(KsjCrawler().plan(ctx)) == []


@pytest.mark.parametrize("files", [[], "url", [42], ["https://evil.example/file"], ["http://nlftp.mlit.go.jp/file"],
                                  ["https://user@nlftp.mlit.go.jp/file"]])
def test_invalid_file_configuration(files):
    spec = SPEC.model_copy(update={"scope": {"files": files}})
    with pytest.raises(ValueError):
        list(KsjCrawler().plan(CrawlContext(spec, FakeManifest(), FakeRawStore(), T0)))


def test_single_file_cap():
    task = next(iter(KsjCrawler().plan(CrawlContext(SPEC, FakeManifest(), FakeRawStore(), T0))))
    fetcher = FakeHttpFetcher({URL: FakeResponse(200, b"a" * (25 * 1024 * 1024 + 1))}, now=lambda: T0)
    assert KsjCrawler().classify(task, fetcher.fetch(URL)) is FetchOutcome.too_large
