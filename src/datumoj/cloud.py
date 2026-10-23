"""One leased cloud round. Public output contains only IDs, counts and fixed reasons."""

from __future__ import annotations

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

from datumoj import wiring
from datumoj.analysis.reports import previous_week_period, weekly_report
from datumoj.core.budget import ParseBudget
from datumoj.core.contracts import PreconditionFailed
from datumoj.core.lease import LeaseError, LeaseManager
from datumoj.core.monitor import _ledger_first_attempts, evaluate_cloud_alerts
from datumoj.core.models import PublishState
from datumoj.core.schedule import Scheduler, utcnow, workspace_lock, write_control
from datumoj.core.state import StateManager, local_write
from datumoj.index.vector import maintain_vectors
from datumoj.pipeline import ParseSummary, _recover_staged


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


def _source(deps, spec, scheduler, remaining):
    record = {
        "source_id": spec.id, "run_id": uuid.uuid4().hex, "started_at": utcnow().isoformat(),
        "ended_at": None, "state": "running", "status": "running", "exit_status": 0,
        "new": 0, "unchanged": 0, "parsed": 0, "failures": {}, "stop_reason": None,
    }
    scheduler.append_run(record)
    phase = "crawl"
    try:
        crawler = deps.crawler_classes[spec.kind]()
        fetcher = deps.fetcher_for(spec)
        try:
            report = deps.run_crawl(spec, crawler, deps.crawl_deps_type(
                raw=deps.raw, manifest=deps.manifest, fetcher=fetcher,
            ))
        finally:
            close = getattr(fetcher, "close", None)
            if close is not None:
                close()
        record.update(new=report.new, unchanged=report.unchanged)
        if report.failures:
            record["failures"]["crawl_failed"] = sum(report.failures.values())
        phase = "parse_index"
        summary = deps.parse_pending(replace(deps.pipeline_deps, vector_limit=remaining), spec.id)
        remaining = summary.vector_remaining
        record["parsed"] = len(summary.parsed)
        # parse_pending also scans staged publications from other sources.
        # Their recovery errors must not turn this source's success into failure.
        def belongs(snapshot_id):
            snapshot = deps.manifest.get_snapshot(snapshot_id)
            return snapshot is None or snapshot.source_id == spec.id

        failed = sum(belongs(snapshot_id) for snapshot_id in summary.failed) + len(summary.no_parser)
        if failed:
            record["failures"]["parse_index_failed"] = failed
        # vector_limit is a deliberate deferral, other embedding failures are a
        # source failure; chunks and keyword results remain publishable.
        missing = sum(reason != "vector_limit" and belongs(snapshot_id)
                      for snapshot_id, reason in summary.vectors_missing.items())
        if missing:
            record["failures"]["embedding_failed"] = missing
    except Exception:
        record["failures"][f"{phase}_failed"] = record["failures"].get(f"{phase}_failed", 0) + 1
        # An interrupted parser cannot report its remaining allowance. Reserve
        # the rest so later sources cannot exceed the round's embedding budget.
        if phase == "parse_index":
            remaining = 0
    record.update(state="final", ended_at=utcnow().isoformat(),
                  status="failed" if record["failures"] else "completed",
                  exit_status=1 if record["failures"] else 0,
                  stop_reason="source_failed" if record["failures"] else None)
    scheduler.append_run(record)
    return record, remaining


def _recover(deps, remaining):
    """Use 107's recovery scan, without starting parses for sources not due."""
    pipeline = replace(deps.pipeline_deps, vector_limit=remaining)
    pipeline._vector_remaining = remaining
    specs = {spec.id: spec for spec in deps.registry.all()}
    parsers = pipeline.parsers if pipeline.parsers is not None else wiring._connected_parsers()
    summary = ParseSummary()
    _recover_staged(pipeline, parsers, specs,
                    {key: ParseBudget.from_scope(spec.scope) for key, spec in specs.items()}, summary)
    if deps.manifest.snapshots_in_publish_state(PublishState.staged):
        raise RuntimeError("staged_recovery_failed")


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


def run(*, work_dir: Path | None = None, max_embed: int = 1000, max_minutes: float = 40) -> dict:
    result = {"status": "failed", "exit_status": 1, "generation": None,
              "sources": [], "due": 0, "new": 0, "unchanged": 0, "parsed": 0,
              "failed_sources": [], "reason": None, "budget_exhausted": False,
              "weekly_report_key": None}
    phase = "configuration"
    manager = leases = lease = deps = None
    started = time.monotonic()
    with _quiet(), ExitStack() as stack:
        try:
            if type(max_embed) is not int or max_embed < 0 or not math.isfinite(max_minutes) or max_minutes < 0:
                raise ValueError("invalid_budget")
            root = Path(work_dir) if work_dir is not None else Path(stack.enter_context(
                tempfile.TemporaryDirectory(prefix="datumoj-cloud-")
            ))
            # Never reuse a previous attempt's local state or a pulled replica.
            if root.is_symlink() or (root.exists() and (not root.is_dir() or any(root.iterdir()))):
                phase = "work_directory"
                raise ValueError("workspace_not_empty")
            manager = wiring.build_state(root)
            leases = LeaseManager(manager.store)
            phase = "lease_acquire"
            try:
                lease = leases.acquire(f"actions/{uuid.uuid4().hex}", force=True)
            except LeaseError:
                if leases.status().get("active"):
                    result.update(status="skipped", exit_status=0, reason="跳过：写入租约已占用")
                    return result
                raise
            with leases.maintain(lease) as check:
                manager.before_write = manager.before_publish = check
                phase = "restore"
                history = manager.list()
                number = history[0].generation if history else 0
                # Resume an immutable orphan before new work. Otherwise push()
                # would publish that old candidate and silently discard this round.
                if manager.store.get(f"generations/{number + 1}.json") is not None:
                    with tempfile.TemporaryDirectory(prefix="datumoj-orphan-") as directory:
                        recovery = StateManager(manager.store, directory, writer=manager.writer, before_publish=check)
                        recovery.before_write = check
                        recovery.push()
                    history = manager.list()
                if history:
                    manager.restore(root, raw="remote")
                else:
                    root.mkdir(parents=True, exist_ok=True)
                with workspace_lock(root):
                    check()
                    phase = "initialization"
                    deps = wiring.build(root)
                    for feature in ("crawl", "parse", "index"):
                        deps.require(feature)
                    if not (root / "control.json").exists():
                        write_control(root, {"enabled": True, "sources": {
                            spec.id: {"enabled": True} for spec in deps.registry.all()
                        }})
                    phase = "due"
                    scheduler = Scheduler(root, deps.registry.all(), lambda spec: None)
                    due = scheduler.due()
                    result["due"] = len(due)
                    remaining = max_embed
                    for source_id in due:
                        phase = "lease_check"
                        check()
                        if time.monotonic() - started >= max_minutes * 60:
                            result["budget_exhausted"] = True
                            break
                        phase = "run_ledger"
                        record, remaining = _source(deps, scheduler.sources[source_id], scheduler, remaining)
                        result["sources"].append(record)
                        for key in ("new", "unchanged", "parsed"):
                            result[key] += record[key]
                        if record["exit_status"]:
                            result["failed_sources"].append({"source_id": source_id, "reasons": record["failures"]})
                        phase = "lease_check"
                        check()
                    phase = "lease_renew"
                    leases.renew(lease.holder, lease.token)
                    check()
                    phase = "staged_recovery"
                    with local_write(root):
                        _recover(deps, remaining)
                    phase = "maintenance"
                    with local_write(root):
                        maintain_vectors(root / "vectors")
                    phase = "publish"
                    check()
                    generation = manager.push()
                    result["generation"] = generation.generation
                    failed = bool(result["failed_sources"])
                    if not failed:
                        phase = "weekly_report"
                        result["weekly_report_key"] = publish_previous_week_report(
                            manager.store, deps, utcnow(), generation,
                        )
                    result.update(status="failed" if failed else "completed", exit_status=int(failed),
                                  reason="source_failed" if failed else None)
        except Exception:
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
                except Exception:
                    result.update(status="failed", exit_status=1, reason="lease_release_failed")
    return result
