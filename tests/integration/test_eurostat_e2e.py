"""Offline Eurostat plugin → real stores → query_sql/cite → snapshot history.

Population, robots and the revised deficit are explicitly synthetic. Recorded
government/GDP bytes and DSDs stay untouched. ReplayTransport rejects new URLs.
"""

from datetime import timedelta
from decimal import Decimal

import httpx
import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.models import Citation, SourceText, TimeBasis
from datumoj.rikolto.core.runner import CrawlDeps
from datumoj.rikolto.testing.fakes import FakeRegistry, FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0
from tests.crawlers.test_eurostat import SPEC
from tests.parsers.test_eurostat import BASE, GOV, data_bytes, dsd_bytes

SQL = ("SELECT source_id, table_key, obs_id, snapshot_sha256, release_id, value FROM observations "
       "WHERE table_key='gov_10dd_edpt1' AND area_code='EL' AND time_label='2009' "
       "AND indicator_code='B9' AND unit='PC_GDP'")


def test_query_citation_and_revised_snapshot_history(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Eurostat tests must never access the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    root = tmp_path / "data"
    deps = wiring.build(root, {"registry": FakeRegistry([SPEC])})
    manager = wiring._ReleaseStateManager(FakeStateObjectStore(), root)
    robot = tmp_path / "robots.synthetic.txt"
    robot.write_text("User-agent: *\nAllow: /eurostat/api/dissemination/sdmx/2.1/\n")
    routes = {"https://ec.europa.eu/robots.txt": Recorded(robot)}
    urls = {}
    for entry in SPEC.scope["datasets"]:
        code = entry["id"]
        dsd = tmp_path / f"{code}.dsd.xml"
        dsd.write_bytes(dsd_bytes(code))
        data = tmp_path / f"{code}.xml"
        data.write_bytes(data_bytes(code))
        urls[code] = f"{BASE}data/{code}/{entry['query']}?startPeriod={entry['startPeriod']}&endPeriod={entry['endPeriod']}"
        routes[f"{BASE}datastructure/ESTAT/{code.upper()}?references=all"] = Recorded(dsd, headers={"etag": code})
        routes[urls[code]] = Recorded(data, headers={"content-type": "application/vnd.sdmx.genericdata+xml;version=2.1"})
    clock = [T0]

    def crawl_and_parse():
        transport = ReplayTransport(routes)
        with PoliteHttpFetcher.from_spec(SPEC, transport=transport, now=lambda: clock[0],
                                        clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            report = deps.run_crawl(SPEC, deps.crawler_classes[SPEC.kind](), CrawlDeps(
                raw=deps.raw, manifest=deps.manifest, fetcher=fetcher, now=lambda: clock[0],
                clock=lambda: 0.0, sleep=lambda _: None,
            ))
        assert not report.failures and report.stop_reason is None, report
        assert len(transport.requests) == 7  # robots + three DSDs + three data responses
        parsed = deps.parse_pending(deps.pipeline_deps, SPEC.id)
        assert not parsed.failed and not parsed.no_parser, parsed

    def assert_answer(answer, value):
        assert answer["gap_state"] == "ok", answer
        assert len(answer["data"]["rows"]) == len(answer["data"]["provenance"]) == 1
        evidence = answer["data"]["provenance"][0]
        assert Decimal(evidence["value"]) == value
        citation = Citation.model_validate(evidence["citation"])
        assert citation.time_basis is TimeBasis.period
        assert citation.period.label == "2009"
        assert citation.published_at is None
        assert citation.resource_url == urls[GOV]
        assert citation.license == SPEC.license.name
        assert citation.attribution == SPEC.license.attribution
        restored = deps.tools.cite(citation.snapshot_sha256, citation.locator,
                                   snapshot_id=citation.snapshot_id, resource_url=citation.resource_url)
        assert restored["data"]["status"] == "ok", restored
        passage, = restored["data"]["passages"]
        assert Decimal(passage["value"]) == value
        assert SourceText.model_validate(passage["indicator"]).content == "B9"
        deps.raw.verify(citation.snapshot_sha256)
        return citation, evidence["obs_id"]

    try:
        crawl_and_parse()
        original, obs_id = assert_answer(deps.tools.query_sql(SQL), Decimal("-15.4"))
        assert original.fetched_at == T0
        assert deps.observations.query_readonly("SELECT count(*) FROM observations").rows == [(80,)]
        assert len(deps.catalog.search("data structure")) == 2
        # Change only the 2009 deficit rate in a temporary synthetic snapshot.
        revised = tmp_path / "gov_revised.synthetic.xml"
        body = data_bytes(GOV)
        assert body.count(b'value="-15.4"') == 1
        revised.write_bytes(body.replace(b'value="-15.4"', b'value="-16.0"'))
        routes[urls[GOV]] = Recorded(revised)
        clock[0] += timedelta(days=1)
        crawl_and_parse()
        manager.push()  # Publish one generation containing both snapshot versions.
        token = deps.observations.pin_generation()
        current, current_id = assert_answer(deps.tools.query_sql(SQL, generation_token=token), Decimal("-16.0"))
        old, old_id = assert_answer(deps.tools.query_sql(
            SQL, generation_token=token, as_of_release=original.snapshot_sha256), Decimal("-15.4"))
        assert obs_id == current_id == old_id
        assert current.snapshot_sha256 != old.snapshot_sha256 == original.snapshot_sha256
        assert current.fetched_at == clock[0] and old.fetched_at == T0
        releases = deps.observations.list_releases(generation_token=token, source_id=SPEC.id, table_key=GOV)
        assert {entry.release_key for entry in releases} == {original.snapshot_sha256, current.snapshot_sha256}
        # ReleaseEntry uses a compatibility fetch-day boundary for snapshots;
        # the observations themselves still have no source publication date.
        for sha in (original.snapshot_sha256, current.snapshot_sha256):
            result = deps.observations.query_readonly(SQL.replace("release_id, value", "release_id, released_at, value"),
                                                     generation_token=token, as_of_release=sha)
            assert all(obs.release_id is None and obs.released_at is None for obs in result.provenance)
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
            getattr(deps, name).close()


def test_robots_blocks_current_api_without_bypass(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("robots test must never access the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    robot = tmp_path / "robots.synthetic.txt"
    robot.write_text("User-agent: *\nDisallow: /eurostat/api/\n")
    transport = ReplayTransport({"https://ec.europa.eu/robots.txt": Recorded(robot)})
    with PoliteHttpFetcher.from_spec(SPEC, transport=transport, clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
        result = fetcher.fetch(BASE + "datastructure/ESTAT/GOV_10DD_EDPT1?references=all")
    assert result.outcome.value == "blocked_by_robots"
    assert transport.requests == ["https://ec.europa.eu/robots.txt"]
