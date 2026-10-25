"""NAS storage must reject wrong conditions before it can become a writer."""

import json
from types import SimpleNamespace

import pytest
from moto import mock_aws
from typer.testing import CliRunner

from datumoj import cli, cloud
from datumoj.core.state_s3 import S3StateObjectStore, probe_conditional_writes
from datumoj.core.storage import StorageConfig


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("DATUMOJ_S3_ACCESS_KEY_ID", "synthetic-access")
    monkeypatch.setenv("DATUMOJ_S3_SECRET_ACCESS_KEY", "synthetic-secret")
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    with mock_aws():
        state = S3StateObjectStore(StorageConfig(
            s3_endpoint="https://s3.amazonaws.com", s3_bucket="synthetic-audit-116",
            s3_region="us-east-1", s3_prefix="synthetic-project",
        ))
        state._client.create_bucket(Bucket="synthetic-audit-116")
        yield state
        state._client.close()


@pytest.mark.parametrize("ignored,expected", [
    (set(), "complete"),
    ({"absent"}, "put_if_absent_existing"),
    ({"match"}, "put_if_match_wrong_etag"),
    ({"delete"}, "delete_if_match_wrong_etag"),
    ({"absent", "match", "delete"}, "put_if_absent_existing"),
])
def test_actual_s3_conditions_are_required_and_probe_is_removed(store, monkeypatch, ignored, expected):
    # Emulate services that silently discard just one condition (or all three).
    original = store._client._make_api_call
    touched = set()
    store.put("current.json", b"preserve-current")
    store.put("write-lease.json", b"preserve-lease")

    def service(operation, params):
        params = params.copy()
        touched.add(params["Key"])
        if operation == "PutObject":
            if "absent" in ignored:
                params.pop("IfNoneMatch", None)
            if "match" in ignored:
                params.pop("IfMatch", None)
        if operation == "DeleteObject" and "delete" in ignored:
            params.pop("IfMatch", None)
        return original(operation, params)

    monkeypatch.setattr(store._client, "_make_api_call", service)
    for _ in range(2):
        assert probe_conditional_writes(store) == {"supported": not ignored, "check": expected}
    assert len(touched) == 2  # A fresh UUID for each probe, no official keys.
    assert all(key.startswith("synthetic-project/probe/") for key in touched)
    monkeypatch.setattr(store._client, "_make_api_call", original)
    assert store.list() == ["current.json", "write-lease.json"]
    assert store.get("current.json")[0] == b"preserve-current"
    assert store.get("write-lease.json")[0] == b"preserve-lease"


@pytest.mark.parametrize("command", ["check", "run"])
def test_failed_probe_never_acquires_a_lease_or_writes_state(store, monkeypatch, tmp_path, command):
    original = store._client.put_object

    def ignores_create_condition(**params):
        params.pop("IfNoneMatch", None)
        return original(**params)

    monkeypatch.setattr(store._client, "put_object", ignores_create_condition)
    monkeypatch.setattr(cloud.wiring, "build_state", lambda _root: SimpleNamespace(store=store))

    def forbidden(*_args, **_kwargs):
        pytest.fail("A failed probe must stop before lease acquisition or state initialization")

    monkeypatch.setattr(cloud, "LeaseManager", forbidden)
    monkeypatch.setattr(cloud.wiring, "build", forbidden)
    args = ["cloud", command]
    root = tmp_path / "work"
    if command == "run":
        args += ["--work-dir", str(root)]
    response = CliRunner().invoke(cli.app, args)
    result = json.loads(response.output)
    assert response.exit_code == result["exit_status"] == 1
    assert result["reason"] == "storage_conditional_unsupported"
    assert result["phase"] == "storage_probe"
    assert result["storage_probe"]["check"] == "put_if_absent_existing"
    assert store.list() == []
    assert not root.exists()
