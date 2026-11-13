"""Capacity and cleanup-only must agree on exactly the same synthetic archive."""

import json

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.testing.fakes import FakeRegistry
from tests.regressions.test_audit_131 import archive as archive  # noqa: PLC0414 -- Shared synthetic fixture.
from tests.test_cloud_cleanup_only import cleanup_world as cleanup_world  # noqa: PLC0414 -- Shared synthetic fixture.


@pytest.mark.parametrize("raw_enabled", [False, True])
def test_usage_and_cleanup_preview_share_exact_reclaimable_counts_and_bytes(cleanup_world, monkeypatch, raw_enabled):
    world = cleanup_world
    monkeypatch.setenv("R2_RAW_CLEANUP", "on" if raw_enabled else "off")
    monkeypatch.setattr(cloud, "load_registry", lambda path: FakeRegistry(world.archive.specs))
    before = dict(world.store._objects)
    usage = cloud.usage(work_dir=world.root / "usage")
    assert "exit_status" not in usage, usage
    assert world.store._objects == before and world.writes == world.deletes == []
    preview = cloud.cleanup_only(work_dir=world.root / "preview", max_minutes=1)
    assert preview["exit_status"] == 0, preview
    report = preview["cleanup"]
    assert set(report) == {
        "enabled", "raw_enabled", "generations_deleted", "copies_deleted", "copies_deleted_bytes", "raw_marked",
        "raw_deleted", "raw_deleted_bytes", "reclaimable_generations", "reclaimable_copies", "reclaimable_copies_bytes",
        "reclaimable_raw", "reclaimable_raw_bytes", "total_bytes", "over_7gb", "failures", "elapsed_s", "timed_out",
        "receipt_status", "raw_deleted_by_receipt", "raw_deleted_by_receipt_bytes",
        "raw_deleted_by_age", "raw_deleted_by_age_bytes",
        "by_source", "reclaimable_orphan_raw", "reclaimable_orphan_raw_bytes", "orphan_raw_enabled",
        "orphan_raw_deleted", "orphan_raw_deleted_bytes", "orphan_raw_skip_reason",
    }
    for kind in ("generations", "copies", "raw"):
        assert usage["reclaimable"][kind]["objects"] == report[f"reclaimable_{kind}"]
    for kind in ("copies", "raw"):
        assert usage["reclaimable"][kind]["bytes"] == report[f"reclaimable_{kind}_bytes"]
    assert usage["reclaimable"]["raw"]["objects"] == 1
    assert usage["reclaimable"]["generations"]["objects"] == 1
    assert usage["reclaimable"]["raw_enabled"] == report["raw_enabled"] == raw_enabled
    assert report["enabled"] is False and world.deletes == []
    assert usage["raw_sources"]["jp-jma"]["objects"] == 4
    assert usage["total"]["bytes"] == sum(len(data) for data, _ in before.values())
    print("USAGE_SAMPLE", json.dumps(usage, sort_keys=True))
    print("CLEANUP_COMPARISON", json.dumps({"usage": usage["reclaimable"], "cleanup": {
        key: value for key, value in report.items() if key.startswith("reclaimable_") or key == "raw_enabled"
    }}, sort_keys=True))
