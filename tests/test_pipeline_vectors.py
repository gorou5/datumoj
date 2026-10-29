from __future__ import annotations

from typer.testing import CliRunner

from datumoj.rikolto import cli, wiring
from datumoj.rikolto.core.contracts import EmbeddingError, ParseStatus
from datumoj.rikolto.core.models import parse_context_fingerprint
from datumoj.rikolto.pipeline import PipelineDeps, parse_pending, reindex, vector_status
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
