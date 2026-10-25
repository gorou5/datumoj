"""方案 A：单调发布点、必需库失败与只扫描 staged 的恢复。全部使用合成数据。"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier
from unittest.mock import Mock

import pytest

from datumoj.core.contracts import ParseError, ParseStatus
from datumoj.core.manifest import SqliteManifest
from datumoj.core.models import ChangeKind, PublishState, parse_context_fingerprint
from datumoj.index.fts import SqliteKeywordIndex
from datumoj.pipeline import PipelineDeps, parse_pending, reindex
from datumoj.store.chunks import SqliteChunkStore
from datumoj.testing.fakes import (
    FakeCatalogStore, FakeChangeLog, FakeChunkStore, FakeEmbedder, FakeKeywordIndex,
    FakeManifest, FakeObservationStore, FakeParser, FakeRawStore, FakeRegistry, FakeVectorIndex,
)
from datumoj.testing.samples import T0, make_observation, make_snapshot, make_spec

KEY = "jp-test:version.txt"


class PublicationParser(FakeParser):
    calls = 0
    broken = False

    def parse(self, snapshot, data, meta=None):
        type(self).calls += 1
        if self.broken:
            raise ParseError("synthetic parse failure")
        result = super().parse(snapshot, data, meta)
        if meta and meta.get("label"):
            result = result.model_copy(update={
                "chunks": [c.model_copy(update={"text": meta["label"]}) for c in result.chunks],
            })
        obs = make_observation(
            snapshot.snapshot_id, "1", source_id=snapshot.source_id, dataset_key="jp-test:ds",
            snapshot_sha256=snapshot.sha256, fetched_at=snapshot.fetched_at,
        )
        return result.model_copy(update={"observations": [obs]})


@pytest.fixture(params=["fake", "sqlite"])
def deps(request, tmp_path):
    PublicationParser.calls = 0
    PublicationParser.broken = False
    sqlite = request.param == "sqlite"
    value = PipelineDeps(
        manifest=SqliteManifest(tmp_path / "manifest.db") if sqlite else FakeManifest(),
        chunks=SqliteChunkStore(tmp_path / "chunks.db") if sqlite else FakeChunkStore(),
        keyword=SqliteKeywordIndex(tmp_path / "keyword.db") if sqlite else FakeKeywordIndex(),
        raw=FakeRawStore(), registry=FakeRegistry([make_spec(), make_spec("jp-other")]),
        catalog=FakeCatalogStore(), observations=FakeObservationStore(),
        vectors=FakeVectorIndex(FakeEmbedder()), change_log=FakeChangeLog(),
        parsers={("fake", "text/plain"): PublicationParser}, vector_state_path=tmp_path / "vectors.json",
    )
    yield value
    if sqlite:
        value.manifest.close()
        value.chunks.close()
        value.keyword.close()


def add(deps, sid, text, *, seconds=0, meta=None, url="https://example.go.jp/version.txt"):
    data = text.encode()
    deps.raw.put(data)
    snap = make_snapshot(snapshot_id=sid, data=data, url=url, fetched_at=T0 + timedelta(seconds=seconds))
    deps.manifest.record_snapshot(snap, meta)
    return snap


def record(deps, snap):
    return deps.manifest.get_parse(
        snap.sha256, PublicationParser.name, PublicationParser.version,
        parse_context_fingerprint(snap, deps.manifest.snapshot_meta(snap.snapshot_id)),
    )


def assert_current(deps, snap, text, absent):
    assert deps.chunks.get_document(KEY).snapshot_sha256 == snap.sha256
    assert deps.chunks.current_order(KEY) == deps.manifest.snapshot_order(snap.snapshot_id)
    assert deps.keyword.search(text).items
    assert not deps.keyword.search(absent).items


@pytest.mark.parametrize("point", ["chunks_after", "observations", "publish_before", "keyword_delete", "keyword_add", "vectors"])
def test_faults_and_next_run_recovery(deps, monkeypatch, point):
    old = add(deps, "old", "ancient")
    assert parse_pending(deps).parsed == ["old"]
    new = add(deps, "new", "modern", seconds=1)
    events_before = deps.change_log.list(kinds=[ChangeKind.added, ChangeKind.modified])

    def fail(*args, **kwargs):
        raise RuntimeError(f"injected {point}")

    with monkeypatch.context() as patch:
        if point == "chunks_after":
            put = deps.chunks.put_document

            def put_then_fail(doc, chunks, *, publish=True):
                assert publish is False
                put(doc, chunks, publish=publish)
                fail()

            patch.setattr(deps.chunks, "put_document", put_then_fail)
        elif point == "observations":
            patch.setattr(deps.observations, "write_table", fail)
        elif point == "publish_before":
            publish = deps.chunks.publish_current

            def fail_before_new(key, sha, order):
                if sha == new.sha256:
                    assert record(deps, new).doc_keys == [KEY]
                    assert deps.manifest.publish_state("new") is PublishState.staged
                    fail()
                return publish(key, sha, order)

            patch.setattr(deps.chunks, "publish_current", fail_before_new)
        elif point.startswith("keyword"):
            patch.setattr(deps.keyword, "delete" if point == "keyword_delete" else "add", fail)
        else:
            patch.setattr(deps.vectors, "add", fail)
        summary = parse_pending(deps)

    if point in {"chunks_after", "observations", "publish_before"}:
        assert "new" in summary.failed
        assert deps.manifest.publish_state("new") is PublishState.failed
        assert_current(deps, old, "ancient", "modern")
        assert deps.change_log.list(kinds=[ChangeKind.added, ChangeKind.modified]) == events_before
        assert record(deps, new).status is ParseStatus.failed
    else:
        assert deps.chunks.get_document(KEY).snapshot_sha256 == new.sha256
        assert deps.chunks.current_order(KEY) == deps.manifest.snapshot_order("new")
        if point.startswith("keyword"):
            assert "new" in summary.failed
            assert deps.manifest.publish_state("new") is PublishState.staged
            assert not deps.keyword.search("modern").items
            assert bool(deps.keyword.search("ancient").items) is (point == "keyword_delete")
        else:
            assert not summary.failed and "new" in summary.vectors_missing
            assert deps.manifest.publish_state("new") is PublishState.published
            assert_current(deps, new, "modern", "ancient")

    # 方案 A 允许发布失败后，解析成功的新观测值仍然可查。
    expected_obs = old if point == "observations" else new
    assert deps.observations.table_order("jp-test", "t1") == deps.manifest.snapshot_order(expected_obs.snapshot_id)
    assert deps.observations.query_readonly("SELECT obs_id FROM observations").rows == [(expected_obs.snapshot_id,)]

    # 用新 deps 实例确认恢复只依靠 manifest 与持久化的 vector missing。
    restarted = replace(deps, vector_state={"missing": {}, "last_error": None})
    summary = parse_pending(restarted)
    assert not summary.failed
    assert deps.manifest.publish_state("new") is PublishState.published
    assert_current(deps, new, "modern", "ancient")
    if point == "vectors":
        assert reindex(restarted, vectors_only=True) == 1
    assert restarted.vectors.search("modern").items
    assert len(deps.change_log.list(kinds=[ChangeKind.added, ChangeKind.modified])) == 2


def test_required_writes_all_finish_before_any_publish(deps, monkeypatch):
    old = add(deps, "old", "ancient")
    parse_pending(deps)
    add(deps, "new", "modern", seconds=1)
    publish = deps.chunks.publish_current

    def checked_publish(key, sha, order):
        if sha != old.sha256:
            assert deps.observations.query_readonly("SELECT obs_id FROM observations WHERE obs_id='new'").rows == [("new",)]
            assert deps.chunks.get_document(key, sha) is not None
            assert deps.chunks.get_document(key).snapshot_sha256 == old.sha256
            assert deps.keyword.search("ancient").items
        return publish(key, sha, order)

    monkeypatch.setattr(deps.chunks, "publish_current", checked_publish)
    assert not parse_pending(deps).failed


def test_no_staged_does_no_recovery_writes_or_chunk_scan(deps, monkeypatch):
    add(deps, "published", "stablecontent")
    parse_pending(deps)
    state = Mock(wraps=deps.manifest.record_publish_state)
    keyword_add = Mock(wraps=deps.keyword.add)
    keyword_delete = Mock(wraps=deps.keyword.delete)
    monkeypatch.setattr(deps.manifest, "record_publish_state", state)
    monkeypatch.setattr(deps.keyword, "add", keyword_add)
    monkeypatch.setattr(deps.keyword, "delete", keyword_delete)
    monkeypatch.setattr(deps.chunks, "iter_chunks", Mock(side_effect=AssertionError("full chunk scan")))
    monkeypatch.setattr(deps.manifest, "get_snapshot", Mock(side_effect=AssertionError("no staged lookup needed")))
    assert not parse_pending(deps).failed
    state.assert_not_called()
    keyword_add.assert_not_called()
    keyword_delete.assert_not_called()


@pytest.mark.parametrize("history_size", [1, 100])
def test_recovery_reads_only_staged_ids_and_their_output_keys(deps, monkeypatch, history_size):
    for i in range(history_size):
        add(deps, f"history-{i}", "archive", url=f"https://example.go.jp/{i}.txt")
    old = add(deps, "old", "ancient", seconds=1)
    parse_pending(deps)
    new = add(deps, "staged", "modern", seconds=2)
    with monkeypatch.context() as patch:
        patch.setattr(deps.keyword, "delete", Mock(side_effect=RuntimeError("index unavailable")))
        assert "staged" in parse_pending(deps).failed
    assert deps.manifest.publish_state("staged") is PublishState.staged
    assert deps.manifest.publish_state(old.snapshot_id) is PublishState.published
    lookup = Mock(wraps=deps.manifest.get_snapshot)
    chunks = Mock(wraps=deps.chunks.chunks_for)
    keyword_add = Mock(wraps=deps.keyword.add)
    state = Mock(wraps=deps.manifest.record_publish_state)
    vectors = Mock(wraps=deps.vectors.add)
    monkeypatch.setattr(deps.manifest, "get_snapshot", lookup)
    monkeypatch.setattr(deps.chunks, "chunks_for", chunks)
    monkeypatch.setattr(deps.keyword, "add", keyword_add)
    monkeypatch.setattr(deps.manifest, "record_publish_state", state)
    monkeypatch.setattr(deps.vectors, "add", vectors)
    monkeypatch.setattr(deps.chunks, "iter_chunks", Mock(side_effect=AssertionError("full chunk scan")))
    snapshots_for = deps.manifest.snapshots_for

    def filtered_history(source_id):
        assert source_id == "jp-other", "recovery must not enumerate source history"
        return snapshots_for(source_id)

    monkeypatch.setattr(deps.manifest, "snapshots_for", filtered_history)
    # 正常解析不访问 jp-test 的历史；恢复仍应处理它唯一的 staged。
    assert not parse_pending(deps, "jp-other").failed
    lookup.assert_called_once_with("staged")
    chunks.assert_called_once_with(KEY)
    assert keyword_add.call_count == vectors.call_count == 1
    state.assert_called_once_with("staged", PublishState.published)
    assert_current(deps, new, "modern", "ancient")


def test_superseded_staged_is_history_only(deps, monkeypatch):
    old = add(deps, "old", "ancient")
    parse_pending(deps)
    new = add(deps, "new", "modern", seconds=1)
    parse_pending(deps)
    deps.manifest.record_publish_state("old", PublishState.staged)
    keyword = Mock(wraps=deps.keyword.add)
    vectors = Mock(wraps=deps.vectors.add)
    monkeypatch.setattr(deps.keyword, "add", keyword)
    monkeypatch.setattr(deps.vectors, "add", vectors)
    assert not parse_pending(deps, "jp-other").failed
    assert deps.manifest.publish_state(old.snapshot_id) is PublishState.published
    keyword.assert_not_called()
    vectors.assert_not_called()
    assert_current(deps, new, "modern", "ancient")


def test_recovery_failure_stays_staged_and_can_retry(deps, monkeypatch):
    add(deps, "new", "modern")
    with monkeypatch.context() as patch:
        patch.setattr(deps.keyword, "add", Mock(side_effect=RuntimeError("index offline")))
        assert "new" in parse_pending(deps).failed
        assert "new" in parse_pending(deps).failed
        assert deps.manifest.publish_state("new") is PublishState.staged
        assert PublicationParser.calls == 1
    assert not parse_pending(deps).failed
    assert deps.manifest.publish_state("new") is PublishState.published
    assert deps.keyword.search("modern").items


def test_same_sha_current_slot_rewrite_is_reindexed_by_recovery(deps, monkeypatch):
    first = add(deps, "first", "samebytes", meta={"label": "alpha"})
    parse_pending(deps)
    add(deps, "later", "samebytes", seconds=1, meta={"label": "beta"})
    parse_pending(deps)
    assert deps.keyword.search("beta").items
    # 较旧上下文重写同一 SHA 槽位，发布水位不变，但关键词必须对应重写后的 current。
    with monkeypatch.context() as patch:
        patch.setattr(deps.keyword, "delete", Mock(side_effect=RuntimeError("index offline")))
        result = parse_pending(deps)
        assert first.snapshot_id in result.failed
    assert deps.manifest.publish_state("first") is PublishState.staged
    assert deps.chunks.current_order(KEY) == deps.manifest.snapshot_order("later")
    assert not parse_pending(deps, "jp-other").failed
    assert deps.manifest.publish_state("first") is PublishState.published
    texts = {c.text for c in deps.chunks.chunks_for(KEY)}
    assert all(deps.keyword.search(text).items for text in texts)


def test_concurrent_old_retries_do_not_touch_current_indexes(deps, tmp_path):
    PublicationParser.broken = True
    old = add(deps, "old", "ancient")
    assert "old" in parse_pending(deps).failed
    PublicationParser.broken = False
    new = add(deps, "new", "modern", seconds=1)
    # 先只允许新快照成功，旧失败保留至两个线程同时重试。
    parse_method = PublicationParser.parse

    def only_new(self, snapshot, data, meta=None):
        if snapshot.snapshot_id == "old":
            raise ParseError("old still unavailable")
        return parse_method(self, snapshot, data, meta)

    PublicationParser.parse = only_new
    try:
        parse_pending(deps)
    finally:
        PublicationParser.parse = parse_method
    before = deps.change_log.list(kinds=[ChangeKind.added, ChangeKind.modified])
    barrier = Barrier(2)

    class ConcurrentParser(PublicationParser):
        def parse(self, snapshot, data, meta=None):
            if snapshot.snapshot_id == "old":
                barrier.wait(timeout=10)
            return super().parse(snapshot, data, meta)

    persistent = isinstance(deps.manifest, SqliteManifest)

    def retry():
        manifest = SqliteManifest(tmp_path / "manifest.db") if persistent else deps.manifest
        chunks = SqliteChunkStore(tmp_path / "chunks.db") if persistent else deps.chunks
        # 旧重试的索引写入即为错误；SQLite 连接也不跨线程传递。
        indexes = Mock()
        indexes.delete.side_effect = AssertionError("old retry changed index")
        indexes.add.side_effect = AssertionError("old retry changed index")
        worker = replace(
            deps, manifest=manifest, chunks=chunks, keyword=indexes, vectors=indexes,
            observations=FakeObservationStore(), parsers={("fake", "text/plain"): ConcurrentParser},
        )
        try:
            result = parse_pending(worker)
            assert not result.failed
            assert chunks.get_document(KEY).snapshot_sha256 == new.sha256
            assert chunks.get_document(KEY, old.sha256) is not None
            indexes.add.assert_not_called()
            indexes.delete.assert_not_called()
        finally:
            if persistent:
                manifest.close()
                chunks.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(retry) for _ in range(2)]
        for future in futures:
            future.result(timeout=20)
    assert_current(deps, new, "modern", "ancient")
    assert deps.manifest.publish_state("old") is PublishState.published
    assert deps.change_log.list(kinds=[ChangeKind.added, ChangeKind.modified]) == before
