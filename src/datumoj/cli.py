"""DATUMOJ 命令行入口。"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel

from datumoj.core.models import Citation
from datumoj import wiring
from datumoj.pipeline import vector_status

app = typer.Typer(no_args_is_help=True, help="抓取、解析、索引并查询本地政府公开数据。")


def _data_dir() -> str:
    return os.environ.get("DATUMOJ_DATA_DIR", "./data")


def _run(feature: str, action: Callable[[Any], Any]) -> None:
    try:
        deps = wiring.build(_data_dir())
        deps.require(feature)
    except wiring.FeatureNotConnected as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(f"运行失败：{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc
    try:
        action(deps)
    except typer.Exit:
        raise
    except wiring.FeatureNotConnected as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(f"参数错误：{exc}", err=True)
        raise typer.Exit(2) from exc
    except Exception as exc:
        typer.echo(f"运行失败：{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc


def _json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, default=str, indent=2)


def _content(value: Any) -> str:
    if isinstance(value, dict) and "content" in value:
        return str(value["content"])
    return "" if value is None else str(value)


def _human_search(deps: Any, result: dict[str, Any]) -> None:
    data = result.get("data", {})
    hits = data.get("hits", [])
    if not hits:
        typer.echo("零命中。")
        typer.echo(f"检索状态：{result.get('gap_state', 'unknown')}")
        if result.get("gap_detail"):
            typer.echo(f"说明：{result['gap_detail']}")
        typer.echo("当前来源：")
        for spec in deps.registry.all():
            typer.echo(f"- {spec.id}：{spec.name}")
        if not deps.registry.all():
            typer.echo("- （无来源）")
        return

    for i, hit in enumerate(hits, 1):
        citation = Citation.model_validate(hit["citation"])
        chunk = deps.chunks.get_chunk(hit["chunk_id"], citation.snapshot_sha256)
        title_path = chunk.title_path if chunk is not None else [hit.get("doc_key", "")]
        typer.echo(f"{i}. {' › '.join(title_path)} [{hit.get('locator', citation.locator)}]")
        typer.echo(f"   摘要：{_content(hit.get('snippet'))}")
        typer.echo(f"   出处：{citation.render()}")
    for warning in result.get("warnings", []):
        typer.echo(f"提示：{warning}")


def _echo_vector_status(deps: Any) -> None:
    if deps.vectors is None or deps.pipeline_deps is None:
        typer.echo("向量索引：关闭")
        return
    state = vector_status(deps.pipeline_deps)
    if state["state"] == "missing":
        typer.echo(f"向量索引：暂缺 {state['missing_blocks']} 个块；最近错误：{state['error']}")
    else:
        typer.echo(f"向量索引：可用（{state['blocks']} 个块）")


@app.command()
def crawl(
    source: str = typer.Argument(..., help="来源 ID"),
    limit: int | None = typer.Option(None, "--limit", min=1, help="本次抓取的条数上限"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只显示计划，不发请求"),
) -> None:
    """抓取一个已登记来源。"""
    def action(deps: Any) -> None:
        spec = deps.registry.get(source)
        if spec is None:
            raise ValueError(f"注册表里没有来源 {source!r}")
        crawler_type = deps.crawler_classes.get(spec.kind)
        if crawler_type is None:
            raise wiring.FeatureNotConnected("该功能尚未接入：没有匹配的采集器")
        fetcher = deps.fetcher_for(spec)
        try:
            crawl_deps = deps.crawl_deps_type(raw=deps.raw, manifest=deps.manifest, fetcher=fetcher)
            report = deps.run_crawl(spec, crawler_type(), crawl_deps, limit=limit, dry_run=dry_run)
        finally:
            close = getattr(fetcher, "close", None)
            if close is not None:
                close()
        typer.echo(f"来源：{report.source_id}")
        if dry_run:
            typer.echo(f"计划 {len(report.planned)} 项：")
            for task in report.planned:
                typer.echo(f"- {task.redacted_url}")
        else:
            typer.echo(f"新增 {report.new}，未变化 {report.unchanged}，失败 {sum(report.failures.values())}")
        if report.stop_reason:
            typer.echo(f"停止原因：{report.stop_reason}")
        if report.failures:
            typer.echo(f"失败分类：{_json(report.failures)}")
            raise typer.Exit(1)
    _run("crawl", action)


@app.command()
def parse(source: str | None = typer.Argument(None, help="可选来源 ID")) -> None:
    """解析还没有处理的快照。"""
    def action(deps: Any) -> None:
        summary = deps.parse_pending(deps.pipeline_deps, source)
        typer.echo(f"解析完成：成功 {len(summary.parsed)}，跳过 {summary.skipped}，无解析器 {len(summary.no_parser)}")
        if summary.vectors_missing:
            typer.echo(f"向量暂缺：{_json(summary.vectors_missing)}", err=True)
        if summary.failed:
            typer.echo(f"解析失败：{_json(summary.failed)}", err=True)
            raise typer.Exit(1)
    _run("parse", action)


@app.command()
def index(
    rebuild: bool = typer.Option(False, "--rebuild", help="从当前文档块重建索引"),
    vectors_only: bool = typer.Option(False, "--vectors-only", help="只重试暂缺文档的向量嵌入"),
) -> None:
    """解析新快照并更新索引；可选择全量重建或只补做向量。"""
    def action(deps: Any) -> None:
        if vectors_only:
            count = deps.reindex(deps.pipeline_deps, vectors_only=True)
            typer.echo(f"向量补做完成：更新 {count} 个块")
            _echo_vector_status(deps)
            return
        summary = deps.parse_pending(deps.pipeline_deps)
        if rebuild:
            count = deps.reindex(deps.pipeline_deps)
            typer.echo(f"索引重建完成：{count} 个块")
        else:
            typer.echo(f"索引更新完成：处理 {len(summary.parsed)} 个新快照")
        if summary.vectors_missing:
            typer.echo(f"向量暂缺：{_json(summary.vectors_missing)}", err=True)
        if summary.failed:
            typer.echo(f"解析失败：{_json(summary.failed)}", err=True)
            raise typer.Exit(1)
    _run("index", action)


@app.command()
def search(
    queries: list[str] = typer.Argument(..., help="一个或多个检索词"),
    k: int = typer.Option(10, "-k", min=1, max=50, help="最多返回条数"),
    source: str | None = typer.Option(None, "--source", help="限制来源 ID"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """按关键词检索文档并显示出处。"""
    def action(deps: Any) -> None:
        result = deps.tools.search_docs(queries, k=k, sources=source)
        if json_output:
            typer.echo(_json(result))
            return
        _human_search(deps, result)
    _run("search", action)


@app.command()
def sql(
    query: str = typer.Argument(..., help="单条只读 SELECT/WITH 查询"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
) -> None:
    """查询只读 observations 视图。"""
    def action(deps: Any) -> None:
        result = deps.tools.query_sql(query)
        if json_output:
            typer.echo(_json(result))
            return
        data = result.get("data", {})
        columns = data.get("columns", [])
        typer.echo("\t".join(columns))
        for row in data.get("rows", []):
            typer.echo("\t".join(" ".join(_content(v).split()) if v is not None else "NULL" for v in row))
        for item in data.get("provenance", []):
            citation = item.get("citation")
            if citation:
                typer.echo(f"出处：{Citation.model_validate(citation).render()}")
        if not data.get("rows"):
            typer.echo("查询没有返回任何行。")
    _run("sql", action)


@app.command()
def status() -> None:
    """查看来源入库状态。"""
    def action(deps: Any) -> None:
        result = deps.tools.coverage()
        sources = result.get("data", {}).get("sources", [])
        if not sources:
            typer.echo("当前没有登记来源。")
        else:
            for item in sources:
                fresh = item.get("freshness", {})
                as_of = fresh.get("as_of") or "无成功抓取"
                typer.echo(f"{item['source_id']}：{item['status']}；快照 {item['snapshots']}；截止 {as_of}")
        for warning in result.get("warnings", []):
            typer.echo(f"提示：{warning}")
        _echo_vector_status(deps)
    _run("status", action)


@app.command()
def mcp() -> None:
    """通过 stdio 启动只读 MCP 服务器。"""
    from datumoj import mcp_server

    os.environ.setdefault(mcp_server.FACTORY_ENV, "datumoj.wiring:build_tools")
    mcp_server.main()


@app.command("eval")
def evaluate_corpus(
    gate: bool = typer.Option(False, "--gate", help="未达到全部评测门槛时退出码为 1"),
    report: Path | None = typer.Option(None, "--report", help="报告目录（默认使用临时目录）"),
) -> None:
    """用完整题库评测本地语料，并输出 JSON 和 Markdown 报告。"""
    from datumoj.eval import check_gate, evaluate, load_questions, write_reports

    def action(deps: Any) -> None:
        def cite(citation: Citation) -> list:
            restored = deps.tools.cite(citation.snapshot_sha256, citation.locator)
            data = restored.get("data", {})
            return data.get("passages", []) if data.get("status") == "ok" else []

        questions = load_questions(Path(__file__).resolve().parents[2] / "eval/questions")
        result = evaluate(
            questions, retriever=deps.retriever, observation_store=deps.observations,
            cite=cite, manifest=deps.manifest, catalog=deps.catalog,
        )
        directory = report if report is not None else Path(tempfile.mkdtemp(prefix="datumoj-eval-"))
        write_reports(result, directory)
        checked = check_gate(result)
        typer.echo(f"评测 {len(questions)} 题；报告：{directory.resolve()}")
        typer.echo("门槛：通过" if checked.passed else "门槛：未通过")
        for failure in checked.failures:
            typer.echo(failure)
        if gate and not checked.passed:
            raise typer.Exit(1)

    _run("search", action)
