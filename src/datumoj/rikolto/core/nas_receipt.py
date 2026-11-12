"""Version 1 NAS raw archive receipts, using one namespace-relative storage key.

Only verified content hashes and a UTC archive time cross the storage boundary.
Malformed, unavailable or stale receipts disable early deletion. Publishing a
receipt needs no lease and performs exactly one PUT, with no remote reads.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from datumoj.rikolto.core.contracts import StateObjectStore

RECEIPT_KEY = "receipts/nas-raw.json"
RAW_LIFETIME = timedelta(hours=48)
_SHA = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class NasReceipt:
    archived_at: datetime
    sha256: frozenset[str]


def _unique_fields(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate receipt field")
    return value


def parse_receipt(data: bytes | None, *, now: datetime) -> tuple[str, NasReceipt | None]:
    """Validate the entire receipt before exposing any confirmed hashes."""
    if data is None:
        return "missing", None
    try:
        value = json.loads(data, object_pairs_hook=_unique_fields)
        if (not isinstance(value, dict) or set(value) != {"version", "archived_at", "sha256"}
                or type(value["version"]) is not int or value["version"] != 1
                or not isinstance(value["archived_at"], str)
                or not isinstance(value["sha256"], list)
                or any(not isinstance(sha, str) or not _SHA.fullmatch(sha) for sha in value["sha256"])):
            return "invalid", None
        archived_at = datetime.fromisoformat(value["archived_at"])
        if archived_at.utcoffset() != timedelta(0) or archived_at > now:
            return "invalid", None
        if now - archived_at > RAW_LIFETIME:
            return "stale", None
        return "ok", NasReceipt(archived_at, frozenset(value["sha256"]))
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return "invalid", None


def read_receipt(store: StateObjectStore, *, now: datetime) -> tuple[str, NasReceipt | None]:
    try:
        found = store.get(RECEIPT_KEY)
        return parse_receipt(found[0] if found is not None else None, now=now)
    except Exception:  # noqa: BLE001 -- Receipt I/O must fall back to age-based cleanup.
        return "invalid", None


def publish_receipt(store: StateObjectStore, raw_dir: Path, *, now: datetime | None = None) -> dict:
    """Stream/hash raw/<prefix>/<sha> files, skipping mismatches and symlinks.

    Missing/unreadable input raises before uploading. The output omits all paths.
    """
    if not raw_dir.is_dir() or raw_dir.is_symlink():
        raise ValueError("raw directory required")
    hashes = []
    for prefix in sorted(raw_dir.iterdir()):
        if prefix.is_symlink() or not prefix.is_dir() or not re.fullmatch(r"[0-9a-f]{2}", prefix.name):
            continue
        for path in sorted(prefix.iterdir()):
            if (path.is_symlink() or not path.is_file() or not _SHA.fullmatch(path.name)
                    or prefix.name != path.name[:2]):
                continue
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest == path.name:
                hashes.append(digest)
    archived_at = now or datetime.now(UTC)
    if archived_at.tzinfo is None:
        raise ValueError("aware archive time required")
    receipt = {"version": 1, "archived_at": archived_at.astimezone(UTC).isoformat(), "sha256": hashes}
    store.put(RECEIPT_KEY, json.dumps(receipt, separators=(",", ":")).encode())
    return receipt
