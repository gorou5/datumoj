"""Read-only, verified installation of the current remote state generation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from datumoj.rikolto.core import schedule as scheduling
from datumoj.rikolto.core.contracts import ContractError, StateObjectStore
from datumoj.rikolto.core.models import StateGeneration, VectorCoverage
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.core.storage import build_state_store, load_storage_config

PULL_RECORD = "state-pull.json"
OBJECT_CACHE = ".pull-cache"
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class PullError(ContractError):
    """Sanitized pull failure; never includes remote configuration or data."""


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_file(root: Path, name: str) -> Path | None:
    parts = PurePosixPath(name).parts
    if not name or PurePosixPath(name).is_absolute() or any(part in {"", ".", ".."} for part in parts):
        return None
    try:
        base = root.resolve()
        path = root.joinpath(*parts)
        resolved = path.resolve()
        return path if resolved.is_relative_to(base) else None
    except OSError:
        return None


class _ReadThroughStore:
    """Reuse locally verified SHA-addressed copies and raw blobs before R2 reads."""

    def __init__(
        self, store: StateObjectStore, reuse_root: Path, previous_root: Path, generation: StateGeneration,
        download_cache: Path,
    ):
        self.store = store
        self.reuse_root = reuse_root
        self.previous_root = previous_root
        self.downloaded_objects = 0
        self.download_cache = download_cache
        self._copy_names: dict[str, list[str]] = {}
        self._copy_formats = {copy.key: copy.format for copy in generation.stores}
        for copy in generation.stores:
            self._copy_names.setdefault(copy.sha256, []).append(copy.name)
        for root in (reuse_root, previous_root):
            try:
                value = json.loads((root / PULL_RECORD).read_bytes())
                for row in value.get("stores", []):
                    if (isinstance(row, dict) and isinstance(row.get("name"), str)
                            and isinstance(row.get("sha256"), str) and _SHA.fullmatch(row["sha256"])):
                        self._copy_names.setdefault(row["sha256"], []).append(row["name"])
            except (OSError, ValueError, AttributeError):
                pass

    def _local(self, key: str) -> bytes | None:
        parts = key.split("/")
        if len(parts) == 3 and parts[:2] == ["copies", "sha256"] and _SHA.fullmatch(parts[2]):
            sha = parts[2]
            cache_key = f"{OBJECT_CACHE}/{key}"
            for root in (self.reuse_root, self.previous_root):
                if root.is_symlink():
                    continue
                cached_path = _safe_file(root, cache_key)
                if cached_path is not None and cached_path.is_file():
                    try:
                        data = cached_path.read_bytes()
                    except OSError:
                        data = b""
                    if _digest(data) == sha:
                        if self._copy_formats.get(key) == "duckdb-empty":
                            copy_path = self.download_cache / key
                            copy_path.parent.mkdir(parents=True, exist_ok=True)
                            copy_path.write_bytes(data)
                        return data
                for name in dict.fromkeys(self._copy_names.get(sha, [])):
                    path = _safe_file(root, name)
                    if path is not None and path.is_file():
                        try:
                            data = path.read_bytes()
                        except OSError:
                            continue
                        if _digest(data) == sha:
                            return data
        if len(parts) == 3 and parts[0] == "raw" and _SHA.fullmatch(parts[2]) and parts[1] == parts[2][:2]:
            for root in (self.reuse_root, self.previous_root):
                if root.is_symlink():
                    continue
                path = _safe_file(root, key)
                if path is not None and path.is_file():
                    try:
                        data = path.read_bytes()
                    except OSError:
                        continue
                    if _digest(data) == parts[2]:
                        return data
        return None

    def get(self, key: str) -> tuple[bytes, str] | None:
        cached = self._local(key)
        if cached is not None:
            return cached, "local-sha256-reuse"
        found = self.store.get(key)
        if found is not None and key.startswith(("copies/sha256/", "raw/")):
            self.downloaded_objects += 1
            if self._copy_formats.get(key) == "duckdb-empty":
                cached_path = self.download_cache / key
                cached_path.parent.mkdir(parents=True, exist_ok=True)
                cached_path.write_bytes(found[0])
        return found

    def list(self, prefix: str = "") -> list[str]:
        return self.store.list(prefix)

    def put(self, key: str, data: bytes) -> None:
        raise PullError("pull 只允许读取远端状态")

    def put_if_absent(self, key: str, data: bytes) -> str:
        raise PullError("pull 只允许读取远端状态")

    def put_if_match(self, key: str, data: bytes, etag: str) -> str:
        raise PullError("pull 只允许读取远端状态")

    def delete_if_match(self, key: str, etag: str) -> None:
        raise PullError("pull 只允许读取远端状态")

    def exists(self, key: str) -> bool:
        return self.store.exists(key)


def data_state(data_dir: str | Path) -> dict[str, Any]:
    """Return safe fields shared by search JSON and MCP version."""
    path = Path(data_dir) / PULL_RECORD
    try:
        value = json.loads(path.read_bytes())
        generation = value["generation"]
        created_at = value["created_at"]
        if type(generation) is not int or generation < 1 or not isinstance(created_at, str):
            raise ValueError
        coverage_data = value.get("vector_coverage")
        coverage = VectorCoverage.model_validate(coverage_data) if coverage_data is not None else None
    except FileNotFoundError:
        return {"data_generation": None, "data_generated_at": None, "data_origin": "本机自建数据"}
    except (OSError, ValueError, KeyError, TypeError):
        return {"data_generation": None, "data_generated_at": None, "data_origin": "代次信息未知"}

    result: dict[str, Any] = {
        "data_generation": generation,
        "data_generated_at": created_at,
        "data_origin": "R2 有效代次副本",
    }
    if coverage is not None and coverage.pending_chunks:
        total = coverage.embedded_chunks + coverage.pending_chunks
        result["vector_coverage_note"] = f"向量覆盖 {coverage.embedded_chunks} / {total}"
    return result


def _install(stage: Path, target: Path, temporary: Path) -> None:
    previous = target.with_name(target.name + ".previous")
    held_previous = temporary / "previous"
    had_target = target.exists()
    had_previous = previous.exists() or previous.is_symlink()
    moved_previous = False
    try:
        if had_target and had_previous:
            os.replace(previous, held_previous)
            moved_previous = True
        if had_target:
            _exchange_directories(stage, target)
            try:
                os.replace(stage, previous)
            except Exception:  # 交换目录后失败须还原原目录，再原样抛出。
                _exchange_directories(stage, target)
                raise
        else:
            os.replace(stage, target)
    except Exception:  # 安装失败须还原 previous 目录，再原样抛出。
        if moved_previous and (held_previous.exists() or held_previous.is_symlink()):
            os.replace(held_previous, previous)
        raise


def _exchange_directories(left: Path, right: Path) -> None:
    """Atomically exchange two existing directories on macOS and Linux."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = getattr(libc, "renamex_np", None)
        if function is None:
            raise OSError("atomic directory exchange is unavailable")
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = function(os.fsencode(left), os.fsencode(right), 2)  # RENAME_SWAP
    elif sys.platform.startswith("linux"):
        function = getattr(libc, "renameat2", None)
        if function is None:
            raise OSError("atomic directory exchange is unavailable")
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = function(-100, os.fsencode(left), -100, os.fsencode(right), 2)  # AT_FDCWD, RENAME_EXCHANGE
    else:
        raise OSError("atomic directory exchange is unavailable on this platform")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def pull(
    data_dir: str | Path | None = None,
    generation: int | None = None,
    *,
    store: StateObjectStore | None = None,
) -> dict[str, Any]:
    """Restore and atomically install a verified generation using read-only GETs."""
    if generation is not None and (type(generation) is not int or generation < 1):
        raise ValueError("代次号必须为正整数")
    try:
        config = load_storage_config(data_dir=data_dir)
        target = config.data_dir
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise PullError("本机数据目录不是可切换的目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        with scheduling.workspace_lock(target):
            remote = store or build_state_store(config)
            listing = StateManager(remote, target).list()
            if not listing:
                raise PullError("远端没有可用代次")
            selected = next((row for row in listing if generation is None or row.generation == generation), None)
            if selected is None:
                raise PullError("指定代次不在当前有效历史中")

            with tempfile.TemporaryDirectory(
                prefix=f".{target.name}.pull-", dir=target.parent, ignore_cleanup_errors=True,
            ) as directory:
                temporary = Path(directory)
                stage = temporary / "data"
                cached_store = _ReadThroughStore(
                    remote, target, target.with_name(target.name + ".previous"), selected,
                    temporary / "download-cache",
                )
                manager = StateManager(cached_store, target)
                report = manager.restore(stage, selected.generation, raw="local")
                download_cache = temporary / "download-cache"
                if download_cache.exists():
                    for path in download_cache.rglob("*"):
                        if path.is_file():
                            relative = path.relative_to(download_cache)
                            cached_path = stage / OBJECT_CACHE / relative
                            cached_path.parent.mkdir(parents=True, exist_ok=True)
                            os.replace(path, cached_path)
                # Recheck the effective pointer before publishing the local directory.
                current = StateManager(remote, target).list()
                if not any(row.generation == selected.generation for row in current):
                    raise PullError("拉取期间指定代次已不再有效，请重试")
                if generation is None and (not current or current[0].generation != selected.generation):
                    raise PullError("拉取期间远端代次已更新，请重试")

                pulled_at = datetime.now(UTC)
                record = {
                    "generation": selected.generation,
                    "created_at": selected.created_at.isoformat(),
                    "pulled_at": pulled_at.isoformat(),
                    "writer": selected.writer.model_dump(mode="json"),
                    "last_successful_runs": {
                        source_id: run.model_dump(mode="json")
                        for source_id, run in selected.last_successful_runs.items()
                    },
                    "vector_coverage": (selected.vector_coverage.model_dump(mode="json")
                                        if selected.vector_coverage is not None else None),
                    "stores": [{"name": copy.name, "sha256": copy.sha256} for copy in selected.stores],
                }
                (stage / PULL_RECORD).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
                (stage / "state.lock").touch()
                _install(stage, target, temporary)

            return {
                "generation": selected.generation,
                "created_at": selected.created_at.isoformat(),
                "pulled_at": pulled_at.isoformat(),
                "copies": len(selected.stores),
                "raw_objects": report["raw_objects"],
                "downloaded_objects": cached_store.downloaded_objects,
                "verified": True,
            }
    except PullError:
        raise
    except Exception:  # noqa: BLE001 -- 拉取边界兜住任意 SDK 或校验失败并保全原目录。
        raise PullError("拉取或校验失败，原数据目录保持不变") from None
