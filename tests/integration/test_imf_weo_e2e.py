"""Offline IMF WEO plugin → real stores → query_sql/cite → release-aware reads.

Recorded samples only (tests/fixtures/imf_weo); robots.txt is a synthetic allow-all file because the
real api.imf.org/robots.txt answered 502 when recorded. ReplayTransport rejects any other URL.
"""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.models import Citation, SourceText
from datumoj.rikolto.core.runner import CrawlDeps
from datumoj.rikolto.testing.fakes import FakeRegistry, FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0
from tests.crawlers.test_imf_sdmx import ACCEPT, FIXTURES, GRC, GRC_FILES, GRC_SPEC, GRC_URLS, SPEC

SID = "intl-imf-weo"
GRC_TABLE = f"WEO:{GRC}"
SQL = ("SELECT source_id, table_key, obs_id, snapshot_sha256, release_id, value FROM observations "
       f"WHERE table_key='{GRC_TABLE}' AND area_code='GRC' AND time_label='2009' AND indicator_code='GGXCNL_NGDP'")
USD, LCU = "JPN+USA.NGDPD.A", "JPN.NGDP.A"
SAMPLES = {
    f"{USD}": ("weo_JPN-USA_NGDPD_2020-2030.csv", "2020", "2030"),
    f"{LCU}": ("weo_JPN_NGDP_2020-2024.csv", "2020", "2024"),
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("IMF tests must never access the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")


def routes_for(tmp_path, extra=None):
    robot = tmp_path / "robots.synthetic.txt"
    robot.write_text("User-agent: *\nAllow: /external/sdmx/\n")
    routes = {"https://api.imf.org/robots.txt": Recorded(robot)}
    for flow, name in GRC_FILES.items():
        routes[GRC_URLS[flow]] = Recorded(FIXTURES / name, headers={"content-type": ACCEPT})
    for key, (name, start, end) in SAMPLES.items():
        routes[f"https://api.imf.org/external/sdmx/2.1/data/IMF.RES,WEO/{key}?startPeriod={start}&endPeriod={end}"] = Recorded(
            FIXTURES / name, headers={"content-type": ACCEPT})
    return {**routes, **(extra or {})}


def build(tmp_path, spec):
    root = tmp_path / "data"
    return root, wiring.build(root, {"registry": FakeRegistry([spec])})


def crawl_and_parse(deps, spec, routes, at=T0):
    transport = ReplayTransport(routes)
    with PoliteHttpFetcher.from_spec(spec, transport=transport, now=lambda: at,
                                    clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
        report = deps.run_crawl(spec, deps.crawler_classes[spec.kind](), CrawlDeps(
            raw=deps.raw, manifest=deps.manifest, fetcher=fetcher, now=lambda: at, clock=lambda: 0.0, sleep=lambda _: None))
    assert not report.failures and report.stop_reason is None, report
    parsed = deps.parse_pending(deps.pipeline_deps, spec.id)
    assert not parsed.failed and not parsed.no_parser, parsed
    return transport


def close(deps):
    for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
        getattr(deps, name).close()


def answer_row(answer, value, release):
    assert answer["gap_state"] == "ok", answer
    assert len(answer["data"]["rows"]) == len(answer["data"]["provenance"]) == 1
    evidence = answer["data"]["provenance"][0]
    assert Decimal(evidence["value"]) == value
    cells = dict(zip(answer["data"]["columns"], answer["data"]["rows"][0], strict=True))
    shown = cells["release_id"]["content"] if isinstance(cells["release_id"], dict) else cells["release_id"]
    assert shown == release  # the row the answer shows is the one from that release
    return evidence


def test_greece_2009_and_three_releases_with_provenance(tmp_path):
    root, deps = build(tmp_path, GRC_SPEC)
    manager = wiring._ReleaseStateManager(FakeStateObjectStore(), root)
    try:
        transport = crawl_and_parse(deps, GRC_SPEC, routes_for(tmp_path))
        assert len(transport.requests) == 4  # robots + the three dataflows of the one Greek key
        assert [h["accept"] for url, h in zip(transport.requests, transport.request_headers, strict=True)
                if "/data/" in url] == [ACCEPT] * 3
        assert [url for url in transport.requests if "/data/" in url] == [
            GRC_URLS["WEO_2025_OCT_VINTAGE"], GRC_URLS["WEO_2026_APR_VINTAGE"], GRC_URLS["WEO"]]

        # Without a cutoff the table's current rows are the newest release (parse order = oldest first).
        evidence = answer_row(deps.tools.query_sql(SQL), Decimal("-15.423608"), "WEO-2026-OCT")
        citation = Citation.model_validate(evidence["citation"])
        assert citation.period.label == "2009"
        assert citation.resource_url == GRC_URLS["WEO"]
        assert citation.license == SPEC.license.name and citation.attribution == SPEC.license.attribution
        restored = deps.tools.cite(citation.snapshot_sha256, citation.locator,
                                   snapshot_id=citation.snapshot_id, resource_url=citation.resource_url)
        assert restored["data"]["status"] == "ok", restored
        passage, = restored["data"]["passages"]
        assert Decimal(passage["value"]) == Decimal("-15.423608")
        assert SourceText.model_validate(passage["indicator"]).content.startswith("Net lending (+) / net borrowing (-)")
        deps.raw.verify(citation.snapshot_sha256)
        assert deps.observations.query_readonly("SELECT count(*) FROM observations").rows == [(16,)]

        # A later crawl re-reads the same three responses (a weekly run): the current rows stay the newest release.
        crawl_and_parse(deps, GRC_SPEC, routes_for(tmp_path), T0 + timedelta(days=7))
        answer_row(deps.tools.query_sql(SQL), Decimal("-15.423608"), "WEO-2026-OCT")
        assert deps.observations.query_readonly("SELECT count(*) FROM observations").rows == [(16,)]

        manager.push()
        token = deps.observations.pin_generation()
        releases = deps.observations.list_releases(generation_token=token, source_id=SID, table_key=GRC_TABLE)
        assert [(r.release_key, r.released_at.isoformat()) for r in releases] == [
            ("WEO-2025-OCT", "2025-10-14"), ("WEO-2026-APR", "2026-04-14"), ("WEO-2026-OCT", "2026-10-13")]
        seen = {}
        for release, value in (("WEO-2025-OCT", "-15.424"), ("WEO-2026-APR", "-15.423608"), ("WEO-2026-OCT", "-15.423608")):
            evidence = answer_row(deps.tools.query_sql(SQL, generation_token=token, as_of_release=release),
                                  Decimal(value), release)
            seen[release] = (evidence["obs_id"], Citation.model_validate(evidence["citation"]).snapshot_sha256)
        # one observation, three releases, three snapshots
        assert len({obs_id for obs_id, _ in seen.values()}) == 1
        assert len({sha for _, sha in seen.values()}) == 3
        released = deps.observations.query_readonly(
            SQL.replace("release_id, value", "release_id, released_at, value"),
            generation_token=token, as_of_release="WEO-2026-APR")
        assert [(o.release_id, o.released_at.isoformat()) for o in released.provenance] == [("WEO-2026-APR", "2026-04-14")]
    finally:
        close(deps)


def test_series_do_not_overwrite_each_other_and_forecasts_are_marked(tmp_path):
    spec = SPEC.model_copy(update={"scope": {**SPEC.scope, "dataflows": ["WEO"], "series": [
        {"query": USD, "startPeriod": "2020", "endPeriod": "2030"}, {"query": LCU, "startPeriod": "2020", "endPeriod": "2024"},
        {"query": GRC, "startPeriod": "2000", "endPeriod": "2015"}]}})
    _, deps = build(tmp_path, spec)
    try:
        crawl_and_parse(deps, spec, routes_for(tmp_path))
        rows = deps.observations.query_readonly(
            "SELECT table_key, area_code, indicator_code, time_label, unit, value, note, release_id FROM observations "
            "ORDER BY table_key, area_code, time_label").rows
        assert len(rows) == 22 + 5 + 16
        assert {r[0] for r in rows} == {f"WEO:{USD}", f"WEO:{LCU}", GRC_TABLE}
        assert {r[7] for r in rows} == {"WEO-2026-OCT"}
        usd = {(r[1], r[3]): r for r in rows if r[2] == "NGDPD"}
        assert usd[("JPN", "2024")][5] == Decimal(4207630342000) and usd[("JPN", "2024")][4] == "USD"
        assert usd[("JPN", "2025")][6].startswith("实绩") and usd[("JPN", "2026")][6].startswith("预测")
        assert usd[("USA", "2030")][6].startswith("预测")
        lcu = {r[3]: r for r in rows if r[2] == "NGDP"}
        assert lcu["2024"][5] == Decimal(636893400000000) and lcu["2024"][4] == "XDC"
        # one catalog dataset per table, so keyword retrieval of one series is not replaced by another
        assert len(deps.catalog.search("World Economic Outlook")) == 3
    finally:
        close(deps)


def test_wrong_key_answering_200_with_an_empty_data_set_fails_the_snapshot(tmp_path):
    bad_key = "GRC.NO_SUCH_INDICATOR.A"
    url = f"https://api.imf.org/external/sdmx/2.1/data/IMF.RES,WEO/{bad_key}?startPeriod=2000&endPeriod=2015"
    spec = SPEC.model_copy(update={"scope": {**SPEC.scope, "dataflows": ["WEO"], "series": [
        {"query": bad_key, "startPeriod": "2000", "endPeriod": "2015"}]}})
    _, deps = build(tmp_path, spec)
    try:
        transport = ReplayTransport(routes_for(tmp_path, {url: Recorded(FIXTURES / "weo_bad_key.csv")}))
        with PoliteHttpFetcher.from_spec(spec, transport=transport, now=lambda: T0,
                                        clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            report = deps.run_crawl(spec, deps.crawler_classes[spec.kind](), CrawlDeps(
                raw=deps.raw, manifest=deps.manifest, fetcher=fetcher, now=lambda: T0, clock=lambda: 0.0, sleep=lambda _: None))
        assert not report.failures  # the HTTP exchange succeeded: the failure is found when parsing
        parsed = deps.parse_pending(deps.pipeline_deps, spec.id)
        assert len(parsed.failed) == 1, parsed
        assert deps.observations.query_readonly("SELECT count(*) FROM observations").rows == [(0,)]
    finally:
        close(deps)
