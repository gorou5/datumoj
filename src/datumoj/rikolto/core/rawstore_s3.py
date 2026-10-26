"""Content-addressed RawStore for an existing S3-compatible bucket (including R2)."""

from __future__ import annotations

import hashlib
import io
import re
import tempfile
from typing import IO

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from datumoj.rikolto.core.contracts import ContractError, CorruptedBlob, NotFoundError
from datumoj.rikolto.core.storage import StorageConfig, load_s3_credentials

_CHUNK_SIZE = 1 << 20
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class S3RawStore:
    """Spool writes/verified reads to disk; never expose SDK error payloads or keys.

    Generic DATUMOJ_S3 credentials are preferred, with R2_* aliases supported.
    The bucket must already exist; this adapter never provisions resources.
    """

    def __init__(self, config: StorageConfig):
        if config.raw_backend != "s3":
            raise ValueError("S3RawStore 需要 raw_backend=s3")
        access_key, secret_key = load_s3_credentials()
        self._bucket = config.s3_bucket
        self._prefix = config.s3_prefix.strip("/")
        try:
            self._client = boto3.client(
                "s3", endpoint_url=config.s3_endpoint, region_name=config.s3_region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=Config(
                    signature_version="s3v4", s3={"addressing_style": "path"},
                    connect_timeout=10, read_timeout=60, retries={"max_attempts": 2},
                ),
            )
        except (BotoCoreError, ValueError):
            raise ContractError("S3 客户端初始化失败，请检查 endpoint 和凭证配置") from None

    def _key(self, sha256: str) -> str:
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return f"{self._prefix + '/' if self._prefix else ''}raw/{sha256[:2]}/{sha256}"

    def put(self, data: bytes | IO[bytes]) -> str:
        with tempfile.TemporaryFile(mode="w+b") as target:
            digest = hashlib.sha256()
            if isinstance(data, bytes):
                digest.update(data)
                target.write(data)
            else:
                while block := data.read(_CHUNK_SIZE):
                    if not isinstance(block, bytes):
                        raise TypeError("raw stream must return bytes")
                    digest.update(block)
                    target.write(block)
            sha256 = digest.hexdigest()
            if self.exists(sha256):
                try:
                    self.verify(sha256)
                except (CorruptedBlob, NotFoundError):
                    pass
                else:
                    return sha256
            target.seek(0)
            try:
                self._client.upload_fileobj(
                    target, self._bucket, self._key(sha256),
                    Config=TransferConfig(use_threads=False),
                )
            except (BotoCoreError, ClientError):
                raise ContractError("S3 上传失败，请检查服务、桶权限和凭证") from None
            return sha256

    def exists(self, sha256: str) -> bool:
        key = self._key(sha256)
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise ContractError("S3 HEAD 失败，请检查服务、桶权限和凭证") from None
        except BotoCoreError:
            raise ContractError("S3 HEAD 失败，请检查服务、桶权限和凭证") from None
        return True

    def open(self, sha256: str) -> IO[bytes]:
        key = self._key(sha256)
        target = tempfile.TemporaryFile(mode="w+b")
        try:
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=key)
                digest = hashlib.sha256()
                with response["Body"] as body:
                    while block := body.read(_CHUNK_SIZE):
                        digest.update(block)
                        target.write(block)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}:
                    raise NotFoundError(f"raw 里没有 {sha256}") from None
                raise ContractError("S3 下载失败，请检查服务、桶权限和凭证") from None
            except BotoCoreError:
                raise ContractError("S3 下载失败，请检查服务、桶权限和凭证") from None
            if digest.hexdigest() != sha256:
                raise CorruptedBlob(f"raw 内容和哈希不一致：{sha256}")
            target.seek(0)
            return io.BufferedReader(target)
        except BaseException:
            target.close()
            raise

    def get(self, sha256: str) -> bytes:
        with self.open(sha256) as source:
            return source.read()

    def verify(self, sha256: str) -> None:
        with self.open(sha256):
            pass
