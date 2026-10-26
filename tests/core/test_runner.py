"""执行器集成测试：真实 101–104、假 Crawler、离线 ReplayTransport。"""

from itertools import count, repeat
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from datumoj.rikolto.core.contracts import Crawler, FetchTask
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import FetchOutcome, redact_url
from datumoj.rikolto.core.rawstore import FileRawStore
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.core.runner import SPA_MIN_TEXT, CrawlDeps, CrawlReport, run_crawl
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.samples import T0, make_spec, sha

BASE = "https://example.go.jp"
A, B, C = (f"{BASE}/{name}.txt" for name in "abc")
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class ObservedReplay(ReplayTransport):
    def __init__(self, mapping, clock):
        super().__init__(mapping)
        self.clock = clock
        self.sent = []

    def handle_request(self, request):
        self.sent.append((str(request.url), self.clock(), request.headers))
        return super().handle_request(request)


class SampleCrawler(Crawler):
    kind = "fake"

    def __init__(self, tasks=None):
        self.tasks = tasks
        self.calls = []
        self.ctx = None

    def plan(self, ctx):
        self.ctx = ctx
        yield from self.tasks if self.tasks is not None else (FetchTask(url=u) for u in ctx.spec.scope["urls"])

    def after_fetch(self, task, result, ctx):
        # 回调在落库之后，插件此时能读到失败和成功快照及原样 meta。
        snapshot = ctx.manifest.latest_snapshot(task.redacted_url)
        assert snapshot.outcome == result.outcome
        assert ctx.manifest.snapshot_meta(snapshot.snapshot_id) == task.meta
        self.calls.append((task, result))
        return ()


@pytest.fixture
def site(tmp_path):
    opened = []
    builds = count()

    def build(routes=None, **settings):
        root = tmp_path / str(next(builds))
        root.mkdir()
        registry_dir = root / "sources"
        registry_dir.mkdir()
        configured = make_spec(**settings)
        (registry_dir / f"{configured.id}.yaml").write_text(
            yaml.safe_dump(configured.model_dump(mode="json")), encoding="utf-8",
        )
        spec = load_registry(registry_dir).get(configured.id)
        mapping = {f"{BASE}/robots.txt": Recorded(status=404)}
        for i, (url, body) in enumerate((routes if routes is not None else {A: b"body"}).items()):
            if isinstance(body, bytes):
                path = root / f"{i}.synthetic"
                path.write_bytes(body)
                body = Recorded(path)
            mapping[url] = body
        clock = Clock()
        replay = ObservedReplay(mapping, clock)
        fetcher = PoliteHttpFetcher.from_spec(
            spec, transport=replay, clock=clock, sleep=clock.sleep, now=lambda: T0, max_retries=0,
        )
        raw = FileRawStore(root)
        manifest = SqliteManifest(root / "manifest.sqlite")
        opened.append((fetcher, manifest))
        deps = CrawlDeps(raw, manifest, fetcher, clock=clock, sleep=clock.sleep, now=lambda: T0)
        return SimpleNamespace(spec=spec, deps=deps, raw=raw, manifest=manifest, replay=replay, clock=clock)

    yield build
    for fetcher, manifest in opened:
        fetcher.close()
        manifest.close()


class TestSampleCrawlerConformance(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return SampleCrawler()

    @pytest.fixture
    def spec(self):
        return make_spec()

    @pytest.fixture
    def make_fetcher(self, site):
        return lambda: site().deps.fetcher


def test_real_fixture_stored_with_meta_and_license(site):
    path = FIXTURES / "stat_dashboard" / "getIndicatorInfo_category0204_rank3.json"
    h = site({A: Recorded(path, headers={"Content-Type": "wrong/type; charset=cp932"})})
    meta = {"kind": "list", "nested": {"date": None, "revisions": [1, "未施行"]}}
    crawler = SampleCrawler([FetchTask(url=A, meta=meta)])
    report = run_crawl(h.spec, crawler, h.deps, limit=7)
    assert isinstance(report, CrawlReport)
    assert (report.new, report.unchanged, report.failures, report.stop_reason) == (1, 0, {}, None)
    snapshot, = h.manifest.snapshots_for(h.spec.id)
    assert h.raw.get(snapshot.sha256) == path.read_bytes()
    assert h.manifest.snapshot_meta(snapshot.snapshot_id) == meta
    assert snapshot.license == h.spec.license.name
    assert crawler.calls[0][0].meta == meta
    assert crawler.ctx.limit == 7 and crawler.ctx.now == T0
    assert report.elapsed_seconds == 1 / h.spec.rate_per_sec


def test_conditional_headers_use_last_success_even_after_failure(site, tmp_path):
    h = site({A: Recorded(status=304)})
    body = b"previous body"
    path = tmp_path / "old.synthetic"
    path.write_bytes(body)
    previous = site({A: Recorded(path, headers={"ETag": '"v1"', "Last-Modified": "Tue, 20 Oct 2026 00:00:00 GMT"})})
    run_crawl(previous.spec, SampleCrawler(), previous.deps)
    snapshot, = previous.manifest.snapshots_for(previous.spec.id)
    h.raw.put(body)
    h.manifest.record_snapshot(snapshot)
    failed = snapshot.model_copy(update={
        "snapshot_id": "failed", "outcome": FetchOutcome.network_error, "sha256": None,
        "size": None, "error": "offline", "etag": "bad-etag", "last_modified": "bad-date",
    })
    h.manifest.record_snapshot(failed)
    crawler = SampleCrawler()
    report = run_crawl(h.spec, crawler, h.deps)
    assert (report.new, report.unchanged, report.failures) == (0, 1, {})
    headers = h.replay.sent[-1][2]
    assert headers["if-none-match"] == snapshot.etag
    assert headers["if-modified-since"] == snapshot.last_modified
    assert crawler.calls[0][1].outcome is FetchOutcome.not_modified
    assert h.manifest.latest_snapshot(A).outcome is FetchOutcome.not_modified
    assert h.raw.get(snapshot.sha256) == body


def test_same_body_is_unchanged_without_304_and_ids_do_not_collide(site):
    h = site()
    assert run_crawl(h.spec, SampleCrawler(), h.deps).new == 1
    report = run_crawl(h.spec, SampleCrawler(), h.deps)
    assert (report.new, report.unchanged) == (0, 1)
    snapshots = h.manifest.snapshots_for(h.spec.id)
    assert len(snapshots) == 2
    assert len({s.snapshot_id for s in snapshots}) == 2
    assert len(list(h.raw.path_for(snapshots[0].sha256).parent.iterdir())) == 1


def test_http_failure_recorded_and_callback_can_schedule_recovery(site):
    class Recovering(SampleCrawler):
        def after_fetch(self, task, result, ctx):
            super().after_fetch(task, result, ctx)
            return [FetchTask(url=B)] if result.outcome is FetchOutcome.http_error else []

    h = site({A: Recorded(status=503), B: b"recovery"})
    crawler = Recovering()
    report = run_crawl(h.spec, crawler, h.deps)
    assert report.failures == {"http_error": 1} and report.new == 1
    failed = h.manifest.latest_snapshot(A)
    assert failed.outcome is FetchOutcome.http_error and failed.error and failed.sha256 is None
    assert [r.outcome for _, r in crawler.calls] == [FetchOutcome.http_error, FetchOutcome.ok]


@pytest.mark.parametrize("reason", ["network_error", "blocked_by_robots", "url_not_allowed"])
def test_other_fetch_failures_recorded_and_callback_called(site, monkeypatch, reason):
    h = site()
    if reason == "blocked_by_robots":
        from datumoj.rikolto.core.http import RobotsRules

        h.deps.fetcher.robots.get(f"{BASE}/robots.txt", lambda: RobotsRules.parse("User-agent: *\nDisallow: /", "DATUMOJ"))
    elif reason == "network_error":
        replay_request = h.replay.handle_request

        def disconnected(request):
            if request.url.path == "/robots.txt":
                return replay_request(request)
            raise httpx.ConnectError("offline", request=request)

        monkeypatch.setattr(h.replay, "handle_request", disconnected)
    crawler = SampleCrawler([FetchTask(url="https://other.go.jp/x" if reason == "url_not_allowed" else A)])
    report = run_crawl(h.spec, crawler, h.deps)
    expected = "network_error" if reason == "url_not_allowed" else reason
    assert report.failures == {expected: 1}
    snapshot, = h.manifest.snapshots_for(h.spec.id)
    assert snapshot.error and snapshot.sha256 is None
    assert len(crawler.calls) == 1


def test_lazy_plan_sees_persisted_previous_page_and_extras_have_priority(site):
    class Paging(SampleCrawler):
        def plan(self, ctx):
            yield FetchTask(url=A)
            assert ctx.manifest.latest_snapshot(A) is not None
            assert ctx.manifest.latest_snapshot(B) is not None
            yield FetchTask(url=C)

        def after_fetch(self, task, result, ctx):
            super().after_fetch(task, result, ctx)
            if task.url == A:
                yield FetchTask(url=B, meta={"page": 2})
                yield FetchTask(url=A)

    h = site({A: b"one", B: b"two", C: b"three"})
    crawler = Paging()
    report = run_crawl(h.spec, crawler, h.deps)
    assert report.new == 3 and report.stop_reason is None
    assert [t.url for t, _ in crawler.calls] == [A, B, C]
    assert h.manifest.snapshot_meta(h.manifest.latest_snapshot(B).snapshot_id) == {"page": 2}


def test_deduplicates_by_redacted_url_and_report_does_not_leak_keys(site):
    h = site({A + "?api_key=one": b"data"})
    crawler = SampleCrawler([FetchTask(url=A + "?api_key=" + key) for key in ("one", "two")])
    report = run_crawl(h.spec, crawler, h.deps)
    assert report.new == 1 and len(crawler.calls) == 1
    snapshot, = h.manifest.snapshots_for(h.spec.id)
    assert snapshot.resource_url == redact_url(A + "?api_key=one")
    assert "api_key=one" not in snapshot.model_dump_json() + repr(report)


@pytest.mark.parametrize("max_files", [1, 2])
def test_file_limit_includes_failures_and_completes_callback(site, max_files):
    class Extra(SampleCrawler):
        def after_fetch(self, task, result, ctx):
            super().after_fetch(task, result, ctx)
            yield FetchTask(url=C)

    h = site({A: Recorded(status=503), B: b"two", C: b"three"}, max_files=max_files)
    crawler = Extra([FetchTask(url=A), FetchTask(url=B)])
    report = run_crawl(h.spec, crawler, h.deps)
    assert len(crawler.calls) == len(h.manifest.snapshots_for(h.spec.id)) == max_files
    assert "因上限停止" in report.stop_reason and "max_files" in report.stop_reason
    assert report.failures == {"http_error": 1}


@pytest.mark.parametrize("second_size, expected", [(60, FetchOutcome.too_large), (40, FetchOutcome.ok)])
def test_byte_limit_completes_callback_and_discards_followups(site, second_size, expected):
    # 回归：100 字节上限，两个 60 字节响应；第二次 too_large 也必须调用 after_fetch。
    class Extra(SampleCrawler):
        def after_fetch(self, task, result, ctx):
            super().after_fetch(task, result, ctx)
            if task.url == B:
                yield FetchTask(url=C)

    h = site({A: b"a" * 60, B: b"b" * second_size, C: b"must not fetch"}, max_total_bytes=100)
    crawler = Extra([FetchTask(url=A), FetchTask(url=B)])
    report = run_crawl(h.spec, crawler, h.deps)
    assert [r.outcome for _, r in crawler.calls] == [FetchOutcome.ok, expected]
    assert C not in h.replay.requests
    assert "因上限停止" in report.stop_reason and "max_total_bytes" in report.stop_reason
    snapshot = h.manifest.latest_snapshot(B)
    assert snapshot.outcome is expected
    if expected is FetchOutcome.too_large:
        assert report.failures == {"too_large": 1} and report.new == 1
        assert snapshot.sha256 is None and not h.raw.exists(sha(b"b" * second_size))
    else:
        assert report.failures == {} and report.new == 2
        assert h.raw.get(snapshot.sha256) == b"b" * second_size


def test_single_file_streaming_limit_also_calls_after_fetch(site):
    h = site({A: b"x" * 101}, max_total_bytes=100)
    crawler = SampleCrawler()
    report = run_crawl(h.spec, crawler, h.deps)
    assert report.failures == {"too_large": 1} and "因上限停止" in report.stop_reason
    assert crawler.calls[0][1].outcome is FetchOutcome.too_large
    assert h.manifest.latest_snapshot(A).sha256 is None


def test_rate_from_registry_and_elapsed_time(site):
    h = site({A: b"a", B: b"b", C: b"c"}, rate_per_sec=0.5)
    report = run_crawl(h.spec, SampleCrawler([FetchTask(url=u) for u in (A, B, C)]), h.deps)
    assert [t for _, t, _ in h.replay.sent] == [0, 2, 4, 6]  # robots 也受 103 的限速。
    assert report.elapsed_seconds == 6


def test_dry_run_only_lists_deduplicated_plan_and_passes_limit(site):
    h = site({})
    tasks = [FetchTask(url=A + "?key=secret", meta={"kind": "list"}), FetchTask(url=A + "?key=other"), FetchTask(url=B)]
    crawler = SampleCrawler(tasks)
    report = run_crawl(h.spec, crawler, h.deps, limit=0, dry_run=True)
    assert [t.url for t in report.planned] == [redact_url(tasks[0].url), B]
    assert report.planned[0].meta == tasks[0].meta
    assert "secret" not in repr(report)
    assert report.new == report.unchanged == 0 and report.failures == {}
    assert crawler.ctx.limit == 0 and not crawler.calls and not h.replay.requests
    assert h.manifest.snapshots_for(h.spec.id) == [] and not list(h.raw.path_for("a" * 64).parent.glob("*"))


def test_dry_run_respects_registry_file_limit(site):
    h = site({}, max_files=1)
    report = run_crawl(h.spec, SampleCrawler([FetchTask(url=A), FetchTask(url=B)]), h.deps, dry_run=True)
    assert len(report.planned) == 1 and "max_files" in report.stop_reason
    assert not h.replay.requests


@pytest.mark.parametrize("body, expected", [
    (b"", FetchOutcome.empty_body),
    (b" \r\n\t", FetchOutcome.empty_body),
    (b"\xef\xbb\xbf", FetchOutcome.empty_body),
    (b"<html><head><title>Title</title></head><body> <!-- comment --> </body></html>", FetchOutcome.empty_body),
    (b'<html><div id="root"></div><script>' + b"fake text" * 100 + b"</script></html>", FetchOutcome.spa_shell),
    (b"<div id='root'>Loading...</div>", FetchOutcome.spa_shell),
    (b'<html><body><div id="__next">Loading</div></body></html>', FetchOutcome.spa_shell),
    (b'<html><body><script src="/_next/__next.js"></script></body></html>', FetchOutcome.spa_shell),
    (('<div id="root">' + "本" * (SPA_MIN_TEXT - 1) + "</div>").encode("cp932"), FetchOutcome.spa_shell),
    (('<div id="root">' + "本" * SPA_MIN_TEXT + "</div>").encode("cp932"), FetchOutcome.ok),
    (b"<html><body><p>Short but real</p></body></html>", FetchOutcome.ok),
    (b"%PDF-1.7\n\x00\xff", FetchOutcome.ok),
    (b'{"markup": "<div id=\"root\"></div>"}', FetchOutcome.ok),
])
def test_default_empty_and_spa_detection_uses_content_and_keeps_original(site, body, expected):
    h = site({A: body})
    report = run_crawl(h.spec, SampleCrawler(), h.deps)
    snapshot = h.manifest.latest_snapshot(A)
    assert snapshot.outcome is expected
    assert h.raw.get(snapshot.sha256) == body
    assert report.failures == ({} if expected is FetchOutcome.ok else {expected.value: 1})


@pytest.mark.parametrize("override", [FetchOutcome.ok, FetchOutcome.empty_body, FetchOutcome.spa_shell])
def test_plugin_classification_precedes_default(site, override):
    class Classified(SampleCrawler):
        def classify(self, task, result):
            assert result.outcome is FetchOutcome.ok and result.content == b'<div id="root"></div>'
            return override

    h = site({A: b'<div id="root"></div>'})
    crawler = Classified()
    run_crawl(h.spec, crawler, h.deps)
    assert h.manifest.latest_snapshot(A).outcome is override
    assert crawler.calls[0][1].outcome is override


@pytest.mark.parametrize("stage", ["plan", "after_fetch"])
@pytest.mark.parametrize("generator", [False, True])
def test_plugin_errors_are_reported_with_partial_progress(site, stage, generator):
    class Broken(SampleCrawler):
        def plan(self, ctx):
            if stage != "plan":
                return [FetchTask(url=A)]
            if not generator:
                raise ValueError(A + "?key=private-value")

            def items():
                yield FetchTask(url=A)
                raise ValueError(A + "?key=private-value")

            return items()

        def after_fetch(self, task, result, ctx):
            super().after_fetch(task, result, ctx)
            if stage != "after_fetch":
                return ()
            if not generator:
                raise ValueError(A + "?key=private-value")

            def items():
                yield FetchTask(url=B)
                raise ValueError(A + "?key=private-value")

            return items()

    h = site()
    report = run_crawl(h.spec, Broken(), h.deps)
    assert report.failures == {f"{stage}_error": 1}
    assert stage in report.stop_reason and "private-value" not in repr(report)
    assert report.new == (0 if stage == "plan" and not generator else 1)
    assert B not in h.replay.requests


@pytest.mark.parametrize("stage", ["plan", "after_fetch"])
def test_runaway_pagination_including_duplicate_urls_is_bounded(site, monkeypatch, stage):
    monkeypatch.setattr("datumoj.rikolto.core.runner.MAX_PLAN_STEPS", 5)

    class Runaway(SampleCrawler):
        def plan(self, ctx):
            return repeat(FetchTask(url=A)) if stage == "plan" else [FetchTask(url=A)]

        def after_fetch(self, task, result, ctx):
            super().after_fetch(task, result, ctx)
            return repeat(FetchTask(url=A)) if stage == "after_fetch" else ()

    h = site()
    report = run_crawl(h.spec, Runaway(), h.deps)
    assert report.new == 1 and report.failures == {f"{stage}_error": 1}
    assert "分页可能没有终止" in report.stop_reason
    assert len(h.manifest.snapshots_for(h.spec.id)) == 1


@pytest.mark.parametrize("store", ["raw", "manifest"])
def test_storage_errors_propagate_instead_of_reporting_success(site, monkeypatch, store):
    h = site()

    def disk_full(*args):
        raise OSError("disk full")

    monkeypatch.setattr(h.raw if store == "raw" else h.manifest, "put" if store == "raw" else "record_snapshot", disk_full)
    with pytest.raises(OSError, match="disk full"):
        run_crawl(h.spec, SampleCrawler(), h.deps)
    assert h.manifest.snapshots_for(h.spec.id) == []
