"""HEAD 探测：生产地址校验不变，所有 HTTP 仅转发到 pytest-httpserver。"""

from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from werkzeug.wrappers import Response

from datumoj.rikolto.core.http import PoliteHttpFetcher, RobotsCache, RobotsRules
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.core.runner import CrawlDeps, run_crawl
from datumoj.rikolto.testing.fakes import FakeCrawler, FakeHttpFetcher, FakeManifest, FakeRawStore
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec

BASE = "https://example.go.jp"
URL = f"{BASE}/file.pdf"
LM = "Tue, 20 Oct 2026 00:00:00 GMT"
HEADERS = {"Last-Modified": LM, "Content-Length": "4", "ETag": '"new"'}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


@pytest.fixture
def site(httpserver):
    clock, sent = Clock(), []
    opened, head_failures = [], []
    with httpx.Client(trust_env=False, timeout=5) as local:
        def forward(request):
            assert request.url.host == "example.go.jp", "测试禁止访问外网"
            sent.append((request.method, request.url.path, clock(), request.headers))
            if request.method == "HEAD" and head_failures:
                raise head_failures.pop(0)
            response = local.request(
                request.method, httpserver.url_for(request.url.path),
                headers={k: v for k, v in request.headers.items() if k != "host"},
            )
            return httpx.Response(
                response.status_code, headers=response.headers, stream=httpx.ByteStream(response.content),
            )

        def route(path="/file.pdf", *, method="HEAD", status=200, headers=None, missing_length=False):
            def respond(request):
                response = Response(b"body", status=status)
                response.headers.update(HEADERS if headers is None else headers)
                if missing_length:
                    response.automatically_set_content_length = False
                    response.headers.pop("Content-Length", None)
                return response

            httpserver.expect_request(path, method=method).respond_with_handler(respond)

        def fetcher(**kwargs):
            kwargs.setdefault("max_retries", 0)
            client = PoliteHttpFetcher(
                ["example.go.jp"], transport=httpx.MockTransport(forward),
                clock=clock, sleep=clock.sleep, now=lambda: T0 + timedelta(minutes=1), **kwargs,
            )
            opened.append(client)
            return client

        httpserver.expect_request("/robots.txt", method="GET").respond_with_data(status=404)
        yield SimpleNamespace(route=route, fetcher=fetcher, sent=sent, clock=clock, head_failures=head_failures)
        for client in opened:
            client.close()
    httpserver.check_assertions()


def methods(site):
    return [method for method, path, _, _ in site.sent if path != "/robots.txt"]


@pytest.mark.parametrize("status,size", [(200, 4), (202, 4), (200, 0)])
def test_matching_head_skips_get_and_has_no_body(site, status, size):
    headers = {**HEADERS, "Content-Length": str(size), "Content-Encoding": "br"}
    site.route(status=status, headers=headers)
    result = site.fetcher(max_bytes=1).fetch(
        URL, etag='"old"', last_modified=LM, size=size, head_probe=True,
    )
    assert (result.outcome, result.http_status, result.content) == (FetchOutcome.not_modified, status, None)
    assert (result.etag, result.last_modified, result.sha256) == ('"new"', LM, None)
    assert methods(site) == ["HEAD"]
    assert "if-none-match" not in site.sent[-1][3]
    assert "if-modified-since" not in site.sent[-1][3]


@pytest.mark.parametrize("headers,status,missing_length", [
    ({**HEADERS, "Last-Modified": "Wed, 21 Oct 2026 00:00:00 GMT"}, 200, False),
    ({**HEADERS, "Content-Length": "5"}, 200, False),
    ({"Content-Length": "4"}, 200, False),
    ({"Last-Modified": LM}, 200, True),
    ({**HEADERS, "Content-Length": "-4"}, 200, False),
    (HEADERS, 405, False),
    (HEADERS, 501, False),
    (HEADERS, 503, False),
    (HEADERS, 403, False),
    (HEADERS, 304, False),
])
def test_probe_mismatch_or_error_falls_back_to_conditional_get(site, headers, status, missing_length):
    site.route(status=status, headers=headers, missing_length=missing_length)
    site.route(method="GET")
    result = site.fetcher().fetch(URL, etag='"old"', last_modified=LM, size=4, head_probe=True)
    assert (result.outcome, result.content) == (FetchOutcome.ok, b"body")
    assert methods(site) == ["HEAD", "GET"]
    assert site.sent[-1][3]["if-none-match"] == '"old"'
    assert site.sent[-1][3]["if-modified-since"] == LM


@pytest.mark.parametrize("kwargs", [
    {}, {"head_probe": False, "last_modified": LM, "size": 4},
    {"head_probe": True, "last_modified": LM}, {"head_probe": True, "size": 4},
])
def test_disabled_or_incomplete_probe_only_sends_get(site, kwargs):
    site.route(method="GET")
    assert site.fetcher().fetch(URL, **kwargs).outcome is FetchOutcome.ok
    assert methods(site) == ["GET"]


def test_head_blocked_by_robots_sends_no_request(site):
    robots = RobotsCache()
    robots.get(f"{BASE}/robots.txt", lambda: RobotsRules.parse("User-agent: *\nDisallow: /", "datumoj"))
    result = site.fetcher(robots=robots).fetch(URL, last_modified=LM, size=4, head_probe=True)
    assert result.outcome is FetchOutcome.blocked_by_robots
    assert site.sent == []


def test_head_and_fallback_get_each_use_a_request_interval(site):
    site.route(status=405)
    site.route(method="GET")
    fetcher = site.fetcher(rate_per_sec=0.5)
    for _ in range(2):
        assert fetcher.fetch(URL, last_modified=LM, size=4, head_probe=True).outcome is FetchOutcome.ok
    assert [(method, at) for method, _, at, _ in site.sent] == [
        ("GET", 0), ("HEAD", 2), ("GET", 4), ("HEAD", 6), ("GET", 8),
    ]


@pytest.mark.parametrize("failure", ["http", "network"])
def test_head_retries_before_fallback_get(site, httpserver, failure):
    if failure == "http":
        httpserver.expect_oneshot_request("/file.pdf", method="HEAD").respond_with_data(status=503)
    else:
        site.head_failures.append(httpx.ConnectError("synthetic HEAD connection failure"))
    site.route(status=405)
    site.route(method="GET")
    result = site.fetcher(max_retries=1, backoff=3).fetch(URL, last_modified=LM, size=4, head_probe=True)
    assert result.outcome is FetchOutcome.ok
    assert methods(site) == ["HEAD", "HEAD", "GET"]
    assert [at for method, _, at, _ in site.sent if method == "HEAD"] == [1, 4]


def test_head_follows_allowed_redirect_and_keeps_head_metadata(site):
    site.route(status=302, headers={"Location": "/final.pdf"})
    site.route("/final.pdf")
    result = site.fetcher().fetch(URL, last_modified=LM, size=4, head_probe=True)
    assert result.outcome is FetchOutcome.not_modified
    assert result.final_url == f"{BASE}/final.pdf"
    assert [path for method, path, _, _ in site.sent if method == "HEAD"] == ["/file.pdf", "/final.pdf"]
    assert methods(site) == ["HEAD", "HEAD"]


@pytest.mark.parametrize("target", ["https://evil.test/file.pdf", "http://127.0.0.1/file.pdf"])
def test_head_redirect_cannot_escape_allowed_hosts(site, target):
    site.route(status=302, headers={"Location": target})
    site.route(method="GET")
    result = site.fetcher().fetch(URL, last_modified=LM, size=4, head_probe=True)
    assert result.outcome is FetchOutcome.ok
    assert methods(site) == ["HEAD", "GET"]
    assert all(path in ("/robots.txt", "/file.pdf") for _, path, _, _ in site.sent)


def test_head_redirect_target_obeys_robots(site):
    robots = RobotsCache()
    robots.get(f"{BASE}/robots.txt", lambda: RobotsRules.parse("User-agent: *\nDisallow: /private", "datumoj"))
    site.route(status=302, headers={"Location": "/private/file.pdf"})
    result = site.fetcher(robots=robots).fetch(URL, last_modified=LM, size=4, head_probe=True)
    assert result.outcome is FetchOutcome.blocked_by_robots
    assert methods(site) == ["HEAD"]


@pytest.mark.parametrize("flag", [True, False, "absent"])
@pytest.mark.parametrize("history", [True, False])
def test_runner_passes_scope_flag_and_last_success_size_without_writing_head_raw(site, monkeypatch, flag, history):
    scope = {"urls": [URL, f"{BASE}/second.pdf"]}
    if flag != "absent":
        scope["head_probe"] = flag
    spec = make_spec(scope=scope, max_files=1)
    manifest, raw = FakeManifest(), FakeRawStore()
    if history:
        previous = make_snapshot(url=URL, data=b"body", etag='"old"', last_modified=LM)
        raw.put(b"body")
        manifest.record_snapshot(previous)
        for i, outcome in enumerate([FetchOutcome.network_error, FetchOutcome.not_modified], 1):
            manifest.record_snapshot(make_snapshot(
                url=URL, outcome=outcome, snapshot_id=f"later-{i}", fetched_at=T0 + timedelta(seconds=i),
            ))

        def no_raw_write(data):
            pytest.fail("HEAD / 304 未变化结果不应写 raw")

        monkeypatch.setattr(raw, "put", no_raw_write)
    site.route()
    site.route(method="GET", status=304 if history else 200)
    fetcher = site.fetcher()
    calls, fetch = [], fetcher.fetch

    def observe(url, **kwargs):
        calls.append((url, kwargs))
        return fetch(url, **kwargs)

    monkeypatch.setattr(fetcher, "fetch", observe)
    deps = CrawlDeps(
        raw, manifest, fetcher, clock=site.clock, sleep=site.clock.sleep, now=lambda: T0 + timedelta(minutes=1),
    )
    report = run_crawl(spec, FakeCrawler(), deps)
    assert calls == [(URL, {
        "etag": '"old"' if history else None, "last_modified": LM if history else None,
        "size": 4 if history else None, "head_probe": flag is True,
    })]
    assert methods(site) == (["HEAD"] if history and flag is True else ["GET"])
    assert (report.new, report.unchanged, report.failures) == (int(not history), int(history), {})
    assert report.stop_reason == "因上限停止：max_files=1"
    snapshot = manifest.snapshots_for(spec.id)[-1]
    assert snapshot.outcome is (FetchOutcome.not_modified if history else FetchOutcome.ok)
    assert snapshot.http_status == (200 if not history or flag is True else 304)
    if history:
        assert snapshot.sha256 is None and snapshot.size is None
        assert manifest.last_success(URL) == previous


@pytest.mark.parametrize("flag", ["true", "false", 0, 1, None, [], {}])
def test_runner_rejects_non_boolean_scope_flag_before_fetch(flag):
    spec = make_spec(scope={"urls": [URL], "head_probe": flag})
    fetcher = FakeHttpFetcher({URL: b"body"})
    with pytest.raises(ValueError, match=r"scope\.head_probe.*布尔"):
        run_crawl(spec, FakeCrawler(), CrawlDeps(FakeRawStore(), FakeManifest(), fetcher))
    assert fetcher.requests == []
