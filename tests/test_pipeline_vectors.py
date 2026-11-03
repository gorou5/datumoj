from __future__ import annotations

import json
from dataclasses import replace

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, wiring
from datumoj.rikolto.core.contracts import EmbeddingError, ParseStatus
from datumoj.rikolto.core.models import PublishState, parse_context_fingerprint
from datumoj.rikolto.pipeline import (
    ParseSummary,
    PipelineDeps,
    _load_vector_state,
    _retry_vectors,
    _start_vectors,
    parse_pending,
    reindex,
    vector_status,
)
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeEmbedder,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
    FakeVectorIndex,
)
from datumoj.rikolto.testing.samples import make_snapshot, make_spec


class BrokenVectorIndex:
    def delete(self, doc_key: str) -> None:
        pass

    def add(self, chunks) -> int:
        raise EmbeddingError("Ollama 返回 404：模型不存在")

    def clear(self) -> None:
        pass


def _deps(vector_state_path=None):
    spec = make_spec("jp-test")
    raw = FakeRawStore()
    manifest = FakeManifest()
    chunks = FakeChunkStore()
    keyword = FakeKeywordIndex()
    deps = PipelineDeps(
        manifest=manifest,
        raw=raw,
        registry=FakeRegistry([spec]),
        chunks=chunks,
        keyword=keyword,
        observations=FakeObservationStore(),
        catalog=FakeCatalogStore(),
        vectors=BrokenVectorIndex(),
        parsers={("fake", "text/plain"): FakeParser},
        vector_state_path=vector_state_path,
    )
    data = "標題\n\n本文を収録する。".encode()
    raw.put(data)
    snapshot = make_snapshot(
        url="https://example.go.jp/a.txt", data=data, snapshot_id="snapshot-1", source_id="jp-test"
    )
    manifest.record_snapshot(snapshot)
    return deps, snapshot


def test_embedding_failure_keeps_parse_and_keyword_index_and_can_be_retried(tmp_path):
    path = tmp_path / "vectors-state.json"
    deps, snapshot = _deps(path)

    summary = parse_pending(deps)

    assert summary.parsed == [snapshot.snapshot_id]
    assert not summary.failed
    assert snapshot.snapshot_id in summary.vectors_missing
    assert "EmbeddingError" in summary.vectors_missing[snapshot.snapshot_id]
    assert deps.manifest.get_parse(
        snapshot.sha256, "fake-text", "1",
        parse_context_fingerprint(snapshot, deps.manifest.snapshot_meta(snapshot.snapshot_id)),
    ).status is ParseStatus.ok
    assert deps.chunks.get_document("jp-test:a.txt") is not None
    assert deps.keyword.search("本文を収録").items
    assert path.exists()
    assert vector_status(deps) == {
        "state": "missing", "missing_blocks": 2, "error": "EmbeddingError"
    }

    # 新的依赖实例读取同一状态文件后，仍可定位缺失文档并补做。
    retry_deps = PipelineDeps(
        manifest=deps.manifest,
        raw=deps.raw,
        registry=deps.registry,
        chunks=deps.chunks,
        keyword=deps.keyword,
        observations=deps.observations,
        catalog=deps.catalog,
        vectors=FakeVectorIndex(FakeEmbedder()),
        parsers=deps.parsers,
        vector_state_path=path,
    )
    assert vector_status(retry_deps)["state"] == "missing"
    assert reindex(retry_deps, vectors_only=True) == 2
    assert vector_status(retry_deps) == {"state": "available", "blocks": 2}
    assert retry_deps.vectors.search("本文を収録").items


def test_vectors_off_skips_vector_construction_and_wires_none(tmp_path, monkeypatch):
    monkeypatch.setenv("DATUMOJ_VECTORS", "off")
    original_import = wiring._import
    vector_imports = []

    def tracked_import(module_name, symbol):
        if module_name in {"datumoj.rikolto.index.embed", "datumoj.rikolto.index.vector"}:
            vector_imports.append(module_name)
            raise AssertionError("vectors=off 不应导入向量模块")
        return original_import(module_name, symbol)

    monkeypatch.setattr(wiring, "_import", tracked_import)
    spec = make_spec("jp-test")
    overrides = {
        "registry": FakeRegistry([spec]),
        "raw": FakeRawStore(),
        "manifest": FakeManifest(),
        "chunks": FakeChunkStore(),
        "keyword": FakeKeywordIndex(),
        "vectors": object(),
        "catalog": FakeCatalogStore(),
        "observations": FakeObservationStore(),
        "parse_pending": parse_pending,
        "reindex": reindex,
        "retriever": object(),
        "tools": object(),
        "crawlers": {"fake": FakeParser},
        "fetcher": object(),
        "run_crawl": object(),
        "crawl_deps_type": object(),
    }

    deps = wiring.build(tmp_path, overrides)

    assert deps.vectors is None
    assert deps.pipeline_deps.vectors is None
    assert not vector_imports


def test_status_prints_missing_vector_count_and_error(tmp_path, monkeypatch):
    vector_deps, _ = _deps(tmp_path / "vectors-state.json")
    # Create the deferred vector state through the same pipeline path as production.
    parse_pending(vector_deps)

    class Tools:
        def coverage(self):
            return {"data": {"sources": []}, "warnings": []}

    built = wiring.Wiring(
        data_dir=tmp_path,
        vectors=vector_deps.vectors,
        pipeline_deps=vector_deps,
        tools=Tools(),
    )
    monkeypatch.setattr(cli.wiring, "build", lambda data_dir: built)

    result = CliRunner().invoke(cli.app, ["status"])

    assert result.exit_code == 0
    assert "向量索引：暂缺 2 个块" in result.output
    assert "EmbeddingError" in result.output


def test_parse_command_lists_vector_missing_snapshot_and_cause(tmp_path, monkeypatch):
    deps, snapshot = _deps()
    built = wiring.Wiring(
        data_dir=tmp_path,
        vectors=deps.vectors,
        pipeline_deps=deps,
        parse_pending=parse_pending,
    )
    monkeypatch.setattr(cli.wiring, "build", lambda data_dir: built)

    result = CliRunner().invoke(cli.app, ["parse"])

    assert result.exit_code == 0
    assert snapshot.snapshot_id in result.output
    assert "向量暂缺" in result.output
    assert "EmbeddingError" in result.output


def test_transient_failure_retries_next_parse_without_reparsing(tmp_path):
    deps, snapshot = _deps(tmp_path / "vectors-state.json")
    parse_pending(deps)
    assert _load_vector_state(deps)["missing"]["jp-test:a.txt"]["failures"] == 1
    restarted = replace(deps, vectors=FakeVectorIndex(FakeEmbedder()), vector_state={"missing": {}})
    summary = parse_pending(restarted)
    assert not summary.parsed and not summary.failed and not summary.vectors_missing
    assert restarted.manifest.publish_state(snapshot.snapshot_id).value == "published"
    assert json.loads(restarted.vector_state_path.read_text()) == {"missing": {}, "last_error": None}
    assert restarted.vectors.search("本文を収録").items


def test_three_failures_stop_automatic_retries_and_keep_pending(tmp_path, monkeypatch):
    deps, snapshot = _deps(tmp_path / "vectors-state.json")
    calls = []
    add = deps.vectors.add

    def fail(chunks):
        calls.append(len(chunks))
        return add(chunks)

    monkeypatch.setattr(deps.vectors, "add", fail)
    for count in (1, 2, 3):
        assert parse_pending(deps).vectors_missing == {snapshot.snapshot_id: "EmbeddingError"}
        assert _load_vector_state(deps)["missing"]["jp-test:a.txt"]["failures"] == count
    before = deps.vector_state_path.read_bytes()
    assert not parse_pending(replace(deps, vector_state={"missing": {}})).vectors_missing
    assert deps.vector_state_path.read_bytes() == before
    assert calls == [2, 2, 2]
    # The retained entry can still be explicitly repaired by an operator.
    deps.vectors = FakeVectorIndex(FakeEmbedder())
    assert reindex(deps, vectors_only=True) == 2
    assert not _load_vector_state(deps)["missing"]


def test_legacy_vector_state_defaults_failures_to_zero_and_retries(tmp_path):
    deps, _ = _deps(tmp_path / "vectors-state.json")
    parse_pending(deps)
    state = json.loads(deps.vector_state_path.read_text())
    del state["missing"]["jp-test:a.txt"]["failures"]
    deps.vector_state_path.write_text(json.dumps(state))
    assert _load_vector_state(deps)["missing"]["jp-test:a.txt"]["failures"] == 0
    restarted = replace(deps, vectors=FakeVectorIndex(FakeEmbedder()), vector_state={"missing": {}})
    assert not parse_pending(restarted).vectors_missing
    assert not _load_vector_state(restarted)["missing"]


def test_zero_allowance_preserves_error_and_retry_count(tmp_path):
    deps, _ = _deps(tmp_path / "vectors-state.json")
    parse_pending(deps)
    before = deps.vector_state_path.read_bytes()
    parse_pending(replace(deps, vector_limit=0))
    assert deps.vector_state_path.read_bytes() == before
    assert _load_vector_state(deps)["missing"]["jp-test:a.txt"]["failures"] == 1


@pytest.mark.parametrize("count", [-1, True, "1", None])
def test_invalid_retry_count_is_rejected(tmp_path, count):
    deps, _ = _deps(tmp_path / "vectors-state.json")
    parse_pending(deps)
    state = json.loads(deps.vector_state_path.read_text())
    state["missing"]["jp-test:a.txt"]["failures"] = count
    deps.vector_state_path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="向量重试计数无效"):
        _load_vector_state(deps)


def test_retry_counts_only_new_embeddings_when_previous_attempt_partially_wrote(tmp_path):
    deps, _ = _deps(tmp_path / "vectors-state.json")
    parse_pending(deps)
    deps.vectors = FakeVectorIndex(FakeEmbedder())
    chunks = deps.chunks.chunks_for("jp-test:a.txt")
    # A previous failed SDK call may already have committed some rows.
    deps.vectors.add(chunks[:1])
    deps.vector_limit = 2
    _start_vectors(deps)
    assert _retry_vectors(deps, None, ParseSummary()) == 1
    assert deps._vector_remaining == 0  # Attempts remain conservatively reserved.
    assert not _load_vector_state(deps)["missing"]


def _set_embed(deps, value):
    spec = deps.registry.get("jp-test")
    deps.registry = FakeRegistry([spec.model_copy(update={"scope": {**spec.scope, "embed": value}})])


def test_source_embed_false_keeps_keyword_and_publication_without_using_allowance(tmp_path, monkeypatch):
    deps, snapshot = _deps(tmp_path / "vectors-state.json")
    _set_embed(deps, False)
    deps.vector_limit = 2
    deps.vectors = FakeVectorIndex(FakeEmbedder())

    def forbidden(*args):
        raise AssertionError("disabled source touched vectors")

    monkeypatch.setattr(deps.vectors, "add", forbidden)
    monkeypatch.setattr(deps.vectors, "delete", forbidden)
    summary = parse_pending(deps)
    assert summary.parsed == [snapshot.snapshot_id] and not summary.failed and not summary.vectors_missing
    assert summary.vector_remaining == 2
    assert deps.manifest.publish_state(snapshot.snapshot_id) is PublishState.published
    assert deps.keyword.search("本文を収録").items
    assert not deps.vectors.search("本文を収録").items
    assert not _load_vector_state(deps)["missing"]


def test_disabled_backlog_is_pruned_but_other_and_unidentified_entries_remain(tmp_path):
    deps, snapshot = _deps(tmp_path / "vectors-state.json")
    parse_pending(deps)
    state = _load_vector_state(deps)
    entry = dict(state["missing"]["jp-test:a.txt"])
    state["missing"].update({
        "snapshot-only": dict(entry),
        "other": {**entry, "snapshot_id": "other-snapshot"},
        "unidentified": {**entry, "snapshot_id": None},
    })
    deps.manifest.record_snapshot(make_snapshot(snapshot_id="other-snapshot", source_id="jp-other"))
    deps.vector_state_path.write_text(json.dumps(state))
    _set_embed(deps, False)
    deps.vector_limit = 0
    parse_pending(deps)
    saved = json.loads(deps.vector_state_path.read_text())
    assert saved["missing"] == {key: value for key, value in state["missing"].items()
                                if key in {"other", "unidentified"}}
    assert deps.manifest.publish_state(snapshot.snapshot_id) is PublishState.published


def test_disabled_backlog_is_pruned_once_per_disabled_set(tmp_path, monkeypatch):
    deps, _ = _deps(tmp_path / "vectors-state.json")
    parse_pending(deps)
    state = _load_vector_state(deps)
    entry = dict(state["missing"]["jp-test:a.txt"])
    # An enabled source's entry stays queued, so any rescan would query it again.
    state["missing"]["other"] = {**entry, "snapshot_id": "other-snapshot"}
    deps.manifest.record_snapshot(make_snapshot(snapshot_id="other-snapshot", source_id="jp-other"))
    deps.vector_state_path.write_text(json.dumps(state))
    _set_embed(deps, False)
    calls = []
    chunks_for = deps.chunks.chunks_for
    monkeypatch.setattr(deps.chunks, "chunks_for", lambda key: calls.append(key) or chunks_for(key))
    _load_vector_state(deps)
    first = len(calls)
    assert first >= 1
    for _ in range(5):
        assert set(_load_vector_state(deps)["missing"]) == {"other"}
    assert len(calls) == first


def test_disabled_backlog_is_pruned_again_after_reenable(tmp_path):
    deps, _ = _deps(tmp_path / "vectors-state.json")
    parse_pending(deps)
    state = _load_vector_state(deps)
    _set_embed(deps, False)
    assert not _load_vector_state(deps)["missing"]
    _set_embed(deps, True)
    _load_vector_state(deps)
    # Work queued while re-enabled must be pruned when the source is disabled again.
    deps.vector_state_dirty = False
    deps.vector_state_path.write_text(json.dumps(state))
    assert _load_vector_state(deps)["missing"]
    _set_embed(deps, False)
    assert not _load_vector_state(deps)["missing"]


def test_disabled_backlog_prune_retries_after_failed_scan(tmp_path, monkeypatch):
    deps, _ = _deps(tmp_path / "vectors-state.json")
    parse_pending(deps)
    _set_embed(deps, False)
    chunks_for = deps.chunks.chunks_for

    def fail(key):
        raise OSError("boom")

    monkeypatch.setattr(deps.chunks, "chunks_for", fail)
    with pytest.raises(OSError):
        _load_vector_state(deps)
    monkeypatch.setattr(deps.chunks, "chunks_for", chunks_for)
    assert not _load_vector_state(deps)["missing"]


@pytest.mark.parametrize("value", [None, 0, 1, "false", "true", [], {}])
def test_source_embed_requires_boolean(value):
    deps, _ = _deps()
    _set_embed(deps, value)
    with pytest.raises(ValueError, match=r"jp-test.*scope.embed.*布尔值"):
        parse_pending(deps)


@pytest.mark.parametrize("explicit", [False, True])
def test_default_and_true_embed_normally(explicit):
    deps, snapshot = _deps()
    if explicit:
        _set_embed(deps, True)
    deps.vectors = FakeVectorIndex(FakeEmbedder())
    summary = parse_pending(deps)
    assert summary.parsed == [snapshot.snapshot_id] and not summary.vectors_missing
    assert deps.vectors.embedder.embedded == 2
    assert deps.vectors.search("本文を収録").items


@pytest.mark.parametrize("limit", [None, 0, 2])
def test_disabled_vectors_survive_parse_retry_and_reindex(tmp_path, limit):
    deps, _ = _deps(tmp_path / "vectors-state.json")
    deps.vectors = FakeVectorIndex(FakeEmbedder())
    parse_pending(deps)
    rows = dict(deps.vectors._rows)
    embedded = deps.vectors.embedder.embedded
    _set_embed(deps, False)
    deps.vector_limit = limit
    data = "更新後の本文".encode()
    deps.raw.put(data)
    deps.manifest.record_snapshot(make_snapshot(
        url="https://example.go.jp/a.txt", data=data, snapshot_id="snapshot-2", source_id="jp-test",
    ))
    parse_pending(deps)
    _start_vectors(deps)
    assert _retry_vectors(deps, None, ParseSummary()) == 0
    assert reindex(deps) == 1
    assert reindex(deps, vectors_only=True) == 0
    assert deps.vectors._rows == rows
    assert deps.vectors.embedder.embedded == embedded
    assert not _load_vector_state(deps)["missing"]


def test_reenabled_source_reindex_recreates_backlog():
    deps, _ = _deps()
    _set_embed(deps, False)
    deps.vectors = FakeVectorIndex(FakeEmbedder())
    parse_pending(deps)
    _set_embed(deps, True)
    deps.vector_limit = 0
    assert reindex(deps) == 2
    assert _load_vector_state(deps)["missing"]["jp-test:a.txt"]["chunks"] == 2


def test_staged_recovery_skips_disabled_vectors_but_repairs_keyword(tmp_path, monkeypatch):
    deps, snapshot = _deps(tmp_path / "vectors-state.json")
    add = deps.keyword.add

    def interrupted(*args):
        raise RuntimeError("synthetic keyword interruption")

    monkeypatch.setattr(deps.keyword, "add", interrupted)
    assert parse_pending(deps).failed
    assert deps.manifest.publish_state(snapshot.snapshot_id) is PublishState.staged
    _set_embed(deps, False)
    monkeypatch.setattr(deps.keyword, "add", add)
    summary = parse_pending(deps)
    assert not summary.failed and not summary.vectors_missing
    assert deps.manifest.publish_state(snapshot.snapshot_id) is PublishState.published
    assert deps.keyword.search("本文を収録").items
    assert not _load_vector_state(deps)["missing"]
