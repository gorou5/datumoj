"""Below the stop line, and for failures before it, every result is as before 105 (no new field)."""

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.core.state import StateManager
from tests.test_cloud_capacity import capacity_inventory
from tests.test_cloud_embed import invoke_embed, seed
from tests.test_cloud_run import invoke, released
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.

# Round capacity fields as written before 105 for a published crawl round.
ROUND_CAPACITY = {"current_bytes", "estimated_new_bytes", "reserve_bytes", "projected_bytes", "tier", "allowed",
                  "reason", "skipped_steps", "uploaded_raw_bytes", "skipped_sources"}


def forbid_mark_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("mark-only publication belongs to the stop tier only")

    monkeypatch.setattr(cloud, "_mark_only_restored", forbidden)
    monkeypatch.setattr(StateManager, "publish_marked_manifest", forbidden)


@pytest.mark.parametrize("total,tier,skipped", [
    (6_000_000_000, "normal", []),
    (7_500_000_000, "report", []),
    (8_100_000_000, "freeze", ["backfill", "weekly_report"]),
])
def test_tiers_below_stop_are_unchanged(world, monkeypatch, total, tier, skipped):
    forbid_mark_only(monkeypatch)
    capacity_inventory(monkeypatch, total)
    result, _ = invoke(world)
    capacity = result["capacity"]
    assert result["status"] == "completed" and result["generation"] == 1 and result["new"] == 3
    assert result["reason"] is None and result["cleanup"]["failures"] == 0
    # A source crossing the freeze line mid-round adds these two existing fields.
    extra = {"mid_round_freeze", "mid_round_projected_bytes"} if tier == "freeze" else set()
    assert set(capacity) == ROUND_CAPACITY | extra and "mark_only" not in capacity
    assert capacity["tier"] == tier and capacity["allowed"] and capacity["skipped_steps"] == skipped
    assert capacity["current_bytes"] == total and capacity["projected_bytes"] == total + 500_000_000
    assert capacity["estimated_new_bytes"] == 0 and capacity["reserve_bytes"] == 500_000_000
    assert capacity["reason"] == f"capacity_{tier}" and capacity["skipped_sources"] == []
    assert (result["weekly_report_key"] is None) == (tier == "freeze")
    assert not (set(result) & {"mark_only", "orphan_resumed"})
    released(world)


def test_restore_failure_keeps_its_reason_and_never_marks(world, monkeypatch):
    assert invoke(world, "first")[0]["generation"] == 1
    capacity_inventory(monkeypatch, 9_100_000_000)
    forbid_mark_only(monkeypatch)

    def broken(*args, **kwargs):
        raise OSError("synthetic private restore detail")

    monkeypatch.setattr(StateManager, "restore", broken)
    result, _ = invoke(world, "restore-failed")
    assert result["status"] == "failed" and result["exit_status"] == 1
    assert result["reason"] == "restore_failed"
    assert result["generation"] is None and "mark_only" not in result["capacity"]
    released(world)


def test_embedding_stop_never_marks(world, monkeypatch):
    seed(world)
    capacity_inventory(monkeypatch, 9_100_000_000)
    forbid_mark_only(monkeypatch)
    result = invoke_embed(world)
    assert result["status"] == "skipped" and result["reason"] == "capacity_stop"
    assert "mark_only" not in result["capacity"] and result["capacity"]["skipped_steps"] == ["backfill", "publish"]
    released(world)
