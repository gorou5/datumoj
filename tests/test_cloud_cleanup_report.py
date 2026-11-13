"""Source accounting and both cloud entry points, using synthetic storage only."""

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.capacity import inventory
from datumoj.rikolto.core.models import PublishState
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.testing.samples import T0, make_spec
from tests.core.test_state_orphan_raw import BODY, KEY
from tests.regressions.test_audit_131 import DAY, publish, record
from tests.regressions.test_audit_131 import archive as archive  # noqa: PLC0414 -- Shared offline fixture.
from tests.test_cloud_cleanup_only import cleanup_world as cleanup_world  # noqa: PLC0414 -- Shared offline fixture.
from tests.test_cloud_run import invoke, released, remote
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.


def test_reclaimable_source_counts_match_capacity_inventory(archive, monkeypatch):
    archive.specs.append(make_spec("jp-other", scope={"raw_expire_latest_types": ["entry"]}))
    owned = record(archive, "owned")
    shared = record(archive, "shared", data=b"Synthetic shared raw")
    record(archive, "shared-other", source_id="jp-other", data=b"Synthetic shared raw")
    archive.deps.manifest.record_publish_state("shared-other", PublishState.published)
    record(archive, "protected", resource_type="feed")
    publish(archive, T0, monkeypatch)
    current, _ = publish(archive, T0 + 10 * DAY, monkeypatch)
    archive.store.put(KEY, BODY)
    usage = inventory(archive.store.list_objects(""), archive.manager.list(), current.generation,
                      archive.manager.data_dir / "manifest.sqlite", archive.specs, now=T0 + 10 * DAY)
    report = archive.manager.cleanup(archive.specs, now=T0 + 10 * DAY, check=lambda: None, enabled=False)
    assert report["failures"] == 0
    assert report["by_source"] == usage["reclaimable_by_source"] == {
        "jp-jma": {"objects": 1, "bytes": owned.size},
        "shared": {"objects": 1, "bytes": shared.size},
        "unreferenced": {"objects": 1, "bytes": len(BODY)},
    }
    assert usage["raw_sources"]["jp-jma"]["objects"] == 2  # Protected feed isn't reclaimable.
    assert report["reclaimable_raw"] == usage["reclaimable"]["raw"]["objects"] == 2
    assert report["reclaimable_raw_bytes"] == usage["reclaimable"]["raw"]["bytes"]
    assert sum(row["objects"] for row in report["by_source"].values()) == (
        report["reclaimable_raw"] + report["reclaimable_orphan_raw"])


@pytest.mark.parametrize("switch", [None, "off", "on", "ON", "true", ""])
@pytest.mark.parametrize("delete", [False, True])
def test_cleanup_only_passes_explicit_on_switch(cleanup_world, monkeypatch, switch, delete):
    if switch is None:
        monkeypatch.delenv("R2_ORPHAN_RAW_CLEANUP", raising=False)
    else:
        monkeypatch.setenv("R2_ORPHAN_RAW_CLEANUP", switch)
    cleanup_world.store.put(KEY, BODY)
    result = cloud.cleanup_only(work_dir=cleanup_world.root / "orphan-switch", max_minutes=1, delete=delete)
    assert result["exit_status"] == 0, result
    report = result["cleanup"]
    assert report["orphan_raw_enabled"] is (switch == "on")
    assert report["reclaimable_orphan_raw"] == 1
    assert report["orphan_raw_deleted"] == int(delete and switch == "on")
    assert cleanup_world.store.exists(KEY) is not (delete and switch == "on")


@pytest.mark.parametrize("switch", [None, "off", "on", "ON"])
def test_run_passes_orphan_switch_after_publication(world, monkeypatch, switch):
    monkeypatch.setenv("R2_CLEANUP", "on")
    monkeypatch.setenv("R2_RAW_CLEANUP", "on")
    if switch is None:
        monkeypatch.delenv("R2_ORPHAN_RAW_CLEANUP", raising=False)
    else:
        monkeypatch.setenv("R2_ORPHAN_RAW_CLEANUP", switch)
    original = StateManager.cleanup

    def cleanup(manager, specs, **kwargs):
        manager.store.put(KEY, BODY)
        return original(manager, specs, **kwargs)

    monkeypatch.setattr(StateManager, "cleanup", cleanup)
    result, _ = invoke(world)
    assert result["status"] == "completed", result
    assert result["cleanup"]["reclaimable_orphan_raw"] == 1
    assert result["cleanup"]["orphan_raw_enabled"] is (switch == "on")
    assert result["cleanup"]["orphan_raw_deleted"] == int(switch == "on")
    assert remote(world).exists(KEY) is not (switch == "on")
    released(world)
