"""Offline characterization only; no real HTTP, production data or runtime edits."""

from types import SimpleNamespace

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.core.runner import CrawlDeps, CrawlReport
from datumoj.rikolto.crawlers.ckan import CkanCrawler
from datumoj.rikolto.pipeline import ParseSummary, PipelineDeps
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeHttpFetcher,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeRawStore,
    FakeRegistry,
)
from datumoj.rikolto.testing.samples import make_snapshot, make_spec


@pytest.mark.parametrize(("previously_failed", "expected_status"), [
    (0, "failed"), (57, "failed"), (58, "degraded"), (64, "degraded"),
])
def test_old_failures_change_classification_not_reported_counts(previously_failed, expected_status):
    """Same 64 failures/6 successful fetches; vary only previous failure history."""
    spec = make_spec("jp-egov-data", kind="ckan", base_url="https://synthetic.example/api")
    manifest = FakeManifest()
    raw = FakeRawStore()
    for index in range(previously_failed):
        manifest.record_snapshot(make_snapshot(
            spec.id, url=f"https://synthetic.example/item/{index}",
            snapshot_id=f"old-{index}", outcome=FetchOutcome.http_error,
        ))

    def crawl(*args):
        for index in range(64):
            outcome = FetchOutcome.http_error if index < 47 else FetchOutcome.empty_body
            manifest.record_snapshot(make_snapshot(
                spec.id, url=f"https://synthetic.example/item/{index}",
                snapshot_id=f"new-{index}", outcome=outcome,
            ))
        return CrawlReport(
            source_id=spec.id, new=4, unchanged=2,
            failures={"http_error": 47, "empty_body": 17}, stop_reason="deadline",
        )

    pipeline = PipelineDeps(
        manifest=manifest, raw=raw, registry=FakeRegistry([spec]),
        chunks=FakeChunkStore(), keyword=FakeKeywordIndex(),
        observations=FakeObservationStore(), catalog=FakeCatalogStore(),
    )
    deps = SimpleNamespace(
        manifest=manifest, raw=raw, crawler_classes={"ckan": CkanCrawler},
        fetcher_for=lambda spec: FakeHttpFetcher({}), crawl_deps_type=CrawlDeps,
        run_crawl=crawl, pipeline_deps=pipeline,
        parse_pending=lambda *args: ParseSummary(parsed=[f"synthetic-{i}" for i in range(6)]),
    )
    rows = []
    result, _ = cloud._source(deps, spec, SimpleNamespace(append_run=rows.append), None)
    assert result["status"] == expected_status
    assert result["exit_status"] == int(expected_status == "failed")
    assert result["failures"] == {"crawl_failed": 64}
    assert result["source_health"]["categories"] == {"http_error": 47, "empty_body": 17}
    assert result["stop_reason"] == "deadline"
    assert rows[-1]["status"] == "incomplete"
    count, _ = cloud._failure_streak(rows, spec.id)
    assert count == int(expected_status == "failed")
