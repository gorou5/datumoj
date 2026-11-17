"""请求头：FetchTask.headers、SourceSpec.key_header、执行器合并与 key 隔离。HTTP 全部转发到本地 pytest-httpserver。"""

import secrets
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from werkzeug.wrappers import Response

from datumoj.rikolto.core.contracts import FetchTask, HttpFetcher
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.testing.samples import T0, make_spec

HOST = "example.go.jp"
ACCEPT = "application/vnd.sdmx.data+csv;version=1.0.0"
KEY_HEADER = "Ocp-Apim-Subscription-Key"
KEY_ENV = "WISTERIA_TEST_KEY"
URL = f"https://{HOST}/data"


@pytest.fixture
def fake_key(monkeypatch):
    key = secrets.token_hex(16)
    monkeypatch.setenv(KEY_ENV, key)
    return key


@pytest.fixture
def site(httpserver):
    """sent：fetcher 发给 transport 的每个请求（host、path、头）；received：本地服务器真正收到的头。"""
    sent, received = [], []
    with httpx.Client(trust_env=False, timeout=5) as local:
        def forward(request):
            assert request.url.host.endswith("go.jp"), "测试禁止访问外网"
            sent.append((request.url.host, request.method, request.url.path, dict(request.headers)))
            response = local.request(
                request.method, httpserver.url_for(request.url.path),
                headers={k: v for k, v in request.headers.items() if k != "host"}, follow_redirects=False,
            )
            return httpx.Response(response.status_code, headers=response.headers, stream=httpx.ByteStream(response.content))

        def route(path="/data", *, method="GET", status=200, headers=None, body="ok", once=False):
            def respond(request):
                received.append((request.method, request.path, dict(request.headers)))
                response = Response(body, status=status)
                response.headers.update(headers or {})
                return response

            expect = httpserver.expect_oneshot_request if once else httpserver.expect_request
            expect(path, method=method).respond_with_handler(respond)

        def fetcher(allowed=(HOST,), **kwargs):
            kwargs.setdefault("max_retries", 0)
            return PoliteHttpFetcher(
                list(allowed), transport=httpx.MockTransport(forward), rate_per_sec=1000,
                sleep=lambda seconds: None, now=lambda: T0 + timedelta(minutes=1), **kwargs,
            )

        httpserver.expect_request("/robots.txt", method="GET").respond_with_data(status=404)
        yield SimpleNamespace(route=route, fetcher=fetcher, sent=sent, received=received)
    httpserver.check_assertions()


def fetch(fetcher, url=URL, **kwargs):
    with fetcher:
        return fetcher.fetch(url, headers={"Accept": ACCEPT}, key_header=KEY_HEADER, key_env=KEY_ENV, **kwargs)


def pages(site):
    return [entry for entry in site.sent if entry[2] != "/robots.txt"]


# ---------- 服务器收到的头 ----------


def test_server_receives_accept_and_key_header(site, fake_key):
    site.route()
    result = fetch(site.fetcher())
    assert result.outcome is FetchOutcome.ok
    (_, _, received), = site.received
    assert received["Accept"] == ACCEPT
    assert received[KEY_HEADER] == fake_key
    assert received["Accept-Encoding"] == "identity"
    assert received["User-Agent"].startswith("DATUMOJ/")
    # robots.txt 不带 Accept，更不带 key
    robots = [headers for _, _, path, headers in site.sent if path == "/robots.txt"]
    assert robots and all(KEY_HEADER.lower() not in h and h["accept"] == "*/*" for h in robots)


def test_conditional_request_keeps_extra_headers(site, fake_key):
    site.route(status=304)
    result = fetch(site.fetcher(), etag='"v1"', last_modified="Tue, 20 Oct 2026 00:00:00 GMT")
    assert result.outcome is FetchOutcome.not_modified
    (_, _, received), = site.received
    assert received["If-None-Match"] == '"v1"' and received["Accept"] == ACCEPT and received[KEY_HEADER] == fake_key


def test_head_probe_and_fallback_get_both_carry_headers(site, fake_key):
    lm = "Tue, 20 Oct 2026 00:00:00 GMT"
    site.route(method="HEAD", headers={"Last-Modified": lm, "Content-Length": "99"})
    site.route(method="GET", headers={"Last-Modified": lm})
    result = fetch(site.fetcher(), etag='"v1"', last_modified=lm, size=4, head_probe=True)
    assert result.outcome is FetchOutcome.ok
    assert [m for m, _, _ in site.received] == ["HEAD", "GET"]
    for _, _, headers in site.received:
        assert headers["Accept"] == ACCEPT and headers[KEY_HEADER] == fake_key


def test_retry_and_redirect_carry_same_headers(site, fake_key):
    site.route("/data", status=503, once=True)
    site.route("/data", status=302, headers={"Location": f"https://{HOST}/final"}, once=True)
    site.route("/final")
    # 503 → 重试 → 302 → /final：三次请求都带同样的头（一次性路由按登记顺序匹配）
    result = fetch(site.fetcher(max_retries=1))
    assert result.outcome is FetchOutcome.ok and result.final_url == f"https://{HOST}/final"
    assert len(site.received) == 3
    for _, _, headers in site.received:
        assert headers["Accept"] == ACCEPT and headers[KEY_HEADER] == fake_key


def test_redirect_inside_allowed_hosts_keeps_key(site, fake_key):
    site.route("/data", status=302, headers={"Location": "https://cdn.example.go.jp/final"})
    site.route("/final")
    result = fetch(site.fetcher(allowed=(HOST, "*.example.go.jp")))
    assert result.outcome is FetchOutcome.ok
    assert [h for h, _, p, _ in pages(site)] == [HOST, "cdn.example.go.jp"]
    assert all(headers[KEY_HEADER.lower()] == fake_key for _, _, _, headers in pages(site))


# ---------- 白名单外：不带 key ----------


def test_redirect_to_unlisted_host_is_blocked_and_never_gets_key(site, fake_key):
    site.route("/data", status=302, headers={"Location": "https://evil.example.com/steal"})
    result = fetch(site.fetcher())
    assert result.outcome is FetchOutcome.network_error and "重定向被拦下" in result.error
    assert {host for host, *_ in site.sent} == {HOST}
    assert fake_key not in repr(result)


def test_key_header_is_not_added_for_unlisted_host(fake_key):
    seen = []

    def capture(request):
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b"x"))

    fetcher = PoliteHttpFetcher([HOST], transport=httpx.MockTransport(capture), sleep=lambda s: None)
    with fetcher:
        # 直接走发送层：即使上层漏检，白名单外的主机也拿不到 key
        for host in ("evil.example.com", HOST):
            fetcher._request(httpx.URL(f"https://{host}/x"), {"Accept": ACCEPT}, None, key=(KEY_HEADER, KEY_ENV))
    by_host = {r.url.host: r.headers for r in seen}
    assert KEY_HEADER not in by_host["evil.example.com"]
    assert by_host[HOST][KEY_HEADER] == fake_key
    assert by_host["evil.example.com"]["Accept"] == ACCEPT


# ---------- 缺 key ----------


@pytest.mark.parametrize("value", [None, ""])
def test_missing_key_fails_without_request(site, monkeypatch, value):
    monkeypatch.delenv(KEY_ENV, raising=False)
    if value is not None:
        monkeypatch.setenv(KEY_ENV, value)
    result = fetch(site.fetcher())
    assert result.outcome is FetchOutcome.network_error
    assert result.error == f"缺少 key（{KEY_ENV}）"
    assert site.sent == []


@pytest.mark.parametrize("suffix", ["\r\nX-Injected: 1", " ", "\t", "\x0b", "\x7f", "é", "鍵"])
def test_unsendable_key_is_rejected_without_leaking(site, monkeypatch, suffix):
    secret = "s" + secrets.token_hex(12)
    monkeypatch.setenv(KEY_ENV, secret + suffix)
    result = fetch(site.fetcher())
    assert result.outcome is FetchOutcome.network_error
    assert result.error == f"key 格式不合法（{KEY_ENV}）" and secret not in repr(result)
    assert site.sent == []


# ---------- 校验 ----------


@pytest.mark.parametrize("name", [
    "Host", "user-agent", "If-None-Match", "IF-MODIFIED-SINCE", "Accept-Encoding", "Authorization", "cookie",
])
def test_forbidden_header_names_are_rejected(name):
    with pytest.raises(ValidationError):
        FetchTask(url=URL, headers={name: "x"})


@pytest.mark.parametrize("name,value", [
    ("Accept", "a\r\nX-Evil: 1"), ("Accept", "a\nb"), ("Accept", "a\0b"), ("Bad Name", "x"), ("", "x"), ("A:B", "x"),
])
def test_bad_header_names_and_values_are_rejected(name, value):
    with pytest.raises(ValidationError):
        FetchTask(url=URL, headers={name: value})


def test_task_headers_default_empty_and_accept_ok():
    assert FetchTask(url=URL).headers == {}
    assert FetchTask(url=URL, headers={"Accept": ACCEPT}).headers == {"Accept": ACCEPT}


def test_key_header_requires_needs_key():
    with pytest.raises(ValidationError, match="needs_key"):
        make_spec(key_header=KEY_HEADER)
    spec = make_spec(key_header=KEY_HEADER, needs_key=f"env:{KEY_ENV}")
    assert (spec.key_header, spec.needs_key) == (KEY_HEADER, f"env:{KEY_ENV}")
    assert make_spec().key_header is None


@pytest.mark.parametrize("name", ["Host", "User-Agent", "Authorization", "Bad Name", "X-Key\r\n"])
def test_key_header_name_is_validated(name):
    with pytest.raises(ValidationError):
        make_spec(key_header=name, needs_key=f"env:{KEY_ENV}")


def test_fetch_rejects_task_header_with_key_header_name(site, fake_key):
    with pytest.raises(ValueError, match="同名"), site.fetcher() as fetcher:
        fetcher.fetch(URL, headers={KEY_HEADER.lower(): "x"}, key_header=KEY_HEADER, key_env=KEY_ENV)
    assert site.sent == []


# ---------- 已有行为不变 ----------


def test_without_new_fields_request_headers_are_unchanged(site):
    site.route()
    with site.fetcher() as fetcher:
        assert isinstance(fetcher, HttpFetcher)
        fetcher.fetch(URL)
        fetcher.fetch(URL, etag='"v1"', last_modified="Tue, 20 Oct 2026 00:00:00 GMT")
    plain, conditional = (headers for _, _, path, headers in site.sent if path == "/data")
    base = {
        "host": HOST, "accept": "*/*", "accept-encoding": "identity", "connection": "keep-alive",
        "user-agent": "DATUMOJ/0.1 (+https://github.com/gorou5/datumoj)",
    }
    assert plain == base
    assert conditional == {**base, "if-none-match": '"v1"', "if-modified-since": "Tue, 20 Oct 2026 00:00:00 GMT"}


# ---------- 录制 ----------


def test_replay_records_request_headers_with_key_redacted(fake_key):
    replay = ReplayTransport({f"https://{HOST}/robots.txt": Recorded(status=404), URL: Recorded()})
    with PoliteHttpFetcher([HOST], transport=replay, sleep=lambda s: None) as fetcher:
        result = fetcher.fetch(URL, headers={"Accept": ACCEPT}, key_header="X-Custom-Auth", key_env=KEY_ENV)
    assert result.outcome is FetchOutcome.ok
    sent = replay.request_headers[-1]
    assert sent["accept"] == ACCEPT and sent["x-custom-auth"] == "REDACTED"
    assert fake_key not in repr(replay.request_headers) + repr(replay.requests)


def test_recorded_response_headers_redact_credentials():
    recorded = Recorded(headers={"Content-Type": "text/csv", "X-Api-Key": "abc", "Set-Cookie": "s=1"})
    assert recorded.headers == {"Content-Type": "text/csv", "X-Api-Key": "REDACTED", "Set-Cookie": "s=1"}
