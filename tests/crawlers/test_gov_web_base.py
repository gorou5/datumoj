"""Offline tests for HTML base href handling in gov_web discovery."""

from pathlib import Path

import pytest

from datumoj.core.contracts import CrawlContext, FetchTask, SourceSpec
from datumoj.core.http import PoliteHttpFetcher, Recorded, ReplayTransport
from datumoj.crawlers.gov_web import GovWebCrawler
from datumoj.testing.fakes import FakeManifest, FakeRawStore
from datumoj.testing.samples import T0, make_spec

ROOT = Path(__file__).resolve().parents[2]
OSAKA_PAGE = "https://www.city.osaka.lg.jp/keizaisenryaku/page/0000681796.html"
OSAKA_BASE = "https://www.city.osaka.lg.jp/keizaisenryaku/"


def _spec(page_url: str, host: str, path_prefix: str) -> SourceSpec:
    return make_spec(
        source_id="jp-gov-web", kind="gov_web", base_url=f"https://{host}/",
        allowed_hosts=[host], rate_per_sec=1000,
        scope={"seeds": [{"url": page_url, "label": "fixture", "path_prefix": path_prefix}]},
    )


def _discover(page_path: Path, page_url: str, host: str, path_prefix: str) -> list[FetchTask]:
    spec = _spec(page_url, host, path_prefix)
    ctx = CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)
    crawler = GovWebCrawler()
    task, = crawler.plan(ctx)
    replay = ReplayTransport({
        f"https://{host}/robots.txt": Recorded(status=404),
        page_url: Recorded(path=page_path),
    })
    with PoliteHttpFetcher.from_spec(spec, transport=replay, sleep=lambda _: None) as fetcher:
        result = fetcher.fetch(task.url)
    return list(crawler.after_fetch(task, result, ctx))


def test_osaka_detail_uses_recorded_base_for_pdf_links():
    fixture = ROOT / "tests/fixtures/gov_web/html/osaka-city/detail-2.html"
    children = _discover(fixture, OSAKA_PAGE, "www.city.osaka.lg.jp", "/keizaisenryaku/")

    expected = OSAKA_BASE + "cmsfiles/contents/0000681/681796/00-1_R9hatsumei-suisenyouryo.pdf"
    pdf_tasks = [task for task in children if task.meta["role"] == "pdf"]
    assert any(task.url == expected for task in pdf_tasks)
    assert all(task.url.startswith(OSAKA_BASE + "cmsfiles/") for task in pdf_tasks)


@pytest.mark.parametrize(("base_tag", "expected"), [
    ('<base href="../assets/"><base href="../ignored/">',
     "https://gov-web.test/policy/startup/assets/guide.pdf"),
    ('<base href="">', "https://gov-web.test/policy/startup/page/guide.pdf"),
    ('<base href="javascript:alert(1)">', "https://gov-web.test/policy/startup/page/guide.pdf"),
])
def test_relative_empty_and_non_http_base_href(tmp_path, base_tag, expected):
    page_url = "https://gov-web.test/policy/startup/page/index.html"
    body = f"<html><head>{base_tag}</head><body>{'政策本文。' * 40}<a href='guide.pdf'>PDF</a></body></html>"
    page = tmp_path / "page.html"
    page.write_text(body, encoding="utf-8")

    children = _discover(page, page_url, "gov-web.test", "/policy/startup/")

    assert [(task.url, task.meta["role"]) for task in children] == [(expected, "pdf")]


def test_external_base_does_not_bypass_allowed_host_filter(tmp_path):
    page_url = "https://gov-web.test/policy/startup/page/index.html"
    body = (
        "<html><head><base href='https://outside.test/policy/startup/'></head><body>"
        + "政策本文。" * 40
        + "<a href='./cmsfiles/outside.pdf'>外部 PDF</a>"
        + "<a href='https://gov-web.test/policy/startup/allowed.pdf'>許可済み PDF</a>"
        + "</body></html>"
    )
    page = tmp_path / "page.html"
    page.write_text(body, encoding="utf-8")

    children = _discover(page, page_url, "gov-web.test", "/policy/startup/")

    assert [(task.url, task.meta["role"]) for task in children] == [
        ("https://gov-web.test/policy/startup/allowed.pdf", "pdf"),
    ]
