"""IMF WEO crawler: requests, headers, scope validation. Offline: recorded samples only."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from datumoj.rikolto.core.contracts import CrawlContext, discover_crawlers
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.crawlers.imf_sdmx import ACCEPT, ImfSdmxCrawler
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse, drive_crawler
from datumoj.rikolto.testing.samples import T0

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/imf_weo"
BASE = "https://api.imf.org/external/sdmx/2.1/"
SPEC = load_registry(ROOT / "sources").get("intl-imf-weo")
assert SPEC is not None
GRC = "GRC.GGXCNL_NGDP.A"
GRC_URLS = {
    "WEO_2025_OCT_VINTAGE": f"{BASE}data/IMF.RES,WEO_2025_OCT_VINTAGE/{GRC}?startPeriod=2000&endPeriod=2015",
    "WEO_2026_APR_VINTAGE": f"{BASE}data/IMF.RES,WEO_2026_APR_VINTAGE/{GRC}?startPeriod=2000&endPeriod=2015",
    "WEO": f"{BASE}data/IMF.RES,WEO/{GRC}?startPeriod=2000&endPeriod=2015",
}
GRC_FILES = {
    "WEO_2025_OCT_VINTAGE": "weo_2025_oct_GRC_GGXCNL_NGDP_2000-2015.csv",
    "WEO_2026_APR_VINTAGE": "weo_2026_apr_GRC_GGXCNL_NGDP_2000-2015.csv",
    "WEO": "weo_GRC_GGXCNL_NGDP_2000-2015.csv",
}
# Only the Greek series has recorded samples for all three flows: the suites crawl just that key.
GRC_SPEC = SPEC.model_copy(update={"scope": {**SPEC.scope, "series": [SPEC.scope["series"][2]]}})


def routes():
    return {GRC_URLS[flow]: FakeResponse(200, (FIXTURES / name).read_bytes(),
                                         {"content-type": "application/vnd.sdmx.data+csv;version=1.0.0"})
            for flow, name in GRC_FILES.items()}


def context(spec=SPEC, limit=None):
    return CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0, limit=limit)


def with_scope(**changes):
    return SPEC.model_copy(update={"scope": {**SPEC.scope, **changes}})


class TestImfSdmxCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return ImfSdmxCrawler()

    @pytest.fixture
    def spec(self):
        return GRC_SPEC

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher(routes(), now=lambda: T0)


def test_plugin_is_discovered():
    assert discover_crawlers()["imf_sdmx"] is ImfSdmxCrawler


def test_yaml_matches_the_agreed_source_definition():
    assert (SPEC.country, SPEC.kind, SPEC.base_url, SPEC.allowed_hosts) == ("intl", "imf_sdmx", BASE, ["api.imf.org"])
    assert (SPEC.rate_per_sec, SPEC.schedule, SPEC.needs_key, SPEC.key_header) == (1, "weekly", None, None)
    lic = SPEC.license
    assert lic.url == "https://www.imf.org/en/About/copyright-and-terms"
    assert lic.attribution.startswith("International Monetary Fund. World Economic Outlook (WEO), ")
    assert (lic.usage_scope.value, lic.redistribution.value, lic.outflow.value) == ("redistributable", "allowed", "raw_ok")
    assert lic.cloud_model_ok is False and lic.verified_at.isoformat() == "2026-11-10"
    assert SPEC.scope["dataflows"] == ["WEO", "WEO_2026_APR_VINTAGE", "WEO_2025_OCT_VINTAGE"]
    assert [(s["query"], s["startPeriod"], s["endPeriod"]) for s in SPEC.scope["series"]] == [
        ("JPN+USA+DEU+FRA+GBR.NGDPD.A", "2000", "2024"), ("JPN+USA+DEU+FRA+GBR.NGDP.A", "2000", "2024"),
        ("GRC.GGXCNL_NGDP.A", "2000", "2015")]


def test_one_task_per_dataflow_and_key_on_the_official_host_with_the_sdmx_csv_accept_header():
    tasks = list(ImfSdmxCrawler().plan(context()))
    assert len(tasks) == 9 and len({t.redacted_url for t in tasks}) == 9  # distinct URLs: the runner dedups on URL
    assert {urlsplit(t.url).hostname for t in tasks} == {"api.imf.org"}
    assert {t.url for t in tasks} >= set(GRC_URLS.values())
    assert ACCEPT == "application/vnd.sdmx.data+csv;version=1.0.0"
    for task in tasks:
        assert task.headers == {"Accept": ACCEPT}
        assert set(task.meta) == {"dataflow", "key", "startPeriod", "endPeriod"}
        assert f"/data/IMF.RES,{task.meta['dataflow']}/{task.meta['key']}?" in task.url
        assert "www.imf.org" not in task.url and "data.imf.org" not in task.url


def test_oldest_release_first_and_current_flow_last_for_every_key():
    tasks = list(ImfSdmxCrawler().plan(context()))
    for key in {t.meta["key"] for t in tasks}:
        assert [t.meta["dataflow"] for t in tasks if t.meta["key"] == key] == [
            "WEO_2025_OCT_VINTAGE", "WEO_2026_APR_VINTAGE", "WEO"]
    # and the order does not depend on how the yaml lists the flows
    shuffled = with_scope(dataflows=["WEO_2026_APR_VINTAGE", "WEO", "WEO_2025_OCT_VINTAGE"])
    assert [t.url for t in ImfSdmxCrawler().plan(context(shuffled))] == [t.url for t in tasks]


def test_headers_reach_the_server_on_the_real_fetcher():
    replay = ReplayTransport({"https://api.imf.org/robots.txt": Recorded(status=404),
                              GRC_URLS["WEO"]: Recorded(FIXTURES / GRC_FILES["WEO"])})
    task = next(t for t in ImfSdmxCrawler().plan(context(GRC_SPEC)) if t.meta["dataflow"] == "WEO")
    with PoliteHttpFetcher.from_spec(GRC_SPEC, transport=replay, clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
        result = fetcher.fetch(task.url, headers=task.headers)
    assert result.outcome.value == "ok" and result.content == (FIXTURES / GRC_FILES["WEO"]).read_bytes()
    assert replay.requests == ["https://api.imf.org/robots.txt", GRC_URLS["WEO"]]
    assert replay.request_headers[-1]["accept"] == ACCEPT


def test_crawl_fetches_all_three_flows_of_the_greek_series():
    steps = drive_crawler(ImfSdmxCrawler(), GRC_SPEC, FakeHttpFetcher(routes()), now=T0)
    assert [s.task.url for s in steps] == [GRC_URLS[f] for f in ("WEO_2025_OCT_VINTAGE", "WEO_2026_APR_VINTAGE", "WEO")]
    assert all(s.result.outcome.value == "ok" for s in steps)


def test_limit():
    assert len(list(ImfSdmxCrawler().plan(context(limit=2)))) == 2
    assert not list(ImfSdmxCrawler().plan(context(limit=0)))


@pytest.mark.parametrize("key", [
    "GRC/GGXCNL_NGDP.A", "GRC.GGXCNL_NGDP.A?x=1", "GRC.GGXCNL_NGDP.A#f", "GRC. GGXCNL_NGDP.A", "GRC.%2F.A",
    "GRC.日本.A", "GRC.GGXCNL_NGDP.A\n", "GRC.GGXCNL_NGDP", "GRC..A", "", None, 5,
])
def test_invalid_key_fails_before_the_first_request(key):
    series = [dict(entry) for entry in SPEC.scope["series"]]
    series[-1]["query"] = key
    with pytest.raises(ValueError, match="key"):
        ImfSdmxCrawler().plan(context(with_scope(series=series)))


@pytest.mark.parametrize("scope", [
    {"dataflows": []}, {"dataflows": "WEO"}, {"dataflows": ["WEO", "WEO"]}, {"dataflows": ["WEO/../x"]},
    {"dataflows": ["WEO_2026_JUL_VINTAGE"]}, {"dataflows": ["IMF.RES,WEO"]}, {"dataflows": [None]},
    {"series": []}, {"series": "x"}, {"series": ["GRC.GGXCNL_NGDP.A"]},
    {"series": [{"query": "GRC.GGXCNL_NGDP.A", "startPeriod": "2015", "endPeriod": "2000"}]},
    {"series": [{"query": "GRC.GGXCNL_NGDP.A", "startPeriod": "2000", "endPeriod": "20x5"}]},
    {"series": [{"query": "GRC.GGXCNL_NGDP.A", "startPeriod": 2000, "endPeriod": "2015"}]},
    {"series": [{"query": "GRC.GGXCNL_NGDP.A", "startPeriod": "2000", "endPeriod": "2015"}] * 2},
    {"dataflows": [["WEO"]]},
])
def test_invalid_scope_is_rejected_before_any_task(scope):
    with pytest.raises(ValueError):
        ImfSdmxCrawler().plan(context(with_scope(**scope)))
