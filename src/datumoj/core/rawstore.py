"""Immutable, content-addressed raw blob storage."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import IO

from datumoj.core.contracts import CorruptedBlob, NotFoundError


_CHUNK_SIZE = 1 << 20
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class FileRawStore:
    """Store immutable blobs under ``<root>/raw/<prefix>/<sha256>``."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(
            root if root is not None else os.environ.get("DATUMOJ_DATA_DIR", "./data")
        )

    def put(self, data: bytes | IO[bytes]) -> str:
        raw_root = self.root / "raw"
        raw_root.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".tmp-", dir=raw_root)
        temp_path = Path(temp_name)
        try:
            digest = hashlib.sha256()
            with os.fdopen(fd, "wb") as target:
                if isinstance(data, bytes):
                    blocks = (data,)
                else:
                    def read_blocks():
                        while block := data.read(_CHUNK_SIZE):
                            if not isinstance(block, bytes):
                                raise TypeError("raw stream must return bytes")
                            yield block

                    blocks = read_blocks()
                for block in blocks:
                    digest.update(block)
                    target.write(block)

            sha256 = digest.hexdigest()
            path = self.path_for(sha256)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            if not path.exists():
                os.replace(temp_path, path)
            return sha256
        finally:
            temp_path.unlink(missing_ok=True)

    def get(self, sha256: str) -> bytes:
        with self.open(sha256) as source:
            return source.read()

    def open(self, sha256: str) -> IO[bytes]:
        path = self.path_for(sha256)
        try:
            return path.open("rb")
        except FileNotFoundError:
            raise NotFoundError(f"raw 里没有 {sha256}") from None

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def verify(self, sha256: str) -> None:
        digest = hashlib.sha256()
        with self.open(sha256) as source:
            while block := source.read(_CHUNK_SIZE):
                digest.update(block)
        if digest.hexdigest() != sha256:
            raise CorruptedBlob(f"raw 内容和哈希不一致：{sha256}")

    def path_for(self, sha256: str) -> Path:
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return self.root / "raw" / sha256[:2] / sha256
