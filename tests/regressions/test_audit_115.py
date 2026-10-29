"""Regression checks for generalized S3 configuration and manual-only Actions."""

import boto3
import pytest
import yaml
from moto import mock_aws

from datumoj.rikolto.core.lease import LeaseManager, remote_configured
from datumoj.rikolto.core.rawstore_s3 import S3RawStore
from datumoj.rikolto.core.state_s3 import S3StateObjectStore
from datumoj.rikolto.core.storage import load_s3_credentials, load_storage_config

S3_VARIABLES = (
    "DATUMOJ_S3_ENDPOINT", "DATUMOJ_S3_ACCESS_KEY_ID", "DATUMOJ_S3_SECRET_ACCESS_KEY",
    "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
)


@pytest.mark.parametrize("legacy", [False, True], ids=["generic", "r2-aliases"])
def test_generic_and_legacy_names_construct_all_s3_adapters(monkeypatch, legacy):
    for name in S3_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "s3")
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", "audit-115-bucket")
    monkeypatch.setenv("DATUMOJ_S3_PREFIX", "audit-115")
    monkeypatch.setenv("DATUMOJ_S3_REGION", "us-east-1")
    endpoint_name = "R2_ENDPOINT" if legacy else "DATUMOJ_S3_ENDPOINT"
    access_name = "R2_ACCESS_KEY_ID" if legacy else "DATUMOJ_S3_ACCESS_KEY_ID"
    secret_name = "R2_SECRET_ACCESS_KEY" if legacy else "DATUMOJ_S3_SECRET_ACCESS_KEY"
    monkeypatch.setenv(endpoint_name, "https://s3.amazonaws.com")
    monkeypatch.setenv(access_name, "synthetic-access")
    monkeypatch.setenv(secret_name, "synthetic-secret")

    with mock_aws():
        client = boto3.client(
            "s3", region_name="us-east-1", aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        client.create_bucket(Bucket="audit-115-bucket")
        config = load_storage_config()
        state = S3StateObjectStore(config)
        raw = S3RawStore(config)
        lease = LeaseManager(state)
        assert config.s3_endpoint == "https://s3.amazonaws.com"
        assert load_s3_credentials() == ("synthetic-access", "synthetic-secret")
        assert remote_configured()
        assert isinstance(lease, LeaseManager)
        raw._client.close()
        state._client.close()
        client.close()


@pytest.mark.parametrize(("generic", "legacy", "new_value", "old_value"), [
    ("DATUMOJ_S3_ENDPOINT", "R2_ENDPOINT", "https://new.example.invalid", "https://old.example.invalid"),
    ("DATUMOJ_S3_ACCESS_KEY_ID", "R2_ACCESS_KEY_ID", "synthetic-new-id", "synthetic-old-id"),
    ("DATUMOJ_S3_SECRET_ACCESS_KEY", "R2_SECRET_ACCESS_KEY", "synthetic-new-secret", "synthetic-old-secret"),
])
def test_conflicting_aliases_rejected_without_echoing_values(generic, legacy, new_value, old_value):
    env = {generic: new_value, legacy: old_value}
    with pytest.raises(ValueError) as error:
        load_storage_config(env)
    output = str(error.value)
    assert generic in output and legacy in output
    assert new_value not in output and old_value not in output


def test_missing_endpoint_names_generic_variable():
    with pytest.raises(ValueError) as error:
        load_storage_config({"DATUMOJ_RAW_BACKEND": "s3", "DATUMOJ_S3_BUCKET": "audit-115-bucket"})
    assert "DATUMOJ_S3_ENDPOINT" in str(error.value)


def test_cloud_workflow_has_no_schedule():
    from pathlib import Path

    workflow_path = Path(__file__).resolve().parents[2] / ".github/workflows/cloud-run.yml"
    workflow = yaml.safe_load(workflow_path.read_text())
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert "schedule" not in workflow["on"]
