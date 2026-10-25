"""Offline S3 conformance, checksums, namespace isolation and sanitized failures."""

import base64
import hashlib
import traceback

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from moto import mock_aws

from datumoj.core.contracts import ContractError
from datumoj.core.state_s3 import S3StateObjectStore, probe_conditional_writes
from datumoj.core.storage import StorageConfig
from datumoj.testing.contract_suites import StateObjectStoreSuite


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
    assert deletes[0]["IfMatch"] == puts[3]["IfMatch"]
    assert deletes[1]["IfMatch"] != deletes[0]["IfMatch"]
    assert "IfMatch" not in deletes[2]  # Unconditional cleanup is probe-only.
    assert {key: state_store.get(key) for key in state_store.list()} == before


@pytest.mark.parametrize("stage", ["PutObject", "GetObject", "HeadObject", "DeleteObject"])
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
    assert state_store.list("probe/") == []
