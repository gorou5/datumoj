from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from datumoj.rikolto.core.contracts import CrawlContext
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.crawlers.ckan import CkanCrawler
from datumoj.rikolto.testing.contract_suites import CrawlerSuite
from datumoj.rikolto.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore, FakeResponse, drive_crawler
from datumoj.rikolto.testing.samples import T0, make_spec

FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures" / "ckan_egov"
API = "https://data.e-gov.go.jp/data/api/action"
ALLOWED = ["data.e-gov.go.jp", "*.go.jp", "*.lg.jp"]
SEARCH = API + "/package_search?q=%2A%3A%2A&rows={rows}&start={start}&sort=id%20asc"
ACTIVITY = API + "/recently_changed_packages_activity_list?limit=100"
SHOW = API + "/package_show?id={id}"
CSV_URL = "https://www.npa.go.jp/a/data.csv"
RUN1 = datetime(2026, 10, 1, tzinfo=timezone.utc)  # 早于 fixture 里的活动时间戳（2026-10-16 起）
RUN2 = datetime(2026, 10, 20, tzinfo=timezone.utc)


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _spec(rows: int = 100, hosts: list[str] = ALLOWED, **scope):
    return make_spec(
        source_id="jp-egov-data", kind="ckan", base_url=API, allowed_hosts=hosts,
        scope={"rows": rows, "activity_limit": 100, **scope},
    )


def _res(url: str = CSV_URL, fmt: str = "CSV", size=100, license_id="CC-BY", rid: str = "r1") -> dict:
    return {"id": rid, "url": url, "format": fmt, "size": size, "license_id": license_id}


def _pkg(pid: str = "p1", resources=None, org: str = "org_0700", modified: str = "2026-01-01T00:00:00", **kw) -> dict:
    return {"id": pid, "name": pid, "license_id": None, "metadata_modified": modified,
            "organization": {"name": org, "title": "警察庁"}, "resources": [_res()] if resources is None else resources, **kw}


def _ok(result) -> bytes:
    return json.dumps({"success": True, "result": result}).encode()


def _search(pkgs: list[dict]) -> bytes:
    return _ok({"count": len(pkgs), "results": pkgs})


def _routes(pkgs: list[dict], rows: int = 100, body: bytes = b"a,b\n1,2\n") -> dict:
    routes: dict = {}
    for i in range(0, len(pkgs), rows):
        routes[SEARCH.format(rows=rows, start=i)] = _search(pkgs[i : i + rows])
    routes[SEARCH.format(rows=rows, start=len(pkgs))] = _search([])
    for pkg in pkgs:
        for res in pkg["resources"]:
            routes.setdefault(res["url"], FakeResponse(200, body, {"Content-Type": "text/csv"}))
    return routes


def _fetcher(routes, **kw) -> FakeHttpFetcher:
    return FakeHttpFetcher(routes, now=lambda: T0, **kw)


def _by_role(steps, role):
    return [s for s in steps if s.task.meta["role"] == role]


class TestCkanCrawlerContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return CkanCrawler()

    @pytest.fixture
    def spec(self):
        return _spec(incremental=False)  # 重跑不写新 blob：增量路径每次的活动列表正文不同，另有测试覆盖

    @pytest.fixture
    def make_fetcher(self):
        pkgs = [_pkg("p1"), _pkg("p2", [_res(rid="r2", url="https://www.npa.go.jp/b.csv")])]
        return lambda: _fetcher(_routes(pkgs))


# ---------- 分页 ----------


def test_paging_follows_actual_row_counts_until_empty_page():
    routes = {
        SEARCH.format(rows=5, start=0): _fixture("package_search_q-jinko_rows5_start0.json"),
        SEARCH.format(rows=5, start=5): _fixture("package_search_q-jinko_rows5_start5.json"),
        SEARCH.format(rows=5, start=10): _fixture("package_search_q-jinko_rows5_start100000_empty.json"),
    }
    fetcher = _fetcher(routes)
    steps = drive_crawler(CkanCrawler(), _spec(rows=5, hosts=["data.e-gov.go.jp"]), fetcher, now=T0)
    # count=1733 与实际无关：第 3 页空才停；资源不在 allowed_hosts，只有 3 个搜索请求
    assert fetcher.requests == [SEARCH.format(rows=5, start=s) for s in (0, 5, 10)]
    assert [s.result.outcome for s in steps] == [FetchOutcome.ok] * 3


def test_short_server_page_does_not_stop_paging():
    pkgs = [_pkg(f"p{i}", []) for i in range(3)]
    routes = {SEARCH.format(rows=5, start=0): _search(pkgs[:2]), SEARCH.format(rows=5, start=2): _search(pkgs[2:]),
              SEARCH.format(rows=5, start=3): _search([])}  # 服务端把 rows=5 截成 2
    fetcher = _fetcher(routes)
    drive_crawler(CkanCrawler(), _spec(rows=5), fetcher, now=T0)
    assert len(fetcher.requests) == 3


def test_limit_caps_datasets():
    pkgs = [_pkg(f"p{i}", []) for i in range(4)]
    fetcher = _fetcher(_routes(pkgs, rows=2))
    drive_crawler(CkanCrawler(), _spec(rows=2), fetcher, limit=2, now=T0)
    assert fetcher.requests == [SEARCH.format(rows=2, start=0)]


# ---------- 增量 ----------


def _show_routes() -> dict:
    cao = json.loads(_fixture("package_show_cao_syukujitsu.json"))["result"]
    others = [_pkg("cb962514-ec67-45c1-8534-dac29d5fcca5", []), _pkg("0f732e1d-e0c4-4a3e-a0de-07937f905190", [])]
    routes = {ACTIVITY: _fixture("recently_changed_packages_activity_list_limit5.json"),
              SHOW.format(id=cao["id"]): _fixture("package_show_cao_syukujitsu.json"),
              cao["resources"][0]["url"]: b"x,y\n"}
    routes.update({SHOW.format(id=p["id"]): _ok(p) for p in others})
    return routes


def test_incremental_uses_activity_list_and_dedups_datasets():
    manifest, raw = FakeManifest(), FakeRawStore()
    full = _fetcher(_routes([_pkg("p1")]))
    drive_crawler(CkanCrawler(), _spec(), full, manifest=manifest, raw=raw, now=RUN1)
    assert full.requests[0].startswith(API + "/package_search")

    inc = _fetcher(_show_routes())
    steps = drive_crawler(CkanCrawler(), _spec(), inc, manifest=manifest, raw=raw, now=RUN2)
    shows = [r for r in inc.requests if "package_show" in r]
    assert inc.requests[0] == ACTIVITY and len(shows) == 3 == len(set(shows))  # 5 条活动 -> 3 个数据集
    assert not [r for r in inc.requests if "package_search" in r]
    assert len(_by_role(steps, "resource")) == 1  # cao 的 CSV


def test_incremental_falls_back_to_full_when_activity_unavailable():
    manifest = FakeManifest()
    drive_crawler(CkanCrawler(), _spec(), _fetcher(_routes([_pkg("p1")])), manifest=manifest, now=RUN1)
    routes = _routes([_pkg("p1")])
    routes[ACTIVITY] = FakeResponse(500)
    steps = drive_crawler(CkanCrawler(), _spec(), _fetcher(routes), manifest=manifest, now=RUN2)
    search = _by_role(steps, "package_search")
    assert steps[0].result.outcome is FetchOutcome.http_error
    assert search and "不可用" in search[0].task.meta["incremental_fallback"]


def test_incremental_falls_back_when_activity_window_too_small():
    manifest = FakeManifest()
    drive_crawler(CkanCrawler(), _spec(), _fetcher(_routes([_pkg("p1")])), manifest=manifest, now=RUN1)
    routes = _routes([_pkg("p1")])
    # 请求 5 条、返回 5 条，且最旧一条（10-16）仍比上次运行（10-01）新：窗口可能漏了变更
    routes[API + "/recently_changed_packages_activity_list?limit=5"] = _fixture(
        "recently_changed_packages_activity_list_limit5.json")
    steps = drive_crawler(CkanCrawler(), _spec(activity_limit=5), _fetcher(routes), manifest=manifest, now=RUN2)
    assert "窗口" in _by_role(steps, "package_search")[0].task.meta["incremental_fallback"]


def test_incremental_can_be_disabled_and_unchanged_datasets_are_not_redownloaded():
    manifest, raw = FakeManifest(), FakeRawStore()
    pkgs = [_pkg("p1")]
    drive_crawler(CkanCrawler(), _spec(incremental=False), _fetcher(_routes(pkgs)), manifest=manifest, raw=raw, now=RUN1)
    again = _fetcher(_routes(pkgs))
    drive_crawler(CkanCrawler(), _spec(incremental=False), again, manifest=manifest, raw=raw, now=RUN2)
    assert CSV_URL not in again.requests
    changed = _fetcher(_routes([_pkg("p1", modified="2026-10-10T00:00:00")]))
    drive_crawler(CkanCrawler(), _spec(incremental=False), changed, manifest=manifest, raw=raw, now=RUN2)
    assert CSV_URL in changed.requests


# ---------- 白名单 ----------


def _downloaded(pkgs, **scope) -> list[str]:
    fetcher = _fetcher(_routes(pkgs))
    drive_crawler(CkanCrawler(), _spec(**scope), fetcher, now=T0)
    return [r for r in fetcher.requests if "/action/" not in r]


def test_format_whitelist_is_normalized():
    pkg = _pkg(resources=[_res("https://a.go.jp/1.csv", "ＣＳＶ", rid="1"), _res("https://a.go.jp/2.pdf", ".PDF", rid="2"),
                          _res("https://a.go.jp/3.zip", "ZIP", rid="3"), _res("https://a.go.jp/4", "", rid="4")])
    assert _downloaded([pkg], formats=["csv", "pdf"]) == ["https://a.go.jp/1.csv", "https://a.go.jp/2.pdf"]
    assert len(_downloaded([pkg])) == 4  # 没写 formats 就不按格式限制


def test_organization_whitelist():
    pkgs = [_pkg("p1", org="org_0700"), _pkg("p2", [_res("https://b.go.jp/x.csv", rid="x")], org="org_0600")]
    assert _downloaded(pkgs, organizations=["org_0600"]) == ["https://b.go.jp/x.csv"]


def test_declared_size_over_limit_is_skipped_but_unknown_size_is_downloaded():
    pkg = _pkg(resources=[_res("https://a.go.jp/big.csv", size=2000, rid="1"), _res("https://a.go.jp/small.csv", size=10, rid="2"),
                          _res("https://a.go.jp/none.csv", size=None, rid="3")])
    assert _downloaded([pkg], max_file_bytes=1000) == ["https://a.go.jp/small.csv", "https://a.go.jp/none.csv"]


def test_host_outside_allowed_hosts_is_metadata_only():
    pkg = _pkg(resources=[_res("https://data.bodik.jp/x.csv", rid="1"), _res("https://a.go.jp/y.csv", rid="2")])
    assert _downloaded([pkg]) == ["https://a.go.jp/y.csv"]


# ---------- 失败与许可 ----------


def test_one_missing_resource_does_not_stop_the_rest():
    pkg = _pkg(resources=[_res("https://a.go.jp/gone.csv", rid="1"), _res("https://a.go.jp/ok.csv", rid="2")])
    routes = _routes([pkg]) | {"https://a.go.jp/gone.csv": FakeResponse(404)}
    steps = drive_crawler(CkanCrawler(), _spec(), _fetcher(routes), now=T0)
    outcomes = {s.task.url: s.result.outcome for s in _by_role(steps, "resource")}
    assert outcomes == {"https://a.go.jp/gone.csv": FetchOutcome.http_error, "https://a.go.jp/ok.csv": FetchOutcome.ok}


def test_html_error_page_behind_redirect_is_not_success():
    html = "<!DOCTYPE html><html><body>ページが見つかりません</body></html>".encode()
    routes = _routes([_pkg()], body=html)
    steps = drive_crawler(CkanCrawler(), _spec(), _fetcher(routes), now=T0)
    assert _by_role(steps, "resource")[0].result.outcome is FetchOutcome.empty_body


def test_api_success_false_is_not_success():
    bad = json.dumps({"success": False, "error": {"message": "Not found"}}).encode()
    steps = drive_crawler(CkanCrawler(), _spec(), _fetcher({SEARCH.format(rows=100, start=0): bad}), now=T0)
    assert [s.result.outcome for s in steps] == [FetchOutcome.empty_body]


def test_license_comes_from_resource_level():
    pkg = _pkg(resources=[_res("https://a.go.jp/1.csv", license_id="CC-BY", rid="1"),
                          _res("https://a.go.jp/2.csv", license_id="", rid="2"),
                          _res("https://a.go.jp/3.csv", license_id=" ", rid="3")])
    steps = drive_crawler(CkanCrawler(), _spec(), _fetcher(_routes([pkg])), now=T0)
    assert [s.task.meta["license"] for s in _by_role(steps, "resource")] == ["CC-BY", None, None]


# ---------- 单文件上限（契约二对齐）----------


def test_actual_body_over_limit_is_too_large_even_if_declared_size_is_small():
    # 不用 drive_crawler：它对带正文的 too_large 覆盖会触发 FetchResult 校验（失败类结果不许有 content），
    # 真实执行器要丢弃正文再记录。这里直接走 plan -> after_fetch -> classify。
    crawler = CkanCrawler()
    spec = _spec(max_file_bytes=1000)
    fetcher = _fetcher(_routes([_pkg(resources=[_res(size=10)])], body=b"x" * 5000))
    ctx = CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)
    first = next(iter(crawler.plan(ctx)))
    task = next(t for t in crawler.after_fetch(first, fetcher.fetch(first.url), ctx) if t.meta["role"] == "resource")
    assert task.meta["max_file_bytes"] == 1000 and task.meta["declared_size"] == 10  # 声明的大小拦不住
    result = fetcher.fetch(task.url)  # 没配 max_bytes 的抓取器：下载成功
    assert result.outcome is FetchOutcome.ok and len(result.content) == 5000
    assert crawler.classify(task, result) is FetchOutcome.too_large


def test_body_within_limit_is_left_to_default_classification():
    crawler = CkanCrawler()
    steps = drive_crawler(crawler, _spec(max_file_bytes=1000), _fetcher(_routes([_pkg()], body=b"x" * 1000)), now=T0)
    step = _by_role(steps, "resource")[0]
    assert crawler.classify(step.task, step.result) is None
