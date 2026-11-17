"""Offline UN Comtrade plugin → real stores → query_sql/cite → getDA releases. Synthetic samples only.

Responses are the synthetic samples of tests/fixtures/comtrade, cut to one year per request (see
tests/parsers/test_un_comtrade.py). ReplayTransport rejects any URL that is not registered and records every
request's headers with the key value masked, so the key is visible as a header but never as a value.
"""

import json
import secrets
from datetime import timedelta
from decimal import Decimal
from itertools import count

import httpx
import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.models import Citation, TimeBasis
from datumoj.rikolto.core.runner import CrawlDeps
from datumoj.rikolto.testing.fakes import FakeRegistry, FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0
from tests.crawlers.test_un_comtrade import KEY_ENV, KEY_HEADER, PARTNER, REPORTERS, SPEC, YEARS, expected_requests
from tests.parsers.test_un_comtrade import RELEASE_842_2023, data_bytes, data_url, release_bytes, release_url

ROBOTS = "https://comtradeapi.un.org/robots.txt"
REVISED_842_2023 = "2084220230120219@2024-06-01T00:00:00"
EXPORT_842 = ("SELECT source_id, table_key, obs_id, snapshot_sha256, release_id, value FROM observations "
              "WHERE area_code='842' AND time_label='2023' AND indicator_code='{flow}'")


def test_query_citation_and_getda_releases(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Comtrade tests must never access the network")

    key = secrets.token_hex(16)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setenv(KEY_ENV, key)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    root = tmp_path / "data"
    deps = wiring.build(root, {"registry": FakeRegistry([SPEC])})
    manager = wiring._ReleaseStateManager(FakeStateObjectStore(), root)
    files = {}
    for reporter in REPORTERS:
        for year in YEARS:
            files[release_url(reporter, year)] = release_bytes(reporter, year)
            files[data_url(reporter, PARTNER[reporter], year)] = data_bytes(reporter, PARTNER[reporter], year)
    routes = {ROBOTS: Recorded(None, status=404)}
    numbers = count(1)

    def publish(url, body):
        path = tmp_path / f"{next(numbers)}.json"
        path.write_bytes(body)
        routes[url] = Recorded(path, headers={"content-type": "application/json"})

    for url, body in files.items():
        publish(url, body)
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
        # The key is a header on every page request (value masked by the replay), never part of a URL.
        sent = [h for url, h in zip(transport.requests, transport.request_headers, strict=True) if url != ROBOTS]
        assert sent and all(h.get(KEY_HEADER.lower()) == "REDACTED" for h in sent)
        assert key not in repr(transport.requests) + repr(transport.request_headers)
        return transport.requests

    def one_row(answer, value, release_id):
        assert answer["gap_state"] == "ok", answer
        assert len(answer["data"]["rows"]) == len(answer["data"]["provenance"]) == 1
        evidence = answer["data"]["provenance"][0]
        assert release_id in str(answer["data"]["rows"][0])  # the release_id column of the SELECT
        assert Decimal(evidence["value"]) == value  # the store keeps a DECIMAL; the verbatim text is in dims
        citation = Citation.model_validate(evidence["citation"])
        assert citation.time_basis is TimeBasis.period and citation.published_at is None
        assert citation.period.label == "2023"
        assert citation.license == SPEC.license.name and citation.attribution == "Source: UN Comtrade."
        assert citation.usage_scope.value == "internal_only"
        restored = deps.tools.cite(citation.snapshot_sha256, citation.locator,
                                   snapshot_id=citation.snapshot_id, resource_url=citation.resource_url)
        assert restored["data"]["status"] == "ok", restored
        passage, = restored["data"]["passages"]
        assert Decimal(passage["value"]) == value
        deps.raw.verify(citation.snapshot_sha256)
        return citation

    try:
        assert crawl_and_parse() == [ROBOTS, *expected_requests()]
        # 6 reporters × 2 years × X/M; the 251 → 251 re-import row of the sample is not in the store.
        assert deps.observations.query_readonly("SELECT count(*) FROM observations").rows == [(24,)]
        assert deps.observations.query_readonly(
            "SELECT count(*) FROM observations WHERE area_code='251' AND time_label='2022'").rows == [(2,)]
        # A week later nothing changed: only the twelve getDA checks go out, no data request.
        clock[0] += timedelta(days=7)
        assert crawl_and_parse() == [ROBOTS, *(release_url(r, y) for r in REPORTERS for y in YEARS)]

        # The United States re-releases 2023 and the numbers move: one data request, a new release.
        clock[0] += timedelta(days=7)
        publish(release_url(842, 2023), release_bytes(842, 2023, revised=True))
        publish(data_url(842, 156, 2023), data_bytes(842, 156, 2023).replace(b"4000000000.0", b"4100000000.0"))
        requests = crawl_and_parse()
        assert [u for u in requests if "/data/" in u] == [data_url(842, 156, 2023)]
        manager.push()
        token = deps.observations.pin_generation()

        old = one_row(deps.tools.query_sql(EXPORT_842.format(flow="X"), generation_token=token,
                                           as_of_release=RELEASE_842_2023), Decimal("4000000000.0"), RELEASE_842_2023)
        new = one_row(deps.tools.query_sql(EXPORT_842.format(flow="X"), generation_token=token),
                      Decimal("4100000000.0"), REVISED_842_2023)
        assert old.snapshot_sha256 != new.snapshot_sha256
        assert old.resource_url == new.resource_url == data_url(842, 156, 2023)
        assert key not in old.resource_url + new.resource_url
        # The import side carries cif and fob next to primaryValue (= cif); the export side has no cif.
        dims = {flow: json.loads(deps.observations.query_readonly(
            f"SELECT dims FROM observations WHERE area_code='842' AND time_label='2023' AND indicator_code='{flow}'",
            generation_token=token).rows[0][0]) for flow in ("X", "M")}
        assert (dims["M"]["cifvalue"], dims["M"]["fobvalue"]) == ("5250000000.0", "5000000000.0")
        assert "cifvalue" not in dims["X"] and dims["X"]["fobvalue"] == "4100000000.0"
        assert dims["X"]["datasetChecksum"] == "999999" and dims["M"]["datasetChecksum"] == "999999"
        releases = deps.observations.list_releases(
            generation_token=token, source_id=SPEC.id, table_key="total_842_156_2023")
        assert [(e.release_key, e.released_at.isoformat()) for e in releases] == [
            (RELEASE_842_2023, "2024-03-01"), (REVISED_842_2023, "2024-06-01")]
        assert "Comtrade" in str(deps.catalog.search("Comtrade"))
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
            getattr(deps, name).close()
