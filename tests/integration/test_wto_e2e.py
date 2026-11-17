"""Offline WTO bulk plugin → real stores → query_sql/cite; a 304 adds no snapshot.

The ZIP is the recorded excerpt; robots.txt is synthetic. ReplayTransport rejects unknown URLs,
so any request outside stats.wto.org's bulk path fails the test.
"""

from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.models import Citation, FetchOutcome, SourceText
from datumoj.rikolto.core.runner import CrawlDeps
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.testing.samples import T0
from tests.parsers.test_wto_bulk import ETAG, LAST_MODIFIED, SPEC, URL, ZIP

SQL = ("SELECT source_id, table_key, obs_id, snapshot_sha256, release_id, released_at, value FROM observations "
       "WHERE area_code='840' AND indicator_code='ITS_MTV_AM' AND time_label='2023'")


def test_query_citation_release_and_304_skips_new_snapshot(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("WTO tests must never access the network")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    deps = wiring.build(tmp_path / "data", {"registry": FakeRegistry([SPEC])})
    robot = tmp_path / "robots.synthetic.txt"
    robot.write_text("User-agent: *\nAllow: /assets/\n")
    headers = {"content-type": "application/x-zip-compressed", "etag": ETAG, "last-modified": LAST_MODIFIED}
    clock = [T0]

    def crawl(zip_route):
        transport = ReplayTransport({"https://stats.wto.org/robots.txt": Recorded(robot), URL: zip_route})
        with PoliteHttpFetcher.from_spec(SPEC, transport=transport, now=lambda: clock[0],
                                        clock=lambda: 0.0, sleep=lambda _: None) as fetcher:
            report = deps.run_crawl(SPEC, deps.crawler_classes[SPEC.kind](), CrawlDeps(
                raw=deps.raw, manifest=deps.manifest, fetcher=fetcher, now=lambda: clock[0],
                clock=lambda: 0.0, sleep=lambda _: None,
            ))
        assert not report.failures and report.stop_reason is None, report
        assert transport.requests == ["https://stats.wto.org/robots.txt", URL]  # no other host or path
        return transport

    try:
        crawl(Recorded(ZIP, headers=headers))
        parsed = deps.parse_pending(deps.pipeline_deps, SPEC.id)
        assert not parsed.failed and not parsed.no_parser, parsed
        assert deps.observations.query_readonly("SELECT count(*) FROM observations").rows == [(120,)]
        answer = deps.tools.query_sql(SQL)
        assert answer["gap_state"] == "ok", answer
        evidence, = answer["data"]["provenance"]
        assert Decimal(evidence["value"]) == Decimal(3168237)
        citation = Citation.model_validate(evidence["citation"])
        assert citation.resource_url == URL and citation.fetched_at == T0
        assert citation.license == SPEC.license.name and citation.attribution == SPEC.license.attribution
        assert "ODbL" in citation.attribution and "WTO" in citation.attribution
        restored = deps.tools.cite(citation.snapshot_sha256, citation.locator,
                                   snapshot_id=citation.snapshot_id, resource_url=citation.resource_url)
        assert restored["data"]["status"] == "ok", restored
        passage, = restored["data"]["passages"]
        assert Decimal(passage["value"]) == Decimal(3168237)
        assert SourceText.model_validate(passage["indicator"]).content.startswith("Merchandise imports")
        deps.raw.verify(citation.snapshot_sha256)
        row = deps.observations.query_readonly(SQL).rows[0]
        assert row[4].startswith(f"{LAST_MODIFIED}|{ETAG}|{citation.snapshot_sha256}") and row[5] == date(2026, 10, 28)

        # A week later the server answers 304 to the conditional request: no new snapshot, no reparse.
        clock[0] += timedelta(days=7)
        transport = crawl(Recorded(None, status=304, headers={"etag": ETAG, "last-modified": LAST_MODIFIED}))
        sent = {k.lower(): v for k, v in transport.request_headers[-1].items()}
        assert sent.get("if-none-match") == ETAG and sent.get("if-modified-since") == LAST_MODIFIED
        snapshots = [s for s in deps.manifest.snapshots_for(SPEC.id) if s.resource_url == URL]
        assert [s.outcome for s in snapshots if s.outcome is FetchOutcome.ok] == [FetchOutcome.ok]
        assert len({s.sha256 for s in snapshots if s.sha256}) == 1
        again = deps.parse_pending(deps.pipeline_deps, SPEC.id)
        assert not again.failed and not again.no_parser, again
        assert deps.observations.query_readonly("SELECT count(*) FROM observations").rows == [(120,)]
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
            getattr(deps, name).close()
