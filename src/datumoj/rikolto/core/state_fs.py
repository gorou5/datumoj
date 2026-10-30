"""POSIX local-disk state objects; network/FUSE filesystems are unsupported."""

from __future__ import annotations

import ctypes
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from datumoj.rikolto.core.contracts import PreconditionFailed
from datumoj.rikolto.core.state_s3 import check_key


def _check_local(fd: int) -> None:
    """Fail closed on unrecognized mounts; do not infer locality from a path."""
    buffer = (ctypes.c_long * 512)()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.fstatfs(fd, ctypes.byref(buffer)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if sys.platform == "darwin":
        # Darwin statfs: f_flags at byte 64, f_fstypename[16] at byte 72.
        # statvfs omits MNT_LOCAL, so query statfs rather than f_flag there.
        flags = ctypes.c_uint32.from_buffer(buffer, 64).value
        kind = bytes(buffer)[72:88].split(b"\0", 1)[0]
        if not flags & 0x1000 or kind not in {b"apfs", b"hfs", b"ufs", b"tmpfs"}:
            raise ValueError("目录状态存储只支持本机磁盘，不支持网络挂载")
    elif sys.platform.startswith("linux"):
        # Linux statfs starts with a native long f_type. A generously sized,
        # aligned buffer avoids depending on the rest of the platform struct.
        local_types = {
            0xEF53, 0x58465342, 0x9123683E, 0x01021994, 0x858458F6,
            0x2FC12FC1, 0xF2F52010, 0x794C7630, 0x52654973,
        }  # ext, XFS, Btrfs, tmpfs, ramfs, ZFS, F2FS, overlay, ReiserFS
        if buffer[0] & 0xFFFFFFFF not in local_types:
            raise ValueError("目录状态存储拒绝网络、FUSE 或未知文件系统，请使用 S3")
    else:
        raise ValueError("目录状态存储仅支持 macOS/Linux 本机磁盘")


class DirStateObjectStore:
    """Atomic version envelopes under a private root, serialized by key flock.

    Each key segment is a directory named ``k-<segment>``; its ``value`` file
    contains a 32-byte random ETag followed by the payload. Thus keys ``a`` and
    ``a/b`` coexist, and lock/temp names cannot collide with object keys.
    Never remove lock files: replacing their inode would split concurrent locks.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        if not self.root.is_absolute() or self.root.is_symlink():
            raise ValueError("状态目录必须是绝对路径且不能是符号链接")
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()
        with self._directory(""):
            pass

    @contextmanager
    def _directory(self, key: str, *, create: bool = False):
        # Keep directory descriptors pinned and never follow child symlinks,
        # including while another process replaces a pathname.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self.root, flags)
        try:
            _check_local(fd)
            for part in key.split("/") if key else ():
                name = "k-" + part
                if create:
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=fd)
                        os.fsync(fd)
                    except FileExistsError:
                        pass
                child = os.open(name, flags, dir_fd=fd)
                os.close(fd)
                fd = child
                _check_local(fd)
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def _read(fd: int, *, payload: bool = True) -> tuple[bytes, str] | None:
        try:
            stream_fd = os.open("value", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            return None
        with os.fdopen(stream_fd, "rb") as stream:
            version = stream.read(32)
            if len(version) != 32 or any(c not in b"0123456789abcdef" for c in version):
                raise ValueError("目录状态对象版本损坏")
            return stream.read() if payload else b"", version.decode("ascii")

    def get(self, key: str) -> tuple[bytes, str] | None:
        check_key(key)
        try:
            with self._directory(key) as fd:
                return self._read(fd)
        except FileNotFoundError:
            return None

    def _write(self, key: str, data: bytes | None, *, mode: str = "put", etag: str = "") -> str:
        import fcntl

        check_key(key)
        with self._directory(key, create=True) as fd:
            try:
                lock_fd = os.open("lock", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                                  0o600, dir_fd=fd)
            except FileExistsError:
                lock_fd = os.open("lock", os.O_RDWR | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(lock_fd, "rb") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                current = self._read(fd, payload=False)
                if ((mode == "absent" and current is not None)
                        or (mode in {"match", "delete"} and (current is None or current[1] != etag))):
                    raise PreconditionFailed("目录状态条件不满足")
                if mode == "delete":
                    os.unlink("value", dir_fd=fd)
                    os.fsync(fd)
                    return ""
                version = uuid.uuid4().hex
                temporary = "tmp-" + version
                try:
                    temp_fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                                      0o600, dir_fd=fd)
                    with os.fdopen(temp_fd, "wb") as stream:
                        stream.write(version.encode("ascii"))
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, "value", src_dir_fd=fd, dst_dir_fd=fd)
                    os.fsync(fd)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=fd)
                    except FileNotFoundError:
                        pass
                return version

    def put(self, key: str, data: bytes) -> None:
        self._write(key, data)

    def put_if_absent(self, key: str, data: bytes) -> str:
        return self._write(key, data, mode="absent")

    def put_if_match(self, key: str, data: bytes, etag: str) -> str:
        return self._write(key, data, mode="match", etag=etag)

    def delete_if_match(self, key: str, etag: str) -> None:
        self._write(key, None, mode="delete", etag=etag)

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    def list(self, prefix: str = "") -> list[str]:
        check_key(prefix, prefix=True)
        keys = []

        def visit(key):
            with self._directory(key) as fd:
                if key and key.startswith(prefix) and self._read(fd, payload=False) is not None:
                    keys.append(key)
                for name in os.listdir(fd):
                    if name.startswith("k-"):
                        child = f"{key}/{name[2:]}" if key else name[2:]
                        check_key(child)
                        visit(child)

        visit("")
        return sorted(keys)
