"""Offline S3 conformance, checksums, namespace isolation and sanitized failures."""

import base64
import hashlib
import traceback

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from moto import mock_aws

from datumoj.core.contracts import ContractError
from datumoj.core.state_s3 import S3StateObjectStore
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
