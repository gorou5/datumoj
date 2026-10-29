"""本机抓取管理页面；开关与运行均委托给 ``core.schedule``。"""

from __future__ import annotations

import hmac
import html
import secrets
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from datumoj.rikolto.core import schedule
from datumoj.rikolto.core.models import PipelineStatus
from datumoj.rikolto.core.monitor import attach_cloud_status
from datumoj.rikolto.index.coverage import INDEX_REBUILDING, index_generation, pipeline_status


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _schedule_label(value: str | None) -> str:
    entry = schedule.SCHEDULES.get(value or "")
    return entry[1] if entry else value or "未设置"


def _raw_bytes(deps: object, source_id: str) -> int:
    manifest = getattr(deps, "manifest", None)
    raw = getattr(deps, "raw", None)
    if manifest is None or raw is None:
        return 0
    sizes: dict[str, int] = {}
    for snapshot in manifest.snapshots_for(source_id):
        sha = snapshot.sha256
        if sha is not None and sha not in sizes and raw.exists(sha):
            sizes[sha] = snapshot.size or 0
    return sum(sizes.values())


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024**2:
        return f"{size / 1024:.1f} KiB"
    if size < 1024**3:
        return f"{size / 1024**2:.1f} MiB"
    return f"{size / 1024**3:.1f} GiB"


def _last_run(last: dict | None) -> str:
    if not last:
        return '<span class="muted">无运行记录</span>'
    started = _e(last.get("started_at"))
    if last.get("state") == "interrupted" or last.get("status") == "interrupted":
        result = "运行中断"
    elif last.get("state") == "running" or last.get("status") == "running":
        result = "运行中"
    elif last.get("status") == "skipped":
        result = f'跳过：{_e(last.get("reason"))}'
    else:
        failures = last.get("failures") or {}
        failure_count = sum(failures.values()) if isinstance(failures, dict) else 0
        parts = [f'新增 {_e(last.get("new", 0) or 0)}', f'未变 {_e(last.get("unchanged", 0) or 0)}']
        if failure_count:
            detail = "，".join(f"{_e(name)} × {_e(count)}" for name, count in failures.items())
            parts.append(f"失败 {_e(failure_count)}（{detail}）")
        else:
            parts.append("失败 0")
        result = "；".join(parts)
    stop = _e(last.get("stop_reason") or "—")
    return f'<span>{started}</span><br><span>{result}</span><br><span class="muted">停止：{stop}</span>'


def _switch_form(token: str, action: str, enabled: bool, source_id: str | None = None) -> str:
    value = "off" if enabled else "on"
    label = "关闭总开关" if enabled else "开启总开关"
    fields = f'<input type="hidden" name="csrf" value="{_e(token)}">'
    if source_id is not None:
        fields += f'<input type="hidden" name="source_id" value="{_e(source_id)}">'
        label = "关闭" if enabled else "开启"
    fields += f'<input type="hidden" name="enabled" value="{value}">'
    fields += f'<button type="submit">{label}</button>'
    return f'<form method="post" action="/" class="inline">{fields}<input type="hidden" name="action" value="{action}"></form>'


def _run_form(token: str, source_id: str) -> str:
    return (
        '<form method="post" action="/" class="inline">'
        f'<input type="hidden" name="csrf" value="{_e(token)}">'
        f'<input type="hidden" name="source_id" value="{_e(source_id)}">'
        '<input type="hidden" name="action" value="run">'
        '<button type="submit" class="secondary">立即运行</button></form>'
    )


def _alerts_section(status: dict) -> str:
    alerts = [alert for row in status["sources"] for alert in row.get("alerts", [])]
    alerts.extend(status.get("cloud_alerts", []))
    alerts.sort(key=lambda alert: (
        0 if alert.get("severity") == "error" else 1,
        str(alert.get("source_id", "")),
        str(alert.get("since", "")),
    ))
    if not alerts:
        content = '<p class="empty">没有提醒。</p>'
    else:
        rows = []
        for alert in alerts:
            severity = alert.get("severity")
            label = "错误" if severity == "error" else "警告"
            rows.append(
                f'<li class="alert-{_e(severity)}">'
                f'<strong>{_e(label)}</strong>　{_e(alert.get("source_id"))}　'
                f'<span class="muted">{_e(alert.get("rule"))}</span>'
                f'<p>{_e(alert.get("message"))}</p>'
                f'<time class="muted">起始：{_e(alert.get("since"))}</time>'
                '</li>'
            )
        content = f'<ul class="alert-list">{"".join(rows)}</ul>'
    return f'<section class="alerts" aria-label="提醒"><h2>提醒</h2>{content}</section>'


def _ledger_warnings_section(status: dict) -> str:
    warnings = status.get("ledger_warnings", [])
    if not warnings:
        return ""
    rows = "".join(
        f'<li>第 {_e(warning.get("line"))} 行：{_e(warning.get("reason"))}</li>'
        for warning in warnings
    )
    return (
        '<section class="alerts" aria-label="运行账本警告">'
        '<h2>运行账本警告</h2><ul class="alert-list">'
        f"{rows}</ul></section>"
    )


def _page(deps: object, scheduler: object, token: str, notice: str | None = None) -> str:
    status = scheduler.status()
    history_reader = getattr(scheduler, "_read_history", None)
    history = history_reader()[0] if callable(history_reader) else []
    now = scheduler.now() if callable(getattr(scheduler, "now", None)) else schedule.utcnow()
    status = attach_cloud_status(
        status, getattr(deps, "data_dir", getattr(scheduler, "data_dir", ".")),
        deps.registry.all(), history, now,
    )
    rows_by_id = {row["source_id"]: row for row in status["sources"]}
    show_cloud_success = status.get("cloud") is not None
    success_heading = "<th>最后成功运行</th>" if show_cloud_success else ""
    notice_html = f'<p class="notice">{_e(notice)}</p>' if notice else ""
    sources = deps.registry.all()
    pipeline_rows: dict[str, dict] = {}
    tools = getattr(deps, "tools", None)
    coverage = getattr(tools, "coverage", None)
    if callable(coverage):
        result = coverage()
        pipeline_rows = {
            row["source_id"]: row.get("pipeline", {})
            for row in result.get("data", {}).get("sources", [])
        }
    elif getattr(deps, "chunks", None) is not None and getattr(deps, "manifest", None) is not None:
        retriever = getattr(deps, "retriever", getattr(tools, "retriever", None))
        generation = index_generation(retriever, data_dir=getattr(deps, "data_dir", None))
        parsers = getattr(tools, "parsers", None)
        for spec in sources:
            pipeline_rows[spec.id] = pipeline_status(
                [spec.id], registry=deps.registry, manifest=deps.manifest, chunks=deps.chunks,
                parsers=parsers, index_generation=generation,
            ).model_dump(mode="json")
    rows = []
    for spec in sources:
        row = rows_by_id[spec.id]
        last = row.get("last_run")
        running = row.get("running", False)
        state = "正在运行" if running else ("开启" if row.get("enabled") else "关闭")
        classes = "on" if row.get("enabled") else "off"
        last_success = row.get("last_successful_run")
        successful_run = (
            f'<time>{_e(last_success.get("finished_at"))}</time>'
            if last_success else '<span class="muted">无成功记录</span>'
        ) if show_cloud_success else ""
        success_cell = f"<td>{successful_run}</td>" if show_cloud_success else ""
        pipeline = pipeline_rows.get(spec.id) or PipelineStatus(
            parseable_snapshots=0, parsed=0, parse_failed=0, needs_ocr=0, searchable_chunks=0,
        ).model_dump(mode="json")
        generation = pipeline.get("index_generation") or "未知"
        if generation == INDEX_REBUILDING:
            generation = "重建中"
        rows.append(
            "<tr>"
            f'<th scope="row"><span class="source-id">{_e(spec.id)}</span><br><span class="source-name">{_e(spec.name)}</span></th>'
            f'<td><span class="pill {classes}">{_e(state)}</span><br>{_switch_form(token, "source", row.get("enabled", False), spec.id)}</td>'
            f'<td>{_e(_schedule_label(row.get("schedule")))}</td>'
            f'<td>{_last_run(last)}</td>'
            f'{success_cell}'
            f'<td>{_e(row.get("next_due") or "—")}</td>'
            f'<td>{"是" if running else "否"}</td>'
            f'<td>{len(deps.manifest.snapshots_for(spec.id)) if getattr(deps, "manifest", None) is not None else 0}</td>'
            f'<td>{_e(_format_bytes(_raw_bytes(deps, spec.id)))}</td>'
            f'<td>已解析 {_e(pipeline.get("parsed", 0))}/{_e(pipeline.get("parseable_snapshots", 0))}；'
            f'解析失败数：{_e(pipeline.get("parse_failed", 0))}；needs_ocr {_e(pipeline.get("needs_ocr", 0))}；'
            f'可检索块 {_e(pipeline.get("searchable_chunks", 0))}；索引 {_e(generation)}</td>'
            f'<td>{_run_form(token, spec.id)}</td>'
            "</tr>"
        )
    if not rows:
        colspan = 11 if show_cloud_success else 10
        rows.append(f'<tr><td colspan="{colspan}" class="empty">当前没有登记来源。</td></tr>')
    master = "开启" if status["enabled"] else "关闭"
    cloud = status.get("cloud")
    cloud_section = ""
    if cloud:
        writer = cloud.get("writer") or {}
        writer_kind = {"actions": "Actions", "local": "本机", "unknown": "未知"}.get(
            writer.get("kind"), "未知",
        )
        cloud_section = (
            '<section class="master" aria-label="云端代次">'
            f'<div><strong>云端代次</strong><br>第 {_e(cloud.get("generation"))} 代；'
            f'生成时间：{_e(cloud.get("created_at"))}；写入者：{_e(writer_kind)}</div>'
            '</section>'
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DATUMOJ 抓取管理</title>
  <style>
    :root {{ color-scheme: light; font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #172033; background: #f4f6fa; }}
    body {{ margin: 0; }}
    main {{ max-width: 1440px; margin: 40px auto; padding: 0 24px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .subtitle, .muted {{ color: #667085; }}
    .master {{ display: flex; align-items: center; gap: 14px; margin: 24px 0; padding: 18px 20px; border: 1px solid #dfe4ec; border-radius: 12px; background: white; }}
    .master strong {{ font-size: 17px; }}
    .notice {{ padding: 12px 16px; border-radius: 8px; background: #e8f2ff; color: #164b84; }}
    .alerts {{ margin: 24px 0; padding: 18px 20px; border: 1px solid #dfe4ec; border-radius: 12px; background: white; }}
    .alerts h2 {{ margin: 0 0 10px; font-size: 18px; }}
    .alert-list {{ margin: 0; padding: 0; list-style: none; }}
    .alert-list li {{ padding: 11px 12px; border-left: 4px solid #d98b18; background: #fff9eb; }}
    .alert-list li + li {{ margin-top: 8px; }}
    .alert-list li.alert-error {{ border-color: #c43d3d; background: #fff0f0; }}
    .alert-list p {{ margin: 5px 0; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid #dfe4ec; border-radius: 12px; background: white; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 1480px; }}
    th, td {{ padding: 13px 14px; border-bottom: 1px solid #edf0f4; text-align: left; vertical-align: top; }}
    thead th {{ background: #f9fafb; color: #475467; font-size: 12px; font-weight: 650; white-space: nowrap; }}
    tbody tr:last-child > * {{ border-bottom: 0; }}
    .source-id {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; color: #475467; }}
    .source-name {{ font-weight: 650; }}
    .pill {{ display: inline-block; margin-bottom: 7px; padding: 2px 9px; border-radius: 99px; font-size: 12px; }}
    .pill.on {{ background: #e8f7ee; color: #17663b; }}
    .pill.off {{ background: #eef0f3; color: #596273; }}
    form.inline {{ display: inline-block; }}
    button {{ border: 0; border-radius: 7px; padding: 7px 10px; background: #176b45; color: white; cursor: pointer; font: inherit; font-size: 13px; }}
    button.secondary {{ background: #eaf0ff; color: #294c9b; }}
    button:hover {{ filter: brightness(.95); }}
    .empty {{ color: #667085; text-align: center; padding: 30px; }}
    footer {{ margin-top: 16px; color: #667085; font-size: 12px; }}
    @media (max-width: 720px) {{ main {{ margin: 24px auto; padding: 0 14px; }} h1 {{ font-size: 23px; }} }}
  </style>
</head>
<body>
  <main>
    <h1>抓取管理</h1>
    <p class="subtitle">DATUMOJ 本机抓取状态与控制</p>
    {notice_html}
    <section class="master" aria-label="总开关">
      <div><strong>总开关</strong><br><span class="pill {'on' if status['enabled'] else 'off'}">{master}</span></div>
      {_switch_form(token, "master", status["enabled"])}
    </section>
    {cloud_section}
    {_alerts_section(status)}
    {_ledger_warnings_section(status)}
    <div class="table-wrap">
      <table>
        <thead><tr><th>来源</th><th>来源开关</th><th>调度频率</th><th>上次运行</th>{success_heading}<th>下次到期</th><th>运行中</th><th>快照数</th><th>raw 空间</th><th>管线覆盖</th><th>操作</th></tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    <footer>立即运行仍会检查总开关、来源开关和来源运行锁。</footer>
  </main>
</body>
</html>"""


def create_app(
    data_dir: str | Path,
    deps: object,
    scheduler: object,
    *,
    token: str | None = None,
    run_now: Callable[[str], object] | None = None,
) -> Starlette:
    """构建单页应用。生产环境应为后台线程提供线程内创建的 ``run_now``。"""
    root = Path(data_dir)
    csrf_token = token or secrets.token_urlsafe(32)
    run_failures: dict[str, str] = {}
    if getattr(scheduler, "manifest", None) is None and hasattr(scheduler, "manifest"):
        scheduler.manifest = getattr(deps, "manifest", None)

    async def home(request: Request) -> Response:
        notice_key = request.query_params.get("notice")
        source_id = request.query_params.get("source_id", "")
        notices = {
            "master-on": "总开关已开启。",
            "master-off": "总开关已关闭。",
            "source-on": "来源开关已开启。",
            "source-off": "来源开关已关闭。",
            "run-started": "已开始。",
            "run-pending": "运行请求已提交，启动状态尚未确认。",
        }
        notice = notices.get(notice_key)
        if notice_key in {"run-started", "run-pending"} and source_id in run_failures:
            notice = f"后台运行失败：{run_failures[source_id]}"
        return HTMLResponse(_page(deps, scheduler, csrf_token, notice))

    async def update(request: Request) -> Response:
        form = await request.form()
        supplied = str(form.get("csrf", ""))
        if not hmac.compare_digest(supplied.encode("utf-8"), csrf_token.encode("utf-8")):
            return PlainTextResponse("无效或缺少防跨站请求令牌", status_code=403)
        action = str(form.get("action", ""))
        source_id = str(form.get("source_id", ""))
        enabled_value = str(form.get("enabled", ""))
        if action == "master":
            if enabled_value not in {"on", "off"}:
                return PlainTextResponse("无效的总开关状态", status_code=400)
            enabled = enabled_value == "on"
            schedule.set_enabled(root, enabled)
            notice = "master-on" if enabled else "master-off"
        elif action == "source":
            if source_id not in scheduler.sources:
                return PlainTextResponse("来源不存在", status_code=404)
            if enabled_value not in {"on", "off"}:
                return PlainTextResponse("无效的来源开关状态", status_code=400)
            enabled = enabled_value == "on"
            schedule.set_enabled(root, enabled, [source_id])
            notice = "source-on" if enabled else "source-off"
        elif action == "run":
            if source_id not in scheduler.sources:
                return PlainTextResponse("来源不存在", status_code=404)
            current = scheduler.status()
            row = next(row for row in current["sources"] if row["source_id"] == source_id)
            if row["running"]:
                return HTMLResponse(_page(deps, scheduler, csrf_token, "正在运行，未重复启动。"), status_code=409)
            if not row["effective_enabled"]:
                reason = "总开关关闭" if not current["enabled"] else "来源开关关闭"
                return HTMLResponse(_page(deps, scheduler, csrf_token, f"未运行：{reason}。"), status_code=409)
            target = run_now or scheduler.run_now
            run_failures.pop(source_id, None)
            worker_done = threading.Event()

            def execute() -> None:
                try:
                    target(source_id)
                except Exception as exc:  # noqa: BLE001 - report worker setup failures on the next page load.
                    run_failures[source_id] = f"{type(exc).__name__}: {exc}"
                finally:
                    worker_done.set()

            try:
                thread = threading.Thread(target=execute, name=f"datumoj-admin-{source_id}", daemon=True)
                thread.start()
            except Exception as exc:  # noqa: BLE001 - thread creation/start failures happen in this request.
                run_failures[source_id] = f"{type(exc).__name__}: {exc}"
                return HTMLResponse(
                    _page(deps, scheduler, csrf_token, f"后台运行失败：{run_failures[source_id]}"),
                    status_code=500,
                )
            deadline = time.monotonic() + 1
            startup_confirmed = False
            while not worker_done.is_set():
                if worker_done.is_set():
                    startup_confirmed = True
                    break
                current = scheduler.status()
                if any(row["source_id"] == source_id and row["running"] for row in current["sources"]):
                    startup_confirmed = True
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                worker_done.wait(min(0.01, remaining))
            if worker_done.is_set() and source_id in run_failures:
                return HTMLResponse(
                    _page(deps, scheduler, csrf_token, f"后台运行失败：{run_failures[source_id]}"),
                    status_code=500,
                )
            if worker_done.is_set():
                startup_confirmed = True
            notice = "run-started" if startup_confirmed else "run-pending"
        else:
            return PlainTextResponse("无效的操作", status_code=400)
        query = {"notice": notice}
        if action == "run":
            query["source_id"] = source_id
        return RedirectResponse("/?" + urlencode(query), status_code=303)

    return Starlette(routes=[Route("/", home, methods=["GET"]), Route("/", update, methods=["POST"])])
