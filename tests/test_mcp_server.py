"""MCP 服务器测试：SDK 的内存会话 + 假实现，每个工具调用一次，并验证错误处理。全部离线。"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from mcp import Client

from datumoj.rikolto import mcp_server
from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.models import ChangeEvent, ChangeKind, Observation, TimeGranularity
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChangeLog,
    FakeChunkStore,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
    FakeRetriever,
)
from datumoj.rikolto.testing.samples import T0, make_dataset, make_resource, make_snapshot, make_spec

TEXT = "お知らせ\n\n人口統計の公開について。"
TOOLS = {"search_docs", "list_changes", "search_catalog", "query_sql", "get_chunk", "cite", "version", "coverage"}


@pytest.fixture
def tools(tmp_path) -> AnalysisTools:
    raw, manifest, chunks = FakeRawStore(tmp_path), FakeManifest(), FakeChunkStore()
    catalog, observations = FakeCatalogStore(), FakeObservationStore()
    registry = FakeRegistry([make_spec("jp-open", kind="fake")])
    data = TEXT.encode()
    snap = make_snapshot("jp-open", "https://example.go.jp/notice.txt", data, fetched_at=T0)
    raw.put(data)
    manifest.record_snapshot(snap, {})
    parsed = FakeParser().parse(snap, data, {})
    chunks.put_document(parsed.documents[0], parsed.chunks)
    observations.write_table("jp-open", "t1", [Observation(
        obs_id="jp-open:1", source_id="jp-open", dataset_key="jp-open:pop", table_key="t1", survey="国勢調査",
        indicator="人口", area_code="13000", area_label="東京都", time_start=date(2020, 1, 1),
        time_end=date(2020, 12, 31), time_label="2020年", granularity=TimeGranularity.year, unit="人",
        value=Decimal("14047594"), snapshot_sha256=snap.sha256, locator="row=1", fetched_at=snap.fetched_at,
    )])
    catalog.upsert(
        datasets=[make_dataset("jp-open:pop", "人口統計 2020")],
        resources=[make_resource("jp-open:pop", "r1", "https://example.go.jp/notice.txt", format="TXT")],
    )
    change_log = FakeChangeLog()
    change_log.append([ChangeEvent.make(
        source_id="jp-open", kind=ChangeKind.added, doc_key=parsed.documents[0].doc_key,
        resource_url=snap.resource_url, new_sha256=snap.sha256, detected_at=T0,
        summary="文書が追加されました",
    )])
    return AnalysisTools(
        retriever=FakeRetriever(chunks, manifest, registry), catalog=catalog, observations=observations,
        chunks=chunks, manifest=manifest, raw=raw, registry=registry, parsers={("fake", "text/plain"): FakeParser},
        change_log=change_log, data_dir=tmp_path, index_built_at=lambda: T0,
    )


def _run(tools: AnalysisTools, fn):
    async def go():
        async with Client(mcp_server.create_server(tools)) as client:
            return await fn(client)

    return asyncio.run(go())


def _call(tools: AnalysisTools, name: str, args: dict[str, Any] | None = None):
    return _run(tools, lambda c: c.call_tool(name, args or {}))


def _data(result) -> dict[str, Any]:
    assert not result.is_error, result.content
    return result.structured_content.get("result", result.structured_content)


def test_lists_all_tools_with_descriptions_and_prompt(tools):
    async def go(c):
        return await c.list_tools(), await c.list_prompts()

    listed, prompts = _run(tools, go)
    assert {t.name for t in listed.tools} == TOOLS
    assert all(t.description and len(t.description) > 40 for t in listed.tools)
    sql = next(t for t in listed.tools if t.name == "query_sql")
    assert "only source of numbers" in sql.description
    search = next(t for t in listed.tools if t.name == "search_docs")
    assert "kinds" in search.description and "pdf" in search.description
    assert [p.name for p in prompts.prompts] == ["datumoj_answer_rules"]


def test_search_docs(tools):
    data = _data(_call(tools, "search_docs", {"queries": "人口統計"}))
    assert data["gap_state"] == "ok"
    assert data["data"]["hits"][0]["locator"] == "section=2"
    filtered = _data(_call(tools, "search_docs", {"queries": "人口統計", "kinds": ["bulletin"]}))
    assert filtered["data"]["hits"][0]["doc_key"] == "jp-open:notice.txt"


def test_list_changes(tools):
    data = _data(_call(tools, "list_changes"))
    assert data["gap_state"] == "ok"
    event = data["data"][0]
    assert event["kind"] == "added"
    assert event["citation"]["snapshot_sha256"]
    assert event["summary"]["content_role"] == "untrusted_source_text"


def test_search_catalog(tools):
    data = _data(_call(tools, "search_catalog", {"query": "人口"}))
    assert data["data"]["datasets"][0]["dataset_key"] == "jp-open:pop"


def test_query_sql(tools):
    data = _data(_call(tools, "query_sql", {"sql": "SELECT obs_id, value FROM observations"}))
    assert data["gap_state"] == "ok"
    assert data["data"]["provenance"][0]["obs_id"] == "jp-open:1"


def test_get_chunk(tools):
    data = _data(_call(tools, "get_chunk", {"chunk_id": "jp-open:notice.txt#section=2", "context": 0}))
    assert data["data"]["status"] == "ok"
    assert len(data["data"]["chunks"]) == 1


def test_cite(tools):
    sha = make_snapshot("jp-open", "https://example.go.jp/notice.txt", TEXT.encode()).sha256
    data = _data(_call(tools, "cite", {"snapshot_sha256": sha, "locator": "section=1"}))
    assert data["data"]["status"] == "ok"


def test_cite_identity_parameters_and_ambiguity(tools):
    original = make_snapshot("jp-open", "https://example.go.jp/notice.txt", TEXT.encode())
    other = make_snapshot("jp-open", "https://example.go.jp/other.txt", TEXT.encode())
    tools.manifest.record_snapshot(other)
    ambiguous = _data(_call(tools, "cite", {"snapshot_sha256": original.sha256, "locator": "section=1"}))
    assert ambiguous["data"]["status"] == "ambiguous"
    assert {candidate["snapshot_id"] for candidate in ambiguous["data"]["candidates"]} == {
        original.snapshot_id, other.snapshot_id,
    }
    restored = _data(_call(tools, "cite", {
        "snapshot_sha256": original.sha256, "locator": "section=1",
        "snapshot_id": original.snapshot_id, "resource_url": original.resource_url,
    }))
    assert restored["data"]["passages"][0]["citation"]["snapshot_id"] == original.snapshot_id
    listed = _run(tools, lambda client: client.list_tools())
    cite = next(tool for tool in listed.tools if tool.name == "cite")
    assert {"snapshot_id", "resource_url"} <= cite.input_schema["properties"].keys()
    assert "ambiguous" in cite.description


def test_version(tools):
    data = _data(_call(tools, "version"))
    assert data["data"]["package"] == "datumoj"
    assert "+" not in data["data"]["version"]


def test_version_includes_pulled_generation_and_vector_coverage(tools):
    (tools.data_dir / "state-pull.json").write_text(
        '{"generation":12,"created_at":"2026-10-23T01:02:03+00:00",'
        '"vector_coverage":{"model":"test","version":"1","dim":3,'
        '"embedded_chunks":7,"pending_chunks":2},"stores":[]}',
        encoding="utf-8",
    )
    data = _data(_call(tools, "version"))["data"]
    assert data["data_generation"] == 12
    assert data["data_generated_at"] == "2026-10-23T01:02:03+00:00"
    assert data["data_origin"] == "R2 有效代次副本"
    assert data["vector_coverage_note"] == "向量覆盖 7 / 9"


def test_version_identifies_local_data_without_pull_record(tools):
    data = _data(_call(tools, "version"))["data"]
    assert data["data_generation"] is None
    assert data["data_generated_at"] is None
    assert data["data_origin"] == "本机自建数据"


def test_factory_disables_vector_initialization_for_pulled_copy(tools, monkeypatch):
    import os

    (tools.data_dir / "state-pull.json").write_text(
        '{"generation":1,"created_at":"2026-10-23T01:02:03+00:00",'
        '"vector_coverage":null,"stores":[]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(tools.data_dir))
    monkeypatch.setenv("DATUMOJ_VECTORS", "on")
    observed = []

    def factory():
        observed.append(os.environ.get("DATUMOJ_VECTORS"))
        return tools

    data = _data(_run(factory, lambda client: client.call_tool("version")))
    assert observed == ["off"]
    assert os.environ["DATUMOJ_VECTORS"] == "on"
    assert data["data"]["data_origin"] == "R2 有效代次副本"


def test_factory_reloads_tools_after_pull_generation_changes(tools, monkeypatch):
    import json

    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(tools.data_dir))
    calls = []

    def factory():
        calls.append(True)
        return tools

    async def go():
        async with Client(mcp_server.create_server(factory)) as client:
            local = _data(await client.call_tool("version"))
            (tools.data_dir / "state-pull.json").write_text(json.dumps({
                "generation": 2,
                "created_at": "2026-10-23T01:02:03+00:00",
                "vector_coverage": None,
                "stores": [],
            }), encoding="utf-8")
            pulled = _data(await client.call_tool("version"))
            return local, pulled

    local, pulled = asyncio.run(go())
    assert len(calls) == 2
    assert local["data"]["data_origin"] == "本机自建数据"
    assert pulled["data"]["data_generation"] == 2


def test_coverage(tools):
    data = _data(_call(tools, "coverage"))
    assert data["data"]["sources"][0]["source_id"] == "jp-open"


@pytest.mark.parametrize(
    ("name", "args", "needle"),
    [
        ("query_sql", {"sql": "DELETE FROM observations"}, "QueryRejected"),
        ("query_sql", {"sql": "   "}, "ValueError"),
        ("search_docs", {"queries": "x", "k": 0}, "ValueError"),
        ("cite", {"snapshot_sha256": "xyz", "locator": "row=1"}, "ValueError"),
        ("get_chunk", {"chunk_id": 5}, ""),  # 参数类型错误也是错误结果
    ],
)
def test_errors_become_error_results_and_server_survives(tools, name, args, needle):
    async def go(c):
        bad = await c.call_tool(name, args)
        good = await c.call_tool("version", {})
        return bad, good

    bad, good = _run(tools, go)
    assert bad.is_error
    assert needle in bad.content[0].text
    assert not good.is_error


def test_prompt_reads_skill_file_or_falls_back(tools, tmp_path, monkeypatch):
    async def get(c):
        return (await c.get_prompt("datumoj_answer_rules")).messages[0].content.text

    skill = tmp_path / "SKILL.md"
    skill.write_text("# 自定义规则\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "SKILL_PATH", skill)
    assert _run(tools, get) == "# 自定义规则\n"
    monkeypatch.setattr(mcp_server, "SKILL_PATH", tmp_path / "missing.md")
    text = _run(tools, get)
    assert text == mcp_server.FALLBACK_RULES
    assert "query_sql" in text


def test_main_without_factory_exits_with_message(monkeypatch):
    monkeypatch.delenv(mcp_server.FACTORY_ENV, raising=False)
    with pytest.raises(SystemExit) as exc:
        mcp_server.main()
    assert mcp_server.FACTORY_ENV in str(exc.value)
