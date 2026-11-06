import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from datumoj.rikolto.core.http import RobotsRules
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.crawlers.data_europa import DataEuropaCrawler
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, drive_crawler
from datumoj.rikolto.testing.samples import T0

ROOT = Path(__file__).parents[2]
FIX = ROOT / "tests/fixtures/data_europa"
SPEC = load_registry(ROOT / "sources").get("eu-data-europa").model_copy(update={"scope": {"catalogue": "publ", "limit": 5}})
BASE = "https://data.europa.eu/api/hub/repo"
PAGE = f"{BASE}/catalogues/publ/datasets?valueType=identifiers&limit=5&offset="
DETAIL = (FIX / "repo_dataset_combined-nomenclature-2023.jsonld").read_bytes()
IDS = json.loads((FIX / "repo_catalogue-publ_identifiers_limit5_offset0.json").read_bytes())


def routes():
    return {PAGE + "0": json.dumps(IDS).encode(), PAGE + "5": json.dumps([IDS[0]]).encode(),
            **{f"{BASE}/datasets/{i}": DETAIL for i in IDS}}


class TestCrawler(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return DataEuropaCrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher(routes(), now=lambda: T0)


def test_pagination_and_robots():
    fetcher = FakeHttpFetcher(routes(), now=lambda: T0)
    steps = drive_crawler(DataEuropaCrawler(), SPEC, fetcher, now=T0)
    assert len(steps) == 7
    assert fetcher.requests.count(PAGE + "5") == 1
    rules = RobotsRules.parse("User-agent: *\nDisallow: /*search?*\nDisallow: /*&sort=*\n", "DATUMOJ")
    for url in fetcher.requests:
        parts = urlsplit(url)
        assert parts.hostname == "data.europa.eu"
        assert rules.blocked_reason(parts.path + ("?" + parts.query if parts.query else "")) is None
        assert "search" not in url and "sort=" not in url
