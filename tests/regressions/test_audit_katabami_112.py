"""Publication protection, completed-source gates and raw key safety; offline only."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.capacity import inventory
from datumoj.rikolto.core import state as state_module
from datumoj.rikolto.core.contracts import StateObject
from datumoj.rikolto.core.models import PublishState
from datumoj.rikolto.core.state import StateError, StateManager
from datumoj.rikolto.testing.fakes import FakeRawStore, FakeRegistry, FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0, make_spec
from tests.regressions.test_audit_131 import archive as archive  # noqa: PLC0414 -- Synthetic offline fixture.
from tests.regressions.test_audit_131 import raw_key, record


def evidence(tmp_path, name, result):
    (tmp_path / f"{name}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))


@pytest.mark.parametrize("protection", ["latest", "pending", "failed"])
def test_marked_protected_raw_missing_everywhere_blocks_new_generation(archive, tmp_path, protection):
    manager = StateManager(archive.store, archive.manager.data_dir, specs=archive.specs)
    manager.push()
    previous = archive.store.get("current.json")
    snap = record(archive, protection, resource_type="feed" if protection == "latest" else "entry",
                  parsed=protection == "latest")
    if protection == "failed":
        archive.deps.manifest.record_publish_state(snap.snapshot_id, PublishState.failed, "synthetic failure")
    manager.mark_raw(archive.specs, T0)
    assert snap.sha256 in archive.deps.manifest.raw_markers()
    (manager.data_dir / raw_key(snap)).unlink()
    with pytest.raises(StateError, match="raw 在本机和远端均缺失") as failure:
        manager.push(retain_local=True)
    assert archive.store.get("current.json") == previous
    assert archive.store.get("generations/2.json") is None
    evidence(tmp_path, f"missing-protected-{protection}", {
        "error": str(failure.value), "current_generation": 1, "new_generation_written": False,
        "local_raw_present": False, "remote_raw_present": archive.store.exists(raw_key(snap)),
    })


@pytest.mark.parametrize("injected", [True, False])
def test_marked_protected_raw_uploads_and_restores_with_same_specs(archive, monkeypatch, tmp_path, injected):
    snap = record(archive, "protected", resource_type="feed")
    manager = StateManager(archive.store, archive.manager.data_dir, specs=archive.specs if injected else None)
    manager.mark_raw(archive.specs, T0)
    loads = []

    def registry(_path):
        assert not injected, "injected specs must take precedence over repository loading"
        loads.append(True)
        return FakeRegistry(archive.specs)

    monkeypatch.setattr(state_module, "load_registry", registry)
    assert not archive.store.exists(raw_key(snap))
    generation = manager.push(retain_local=True)
    assert len(loads) == int(not injected)  # Publication and its verification share one load.
    assert archive.store.get(raw_key(snap))[0] == (manager.data_dir / raw_key(snap)).read_bytes()
    for mode in ("remote", "local"):
        report = manager.restore(tmp_path / f"restore-{mode}", raw=mode)
        assert report["verified"] and report["raw_objects"] == 1 and report["sampled_chunks"] > 0
    evidence(tmp_path, "protected-upload", {"generation": generation.generation, "uploaded": True,
                                           "next_restore_verified": True})


def test_post_publication_checks_preexisting_marked_protected_raw(archive, monkeypatch):
    snap = record(archive, "protected", resource_type="feed")
    manager = StateManager(archive.store, archive.manager.data_dir, specs=archive.specs)
    manager.mark_raw(archive.specs, T0)
    manager.push()
    publish = manager._publish

    def disappear(candidate, etag):
        publish(candidate, etag)
        archive.store.delete_if_match(raw_key(snap), archive.store.get(raw_key(snap))[1])

    monkeypatch.setattr(manager, "_publish", disappear)
    with pytest.raises(StateError, match="发布后 raw 缺失或大小不符"):
        manager.push(retain_local=True)
    assert not (manager.data_dir / ".observation-generations/2/verified.json").exists()


def test_raw_marker_does_not_skip_citation_dependency_check(archive, monkeypatch):
    record(archive, "citation", resource_type="feed")
    manager = StateManager(archive.store, archive.manager.data_dir, specs=archive.specs)
    manager.mark_raw(archive.specs, T0)
    monkeypatch.setattr(state_module, "document_snapshot", lambda *_args: None)
    with pytest.raises(StateError, match="块的出处无法还原到快照和 raw"):
        manager.push()
    assert archive.store.get("generations/1.json") is None
    assert archive.store.get("current.json") is None


class LogicalBytes(bytes):
    """Use small synthetic bodies to simulate large successful raw uploads."""

    def __new__(cls, body, size):
        data = super().__new__(cls, body)
        data.size = size
        return data

    def __len__(self):
        return self.size


def run_round(tmp_path, monkeypatch, initial, growth):
    store, raw = FakeStateObjectStore(), FakeRawStore()
    specs = [make_spec(f"jp-completed{i}") for i in range(len(growth))]
    events, scans = [], []
    objects = store.list_objects

    def listing(prefix=""):
        if not prefix:
            scans.append(tuple(events))
        rows = objects(prefix)
        if not prefix:
            rows.append(StateObject("other/synthetic", initial - sum(row.size_bytes for row in rows), '"size"'))
        return rows

    def source(deps, spec, _scheduler, remaining, *_args):
        index = len(events)
        assert spec.id == specs[index].id
        events.append(spec.id)
        deps.raw.put(LogicalBytes(spec.id.encode(), growth[index]))
        return {"source_id": spec.id, "exit_status": 0, "failures": {},
                "new": 1, "unchanged": 0, "parsed": 1}, remaining

    def backfill(*_args, **_kwargs):
        events.append("backfill")
        return 0

    def weekly(*_args):
        events.append("weekly_report")
        return "reports/weekly/synthetic.md"

    manager = SimpleNamespace(store=store, writer=SimpleNamespace(kind="nas"), list=list,
                              mark_raw=lambda *_args: 0, push=lambda: SimpleNamespace(generation=1),
                              cleanup=lambda *_args, **_kwargs: {
                                  "failures": 0, "timed_out": False, "total_bytes": initial + sum(growth),
                              })
    deps = SimpleNamespace(raw=raw, registry=FakeRegistry(specs), require=lambda _feature: None)
    scheduler = SimpleNamespace(sources={spec.id: spec for spec in specs})
    monkeypatch.setattr(store, "list_objects", listing)
    monkeypatch.setattr(cloud.wiring, "build_state", lambda _root: manager)
    monkeypatch.setattr(cloud.wiring, "build", lambda _root: deps)
    monkeypatch.setattr(cloud, "Scheduler", lambda *_args: scheduler)
    monkeypatch.setattr(cloud, "_due", lambda *_args: [spec.id for spec in specs])
    monkeypatch.setattr(cloud, "_source", source)
    monkeypatch.setattr(cloud, "_recover", lambda *_args: 0)
    monkeypatch.setattr(cloud, "_backfill", backfill)
    monkeypatch.setattr(cloud, "maintain_vectors", lambda *_args: None)
    monkeypatch.setattr(cloud, "_source_health", lambda *_args: [])
    monkeypatch.setattr(cloud, "publish_previous_week_report", weekly)
    result = cloud.run(work_dir=tmp_path / "work")
    assert all(not history for history in scans)  # No full bucket inventory during the source loop.
    assert result["status"] == "completed" and result["generation"] == 1
    admission = cloud.gate(initial, 0, "essential", reserve_bytes=500_000_000)
    assert {key: result["capacity"][key] for key in ("allowed", "tier", "reason")} == {
        key: admission[key] for key in ("allowed", "tier", "reason")
    }
    return result, events, specs


@pytest.mark.parametrize("initial", [7_900_000_000, 8_200_000_000])
def test_completed_source_freezes_optional_work_but_keeps_crawling(tmp_path, monkeypatch, initial):
    # At 8.2 GB the unchanged 0.5 GB reserve already places admission in freeze.
    # 7.9 GB also exercises a new report -> freeze transition after the first upload.
    result, events, specs = run_round(tmp_path, monkeypatch, initial, [200_000_000, 50_000_000])
    capacity = result["capacity"]
    assert events == [spec.id for spec in specs]
    assert capacity["mid_round_freeze"] and not capacity.get("mid_round_stop")
    assert capacity["uploaded_raw_bytes"] == 250_000_000
    assert capacity["mid_round_projected_bytes"] == initial + 250_000_000 + 500_000_000
    assert capacity["projected_bytes"] == initial + 500_000_000
    assert capacity["skipped_steps"] == ["backfill", "weekly_report"]
    assert capacity["skipped_sources"] == [] and result["weekly_report_key"] is None
    evidence(tmp_path, f"mid-round-freeze-{initial}", result)


def test_last_source_crossing_stop_is_reported(tmp_path, monkeypatch):
    result, events, specs = run_round(tmp_path, monkeypatch, 8_200_000_000, [200_000_000, 700_000_000])
    capacity = result["capacity"]
    assert events == [spec.id for spec in specs]
    assert capacity["mid_round_stop"] and capacity["next_round"] == "cleanup_only"
    assert capacity["uploaded_raw_bytes"] == 900_000_000
    assert capacity["projected_bytes"] == 9_600_000_000
    assert capacity["skipped_steps"] == ["backfill", "weekly_report"]
    assert capacity["skipped_sources"] == [] and result["weekly_report_key"] is None
    evidence(tmp_path, "last-source-stop", result)


def test_unrecognized_raw_keys_survive_and_are_counted_only(archive):
    manager, store = archive.manager, archive.store
    current = manager.push()
    body = b"Synthetic unreferenced raw"
    sha = hashlib.sha256(body).hexdigest()
    canonical = f"raw/{sha[:2]}/{sha}"
    invalid = ["raw/readme.txt", f"raw/zz/{sha}", f"raw/00/{'1' * 64}",
               f"raw/AB/{'A' * 64}", f"raw/{sha[:2]}/{sha[:-1]}",
               f"raw/nested/{sha[:2]}/{sha}", f"raw/{sha[:2]}/{sha}/extra"]
    for key in [canonical, *invalid]:
        store.put(key, body)
    usage = inventory(store.list_objects(""), [current], 1, manager.data_dir / "manifest.sqlite",
                      archive.specs, now=T0)
    assert usage["unrecognized_raw_keys"] == len(invalid)
    assert usage["reclaimable_by_source"]["unreferenced"] == {"objects": 1, "bytes": len(body)}
    report = manager.cleanup(archive.specs, now=T0, check=lambda: None,
                             raw_enabled=True, orphan_raw_enabled=True)
    assert report["failures"] == 0 and report["unrecognized_raw_keys"] == len(invalid)
    assert report["reclaimable_orphan_raw"] == report["orphan_raw_deleted"] == 1
    assert report["reclaimable_orphan_raw_bytes"] == report["orphan_raw_deleted_bytes"] == len(body)
    assert not store.exists(canonical) and all(store.exists(key) for key in invalid)
    assert all(key not in json.dumps(report) for key in invalid)
