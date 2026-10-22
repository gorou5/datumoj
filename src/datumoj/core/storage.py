"""Storage locations shared by CLI, wiring and individual stores (no credentials)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, model_validator


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    data_dir: Path = Path("./data")
    raw_backend: Literal["local", "s3"] = "local"
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_prefix: str = ""
    s3_region: str = "auto"

    @model_validator(mode="after")
    def validate_s3(self) -> Self:
        if self.raw_backend == "s3":
            if not self.s3_bucket or not self.s3_bucket.strip():
                raise ValueError("缺少 DATUMOJ_S3_BUCKET")
            if not self.s3_endpoint:
                raise ValueError("缺少 R2_ENDPOINT（或 DATUMOJ_S3_ENDPOINT）")
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


def load_storage_config(
    env: Mapping[str, str] = os.environ, data_dir: str | Path | None = None,
) -> StorageConfig:
    """Explicit local directory wins over the environment; raw defaults to local."""
    return StorageConfig(
        data_dir=data_dir if data_dir is not None else env.get("DATUMOJ_DATA_DIR", "./data"),
        raw_backend=env.get("DATUMOJ_RAW_BACKEND", "local"),
        s3_endpoint=env.get("R2_ENDPOINT") or env.get("DATUMOJ_S3_ENDPOINT"),
        s3_bucket=env.get("DATUMOJ_S3_BUCKET"),
        s3_prefix=env.get("DATUMOJ_S3_PREFIX", ""),
        s3_region=env.get("DATUMOJ_S3_REGION", "auto"),
    )
