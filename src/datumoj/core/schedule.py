"""本机抓取开关、串行调度和 launchd；抓取执行函数由入口注入。"""

from __future__ import annotations

import fcntl
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from datumoj.core.contracts import Manifest, SourceSpec
from datumoj.core.monitor import evaluate_alerts


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
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            yield None
            return
        try:
            yield stream
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def write_control(data_dir: str | Path, control: dict[str, Any]) -> None:
    """原子替换完整开关；增量更新请用 set_enabled，避免读改写竞争。"""
    checked = _validate_control(control)
    root = Path(data_dir)
    with _locked(root / "control.lock"):
        _atomic_write(root / "control.json", json.dumps(checked, ensure_ascii=False).encode())


def set_enabled(
    data_dir: str | Path, enabled: bool, source_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """source_ids=None 更新总开关，否则仅更新指定来源，不改总开关。"""
    if type(enabled) is not bool:
        raise ValueError("enabled 必须是布尔值")
    root = Path(data_dir)
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
    if schedule in (None, "", "manual"):
        return None
    intervals = {"hourly": timedelta(hours=1), "daily": timedelta(days=1), "monthly": timedelta(days=30)}
    if schedule not in intervals:
        raise ValueError(f"不支持的调度：{schedule!r}")
    return last_start + intervals[schedule] if last_start is not None else now


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

    def _history(self) -> list[dict[str, Any]]:
        path = self.data_dir / "runs.jsonl"
        if not path.exists():
            return []
        with _locked(self.data_dir / "runs.lock"):
            try:
                return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            except (ValueError, UnicodeError) as exc:
                raise ValueError(f"运行记录 {path} 无效：{exc}") from exc

    def _append(self, record: dict[str, Any]) -> None:
        with (
            _locked(self.data_dir / "runs.lock"),
            (self.data_dir / "runs.jsonl").open("a", encoding="utf-8") as stream,
        ):
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _last(self, source_id: str) -> dict[str, Any] | None:
        return next((row for row in reversed(self._history()) if row["source_id"] == source_id), None)

    def status(self) -> dict[str, Any]:
        control = read_control(self.data_dir)
        now = self.now()
        history = self._history()
        rows = []
        for spec in self.sources.values():
            last = next((row for row in reversed(history) if row["source_id"] == spec.id), None)
            due = next_due(spec.schedule, datetime.fromisoformat(last["started_at"]) if last else None, now)
            with _locked(self._lock_path(spec.id), blocking=False) as lock:
                running = lock is None
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
        return {"enabled": control["enabled"], "sources": rows,
                "alert_count": sum(len(row["alerts"]) for row in rows)}

    def _run(self, source_id: str, *, scheduled: bool) -> dict[str, Any]:
        if source_id not in self.sources:
            raise ValueError(f"注册表里没有来源 {source_id!r}")
        spec = self.sources[source_id]

        def skip(reason: str) -> dict[str, Any]:
            return {"source_id": source_id, "status": "skipped", "reason": reason}

        with _locked(self._lock_path(source_id), blocking=False) as lock:
            if lock is None:
                return skip("正在运行")
            # Never unlink lock files: waiters must share the same inode. A crashed process's
            # flock is released by the kernel, so stale PID metadata can simply be overwritten.
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
            lock.seek(0)
            lock.truncate()
            json.dump({"pid": os.getpid(), "started_at": started.isoformat()}, lock)
            lock.flush()
            record = {
                "source_id": source_id, "started_at": started.isoformat(),
                "new": None, "unchanged": None, "failures": {}, "stop_reason": None,
                "exit_status": 0, "status": "completed",
            }
            try:
                report = self.execute(spec)
                record.update(new=report.new, unchanged=report.unchanged, failures=report.failures,
                              stop_reason=report.stop_reason, exit_status=1 if report.failures else 0)
            except Exception as exc:  # noqa: BLE001 - Persist failures at the crawl boundary, then continue the tick.
                record.update(failures={type(exc).__name__: 1}, stop_reason=str(exc), exit_status=1)
            record["ended_at"] = self.now().isoformat()
            self._append(record)
            return record

    def tick(self) -> list[dict[str, Any]]:
        """逐个运行；拿到来源锁之后重新核对开关和到期时间。"""
        return [self._run(source_id, scheduled=True) for source_id in self.sources]

    def run_now(self, source_id: str) -> dict[str, Any]:
        return self._run(source_id, scheduled=False)


LABEL = "com.datumoj.schedule"


def launchd_plist(repo_root: str | Path, data_dir: str | Path, *, uv: str | None = None) -> bytes:
    root = Path(repo_root).resolve()
    data = Path(data_dir).resolve()
    executable = uv or os.environ.get("UV") or shutil.which("uv") or "/opt/homebrew/bin/uv"
    # launchd has a minimal PATH; resolve explicit relative executables in the current context.
    executable = shutil.which(executable) or str(Path(executable).absolute())
    log = str(data / "logs" / "schedule.log")
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [executable, "run", "--frozen", "datumoj", "--data-dir", str(data), "schedule", "tick"],
        "WorkingDirectory": str(root), "StartInterval": 300,
        "StandardOutPath": log, "StandardErrorPath": log,
    })


def install_launchd(
    repo_root: str | Path, data_dir: str | Path, *, install_path: Path | None = None,
    launchctl: Callable[..., Any] | None = None, uv: str | None = None,
) -> Path:
    path = install_path if install_path is not None else Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    (Path(data_dir) / "logs").mkdir(parents=True, exist_ok=True)
    _atomic_write(path, launchd_plist(repo_root, data_dir, uv=uv))
    (launchctl or subprocess.run)(["launchctl", "load", str(path)], check=True)
    return path


def uninstall_launchd(
    *, install_path: Path | None = None, launchctl: Callable[..., Any] | None = None,
) -> Path:
    path = install_path if install_path is not None else Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if path.exists():
        (launchctl or subprocess.run)(["launchctl", "unload", str(path)], check=True)
        path.unlink()
    return path
