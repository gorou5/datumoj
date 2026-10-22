"""Offline gov_web contract and runner tests; recorded files are read-only."""

from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import yaml
from werkzeug.wrappers import Response

from datumoj.core.contracts import CrawlContext, FetchResult, SourceSpec, discover_crawlers
from datumoj.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.core.models import FetchOutcome, parent_ref
from datumoj.core.runner import CrawlDeps, run_crawl
from datumoj.core.schedule import read_control
from datumoj.crawlers.gov_web import GovWebCrawler, normalize_url
from datumoj.testing.contract_suites import CrawlerSuite
from datumoj.testing.fakes import FakeHttpFetcher, FakeManifest, FakeRawStore
from datumoj.testing.samples import T0, make_spec, sha

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/gov_web"
SITE = FIXTURES / "html/synthetic/site"
BASE = "http://gov-web.test"
PREFIX = "/policy/startup/"
SEED = PREFIX + "index.html"
DETAIL = PREFIX + "detail-a.html"
DEEPER = PREFIX + "deeper/detail-b.html"
PDF = PREFIX + "guide.pdf"
PRIVATE = PREFIX + "private/internal.html"
LAST_MODIFIED = "Wed, 21 Oct 2026 00:00:00 GMT"


def spec_for(base=BASE, **scope):
    return make_spec(
        source_id="jp-gov-web", kind="gov_web", base_url=base,
        allowed_hosts=[urlsplit(base).hostname], rate_per_sec=1000,
        scope={"seeds": [{"url": base + SEED, "label": "架空市 創業支援"}], **scope},
    )


def context(spec):
    return CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)


def fetched(task, data, **kwargs):
    return FetchResult(url=task.redacted_url, outcome=FetchOutcome.ok, content=data, fetched_at=T0, **kwargs)


def page_with_links(*links):
    return ("<html><body>" + "政策本文。" * 30 + "".join(links) + "</body></html>").encode()


class TestGovWebContract(CrawlerSuite):
    @pytest.fixture
    def crawler(self):
        return GovWebCrawler()

    @pytest.fixture
    def spec(self):
        return spec_for()

    @pytest.fixture
    def make_fetcher(self):
        routes = {BASE + "/" + p.relative_to(SITE).as_posix(): p.read_bytes()
                  for p in SITE.rglob("*") if p.is_file()}
        return lambda: FakeHttpFetcher(routes, now=lambda: T0)


def test_source_configuration_and_disabled_default(tmp_path):
    spec = SourceSpec.model_validate(yaml.safe_load((ROOT / "sources/jp-gov-web.yaml").read_text()))
    assert spec.kind == "gov_web" and spec.schedule == "daily"
    assert spec.allowed_hosts == ["www8.cao.go.jp", "www.soumu.go.jp", "www.city.osaka.lg.jp"]
    assert [s["path_prefix"] for s in spec.scope["seeds"]] == [
        "/cstp/openinnovation/", "/denshijiti/", "/keizaisenryaku/",
    ]
    assert spec.scope["max_depth"] == 2 and spec.scope["max_pages_per_seed"] == 500
    assert spec.scope["follow_pdf"] is True
    assert spec.scope["pdf_recheck_days"] == 7 and spec.scope["head_probe"] is True
    assert spec.license.redistribution == "unverified" and spec.license.usage_scope == "internal_only"
    assert not read_control(tmp_path)["sources"].get(spec.id, {}).get("enabled", False)
    assert discover_crawlers()["gov_web"] is GovWebCrawler
    tasks = list(GovWebCrawler().plan(context(spec)))
    assert len(tasks) == 3
    assert all(t.meta["role"] == "page" and t.meta["depth"] == 0 and t.meta["seed"] == t.url for t in tasks)


@pytest.mark.parametrize(("url", "expected"), [
    ("HTTPS://WWW8.CAO.GO.JP:443/a/../policy/%64etail.html?utm_source=a&UTM_X=b&id=2#x",
     "https://www8.cao.go.jp/policy/detail.html?id=2"),
    ("http://Gov-Web.test:80/policy/./startup/detail-a.html?fbclid=x&gclid=y&blank=",
     BASE + DETAIL + "?blank="),
    ("../startup/detail-a.html#x", BASE + DETAIL),
    ("detail-a.html?utm_campaign=x", BASE + DETAIL),
    ("mailto:a@example.org", None), ("javascript:void(0)", None), ("tel:123", None),
    ("ftp://gov-web.test/a", None), ("https://user:pass@gov-web.test/a", None),
    ("http://gov-web.test:bad/a", None), ("http://[invalid/a", None),
    ("/policy/startup/%2e%2e/other/out.html", BASE + "/policy/other/out.html"),
    ("/policy/startup/%2F..%2fother/out.html", None),
    ("https://gov-web.test\\@evil.test/a", None),
])
def test_url_normalization(url, expected):
    assert normalize_url(url, BASE + SEED) == expected


@pytest.fixture
def local_site(httpserver):
    """Public test hostname maps ONLY to pytest-httpserver through injected transport.

    PoliteHttpFetcher correctly rejects loopback URLs in production; this transport
    keeps its host/robots checks intact while all sockets go to the local server.
    """
    requests = []
    for path in SITE.rglob("*"):
        if not path.is_file():
            continue
        route = "/" + path.relative_to(SITE).as_posix()
        data = path.read_bytes()

        def respond(request, body=data, route=route):
            requests.append((route, dict(request.headers)))
            headers = {"ETag": '"' + sha(body) + '"', "Last-Modified": LAST_MODIFIED}
            if request.headers.get("If-None-Match") == headers["ETag"]:
                return Response(status=304, headers=headers)
            media = "application/pdf" if body.startswith(b"%PDF-") else "text/html"
            return Response(body, headers=headers, content_type=media)

        httpserver.expect_request(route).respond_with_handler(respond)

    with httpx.Client(trust_env=False, timeout=5) as local:
        def forward(request):
            assert request.url.host == "gov-web.test", "test attempted an external request"
            target = httpserver.url_for(request.url.path)
            if request.url.query:
                target += "?" + request.url.query.decode("ascii")
            response = local.get(target, headers={k: v for k, v in request.headers.items() if k != "host"})
            return httpx.Response(response.status_code, headers=response.headers,
                                  stream=httpx.ByteStream(response.content))

        with PoliteHttpFetcher.from_spec(spec_for(), transport=httpx.MockTransport(forward),
                                        sleep=lambda _: None, now=lambda: T0) as fetcher:
            yield fetcher, requests
    httpserver.check_assertions()


def run(spec, fetcher, crawler=None, manifest=None, raw=None):
    manifest = manifest if manifest is not None else FakeManifest()
    raw = raw if raw is not None else FakeRawStore()
    report = run_crawl(spec, crawler or GovWebCrawler(), CrawlDeps(
        raw=raw, manifest=manifest, fetcher=fetcher, sleep=lambda _: None, now=lambda: T0,
    ))
    return report, manifest, raw


def test_synthetic_site_depth_scope_robots_dedup_pdf_and_conditional_rerun(local_site):
    fetcher, requests = local_site
    crawler = GovWebCrawler()
    report, manifest, raw = run(spec_for(), fetcher, crawler)
    assert report.stop_reason is None
    assert report.new == 4 and report.failures == {"blocked_by_robots": 1}
    paths = [path for path, _ in requests]
    assert set(paths) == {"/robots.txt", SEED, DETAIL, DEEPER, PDF}
    assert paths.count(DETAIL) == 1
    assert PRIVATE not in paths
    snapshots = manifest.snapshots_for("jp-gov-web")
    assert len(snapshots) == 5
    pdf = next(s for s in snapshots if s.resource_url == BASE + PDF)
    meta = manifest.snapshot_meta(pdf.snapshot_id)
    assert meta["role"] == "pdf"
    assert parent_ref(meta) == (BASE + SEED, sha((SITE / SEED.lstrip("/")).read_bytes()))
    assert raw.get(pdf.sha256) == (SITE / PDF.lstrip("/")).read_bytes()
    assert {manifest.snapshot_meta(s.snapshot_id)["depth"] for s in snapshots} == {0, 1, 2}

    requests.clear()
    second, _, _ = run(spec_for(), fetcher, crawler, manifest, raw)
    assert second.new == 0 and second.unchanged == 4
    assert second.failures == {"blocked_by_robots": 1}
    assert {path for path, _ in requests} == {SEED, DETAIL, DEEPER, PDF}
    for path, headers in requests:
        assert headers["If-None-Match"] == '"' + sha((SITE / path.lstrip("/")).read_bytes()) + '"'
        assert headers["If-Modified-Since"] == LAST_MODIFIED
    second_pdf = [s for s in manifest.snapshots_for("jp-gov-web") if s.resource_url == BASE + PDF][-1]
    assert parent_ref(manifest.snapshot_meta(second_pdf.snapshot_id)) == parent_ref(meta)


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_per_seed_cap_is_visible_in_persisted_meta(local_site, caplog, limit):
    fetcher, _ = local_site
    report, manifest, _ = run(spec_for(max_pages_per_seed=limit), fetcher)
    snapshots = manifest.snapshots_for("jp-gov-web")
    assert len(snapshots) == limit
    metadata = [manifest.snapshot_meta(s.snapshot_id) for s in snapshots]
    assert sorted(m["seed_task_number"] for m in metadata) == list(range(1, limit + 1))
    assert sum(m["seed_limit_reached"] for m in metadata) == 1
    assert "max_pages_per_seed=" + str(limit) in caplog.text
    assert report.stop_reason is None  # Per-seed exhaustion does not stop other seeds.


def test_budgets_are_independent_and_duplicates_do_not_consume_budget():
    seeds = [{"url": BASE + f"/{name}/index.html", "label": name} for name in ("one", "two")]
    ctx = context(spec_for(seeds=seeds, max_pages_per_seed=2))
    crawler = GovWebCrawler()
    tasks = list(crawler.plan(ctx))
    body = page_with_links('<a href="a.html">a</a>', '<a href="a.html#dup">dup</a>', '<a href="b.html">b</a>')
    for seed in tasks:
        children = list(crawler.after_fetch(seed, fetched(seed, body), ctx))
        assert len(children) == 1
        assert children[0].meta["seed_task_number"] == 2 and children[0].meta["seed_limit_reached"]
        assert list(crawler.after_fetch(seed, fetched(seed, body), ctx)) == []


@pytest.mark.parametrize(("follow_pdf", "depth", "expected", "failures"), [
    (False, 0, {SEED}, {}),
    (False, 2, {SEED, DETAIL, DEEPER}, {"blocked_by_robots": 1}),
    (True, 0, {SEED, PDF}, {}),
])
def test_follow_pdf_and_depth_limits(local_site, follow_pdf, depth, expected, failures):
    fetcher, requests = local_site
    report, _, _ = run(spec_for(follow_pdf=follow_pdf, max_depth=depth), fetcher)
    assert report.new == len(expected) and report.failures == failures
    assert {p for p, _ in requests} == {"/robots.txt", *expected}


def test_pdfs_on_depth_boundary_and_type_hint_without_extension():
    ctx = context(spec_for(max_depth=0))
    crawler = GovWebCrawler()
    task, = crawler.plan(ctx)
    body = page_with_links('<a href="download" type="application/pdf">PDF</a>',
                           '<a href="guide.PDF">PDF</a>', '<a href="detail.html">HTML</a>')
    children = list(crawler.after_fetch(task, fetched(task, body), ctx))
    assert [t.meta["role"] for t in children] == ["pdf", "pdf"]
    assert all(parent_ref(t.meta) == (BASE + SEED, sha(body)) for t in children)


def test_html_body_overrides_pdf_link_hint_and_filename():
    ctx = context(spec_for())
    crawler = GovWebCrawler()
    seed, = crawler.plan(ctx)
    body = page_with_links('<a href="guide.pdf" type="application/pdf">guide</a>')
    task, = crawler.after_fetch(seed, fetched(seed, body), ctx)
    assert task.meta["role"] == "pdf"
    html_result = fetched(task, page_with_links('<a href="detail-a.html">detail</a>'),
                          content_type="application/pdf")
    assert crawler.classify(task, html_result) is None
    assert task.meta["role"] == "page"
    detail, = crawler.after_fetch(task, html_result, ctx)
    assert detail.url == BASE + DETAIL


@pytest.mark.parametrize("content_type", ["application/pdf", "text/html", "application/octet-stream"])
def test_extensionless_pdf_identified_by_content_before_metadata_is_persisted(content_type, tmp_path):
    spec = spec_for()
    body = page_with_links('<a href="download">download</a>')
    page = tmp_path / "index.html"
    page.write_bytes(body)
    transport = ReplayTransport({
        BASE + "/robots.txt": Recorded(status=404),
        BASE + SEED: Recorded(path=page),
        BASE + PREFIX + "download": Recorded(path=SITE / PDF.lstrip("/"), headers={"content-type": content_type}),
    })
    with PoliteHttpFetcher.from_spec(spec, transport=transport, sleep=lambda _: None) as fetcher:
        report, manifest, raw = run(spec, fetcher)
    assert report.new == 2 and not report.failures
    snapshot = manifest.latest_snapshot(BASE + PREFIX + "download")
    meta = manifest.snapshot_meta(snapshot.snapshot_id)
    assert meta["role"] == "pdf" and parent_ref(meta) == (BASE + SEED, sha(body))
    cached = ReplayTransport({
        BASE + "/robots.txt": Recorded(status=404), BASE + SEED: Recorded(status=304),
        BASE + PREFIX + "download": Recorded(status=304),
    })
    with PoliteHttpFetcher.from_spec(spec, transport=cached, sleep=lambda _: None) as fetcher:
        second, _, _ = run(spec, fetcher, manifest=manifest, raw=raw)
    assert second.unchanged == 2 and not second.failures
    snapshot = manifest.latest_snapshot(BASE + PREFIX + "download")
    assert manifest.snapshot_meta(snapshot.snapshot_id)["role"] == "pdf"


@pytest.mark.parametrize(("fixture", "url", "prefix", "detail"), [
    ("osaka-city", "https://www.city.osaka.lg.jp/keizaisenryaku/category/3037-1-0-0-0-0-0-0-0-0.html",
     "/keizaisenryaku/", "/keizaisenryaku/page/0000685899.html"),
    ("soumu", "https://www.soumu.go.jp/denshijiti/digital_transformation.html",
     "/denshijiti/", "/denshijiti/index_00001.html"),
])
def test_recorded_seed_links_with_content_based_encoding(fixture, url, prefix, detail):
    spec = spec_for("https://" + urlsplit(url).hostname,
                    seeds=[{"url": url, "label": fixture, "path_prefix": prefix}])
    ctx = context(spec)
    crawler = GovWebCrawler()
    task, = crawler.plan(ctx)
    body = (FIXTURES / "html" / fixture / "seed.html").read_bytes()
    result = fetched(task, body, content_type="application/pdf; charset=ascii")
    assert crawler.classify(task, result) is None  # HTML content wins over incorrect headers.
    children = list(crawler.after_fetch(task, result, ctx))
    assert "https://" + urlsplit(url).hostname + detail in {t.url for t in children}
    assert all(urlsplit(t.url).path.startswith(prefix) for t in children)


def test_spa_allow_rule_and_short_html_classification():
    seed = BASE + "/index.html"
    spec = spec_for(seeds=[{"url": seed, "label": "SPA"}])
    transport = ReplayTransport({
        BASE + "/robots.txt": FIXTURES / "html/spa/robots.txt",
        seed: FIXTURES / "html/spa/shell.html",
    })
    with PoliteHttpFetcher.from_spec(spec, transport=transport, sleep=lambda _: None) as fetcher:
        report, manifest, raw = run(spec, fetcher)
    assert report.failures == {"spa_shell": 1}
    assert manifest.latest_snapshot(seed).outcome is FetchOutcome.spa_shell
    assert sha((FIXTURES / "html/spa/shell.html").read_bytes()) in raw.blobs
    crawler = GovWebCrawler()
    task, = crawler.plan(context(spec))
    assert crawler.classify(task, fetched(task, b"<html><body>short</body></html>")) is FetchOutcome.spa_shell
    assert crawler.classify(task, fetched(task, page_with_links())) is None


@pytest.mark.parametrize("scope", [
    {"seeds": []}, {"max_depth": -1}, {"max_depth": True}, {"max_pages_per_seed": 0},
    {"max_pages_per_seed": 1.5}, {"follow_pdf": "yes"},
    {"seeds": [{"url": "https://external.test/a", "label": "bad"}]},
    {"seeds": [{"url": BASE + SEED, "label": ""}]},
    {"seeds": [{"url": BASE + SEED, "label": "bad", "path_prefix": "//evil.test/"}]},
    {"seeds": [{"url": BASE + SEED, "label": "bad", "path_prefix": "/policy/other/"}]},
])
def test_invalid_scope_is_rejected(scope):
    with pytest.raises(ValueError):
        list(GovWebCrawler().plan(context(spec_for(**scope))))


def test_failed_and_non_html_results_never_discover_links():
    ctx = context(spec_for())
    crawler = GovWebCrawler()
    task, = crawler.plan(ctx)
    failure = FetchResult(url=task.url, outcome=FetchOutcome.http_error, http_status=500,
                          fetched_at=T0, error="HTTP 500")
    assert list(crawler.after_fetch(task, failure, ctx)) == []
    assert list(crawler.after_fetch(task, fetched(task, b"plain text"), ctx)) == []
    missing = FetchResult(url=task.url, outcome=FetchOutcome.not_modified, http_status=304, fetched_at=T0)
    assert list(crawler.after_fetch(task, missing, ctx)) == []
