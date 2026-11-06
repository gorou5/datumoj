"""Audit 139: aborted crawls, per-file ratios and nonblocking Federal Register replay."""

import json
from collections import Counter
from datetime import timedelta

import pytest

from datumoj.rikolto.core.contracts import CrawlContext
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.core.runner import CrawlDeps, CrawlReport, run_crawl
from datumoj.rikolto.crawlers.fedreg import FedregCrawler, _progress
from datumoj.rikolto.pipeline import ParseSummary
from datumoj.rikolto.testing.fakes import (
    FakeCrawler,
    FakeHttpFetcher,
    FakeManifest,
    FakeRawStore,
    FakeRegistry,
    FakeResponse,
)
from datumoj.rikolto.testing.samples import T0, make_snapshot
from tests.crawlers.test_fedreg import SPEC
from tests.regressions.test_audit_136 import detail, search, xml
from tests.regressions.test_audit_138 import source_run
from tests.test_cloud_run import invoke
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Offline cloud fixture.
from tests.test_pipeline_retry import FixedParser, build
from tests.test_source_issues import fake_gh as fake_gh  # noqa: PLC0414 -- Local fake executable.
from tests.test_source_issues import run_script


def test_fedreg_interface_abort_fails_cli_and_opens_issue_after_two_rounds(world, fake_gh, httpserver):
    class Aborted(FakeCrawler):
        def after_fetch(self, task, result, ctx):
            raise ValueError("Synthetic changed search envelope")

    spec = world.specs[0].model_copy(update={"id": "us-fedreg"})
    world.overrides.update(registry=FakeRegistry([spec]), crawlers={"fake": Aborted})
    for attempt in (1, 2):
        httpserver.clear()
        httpserver.expect_request("/source0.txt").respond_with_data(f"Synthetic changed search {attempt}", content_type="text/plain")
        result, _ = invoke(world, f"aborted-{attempt}")
        source, = result["sources"]
        assert (source["new"], source["parsed"], source["failures"]) == (1, 1, {"crawl_failed": 1})
        assert source["status"] == "failed" and source["exit_status"] == result["exit_status"] == 1
        row, = result["source_health"]
        assert row["status"] == "failed" and row["consecutive_failures"] == attempt
        assert run_script(world.tmp, result["source_health"], result["generation"]).returncode == 0
        issues = json.loads(fake_gh.read_text())["issues"]
        assert len(issues) == attempt - 1
        world.clock[0] += timedelta(hours=1)
    assert issues[0]["title"] == "[source-broken] us-fedreg"


@pytest.mark.parametrize("reason", ["plan_error", "after_fetch_error"])
def test_abort_cannot_be_offset_by_success_or_old_failed_urls(reason):
    pipeline, _ = build(FixedParser)
    url = "https://example.go.jp/permanent"
    pipeline.manifest.record_snapshot(make_snapshot(url=url, snapshot_id="old", outcome=FetchOutcome.http_error))

    def crawl(*args):
        pipeline.manifest.record_snapshot(make_snapshot(url=url, snapshot_id="retry", outcome=FetchOutcome.http_error))
        return CrawlReport(source_id="jp-test", new=100, failures={reason: 1})

    result, ledger = source_run(pipeline=pipeline, crawl=crawl, parse=lambda *a: ParseSummary(parsed=["ok"] * 100))
    assert result["status"] == "failed" and result["exit_status"] == 1
    assert ledger[-1]["status"] == "failed"


@pytest.mark.parametrize("reason,new,parsed,failures,status", [
    ("http_error", 1, 1, 2, "failed"),  # Two dead links among three new URLs.
    ("http_error", 2, 2, 1, "degraded"),
    ("http_error", 119, 119, 50, "degraded"),  # jp-egov-data counts.
    ("http_error", 1, 1, 1, "degraded"),  # Exactly half.
    ("network_error", 2, 2, 1, "degraded"),  # Item errors are not aborts.
])
def test_fetch_ratio_counts_files_once(reason, new, parsed, failures, status):
    result, _ = source_run(crawl=lambda *a: CrawlReport(
        source_id="jp-test", new=new, failures={reason: failures}),
        parse=lambda *a: ParseSummary(parsed=["ok"] * parsed))
    assert result["status"] == status and result["exit_status"] == int(status == "failed")


@pytest.mark.parametrize("count,status", [(1, "degraded"), (2, "failed")])
def test_parse_ratio_is_independent_of_fetch_successes(count, status):
    failures = {f"failed-{i}": "ParseError: synthetic_failure: invalid" for i in range(count)}
    result, _ = source_run(119, parse=lambda *a: ParseSummary(
        parsed=["ok"], failed=failures, newly_failed=list(failures)))
    assert result["status"] == status and result["exit_status"] == int(status == "failed")


@pytest.mark.parametrize("phase", ["crawl", "parse"])
def test_repeated_failures_cannot_dilute_new_failure_ratio(phase):
    pipeline, _ = build(FixedParser)
    for index in range(10):
        pipeline.manifest.record_snapshot(make_snapshot(
            url=f"https://example.go.jp/old-{index}", snapshot_id=f"old-{index}", outcome=FetchOutcome.http_error))

    def crawl(*args):
        for index in range(10):
            pipeline.manifest.record_snapshot(make_snapshot(
                url=f"https://example.go.jp/old-{index}", snapshot_id=f"retry-{index}", outcome=FetchOutcome.http_error))
        return CrawlReport(source_id="jp-test", new=1 if phase == "crawl" else 119,
                           failures={"http_error": 12} if phase == "crawl" else {})

    summary = ParseSummary(parsed=["ok"])
    if phase == "parse":
        summary.failed = {f"failure-{index}": "RuntimeError" for index in range(12)}
        summary.newly_failed = ["failure-0", "failure-1"]
    result, _ = source_run(pipeline=pipeline, crawl=crawl, parse=lambda *a: summary)
    assert result["status"] == "failed" and result["exit_status"] == 1


def fedreg_round(manifest, raw, day, documents, *, failed_role=None):
    now = T0 + timedelta(days=day)
    ctx = CrawlContext(SPEC, manifest, raw, now)
    crawler = FedregCrawler()
    tasks = list(crawler.plan(ctx))
    routes = {task.url: FakeResponse(200, search(documents)) for task in tasks if task.meta["role"] == "search"}
    for task in tasks:
        routes.setdefault(task.url, FakeResponse(404))
    for doc in documents:
        number = doc["document_number"]
        routes[SPEC.base_url + f"documents/{number}.json"] = FakeResponse(200, json.dumps(doc).encode())
        routes[doc["full_text_xml_url"]] = FakeResponse(200, xml(number))
    if failed_role:
        first = documents[0]
        url = (SPEC.base_url + f"documents/{first['document_number']}.json" if failed_role == "detail"
               else first["full_text_xml_url"])
        routes[url] = FakeResponse(404)
    fetcher = FakeHttpFetcher(routes)
    report = run_crawl(SPEC, crawler, CrawlDeps(
        manifest=manifest, raw=raw, fetcher=fetcher, now=lambda: now, clock=lambda: 0, sleep=lambda _: None))
    assert not ({"plan_error", "after_fetch_error"} & report.failures.keys())
    return ctx, fetcher.requests


@pytest.mark.parametrize("failed_role", ["detail", "xml"])
def test_permanent_failure_does_not_block_new_documents_or_advance_coverage(failed_role):
    manifest, raw, attempts = FakeManifest(), FakeRawStore(), Counter()
    docs = [detail("2026-1"), detail("2026-2")]
    for day in range(4):
        if day:
            docs.append({**detail(f"2026-{day + 2}"), "publication_date": (T0 + timedelta(days=day)).date().isoformat()})
        ctx, calls = fedreg_round(manifest, raw, day, docs, failed_role=failed_role)
        attempts.update(calls)
        covered, resume, _ = _progress(ctx, raw.get)
        assert covered is None and resume is not None
        newest = docs[-1]
        snap = manifest.last_success(newest["full_text_xml_url"])
        assert snap is not None and raw.get(snap.sha256) == xml(newest["document_number"])
    for doc in docs[1:]:
        assert attempts[SPEC.base_url + f"documents/{doc['document_number']}.json"] == 1
        assert attempts[doc["full_text_xml_url"]] == 1
    failed_url = (SPEC.base_url + "documents/2026-1.json" if failed_role == "detail" else docs[0]["full_text_xml_url"])
    assert attempts[failed_url] == 4
    assert sum("documents.json?" in url for url in attempts) == 4
    # A later complete but disjoint search must not erase the older gap.
    fedreg_round(manifest, raw, 4, docs[1:], failed_role=None)
    assert _progress(ctx, raw.get)[0] is None
    # When the old document recovers, all windows can finally advance.
    recovered, _ = fedreg_round(manifest, raw, 5, docs)
    assert _progress(recovered, raw.get)[:2] == ((T0 + timedelta(days=5)).date(), None)


def test_replay_skips_xml_body_and_shares_json_reads_across_progress_and_pending(monkeypatch):
    manifest, raw = FakeManifest(), FakeRawStore()
    docs = [detail("2026-1"), detail("2026-2")]
    fedreg_round(manifest, raw, 0, docs, failed_role="detail")
    reads = Counter()
    original_get = raw.get
    xml_sha = manifest.last_success(docs[1]["full_text_xml_url"]).sha256

    def get(sha):
        reads[sha] += 1
        assert sha != xml_sha
        return original_get(sha)

    monkeypatch.setattr(raw, "get", get)
    ctx = CrawlContext(SPEC, manifest, raw, T0 + timedelta(days=1))
    tasks = list(FedregCrawler().plan(ctx))
    assert [task.meta["role"] for task in tasks] == ["detail", "search"]
    assert reads and set(reads.values()) == {1}


@pytest.mark.parametrize("missing", ["snapshot", "sha"])
def test_xml_without_successful_snapshot_and_sha_is_still_pending(missing):
    manifest, raw = FakeManifest(), FakeRawStore()
    doc = detail("2026-1")
    # Record only discovery and detail, without a successful XML snapshot.
    spec = SPEC.model_copy(update={"max_files": 2})
    now = T0
    root = next(iter(FedregCrawler().plan(CrawlContext(spec, manifest, raw, now))))
    fetcher = FakeHttpFetcher({root.url: FakeResponse(200, search([doc])),
                               SPEC.base_url + "documents/2026-1.json": FakeResponse(200, json.dumps(doc).encode())})
    run_crawl(spec, FedregCrawler(), CrawlDeps(raw, manifest, fetcher, now=lambda: now, clock=lambda: 0, sleep=lambda _: None))
    if missing == "sha":
        manifest.record_snapshot(make_snapshot(
            source_id=SPEC.id, url=doc["full_text_xml_url"], outcome=FetchOutcome.not_modified, data=None),
            {"role": "xml", "run": now.isoformat()})
    ctx = CrawlContext(SPEC, manifest, raw, now + timedelta(days=1))
    covered, resume, _ = _progress(ctx, raw.get)
    assert covered is None and resume is not None
    assert next(iter(FedregCrawler().plan(ctx))).url == doc["full_text_xml_url"]
