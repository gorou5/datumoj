"""One leased cloud round. Public output contains only IDs, counts and fixed reasons."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout, suppress
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

from datumoj.rikolto import wiring
from datumoj.rikolto.analysis.reports import previous_week_period, weekly_report
from datumoj.rikolto.capacity import LIMITS, WEEKLY_REPORT_RESERVE, gate
from datumoj.rikolto.core.budget import ParseBudget
from datumoj.rikolto.core.contracts import PreconditionFailed, StateObject
from datumoj.rikolto.core.http import PoliteHttpFetcher
from datumoj.rikolto.core.lease import LEASE_KEY, LeaseError, LeaseManager
from datumoj.rikolto.core.models import FetchOutcome, PublishState
from datumoj.rikolto.core.monitor import _ledger_first_attempts, evaluate_cloud_alerts
from datumoj.rikolto.core.nas_receipt import read_receipt
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.core.runner import DEFAULT_MAX_RUN_SECONDS
from datumoj.rikolto.core.schedule import Scheduler, utcnow, workspace_lock, write_control
from datumoj.rikolto.core.state import StateManager, local_write
from datumoj.rikolto.core.state_s3 import S3StateObjectStore, probe_conditional_writes
from datumoj.rikolto.index.vector import maintain_vectors
from datumoj.rikolto.pipeline import (
    VECTOR_FAILURE_LIMIT,
    ParseSummary,
    _load_vector_state,
    _recover_staged,
    _retry_vectors,
    _start_vectors,
    backfill_projections,
    projection_counts,
)

_BACKFILL_BATCH = 32
_MAX_REQUEST_SECONDS = 300
_PIPELINE_GRACE_SECONDS = 600
_MISSING_AFTER = timedelta(hours=24)


@contextmanager
def _quiet():
    # SDK debug logs, parser tracebacks and plugin stdout may contain source text
    # or configuration. Suppress them, including heartbeat-thread logging.
    previous = logging.root.manager.disable
    with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
        logging.disable(sys.maxsize)
        try:
            yield
        finally:
            logging.disable(previous)


def _progress(event, started, record=None):
    record = record or {}
    if "embedded" in record:
        row = {key: record[key] for key in (
            "embedded", "vector_pending", "embedding_deferred", "embedding_failed",
        )}
        row.update(event=event, elapsed_s=round(time.monotonic() - started, 3))
        for key in ("publish_timings", "restore_timings"):
            row[key] = record.get(key, {})
        print(json.dumps(row), file=sys.__stderr__, flush=True)
        return
    failures = dict(record.get("failures", {}))
    for source in record.get("sources", []):
        for reason, count in source["failures"].items():
            failures[reason] = failures.get(reason, 0) + count
    if "failed_sources" in record:
        failures = {}
        for source in record["failed_sources"]:
            for reason, count in source["reasons"].items():
                failures[reason] = failures.get(reason, 0) + count
    row = {key: record.get(key, 0) for key in ("new", "unchanged", "parsed")}
    row.update(event=event, source_id=record.get("source_id"),
               elapsed_s=round(time.monotonic() - started, 3),
               failures=failures, stop_reason=record.get("stop_reason"))
    for key in ("publish_timings", "restore_timings"):
        row[key] = record.get(key, {})
    for key in ("embedding_deferred", "backfill_embedded", "vector_pending"):
        if record.get(key) or event in {"backfill_start", "backfill_end"}:
            row[key] = record.get(key, 0)
    print(json.dumps(row, ensure_ascii=False), file=sys.__stderr__, flush=True)


_TIMING_FIELDS = frozenset({
    'total', 'cleanup', 'prepare', 'stage', 'validate', 'upload_copies', 'upload_raw',
    'sample_provenance', 'write_generation', 'publish', 'download_copies', 'download_raw',
    'downloaded_bytes', 'reused_bytes', 'post_publish', 'post_publish_inventory',
    'post_publish_sample_provenance', 'post_publish_retain',
})


def _safe_timings(values):
    return {key: value for key, value in values.items()
            if key in _TIMING_FIELDS and type(value) in (int, float) and math.isfinite(value) and value >= 0}


def _maintenance_failure(operation, exc):
    """Describe a failed boundary using fixed codes, never exception text or names."""
    if operation not in {"local_write_enter", "maintain_vectors", "local_write_exit", "lease_check"}:
        operation = "unknown"
    category = next((name for kind, name in (
        (LeaseError, "lease_error"), (OSError, "os_error"), (ValueError, "value_error"),
        (LookupError, "lookup_error"), (RuntimeError, "runtime_error"), (MemoryError, "memory_error"),
    ) if isinstance(exc, kind)), "unexpected_error")
    details = {"phase": "maintenance", "operation": operation, "error_category": category}
    print(json.dumps({"event": "maintenance_failed", **details}), file=sys.__stderr__, flush=True)
    return details


def _throttled_check(check, *, monotonic=time.monotonic):
    """Check at most once per 30s for immutable uploads, shared by transfer threads."""
    last = None
    lock = Lock()

    def throttled():
        nonlocal last
        with lock:
            now = monotonic()
            if last is None or now - last >= 30:
                check()
                last = now

    return throttled


def _due(scheduler, root):
    regular = set(scheduler.due())
    if not (root / "runs.jsonl").exists():
        return sorted(regular)  # All due sources have never run.
    sources = {source["source_id"]: source for source in scheduler.status()["sources"]}
    due = regular.copy()
    # Core schedules every attempt, even incomplete ones. Cloud must retry
    # a deadline without waiting another interval; retain the real times.
    for source in sources.values():
        last = source["last_run"]
        if (source["effective_enabled"] and source["next_due"] is not None
                and last and last.get("stop_reason") == "deadline"):
            due.add(source["source_id"])

    def priority(source_id):
        last = sources[source_id]["last_run"]
        if last is None:
            return (0, 0, source_id)
        return (1 if source_id in regular else 2,
                datetime.fromisoformat(last["started_at"]).timestamp(), source_id)

    return sorted(due, key=priority)


def _source(deps, spec, scheduler, remaining, started=None, deadline=None):
    started = time.monotonic() if started is None else started
    crawl_started = time.monotonic()
    record = {
        "source_id": spec.id, "run_id": uuid.uuid4().hex, "started_at": utcnow().isoformat(),
        "ended_at": None, "state": "running", "status": "running", "exit_status": 0,
        "new": 0, "unchanged": 0, "parsed": 0, "failures": {}, "stop_reason": None, "embedding_deferred": 0,
    }
    scheduler.append_run(record)
    health = {"source_id": spec.id, "status": "ok", "phase": None, "categories": {},
              "consecutive_failures": 0, "first_failure_at": None}
    deadline_phase = None
    crashed = False
    crawl_newly_failed = parse_newly_failed = 0
    phase = "crawl"
    try:
        previous = deps.manifest.snapshots_for(spec.id)
        seen = {snap.snapshot_id for snap in previous}
        failed_urls = {snap.resource_url for snap in previous
                       if snap.outcome not in {FetchOutcome.ok, FetchOutcome.not_modified}}
        crawler = deps.crawler_classes[spec.kind]()
        fetcher = deps.fetcher_for(spec)
        max_run = spec.scope.get("max_run_seconds", DEFAULT_MAX_RUN_SECONDS)
        if (isinstance(fetcher, PoliteHttpFetcher) and type(max_run) in (int, float)
                and math.isfinite(max_run) and max_run > 0):
            fetcher.max_request_seconds = min(
                _MAX_REQUEST_SECONDS, max(0, max_run - (time.monotonic() - crawl_started)),
            )
        try:
            report = deps.run_crawl(spec, crawler, deps.crawl_deps_type(
                raw=deps.raw, manifest=deps.manifest, fetcher=fetcher,
            ))
        finally:
            close = getattr(fetcher, "close", None)
            if close is not None:
                close()
        record.update(new=report.new, unchanged=report.unchanged,
                      stop_reason="deadline" if report.stop_reason == "deadline" else None)
        crashed = any(reason in report.failures for reason in ("plan_error", "after_fetch_error"))
        if report.failures:
            record["failures"]["crawl_failed"] = sum(report.failures.values())
            repeated = sum(snap.resource_url in failed_urls for snap in deps.manifest.snapshots_for(spec.id)
                           if snap.snapshot_id not in seen
                           and snap.outcome not in {FetchOutcome.ok, FetchOutcome.not_modified})
            crawl_newly_failed = max(0, record["failures"]["crawl_failed"] - repeated)
            for category, count in report.failures.items():
                category = category if category in {outcome.value for outcome in FetchOutcome} else "other"
                _health_failure(health, phase, category, count)
        if record["stop_reason"] == "deadline":
            deadline_phase = phase
        _progress("crawl_end", started, record)
        phase = "parse_index"
        pipeline = replace(deps.pipeline_deps, vector_limit=remaining, deadline=deadline, clock=time.monotonic)
        before = _failure_counts(pipeline)
        summary = deps.parse_pending(pipeline, spec.id)
        remaining = summary.vector_remaining
        record["parsed"] = len(summary.parsed)
        if summary.stop_reason == "deadline":
            record["stop_reason"] = "deadline"
            deadline_phase = phase
        # parse_pending also scans staged publications from other sources.
        # Their recovery errors must not turn this source's success into failure.
        def belongs(snapshot_id):
            snapshot = deps.manifest.get_snapshot(snapshot_id)
            return snapshot is None or snapshot.source_id == spec.id

        cached_failures = set(summary.cached_failures)
        parse_failures = {key: reason for key, reason in summary.failed.items()
                          if key not in cached_failures and belongs(key) and _error_category(reason) != "empty_content"}
        failed = len(parse_failures) + len(summary.no_parser)
        parse_newly_failed = sum(key in parse_failures for key in summary.newly_failed) + len(summary.no_parser)
        if failed:
            record["failures"]["parse_index_failed"] = failed
        for reason in parse_failures.values():
            _health_failure(health, phase, _error_category(reason))
        if summary.no_parser:
            _health_failure(health, phase, "other", len(summary.no_parser))
        deferred, failed = _embedding_counts(pipeline, before, spec.id)
        if deferred:
            record["embedding_deferred"] = deferred
        if failed:
            record["failures"]["embedding_failed"] = failed
            _health_failure(health, "embedding", "embedding_failed", failed)
        _progress("parse_end", started, record)
    except Exception as exc:  # noqa: BLE001 -- 单来源失败需隔离，保留本轮其他来源的执行。
        crashed = True
        record["failures"][f"{phase}_failed"] = record["failures"].get(f"{phase}_failed", 0) + 1
        reason = type(exc).__name__
        if reason == "ParseError" and len(exc.args) == 1 and type(exc.args[0]) is str:
            reason += ": " + exc.args[0]
        _health_failure(health, phase, _error_category(reason))
        # An interrupted parser cannot report its remaining allowance. Reserve
        # the rest so later sources cannot exceed the round's embedding budget.
        if phase == "parse_index":
            remaining = 0
    successes = record["new"] + record["unchanged"] + record["parsed"]
    # F / (successes_in_phase + F) > 1/2 iff F > successes_in_phase; old failures are excluded.
    failed = crashed or bool(record["failures"] and (
        successes == 0
        or crawl_newly_failed > record["new"] + record["unchanged"]
        or parse_newly_failed > record["parsed"]
    ))
    status = "failed" if failed else "degraded" if record["failures"] else "completed"
    record.update(state="final", ended_at=utcnow().isoformat(), status=status,
                  exit_status=int(failed),
                  stop_reason=record["stop_reason"] or ("source_failed" if failed else None))
    health["status"] = "failed" if failed else "degraded" if record["failures"] else "ok"
    # Preserve result status/exit semantics, but never advertise a partial crawl
    # as a successful run in the published generation's last_successful_runs.
    ledger = record
    if status == "degraded":
        # StateManager's existing successful-run contract requires completed and
        # an empty failures map. Retain item counts in the same ledger row.
        ledger = {**record, "status": "completed", "source_status": status,
                  "failures": {}, "item_failures": record["failures"]}
    if record["stop_reason"] == "deadline":
        ledger = {**ledger, "status": "incomplete"}
    scheduler.append_run(ledger)
    if not record["failures"] and deadline_phase is not None:
        health.update(status="deadline", phase=deadline_phase)
    # The extra value is consumed by _round, never written into the ledger or
    # the existing sources result. Only source_health exposes the new summary.
    return {**record, "source_health": health}, remaining


def _error_category(reason):
    name, separator, detail = reason.partition(":")
    if name == "ParseError":
        code, separator, suffix = detail.strip().partition(":")
        return code if separator and not suffix.startswith("//") and re.fullmatch(r"[a-z_]{1,40}", code) else "other"
    if name == "budget_exceeded":
        return "BudgetExceeded"  # safe_reason uses this fixed prefix instead of the class name.
    return name if not detail.startswith("//") and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", name) else "other"


def _health_failure(health, phase, category, count=1):
    health["status"] = "failed"
    health["phase"] = health["phase"] or phase
    health["categories"][category] = health["categories"].get(category, 0) + count


def _final_runs(history, source_id):
    # Collapse duplicate final records by run ID; running records never count.
    # Legacy rows without state/run_id represent one completed attempt each.
    final = {row.get("run_id", index): (index, row) for index, row in enumerate(history)
             if row["source_id"] == source_id and row.get("state", "final") == "final"}
    ordered = sorted(final.values(), key=lambda item: (
        datetime.fromisoformat(item[1]["started_at"]).timestamp(), item[0],
    ), reverse=True)
    return [row for _, row in ordered]


def _failure_streak(history, source_id):
    count, first = 0, None
    for row in _final_runs(history, source_id):
        if row.get("source_status") == "degraded" or row["status"] == "degraded":
            break
        if row.get("stop_reason") == "deadline" and not row.get("failures"):
            break
        if not (row.get("failures") or row["exit_status"] or row["status"] == "failed"):
            break  # Includes deadline without errors: neither failure nor success.
        count += 1
        first = row["started_at"]
    return count, first


def _source_health(scheduler, current, generation=None):
    # Older callers can replace the scheduler/source executor without exposing
    # a ledger. Do not invent health evidence for those adapters.
    if not hasattr(scheduler, "_read_history"):
        return current
    history = scheduler._read_history()[0]
    health = {row["source_id"]: dict(row) for row in current}
    if generation is not None:
        source_status = scheduler.status()["sources"]
        enabled = {row["source_id"] for row in source_status if row["effective_enabled"]}
        enabled_since = {row["source_id"]: row["enabled_since"] for row in source_status if row.get("enabled_since")}
        successes = {key: {"finished_at": run.finished_at.isoformat()}
                     for key, run in generation.last_successful_runs.items()}
        alerts = evaluate_cloud_alerts(
            scheduler.sources.values(), enabled, successes, generation.created_at, utcnow(),
            generation=generation.generation, first_attempts=_ledger_first_attempts(history),
            enabled_since=enabled_since,
        )
        for alert in alerts:
            if alert.rule != "cloud_run_missing":
                continue
            if health.get(alert.source_id, {}).get("status") in {"failed", "deadline"}:
                continue
            runs = _final_runs(history, alert.source_id)
            # Backlogged sources overrun their schedule while still getting turns;
            # only a source without an attempt for this long is really missing.
            last = datetime.fromisoformat(runs[0]["started_at"]) if runs else alert.since
            if alert.source_id in enabled_since:
                last = max(last, datetime.fromisoformat(enabled_since[alert.source_id]))
            if utcnow() - last < _MISSING_AFTER:
                continue
            row = health.setdefault(alert.source_id, {
                "source_id": alert.source_id, "categories": {},
                "consecutive_failures": 0, "first_failure_at": None,
            })
            row.update(status="missing", phase="schedule")
            # Not run this round: keep the last failure's evidence for the issue.
            if not row["categories"] and runs and runs[0].get("failures"):
                row["categories"] = dict(runs[0]["failures"])
                row["phase"] = next(iter(runs[0]["failures"])).removesuffix("_failed")
    for row in health.values():
        row["consecutive_failures"], row["first_failure_at"] = _failure_streak(history, row["source_id"])
    return list(health.values())


def _failure_counts(pipeline):
    return {key: item.get("failures", 0) for key, item in _load_vector_state(pipeline)["missing"].items()}


def _embedding_counts(pipeline, before, source_id):
    deferred = failed = 0
    for key, item in _load_vector_state(pipeline)["missing"].items():
        count = item.get("failures", 0)
        if count <= before.get(key, 0):
            continue
        snapshot = pipeline.manifest.get_snapshot(item.get("snapshot_id"))
        belongs = snapshot.source_id == source_id if snapshot is not None else key.startswith(source_id + ":")
        if belongs:
            failed += int(before.get(key, 0) < VECTOR_FAILURE_LIMIT <= count)
            deferred += int(count < VECTOR_FAILURE_LIMIT)
    return deferred, failed


def _backfill(deps, remaining, started, max_minutes, check, result, before_round, *, embed_only=False):
    pipeline = getattr(deps, "pipeline_deps", None)
    if pipeline is None:
        return remaining
    pipeline = replace(pipeline, deadline=started + max_minutes * 60 + _PIPELINE_GRACE_SECONDS, clock=time.monotonic)
    # An error gets another attempt next round, never a tight loop this round.
    exclude = {key for key, count in _failure_counts(pipeline).items() if count > before_round.get(key, 0)}
    announced = False
    minimum_seconds = 60 if embed_only else 120
    if embed_only:
        result["vector_pending"] = sum(item["chunks"] for item in _load_vector_state(pipeline)["missing"].values())
        _progress("embed_start", started, result)
    while (pipeline.vectors is not None and (remaining is None or remaining > 0)
           and (embed_only or max_minutes * 60 - (time.monotonic() - started) >= minimum_seconds)
           and any(key not in exclude and item.get("failures", 0) < VECTOR_FAILURE_LIMIT
                   for key, item in _load_vector_state(pipeline)["missing"].items())):
        check()
        if max_minutes * 60 - (time.monotonic() - started) < minimum_seconds:
            if embed_only:
                result["budget_exhausted"] = True
            break
        if not announced and not embed_only:
            _progress("backfill_start", started, result)
            announced = True
        pipeline.vector_limit = _BACKFILL_BATCH if remaining is None else min(_BACKFILL_BATCH, remaining)
        _start_vectors(pipeline)
        before = _failure_counts(pipeline)
        with local_write(deps.data_dir):
            embedded = _retry_vectors(pipeline, None, ParseSummary(), exclude=exclude)
        used = pipeline.vector_limit - pipeline._vector_remaining
        if remaining is not None:
            remaining -= used
        result["embedded" if embed_only else "backfill_embedded"] += embedded
        for spec in deps.registry.all():
            deferred, failed = _embedding_counts(pipeline, before, spec.id)
            result["embedding_deferred"] += deferred
            if embed_only:
                result["embedding_failed"] += failed
            elif failed:
                existing = next((row for row in result["failed_sources"] if row["source_id"] == spec.id), None)
                if existing is None:
                    existing = {"source_id": spec.id, "reasons": {}}
                    result["failed_sources"].append(existing)
                reasons = existing["reasons"]
                reasons["embedding_failed"] = reasons.get("embedding_failed", 0) + failed
        exclude.update(key for key, count in _failure_counts(pipeline).items() if count > before.get(key, 0))
        if embed_only:
            result["vector_pending"] = sum(item["chunks"] for item in _load_vector_state(pipeline)["missing"].values())
            _progress("embed_batch", started, result)
        if not used:
            break
    result["vector_pending"] = sum(item["chunks"] for item in _load_vector_state(pipeline)["missing"].values())
    if announced:
        _progress("backfill_end", started, result)
    return remaining


def _recover(deps, remaining, deadline=None):
    """Use 107's recovery scan, without starting parses for sources not due."""
    pipeline = replace(deps.pipeline_deps, vector_limit=remaining, deadline=deadline, clock=time.monotonic)
    pipeline._vector_remaining = remaining
    specs = {spec.id: spec for spec in deps.registry.all()}
    parsers = pipeline.parsers if pipeline.parsers is not None else wiring._connected_parsers()
    summary = ParseSummary()
    _recover_staged(pipeline, parsers, specs,
                    {key: ParseBudget.from_scope(spec.scope) for key, spec in specs.items()}, summary)
    if deps.manifest.snapshots_in_publish_state(PublishState.staged):
        raise RuntimeError("staged_recovery_failed")
    return pipeline._vector_remaining


def _weekly_report_bytes(content: str) -> bytes:
    data = content.encode("utf-8")
    if len(data) <= WEEKLY_REPORT_RESERVE:
        return data
    lines = content.splitlines(keepends=True)
    # weekly_report renders each event on one line, with an optional title.
    events = [line.startswith(("- 摘要：", "- 标题：")) for line in lines]
    omitted = sum(events)

    def notice(count):
        return f"\n\n报告超过 1 MB，后续内容已省略；省略 {count} 条事件。\n".encode()

    budget = WEEKLY_REPORT_RESERVE - len(notice(omitted))
    kept = bytearray()
    for line, event in zip(lines, events, strict=True):
        encoded = line.encode("utf-8")
        if len(kept) + len(encoded) > budget:
            break
        kept.extend(encoded)
        omitted -= event
    return bytes(kept) + notice(omitted)


def publish_previous_week_report(store, deps, now: datetime, generation=None) -> str:
    """Publish the previous ISO week's report once, using an immutable key."""
    report_at = generation.created_at if generation is not None else now
    start, end, week_key = previous_week_period(report_at.date())
    object_key = f"reports/weekly/{week_key}.md"
    if store.get(object_key) is None:
        scheduler = Scheduler(
            deps.data_dir, deps.registry.all(), lambda spec: None, manifest=deps.manifest,
        )
        scheduler.now = lambda: report_at
        status = scheduler.status()
        alerts = [alert for source in status["sources"] for alert in source["alerts"]]
        if generation is not None:
            enabled = {
                row["source_id"] for row in status["sources"] if row.get("effective_enabled")
            }
            last_successful = {
                source_id: {"finished_at": run.finished_at.isoformat()}
                for source_id, run in generation.last_successful_runs.items()
            }
            first_attempts = _ledger_first_attempts(scheduler._read_history()[0])
            alerts.extend(evaluate_cloud_alerts(
                deps.registry.all(), enabled, last_successful,
                generation.created_at, report_at, generation=generation.generation,
                first_attempts=first_attempts,
                enabled_since={row["source_id"]: row["enabled_since"] for row in status["sources"]
                               if row.get("enabled_since")},
            ))
        content = weekly_report(deps, start, end, alerts)
        try:
            store.put_if_absent(object_key, _weekly_report_bytes(content))
        except PreconditionFailed:
            # Another successful round won the same weekly key.
            pass
    return object_key


def check() -> dict:
    """Check storage without opening local data stores or acquiring a lease."""
    with _quiet(), tempfile.TemporaryDirectory(prefix="datumoj-check-") as directory:
        try:
            manager = wiring.build_state(Path(directory))
        except Exception:  # noqa: BLE001 -- 任意配置失败均返回固定检查结果。
            return {"status": "failed", "exit_status": 1,
                    "reason": "configuration_failed", "phase": "configuration"}
        probe = probe_conditional_writes(manager.store)
        return {"status": "completed" if probe["supported"] else "failed",
                "exit_status": 0 if probe["supported"] else 1,
                "reason": None if probe["supported"] else "storage_conditional_unsupported",
                "phase": "storage_probe", "storage_probe": probe}


def run(*, work_dir: Path | None = None, max_embed: int = 1000, max_minutes: float = 40,
        reserve_minutes: float = 10, no_cleanup: bool = False) -> dict:
    return _round(work_dir=work_dir, max_embed=max_embed, max_minutes=max_minutes,
                  reserve_minutes=reserve_minutes, embed_only=False, no_cleanup=no_cleanup)


def embed(*, work_dir: Path | None = None, max_minutes: float,
          reserve_minutes: float = 10, max_embed: int | None = None, no_cleanup: bool = False) -> dict:
    """Embed existing backlog in bounded batches, publishing only successful work."""
    return _round(work_dir=work_dir, max_embed=max_embed, max_minutes=max_minutes,
                  reserve_minutes=reserve_minutes, embed_only=True, no_cleanup=no_cleanup)


def _empty_work_root(work_dir, stack):
    root = Path(work_dir) if work_dir is not None else Path(stack.enter_context(
        tempfile.TemporaryDirectory(prefix="datumoj-cloud-")
    ))
    if root.is_symlink() or (root.exists() and (not root.is_dir() or any(root.iterdir()))):
        raise ValueError("workspace_not_empty")
    return root


def _project_storage_bytes(store):
    scoped, other = _usage_objects(store)
    sizes = [obj.size_bytes for obj in (*scoped, *other)]
    if any(type(size) is not int or size < 0 for size in sizes):
        raise ValueError("invalid_capacity_size")
    return sum(sizes)


def _round_capacity(manager, *, embed_only=False, weekly_report=False):
    current = estimate = None
    try:
        current = _project_storage_bytes(manager.store)
        # Embedding republishes local stores. Budget a complete replacement of
        # the current stores, without counting checksum-only SQLite descriptors.
        if weekly_report:
            estimate = WEEKLY_REPORT_RESERVE
        else:
            history = manager.list() if embed_only else []
            estimate = sum(copy.size_bytes for copy in history[0].stores
                           if copy.format != "sqlite-blocks-v1") if history else 0
    except Exception:  # noqa: BLE001 -- An unavailable inventory/estimate must fail closed.
        estimate = None
    return gate(current, estimate, "optional" if embed_only or weekly_report else "essential",
                reserve_bytes=0 if embed_only or weekly_report else LIMITS.crawl_reserve)


def _report_capacity(capacity):
    if capacity["tier"] != "normal":
        print(json.dumps({"event": "capacity", **capacity}), file=sys.__stderr__, flush=True)


class _RawUploads:
    """Count successful new raw content through the existing RawStore contract."""

    def __init__(self, raw):
        self.raw = raw
        self.bytes = 0

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def get(self, sha256):
        return self.raw.get(sha256)

    def open(self, sha256):
        return self.raw.open(sha256)

    def exists(self, sha256):
        return self.raw.exists(sha256)

    def verify(self, sha256):
        return self.raw.verify(sha256)

    def put(self, data):
        if not isinstance(data, bytes):
            # Preserve streaming and caller ownership, including non-seekable inputs.
            with tempfile.TemporaryFile() as spool:
                digest = hashlib.sha256()
                while block := data.read(1 << 20):
                    digest.update(block)
                    spool.write(block)
                return self._put(spool, digest.hexdigest(), spool.tell())
        return self._put(data, hashlib.sha256(data).hexdigest(), len(data))

    def _put(self, data, sha256, size):
        from datumoj.rikolto.core.rawstore_s3 import S3RawStore

        if not isinstance(data, bytes):
            data.seek(0)
        if isinstance(self.raw, S3RawStore):
            result, created = self.raw.put_with_status(data)
        else:
            existed = self.raw.exists(sha256)
            result = self.raw.put(data)
            created = not existed
        if created:
            self.bytes += size
        return result


def _next_round(result):
    if result.get("cleanup", {}).get("total_bytes", 0) >= LIMITS.stop:
        result["capacity"]["next_round"] = "cleanup_only"


def _usage_objects(store):
    if not isinstance(store, S3StateObjectStore):
        return store.list_objects(""), []
    # Capacity covers this whole bucket, including data outside the configured
    # namespace. Never enumerate other buckets (the read token need not allow it).
    base = store._prefix + "/" if store._prefix else ""
    scoped, other = [], []
    for page in store._client.get_paginator("list_objects_v2").paginate(Bucket=store._bucket):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.startswith(base):
                scoped.append(StateObject(key[len(base):], obj["Size"], obj["ETag"]))
            else:
                other.append(StateObject(key, obj["Size"], obj["ETag"]))
    return scoped, other


def _usage_manifest(manager, generation, path):
    copy = next(c for c in generation.stores if c.name == "manifest.sqlite")
    _usage_copy(manager, generation, copy, path)


def _usage_copy(manager, generation, copy, path):
    from datumoj.rikolto.core.state import _block_groups, _digest

    parts = _block_groups(generation.stores).get(copy.name, [copy])
    # The assembled SQLite checksum entry is virtual, never an object GET.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        for part in parts:
            found = manager.store.get(part.key)
            if found is None or len(found[0]) != part.size_bytes or _digest(found[0]) != part.sha256:
                raise ValueError("invalid_usage_copy")
            stream.write(found[0])
    with path.open("rb") as stream:
        if path.stat().st_size != copy.size_bytes or hashlib.file_digest(stream, "sha256").hexdigest() != copy.sha256:
            raise ValueError("invalid_usage_copy")


def _usage_databases(manager, generation, root, now):
    from datumoj.rikolto.db_usage import measure_usage

    paths = {"manifest": root / "manifest.sqlite"}
    for copy in generation.stores:
        if copy.name in {"chunks.sqlite", "changes.sqlite", "catalog.sqlite"}:
            path = root / copy.name
            _usage_copy(manager, generation, copy, path)
            paths[copy.name.removesuffix(".sqlite")] = path
        elif copy.name.startswith("vectors/") and copy.name != "vectors/vectors-state.json":
            path = root / copy.name
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("invalid_usage_path")
            _usage_copy(manager, generation, copy, path)
            paths["vectors"] = root / "vectors"
    return measure_usage(**paths, now=now)


def usage(*, work_dir: Path | None = None, db: bool = False) -> dict:
    """Inventory this bucket without leases, probes, restore, writes or deletes.

    Optionally measures current database copies in a disposable directory. All errors
    cross this boundary as fixed phase/reason codes, never SDK or source text.
    """
    from botocore.exceptions import ClientError

    from datumoj.rikolto.capacity import inventory

    phase = "configuration"
    with ExitStack() as stack, _quiet():
        try:
            root = _empty_work_root(work_dir, stack)
            manager = wiring.build_state(root)
            phase = "list"
            objects, other = _usage_objects(manager.store)
            phase = "generations"
            current, etag = manager._current()
            generations = [manager._generation(int(match[1])) for obj in objects
                           if (match := re.fullmatch(r"generations/([1-9][0-9]*)\.json", obj.key))]
            generation = next((g for g in generations if g.generation == current), None)
            if generation is None:
                raise ValueError("current_missing")
            if any(g.generation > current for g in generations):
                raise ValueError("orphan_generation")
            phase = "manifest"
            root.mkdir(parents=True, exist_ok=True)
            if db:
                root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="db-usage-", dir=root)))
            manifest = root / "manifest.sqlite"
            _usage_manifest(manager, generation, manifest)
            phase = "inventory"
            specs = load_registry(Path(__file__).resolve().parents[3] / "sources").all()
            now = utcnow()
            _, receipt = read_receipt(manager.store, now=now)
            result = inventory(objects, generations, current, manifest, specs, now=now,
                               raw_enabled=os.environ.get("R2_RAW_CLEANUP", "off").lower() == "on",
                               other_objects=other, receipt=receipt,
                               retained_refs=manager.generation_raw_references)
            if db:
                phase = "db"
                result["db"] = _usage_databases(manager, generation, root, utcnow())
                phase = "inventory"
            if manager._current() != (current, etag):
                raise ValueError("generation_changed")
            return result
        except Exception as exc:  # noqa: BLE001 -- Only fixed reasons leave the read-only cloud boundary.
            reason = f"{phase}_failed"
            if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in {
                "AccessDenied", "Forbidden", "403", "AllAccessDisabled",
            }:
                reason = f"{phase}_denied"
            elif isinstance(exc, ValueError) and str(exc) in {
                "workspace_not_empty", "current_missing", "orphan_generation", "generation_changed",
            }:
                reason = str(exc)
            return {"exit_status": 1, "phase": phase, "reason": reason}


def _projection_estimate(pipeline):
    sources = projection_counts(pipeline)
    count = size = 0
    for chunk in pipeline.chunks.iter_chunks():
        count += 1
        size += len(chunk.model_dump_json().encode())
    average = math.ceil(size / count) if count else 0
    new = sum(row["new_chunks"] for row in sources.values())
    # An empty legacy block store has no empirical average. Use actual projected
    # JSON sizes in that case, rather than claiming that new blocks cost zero.
    if not count and new:
        from datumoj.rikolto.index.projections import project as project_blocks
        from datumoj.rikolto.pipeline import _projection_batches

        size = sum(len(chunk.model_dump_json().encode())
                   for _, datasets, rows in _projection_batches(pipeline)
                   for _, chunks in project_blocks(datasets, rows) for chunk in chunks)
        average = math.ceil(size / new)
    return {"sources": sources, "new_chunks": new, "existing_chunks": count,
            "average_chunk_bytes": average, "estimated_new_bytes": new * average}


def project(*, work_dir: Path | None = None, write: bool = False) -> dict:
    """Preview locally by default; explicit writes use the leased generation publisher."""
    result = {"capacity": gate(None, None, "optional")}
    deps = leases = lease = None
    phase = "configuration"
    with ExitStack() as stack, _quiet():
        try:
            root = _empty_work_root(work_dir, stack)
            manager = wiring.build_state(root)
            phase = "restore"
            history = manager.list()
            number = history[0].generation if history else 0
            if history:
                manager.restore(root, generation=number, raw="remote")
            else:
                root.mkdir(parents=True, exist_ok=True)
            with workspace_lock(root):
                deps = wiring.build(root, {"vectors": None})
                deps.require("index")
                phase = "estimate"
                result.update(_projection_estimate(deps.pipeline_deps))
                result["current_bytes"] = _project_storage_bytes(manager.store)
                result["capacity"] = gate(result["current_bytes"], result["estimated_new_bytes"], "optional")
                if not write:
                    return result
                _report_capacity(result["capacity"])
                if not result["capacity"]["allowed"]:
                    result["capacity"]["skipped_steps"] = ["project"]
                    result.update(exit_status=1, reason="storage_limit_exceeded")
                    return result
                if not result["new_chunks"]:
                    return {**result, "generation": number, "before_bytes": result["current_bytes"],
                            "after_bytes": result["current_bytes"], "added_chunks": 0}
                # Report the measured account total before the first R2 write
                # (lease acquisition itself writes), keeping stdout as one JSON.
                print(json.dumps({key: result[key] for key in ("current_bytes", "estimated_new_bytes")}),
                      file=sys.__stderr__, flush=True)
                phase = "lease_acquire"
                leases = LeaseManager(manager.store)
                lease = leases.acquire(f"{manager.writer.kind}/{uuid.uuid4().hex}", force=True)
                with leases.maintain(lease) as check:
                    # The preview was lease-free. Reject a stale base rather than
                    # publishing over work committed between restore and acquire.
                    current = manager.list()
                    if (current[0].generation if current else 0) != number:
                        raise ValueError("generation_changed")
                    if manager.store.get(f"generations/{number + 1}.json") is not None:
                        raise ValueError("orphan_generation")
                    phase = "storage_check"
                    result["capacity"] = gate(None, result["estimated_new_bytes"], "optional")
                    result["before_bytes"] = _project_storage_bytes(manager.store)
                    result["capacity"] = gate(result["before_bytes"], result["estimated_new_bytes"], "optional")
                    _report_capacity(result["capacity"])
                    if not result["capacity"]["allowed"]:
                        result["capacity"]["skipped_steps"] = ["project"]
                        result.update(exit_status=1, reason="storage_limit_exceeded")
                        return result
                    if not _probe_lease_writes(manager.store, check):
                        raise ValueError("storage_conditional_unsupported")
                    manager.before_write = _throttled_check(check)
                    manager.before_metadata_write = manager.before_publish = check
                    phase = "projections"
                    with local_write(root):
                        result["added_chunks"] = backfill_projections(deps.pipeline_deps, update_keyword=True)
                    phase = "publish"
                    check()
                    generation = (StateManager.push(manager, retain_local=True)
                                  if isinstance(manager, StateManager) else manager.push())
                    result["generation"] = generation.generation
                    phase = "post_publish_check"
                    result["after_bytes"] = _project_storage_bytes(manager.store)
                    if result["after_bytes"] >= LIMITS.stop:
                        result["capacity"]["next_round"] = "cleanup_only"
        except Exception as error:  # noqa: BLE001 -- Hide source text and SDK configuration at the cloud boundary.
            # Only our own fixed refusal codes pass through; anything else reports the phase alone.
            known = isinstance(error, ValueError) and str(error) in _PROJECT_REFUSALS
            result.update(exit_status=1, reason=str(error) if known else f"{phase}_failed")
            if result["capacity"]["reason"] == "capacity_unknown":
                result["capacity"]["skipped_steps"] = ["project"]
        finally:
            if deps is not None:
                for name in ("manifest", "chunks", "keyword", "catalog", "change_log", "raw"):
                    close = getattr(getattr(deps, name, None), "close", None)
                    if callable(close):
                        with suppress(Exception):
                            close()
            if lease is not None:
                try:
                    leases.release(lease.holder, lease.token)
                except Exception:  # noqa: BLE001 -- Release failures must be visible.
                    result["exit_status"] = 1
                    result.setdefault("reason", "lease_release_failed")
                    result.setdefault("cleanup_errors", []).append("lease_release_failed")
                if "after_bytes" in result:
                    try:
                        result["after_bytes"] = _project_storage_bytes(manager.store)
                    except Exception:  # noqa: BLE001 -- Do not claim a verified final total if listing failed.
                        result.pop("after_bytes", None)
                        result["exit_status"] = 1
                        result.setdefault("reason", "storage_check_failed")
                        result.setdefault("cleanup_errors", []).append("storage_check_failed")
    return result


_PROJECT_REFUSALS = frozenset({"generation_changed", "orphan_generation", "storage_conditional_unsupported",
                               "workspace_not_empty"})


def _probe_lease_writes(store, check):
    """Test conditions using unchanged owned lease bytes, without probe objects or DELETE.

    Acquire first so an occupied lease is never used as a probe. Even a backend
    ignoring conditions receives only the current lease, never a dummy payload.
    """
    check()
    data, etag = store.get(LEASE_KEY)
    for write in (
        lambda: store.put_if_absent(LEASE_KEY, data),
        lambda: store.put_if_match(LEASE_KEY, data, f'"{uuid.uuid4().hex}"'),
    ):
        check()
        try:
            write()
        except PreconditionFailed:
            pass
        else:
            return False
        if store.get(LEASE_KEY) != (data, etag):
            return False
    check()
    updated = store.put_if_match(LEASE_KEY, data, etag)
    return store.get(LEASE_KEY) == (data, updated)


def _cleanup_restored(manager, root, check, result, *, enabled, raw_enabled, orphan_raw_enabled, max_minutes):
    """Shared cleanup-only path; start its full time allowance after restore."""
    with workspace_lock(root):
        check()
        result["phase"] = "initialization"
        specs = load_registry(Path(__file__).resolve().parents[3] / "sources").all()
        result["phase"] = "cleanup"
        result["cleanup"] = manager.cleanup(
            specs, now=utcnow(), check=check, enabled=enabled, raw_enabled=raw_enabled,
            orphan_raw_enabled=orphan_raw_enabled,
            deadline=time.monotonic() + max_minutes * 60 + _PIPELINE_GRACE_SECONDS + 1,
        )


def cleanup_only(*, work_dir: Path | None = None, max_minutes: float, delete: bool = False) -> dict:
    """Clean already published state; default to inventory only, never publish or mark raw.

    max_minutes is the work allowance, with a full 600s cleanup window reserved
    after restore. Exceptions expose only fixed phase/reason codes.
    """
    enabled = delete and os.environ.get("R2_CLEANUP", "on").lower() != "off"
    raw_enabled = os.environ.get("R2_RAW_CLEANUP", "off").lower() == "on"
    orphan_raw_enabled = os.environ.get("R2_ORPHAN_RAW_CLEANUP", "off") == "on"
    result = {"status": "failed", "exit_status": 1, "reason": None, "phase": "configuration",
              "mode": "delete" if delete else "dry_run", "generation": None,
              "cleanup": {"enabled": enabled, "raw_enabled": raw_enabled,
                          "failures": 0, "elapsed_s": 0.0, "timed_out": False}, "restore_timings": {}}
    result["cleanup"].update(orphan_raw_enabled=orphan_raw_enabled, orphan_raw_skip_reason=None)
    leases = lease = None
    with _quiet():
        stack = ExitStack()
        try:
            if (type(max_minutes) not in (int, float) or not math.isfinite(max_minutes)
                    or max_minutes < 0 or type(delete) is not bool):
                raise ValueError("invalid_budget")
            result["phase"] = "work_directory"
            root = _empty_work_root(work_dir, stack)
            result["phase"] = "configuration"
            manager = wiring.build_state(root)
            if isinstance(manager.store, S3StateObjectStore):
                manager.transfer_workers = 8
            leases = LeaseManager(manager.store)
            result["phase"] = "lease_acquire"
            try:
                lease = leases.acquire(f"{manager.writer.kind}/{uuid.uuid4().hex}", force=True)
            except LeaseError:
                if leases.status().get("active"):
                    result.update(status="skipped", exit_status=0, reason="跳过：写入租约已占用")
                    return result
                raise
            with leases.maintain(lease) as check:
                result["phase"] = "storage_probe"
                if not _probe_lease_writes(manager.store, check):
                    result["reason"] = "storage_conditional_unsupported"
                    return result
                result["phase"] = "restore"
                history = manager.list()
                result["generation"] = number = history[0].generation if history else 0
                if manager.store.get(f"generations/{number + 1}.json") is not None:
                    result["reason"] = "orphan_generation"
                    result["cleanup"]["orphan_raw_skip_reason"] = "orphan_generation"
                    return result
                if history:
                    try:
                        manager.restore(root, raw="remote")
                    finally:
                        result["restore_timings"] = _safe_timings(getattr(manager, "last_timings", {}))
                else:
                    root.mkdir(parents=True, exist_ok=True)
                _cleanup_restored(manager, root, check, result, enabled=enabled,
                                  raw_enabled=raw_enabled, orphan_raw_enabled=orphan_raw_enabled,
                                  max_minutes=max_minutes)
                report = result["cleanup"]
                reason = ("cleanup_failed" if report["failures"] else
                          "cleanup_timed_out" if report["timed_out"] else None)
                result.update(status="failed" if reason else "completed",
                              exit_status=int(reason is not None), reason=reason)
        except Exception:  # noqa: BLE001 -- Cloud boundary must never expose SDK/configuration errors.
            result.update(status="failed", exit_status=1, reason=f"{result['phase']}_failed")
        finally:
            if lease is not None:
                try:
                    leases.release(lease.holder, lease.token)
                except Exception:  # noqa: BLE001 -- Failed release must not be reported as success.
                    result.update(status="failed", exit_status=1,
                                  reason="lease_release_failed", phase="lease_release")
            try:
                stack.close()
            except Exception:  # noqa: BLE001 -- Temporary-directory errors can include private paths.
                result.update(status="failed", exit_status=1,
                              reason="work_directory_cleanup_failed", phase="work_directory_cleanup")
    return result


def _round(*, work_dir, max_embed, max_minutes, reserve_minutes, embed_only, no_cleanup=False):
    result = {"status": "failed", "exit_status": 1, "generation": None,
              "sources": [], "source_health": [], "due": 0, "new": 0, "unchanged": 0, "parsed": 0,
              "failed_sources": [], "reason": None, "budget_exhausted": False,
              "weekly_report_key": None, "reserve_minutes": reserve_minutes,
              "publish_timings": {}, "restore_timings": {},
              "embedding_deferred": 0, "backfill_embedded": 0, "vector_pending": 0}
    if embed_only:
        result = {"status": "failed", "exit_status": 1, "generation": None,
                  "embedded": 0, "vector_pending": 0, "embedding_deferred": 0, "embedding_failed": 0,
                  "budget_exhausted": False, "reason": None, "publish_timings": {}, "restore_timings": {}}
    result["capacity"] = gate(None, None, "optional" if embed_only else "essential")
    cleanup_enabled = not no_cleanup and os.environ.get("R2_CLEANUP", "on").lower() != "off"
    raw_cleanup_enabled = os.environ.get("R2_RAW_CLEANUP", "off").lower() == "on"
    orphan_raw_cleanup_enabled = os.environ.get("R2_ORPHAN_RAW_CLEANUP", "off") == "on"
    result["cleanup"] = {"enabled": cleanup_enabled, "raw_enabled": raw_cleanup_enabled,
                         "elapsed_s": 0.0, "timed_out": False}
    result["cleanup"].update(orphan_raw_enabled=orphan_raw_cleanup_enabled, orphan_raw_skip_reason=None)
    phase = "configuration"
    manager = leases = lease = deps = None
    started = time.monotonic()
    with _quiet(), ExitStack() as stack:
        try:
            if ((not (embed_only and max_embed is None) and (type(max_embed) is not int or max_embed < 0))
                    or not math.isfinite(max_minutes) or max_minutes < 0
                    or not math.isfinite(reserve_minutes) or reserve_minutes < 0):
                raise ValueError("invalid_budget")
            # Never reuse a previous attempt's local state or a pulled replica.
            try:
                root = _empty_work_root(work_dir, stack)
            except ValueError:
                phase = "work_directory"
                raise
            manager = wiring.build_state(root)
            if isinstance(manager.store, S3StateObjectStore):
                manager.transfer_workers = 8
            # This preflight selects the probe, not the run decision: run must
            # still acquire and restore before refusing work (restore errors win).
            # Near capacity, probe unchanged owned lease bytes instead of creating
            # disposable objects. Preserve the existing low-capacity probe path.
            if embed_only:
                preflight = _round_capacity(manager, embed_only=True)
                result["capacity"] = preflight
                _report_capacity(preflight)
                if not preflight["allowed"]:
                    preflight["skipped_steps"] = ["backfill", "publish"]
                    unknown = preflight["reason"] == "capacity_unknown"
                    result.update(status="failed" if unknown else "skipped", exit_status=int(unknown),
                                  reason=preflight["reason"])
                    # A stopped embedding retry may still finish an already uploaded
                    # orphan. Ordinary freeze/stop skips keep their lease-free path.
                    history = manager.list() if preflight["tier"] == "stop" and not unknown else []
                    number = history[0].generation if history else 0
                    if unknown or preflight["tier"] != "stop" or manager.store.get(
                        f"generations/{number + 1}.json"
                    ) is None:
                        return result
                lease_probe = preflight["tier"] != "normal"
            else:
                try:
                    lease_probe = _project_storage_bytes(manager.store) + LIMITS.crawl_reserve >= LIMITS.report
                except Exception:  # noqa: BLE001 -- Use only lease bytes if disposable probes may be unsafe.
                    lease_probe = True
            if not lease_probe:
                phase = "storage_probe"
                probe = probe_conditional_writes(manager.store)
                if not probe["supported"]:
                    result.update(reason="storage_conditional_unsupported", phase=phase, storage_probe=probe)
                    return result
            leases = LeaseManager(manager.store)
            phase = "lease_acquire"
            try:
                lease = leases.acquire(f"{manager.writer.kind}/{uuid.uuid4().hex}", force=True)
            except LeaseError:
                if leases.status().get("active"):
                    result.update(status="skipped", exit_status=0, reason="跳过：写入租约已占用")
                    return result
                raise
            with leases.maintain(lease) as check:
                manager.before_write = _throttled_check(check)
                manager.before_metadata_write = manager.before_publish = check
                cache = os.environ.get('RUNNER_TEMP')
                if cache:
                    manager.cache_dir = Path(cache) / 'rikolto-blocks'
                phase = "restore"
                history = manager.list()
                number = history[0].generation if history else 0
                if history:
                    try:
                        manager.restore(root, raw="remote")
                    finally:
                        result["restore_timings"] = _safe_timings(getattr(manager, "last_timings", {}))
                else:
                    root.mkdir(parents=True, exist_ok=True)
                if lease_probe:
                    phase = "storage_probe"
                    if not _probe_lease_writes(manager.store, check):
                        result.update(reason="storage_conditional_unsupported", phase=phase)
                        return result
                phase = "capacity"
                capacity = result["capacity"] = _round_capacity(manager, embed_only=embed_only)
                _report_capacity(capacity)
                orphan = manager.store.get(f"generations/{number + 1}.json") is not None
                stopped = capacity["reason"] == "capacity_stop"
                # Finish only an immutable uploaded orphan before stop cleanup.
                # Unknown capacity never authorizes any publication.
                if orphan and (capacity["allowed"] or stopped):
                    phase = "restore"
                    specs = manager.specs
                    if specs is None:
                        specs = load_registry(Path(__file__).resolve().parents[3] / "sources").all()
                    with tempfile.TemporaryDirectory(prefix="datumoj-orphan-") as directory:
                        recovery = StateManager(manager.store, directory, writer=manager.writer,
                                                before_publish=check, transfer_workers=manager.transfer_workers,
                                                specs=specs)
                        recovery.cache_dir = manager.cache_dir
                        recovery.before_write = _throttled_check(check)
                        recovery.before_metadata_write = check
                        recovery.push()
                    result["orphan_resumed"] = True
                    shutil.rmtree(root)
                    try:
                        manager.restore(root, raw="remote")
                    finally:
                        result["restore_timings"] = _safe_timings(getattr(manager, "last_timings", {}))
                if not capacity["allowed"]:
                    capacity["skipped_steps"] = (["backfill", "publish"] if embed_only else
                                                 ["crawl", "backfill", "mark_raw", "publish", "weekly_report"])
                    unknown = capacity["reason"] == "capacity_unknown"
                    result.update(status="failed" if unknown else "skipped", exit_status=int(unknown),
                                  reason=capacity["reason"])
                    if stopped and (not embed_only or orphan):
                        phase = "cleanup"
                        _cleanup_restored(manager, root, check, result, enabled=cleanup_enabled,
                                          raw_enabled=raw_cleanup_enabled,
                                          orphan_raw_enabled=orphan_raw_cleanup_enabled, max_minutes=max_minutes)
                        _next_round(result)
                        if result["cleanup"].get("failures") or result["cleanup"].get("timed_out"):
                            result.update(status="failed", exit_status=1, reason="capacity_stop_cleanup_failed")
                    return result
                if capacity["tier"] == "freeze":
                    capacity["skipped_steps"] = ["backfill", "weekly_report"]
                with workspace_lock(root):
                    check()
                    phase = "initialization"
                    deps = wiring.build(root)
                    if isinstance(manager, StateManager) and manager.specs is None:
                        manager.specs = deps.registry.all()
                    for feature in (("index",) if embed_only else ("crawl", "parse", "index")):
                        deps.require(feature)
                    if not embed_only:
                        if not (root / "control.json").exists():
                            write_control(root, {"enabled": True, "sources": {
                                spec.id: {"enabled": True} for spec in deps.registry.all()
                            }}, now=utcnow())
                        phase = "due"
                        scheduler = Scheduler(root, deps.registry.all(), lambda spec: None)
                        if hasattr(scheduler, "observe_enabled"):
                            scheduler.observe_enabled()
                        due = _due(scheduler, root)
                        result["due"] = len(due)
                        remaining = max_embed
                        pipeline = getattr(deps, "pipeline_deps", None)
                        before_round = _failure_counts(pipeline) if pipeline is not None else {}
                        uploads = _RawUploads(getattr(deps, "raw", None))
                        deps.raw = uploads
                        capacity.update(uploaded_raw_bytes=0, skipped_sources=[])
                        for source_index, source_id in enumerate(due):
                            phase = "lease_check"
                            check()
                            seconds_left = max_minutes * 60 - (time.monotonic() - started)
                            if seconds_left < 60:
                                result["budget_exhausted"] = True
                                break
                            source_capacity = gate(capacity["current_bytes"], uploads.bytes, "essential",
                                                   reserve_bytes=LIMITS.crawl_reserve)
                            _report_capacity(source_capacity)
                            if source_capacity["tier"] == "stop":
                                capacity.update(mid_round_stop=True, projected_bytes=source_capacity["projected_bytes"],
                                                next_round="cleanup_only", skipped_sources=[
                                    {"source_id": skipped, "reason": "capacity_stop_mid_round"}
                                    for skipped in due[source_index:]
                                ], skipped_steps=["crawl", "backfill", "weekly_report"])
                                break
                            phase = "run_ledger"
                            spec = scheduler.sources[source_id]
                            max_run = spec.scope.get("max_run_seconds", DEFAULT_MAX_RUN_SECONDS)
                            # Invalid source limits must still reach runner validation,
                            # rather than being masked by the round's valid limit.
                            if (type(max_run) in (int, float) and math.isfinite(max_run) and max_run > 0):
                                max_run = min(max_run, seconds_left)
                            scope = {**spec.scope, "max_run_seconds": max_run}
                            _progress("source_start", started, {"source_id": source_id})
                            record, remaining = _source(
                                deps, spec.model_copy(update={"scope": scope}), scheduler, remaining,
                                started, started + max_minutes * 60 + _PIPELINE_GRACE_SECONDS,
                            )
                            capacity["uploaded_raw_bytes"] = uploads.bytes
                            # Account for the completed source, including the final one.
                            # Keep admission fields and the initial byte baseline intact.
                            completed_capacity = gate(
                                current_bytes=capacity["current_bytes"], estimated_new_bytes=uploads.bytes,
                                category="essential", reserve_bytes=LIMITS.crawl_reserve,
                            )
                            _report_capacity(completed_capacity)
                            if completed_capacity["tier"] in {"freeze", "stop"}:
                                capacity.update(mid_round_freeze=True,
                                                mid_round_projected_bytes=completed_capacity["projected_bytes"],
                                                skipped_steps=["backfill", "weekly_report"])
                            if completed_capacity["tier"] == "stop":
                                capacity.update(mid_round_stop=True, projected_bytes=completed_capacity["projected_bytes"],
                                                next_round="cleanup_only", skipped_sources=[
                                    {"source_id": skipped, "reason": "capacity_stop_mid_round"}
                                    for skipped in due[source_index + 1:]
                                ])
                                if capacity["skipped_sources"]:
                                    capacity["skipped_steps"].insert(0, "crawl")
                            health = record.pop("source_health", None)
                            if health is not None:
                                health["consecutive_failures"], health["first_failure_at"] = _failure_streak(
                                    scheduler._read_history()[0], source_id,
                                )
                                result["source_health"].append(health)
                            _progress("source_end", started, record)
                            result["sources"].append(record)
                            for key in ("new", "unchanged", "parsed"):
                                result[key] += record[key]
                            result["embedding_deferred"] += record.get("embedding_deferred", 0)
                            if record["exit_status"]:
                                result["failed_sources"].append({"source_id": source_id, "reasons": record["failures"]})
                            phase = "lease_check"
                            check()
                    phase = "lease_renew"
                    leases.renew(lease.holder, lease.token)
                    check()
                    if embed_only:
                        phase = "embed"
                        pipeline = getattr(deps, "pipeline_deps", None)
                        before_round = _failure_counts(pipeline) if pipeline is not None else {}
                        _backfill(deps, max_embed, started, max_minutes, check, result, before_round, embed_only=True)
                        failed = bool(result["embedding_failed"])
                        result.update(status="failed" if failed else "completed", exit_status=int(failed),
                                      reason="embedding_failed" if failed else None)
                        if not result["embedded"] and not (pipeline is not None and pipeline._vector_pending_removed):
                            return result
                    else:
                        phase = "staged_recovery"
                        with local_write(root):
                            remaining = _recover(deps, remaining, started + max_minutes * 60 + _PIPELINE_GRACE_SECONDS)
                        if (capacity["tier"] not in {"freeze", "stop"}
                                and not (capacity.get("mid_round_freeze") or capacity.get("mid_round_stop"))):
                            phase = "backfill"
                            remaining = _backfill(deps, remaining, started, max_minutes, check, result, before_round)
                    phase = "maintenance"
                    maintenance_operation = "local_write_enter"
                    with local_write(root):
                        maintenance_operation = "maintain_vectors"
                        maintain_vectors(root / "vectors")
                        maintenance_operation = "local_write_exit"
                    marked = marking_failed = 0
                    maintenance_operation = "lease_check"
                    check()
                    try:
                        with local_write(root):
                            marked = manager.mark_raw(deps.registry.all(), utcnow())
                    except Exception:  # noqa: BLE001 -- Retention maintenance cannot fail the round.
                        marking_failed = 1
                    phase = "publish"
                    check()
                    _progress("publish_start", started, result)
                    publish_started = time.monotonic()
                    try:
                        # Keep the frozen boundary in StateManager for inventory verification
                        # and observation evidence, avoiding wiring's full restore hook.
                        generation = (StateManager.push(manager, retain_local=True)
                                      if isinstance(manager, StateManager) else manager.push())
                    finally:
                        push_timings = getattr(manager, "last_push_timings", {})
                        last_timings = getattr(manager, "last_timings", push_timings)
                        timings = dict(push_timings)
                        elapsed = time.monotonic() - publish_started
                        timings.setdefault('post_publish', 0.0)
                        if last_timings is not push_timings:
                            timings.update({f"post_publish_{key}": value
                                            for key, value in last_timings.items() if key != "total"})
                        timings["total"] = elapsed
                        manager.last_timings = result["publish_timings"] = _safe_timings(timings)
                        _progress("publish_end", started, result)
                    result["generation"] = generation.generation
                    cleanup = getattr(manager, "cleanup", None)
                    if callable(cleanup):
                        result["cleanup"] = cleanup(
                            deps.registry.all(), now=utcnow(), check=check,
                            enabled=cleanup_enabled, raw_enabled=raw_cleanup_enabled, marked=marked,
                            orphan_raw_enabled=orphan_raw_cleanup_enabled,
                            deadline=started + (max_minutes + reserve_minutes) * 60 + _PIPELINE_GRACE_SECONDS,
                        )
                        result["cleanup"]["failures"] += marking_failed
                    else:
                        # Older injected managers can publish without optional maintenance.
                        result["cleanup"]["failures"] = 1
                    _next_round(result)
                    _progress("cleanup_end", started, result)
                    if not embed_only:
                        result["source_health"] = _source_health(scheduler, result["source_health"], generation)
                    failed = bool(result["embedding_failed"] if embed_only else result["failed_sources"])
                    if (not failed and not embed_only and capacity["tier"] not in {"freeze", "stop"}
                            and not (capacity.get("mid_round_freeze") or capacity.get("mid_round_stop"))):
                        phase = "weekly_report"
                        weekly_capacity = _round_capacity(manager, weekly_report=True)
                        _report_capacity(weekly_capacity)
                        if weekly_capacity["allowed"] and capacity.get("next_round") != "cleanup_only":
                            result["weekly_report_key"] = publish_previous_week_report(
                                manager.store, deps, utcnow(), generation,
                            )
                        else:
                            capacity["skipped_steps"].append("weekly_report")
                    result.update(status="failed" if failed else "completed", exit_status=int(failed),
                                  reason=("embedding_failed" if embed_only else "source_failed") if failed else None)
        except Exception as exc:  # noqa: BLE001 -- 云端运行边界必须把任意 SDK 或插件失败记为本轮失败。
            result.update(status="failed", exit_status=1, reason=f"{phase}_failed")
            if phase == "maintenance":
                result["maintenance_failure"] = _maintenance_failure(maintenance_operation, exc)
        finally:
            if deps is not None:
                for name in ("manifest", "chunks", "keyword", "catalog", "change_log", "raw"):
                    close = getattr(getattr(deps, name, None), "close", None)
                    if callable(close):
                        with suppress(Exception):
                            close()
            if lease is not None:
                try:
                    leases.release(lease.holder, lease.token)
                except Exception:  # noqa: BLE001 -- 任意租约释放失败均须阻止本轮被误报成功。
                    result.update(status="failed", exit_status=1, reason="lease_release_failed")
            _progress("round_end", started, result)
    return result
