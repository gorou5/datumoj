"""本机抓取管理页面；开关与运行均委托给 ``core.schedule``。"""

from __future__ import annotations

import html
import hmac
import secrets
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from datumoj.core import schedule


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _schedule_label(value: str | None) -> str:
    labels = {"hourly": "每小时", "daily": "每天", "monthly": "每 30 天", "manual": "手动"}
    return labels.get(value or "", value or "未设置")


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
    if last.get("status") == "skipped":
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


def _page(deps: object, scheduler: object, token: str, notice: str | None = None) -> str:
    status = scheduler.status()
    rows_by_id = {row["source_id"]: row for row in status["sources"]}
    notice_html = f'<p class="notice">{_e(notice)}</p>' if notice else ""
    sources = deps.registry.all()
    rows = []
    for spec in sources:
        row = rows_by_id[spec.id]
        last = row.get("last_run")
        running = row.get("running", False)
        state = "正在运行" if running else ("开启" if row.get("enabled") else "关闭")
        classes = "on" if row.get("enabled") else "off"
        rows.append(
            "<tr>"
            f'<th scope="row"><span class="source-id">{_e(spec.id)}</span><br><span class="source-name">{_e(spec.name)}</span></th>'
            f'<td><span class="pill {classes}">{_e(state)}</span><br>{_switch_form(token, "source", row.get("enabled", False), spec.id)}</td>'
            f'<td>{_e(_schedule_label(row.get("schedule")))}</td>'
            f'<td>{_last_run(last)}</td>'
            f'<td>{_e(row.get("next_due") or "—")}</td>'
            f'<td>{"是" if running else "否"}</td>'
            f'<td>{len(deps.manifest.snapshots_for(spec.id)) if getattr(deps, "manifest", None) is not None else 0}</td>'
            f'<td>{_e(_format_bytes(_raw_bytes(deps, spec.id)))}</td>'
            f'<td>{_run_form(token, spec.id)}</td>'
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="9" class="empty">当前没有登记来源。</td></tr>')
    master = "开启" if status["enabled"] else "关闭"
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
    .table-wrap {{ overflow-x: auto; border: 1px solid #dfe4ec; border-radius: 12px; background: white; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 1120px; }}
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
    <div class="table-wrap">
      <table>
        <thead><tr><th>来源</th><th>来源开关</th><th>调度频率</th><th>上次运行</th><th>下次到期</th><th>运行中</th><th>快照数</th><th>raw 空间</th><th>操作</th></tr></thead>
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

    async def home(request: Request) -> Response:
        notice_key = request.query_params.get("notice")
        notices = {
            "master-on": "总开关已开启。",
            "master-off": "总开关已关闭。",
            "source-on": "来源开关已开启。",
            "source-off": "来源开关已关闭。",
            "run-started": "已开始。",
        }
        return HTMLResponse(_page(deps, scheduler, csrf_token, notices.get(notice_key)))

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

            def execute() -> None:
                try:
                    target(source_id)
                except Exception:  # noqa: BLE001 - the scheduler persists crawl failures; keep worker alive on setup errors.
                    return

            threading.Thread(target=execute, name=f"datumoj-admin-{source_id}", daemon=True).start()
            notice = "run-started"
        else:
            return PlainTextResponse("无效的操作", status_code=400)
        return RedirectResponse("/?" + urlencode({"notice": notice}), status_code=303)

    return Starlette(routes=[Route("/", home, methods=["GET"]), Route("/", update, methods=["POST"])])
