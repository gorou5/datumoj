"""对来源运行和解析状态计算提醒。"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from datumoj.core.contracts import Manifest, ParseStatus, SourceSpec
from datumoj.core.models import FailureKind, classify_failure


CONSECUTIVE_FAILURES = 3
PARSE_RECENT_WINDOW = timedelta(hours=24)
PARSE_BASELINE_WINDOW = timedelta(days=7)
PARSE_FAILURE_RATE_INCREASE = 0.20
PARSE_MIN_SNAPSHOTS = 10
OVERDUE_CYCLES = 2
RETENTION_GAP = timedelta(hours=6)

_KIND_LABELS = {
    FailureKind.source_unavailable: "来源不可用",
    FailureKind.rate_limited: "频率受限",
    FailureKind.auth_failed: "认证失败",
    FailureKind.format_changed: "格式变化",
    FailureKind.policy: "策略限制",
    FailureKind.other: "其他失败",
}


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    rule: Literal["consecutive_failures", "parse_failure_rate", "overdue", "retention_gap"]
    severity: Literal["warning", "error"]
    message: str
    since: datetime
    details: dict[str, JsonValue]


def _failure_details(manifest: Manifest | None, source_id: str) -> tuple[dict[str, JsonValue], str]:
    counts: Counter[str] = Counter()
    retryability: dict[str, Counter[str]] = {}
    if manifest is not None:
        summary = manifest.failure_summary(source_id)
        for snapshot in summary.latest_failures:
            result = classify_failure(snapshot.outcome, snapshot.http_status)
            if result is None:
                continue
            kind = result.kind.value
            counts[kind] += 1
            retryability.setdefault(kind, Counter())["retryable" if result.retryable else "not_retryable"] += 1

    details: dict[str, JsonValue] = {
        "counts_by_kind": dict(sorted(counts.items())),
        "retryability_by_kind": {
            kind: {"retryable": values["retryable"], "not_retryable": values["not_retryable"]}
            for kind, values in sorted(retryability.items())
        },
    }
    labels = [
        f"{_KIND_LABELS[FailureKind(kind)]} {count} 次"
        for kind, count in sorted(counts.items())
    ]
    return details, "、".join(labels) if labels else "暂无抓取失败分类"


def _run_time(run: dict, key: str) -> datetime | None:
    value = run.get(key)
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_success(run: dict) -> bool:
    return run.get("status") == "completed" and run.get("exit_status") == 0


def _rate(stats: dict, status: ParseStatus) -> tuple[int, int]:
    failed = stats.get(ParseStatus.failed, stats.get(ParseStatus.failed.value, 0))
    total = sum(stats.values())
    return int(failed), int(total)


def evaluate_alerts(
    spec: SourceSpec,
    runs: list[dict],
    manifest: Manifest | None,
    now: datetime,
    *,
    enabled: bool,
    consecutive_limit: int = CONSECUTIVE_FAILURES,
    parse_recent_window: timedelta = PARSE_RECENT_WINDOW,
    parse_baseline_window: timedelta = PARSE_BASELINE_WINDOW,
    parse_failure_rate_increase: float = PARSE_FAILURE_RATE_INCREASE,
    parse_min_snapshots: int = PARSE_MIN_SNAPSHOTS,
    overdue_cycles: int = OVERDUE_CYCLES,
    retention_gap: timedelta = RETENTION_GAP,
) -> list[Alert]:
    """基于已读运行记录、Manifest 和注入时钟生成来源提醒。"""
    if (
        consecutive_limit < 1 or parse_min_snapshots < 1 or overdue_cycles < 1
        or parse_recent_window <= timedelta(0) or parse_baseline_window <= parse_recent_window
        or parse_failure_rate_increase < 0 or retention_gap < timedelta(0)
    ):
        raise ValueError("提醒阈值必须是非负值，次数至少为 1")

    failure_details, failure_label = _failure_details(manifest, spec.id)
    has_failure_kinds = bool(failure_details["counts_by_kind"])
    failure_note = f"；近期抓取失败类型：{failure_label}" if has_failure_kinds else ""

    def details(**extra: JsonValue) -> dict[str, JsonValue]:
        return {"recent_failure_summary": failure_details, **extra}

    alerts: list[Alert] = []
    source_runs = [run for run in runs if run.get("source_id") == spec.id]
    ordered_runs = sorted(
        (run for run in source_runs if _run_time(run, "started_at") is not None),
        key=lambda run: _run_time(run, "started_at"),
    )
    latest = ordered_runs[-consecutive_limit:]
    if len(latest) == consecutive_limit and all(
        run.get("status") != "completed" or run.get("exit_status") != 0 for run in latest
    ):
        since = _run_time(latest[0], "started_at") or now
        alerts.append(Alert(
            source_id=spec.id,
            rule="consecutive_failures",
            severity="error",
            message=(
                f"最近 {consecutive_limit} 次运行均失败："
                f"{failure_label if has_failure_kinds else 'Manifest 中没有可分类的失败快照'}。"
            ),
            since=since,
            details=details(count=consecutive_limit),
        ))

    if manifest is not None:
        recent_since = now - parse_recent_window
        baseline_since = now - parse_baseline_window
        recent_stats = manifest.parse_stats(spec.id, since=recent_since)
        all_stats = manifest.parse_stats(spec.id, since=baseline_since)
        recent_failed, recent_total = _rate(recent_stats, ParseStatus.failed)
        all_failed, all_total = _rate(all_stats, ParseStatus.failed)
        baseline_failed = max(0, all_failed - recent_failed)
        baseline_total = max(0, all_total - recent_total)
        if recent_total >= parse_min_snapshots and baseline_total:
            recent_rate = recent_failed / recent_total
            baseline_rate = baseline_failed / baseline_total
            increase = recent_rate - baseline_rate
            if increase >= parse_failure_rate_increase:
                alerts.append(Alert(
                    source_id=spec.id,
                    rule="parse_failure_rate",
                    severity="warning",
                    message=(
                        f"最近 {parse_recent_window.total_seconds() / 3600:g} 小时解析失败率为 {recent_rate:.0%}，"
                        f"比此前 {parse_baseline_window.total_seconds() / 86400:g} 天（不含最近窗口）高 "
                        f"{increase * 100:.0f} 个百分点{failure_note}。"
                    ),
                    since=recent_since,
                    details=details(
                        recent_failed=recent_failed,
                        recent_snapshots=recent_total,
                        recent_rate=recent_rate,
                        baseline_failed=baseline_failed,
                        baseline_snapshots=baseline_total,
                        baseline_rate=baseline_rate,
                        increase=increase,
                        increase_percentage_points=increase * 100,
                        threshold_percentage_points=parse_failure_rate_increase * 100,
                    ),
                ))

    successful_runs = [run for run in ordered_runs if _is_success(run)]
    successful_times = [
        timestamp
        for run in successful_runs
        if (timestamp := (_run_time(run, "ended_at") or _run_time(run, "started_at"))) is not None
    ]
    schedule_intervals = {
        "hourly": timedelta(hours=1),
        "daily": timedelta(hours=24),
        "monthly": timedelta(days=30),
    }
    interval = schedule_intervals.get(spec.schedule or "")
    if enabled and interval is not None:
        anchor = successful_times[-1] if successful_times else (
            _run_time(ordered_runs[0], "started_at") if ordered_runs else None
        )
        overdue_after = interval * overdue_cycles
        if anchor is not None and now - anchor > overdue_after:
            alerts.append(Alert(
                source_id=spec.id,
                rule="overdue",
                severity="warning",
                message=(
                    f"超过 {overdue_cycles} 个调度周期未成功运行；起算时间为 {anchor.isoformat()}"
                    f"{failure_note}。"
                ),
                since=anchor,
                details=details(
                    schedule=spec.schedule or "",
                    overdue_after_seconds=int(overdue_after.total_seconds()),
                    last_success_at=successful_times[-1].isoformat() if successful_times else None,
                ),
            ))

    short_retention = spec.scope.get("short_retention") is True
    if short_retention:
        points = list(zip(successful_times, successful_times[1:]))
        if enabled and successful_times:
            points.append((successful_times[-1], now))
        for start, end in points:
            if end - start <= retention_gap:
                continue
            alerts.append(Alert(
                source_id=spec.id,
                rule="retention_gap",
                severity="error",
                message=(
                    f"短保留来源在 {start.isoformat()} 至 {end.isoformat()} 出现超过 "
                    f"{retention_gap.total_seconds() / 3600:g} 小时缺口；缺口超过来源保留期后将无法补回"
                    f"{failure_note}。"
                ),
                since=start,
                details=details(
                    gap_start=start.isoformat(),
                    gap_end=end.isoformat(),
                    duration_seconds=int((end - start).total_seconds()),
                    retention_note=spec.retention_note,
                ),
            ))

    return alerts
