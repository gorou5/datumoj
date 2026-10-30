"""One storage configuration, including the existing local defaults."""

from pathlib import Path

import pytest

from datumoj.rikolto.core.storage import StorageConfig, load_storage_config


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
        "DATUMOJ_S3_ENDPOINT": "https://example.invalid", "DATUMOJ_S3_PREFIX": "project",
        "DATUMOJ_S3_REGION": "us-east-1",
    }
    config = load_storage_config(env)
    assert config.data_dir == tmp_path / "env"
    assert config.s3_endpoint == env["DATUMOJ_S3_ENDPOINT"]
    assert config.s3_bucket == "raw-bucket" and config.s3_prefix == "project"
    assert config.s3_region == "us-east-1" and config.raw_backend == "s3"
    assert load_storage_config(env, tmp_path / "explicit").data_dir == tmp_path / "explicit"
    del env["R2_ENDPOINT"]
    assert load_storage_config(env).s3_endpoint == env["DATUMOJ_S3_ENDPOINT"]


@pytest.mark.parametrize("missing", ["DATUMOJ_S3_BUCKET", "DATUMOJ_S3_ENDPOINT"])
def test_missing_s3_settings(missing):
    env = {
        "DATUMOJ_RAW_BACKEND": "s3", "DATUMOJ_S3_BUCKET": "bucket",
        "DATUMOJ_S3_ENDPOINT": "https://example.invalid",
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


def test_generic_and_legacy_s3_variables_resolve_and_conflicts_hide_values():
    from datumoj.rikolto.core.storage import load_s3_credentials

    generic = {
        "DATUMOJ_S3_ENDPOINT": "https://generic.example.invalid",
        "DATUMOJ_S3_ACCESS_KEY_ID": "synthetic-id",
        "DATUMOJ_S3_SECRET_ACCESS_KEY": "synthetic-secret",
    }
    config = load_storage_config(generic)
    assert config.s3_endpoint == generic["DATUMOJ_S3_ENDPOINT"]
    assert load_s3_credentials(generic) == ("synthetic-id", "synthetic-secret")

    legacy = {
        "R2_ENDPOINT": "https://legacy.example.invalid",
        "R2_ACCESS_KEY_ID": "synthetic-id",
        "R2_SECRET_ACCESS_KEY": "synthetic-secret",
    }
    assert load_storage_config(legacy).s3_endpoint == legacy["R2_ENDPOINT"]
    assert load_s3_credentials(legacy) == ("synthetic-id", "synthetic-secret")

    same_values = {
        **generic,
        "R2_ENDPOINT": generic["DATUMOJ_S3_ENDPOINT"],
        "R2_ACCESS_KEY_ID": generic["DATUMOJ_S3_ACCESS_KEY_ID"],
        "R2_SECRET_ACCESS_KEY": generic["DATUMOJ_S3_SECRET_ACCESS_KEY"],
    }
    assert load_storage_config(same_values).s3_endpoint == generic["DATUMOJ_S3_ENDPOINT"]
    assert load_s3_credentials(same_values) == ("synthetic-id", "synthetic-secret")

    conflicts = [
        {"DATUMOJ_S3_ENDPOINT": "https://new.example.invalid", "R2_ENDPOINT": "https://old.example.invalid"},
        {"DATUMOJ_S3_ACCESS_KEY_ID": "new-id", "R2_ACCESS_KEY_ID": "old-id"},
        {"DATUMOJ_S3_SECRET_ACCESS_KEY": "new-secret", "R2_SECRET_ACCESS_KEY": "old-secret"},
    ]
    for env in conflicts:
        with pytest.raises(ValueError) as error:
            if "DATUMOJ_S3_ENDPOINT" in env:
                load_storage_config(env)
            else:
                load_s3_credentials(env)
        output = str(error.value)
        assert "DATUMOJ_S3_" in output and "R2_" in output
        assert not any(value in output for value in env.values())


@pytest.mark.parametrize("endpoint", [
    "https://user:synthetic-secret@example.invalid", "https://example.invalid?key=synthetic-secret",
    "https://example.invalid#synthetic-secret", "file:///tmp/bucket", "https://[invalid",
])
def test_endpoint_cannot_embed_credentials(endpoint):
    with pytest.raises(ValueError) as error:
        StorageConfig(s3_endpoint=endpoint)
    assert "synthetic-secret" not in str(error.value)


def test_all_local_entrypoints_share_config(tmp_path, monkeypatch):
    from datumoj.rikolto import cli, wiring
    from datumoj.rikolto.analysis.tools import AnalysisTools
    from datumoj.rikolto.core.rawstore import FileRawStore

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

    from datumoj.rikolto import wiring

    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATUMOJ_RAW_BACKEND", "local")
    sentinel = object()

    def build(root):
        assert root == str(tmp_path)
        return SimpleNamespace(require=lambda feature: None, tools=sentinel)

    monkeypatch.setattr(wiring, "build", build)
    assert wiring.build_tools() is sentinel


def test_dir_configuration_and_lease_detection(tmp_path):
    from datumoj.rikolto.core.storage import s3_remote_configured

    env = {"DATUMOJ_STATE_BACKEND": "dir", "DATUMOJ_STATE_DIR": str(tmp_path / "state")}
    config = load_storage_config(env, tmp_path / "data")
    assert config.state_backend == "dir" and config.state_dir == tmp_path / "state"
    assert s3_remote_configured(env)
    assert not s3_remote_configured({})
    assert load_storage_config({}).state_backend == "s3"
    with pytest.raises(ValueError):
        load_storage_config({"DATUMOJ_STATE_BACKEND": "unknown"})


@pytest.mark.parametrize("location", [None, "relative", "data", "data/nested", "."])
def test_invalid_or_overlapping_state_directory(tmp_path, location):
    state = None if location is None else (location if location == "relative" else tmp_path / location)
    with pytest.raises(ValueError, match="DATUMOJ_STATE_DIR"):
        StorageConfig(data_dir=tmp_path / "data", state_backend="dir", state_dir=state)


def test_symlink_overlap_rejected(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "data")
    with pytest.raises(ValueError, match="重叠"):
        StorageConfig(data_dir=tmp_path / "data", state_backend="dir", state_dir=tmp_path / "alias/state")
