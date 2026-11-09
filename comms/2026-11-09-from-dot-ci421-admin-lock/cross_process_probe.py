"""Synthetic cross-process check for the scheduler status/start probe gate.

Run with the candidate source tree on PYTHONPATH:
    PYTHONPATH=src python cross_process_probe.py

Uses temporary data, a fake crawler, and real POSIX file locks. Remote lease
configuration is disabled explicitly. It does not contact production services.
This checks the observer collision only, not all startup-result reporting or
manual-request deduplication. It does not execute or validate native Windows.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from datumoj.rikolto.core import schedule
from datumoj.rikolto.testing.samples import T0, make_spec

WAIT_SECONDS = 10
SOURCE_ID = "jp-one"


def observe(root, ready, release, results):
    """Hold the real source probe until the parent attempts startup."""
    original_locked = schedule._locked
    source_path = Path(root) / "locks" / f"{SOURCE_ID}.lock"

    @contextmanager
    def hold_probe(path, **kwargs):
        with original_locked(path, **kwargs) as lock:
            if path == source_path and lock is not None:
                ready.set()
                assert release.wait(WAIT_SECONDS), "Observer release timed out"
            yield lock

    with patch.object(schedule, "_locked", hold_probe):
        scheduler = schedule.Scheduler(
            root, [make_spec(SOURCE_ID, schedule="hourly")], lambda _: None,
            now=lambda: T0,
        )
        results.put(scheduler.status()["sources"][0]["running"])


def main():
    if os.name == "nt" or schedule.fcntl is None:
        raise SystemExit("Requires POSIX fork and real fcntl locks; Windows is not validated.")

    context = multiprocessing.get_context("fork")
    with tempfile.TemporaryDirectory(prefix="datumoj-probe-review-") as directory:
        root = Path(directory)
        calls = []
        outcomes = []
        errors = []

        def crawl(spec):
            calls.append(spec.id)
            return SimpleNamespace(new=1, unchanged=0, failures={}, stop_reason=None)

        scheduler = schedule.Scheduler(
            root, [make_spec(SOURCE_ID, schedule="hourly")], crawl, now=lambda: T0,
        )
        schedule.set_enabled(root, True)
        schedule.set_enabled(root, True, [SOURCE_ID])

        ready, release = context.Event(), context.Event()
        observer_results = context.Queue()
        process = context.Process(
            target=observe, args=(str(root), ready, release, observer_results),
        )
        gate_attempted = threading.Event()
        runner_done = threading.Event()
        original_locked = schedule._locked

        @contextmanager
        def notify_gate(path, **kwargs):
            if threading.current_thread().name == "probe-review-runner" and path == root / "runs.lock":
                gate_attempted.set()
            with original_locked(path, **kwargs) as lock:
                yield lock

        def run():
            try:
                outcomes.append(scheduler.run_now(SOURCE_ID))
            except BaseException as error:  # noqa: BLE001 -- collect synthetic worker outcomes without exception text.
                errors.append(type(error).__name__)
            finally:
                runner_done.set()

        worker = threading.Thread(target=run, name="probe-review-runner", daemon=True)
        # Start the child before introducing a background thread.
        process.start()
        try:
            assert ready.wait(WAIT_SECONDS), "Observer did not acquire its source probe"
            with (
                patch.object(schedule, "_locked", notify_gate),
                patch.object(schedule, "remote_configured", return_value=False),
            ):
                worker.start()
                assert gate_attempted.wait(WAIT_SECONDS), "Runner did not attempt the probe gate"
                assert not runner_done.is_set() and not calls, "Runner bypassed the held probe gate"
                release.set()
                assert runner_done.wait(WAIT_SECONDS), "Runner did not finish after observer release"
                worker.join(WAIT_SECONDS)
                assert not worker.is_alive(), "Runner thread did not exit"
            process.join(WAIT_SECONDS)
            assert process.exitcode == 0, "Observer process did not exit successfully"
            observer_running = observer_results.get(timeout=WAIT_SECONDS)
            assert observer_running is False, "Observer incorrectly reported a running worker"
            assert not errors, errors
            assert calls == [SOURCE_ID], calls
            assert len(outcomes) == 1 and outcomes[0]["status"] == "completed", outcomes
            assert outcomes[0]["new"] == 1, outcomes
            print(json.dumps({
                "check": "cross_process_status_probe_gate",
                "result": "passed",
                "observer_running": observer_running,
                "runner_status": outcomes[0]["status"],
                "crawler_calls": calls,
                "native_windows_executed": False,
            }, sort_keys=True))
        finally:
            release.set()
            if worker.ident is not None:
                worker.join(WAIT_SECONDS)
            process.join(WAIT_SECONDS)
            if process.is_alive():
                process.terminate()
                process.join(WAIT_SECONDS)
            observer_results.close()
            observer_results.join_thread()


if __name__ == "__main__":
    main()
