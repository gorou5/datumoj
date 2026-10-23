"""Immutable, content-addressed raw blob storage."""

from __future__ import annotations

import hashlib
import io
import os
import re
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from datumoj.core.contracts import CorruptedBlob, NotFoundError
from datumoj.core.storage import load_storage_config


_CHUNK_SIZE = 1 << 20
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class _VerifiedFile(io.RawIOBase):
    """Hash a file as it is consumed and quarantine it if the digest is wrong."""

    def __init__(self, source: IO[bytes], path: Path, sha256: str):
        self._source = source
        self._path = path
        self._sha256 = sha256
        self._digest = hashlib.sha256()
        info = os.fstat(source.fileno())
        self._identity = info.st_dev, info.st_ino
        self._checked = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        count = self._source.readinto(buffer)
        if count:
            self._digest.update(memoryview(buffer)[:count])
            return count
        if not self._checked:
            self._checked = True
            if self._digest.hexdigest() != self._sha256:
                self._quarantine()
                raise CorruptedBlob(f"raw 内容和哈希不一致：{self._sha256}")
        return 0

    def _quarantine(self) -> None:
        try:
            info = self._path.stat()
        except FileNotFoundError:
            return
        if (info.st_dev, info.st_ino) != self._identity:
            return

        quarantine = self._path.parent.parent / ".quarantine"
        quarantine.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        target = quarantine / f"{self._sha256}-{timestamp}"
        suffix = 1
        while target.exists():
            target = quarantine / f"{self._sha256}-{timestamp}-{suffix}"
            suffix += 1
        try:
            os.replace(self._path, target)
        except FileNotFoundError:
            # Another reader or writer moved/replaced the same blob meanwhile.
            return

    def close(self) -> None:
        try:
            self._source.close()
        finally:
            super().close()


class FileRawStore:
    """Store immutable blobs under ``<root>/raw/<prefix>/<sha256>``."""

    def __init__(self, root: str | Path | None = None):
        self.root = load_storage_config(data_dir=root).data_dir

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
            if path.exists():
                try:
                    self.verify(sha256)
                except (CorruptedBlob, NotFoundError):
                    pass
                else:
                    return sha256
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
            source = path.open("rb")
        except FileNotFoundError:
            raise NotFoundError(f"raw 里没有 {sha256}") from None
        return io.BufferedReader(_VerifiedFile(source, path, sha256))

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def verify(self, sha256: str) -> None:
        with self.open(sha256) as source:
            while source.read(_CHUNK_SIZE):
                pass

    def path_for(self, sha256: str) -> Path:
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return self.root / "raw" / sha256[:2] / sha256
