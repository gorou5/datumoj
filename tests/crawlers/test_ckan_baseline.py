from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from datumoj.rikolto.crawlers.ckan import CkanCrawler
from datumoj.rikolto.core.runner import CrawlDeps, run_crawl
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse, drive_crawler
from datumoj.rikolto.testing.samples import make_spec

API = "https://data.e-gov.go.jp/data/api/action"
SEARCH = API + "/package_search?q=%2A%3A%2A&rows={rows}&start={start}&sort=id%20asc"
ACTIVITY = API + "/recently_changed_packages_activity_list?limit=100"
RUN1 = datetime(2026, 10, 1, tzinfo=timezone.utc)
RUN2 = datetime(2026, 10, 2, tzinfo=timezone.utc)
RUN3 = datetime(2026, 10, 3, tzinfo=timezone.utc)


def _spec(*, rows: int = 2, max_files: int = 100, incremental: bool = True):
    return make_spec(
        source_id="jp-egov-ckan-baseline", kind="ckan", base_url=API,
        allowed_hosts=["data.e-gov.go.jp"], max_files=max_files,
        scope={"rows": rows, "incremental": incremental, "activity_limit": 100},
    )


def _page(ids: list[str]) -> bytes:
    packages = [{"id": pid, "resources": []} for pid in ids]
    return json.dumps({"success": True, "result": {"count": 999, "results": packages}}).encode()


def _activity(items: list[dict] | None = None) -> bytes:
    return json.dumps({"success": True, "result": items or []}).encode()


def _routes(pages: dict[int, list[str]], *, rows: int = 2) -> dict:
    return {SEARCH.format(rows=rows, start=start): _page(ids) for start, ids in pages.items()}


def _fetcher(routes: dict) -> FakeHttpFetcher:
    return FakeHttpFetcher(routes, now=lambda: RUN1)


def _roles(steps) -> list[str]:
    return [step.task.meta["role"] for step in steps]


def _run(manifest, raw, routes, *, when, max_files=100, limit=None, incremental=True):
    return drive_crawler(
        CkanCrawler(), _spec(max_files=max_files, incremental=incremental), _fetcher(routes), manifest=manifest, raw=raw,
        limit=limit, now=when,
    )


def test_complete_full_run_is_used_as_next_incremental_baseline():
    manifest, raw = FakeManifest(), FakeRawStore()
    full = _run(manifest, raw, _routes({0: ["p0", "p1"], 2: ["p2"], 3: []}), when=RUN1)
    assert _roles(full) == ["package_search"] * 3

    incremental = _run(manifest, raw, {ACTIVITY: _activity()}, when=RUN2)
    assert _roles(incremental) == ["activity"]
    assert incremental[0].task.meta["since"] == RUN1.isoformat()


def test_unchanged_search_pages_reuse_raw_body_and_complete_the_full_run():
    manifest, raw = FakeManifest(), FakeRawStore()
    routes = {
        SEARCH.format(rows=2, start=0): FakeResponse(200, _page(["p0", "p1"]), {"etag": "page-0"}),
        SEARCH.format(rows=2, start=2): FakeResponse(200, _page([]), {"etag": "page-2"}),
    }
    _run(manifest, raw, routes, when=RUN1)

    unchanged = _run(manifest, raw, routes, when=RUN2, incremental=False)
    assert [step.result.outcome for step in unchanged] == [FetchOutcome.not_modified] * 2
    next_run = _run(manifest, raw, {ACTIVITY: _activity()}, when=RUN3)
    assert _roles(next_run) == ["activity"]
    assert next_run[0].task.meta["since"] == RUN2.isoformat()


@pytest.mark.parametrize(
    ("max_files", "limit", "pages"),
    [
        (1, None, {0: ["p0"]}),
        (1, None, {0: ["p0", "p1"], 2: ["p2"]}),
        (100, 1, {0: ["p0"]}),
    ],
    ids=["max-files-short-page", "max-files-full-page", "limit"],
)
def test_interrupted_full_run_falls_back_to_full_with_reason(max_files, limit, pages):
    manifest, raw = FakeManifest(), FakeRawStore()
    first = _run(
        manifest, raw, _routes(pages),
        when=RUN1, max_files=max_files, limit=limit,
    )
    assert _roles(first) == ["package_search"]

    next_run = _run(manifest, raw, _routes({0: ["p0"], 1: []}), when=RUN2)
    search = next_run[0]
    assert search.task.meta["role"] == "package_search"
    assert "上次全量未完成" in search.task.meta["incremental_fallback"]
    assert "分页未走到末页" in search.task.meta["incremental_fallback"]
    assert manifest.snapshot_meta(search.snapshot.snapshot_id)["incremental_fallback"] == search.task.meta[
        "incremental_fallback"
    ]


def test_failed_search_page_makes_next_run_fall_back_to_full():
    manifest, raw = FakeManifest(), FakeRawStore()
    routes = _routes({0: ["p0", "p1"]})
    routes[SEARCH.format(rows=2, start=2)] = FakeResponse(503)
    failed_full = _run(manifest, raw, routes, when=RUN1)
    assert [step.result.outcome for step in failed_full] == [FetchOutcome.ok, FetchOutcome.http_error]

    next_run = _run(manifest, raw, _routes({0: ["p0"], 1: []}), when=RUN2)
    assert next_run[0].task.meta["role"] == "package_search"
    assert "分页请求失败" in next_run[0].task.meta["incremental_fallback"]


def test_fallback_reason_is_visible_in_dry_run_report():
    manifest, raw = FakeManifest(), FakeRawStore()
    _run(manifest, raw, _routes({0: ["p0", "p1"]}), when=RUN1, limit=1)

    report = run_crawl(
        _spec(), CkanCrawler(), CrawlDeps(raw=raw, manifest=manifest, fetcher=_fetcher({}), now=lambda: RUN2),
        dry_run=True,
    )
    assert "上次全量未完成" in report.planned[0].meta["incremental_fallback"]


def test_incremental_run_advances_baseline_for_the_following_incremental():
    manifest, raw = FakeManifest(), FakeRawStore()
    _run(manifest, raw, _routes({0: ["p0"], 1: []}), when=RUN1)

    second = _run(manifest, raw, {ACTIVITY: _activity()}, when=RUN2)
    assert _roles(second) == ["activity"]
    third = _run(manifest, raw, {ACTIVITY: _activity()}, when=RUN3)
    assert _roles(third) == ["activity"]
    assert third[0].task.meta["since"] == RUN2.isoformat()
