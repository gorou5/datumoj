"""WISTERIA-105: only exact types and complete code-owned templates are shared."""

import asyncio

import pytest
from mcp import Client

from datumoj.rikolto import mcp_server
from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.contracts import (
    AmbiguousRelease,
    GenerationUnavailable,
    InvalidGenerationToken,
    QueryError,
    QueryRejected,
    QueryTimeout,
    ReleaseDateUnavailable,
    ReleaseNotFound,
    TooManyRows,
)
from datumoj.rikolto.core.errors import safe_reason
from datumoj.rikolto.store.observations import DuckDBObservationStore
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeManifest,
    FakeObservationStore,
    FakeRawStore,
    FakeRegistry,
    FakeRetriever,
)

# One independent example for every new template, including each fixed message.
CASES = [
    (ValueError, "k 必须是 1–100 的整数"),
    (ValueError, "sources 必须是非空的字符串列表"),
    (ValueError, "sources 里不能有空白或非字符串"),
    (ValueError, "modified_since 必须是日期或 YYYY-MM-DD"),
    (ValueError, "since 必须是带时区的 ISO-8601 日期时间或 YYYY-MM-DD"),
    (ValueError, "since 必须带时区"),
    (ValueError, "source_id 必须是非空字符串"),
    (ValueError, "invalid JSON object"),
    (ValueError, "invalid JSON array"),
    (ValueError, "since 不能晚于 until"),
    (ValueError, "since/until datetime 必须带时区"),
    (ValueError, "kinds 只能包含 law, html_page, pdf, dataset, bulletin"),
    (ValueError, "kinds 只能包含 added, modified, removed, fetch_failed, parse_failed"),
    (ValueError, "query 必须是字符串（可以为空，表示只按条件列出）"),
    (ValueError, "query 不能为空白"),
    (ValueError, "sql 不能为空白"),
    (ValueError, "timeout_s 必须是有限正数"),
    (ValueError, "chunk_id 不能为空白"),
    (ValueError, "snapshot_sha256 必须是 64 位小写十六进制"),
    (ValueError, "locator 必须是字符串"),
    (ValueError, "max_rows 必须 ≥ 1，timeout_s 必须 > 0"),
    (ValueError, "timeout_s 必须 > 0"),
    (ValueError, "as_of_release 不能是空发布标识"),
    (ValueError, "as_of_release 日期对象须只有 date 字段"),
    (QueryTimeout, "query_sql 整个请求超过 10.5 秒"),
    (QueryTimeout, "查询超过 10 秒"),
    (QueryTimeout, "describe 超过 0.5 秒"),
    (QueryTimeout, "查询（含连接、查询和出处回查）超过 10 秒"),
    (TooManyRows, "结果超过 1000 行，请加条件或聚合"),
    (QueryRejected, "只允许单条 SELECT / WITH 查询"),
    (QueryRejected, "不允许多条语句"),
    (QueryRejected, "不允许使用 'read_csv'（读文件或执行 SQL 串）"),
    (QueryError, "发布感知读取需要持久化 SnapshotOrder"),
    (QueryError, "历史段引用无效"),
    (QueryError, "历史文件引用无效"),
    (QueryError, "发布历史段路径无效"),
    (QueryError, "发布历史段冲突"),
    (QueryError, "历史迁移核对不一致"),
    (QueryError, "历史迁移路径无效"),
    (InvalidGenerationToken, "generation_token 格式无效"),
    (InvalidGenerationToken, "generation_token 清单哈希不匹配"),
    (InvalidGenerationToken, "generation_token 必须显式提供"),
    (GenerationUnavailable, "截至查询需要已发布代次"),
    (GenerationUnavailable, "没有已发布代次"),
    (GenerationUnavailable, "已发布代次的保留记录不可用"),
    (GenerationUnavailable, "代次校验记录不匹配"),
    (GenerationUnavailable, "代次依赖已改变"),
    (GenerationUnavailable, "代次查询文件没有校验记录"),
    (GenerationUnavailable, "代次或其必需依赖不可用"),
    (GenerationUnavailable, "代次依赖路径无效"),
    (GenerationUnavailable, "代次依赖不能是符号链接"),
    (GenerationUnavailable, "保留代次编号发生冲突"),
    (GenerationUnavailable, "代次校验记录无效"),
    (GenerationUnavailable, "保留代次依赖已改变"),
    (GenerationUnavailable, "前代依赖已改变"),
    (GenerationUnavailable, "发布历史段路径无效"),
    (GenerationUnavailable, "DuckDB 重建描述不匹配"),
    (GenerationUnavailable, "DuckDB 重建副本非空"),
    (GenerationUnavailable, "代次存储副本校验失败"),
    (GenerationUnavailable, "代次包含未完成发布"),
    (GenerationUnavailable, "raw 哈希无效"),
    (GenerationUnavailable, "代次 raw 校验失败"),
    (ReleaseNotFound, "该代次没有指定发布标识"),
    (ReleaseNotFound, "该代次没有指定来源发布项"),
    (AmbiguousRelease, "发布标识跨来源或对应多个日期"),
    (AmbiguousRelease, "同一引用的不同身份具有相同发布排序键"),
    (ReleaseDateUnavailable, "显式来源发布缺少 released_at"),
]


@pytest.mark.parametrize(("kind", "message"), CASES)
def test_every_new_template_is_allowed(kind, message):
    assert safe_reason(kind(message)) == f"{kind.__name__}: {message}"


@pytest.mark.parametrize(("kind", "message"), CASES)
@pytest.mark.parametrize("payload", ["private-dir/file.csv", r"private-dir\file.csv", "SELECT secret FROM t", "\n"])
def test_templates_reject_prefixes_suffixes_and_embedded_data(kind, message, payload):
    for text in (payload + message, message + payload, message[:1] + payload + message[1:]):
        assert safe_reason(kind(text)) == kind.__name__


@pytest.mark.parametrize(("kind", "message"), CASES)
def test_subclasses_and_wrong_types_are_not_allowed(kind, message):
    derived = type("Derived", (kind,), {})
    assert safe_reason(derived(message)) == "Derived"
    assert safe_reason(RuntimeError(message)) == "RuntimeError"


@pytest.mark.parametrize(("kind", "message"), [
    (ValueError, "private/file 必须带时区"),
    (ValueError, "SELECT secret FROM t 必须带时区"),
    (ValueError, "k 必须是 1–SELECT secret FROM t 的整数"),
    (ValueError, "kinds 只能包含 select secret from t"),
    (ValueError, "kinds 只能包含 pdf, private/file"),
    (ValueError, "kinds 只能包含 pdf, 'secret'"),
    (QueryTimeout, "查询超过 private/file 秒"),
    (QueryTimeout, "查询超过 SELECT secret FROM t 秒"),
    (TooManyRows, "结果超过 1\n000 行，请加条件或聚合"),
    (QueryRejected, "不允许使用 'read_csv/private'（读文件或执行 SQL 串）"),
    (QueryRejected, "不允许使用 'read_csv\\private'（读文件或执行 SQL 串）"),
    (QueryRejected, "不允许使用 'SELECT secret FROM t'（读文件或执行 SQL 串）"),
    (QueryRejected, "不允许使用 'read_csv' OR 'x'（读文件或执行 SQL 串）"),
    (QueryRejected, "不允许使用 'read_\ncsv'（读文件或执行 SQL 串）"),
    (QueryError, "SQL 无法解析：SELECT secret FROM t"),
    (QueryRejected, "查询试图访问外部资源：private/file.csv"),
    (QueryError, "Binder Error: SELECT secret FROM t at private/file.csv"),
])
def test_placeholder_injections_and_duckdb_diagnostics_are_hidden(kind, message):
    assert safe_reason(kind(message)) == kind.__name__


def test_real_duckdb_passthrough_still_hides_message(tmp_path):
    store = DuckDBObservationStore(tmp_path / "observations")
    with pytest.raises(QueryError) as caught:
        store.query_readonly("SELECT missing_column_sentinel_105 FROM observations")
    assert type(caught.value) is QueryError
    assert "missing_column_sentinel_105" in str(caught.value)
    assert safe_reason(caught.value) == "QueryError"


def test_mcp_parameter_error_includes_actionable_message(tmp_path):
    chunks, manifest, registry = FakeChunkStore(), FakeManifest(), FakeRegistry([])
    tools = AnalysisTools(
        retriever=FakeRetriever(chunks, manifest, registry), catalog=FakeCatalogStore(),
        observations=FakeObservationStore(), chunks=chunks, manifest=manifest,
        raw=FakeRawStore(), registry=registry, data_dir=tmp_path,
    )

    async def call():
        async with Client(mcp_server.create_server(tools)) as client:
            bad = await client.call_tool("list_changes", {"since": "2026-10-30T12:00:00"})
            assert bad.is_error
            assert "ValueError: since 必须带时区" in bad.content[0].text
            assert not (await client.call_tool("version")).is_error

    asyncio.run(call())
