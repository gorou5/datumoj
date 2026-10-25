"""Atomic state objects in an existing S3/R2 namespace; no resource provisioning."""

from __future__ import annotations

import base64
import hashlib
import uuid
from pathlib import PurePosixPath

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from datumoj.core.contracts import ContractError, PreconditionFailed
from datumoj.core.storage import StorageConfig, load_s3_credentials


def probe_conditional_writes(store: S3StateObjectStore) -> dict:
    """Exercise real conditions on one disposable key; expose fixed labels only.

    Cleanup deliberately uses an unconditional DELETE: the very capability being
    tested may be broken. That DELETE can only target this invocation's probe.
    """
    key = f"probe/{uuid.uuid4().hex}"
    result = {"supported": False, "check": "put_if_absent_create"}
    try:
        etag = store.put_if_absent(key, b"initial")
        if store.get(key) != (b"initial", etag):
            return result
        result["check"] = "put_if_absent_existing"
        try:
            store.put_if_absent(key, b"unexpected")
        except PreconditionFailed:
            pass
        else:
            return result
        if store.get(key) != (b"initial", etag):
            return result

        result["check"] = "put_if_match_update"
        etag = store.put_if_match(key, b"updated", etag)
        if store.get(key) != (b"updated", etag):
            return result
        wrong = f'"{uuid.uuid4().hex}"'
        while wrong == etag:
            wrong = f'"{uuid.uuid4().hex}"'
        result["check"] = "put_if_match_wrong_etag"
        try:
            store.put_if_match(key, b"unexpected", wrong)
        except PreconditionFailed:
            pass
        else:
            return result
        if store.get(key) != (b"updated", etag):
            return result

        result["check"] = "delete_if_match_wrong_etag"
        try:
            store.delete_if_match(key, wrong)
        except PreconditionFailed:
            pass
        else:
            return result
        if store.get(key) != (b"updated", etag):
            return result
        result["check"] = "delete_if_match_delete"
        store.delete_if_match(key, etag)
        if store.get(key) is not None:
            return result
        result.update(supported=True, check="complete")
        return result
    except Exception:
        return result
    finally:
        try:
            store._client.delete_object(Bucket=store._bucket, Key=store._key(key))
        except Exception:
            # Preserve the original failing check. A successful probe whose
            # cleanup failed must also refuse the run.
            if result["supported"]:
                result.update(supported=False, check="cleanup")
            result["cleanup_failed"] = True


def check_key(key: str, *, prefix: bool = False) -> str:
    """Keys stay relative to the configured namespace, on every operation."""
    if not isinstance(key, str) or (not key and not prefix):
        raise ValueError("无效的相对对象键")
    parts = key.rstrip("/").split("/")
    if key and (PurePosixPath(key).is_absolute() or "\\" in key or ":" in key
                or any(p in {"", ".", ".."} for p in parts) or any(ord(c) < 32 for c in key)):
        raise ValueError("无效的相对对象键")
    return key


class S3StateObjectStore:
    """StateObjectStore, with server-validated writes and opaque service ETags."""

    def __init__(self, config: StorageConfig):
        if not config.s3_endpoint or not config.s3_bucket:
            raise ValueError("状态存储需要 DATUMOJ_S3_ENDPOINT 和 DATUMOJ_S3_BUCKET")
        access_key, secret_key = load_s3_credentials()
        self._bucket = config.s3_bucket
        self._prefix = config.s3_prefix.strip("/")
        check_key(self._prefix, prefix=True)
        try:
            self._client = boto3.client(
                "s3", endpoint_url=config.s3_endpoint, region_name=config.s3_region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                              connect_timeout=10, read_timeout=60, retries={"max_attempts": 2}),
            )
        except (BotoCoreError, ValueError):
            raise ContractError("S3 状态客户端初始化失败") from None

    def _key(self, key: str, *, prefix: bool = False) -> str:
        check_key(key, prefix=prefix)
        return (self._prefix + "/" if self._prefix else "") + key

    @staticmethod
    def _error(exc: ClientError, *, conditional: bool = False, missing: bool = False) -> bool:
        code = exc.response.get("Error", {}).get("Code")
        if conditional and code in {"PreconditionFailed", "412", "NoSuchKey", "404", "NotFound"}:
            raise PreconditionFailed("S3 状态条件不满足") from None
        if missing and code in {"NoSuchKey", "404", "NotFound"}:
            return True
        raise ContractError("S3 状态操作失败") from None

    def get(self, key: str) -> tuple[bytes, str] | None:
        full = self._key(key)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=full)
            with response["Body"] as body:
                return body.read(), response["ETag"]
        except ClientError as exc:
            self._error(exc, missing=True)
            return None
        except BotoCoreError:
            raise ContractError("S3 状态下载失败") from None

    def _put(self, key: str, data: bytes, **conditions: str) -> str:
        full = self._key(key)
        # The service validates bytes in transit. Never GET a potentially multi-GB
        # content object after PUT, including when conditional creation loses.
        checksum = base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")
        try:
            response = self._client.put_object(
                Bucket=self._bucket, Key=full, Body=data, ChecksumSHA256=checksum, **conditions,
            )
            return response["ETag"]
        except ClientError as exc:
            self._error(exc, conditional=bool(conditions))
            raise AssertionError("unreachable")
        except BotoCoreError:
            raise ContractError("S3 状态上传失败") from None

    def put(self, key: str, data: bytes) -> None:
        self._put(key, data)

    def put_if_absent(self, key: str, data: bytes) -> str:
        return self._put(key, data, IfNoneMatch="*")

    def put_if_match(self, key: str, data: bytes, etag: str) -> str:
        return self._put(key, data, IfMatch=etag)

    def delete_if_match(self, key: str, etag: str) -> None:
        full = self._key(key)
        # S3 treats DELETE of an absent key as success, even with IfMatch.
        # HEAD is only for the absent-key contract; the deletion remains atomic.
        if not self.exists(key):
            raise PreconditionFailed("S3 状态条件不满足")
        try:
            self._client.delete_object(Bucket=self._bucket, Key=full, IfMatch=etag)
        except ClientError as exc:
            self._error(exc, conditional=True)
        except BotoCoreError:
            raise ContractError("S3 状态删除失败") from None

    def exists(self, key: str) -> bool:
        full = self._key(key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=full)
            return True
        except ClientError as exc:
            self._error(exc, missing=True)
            return False
        except BotoCoreError:
            raise ContractError("S3 状态检查失败") from None

    def list(self, prefix: str = "") -> list[str]:
        full = self._key(prefix, prefix=True)
        base = self._prefix + "/" if self._prefix else ""
        try:
            pages = self._client.get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=full)
            return sorted(obj["Key"][len(base):] for page in pages for obj in page.get("Contents", []))
        except (ClientError, BotoCoreError):
            raise ContractError("S3 状态列举失败") from None
