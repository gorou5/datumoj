"""Offline projection jobs against the existing generation/lease publisher."""

import json
from contextlib import closing
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, cloud, wiring
from datumoj.rikolto.core.contracts import StateObject
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.testing.fakes import FakeRawStore, FakeRegistry, FakeStateObjectStore
from datumoj.rikolto.testing.samples import (
    make_chunk,
    make_dataset,
    make_document,
    make_observation,
    make_snapshot,
    make_spec,
)


class RecordingStore(FakeStateObjectStore):
    def __init__(self):
        super().__init__()
        self.writes = []
        self.extra_bytes = 0

    def _write(self, key, data):
        self.writes.append(key)
        return super()._write(key, data)

    def delete_if_match(self, key, etag):
        self.writes.append(key)
        return super().delete_if_match(key, etag)

    def list_objects(self, prefix=""):
        return [*super().list_objects(prefix), StateObject("other-data", self.extra_bytes, '"synthetic"')]


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", "synthetic")
    monkeypatch.setenv("R2_ENDPOINT", "https://synthetic.invalid")
    registry = FakeRegistry([make_spec()])
    original = wiring.build

    def build(root, overrides=None):
        return original(root, {"registry": registry, "vectors": None, "raw": FakeRawStore(), **(overrides or {})})

    store = RecordingStore()
    seed_root = tmp_path / "seed"
    deps = original(seed_root, {"registry": registry, "vectors": None})
    snapshot = make_snapshot()
    deps.raw.put(b"hello")
    deps.manifest.record_snapshot(snapshot)
    deps.catalog.upsert([make_dataset("jp-test:data", "Synthetic catalog", snapshot_sha256=snapshot.sha256,
                                      locator="entry=1")])
    deps.observations.write_table("jp-test", "table", [make_observation(
        f"o{i}", None, note="-", source_id="jp-test", dataset_key="jp-test:data", table_key="table",
        snapshot_sha256=snapshot.sha256, locator=f"row={i}",
    ) for i in range(10)])
    body = make_chunk("jp-test:body", snapshot.sha256, "section=1", "Existing body")
    deps.chunks.put_document(make_document(body.doc_key, snapshot.sha256), [body])
    StateManager(store, seed_root).push()
    for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
        component = getattr(deps, name, None)
        if component is not None:
            component.close()
    store.writes.clear()
    monkeypatch.setattr(wiring, "build", build)
    monkeypatch.setattr(wiring, "build_state", lambda root: StateManager(store, root))
    return SimpleNamespace(store=store, tmp=tmp_path)


def test_dry_run_has_only_counts_and_bytes_and_never_writes_or_leases(world, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("dry run tried to acquire a lease or probe writes")

    monkeypatch.setattr(cloud.LeaseManager, "acquire", forbidden)
    monkeypatch.setattr(cloud, "probe_conditional_writes", forbidden)
    before = dict(world.store._objects)
    response = CliRunner().invoke(cli.app, ["cloud", "project", "--work-dir", str(world.tmp / "preview")])
    assert response.exit_code == 0, response.output
    result = json.loads(response.output)
    assert result["new_chunks"] == 11
    assert result["sources"] == {"jp-test": {"catalog_entries": 1, "observation_rows": 10, "new_chunks": 11}}
    assert result["estimated_new_bytes"] == result["average_chunk_bytes"] * 11 > 0
    assert result["current_bytes"] > 0 and result["existing_chunks"] == 1
    assert not world.store.writes and world.store._objects == before
    assert all(type(value) is int for key, value in result.items() if key != "sources")
    print("PROJECT DRY RUN", json.dumps(result, sort_keys=True))


@pytest.mark.parametrize("extra", [8_500_000_000, 9_000_000_000])
def test_over_limit_stops_before_any_remote_write(world, extra):
    world.store.extra_bytes = extra
    result = cloud.project(work_dir=world.tmp / "blocked", write=True)
    assert result["exit_status"] == 1 and result["reason"] == "storage_limit_exceeded"
    assert world.store.writes == []
    assert json.loads(world.store.get("current.json")[0])["generation"] == 1


def test_estimated_increment_is_included_in_limit(world, monkeypatch):
    monkeypatch.setattr(cloud, "_project_storage_bytes", lambda store: 8_499_999_999)
    result = cloud.project(work_dir=world.tmp / "blocked", write=True)
    assert result["reason"] == "storage_limit_exceeded" and not world.store.writes


def test_write_advances_generation_and_indexes_only_projections(world):
    result = cloud.project(work_dir=world.tmp / "write", write=True)
    assert "exit_status" not in result, result
    assert result["generation"] == 2 and result["added_chunks"] == 11
    assert result["after_bytes"] > result["before_bytes"]
    assert result["after_bytes"] == sum(obj.size_bytes for obj in world.store.list_objects())
    assert json.loads(world.store.get("current.json")[0])["generation"] == 2
    assert json.loads(world.store.get("write-lease.json")[0]).keys() == {"released_at"}
    from datumoj.rikolto.store.chunks import SqliteChunkStore

    with closing(SqliteChunkStore(world.tmp / "write/chunks.sqlite")) as chunks:
        assert len(chunks.chunks_for("jp-test:data")) == 11
        assert len(chunks.chunks_for("jp-test:body")) == 1
    second = cloud.project(work_dir=world.tmp / "again", write=True)
    assert second["added_chunks"] == 0 and second["generation"] == 2


def test_storage_total_includes_other_prefixes_and_buckets(monkeypatch):
    # The StateObjectStore inventory would see only its Rikolto prefix.
    from datumoj.rikolto.core.state_s3 import S3StateObjectStore

    calls = []

    class Paginator:
        def __init__(self, operation):
            self.operation = operation

        def paginate(self, **kwargs):
            calls.append((self.operation, kwargs))
            if self.operation == "list_buckets":
                return [{"Buckets": [{"Name": "primary"}]}, {"Buckets": [{"Name": "secondary"}]}]
            return [{"Contents": [{"Key": "rikolto/a", "Size": 3}]},
                    {"Contents": [{"Key": "tel/b", "Size": 7}]}]

    store = S3StateObjectStore.__new__(S3StateObjectStore)
    store._client = SimpleNamespace(get_paginator=Paginator)
    assert cloud._project_storage_bytes(store) == 20
    assert calls == [("list_buckets", {}), ("list_objects_v2", {"Bucket": "primary"}),
                     ("list_objects_v2", {"Bucket": "secondary"})]


@pytest.mark.parametrize("options,write", [([], False), (["--dry-run"], False), (["--write"], True)])
def test_cli_modes(monkeypatch, tmp_path, options, write):
    seen = []
    monkeypatch.setattr(cloud, "project", lambda **kw: seen.append(kw) or {"new_chunks": 3})
    response = CliRunner().invoke(cli.app, ["cloud", "project", "--work-dir", str(tmp_path), *options])
    assert response.exit_code == 0 and json.loads(response.output) == {"new_chunks": 3}
    assert seen == [{"work_dir": tmp_path, "write": write}]


def test_empty_block_store_uses_projected_bytes_instead_of_zero(world, monkeypatch):
    original = cloud._projection_estimate

    def empty_estimate(pipeline):
        from dataclasses import replace

        from datumoj.rikolto.testing.fakes import FakeChunkStore

        return original(replace(pipeline, chunks=FakeChunkStore()))

    monkeypatch.setattr(cloud, "_projection_estimate", empty_estimate)
    result = cloud.project(work_dir=world.tmp / "empty-preview")
    assert result["existing_chunks"] == 0 and result["estimated_new_bytes"] > 0
    assert not world.store.writes


def test_lost_generation_is_rejected_under_lease(world, monkeypatch):
    original = StateManager.list

    def history(manager):
        result = original(manager)
        found = world.store.get("write-lease.json")
        if found and "token" in json.loads(found[0]):
            return [result[0].model_copy(update={"generation": 2})]
        return result

    monkeypatch.setattr(StateManager, "list", history)
    result = cloud.project(work_dir=world.tmp / "raced", write=True)
    assert result["exit_status"] == 1 and result["reason"] == "generation_changed"
    assert set(world.store.writes) == {"write-lease.json"}
    assert json.loads(world.store.get("current.json")[0])["generation"] == 1


def test_orphan_is_not_silently_published_instead_of_projections(world):
    world.store.put("generations/2.json", b"synthetic orphan")
    world.store.writes.clear()
    result = cloud.project(work_dir=world.tmp / "orphan", write=True)
    assert result["exit_status"] == 1 and result["reason"] == "orphan_generation"
    assert set(world.store.writes) == {"write-lease.json"}
    assert json.loads(world.store.get("current.json")[0])["generation"] == 1


def test_capacity_is_rechecked_after_acquiring_lease(world, monkeypatch):
    totals = iter((1000, 8_500_000_000))
    monkeypatch.setattr(cloud, "_project_storage_bytes", lambda store: next(totals))
    result = cloud.project(work_dir=world.tmp / "grew", write=True)
    assert result["reason"] == "storage_limit_exceeded"
    assert set(world.store.writes) == {"write-lease.json"}
    assert json.loads(world.store.get("current.json")[0])["generation"] == 1
