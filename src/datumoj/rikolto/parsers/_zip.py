"""Bounded, atomic ZIP reads in memory; member names are never filesystem paths."""

from __future__ import annotations

import io
import stat
import struct
import zlib
from pathlib import PurePosixPath, PureWindowsPath
from zipfile import BadZipFile, ZipFile, ZipInfo

from datumoj.rikolto.core.contracts import ParseError

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_MEMBERS = 10_000


def _name(info: ZipInfo) -> str:
    name = info.orig_filename
    if not info.flag_bits & 0x800:
        raw = name.encode("cp437")  # Recover bytes decoded by zipfile's legacy default.
        try:
            name = raw.decode("cp932")
        except UnicodeDecodeError:
            name = raw.decode("utf-8")
    path = PurePosixPath(name)
    if (not name or "\x00" in name or "\\" in name or path.is_absolute()
            or PureWindowsPath(name).drive or ".." in path.parts or path == PurePosixPath(".")):
        raise ParseError(f"Unsafe ZIP member path: {name!r}")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ParseError(f"ZIP symlink is forbidden: {name!r}")
    return name


def read_zip(
    data: bytes, *, max_total_bytes: int = MAX_UNCOMPRESSED_BYTES, max_members: int = MAX_MEMBERS,
) -> dict[str, bytes]:
    """Validate every entry first, then read all members or fail without returning partial data.

    No recursive expansion. Callers decode each member with its own known encoding.
    The compressed input is capped at 25 MiB; limits may only tighten the defaults.
    """
    if not 0 < max_total_bytes <= MAX_UNCOMPRESSED_BYTES or not 0 < max_members <= MAX_MEMBERS:
        raise ValueError("ZIP limits must be positive and cannot exceed the defaults")
    if len(data) > MAX_FILE_BYTES:
        raise ParseError("ZIP input exceeds 25 MiB")
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > max_members:
                raise ParseError("ZIP member count exceeds limit")
            if sum(info.file_size for info in entries) > max_total_bytes:
                raise ParseError("ZIP uncompressed size exceeds limit")
            names = [_name(info) for info in entries]
            if len({str(PurePosixPath(name)) for name in names}) != len(names):
                raise ParseError("Duplicate ZIP member path")
            result = {}
            total = 0
            for name, info in zip(names, entries, strict=True):
                if info.is_dir():
                    continue
                with archive.open(info) as source:
                    payload = source.read(max_total_bytes - total + 1)
                total += len(payload)
                if total > max_total_bytes or len(payload) != info.file_size:
                    raise ParseError("ZIP expanded size exceeds limit or differs from declaration")
                result[name] = payload
            return result
    except (BadZipFile, UnicodeError, RuntimeError, NotImplementedError, OSError, EOFError,
            ValueError, struct.error, zlib.error) as exc:
        raise ParseError(f"Invalid ZIP: {exc}") from exc
