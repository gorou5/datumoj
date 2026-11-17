"""Offline OECD plugin → real stores → query_sql/cite → per-EDITION releases.

Recorded bytes are served untouched where they exist. Synthetic (see tests/parsers/test_oecd_sdmx.py):
one file per revisions EDITION (whole recorded lines) and the HOU_EAR contentconstraint (the recorded
NAMAIN10 one with ids rewritten). ReplayTransport rejects any URL that is not registered.
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
from tests.crawlers.test_oecd_sdmx import CONSTRAINTS, EDITIONS, SPEC
from tests.parsers.test_oecd_sdmx import (
    HOU,
    HOU_URL,
    NAMAIN,
    NAMAIN_URL,
    RECORDED_VALID_FROM,
    constraint_bytes,
    edition_bytes,
    hou_bytes,
    namain_bytes,
)

ROBOTS = "https://sdmx.oecd.org/robots.txt"
REVISIONS = ("SELECT source_id, table_key, obs_id, snapshot_sha256, release_id, value FROM observations "
             "WHERE table_key='df_stes_revisions' AND time_label='2018-01'")
GDP = ("SELECT source_id, table_key, obs_id, snapshot_sha256, release_id, value FROM observations "
       "WHERE table_key='df_table1_expenditure' AND area_code='JPN' AND time_label='2020' AND unit='{unit}'")


def test_query_citation_and_per_edition_releases(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("OECD tests must never access the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    root = tmp_path / "data"
    deps = wiring.build(root, {"registry": FakeRegistry([SPEC])})
    manager = wiring._ReleaseStateManager(FakeStateObjectStore(), root)
    files = {
        CONSTRAINTS[0]: constraint_bytes(NAMAIN), CONSTRAINTS[1]: constraint_bytes(HOU, "2026-11-01T00:00:00Z"),
        NAMAIN_URL: namain_bytes(), HOU_URL: hou_bytes(),
        EDITIONS[0]: edition_bytes("201901"), EDITIONS[1]: edition_bytes("201902"),
    }
    routes = {ROBOTS: Recorded(None, status=404)}
    for number, (url, body) in enumerate(files.items()):
        path = tmp_path / f"{number}.bin"
        path.write_bytes(body)
        routes[url] = Recorded(path, headers={"content-type": "application/vnd.sdmx.data+csv; charset=utf-8"})
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
        parsed = deps.parse_pending(deps.pipeline_deps, SPEC.id)
        assert not parsed.failed and not parsed.no_parser, parsed
        return transport.requests

    def one_row(answer, value, release_id):
        assert answer["gap_state"] == "ok", answer
        assert len(answer["data"]["rows"]) == len(answer["data"]["provenance"]) == 1
        evidence = answer["data"]["provenance"][0]
        assert release_id in str(answer["data"]["rows"][0])  # the release_id column of the SELECT
        assert Decimal(evidence["value"]) == value and str(Decimal(evidence["value"])) == str(value)
        citation = Citation.model_validate(evidence["citation"])
        assert citation.time_basis is TimeBasis.period and citation.published_at is None
        assert citation.license == SPEC.license.name and citation.attribution == SPEC.license.attribution
        assert citation.usage_scope.value == "internal_only"
        restored = deps.tools.cite(citation.snapshot_sha256, citation.locator,
                                   snapshot_id=citation.snapshot_id, resource_url=citation.resource_url)
        assert restored["data"]["status"] == "ok", restored
        passage, = restored["data"]["passages"]
        assert Decimal(passage["value"]) == value
        deps.raw.verify(citation.snapshot_sha256)
        return citation, evidence

    try:
        requests = crawl_and_parse()
        assert requests == [ROBOTS, CONSTRAINTS[0], NAMAIN_URL, CONSTRAINTS[1], HOU_URL, *EDITIONS]
        # Current rows: NAMAIN10 (20) + HOU_EAR (12) + the last EDITION written (201902: 6); 201901 stays in the history.
        assert deps.observations.query_readonly("SELECT count(*) FROM observations").rows == [(20 + 12 + 6,)]
        # A week later nothing changed: only the two contentconstraint checks go out, no data request.
        clock[0] += timedelta(days=7)
        assert crawl_and_parse() == [ROBOTS, *CONSTRAINTS]
        manager.push()
        token = deps.observations.pin_generation()

        # Both EDITIONs of the same observation are queryable as releases.
        old, _ = one_row(deps.tools.query_sql(REVISIONS, generation_token=token, as_of_release="201901"),
                         Decimal("81.41"), "201901")
        new, _ = one_row(deps.tools.query_sql(REVISIONS, generation_token=token, as_of_release="201902"),
                         Decimal("81.51"), "201902")
        latest, _ = one_row(deps.tools.query_sql(REVISIONS, generation_token=token), Decimal("81.51"), "201902")
        assert old.snapshot_sha256 != new.snapshot_sha256 == latest.snapshot_sha256
        assert old.resource_url == EDITIONS[0] and new.resource_url == EDITIONS[1]
        assert old.period.label == "2018-01"
        releases = deps.observations.list_releases(generation_token=token, source_id=SPEC.id, table_key="df_stes_revisions")
        assert [(e.release_key, e.released_at.isoformat()) for e in releases] == [
            ("201901", "2019-01-01"), ("201902", "2019-02-01")]

        # UNIT_MULT=6: the value is the recorded text, the multiplier travels in the unit.
        citation, _ = one_row(deps.tools.query_sql(GDP.format(unit="JPY ×10^6"), generation_token=token),
                              Decimal(554074400), f"{NAMAIN}@{RECORDED_VALID_FROM}")
        assert citation.resource_url == NAMAIN_URL
        usd = deps.tools.query_sql(GDP.format(unit="USD_EXC ×10^6"), generation_token=token)
        assert [Decimal(p["value"]) for p in usd["data"]["provenance"]] == [Decimal("5189197.544")]
        entry, = deps.observations.list_releases(generation_token=token, source_id=SPEC.id, table_key="df_table1_expenditure")
        assert (entry.release_key, entry.released_at.isoformat()) == (f"{NAMAIN}@{RECORDED_VALID_FROM}", "2026-11-16")
        assert SourceText.model_validate(deps.tools.cite(
            citation.snapshot_sha256, citation.locator, snapshot_id=citation.snapshot_id,
            resource_url=citation.resource_url)["data"]["passages"][0]["indicator"]).content == "B1GQ"
        assert HOU in str(deps.catalog.search("hou_ear") or deps.catalog.search("DSD_EAR"))
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
            getattr(deps, name).close()
