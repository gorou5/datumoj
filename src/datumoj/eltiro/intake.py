"""Local file trust boundary. Demand text is never executed or included in receipts."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import yaml
from pydantic import TypeAdapter, ValidationError

from datumoj.eltiro.models import (
    MAX_FILE_BYTES,
    MODEL_TIERS,
    ConsumerConfig,
    ConsumersConfig,
    DemandSpec,
    Id,
    Version,
    load_demand,
)
from datumoj.eltiro.registry_store import DemandConflict, DemandLimitExceeded, SQLiteDemandRegistry
from datumoj.rikolto.core.contracts import Registry

SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z")
MESSAGES = {
    "accepted": "Demand registered.",
    "invalid_demand": "Demand validation failed.",
    "consumer_mismatch": "Demand consumer does not match this inbox.",
    "source_forbidden": "A preferred source is unavailable or unauthorized.",
    "outflow_forbidden": "Requested license acceptance exceeds authorization.",
    "model_forbidden": "Requested model settings exceed authorization.",
    "active_limit": "Active demand limit reached.",
    "version_conflict": "Demand identity cannot be replaced.",
    "file_too_large": "Demand exceeds the file size limit.",
    "symlink": "Symbolic links are not accepted.",
    "unsafe_filename": "File name contains unsupported characters.",
    "file_changed": "File changed during intake; publish complete files atomically.",
    "file_unreadable": "File could not be read safely.",
    "not_regular": "Only regular files are accepted.",
}


def open_directory(root: Path, path: Path) -> int:
    """Open/create below a trusted real root, refusing symlinks on every component."""
    relative = path.relative_to(root)
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts:
            if part in (".", ".."):
                raise ValueError("invalid directory")
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _directory(root: Path, value: str) -> Path:
    path = root / value
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("consumer directory must be inside data directory")
    # Refuse directory symlinks even when their current target is inside the root.
    if path.absolute() != resolved:
        raise ValueError("consumer directories must use real paths without traversal")
    return resolved


def load_consumers(data_dir: str | Path) -> ConsumersConfig:
    root = Path(data_dir).resolve()
    state = root / "eltiro"
    descriptor = open_directory(root, state)
    try:
        file_descriptor = os.open("consumers.yaml", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file_descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise ValueError("invalid consumer configuration file")
            content = stream.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ValueError("consumer configuration too large")
        if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(content)):
            raise ValueError("consumer configuration aliases are not allowed")
        config = ConsumersConfig.model_validate(yaml.safe_load(content))
        directories = [_directory(root, value) for consumer in config.consumers
                       for value in (consumer.inbox, consumer.outbox)]
        for index, directory in enumerate(directories):
            if any(directory.is_relative_to(other) or other.is_relative_to(directory)
                   for other in directories[:index]):
                raise ValueError("consumer directories must not overlap")
            if any(internal.is_relative_to(directory) or directory.is_relative_to(internal)
                   for internal in (state / "consumers.yaml", state / "audit.jsonl", state / "demands.sqlite3",
                                    state / "intake.lock")):
                raise ValueError("consumer directories overlap internal state")
        return config
    except (ValueError, OSError, yaml.YAMLError, UnicodeError, RecursionError):
        raise ValueError("invalid consumer configuration") from None
    finally:
        os.close(descriptor)


def _policy_code(demand: DemandSpec, consumer: ConsumerConfig, sources: Registry) -> str:
    if demand.consumer != consumer.id:
        return "consumer_mismatch"
    preferred = {source for indicator in demand.indicators for source in indicator.preferred_sources}
    if any(sources.get(source) is None or source not in consumer.allowed_sources for source in preferred):
        return "source_forbidden"
    if not set(demand.accept_outflow) <= set(consumer.accept_outflow):
        return "outflow_forbidden"
    models, limits = demand.models, consumer.models
    if (MODEL_TIERS.index(models.max_tier) > MODEL_TIERS.index(limits.max_tier)
            or (models.allow_cloud and not limits.allow_cloud)
            or models.max_calls_per_run > consumer.max_calls_per_run
            or (models.max_tier != "none" and not preferred <= set(limits.authorized_sources))
            or (models.max_tier != "none" and not set(limits.allowed_providers) - {"none"})
            or (models.allow_cloud and not set(limits.allowed_providers) & {"cloud_api", "claude_plan"})):
        return "model_forbidden"
    # Registry licenses remain authoritative at routing/output time. accept_outflow is
    # an acceptance set, not a grant of raw export or permission for cloud processing.
    return "accepted"


def _read_file(directory: int, name: str) -> tuple[bytes, str | None, str, os.stat_result]:
    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode):
        # Hash the link itself, never its target; do not reveal its text.
        digest = hashlib.sha256(os.fsencode(os.readlink(name, dir_fd=directory))).hexdigest()
        return b"", digest, "symlink", info
    if not stat.S_ISREG(info.st_mode):
        return b"", None, "not_regular", info
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    digest = hashlib.sha256()
    content = bytearray()
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            return b"", digest.hexdigest(), "file_changed", info
        too_large = opened.st_size > MAX_FILE_BYTES  # Decide before reading or parsing.
        remaining = opened.st_size + 1
        while remaining:
            chunk = stream.read(min(65536, remaining))
            if not chunk:
                break
            digest.update(chunk)
            if not too_large:
                content.extend(chunk)
            remaining -= len(chunk)
        after = os.fstat(stream.fileno())
    if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        return b"", digest.hexdigest(), "file_changed", info
    code = "file_too_large" if too_large or len(content) > MAX_FILE_BYTES else "accepted"
    return bytes(content), digest.hexdigest(), code, after


def _json_file(directory: int, name: str, value: dict) -> None:
    """Publish a complete JSON file atomically, without replacing an existing receipt."""
    temporary = "." + uuid4().hex
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
    finally:
        os.unlink(temporary, dir_fd=directory)


class IntakeService:
    def __init__(self, data_dir: str | Path, registry: SQLiteDemandRegistry, sources: Registry):
        self.root = Path(data_dir).resolve()
        self.config = load_consumers(self.root)
        self.registry = registry
        self.sources = sources

    @contextmanager
    def _locked(self):
        state = open_directory(self.root, self.root / "eltiro")
        try:
            descriptor = os.open("intake.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600, dir_fd=state)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode) or os.fstat(descriptor).st_nlink != 1:
                    raise ValueError("invalid intake lock")
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                # Check all destinations before moving files or changing the registry.
                for consumer in self.config.consumers:
                    for directory in (consumer.outbox, str(Path(consumer.inbox) / "processed"),
                                      str(Path(consumer.inbox) / "rejected")):
                        os.close(open_directory(self.root, _directory(self.root, directory)))
                audit = os.open("audit.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                0o600, dir_fd=state)
                try:
                    if not stat.S_ISREG(os.fstat(audit).st_mode) or os.fstat(audit).st_nlink != 1:
                        raise ValueError("invalid audit file")
                finally:
                    os.close(audit)
                yield
            finally:
                os.close(descriptor)
        finally:
            os.close(state)

    def consumer(self, consumer_id: str) -> ConsumerConfig:
        for consumer in self.config.consumers:
            if consumer.id == consumer_id:
                return consumer
        raise ValueError("unknown consumer")

    def scan(self) -> dict[str, int]:
        with self._locked():
            return self._scan()

    def _scan(self) -> dict[str, int]:
        counts = {"accepted": 0, "rejected": 0}
        for consumer in self.config.consumers:
            inbox = open_directory(self.root, _directory(self.root, consumer.inbox))
            try:
                for name in sorted(os.listdir(inbox)):
                    if name.startswith(".") or stat.S_ISDIR(os.stat(name, dir_fd=inbox, follow_symlinks=False).st_mode):
                        continue
                    receipt = self._process(consumer, name)
                    counts[receipt["conclusion"]] += 1
            finally:
                os.close(inbox)
        return counts

    def process(self, consumer: ConsumerConfig, name: str) -> dict:
        with self._locked():
            return self._process(self.consumer(consumer.id), name)

    def _process(self, consumer: ConsumerConfig, name: str) -> dict:
        if Path(name).name != name or name in (".", ".."):
            raise ValueError("invalid intake filename")
        inbox_path = _directory(self.root, consumer.inbox)
        inbox = open_directory(self.root, inbox_path)
        try:
            try:
                content, digest, code, info = _read_file(inbox, name)
            except OSError:
                info = os.stat(name, dir_fd=inbox, follow_symlinks=False)
                content, digest, code = b"", None, "file_unreadable"
            if code == "accepted" and not SAFE_NAME.fullmatch(name):
                code = "unsafe_filename"
            demand = None
            identity = None
            if code == "accepted":
                try:
                    demand = load_demand(content)
                except ValidationError:
                    # Parsing succeeded, but a field failed validation. Reflect only
                    # validated identifiers belonging to this inbox, never error input.
                    data = yaml.safe_load(content)
                    try:
                        if data.get("consumer") == consumer.id:
                            identity = (TypeAdapter(Id).validate_python(data.get("id")),
                                        TypeAdapter(Version).validate_python(data.get("version")))
                    except (ValueError, AttributeError):
                        pass
                    code = "invalid_demand"
                except ValueError:
                    code = "invalid_demand"
                else:
                    code = _policy_code(demand, consumer, self.sources)
                    if demand.consumer == consumer.id:
                        identity = (demand.id, demand.version)
            current = os.stat(name, dir_fd=inbox, follow_symlinks=False)
            unchanged = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) == (
                info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if not unchanged:
                code = "file_changed"
            # The destinations were preflighted under the process lock. A rename
            # into a newly reserved empty directory never overwrites an archive.
            conclusion = "accepted" if code == "accepted" else "rejected"
            if code == "accepted":
                try:
                    self.registry.register(demand, file_sha256=digest, max_active_demands=consumer.max_active_demands)
                except DemandConflict:
                    code = "version_conflict"
                except DemandLimitExceeded:
                    code = "active_limit"
                conclusion = "accepted" if code == "accepted" else "rejected"
            # Invalid/mismatched documents cannot reflect an asserted foreign identity.
            receipt = {"conclusion": conclusion, "demand_id": identity[0] if identity else None,
                       "version": identity[1] if identity else None, "file_sha256": digest,
                       "reason_code": code, "message": MESSAGES[code]}
            ticket = uuid4().hex
            # Leave the input retryable if audit/receipt persistence fails. Registry
            # registration is idempotent; an interrupted retry may append another audit.
            self._record(consumer, ticket, receipt)
            if unchanged:
                archive = open_directory(self.root, inbox_path / ("processed" if conclusion == "accepted" else "rejected"))
                try:
                    os.mkdir(ticket, 0o700, dir_fd=archive)
                    destination = os.open(ticket, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=archive)
                    try:
                        os.rename(name, name if SAFE_NAME.fullmatch(name) else "demand.bin",
                                  src_dir_fd=inbox, dst_dir_fd=destination)
                    finally:
                        os.close(destination)
                finally:
                    os.close(archive)
            return receipt
        finally:
            os.close(inbox)

    def _record(self, consumer: ConsumerConfig, ticket: str, receipt: dict) -> None:
        state = open_directory(self.root, self.root / "eltiro")
        try:
            descriptor = os.open("audit.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600, dir_fd=state)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError("audit must be a regular file")
                entry = {"time": datetime.now(UTC).isoformat(), "consumer": consumer.id,
                         "file_sha256": receipt["file_sha256"], "conclusion": receipt["conclusion"],
                         "reason_code": receipt["reason_code"]}
                line = (json.dumps(entry) + "\n").encode()
                if os.write(descriptor, line) != len(line):
                    raise OSError("incomplete audit write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            os.close(state)
        outbox = open_directory(self.root, _directory(self.root, consumer.outbox))
        try:
            _json_file(outbox, ticket + ".json", receipt)
        finally:
            os.close(outbox)

    def submit(self, consumer_id: str, file: str | Path) -> dict:
        with self._locked():
            return self._submit(consumer_id, file)

    def _submit(self, consumer_id: str, file: str | Path) -> dict:
        consumer = self.consumer(consumer_id)
        path = Path(file)
        # Read through the same no-follow/size/hash checks before copying to the inbox.
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            content, digest, code, _ = _read_file(parent, path.name)
        finally:
            os.close(parent)
        if code == "accepted" and not SAFE_NAME.fullmatch(path.name):
            code = "unsafe_filename"
        if code != "accepted":
            receipt = {"conclusion": "rejected", "demand_id": None, "version": None, "file_sha256": digest,
                       "reason_code": code, "message": MESSAGES[code]}
            self._record(consumer, uuid4().hex, receipt)
            return receipt
        inbox = open_directory(self.root, _directory(self.root, consumer.inbox))
        name = uuid4().hex + ".yaml"
        temporary = "." + uuid4().hex
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=inbox)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, name, src_dir_fd=inbox, dst_dir_fd=inbox, follow_symlinks=False)
        finally:
            os.unlink(temporary, dir_fd=inbox)
            os.close(inbox)
        return self._process(consumer, name)
