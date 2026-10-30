"""Storage locations shared by CLI, wiring and individual stores (no credentials)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from datumoj.rikolto.core.contracts import StateObjectStore


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    data_dir: Path = Path("./data")
    raw_backend: Literal["local", "s3"] = "local"
    state_backend: Literal["s3", "dir"] = "s3"
    state_dir: Path | None = None
    s3_endpoint: str | None = Field(default=None, repr=False)
    s3_bucket: str | None = None
    s3_prefix: str = ""
    s3_region: str = "auto"

    @model_validator(mode="after")
    def validate_s3(self) -> Self:
        if self.state_backend == "dir":
            if self.state_dir is None or not self.state_dir.is_absolute():
                raise ValueError("DATUMOJ_STATE_DIR 必须是绝对路径")
            state, data = self.state_dir.resolve(), self.data_dir.resolve()
            if state.is_relative_to(data) or data.is_relative_to(state):
                raise ValueError("DATUMOJ_STATE_DIR 不能与 data_dir 重叠")
        if self.raw_backend == "s3":
            if not self.s3_bucket or not self.s3_bucket.strip():
                raise ValueError("缺少 DATUMOJ_S3_BUCKET")
            if not self.s3_endpoint:
                raise ValueError("缺少 DATUMOJ_S3_ENDPOINT（旧名 R2_ENDPOINT 也可）")
        if self.s3_endpoint:
            try:
                url = urlsplit(self.s3_endpoint)
                valid = (
                    url.scheme in {"https", "http"} and url.hostname and url.port != 0
                    and not url.username and not url.password and not url.query and not url.fragment
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("S3 endpoint 必须是无凭证、无查询参数的 HTTP(S) URL")
        return self


def _resolve_s3_value(
    env: Mapping[str, str], name: str, old_name: str | None = None,
) -> str | None:
    value = env.get(name)
    old_value = env.get(old_name) if old_name else None
    if value and old_value and value != old_value:
        raise ValueError(f"配置冲突：{name} 与 {old_name}")
    return value or old_value


def load_s3_credentials(env: Mapping[str, str] = os.environ) -> tuple[str, str]:
    """Resolve credentials without putting them in a config object or errors."""
    access_key = _resolve_s3_value(env, "DATUMOJ_S3_ACCESS_KEY_ID", "R2_ACCESS_KEY_ID")
    secret_key = _resolve_s3_value(env, "DATUMOJ_S3_SECRET_ACCESS_KEY", "R2_SECRET_ACCESS_KEY")
    if not access_key:
        raise ValueError("缺少 DATUMOJ_S3_ACCESS_KEY_ID（旧名 R2_ACCESS_KEY_ID 也可）")
    if not secret_key:
        raise ValueError("缺少 DATUMOJ_S3_SECRET_ACCESS_KEY（旧名 R2_SECRET_ACCESS_KEY 也可）")
    return access_key, secret_key


def s3_remote_configured(env: Mapping[str, str] = os.environ) -> bool:
    """Whether shared state needs a lease, including incomplete configuration."""
    endpoint = _resolve_s3_value(env, "DATUMOJ_S3_ENDPOINT", "R2_ENDPOINT")
    return bool(env.get("DATUMOJ_STATE_BACKEND", "s3") != "s3"
                or endpoint or env.get("DATUMOJ_S3_BUCKET"))


def load_storage_config(
    env: Mapping[str, str] = os.environ, data_dir: str | Path | None = None,
) -> StorageConfig:
    """Explicit local directory wins over the environment; raw defaults to local."""
    # Validate legacy credential aliases even when the selected backend is local.
    _resolve_s3_value(env, "DATUMOJ_S3_ACCESS_KEY_ID", "R2_ACCESS_KEY_ID")
    _resolve_s3_value(env, "DATUMOJ_S3_SECRET_ACCESS_KEY", "R2_SECRET_ACCESS_KEY")
    return StorageConfig(
        data_dir=data_dir if data_dir is not None else env.get("DATUMOJ_DATA_DIR", "./data"),
        raw_backend=env.get("DATUMOJ_RAW_BACKEND", "local"),
        state_backend=env.get("DATUMOJ_STATE_BACKEND", "s3"),
        state_dir=env.get("DATUMOJ_STATE_DIR") or None,
        s3_endpoint=_resolve_s3_value(env, "DATUMOJ_S3_ENDPOINT", "R2_ENDPOINT"),
        s3_bucket=env.get("DATUMOJ_S3_BUCKET"),
        s3_prefix=env.get("DATUMOJ_S3_PREFIX", ""),
        s3_region=env.get("DATUMOJ_S3_REGION", "auto"),
    )


def build_state_store(config: StorageConfig) -> StateObjectStore:
    """Select the state backend once for publication, pull and write leases."""
    if config.state_backend == "dir":
        from datumoj.rikolto.core.state_fs import DirStateObjectStore

        return DirStateObjectStore(config.state_dir)
    from datumoj.rikolto.core.state_s3 import S3StateObjectStore

    return S3StateObjectStore(config)
