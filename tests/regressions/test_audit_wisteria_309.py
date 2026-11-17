"""WISTERIA-309：key 只走请求头、只在发请求那一刻取值，不进任何落盘或输出。

端到端：真实 run_crawl、SqliteManifest、FileRawStore、PoliteHttpFetcher；HTTP 只到本地 pytest-httpserver。
每个泄露检查都先断言服务器确实收到了 key（正对照），再在各处搜索这个值。
"""

import json
import logging
import secrets
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Response

import datumoj.rikolto.core.http as http_module
from datumoj.rikolto.core.contracts import Crawler, FetchTask
from datumoj.rikolto.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.rawstore import FileRawStore
from datumoj.rikolto.core.runner import CrawlDeps, run_crawl
from datumoj.rikolto.testing.samples import T0, make_spec

HOST = "example.go.jp"
ACCEPT = "application/vnd.sdmx.data+csv;version=1.0.0"
KEY_HEADER = "Ocp-Apim-Subscription-Key"
KEY_ENV = "WISTERIA_309_KEY"
GOOD, BAD = f"https://{HOST}/good", f"https://{HOST}/bad"


@pytest.fixture
def fake_key(monkeypatch):
    key = "k" + secrets.token_hex(20)
    monkeypatch.setenv(KEY_ENV, key)
    return key


class HeaderCrawler(Crawler):
    kind = "fake"

    def __init__(self, urls):
        self.urls = urls

    def plan(self, ctx):
        for url in self.urls:
            yield FetchTask(url=url, headers={"Accept": ACCEPT}, meta={"role": "data"})


def spec_with_key():
    return make_spec(needs_key=f"env:{KEY_ENV}", key_header=KEY_HEADER, scope={"urls": [GOOD]})


def everything_under(root: Path) -> bytes:
    return b"\n".join(p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file())


def crawl(transport, root, urls, caplog=None, **fetcher_kwargs):
    spec = spec_with_key()
    fetcher = PoliteHttpFetcher.from_spec(
        spec, transport=transport, sleep=lambda s: None, now=lambda: T0 + timedelta(minutes=1), **fetcher_kwargs,
    )
    raw, manifest = FileRawStore(root), SqliteManifest(root / "manifest.sqlite")
    deps = CrawlDeps(raw, manifest, fetcher, sleep=lambda s: None, now=lambda: T0)
    try:
        report = run_crawl(spec, HeaderCrawler(urls), deps)
        snapshots = [manifest.latest_snapshot(u) for u in urls]
        records = [(s.model_dump_json(), json.dumps(manifest.snapshot_meta(s.snapshot_id))) for s in snapshots if s]
        return report, snapshots, records
    finally:
        fetcher.close()
        manifest.close()


@pytest.fixture
def site(httpserver):
    received = []
    with httpx.Client(trust_env=False, timeout=5) as local:
        def forward(request):
            assert request.url.host == HOST, "测试禁止访问外网"
            response = local.request(
                request.method, httpserver.url_for(request.url.path),
                headers={k: v for k, v in request.headers.items() if k != "host"},
            )
            return httpx.Response(response.status_code, headers=response.headers, stream=httpx.ByteStream(response.content))

        def route(path, status=200):
            def respond(request):
                received.append((request.path, dict(request.headers)))
                return Response(b"a,b\n1,2\n" if status == 200 else b"upstream error", status=status)

            httpserver.expect_request(path).respond_with_handler(respond)

        httpserver.expect_request("/robots.txt").respond_with_data(status=404)
        yield type("Site", (), {"route": staticmethod(route), "transport": httpx.MockTransport(forward),
                                "received": received})
    httpserver.check_assertions()


def test_server_gets_headers_and_key_value_is_nowhere_else(site, fake_key, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    site.route("/good")
    site.route("/bad", status=500)
    report, snapshots, records = crawl(site.transport, tmp_path, [GOOD, BAD])

    # 正对照：服务器收到了 Accept 和 key，所以下面“找不到”才有意义
    paths = [path for path, _ in site.received]
    assert paths[0] == "/good" and set(paths) == {"/good", "/bad"} and paths.count("/bad") > 1  # 500 会重试
    for _, headers in site.received:
        assert headers["Accept"] == ACCEPT and headers[KEY_HEADER] == fake_key
    assert [s.outcome.value for s in snapshots] == ["ok", "http_error"]

    haystacks = {
        "磁盘文件（manifest、raw）": everything_under(tmp_path).decode("latin-1"),
        "快照与 meta": "\n".join(a + b for a, b in records),
        "报告": repr(report),
        "日志": caplog.text + "".join(r.getMessage() for r in caplog.records),
        "快照错误": " ".join(s.error or "" for s in snapshots),
    }
    for name, text in haystacks.items():
        assert fake_key not in text, f"key 值出现在：{name}"
    assert caplog.records, "日志捕获为空，检查没有意义"


def test_missing_key_fails_the_task_with_variable_name_only(site, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv(KEY_ENV, raising=False)
    site.route("/good")
    report, snapshots, _ = crawl(site.transport, tmp_path, [GOOD])
    assert site.received == []
    assert snapshots[0].outcome.value == "network_error"
    assert snapshots[0].error == f"缺少 key（{KEY_ENV}）"
    assert report.failures == {"network_error": 1}


def test_task_header_named_like_key_header_fails_task_without_request(site, fake_key, tmp_path):
    class Clash(HeaderCrawler):
        def plan(self, ctx):
            yield FetchTask(url=GOOD, headers={KEY_HEADER.lower(): "x"})

    spec = spec_with_key()
    fetcher = PoliteHttpFetcher.from_spec(spec, transport=site.transport, sleep=lambda s: None)
    manifest = SqliteManifest(tmp_path / "m.sqlite")
    try:
        report = run_crawl(spec, Clash([]), CrawlDeps(FileRawStore(tmp_path), manifest, fetcher, now=lambda: T0))
        snapshot = manifest.latest_snapshot(GOOD)
    finally:
        fetcher.close()
        manifest.close()
    assert site.received == [] and snapshot.outcome.value == "network_error"
    assert "同名" in snapshot.error and fake_key not in snapshot.error
    assert report.failures == {"network_error": 1}


def test_replay_recording_never_holds_key(fake_key, tmp_path):
    body = tmp_path / "body.synthetic"
    body.write_bytes(b"a,b\n1,2\n")
    replay = ReplayTransport({f"https://{HOST}/robots.txt": Recorded(status=404), GOOD: Recorded(body)})
    report, snapshots, _ = crawl(replay, tmp_path, [GOOD])
    assert snapshots[0].outcome.value == "ok"
    sent = replay.request_headers[-1]
    assert sent["accept"] == ACCEPT and sent[KEY_HEADER.lower()] == "REDACTED"
    assert fake_key not in repr(replay.request_headers) + repr(replay.requests) + repr(report)


# ---------- 真实 transport：httpx / httpcore 的 DEBUG 日志 ----------


def test_debug_logs_of_real_transport_do_not_contain_key(monkeypatch, fake_key, caplog):
    # 地址检查拒绝回环地址；只在本测试里放行，请求仍只到本测试自己起的 127.0.0.1 服务器。
    monkeypatch.setattr(http_module, "_non_public", lambda text: False)
    monkeypatch.setenv("NO_PROXY", "*")
    received = []

    def respond(request):
        received.append(dict(request.headers))
        return Response(b"a,b\n1,2\n")

    with HTTPServer(host="127.0.0.1", port=0) as server:
        server.expect_request("/robots.txt").respond_with_data(status=404)
        server.expect_request("/data").respond_with_handler(respond)
        url = server.url_for("/data")
        spec = make_spec(
            needs_key=f"env:{KEY_ENV}", key_header=KEY_HEADER, allowed_hosts=["127.0.0.1"], scope={"urls": [url]},
        )
        caplog.set_level(logging.DEBUG)
        fetcher = PoliteHttpFetcher.from_spec(spec, sleep=lambda s: None, now=lambda: T0 + timedelta(minutes=1))
        with fetcher:
            result = fetcher.fetch(url, headers={"Accept": ACCEPT}, key_header=KEY_HEADER, key_env=KEY_ENV)
    assert result.outcome.value == "ok"
    assert received[0][KEY_HEADER] == fake_key and received[0]["Accept"] == ACCEPT
    assert any(r.name.startswith("httpcore") for r in caplog.records), "没有捕获到 httpcore 的 DEBUG 日志"
    assert fake_key not in caplog.text + repr(result)
