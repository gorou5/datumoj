"""对来源运行和解析状态计算提醒。"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
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
    rule: Literal[
        "consecutive_failures", "parse_failure_rate", "overdue", "retention_gap",
        "cloud_run_missing", "cloud_stalled", "replica_stale",
    ]
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
    return (
        run.get("state") != "interrupted"
        and run.get("status") == "completed"
        and run.get("exit_status") == 0
    )


def _rate(stats: dict, status: ParseStatus) -> tuple[int, int]:
    failed = stats.get(ParseStatus.failed, stats.get(ParseStatus.failed.value, 0))
    total = sum(stats.values())
    return int(failed), int(total)


def _schedule_interval(schedule: str | None) -> timedelta | None:
    # Lazy import avoids the schedule -> monitor dependency cycle.
    from datumoj.core.schedule import SCHEDULES

    entry = SCHEDULES.get(schedule or "")
    return entry[0] if entry else None


def _aware_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _ledger_successes(runs: Iterable[dict]) -> dict[str, dict[str, str]]:
    latest: dict[str, tuple[datetime, dict[str, str]]] = {}
    for run in runs:
        if (not isinstance(run, dict) or run.get("state") == "running"
                or run.get("status") != "completed" or run.get("exit_status") != 0):
            continue
        source_id = run.get("source_id")
        finished_at = _aware_datetime(run.get("ended_at"))
        run_id = run.get("run_id")
        if not isinstance(source_id, str) or not source_id or finished_at is None:
            continue
        if not isinstance(run_id, str) or not run_id:
            run_id = "unknown"
        previous = latest.get(source_id)
        if previous is None or finished_at > previous[0]:
            latest[source_id] = (
                finished_at, {"run_id": run_id, "finished_at": finished_at.isoformat()},
            )
    return {source_id: value[1] for source_id, value in latest.items()}


def _ledger_first_attempts(runs: Iterable[dict]) -> dict[str, str]:
    first: dict[str, datetime] = {}
    for run in runs:
        if not isinstance(run, dict):
            continue
        source_id = run.get("source_id")
        started_at = _aware_datetime(run.get("started_at"))
        if not isinstance(source_id, str) or not source_id or started_at is None:
            continue
        previous = first.get(source_id)
        if previous is None or started_at < previous:
            first[source_id] = started_at
    return {source_id: started_at.isoformat() for source_id, started_at in first.items()}


def _generation_successes(value: object) -> dict[str, dict[str, str]] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, dict[str, str]] = {}
    for source_id, run in value.items():
        if not isinstance(source_id, str) or not isinstance(run, dict):
            continue
        run_id = run.get("run_id")
        finished_at = _aware_datetime(run.get("finished_at"))
        if isinstance(run_id, str) and run_id and finished_at is not None:
            result[source_id] = {"run_id": run_id, "finished_at": finished_at.isoformat()}
    return result


def evaluate_cloud_alerts(
    sources: Iterable[SourceSpec],
    enabled_sources: set[str],
    last_successful_runs: Mapping[str, Mapping[str, str]],
    generation_created_at: datetime,
    now: datetime,
    *,
    generation: int | None = None,
    first_attempts: Mapping[str, str] | None = None,
) -> list[Alert]:
    """Alert on missed cloud source cycles and a stalled generation pointer."""
    if generation_created_at.utcoffset() is None or now.utcoffset() is None:
        raise ValueError("云端代次和当前时间必须带时区")

    specs = list(sources)
    scheduled = []
    for spec in specs:
        interval = _schedule_interval(spec.schedule)
        if spec.id in enabled_sources and interval is not None:
            scheduled.append((spec, interval))
    alerts: list[Alert] = []
    for spec, interval in scheduled:
        run = last_successful_runs.get(spec.id)
        anchor = _aware_datetime(run.get("finished_at")) if run is not None else None
        first_attempt = _aware_datetime(first_attempts.get(spec.id)) if first_attempts else None
        anchor = anchor or first_attempt or generation_created_at
        if now - anchor > interval:
            alerts.append(Alert(
                source_id=spec.id,
                rule="cloud_run_missing",
                severity="warning",
                message=f"超过一个 {spec.schedule} 调度周期没有成功运行。",
                since=anchor,
                details={
                    "schedule": spec.schedule or "",
                    "expected_after_seconds": int(interval.total_seconds()),
                    "last_success_at": anchor.isoformat() if run is not None else None,
                    "first_attempt_at": first_attempt.isoformat() if run is None and first_attempt else None,
                    "generation": generation,
                },
            ))

    stall_after = timedelta(hours=2)
    if now - generation_created_at > stall_after:
        alerts.append(Alert(
            source_id="cloud",
            rule="cloud_stalled",
            severity="error",
            message="云端状态代次超过预期时间没有更新。",
            since=generation_created_at,
            details={
                "stalled_after_seconds": int(stall_after.total_seconds()),
                "generation": generation,
            },
        ))
    return alerts


def attach_cloud_status(
    status: dict,
    data_dir: str | Path,
    sources: Iterable[SourceSpec],
    runs: Iterable[dict],
    now: datetime,
) -> dict:
    """Add generation metadata and cloud-gap alerts for a pulled local replica."""
    marker = Path(data_dir) / "state-pull.json"
    if marker.is_symlink() or not marker.is_file():
        return status
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
        generation = value.get("generation")
        created_at = _aware_datetime(value.get("created_at"))
        pulled_at = _aware_datetime(value.get("pulled_at"))
    except (OSError, UnicodeError, ValueError, AttributeError):
        return status
    if type(generation) is not int or generation < 1 or created_at is None:
        return status
    if now.utcoffset() is None:
        raise ValueError("云端监控当前时间必须带时区")

    last_successful = _ledger_successes(runs)
    generation_runs = _generation_successes(value.get("last_successful_runs"))
    if generation_runs is not None:
        last_successful.update(generation_runs)
    first_attempts = _ledger_first_attempts(runs)
    raw_writer = value.get("writer")
    writer_kind = raw_writer.get("kind") if isinstance(raw_writer, dict) else None
    writer = {
        "kind": writer_kind if isinstance(writer_kind, str) and writer_kind in {"actions", "local", "nas"}
        else "unknown",
    }
    enabled_sources = {
        row["source_id"] for row in status.get("sources", []) if row.get("effective_enabled")
    }
    if pulled_at is None or now - pulled_at > timedelta(hours=2):
        alerts = [Alert(
            source_id="cloud", rule="replica_stale", severity="warning",
            message="本机云端副本超过两小时未更新，请先执行 datumoj pull。",
            since=pulled_at or created_at,
            details={
                "pulled_at": pulled_at.isoformat() if pulled_at else None,
                "stale_after_seconds": 7200,
            },
        )]
    else:
        alerts = evaluate_cloud_alerts(
            sources, enabled_sources, last_successful, created_at, now, generation=generation,
            first_attempts=first_attempts,
        )
    source_rows = {row["source_id"]: row for row in status.get("sources", [])}
    global_alerts = []
    for alert in alerts:
        serialized = alert.model_dump(mode="json")
        row = source_rows.get(alert.source_id)
        if row is None:
            global_alerts.append(serialized)
        else:
            row.setdefault("alerts", []).append(serialized)
    for row in status.get("sources", []):
        success = last_successful.get(row["source_id"])
        row["last_successful_run"] = {"finished_at": success["finished_at"]} if success else None
    cloud_successes: dict[str, dict[str, str] | None] = {
        source_id: {"finished_at": run["finished_at"]}
        for source_id, run in last_successful.items()
    }
    for row in status.get("sources", []):
        cloud_successes.setdefault(row["source_id"], None)
    status["cloud"] = {
        "generation": generation,
        "created_at": created_at.isoformat(),
        "pulled_at": pulled_at.isoformat() if pulled_at else None,
        "status_basis": "以上次 pull 时的远端状态为准",
        "writer": writer,
        "last_successful_runs": cloud_successes,
    }
    status["cloud_alerts"] = global_alerts
    status["alert_count"] = sum(len(row.get("alerts", [])) for row in status.get("sources", [])) + len(global_alerts)
    return status


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
    source_runs = [
        run for run in runs
        if run.get("source_id") == spec.id
        and run.get("state") != "running"
        and run.get("status") != "running"
    ]
    ordered_runs = sorted(
        (run for run in source_runs if _run_time(run, "started_at") is not None),
        key=lambda run: _run_time(run, "started_at"),
    )
    latest = ordered_runs[-consecutive_limit:]
    if len(latest) == consecutive_limit and all(not _is_success(run) for run in latest):
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
    interval = _schedule_interval(spec.schedule)
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
