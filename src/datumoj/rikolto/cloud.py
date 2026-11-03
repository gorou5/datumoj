"""One leased cloud round. Public output contains only IDs, counts and fixed reasons."""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import tempfile
import time
import uuid
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout, suppress
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import Lock

from datumoj.rikolto import wiring
from datumoj.rikolto.analysis.reports import previous_week_period, weekly_report
from datumoj.rikolto.core.budget import ParseBudget
from datumoj.rikolto.core.contracts import PreconditionFailed
from datumoj.rikolto.core.http import PoliteHttpFetcher
from datumoj.rikolto.core.lease import LeaseError, LeaseManager
from datumoj.rikolto.core.models import PublishState
from datumoj.rikolto.core.monitor import _ledger_first_attempts, evaluate_cloud_alerts
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
)

_BACKFILL_BATCH = 32
_MAX_REQUEST_SECONDS = 300
_PIPELINE_GRACE_SECONDS = 600


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
    phase = "crawl"
    try:
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
        if report.failures:
            record["failures"]["crawl_failed"] = sum(report.failures.values())
        _progress("crawl_end", started, record)
        phase = "parse_index"
        pipeline = replace(deps.pipeline_deps, vector_limit=remaining, deadline=deadline, clock=time.monotonic)
        before = _failure_counts(pipeline)
        summary = deps.parse_pending(pipeline, spec.id)
        remaining = summary.vector_remaining
        record["parsed"] = len(summary.parsed)
        if summary.stop_reason == "deadline":
            record["stop_reason"] = "deadline"
        # parse_pending also scans staged publications from other sources.
        # Their recovery errors must not turn this source's success into failure.
        def belongs(snapshot_id):
            snapshot = deps.manifest.get_snapshot(snapshot_id)
            return snapshot is None or snapshot.source_id == spec.id

        failed = sum(belongs(snapshot_id) for snapshot_id in summary.failed) + len(summary.no_parser)
        if failed:
            record["failures"]["parse_index_failed"] = failed
        deferred, failed = _embedding_counts(pipeline, before, spec.id)
        if deferred:
            record["embedding_deferred"] = deferred
        if failed:
            record["failures"]["embedding_failed"] = failed
        _progress("parse_end", started, record)
    except Exception:  # noqa: BLE001 -- 单来源失败需隔离，保留本轮其他来源的执行。
        record["failures"][f"{phase}_failed"] = record["failures"].get(f"{phase}_failed", 0) + 1
        # An interrupted parser cannot report its remaining allowance. Reserve
        # the rest so later sources cannot exceed the round's embedding budget.
        if phase == "parse_index":
            remaining = 0
    record.update(state="final", ended_at=utcnow().isoformat(),
                  status="failed" if record["failures"] else "completed",
                  exit_status=1 if record["failures"] else 0,
                  stop_reason=record["stop_reason"] or ("source_failed" if record["failures"] else None))
    # Preserve result status/exit semantics, but never advertise a partial crawl
    # as a successful run in the published generation's last_successful_runs.
    scheduler.append_run({**record, "status": "incomplete"} if record["stop_reason"] == "deadline" else record)
    return record, remaining


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
            ))
        content = weekly_report(deps, start, end, alerts)
        try:
            store.put_if_absent(object_key, content.encode("utf-8"))
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
        reserve_minutes: float = 10) -> dict:
    return _round(work_dir=work_dir, max_embed=max_embed, max_minutes=max_minutes,
                  reserve_minutes=reserve_minutes, embed_only=False)


def embed(*, work_dir: Path | None = None, max_minutes: float,
          reserve_minutes: float = 10, max_embed: int | None = None) -> dict:
    """Embed existing backlog in bounded batches, publishing only successful work."""
    return _round(work_dir=work_dir, max_embed=max_embed, max_minutes=max_minutes,
                  reserve_minutes=reserve_minutes, embed_only=True)


def _round(*, work_dir, max_embed, max_minutes, reserve_minutes, embed_only):
    result = {"status": "failed", "exit_status": 1, "generation": None,
              "sources": [], "due": 0, "new": 0, "unchanged": 0, "parsed": 0,
              "failed_sources": [], "reason": None, "budget_exhausted": False,
              "weekly_report_key": None, "reserve_minutes": reserve_minutes,
              "publish_timings": {}, "restore_timings": {},
              "embedding_deferred": 0, "backfill_embedded": 0, "vector_pending": 0}
    if embed_only:
        result = {"status": "failed", "exit_status": 1, "generation": None,
                  "embedded": 0, "vector_pending": 0, "embedding_deferred": 0, "embedding_failed": 0,
                  "budget_exhausted": False, "reason": None, "publish_timings": {}, "restore_timings": {}}
    phase = "configuration"
    manager = leases = lease = deps = None
    started = time.monotonic()
    with _quiet(), ExitStack() as stack:
        try:
            if ((not (embed_only and max_embed is None) and (type(max_embed) is not int or max_embed < 0))
                    or not math.isfinite(max_minutes) or max_minutes < 0
                    or not math.isfinite(reserve_minutes) or reserve_minutes < 0):
                raise ValueError("invalid_budget")
            root = Path(work_dir) if work_dir is not None else Path(stack.enter_context(
                tempfile.TemporaryDirectory(prefix="datumoj-cloud-")
            ))
            # Never reuse a previous attempt's local state or a pulled replica.
            if root.is_symlink() or (root.exists() and (not root.is_dir() or any(root.iterdir()))):
                phase = "work_directory"
                raise ValueError("workspace_not_empty")
            manager = wiring.build_state(root)
            if isinstance(manager.store, S3StateObjectStore):
                manager.transfer_workers = 8
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
                phase = "restore"
                history = manager.list()
                number = history[0].generation if history else 0
                # Resume an immutable orphan before new work. Otherwise push()
                # would publish that old candidate and silently discard this round.
                if manager.store.get(f"generations/{number + 1}.json") is not None:
                    with tempfile.TemporaryDirectory(prefix="datumoj-orphan-") as directory:
                        recovery = StateManager(manager.store, directory, writer=manager.writer,
                                                before_publish=check, transfer_workers=manager.transfer_workers)
                        recovery.before_write = _throttled_check(check)
                        recovery.before_metadata_write = check
                        recovery.push()
                    history = manager.list()
                if history:
                    try:
                        manager.restore(root, raw="remote")
                    finally:
                        result["restore_timings"] = dict(getattr(manager, "last_timings", {}))
                else:
                    root.mkdir(parents=True, exist_ok=True)
                with workspace_lock(root):
                    check()
                    phase = "initialization"
                    deps = wiring.build(root)
                    for feature in (("index",) if embed_only else ("crawl", "parse", "index")):
                        deps.require(feature)
                    if not embed_only:
                        if not (root / "control.json").exists():
                            write_control(root, {"enabled": True, "sources": {
                                spec.id: {"enabled": True} for spec in deps.registry.all()
                            }})
                        phase = "due"
                        scheduler = Scheduler(root, deps.registry.all(), lambda spec: None)
                        due = _due(scheduler, root)
                        result["due"] = len(due)
                        remaining = max_embed
                        pipeline = getattr(deps, "pipeline_deps", None)
                        before_round = _failure_counts(pipeline) if pipeline is not None else {}
                        for source_id in due:
                            phase = "lease_check"
                            check()
                            seconds_left = max_minutes * 60 - (time.monotonic() - started)
                            if seconds_left < 60:
                                result["budget_exhausted"] = True
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
                        phase = "backfill"
                        remaining = _backfill(deps, remaining, started, max_minutes, check, result, before_round)
                    phase = "maintenance"
                    with local_write(root):
                        maintain_vectors(root / "vectors")
                    phase = "publish"
                    check()
                    _progress("publish_start", started, result)
                    publish_started = time.monotonic()
                    try:
                        generation = manager.push()
                    finally:
                        push_timings = getattr(manager, "last_push_timings", {})
                        last_timings = getattr(manager, "last_timings", push_timings)
                        timings = dict(push_timings)
                        elapsed = time.monotonic() - publish_started
                        # wiring's release hook restores published data after the
                        # base push. Include that entire tail without changing its semantics.
                        timings["post_publish"] = max(0.0, elapsed - timings.get("total", 0.0))
                        if last_timings is not push_timings:
                            timings.update({f"post_publish_{key}": value
                                            for key, value in last_timings.items() if key != "total"})
                        timings["total"] = elapsed
                        manager.last_timings = result["publish_timings"] = timings
                        _progress("publish_end", started, result)
                    result["generation"] = generation.generation
                    failed = bool(result["embedding_failed"] if embed_only else result["failed_sources"])
                    if not failed and not embed_only:
                        phase = "weekly_report"
                        result["weekly_report_key"] = publish_previous_week_report(
                            manager.store, deps, utcnow(), generation,
                        )
                    result.update(status="failed" if failed else "completed", exit_status=int(failed),
                                  reason=("embedding_failed" if embed_only else "source_failed") if failed else None)
        except Exception:  # noqa: BLE001 -- 云端运行边界必须把任意 SDK 或插件失败记为本轮失败。
            result.update(status="failed", exit_status=1, reason=f"{phase}_failed")
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
