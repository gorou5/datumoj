"""礼貌 HTTP 客户端（DATUMOJ-IVY-103）：全部走 httpx.MockTransport / ReplayTransport，不联网、不真的 sleep。"""

import gzip
import logging
from datetime import UTC, timedelta
from email.utils import format_datetime
from pathlib import Path
from urllib import robotparser

import httpx
import pytest

from datumoj.rikolto.core.contracts import UrlNotAllowed
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport, RobotsCache, RobotsRules
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.testing.contract_suites import HttpFetcherSuite
from datumoj.rikolto.testing.fakes import UnregisteredUrl
from datumoj.rikolto.testing.samples import T0, make_spec

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BASE = "https://example.go.jp"
ROBOTS = f"{BASE}/robots.txt"


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class Site:
    """MockTransport 的路由：URL → (状态, 头, 正文)、它们的列表（依次返回，最后一个重复）或 callable(request)。

    没登记的 robots.txt 回 404（不限制）；``robots`` 给了文本就回 200。``log`` 记录 (URL, 请求时刻)。
    """

    def __init__(self, routes=None, robots=None, clock=None):
        self.routes = dict(routes or {})
        self.robots = robots
        self.clock = clock
        self.log: list[tuple[str, float | None]] = []
        self.headers: list[httpx.Headers] = []

    @property
    def urls(self) -> list[str]:
        return [u for u, _ in self.log]

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.log.append((url, self.clock() if self.clock else None))
        self.headers.append(request.headers)
        spec = self.routes.get(url)
        if spec is None and request.url.path == "/robots.txt":
            spec = (404, {}, b"") if self.robots is None else (200, {}, self.robots.encode())
        assert spec is not None, f"测试没有登记 {url}"
        if isinstance(spec, list):
            spec = spec.pop(0) if len(spec) > 1 else spec[0]
        if callable(spec):
            return spec(request)
        status, headers, body = spec
        return raw_response(status, headers, body)


def raw_response(status, headers, body):
    """像真实网络那样给原始字节（content= 会让 httpx 先按 Content-Encoding 解码）。"""
    return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body))


def ok(body=b"ok", **headers):
    return (200, headers, body)


def make(site, clock=None, *, allowed_hosts=("example.go.jp",), **kw):
    clock = clock or FakeClock()
    return PoliteHttpFetcher(
        allowed_hosts, transport=httpx.MockTransport(site.handle), clock=clock, sleep=clock.sleep,
        now=lambda: T0, **kw,
    )


def allow_all(*robots_urls):
    """预先放好“不限制”的 robots 缓存，测试里的 sleep 就只剩被测的那部分。"""
    cache = RobotsCache()
    for url in robots_urls or (ROBOTS,):
        cache.get(url, RobotsRules)
    return cache


# ---------- 合格测试 ----------


class TestPoliteHttpFetcherSuite(HttpFetcherSuite):
    @pytest.fixture
    def make_fetcher(self):
        def build(routes, max_bytes=None):
            table = {str(httpx.URL(u)): r for u, r in routes.items()}

            def handle(request):
                if request.url.path == "/robots.txt":
                    return httpx.Response(404)
                resp = table[str(request.url)]
                if resp.network_error:
                    raise httpx.ConnectError(resp.network_error, request=request)
                status, headers, body = resp.respond(
                    request.headers.get("if-none-match"), request.headers.get("if-modified-since")
                )
                return raw_response(status, headers, body)

            clock = FakeClock()
            return PoliteHttpFetcher(
                ["example.go.jp"], max_bytes=max_bytes, transport=httpx.MockTransport(handle),
                clock=clock, sleep=clock.sleep,
            )

        return build


# ---------- 2. 限速 ----------


def test_rate_limit_is_per_host_and_never_really_sleeps():
    clock = FakeClock()
    other = "https://www.stat.go.jp"
    site = Site({f"{BASE}/a": ok(), f"{BASE}/b": ok(), f"{other}/c": ok()}, clock=clock)
    fetcher = make(site, clock, allowed_hosts=["example.go.jp", "*.stat.go.jp"])
    for url in (f"{BASE}/a", f"{BASE}/b", f"{other}/c"):
        assert fetcher.fetch(url).outcome is FetchOutcome.ok
    assert site.log == [
        (ROBOTS, 0.0), (f"{BASE}/a", 1.0), (f"{BASE}/b", 2.0),
        (f"{other}/robots.txt", 2.0),  # 另一个 host 不用等
        (f"{other}/c", 3.0),
    ]
    assert clock.sleeps == [1.0, 1.0, 1.0]


def test_rate_comes_from_spec():
    clock = FakeClock()
    site = Site({f"{BASE}/a": ok(), f"{BASE}/b": ok()}, clock=clock)
    spec = make_spec(rate_per_sec=0.5, max_total_bytes=5)
    fetcher = PoliteHttpFetcher.from_spec(
        spec, transport=httpx.MockTransport(site.handle), clock=clock, sleep=clock.sleep, robots=allow_all()
    )
    assert fetcher.max_bytes == 5
    fetcher.fetch(f"{BASE}/a")
    fetcher.fetch(f"{BASE}/b")
    assert clock.sleeps == [2.0]


@pytest.mark.parametrize("kw", [{"rate_per_sec": 0}, {"max_bytes": 0}, {"max_retries": -1}])
def test_invalid_settings(kw):
    with pytest.raises(ValueError):
        make(Site(), **kw)


# ---------- 3. 条件请求与重试 ----------


def test_conditional_request_headers_and_304():
    lm = "Tue, 20 Oct 2026 00:00:00 GMT"
    site = Site({f"{BASE}/x": (304, {"ETag": '"v1"'}, b"")})
    r = make(site).fetch(f"{BASE}/x", etag='"v1"', last_modified=lm)
    assert (r.outcome, r.http_status, r.content, r.etag) == (FetchOutcome.not_modified, 304, None, '"v1"')
    sent = site.headers[-1]
    assert sent["if-none-match"] == '"v1"' and sent["if-modified-since"] == lm


def test_no_conditional_headers_without_validators():
    site = Site({f"{BASE}/x": ok()})
    make(site).fetch(f"{BASE}/x")
    assert "if-none-match" not in site.headers[-1] and "if-modified-since" not in site.headers[-1]


def test_5xx_retried_with_exponential_backoff():
    clock = FakeClock()
    site = Site({f"{BASE}/x": [(503, {}, b""), (502, {}, b""), ok(b"done")]})
    r = make(site, clock, robots=allow_all()).fetch(f"{BASE}/x")
    assert (r.outcome, r.content) == (FetchOutcome.ok, b"done")
    assert clock.sleeps == [1.0, 2.0]


def test_retries_are_capped():
    clock = FakeClock()
    site = Site({f"{BASE}/x": (500, {}, b"")})
    r = make(site, clock, robots=allow_all(), max_retries=3).fetch(f"{BASE}/x")
    assert (r.outcome, r.http_status) == (FetchOutcome.http_error, 500)
    assert len(site.log) == 4
    assert clock.sleeps == [1.0, 2.0, 4.0]


def test_backoff_capped_by_max_wait():
    clock = FakeClock()
    site = Site({f"{BASE}/x": (500, {}, b"")})
    make(site, clock, robots=allow_all(), max_retries=4, backoff=10, max_wait=25).fetch(f"{BASE}/x")
    assert clock.sleeps == [10.0, 20.0, 25.0, 25.0]


def test_retry_after_seconds_is_respected():
    clock = FakeClock()
    site = Site({f"{BASE}/x": [(429, {"Retry-After": "7"}, b""), ok()]})
    r = make(site, clock, robots=allow_all()).fetch(f"{BASE}/x")
    assert r.outcome is FetchOutcome.ok
    assert clock.sleeps == [7.0]


def test_retry_after_http_date_is_respected():
    clock = FakeClock()
    when = format_datetime((T0 + timedelta(seconds=30)).astimezone(UTC), usegmt=True)
    site = Site({f"{BASE}/x": [(503, {"Retry-After": when}, b""), ok()]})
    assert make(site, clock, robots=allow_all()).fetch(f"{BASE}/x").outcome is FetchOutcome.ok
    assert clock.sleeps == [30.0]


def test_retry_after_longer_than_max_wait_gives_up():
    clock = FakeClock()
    site = Site({f"{BASE}/x": (429, {"Retry-After": "3600"}, b"")})
    r = make(site, clock, robots=allow_all(), max_wait=60).fetch(f"{BASE}/x")
    assert (r.outcome, r.http_status) == (FetchOutcome.http_error, 429)
    assert "Retry-After" in r.error
    assert len(site.log) == 1 and clock.sleeps == []


def test_network_error_retried_then_reported():
    clock = FakeClock()

    def down(request):
        raise httpx.ConnectError("connection refused", request=request)

    site = Site({f"{BASE}/x": down})
    r = make(site, clock, robots=allow_all(), max_retries=2).fetch(f"{BASE}/x")
    assert r.outcome is FetchOutcome.network_error and "ConnectError" in r.error
    assert len(site.log) == 3 and clock.sleeps == [1.0, 2.0]


def test_network_error_then_success():
    def flaky(request):
        raise httpx.ReadTimeout("timed out", request=request)

    site = Site({f"{BASE}/x": [flaky, ok(b"back")]})
    r = make(site, robots=allow_all()).fetch(f"{BASE}/x")
    assert (r.outcome, r.content) == (FetchOutcome.ok, b"back")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_other_4xx_not_retried(status):
    clock = FakeClock()
    site = Site({f"{BASE}/x": (status, {}, b"")})
    r = make(site, clock, robots=allow_all()).fetch(f"{BASE}/x")
    assert (r.outcome, r.http_status) == (FetchOutcome.http_error, status)
    assert len(site.log) == 1 and clock.sleeps == []


# ---------- 4. 大小上限 ----------


def counting_stream(n_chunks, chunk):
    pulled = []

    def gen():
        for _ in range(n_chunks):
            pulled.append(1)
            yield chunk

    return gen, pulled


def test_too_large_aborts_while_streaming():
    gen, pulled = counting_stream(1000, b"x" * 1024)
    site = Site({f"{BASE}/big": lambda request: httpx.Response(200, content=gen())})
    r = make(site, max_bytes=4096).fetch(f"{BASE}/big")
    assert r.outcome is FetchOutcome.too_large and r.content is None and "4096" in r.error
    assert len(pulled) <= 5  # 读到第 5 块就超限，后面的不再读


def test_too_large_by_content_length_reads_nothing():
    def never():
        raise AssertionError("不应该读正文")
        yield b""

    site = Site({f"{BASE}/big": lambda request: httpx.Response(200, headers={"Content-Length": "999999"},
                                                                content=never())})
    assert make(site, max_bytes=100).fetch(f"{BASE}/big").outcome is FetchOutcome.too_large


def test_body_exactly_at_limit_is_ok():
    site = Site({f"{BASE}/x": ok(b"y" * 100)})
    r = make(site, max_bytes=100).fetch(f"{BASE}/x")
    assert r.outcome is FetchOutcome.ok and len(r.content) == 100


# ---------- 5. 重定向逐跳检查 ----------


def test_redirect_followed():
    site = Site({f"{BASE}/old": (301, {"Location": "/new"}, b""), f"{BASE}/new": ok(b"moved")})
    r = make(site).fetch(f"{BASE}/old")
    assert (r.outcome, r.content, r.url, r.final_url) == (FetchOutcome.ok, b"moved", f"{BASE}/old", f"{BASE}/new")


def test_no_redirect_means_no_final_url():
    assert make(Site({f"{BASE}/x": ok()})).fetch(f"{BASE}/x").final_url is None


@pytest.mark.parametrize("location", [
    "https://evil.example.com/x",
    "https://go.jp.evil.com/x",
    "ftp://example.go.jp/x",
    "file:///etc/passwd",
    "http://127.0.0.1/x",
    "http://10.0.0.8/x",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1]/x",
    "http://[::ffff:127.0.0.1]/x",
    "http://localhost/x",
])
def test_redirect_hop_rejected(location):
    allowed = ["example.go.jp", "*.go.jp", "127.0.0.1", "10.0.0.8", "169.254.169.254", "::1", "::ffff:127.0.0.1",
               "localhost"]
    site = Site({f"{BASE}/start": (302, {"Location": location}, b"")})
    r = make(site, allowed_hosts=allowed).fetch(f"{BASE}/start")
    assert r.outcome is FetchOutcome.network_error and r.content is None
    assert "重定向被拦下" in r.error
    assert site.urls == [ROBOTS, f"{BASE}/start"]  # 被拦下的目标没有请求


def test_host_resolving_to_private_address_rejected():
    dns = {"example.go.jp": ["160.17.1.1"], "intranet.go.jp": ["10.1.2.3"]}
    site = Site({f"{BASE}/start": (302, {"Location": "https://intranet.go.jp/x"}, b"")})
    fetcher = make(site, allowed_hosts=["*.go.jp", "example.go.jp"], resolver=dns.__getitem__)
    r = fetcher.fetch(f"{BASE}/start")
    assert r.outcome is FetchOutcome.network_error and "10.1.2.3" in r.error
    with pytest.raises(UrlNotAllowed):
        fetcher.fetch("https://intranet.go.jp/x")


@pytest.mark.parametrize("url,allowed", [
    ("https://www.e-stat.go.jp/x", True),
    ("https://example.go.jp/x", True),
    ("https://go.jp.evil.com/x", False),
    ("https://evilgo.jp/x", False),
    ("https://go.jp/x", False),
])
def test_wildcard_allowed_hosts(url, allowed):
    site = Site({url: ok()})
    fetcher = make(site, allowed_hosts=["*.go.jp"])
    if allowed:
        assert fetcher.fetch(url).outcome is FetchOutcome.ok
    else:
        with pytest.raises(UrlNotAllowed):
            fetcher.fetch(url)
        assert site.log == []


@pytest.mark.parametrize("url", [
    "ftp://example.go.jp/x", "https://other.example.com/x", "http://127.0.0.1/x", "http://[::1]/x",
    "http://[::ffff:10.0.0.1]/x", "http://localhost/x", "http://192.168.1.1/x",
])
def test_initial_url_rejected(url):
    site = Site()
    fetcher = make(site, allowed_hosts=["example.go.jp", "127.0.0.1", "::1", "::ffff:10.0.0.1", "localhost",
                                        "192.168.1.1"])
    with pytest.raises(UrlNotAllowed):
        fetcher.fetch(url)
    assert site.log == []


def test_malformed_url_is_value_error():
    with pytest.raises(ValueError) as exc:
        make(Site()).fetch("https://example.go.jp:notaport/x?appId=SECRET")
    assert "SECRET" not in str(exc.value) and exc.value.__cause__ is None


def test_too_many_redirects():
    site = Site({f"{BASE}/loop": (302, {"Location": "/loop"}, b"")})
    r = make(site, max_redirects=3).fetch(f"{BASE}/loop")
    assert r.outcome is FetchOutcome.network_error and "重定向超过 3 次" in r.error
    assert site.urls.count(f"{BASE}/loop") == 4


def test_redirect_without_location_is_http_error():
    r = make(Site({f"{BASE}/x": (302, {}, b"")})).fetch(f"{BASE}/x")
    assert (r.outcome, r.http_status) == (FetchOutcome.http_error, 302)


# ---------- 6、13. 编码与 gzip ----------


def test_requests_identity_and_decodes_gzip_anyway():
    body = "人口,1234\n".encode("cp932")
    site = Site({f"{BASE}/x": ok(gzip.compress(body), **{"Content-Encoding": "gzip", "Content-Type": "text/csv"})})
    r = make(site).fetch(f"{BASE}/x")
    assert (r.outcome, r.content, r.content_type) == (FetchOutcome.ok, body, "text/csv")
    assert all(h["accept-encoding"] == "identity" for h in site.headers)


def test_multi_member_gzip():
    stream = gzip.compress(b"part1-") + gzip.compress(b"part2-") + gzip.compress(b"part3")
    site = Site({f"{BASE}/x": ok(stream, **{"Content-Encoding": "gzip"})})
    assert make(site).fetch(f"{BASE}/x").content == b"part1-part2-part3"


def test_multi_member_split_across_chunks_with_padding():
    stream = gzip.compress(b"a" * 70_000) + gzip.compress(b"b" * 10) + b"\0\0\0"
    pieces = [stream[i:i + 7] for i in range(0, len(stream), 7)]
    site = Site({f"{BASE}/x": lambda request: httpx.Response(200, headers={"Content-Encoding": "gzip"},
                                                              content=iter(pieces))})
    assert make(site).fetch(f"{BASE}/x").content == b"a" * 70_000 + b"b" * 10


def test_gzip_limit_counts_decoded_total_across_members():
    stream = b"".join(gzip.compress(b"z" * 40) for _ in range(3))  # 每段 40 字节，合计 120
    assert len(stream) < 100
    site = Site({f"{BASE}/x": ok(stream, **{"Content-Encoding": "gzip"})})
    assert make(site, max_bytes=100).fetch(f"{BASE}/x").outcome is FetchOutcome.too_large
    assert make(Site({f"{BASE}/x": ok(stream, **{"Content-Encoding": "gzip"})}), max_bytes=120).fetch(
        f"{BASE}/x").content == b"z" * 120


def test_gzip_bomb_aborts_while_decoding():
    member = gzip.compress(b"\0" * (1 << 20))  # 1 MiB 解压后，压缩后约 1 KiB
    gen, pulled = counting_stream(1000, member)
    site = Site({f"{BASE}/x": lambda request: httpx.Response(200, headers={"Content-Encoding": "gzip"},
                                                              content=gen())})
    r = make(site, max_bytes=100_000).fetch(f"{BASE}/x")
    assert r.outcome is FetchOutcome.too_large
    assert len(pulled) == 1  # 第一段还没解完就超限


@pytest.mark.parametrize("body", [b"not gzip at all", gzip.compress(b"x" * 1000)[:-12],
                                  gzip.compress(b"ok") + b"trailing garbage"])
def test_broken_gzip_is_network_error(body):
    site = Site({f"{BASE}/x": ok(body, **{"Content-Encoding": "gzip"})})
    r = make(site).fetch(f"{BASE}/x")
    assert r.outcome is FetchOutcome.network_error and "_BodyError" in r.error and r.content is None


def test_unsupported_content_encoding():
    r = make(Site({f"{BASE}/x": ok(b"...", **{"Content-Encoding": "br"})})).fetch(f"{BASE}/x")
    assert r.outcome is FetchOutcome.network_error and "_BodyError" in r.error


# ---------- 7、13. URL 去 Key ----------


def test_keys_removed_from_results_errors_and_logs(caplog):
    url = f"{BASE}/api?appId=SECRET1&q=1"
    site = Site({
        url: (302, {"Location": "/api2?api_key=SECRET2&q=2"}, b""),
        f"{BASE}/api2?api_key=SECRET2&q=2": [(503, {}, b""), (404, {}, b"")],
    })
    with caplog.at_level(logging.DEBUG):
        r = make(site).fetch(url)
    assert r.outcome is FetchOutcome.http_error
    assert r.url == f"{BASE}/api?appId=REDACTED&q=1"
    assert r.final_url == f"{BASE}/api2?api_key=REDACTED&q=2"
    dumped = r.model_dump_json() + repr(r) + r.error
    assert "SECRET" not in dumped
    httpx_lines = [rec.getMessage() for rec in caplog.records if rec.name == "httpx"]
    assert httpx_lines and all("REDACTED" in m for m in httpx_lines if "/api" in m)
    assert "SECRET" not in caplog.text
    assert any("重试" in rec.getMessage() for rec in caplog.records)  # 自己的重试日志也在里面


def test_keys_removed_from_network_errors_and_exceptions():
    def down(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    url = f"{BASE}/api?token=SECRET&x=1"
    r = make(Site({url: down}), max_retries=0).fetch(url)
    assert r.outcome is FetchOutcome.network_error and "token=REDACTED" in r.error and "SECRET" not in r.error
    with pytest.raises(UrlNotAllowed) as exc:
        make(Site()).fetch("https://other.example.com/api?appId=SECRET")
    assert "SECRET" not in str(exc.value) and "REDACTED" in str(exc.value)


def test_redirect_rejection_message_redacted():
    site = Site({f"{BASE}/x": (302, {"Location": "https://evil.example.com/?key=SECRET"}, b"")})
    r = make(site).fetch(f"{BASE}/x")
    assert "SECRET" not in r.error and "key=REDACTED" in r.error


# ---------- 8、12. robots.txt ----------


def test_robots_disallow_blocks_without_request_and_is_cached():
    site = Site({f"{BASE}/public": ok()}, robots="User-agent: *\nDisallow: /private\n")
    fetcher = make(site)
    r = fetcher.fetch(f"{BASE}/private/data")
    assert r.outcome is FetchOutcome.blocked_by_robots and "Disallow: /private" in r.error and r.content is None
    assert fetcher.fetch(f"{BASE}/public").outcome is FetchOutcome.ok
    assert fetcher.fetch(f"{BASE}/private").outcome is FetchOutcome.blocked_by_robots
    assert site.urls == [ROBOTS, f"{BASE}/public"]  # robots.txt 只取一次，被禁止的从没请求


def test_robots_checked_again_on_every_redirect_hop():
    """审查打回的复现：/start 302 到被禁止的 /private/data，必须 blocked_by_robots 且不请求目标。"""
    site = Site({
        f"{BASE}/start": (302, {"Location": "/private/data"}, b""),
        f"{BASE}/private/data": ok(b"secret page"),
    }, robots="User-agent: *\nDisallow: /private\n")
    r = make(site).fetch(f"{BASE}/start")
    assert r.outcome is FetchOutcome.blocked_by_robots and r.content is None
    assert r.final_url == f"{BASE}/private/data"
    assert site.urls == [ROBOTS, f"{BASE}/start"]


def test_redirect_to_other_host_uses_that_hosts_robots():
    www = "https://www.example.go.jp"
    site = Site({
        f"{BASE}/start": (302, {"Location": f"{www}/data"}, b""),
        f"{www}/robots.txt": (200, {}, b"User-agent: *\nDisallow: /data\n"),
    })
    r = make(site, allowed_hosts=["example.go.jp", "www.example.go.jp"]).fetch(f"{BASE}/start")
    assert r.outcome is FetchOutcome.blocked_by_robots
    assert site.urls == [ROBOTS, f"{BASE}/start", f"{www}/robots.txt"]


def test_robots_cache_expires_after_ttl():
    clock = FakeClock()
    site = Site({f"{BASE}/a": ok()})
    fetcher = make(site, clock, robots=RobotsCache(ttl=100, clock=clock))
    fetcher.fetch(f"{BASE}/a")
    fetcher.fetch(f"{BASE}/a")
    clock.t += 100
    fetcher.fetch(f"{BASE}/a")
    assert site.urls.count(ROBOTS) == 2


@pytest.mark.parametrize("reply", [(503, {}, b""), (429, {}, b""), "network"])
def test_unreachable_robots_disallows_everything(reply):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    site = Site({ROBOTS: down if reply == "network" else reply, f"{BASE}/a": ok()})
    r = make(site, max_retries=1).fetch(f"{BASE}/a")
    assert r.outcome is FetchOutcome.blocked_by_robots and "全部禁止" in r.error
    assert f"{BASE}/a" not in site.urls


@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_missing_robots_allows_everything(status):
    site = Site({ROBOTS: (status, {}, b""), f"{BASE}/a": ok()})
    assert make(site).fetch(f"{BASE}/a").outcome is FetchOutcome.ok


def test_robots_redirect_followed():
    site = Site({
        ROBOTS: (301, {"Location": "/robots-v2.txt"}, b""),
        f"{BASE}/robots-v2.txt": (200, {}, b"User-agent: *\nDisallow: /a\n"),
    })
    assert make(site).fetch(f"{BASE}/a").outcome is FetchOutcome.blocked_by_robots


def test_robots_gzip_and_percent_encoded_request_path():
    robots = gzip.compress(b"User-agent: *\nDisallow: /private\n")
    site = Site({ROBOTS: (200, {"Content-Encoding": "gzip"}, robots)})
    assert make(site).fetch(f"{BASE}/%70rivate/x").outcome is FetchOutcome.blocked_by_robots


def rules(text, agent="datumoj"):
    return RobotsRules.parse(text, agent)


def allowed(text, path, agent="datumoj"):
    return rules(text, agent).blocked_reason(path) is None


@pytest.mark.parametrize("text", [
    "User-agent: *\nDisallow: /p\nAllow: /p/public\n",
    "User-agent: *\nAllow: /p/public\nDisallow: /p\n",
])
def test_longest_match_wins_regardless_of_order(text):
    assert allowed(text, "/p/public/x")
    assert not allowed(text, "/p/other")


def test_not_robotparser_first_match_semantics():
    text = "User-agent: *\nDisallow: /p\nAllow: /p/public\n"
    parser = robotparser.RobotFileParser()
    parser.parse(text.splitlines())
    assert not parser.can_fetch("datumoj", "https://example.go.jp/p/public/x")  # 标准库按书写顺序
    assert allowed(text, "/p/public/x")  # RFC 9309 按最长匹配


@pytest.mark.parametrize("text", [
    "User-agent: *\nDisallow: /page\nAllow: /page\n",
    "User-agent: *\nAllow: /page\nDisallow: /page\n",
    "User-agent: *\nDisallow: /*.php\nAllow: /a.php\n",
])
def test_equal_length_allow_wins(text):
    path = "/a.php" if "php" in text else "/page"
    assert allowed(text, path)


@pytest.mark.parametrize("pattern,path,blocked", [
    ("/*.pdf$", "/a/b.pdf", True),
    ("/*.pdf$", "/a/b.pdf?x=1", False),
    ("/*.pdf$", "/a/b.pdfx", False),
    ("/tmp*/x", "/tmp123/x", True),
    ("/tmp*/x", "/tmp123/y", False),
    ("/*?appId=", "/api?appId=1", True),
    ("/exact$", "/exact", True),
    ("/exact$", "/exact/more", False),
    ("/private", "/%70rivate/x", True),  # %70 = p，非保留字符要解码
    ("/%70rivate", "/private/x", True),
    ("/a%2Fb", "/a/b", False),  # %2F 是保留字符，不解码
    ("/a/b", "/a%2Fb", False),
    ("/a%2fb", "/a%2Fb", True),  # 十六进制大小写不影响
    ("/統計", "/%E7%B5%B1%E8%A8%88/x", True),  # 非 ASCII 按 UTF-8 编码后比较
    ("/%7Euser", "/~user/x", True),
])
def test_pattern_matching(pattern, path, blocked):
    assert allowed(f"User-agent: *\nDisallow: {pattern}\n", path) is not blocked


def test_agent_specific_group_overrides_star():
    text = "User-agent: *\nDisallow: /\n\nUser-agent: DATUMOJ\nAllow: /\nDisallow: /secret\n"
    assert allowed(text, "/anything")
    assert not allowed(text, "/secret/x")
    assert not allowed(text, "/anything", agent="otherbot")


def test_groups_comments_and_empty_rules():
    text = (
        "﻿# comment\nUser-agent: a\nUser-agent: datumoj/2.0  # 多个 user-agent 共用一组\nDisallow: /x\n"
        "Sitemap: https://example.go.jp/sitemap.xml\nDisallow: /y\n\nUser-agent: *\nDisallow:\n"
    )
    assert not allowed(text, "/x") and not allowed(text, "/y/1")
    assert allowed(text, "/z")
    assert allowed("User-agent: *\nDisallow:\n", "/anything")
    assert allowed("", "/anything")


def test_robots_txt_itself_is_never_blocked():
    site = Site(robots="User-agent: *\nDisallow: /\n")
    r = make(site).fetch(ROBOTS)
    assert r.outcome is FetchOutcome.ok


# ---------- 9、11. 回放 ----------

STAT = "https://dashboard.e-stat.go.jp"
INFO_URL = f"{STAT}/api/1.0/Json/getIndicatorInfo?Lang=JP&Category=0204&RegionalRank=3"
INFO_FILE = FIXTURES / "stat_dashboard" / "getIndicatorInfo_category0204_rank3.json"


def replay_fetcher(transport, hosts=("example.go.jp", "dashboard.e-stat.go.jp")):
    clock = FakeClock()
    return PoliteHttpFetcher(hosts, transport=transport, clock=clock, sleep=clock.sleep)


def test_replay_real_fixture():
    transport = ReplayTransport({
        f"{STAT}/robots.txt": (None, 404),
        INFO_URL: Recorded(INFO_FILE, headers={"Content-Type": "application/json;charset=UTF-8"}),
    })
    r = replay_fetcher(transport).fetch(INFO_URL)
    assert (r.outcome, r.http_status, r.content) == (FetchOutcome.ok, 200, INFO_FILE.read_bytes())
    assert r.content_type == "application/json;charset=UTF-8"


def test_replay_ignores_keys_and_param_order():
    transport = ReplayTransport({
        ROBOTS: (None, 404),
        f"{BASE}/api?b=2&a=1&appId=REDACTED": INFO_FILE,
        f"{BASE}/api2?q=1": (INFO_FILE, 200),
    })
    fetcher = replay_fetcher(transport)
    assert fetcher.fetch(f"{BASE}/api?a=1&appId=REALKEY&b=2").outcome is FetchOutcome.ok
    assert fetcher.fetch(f"{BASE}/api2?api_key=REALKEY&q=1").outcome is FetchOutcome.ok
    assert "REALKEY" not in " ".join(transport.requests)


def test_replay_status_codes():
    transport = ReplayTransport({
        ROBOTS: (None, 404), f"{BASE}/gone": (None, 410), f"{BASE}/same": Recorded(status=304),
    })
    fetcher = replay_fetcher(transport)
    r = fetcher.fetch(f"{BASE}/gone")
    assert (r.outcome, r.http_status) == (FetchOutcome.http_error, 410)
    assert fetcher.fetch(f"{BASE}/same", etag='"v"').outcome is FetchOutcome.not_modified


def test_replay_unregistered_url_raises_through_fetcher_without_retry():
    transport = ReplayTransport({ROBOTS: (None, 404)})
    with pytest.raises(UnregisteredUrl) as exc:
        replay_fetcher(transport).fetch(f"{BASE}/missing?appId=SECRET")
    assert "SECRET" not in str(exc.value)
    assert transport.requests == [ROBOTS, f"{BASE}/missing?appId=REDACTED"]


def test_replay_unregistered_robots_raises():
    with pytest.raises(UnregisteredUrl):
        replay_fetcher(ReplayTransport({f"{BASE}/a": INFO_FILE})).fetch(f"{BASE}/a")


@pytest.mark.parametrize("registered,requested", [("/a%2Fb", "/a/b"), ("/a/b", "/a%2Fb")])
def test_replay_compares_raw_path(registered, requested):
    transport = ReplayTransport({f"{BASE}{registered}": INFO_FILE})
    with httpx.Client(transport=transport) as client:
        assert client.get(f"{BASE}{registered}").status_code == 200
        with pytest.raises(UnregisteredUrl):
            client.get(f"{BASE}{requested}")


def test_replay_identifies_keys_by_name_not_value():
    transport = ReplayTransport({f"{BASE}/q?note=REDACTED&q=1": INFO_FILE})
    with httpx.Client(transport=transport) as client:
        assert client.get(f"{BASE}/q?q=1&note=REDACTED").status_code == 200
        for other in ("/q?q=1", "/q?note=other&q=1"):
            with pytest.raises(UnregisteredUrl):
                client.get(f"{BASE}{other}")


def test_replay_missing_fixture_fails_at_construction(tmp_path):
    with pytest.raises(FileNotFoundError):
        ReplayTransport({f"{BASE}/a": tmp_path / "nope.json"})


def test_replay_duplicate_after_redaction_rejected():
    with pytest.raises(ValueError):
        ReplayTransport({f"{BASE}/a?appId=1": INFO_FILE, f"{BASE}/a?appId=2": INFO_FILE})
