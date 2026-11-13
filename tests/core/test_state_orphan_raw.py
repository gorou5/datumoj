"""Orphan raw safety and reupload, with local manifests and fake storage only."""

import hashlib
from contextlib import closing

import pytest

from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.state import plan_cleanup
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec
from tests.core.test_state import corpus as corpus  # noqa: PLC0414 -- Shared offline fixture.
from tests.core.test_state import state as state  # noqa: PLC0414 -- Shared offline fixture.
from tests.test_capacity import obj

BODY = b"Synthetic interrupted upload\n\nSame content on retry"
SHA = hashlib.sha256(BODY).hexdigest()
KEY = f"raw/{SHA[:2]}/{SHA}"


@pytest.mark.parametrize("reference", ["snapshot", "optional_snapshot", "marker_only", None])
def test_plan_excludes_every_manifest_reference(tmp_path, reference):
    path = tmp_path / "manifest.sqlite"
    with closing(SqliteManifest(path)) as manifest:
        if reference in {"snapshot", "optional_snapshot"}:
            manifest.record_snapshot(make_snapshot(data=BODY))
        if reference in {"optional_snapshot", "marker_only"}:
            manifest.mark_raw_optional(SHA, T0)
    raw = obj(KEY, len(BODY))
    outside = obj(f"other/{SHA}", len(BODY))
    plan = plan_cleanup([raw, outside], [], 0, path, [], now=T0)
    assert plan["orphan_raw"] == ([raw] if reference is None else [])
    assert plan["raw"] == []


@pytest.mark.parametrize("descriptor", ["generations/2.json", "generations/9.json"])
def test_orphan_generation_vetoes_whole_class(state, descriptor):
    manager, store = state
    current = manager.push()
    store.put(KEY, BODY)
    # Even an unreadable unpublished descriptor must veto deletion.
    store.put(descriptor, b"unpublished synthetic descriptor")
    plan = plan_cleanup(store.list_objects(""), [current], 1,
                        manager.data_dir / "manifest.sqlite", [make_spec()], now=T0)
    assert plan["orphan_raw"] == []
    report = manager.cleanup([make_spec()], now=T0, check=lambda: None,
                             raw_enabled=True, orphan_raw_enabled=True)
    assert report["failures"] == 0
    assert report["orphan_raw_skip_reason"] == "orphan_generation"
    assert report["reclaimable_orphan_raw"] == report["orphan_raw_deleted"] == 0
    assert store.get(KEY)[0] == BODY


@pytest.mark.parametrize("switches,deleted", [
    ({}, False),  # Default is statistics only, even with raw cleanup on.
    ({"orphan_raw_enabled": False}, False),
    ({"orphan_raw_enabled": True}, True),
    ({"orphan_raw_enabled": True, "raw_enabled": False}, False),
    ({"orphan_raw_enabled": True, "enabled": False}, False),
])
def test_switches_count_before_deletion(state, switches, deleted):
    manager, store = state
    manager.push()
    store.put(KEY, BODY)
    before = sum(o.size_bytes for o in store.list_objects(""))
    report = manager.cleanup([make_spec()], now=T0, check=lambda: None,
                             **{"raw_enabled": True, **switches})
    assert report["failures"] == 0 and report["orphan_raw_skip_reason"] is None
    assert report["reclaimable_orphan_raw"] == 1
    assert report["reclaimable_orphan_raw_bytes"] == len(BODY)
    assert report["orphan_raw_deleted"] == int(deleted)
    assert report["orphan_raw_deleted_bytes"] == len(BODY) * deleted
    assert report["raw_deleted"] == report["raw_deleted_bytes"] == 0
    assert report["by_source"]["unreferenced"] == {"objects": 1, "bytes": len(BODY)}
    assert store.exists(KEY) is not deleted
    assert report["total_bytes"] == before - len(BODY) * deleted


@pytest.mark.parametrize("lost_at", [1, 2])
def test_lease_loss_before_planning_or_delete_prevents_orphan_deletion(state, lost_at):
    manager, store = state
    manager.push()
    store.put(KEY, BODY)
    calls = 0

    def check():
        nonlocal calls
        calls += 1
        if calls == lost_at:
            raise OSError("synthetic lost lease")

    report = manager.cleanup([make_spec()], now=T0, check=check,
                             raw_enabled=True, orphan_raw_enabled=True)
    assert report["failures"] == 1 and report["orphan_raw_deleted"] == 0
    assert store.exists(KEY) and calls == lost_at


def test_plan_checks_guard(tmp_path):
    path = tmp_path / "manifest.sqlite"
    with closing(SqliteManifest(path)):
        pass

    def lost():
        raise OSError("synthetic lost guard")

    with pytest.raises(OSError, match="lost guard"):
        plan_cleanup([obj(KEY, len(BODY))], [], 0, path, [], now=T0, check=lost)


def test_same_content_is_uploaded_again_and_published_after_orphan_deletion(state, corpus, tmp_path):
    manager, store = state
    _, deps, _ = corpus
    manager.push()
    store.put(KEY, BODY)  # Previous failed round uploaded without publishing a snapshot.
    report = manager.cleanup([make_spec()], now=T0, check=lambda: None,
                             raw_enabled=True, orphan_raw_enabled=True)
    assert report["orphan_raw_deleted"] == 1 and not store.exists(KEY)
    deps.raw.put(BODY)  # Fetch the same bytes again, then parse and publish normally.
    snap = make_snapshot(data=BODY, snapshot_id="retry")
    deps.manifest.record_snapshot(snap)
    assert deps.parse_pending(deps.pipeline_deps).parsed == [snap.snapshot_id]
    store.reset()
    candidate = manager.push(retain_local=True)
    assert candidate.generation == 2 and KEY in store.puts
    assert store.get(KEY)[0] == BODY
    assert manager.restore(tmp_path / "restored", raw="local")["verified"]
    assert (tmp_path / "restored" / KEY).read_bytes() == BODY
