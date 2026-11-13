"""Retained raw provenance, NAS accounting and bounded reports; fake storage only."""

import hashlib
import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.capacity import WEEKLY_REPORT_RESERVE
from datumoj.rikolto.core import state as state_module
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.nas_receipt import RECEIPT_KEY
from datumoj.rikolto.core.state import RETENTION_WINDOW, StateManager, plan_cleanup
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0
from tests.regressions.test_audit_131 import archive as archive  # noqa: PLC0414 -- Offline fixture.
from tests.regressions.test_audit_131 import publish, raw_key, record
from tests.regressions.test_audit_katabami_112 import run_round
from tests.test_cloud_cleanup_only import cleanup_world as cleanup_world  # noqa: PLC0414 -- Offline fixture.
from tests.test_cloud_reports import _deps

HOUR = timedelta(hours=1)


@pytest.fixture
def rollback_world(archive, monkeypatch, tmp_path):
    first_raw = record(archive, "A", resource_type="feed")
    first, _ = publish(archive, T0, monkeypatch)
    second_raw = record(archive, "B", when=T0 + HOUR, resource_type="feed")
    second, _ = publish(archive, T0 + HOUR, monkeypatch)
    restored = tmp_path / "rollback-g1"
    archive.manager.restore(restored, first.generation, raw="local")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return T0 + 2 * HOUR

    monkeypatch.setattr(state_module, "datetime", Clock)
    manager = StateManager(archive.store, restored, specs=archive.specs)
    third = manager.push()
    assert third.generation == 3
    return SimpleNamespace(manager=manager, store=archive.store, specs=archive.specs,
                           first_raw=first_raw, second=second, second_raw=second_raw)


def test_rollback_publish_keeps_raw_referenced_by_retained_generation(rollback_world, tmp_path):
    world = rollback_world
    before = world.manager.cleanup(world.specs, now=T0 + 2 * HOUR, check=lambda: None,
                                   raw_enabled=True, orphan_raw_enabled=True)
    assert before["failures"] == 0 and before["orphan_raw_skip_reason"] is None
    assert before["reclaimable_orphan_raw"] == before["orphan_raw_deleted"] == 0
    assert world.store.exists(raw_key(world.second_raw))
    assert world.manager.restore(tmp_path / "restore-g2", 2, raw="local")["verified"]
    # The inclusive boundary still protects B. It becomes an orphan only later.
    boundary = T0 + HOUR + RETENTION_WINDOW
    assert world.manager.cleanup(world.specs, now=boundary, check=lambda: None,
                                 enabled=False)["reclaimable_orphan_raw"] == 0
    after = world.manager.cleanup(world.specs, now=boundary + timedelta(microseconds=1),
                                  check=lambda: None, raw_enabled=True, orphan_raw_enabled=True)
    assert after["failures"] == 0 and after["orphan_raw_deleted"] == 1
    assert not world.store.exists(raw_key(world.second_raw))
    print("ROLLBACK_RAW", json.dumps({"retained": before, "expired": after}))


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_unavailable_retained_manifest_skips_only_orphan_class(rollback_world, monkeypatch, damage):
    world = rollback_world
    block = next(copy for copy in world.second.stores if copy.name == "manifest.sqlite.blocks/0")
    if damage == "missing":
        world.store.delete_if_match(block.key, world.store.get(block.key)[1])
    else:
        corrupted = bytearray(world.store.get(block.key)[0])
        corrupted[0] ^= 1  # Preserve size so the SHA256 check must reject it.
        world.store.put(block.key, bytes(corrupted))
    # Include a second real orphan: uncertainty must veto the whole class.
    body = b"Synthetic unreferenced raw"
    sha = hashlib.sha256(body).hexdigest()
    key = f"raw/{sha[:2]}/{sha}"
    world.store.put(key, body)
    world.store.put("copies/sha256/unused", b"unused")
    now = T0 + 2 * HOUR
    world.store.put(RECEIPT_KEY, json.dumps({"version": 1, "archived_at": now.astimezone(UTC).isoformat(),
                                           "sha256": [world.first_raw.sha256]}).encode())
    specs = [spec.model_copy(update={"scope": {**spec.scope, "raw_expire_latest_types": ["feed"]}})
             for spec in world.specs]
    reads = []
    get = world.store.get

    def tracked(key):
        reads.append(key)
        return get(key)

    monkeypatch.setattr(world.store, "get", tracked)
    report = world.manager.cleanup(specs, now=now, check=lambda: None,
                                   raw_enabled=True, orphan_raw_enabled=True)
    assert report["failures"] == 0 and report["orphan_raw_skip_reason"] == "retained_refs_unavailable"
    assert report["orphan_raw_deleted"] == report["reclaimable_orphan_raw"] == 0
    assert report["copies_deleted"] == 1
    assert report["raw_deleted"] == report["raw_deleted_by_receipt"] == 1
    assert world.store.exists(key) and world.store.exists(raw_key(world.second_raw))
    manifest_keys = {copy.key for g in world.manager.list() for copy in g.stores
                     if copy.name.startswith("manifest.sqlite")}
    assert all(key in manifest_keys for key in reads if key.startswith("copies/"))
    assert not any(key.startswith("raw/") for key in reads)
    print("UNAVAILABLE_REFS", json.dumps(report))


def test_retained_manifest_reads_reuse_verified_blocks(rollback_world, monkeypatch, tmp_path):
    world = rollback_world
    world.manager.cache_dir = tmp_path / "cache"
    expected = world.manager.generation_raw_references(world.second)
    assert world.second_raw.sha256 in expected

    def forbidden(_key):
        pytest.fail("verified cached manifest blocks should be reused")

    monkeypatch.setattr(world.manager, "_get", forbidden)
    assert world.manager.generation_raw_references(world.second) == expected
    assert world.manager.last_timings["reused_bytes"] > 0


@pytest.mark.parametrize("reference", ["snapshot", "optional_snapshot", "marker_only"])
def test_retained_reference_includes_optional_and_marker_only(tmp_path, reference):
    from datumoj.rikolto.testing.samples import make_snapshot
    from tests.test_capacity import copy, generation, obj

    path = tmp_path / "manifest.sqlite"
    old_path = tmp_path / "old" / "manifest.sqlite"
    old_path.parent.mkdir()
    snap = make_snapshot()
    with closing(SqliteManifest(path)):
        pass
    with closing(SqliteManifest(old_path)) as manifest:
        if reference != "marker_only":
            manifest.record_snapshot(snap)
        if reference != "snapshot":
            manifest.mark_raw_optional(snap.sha256, T0)
    store = FakeStateObjectStore()
    part = copy("manifest.sqlite", old_path.read_bytes(), "sqlite")
    store.put(part.key, old_path.read_bytes())
    old = generation(1, [part])
    current = generation(2, [part])
    raw = obj(raw_key(snap), snap.size)
    manager = StateManager(store, tmp_path)
    plan = plan_cleanup([raw], [old, current], 2, path, [], now=T0,
                        retained_refs=manager.generation_raw_references)
    assert plan["orphan_raw"] == [] and plan["orphan_raw_skip_reason"] is None


@pytest.mark.parametrize("receipt_status", ["ok", "missing", "unreadable", "invalid", "stale"])
def test_usage_receipt_matches_cleanup_preview(cleanup_world, monkeypatch, receipt_status):
    world = cleanup_world
    now = T0 + 10 * timedelta(days=1)
    get = world.store.get
    if receipt_status != "missing":
        when = now - (3 * timedelta(days=1) if receipt_status == "stale" else timedelta(0))
        content = json.dumps({"version": 1, "archived_at": when.astimezone(UTC).isoformat(),
                              "sha256": [world.young.sha256, world.fresh.sha256]}).encode()
        world.store.put(RECEIPT_KEY, b"invalid" if receipt_status == "invalid" else content)
    if receipt_status == "unreadable":
        def unreadable(key):
            if key == RECEIPT_KEY:
                raise OSError("synthetic receipt unavailable")
            return get(key)
        monkeypatch.setattr(world.store, "get", unreadable)
    usage = cloud.usage(work_dir=world.root / "usage-receipt")
    preview = cloud.cleanup_only(work_dir=world.root / "preview-receipt", max_minutes=1)
    assert "exit_status" not in usage and preview["exit_status"] == 0
    report = preview["cleanup"]
    assert report["receipt_status"] == receipt_status
    assert usage["reclaimable"]["raw"] == {
        "objects": report["reclaimable_raw"], "bytes": report["reclaimable_raw_bytes"],
    }
    assert usage["reclaimable_by_source"] == report["by_source"]
    assert report["reclaimable_raw"] == (3 if receipt_status == "ok" else 1)
    assert ("unrecognized_raw_keys" in usage) is ("unrecognized_raw_keys" in report)
    print("RECEIPT_COMPARISON", json.dumps({"receipt_status": receipt_status,
                                           "usage": usage["reclaimable"], "cleanup": report}))


@pytest.mark.parametrize("long", [False, True])
def test_uploaded_weekly_report_is_bounded_in_utf8(tmp_path, monkeypatch, long):
    store = FakeStateObjectStore()
    # Multibyte content tests bytes, and a single huge event must be omitted whole.
    lines = ["# 周报\n", "- 摘要：小事件。\n", "- 摘要：" + "あ" * WEEKLY_REPORT_RESERVE + "\n",
             "- 标题：最后事件；摘要：变化。\n", "- [warning] 提醒中也可能包含摘要：文本。\n"] if long else [
                 "# 周报\n", "- 摘要：正常事件。\n",
             ]
    content = "".join(lines)
    monkeypatch.setattr(cloud, "weekly_report", lambda *_args: content)
    key = cloud.publish_previous_week_report(store, _deps(tmp_path), T0)
    data = store.get(key)[0]
    assert len(data) <= WEEKLY_REPORT_RESERVE
    text = data.decode("utf-8")
    if long:
        assert text.startswith("".join(lines[:2])) and text.endswith("省略 2 条事件。\n")
    else:
        assert data == content.encode("utf-8")
    print("WEEKLY_REPORT", json.dumps({"long": long, "input_bytes": len(content.encode()),
                                       "uploaded_bytes": len(data), "tail": text[-100:]}, ensure_ascii=False))


def test_weekly_report_near_limit_keeps_only_complete_events():
    line = "- 摘要：" + "日本語" * 100 + "\n"
    data = cloud._weekly_report_bytes("# 周报\n" + line * 2000)
    text = data.decode("utf-8")
    kept = text.count(line)
    assert WEEKLY_REPORT_RESERVE - len(line.encode()) - 100 < len(data) <= WEEKLY_REPORT_RESERVE
    assert text.endswith(f"省略 {2000 - kept} 条事件。\n")
    # At the exact byte limit, preserve the body byte-for-byte.
    exact = "あ" * (WEEKLY_REPORT_RESERVE // 3) + "x" * (WEEKLY_REPORT_RESERVE % 3)
    assert cloud._weekly_report_bytes(exact) == exact.encode()


def test_mid_round_stop_alone_skips_optional_work(tmp_path, monkeypatch):
    gate = cloud.gate
    round_capacity = cloud._round_capacity
    calls = 0

    def stop_at_source(*args, **kwargs):
        nonlocal calls
        decision = gate(*args, **kwargs)
        calls += 1
        if calls == 3:  # Placeholder and admission succeeded; stop before the first source.
            return {**decision, "tier": "stop", "allowed": False}
        return decision

    def capacity(manager, **kwargs):
        assert not kwargs.get("weekly_report"), "stop alone must skip even the weekly write gate"
        return round_capacity(manager, **kwargs)

    monkeypatch.setattr(cloud, "gate", stop_at_source)
    monkeypatch.setattr(cloud, "_round_capacity", capacity)
    result, events, _ = run_round(tmp_path, monkeypatch, 1_000_000, [10])
    assert result["capacity"]["mid_round_stop"] and not result["capacity"].get("mid_round_freeze")
    assert events == [] and result["weekly_report_key"] is None
    assert result["capacity"]["skipped_steps"] == ["crawl", "backfill", "weekly_report"]


def test_usage_and_cleanup_share_retained_orphan_protection(rollback_world, monkeypatch, tmp_path):
    world = rollback_world
    monkeypatch.setattr(cloud.wiring, "build_state", lambda root: StateManager(world.store, root))
    monkeypatch.setattr(cloud, "utcnow", lambda: T0 + 2 * HOUR)
    usage = cloud.usage(work_dir=tmp_path / "usage")
    report = world.manager.cleanup(world.specs, now=T0 + 2 * HOUR, enabled=False, check=lambda: None)
    assert "exit_status" not in usage
    assert usage["reclaimable_by_source"] == report["by_source"]
    assert report["reclaimable_orphan_raw"] == 0
