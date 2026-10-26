from __future__ import annotations

import pytest

from datumoj.rikolto.core.budget import (
    PARSE_BUDGET_META_KEY,
    BudgetChecker,
    BudgetExceeded,
    ParseBudget,
)
from datumoj.rikolto.core.contracts import CrawlContext, Crawler, FetchResult, FetchTask, ParseRecord, ParseStatus
from datumoj.rikolto.core.models import ChangeKind, PublishState, parse_context_fingerprint
from datumoj.rikolto.core.runner import CrawlDeps, run_crawl
from datumoj.rikolto.parsers.html_page import HtmlPageParser
from datumoj.rikolto.parsers.pdf import PdfParser
from datumoj.rikolto.pipeline import PipelineDeps, parse_pending
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChangeLog,
    FakeChunkStore,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
)
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


def _meta(**overrides):
    return {PARSE_BUDGET_META_KEY: ParseBudget(**overrides).as_dict()}


def test_checker_limits_allow_exact_boundary_and_report_each_item():
    clock = Clock()
    checker = BudgetChecker(ParseBudget(max_pages=1, max_text_chars=4, max_output_chunks=1, max_seconds=5), clock)
    checker.add_page()
    checker.add_text(4)
    checker.add_output_chunk()
    clock.value = 5
    checker.check_time()

    cases = [
        ("pages", lambda checker: (checker.add_page(), checker.add_page())),
        ("text_chars", lambda checker: (checker.add_text(4), checker.add_text(1))),
        ("output_chunks", lambda checker: (checker.add_output_chunk(), checker.add_output_chunk())),
    ]
    for item, exceed in cases:
        checker = BudgetChecker(ParseBudget(max_pages=1, max_text_chars=4, max_output_chunks=1), Clock())
        with pytest.raises(BudgetExceeded, match=f"budget_exceeded:{item}"):
            exceed(checker)

    clock = Clock()
    checker = BudgetChecker(ParseBudget(max_seconds=5), clock)
    clock.value = 5.001
    with pytest.raises(BudgetExceeded, match="budget_exceeded:duration_seconds"):
        checker.check_time()


def test_source_scope_overrides_parse_budget():
    budget = ParseBudget.from_scope({
        "parse_budget": {
            "max_pages": 7,
            "max_text_chars": 800,
            "max_output_chunks": 9,
            "max_seconds": 2.5,
        },
    })
    assert budget == ParseBudget(max_pages=7, max_text_chars=800, max_output_chunks=9, max_seconds=2.5)
    assert ParseBudget.from_scope({}) == ParseBudget()


class _FakeTextPage:
    def __init__(self, text):
        self.text = text

    def get_text_range(self):
        return self.text

    def close(self):
        pass


class _FakePage:
    def __init__(self, text):
        self.text = text

    def get_textpage(self):
        return _FakeTextPage(self.text)

    def close(self):
        pass


class _FakePdf:
    texts = ["本文" * 30, "資料" * 30]

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, index):
        return _FakePage(self.texts[index])

    def get_metadata_dict(self):
        return {}

    def close(self):
        pass


def test_pdf_parser_checks_pages_text_and_output_chunks(monkeypatch):
    from datumoj.rikolto.parsers import pdf

    monkeypatch.setattr(pdf.pdfium, "PdfDocument", lambda data: _FakePdf())
    data = b"%PDF-synthetic"
    snapshot = make_snapshot(url="https://example.go.jp/a.pdf", data=data, content_type="application/pdf")
    parsed = PdfParser().parse(
        snapshot, data,
        _meta(max_pages=2, max_text_chars=120, max_output_chunks=2, max_seconds=5),
    )
    assert len(parsed.chunks) == 2

    for overrides, item in (
        ({"max_pages": 1}, "pages"),
        ({"max_text_chars": 119}, "text_chars"),
        ({"max_output_chunks": 1}, "output_chunks"),
    ):
        with pytest.raises(BudgetExceeded, match=f"budget_exceeded:{item}"):
            PdfParser().parse(snapshot, data, _meta(**overrides))


def test_html_parser_checks_page_text_and_output_chunk_budgets():
    data = (
        "<html><body><main><h1>T</h1><h2>A</h2><p>"
        + "あ" * 50
        + "</p><h2>B</h2><p>"
        + "い" * 50
        + "</p></main></body></html>"
    ).encode()
    snapshot = make_snapshot(url="https://example.go.jp/a.html", data=data, content_type="text/html")
    result = HtmlPageParser().parse(
        snapshot, data, _meta(max_pages=1, max_text_chars=103, max_output_chunks=2, max_seconds=5),
    )
    assert len(result.chunks) == 2

    for overrides, item in (
        ({"max_text_chars": 102}, "text_chars"),
        ({"max_output_chunks": 1}, "output_chunks"),
    ):
        with pytest.raises(BudgetExceeded, match=f"budget_exceeded:{item}"):
            HtmlPageParser().parse(snapshot, data, _meta(**overrides))


@pytest.mark.parametrize(
    ("budget_override", "expected_error"),
    [
        ({"max_output_chunks": 1}, "budget_exceeded:output_chunks"),
        ({"max_text_chars": 8}, "budget_exceeded:text_chars"),
    ],
)
def test_pipeline_records_policy_failure_without_publishing_or_retrying(budget_override, expected_error):
    class CountingParser(FakeParser):
        calls = 0

        def parse(self, snapshot, data, meta=None):
            type(self).calls += 1
            return super().parse(snapshot, data, meta)

    CountingParser.calls = 0
    spec = make_spec(scope={
        "urls": ["https://example.go.jp/a.txt"],
        "parse_budget": budget_override,
    })
    manifest, raw, chunks, changelog = FakeManifest(), FakeRawStore(), FakeChunkStore(), FakeChangeLog()
    deps = PipelineDeps(
        manifest=manifest,
        raw=raw,
        registry=FakeRegistry([spec]),
        chunks=chunks,
        keyword=FakeKeywordIndex(),
        observations=FakeObservationStore(),
        catalog=FakeCatalogStore(),
        change_log=changelog,
        parsers={("fake", "text/plain"): CountingParser},
    )
    data = b"Title\n\nbody"
    raw.put(data)
    snapshot = make_snapshot(data=data, snapshot_id="budgeted")
    manifest.record_snapshot(snapshot)

    first = parse_pending(deps)
    assert first.failed == {snapshot.snapshot_id: expected_error}
    assert chunks.get_document("jp-test:a.txt") is None
    assert chunks._versions == {}
    assert manifest.publish_state(snapshot.snapshot_id) is PublishState.failed
    fingerprint = parse_context_fingerprint(
        snapshot, {PARSE_BUDGET_META_KEY: ParseBudget.from_scope(spec.scope).as_dict()},
    )
    record = manifest.get_parse(snapshot.sha256, CountingParser.name, CountingParser.version, fingerprint)
    assert record.status is ParseStatus.failed and record.error == expected_error
    assert "[policy]" in changelog.list(kinds=[ChangeKind.parse_failed])[0].summary

    second = parse_pending(deps)
    assert second.failed == first.failed and second.skipped == 1
    assert CountingParser.calls == 1


def test_new_default_budget_context_rechecks_legacy_parser_success(monkeypatch):
    class BudgetAwareParser(FakeParser):
        name = "pdf"
        calls = 0

        def parse(self, snapshot, data, meta=None):
            type(self).calls += 1
            return super().parse(snapshot, data, meta)

    monkeypatch.setattr(
        "datumoj.rikolto.pipeline.ParseBudget.from_scope",
        lambda scope: ParseBudget(max_output_chunks=1),
    )
    spec = make_spec()
    manifest, raw = FakeManifest(), FakeRawStore()
    deps = PipelineDeps(
        manifest=manifest,
        raw=raw,
        registry=FakeRegistry([spec]),
        chunks=FakeChunkStore(),
        keyword=FakeKeywordIndex(),
        observations=FakeObservationStore(),
        catalog=FakeCatalogStore(),
        parsers={("fake", "text/plain"): BudgetAwareParser},
    )
    data = b"Title\n\nbody"
    raw.put(data)
    snapshot = make_snapshot(data=data, snapshot_id="legacy-success")
    manifest.record_snapshot(snapshot)
    manifest.record_parse(ParseRecord(
        sha256=snapshot.sha256,
        parser=BudgetAwareParser.name,
        version=BudgetAwareParser.version,
        status=ParseStatus.ok,
        context_fingerprint=parse_context_fingerprint(snapshot, {}),
        doc_keys=[],
    ))

    summary = parse_pending(deps)

    assert summary.failed == {snapshot.snapshot_id: "budget_exceeded:output_chunks"}
    assert BudgetAwareParser.calls == 1


class _DeadlineCrawler(Crawler):
    kind = "fake"

    def __init__(self, urls):
        self.urls = urls
        self.after_fetch_calls = 0

    def plan(self, ctx: CrawlContext):
        for url in self.urls:
            yield FetchTask(url=url)

    def after_fetch(self, task, result, ctx):
        self.after_fetch_calls += 1
        return ()


class _DeadlineFetcher:
    def __init__(self, clock):
        self.clock = clock
        self.urls = []

    def fetch(self, url, **kwargs):
        self.urls.append(url)
        self.clock.value = 1.0
        return FetchResult(url=url, outcome="ok", fetched_at=T0, http_status=200, content=b"body")


def test_run_crawl_deadline_keeps_completed_snapshot_and_stops_dispatching():
    clock = Clock()
    first, second = "https://example.go.jp/a", "https://example.go.jp/b"
    spec = make_spec(scope={"urls": [first, second], "max_run_seconds": 1})
    manifest, fetcher = FakeManifest(), _DeadlineFetcher(clock)
    crawler = _DeadlineCrawler([first, second])

    report = run_crawl(
        spec,
        crawler,
        CrawlDeps(FakeRawStore(), manifest, fetcher, clock=clock, now=lambda: T0),
    )

    assert report.stop_reason == "deadline"
    assert fetcher.urls == [first]
    assert len(manifest.snapshots_for(spec.id)) == 1
    assert crawler.after_fetch_calls == 0
    assert report.new == 1

