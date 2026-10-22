"""One storage configuration, including the existing local defaults."""

from pathlib import Path

import pytest

from datumoj.core.storage import StorageConfig, load_storage_config


def test_defaults():
    config = load_storage_config(env={})
    assert config == StorageConfig()
    assert config.data_dir == Path("./data")
    assert config.raw_backend == "local"
    assert config.s3_prefix == "" and config.s3_region == "auto"
    assert config.s3_bucket is None and config.s3_endpoint is None


def test_environment_and_explicit_directory(tmp_path):
    env = {
        "DATUMOJ_DATA_DIR": str(tmp_path / "env"), "DATUMOJ_RAW_BACKEND": "s3",
        "DATUMOJ_S3_BUCKET": "raw-bucket", "R2_ENDPOINT": "https://example.invalid",
        "DATUMOJ_S3_ENDPOINT": "https://fallback.invalid", "DATUMOJ_S3_PREFIX": "project",
        "DATUMOJ_S3_REGION": "us-east-1",
    }
    config = load_storage_config(env)
    assert config.data_dir == tmp_path / "env"
    assert config.s3_endpoint == env["R2_ENDPOINT"]
    assert config.s3_bucket == "raw-bucket" and config.s3_prefix == "project"
    assert config.s3_region == "us-east-1" and config.raw_backend == "s3"
    assert load_storage_config(env, tmp_path / "explicit").data_dir == tmp_path / "explicit"
    del env["R2_ENDPOINT"]
    assert load_storage_config(env).s3_endpoint == env["DATUMOJ_S3_ENDPOINT"]


@pytest.mark.parametrize("missing", ["DATUMOJ_S3_BUCKET", "R2_ENDPOINT"])
def test_missing_s3_settings(missing):
    env = {
        "DATUMOJ_RAW_BACKEND": "s3", "DATUMOJ_S3_BUCKET": "bucket",
        "R2_ENDPOINT": "https://example.invalid",
    }
    del env[missing]
    with pytest.raises(ValueError, match=missing):
        load_storage_config(env)


def test_unknown_backend():
    with pytest.raises(ValueError, match="raw_backend"):
        load_storage_config({"DATUMOJ_RAW_BACKEND": "ftp"})


def test_config_contains_no_credentials():
    config = load_storage_config({"R2_ACCESS_KEY_ID": "synthetic-id", "R2_SECRET_ACCESS_KEY": "synthetic-secret"})
    assert "synthetic" not in repr(config)
    assert "synthetic" not in config.model_dump_json()
    with pytest.raises(ValueError) as error:
        StorageConfig(R2_SECRET_ACCESS_KEY="synthetic-secret")
    assert "synthetic-secret" not in str(error.value)


@pytest.mark.parametrize("endpoint", [
    "https://user:synthetic-secret@example.invalid", "https://example.invalid?key=synthetic-secret",
    "https://example.invalid#synthetic-secret", "file:///tmp/bucket", "https://[invalid",
])
def test_endpoint_cannot_embed_credentials(endpoint):
    with pytest.raises(ValueError) as error:
        StorageConfig(s3_endpoint=endpoint)
    assert "synthetic-secret" not in str(error.value)


def test_all_local_entrypoints_share_config(tmp_path, monkeypatch):
    from datumoj import cli, wiring
    from datumoj.analysis.tools import AnalysisTools
    from datumoj.core.rawstore import FileRawStore

    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    assert cli._data_dir() == str(tmp_path)
    assert FileRawStore().root == tmp_path
    tools = AnalysisTools(
        retriever=None, catalog=None, observations=None, chunks=None,
        manifest=None, raw=None, registry=None,
    )
    assert tools.data_dir == tmp_path
    # Isolate wiring here from optional indexes and concrete database implementations.
    overrides = {name: None for name in (
        "registry", "manifest", "chunks", "keyword", "vectors", "catalog", "observations",
        "retriever", "tools",
    )}
    deps = wiring.build(overrides=overrides)
    assert isinstance(deps.raw, FileRawStore) and deps.raw.root == tmp_path


def test_build_tools_uses_loaded_directory(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from datumoj import wiring

    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    sentinel = object()

    def build(root):
        assert root == str(tmp_path)
        return SimpleNamespace(require=lambda feature: None, tools=sentinel)

    monkeypatch.setattr(wiring, "build", build)
    assert wiring.build_tools() is sentinel
