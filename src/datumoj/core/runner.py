"""驱动 Crawler：惰性计划、条件请求、raw / manifest 落库和运行上限。"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from uuid import uuid4

from datumoj.core.contracts import (
    CrawlContext,
    Crawler,
    FetchResult,
    FetchTask,
    HttpFetcher,
    Manifest,
    RawStore,
    SourceSpec,
    UrlNotAllowed,
)
from datumoj.core.models import FAILED_OUTCOMES, FetchOutcome, redact_url

SPA_MIN_TEXT = 100
MAX_PLAN_STEPS = 10_000
"""包括重复 URL 在内的产出任务上限，避免错误分页或无限重复任务。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CrawlDeps:
    """存储由调用方持有；fetcher 应按 spec 配置 host、robots、重试及流式单文件上限。

    例如使用 PoliteHttpFetcher.from_spec(spec)。执行器另行保证抓取间隔和累计上限。
    时钟可注入，以便离线测试不用真的等待。
    """

    raw: RawStore
    manifest: Manifest
    fetcher: HttpFetcher
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = _utcnow


@dataclass
class CrawlReport:
    source_id: str
    new: int = 0
    unchanged: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None
    elapsed_seconds: float = 0.0
    planned: list[FetchTask] = field(default_factory=list)
    """dry_run 的去重计划；URL 已去 Key，不推测需要正文才能发现的任务。"""


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0
        self.root = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"head", "script", "style"}:
            self.hidden += 1
        if dict(attrs).get("id", "") in {"root", "__next", "app"}:
            self.root = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"head", "script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _default_outcome(data: bytes) -> FetchOutcome:
    # 按正文判断，不信 Content-Type / charset / 扩展名。
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp932", errors="replace")
    if not text.strip():
        return FetchOutcome.empty_body
    if re.match(r"\s*<(?:!doctype\s+html\b|html\b|head\b|body\b|div\b|p\b|script\b)", text, re.I):
        page = _HtmlText()
        page.feed(text)
        page.close()
        visible = "".join("".join(page.parts).split())
        if len(visible) < SPA_MIN_TEXT and (page.root or "__next" in text.lower()):
            return FetchOutcome.spa_shell
        if not visible:
            return FetchOutcome.empty_body
    return FetchOutcome.ok


def _with_outcome(result: FetchResult, outcome: FetchOutcome, error: str | None = None) -> FetchResult:
    fields = result.model_dump()
    fields["outcome"] = outcome
    if outcome in FAILED_OUTCOMES or outcome is FetchOutcome.not_modified:
        fields["content"] = None
    if outcome is not FetchOutcome.ok and outcome is not FetchOutcome.not_modified:
        fields["error"] = error or result.error or outcome.value
    return FetchResult.model_validate(fields)


def run_crawl(
    spec: SourceSpec,
    crawler: Crawler,
    deps: CrawlDeps,
    limit: int | None = None,
    dry_run: bool = False,
) -> CrawlReport:
    """依次执行计划；追加任务优先，同一次运行按去 Key 的 URL 去重。

    limit 只传给插件；max_files 对所有抓取（含失败和 304）计数。采集器未拒收的正文计入累计字节；
    抓取器报告超过总上限时，或累计字节超过总上限时，记 too_large、不写 raw，完成本次 after_fetch 后停止。
    空正文 / SPA 原文仍存 raw 以便诊断。存储错误直接抛出，不能把未成功落库的运行报告为成功。
    """
    started = deps.clock()
    report = CrawlReport(source_id=spec.id)
    ctx = CrawlContext(spec=spec, manifest=deps.manifest, raw=deps.raw, now=deps.now(), limit=limit)
    queue: deque[FetchTask] = deque()
    seen: set[str] = set()
    produced = fetched = total_bytes = 0
    last_fetch: float | None = None

    def failure(reason: str) -> None:
        report.failures[reason] = report.failures.get(reason, 0) + 1

    def plugin_error(stage: str, exc: Exception) -> None:
        failure(f"{stage}_error")
        message = re.sub(r"https?://[^\s\"'<>]+", lambda m: redact_url(m.group()), str(exc), flags=re.I)
        reason = f"{stage} 失败：{type(exc).__name__}: {message}"
        report.stop_reason = f"{report.stop_reason}；{reason}" if report.stop_reason else reason

    def count_task() -> None:
        nonlocal produced
        produced += 1
        if produced > MAX_PLAN_STEPS:
            raise ValueError(f"任务超过 {MAX_PLAN_STEPS} 个，分页可能没有终止")

    try:
        try:
            plan = iter(crawler.plan(ctx))
        except Exception as exc:
            plugin_error("plan", exc)
            return report
        while True:
            try:
                if queue:
                    task = queue.popleft()
                else:
                    task = next(plan, None)
                    if task is None:
                        break
                    count_task()
            except Exception as exc:
                plugin_error("plan", exc)
                break
            url = task.redacted_url
            if url in seen:
                continue
            seen.add(url)
            if dry_run:
                report.planned.append(FetchTask(url=url, meta=task.meta))
                if len(report.planned) >= spec.max_files:
                    report.stop_reason = f"因上限停止：max_files={spec.max_files}"
                    break
                continue

            prev = deps.manifest.last_success(url)
            if last_fetch is not None:
                wait = last_fetch + 1 / spec.rate_per_sec - deps.clock()
                if wait > 0:
                    deps.sleep(wait)
            last_fetch = deps.clock()
            try:
                result = deps.fetcher.fetch(task.url, etag=prev and prev.etag, last_modified=prev and prev.last_modified)
            except UrlNotAllowed as exc:
                result = FetchResult(
                    url=url, outcome=FetchOutcome.network_error, fetched_at=deps.now(), error=str(exc),
                )
            fetched += 1
            fetcher_too_large = result.outcome is FetchOutcome.too_large
            if not fetcher_too_large:
                override = crawler.classify(task, result)
                if override is not None:
                    result = _with_outcome(result, override)
                elif result.outcome is FetchOutcome.ok:
                    result = _with_outcome(result, _default_outcome(result.content))

            # 采集器可以在单文件限制下拒收正文；这类内容不会写 raw，也不占来源累计容量。
            crawler_too_large = result.outcome is FetchOutcome.too_large and not fetcher_too_large
            if not crawler_too_large:
                total_bytes += len(result.content) if result.content is not None else 0
            total_too_large = total_bytes > spec.max_total_bytes
            if total_too_large:
                result = _with_outcome(result, FetchOutcome.too_large, "累计正文超过 max_total_bytes 上限")
            if fetcher_too_large or total_bytes >= spec.max_total_bytes:
                report.stop_reason = f"因上限停止：max_total_bytes={spec.max_total_bytes}"
            elif fetched >= spec.max_files:
                report.stop_reason = f"因上限停止：max_files={spec.max_files}"

            if result.content is not None:
                deps.raw.put(result.content)
            snapshot = result.to_snapshot(uuid4().hex, spec.id, license=spec.license.name)
            deps.manifest.record_snapshot(snapshot, task.meta)
            if result.outcome is FetchOutcome.not_modified:
                report.unchanged += 1
            elif result.outcome is FetchOutcome.ok:
                if prev is not None and prev.sha256 == snapshot.sha256:
                    report.unchanged += 1
                else:
                    report.new += 1
            else:
                failure(result.outcome.value)

            # 所有已抓取的结果都先落库并完成回调，包括触发上限的结果；追加任务不再执行。
            try:
                for extra in crawler.after_fetch(task, result, ctx):
                    count_task()
                    if report.stop_reason is None:
                        queue.append(extra)
            except Exception as exc:
                plugin_error("after_fetch", exc)
            if report.stop_reason is not None:
                break
        return report
    finally:
        report.elapsed_seconds = deps.clock() - started
