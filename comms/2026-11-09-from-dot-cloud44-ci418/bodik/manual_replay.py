"""Offline CKAN replay. No real HTTP, credentials, state store, or remote writes.

The regression assertion intentionally fails on ac478731: changing only the
crawler's run_started bookkeeping causes a byte-identical HTTP 200 to parse again.
Other cases characterize the current counters and protect the diagnosis.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from datumoj.rikolto import cloud, wiring
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import FetchOutcome, parse_context_fingerprint
from datumoj.rikolto.core.runner import CrawlDeps, CrawlReport, run_crawl
from datumoj.rikolto.crawlers.ckan import CkanCrawler
from datumoj.rikolto.pipeline import ParseSummary, PipelineDeps, parse_pending
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeHttpFetcher,
    FakeKeywordIndex,
    FakeObservationStore,
    FakeRawStore,
    FakeRegistry,
    FakeResponse,
)
from datumoj.rikolto.testing.samples import make_snapshot, make_spec

T0 = datetime(2026, 11, 9, tzinfo=UTC)
API = "https://synthetic.example/api/3/action"
URL0 = API + "/package_search?q=synthetic&rows=3&start=0&sort=id%20asc"
URL1 = API + "/package_search?q=synthetic&rows=3&start=1&sort=id%20asc"
PAGE = json.dumps({"success": True, "result": {"count": 1, "results": [
    {"id": "synthetic-facility", "title": "Synthetic facility", "resources": []},
]}}).encode()
EMPTY = b'{"success":true,"result":{"count":1,"results":[]}}'


@pytest.fixture
def world(tmp_path):
    class CountingCkan(wiring._CkanPages):
        calls = 0

        def parse(self, snapshot, data, meta=None):
            type(self).calls += 1
            return super().parse(snapshot, data, meta)

    spec = make_spec(
        "jp-bodik", kind="ckan", base_url=API,
        allowed_hosts=["synthetic.example"],
        scope={"q": "synthetic", "rows": 3, "incremental": False, "datastore": True},
    )
    manifest = SqliteManifest(tmp_path / "manifest.db")
    deps = PipelineDeps(
        manifest=manifest, raw=FakeRawStore(), registry=FakeRegistry([spec]),
        chunks=FakeChunkStore(), keyword=FakeKeywordIndex(),
        observations=FakeObservationStore(), catalog=FakeCatalogStore(),
        parsers={("ckan", "*"): CountingCkan}, vectors=None,
    )
    yield SimpleNamespace(spec=spec, deps=deps, parser=CountingCkan)
    manifest.close()


def roundtrip(world, when, *, conditional=False):
    headers = {"content-type": "application/json"}
    if conditional:
        headers["etag"] = '"synthetic-v1"'
    fetcher = FakeHttpFetcher({
        URL0: FakeResponse(200, PAGE, headers),
        URL1: FakeResponse(200, EMPTY, headers),
    }, now=lambda: when)
    crawl = run_crawl(world.spec, CkanCrawler(), CrawlDeps(
        manifest=world.deps.manifest, raw=world.deps.raw, fetcher=fetcher,
        now=lambda: when, clock=lambda: 0.0, sleep=lambda seconds: None,
    ))
    parsed = parse_pending(world.deps, "jp-bodik")
    assert crawl.failures == {} and parsed.failed == {}
    return crawl, parsed


@pytest.mark.parametrize(("conditional", "expected_parsed"), [
    (False, [2, 2, 2]),
    (True, [2, 0, 0]),
])
def test_current_counter_matrix(world, conditional, expected_parsed):
    rows = []
    for index in range(3):
        crawl, parsed = roundtrip(
            world, T0 + timedelta(days=index), conditional=conditional,
        )
        rows.append((crawl.new, crawl.unchanged, len(parsed.parsed), world.parser.calls))
        assert len(parsed.parsed) == expected_parsed[index]
    assert [row[:2] for row in rows] == [(2, 0), (0, 2), (0, 2)]
    assert world.parser.calls == sum(expected_parsed)
    assert len(world.deps.raw.blobs) == 2
    assert len(world.deps.catalog.search("", k=10)) == 1
    assert list(world.deps.chunks.iter_chunks()) == []
    # No new observations: historical successful empty doc_keys are reusable.
    assert parse_pending(world.deps, "jp-bodik").parsed == []
    assert world.parser.calls == sum(expected_parsed)
    print(json.dumps({"conditional": conditional,
                      "rows_new_unchanged_parsed_total_calls": rows}))


def test_same_metadata_new_observation_reuses_without_parse(world):
    roundtrip(world, T0)
    for index, previous in enumerate(world.deps.manifest.snapshots_for("jp-bodik")):
        snapshot = previous.model_copy(update={
            "snapshot_id": f"repeat-{index}", "fetched_at": T0 + timedelta(days=1),
        })
        world.deps.manifest.record_snapshot(
            snapshot, world.deps.manifest.snapshot_meta(previous.snapshot_id),
        )
    assert parse_pending(world.deps, "jp-bodik").parsed == []
    assert world.parser.calls == 2


def test_only_run_started_changes_between_identical_200_pages(world):
    roundtrip(world, T0)
    roundtrip(world, T0 + timedelta(days=1))
    pages = [s for s in world.deps.manifest.snapshots_for("jp-bodik") if s.resource_url == URL0]
    first, second = pages
    before = world.deps.manifest.snapshot_meta(first.snapshot_id)
    after = world.deps.manifest.snapshot_meta(second.snapshot_id)
    assert first.outcome is second.outcome is FetchOutcome.ok
    assert first.sha256 == second.sha256
    assert {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)} == {"run_started"}
    assert parse_context_fingerprint(first, before) != parse_context_fingerprint(second, after)


def test_empty_output_roles_still_increment_parsed(world):
    for index, role in enumerate(("resource", "activity", "datastore")):
        body = b"synthetic body, intentionally ignored by the CKAN wrapper"
        world.deps.raw.put(body)
        snapshot = make_snapshot(
            "jp-bodik", url=f"https://synthetic.example/{role}", data=body,
            snapshot_id=f"ignored-{index}", fetched_at=T0,
        )
        world.deps.manifest.record_snapshot(snapshot, {"role": role, "run_started": T0.isoformat()})
    parsed = parse_pending(world.deps, "jp-bodik")
    assert len(parsed.parsed) == world.parser.calls == 3
    assert world.deps.catalog.search("", k=10) == []
    assert list(world.deps.chunks.iter_chunks()) == []
    assert world.deps.observations.query_readonly("SELECT count(*) FROM observations").rows == [(0,)]


def test_cloud44_aggregate_is_degraded_but_ledger_incomplete(world):
    rows = []
    deps = SimpleNamespace(
        manifest=world.deps.manifest, raw=world.deps.raw,
        crawler_classes={"ckan": CkanCrawler},
        fetcher_for=lambda spec: FakeHttpFetcher({}),
        crawl_deps_type=CrawlDeps,
        run_crawl=lambda *args: CrawlReport(
            source_id="jp-bodik", new=5, unchanged=169,
            failures={"http_error": 1}, stop_reason="deadline",
        ),
        pipeline_deps=world.deps,
        parse_pending=lambda *args: ParseSummary(parsed=[f"synthetic-{i}" for i in range(174)]),
    )
    result, _ = cloud._source(deps, world.spec, SimpleNamespace(append_run=rows.append), None)
    assert result["status"] == "degraded" and result["exit_status"] == 0
    assert result["source_health"]["categories"] == {"http_error": 1}
    assert result["source_health"]["phase"] == "crawl"
    assert result["stop_reason"] == "deadline"
    assert rows[-1]["status"] == "incomplete"
    assert rows[-1]["source_status"] == "degraded"
    assert rows[-1]["item_failures"] == {"crawl_failed": 1}
    assert cloud._failure_streak(rows, "jp-bodik") == (0, None)


def test_identical_200_new_run_should_reuse_ckan_parse_artifacts(world):
    """Desired optimization regression: expected to fail on the inspected head.

    A fix must retain run_started in stored crawl metadata and preserve the
    generic full-metadata fingerprint contract for all other parser consumers.
    """
    _, first = roundtrip(world, T0)
    assert len(first.parsed) == 2
    crawl, second = roundtrip(world, T0 + timedelta(days=1))
    assert (crawl.new, crawl.unchanged) == (0, 2)
    assert second.parsed == [], "unchanged CKAN bytes were reparsed solely for a new run_started"
