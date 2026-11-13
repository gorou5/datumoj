"""Capacity failures, orphan recovery and raw validation with offline storage."""

import json
from contextlib import closing
from datetime import timedelta
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, cloud, db_usage
from datumoj.rikolto.capacity import WEEKLY_REPORT_RESERVE
from datumoj.rikolto.core.contracts import PreconditionFailed
from datumoj.rikolto.core.lease import LEASE_KEY
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import PublishState
from datumoj.rikolto.core.nas_receipt import RECEIPT_KEY, read_receipt
from datumoj.rikolto.core.state import StateError, StateManager, _raw_refs, raw_retention
from datumoj.rikolto.core.state_s3 import S3StateObjectStore
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec
from tests.core.test_nas_receipt import local_raw
from tests.regressions.test_audit_131 import archive as archive  # noqa: PLC0414 -- Offline fixture.
from tests.regressions.test_audit_131 import cleanup, publish, raw_key, record
from tests.test_cloud_capacity import capacity_inventory
from tests.test_cloud_embed import invoke_embed, seed
from tests.test_cloud_run import invoke, released, remote
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Offline fixture.


def evidence(tmp_path, name, result):
    (tmp_path / f"evidence-{name}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))


@pytest.mark.parametrize("mode", ["run", "embed"])
def test_unknown_capacity_is_a_failed_job(world, monkeypatch, mode):
    def unavailable(store):
        raise OSError("synthetic unavailable inventory")

    monkeypatch.setattr(cloud, "_usage_objects", unavailable)
    result = invoke(world)[0] if mode == "run" else invoke_embed(world)
    assert result["status"] == "failed" and result["exit_status"] != 0
    assert result["reason"] == "capacity_unknown" and result["generation"] is None
    assert not world.components
    evidence(world.tmp, f"statistics-failure-{mode}", result)


@pytest.mark.parametrize("failure", ["failures", "timed_out"])
def test_stop_cleanup_failure_is_a_failed_job(world, monkeypatch, failure):
    assert invoke(world, "first")[0]["generation"] == 1
    components = len(world.components)
    capacity_inventory(monkeypatch, 9_100_000_000)
    cleanup_method = StateManager.cleanup

    def failed(manager, *args, **kwargs):
        return {**cleanup_method(manager, *args, **kwargs), failure: 1 if failure == "failures" else True}

    monkeypatch.setattr(StateManager, "cleanup", failed)
    result, _ = invoke(world)
    assert result["status"] == "failed" and result["exit_status"] != 0
    assert result["reason"] == "capacity_stop_cleanup_failed"
    assert len(world.components) == components
    if failure == "timed_out":
        assert result["cleanup"]["failures"] == 0
    evidence(world.tmp, f"stop-cleanup-{failure}", result)
    released(world)


@pytest.mark.parametrize("mode,existing", [("run", False), ("run", True), ("embed", True)])
def test_stop_resumes_only_uploaded_orphan_then_cleans_again(world, monkeypatch, mode, existing):
    call = (lambda name: invoke(world, name)[0]) if mode == "run" else (lambda name: invoke_embed(world, name))
    if mode == "embed":
        seed(world)
        existing = True
    elif existing:
        assert call("first")["generation"] == 1
    number = 1 if existing else 0
    publish_method = StateManager._publish

    def lost_cas(*args):
        raise PreconditionFailed("synthetic lost CAS")

    monkeypatch.setattr(StateManager, "_publish", lost_cas)
    world.clock[0] += timedelta(hours=1)
    assert call("orphan")["status"] == "failed"
    store = remote(world)
    candidate = store.get(f"generations/{number + 1}.json")
    assert candidate is not None
    monkeypatch.setattr(StateManager, "_publish", publish_method)
    capacity_inventory(monkeypatch, 9_100_000_000)
    writes = []
    put = S3StateObjectStore._put

    def tracked(store, key, data, **conditions):
        writes.append(key)
        return put(store, key, data, **conditions)

    monkeypatch.setattr(S3StateObjectStore, "_put", tracked)
    components = len(world.components)
    result = call("resumed")
    assert result["status"] == "skipped" and result["exit_status"] == 0
    assert result["reason"] == "capacity_stop" and result["orphan_resumed"] is True
    assert result["cleanup"]["failures"] == 0 and not result["cleanup"]["timed_out"]
    assert store.get(f"generations/{number + 1}.json") == candidate
    assert json.loads(store.get("current.json")[0])["generation"] == number + 1
    assert store.get(f"generations/{number + 2}.json") is None
    assert set(writes) == {LEASE_KEY, "current.json"}
    assert len(world.components) == components
    evidence(world.tmp, f"stop-orphan-{mode}-{number}", result)
    # The following run can clean normally, with no orphan refusal or publication.
    writes.clear()
    again, _ = invoke(world, "next-cleanup")
    assert again["reason"] == "capacity_stop" and again["status"] == "skipped"
    assert again["cleanup"]["failures"] == 0 and "orphan_resumed" not in again
    assert set(writes) == {LEASE_KEY}
    released(world)


def test_weekly_report_uses_its_own_small_optional_budget(world, monkeypatch):
    capacity_inventory(monkeypatch, 7_500_000_000)
    manager = SimpleNamespace(store=None)
    monkeypatch.setattr(cloud, "_project_storage_bytes", lambda store: 7_500_000_000)
    decision = cloud._round_capacity(manager, weekly_report=True)
    assert decision["estimated_new_bytes"] == WEEKLY_REPORT_RESERVE
    assert decision["reserve_bytes"] == 0 and decision["allowed"]
    result, _ = invoke(world)
    assert result["status"] == "completed" and result["weekly_report_key"] is not None
    assert remote(world).get(result["weekly_report_key"]) is not None
    evidence(world.tmp, "weekly-7_5gb", {"weekly_capacity": decision, "result": result})


def test_vector_usage_reads_every_page(tmp_path, monkeypatch):
    import lancedb

    names = ["chunks", *[f"chunks_g{i}" for i in range(1, 301)]]
    def list_tables(page_token=None):
        start = int(page_token or 0)
        end = start + 100
        return SimpleNamespace(tables=names[start:end], page_token=str(end) if end < len(names) else None)

    opened = []
    class Table:
        def search(self):
            return self

        def select(self, columns):
            return self

        def to_batches(self, **kwargs):
            return []

    def open_table(name):
        opened.append(name)
        return Table()

    monkeypatch.setattr(lancedb, "connect", lambda path: SimpleNamespace(
        list_tables=list_tables, open_table=open_table))
    (tmp_path / "_meta.json").write_text('{"generation":300}')
    assert db_usage._vectors(tmp_path, None)["rows"] == 0
    assert opened == names


@pytest.mark.parametrize("mode", ["remote", "local"])
@pytest.mark.parametrize("protection", ["latest", "pending", "failed"])
@pytest.mark.parametrize("tamper", ["missing", "size"])
def test_marked_protected_raw_is_required_on_restore(archive, monkeypatch, mode, protection, tamper):
    snap = record(archive, "protected", resource_type="feed", parsed=protection == "latest")
    if protection == "failed":
        archive.deps.manifest.record_publish_state(snap.snapshot_id, PublishState.failed)
    publish(archive, T0, monkeypatch)
    assert snap.sha256 in archive.deps.manifest.raw_markers()
    if tamper == "missing":
        archive.store.delete_if_match(raw_key(snap), archive.store.get(raw_key(snap))[1])
    else:
        archive.store.put(raw_key(snap), b"wrong size")
    target = archive.root / "restore"
    message = "远端缺少 manifest 引用的 raw" if tamper == "missing" else "raw.*校验失败"
    with pytest.raises(StateError, match=message) as error:
        archive.manager.restore(target, raw=mode)
    assert not target.exists()
    evidence(archive.root, f"restore-{mode}-{protection}-{tamper}", {"error": str(error.value), "raw_mode": mode})


def test_staged_raw_remains_in_required_set(tmp_path):
    snap = make_snapshot()
    with closing(SqliteManifest(tmp_path / "manifest.sqlite")) as manifest:
        manifest.record_snapshot(snap, {"resource_type": "entry"})
        manifest.record_publish_state(snap.snapshot_id, PublishState.staged)
        manifest.mark_raw_optional(snap.sha256, T0)
    assert _raw_refs(tmp_path, specs=[make_spec(scope={"raw_expire_latest_types": ["entry"]})]) == {snap.sha256}


@pytest.mark.parametrize("mode", ["remote", "local"])
def test_marked_unprotected_history_can_be_absent(archive, monkeypatch, mode):
    old = record(archive, "old-feed", resource_type="feed")
    record(archive, "latest-feed", resource_type="feed", url=old.resource_url, when=T0 + timedelta(hours=1))
    publish(archive, T0 + timedelta(hours=1), monkeypatch)
    archive.store.delete_if_match(raw_key(old), archive.store.get(raw_key(old))[1])
    assert archive.manager.restore(archive.root / "restore", raw=mode)["verified"]


def test_one_day_source_expires_at_24_hours(archive, monkeypatch):
    snap = record(archive, "one-day")
    archive.specs[:] = [archive.spec.model_copy(update={"scope": {
        "raw_retention_days": 1, "raw_expire_latest_types": ["entry"],
    }})]
    publish(archive, T0, monkeypatch)
    expiry = raw_retention(archive.manager.data_dir / "manifest.sqlite", archive.specs, T0)
    assert expiry[snap.sha256] == T0 + timedelta(hours=24)
    assert cleanup(archive, T0 + timedelta(hours=24) - timedelta(microseconds=1), raw_enabled=True)["raw_deleted"] == 0
    assert cleanup(archive, T0 + timedelta(hours=24), raw_enabled=True)["raw_deleted_by_age"] == 1


@pytest.mark.parametrize("qualified", [False, True])
def test_receipt_command_summary_or_fixed_empty_reason(tmp_path, monkeypatch, qualified):
    store = FakeStateObjectStore()
    store.put(RECEIPT_KEY, b"previous receipt")
    if qualified:
        root, sha = local_raw(tmp_path)
    else:
        root = tmp_path / "empty"
        root.mkdir()
        (root / "README").write_text("not a raw file")
    monkeypatch.setattr(cli.wiring, "build_state", lambda root: SimpleNamespace(store=store))
    response = CliRunner().invoke(cli.app, ["nas", "receipt", str(root)])
    result = json.loads(response.output)
    if qualified:
        assert response.exit_code == 0 and result["count"] == 1
        assert set(result) == {"count", "archived_at"} and sha not in response.output
        assert json.loads(store.get(RECEIPT_KEY)[0])["sha256"] == [sha]
    else:
        assert response.exit_code != 0 and result == {"reason": "no_qualified_raw_files"}
        assert store.get(RECEIPT_KEY)[0] == b"previous receipt"


def test_receipt_read_failure_is_distinct_and_visible_in_cleanup(archive, monkeypatch):
    publish(archive, T0, monkeypatch)
    original = archive.store.get
    def get(key):
        if key == RECEIPT_KEY:
            raise OSError("synthetic private error")
        return original(key)

    monkeypatch.setattr(archive.store, "get", get)
    assert read_receipt(archive.store, now=T0) == ("unreadable", None)
    assert cleanup(archive, T0)["receipt_status"] == "unreadable"
    monkeypatch.setattr(archive.store, "get", original)
    archive.store.put(RECEIPT_KEY, b"invalid JSON")
    assert read_receipt(archive.store, now=T0) == ("invalid", None)
