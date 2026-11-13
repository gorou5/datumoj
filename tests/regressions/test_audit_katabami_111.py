"""109/110 review follow-ups; synthetic state, paginated stubs and moto only."""

import io
import json
from collections import Counter
from datetime import timedelta
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, cloud, db_usage, wiring
from datumoj.rikolto.core import state as state_module
from datumoj.rikolto.core.contracts import PreconditionFailed, RegistryError
from datumoj.rikolto.core.nas_receipt import RECEIPT_KEY
from datumoj.rikolto.core.rawstore_s3 import S3RawStore
from datumoj.rikolto.core.state import StateError, StateManager
from datumoj.rikolto.core.storage import load_storage_config
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.testing.samples import T0
from tests.core.test_nas_receipt import receipt_bytes
from tests.regressions.test_audit_131 import archive as archive  # noqa: PLC0414 -- Synthetic state fixture.
from tests.regressions.test_audit_131 import cleanup, publish, raw_key, record
from tests.test_cloud_capacity import capacity_inventory
from tests.test_cloud_run import BODY, invoke, released
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Offline moto/loopback fixture.


@pytest.mark.parametrize("end", [None, ""])
@pytest.mark.parametrize("pages", [1, 3])
def test_lance_pagination_stops_on_both_empty_tokens(tmp_path, monkeypatch, end, pages):
    (tmp_path / "_meta.json").write_text(json.dumps({"generation": 0}))
    calls = []
    names = ["chunks", "chunks_g1", "chunks_g2"][:pages]

    def list_tables(page_token=None):
        index = 0 if page_token is None else int(page_token)
        calls.append(page_token)
        assert len(calls) <= pages  # A repeated empty token must fail instead of hanging.
        return SimpleNamespace(tables=[names[index]], page_token=str(index + 1) if index + 1 < pages else end)

    batch = SimpleNamespace(to_pylist=lambda: [{"chunk_id": "synthetic", "doc_key": "jp-test:doc", "text_hash": "0"}])
    query = SimpleNamespace(to_batches=lambda **_kwargs: [batch])
    table = SimpleNamespace(search=lambda: SimpleNamespace(select=lambda _columns: query))
    db = SimpleNamespace(list_tables=list_tables, open_table=lambda _name: table)
    monkeypatch.setattr(state_module.lancedb, "connect", lambda _path: db)
    result = db_usage._vectors(tmp_path, None)
    assert calls == [None, *map(str, range(1, pages))]
    assert result["rows"] == pages and result["active_table_rows"] == 1
    assert result["retained_table_rows"] == pages - 1


def unavailable_registry(_path):
    raise RegistryError("synthetic registry unavailable")


@pytest.mark.parametrize("factory", [StateManager, wiring.build_state])
def test_injected_specs_restore_without_repository_registry(archive, monkeypatch, factory):
    record(archive, "injected", resource_type="feed")
    generation = archive.manager.push()
    if factory is StateManager:
        manager = factory(archive.store, archive.manager.data_dir, specs=archive.specs)
    else:
        monkeypatch.setattr(wiring, "build_state_store", lambda _config: archive.store)
        manager = factory(archive.manager.data_dir, specs=archive.specs)
    monkeypatch.setattr(state_module, "load_registry", unavailable_registry)
    monkeypatch.setattr("datumoj.rikolto.core.registry.load_registry", unavailable_registry)
    assert manager.restore(archive.root / "injected", generation.generation, raw="remote")["verified"]


@pytest.mark.parametrize("entry", ["core", "wiring", "cli"])
def test_default_registry_failure_retains_restore_reason(archive, monkeypatch, entry):
    record(archive, "registry", resource_type="feed")
    archive.manager.push()
    monkeypatch.setattr(state_module, "load_registry", unavailable_registry)
    monkeypatch.setattr("datumoj.rikolto.core.registry.load_registry", unavailable_registry)
    monkeypatch.setattr(wiring, "build_state_store", lambda _config: archive.store)
    target = archive.root / "registry-failure"
    reason = "状态恢复校验失败，未交出可用目录"
    if entry == "cli":
        response = CliRunner().invoke(cli.app, ["state", "restore", "--to", str(target), "--raw", "remote"])
        assert response.exit_code == 1
        assert json.loads(response.output)["error"] == "状态操作失败，请检查配置、代次完整性和写入状态"
    else:
        manager = archive.manager if entry == "core" else wiring.build_state(archive.manager.data_dir)
        with pytest.raises(StateError, match=f"^{reason}$"):
            manager.restore(target, raw="remote")
    assert not target.exists()


@pytest.mark.parametrize("orphan", [False, True])
def test_one_raw_inventory_per_remote_restore_or_orphan(archive, monkeypatch, orphan):
    record(archive, "inventory", resource_type="feed")
    generation = archive.manager.push()
    if orphan:
        # Simulate a candidate fully uploaded before a failed pointer CAS.
        archive.store.delete_if_match("current.json", archive.store.get("current.json")[1])
    calls = Counter()
    original_list, original_objects = archive.store.list, archive.store.list_objects

    def listing(prefix):
        calls[prefix] += 1
        return original_list(prefix)

    def objects(prefix=""):
        calls[prefix] += 1
        return original_objects(prefix)

    monkeypatch.setattr(archive.store, "list", listing)
    monkeypatch.setattr(archive.store, "list_objects", objects)
    if orphan:
        assert archive.manager.push() == generation
    else:
        assert archive.manager.restore(archive.root / "one-list", raw="remote")["verified"]
    assert calls["raw/"] == 1


@pytest.mark.parametrize("mode", ["local", "remote"])
def test_rollback_missing_protected_raw_is_distinct_from_current(archive, monkeypatch, mode):
    hour = timedelta(hours=1)
    url = "https://www.data.jma.go.jp/rollback-feed.xml"
    old = record(archive, "old", resource_type="feed", url=url)
    first, _ = publish(archive, T0, monkeypatch)
    latest = record(archive, "latest", when=T0 + hour, resource_type="feed", url=url)
    publish(archive, T0 + hour, monkeypatch)
    archive.store.put(RECEIPT_KEY, receipt_bytes([old.sha256, latest.sha256], T0 + hour))
    assert cleanup(archive, T0 + hour, raw_enabled=True)["raw_deleted_by_receipt"] == 1
    target = archive.root / "rollback"
    with pytest.raises(StateError, match="^rollback_raw_expired$"):
        archive.manager.restore(target, first.generation, raw=mode)
    assert not target.exists()
    monkeypatch.setattr(wiring, "build_state_store", lambda _config: archive.store)
    response = CliRunner().invoke(cli.app, ["state", "restore", "--to", str(target),
                                           "--generation", str(first.generation), "--raw", mode])
    assert response.exit_code == 1 and json.loads(response.output) == {"error": "rollback_raw_expired"}
    assert archive.manager.restore(archive.root / "current-ok", raw=mode)["verified"]
    key = raw_key(latest)
    archive.store.delete_if_match(key, archive.store.get(key)[1])
    with pytest.raises(StateError, match="^远端缺少 manifest 引用的 raw$"):
        archive.manager.restore(archive.root / "current-broken", raw=mode)


@pytest.mark.parametrize("mode", ["local", "remote"])
def test_rollback_size_corruption_keeps_original_verification(archive, monkeypatch, mode):
    snap = record(archive, "protected", resource_type="feed")
    first, _ = publish(archive, T0, monkeypatch)
    publish(archive, T0 + timedelta(hours=1), monkeypatch)
    archive.store.put(raw_key(snap), b"corrupt")
    with pytest.raises(StateError, match="^raw SHA256 或大小校验失败$"):
        archive.manager.restore(archive.root / "corrupt", first.generation, raw=mode)


@pytest.mark.parametrize("stream", [False, True])
def test_s3_upload_count_uses_one_head_per_put(world, monkeypatch, stream):
    raw = S3RawStore(load_storage_config())
    calls = []
    original = raw._client.head_object

    def head(**kwargs):
        calls.append(kwargs["Key"])
        return original(**kwargs)

    monkeypatch.setattr(raw._client, "head_object", head)
    uploads = cloud._RawUploads(raw)
    try:
        for expected_count in (1, 2):
            data = io.BytesIO(b"counted") if stream else b"counted"
            sha = uploads.put(data)
            assert len(calls) == expected_count
            assert uploads.bytes == len(b"counted")
        # Repair existing corruption, preserving the original count boundary.
        raw._client.put_object(Bucket=raw._bucket, Key=raw._key(sha), Body=b"corrupt")
        uploads.put(b"counted")
        assert len(calls) == 3 and uploads.bytes == len(b"counted")
        assert raw.get(sha) == b"counted"
        def fail(*_args, **_kwargs):
            raise PreconditionFailed("synthetic failure")
        monkeypatch.setattr(raw._client, "upload_fileobj", fail)
        with pytest.raises(PreconditionFailed):
            uploads.put(b"failed")
        assert len(calls) == 4 and uploads.bytes == len(b"counted")
    finally:
        raw._client.close()


def test_mid_round_stop_preserves_admission_and_publishes(world, monkeypatch):
    initial = 8_500_000_000 - len(BODY)
    capacity_inventory(monkeypatch, initial)
    admission = cloud.gate(initial, 0, "essential", reserve_bytes=500_000_000)
    result, _ = invoke(world)
    capacity = result["capacity"]
    assert {key: capacity[key] for key in ("allowed", "tier", "reason")} == {
        key: admission[key] for key in ("allowed", "tier", "reason")
    }
    assert capacity["allowed"] and capacity["mid_round_stop"]
    assert capacity["next_round"] == "cleanup_only"
    assert capacity["skipped_sources"] == [
        {"source_id": spec.id, "reason": "capacity_stop_mid_round"} for spec in world.specs[1:]
    ]
    assert result["status"] == "completed" and result["generation"] == 1
    assert [source["source_id"] for source in result["sources"]] == [world.specs[0].id]
    assert result["weekly_report_key"] is None and result["cleanup"]["failures"] == 0
    released(world)


def test_production_cloud_and_cli_supply_specs_to_core(world, monkeypatch):
    # Composition owns registry loading. Core's repository fallback must remain unused.
    monkeypatch.setattr(state_module, "load_registry", unavailable_registry)
    monkeypatch.setattr("datumoj.rikolto.core.registry.load_registry", lambda _path: FakeRegistry(world.specs))
    first, _ = invoke(world)
    assert first["status"] == "completed"
    second, _ = invoke(world, "second")
    assert second["status"] == "completed" and second["generation"] == 2
    response = CliRunner().invoke(cli.app, ["state", "restore", "--to", str(world.tmp / "cli-restore"),
                                           "--raw", "remote"])
    assert response.exit_code == 0 and json.loads(response.output)["verified"]
    released(world)
