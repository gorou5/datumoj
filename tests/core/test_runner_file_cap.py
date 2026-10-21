"""执行器区分来源总容量和采集器的单文件上限。"""

import pytest

from datumoj.core.contracts import Crawler, FetchTask
from datumoj.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.core.manifest import SqliteManifest
from datumoj.core.models import FetchOutcome
from datumoj.core.rawstore import FileRawStore
from datumoj.core.runner import CrawlDeps, run_crawl
from datumoj.testing.samples import T0, make_spec, sha

BASE = "https://example.go.jp"


class FileCapCrawler(Crawler):
    kind = "fake"

    def __init__(self, urls, *, single_file_limit=50):
        self.urls = urls
        self.single_file_limit = single_file_limit
        self.classified = []
        self.after = []

    def plan(self, ctx):
        yield from (FetchTask(url=url) for url in self.urls)

    def classify(self, task, result):
        self.classified.append(task.url)
        if result.content is not None and len(result.content) > self.single_file_limit:
            return FetchOutcome.too_large
        return None

    def after_fetch(self, task, result, ctx):
        snapshot = ctx.manifest.latest_snapshot(task.redacted_url)
        assert snapshot is not None and snapshot.outcome is result.outcome
        self.after.append((task.url, result.outcome))
        return ()


@pytest.fixture
def build_site(tmp_path):
    opened = []

    def build(bodies, *, max_total_bytes=100, single_file_limit=50):
        spec = make_spec(
            max_total_bytes=max_total_bytes,
            max_files=10,
            scope={"urls": list(bodies)},
        )
        mapping = {f"{BASE}/robots.txt": Recorded(status=404)}
        for index, (url, body) in enumerate(bodies.items()):
            path = tmp_path / f"{len(opened)}-{index}.synthetic"
            path.write_bytes(body)
            mapping[url] = Recorded(path)

        replay = ReplayTransport(mapping)
        fetcher = PoliteHttpFetcher.from_spec(
            spec,
            transport=replay,
            max_retries=0,
            clock=lambda: 0.0,
            sleep=lambda _: None,
            now=lambda: T0,
        )
        raw = FileRawStore(tmp_path / f"raw-{len(opened)}")
        manifest = SqliteManifest(tmp_path / f"manifest-{len(opened)}.sqlite")
        opened.append((fetcher, manifest))
        crawler = FileCapCrawler(bodies, single_file_limit=single_file_limit)
        deps = CrawlDeps(raw, manifest, fetcher, clock=lambda: 0.0, sleep=lambda _: None, now=lambda: T0)
        return spec, crawler, deps, replay, raw, manifest

    yield build
    for fetcher, manifest in opened:
        fetcher.close()
        manifest.close()


def test_classifier_single_file_limit_records_failure_and_continues(build_site):
    a, b, c = (f"{BASE}/{name}.txt" for name in "abc")
    bodies = {a: b"a" * 50, b: b"b" * 60, c: b"c" * 40}
    spec, crawler, deps, replay, raw, manifest = build_site(bodies)

    report = run_crawl(spec, crawler, deps)

    assert report.failures == {"too_large": 1}
    assert report.stop_reason is None
    assert [url for url, _ in crawler.after] == [a, b, c]
    assert [outcome for _, outcome in crawler.after] == [FetchOutcome.ok, FetchOutcome.too_large, FetchOutcome.ok]
    assert all(url in replay.requests for url in (a, b, c))
    failed = manifest.latest_snapshot(b)
    assert failed.outcome is FetchOutcome.too_large and failed.sha256 is None
    assert not raw.exists(sha(bodies[b]))


def test_cumulative_limit_stops_after_callback(build_site):
    a, b, c = (f"{BASE}/{name}.txt" for name in "abc")
    bodies = {a: b"a" * 60, b: b"b" * 41, c: b"c"}
    spec, crawler, deps, replay, _, manifest = build_site(bodies, single_file_limit=1000)

    report = run_crawl(spec, crawler, deps)

    assert report.failures == {"too_large": 1}
    assert report.stop_reason == f"因上限停止：max_total_bytes={spec.max_total_bytes}"
    assert crawler.after == [(a, FetchOutcome.ok), (b, FetchOutcome.too_large)]
    assert c not in replay.requests
    assert manifest.latest_snapshot(b).outcome is FetchOutcome.too_large


def test_fetcher_too_large_stops_before_classify_and_after_callback_runs(build_site):
    a, b, c = (f"{BASE}/{name}.txt" for name in "abc")
    bodies = {a: b"a" * 10, b: b"b" * 101, c: b"c"}
    spec, crawler, deps, replay, raw, manifest = build_site(bodies, single_file_limit=1000)

    report = run_crawl(spec, crawler, deps)

    assert report.failures == {"too_large": 1}
    assert report.stop_reason == f"因上限停止：max_total_bytes={spec.max_total_bytes}"
    assert crawler.classified == [a]
    assert crawler.after == [(a, FetchOutcome.ok), (b, FetchOutcome.too_large)]
    assert c not in replay.requests
    failed = manifest.latest_snapshot(b)
    assert failed.outcome is FetchOutcome.too_large and failed.sha256 is None
    assert not raw.exists(sha(bodies[b]))
