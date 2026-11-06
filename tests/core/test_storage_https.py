"""S3 endpoints require TLS except for local loopback testing."""

import pytest

from datumoj.rikolto.core.storage import StorageConfig, load_storage_config


@pytest.mark.parametrize("endpoint", [
    "https://nas.example:9000", "http://localhost", "http://127.0.0.1:9000",
    "http://127.255.255.255", "http://[::1]:9000",
])
def test_secure_or_loopback_endpoint(endpoint):
    assert StorageConfig(s3_endpoint=endpoint).s3_endpoint == endpoint


@pytest.mark.parametrize("endpoint", [
    "http://nas.example:9000", "http://192.168.1.10:9000",
    "http://localhost.example.com", "http://127.0.0.1.example", "http://[::2]",
])
def test_non_loopback_http_rejected(endpoint):
    with pytest.raises(ValueError, match="必须用 HTTPS（只有本机回环地址允许 http）") as error:
        StorageConfig(s3_endpoint=endpoint, s3_bucket="private-bucket")
    assert "private-bucket" not in str(error.value)


def test_legacy_endpoint_is_checked():
    with pytest.raises(ValueError, match="必须用 HTTPS"):
        load_storage_config({"R2_ENDPOINT": "http://nas.example:9000"})
    assert load_storage_config({"R2_ENDPOINT": "http://localhost"}).s3_endpoint == "http://localhost"
