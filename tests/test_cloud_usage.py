"""Prove usage only lists objects and reads descriptors/current manifest."""

import json
from contextlib import closing
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from typer.testing import CliRunner

from datumoj.rikolto import cli, cloud
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.state import StateManager, _sqlite_copies
from datumoj.rikolto.core.state_s3 import S3StateObjectStore
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0, make_snapshot
from tests.test_capacity import copy, generation


@pytest.fixture(params=["sqlite", "sqlite-blocks-v1"])
def usage_world(tmp_path, monkeypatch, request):
    manifest_path = tmp_path / "seed" / "manifest.sqlite"
    manifest_path.parent.mkdir()
    snapshot = make_snapshot()
    with closing(SqliteManifest(manifest_path)) as manifest:
        manifest.record_snapshot(snapshot, {"synthetic_padding": "p" * 300000} if request.param != "sqlite" else {})
    store = FakeStateObjectStore()
    if request.param == "sqlite":
        stores = [copy("manifest.sqlite", manifest_path.read_bytes(), "sqlite")]
    else:
        stores = _sqlite_copies(manifest_path)
    extra = copy("control.json", b'{"private":"must not download"}')
    stores.append(extra)
    for part in stores:
        if part.format == "sqlite-blocks-v1":
            continue
        if part == extra:
            data = b'{"private":"must not download"}'
        elif part.format == "sqlite":
            data = manifest_path.read_bytes()
        else:
            data = manifest_path.read_bytes()[int(part.name.split("/")[-1]) * 262144:][:part.size_bytes]
        store.put(part.key, data)
    descriptor = generation(1, stores)
    store.put("generations/1.json", descriptor.model_dump_json().encode())
    store.put("current.json", b'{"generation":1}')
    store.put(f"raw/{snapshot.sha256[:2]}/{snapshot.sha256}", b"hello")
    store.put("outside/secret", b"secret")
    before = dict(store._objects)
    reads = []
    original_get = store.get
    allowed = {"current.json", "generations/1.json"} | {
        part.key for part in stores if part.name.startswith("manifest.sqlite") and part.format != "sqlite-blocks-v1"
    }

    def get(key):
        assert key in allowed, f"usage tried to read disallowed object: {key}"
        reads.append(key)
        return original_get(key)

    def forbidden(*args, **kwargs):
        pytest.fail("usage tried to write, delete, restore, initialize stores, probe or acquire a lease")

    monkeypatch.setattr(store, "get", get)
    for name in ("put", "put_if_absent", "put_if_match", "delete_if_match", "exists", "list"):
        monkeypatch.setattr(store, name, forbidden)
    monkeypatch.setattr(StateManager, "restore", forbidden)
    monkeypatch.setattr(cloud.LeaseManager, "acquire", forbidden)
    monkeypatch.setattr(cloud, "probe_conditional_writes", forbidden)
    monkeypatch.setattr(cloud, "_probe_lease_writes", forbidden)
    monkeypatch.setattr(cloud.wiring, "build", forbidden)
    monkeypatch.setattr(cloud.wiring, "build_state", lambda root: StateManager(store, root))
    monkeypatch.setattr(cloud, "utcnow", lambda: T0)
    return SimpleNamespace(store=store, before=before, reads=reads, allowed=allowed, tmp=tmp_path,
                           stores=stores, original_get=original_get)


def test_cli_usage_is_read_only_and_json_never_contains_keys_hashes_or_config(usage_world, monkeypatch):
    world = usage_world
    monkeypatch.setenv("R2_ENDPOINT", "https://private.example.invalid")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "private-credential")
    response = CliRunner().invoke(cli.app, ["cloud", "usage", "--work-dir", str(world.tmp / "usage")])
    assert response.exit_code == 0, response.output
    report = json.loads(response.output)
    assert report["total"]["objects"] == len(world.before)
    assert report["total"]["bytes"] == sum(len(data) for data, _ in world.before.values())
    assert report["current_generation"]["generation"] == 1
    assert set(world.reads) == world.allowed
    assert world.store._objects == world.before
    for key in world.before:
        assert key not in response.output
        if key.startswith(("raw/", "copies/")):
            assert key.split("/")[-1] not in response.output
    for forbidden in ("private-credential", "private.example.invalid", "secret", "endpoint", "bucket_name"):
        assert forbidden not in response.output
    assert report["raw_sources"]["jp-test"]["objects"] == 1


@pytest.mark.parametrize("phase", ["configuration", "list", "generations", "manifest", "inventory"])
def test_arbitrary_errors_are_fixed_phase_codes(usage_world, monkeypatch, phase):
    def fail(*args, **kwargs):
        print("bucket private-credential raw/secret")
        raise RuntimeError("bucket private-credential raw/secret")

    target = {"configuration": (cloud.wiring, "build_state"), "list": (cloud, "_usage_objects"),
              "generations": (StateManager, "_current"), "manifest": (cloud, "_usage_manifest"),
              "inventory": (cloud, "load_registry")}[phase]
    monkeypatch.setattr(*target, fail)
    response = CliRunner().invoke(cli.app, ["cloud", "usage"])
    assert response.exit_code == 1
    assert json.loads(response.output) == {"exit_status": 1, "phase": phase, "reason": phase + "_failed"}


def test_s3_inventory_lists_only_this_whole_bucket_and_classifies_namespace():
    calls = []

    class Paginator:
        def paginate(self, **kwargs):
            calls.append(kwargs)
            return [{"Contents": [{"Key": "rikolto/raw/a", "Size": 3, "ETag": "a"}]},
                    {"Contents": [{"Key": "tel/copies/a", "Size": 7, "ETag": "b"}]}]

    def paginator(operation):
        assert operation == "list_objects_v2"
        return Paginator()

    store = S3StateObjectStore.__new__(S3StateObjectStore)
    store._client = SimpleNamespace(get_paginator=paginator)
    store._bucket, store._prefix = "synthetic-private-bucket", "rikolto"
    scoped, other = cloud._usage_objects(store)
    assert calls == [{"Bucket": "synthetic-private-bucket"}]
    assert [(o.key, o.size_bytes) for o in scoped] == [("raw/a", 3)]
    assert [(o.key, o.size_bytes) for o in other] == [("tel/copies/a", 7)]


def test_list_denied_is_sanitized(usage_world, monkeypatch):
    def denied(*args):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "private-bucket"}}, "ListObjectsV2")

    monkeypatch.setattr(cloud, "_usage_objects", denied)
    assert cloud.usage() == {"exit_status": 1, "phase": "list", "reason": "list_denied"}


def test_corrupt_manifest_is_rejected_without_returning_partial_statistics(usage_world, monkeypatch):
    world = usage_world

    def corrupt(key):
        if key.startswith("copies/"):
            return b"invalid", "etag"
        return world.original_get(key)

    monkeypatch.setattr(world.store, "get", corrupt)
    assert cloud.usage() == {"exit_status": 1, "phase": "manifest", "reason": "manifest_failed"}


def test_changed_current_is_rejected(usage_world, monkeypatch):
    calls = []

    def current(self):
        calls.append(1)
        return (1, "first" if len(calls) == 1 else "changed")

    monkeypatch.setattr(StateManager, "_current", current)
    assert cloud.usage() == {"exit_status": 1, "phase": "inventory", "reason": "generation_changed"}


def test_cli_propagates_work_dir_and_error(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cloud, "usage", lambda **kw: calls.append(kw) or {
        "exit_status": 1, "phase": "list", "reason": "list_denied",
    })
    response = CliRunner().invoke(cli.app, ["cloud", "usage", "--work-dir", str(tmp_path)])
    assert response.exit_code == 1 and json.loads(response.output)["reason"] == "list_denied"
    assert calls == [{"work_dir": tmp_path}]
