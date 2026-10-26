"""PDF recheck scheduling uses only the injected crawl clock."""

from datetime import timedelta
import logging
from urllib.parse import urlsplit

import pytest

from datumoj.rikolto.core.contracts import CrawlContext, FetchResult
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.crawlers.gov_web import GovWebCrawler
from datumoj.rikolto.testing.fakes import FakeManifest, FakeRawStore
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec

BASE = "http://gov-web.test"
SEED = BASE + "/policy/startup/index.html"
PDF = BASE + "/policy/startup/guide.pdf"
PAGE = BASE + "/policy/startup/detail.html"


def context(**scope):
    spec = make_spec(
        source_id="jp-gov-web", kind="gov_web", base_url=BASE,
        allowed_hosts=[urlsplit(BASE).hostname], rate_per_sec=100,
        scope={"seeds": [{"url": SEED, "label": "政策"}], **scope},
    )
    manifest = FakeManifest()
    return spec, manifest, CrawlContext(spec=spec, manifest=manifest, raw=FakeRawStore(), now=T0)


def successful_pdf(manifest, days_ago):
    previous = make_snapshot(
        source_id="jp-gov-web", url=PDF, data=b"%PDF-test",
        fetched_at=T0 - timedelta(days=days_ago),
    )
    manifest.record_snapshot(previous, {"role": "pdf"})


def fetched(task, body):
    return FetchResult(
        url=task.redacted_url, outcome=FetchOutcome.ok, content=body, fetched_at=T0,
    )


def listing(*links):
    return ("<html><body>" + "政策本文。" * 30 + "".join(links) + "</body></html>").encode()


@pytest.mark.parametrize(("days_ago", "expected"), [(6, False), (8, True)])
def test_pdf_recheck_interval(days_ago, expected):
    spec, manifest, ctx = context(pdf_recheck_days=7)
    successful_pdf(manifest, days_ago)
    crawler = GovWebCrawler()
    task, = crawler.plan(ctx)

    children = list(crawler.after_fetch(task, fetched(task, listing('<a href="guide.pdf">PDF</a>')), ctx))

    assert any(child.url == PDF for child in children) is expected


def test_pdf_without_successful_snapshot_is_scheduled():
    _, _, ctx = context(pdf_recheck_days=7)
    crawler = GovWebCrawler()
    task, = crawler.plan(ctx)

    children = list(crawler.after_fetch(task, fetched(task, listing('<a href="guide.pdf">PDF</a>')), ctx))

    assert [child.url for child in children] == [PDF]


def test_pdf_recheck_unset_preserves_always_schedule_behavior():
    _, manifest, ctx = context()
    successful_pdf(manifest, 1)
    crawler = GovWebCrawler()
    task, = crawler.plan(ctx)

    children = list(crawler.after_fetch(task, fetched(task, listing('<a href="guide.pdf">PDF</a>')), ctx))

    assert [child.url for child in children] == [PDF]


@pytest.mark.parametrize("value", [0, -1, True, "7"])
def test_invalid_pdf_recheck_days_is_rejected(value):
    _, _, ctx = context(pdf_recheck_days=value)

    with pytest.raises(ValueError, match="scope.pdf_recheck_days 必须是正整数"):
        list(GovWebCrawler().plan(ctx))


def test_recent_pdf_is_seen_but_does_not_consume_page_budget(caplog):
    _, manifest, ctx = context(pdf_recheck_days=7, max_pages_per_seed=2)
    successful_pdf(manifest, 6)
    crawler = GovWebCrawler()
    task, = crawler.plan(ctx)
    body = listing(
        '<a href="guide.pdf">PDF</a>', '<a href="guide.pdf#duplicate">PDF duplicate</a>',
        '<a href="detail.html">HTML</a>',
    )

    caplog.set_level(logging.INFO)
    children = list(crawler.after_fetch(task, fetched(task, body), ctx))

    assert [child.url for child in children] == [PAGE]
    assert children[0].meta["seed_task_number"] == 2
    assert "gov_web pdf_recheck skipped=1" in caplog.text
    assert list(crawler.after_fetch(task, fetched(task, body), ctx)) == []
