import asyncio
from types import SimpleNamespace

from mcp import Client
from starlette.testclient import TestClient
from typer.testing import CliRunner

from datumoj import cli, mcp_server
from datumoj.admin import create_app
from datumoj.analysis.tools import AnalysisTools
from datumoj.core.contracts import ParseRecord, ParseStatus
from datumoj.core.models import FetchOutcome, GapState, parse_context_fingerprint
from datumoj.core.schedule import Scheduler
from datumoj.index.coverage import (
    INDEX_REBUILDING,
    NOT_INDEXED_MAJORITY,
    gap_state,
    index_generation,
    pipeline_status,
    rebuilding_index,
)
from datumoj.index.hybrid import HybridRetriever
from datumoj.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
)
from datumoj.testing.samples import T0, make_chunk, make_document, make_snapshot, make_spec


def _env(tmp_path, *, source_ids=("jp-test",)):
    specs = [make_spec(source_id, kind="fake") for source_id in source_ids]
    registry, manifest = FakeRegistry(specs), FakeManifest()
    chunks, keyword = FakeChunkStore(), FakeKeywordIndex()
    raw = FakeRawStore(tmp_path)
    for index, source_id in enumerate(source_ids):
        raw_bytes = f"body {source_id} {index}".encode()
        snapshot = make_snapshot(source_id, f"https://example.go.jp/{source_id}/{index}", raw_bytes)
        manifest.record_snapshot(snapshot)
    retriever = HybridRetriever(chunks, manifest, registry, keyword)
    tools = AnalysisTools(
        retriever=retriever,
        catalog=FakeCatalogStore(),
        observations=FakeObservationStore(),
        chunks=chunks,
        manifest=manifest,
        raw=raw,
        registry=registry,
        parsers={("fake", "text/plain"): FakeParser},
        data_dir=tmp_path,
    )
    return SimpleNamespace(
        registry=registry, manifest=manifest, chunks=chunks, keyword=keyword,
        raw=raw, retriever=retriever, tools=tools, data_dir=tmp_path, specs=specs,
        vectors=None, pipeline_deps=None,
    )


def _parse(env, snapshot, *, status=ParseStatus.ok, doc=None, chunks=()):
    doc_keys = []
    if doc is not None:
        env.chunks.put_document(doc, chunks)
        env.keyword.add(chunks)
        doc_keys = [doc.doc_key]
    env.manifest.record_parse(ParseRecord(
        sha256=snapshot.sha256,
        parser=FakeParser.name,
        version=FakeParser.version,
        status=status,
        context_fingerprint=parse_context_fingerprint(snapshot, env.manifest.snapshot_meta(snapshot.snapshot_id)),
        error="synthetic parse failure" if status is ParseStatus.failed else None,
        doc_keys=doc_keys,
    ))


def _only_snapshot(env):
    return env.manifest.snapshots_for("jp-test")[0]


def test_unparsed_and_failed_snapshots_are_not_reported_as_zero_hits(tmp_path):
    unparsed = _env(tmp_path / "unparsed")
    result = unparsed.tools.search_docs("no matching phrase", sources="jp-test")
    assert result["gap_state"] == "not_indexed"
    assert result["pipeline"]["parseable_snapshots"] == 1
    assert result["pipeline"]["parsed"] == 0
    assert "没有可检索块" in result["gap_detail"]

    failed = _env(tmp_path / "failed")
    _parse(failed, _only_snapshot(failed), status=ParseStatus.failed)
    result = failed.tools.search_docs("no matching phrase", sources="jp-test")
    assert result["gap_state"] == "not_indexed"
    assert result["pipeline"]["parse_failed"] == 1
    assert "解析失败 1" in result["gap_detail"]


def test_needs_ocr_is_counted_from_parse_artifacts_and_blocks_zero_hits(tmp_path):
    env = _env(tmp_path)
    snapshot = _only_snapshot(env)
    doc = make_document(
        "jp-test:scanned", snapshot.sha256, metadata={"needs_ocr_pages": "2"},
    )
    _parse(env, snapshot, doc=doc)
    status = pipeline_status(
        ["jp-test"], registry=env.registry, manifest=env.manifest, chunks=env.chunks,
        parsers={("fake", "text/plain"): FakeParser},
    )
    state, detail = gap_state(
        ["jp-test"], [], registry=env.registry, manifest=env.manifest, chunks=env.chunks,
        parsers={("fake", "text/plain"): FakeParser},
    )
    assert status.parsed == 1 and status.needs_ocr == 1 and status.searchable_chunks == 0
    assert state is GapState.not_indexed and "needs_ocr 1" in detail


def test_rebuilding_lock_is_visible_as_pipeline_generation(tmp_path):
    env = _env(tmp_path)
    with rebuilding_index(env.data_dir):
        assert index_generation(env.retriever, data_dir=env.data_dir) == INDEX_REBUILDING
        result = env.tools.search_docs("no matching phrase", sources="jp-test")
        assert result["pipeline"]["index_generation"] == INDEX_REBUILDING
        assert result["gap_state"] == "not_indexed"
    assert index_generation(env.retriever, data_dir=env.data_dir) is None


def test_real_zero_hits_require_parsed_searchable_content_and_majority_is_strict(tmp_path):
    env = _env(tmp_path)
    snapshot = _only_snapshot(env)
    doc = make_document("jp-test:ready", snapshot.sha256)
    chunk = make_chunk(doc.doc_key, snapshot.sha256, "section=1", "indexed content")
    _parse(env, snapshot, doc=doc, chunks=[chunk])
    second = make_snapshot(
        "jp-test", "https://example.go.jp/extra/1", b"ready body", fetched_at=T0.replace(minute=1),
        snapshot_id="extra-ready",
    )
    env.manifest.record_snapshot(second)
    second_doc = make_document("jp-test:ready-2", second.sha256)
    second_chunk = make_chunk(second_doc.doc_key, second.sha256, "section=1", "another indexed content")
    _parse(env, second, doc=second_doc, chunks=[second_chunk])
    result = env.tools.search_docs("not present", sources="jp-test")
    assert result["gap_state"] == "zero_hits"
    assert result["pipeline"]["parsed"] == 2 and result["pipeline"]["searchable_chunks"] == 2

    for index in range(2, 5):
        body = f"extra {index}".encode()
        extra = make_snapshot(
            "jp-test", f"https://example.go.jp/extra/{index}", body,
            fetched_at=T0.replace(minute=index), snapshot_id=f"extra-{index}",
        )
        env.manifest.record_snapshot(extra)
        if index == 3:
            _parse(env, extra, status=ParseStatus.failed)
        if index == 2:
            state, _ = gap_state(
                ["jp-test"], [], registry=env.registry, manifest=env.manifest, chunks=env.chunks,
                parsers={("fake", "text/plain"): FakeParser},
            )
            assert state is GapState.zero_hits
    status = pipeline_status(
        ["jp-test"], registry=env.registry, manifest=env.manifest, chunks=env.chunks,
        parsers={("fake", "text/plain"): FakeParser},
    )
    assert NOT_INDEXED_MAJORITY == 0.5
    assert status.parseable_snapshots == 5 and status.parsed == 2 and status.parse_failed == 1
    # 3/5 non-ready snapshots is a strict majority; at 2/4 it remains a true zero-hit result.
    state, _ = gap_state(
        ["jp-test"], [], registry=env.registry, manifest=env.manifest, chunks=env.chunks,
        parsers={("fake", "text/plain"): FakeParser},
    )
    assert state is GapState.not_indexed


def test_all_sources_pipeline_counts_sum_each_source(tmp_path):
    env = _env(tmp_path, source_ids=("jp-one", "jp-two"))
    for source_id in ("jp-one", "jp-two"):
        snapshot = env.manifest.snapshots_for(source_id)[0]
        doc = make_document(f"{source_id}:doc", snapshot.sha256)
        chunk = make_chunk(doc.doc_key, snapshot.sha256, "section=1", "stored text")
        _parse(env, snapshot, doc=doc, chunks=[chunk])
    status = pipeline_status(
        None, registry=env.registry, manifest=env.manifest, chunks=env.chunks,
        parsers={("fake", "text/plain"): FakeParser},
    )
    assert status.parseable_snapshots == 2
    assert status.parsed == 2
    assert status.searchable_chunks == 2
    result = env.tools.coverage()
    assert result["pipeline"] == status.model_dump(mode="json")


def test_cli_and_admin_show_the_same_pipeline_counts_as_tools(tmp_path, monkeypatch):
    env = _env(tmp_path)
    snapshot = _only_snapshot(env)
    doc = make_document("jp-test:doc", snapshot.sha256)
    chunk = make_chunk(doc.doc_key, snapshot.sha256, "section=1", "stored text")
    _parse(env, snapshot, doc=doc, chunks=[chunk])
    coverage = env.tools.coverage()
    row = coverage["data"]["sources"][0]
    expected = row["pipeline"]

    scheduler = Scheduler(tmp_path, env.specs, lambda _: None, now=lambda: T0)
    app = create_app(tmp_path, env, scheduler, token="test")
    with TestClient(app) as client:
        page = client.get("/").text
    assert f"已解析 {expected['parsed']}/{expected['parseable_snapshots']}" in page
    assert f"可检索块 {expected['searchable_chunks']}" in page

    env.require = lambda _: None
    monkeypatch.setattr(cli.wiring, "build", lambda _root: env)
    cli_result = CliRunner().invoke(cli.app, ["--data-dir", str(tmp_path), "status"])
    assert cli_result.exit_code == 0, cli_result.output
    assert f"已解析 {expected['parsed']}/{expected['parseable_snapshots']}" in cli_result.output
    assert f"可检索块 {expected['searchable_chunks']}" in cli_result.output

    async def call_mcp():
        async with Client(mcp_server.create_server(env.tools)) as client:
            result = await client.call_tool("search_docs", {"queries": "absent phrase", "sources": "jp-test"})
            assert not result.is_error, result.content
            return result.structured_content.get("result", result.structured_content)

    mcp_result = asyncio.run(call_mcp())
    assert mcp_result["pipeline"]["parsed"] == expected["parsed"]
    assert mcp_result["pipeline"]["searchable_chunks"] == expected["searchable_chunks"]

    with rebuilding_index(env.data_dir):
        with TestClient(app) as client:
            rebuilding_page = client.get("/").text
        assert "索引 重建中" in rebuilding_page
        rebuilding_cli = CliRunner().invoke(cli.app, ["--data-dir", str(tmp_path), "status"])
        assert rebuilding_cli.exit_code == 0, rebuilding_cli.output
        assert "索引 重建中" in rebuilding_cli.output


def test_multisource_search_uses_aggregate_pipeline_on_empty_results(tmp_path):
    env = _env(tmp_path, source_ids=("jp-one", "jp-two"))
    for source_id in ("jp-one", "jp-two"):
        snapshot = env.manifest.snapshots_for(source_id)[0]
        doc = make_document(f"{source_id}:doc", snapshot.sha256)
        chunk = make_chunk(doc.doc_key, snapshot.sha256, "section=1", "stored text")
        _parse(env, snapshot, doc=doc, chunks=[chunk])
    result = env.tools.search_docs("absent phrase")
    assert result["gap_state"] == "zero_hits"
    assert result["pipeline"]["parseable_snapshots"] == 2
    assert result["pipeline"]["parsed"] == 2
    assert result["pipeline"]["searchable_chunks"] == 2
