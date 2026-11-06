"""Source health uses first failures; all sources, parsers and gh are offline."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.core.budget import BudgetExceeded
from datumoj.rikolto.core.contracts import ParseError
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.core.runner import CrawlReport
from datumoj.rikolto.pipeline import ParseSummary, parse_pending
from datumoj.rikolto.testing.fakes import FakeParser
from datumoj.rikolto.testing.samples import make_snapshot
from tests.test_cloud_run import BODY, invoke
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Offline cloud fixture.
from tests.test_pipeline_retry import BrokenParser, FixedParser, build
from tests.test_source_issues import fake_gh as fake_gh  # noqa: PLC0414 -- Local fake executable.
from tests.test_source_issues import health, run_script


def source_run(successes=0, failures=0, *, pipeline=None, crawl=None, parse=None, stop=None):
    pipeline = pipeline or build(FixedParser)[0]
    spec = pipeline.registry.all()[0]
    report = CrawlReport(source_id=spec.id, new=successes,
                         failures={"http_error": failures} if failures else {}, stop_reason=stop)
    ledger = []
    deps = SimpleNamespace(
        manifest=pipeline.manifest, raw=pipeline.raw, pipeline_deps=pipeline,
        crawler_classes={spec.kind: object}, fetcher_for=lambda _: object(),
        crawl_deps_type=SimpleNamespace, run_crawl=crawl or (lambda *a: report),
        parse_pending=parse or (lambda *a: ParseSummary()),
    )
    result, _ = cloud._source(deps, spec, SimpleNamespace(append_run=ledger.append), None)
    return result, ledger


@pytest.mark.parametrize("successes,failures,status", [
    (2, 1, "degraded"), (1, 1, "degraded"), (1, 2, "failed"), (0, 1, "failed"),
])
def test_first_failure_ratio_and_zero_success(successes, failures, status):
    result, ledger = source_run(successes, failures)
    assert result["status"] == result["source_health"]["status"] == status
    assert result["exit_status"] == int(status == "failed")
    assert result["failures"] == {"crawl_failed": failures}
    assert result["source_health"]["categories"] == {"http_error": failures}
    if status == "degraded":
        assert ledger[-1]["item_failures"] == result["failures"]
        assert cloud._failure_streak(ledger, "jp-test") == (0, None)


@pytest.mark.parametrize("phase", ["crawl", "parse_index"])
def test_uncaught_exception_always_fails(phase):
    def crash(*args):
        raise RuntimeError("synthetic failure")

    result, _ = source_run(10, crawl=crash if phase == "crawl" else None,
                           parse=crash if phase == "parse_index" else None)
    assert result["status"] == "failed" and result["exit_status"] == 1
    assert result["source_health"]["phase"] == phase


def test_parse_success_does_not_offset_total_fetch_failure():
    summary = ParseSummary(parsed=["backlog"])
    result, _ = source_run(failures=1, parse=lambda *a: summary)
    assert result["status"] == "failed" and result["exit_status"] == 1


def test_old_fetch_failures_remain_visible_but_do_not_enter_ratio():
    pipeline, _ = build(FixedParser)
    for index in range(2):
        pipeline.manifest.record_snapshot(make_snapshot(
            url=f"https://example.go.jp/dead{index}", snapshot_id=f"old{index}",
            outcome=FetchOutcome.http_error,
        ))

    def crawl(*args):
        for index in range(2):
            pipeline.manifest.record_snapshot(make_snapshot(
                url=f"https://example.go.jp/dead{index}", snapshot_id=f"retry{index}",
                outcome=FetchOutcome.http_error,
            ))
        return CrawlReport(source_id="jp-test", unchanged=1, failures={"http_error": 2})

    result, _ = source_run(pipeline=pipeline, crawl=crawl)
    assert result["status"] == "degraded" and result["exit_status"] == 0
    assert result["failures"] == {"crawl_failed": 2}


def test_old_parse_failures_remain_visible_but_do_not_enter_ratio():
    pipeline, snap = build(BrokenParser)
    first = parse_pending(pipeline)
    assert first.newly_failed == [snap.snapshot_id]
    second = parse_pending(pipeline)
    assert second.failed == first.failed and second.newly_failed == []
    result, _ = source_run(1, 1, pipeline=pipeline, parse=lambda *a: second)
    assert result["status"] == "degraded"  # 2 failures / 3 attempts, only 1 new.
    assert result["failures"] == {"crawl_failed": 1, "parse_index_failed": 1}
    # A source with no success still fails, even when every failure is old.
    result, _ = source_run(pipeline=pipeline, parse=lambda *a: second)
    assert result["status"] == "failed"


class TimeoutParser(FakeParser):
    calls = 0

    def parse(self, snapshot, data, meta=None):
        type(self).calls += 1
        raise BudgetExceeded("duration_seconds")


def test_timeout_retries_once_per_context_per_round_then_recovers():
    TimeoutParser.calls = 0
    pipeline, snap = build(TimeoutParser)
    pipeline.manifest.record_snapshot(snap.model_copy(update={"snapshot_id": "same-context"}))
    first = parse_pending(pipeline)
    assert len(first.failed) == 1 and len(first.newly_failed) == 1
    assert TimeoutParser.calls == 1
    second = parse_pending(pipeline)
    assert len(second.failed) == 1 and second.newly_failed == []
    assert TimeoutParser.calls == 2
    pipeline.parsers = {("fake", "text/plain"): FixedParser}
    recovered = parse_pending(pipeline)
    assert recovered.retried and not recovered.failed
    result, _ = source_run(1, pipeline=pipeline, parse=lambda *a: recovered)
    assert result["source_health"]["status"] == "ok" and result["exit_status"] == 0


def test_first_timeout_recovers_on_the_very_next_round():
    pipeline, snap = build(TimeoutParser)
    first = parse_pending(pipeline)
    result, _ = source_run(pipeline=pipeline, parse=lambda *a: first)
    assert result["status"] == "failed" and result["exit_status"] == 1
    pipeline.parsers = {("fake", "text/plain"): FixedParser}
    second = parse_pending(pipeline)
    assert second.retried == [snap.snapshot_id] and second.failed == {}
    result, _ = source_run(pipeline=pipeline, parse=lambda *a: second)
    assert result["source_health"]["status"] == "ok" and result["exit_status"] == 0


@pytest.mark.parametrize("item", ["pages", "text_chars", "output_chunks"])
def test_deterministic_budget_failure_is_cached_without_recounting(item):
    class Limited(FakeParser):
        calls = 0

        def parse(self, snapshot, data, meta=None):
            type(self).calls += 1
            raise BudgetExceeded(item)

    pipeline, snap = build(Limited)
    first = parse_pending(pipeline)
    assert first.failed == {snap.snapshot_id: f"budget_exceeded:{item}"}
    second = parse_pending(pipeline)
    assert second.failed == first.failed and second.cached_failures == [snap.snapshot_id]
    assert second.newly_failed == [] and Limited.calls == 1
    result, _ = source_run(pipeline=pipeline, parse=lambda *a: second)
    assert result["failures"] == {} and result["exit_status"] == 0
    assert result["source_health"]["status"] == "ok"


def test_empty_content_is_not_a_failure_even_on_first_attempt():
    class Empty(FakeParser):
        def parse(self, snapshot, data, meta=None):
            raise ParseError("empty_content: HTML 页面正文少于 100 字")

    pipeline, snap = build(Empty)
    for _ in range(2):
        summary = parse_pending(pipeline)
        assert summary.empty_content == [snap.snapshot_id]
        assert summary.failed == {} and summary.newly_failed == []
        result, _ = source_run(pipeline=pipeline, parse=lambda *a, summary=summary: summary)
        assert result["exit_status"] == 0 and result["failures"] == {}
        assert result["source_health"]["status"] == "ok"


def test_retry_stops_at_existing_deadline_between_snapshots():
    timer = [0]

    class TimedOut(FakeParser):
        calls = 0

        def parse(self, snapshot, data, meta=None):
            type(self).calls += 1
            timer[0] += 2
            raise BudgetExceeded("duration_seconds")

    pipeline, snap = build(TimedOut)
    pipeline.manifest.record_snapshot(snap.model_copy(update={
        "snapshot_id": "other-context", "resource_url": "https://example.go.jp/other",
    }))
    parse_pending(pipeline)  # Both failures are now eligible for retry.
    timer[0] = 0
    TimedOut.calls = 0
    pipeline.clock = lambda: timer[0]
    pipeline.deadline = 1
    summary = parse_pending(pipeline)
    assert TimedOut.calls == 1 and summary.stop_reason == "deadline"
    assert len(summary.failed) == 1 and summary.newly_failed == []


def test_deadline_alone_remains_incomplete_and_successful_exit():
    result, ledger = source_run(stop="deadline")
    assert result["status"] == "completed" and result["exit_status"] == 0
    assert result["failures"] == {} and result["stop_reason"] == "deadline"
    assert result["source_health"]["status"] == "deadline"
    assert ledger[-1]["status"] == "incomplete"
    assert cloud._failure_streak(ledger, "jp-test") == (0, None)


def test_degraded_never_opens_issue_even_with_stale_streak(fake_gh, tmp_path):
    for count in (0, 5):
        assert run_script(tmp_path, [health("degraded", count)]).returncode == 0
    assert json.loads(fake_gh.read_text())["issues"] == []


def test_minority_dead_links_close_existing_issue_and_publish_success(world, httpserver, fake_gh):
    world.clock[0] -= timedelta(hours=4)  # Four hourly rounds must stay before publication's real clock.
    httpserver.clear()
    for index in range(3):
        httpserver.expect_request(f"/source{index}.txt").respond_with_data(
            BODY, status=503 if index == 0 else 200, content_type="text/plain",
        )
    for attempt in (1, 2):
        result, _ = invoke(world, f"failed-{attempt}")
        row = next(row for row in result["source_health"] if row["source_id"] == "jp-cloud0")
        assert result["exit_status"] == 1 and row["consecutive_failures"] == attempt
        assert run_script(world.tmp, result["source_health"], result["generation"]).returncode == 0
        world.clock[0] += timedelta(hours=1)
    assert len(json.loads(fake_gh.read_text())["issues"]) == 1
    httpserver.clear()
    for index in range(3):
        httpserver.expect_request(f"/source{index}.txt").respond_with_data(BODY, content_type="text/plain")
    urls = world.specs[0].scope["urls"]
    urls.extend([httpserver.url_for("/extra"), httpserver.url_for("/dead")])
    httpserver.expect_request("/extra").respond_with_data(BODY + b"extra", content_type="text/plain")
    httpserver.expect_request("/dead").respond_with_data("gone", status=404)
    for attempt in (1, 2):
        result, root = invoke(world, f"degraded-{attempt}")
        assert result["exit_status"] == 0 and result["failed_sources"] == []
        source = next(row for row in result["sources"] if row["source_id"] == "jp-cloud0")
        row = next(row for row in result["source_health"] if row["source_id"] == "jp-cloud0")
        assert source["status"] == row["status"] == "degraded"
        assert source["failures"] == {"crawl_failed": 1} and row["categories"] == {"http_error": 1}
        assert row["consecutive_failures"] == 0 and row["first_failure_at"] is None
        generation = world.managers[-1].list()[0]
        assert generation.last_successful_runs["jp-cloud0"].run_id == source["run_id"]
        ledger = [json.loads(line) for line in (root / "runs.jsonl").read_text().splitlines()]
        final = [entry for entry in ledger if entry.get("run_id") == source["run_id"]][-1]
        assert final["item_failures"] == source["failures"]
        assert run_script(world.tmp, result["source_health"], result["generation"]).returncode == 0
        world.clock[0] += timedelta(hours=1)
    issues = json.loads(fake_gh.read_text())["issues"]
    assert len(issues) == 1 and issues[0]["closed"]
    assert issues[0]["comments"] == ["已恢复（第 3 代）"]
