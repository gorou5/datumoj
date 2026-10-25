"""S3 contract tests use an actual HTTP endpoint on loopback, provided by moto."""

import hashlib
import io
import traceback
import uuid

import boto3
import pytest
from botocore.stub import Stubber
from moto.server import ThreadedMotoServer

from datumoj.core.contracts import ContractError, CorruptedBlob
from datumoj.core.rawstore_s3 import S3RawStore
from datumoj.core.storage import StorageConfig
from datumoj.testing.contract_suites import RawStoreSuite


@pytest.fixture(scope="module")
def endpoint():
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    host, port = server.get_host_and_port()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.stop()


@pytest.fixture
def config(endpoint, monkeypatch):
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "synthetic-test-id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "synthetic-test-secret")
    bucket = f"raw-{uuid.uuid4().hex}"
    client = boto3.client(
        "s3", endpoint_url=endpoint, region_name="us-east-1",
        aws_access_key_id="synthetic-test-id", aws_secret_access_key="synthetic-test-secret",
    )
    client.create_bucket(Bucket=bucket)
    client.close()
    return StorageConfig(raw_backend="s3", s3_endpoint=endpoint, s3_bucket=bucket, s3_prefix="/test/project/")


class TestS3RawStore(RawStoreSuite):
    @pytest.fixture
    def store(self, config):
        store = S3RawStore(config)
        try:
            yield store
        finally:
            store._client.close()

    def corrupt(self, store, sha256):
        store._client.put_object(Bucket=store._bucket, Key=store._key(sha256), Body=b"tampered")

    def test_layout_and_deduplication(self, store, monkeypatch):
        sha = store.put(b"same")
        assert store._key(sha) == f"test/project/raw/{sha[:2]}/{sha}"
        objects = store._client.list_objects_v2(Bucket=store._bucket)["Contents"]
        assert [obj["Key"] for obj in objects] == [store._key(sha)]

        def unexpected_upload(*args, **kwargs):
            pytest.fail("duplicate content was uploaded again")

        monkeypatch.setattr(store._client, "upload_fileobj", unexpected_upload)
        assert store.put(io.BytesIO(b"same")) == sha

    def test_empty_prefix(self, config):
        store = S3RawStore(config.model_copy(update={"s3_prefix": ""}))
        try:
            sha = store.put(b"no prefix")
            assert store._key(sha) == f"raw/{sha[:2]}/{sha}"
            assert store.get(sha) == b"no prefix"
        finally:
            store._client.close()

    def test_twenty_megabytes_are_streamed(self, store):
        length = 20 * 1024 * 1024 + 17

        class Source:
            remaining = length
            reads = 0
            digest = hashlib.sha256()

            def read(self, size):
                assert 0 < size <= 1024 * 1024
                block = b"x" * min(size, self.remaining)
                self.remaining -= len(block)
                self.reads += 1
                self.digest.update(block)
                return block

        source = Source()
        sha = store.put(source)
        assert sha == source.digest.hexdigest() and source.reads > 20
        assert store._client.head_object(Bucket=store._bucket, Key=store._key(sha))["ContentLength"] == length
        store.verify(sha)

    @pytest.mark.parametrize("method", ["get", "open"])
    def test_reads_reject_corruption(self, store, method):
        sha = store.put(b"original")
        self.corrupt(store, sha)
        with pytest.raises(CorruptedBlob):
            getattr(store, method)(sha)

    def test_nonbinary_stream_rejected(self, store):
        with pytest.raises(TypeError, match="bytes"):
            store.put(io.StringIO("text"))
        assert store._client.list_objects_v2(Bucket=store._bucket)["KeyCount"] == 0

    def test_open_is_readonly_and_seekable(self, store):
        sha = store.put(b"read only")
        with store.open(sha) as source:
            assert not source.writable()
            with pytest.raises(io.UnsupportedOperation):
                source.write(b"change")
            assert source.read(4) == b"read"
            source.seek(0)
            assert source.read() == b"read only"


@pytest.mark.parametrize(("missing", "legacy"), [
    ("DATUMOJ_S3_ACCESS_KEY_ID", "R2_ACCESS_KEY_ID"),
    ("DATUMOJ_S3_SECRET_ACCESS_KEY", "R2_SECRET_ACCESS_KEY"),
])
def test_missing_credentials(config, monkeypatch, missing, legacy):
    monkeypatch.delenv(missing, raising=False)
    monkeypatch.delenv(legacy, raising=False)
    with pytest.raises(ValueError, match=f"缺少 {missing}"):
        S3RawStore(config)


@pytest.mark.parametrize("operation", ["head_object", "get_object", "put_object"])
def test_wrong_credentials_do_not_leak(config, monkeypatch, caplog, operation):
    # Moto accepts arbitrary signatures. Stub its auth failure response (including
    # hostile echoed credentials) to exercise the same SDK boundary without R2.
    access, secret = "synthetic-wrong-id", "synthetic-wrong-secret"
    monkeypatch.setenv("R2_ACCESS_KEY_ID", access)
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", secret)
    store = S3RawStore(config)
    sha = hashlib.sha256(b"test").hexdigest()
    try:
        with Stubber(store._client) as stub:
            if operation == "put_object":
                stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
            stub.add_client_error(
                operation, service_error_code="SignatureDoesNotMatch",
                service_message=f"Rejected {access} {secret}", http_status_code=403,
            )
            with pytest.raises(ContractError) as error:
                if operation == "head_object":
                    store.exists(sha)
                elif operation == "get_object":
                    store.get(sha)
                else:
                    store.put(b"test")
        visible = "".join(traceback.format_exception(error.value)) + repr(store) + repr(config) + caplog.text
        assert access not in visible and secret not in visible
    finally:
        store._client.close()


def test_wiring_selects_s3_keeps_other_paths_local(config, tmp_path, monkeypatch):
    from datumoj import wiring

    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "s3")
    monkeypatch.setenv("R2_ENDPOINT", config.s3_endpoint)
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", config.s3_bucket)
    monkeypatch.setenv("DATUMOJ_S3_PREFIX", config.s3_prefix)
    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(tmp_path / "env"))
    calls = {}
    real_get = wiring._get

    def get(overrides, key, module, symbol, *args):
        if key == "raw":
            return real_get(overrides, key, module, symbol, *args)
        calls[key] = args
        return None

    monkeypatch.setattr(wiring, "_get", get)
    root = tmp_path / "explicit"
    deps = wiring.build(root, {"registry": None, "vectors": None})
    try:
        assert isinstance(deps.raw, S3RawStore) and deps.data_dir == root
        assert calls["manifest"] == (root / "manifest.sqlite",)
        assert calls["chunks"] == (root / "chunks.sqlite",)
        assert calls["keyword"] == (root / "keyword.sqlite",)
        assert calls["catalog"] == (root / "catalog.sqlite",)
        assert calls["observations"] == (root,)
        sha = deps.raw.put(b"wired")
        assert deps.raw.get(sha) == b"wired"
    finally:
        deps.raw._client.close()
