from dataclasses import replace
from datetime import timedelta

import pytest

from datumoj.changes import detect, normalize_text
from datumoj.core.contracts import ParseResult, ParseStatus
from datumoj.core.models import ChangeKind, DocKind, FetchOutcome
from datumoj.pipeline import PipelineDeps, parse_pending
from datumoj.store.changes import SqliteChangeLog
from datumoj.testing.fakes import (
    FakeCatalogStore, FakeChangeLog, FakeChunkStore, FakeKeywordIndex, FakeManifest,
    FakeObservationStore, FakeParser, FakeRawStore, FakeRegistry,
)
from datumoj.testing.samples import T0, make_document, make_snapshot, make_spec, sha


def documents(kind=DocKind.html_page, **kw):
    old = make_document("jp-test:doc", sha(b"old"), kind=kind, **kw)
    return old, old.model_copy(update={"snapshot_sha256": sha(b"new")})


def test_added_is_pure_and_references_snapshot():
    _, new = documents()
    event = detect(None, "", new, "本文")
    assert event == detect(None, "", new, "本文")
    assert event.kind is ChangeKind.added
    assert event.doc_key == new.doc_key and event.new_sha256 == new.snapshot_sha256
    assert event.old_sha256 is None and "新增" in event.summary
    assert detect(new, "本文", new, "本文") is None
    assert detect(None, "", new, "本文", detected_at=T0).event_id == event.event_id


@pytest.mark.parametrize("kind", [DocKind.html_page, DocKind.pdf])
@pytest.mark.parametrize("replacements,modified", [(1, False), (2, False), (3, True)])
def test_similarity_threshold_inclusive_boundary(kind, replacements, modified):
    old, new = documents(kind)
    text = "".join(chr(0x4E00 + i) for i in range(100))
    event = detect(old, text, new, text[:-replacements] + "甲" * replacements)
    assert (event is not None) is modified
    if modified:
        assert event.kind is ChangeKind.modified
        assert "3.0%" in event.summary
        assert (event.old_sha256, event.new_sha256) == (old.snapshot_sha256, new.snapshot_sha256)


def test_threshold_constant_can_be_adjusted(monkeypatch):
    monkeypatch.setattr("datumoj.changes.SIMILARITY_THRESHOLD", 1.0)
    old, new = documents()
    assert detect(old, "a" * 100, new, "a" * 99 + "b") is not None


@pytest.mark.parametrize("kind", [DocKind.html_page, DocKind.pdf])
def test_navigation_dates_and_whitespace_are_ignored(kind):
    old, new = documents(kind)
    before = "ホーム > 旧分類\n最終更新日: 2026年1月1日\nＡ  本文\nお問い合わせ"
    after = "ホーム > 新分類 > 記事\n最終更新日: 2026-10-22\nA 本文\nサイトマップ"
    assert detect(old, before, new, after) is None
    assert normalize_text(before) == "A 本文"


def test_business_dates_and_numbers_are_preserved():
    old, new = documents()
    assert detect(old, "施行日は2026年1月1日です", new, "施行日は2030年9月9日です") is not None


@pytest.mark.parametrize("key", ["law_revision_id", "revision_id", "law_revision", "revision"])
def test_law_revision_takes_precedence_over_body(key):
    old, new = documents(DocKind.law, metadata={key: "rev1"})
    assert detect(old, "旧本文", new, "全く異なる本文") is None
    revised = new.model_copy(update={"metadata": {key: "rev2"}})
    event = detect(old, "同じ本文", revised, "同じ本文")
    assert event.kind is ChangeKind.modified and "rev1 → rev2" in event.summary


def test_law_without_both_revision_ids_uses_body_hash():
    old, new = documents(DocKind.law)
    assert detect(old, "本文", new, "本文") is None
    assert detect(old, "a" * 100, new, "a" * 99 + "b").kind is ChangeKind.modified
    old = old.model_copy(update={"metadata": {"law_revision_id": "rev1"}})
    assert detect(old, "本文", new, "本文") is None


def test_ivy_law_metadata_without_kind_uses_law_body_hash():
    old, new = documents(None, metadata={"law_num": "法律第一号"})
    assert detect(old, "a" * 100, new, "a" * 99 + "b").kind is ChangeKind.modified


def test_law_revision_metadata_without_kind():
    old, new = documents(None, metadata={"law_revision_id": "rev1"})
    new = new.model_copy(update={"metadata": {"law_revision_id": "rev2"}})
    assert detect(old, "本文", new, "本文").kind is ChangeKind.modified


def test_statistical_table_uses_snapshot_hash_even_with_equal_body():
    old, new = documents(DocKind.dataset)
    event = detect(old, "同じ本文", new, "同じ本文")
    assert event.kind is ChangeKind.modified and "统计表" in event.summary
    assert detect(new, "", new, "") is None


def test_invalid_pair_rejected_and_summary_is_one_line():
    old, new = documents()
    with pytest.raises(ValueError, match="doc_key"):
        detect(old, "", new.model_copy(update={"doc_key": "jp-test:other"}), "")
    event = detect(None, "", new.model_copy(update={"title": "标题\n另一行"}), "正文")
    assert "\n" not in event.summary


def deps():
    return PipelineDeps(
        manifest=FakeManifest(), raw=FakeRawStore(), registry=FakeRegistry([make_spec()]),
        chunks=FakeChunkStore(), keyword=FakeKeywordIndex(), observations=FakeObservationStore(),
        catalog=FakeCatalogStore(), parsers={("fake", "text/plain"): FakeParser}, change_log=FakeChangeLog(),
    )


def add(value, data=b"first body", url="https://example.go.jp/a.txt", **kw):
    value.raw.put(data)
    index = len(value.manifest.snapshots_for("jp-test"))
    snap = make_snapshot(data=data, url=url, snapshot_id=f"s{index}", fetched_at=T0 + timedelta(hours=index), **kw)
    value.manifest.record_snapshot(snap)
    return snap


def test_pipeline_add_modify_reparse_and_version_upgrade_are_idempotent():
    value = deps()
    first = add(value)
    second = add(value, b"completely different body")
    assert parse_pending(value).parsed == [first.snapshot_id, second.snapshot_id]
    events = value.change_log.list()
    assert [e.kind for e in events] == [ChangeKind.added, ChangeKind.modified]
    assert all(e.resource_url == first.resource_url for e in events)
    assert events[1].old_sha256 == first.sha256 and events[1].new_sha256 == second.sha256
    assert value.chunks.get_document("jp-test:a.txt", first.sha256) is not None
    assert parse_pending(value).parsed == []

    class V2(FakeParser):
        version = "2"

    value.parsers = {("fake", "text/plain"): V2}
    assert len(parse_pending(value).parsed) == 2
    assert value.change_log.list() == events


def test_existing_document_is_not_backfilled():
    value = deps()
    snap = add(value)
    parsed = FakeParser().parse(snap, value.raw.get(snap.sha256))
    value.chunks.put_document(parsed.documents[0], parsed.chunks)
    parse_pending(value)
    assert value.change_log.list() == []
    add(value, b"different body")
    parse_pending(value)
    assert [e.kind for e in value.change_log.list()] == [ChangeKind.modified]


def test_parse_failure_classification_reference_and_retry():
    value = deps()
    bad = add(value, b"\xff")
    good = add(value, url="https://example.go.jp/good.txt")
    result = parse_pending(value)
    assert result.parsed == [good.snapshot_id] and bad.snapshot_id in result.failed
    assert value.manifest.get_parse(bad.sha256, FakeParser.name, FakeParser.version).status is ParseStatus.failed
    failures = value.change_log.list(kinds=[ChangeKind.parse_failed])
    assert len(failures) == 1
    assert failures[0].new_sha256 == bad.sha256 and failures[0].resource_url == bad.resource_url
    assert "format_changed" in failures[0].summary and bad.snapshot_id in failures[0].summary
    parse_pending(value)
    assert value.change_log.list(kinds=[ChangeKind.parse_failed]) == failures


@pytest.mark.parametrize("outcome,status,classification", [
    (FetchOutcome.http_error, 429, "rate_limited"),
    (FetchOutcome.http_error, 401, "auth_failed"),
    (FetchOutcome.http_error, 503, "source_unavailable"),
    (FetchOutcome.network_error, None, "source_unavailable"),
    (FetchOutcome.spa_shell, 200, "format_changed"),
    (FetchOutcome.too_large, 200, "policy"),
])
def test_latest_fetch_failure_classification_and_idempotence(outcome, status, classification):
    value = deps()
    snap = add(value, outcome=outcome, http_status=status, sha256=None, error="failure\nreason")
    assert parse_pending(value).parsed == []
    events = value.change_log.list()
    assert len(events) == 1 and events[0].kind is ChangeKind.fetch_failed
    assert classification in events[0].summary and snap.snapshot_id in events[0].summary
    assert events[0].resource_url == snap.resource_url
    assert "\n" not in events[0].summary
    parse_pending(value)
    assert value.change_log.list() == events


def test_only_latest_resource_status_generates_fetch_failure():
    value = deps()
    add(value, outcome=FetchOutcome.http_error)
    add(value)
    add(value, outcome=FetchOutcome.not_modified)
    parse_pending(value)
    assert value.change_log.list(kinds=[ChangeKind.fetch_failed]) == []


@pytest.mark.parametrize("status", [404, 410])
def test_removed_requires_previous_success_and_keeps_stored_document(status):
    value = deps()
    old = add(value)
    missing = add(value, outcome=FetchOutcome.http_error, http_status=status)
    add(value, url="https://example.go.jp/never.txt", outcome=FetchOutcome.http_error, http_status=status)
    parse_pending(value)
    removed = value.change_log.list(kinds=[ChangeKind.removed])
    assert len(removed) == 1 and removed[0].doc_key == "jp-test:a.txt"
    assert removed[0].old_sha256 == old.sha256 and removed[0].new_sha256 is None
    assert str(status) in removed[0].summary and missing.snapshot_id in removed[0].summary
    assert value.chunks.get_document("jp-test:a.txt") is not None
    events = value.change_log.list()
    parse_pending(value)
    assert value.change_log.list() == events


def test_removed_does_not_include_other_resource_with_identical_bytes():
    class Csv(FakeParser):
        name = "fake-csv"
        content_type = "text/csv"

    value = deps()
    value.parsers[("fake", "text/csv")] = Csv
    add(value)
    add(value, url="https://example.go.jp/b.csv", content_type="text/csv")
    assert len(parse_pending(value).parsed) == 2
    add(value, outcome=FetchOutcome.http_error, http_status=404)
    parse_pending(value)
    assert [e.doc_key for e in value.change_log.list(kinds=[ChangeKind.removed])] == ["jp-test:a.txt"]


def test_non_removal_failure_and_later_recovery():
    value = deps()
    add(value)
    add(value, outcome=FetchOutcome.http_error, http_status=503)
    parse_pending(value)
    assert value.change_log.list(kinds=[ChangeKind.removed]) == []
    assert len(value.change_log.list(kinds=[ChangeKind.fetch_failed])) == 1
    add(value, outcome=FetchOutcome.http_error, http_status=404)
    add(value, outcome=FetchOutcome.not_modified)
    parse_pending(value)
    assert value.change_log.list(kinds=[ChangeKind.removed]) == []
    assert len(value.change_log.list(kinds=[ChangeKind.fetch_failed])) == 1


def test_fetch_failures_honor_source_filter():
    value = deps()
    value.registry = FakeRegistry([make_spec(), make_spec("jp-other")])
    add(value, outcome=FetchOutcome.network_error)
    other = make_snapshot(source_id="jp-other", outcome=FetchOutcome.network_error)
    value.manifest.record_snapshot(other)
    parse_pending(value, source_id="jp-test")
    assert [e.source_id for e in value.change_log.list()] == ["jp-test"]


def test_parse_failure_survives_log_write_failure(monkeypatch, caplog):
    value = deps()

    def broken(events):
        raise RuntimeError("log unavailable")

    monkeypatch.setattr(value.change_log, "append", broken)
    bad = add(value, b"\xff")
    assert bad.snapshot_id in parse_pending(value).failed
    assert value.manifest.get_parse(bad.sha256, FakeParser.name, FakeParser.version).status is ParseStatus.failed
    assert "解析失败事件写入失败" in caplog.text


def test_removed_document_without_chunks_uses_log_association():
    class Empty(FakeParser):
        def parse(self, snapshot, data, meta=None):
            parsed = super().parse(snapshot, data, meta)
            return ParseResult(documents=parsed.documents)

    value = deps()
    value.parsers = {("fake", "text/plain"): Empty}
    add(value)
    parse_pending(value)
    add(value, outcome=FetchOutcome.http_error, http_status=410)
    parse_pending(value)
    assert [e.doc_key for e in value.change_log.list(kinds=[ChangeKind.removed])] == ["jp-test:a.txt"]


@pytest.mark.parametrize("failure", ["detect", "append", "old_document", "fetch_list"])
def test_change_errors_warn_without_failing_parse(monkeypatch, caplog, failure):
    value = deps()

    def broken(*args, **kwargs):
        raise RuntimeError("change subsystem failed")

    if failure == "detect":
        monkeypatch.setattr("datumoj.pipeline.detect", broken)
    elif failure == "append":
        monkeypatch.setattr(value.change_log, "append", broken)
    elif failure == "old_document":
        monkeypatch.setattr(value.chunks, "get_document", broken)
    else:
        monkeypatch.setattr(value.change_log, "list", broken)
        add(value, outcome=FetchOutcome.http_error, http_status=404)
    good = add(value)
    if failure == "fetch_list":
        add(value, outcome=FetchOutcome.http_error, http_status=404)
    result = parse_pending(value)
    assert result.parsed == [good.snapshot_id] and not result.failed
    assert value.manifest.get_parse(good.sha256, FakeParser.name, FakeParser.version).status is ParseStatus.ok
    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_wiring_change_log_path_and_override(tmp_path, monkeypatch):
    from datumoj.wiring import build

    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    value = deps()
    overrides = {name: getattr(value, name) for name in (
        "raw", "manifest", "registry", "chunks", "keyword", "observations", "catalog", "parsers",
    )}
    overrides.update(crawlers={"fake": object}, tools=object(), retriever=object(), fetcher=object())
    wiring = build(tmp_path, overrides)
    assert isinstance(wiring.change_log, SqliteChangeLog)
    assert (tmp_path / "changes.sqlite").is_file()
    snap = add(value)
    assert parse_pending(wiring.pipeline_deps).parsed == [snap.snapshot_id]
    assert wiring.change_log.list()[0].new_sha256 == snap.sha256
    wiring.change_log.close()
    other = build(tmp_path / "override", {**overrides, "change_log": value.change_log})
    assert other.change_log is value.change_log and other.pipeline_deps.change_log is value.change_log
    assert not (tmp_path / "override" / "changes.sqlite").exists()


def test_optional_log_keeps_existing_pipeline_callers_working():
    value = replace(deps(), change_log=None)
    snap = add(value)
    assert parse_pending(value).parsed == [snap.snapshot_id]
