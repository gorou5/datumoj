"""本机抓取开关、串行调度和 launchd；抓取执行函数由入口注入。"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import local
from typing import Any

from datumoj.rikolto.core.contracts import Manifest, SourceSpec
from datumoj.rikolto.core.lease import LeaseManager, remote_configured
from datumoj.rikolto.core.monitor import evaluate_alerts

try:
    import fcntl
except ImportError:
    import errno
    import msvcrt
    import time
    fcntl = None

_WINDOWS_LOCK_OFFSET = 1 << 20
_WORKSPACE_LOCKS = local()

# Each entry is (interval, display label); manual schedules have no interval.
SCHEDULES: dict[str, tuple[timedelta | None, str]] = {
    "hourly": (timedelta(hours=1), "每小时"),
    "daily": (timedelta(days=1), "每天"),
    "weekly": (timedelta(days=7), "每周"),
    "monthly": (timedelta(days=30), "每 30 天"),
    "manual": (None, "手动"),
}


class ReadOnlyReplicaError(ValueError):
    """A pulled generation cannot be used as a local write workspace."""


def is_pulled_replica(data_dir: str | Path) -> bool:
    marker = Path(data_dir) / "state-pull.json"
    return marker.exists() or marker.is_symlink()


def ensure_writable(data_dir: str | Path) -> None:
    if is_pulled_replica(data_dir):
        raise ReadOnlyReplicaError(
            "这是 rikolto pull 创建的只读副本；本版本不支持本机写回。"
            "写入需要有效租约和明确的 --force-local 选项；本版本尚不支持本机写回。"
        )


def utcnow() -> datetime:
    return datetime.now(UTC)


def _validate_control(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or type(value.get("enabled", False)) is not bool:
        raise ValueError("总开关 enabled 必须是布尔值")
    sources = value.get("sources", {})
    if not isinstance(sources, dict):
        raise TypeError("sources 必须是对象")
    for source_id, state in sources.items():
        if not isinstance(state, dict) or type(state.get("enabled", False)) is not bool:
            raise ValueError(f"sources.{source_id}.enabled 必须是布尔值")
    return {"enabled": value.get("enabled", False), "sources": sources}


def read_control(data_dir: str | Path) -> dict[str, Any]:
    """缺失的总开关/来源开关均关闭；损坏的文件拒绝读取。"""
    path = Path(data_dir) / "control.json"
    try:
        return _validate_control(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return {"enabled": False, "sources": {}}
    except (TypeError, ValueError) as exc:
        raise ValueError(f"开关文件 {path} 无效：{exc}") from exc


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _locked(path: Path, *, blocking: bool = True) -> Iterator[Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        if fcntl is not None:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:
                yield None
                return
            try:
                yield stream
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
            return

        while True:
            stream.seek(_WINDOWS_LOCK_OFFSET)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno != errno.EACCES:
                    raise
                if not blocking:
                    yield None
                    return
                time.sleep(0.05)
            else:
                break
        try:
            yield stream
        finally:
            stream.seek(_WINDOWS_LOCK_OFFSET)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def workspace_lock(data_dir: str | Path) -> Iterator[None]:
    """Serialize local operations with pulls through a reentrant directory lock."""
    root = Path(data_dir).resolve()
    key = hashlib.sha256(os.fsencode(root)).hexdigest()
    if os.name == "nt":
        lock_base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        lock_dir = lock_base / "Datumoj" / "workspace-locks"
    else:
        lock_dir = Path("/tmp") / f"datumoj-workspace-locks-{os.getuid()}"
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        info = lock_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError("workspace lock directory is not private to the current user")
    lock_path = lock_dir / f"{key}.lock"
    if getattr(_WORKSPACE_LOCKS, "pid", None) != os.getpid():
        _WORKSPACE_LOCKS.pid = os.getpid()
        _WORKSPACE_LOCKS.held = set()
    held = _WORKSPACE_LOCKS.held
    lock_key = str(lock_path)
    if lock_key in held:
        yield
        return
    with _locked(lock_path):
        held.add(lock_key)
        try:
            yield
        finally:
            held.remove(lock_key)


def write_control(data_dir: str | Path, control: dict[str, Any]) -> None:
    """原子替换完整开关；增量更新请用 set_enabled，避免读改写竞争。"""
    checked = _validate_control(control)
    root = Path(data_dir)
    with workspace_lock(root):
        ensure_writable(root)
        with _locked(root / "control.lock"):
            _atomic_write(root / "control.json", json.dumps(checked, ensure_ascii=False).encode())


def set_enabled(
    data_dir: str | Path, enabled: bool, source_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """source_ids=None 更新总开关，否则仅更新指定来源，不改总开关。"""
    if type(enabled) is not bool:
        raise ValueError("enabled 必须是布尔值")
    root = Path(data_dir)
    with workspace_lock(root):
        ensure_writable(root)
        with _locked(root / "control.lock"):
            control = read_control(root)
            if source_ids is None:
                control["enabled"] = enabled
            else:
                for source_id in source_ids:
                    control["sources"][source_id] = {"enabled": enabled}
            _atomic_write(root / "control.json", json.dumps(control, ensure_ascii=False).encode())
        return control


def next_due(schedule: str | None, last_start: datetime | None, now: datetime) -> datetime | None:
    """首轮立即到期；monthly 固定 30 天；manual/空永不到期。"""
    if schedule in (None, ""):
        return None
    try:
        interval = SCHEDULES[schedule][0]
    except KeyError:
        raise ValueError(f"不支持的调度：{schedule!r}") from None
    if interval is None:
        return None
    return last_start + interval if last_start is not None else now


class Scheduler:
    def __init__(
        self, data_dir: str | Path, sources: Iterable[SourceSpec], execute: Callable[[SourceSpec], Any],
        *, now: Callable[[], datetime] = utcnow, manifest: Manifest | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.sources = {spec.id: spec for spec in sources}
        self.execute = execute
        self.now = now
        self.manifest = manifest

    def _lock_path(self, source_id: str) -> Path:
        # SourceSpec validates IDs; lookup also rejects arbitrary path components from callers.
        spec = self.sources[source_id]
        return self.data_dir / "locks" / f"{spec.id}.lock"

    def _read_history(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        path = self.data_dir / "runs.jsonl"
        if not path.exists():
            return [], []
        history: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        with _locked(self.data_dir / "runs.lock"):
            for number, raw_line in enumerate(path.read_bytes().splitlines(), start=1):
                try:
                    row = json.loads(raw_line.decode("utf-8"))
                except UnicodeError:
                    warnings.append({"line": number, "reason": "不是有效的 UTF-8"})
                    continue
                except json.JSONDecodeError as exc:
                    warnings.append({"line": number, "reason": f"不是合法 JSON：{exc.msg}"})
                    continue
                reason = self._invalid_run_reason(row)
                if reason is not None:
                    warnings.append({"line": number, "reason": reason})
                    continue
                history.append(row)
        return history, warnings

    @staticmethod
    def _invalid_run_reason(row: Any) -> str | None:
        if not isinstance(row, dict):
            return "记录必须是 JSON 对象"
        required = {"source_id", "started_at", "status", "exit_status"}
        missing = sorted(required - row.keys())
        if missing:
            return f"缺少必填字段：{', '.join(missing)}"
        if not isinstance(row["source_id"], str) or not row["source_id"]:
            return "source_id 必须是非空字符串"
        if not isinstance(row["started_at"], str):
            return "started_at 必须是字符串"
        try:
            datetime.fromisoformat(row["started_at"])
        except ValueError:
            return "started_at 不是有效的 ISO 日期时间"
        if not isinstance(row["status"], str) or type(row["exit_status"]) is not int:
            return "status 或 exit_status 类型无效"

        has_run_id = "run_id" in row
        has_state = "state" in row
        if has_run_id != has_state:
            return "run_id 和 state 必须同时存在"
        if has_run_id:
            if not isinstance(row["run_id"], str) or not row["run_id"]:
                return "run_id 必须是非空字符串"
            if not isinstance(row["state"], str) or row["state"] not in {"running", "final"}:
                return "state 必须是 running 或 final"
            if row["state"] == "final":
                new_required = {"ended_at", "new", "unchanged", "failures", "stop_reason"}
                missing = sorted(new_required - row.keys())
                if missing:
                    return f"结束记录缺少必填字段：{', '.join(missing)}"
        return None

    def _history(self) -> list[dict[str, Any]]:
        return self._read_history()[0]

    @staticmethod
    def _logical_history(history: list[dict[str, Any]], locked_sources: dict[str, bool]) -> list[dict[str, Any]]:
        logical: list[dict[str, Any]] = []
        run_positions: dict[tuple[str, str], int] = {}
        for row in history:
            if "run_id" not in row:
                logical.append({**row, "state": "final"})
                continue
            key = (row["source_id"], row["run_id"])
            if key in run_positions:
                position = run_positions[key]
                logical[position] = row
            else:
                run_positions[key] = len(logical)
                logical.append(row)

        for index, row in enumerate(logical):
            if row.get("state") == "running":
                if locked_sources.get(row["source_id"], False):
                    continue
                logical[index] = {
                    **row, "state": "interrupted", "status": "interrupted", "exit_status": 1,
                    "stop_reason": "运行锁已释放，但没有结束记录",
                }
        return logical

    def _append(self, record: dict[str, Any]) -> None:
        with (
            _locked(self.data_dir / "runs.lock"),
            (self.data_dir / "runs.jsonl").open("a+b") as stream,
        ):
            stream.seek(0, os.SEEK_END)
            if stream.tell():
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    stream.seek(0, os.SEEK_END)
                    stream.write(b"\n")
            stream.seek(0, os.SEEK_END)
            stream.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())

    def _last(self, source_id: str) -> dict[str, Any] | None:
        return self._last_attempt(self._history(), source_id)

    def append_run(self, record: dict[str, Any]) -> None:
        """Append a validated ledger row for an externally coordinated run."""
        reason = self._invalid_run_reason(record)
        if reason is not None:
            raise ValueError(reason)
        if record["source_id"] not in self.sources:
            raise ValueError("运行记录的来源不在注册表中")
        with workspace_lock(self.data_dir):
            ensure_writable(self.data_dir)
            self._append(record)

    @staticmethod
    def _last_attempt(history: list[dict[str, Any]], source_id: str) -> dict[str, Any] | None:
        return next((row for row in reversed(history) if row["source_id"] == source_id
                     and row.get("reason_code") != "write_lease_unavailable"), None)

    def due(self) -> list[str]:
        """Enabled, due source IDs using the same ledger and next_due as tick."""
        control = read_control(self.data_dir)
        history = self._history()
        now = self.now()
        result = []
        for spec in self.sources.values():
            if not control["enabled"] or not control["sources"].get(spec.id, {}).get("enabled", False):
                continue
            last = self._last_attempt(history, spec.id)
            due = next_due(spec.schedule, datetime.fromisoformat(last["started_at"]) if last else None, now)
            if due is not None and due <= now:
                result.append(spec.id)
        return sorted(result)

    def _remote_lease(self) -> LeaseManager:
        from datumoj.rikolto.core.state_s3 import S3StateObjectStore
        from datumoj.rikolto.core.storage import load_storage_config

        return LeaseManager(S3StateObjectStore(load_storage_config(data_dir=self.data_dir)), now=self.now)

    def status(self) -> dict[str, Any]:
        control = read_control(self.data_dir)
        now = self.now()
        history, ledger_warnings = self._read_history()
        locked_sources: dict[str, bool] = {}
        for spec in self.sources.values():
            with _locked(self._lock_path(spec.id), blocking=False) as lock:
                locked_sources[spec.id] = lock is None
        history = self._logical_history(history, locked_sources)
        rows = []
        for spec in self.sources.values():
            last = self._last_attempt(history, spec.id)
            due = next_due(spec.schedule, datetime.fromisoformat(last["started_at"]) if last else None, now)
            running = locked_sources[spec.id]
            enabled = control["sources"].get(spec.id, {}).get("enabled", False)
            effective_enabled = control["enabled"] and enabled
            alerts = [
                alert.model_dump(mode="json")
                for alert in evaluate_alerts(spec, history, self.manifest, now, enabled=effective_enabled)
            ]
            rows.append({
                "source_id": spec.id, "enabled": enabled,
                "effective_enabled": effective_enabled,
                "schedule": spec.schedule, "last_run": last,
                "next_due": due.isoformat() if due else None, "running": running, "alerts": alerts,
            })
        return {
            "enabled": control["enabled"], "sources": rows,
            "alert_count": sum(len(row["alerts"]) for row in rows),
            "ledger_warnings": ledger_warnings,
        }

    def _run(self, source_id: str, *, scheduled: bool) -> dict[str, Any]:
        if source_id not in self.sources:
            raise ValueError(f"注册表里没有来源 {source_id!r}")
        spec = self.sources[source_id]

        def skip(reason: str) -> dict[str, Any]:
            return {"source_id": source_id, "status": "skipped", "reason": reason}

        with ExitStack() as stack:
            lock = stack.enter_context(_locked(self._lock_path(source_id), blocking=False))
            if lock is None:
                return skip("正在运行")
            # 不要删除锁文件，否则等待者可能锁住不同文件。
            # 进程退出时内核会释放锁，旧 PID 元数据可直接覆盖。
            control = read_control(self.data_dir)
            if not control["enabled"]:
                return skip("总开关关闭")
            if not control["sources"].get(source_id, {}).get("enabled", False):
                return skip("来源开关关闭")
            started = self.now()
            if scheduled:
                last = self._last(source_id)
                due = next_due(spec.schedule, datetime.fromisoformat(last["started_at"]) if last else None, started)
                if due is None:
                    return skip("不自动运行")
                if due > started:
                    return skip("尚未到期")
            check_lease = None
            remote = remote_configured()
            if remote:
                try:
                    manager = self._remote_lease()
                    lease = manager.acquire(f"local/{uuid.uuid4().hex}")

                    def release() -> None:
                        try:
                            manager.release(lease.holder, lease.token)
                        except Exception:
                            pass  # Never delete a successor's lease or expose SDK details.

                    stack.callback(release)
                    check_lease = stack.enter_context(manager.maintain(lease))
                except Exception:
                    result = {**skip("远端写入租约不可用，本轮跳过"),
                              "reason_code": "write_lease_unavailable", "exit_status": 0,
                              "started_at": started.isoformat()}
                    self._append(result)
                    return result
            lock.seek(0)
            lock.truncate()
            json.dump({"pid": os.getpid(), "started_at": started.isoformat()}, lock)
            lock.flush()
            record = {
                "source_id": source_id, "started_at": started.isoformat(),
                "new": None, "unchanged": None, "failures": {}, "stop_reason": None,
                "exit_status": 0, "status": "running", "ended_at": None,
                "run_id": str(uuid.uuid4()), "state": "running",
            }
            self._append(record)
            try:
                if check_lease:
                    check_lease()
                report = self.execute(spec)
                if check_lease:
                    check_lease()
                record.update(new=report.new, unchanged=report.unchanged, failures=report.failures,
                              stop_reason=report.stop_reason, exit_status=1 if report.failures else 0)
            except Exception as exc:
                reason = "远端租约或运行失败，本轮停止" if remote else str(exc)
                record.update(failures={type(exc).__name__: 1}, stop_reason=reason, exit_status=1)
            record["ended_at"] = self.now().isoformat()
            record["status"] = "completed"
            record["state"] = "final"
            self._append(record)
            return record

    def tick(self) -> list[dict[str, Any]]:
        """逐个运行；拿到来源锁之后重新核对开关和到期时间。"""
        with workspace_lock(self.data_dir):
            ensure_writable(self.data_dir)
            return [self._run(source_id, scheduled=True) for source_id in self.sources]

    def run_now(self, source_id: str) -> dict[str, Any]:
        with workspace_lock(self.data_dir):
            ensure_writable(self.data_dir)
            return self._run(source_id, scheduled=False)


LABEL = "com.datumoj.rikolto.schedule"
LEGACY_LABEL = "com.datumoj.schedule"


def launchd_plist(repo_root: str | Path, data_dir: str | Path, *, uv: str | None = None) -> bytes:
    root = Path(repo_root).resolve()
    data = Path(data_dir).resolve()
    executable = uv or os.environ.get("UV") or shutil.which("uv") or "/opt/homebrew/bin/uv"
    # launchd has a minimal PATH; resolve explicit relative executables in the current context.
    executable = shutil.which(executable) or str(Path(executable).absolute())
    log = str(data / "logs" / "schedule.log")
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [executable, "run", "--frozen", "rikolto", "--data-dir", str(data), "schedule", "tick"],
        "WorkingDirectory": str(root), "StartInterval": 300,
        "StandardOutPath": log, "StandardErrorPath": log,
    })


def install_launchd(
    repo_root: str | Path, data_dir: str | Path, *, install_path: Path | None = None,
    launchctl: Callable[..., Any] | None = None, uv: str | None = None,
) -> Path:
    path = install_path if install_path is not None else Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    legacy = path.with_name(f"{LEGACY_LABEL}.plist")
    if legacy.exists():
        (launchctl or subprocess.run)(["launchctl", "unload", str(legacy)], check=True)
        legacy.unlink()
    (Path(data_dir) / "logs").mkdir(parents=True, exist_ok=True)
    _atomic_write(path, launchd_plist(repo_root, data_dir, uv=uv))
    (launchctl or subprocess.run)(["launchctl", "load", str(path)], check=True)
    return path


def uninstall_launchd(
    *, install_path: Path | None = None, launchctl: Callable[..., Any] | None = None,
) -> Path:
    path = install_path if install_path is not None else Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    for candidate in (path.with_name(f"{LEGACY_LABEL}.plist"), path):
        if candidate.exists():
            (launchctl or subprocess.run)(["launchctl", "unload", str(candidate)], check=True)
            candidate.unlink()
    return path
