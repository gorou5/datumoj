"""All cloud DB downloads use GET; publish/probe/lease paths are forbidden."""

import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, cloud, db_usage
from datumoj.rikolto.core.models import VectorCoverage
from datumoj.rikolto.core.state import StateManager, _sqlite_copies
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from tests.test_capacity import copy, generation
from tests.test_db_usage import NOW, make_vectors
from tests.test_db_usage import db_world as db_world  # noqa: PLC0414 -- Re-export pytest fixture.


@pytest.fixture(params=["sqlite", "sqlite-blocks-v1"])
def cloud_world(db_world, monkeypatch, request):
    world = db_world
    vectors = make_vectors(world)
    if request.param == "sqlite-blocks-v1":
        with closing(sqlite3.connect(world.root / "manifest.sqlite")) as db, db:
            db.execute("UPDATE snapshots SET meta=? WHERE seq=1", (json.dumps({"padding": "p" * 300000}),))
    store = FakeStateObjectStore()
    copies = []
    allowed = {"current.json", "generations/1.json"}
    for path in sorted(world.root.glob("*.sqlite")):
        data = path.read_bytes()
        parts = _sqlite_copies(path) if request.param == "sqlite-blocks-v1" else [copy(path.name, data, "sqlite")]
        for part in parts:
            if part.format == "sqlite-blocks-v1":
                continue
            offset = int(part.name.split("/")[-1]) * 262144 if part.format == "sqlite-block-v1" else 0
            store.put(part.key, data[offset:offset + part.size_bytes])
            allowed.add(part.key)
        copies.extend(parts)
    for path in sorted(vectors.rglob("*")):
        if path.is_file():
            part = copy(path.relative_to(world.root).as_posix(), path.read_bytes())
            copies.append(part)
            store.put(part.key, path.read_bytes())
            allowed.add(part.key)
    vector_state = copy("vectors/vectors-state.json", b'{"missing":{},"last_error":null}')
    copies.append(vector_state)
    store.put(vector_state.key, b'{"missing":{},"last_error":null}')
    private = copy("control.json", b'"PRIVATE-CONTROL"')
    copies.append(private)
    store.put(private.key, b'"PRIVATE-CONTROL"')
    descriptor = generation(1, copies).model_copy(update={
        "vector_coverage": VectorCoverage(model="fake", version="1", dim=2, embedded_chunks=3, pending_chunks=0),
    })
    store.put("generations/1.json", descriptor.model_dump_json().encode())
    store.put("current.json", b'{"generation":1}')
    store.put("raw/private-secret", b"PRIVATE-RAW")
    before = dict(store._objects)
    reads = []
    original_get = store.get
    def get(key):
        assert key in allowed
        reads.append(key)
        return original_get(key)
    def forbidden(*args, **kwargs):
        pytest.fail("write/delete/lease/probe/restore/build invoked")
    monkeypatch.setattr(store, "get", get)
    for name in ("put", "put_if_absent", "put_if_match", "delete_if_match", "exists", "list"):
        monkeypatch.setattr(store, name, forbidden)
    monkeypatch.setattr(StateManager, "restore", forbidden)
    monkeypatch.setattr(cloud.LeaseManager, "acquire", forbidden)
    monkeypatch.setattr(cloud, "probe_conditional_writes", forbidden)
    monkeypatch.setattr(cloud, "_probe_lease_writes", forbidden)
    monkeypatch.setattr(cloud.wiring, "build", forbidden)
    monkeypatch.setattr(cloud.wiring, "build_state", lambda root: StateManager(store, root))
    monkeypatch.setattr(cloud, "utcnow", lambda: NOW)
    return SimpleNamespace(seed=world, store=store, reads=reads, before=before, allowed=allowed,
                           original_get=original_get, copies=copies)


def test_db_is_additive_read_only_and_temporary(cloud_world, tmp_path):
    world = cloud_world
    baseline = cloud.usage()
    assert "db" not in baseline and baseline.get("exit_status", 0) == 0
    manifest_keys = {part.key for part in world.copies if part.name.startswith("manifest.sqlite")
                     and part.format != "sqlite-blocks-v1"}
    assert set(world.reads) == {"current.json", "generations/1.json"} | manifest_keys
    world.reads.clear()
    root = tmp_path / "usage"
    report = cloud.usage(db=True, work_dir=root)
    assert report.get("exit_status", 0) == 0, report
    assert {key: value for key, value in report.items() if key != "db"} == baseline
    assert report["db"] == db_usage.measure_usage(
        manifest=world.seed.root / "manifest.sqlite", chunks=world.seed.root / "chunks.sqlite",
        changes=world.seed.root / "changes.sqlite", catalog=world.seed.root / "catalog.sqlite",
        vectors=world.seed.root / "vectors", now=NOW,
    )
    assert set(world.reads) == world.allowed
    assert not list(root.iterdir())
    assert world.store._objects == world.before


def test_cli_db_option(cloud_world, tmp_path):
    response = CliRunner().invoke(cli.app, ["cloud", "usage", "--db", "--work-dir", str(tmp_path / "usage")])
    assert response.exit_code == 0, response.output
    assert json.loads(response.output)["db"]["vectors"]["rows"] == 4


@pytest.mark.parametrize("failure", ["missing", "checksum", "assembled_checksum", "analysis"])
def test_db_failures_are_sanitized_and_files_removed(cloud_world, tmp_path, monkeypatch, failure):
    world = cloud_world
    part = next(part for part in world.copies if part.name.startswith("chunks.sqlite")
                and part.format != "sqlite-blocks-v1")
    def broken_get(key):
        if key == part.key:
            return None if failure == "missing" else (b"SENSITIVE https://private.invalid", "etag")
        return world.original_get(key)
    if failure in {"missing", "checksum"}:
        monkeypatch.setattr(world.store, "get", broken_get)
    elif failure == "assembled_checksum":
        descriptor = generation(1, world.copies).model_copy(update={
            "stores": [part.model_copy(update={"sha256": "0" * 64})
                       if part.name == "chunks.sqlite" else part for part in world.copies],
        })
        # Keep valid part objects but corrupt the assembled descriptor in the
        # helper: this exercises the final assembled-file digest independently.
        original = cloud._usage_databases
        def bad_descriptor(manager, gen, root, now):
            return original(manager, descriptor, root, now)
        monkeypatch.setattr(cloud, "_usage_databases", bad_descriptor)
    else:
        def fail(**kwargs):
            print("SENSITIVE https://private.invalid")
            raise RuntimeError("SENSITIVE https://private.invalid")
        monkeypatch.setattr(db_usage, "measure_usage", fail)
    root = tmp_path / "usage"
    assert cloud.usage(db=True, work_dir=root) == {"exit_status": 1, "phase": "db", "reason": "db_failed"}
    assert not list(root.iterdir())
    assert world.store._objects == world.before


def test_pointer_change_during_db_analysis_is_rejected(cloud_world, monkeypatch):
    calls = []
    def current(self):
        calls.append(1)
        return 1, "first" if len(calls) == 1 else "changed"
    monkeypatch.setattr(StateManager, "_current", current)
    assert cloud.usage(db=True) == {"exit_status": 1, "phase": "inventory", "reason": "generation_changed"}
