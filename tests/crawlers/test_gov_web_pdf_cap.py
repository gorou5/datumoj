from urllib.parse import urlsplit

import pytest

from datumoj.core.contracts import CrawlContext, FetchResult
from datumoj.core.models import FetchOutcome
from datumoj.crawlers.gov_web import GovWebCrawler
from datumoj.testing.fakes import FakeManifest, FakeRawStore
from datumoj.testing.samples import T0, make_spec

BASE = "http://gov-web.test"
SEED = "/policy/startup/index.html"


def spec_for(**scope):
    return make_spec(
        source_id="jp-gov-web", kind="gov_web", base_url=BASE,
        allowed_hosts=[urlsplit(BASE).hostname], rate_per_sec=1000,
        scope={"seeds": [{"url": BASE + SEED, "label": "架空市 政策"}], **scope},
    )


def context(spec):
    return CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)


def fetched(task, data):
    return FetchResult(url=task.redacted_url, outcome=FetchOutcome.ok, content=data, fetched_at=T0)


def page_with_links(*links):
    return ("<html><body>" + "政策本文。" * 30 + "".join(links) + "</body></html>").encode()


def test_page_cap_does_not_prevent_pdf_tasks_and_logs_page_limit(caplog):
    crawler = GovWebCrawler()
    ctx = context(spec_for(max_pages_per_seed=1, max_pdfs_per_seed=1))
    seed, = crawler.plan(ctx)
    body = page_with_links('<a href="a.html">a</a>', '<a href="b.html">b</a>',
                           '<a href="guide.pdf">guide</a>')

    children = list(crawler.after_fetch(seed, fetched(seed, body), ctx))

    assert [task.meta["role"] for task in children] == ["pdf"]
    pdf, = children
    assert pdf.meta["max_pdfs_per_seed"] == 1
    assert pdf.meta["seed_pdf_number"] == 1
    assert pdf.meta["seed_pdf_limit_reached"] is True
    assert "seed_task_number" not in pdf.meta
    assert caplog.text.count("max_pages_per_seed=1 reached") == 1


def test_pdf_cap_does_not_prevent_page_tasks_and_logs_pdf_limit(caplog):
    crawler = GovWebCrawler()
    ctx = context(spec_for(max_pages_per_seed=3, max_pdfs_per_seed=1))
    seed, = crawler.plan(ctx)
    body = page_with_links('<a href="first.pdf">first</a>', '<a href="second.pdf">second</a>',
                           '<a href="detail.html">detail</a>', '<a href="third.pdf">third</a>')

    children = list(crawler.after_fetch(seed, fetched(seed, body), ctx))

    assert [task.meta["role"] for task in children] == ["pdf", "page"]
    pdf, page = children
    assert pdf.meta["seed_pdf_number"] == 1 and pdf.meta["seed_pdf_limit_reached"] is True
    assert page.meta["seed_task_number"] == 2 and page.meta["seed_limit_reached"] is False
    assert "seed_pdf_number" not in page.meta
    assert caplog.text.count("max_pdfs_per_seed=1 reached") == 1


def test_pdf_cap_defaults_to_page_cap():
    crawler = GovWebCrawler()
    ctx = context(spec_for(max_pages_per_seed=2))
    seed, = crawler.plan(ctx)
    body = page_with_links('<a href="guide.pdf">guide</a>')

    pdf, = crawler.after_fetch(seed, fetched(seed, body), ctx)

    assert pdf.meta["max_pdfs_per_seed"] == 2
    assert pdf.meta["seed_pdf_number"] == 1
    assert pdf.meta["seed_pdf_limit_reached"] is False


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "2"])
def test_invalid_pdf_cap_is_rejected(limit):
    with pytest.raises(ValueError, match="max_pdfs_per_seed"):
        list(GovWebCrawler().plan(context(spec_for(max_pdfs_per_seed=limit))))


def test_content_identified_pdf_moves_to_pdf_count_and_renumbers_pending_pages():
    crawler = GovWebCrawler()
    ctx = context(spec_for(max_pages_per_seed=3, max_pdfs_per_seed=2))
    seed, = crawler.plan(ctx)
    body = page_with_links('<a href="download">download</a>', '<a href="detail.html">detail</a>')
    download, detail = crawler.after_fetch(seed, fetched(seed, body), ctx)

    crawler.classify(download, fetched(download, b"%PDF-1.4\nsynthetic"))

    assert download.meta["role"] == "pdf"
    assert download.meta["seed_pdf_number"] == 1
    assert "seed_task_number" not in download.meta
    assert detail.meta["seed_task_number"] == 2

