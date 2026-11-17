"""UN Comtrade crawler: getDA first, one data request per reporter-partner-year, key only in the request header."""

from __future__ import annotations

import secrets
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest

from datumoj.rikolto.core.contracts import CrawlContext, discover_crawlers
from datumoj.rikolto.core.http import PoliteHttpFetcher
from datumoj.rikolto.core.models import FetchOutcome, Outflow, UsageScope
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.core.registry_export import export_registry
from datumoj.rikolto.core.runner import CrawlDeps, run_crawl
from datumoj.rikolto.core.schedule import next_due
from datumoj.rikolto.crawlers.un_comtrade import UnComtradeCrawler
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse, drive_crawler
from datumoj.rikolto.testing.samples import T0
from tests.parsers.test_un_comtrade import (
    BASE,
    data_bytes,
    data_url,
    empty_release_bytes,
    release_bytes,
    release_url,
)

ROOT = Path(__file__).parents[2]
KEY_ENV = "COMTRADE_API_KEY"
KEY_HEADER = "Ocp-Apim-Subscription-Key"
YAML = load_registry(ROOT / "sources").get("intl-comtrade")
assert YAML is not None
# core/registry.py rejects `key_header: Ocp-Apim-Subscription-Key` in a source yaml (its plaintext-key check
# wants every field named *key* to start with "env:"), so the yaml cannot carry it yet; see summary.md.
# Everything below runs on the yaml's spec plus the header the source is meant to declare.
REAL = YAML.model_copy(update={"key_header": YAML.key_header or KEY_HEADER})
PAIRS = [(842, 156), (276, 251), (392, 410)]
REPORTERS = [842, 156, 276, 251, 392, 410]  # plan order: the first country of each pair, then its partner
PARTNER = {a: b for a, b in PAIRS} | {b: a for a, b in PAIRS}
YEARS = (2022, 2023)
SPEC = REAL.model_copy(update={"scope": {**REAL.scope, "start_year": 2022, "end_year": 2023}})
LATER = T0 + timedelta(days=7)


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    key = secrets.token_hex(16)
    monkeypatch.setenv(KEY_ENV, key)
    return key


def expected_requests(skip=()):
    """Every getDA is followed by the data request(s) it triggered (follow-up tasks go first)."""
    urls = []
    for reporter in REPORTERS:
        for year in YEARS:
            urls.append(release_url(reporter, year))
            if (reporter, year) not in skip:
                urls.append(data_url(reporter, PARTNER[reporter], year))
    return urls


def routes(*, revised=(), empty=(), data_status=None):
    """revised / empty: (reporter, year) whose getDA moved / lists nothing; data_status: {(reporter, year): status}."""
    out = {}
    for reporter in REPORTERS:
        for year in YEARS:
            if (reporter, year) in empty:
                release = empty_release_bytes()
            else:
                release = release_bytes(reporter, year, revised=(reporter, year) in revised)
            out[release_url(reporter, year)] = FakeResponse(200, release)
            status = (data_status or {}).get((reporter, year), 200)
            out[data_url(reporter, PARTNER[reporter], year)] = FakeResponse(
                status, data_bytes(reporter, PARTNER[reporter], year) if status == 200 else b"")
    return out


def run(manifest, raw, responses, *, now=T0, spec=SPEC, limit=None):
    fetcher = FakeHttpFetcher(responses, now=lambda: now)
    steps = drive_crawler(UnComtradeCrawler(), spec, fetcher, manifest=manifest, raw=raw, now=now, limit=limit)
    return fetcher, steps


def context(spec=SPEC, limit=None):
    return CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0, limit=limit)


def first_task(spec):
    return next(iter(UnComtradeCrawler().plan(context(spec))))


class TestUnComtradeCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return UnComtradeCrawler()

    @pytest.fixture
    def spec(self):
        return SPEC

    @pytest.fixture
    def make_fetcher(self):
        return lambda: FakeHttpFetcher(routes(), now=lambda: T0)


def test_requests_follow_the_scope_on_the_official_host():
    assert discover_crawlers()["un_comtrade"] is UnComtradeCrawler
    fetcher, steps = run(FakeManifest(), FakeRawStore(), routes())
    assert fetcher.requests == expected_requests()
    assert all(urlsplit(url).hostname == "comtradeapi.un.org" for url in fetcher.requests)
    assert not any("preview" in url or "comtradeplus" in url for url in fetcher.requests)
    assert [s.task.meta["request_type"] for s in steps] == ["release", "data"] * 12
    data = [s.task for s in steps if s.task.meta["request_type"] == "data"]
    assert len(data) == 12 and all(not t.headers for t in data)
    for url in (t.url for t in data):
        assert "/data/v1/get/C/A/HS?" in url
        assert url.endswith("flowCode=X,M&cmdCode=TOTAL&partner2Code=0&customsCode=C00&motCode=0")
    first = data[0]
    assert first.meta["reporter"] == 842 and first.meta["partner"] == 156 and first.meta["year"] == 2022
    assert first.meta["releases"] == [{
        "period": 2022, "classificationCode": "H6", "datasetCode": 208422022012021, "datasetChecksum": 100842,
        "lastReleased": "2024-03-01T00:00:00"}]


def test_comtrade_codes_are_used_not_m49():
    fetcher, _ = run(FakeManifest(), FakeRawStore(), routes())
    text = "\n".join(fetcher.requests)
    assert "reporterCode=251&" in text and "partnerCode=251&" in text and "reporterCode=842&" in text
    assert "Code=250" not in text and "Code=840" not in text


def test_the_key_is_never_in_a_task_url(api_key):
    fetcher, steps = run(FakeManifest(), FakeRawStore(), routes())
    for step in steps:
        assert step.task.url == step.task.redacted_url and api_key not in step.task.url
        assert step.snapshot.resource_url == step.task.url
    assert not any("key" in url.lower() for url in fetcher.requests)


def test_unchanged_checksum_sends_no_data_request():
    manifest, raw = FakeManifest(), FakeRawStore()
    run(manifest, raw, routes())
    fetcher, steps = run(manifest, raw, routes(), now=LATER)
    assert fetcher.requests == [release_url(r, y) for r in REPORTERS for y in YEARS]
    assert all(s.task.meta["request_type"] == "release" for s in steps)


def test_changed_checksum_fetches_only_that_reporter_year_and_is_then_settled():
    manifest, raw = FakeManifest(), FakeRawStore()
    run(manifest, raw, routes())
    moved = routes(revised={(842, 2023)})
    fetcher, steps = run(manifest, raw, moved, now=LATER)
    assert [u for u in fetcher.requests if "/data/" in u] == [data_url(842, 156, 2023)]
    data, = (s for s in steps if s.task.meta["request_type"] == "data")
    assert data.task.meta["releases"][0]["datasetChecksum"] == 999999
    # The new checksum is stored with that data snapshot, so the next run compares against it.
    fetcher, _ = run(manifest, raw, moved, now=LATER + timedelta(days=7))
    assert not [u for u in fetcher.requests if "/data/" in u]


def test_a_failed_data_fetch_is_retried_although_getda_is_unchanged():
    manifest, raw = FakeManifest(), FakeRawStore()
    _, steps = run(manifest, raw, routes(data_status={(251, 2022): 503}))
    assert [s.result.outcome for s in steps if s.task.url == data_url(251, 276, 2022)] == [FetchOutcome.http_error]
    fetcher, _ = run(manifest, raw, routes(), now=LATER)
    assert [u for u in fetcher.requests if "/data/" in u] == [data_url(251, 276, 2022)]


def test_a_year_that_getda_does_not_list_sends_no_data_request_and_is_not_a_failure():
    skip = {(842, 2023), (392, 2022)}
    fetcher, steps = run(FakeManifest(), FakeRawStore(), routes(empty=skip))
    assert fetcher.requests == expected_requests(skip)
    assert all(s.result.outcome is FetchOutcome.ok for s in steps)


def test_a_failed_getda_does_not_schedule_its_data():
    responses = routes() | {release_url(156, 2023): FakeResponse(503)}
    fetcher, _ = run(FakeManifest(), FakeRawStore(), responses)
    assert data_url(156, 842, 2023) not in fetcher.requests
    assert data_url(156, 842, 2022) in fetcher.requests and data_url(410, 392, 2023) in fetcher.requests


def test_limit_applies_to_planned_getda_tasks():
    assert len(list(UnComtradeCrawler().plan(context(limit=3)))) == 3
    assert not list(UnComtradeCrawler().plan(context(limit=0)))


# ---- the key ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, ""])
def test_missing_key_fails_the_plan_naming_only_the_variable(monkeypatch, value):
    monkeypatch.delenv(KEY_ENV)
    if value is not None:
        monkeypatch.setenv(KEY_ENV, value)
    with pytest.raises(ValueError) as caught:
        next(iter(UnComtradeCrawler().plan(context())))
    assert str(caught.value) == f"{KEY_ENV} 必须是非空字符串"
    fetcher = FakeHttpFetcher(routes(), now=lambda: T0)
    with pytest.raises(ValueError):
        drive_crawler(UnComtradeCrawler(), SPEC, fetcher, now=T0)
    assert fetcher.requests == []


def run_real(spec, handler, *, manifest=None, raw=None):
    """The real runner and PoliteHttpFetcher; ``handler`` is the whole network (httpx.MockTransport)."""
    manifest, raw = manifest or FakeManifest(), raw or FakeRawStore()
    with PoliteHttpFetcher.from_spec(
        spec, transport=httpx.MockTransport(handler), now=lambda: T0, clock=lambda: 0.0, sleep=lambda _: None,
    ) as fetcher:
        report = run_crawl(spec, UnComtradeCrawler(), CrawlDeps(
            raw=raw, manifest=manifest, fetcher=fetcher, now=lambda: T0, clock=lambda: 0.0, sleep=lambda _: None))
    return report, manifest


def test_missing_key_stops_the_run_without_any_request(monkeypatch):
    monkeypatch.delenv(KEY_ENV)
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(404, stream=httpx.ByteStream(b""))

    report, manifest = run_real(SPEC, handler)
    assert seen == [] and manifest.snapshots_for(SPEC.id) == []
    assert report.failures == {"plan_error": 1}
    assert report.stop_reason == f"plan 失败：ValueError: {KEY_ENV} 必须是非空字符串"


def test_a_source_without_key_header_fails_the_run_instead_of_asking_without_a_key():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(404, stream=httpx.ByteStream(b""))

    report, manifest = run_real(SPEC.model_copy(update={"key_header": None}), handler)  # the yaml as committed today
    assert seen == [] and manifest.snapshots_for(SPEC.id) == []
    assert report.failures == {"plan_error": 1} and "key_header" in report.stop_reason


def test_key_goes_in_the_request_header_only(api_key):
    seen = []
    table = {url: r.body for url, r in routes().items()}

    def handler(request):
        seen.append((str(request.url), request.url.host, request.headers.get(KEY_HEADER)))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "application/json"},
                              stream=httpx.ByteStream(table[str(request.url)]))

    report, manifest = run_real(SPEC, handler)
    assert not report.failures and report.stop_reason is None, report
    pages = [(url, key) for url, _, key in seen if not url.endswith("/robots.txt")]
    assert [url for url, _ in pages] == expected_requests()
    assert {key for _, key in pages} == {api_key}  # every data and getDA request carries it
    assert all(api_key not in url for url, _, _ in seen)
    assert [key for url, _, key in seen if url.endswith("/robots.txt")] in ([], [None])  # never sent to robots.txt
    assert not any("preview" in url for url, _, _ in seen)
    # Nothing that is stored or reported contains the key.
    stored = [repr(s) for s in manifest.snapshots_for(SPEC.id)]
    stored += [repr(manifest.snapshot_meta(s.snapshot_id)) for s in manifest.snapshots_for(SPEC.id)]
    assert len(stored) == 48 and not any(api_key in text for text in stored) and api_key not in repr(report)


def test_a_401_from_the_server_is_a_recorded_failure_not_a_preview_fallback():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(401, stream=httpx.ByteStream(b'{"statusCode": 401, "message": "Access denied"}'))

    report, manifest = run_real(SPEC, handler)
    assert not any("preview" in path for path in seen)
    assert report.failures == {"http_error": 12}  # every getDA fails; no data request follows
    assert {s.outcome for s in manifest.snapshots_for(SPEC.id)} == {FetchOutcome.http_error}


# ---- scope ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("changes", [
    {"pairs": [[840, 156]]}, {"pairs": [[276, 250]]},  # M49 codes, not Comtrade's
    {"pairs": []}, {"pairs": None}, {"pairs": [[842]]}, {"pairs": [[842, 842]]}, {"pairs": [[842, 156], [156, 842]]},
    {"pairs": [[842, "156"]]}, {"pairs": [[842, 0]]}, {"pairs": [[842, 1000]]}, {"pairs": [[842, True]]},
    {"start_year": 2025, "end_year": 2024}, {"start_year": "2015"}, {"end_year": None}, {"start_year": 1900},
    {"flows": []}, {"flows": ["X", "X"]}, {"flows": ["X", "RX"]}, {"flows": "X,M"},
    {"cmdCode": "0101"}, {"cmdCode": None},
])
def test_invalid_scope_fails_before_the_first_request(changes):
    spec = SPEC.model_copy(update={"scope": {**SPEC.scope, **changes}})
    with pytest.raises(ValueError):
        next(iter(UnComtradeCrawler().plan(context(spec))))


def test_a_reporter_in_two_pairs_gets_one_getda_and_both_partners():
    spec = SPEC.model_copy(update={"scope": {**SPEC.scope, "pairs": [[842, 156], [842, 124]], "start_year": 2023}})
    reporters = [t.meta["reporter"] for t in UnComtradeCrawler().plan(context(spec))]
    assert reporters == [842, 156, 124]
    assert first_task(spec).meta["partners"] == [156, 124]


# ---- the registered source --------------------------------------------------------------------------------------


def test_registered_source_matches_the_agreed_scope_and_license():
    assert (REAL.kind, REAL.schedule, REAL.base_url, REAL.allowed_hosts) == (
        "un_comtrade", "manual", BASE, ["comtradeapi.un.org"])
    assert (YAML.needs_key, YAML.rate_per_sec) == (f"env:{KEY_ENV}", 0.5)
    assert YAML.key_header in (None, KEY_HEADER)  # None until core/registry.py accepts key_header in yaml
    lic = REAL.license
    assert lic.url == "https://comtradeplus.un.org/en/license-agreement" and lic.attribution == "Source: UN Comtrade."
    assert (lic.usage_scope, lic.redistribution, lic.outflow, lic.cloud_model_ok) == (
        UsageScope.internal_only, "unverified", Outflow.no_outflow, False)
    assert REAL.scope["pairs"] == [[842, 156], [276, 251], [392, 410]]
    assert (REAL.scope["start_year"], REAL.scope["end_year"], REAL.scope["flows"], REAL.scope["cmdCode"]) == (
        2015, 2024, ["X", "M"], "TOTAL")
    assert next_due(REAL.schedule, None, T0) is None  # manual: never due, only a person starts it


def test_registered_source_plans_getda_for_every_reporter_and_year_within_max_files():
    tasks = list(UnComtradeCrawler().plan(context(REAL)))
    assert len(tasks) == 60 and len({t.url for t in tasks}) == 60
    assert {t.meta["reporter"] for t in tasks} == set(REPORTERS)
    assert {t.meta["year"] for t in tasks} == set(range(2015, 2025))
    assert all(t.url.startswith(f"{BASE}public/v1/getDA/C/A/HS?reporterCode=") for t in tasks)
    assert 60 + 60 <= REAL.max_files  # a getDA and a data request for each reporter-year


def test_catalog_file_matches_the_export(tmp_path):
    export_registry(ROOT / "sources", tmp_path)
    assert (tmp_path / "intl-comtrade.json").read_bytes() == (ROOT / "catalog/intl-comtrade.json").read_bytes()
