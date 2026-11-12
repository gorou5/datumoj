"""Below the report line, retain every pre-gate run/embed result field."""

import pytest

from tests.test_cloud_embed import invoke_embed, seed
from tests.test_cloud_run import invoke
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.

# Captured from 3735a92 by results/DATUMOJ-KATABAMI-102/capture.py.
# Inline so the test also runs in clean checkouts, where /results/ is ignored.
BASELINES = {
    "run": {
        "backfill_embedded": 0,
        "budget_exhausted": False,
        "cleanup": {
            "copies_deleted": 0,
            "copies_deleted_bytes": 0,
            "elapsed_s": 0,
            "enabled": True,
            "failures": 0,
            "generations_deleted": 0,
            "over_7gb": False,
            "raw_deleted": 0,
            "raw_deleted_bytes": 0,
            "raw_enabled": False,
            "raw_marked": 0,
            "reclaimable_copies": 0,
            "reclaimable_copies_bytes": 0,
            "reclaimable_generations": 0,
            "reclaimable_raw": 0,
            "reclaimable_raw_bytes": 0,
            "timed_out": False,
            "total_bytes": 0,
        },
        "due": 3,
        "embedding_deferred": 0,
        "exit_status": 0,
        "failed_sources": [],
        "generation": 1,
        "new": 3,
        "parsed": 3,
        "publish_timings": {},
        "reason": None,
        "reserve_minutes": 10.0,
        "restore_timings": {},
        "source_health": [
            {
                "categories": {},
                "consecutive_failures": 0,
                "first_failure_at": None,
                "phase": None,
                "source_id": "jp-cloud0",
                "status": "ok",
            },
            {
                "categories": {},
                "consecutive_failures": 0,
                "first_failure_at": None,
                "phase": None,
                "source_id": "jp-cloud1",
                "status": "ok",
            },
            {
                "categories": {},
                "consecutive_failures": 0,
                "first_failure_at": None,
                "phase": None,
                "source_id": "jp-cloud2",
                "status": "ok",
            },
        ],
        "sources": [
            {
                "embedding_deferred": 0,
                "ended_at": "synthetic_variable",
                "exit_status": 0,
                "failures": {},
                "new": 1,
                "parsed": 1,
                "run_id": "synthetic_variable",
                "source_id": "jp-cloud0",
                "started_at": "synthetic_variable",
                "state": "final",
                "status": "completed",
                "stop_reason": None,
                "unchanged": 0,
            },
            {
                "embedding_deferred": 0,
                "ended_at": "synthetic_variable",
                "exit_status": 0,
                "failures": {},
                "new": 1,
                "parsed": 1,
                "run_id": "synthetic_variable",
                "source_id": "jp-cloud1",
                "started_at": "synthetic_variable",
                "state": "final",
                "status": "completed",
                "stop_reason": None,
                "unchanged": 0,
            },
            {
                "embedding_deferred": 0,
                "ended_at": "synthetic_variable",
                "exit_status": 0,
                "failures": {},
                "new": 1,
                "parsed": 1,
                "run_id": "synthetic_variable",
                "source_id": "jp-cloud2",
                "started_at": "synthetic_variable",
                "state": "final",
                "status": "completed",
                "stop_reason": None,
                "unchanged": 0,
            },
        ],
        "status": "completed",
        "unchanged": 0,
        "vector_pending": 0,
        "weekly_report_key": "reports/weekly/synthetic-week.md",
    },
    "embed": {
        "budget_exhausted": False,
        "cleanup": {
            "copies_deleted": 0,
            "copies_deleted_bytes": 0,
            "elapsed_s": 0,
            "enabled": True,
            "failures": 0,
            "generations_deleted": 0,
            "over_7gb": False,
            "raw_deleted": 0,
            "raw_deleted_bytes": 0,
            "raw_enabled": False,
            "raw_marked": 0,
            "reclaimable_copies": 0,
            "reclaimable_copies_bytes": 0,
            "reclaimable_generations": 0,
            "reclaimable_raw": 0,
            "reclaimable_raw_bytes": 0,
            "timed_out": False,
            "total_bytes": 0,
        },
        "embedded": 6,
        "embedding_deferred": 0,
        "embedding_failed": 0,
        "exit_status": 0,
        "generation": 2,
        "publish_timings": {},
        "reason": None,
        "restore_timings": {},
        "status": "completed",
        "vector_pending": 0,
    },
}


def stable_result(value):
    """Mask only wall-clock timings, random IDs and physical SQLite byte totals.

    All keys and nonvolatile values (including cleanup decisions and counters)
    must match the result captured from the original HEAD implementation.
    """
    if isinstance(value, list):
        return [stable_result(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"publish_timings", "restore_timings"}:
            result[key] = {}
        elif key in {"elapsed_s", "total_bytes"}:
            result[key] = 0
        elif key in {"run_id", "started_at", "ended_at"} and item is not None:
            result[key] = "synthetic_variable"
        elif key == "weekly_report_key" and item is not None:
            result[key] = "reports/weekly/synthetic-week.md"
        else:
            result[key] = stable_result(item)
    return result


@pytest.mark.parametrize("mode", ["run", "embed"])
def test_normal_result_matches_pre_gate_baseline(world, mode):
    if mode == "embed":
        seed(world)
        result = invoke_embed(world)
    else:
        result, _ = invoke(world)
    capacity = result.pop("capacity")
    assert capacity["tier"] == "normal" and capacity["allowed"]
    assert capacity["skipped_steps"] == []
    expected = BASELINES[mode]
    assert stable_result(result) == expected
