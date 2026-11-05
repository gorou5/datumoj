"""Offline S3 conformance, checksums, namespace isolation and sanitized failures."""

import base64
import hashlib
import json
import traceback
from contextlib import closing
from datetime import timedelta

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from moto import mock_aws

from datumoj.rikolto.core.contracts import ContractError
from datumoj.rikolto.core.lease import LEASE_KEY, LeaseError, LeaseManager
from datumoj.rikolto.core.state_s3 import S3StateObjectStore, probe_conditional_writes
from datumoj.rikolto.core.storage import StorageConfig
from datumoj.rikolto.testing.contract_suites import StateObjectStoreSuite
from datumoj.rikolto.testing.samples import T0


@pytest.fixture
def state_store(monkeypatch):
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "synthetic-state-id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "synthetic-state-secret")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing", aws_secret_access_key="testing")
        client.create_bucket(Bucket="synthetic-state-bucket")
        store = S3StateObjectStore(StorageConfig(
            s3_endpoint="https://s3.amazonaws.com", s3_bucket="synthetic-state-bucket",
            s3_prefix="/project/state/", s3_region="us-east-1",
        ))
        yield store
        store._client.close()
        client.close()


class TestS3StateObjectStore(StateObjectStoreSuite):
    @pytest.fixture
    def state_store(self, state_store):
        return state_store


def test_paginated_listing_and_namespace(state_store):
    # Force short pages without manufacturing 1000 irrelevant large objects.
    for key in ("copies/sha256/a", "copies/sha256/b", "copies/sha256/c", "raw/a"):
        state_store.put(key, b"test")
    client = state_store._client
    client.put_object(Bucket=state_store._bucket, Key="other/copies/sha256/leak", Body=b"test")
    paginator = client.get_paginator("list_objects_v2")
    original = client.get_paginator

    class ShortPages:
        def paginate(self, **kwargs):
            return paginator.paginate(**kwargs, PaginationConfig={"PageSize": 1})

    client.get_paginator = lambda name: ShortPages()
    try:
        assert state_store.list("copies/sha256/") == [f"copies/sha256/{x}" for x in "abc"]
        assert state_store.list("") == [f"copies/sha256/{x}" for x in "abc"] + ["raw/a"]
    finally:
        client.get_paginator = original


def test_checksum_sent_without_get_or_head(state_store, monkeypatch):
    calls = []
    original = state_store._client.put_object

    def put(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(state_store._client, "put_object", put)
    monkeypatch.setattr(state_store._client, "get_object", lambda **kw: pytest.fail("PUT must not GET"))
    monkeypatch.setattr(state_store._client, "head_object", lambda **kw: pytest.fail("PUT must not HEAD"))
    state_store.put_if_absent("copies/a", b"bytes")
    assert calls[0]["ChecksumSHA256"] == base64.b64encode(hashlib.sha256(b"bytes").digest()).decode()
    assert calls[0]["IfNoneMatch"] == "*"


@pytest.mark.parametrize("operation,args", [
    ("get", ("a",)), ("put", ("a", b"b")), ("put_if_absent", ("a", b"b")),
    ("put_if_match", ("a", b"b", '"etag"')), ("exists", ("a",)), ("list", ("",)),
    ("delete_if_match", ("a", '"etag"')),
    ("delete_objects", (["raw/a"],)),
])
@pytest.mark.parametrize("network", [False, True])
def test_errors_do_not_expose_configuration(state_store, monkeypatch, caplog, operation, args, network):
    secrets = ["synthetic-state-id", "synthetic-state-secret", "https://s3.amazonaws.com", "synthetic-state-bucket"]
    detail = " ".join(secrets)

    def fail(*a, **kw):
        if network:
            raise EndpointConnectionError(endpoint_url=detail)
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": detail}}, "test")

    monkeypatch.setattr(state_store._client, "_make_api_call", fail)
    with pytest.raises(ContractError) as caught:
        getattr(state_store, operation)(*args)
    output = "".join(traceback.format_exception(caught.value)) + caplog.text
    assert all(secret not in output for secret in secrets)


@pytest.mark.parametrize("key", ["../escape", "/absolute", "copies/../escape", "a//b", "a\\b", "a:b", "a\n"])
def test_invalid_keys_never_reach_sdk(state_store, key):
    for action in (state_store.get, state_store.exists, state_store.list):
        with pytest.raises(ValueError):
            action(key)


def test_probe_supported_and_only_touches_disposable_keys(state_store, monkeypatch):
    for key in ("current.json", "write-lease.json", "generations/1.json"):
        state_store.put(key, b"preserve")
    before = {key: state_store.get(key) for key in state_store.list()}
    calls = []
    original = state_store._client._make_api_call

    def observe(operation, params):
        calls.append((operation, params.copy()))
        return original(operation, params)

    monkeypatch.setattr(state_store._client, "_make_api_call", observe)
    assert probe_conditional_writes(state_store) == {"supported": True, "check": "complete"}
    assert all(params["Key"].startswith("project/state/probe/") for _, params in calls)
    assert len({params["Key"] for _, params in calls}) == 1
    puts = [params for operation, params in calls if operation == "PutObject"]
    deletes = [params for operation, params in calls if operation == "DeleteObject"]
    assert [p.get("IfNoneMatch") for p in puts] == ["*", "*", None, None]
    assert puts[2]["IfMatch"] != puts[3]["IfMatch"]
    assert len(deletes) == 1 and "IfMatch" not in deletes[0]  # Probe-only cleanup.
    assert {key: state_store.get(key) for key in state_store.list()} == before


@pytest.mark.parametrize("stage", ["PutObject", "GetObject"])
def test_probe_io_failure_is_sanitized_and_cleanup_attempted(state_store, monkeypatch, stage):
    original = state_store._client._make_api_call
    cleanup = []

    def fail(operation, params):
        if operation == "DeleteObject" and "IfMatch" not in params:
            cleanup.append(params["Key"])
            return original(operation, params)
        if operation == stage:
            raise EndpointConnectionError(endpoint_url="https://private.example.invalid/secret")
        return original(operation, params)

    monkeypatch.setattr(state_store._client, "_make_api_call", fail)
    result = probe_conditional_writes(state_store)
    assert not result["supported"]
    assert "private" not in str(result) and "secret" not in str(result)
    assert len(cleanup) == 1 and cleanup[0].startswith("project/state/probe/")
    assert state_store.list("probe/") == []


def test_probe_cleanup_failure_refuses_run(state_store, monkeypatch):
    original = state_store._client.delete_object

    def delete(**params):
        if "IfMatch" not in params:
            raise RuntimeError("synthetic private details")
        return original(**params)

    monkeypatch.setattr(state_store._client, "delete_object", delete)
    assert probe_conditional_writes(state_store) == {
        "supported": False, "check": "cleanup", "cleanup_failed": True,
    }
    assert len(state_store.list("probe/")) == 1  # Failed cleanup leaves only the disposable key.


def test_r2_ignored_delete_condition_allows_probe_and_safe_lease_lifecycle(state_store, monkeypatch):
    original = state_store._client.delete_object
    deletes = []

    def ignores_delete_match(**params):
        deletes.append(params.copy())
        params.pop("IfMatch", None)
        return original(**params)

    monkeypatch.setattr(state_store._client, "delete_object", ignores_delete_match)
    assert probe_conditional_writes(state_store) == {"supported": True, "check": "complete"}
    assert len(deletes) == 1 and "IfMatch" not in deletes[0]
    assert state_store.list() == []

    clock = [T0]
    manager = LeaseManager(state_store, now=lambda: clock[0], ttl=timedelta(minutes=1))
    owner = manager.acquire("local/one")
    manager.release(owner.holder, owner.token)
    released = json.loads(state_store.get(LEASE_KEY)[0])
    assert set(released) == {"released_at"} and "token" not in released
    assert manager.status() == {"held": False}
    successor = manager.acquire("actions/new")
    manager.check(successor)
    clock[0] = successor.expires_at
    replacement = manager.acquire("nas/takeover", force=True)
    before = state_store.get(LEASE_KEY)
    for old in (owner, successor):
        with pytest.raises(LeaseError, match="不属于本次持有者"):
            manager.release(old.holder, old.token)
        assert state_store.get(LEASE_KEY) == before
    manager.check(replacement)
    assert len(deletes) == 1  # Lease operations never send DELETE.


def test_concurrent_push_and_restore_on_s3(state_store, tmp_path):
    from datumoj.rikolto.core.manifest import SqliteManifest
    from datumoj.rikolto.core.state import StateManager
    from datumoj.rikolto.testing.samples import make_snapshot

    root = tmp_path / "source"
    root.mkdir()
    expected = {}
    with closing(SqliteManifest(root / "manifest.sqlite")) as manifest:
        for index in range(12):
            data = f"synthetic concurrent S3 raw {index}".encode()
            snapshot = make_snapshot(data=data, snapshot_id=f"synthetic-{index}")
            manifest.record_snapshot(snapshot)
            raw = root / "raw" / snapshot.sha256[:2] / snapshot.sha256
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(data)
            expected[raw.relative_to(root)] = data
    manager = StateManager(state_store, root, transfer_workers=8)
    candidate = manager.push()
    target = tmp_path / "replica"
    report = manager.restore(target)
    assert candidate.generation == report["generation"] == 1 and report["verified"]
    for path, data in expected.items():
        assert (target / path).read_bytes() == data
    with closing(SqliteManifest(target / "manifest.sqlite")) as manifest:
        for index in range(12):
            assert manifest.get_snapshot(f"synthetic-{index}").sha256 in {path.name for path in expected}
    assert state_store._client.meta.config.max_pool_connections >= 8
    assert state_store._client.meta.config.connect_timeout == 10
    assert state_store._client.meta.config.read_timeout == 60
    assert state_store._client.meta.config.retries["total_max_attempts"] == 3


def test_inventory_cleanup_does_not_download_or_head_objects(state_store, monkeypatch):
    state_store.put("copies/a", b"abc")
    state_store.put("raw/a", b"12345")
    monkeypatch.setattr(state_store._client, "get_object", lambda **kw: pytest.fail("inventory must not GET"))
    monkeypatch.setattr(state_store._client, "head_object", lambda **kw: pytest.fail("inventory deletion must not HEAD"))
    objects = state_store.list_objects("")
    assert [(obj.key, obj.size_bytes) for obj in objects] == [("copies/a", 3), ("raw/a", 5)]
    for obj in objects:
        state_store.delete_if_match(obj.key, obj.etag)
    assert state_store.list_objects("") == []


def test_bulk_cleanup_uses_only_requested_keys_without_get_or_head(state_store, monkeypatch):
    keys = ["generations/1.json", "copies/sha256/orphan", "raw/old"]
    for key in [*keys, "current.json", LEASE_KEY, "raw/keep"]:
        state_store.put(key, b"synthetic")
    state_store.list_objects("")
    monkeypatch.setattr(state_store._client, "get_object", lambda **kw: pytest.fail("bulk must not GET"))
    monkeypatch.setattr(state_store._client, "head_object", lambda **kw: pytest.fail("bulk must not HEAD"))
    assert state_store.delete_objects(keys) == set(keys)
    assert state_store.list("") == ["current.json", "raw/keep", LEASE_KEY]
    assert not state_store._listed_keys.intersection(keys)


@pytest.mark.parametrize("count", [0, 201])
def test_bulk_rejects_invalid_batch_size_before_sdk(state_store, monkeypatch, count):
    monkeypatch.setattr(state_store._client, "delete_objects", lambda **kw: pytest.fail("must not DELETE"))
    with pytest.raises(ValueError, match="200"):
        state_store.delete_objects([f"raw/{i}" for i in range(count)])


def test_retired_object_boundary_rejects_coordination_keys_and_checks_lease():
    from datumoj.rikolto.core.contracts import StateObject
    from datumoj.rikolto.core.state_s3 import delete_retired_object
    from datumoj.rikolto.testing.fakes import FakeStateObjectStore

    store = FakeStateObjectStore()
    for key in ("current.json", LEASE_KEY, "reports/weekly/report.md", "copies/sha256/orphan"):
        store.put(key, b"preserve")
    objects = {obj.key: obj for obj in store.list_objects("")}
    for key in ("current.json", LEASE_KEY, "reports/weekly/report.md"):
        with pytest.raises(ValueError, match="清理只允许"):
            delete_retired_object(store, objects[key], lambda: None)
    with pytest.raises(ValueError):
        delete_retired_object(store, StateObject("../current.json", 0, "etag"), lambda: None)

    def lost():
        raise LeaseError("synthetic lease lost")

    with pytest.raises(LeaseError):
        delete_retired_object(store, objects["copies/sha256/orphan"], lost)
    assert store.list_objects("") == list(objects.values())
