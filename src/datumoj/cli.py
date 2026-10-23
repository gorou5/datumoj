"""DATUMOJ 命令行入口。"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel

from datumoj import wiring
from datumoj.analysis.reports import previous_week_period, weekly_report
from datumoj.core import schedule as scheduling
from datumoj.core.lease import LeaseManager, remote_configured
from datumoj.core.monitor import attach_cloud_status
from datumoj.core.models import Citation
from datumoj.core.pull import data_state
from datumoj.core.pull import pull as pull_state
from datumoj.core.registry_export import export_registry
from datumoj.core.storage import load_storage_config
from datumoj.index.coverage import INDEX_REBUILDING, rebuilding_index
from datumoj.pipeline import vector_status

app = typer.Typer(no_args_is_help=True, help="抓取、解析、索引并查询本地政府公开数据。")
schedule_app = typer.Typer(no_args_is_help=True, help="本机抓取开关和调度。")
launchd_app = typer.Typer(no_args_is_help=True, help="生成、安装或卸载 launchd 任务。")
registry_app = typer.Typer(no_args_is_help=True, help="数据源注册表导出。")
report_app = typer.Typer(no_args_is_help=True, help="生成资料库变化报告。")
diagnose_app = typer.Typer(no_args_is_help=True, help="只读检查本地证据，不升级数据库。")
state_app = typer.Typer(no_args_is_help=True, help="推送和校验恢复状态代次。")
lease_app = typer.Typer(no_args_is_help=True, help="写入租约；后续命令从 DATUMOJ_LEASE_TOKEN 读取令牌。")
cloud_app = typer.Typer(no_args_is_help=True, help="持租约运行到期来源并发布云端代次。")
index_app = typer.Typer(invoke_without_command=True, help="更新、重建或维护索引。")
state_app.add_typer(lease_app, name="lease")
app.add_typer(schedule_app, name="schedule")
schedule_app.add_typer(launchd_app, name="launchd")
app.add_typer(registry_app, name="registry")
app.add_typer(report_app, name="report")
app.add_typer(diagnose_app, name="diagnose")
app.add_typer(state_app, name="state")
app.add_typer(cloud_app, name="cloud")
app.add_typer(index_app, name="index")
_DATA_DIR: ContextVar[str | None] = ContextVar("datumoj_data_dir", default=None)


@app.callback()
def main(
    ctx: typer.Context,
    data_dir: Annotated[Path | None, typer.Option("--data-dir", help="数据目录（优先于 DATUMOJ_DATA_DIR）")] = None,
) -> None:
    token = _DATA_DIR.set(str(data_dir) if data_dir is not None else None)
    ctx.call_on_close(lambda: _DATA_DIR.reset(token))


def _data_dir() -> str:
    return str(load_storage_config(data_dir=_DATA_DIR.get()).data_dir)


def _build_wiring(root: str | Path) -> Any:
    if not scheduling.is_pulled_replica(root):
        return wiring.build(root)
    previous_vectors = os.environ.get("DATUMOJ_VECTORS")
    os.environ["DATUMOJ_VECTORS"] = "off"
    try:
        return wiring.build(root)
    finally:
        if previous_vectors is None:
            os.environ.pop("DATUMOJ_VECTORS", None)
        else:
            os.environ["DATUMOJ_VECTORS"] = previous_vectors


@schedule_app.callback()
def schedule_options(
    ctx: typer.Context,
    data_dir: Annotated[Path | None, typer.Option("--data-dir", help="本次调度使用的数据目录")] = None,
) -> None:
    if data_dir is not None:
        token = _DATA_DIR.set(str(data_dir))
        ctx.call_on_close(lambda: _DATA_DIR.reset(token))


def _run(feature: str, action: Callable[[Any], Any], *, allow_pulled: bool = False) -> None:
    root = _data_dir()
    try:
        with scheduling.workspace_lock(root):
            try:
                if scheduling.is_pulled_replica(root) and feature not in {"search", "sql", "status"} and not allow_pulled:
                    scheduling.ensure_writable(root)
                deps = _build_wiring(root)
                deps.require(feature)
            except scheduling.ReadOnlyReplicaError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(1) from exc
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
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"运行失败：{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc


def _json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, default=str, indent=2)


def _state_call(action: Callable[[], Any]) -> None:
    try:
        typer.echo(_json(action()))
    except Exception:
        # Even configuration/SDK/validation exceptions can contain secrets.
        typer.echo(_json({"error": "状态操作失败，请检查配置、代次完整性和写入状态"}))
        raise typer.Exit(1) from None


@cloud_app.command("run")
def cloud_run(
    work_dir: Path | None = typer.Option(None, "--work-dir", help="空的工作目录；默认使用临时目录"),
    max_embed: int = typer.Option(1000, "--max-embed", min=0, help="整轮嵌入块数上限"),
    max_minutes: float = typer.Option(40, "--max-minutes", min=0, help="到期后停止开始新来源"),
) -> None:
    from datumoj.cloud import run

    result = run(work_dir=work_dir, max_embed=max_embed, max_minutes=max_minutes)
    typer.echo(_json(result))
    raise typer.Exit(result["exit_status"])


@state_app.command("push")
def state_push(data_dir: Path | None = typer.Option(None, "--data-dir")) -> None:
    root = data_dir if data_dir is not None else _data_dir()

    def action():
        manager = wiring.build_state(root)
        if not remote_configured():
            return manager.push()
        leases = LeaseManager(manager.store)
        lease = leases.credentials()
        previous_publish, previous_write = manager.before_publish, manager.before_write
        with leases.maintain(lease) as check:
            def before_publish():
                if previous_publish:
                    previous_publish()
                check()

            def before_write():
                if previous_write:
                    previous_write()
                check()

            manager.before_publish, manager.before_write = before_publish, before_write
            try:
                return manager.push()
            finally:
                manager.before_publish, manager.before_write = previous_publish, previous_write
    try:
        with scheduling.workspace_lock(root):
            scheduling.ensure_writable(root)
            _state_call(action)
    except scheduling.ReadOnlyReplicaError as exc:
        typer.echo(_json({"error": str(exc)}))
        raise typer.Exit(1) from exc


@app.command("pull")
def pull_command(
    data_dir: Path | None = typer.Option(None, "--data-dir", help="本机数据目录"),
    generation: int | None = typer.Option(None, "--generation", min=1, help="指定有效历史代次"),
) -> None:
    """从远端只读拉取并校验最新有效状态代次。"""
    def action() -> dict[str, Any]:
        root = Path(data_dir if data_dir is not None else _data_dir())
        with scheduling.workspace_lock(root):
            result = pull_state(root, generation)
            marker = root / "state-pull.json"
            try:
                record = json.loads(marker.read_text(encoding="utf-8"))
                if record.get("generation") == result["generation"]:
                    manager = wiring.build_state(root)
                    try:
                        selected = next(
                            row for row in manager.list()
                            if row.generation == result["generation"]
                        )
                        record["writer"] = selected.writer.model_dump(mode="json")
                        record["last_successful_runs"] = {
                            source_id: run.model_dump(mode="json")
                            for source_id, run in selected.last_successful_runs.items()
                        }
                        fd, temporary = tempfile.mkstemp(prefix=".state-pull-", dir=root)
                        try:
                            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                                json.dump(record, stream, ensure_ascii=False)
                                stream.flush()
                                os.fsync(stream.fileno())
                            os.replace(temporary, marker)
                        finally:
                            if os.path.exists(temporary):
                                os.unlink(temporary)
                    finally:
                        client = getattr(manager.store, "_client", None)
                        close = getattr(client, "close", None)
                        if callable(close):
                            close()
            except Exception:
                # The installed replica remains usable; status falls back to its run ledger.
                pass
        return result

    _state_call(action)


def _leases() -> LeaseManager:
    return LeaseManager(wiring.build_state(_data_dir()).store)


@lease_app.command("acquire")
def lease_acquire(
    force: bool = typer.Option(False, "--force"),
    holder: str | None = typer.Option(None, "--holder"),
) -> None:
    """Only this response contains the new token; keep stdout private."""
    def action():
        run_id = os.environ.get("GITHUB_RUN_ID")
        owner = holder or (f"actions/{run_id}" if run_id else f"local/{uuid.uuid4().hex}")
        return _leases().acquire(owner, force=force)
    _state_call(action)


@lease_app.command("renew")
def lease_renew() -> None:
    def action():
        manager = _leases()
        lease = manager.credentials()
        renewed = manager.renew(lease.holder, lease.token)
        return {"renewed": True, "expires_at": renewed.expires_at.isoformat()}
    _state_call(action)


@lease_app.command("release")
def lease_release() -> None:
    def action():
        manager = _leases()
        lease = manager.credentials(active=False)
        manager.release(lease.holder, lease.token)
        return {"released": True}
    _state_call(action)


@lease_app.command("status")
def lease_status() -> None:
    _state_call(lambda: _leases().status())


@state_app.command("restore")
def state_restore(
    to: Path = typer.Option(..., "--to"),
    generation: int | None = typer.Option(None, "--generation", min=1),
    raw: str = typer.Option("local", "--raw", help="local 或 remote"),
) -> None:
    _state_call(lambda: wiring.build_state(_data_dir()).restore(to, generation, raw=raw))


@state_app.command("list")
def state_list() -> None:
    def action():
        return [{"generation": g.generation, "created_at": g.created_at.isoformat(),
                 "writer": g.writer.model_dump(), "copies": len(g.stores),
                 "size_bytes": sum(c.size_bytes for c in g.stores)}
                for g in wiring.build_state(_data_dir()).list()]
    _state_call(action)


@diagnose_app.command("evidence")
def diagnose_evidence(
    data_dir: Path | None = typer.Option(None, "--data-dir", help="待检查的本地数据目录"),
    verify_raw: bool = typer.Option(False, "--verify-raw", help="读取全部 raw 校验哈希（只读）"),
) -> None:
    """输出 JSON 风险清单；请先停止目录的写入者。"""
    from datumoj.diagnose import evidence

    try:
        # Deliberately bypass _run/wiring.build: their constructors migrate stores.
        typer.echo(_json(evidence(data_dir if data_dir is not None else _data_dir(), verify_raw=verify_raw)))
    except Exception as exc:
        typer.echo(f"诊断失败：{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc


@diagnose_app.command("storage")
def diagnose_storage(
    data_dir: Annotated[Path | None, typer.Option("--data-dir", help="待检查的本地数据目录")] = None,
) -> None:
    """只读统计体积与向量版本；先停止写入，压缩节省量仅为估算。"""
    from datumoj.diagnose import storage

    try:
        typer.echo(_json(storage(data_dir if data_dir is not None else _data_dir())))
    except Exception as exc:  # noqa: BLE001 -- sanitize storage/configuration errors at the CLI boundary
        # Paths, storage configuration and underlying exception text are private.
        typer.echo(_json({"error": type(exc).__name__}), err=True)
        raise typer.Exit(1) from None


@app.command("replay")
def replay_evidence(
    source: Path = typer.Option(..., "--from", help="旧数据目录（只读；请先停止写入）"),
    target: Path = typer.Option(..., "--to", help="新的空目录；不会自动切换数据目录"),
    vectors: bool = typer.Option(False, "--vectors", help="同时建立向量索引（需要本机嵌入服务）"),
) -> None:
    """在新目录重放 manifest/raw，输出对比；partial 结果需人工检查。"""
    from datumoj.diagnose import replay

    try:
        result = replay(source, target, vectors=vectors)
        typer.echo(_json(result))
    except ValueError as exc:
        typer.echo(f"参数错误：{exc}", err=True)
        raise typer.Exit(2) from exc
    except Exception as exc:
        typer.echo(f"重放失败：{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc
    if result["status"] != "complete":
        raise typer.Exit(1)


def _content(value: Any) -> str:
    if isinstance(value, dict) and "content" in value:
        return str(value["content"])
    return "" if value is None else str(value)


def _human_search(deps: Any, result: dict[str, Any]) -> None:
    data = result.get("data", {})
    hits = data.get("hits", [])
    pipeline = result.get("pipeline")
    if pipeline:
        typer.echo(
            "管线："
            f"可解析快照 {pipeline.get('parseable_snapshots', 0)}；已解析 {pipeline.get('parsed', 0)}；"
            f"解析失败 {pipeline.get('parse_failed', 0)}；needs_ocr {pipeline.get('needs_ocr', 0)}；"
            f"可检索块 {pipeline.get('searchable_chunks', 0)}；"
            f"索引代次 {pipeline.get('index_generation') or '未知'}"
        )
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


@registry_app.command("export")
def registry_export(
    out: Path = typer.Option(Path("catalog"), "--out", help="导出目录"),
) -> None:
    """将 sources/*.yaml 导出为注册层 catalog/*.json。"""
    sources = Path(__file__).resolve().parents[2] / "sources"
    try:
        paths = export_registry(sources, out)
    except Exception as exc:
        typer.echo(f"导出失败：{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"已导出 {len(paths)} 个来源描述到 {out}")


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
        report = _crawl_report(deps, spec, limit=limit, dry_run=dry_run)
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


def _crawl_report(deps: Any, spec: Any, *, limit: int | None = None, dry_run: bool = False) -> Any:
    deps.require("crawl")
    crawler_type = deps.crawler_classes.get(spec.kind)
    if crawler_type is None:
        raise wiring.FeatureNotConnected("该功能尚未接入：没有匹配的采集器")
    fetcher = deps.fetcher_for(spec)
    try:
        crawl_deps = deps.crawl_deps_type(raw=deps.raw, manifest=deps.manifest, fetcher=fetcher)
        return deps.run_crawl(spec, crawler_type(), crawl_deps, limit=limit, dry_run=dry_run)
    finally:
        close = getattr(fetcher, "close", None)
        if close is not None:
            close()


def _scheduler(*, require_writable: bool = False) -> scheduling.Scheduler:
    return _scheduler_at(
        _data_dir(), read_only=remote_configured(), require_writable=require_writable,
    )[1]


def _scheduler_at(
    root: str | Path, *, read_only: bool = False, require_writable: bool = False,
) -> tuple[Any, scheduling.Scheduler]:
    with scheduling.workspace_lock(root):
        if require_writable:
            scheduling.ensure_writable(root)
        if read_only:
            from datumoj.core.registry import load_registry

            registry = load_registry(Path(__file__).resolve().parents[2] / "sources")
            # Opening stores can migrate schemas. Defer that until _run holds the
            # remote lease; due selection only needs registry/control/run history.
            scheduler = scheduling.Scheduler(
                root, registry.all(), lambda spec: _crawl_report(_build_wiring(root), spec),
            )
            return None, scheduler
        deps = _build_wiring(root)
        if deps.registry is None:
            raise wiring.FeatureNotConnected("该功能尚未接入：没有来源注册表")
        scheduler = scheduling.Scheduler(
            root, deps.registry.all(), lambda spec: _crawl_report(deps, spec), manifest=deps.manifest,
        )
        return deps, scheduler


def _monitored_status(scheduler: Any) -> dict[str, Any]:
    status = scheduler.status()
    reader = getattr(scheduler, "_read_history", None)
    history = reader()[0] if callable(reader) else []
    now = scheduler.now() if callable(getattr(scheduler, "now", None)) else datetime.now(UTC)
    return attach_cloud_status(
        status, scheduler.data_dir, scheduler.sources.values(), history, now,
    )


def _pulled_created_at(data_dir: str | Path) -> datetime | None:
    marker = Path(data_dir) / "state-pull.json"
    try:
        if marker.is_symlink():
            return None
        value = json.loads(marker.read_text(encoding="utf-8"))
        created_at = datetime.fromisoformat(value["created_at"])
        return created_at if created_at.utcoffset() is not None else None
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        return None


@report_app.command("weekly")
def report_weekly(
    since: str | None = typer.Option(None, "--since", help="开始日期（YYYY-MM-DD）"),
    until: str | None = typer.Option(None, "--until", help="结束日期（YYYY-MM-DD）"),
    out: Path | None = typer.Option(None, "--out", help="报告输出路径"),
    from_state: bool = typer.Option(False, "--from-state", help="从本机拉取的只读状态重生成上一完整周报"),
) -> None:
    """生成带出处的每周变化 Markdown。"""
    def parse_date(value: str | None, name: str) -> date | None:
        if value is None:
            return None
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{name} 必须是 YYYY-MM-DD") from exc
        if parsed.isoformat() != value:
            raise ValueError(f"{name} 必须是 YYYY-MM-DD")
        return parsed

    def action(deps: Any) -> None:
        if any(item is None for item in (deps.registry, deps.manifest, deps.chunks, deps.change_log)):
            raise wiring.FeatureNotConnected("周报所需的注册表、快照、文档或变化日志尚未接入")
        now = datetime.now(UTC)
        if from_state:
            if since is not None or until is not None:
                raise ValueError("--from-state 不能与 --since/--until 同时使用")
            if not scheduling.is_pulled_replica(deps.data_dir):
                raise ValueError("--from-state 需要 datumoj pull 创建的本机副本")
            now = _pulled_created_at(deps.data_dir)
            if now is None:
                raise ValueError("本机副本的代次生成时间无效")
            start_day, end_day, week_key = previous_week_period(now.date())
        else:
            end_day = parse_date(until, "until") or now.date()
            start_day = parse_date(since, "since") or (end_day - timedelta(days=6))
            iso_year, iso_week, _ = end_day.isocalendar()
            week_key = f"{iso_year}-{iso_week:02d}"
        if start_day > end_day:
            raise ValueError("since 不能晚于 until")
        scheduler = scheduling.Scheduler(
            deps.data_dir, deps.registry.all(), lambda spec: _crawl_report(deps, spec),
            manifest=deps.manifest,
        )
        if from_state:
            scheduler.now = lambda: now
        status = _monitored_status(scheduler)
        alerts = [alert for source in status["sources"] for alert in source["alerts"]]
        alerts.extend(status.get("cloud_alerts", []))
        content = weekly_report(deps, start_day, end_day, alerts)
        path = out or (
            deps.data_dir / "reports" / "weekly" / f"{week_key}.md"
            if from_state else deps.data_dir / "reports" / f"weekly-{week_key[:4]}-W{week_key[-2:]}.md"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        typer.echo(str(path))
    _run("report", action, allow_pulled=from_state)


def _schedule_call(action: Callable[[], Any]) -> None:
    try:
        typer.echo(_json(action()))
    except typer.Exit:
        raise
    except ValueError as exc:
        typer.echo(f"参数错误：{exc}", err=True)
        raise typer.Exit(2) from exc
    except Exception as exc:
        typer.echo(f"运行失败：{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc


@schedule_app.command("status")
def schedule_status() -> None:
    """显示总开关、来源开关、运行记录、下次到期和运行锁（JSON）。"""
    _schedule_call(lambda: _monitored_status(_scheduler()))


@schedule_app.command("due")
def schedule_due(
    data_dir: Path | None = typer.Option(None, "--data-dir"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 来源 ID 数组"),
) -> None:
    _schedule_call(lambda: _scheduler_at(
        data_dir if data_dir is not None else _data_dir(), read_only=True,
    )[1].due())


def _set_sources(source: str | None, all_sources: bool, enabled: bool) -> Any:
    if (source is None) == (not all_sources):
        raise ValueError("请指定 SOURCE 或 --all，且只能选一个")
    scheduler = _writable_scheduler()
    if all_sources:
        ids = set(scheduler.sources) | set(scheduling.read_control(_data_dir())["sources"])
    else:
        if source not in scheduler.sources:
            raise ValueError(f"注册表里没有来源 {source!r}")
        ids = [source]
    return scheduling.set_enabled(_data_dir(), enabled, ids)


@schedule_app.command("enable")
def schedule_enable(
    source: str | None = typer.Argument(None), all_sources: bool = typer.Option(False, "--all"),
) -> None:
    """打开来源开关；总开关单独设置。"""
    _schedule_call(lambda: _set_sources(source, all_sources, True))


@schedule_app.command("disable")
def schedule_disable(
    source: str | None = typer.Argument(None), all_sources: bool = typer.Option(False, "--all"),
) -> None:
    """关闭指定来源或全部来源开关。"""
    _schedule_call(lambda: _set_sources(source, all_sources, False))


@schedule_app.command("master")
def schedule_master(state: str = typer.Argument(..., help="on 或 off")) -> None:
    def action() -> Any:
        if state not in {"on", "off"}:
            raise ValueError("总开关必须为 on 或 off")
        return scheduling.set_enabled(_data_dir(), state == "on")
    _schedule_call(action)


def _schedule_results(action: Callable[[], Any], *, manual: bool = False) -> None:
    def report() -> Any:
        result = action()
        typer.echo(_json(result))
        rows = [result] if manual else result
        if any(row.get("exit_status", 0) or (manual and row["status"] == "skipped"
                   and row.get("reason_code") != "write_lease_unavailable") for row in rows):
            raise typer.Exit(1)
        raise typer.Exit(0)
    _schedule_call(report)


def _writable_scheduler() -> scheduling.Scheduler:
    root = _data_dir()
    with scheduling.workspace_lock(root):
        scheduling.ensure_writable(root)
        return _scheduler()


@schedule_app.command("tick")
def schedule_tick() -> None:
    """串行运行已开启且到期的来源。"""
    _schedule_results(lambda: _writable_scheduler().tick())


@schedule_app.command("run-now")
@schedule_app.command("run")
def schedule_run(source: str = typer.Argument(...)) -> None:
    """忽略到期时间运行一次；仍检查总开关、来源开关和锁。"""
    _schedule_results(lambda: _writable_scheduler().run_now(source), manual=True)


@launchd_app.command("show")
def launchd_show() -> None:
    typer.echo(scheduling.launchd_plist(Path(__file__).resolve().parents[2], _data_dir()).decode(), nl=False)


@launchd_app.command("install")
def launchd_install() -> None:
    def action() -> Any:
        root = _data_dir()
        with scheduling.workspace_lock(root):
            scheduling.ensure_writable(root)
            return scheduling.install_launchd(Path(__file__).resolve().parents[2], root)

    _schedule_call(action)


@launchd_app.command("uninstall")
def launchd_uninstall() -> None:
    _schedule_call(scheduling.uninstall_launchd)


@app.command()
def admin(
    port: Annotated[int, typer.Option("--port", min=1, max=65535, help="本机网页端口")] = 8765,
    data_dir: Annotated[Path | None, typer.Option("--data-dir", help="本次管理页面使用的数据目录")] = None,
) -> None:
    """启动只监听 127.0.0.1 的抓取管理页面。"""
    root = data_dir if data_dir is not None else _data_dir()
    try:
        with scheduling.workspace_lock(root):
            scheduling.ensure_writable(root)
            deps, scheduler = _scheduler_at(root)
    except scheduling.ReadOnlyReplicaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except wiring.FeatureNotConnected as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(f"运行失败：{type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc

    import uvicorn

    from datumoj.admin import create_app

    def run_now_in_worker(source_id: str) -> Any:
        # The real storage implementations may own thread-affine SQLite connections.
        # Build their dependencies in the worker that invokes Scheduler.run_now.
        _, worker_scheduler = _scheduler_at(root)
        return worker_scheduler.run_now(source_id)

    web_app = create_app(root, deps, scheduler, run_now=run_now_in_worker)
    typer.echo(f"管理页面：http://127.0.0.1:{port}/")
    uvicorn.run(web_app, host="127.0.0.1", port=port)


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


@index_app.callback()
def index(
    ctx: typer.Context,
    rebuild: bool = typer.Option(False, "--rebuild", help="从当前文档块重建索引"),
    vectors_only: bool = typer.Option(False, "--vectors-only", help="只重试暂缺文档的向量嵌入"),
) -> None:
    """解析新快照并更新索引；可选择全量重建或只补做向量。"""
    if ctx.invoked_subcommand is not None:
        return
    def action(deps: Any) -> None:
        if vectors_only:
            count = deps.reindex(deps.pipeline_deps, vectors_only=True)
            typer.echo(f"向量补做完成：更新 {count} 个块")
            _echo_vector_status(deps)
            return
        summary = deps.parse_pending(deps.pipeline_deps)
        if rebuild:
            pipeline = deps.pipeline_deps
            # Wiring may run parse_pending on dataclasses.replace(deps). The
            # summary is the reliable boundary for the remaining allowance.
            if pipeline.vector_limit is not None:
                pipeline = replace(pipeline, vector_limit=summary.vector_remaining)
                if summary.vector_remaining == 0:
                    pipeline = replace(pipeline, vectors=None)
            with rebuilding_index(deps.data_dir):
                count = deps.reindex(pipeline)
            typer.echo(f"索引重建完成：{count} 个块")
        else:
            typer.echo(f"索引更新完成：处理 {len(summary.parsed)} 个新快照")
        if summary.vectors_missing:
            typer.echo(f"向量暂缺：{_json(summary.vectors_missing)}", err=True)
        if summary.failed:
            typer.echo(f"解析失败：{_json(summary.failed)}", err=True)
            raise typer.Exit(1)
    _run("index", action)


@index_app.command("maintain")
def index_maintain(
    data_dir: Annotated[Path | None, typer.Option("--data-dir")] = None,
    retain_hours: Annotated[float | None, typer.Option("--retain-hours", min=0)] = None,
) -> None:
    """合并向量碎片并清理旧版本，输出前后计数。"""
    from datumoj.core.state import local_write
    from datumoj.index.vector import maintain_vectors

    root = data_dir if data_dir is not None else Path(_data_dir())

    def action():
        with local_write(root):
            return maintain_vectors(root / "vectors", None if retain_hours is None else timedelta(hours=retain_hours))

    with scheduling.workspace_lock(root):
        scheduling.ensure_writable(root)
        _state_call(action)


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
            result.update(data_state(deps.data_dir))
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
                pipeline = item.get("pipeline", {})
                generation = pipeline.get("index_generation") or "未知"
                if generation == INDEX_REBUILDING:
                    generation = "重建中"
                typer.echo(
                    f"{item['source_id']}：{item['status']}；快照 {item['snapshots']}；截止 {as_of}；"
                    f"已解析 {pipeline.get('parsed', 0)}/{pipeline.get('parseable_snapshots', 0)}；"
                    f"解析失败 {pipeline.get('parse_failed', 0)}；needs_ocr {pipeline.get('needs_ocr', 0)}；"
                    f"可检索块 {pipeline.get('searchable_chunks', 0)}；索引 {generation}"
                )
        for warning in result.get("warnings", []):
            typer.echo(f"提示：{warning}")
        _echo_vector_status(deps)
    _run("status", action)


@app.command()
def mcp() -> None:
    """通过 stdio 启动只读 MCP 服务器。"""
    from datumoj import mcp_server

    os.environ.setdefault(mcp_server.FACTORY_ENV, "datumoj.wiring:build_tools")
    previous_data_dir = os.environ.get("DATUMOJ_DATA_DIR")
    os.environ["DATUMOJ_DATA_DIR"] = _data_dir()
    try:
        mcp_server.main()
    finally:
        if previous_data_dir is None:
            os.environ.pop("DATUMOJ_DATA_DIR", None)
        else:
            os.environ["DATUMOJ_DATA_DIR"] = previous_data_dir


@app.command("eval")
def evaluate_corpus(
    gate: bool = typer.Option(False, "--gate", help="未达到全部评测门槛时退出码为 1"),
    report: Path | None = typer.Option(None, "--report", help="报告目录（默认使用临时目录）"),
    questions_dir: Path | None = typer.Option(None, "--questions", help="题库目录（默认 eval/questions 全部题库）"),
) -> None:
    """用题库评测本地语料，并输出 JSON 和 Markdown 报告。"""
    from datumoj.eval import check_gate, evaluate, load_questions, write_reports

    def action(deps: Any) -> None:
        def cite(citation: Citation) -> list:
            restored = deps.tools.cite(citation.snapshot_sha256, citation.locator)
            data = restored.get("data", {})
            return data.get("passages", []) if data.get("status") == "ok" else []

        questions = load_questions(questions_dir or Path(__file__).resolve().parents[2] / "eval/questions")
        result = evaluate(
            questions, retriever=deps.retriever, observation_store=deps.observations,
            cite=cite, manifest=deps.manifest, catalog=deps.catalog,
            list_changes=deps.tools.list_changes,
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
