"""TACHIBANA-001：契约修订、假实现与 SQLite 解析统计，全程离线。"""

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from datumoj.rikolto.core.contracts import ChangeLog, FetchTask, ParseRecord, ParseStatus, RawStore
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import (
    META_PARENT_SHA256,
    META_PARENT_URL,
    ChangeEvent,
    ChangeKind,
    Dataset,
    DocKind,
    Document,
    FailureClass,
    FailureKind,
    FetchOutcome,
    classify_failure,
    classify_parse_failure,
    export_schemas,
    parent_ref,
    redact_url,
)
from datumoj.rikolto.core.rawstore import FileRawStore
from datumoj.rikolto.testing.contract_suites import ChangeLogSuite, ManifestSuite
from datumoj.rikolto.testing.fakes import FakeChangeLog, FakeManifest, FakeParser, FakeRawStore
from datumoj.rikolto.testing.samples import T0, make_dataset, make_document, make_snapshot, sha

URL = "https://example.go.jp/page?appId=REDACTED"
OLD, NEW = sha(b"old"), sha(b"new")


def event(**overrides):
    fields = {"source_id": "jp-test", "kind": ChangeKind.modified, "doc_key": "jp-test:law",
              "resource_url": URL, "old_sha256": OLD, "new_sha256": NEW, "detected_at": T0, "summary": "内容更新"}
    fields.update(overrides)
    return ChangeEvent.make(**fields)


def test_enum_values():
    assert set(DocKind) == {"law", "html_page", "pdf", "dataset", "bulletin"}
    assert set(ChangeKind) == {"added", "modified", "removed", "fetch_failed", "parse_failed"}
    assert set(FailureKind) == {"source_unavailable", "rate_limited", "auth_failed", "format_changed", "policy", "other"}


@pytest.mark.parametrize("kind", list(DocKind))
def test_document_kind_and_legacy_compatibility(kind):
    legacy = make_document("jp-test:law", NEW)
    assert legacy.kind is None
    assert Document.model_validate_json(legacy.model_dump_json(exclude={"kind"})).kind is None
    doc = make_document("jp-test:law", NEW, kind=kind)
    assert doc.kind is kind
    assert Document.model_validate_json(doc.model_dump_json()) == doc
    with pytest.raises(ValidationError):
        make_document("jp-test:law", NEW, kind="unknown")


def test_dataset_snapshot_reference_and_legacy_compatibility():
    legacy = make_dataset("jp-test:table", "統計")
    assert (legacy.snapshot_sha256, legacy.locator) == (None, None)
    assert Dataset.model_validate_json(legacy.model_dump_json(exclude={"snapshot_sha256", "locator"})) == legacy
    assert make_dataset("jp-test:table", "統計", snapshot_sha256=NEW).snapshot_sha256 == NEW
    record = make_dataset("jp-test:table", "統計", snapshot_sha256=NEW, locator="row=3")
    assert Dataset.model_validate_json(record.model_dump_json()) == record


@pytest.mark.parametrize("fields", [
    {"locator": "row=3"}, {"snapshot_sha256": "bad"}, {"snapshot_sha256": NEW, "locator": "bad=3"},
])
def test_dataset_rejects_invalid_snapshot_reference(fields):
    with pytest.raises(ValidationError):
        make_dataset("jp-test:table", "統計", **fields)


def test_event_identity_is_stable_and_has_fixed_encoding():
    first = event()
    identity = f'["jp-test","modified","jp-test:law","https://example.go.jp/page?appId=REDACTED","{OLD}","{NEW}"]'
    assert first.event_id == hashlib.sha256(identity.encode()).hexdigest()
    assert event(detected_at=T0 + timedelta(days=2), summary="別の説明", locator="page=3").event_id == first.event_id
    assert ChangeEvent.model_validate_json(first.model_dump_json()) == first
    assert event(kind="modified").event_id == first.event_id
    with pytest.raises(ValidationError):
        ChangeEvent.model_validate(first.model_dump() | {"event_id": "a" * 64})
    with pytest.raises(ValidationError):
        first.summary = "不可变"


@pytest.mark.parametrize("fields", [
    {"source_id": "jp-other", "doc_key": "jp-other:law"}, {"doc_key": "jp-test:other"},
    {"resource_url": URL + "&page=2"}, {"old_sha256": sha(b"older")}, {"new_sha256": sha(b"newer")},
    {"kind": ChangeKind.removed}, {"doc_key": None}, {"resource_url": None},
])
def test_each_identity_field_changes_id(fields):
    assert event(**fields).event_id != event().event_id


@pytest.mark.parametrize("kind,old,new", [
    (ChangeKind.added, None, NEW), (ChangeKind.removed, OLD, None), (ChangeKind.removed, OLD, NEW),
    (ChangeKind.modified, OLD, NEW), (ChangeKind.fetch_failed, None, None), (ChangeKind.parse_failed, None, NEW),
])
def test_valid_event_kinds(kind, old, new):
    assert event(kind=kind, old_sha256=old, new_sha256=new).kind is kind


@pytest.mark.parametrize("fields", [
    {"doc_key": None, "resource_url": None}, {"doc_key": "jp-other:law"}, {"doc_key": "bad"},
    {"source_id": "BAD"}, {"kind": "unknown"}, {"kind": "added", "old_sha256": None, "new_sha256": None},
    {"kind": "added"}, {"kind": "removed", "old_sha256": None},
    {"old_sha256": None}, {"new_sha256": None}, {"new_sha256": OLD}, {"old_sha256": "BAD"},
    {"resource_url": "https://example.go.jp/?key=SECRET"}, {"resource_url": "file:///data"},
    {"detected_at": T0.replace(tzinfo=None)}, {"locator": "page=1;page=2"},
    {"summary": ""}, {"summary": "  "}, {"summary": "one\ntwo"}, {"summary": "one\rtwo"},
    {"summary": "one\u2028two"},
])
def test_event_constraints(fields):
    with pytest.raises(ValidationError):
        event(**fields)


def test_event_required_fields():
    data = event().model_dump()
    for field in ["event_id", "source_id", "kind", "detected_at", "summary"]:
        with pytest.raises(ValidationError):
            ChangeEvent.model_validate({key: value for key, value in data.items() if key != field})


class TestFakeChangeLog(ChangeLogSuite):
    @pytest.fixture
    def change_log(self):
        return FakeChangeLog()


def test_change_log_default_limit():
    log = FakeChangeLog()
    assert isinstance(log, ChangeLog)
    log.append(event(doc_key=f"jp-test:{i}") for i in range(1001))
    assert len(log.list()) == 1000
    assert len(log.list(limit=1001)) == 1001


# 分类规则表：每个 outcome、每个特定状态码、全部 5xx / 其他 4xx 和未知情况。
FAILURE_CASES = [
    (FetchOutcome.network_error, None, FailureKind.source_unavailable, True),
    (FetchOutcome.spa_shell, 200, FailureKind.format_changed, False),
    (FetchOutcome.empty_body, 200, FailureKind.format_changed, False),
    (FetchOutcome.too_large, 200, FailureKind.policy, False),
    (FetchOutcome.blocked_by_robots, None, FailureKind.policy, False),
    *[(FetchOutcome.http_error, code, FailureKind.source_unavailable, True) for code in [408, *range(500, 600)]],
    *[(FetchOutcome.http_error, code, FailureKind.source_unavailable, False) for code in [404, 410]],
    (FetchOutcome.http_error, 429, FailureKind.rate_limited, True),
    *[(FetchOutcome.http_error, code, FailureKind.auth_failed, False) for code in [401, 403, 407]],
    *[(FetchOutcome.http_error, code, FailureKind.other, False) for code in range(400, 500)
      if code not in {401, 403, 404, 407, 408, 410, 429}],
    *[(FetchOutcome.http_error, code, FailureKind.other, False) for code in [None, 200, 304, 499, 600]],
]


@pytest.mark.parametrize("outcome,status,kind,retryable", FAILURE_CASES)
def test_failure_classification_table(outcome, status, kind, retryable):
    assert classify_failure(outcome, status) == FailureClass(kind=kind, retryable=retryable)


@pytest.mark.parametrize("outcome", [FetchOutcome.ok, FetchOutcome.not_modified])
@pytest.mark.parametrize("status", [None, 200, 304, 401, 404, 408, 429, 503])
def test_success_is_never_failure(outcome, status):
    assert classify_failure(outcome, status) is None


@pytest.mark.parametrize("outcome,status,kind,retryable", [
    (FetchOutcome.network_error, 404, FailureKind.source_unavailable, True),
    (FetchOutcome.spa_shell, 503, FailureKind.format_changed, False),
    (FetchOutcome.empty_body, 429, FailureKind.format_changed, False),
    (FetchOutcome.too_large, 503, FailureKind.policy, False),
    (FetchOutcome.blocked_by_robots, 403, FailureKind.policy, False),
])
def test_explicit_outcome_takes_priority(outcome, status, kind, retryable):
    assert classify_failure(outcome, status) == FailureClass(kind=kind, retryable=retryable)


def test_parse_failure_classification():
    result = classify_parse_failure()
    assert result == FailureClass(kind=FailureKind.format_changed, retryable=False)
    assert FailureClass.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("meta", [
    {}, {META_PARENT_URL: URL}, {META_PARENT_SHA256: NEW},
    {META_PARENT_URL: URL, META_PARENT_SHA256: "bad"},
    {META_PARENT_URL: URL, META_PARENT_SHA256: "AB" * 32},
    {META_PARENT_URL: URL, META_PARENT_SHA256: NEW + "\n"},
    {META_PARENT_URL: URL, META_PARENT_SHA256: None},
    {META_PARENT_URL: 123, META_PARENT_SHA256: NEW},
    {META_PARENT_URL: "file:///data", META_PARENT_SHA256: NEW},
    {META_PARENT_URL: "https://example.go.jp/?appId=SECRET", META_PARENT_SHA256: NEW},
    {META_PARENT_URL: "https://user:SECRET@example.go.jp/page", META_PARENT_SHA256: NEW},
])
def test_invalid_parent_reference(meta):
    assert parent_ref(meta) is None


@pytest.mark.parametrize("backend", ["fake", "sqlite"])
def test_parent_meta_roundtrips_to_parser(backend, tmp_path):
    manifest = FakeManifest() if backend == "fake" else SqliteManifest(tmp_path / "manifest.db")
    try:
        assert (META_PARENT_URL, META_PARENT_SHA256) == ("parent_url", "parent_sha256")
        meta = {META_PARENT_URL: redact_url("https://example.go.jp/?key=SECRET"), META_PARENT_SHA256: OLD}
        task = FetchTask(url="https://example.go.jp/attachment", meta=meta)
        snap = make_snapshot(data=b"document")
        manifest.record_snapshot(snap, task.meta)
        saved = manifest.snapshot_meta(snap.snapshot_id)
        assert saved == meta
        assert parent_ref(saved) == (meta[META_PARENT_URL], OLD)
        doc = FakeParser().parse(snap, b"document", saved).documents[0]
        assert doc.metadata == meta
        assert doc.kind is not None
        assert parent_ref(doc.metadata) == parent_ref(meta)
    finally:
        if backend == "sqlite":
            manifest.close()


class TestManifestStats(ManifestSuite):
    @pytest.fixture(params=["fake", "sqlite"])
    def manifest(self, request, tmp_path):
        result = FakeManifest() if request.param == "fake" else SqliteManifest(tmp_path / "manifest.db")
        yield result
        if request.param == "sqlite":
            result.close()


def test_parse_stats_persist_across_connections(tmp_path):
    path = tmp_path / "manifest.db"
    first = SqliteManifest(path)
    snap = make_snapshot()
    first.record_snapshot(snap)
    first.record_parse(ParseRecord(sha256=snap.sha256, parser="p", version="1", status=ParseStatus.ok))
    first.record_parse(ParseRecord(sha256=snap.sha256, parser="p", version="2", status=ParseStatus.failed, error="bad"))
    first.close()
    second = SqliteManifest(path)
    try:
        assert second.parse_stats(snap.source_id, T0) == {ParseStatus.failed: 1}
        second.record_parse(ParseRecord(sha256=snap.sha256, parser="p", version="1", status=ParseStatus.ok))
        assert second.parse_stats(snap.source_id) == {ParseStatus.ok: 1}
    finally:
        second.close()


def test_raw_store_contract_has_no_local_path(tmp_path):
    raw = FakeRawStore(tmp_path)  # 保留 IVY 调用方的构造参数。
    assert not hasattr(RawStore, "path_for")
    assert not hasattr(raw, "path_for")
    assert isinstance(raw, RawStore)
    digest = raw.put(b"remote-compatible")
    with raw.open(digest) as stream:
        assert stream.read() == b"remote-compatible"
    raw.verify(digest)


def test_file_raw_store_keeps_path_layout(tmp_path):
    # 从通用 RawStoreSuite 移出的本机布局断言，仅适用于 FileRawStore。
    store = FileRawStore(tmp_path)
    digest = store.put(b"x")
    path = store.path_for(digest)
    assert (path.parent.parent.name, path.parent.name, path.name) == ("raw", digest[:2], digest)
    assert path.read_bytes() == b"x"


def test_new_schemas_are_exported_and_committed(tmp_path):
    written = export_schemas(tmp_path)
    names = {p.stem.removesuffix(".schema") for p in written}
    assert {"DocKind", "ChangeKind", "ChangeEvent", "FailureKind", "FailureClass"} <= names
    root = Path(__file__).resolve().parents[2] / "schemas"
    for path in written:
        assert json.loads(path.read_text()) == json.loads((root / path.name).read_text())
