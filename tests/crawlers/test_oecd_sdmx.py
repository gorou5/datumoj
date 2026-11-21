"""OECD SDMX crawler: contentconstraint first, per-EDITION revisions, 60 requests per hour across runs."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from datumoj.rikolto.core.contracts import CrawlContext, discover_crawlers
from datumoj.rikolto.core.models import FetchOutcome, Outflow, UsageScope
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.crawlers.oecd_sdmx import MAX_REQUESTS_PER_HOUR, OecdSdmxCrawler
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse, drive_crawler
from datumoj.rikolto.testing.samples import T0, make_snapshot
from tests.parsers.test_oecd_sdmx import (
    AGENCY,
    BASE,
    HOU,
    HOU_URL,
    NAMAIN,
    NAMAIN_URL,
    RECORDED_VALID_FROM,
    STES,
    constraint_bytes,
    constraint_url,
    edition_bytes,
    edition_url,
    hou_bytes,
    namain_bytes,
    structure_bytes,
    structure_url,
)

ROOT = Path(__file__).parents[2]
REAL = load_registry(ROOT / "sources").get("intl-oecd")
assert REAL is not None
NEW_VALID_FROM = "2026-11-23T08:00:00Z"
FLOWS = [
    {"id": NAMAIN, "agency": AGENCY[NAMAIN], "version": "2.0", "startPeriod": "2020", "endPeriod": "2024",
     "query": "A.JPN+USA.S1.S1.B1GQ._Z._Z._Z.XDC+USD_EXC.V.N.T0102"},
    {"id": HOU, "agency": AGENCY[HOU], "version": "1.0", "startPeriod": "2018-01", "endPeriod": "2018-12",
     "query": "JPN.EAR.IX.N.C.S1.M"},
    {"id": STES, "agency": AGENCY[STES], "version": "4.0", "startPeriod": "2017-10", "endPeriod": "2018-03",
     "query": "JPN.M.H_EARN.IX.C", "editions": ["201901", "201902"]},
]
SPEC = REAL.model_copy(update={"scope": {"raw_retention_days": 2, "dataflows": FLOWS}})
CONSTRAINTS = [constraint_url(NAMAIN), constraint_url(HOU)]
EDITIONS = [edition_url("201901"), edition_url("201902")]
STRUCTURES = [structure_url(NAMAIN), structure_url(HOU)]
ALL = [CONSTRAINTS[0], STRUCTURES[0], NAMAIN_URL, CONSTRAINTS[1], STRUCTURES[1], HOU_URL, *EDITIONS]


def routes(*, last_modified=True, valid_from=RECORDED_VALID_FROM, data_status=200):
    """Recorded last-modified headers (the constraint's is 0001-01-01); the EDITION files are synthetic splits."""
    def reply(body, modified, status=200):
        headers = {"content-type": "application/vnd.sdmx.data+csv; charset=utf-8"}
        return FakeResponse(status, body, {**headers, **({"last-modified": modified} if last_modified else {})})

    return {
        CONSTRAINTS[0]: reply(constraint_bytes(NAMAIN, valid_from), "0001-01-01"),
        CONSTRAINTS[1]: reply(constraint_bytes(HOU, "2026-11-01T00:00:00Z"), "0001-01-01"),
        STRUCTURES[0]: reply(structure_bytes(), "2023-12-04"),
        STRUCTURES[1]: reply(structure_bytes(), "2023-12-04"),  # synthetic: the NAMAIN10 codelists again
        NAMAIN_URL: reply(namain_bytes(), "2024-09-25", data_status),
        HOU_URL: reply(hou_bytes(), "2023-11-14"),
        EDITIONS[0]: reply(edition_bytes("201901"), "2023-12-22"),
        EDITIONS[1]: reply(edition_bytes("201902"), "2023-12-22"),
    }


def run(manifest, raw, responses, *, now=T0, spec=SPEC, limit=None):
    fetcher = FakeHttpFetcher(responses, now=lambda: now)
    steps = drive_crawler(OecdSdmxCrawler(), spec, fetcher, manifest=manifest, raw=raw, now=now, limit=limit)
    return fetcher, steps


def seed(manifest, count, *, age=timedelta(minutes=10), now=T0, **changes):
    for i in range(count):
        snap = make_snapshot("intl-oecd", f"{BASE}seed/{i}", b"x", fetched_at=now - age, snapshot_id=f"seed-{age}-{i}")
        manifest.record_snapshot(snap.model_copy(update=changes))


def context(spec=SPEC, manifest=None, limit=None):
    return CrawlContext(spec=spec, manifest=manifest or FakeManifest(), raw=FakeRawStore(), now=T0, limit=limit)


class TestOecdCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return OecdSdmxCrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher(routes(), now=lambda: T0)


def test_requests_follow_the_scope_on_the_official_host():
    assert discover_crawlers()["oecd_sdmx"] is OecdSdmxCrawler
    fetcher, steps = run(FakeManifest(), FakeRawStore(), routes())
    assert fetcher.requests == ALL  # constraint → codelists → its data, then the revision editions
    assert [s.task.meta["request_type"] for s in steps] == [
        "constraint", "structure", "data", "constraint", "structure", "data", "edition", "edition"]
    assert all(urlsplit(url).hostname == "sdmx.oecd.org" for url in fetcher.requests)
    assert all("format=csv" in url for url in fetcher.requests if "/data/" in url)
    assert not any("_CPC" in url or "PPP" in url for url in fetcher.requests)
    data = steps[2].task
    assert data.url == NAMAIN_URL  # the data request itself is unchanged
    assert data.meta["valid_from"] == RECORDED_VALID_FROM and data.meta["id"] == NAMAIN and data.meta["version"] == "2.0"
    assert data.meta["code_labels"]["TRANSACTION"]["B1GQ"] == "Gross domestic product"
    assert steps[6].task.meta["edition"] == "201901" and not steps[6].task.headers


def test_unchanged_contentconstraint_sends_no_data_request():
    manifest, raw = FakeManifest(), FakeRawStore()
    run(manifest, raw, routes())
    # Same Last-Modified as recorded: the fake server answers 304 and the stored constraint is reused.
    fetcher, steps = run(manifest, raw, routes(), now=T0 + timedelta(days=7))
    assert fetcher.requests == CONSTRAINTS
    assert {s.result.outcome for s in steps} == {FetchOutcome.not_modified}
    # Server without conditional support (200, same bytes): still no data request.
    fetcher, steps = run(manifest, raw, routes(last_modified=False), now=T0 + timedelta(days=14))
    assert fetcher.requests == CONSTRAINTS
    assert {s.result.outcome for s in steps} == {FetchOutcome.ok}


def test_http_last_modified_is_not_the_release_marker():
    manifest, raw = FakeManifest(), FakeRawStore()
    run(manifest, raw, routes())
    changed = {url: FakeResponse(200, r.body, {**r.headers, "last-modified": "2030-01-01"}) for url, r in routes().items()}
    fetcher, _ = run(manifest, raw, changed, now=T0 + timedelta(days=7))
    assert fetcher.requests == CONSTRAINTS  # headers moved, validFrom did not


def test_changed_valid_from_fetches_only_that_dataflow_again():
    manifest, raw = FakeManifest(), FakeRawStore()
    run(manifest, raw, routes())
    later = T0 + timedelta(days=7)
    fetcher, steps = run(manifest, raw, routes(last_modified=False, valid_from=NEW_VALID_FROM), now=later)
    assert fetcher.requests == [CONSTRAINTS[0], STRUCTURES[0], NAMAIN_URL, CONSTRAINTS[1]]
    assert steps[2].task.meta["valid_from"] == NEW_VALID_FROM
    # The new marker is stored with the data snapshot, so the next run compares against it.
    fetcher, _ = run(manifest, raw, routes(last_modified=False, valid_from=NEW_VALID_FROM), now=later + timedelta(days=7))
    assert fetcher.requests == CONSTRAINTS


def test_304_on_the_data_does_not_count_as_fetched_for_the_new_valid_from():
    manifest, raw = FakeManifest(), FakeRawStore()
    run(manifest, raw, routes())
    # validFrom moved, but the recorded Last-Modified of the data is old, so the fake server answers 304.
    moved = routes() | {CONSTRAINTS[0]: FakeResponse(200, constraint_bytes(NAMAIN, NEW_VALID_FROM), {})}
    for week in (1, 2):
        fetcher, steps = run(manifest, raw, moved, now=T0 + timedelta(days=7 * week))
        assert fetcher.requests == [CONSTRAINTS[0], STRUCTURES[0], NAMAIN_URL, CONSTRAINTS[1]]  # asked again, not marked done
        assert steps[2].result.outcome is FetchOutcome.not_modified
        assert steps[2].task.meta["valid_from"] == NEW_VALID_FROM


def test_data_fetch_that_failed_is_retried_although_the_constraint_is_unchanged():
    manifest, raw = FakeManifest(), FakeRawStore()
    fetcher, steps = run(manifest, raw, routes(data_status=503))
    assert steps[2].result.outcome is FetchOutcome.http_error
    fetcher, _ = run(manifest, raw, routes(), now=T0 + timedelta(days=7))
    assert fetcher.requests == [CONSTRAINTS[0], STRUCTURES[0], NAMAIN_URL, CONSTRAINTS[1]]  # editions and HOU_EAR already in


def test_failed_constraint_does_not_schedule_its_data():
    responses = routes() | {CONSTRAINTS[0]: FakeResponse(503)}
    fetcher, _ = run(FakeManifest(), FakeRawStore(), responses)
    assert fetcher.requests == [CONSTRAINTS[0], CONSTRAINTS[1], STRUCTURES[1], HOU_URL, *EDITIONS]


def test_failed_structure_does_not_schedule_its_data():
    responses = routes() | {STRUCTURES[0]: FakeResponse(503)}
    fetcher, _ = run(FakeManifest(), FakeRawStore(), responses)
    assert NAMAIN_URL not in fetcher.requests and HOU_URL in fetcher.requests


def test_ingested_editions_are_not_requested_again_but_new_ones_are():
    manifest, raw = FakeManifest(), FakeRawStore()
    run(manifest, raw, routes())
    new = edition_url("201903")
    flows = [*FLOWS[:2], {**FLOWS[2], "editions": ["201901", "201902", "201903"]}]
    spec = SPEC.model_copy(update={"scope": {"raw_retention_days": 2, "dataflows": flows}})
    # The body is only routed here; this test never parses it.
    responses = routes() | {new: FakeResponse(200, edition_bytes("201902"))}
    fetcher, _ = run(manifest, raw, responses, now=T0 + timedelta(days=7), spec=spec)
    assert fetcher.requests == [*CONSTRAINTS, new]


def test_limit_applies_to_planned_tasks():
    assert len(list(OecdSdmxCrawler().plan(context(limit=1)))) == 1
    assert not list(OecdSdmxCrawler().plan(context(limit=0)))


# ---- 60 requests per hour --------------------------------------------------------------------------------------


def test_budget_is_60_per_hour():
    assert MAX_REQUESTS_PER_HOUR == 60


def test_no_request_once_60_were_made_in_the_last_hour():
    manifest = FakeManifest()
    seed(manifest, 60)
    assert not list(OecdSdmxCrawler().plan(context(manifest=manifest)))
    fetcher, steps = run(manifest, FakeRawStore(), routes())
    assert fetcher.requests == [] and steps == []


def test_budget_stops_the_run_exactly_at_60_without_failing():
    manifest, raw = FakeManifest(), FakeRawStore()
    seed(manifest, 57)
    fetcher, steps = run(manifest, raw, routes())
    # 58th: constraint; 59th: codelists; 60th: its data. The rest waits for the next hour.
    assert fetcher.requests == [CONSTRAINTS[0], STRUCTURES[0], NAMAIN_URL]
    assert all(s.result.outcome is FetchOutcome.ok for s in steps)
    seed_58 = FakeManifest()
    seed(seed_58, 58)
    fetcher, _ = run(seed_58, FakeRawStore(), routes())
    # Codelists + data would be the 60th and 61st: neither is fetched; the next constraint check is the 60th.
    assert fetcher.requests == CONSTRAINTS


def test_budget_carries_over_between_runs_through_the_manifest():
    manifest, raw = FakeManifest(), FakeRawStore()
    run(manifest, raw, routes())  # 8 requests recorded at T0
    seed(manifest, 50, age=timedelta(minutes=5))
    fetcher, _ = run(manifest, raw, routes(), now=T0 + timedelta(minutes=30))
    assert fetcher.requests == CONSTRAINTS  # 58 used; both constraint checks fit, nothing more is planned
    fetcher, _ = run(manifest, raw, routes(), now=T0 + timedelta(minutes=31))
    assert fetcher.requests == []  # 60 used
    fetcher, _ = run(manifest, raw, routes(), now=T0 + timedelta(minutes=70))
    assert fetcher.requests == CONSTRAINTS  # the window has moved past the seeded and first-run requests


def test_requests_older_than_an_hour_do_not_count():
    manifest = FakeManifest()
    seed(manifest, 60, age=timedelta(minutes=61))
    fetcher, _ = run(manifest, FakeRawStore(), routes())
    assert fetcher.requests == ALL


def test_failures_and_304s_count_and_a_429_closes_the_window():
    manifest = FakeManifest()
    seed(manifest, 59, outcome=FetchOutcome.http_error, http_status=503, error="HTTP 503", sha256=None, size=None)
    fetcher, _ = run(manifest, FakeRawStore(), routes())
    assert fetcher.requests == [CONSTRAINTS[0]]  # 59 failures + this constraint check = 60: no data request
    blocked = FakeManifest()
    seed(blocked, 1, outcome=FetchOutcome.http_error, http_status=429, error="HTTP 429", sha256=None, size=None)
    assert not list(OecdSdmxCrawler().plan(context(manifest=blocked)))


# ---- scope ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("changes", [
    {"query": "A.X/EL"}, {"query": "A.X?x=1"}, {"query": "A.%2f"}, {"query": "A. X"}, {"query": ""}, {"query": None},
    {"query": "A.JPN.XDC+USD_PPP"},  # PPP basis
    {"id": "DSD_NAMAIN10@DF_TABLE1_EXPENDITURE_CPC"},  # PPP-based dataflow
    {"id": "DF_TABLE1_EXPENDITURE"}, {"agency": "OECD/SDD"}, {"version": "2.x"}, {"version": 2.0},
    {"startPeriod": "20"}, {"startPeriod": "2025", "endPeriod": "2024"}, {"endPeriod": 2024},
    {"editions": ["2019-01"]}, {"editions": []}, {"editions": ["201901", "201901"]},
])
def test_invalid_scope_fails_before_the_first_request(changes):
    flows = [{**FLOWS[0], **changes}, *FLOWS[1:]]
    spec = SPEC.model_copy(update={"scope": {"dataflows": flows}})
    with pytest.raises(ValueError):
        next(iter(OecdSdmxCrawler().plan(context(spec))))


@pytest.mark.parametrize("dataflows", [None, [], ["x"], [FLOWS[0], FLOWS[0]]])
def test_dataflows_must_be_a_unique_non_empty_list_of_objects(dataflows):
    spec = SPEC.model_copy(update={"scope": {"dataflows": dataflows}})
    with pytest.raises(ValueError):
        next(iter(OecdSdmxCrawler().plan(context(spec))))


# ---- the registered source --------------------------------------------------------------------------------------


def test_registered_source_matches_the_agreed_scope_and_license():
    assert (REAL.kind, REAL.schedule, REAL.base_url, REAL.allowed_hosts) == (
        "oecd_sdmx", "weekly", "https://sdmx.oecd.org/public/rest/", ["sdmx.oecd.org"])
    assert REAL.rate_per_sec <= 1 / 60 and REAL.needs_key is None and REAL.key_header is None
    lic = REAL.license
    assert lic.url == "https://www.oecd.org/en/about/terms-conditions.html" and "OECD" in lic.attribution
    assert (lic.usage_scope, lic.redistribution, lic.outflow, lic.cloud_model_ok) == (
        UsageScope.internal_only, "unverified", Outflow.no_outflow, False)
    flows = {f["id"]: f for f in REAL.scope["dataflows"]}
    assert set(flows) == {NAMAIN, HOU, STES}
    assert flows[NAMAIN] | {} == {
        "id": NAMAIN, "agency": "OECD.SDD.NAD", "version": "2.0", "startPeriod": "2000", "endPeriod": "2024",
        "query": "A.JPN+USA+DEU+FRA+GBR.S1.S1.B1GQ._Z._Z._Z.XDC+USD_EXC.V.N.T0102"}
    assert (flows[HOU]["agency"], flows[HOU]["version"], flows[HOU]["query"]) == ("OECD.SDD.TPS", "1.0", "JPN.EAR.IX.N.C.S1.M")
    assert (flows[STES]["agency"], flows[STES]["version"], flows[STES]["query"]) == ("OECD.SDD.STES", "4.0", "JPN.M.H_EARN.IX.C")
    assert "AV_AN_WAGE" not in str(REAL.scope) and "_CPC" not in str(REAL.scope)


def test_registered_source_plans_constraints_and_editions_only():
    urls = [t.url for t in OecdSdmxCrawler().plan(context(REAL))]
    assert urls[:2] == CONSTRAINTS
    assert urls[2].startswith(BASE + "data/OECD.SDD.STES,DSD_STES_REVISIONS@DF_STES_REVISIONS,4.0/JPN.M.H_EARN.IX.C.")
    assert all(urlsplit(u).hostname == "sdmx.oecd.org" and "_CPC" not in u for u in urls)
    assert len(urls) == 2 + len(REAL.scope["dataflows"][2]["editions"]) <= MAX_REQUESTS_PER_HOUR
