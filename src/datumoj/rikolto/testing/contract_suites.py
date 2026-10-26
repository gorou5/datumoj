"""每个协议一套可复用的合格测试。

实现方在自己的测试文件里继承对应的 Suite，覆盖它要求的 fixture（以及个别要求覆盖的方法）即可：

    from datumoj.rikolto.testing.contract_suites import ManifestSuite

    class TestSqliteManifest(ManifestSuite):
        @pytest.fixture
        def manifest(self, tmp_path):
            return SqliteManifest(tmp_path / "manifest.db")

Suite 的类名不以 Test 开头，不会被单独收集。测的是行为（返回值、错误类型、替换与保留语义），
不只是签名；假实现（``fakes.py``）必须全部通过，真实实现通过后就能替换假实现。
"""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, timedelta, timezone
from decimal import Decimal
from threading import Barrier
from typing import Any
from urllib.parse import urlsplit

import pytest

from datumoj.rikolto.core.contracts import (
    KIND_PATTERN,
    AmbiguousRelease,
    CatalogFilter,
    CatalogStore,
    ChangeLog,
    ChunkStore,
    ContentState,
    CorruptedBlob,
    CrawlContext,
    Crawler,
    DuplicateError,
    Embedder,
    FetchTask,
    GenerationUnavailable,
    HttpFetcher,
    InvalidGenerationToken,
    KeywordIndex,
    Manifest,
    ModelMismatch,
    NotFoundError,
    ObservationReleaseStore,
    ObservationStore,
    ParseError,
    Parser,
    ParseRecord,
    ParseResult,
    ParseStatus,
    PreconditionFailed,
    QueryError,
    QueryRejected,
    RawStore,
    Registry,
    ReleaseDateUnavailable,
    ReleaseNotFound,
    Retriever,
    SourceSpec,
    StateObjectStore,
    TooManyRows,
    UrlNotAllowed,
    VectorIndex,
    citation_for_chunk,
)
from datumoj.rikolto.core.models import (
    ChangeEvent,
    ChangeKind,
    EvidenceState,
    FetchOutcome,
    GapState,
    GenerationToken,
    LicenseInfo,
    ObservationRef,
    PublishState,
    Redistribution,
    SnapshotOrder,
    SourceText,
    UsageScope,
    parse_context_fingerprint,
    redact_url,
)
from datumoj.rikolto.testing.fakes import FakeEmbedder, FakeManifest, FakeRawStore, FakeResponse, drive_crawler
from datumoj.rikolto.testing.samples import (
    T0,
    ParseSample,
    RetrievalCorpus,
    make_chunk,
    make_dataset,
    make_document,
    make_observation,
    make_resource,
    make_snapshot,
    make_spec,
    make_state_generation,
    sha,
)

H = timedelta(hours=1)


def _missing(name: str) -> Any:
    raise NotImplementedError(f"实现方必须覆盖 fixture / 方法 {name}")


# ---------- StateObjectStore ----------


class StateObjectStoreSuite:
    """fixture ``state_store``：每个测试一个空存储；两个线程共享它竞争同一个键。"""

    @pytest.fixture
    def state_store(self) -> StateObjectStore:
        return _missing("state_store")

    def test_conforms(self, state_store):
        assert isinstance(state_store, StateObjectStore)

    def test_missing_and_empty_listing(self, state_store):
        assert state_store.get("missing") is None
        assert not state_store.exists("missing")
        assert state_store.list("") == []

    @pytest.mark.parametrize("data", [b"", b"\x00\xff\r\n"])
    def test_put_get_and_overwrite(self, state_store, data):
        assert state_store.put("copies/a", data) is None
        stored, etag = state_store.get("copies/a")
        assert stored == data and isinstance(etag, str) and etag
        assert state_store.exists("copies/a")
        state_store.put("copies/a", b"replacement")
        assert state_store.get("copies/a")[0] == b"replacement"
        assert state_store.get("copies/a")[1] != etag

    def test_create_only_and_failed_create_preserves_object(self, state_store):
        etag = state_store.put_if_absent("generations/1.json", b"one")
        assert isinstance(etag, str) and etag
        assert state_store.get("generations/1.json") == (b"one", etag)
        for data in (b"one", b"two"):
            with pytest.raises(PreconditionFailed):
                state_store.put_if_absent("generations/1.json", data)
            assert state_store.get("generations/1.json") == (b"one", etag)

    def test_matching_replace_and_stale_operations(self, state_store):
        old = state_store.put_if_absent("current.json", b"one")
        new = state_store.put_if_match("current.json", b"two", old)
        assert isinstance(new, str) and new and new != old
        assert state_store.get("current.json") == (b"two", new)
        with pytest.raises(PreconditionFailed):
            state_store.put_if_match("current.json", b"three", old)
        with pytest.raises(PreconditionFailed):
            state_store.delete_if_match("current.json", old)
        assert state_store.get("current.json") == (b"two", new)
        assert state_store.delete_if_match("current.json", new) is None
        assert state_store.get("current.json") is None
        assert not state_store.exists("current.json")
        assert state_store.list("") == []

    def test_missing_conditional_operations_fail(self, state_store):
        with pytest.raises(PreconditionFailed):
            state_store.put_if_match("missing", b"data", '"unknown"')
        with pytest.raises(PreconditionFailed):
            state_store.delete_if_match("missing", '"unknown"')
        assert state_store.get("missing") is None

    def test_listing_is_sorted_literal_prefix_and_detached(self, state_store):
        keys = ["copies/b", "other/a", "copies/a", "copies-plus/a"]
        for key in keys:
            state_store.put(key, b"data")
        assert state_store.list("") == sorted(keys)
        assert state_store.list("copies/") == ["copies/a", "copies/b"]
        assert state_store.list("copies") == ["copies-plus/a", "copies/a", "copies/b"]
        assert state_store.list("absent/") == []
        state_store.list("").clear()
        assert state_store.list("") == sorted(keys)

    @pytest.mark.parametrize("operation", ["put_if_absent", "put_if_match"])
    def test_two_writers_only_one_wins(self, state_store, operation):
        key = "current.json"
        args = () if operation == "put_if_absent" else (state_store.put_if_absent(key, b"old"),)
        barrier = Barrier(2)

        def compete(data):
            barrier.wait(timeout=5)
            try:
                return data, getattr(state_store, operation)(key, data, *args)
            except PreconditionFailed:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(compete, [b"writer-a", b"writer-b"]))
        winners = [outcome for outcome in outcomes if outcome is not None]
        assert len(winners) == 1
        assert state_store.get(key) == winners[0]


# ---------- Registry ----------


class RegistrySuite:
    """fixture ``make_registry(specs) -> Registry``：用给定的来源建一个注册表（真实实现可以先写成 yaml 再加载）。"""

    @pytest.fixture
    def make_registry(self) -> Callable[[list[SourceSpec]], Registry]:
        return _missing("make_registry")

    @pytest.fixture
    def specs(self) -> list[SourceSpec]:
        verified = LicenseInfo(
            name="CC BY 4.0", url="https://creativecommons.org/licenses/by/4.0/", attribution="出典：テスト省",
            usage_scope=UsageScope.redistributable, redistribution=Redistribution.allowed, outflow="raw_ok",
            verified_at=date(2026, 10, 1), notes="テスト用の検証根拠",
        )
        return [
            make_spec("jp-b", kind="ckan", needs_key="env:JP_B_KEY", schedule="daily"),
            make_spec("jp-a", kind="egov_law", license=verified, rate_per_sec=0.5, retention_note="約 7 日"),
        ]

    def test_conforms(self, make_registry, specs):
        assert isinstance(make_registry(specs), Registry)

    def test_get_returns_equal_spec(self, make_registry, specs):
        reg = make_registry(specs)
        for spec in specs:
            assert reg.get(spec.id) == spec

    def test_unknown_is_none(self, make_registry, specs):
        assert make_registry(specs).get("jp-unknown") is None

    def test_all_sorted_by_id(self, make_registry, specs):
        assert [s.id for s in make_registry(specs).all()] == ["jp-a", "jp-b"]

    def test_empty_registry(self, make_registry):
        reg = make_registry([])
        assert reg.all() == [] and reg.get("jp-a") is None


# ---------- RawStore ----------


class RawStoreSuite:
    """fixture ``store``；方法 ``corrupt(store, sha256)``：直接篡改已存内容（测 verify 用）。"""

    @pytest.fixture
    def store(self) -> RawStore:
        return _missing("store")

    def corrupt(self, store: RawStore, sha256: str) -> None:
        _missing("corrupt")

    @pytest.fixture
    def supports_corruption_recovery(self, store: RawStore) -> bool:
        # FakeRawStore is a lightweight contract fixture and is intentionally unchanged here.
        return not isinstance(store, FakeRawStore)

    def test_conforms(self, store):
        assert isinstance(store, RawStore)

    def test_put_returns_sha256_and_get_roundtrips(self, store):
        data = "個人情報の保護に関する法律".encode("cp932")
        digest = store.put(data)
        assert digest == hashlib.sha256(data).hexdigest()
        assert store.get(digest) == data
        assert store.exists(digest)

    def test_put_same_content_twice_is_noop(self, store):
        assert store.put(b"same") == store.put(b"same")
        assert store.get(sha(b"same")) == b"same"

    def test_put_stream(self, store):
        data = bytes(range(256)) * 12_000  # ~3MB
        digest = store.put(io.BytesIO(data))
        assert digest == sha(data)
        with store.open(digest) as fh:
            assert fh.read() == data

    def test_put_empty(self, store):
        digest = store.put(b"")
        assert digest == sha(b"") and store.get(digest) == b""

    def test_distinct_contents(self, store):
        a, b = store.put(b"a"), store.put(b"b")
        assert a != b and store.get(a) == b"a" and store.get(b) == b"b"

    def test_missing_blob(self, store):
        missing = sha(b"never stored")
        assert not store.exists(missing)
        for call in (store.get, store.open, store.verify):
            with pytest.raises(NotFoundError):
                call(missing)

    @pytest.mark.parametrize("bad", ["", "abc", "AB" * 32, "g" * 64, "../" + "a" * 61])
    def test_invalid_sha_rejected(self, store, bad):
        for call in (store.get, store.open, store.exists, store.verify):
            with pytest.raises(ValueError):
                call(bad)

    def test_verify_detects_corruption(self, store):
        digest = store.put(b"original bytes")
        store.verify(digest)
        self.corrupt(store, digest)
        with pytest.raises(CorruptedBlob):
            store.verify(digest)

    @pytest.mark.parametrize("method", ["get", "open"])
    def test_read_rejects_corrupted_blob_and_put_repairs_it(self, store, method, supports_corruption_recovery):
        if not supports_corruption_recovery:
            return
        data = b"original bytes"
        digest = store.put(data)
        self.corrupt(store, digest)
        with pytest.raises(CorruptedBlob):
            if method == "get":
                store.get(digest)
            else:
                with store.open(digest) as source:
                    source.read()

        assert store.put(data) == digest
        assert store.get(digest) == data


# ---------- Manifest ----------

URL_A = "https://example.go.jp/a.txt"
URL_B = "https://example.go.jp/b.txt"


class ManifestSuite:
    """fixture ``manifest``：一个空的 manifest。"""

    @pytest.fixture
    def manifest(self) -> Manifest:
        return _missing("manifest")

    def test_conforms(self, manifest):
        assert isinstance(manifest, Manifest)

    def test_roundtrip_keeps_every_field(self, manifest):
        snap = make_snapshot(url=URL_A, etag='"v1"', last_modified="Tue, 20 Oct 2026 00:00:00 GMT", license="CC BY")
        manifest.record_snapshot(snap)
        assert manifest.latest_snapshot(URL_A) == snap
        assert manifest.snapshots_for("jp-test") == [snap]

    def test_duplicate_snapshot_id(self, manifest):
        manifest.record_snapshot(make_snapshot(snapshot_id="s1"))
        with pytest.raises(DuplicateError):
            manifest.record_snapshot(make_snapshot(snapshot_id="s1", data=b"other"))

    def test_failed_fetch_is_kept_verbatim(self, manifest):
        snap = make_snapshot(url=URL_A, outcome=FetchOutcome.http_error, error="HTTP 503 Service Unavailable")
        manifest.record_snapshot(snap)
        assert manifest.latest_snapshot(URL_A) == snap
        assert manifest.last_success(URL_A) is None

    def test_latest_by_fetched_at_not_insertion(self, manifest):
        new = make_snapshot(url=URL_A, data=b"new", fetched_at=T0 + H, snapshot_id="new")
        old = make_snapshot(url=URL_A, data=b"old", fetched_at=T0, snapshot_id="old")
        manifest.record_snapshot(new)
        manifest.record_snapshot(old)
        assert manifest.latest_snapshot(URL_A).snapshot_id == "new"
        assert [s.snapshot_id for s in manifest.snapshots_for("jp-test")] == ["old", "new"]

    def test_latest_tie_prefers_later_write(self, manifest):
        manifest.record_snapshot(make_snapshot(url=URL_A, data=b"1", snapshot_id="first"))
        manifest.record_snapshot(make_snapshot(url=URL_A, data=b"2", snapshot_id="second"))
        assert manifest.latest_snapshot(URL_A).snapshot_id == "second"

    def test_last_success_skips_failures_and_not_modified(self, manifest):
        manifest.record_snapshot(make_snapshot(url=URL_A, fetched_at=T0, snapshot_id="ok"))
        manifest.record_snapshot(make_snapshot(url=URL_A, fetched_at=T0 + H, outcome=FetchOutcome.not_modified,
                                               snapshot_id="304"))
        manifest.record_snapshot(make_snapshot(url=URL_A, fetched_at=T0 + 2 * H, outcome=FetchOutcome.network_error,
                                               snapshot_id="err"))
        assert manifest.last_success(URL_A).snapshot_id == "ok"
        assert manifest.latest_snapshot(URL_A).snapshot_id == "err"

    def test_unknown_url(self, manifest):
        assert manifest.latest_snapshot(URL_B) is None
        assert manifest.last_success(URL_B) is None

    def test_lookup_by_url_ignores_keys(self, manifest):
        url = "https://example.go.jp/api?appId=SECRET&q=1"
        manifest.record_snapshot(make_snapshot(url=redact_url(url)))
        assert manifest.latest_snapshot(url) is not None
        assert manifest.last_success(url) is not None

    def test_snapshots_for_filters_source(self, manifest):
        manifest.record_snapshot(make_snapshot("jp-test", URL_A, snapshot_id="a"))
        manifest.record_snapshot(make_snapshot("jp-other", URL_B, snapshot_id="b"))
        assert [s.snapshot_id for s in manifest.snapshots_for("jp-other")] == ["b"]
        assert manifest.snapshots_for("jp-none") == []

    def test_snapshots_by_sha256(self, manifest):
        manifest.record_snapshot(make_snapshot(url=URL_A, data=b"same", snapshot_id="a", fetched_at=T0 + H))
        manifest.record_snapshot(make_snapshot(url=URL_B, data=b"same", snapshot_id="b", fetched_at=T0))
        manifest.record_snapshot(make_snapshot(url=URL_B, data=b"diff", snapshot_id="c"))
        assert [s.snapshot_id for s in manifest.snapshots_by_sha256(sha(b"same"))] == ["b", "a"]
        assert manifest.snapshots_by_sha256(sha(b"none")) == []

    def test_failure_summary(self, manifest):
        rec = manifest.record_snapshot
        rec(make_snapshot(url=URL_A, fetched_at=T0, outcome=FetchOutcome.http_error, snapshot_id="a1"))
        rec(make_snapshot(url=URL_A, fetched_at=T0 + H, snapshot_id="a2"))  # A 后来恢复了
        rec(make_snapshot(url=URL_B, fetched_at=T0, outcome=FetchOutcome.http_error, snapshot_id="b1"))
        rec(make_snapshot(url=URL_B, fetched_at=T0 + H, outcome=FetchOutcome.too_large, snapshot_id="b2"))
        summary = manifest.failure_summary("jp-test")
        assert summary.counts == {FetchOutcome.http_error: 2, FetchOutcome.too_large: 1}
        assert [s.snapshot_id for s in summary.latest_failures] == ["b2"]
        assert manifest.failure_summary("jp-none").counts == {}

    def test_freshness(self, manifest):
        assert manifest.freshness("jp-test").as_of is None
        rec = manifest.record_snapshot
        rec(make_snapshot(url=URL_A, fetched_at=T0, snapshot_id="a1"))
        rec(make_snapshot(url=URL_A, fetched_at=T0 + 3 * H, outcome=FetchOutcome.not_modified, snapshot_id="a2"))
        rec(make_snapshot(url=URL_B, fetched_at=T0 + H, snapshot_id="b1"))
        rec(make_snapshot(url=URL_B, fetched_at=T0 + 5 * H, outcome=FetchOutcome.network_error, snapshot_id="b2"))
        fresh = manifest.freshness("jp-test")
        assert fresh.as_of == T0 + 3 * H
        assert fresh.oldest_fetched_at == T0 + H
        assert fresh.by_source == {"jp-test": T0 + 3 * H}

    def test_snapshot_meta(self, manifest):
        meta = {"asof": "2026-10-20", "pending": [{"law": "令和七年法律第一号", "enforced": "2027-04-01"}], "n": 3}
        manifest.record_snapshot(make_snapshot(snapshot_id="with-meta"), meta)
        manifest.record_snapshot(make_snapshot(snapshot_id="no-meta", data=b"x"))
        assert manifest.snapshot_meta("with-meta") == meta
        assert manifest.snapshot_meta("no-meta") == {}
        with pytest.raises(NotFoundError):
            manifest.snapshot_meta("missing")

    def test_meta_must_be_json(self, manifest):
        with pytest.raises(ValueError):
            manifest.record_snapshot(make_snapshot(snapshot_id="bad"), {"x": object()})

    def test_snapshot_order_uses_fetch_time_then_persistent_sequence(self, manifest):
        assert manifest.snapshot_order("missing") is None
        for sid, when in [("new", T0 + H), ("old", T0), ("tie", T0 + H)]:
            manifest.record_snapshot(make_snapshot(snapshot_id=sid, fetched_at=when))
        new, old, tie = (manifest.snapshot_order(sid) for sid in ("new", "old", "tie"))
        assert old < new < tie
        assert new.fetched_at == T0 + H
        assert new.seq < old.seq < tie.seq

    def test_get_snapshot_by_id(self, manifest):
        assert manifest.get_snapshot("missing") is None
        snaps = [make_snapshot(snapshot_id="older"), make_snapshot(snapshot_id="newer", fetched_at=T0 + H),
                 make_snapshot(snapshot_id="failed", outcome=FetchOutcome.network_error)]
        for snap in snaps:
            manifest.record_snapshot(snap)
        for snap in snaps:
            assert manifest.get_snapshot(snap.snapshot_id) == snap

    def test_publish_state_is_per_snapshot_and_queryable(self, manifest):
        assert manifest.publish_state("missing") is None
        assert manifest.snapshots_in_publish_state(PublishState.staged) == []
        for sid in ("a", "b", "legacy"):
            manifest.record_snapshot(make_snapshot(snapshot_id=sid))
            assert manifest.publish_state(sid) is None
        manifest.record_publish_state("a", PublishState.staged)
        manifest.record_publish_state("b", PublishState.failed, "write failed")
        assert manifest.publish_state("a") is PublishState.staged
        assert manifest.publish_state("b") is PublishState.failed
        assert manifest.snapshots_in_publish_state(PublishState.staged) == ["a"]
        assert manifest.snapshots_in_publish_state(PublishState.failed) == ["b"]
        manifest.record_publish_state("b", PublishState.staged)
        assert set(manifest.snapshots_in_publish_state(PublishState.staged)) == {"a", "b"}
        assert manifest.snapshots_in_publish_state(PublishState.failed) == []
        manifest.record_publish_state("a", PublishState.published)
        assert manifest.snapshots_in_publish_state(PublishState.staged) == ["b"]
        assert manifest.snapshots_in_publish_state(PublishState.published) == ["a"]
        assert manifest.publish_state("legacy") is None
        with pytest.raises(NotFoundError):
            manifest.record_publish_state("missing", PublishState.staged)
        with pytest.raises(ValueError):
            manifest.record_publish_state("a", "invalid")

    def test_parse_context_is_part_of_key(self, manifest):
        snap = make_snapshot(snapshot_id="one")
        same = make_snapshot(snapshot_id="repeat", fetched_at=T0 + H)
        other = make_snapshot(url=URL_B, snapshot_id="other")
        for s in (snap, same, other):
            manifest.record_snapshot(s, {"nested": {"b": 2, "a": 1}})
        fp = parse_context_fingerprint(snap, manifest.snapshot_meta(snap.snapshot_id))
        same_fp = parse_context_fingerprint(same, {"nested": {"a": 1, "b": 2}})
        other_fp = parse_context_fingerprint(other, manifest.snapshot_meta(other.snapshot_id))
        assert fp == same_fp and fp != other_fp
        legacy = ParseRecord(sha256=snap.sha256, parser="p", version="1", status=ParseStatus.ok)
        manifest.record_parse(legacy)
        assert manifest.get_parse(snap.sha256, "p", "1", fp) is None
        record = legacy.model_copy(update={"context_fingerprint": fp})
        manifest.record_parse(record)
        assert manifest.get_parse(snap.sha256, "p", "1", same_fp) == record
        assert manifest.get_parse(snap.sha256, "p", "1", other_fp) is None
        changed = parse_context_fingerprint(snap, {"nested": {"a": 9}})
        assert manifest.get_parse(snap.sha256, "p", "1", changed) is None
        failed = record.model_copy(update={"context_fingerprint": other_fp, "status": ParseStatus.failed, "error": "bad"})
        manifest.record_parse(failed)
        assert manifest.get_parse(snap.sha256, "p", "1", fp) == record
        assert manifest.get_parse(snap.sha256, "p", "1", other_fp) == failed
        manifest.record_parse(record.model_copy(update={"error": "rewritten"}))
        assert manifest.get_parse(snap.sha256, "p", "1", other_fp) == failed

    def test_contextual_parse_stats_do_not_cross_resources(self, manifest):
        first = make_snapshot(snapshot_id="first")
        second = make_snapshot(snapshot_id="second", url=URL_B)
        manifest.record_snapshot(first)
        manifest.record_snapshot(second)
        manifest.record_parse(ParseRecord(sha256=first.sha256, parser="p", version="1", status=ParseStatus.ok,
                                           context_fingerprint=parse_context_fingerprint(first, {})))
        assert manifest.parse_stats(first.source_id) == {ParseStatus.ok: 1}

    def test_parse_records(self, manifest):
        digest = sha(b"hello")
        assert manifest.get_parse(digest, "p", "1") is None
        failed = ParseRecord(sha256=digest, parser="p", version="1", status=ParseStatus.failed, error="XML 损坏")
        manifest.record_parse(failed)
        assert manifest.get_parse(digest, "p", "1") == failed
        ok = ParseRecord(sha256=digest, parser="p", version="1", status=ParseStatus.ok)
        manifest.record_parse(ok)
        assert manifest.get_parse(digest, "p", "1") == ok
        assert manifest.get_parse(digest, "p", "2") is None
        assert manifest.get_parse(digest, "q", "1") is None

    def test_parse_outputs_and_last_successful_writer(self, manifest):
        digest = sha(b"shared")
        first = ParseRecord(sha256=digest, parser="p", version="1", status=ParseStatus.ok,
                            context_fingerprint=sha(b"context-a"), doc_keys=["jp-test:a", "jp-test:b"])
        second = first.model_copy(update={"context_fingerprint": sha(b"context-b"), "doc_keys": ["jp-test:b"]})
        assert manifest.last_writer("jp-test:a", digest) is None
        manifest.record_parse(first)
        assert manifest.get_parse(digest, "p", "1", first.context_fingerprint) == first
        manifest.record_parse(second)
        assert manifest.last_writer("jp-test:a", digest) == first.context_fingerprint
        assert manifest.last_writer("jp-test:b", digest) == second.context_fingerprint
        # 同一个解析键覆盖，也必须算新的成功写入。
        manifest.record_parse(first)
        assert manifest.last_writer("jp-test:b", digest) == first.context_fingerprint
        # 失败覆盖成功状态，仍保留最后成功写入者。
        manifest.record_parse(first.model_copy(update={"status": ParseStatus.failed, "error": "bad"}))
        manifest.record_parse(second.model_copy(update={"status": ParseStatus.no_parser}))
        assert manifest.last_writer("jp-test:b", digest) == first.context_fingerprint
        assert manifest.last_writer("jp-test:a", sha(b"other")) is None
        unknown = first.model_copy(update={"parser": "unknown", "doc_keys": None})
        manifest.record_parse(unknown)
        assert manifest.get_parse(digest, "unknown", "1", first.context_fingerprint).doc_keys is None
        empty = second.model_copy(update={"doc_keys": []})
        manifest.record_parse(empty)
        assert manifest.get_parse(digest, "p", "1", empty.context_fingerprint).doc_keys == []
        assert manifest.last_writer("jp-test:b", digest) == first.context_fingerprint

    def test_parse_stats_counts_snapshots_by_fetch_time(self, manifest):
        assert manifest.parse_stats("jp-test") == {}
        for sid, source, body, when in [
            ("old", "jp-test", b"same", T0),
            ("boundary", "jp-test", b"same", T0 + H),
            ("new", "jp-test", b"other", T0 + 2 * H),
            ("unparsed", "jp-test", b"unparsed", T0 + 3 * H),
            ("foreign", "jp-other", b"same", T0 + 4 * H),
        ]:
            manifest.record_snapshot(make_snapshot(source, data=body, snapshot_id=sid, fetched_at=when))
        manifest.record_snapshot(make_snapshot(snapshot_id="error", outcome=FetchOutcome.network_error,
                                               fetched_at=T0 + 4 * H))
        manifest.record_parse(ParseRecord(sha256=sha(b"same"), parser="p", version="1", status=ParseStatus.ok))
        manifest.record_parse(ParseRecord(sha256=sha(b"other"), parser="-", version="0", status=ParseStatus.no_parser))
        # 没有对应快照的解析记录不参与统计。
        manifest.record_parse(ParseRecord(sha256=sha(b"orphan"), parser="p", version="1", status=ParseStatus.ok))
        assert manifest.parse_stats("jp-test") == {ParseStatus.ok: 2, ParseStatus.no_parser: 1}
        boundary = (T0 + H).astimezone(timezone(timedelta(hours=9)))
        assert manifest.parse_stats("jp-test", boundary) == {ParseStatus.ok: 1, ParseStatus.no_parser: 1}
        assert manifest.parse_stats("jp-test", T0 + 3 * H) == {}
        assert manifest.parse_stats("jp-other") == {ParseStatus.ok: 1}
        assert manifest.parse_stats("jp-missing") == {}

    def test_parse_stats_uses_last_write_across_versions_and_parsers(self, manifest):
        snap = make_snapshot()
        manifest.record_snapshot(snap)
        first = ParseRecord(sha256=snap.sha256, parser="p", version="9", status=ParseStatus.ok)
        manifest.record_parse(first)
        manifest.record_parse(ParseRecord(sha256=snap.sha256, parser="p", version="10",
                                          status=ParseStatus.failed, error="bad format"))
        assert manifest.parse_stats(snap.source_id) == {ParseStatus.failed: 1}
        manifest.record_parse(ParseRecord(sha256=snap.sha256, parser="q", version="1", status=ParseStatus.no_parser))
        assert manifest.parse_stats(snap.source_id) == {ParseStatus.no_parser: 1}
        manifest.record_parse(first)
        assert manifest.parse_stats(snap.source_id) == {ParseStatus.ok: 1}

    def test_parse_stats_rejects_naive_boundary(self, manifest):
        with pytest.raises(ValueError):
            manifest.parse_stats("jp-test", T0.replace(tzinfo=None))


class ChangeLogSuite:
    """fixture ``change_log``：一个空的 ChangeLog。"""

    @pytest.fixture
    def change_log(self) -> ChangeLog:
        return _missing("change_log")

    @staticmethod
    def event(doc_id="a", **overrides):
        fields = {"source_id": "jp-test", "kind": ChangeKind.added, "doc_key": f"jp-test:{doc_id}",
                  "new_sha256": sha(b"new"), "detected_at": T0, "summary": "新增文档"}
        fields.update(overrides)
        return ChangeEvent.make(**fields)

    def test_conforms(self, change_log):
        assert isinstance(change_log, ChangeLog)
        assert change_log.list() == []

    def test_idempotent_append_preserves_first(self, change_log):
        first = self.event()
        repeat = self.event(detected_at=T0 + H, summary="再次发现", locator="page=2")
        assert first.event_id == repeat.event_id
        change_log.append(iter([first, first]))
        change_log.append([repeat])
        assert change_log.list() == [first]

    def test_order_limit_and_ties(self, change_log):
        a, b, c = self.event("a", detected_at=T0 + H), self.event("b"), self.event("c")
        change_log.append([a, b, c])
        assert change_log.list() == [b, c, a]
        assert change_log.list(limit=2) == [b, c]
        assert change_log.list(limit=0) == []
        with pytest.raises(ValueError):
            change_log.list(limit=-1)

    def test_filters_are_combined(self, change_log):
        a = self.event()
        b = self.event("b", kind=ChangeKind.fetch_failed, new_sha256=None)
        c = self.event(source_id="jp-other", doc_key="jp-other:c")
        change_log.append([a, b, c])
        assert change_log.list(source_ids=["jp-other"]) == [c]
        assert change_log.list(kinds=[ChangeKind.fetch_failed]) == [b]
        assert change_log.list(source_ids=["jp-test"], kinds=[ChangeKind.added]) == [a]
        assert change_log.list(source_ids=["jp-test", "jp-other"], kinds=[ChangeKind.added]) == [a, c]
        assert change_log.list(source_ids=[]) == []
        assert change_log.list(kinds=[]) == []

    def test_inclusive_time_boundaries(self, change_log):
        a, b, c = (self.event(str(i), detected_at=T0 + i * H) for i in range(3))
        change_log.append([c, a, b])
        assert change_log.list(since=T0 + H) == [b, c]
        assert change_log.list(until=T0 + H) == [a, b]
        same_instant = (T0 + H).astimezone(timezone(timedelta(hours=9)))
        assert change_log.list(since=same_instant, until=T0 + H) == [b]
        assert change_log.list(since=T0 + 2 * H, until=T0) == []
        assert change_log.list(since=T0 + H, until=T0 + 2 * H, kinds=[ChangeKind.added], limit=1) == [b]

    @pytest.mark.parametrize("field", ["since", "until"])
    def test_naive_time_rejected(self, change_log, field):
        with pytest.raises(ValueError):
            change_log.list(**{field: T0.replace(tzinfo=None)})


# ---------- HttpFetcher ----------

BASE = "https://example.go.jp"


class HttpFetcherSuite:
    """fixture ``make_fetcher(routes, max_bytes=None) -> HttpFetcher``。

    routes 是 {URL: FakeResponse}，URL 都在 https://example.go.jp 下（allowed_hosts 要包含它）。
    服务器行为用 ``FakeResponse.respond(if_none_match, if_modified_since)`` 得到（条件请求匹配时回 304）；
    ``network_error`` 不为空时模拟连接失败。重试和限速不能真的 sleep（时钟要注入）。
    """

    @pytest.fixture
    def make_fetcher(self) -> Callable[..., HttpFetcher]:
        return _missing("make_fetcher")

    def test_conforms(self, make_fetcher):
        assert isinstance(make_fetcher({}), HttpFetcher)

    def test_ok(self, make_fetcher):
        body = "人口,1234".encode("cp932")
        url = f"{BASE}/data.csv"
        fetcher = make_fetcher({url: FakeResponse(200, body, {"Content-Type": "text/csv", "ETag": '"v1"'})})
        r = fetcher.fetch(url)
        assert (r.outcome, r.http_status, r.content, r.url) == (FetchOutcome.ok, 200, body, url)
        assert r.sha256 == sha(body) and r.etag == '"v1"' and r.content_type.startswith("text/csv")
        assert r.fetched_at.tzinfo is not None

    def test_conditional_etag(self, make_fetcher):
        url = f"{BASE}/e"
        fetcher = make_fetcher({url: FakeResponse(200, b"body", {"ETag": '"v1"'})})
        r = fetcher.fetch(url, etag='"v1"')
        assert r.outcome is FetchOutcome.not_modified and r.content is None
        assert fetcher.fetch(url, etag='"v0"').outcome is FetchOutcome.ok

    def test_conditional_last_modified(self, make_fetcher):
        url, lm = f"{BASE}/lm", "Tue, 20 Oct 2026 00:00:00 GMT"
        fetcher = make_fetcher({url: FakeResponse(200, b"body", {"Last-Modified": lm})})
        assert fetcher.fetch(url, last_modified=lm).outcome is FetchOutcome.not_modified
        r = fetcher.fetch(url)
        assert r.outcome is FetchOutcome.ok and r.last_modified == lm

    def test_size_alone_keeps_default_get(self, make_fetcher):
        url = f"{BASE}/size"
        r = make_fetcher({url: FakeResponse(200, b"body")}).fetch(url, size=4, head_probe=False)
        assert (r.outcome, r.http_status, r.content) == (FetchOutcome.ok, 200, b"body")

    def test_head_probe_matches_both_validators(self, make_fetcher):
        url, lm = f"{BASE}/head", "Tue, 20 Oct 2026 00:00:00 GMT"
        headers = {"Last-Modified": lm, "Content-Length": "4", "ETag": '"new"'}
        fetcher = make_fetcher({url: FakeResponse(200, b"body", headers)})
        r = fetcher.fetch(url, etag='"old"', last_modified=lm, size=4, head_probe=True)
        assert (r.outcome, r.http_status, r.content) == (FetchOutcome.not_modified, 200, None)
        assert (r.etag, r.last_modified) == ('"new"', lm)

    def test_head_probe_size_mismatch_falls_back_to_conditional_get(self, make_fetcher):
        url, lm = f"{BASE}/head", "Tue, 20 Oct 2026 00:00:00 GMT"
        headers = {"Last-Modified": lm, "Content-Length": "4"}
        fetcher = make_fetcher({url: FakeResponse(200, b"body", headers)})
        r = fetcher.fetch(url, last_modified=lm, size=5, head_probe=True)
        assert (r.outcome, r.http_status, r.content) == (FetchOutcome.not_modified, 304, None)

    def test_head_probe_parameters_are_keyword_only(self, make_fetcher):
        with pytest.raises(TypeError):
            make_fetcher({}).fetch(f"{BASE}/head", None, None, 4, True)

    @pytest.mark.parametrize("status", [404, 500])
    def test_http_error(self, make_fetcher, status):
        url = f"{BASE}/missing"
        r = make_fetcher({url: FakeResponse(status, b"error page")}).fetch(url)
        assert (r.outcome, r.http_status, r.content) == (FetchOutcome.http_error, status, None)
        assert r.error

    def test_network_error(self, make_fetcher):
        url = f"{BASE}/down"
        r = make_fetcher({url: FakeResponse(network_error="connection refused")}).fetch(url)
        assert r.outcome is FetchOutcome.network_error and r.content is None and r.error

    def test_too_large(self, make_fetcher):
        url = f"{BASE}/big"
        r = make_fetcher({url: FakeResponse(200, b"x" * 1000)}, max_bytes=100).fetch(url)
        assert r.outcome is FetchOutcome.too_large and r.content is None and r.error

    @pytest.mark.parametrize("status", [200, 404])
    def test_keys_never_leak(self, make_fetcher, status):
        url = f"{BASE}/api?appId=SECRET123&statsDataId=1"
        r = make_fetcher({url: FakeResponse(status, b"{}")}).fetch(url)
        assert r.url == redact_url(url)
        assert "SECRET123" not in r.model_dump_json() and "SECRET123" not in repr(r)

    @pytest.mark.parametrize("url", ["ftp://example.go.jp/x", "file:///etc/passwd"])
    def test_non_http_rejected(self, make_fetcher, url):
        with pytest.raises(UrlNotAllowed):
            make_fetcher({}).fetch(url)


# ---------- Crawler ----------


def _host_allowed(host: str, patterns: Sequence[str]) -> bool:
    return any(host == p or (p.startswith("*.") and host.endswith(p[1:])) for p in patterns)


class CrawlerSuite:
    """fixture ``crawler``、``spec``（来源配置）、``make_fetcher() -> HttpFetcher``（每次给一个新的回放，
    覆盖这次采集会请求的全部 URL；可以用 ``FakeHttpFetcher``）。用 ``drive_crawler`` 驱动，不需要真实执行器。
    """

    @pytest.fixture
    def crawler(self) -> Crawler:
        return _missing("crawler")

    @pytest.fixture
    def spec(self) -> SourceSpec:
        return _missing("spec")

    @pytest.fixture
    def make_fetcher(self) -> Callable[[], HttpFetcher]:
        return _missing("make_fetcher")

    def test_declares_kind(self, crawler, spec):
        assert re.fullmatch(KIND_PATTERN, type(crawler).kind)
        assert type(crawler).kind == spec.kind

    def test_plan_yields_tasks_on_allowed_hosts(self, crawler, spec):
        ctx = CrawlContext(spec=spec, manifest=FakeManifest(), raw=FakeRawStore(), now=T0)
        tasks = list(crawler.plan(ctx))
        assert tasks, "plan 至少给出一个任务"
        for task in tasks:
            assert isinstance(task, FetchTask)
            assert _host_allowed(urlsplit(task.url).hostname or "", spec.allowed_hosts), task.redacted_url

    def test_crawl_terminates_within_scope(self, crawler, spec, make_fetcher):
        steps = drive_crawler(crawler, spec, make_fetcher(), now=T0)
        assert steps
        for step in steps:
            assert _host_allowed(urlsplit(step.task.url).hostname or "", spec.allowed_hosts)
            assert step.snapshot.resource_url == step.task.redacted_url

    def test_deterministic(self, crawler, spec, make_fetcher):
        first = [s.task.redacted_url for s in drive_crawler(crawler, spec, make_fetcher(), now=T0)]
        second = [s.task.redacted_url for s in drive_crawler(type(crawler)(), spec, make_fetcher(), now=T0)]
        assert first == second

    def test_rerun_stores_nothing_new(self, crawler, spec, make_fetcher):
        manifest, raw = FakeManifest(), FakeRawStore()
        drive_crawler(crawler, spec, make_fetcher(), manifest=manifest, raw=raw, now=T0)
        blobs = set(raw.blobs)
        drive_crawler(type(crawler)(), spec, make_fetcher(), manifest=manifest, raw=raw, now=T0 + H)
        assert set(raw.blobs) == blobs


# ---------- Parser ----------


class ParserSuite:
    """fixture ``parser``、``samples``（list[ParseSample]，真实样本）；``bad_samples``（应抛 ParseError 的
    原始字节，默认是一段 PNG 头，可以覆盖成损坏样本、含实体声明的恶意 XML 等）。
    """

    @pytest.fixture
    def parser(self) -> Parser:
        return _missing("parser")

    @pytest.fixture
    def samples(self) -> list[ParseSample]:
        return _missing("samples")

    @pytest.fixture
    def bad_samples(self) -> list[bytes]:
        return [b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00"]

    def test_declarations(self, parser):
        cls = type(parser)
        assert re.fullmatch(KIND_PATTERN, cls.source_kind)
        assert cls.content_type == "*" or re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", cls.content_type)
        assert cls.name.strip() and cls.version.strip()

    def test_samples_parse_consistently(self, parser, samples):
        assert samples, "至少提供一个样本"
        for s in samples:
            result = parser.parse(s.snapshot, s.data, s.meta)
            assert isinstance(result, ParseResult)
            result.check_against(s.snapshot)
            assert result.documents or result.observations or result.datasets, "样本应产出内容"

    def test_deterministic(self, parser, samples):
        for s in samples:
            assert parser.parse(s.snapshot, s.data, s.meta) == parser.parse(s.snapshot, s.data, s.meta)

    def test_bad_input_raises_parse_error(self, parser, samples, bad_samples):
        snap = samples[0].snapshot
        for data in bad_samples:
            bad = snap.model_copy(update={"sha256": sha(data), "size": len(data)})
            with pytest.raises(ParseError):
                parser.parse(bad, data, samples[0].meta)


# ---------- ChunkStore ----------

DOC = "jp-test:doc1"
SHA1, SHA2 = sha(b"v1"), sha(b"v2")


def _doc_v(sha256: str, texts: Sequence[str], doc_key: str = DOC) -> tuple[Any, list[Any]]:
    doc = make_document(doc_key, sha256)
    return doc, [make_chunk(doc_key, sha256, f"art={i}", t) for i, t in enumerate(texts, 1)]


class ChunkStoreSuite:
    """fixture ``store``：一个空的 ChunkStore。"""

    @pytest.fixture
    def store(self) -> ChunkStore:
        return _missing("store")

    def test_conforms(self, store):
        assert isinstance(store, ChunkStore)

    def test_put_and_get(self, store):
        doc, chunks = _doc_v(SHA1, ["第一条の本文", "第二条の本文", "第三条の本文"])
        store.put_document(doc, chunks)
        assert store.get_document(DOC) == doc
        assert store.chunks_for(DOC) == chunks
        assert store.get_chunk(chunks[1].chunk_id) == chunks[1]

    def test_new_snapshot_replaces_but_keeps_old(self, store):
        old_doc, old = _doc_v(SHA1, ["旧第一条", "旧第二条"])
        new_doc, new = _doc_v(SHA2, ["新第一条"])
        store.put_document(old_doc, old)
        store.put_document(new_doc, new)
        assert store.get_document(DOC) == new_doc
        assert store.chunks_for(DOC) == new
        assert store.get_chunk(new[0].chunk_id).text == "新第一条"
        assert store.get_chunk(old[1].chunk_id) is None, "当前版本已没有第二条"
        assert store.get_document(DOC, SHA1) == old_doc
        assert store.chunks_for(DOC, SHA1) == old
        assert store.get_chunk(old[1].chunk_id, SHA1) == old[1]

    def test_same_snapshot_rewrite_replaces(self, store):
        doc, chunks = _doc_v(SHA1, ["一", "二", "三"])
        store.put_document(doc, chunks)
        store.put_document(doc, chunks[:1])
        assert store.chunks_for(DOC) == chunks[:1]

    def test_rejects_inconsistent_chunks(self, store):
        doc, chunks = _doc_v(SHA1, ["一", "二"])
        other_doc = make_chunk("jp-test:other", SHA1, "art=9", "別文書")
        other_sha = make_chunk(DOC, SHA2, "art=9", "別快照")
        for bad in ([*chunks, other_doc], [*chunks, other_sha], [chunks[0], chunks[0]]):
            with pytest.raises(ValueError):
                store.put_document(doc, bad)

    def test_iter_chunks_current_only(self, store):
        store.put_document(*_doc_v(SHA1, ["旧"]))
        store.put_document(*_doc_v(SHA2, ["新"]))
        store.put_document(*_doc_v(SHA1, ["他の来源"], doc_key="jp-other:d"))
        assert sorted(c.text for c in store.iter_chunks()) == sorted(["新", "他の来源"])
        assert [c.text for c in store.iter_chunks(["jp-other"])] == ["他の来源"]

    def reopen_store(self, store):
        """内存实现保留实例；持久化实现覆盖本方法，关闭后重新打开。"""
        return store

    def test_staged_versions_are_invisible_until_published(self, store):
        doc, chunks = _doc_v(SHA1, ["暂存"])
        store.put_document(doc, chunks, publish=False)
        assert store.get_document(DOC) is None
        assert store.chunks_for(DOC) == []
        assert store.get_chunk(chunks[0].chunk_id) is None
        assert list(store.iter_chunks()) == []
        assert store.get_document(DOC, SHA1) == doc
        assert store.get_chunk(chunks[0].chunk_id, SHA1) == chunks[0]
        assert store.current_order(DOC) is None
        assert store.publish_current(DOC, SHA1, SnapshotOrder(fetched_at=T0, seq=1))
        assert store.chunks_for(DOC) == chunks

    def test_publication_is_monotonic_and_keeps_watermark(self, store):
        old_doc, old = _doc_v(SHA1, ["旧"])
        new_doc, new = _doc_v(SHA2, ["新"])
        early = SnapshotOrder(fetched_at=T0, seq=99)
        latest = SnapshotOrder(fetched_at=T0 + H, seq=1)
        store.put_document(new_doc, new, publish=False)
        assert store.publish_current(DOC, SHA2, latest)
        store.put_document(old_doc, old, publish=False)
        assert store.chunks_for(DOC) == new
        store = self.reopen_store(store)
        assert store.current_order(DOC) == latest
        assert not store.publish_current(DOC, SHA1, early)
        assert not store.publish_current(DOC, SHA1, latest)
        assert store.get_document(DOC) == new_doc
        assert store.current_order(DOC) == latest
        # 同一抓取时间的序号打破平局；回到旧字节也可以是更新的观察。
        tie = SnapshotOrder(fetched_at=T0 + H, seq=2)
        assert store.publish_current(DOC, SHA1, tie)
        assert store.chunks_for(DOC) == old
        assert store.current_order(DOC) == tie
        assert store.chunks_for(DOC, SHA2) == new

    def test_publication_is_scoped_by_document(self, store):
        store.put_document(*_doc_v(SHA1, ["一"]), publish=False)
        store.put_document(*_doc_v(SHA2, ["二"], "jp-other:d"), publish=False)
        newer = SnapshotOrder(fetched_at=T0 + H, seq=2)
        older = SnapshotOrder(fetched_at=T0, seq=1)
        assert store.publish_current(DOC, SHA1, newer)
        assert store.publish_current("jp-other:d", SHA2, older)
        with pytest.raises(NotFoundError):
            store.publish_current(DOC, sha(b"missing"), SnapshotOrder(fetched_at=T0 + 2 * H, seq=3))
        assert store.current_order(DOC) == newer
        assert store.get_document(DOC).snapshot_sha256 == SHA1
        assert store.current_order("jp-missing:d") is None

    def test_legacy_put_preserves_watermark(self, store):
        store.put_document(*_doc_v(SHA1, ["一"]))
        order = SnapshotOrder(fetched_at=T0, seq=1)
        store.publish_current(DOC, SHA1, order)
        store.put_document(*_doc_v(SHA2, ["二"]))
        assert store.get_document(DOC).snapshot_sha256 == SHA2
        assert store.current_order(DOC) == order

    def test_unknown(self, store):
        assert store.get_document("jp-test:none") is None
        assert store.get_chunk("jp-test:none#art=1") is None
        assert store.chunks_for("jp-test:none") == []
        assert list(store.iter_chunks()) == []


# ---------- KeywordIndex ----------


def _kw_corpus() -> list[Any]:
    s = SHA1
    return [
        make_chunk("jp-law:a", s, "art=1", "個人情報取扱事業者は、あらかじめ本人の同意を得ないで個人データを第三者に提供してはならない。",
                   title_path=["個人情報の保護に関する法律", "第二十七条"]),
        make_chunk("jp-law:a", s, "art=2", "行政機関の長は、開示請求があったときは開示しなければならない。",
                   title_path=["個人情報の保護に関する法律", "第七十八条"]),
        make_chunk("jp-law:b", s, "art=1", "２０２０年の国勢調査の結果を公表する。", title_path=["統計法"]),
        make_chunk("jp-news:c", s, "entry=1", "大阪府でAI創業支援の補助金の公募を開始した。", title_path=["お知らせ"]),
        make_chunk("jp-news:c", s, "entry=2", "東京都の人口推移について。", title_path=["お知らせ", "統計"]),
    ]


class KeywordIndexSuite:
    """fixture ``index``：一个空的 KeywordIndex。"""

    @pytest.fixture
    def index(self) -> KeywordIndex:
        return _missing("index")

    @pytest.fixture
    def filled(self, index) -> KeywordIndex:
        index.add(_kw_corpus())
        return index

    def _ids(self, result) -> list[str]:
        return [h.chunk_id for h in result.items]

    def test_conforms(self, index):
        assert isinstance(index, KeywordIndex)

    def test_substring_hit(self, filled):
        ids = self._ids(filled.search("第三者に提供"))
        assert ids == ["jp-law:a#art=1"]

    def test_no_hit(self, filled):
        assert filled.search("存在しない語句").items == []

    def test_title_path_is_indexed(self, filled):
        ids = self._ids(filled.search("個人情報の保護に関する法律"))
        assert set(ids) == {"jp-law:a#art=1", "jp-law:a#art=2"}

    def test_nfkc_normalized(self, filled):
        assert self._ids(filled.search("2020年の国勢調査")) == ["jp-law:b#art=1"]
        assert self._ids(filled.search("ＡＩ創業")) == ["jp-news:c#entry=1"]

    @pytest.mark.parametrize("query", ["大阪", "府"])
    def test_short_queries(self, filled, query):
        assert "jp-news:c#entry=1" in self._ids(filled.search(query))

    def test_source_filter(self, filled):
        ids = self._ids(filled.search("統計", source_ids=["jp-news"]))
        assert ids and all(i.startswith("jp-news:") for i in ids)

    def test_k_and_order(self, filled):
        result = filled.search("の", k=2)
        assert len(result.items) == 2
        scores = [h.score for h in result.items]
        assert scores == sorted(scores, reverse=True)

    def test_add_replaces_by_chunk_id(self, filled):
        old = _kw_corpus()[3]
        filled.add([old.model_copy(update={"text": "名古屋市の公募情報。"})])
        assert filled.search("AI創業").items == []
        assert self._ids(filled.search("名古屋市")) == [old.chunk_id]

    def test_delete_and_clear(self, filled):
        filled.delete("jp-law:a")
        assert filled.search("第三者に提供").items == []
        assert filled.search("国勢調査").items
        filled.delete("jp-law:none")
        filled.clear()
        assert filled.search("国勢調査").items == []

    @pytest.mark.parametrize("query,k", [("", 10), ("   ", 10), ("人口", 0)])
    def test_bad_args(self, filled, query, k):
        with pytest.raises(ValueError):
            filled.search(query, k=k)


# ---------- Embedder ----------


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


class EmbedderSuite:
    """fixture ``embedder``。"""

    @pytest.fixture
    def embedder(self) -> Embedder:
        return _missing("embedder")

    def test_conforms(self, embedder):
        assert isinstance(embedder, Embedder)
        assert isinstance(embedder.model, str) and embedder.model.strip()
        assert isinstance(embedder.dim, int) and embedder.dim > 0

    def test_shapes(self, embedder):
        vectors = embedder.embed(["個人情報", "人口推移", "AI"])
        assert len(vectors) == 3
        for v in vectors:
            assert len(v) == embedder.dim and all(math.isfinite(x) for x in v)
            assert any(x != 0 for x in v)

    def test_empty_input(self, embedder):
        assert embedder.embed([]) == []

    def test_order_and_stability(self, embedder):
        a, b = embedder.embed(["個人情報の保護", "東京都の人口"])
        b2, a2 = embedder.embed(["東京都の人口", "個人情報の保護"])
        assert _cos(a, a2) > 0.999 and _cos(b, b2) > 0.999
        assert _cos(a, b) < 0.999


# ---------- VectorIndex ----------


class VectorIndexSuite:
    """fixture ``make_index(embedder) -> VectorIndex``：同一个测试里多次调用要打开**同一份**索引存储；
    ``embedder`` 默认是 FakeEmbedder，可以覆盖成自己的确定性模型；``other_embedder`` 是模型名不同的另一个。
    """

    @pytest.fixture
    def make_index(self) -> Callable[[Embedder], VectorIndex]:
        return _missing("make_index")

    @pytest.fixture
    def embedder(self) -> Embedder:
        return FakeEmbedder()

    @pytest.fixture
    def other_embedder(self) -> Embedder:
        return FakeEmbedder(model="fake-hash-v2")

    @pytest.fixture
    def index(self, make_index, embedder) -> VectorIndex:
        return make_index(embedder)

    def test_conforms(self, index):
        assert isinstance(index, VectorIndex)

    def test_add_embeds_only_new_or_changed(self, index):
        chunks = _kw_corpus()
        assert index.add(chunks) == len(chunks)
        assert index.add(chunks) == 0
        changed = chunks[0].model_copy(update={"text": "改正後の本文。"})
        extra = make_chunk("jp-law:z", SHA1, "art=1", "新しい条文。")
        assert index.add([changed, chunks[1], extra]) == 2

    def test_exact_text_ranks_first(self, index):
        chunks = _kw_corpus()
        index.add(chunks)
        result = index.search(chunks[2].text, k=3)
        assert result.items[0].chunk_id == chunks[2].chunk_id
        assert len(result.items) == 3
        scores = [h.score for h in result.items]
        assert scores == sorted(scores, reverse=True)

    def test_source_filter(self, index):
        index.add(_kw_corpus())
        ids = [h.chunk_id for h in index.search("個人情報", k=10, source_ids=["jp-news"]).items]
        assert ids and all(i.startswith("jp-news:") for i in ids)

    def test_delete_and_clear(self, index):
        chunks = _kw_corpus()
        index.add(chunks)
        index.delete("jp-law:a")
        ids = {h.chunk_id for h in index.search("個人情報", k=10).items}
        assert ids == {c.chunk_id for c in chunks if c.doc_key != "jp-law:a"}
        index.clear()
        assert index.search("個人情報").items == []

    def test_model_mismatch(self, make_index, embedder, other_embedder):
        make_index(embedder).add(_kw_corpus())
        with pytest.raises(ModelMismatch):
            make_index(other_embedder)

    @pytest.mark.parametrize("query,k", [(" ", 10), ("人口", 0)])
    def test_bad_args(self, index, query, k):
        with pytest.raises(ValueError):
            index.search(query, k=k)


# ---------- Retriever ----------

LAW_SHA, NEWS_SHA = sha(b"law"), sha(b"news")


def retrieval_corpus() -> RetrievalCorpus:
    """RetrieverSuite 用的语料。jp-law、jp-news 有文档；jp-stat 已入库但没有块；jp-fail 只有失败抓取；
    jp-new 在注册表里但没抓过；jp-gone 不在注册表里。
    """
    law_spec = make_spec("jp-law", kind="egov_law", license=LicenseInfo(name="政府標準利用規約（第2.0版）",
                                                                        attribution="出典：e-Gov法令検索"))
    specs = [law_spec, make_spec("jp-news", kind="fake"), make_spec("jp-stat", kind="fake"),
             make_spec("jp-fail", kind="fake"), make_spec("jp-new", kind="fake")]
    law_url, news_url = "https://example.go.jp/law/a", "https://example.go.jp/news/c"
    snapshots = [
        make_snapshot("jp-law", law_url, b"law", fetched_at=T0, snapshot_id="law-1"),
        make_snapshot("jp-news", news_url, b"news", fetched_at=T0 + 2 * H, snapshot_id="news-1"),
        make_snapshot("jp-stat", "https://example.go.jp/stat", b"stat", fetched_at=T0, snapshot_id="stat-1"),
        make_snapshot("jp-fail", "https://example.go.jp/fail", outcome=FetchOutcome.http_error, snapshot_id="fail-1"),
    ]
    eff = date(2026, 10, 20)
    law_chunks = [
        make_chunk("jp-law:a", LAW_SHA, "art=27;para=1", "個人情報取扱事業者は、本人の同意を得ないで個人データを第三者に提供してはならない。",
                   title_path=["個人情報の保護に関する法律", "第二十七条"], effective_at=eff),
        make_chunk("jp-law:a", LAW_SHA, "art=28", "外国にある第三者への提供の制限について定める。",
                   title_path=["個人情報の保護に関する法律", "第二十八条"], effective_at=eff),
        make_chunk("jp-law:a", LAW_SHA, "art=78", "開示請求の手続について定める。",
                   title_path=["個人情報の保護に関する法律", "第七十八条"], effective_at=eff),
    ]
    news_chunks = [
        make_chunk("jp-news:c", NEWS_SHA, "entry=1", "大阪府でAI創業支援の補助金の公募を開始した。第三者機関が審査する。",
                   published_at=date(2026, 9, 1)),
        make_chunk("jp-news:c", NEWS_SHA, "entry=2", "個人情報の取扱いに関するお知らせ。", published_at=date(2025, 1, 10)),
    ]
    return RetrievalCorpus(
        specs=specs,
        snapshots=snapshots,
        documents=[
            (make_document("jp-law:a", LAW_SHA, "個人情報の保護に関する法律", effective_at=eff), law_chunks),
            (make_document("jp-news:c", NEWS_SHA, "お知らせ"), news_chunks),
        ],
    )


class RetrieverSuite:
    """fixture ``make_retriever(corpus: RetrievalCorpus) -> Retriever``：用语料建好全部依赖
    （注册表、manifest、chunk 存储和索引）后返回检索器。
    """

    @pytest.fixture
    def make_retriever(self) -> Callable[[RetrievalCorpus], Retriever]:
        return _missing("make_retriever")

    @pytest.fixture
    def corpus(self) -> RetrievalCorpus:
        return retrieval_corpus()

    @pytest.fixture
    def retriever(self, make_retriever, corpus) -> Retriever:
        return make_retriever(corpus)

    def _chunk(self, corpus: RetrievalCorpus, chunk_id: str) -> Any:
        return next(c for _, chunks in corpus.documents for c in chunks if c.chunk_id == chunk_id)

    def test_conforms(self, retriever):
        assert isinstance(retriever, Retriever)

    def test_hit_with_full_citation(self, retriever, corpus):
        result = retriever.search(["第三者に提供"])
        assert result.gap_state is GapState.ok
        hit = next(h for h in result.data if h.chunk_id == "jp-law:a#art=27;para=1")
        chunk = self._chunk(corpus, hit.chunk_id)
        assert hit.citation == citation_for_chunk(chunk, corpus.snapshots[0], corpus.specs[0].license)
        assert hit.citation.usage_scope is UsageScope.internal_only
        assert isinstance(hit.snippet, SourceText) and hit.snippet.content in chunk.text
        assert hit.doc_key == "jp-law:a" and hit.locator == "art=27;para=1"
        assert result.freshness.as_of is not None

    def test_dedup_order_and_k(self, retriever):
        result = retriever.search(["第三者", "個人情報", "提供"], k=3)
        ids = [h.chunk_id for h in result.data]
        assert len(ids) == len(set(ids)) and 0 < len(ids) <= 3
        scores = [h.score for h in result.data]
        assert scores == sorted(scores, reverse=True)

    def test_source_filter(self, retriever):
        result = retriever.search(["第三者"], source_ids=["jp-news"])
        assert result.data and all(h.citation.source_id == "jp-news" for h in result.data)

    def test_date_range(self, retriever):
        result = retriever.search(["個人情報"], date_range=(date(2026, 1, 1), None))
        ids = {h.chunk_id for h in result.data}
        assert "jp-news:c#entry=2" not in ids and "jp-law:a#art=27;para=1" in ids

    def test_freshness_from_used_snapshots(self, retriever, corpus):
        result = retriever.search(["大阪府でAI創業支援"], source_ids=["jp-news"])
        assert result.freshness.as_of == corpus.snapshots[1].fetched_at
        assert result.freshness.by_source.get("jp-news") == corpus.snapshots[1].fetched_at

    @pytest.mark.parametrize("source,state", [
        ("jp-stat", GapState.zero_hits),
        ("jp-fail", GapState.fetch_failed),
        ("jp-new", GapState.not_ingested),
        ("jp-gone", GapState.out_of_scope),
    ])
    def test_gap_states(self, retriever, source, state):
        result = retriever.search(["第三者"], source_ids=[source])
        assert result.data == [] and result.gap_state is state
        if state is GapState.fetch_failed:
            assert result.gap_detail

    @pytest.mark.parametrize("queries,k", [([], 10), (["", " "], 10), (["人口"], 0)])
    def test_bad_args(self, retriever, queries, k):
        with pytest.raises(ValueError):
            retriever.search(queries, k=k)


# ---------- CatalogStore ----------


def _catalog_data() -> tuple[list[Any], list[Any]]:
    datasets = [
        make_dataset("jp-data:pop", "人口統計データ", description="都道府県別の人口", publisher="総務省",
                     tags=["人口", "統計"], modified_at=T0),
        make_dataset("jp-data:holiday", "国民の祝日", description="祝日一覧（ＣＳＶ）", publisher=" 内閣府 ",
                     tags=["カレンダー"], modified_at=T0 - 400 * 24 * H),
        make_dataset("jp-data:nodate", "警備業の概況", publisher="警察庁"),
        make_dataset("jp-other:x", "人口統計（他の来源）", publisher="総務省", modified_at=T0),
    ]
    resources = [
        make_resource("jp-data:pop", "r1", "https://example.go.jp/pop.csv", format="ＣＳＶ", license=" "),
        make_resource("jp-data:pop", "r2", "https://example.go.jp/pop.xlsx", format="XLSX", license="cc-by"),
        make_resource("jp-data:holiday", "r1", "https://example.go.jp/syukujitsu.csv", format=".CSV", license=""),
        make_resource("jp-data:nodate", "r1", "https://example.go.jp/keibi.pdf", format="PDF"),
    ]
    return datasets, resources


class CatalogStoreSuite:
    """fixture ``store``：一个空的 CatalogStore。"""

    @pytest.fixture
    def store(self) -> CatalogStore:
        return _missing("store")

    @pytest.fixture
    def filled(self, store) -> CatalogStore:
        datasets, resources = _catalog_data()
        store.upsert(datasets, resources)
        return store

    def _keys(self, hits) -> list[str]:
        return [h.dataset.dataset_key for h in hits]

    def test_conforms(self, store):
        assert isinstance(store, CatalogStore)

    def test_roundtrip_keeps_raw_values(self, filled):
        datasets, resources = _catalog_data()
        assert filled.get_dataset("jp-data:holiday") == datasets[1]
        entries = filled.resources_for("jp-data:pop")
        assert [e.resource for e in entries] == resources[:2]
        assert [e.resource.license for e in entries] == [" ", "cc-by"]
        assert all(e.state is ContentState.catalog_only for e in entries)
        assert filled.get_dataset("jp-data:none") is None and filled.resources_for("jp-data:none") == []

    def test_resource_needs_dataset(self, store):
        with pytest.raises(ValueError):
            store.upsert(resources=[make_resource("jp-data:missing", "r1", "https://example.go.jp/x.csv")])

    def test_upsert_overwrites_but_keeps_state(self, filled):
        url = "https://example.go.jp/pop.csv"
        assert filled.set_content_state(url, ContentState.observations_loaded) == 1
        _, resources = _catalog_data()
        filled.upsert(resources=[resources[0].model_copy(update={"name": "改名"})])
        entry = filled.resources_for("jp-data:pop")[0]
        assert entry.resource.name == "改名" and entry.state is ContentState.observations_loaded
        filled.upsert([make_dataset("jp-data:pop", "人口統計データ（改訂）")])
        assert filled.get_dataset("jp-data:pop").title == "人口統計データ（改訂）"

    def test_hit_state_is_most_advanced(self, filled):
        assert filled.set_content_state("https://example.go.jp/pop.xlsx", ContentState.content_fetched) == 1
        assert filled.set_content_state("https://example.go.jp/none", ContentState.content_fetched) == 0
        hit = next(h for h in filled.search("人口統計") if h.dataset.dataset_key == "jp-data:pop")
        assert hit.state is ContentState.content_fetched
        assert {e.resource.resource_id: e.state for e in hit.resources}["r1"] is ContentState.catalog_only

    def test_search_fields(self, filled):
        assert "jp-data:pop" in self._keys(filled.search("人口統計"))
        assert self._keys(filled.search("祝日一覧")) == ["jp-data:holiday"]  # 说明
        assert self._keys(filled.search("カレンダー")) == ["jp-data:holiday"]  # 标签
        assert "jp-data:holiday" in self._keys(filled.search("csv"))  # NFKC + 大小写
        assert filled.search("存在しない語句") == []

    def test_filters(self, filled):
        assert sorted(self._keys(filled.search("人口統計"))) == ["jp-data:pop", "jp-other:x"]
        assert self._keys(filled.search("人口統計", CatalogFilter(source_ids=["jp-data"]))) == ["jp-data:pop"]
        assert self._keys(filled.search("", CatalogFilter(publishers=["内閣府"]))) == ["jp-data:holiday"]
        assert sorted(self._keys(filled.search("", CatalogFilter(formats=["csv"])))) == ["jp-data:holiday", "jp-data:pop"]
        recent = self._keys(filled.search("", CatalogFilter(modified_since=T0 - 24 * H)))
        assert sorted(recent) == ["jp-data:pop", "jp-other:x"]

    def test_k(self, filled):
        assert len(filled.search("", k=2)) == 2
        with pytest.raises(ValueError):
            filled.search("人口", k=0)


# ---------- ObservationStore ----------


def _obs_table() -> list[Any]:
    return [
        make_observation("o1", "13960000", area_code="13000", area_label="東京都", locator="row=1"),
        make_observation("o2", "8780000.5", area_code="27000", area_label="大阪府", locator="row=2",
                         dims={"性別": "総数"}),
        make_observation("o3", None, note="x", area_code="47000", area_label="沖縄県", locator="row=3"),
    ]


class ObservationStoreSuite:
    """fixture ``store``：一个空的 ObservationStore。"""

    @pytest.fixture
    def store(self) -> ObservationStore:
        return _missing("store")

    @pytest.fixture
    def filled(self, store) -> ObservationStore:
        store.write_table("jp-stat", "t1", _obs_table())
        return store

    def test_conforms(self, store):
        assert isinstance(store, ObservationStore)

    def test_query_with_provenance(self, filled):
        r = filled.query_readonly("SELECT obs_id, area_code, value, note FROM observations ORDER BY obs_id")
        assert r.columns == ["obs_id", "area_code", "value", "note"]
        assert [row[0] for row in r.rows] == ["o1", "o2", "o3"]
        assert Decimal(r.rows[1][2]) == Decimal("8780000.5")
        assert r.provenance == _obs_table()

    def test_missing_value_stays_null(self, filled):
        r = filled.query_readonly("SELECT value, note FROM observations WHERE obs_id = 'o3'")
        assert r.rows == [(None, "x")]

    def test_no_obs_id_no_provenance(self, filled):
        r = filled.query_readonly("SELECT count(*) AS n FROM observations")
        assert r.rows == [(3,)] and r.provenance == []

    def test_rewrite_replaces_table(self, filled):
        filled.write_table("jp-stat", "t1", [make_observation("o9", "1")])
        r = filled.query_readonly("SELECT obs_id FROM observations")
        assert r.rows == [("o9",)]
        filled.write_table("jp-stat", "t1", [])
        assert filled.query_readonly("SELECT obs_id FROM observations").rows == []

    def test_tables_are_independent(self, filled):
        filled.write_table("jp-stat", "t2", [make_observation("p1", "5", table_key="t2")])
        r = filled.query_readonly("SELECT table_key, count(*) AS n FROM observations GROUP BY table_key ORDER BY table_key")
        assert r.rows == [("t1", 3), ("t2", 1)]

    def test_table_order_allows_equal_and_newer_but_rejects_older(self, store):
        older = SnapshotOrder(fetched_at=T0, seq=99)
        newer = SnapshotOrder(fetched_at=T0 + H, seq=1)
        tie = SnapshotOrder(fetched_at=T0 + H, seq=2)
        assert store.table_order("jp-stat", "t1") is None
        assert store.write_table("jp-stat", "t1", _obs_table(), order=newer) is True
        assert store.write_table("jp-stat", "t1", [make_observation("old", "1")], order=older) is False
        assert store.query_readonly("SELECT count(*) FROM observations").rows == [(3,)]
        assert store.table_order("jp-stat", "t1") == newer
        assert store.write_table("jp-stat", "t1", [make_observation("equal", "2")], order=newer) is True
        assert store.query_readonly("SELECT obs_id FROM observations").rows == [("equal",)]
        assert store.write_table("jp-stat", "t1", [make_observation("tie", "3")], order=tie) is True
        assert store.table_order("jp-stat", "t1") == tie

    def test_ordered_delete_keeps_watermark_and_blocks_resurrection(self, store):
        older = SnapshotOrder(fetched_at=T0, seq=1)
        newer = SnapshotOrder(fetched_at=T0, seq=2)
        assert store.write_table("jp-stat", "t1", _obs_table(), order=newer)
        assert not store.write_table("jp-stat", "t1", [], order=older)
        assert store.query_readonly("SELECT count(*) FROM observations").rows == [(3,)]
        assert store.write_table("jp-stat", "t1", [], order=newer)
        assert store.table_order("jp-stat", "t1") == newer
        assert store.describe().tables == []
        assert not store.write_table("jp-stat", "t1", _obs_table(), order=older)
        assert store.query_readonly("SELECT obs_id FROM observations").rows == []
        assert store.write_table("jp-stat", "t1", _obs_table(), order=newer)

    def test_legacy_writes_are_unconditional_and_clear_order(self, store):
        assert store.write_table("jp-stat", "t1", _obs_table()) is True
        assert store.table_order("jp-stat", "t1") is None
        order = SnapshotOrder(fetched_at=T0, seq=1)
        assert store.write_table("jp-stat", "t1", _obs_table(), order=order)
        assert store.write_table("jp-stat", "t1", [make_observation("legacy", "1")]) is True
        assert store.table_order("jp-stat", "t1") is None
        assert store.write_table("jp-stat", "t1", [], order=order)
        assert store.write_table("jp-stat", "t1", []) is True
        assert store.table_order("jp-stat", "t1") is None

    @pytest.mark.parametrize("source_id,table_key", [("jp-stat", "t2"), ("jp-other", "t1")])
    def test_orders_are_isolated_by_source_and_table(self, store, source_id, table_key):
        newer = SnapshotOrder(fetched_at=T0 + H, seq=2)
        older = SnapshotOrder(fetched_at=T0, seq=1)
        store.write_table("jp-stat", "t1", _obs_table(), order=newer)
        obs = make_observation("other", "1", source_id=source_id, dataset_key=f"{source_id}:ds", table_key=table_key)
        assert store.write_table(source_id, table_key, [obs], order=older)
        assert store.table_order(source_id, table_key) == older
        assert store.table_order("jp-stat", "t1") == newer

    def test_write_validation(self, store):
        with pytest.raises(ValueError):
            store.write_table("jp-stat", "t2", _obs_table())  # table_key 不符
        with pytest.raises(ValueError):
            store.write_table("jp-stat", "t1", [make_observation("o1", "1"), make_observation("o1", "2")])

    @pytest.mark.parametrize("sql", [
        "INSERT INTO observations (obs_id) VALUES ('x')",
        "DELETE FROM observations",
        "DROP TABLE observations",
        "CREATE TABLE t AS SELECT 1",
        "SELECT 1; SELECT 2",
        "SELECT 1; DROP TABLE observations",
        "ATTACH 'other.db' AS other",
        "COPY observations TO 'out.csv'",
        "PRAGMA table_info('observations')",
        "SET enable_external_access = true",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_parquet('x.parquet')",
    ])
    def test_rejected(self, filled, sql):
        with pytest.raises(QueryRejected):
            filled.query_readonly(sql)
        assert len(filled.query_readonly("SELECT obs_id FROM observations").rows) == 3

    def test_too_many_rows(self, filled):
        with pytest.raises(TooManyRows):
            filled.query_readonly("SELECT obs_id FROM observations", max_rows=2)
        assert len(filled.query_readonly("SELECT obs_id FROM observations", max_rows=3).rows) == 3

    def test_sql_error(self, filled):
        with pytest.raises(QueryError):
            filled.query_readonly("SELECT no_such_column FROM observations")

    def test_describe(self, filled):
        cat = filled.describe()
        assert "value" in cat.columns and "obs_id" in cat.columns
        (t,) = cat.tables
        assert (t.source_id, t.table_key, t.rows, t.area_count) == ("jp-stat", "t1", 3, 3)
        assert t.surveys == ["国勢調査"] and t.indicators == ["人口"] and t.dataset_keys == ["jp-stat:pop"]
        assert (t.time_start, t.time_end) == (date(2020, 1, 1), date(2020, 12, 31))


class EvidenceIdentitySuite:
    """身份纯函数的合格测试，无存储 fixture。"""

    def test_observation_ref_roundtrip_and_table_isolation(self):
        for table in ["table", "a:b|c;d=e", '日本語/["\\]']:
            ref = ObservationRef(source_id="jp-test", table_key=table, obs_id="same:|;=\n行")
            assert ObservationRef.parse(str(ref)) == ref
            assert str(ref) == str(ObservationRef.parse(str(ref)))
        a = ObservationRef(source_id="jp-test", table_key="one", obs_id="id")
        b = ObservationRef(source_id="jp-test", table_key="two", obs_id="id")
        c = ObservationRef(source_id="jp-other", table_key="one", obs_id="id")
        assert len({str(a), str(b), str(c)}) == 3

    def test_fingerprint_stability_and_input_boundaries(self):
        snap = make_snapshot()
        meta = {"日本語": [None, True, {"a": 1, "b": 2}]}
        reordered = {"日本語": [None, True, {"b": 2, "a": 1}]}
        fp = parse_context_fingerprint(snap, meta)
        assert fp == parse_context_fingerprint(snap, reordered)
        repeat = make_snapshot(snapshot_id="repeat", fetched_at=T0 + H, etag='"different"')
        assert fp == parse_context_fingerprint(repeat, meta)
        for other in [make_snapshot("jp-other"), make_snapshot(url=URL_B), make_snapshot(content_type="text/html")]:
            assert fp != parse_context_fingerprint(other, meta)
        assert fp != parse_context_fingerprint(snap, {"日本語": [True, None, {"a": 1, "b": 2}]})
        assert parse_context_fingerprint(snap, None) == parse_context_fingerprint(snap, {})
        for invalid in [{"x": object()}, {"x": float("nan")}, {"x": float("inf")}]:
            with pytest.raises(ValueError):
                parse_context_fingerprint(snap, invalid)


RELEASE_SELECT = "SELECT source_id, table_key, obs_id, snapshot_sha256, release_id, value FROM observations"


class ObservationReleaseSuite:
    """301 的发布感知合格测试，与旧 ObservationStoreSuite 分开接入。

    fixture store 返回空库；publish(store, StateGeneration) 模拟完成条件发布；
    discard(store, generation) 模拟清单或任何依赖不可用。两者都是测试夹具，不是写库协议。
    """

    @pytest.fixture
    def store(self):
        return _missing("store")

    @pytest.fixture
    def publish(self):
        return _missing("publish")

    @pytest.fixture
    def discard(self):
        return _missing("discard")

    @pytest.fixture
    def releases(self, store):
        observations = []
        for seq, (release, day, value) in enumerate([
            ("first", date(2026, 4, 1), "1"),
            ("second", date(2026, 4, 1), "2"),
            ("third", date(2026, 10, 1), "3"),
        ], 1):
            obs = make_observation("same", value, release_id=release, released_at=day,
                                   snapshot_sha256=sha(release.encode()))
            store.write_table("jp-stat", "t1", [obs], order=SnapshotOrder(fetched_at=T0, seq=seq))
            observations.append(obs)
        return observations

    def test_as_of_date_and_release_keep_history(self, store, releases, publish):
        publish(store, make_state_generation())
        for cutoff, expected in [("first", releases[0]), ("second", releases[1]),
                                 (date(2026, 4, 1), releases[1]), (None, releases[2])]:
            result = store.query_readonly(RELEASE_SELECT, as_of_release=cutoff)
            assert result.provenance == [expected]
            assert result.row_evidence[0].identities == [expected.identity]
            assert result.row_evidence[0].generation == 1
            assert result.rows[0][-1] == expected.value
            assert result.evidence_state is EvidenceState.complete
        assert store.query_readonly(RELEASE_SELECT, as_of_release=date(2026, 3, 31)).rows == []

    def test_cross_table_and_source_identity(self, store, publish):
        observations = []
        for seq, (source, table) in enumerate([("jp-stat", "t1"), ("jp-stat", "t2"), ("jp-other", "t1")], 1):
            obs = make_observation("same", str(seq), source_id=source, dataset_key=f"{source}:pop",
                                   table_key=table, release_id=f"r{seq}", released_at=date(2026, 4, 1))
            store.write_table(source, table, [obs], order=SnapshotOrder(fetched_at=T0, seq=seq))
            observations.append(obs)
        publish(store, make_state_generation())
        result = store.query_readonly(RELEASE_SELECT + " ORDER BY value", as_of_release=date(2026, 4, 1))
        assert result.provenance == observations
        assert [r.identities for r in result.row_evidence] == [[o.identity] for o in observations]
        ambiguous = store.query_readonly("SELECT obs_id FROM observations")
        assert ambiguous.provenance == []
        assert ambiguous.evidence_state is EvidenceState.unavailable
        assert all(row.identities == [] for row in ambiguous.row_evidence)

    def test_token_pins_old_generation_during_publication(self, store, releases, publish):
        first_manifest = make_state_generation()
        publish(store, first_manifest)
        token = store.pin_generation()
        assert GenerationToken.parse(token).manifest_sha256 == sha(first_manifest.model_dump_json().encode())
        newer = make_observation("same", "4", release_id="fourth", released_at=date(2026, 10, 2),
                                 snapshot_sha256=sha(b"fourth"))
        store.write_table("jp-stat", "t1", [newer], order=SnapshotOrder(fetched_at=T0, seq=4))
        # 未发布的写入也不能泄露到当前代次。
        assert store.query_readonly(RELEASE_SELECT).provenance == [releases[2]]
        publish(store, make_state_generation(2))
        pinned = store.query_readonly(RELEASE_SELECT, generation_token=token)
        assert pinned.provenance == [releases[2]]
        assert pinned.generation_token == token and pinned.row_evidence[0].generation == 1
        assert store.query_readonly(RELEASE_SELECT).provenance == [newer]
        assert store.query_readonly(RELEASE_SELECT, generation_token=token,
                                    as_of_release="first").provenance == [releases[0]]
        with pytest.raises(ReleaseNotFound):
            store.query_readonly(RELEASE_SELECT, generation_token=token, as_of_release="fourth")

    def test_invalid_token_never_falls_back_even_for_empty_results(self, store, releases, publish):
        publish(store, make_state_generation())
        for token in ["", "garbage", "[]", '["rikolto-generation-v2",1,"' + "a" * 64 + '"]',
                      str(GenerationToken(generation=1, manifest_sha256="0" * 64))]:
            with pytest.raises(InvalidGenerationToken):
                store.query_readonly(RELEASE_SELECT + " WHERE 1=0", generation_token=token)
        unknown = str(GenerationToken(generation=2, manifest_sha256="0" * 64))
        with pytest.raises(GenerationUnavailable):
            store.query_readonly(RELEASE_SELECT, generation_token=unknown)

    def test_unavailable_generation_does_not_fall_back(self, store, releases, publish, discard):
        with pytest.raises(GenerationUnavailable):
            store.pin_generation()
        publish(store, make_state_generation())
        token = store.pin_generation()
        publish(store, make_state_generation(2))
        discard(store, 1)
        with pytest.raises(GenerationUnavailable):
            store.query_readonly(RELEASE_SELECT, generation_token=token)
        assert store.query_readonly(RELEASE_SELECT).row_evidence[0].generation == 2
        token2 = store.pin_generation()
        discard(store, 2)
        with pytest.raises(GenerationUnavailable):
            store.query_readonly(RELEASE_SELECT, generation_token=token2)
        with pytest.raises(GenerationUnavailable):
            store.query_readonly(RELEASE_SELECT)

    def test_legacy_releases_use_snapshot_and_utc_fetch_date(self, store, publish):
        old = make_observation("same", None, note="秘匿", snapshot_sha256=sha(b"legacy1"),
                               fetched_at=T0, released_at=date(1999, 1, 1))
        new = make_observation("same", "0", snapshot_sha256=sha(b"legacy2"), fetched_at=T0 + H * 25)
        for seq, obs in enumerate([old, new], 1):
            store.write_table("jp-stat", "t1", [obs], order=SnapshotOrder(fetched_at=obs.fetched_at, seq=seq))
        publish(store, make_state_generation())
        result = store.query_readonly(RELEASE_SELECT, as_of_release=old.snapshot_sha256)
        assert result.provenance == [old]
        assert result.rows[0][-1] is None and result.provenance[0].note == "秘匿"
        assert result.row_evidence[0].identities[0].release_id is None
        assert store.query_readonly(RELEASE_SELECT, as_of_release=date(1999, 1, 1)).rows == []
        assert store.query_readonly(RELEASE_SELECT, as_of_release=T0.astimezone(UTC).date()).provenance == [old]
        assert store.query_readonly(RELEASE_SELECT).provenance == [new]

    def test_as_of_aggregate_has_evidence_state_and_input_scope(self, store, releases, publish):
        publish(store, make_state_generation())
        token = store.pin_generation()
        sql = "SELECT sum(value) FROM observations"
        result = store.query_readonly(sql, generation_token=token, as_of_release="second")
        assert Decimal(str(result.rows[0][0])) == Decimal(2)
        assert result.evidence_state is EvidenceState.unavailable
        assert result.row_evidence[0].identities == [] and result.row_evidence[0].generation == 1
        assert result.evidence_inputs == {"sql": sql, "generation": 1, "as_of_release": "second"}

    def test_unknown_or_ambiguous_release_is_error(self, store, publish):
        for seq, source in enumerate(["jp-stat", "jp-other"], 1):
            obs = make_observation("same", "1", source_id=source, dataset_key=f"{source}:pop",
                                   release_id="shared-release", released_at=date(2026, 4, 1))
            store.write_table(source, "t1", [obs], order=SnapshotOrder(fetched_at=T0, seq=seq))
        publish(store, make_state_generation())
        with pytest.raises(ReleaseNotFound):
            store.query_readonly(RELEASE_SELECT, as_of_release="unknown")
        with pytest.raises(AmbiguousRelease):
            store.query_readonly(RELEASE_SELECT, as_of_release="shared-release")

    def test_explicit_release_without_date_does_not_use_fetch_date(self, store, publish):
        obs = make_observation("same", "1", release_id="undated")
        store.write_table("jp-stat", "t1", [obs], order=SnapshotOrder(fetched_at=T0, seq=1))
        publish(store, make_state_generation())
        with pytest.raises(ReleaseDateUnavailable):
            store.query_readonly(RELEASE_SELECT, as_of_release=T0.date())

    def test_missing_snapshot_order_is_not_invented(self, store, publish):
        store.write_table("jp-stat", "t1", [make_observation("same", "1")])
        publish(store, make_state_generation())
        with pytest.raises(QueryError):
            store.query_readonly(RELEASE_SELECT, as_of_release=T0.date())


    def test_snapshot_time_precedes_sequence_and_same_release_refetch(self, store, publish):
        first = make_observation("same", "1", release_id="one", released_at=date(2026, 4, 1),
                                 snapshot_sha256=sha(b"one"), fetched_at=T0)
        second = make_observation("same", "2", release_id="two", released_at=date(2026, 4, 1),
                                  snapshot_sha256=sha(b"two"), fetched_at=T0 + H)
        repeat = make_observation("same", "3", release_id="two", released_at=date(2026, 4, 1),
                                  snapshot_sha256=sha(b"two-refetch"), fetched_at=T0 + H * 2)
        for obs, seq in [(first, 99), (second, 2), (repeat, 1)]:
            store.write_table("jp-stat", "t1", [obs], order=SnapshotOrder(fetched_at=obs.fetched_at, seq=seq))
        publish(store, make_state_generation())
        assert store.query_readonly(RELEASE_SELECT, as_of_release="one").provenance == [first]
        assert store.query_readonly(RELEASE_SELECT, as_of_release="two").provenance == [repeat]

    def test_batch_accepts_distinct_release_identities(self, store, publish):
        first = make_observation("same", "1", release_id="one", released_at=date(2026, 4, 1))
        second = make_observation("same", "2", release_id="two", released_at=date(2026, 4, 2))
        store.write_table("jp-stat", "t1", [first, second], order=SnapshotOrder(fetched_at=T0, seq=1))
        publish(store, make_state_generation())
        assert store.query_readonly(RELEASE_SELECT, as_of_release="one").provenance == [first]
        assert store.query_readonly(RELEASE_SELECT).provenance == [second]


    def test_release_protocol_capability(self, store):
        assert isinstance(store, ObservationStore)
        assert isinstance(store, ObservationReleaseStore)
        assert "release_id" in store.describe().columns and "released_at" in store.describe().columns

    def test_deleted_current_does_not_resurrect_archived_releases(self, store, releases, publish):
        publish(store, make_state_generation())
        token = store.pin_generation()
        store.write_table("jp-stat", "t1", [], order=SnapshotOrder(fetched_at=T0, seq=4))
        publish(store, make_state_generation(2))
        assert store.query_readonly(RELEASE_SELECT).rows == []
        assert store.query_readonly(RELEASE_SELECT, generation_token=token).provenance == [releases[2]]
        assert store.query_readonly(RELEASE_SELECT, as_of_release="first").provenance == [releases[0]]

    def test_pinned_observations_do_not_share_mutable_input_or_result(self, store, publish):
        obs = make_observation("same", "1", dims={"kind": "original"})
        store.write_table("jp-stat", "t1", [obs], order=SnapshotOrder(fetched_at=T0, seq=1))
        publish(store, make_state_generation())
        token = store.pin_generation()
        obs.dims["kind"] = "input changed"
        result = store.query_readonly(RELEASE_SELECT, generation_token=token)
        assert result.provenance[0].dims == {"kind": "original"}
        result.provenance[0].dims["kind"] = "result changed"
        assert store.query_readonly(RELEASE_SELECT, generation_token=token).provenance[0].dims == {"kind": "original"}


    @pytest.mark.parametrize("cutoff", ["", "  ", 20260401, T0])
    def test_invalid_cutoff_is_rejected(self, store, releases, publish, cutoff):
        publish(store, make_state_generation())
        with pytest.raises(ValueError):
            store.query_readonly(RELEASE_SELECT, as_of_release=cutoff)


    def test_legacy_a_b_a_retains_first_occurrence_for_early_cutoff(self, store, publish):
        first = make_observation("same", "1", snapshot_sha256=sha(b"legacy-a"), fetched_at=T0)
        second = make_observation("same", "2", snapshot_sha256=sha(b"legacy-b"), fetched_at=T0 + H * 25)
        repeat = first.model_copy(update={"fetched_at": T0 + H * 49})
        assert first.identity == repeat.identity
        for seq, obs in enumerate([first, second, repeat], 1):
            store.write_table("jp-stat", "t1", [obs], order=SnapshotOrder(fetched_at=obs.fetched_at, seq=seq))
        publish(store, make_state_generation())
        for obs in [first, second, repeat]:
            day = obs.fetched_at.astimezone(UTC).date()
            assert store.query_readonly(RELEASE_SELECT, as_of_release=day).provenance == [obs]
        assert store.query_readonly(RELEASE_SELECT).provenance == [repeat]
        with pytest.raises(AmbiguousRelease):
            store.query_readonly(RELEASE_SELECT, as_of_release=first.snapshot_sha256)
