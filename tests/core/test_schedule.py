"""调度核心：离线时钟、开关、追加记录和跨进程 flock。"""

import json
import multiprocessing
import os
import plistlib
import subprocess
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from datumoj.rikolto.core.schedule import (
    Scheduler,
    install_launchd,
    launchd_plist,
    next_due,
    read_control,
    set_enabled,
    uninstall_launchd,
    write_control,
)
from datumoj.rikolto.testing.samples import T0, make_spec


@pytest.fixture(autouse=True)
def no_real_launchctl(monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("测试不得执行真实 launchctl")

    monkeypatch.setattr(subprocess, "run", forbidden)


def report(**changes):
    return SimpleNamespace(**({"new": 2, "unchanged": 3, "failures": {}, "stop_reason": None} | changes))


def setup_scheduler(tmp_path, schedules=("hourly",), execute=None):
    specs = [make_spec(f"jp-test{i}", schedule=value) for i, value in enumerate(schedules)]
    calls = []

    def crawl(spec):
        calls.append(spec.id)
        return report()

    clock = [T0]
    scheduler = Scheduler(tmp_path, specs, execute or crawl, now=lambda: clock[0])
    return scheduler, calls, clock


def enable(scheduler):
    set_enabled(scheduler.data_dir, True)
    set_enabled(scheduler.data_dir, True, scheduler.sources)


def test_default_closed_and_switch_combinations(tmp_path):
    scheduler, calls, _ = setup_scheduler(tmp_path)
    assert read_control(tmp_path) == {"enabled": False, "sources": {}}
    assert scheduler.status()["sources"][0]["enabled"] is False
    assert scheduler.tick()[0]["reason"] == "总开关关闭"
    assert scheduler.run_now("jp-test0")["reason"] == "总开关关闭"
    set_enabled(tmp_path, True, ["jp-test0"])
    assert scheduler.tick()[0]["reason"] == "总开关关闭"
    set_enabled(tmp_path, False, ["jp-test0"])
    set_enabled(tmp_path, True)
    assert scheduler.tick()[0]["reason"] == "来源开关关闭"
    assert scheduler.run_now("jp-test0")["reason"] == "来源开关关闭"
    assert not calls and not (tmp_path / "runs.jsonl").exists()
    set_enabled(tmp_path, True, ["jp-test0"])
    assert scheduler.tick()[0]["new"] == 2
    assert calls == ["jp-test0"]
    # An unlisted source stays closed when the master switch is on.
    other = Scheduler(tmp_path, [make_spec("jp-other", schedule="hourly")], lambda _: pytest.fail("closed"))
    assert other.tick()[0]["reason"] == "来源开关关闭"


@pytest.mark.parametrize("bad", ["{", "[]", '{"enabled": "false"}', '{"sources": []}',
                                     '{"sources": {"jp-test": {"enabled": 1}}}', "\ufffd"])
def test_corrupt_control_fails_closed(tmp_path, bad):
    path = tmp_path / "control.json"
    path.write_text(bad)
    scheduler, calls, _ = setup_scheduler(tmp_path)
    with pytest.raises(ValueError, match="control.json"):
        scheduler.tick()
    with pytest.raises(ValueError, match="control.json"):
        set_enabled(tmp_path, True)
    assert not calls and path.read_text() == bad


def test_atomic_control_replace_preserves_original_on_failure(tmp_path, monkeypatch):
    import datumoj.rikolto.core.schedule as module

    write_control(tmp_path, {"enabled": False, "sources": {}})
    previous = (tmp_path / "control.json").read_bytes()

    def fail(*_):
        raise OSError("rename failed")

    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError, match="rename failed"):
        set_enabled(tmp_path, True)
    assert (tmp_path / "control.json").read_bytes() == previous
    assert not list(tmp_path.glob(".control.json.*"))


@pytest.mark.parametrize("value,delta", [("hourly", timedelta(hours=1)), ("daily", timedelta(days=1)),
                                        ("weekly", timedelta(days=7)), ("monthly", timedelta(days=30))])
def test_next_due_uses_start(value, delta):
    assert next_due(value, T0, T0) == T0 + delta
    assert next_due(value, None, T0) == T0


@pytest.mark.parametrize("value", [None, "", "manual"])
def test_manual_never_due(value):
    assert next_due(value, T0, T0) is None
    assert next_due(value, None, T0) is None


def test_unknown_schedule_is_explicit_error():
    with pytest.raises(ValueError, match="不支持"):
        next_due("yearly", None, T0)


def test_tick_due_and_run_now_bypasses_due_with_append_only_records(tmp_path):
    scheduler, calls, clock = setup_scheduler(tmp_path, ("hourly", "manual"))
    enable(scheduler)
    first = scheduler.tick()
    assert first[0]["exit_status"] == 0 and first[1]["reason"] == "不自动运行"
    original = (tmp_path / "runs.jsonl").read_bytes()
    clock[0] += timedelta(minutes=59)
    assert scheduler.tick()[0]["reason"] == "尚未到期"
    clock[0] += timedelta(minutes=1)
    scheduler.tick()
    scheduler.run_now("jp-test0")
    scheduler.run_now("jp-test1")
    assert calls == ["jp-test0", "jp-test0", "jp-test0", "jp-test1"]
    assert (tmp_path / "runs.jsonl").read_bytes().startswith(original)
    rows = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    assert len(rows) == 8
    assert [row["state"] for row in rows] == ["running", "final"] * 4
    assert all(rows[index]["run_id"] == rows[index + 1]["run_id"] for index in range(0, len(rows), 2))
    assert {key: value for key, value in rows[1].items() if key not in {"run_id", "state"}} == {
        "source_id": "jp-test0", "started_at": T0.isoformat(), "ended_at": T0.isoformat(),
        "new": 2, "unchanged": 3, "failures": {}, "stop_reason": None,
        "exit_status": 0, "status": "completed",
    }
    status = scheduler.status()
    assert status["sources"][0]["last_run"] == rows[5]
    assert status["sources"][0]["next_due"] == (T0 + timedelta(hours=2)).isoformat()
    assert status["sources"][1]["next_due"] is None
    assert status["sources"][0]["effective_enabled"] is True


def test_failure_counts_stop_reason_end_time_and_continue(tmp_path):
    def crawl(spec):
        if spec.id == "jp-test0":
            clock[0] += timedelta(minutes=10)
            return report(failures={"network_error": 2, "too_large": 1}, stop_reason="max_files")
        raise OSError("storage failed")

    scheduler, _, clock = setup_scheduler(tmp_path, ("hourly", "daily"), crawl)
    enable(scheduler)
    records = scheduler.tick()
    assert records[0]["failures"] == {"network_error": 2, "too_large": 1}
    assert records[0]["stop_reason"] == "max_files"
    assert records[0]["exit_status"] == records[1]["exit_status"] == 1
    assert records[1]["failures"] == {"OSError": 1}
    assert records[1]["stop_reason"] == "OSError"
    assert records[1]["new"] is None and records[1]["unchanged"] is None
    assert records[0]["ended_at"] == clock[0].isoformat()
    assert scheduler.status()["sources"][0]["next_due"] == (T0 + timedelta(hours=1)).isoformat()


def test_checks_switches_between_serial_sources(tmp_path):
    def crawl(spec):
        assert spec.id == "jp-test0"
        set_enabled(tmp_path, False)
        return report()

    scheduler, _, _ = setup_scheduler(tmp_path, ("hourly", "hourly"), crawl)
    enable(scheduler)
    assert scheduler.tick()[1]["reason"] == "总开关关闭"


def hold_lock(path, ready, release):
    from datumoj.rikolto.core.schedule import _locked

    with _locked(Path(path)) as stream:
        stream.seek(0)
        stream.truncate()
        json.dump({"pid": os.getpid()}, stream)
        stream.flush()
        ready.set()
        release.wait(10)
        os._exit(0)


def test_live_lock_blocks_tick_and_manual_and_crash_releases_stale_lock(tmp_path):
    scheduler, calls, _ = setup_scheduler(tmp_path)
    enable(scheduler)
    path = tmp_path / "locks" / "jp-test0.lock"
    path.parent.mkdir()
    # 用 fork：spawn 的子进程要按模块名重新导入本测试文件，是否可导入取决于运行环境（pytest 的导入方式和 PYTHONPATH）。
    ctx = multiprocessing.get_context("spawn" if os.name == "nt" else "fork")
    ready, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=hold_lock, args=(str(path), ready, release))
    process.start()
    try:
        assert ready.wait(10)
        inode = path.stat().st_ino
        assert scheduler.status()["sources"][0]["running"] is True
        assert scheduler.tick()[0]["reason"] == "正在运行"
        assert scheduler.run_now("jp-test0")["reason"] == "正在运行"
        assert not calls
        release.set()
        process.join(10)
        assert process.exitcode == 0
        assert json.loads(path.read_text())["pid"] == process.pid
        assert scheduler.status()["sources"][0]["running"] is False
        assert scheduler.run_now("jp-test0")["exit_status"] == 0
        assert path.stat().st_ino == inode
        assert json.loads(path.read_text())["pid"] == os.getpid()
    finally:
        if process.is_alive():
            process.terminate()
        process.join(10)


def test_reentrant_scheduler_cannot_run_same_source(tmp_path):
    def crawl(_):
        assert scheduler.run_now("jp-test0")["reason"] == "正在运行"
        assert scheduler.tick()[0]["reason"] == "正在运行"
        return report()

    scheduler, _, _ = setup_scheduler(tmp_path, execute=crawl)
    enable(scheduler)
    assert scheduler.tick()[0]["exit_status"] == 0


def test_unknown_source_cannot_create_lock_outside_data(tmp_path):
    scheduler, _, _ = setup_scheduler(tmp_path)
    with pytest.raises(ValueError, match="没有来源"):
        scheduler.run_now("../../escape")
    assert not (tmp_path / "locks").exists()


def test_launchd_plist_and_injected_install_uninstall(tmp_path):
    repo, data = tmp_path / "repo with spaces", tmp_path / "data with spaces"
    path = tmp_path / "LaunchAgents" / "test.plist"
    calls = []

    def fake_launchctl(args, *, check):
        assert check and path.exists()
        calls.append(args)

    content = launchd_plist(repo, data, uv="/custom/bin/uv")
    plist = plistlib.loads(content)
    assert plist == {
        "Label": "com.datumoj.rikolto.schedule", "StartInterval": 300,
        "WorkingDirectory": str(repo),
        "ProgramArguments": ["/custom/bin/uv", "run", "--frozen", "rikolto", "--data-dir", str(data),
                             "schedule", "tick"],
        "StandardOutPath": str(data / "logs" / "schedule.log"),
        "StandardErrorPath": str(data / "logs" / "schedule.log"),
    }
    install_launchd(repo, data, install_path=path, launchctl=fake_launchctl, uv="/custom/bin/uv")
    assert path.read_bytes() == content and (data / "logs").is_dir()
    uninstall_launchd(install_path=path, launchctl=fake_launchctl)
    assert not path.exists()
    assert calls == [["launchctl", "load", str(path)], ["launchctl", "unload", str(path)]]
    uninstall_launchd(install_path=path, launchctl=fake_launchctl)
    assert len(calls) == 2


def test_failed_unload_keeps_plist(tmp_path):
    path = tmp_path / "test.plist"
    path.write_bytes(b"keep me")

    def fail(args, **_):
        raise subprocess.CalledProcessError(1, args)

    with pytest.raises(subprocess.CalledProcessError):
        uninstall_launchd(install_path=path, launchctl=fail)
    assert path.read_bytes() == b"keep me"


def test_uv_resolution_matches_makefile(tmp_path, monkeypatch):
    import datumoj.rikolto.core.schedule as module

    monkeypatch.setenv("UV", "/override/uv")
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    assert plistlib.loads(launchd_plist(tmp_path, tmp_path))["ProgramArguments"][0] == "/override/uv"
    monkeypatch.delenv("UV")
    monkeypatch.setattr(module.shutil, "which", lambda _: "/path/bin/uv")
    assert plistlib.loads(launchd_plist(tmp_path, tmp_path))["ProgramArguments"][0] == "/path/bin/uv"
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    assert plistlib.loads(launchd_plist(tmp_path, tmp_path))["ProgramArguments"][0] == "/opt/homebrew/bin/uv"


def test_schedule_imports_without_fcntl(tmp_path, monkeypatch):
    if os.name == "nt":
        import datumoj.rikolto.core.schedule as module

        assert module.fcntl is None
        assert module.msvcrt is not None
        with module._locked(tmp_path / "lock") as stream:
            assert stream is not None
        return

    import errno
    import importlib.util
    import sys
    import types
    from pathlib import Path

    locks = set()
    fake = types.ModuleType("msvcrt")
    fake.LK_NBLCK = 1
    fake.LK_UNLCK = 2

    def locking(fd, mode, nbytes):
        stat = os.fstat(fd)
        offset = os.lseek(fd, 0, os.SEEK_CUR)
        key = (stat.st_dev, stat.st_ino, offset, nbytes)
        if mode == fake.LK_NBLCK:
            if key in locks:
                raise OSError(errno.EACCES, "lock is held")
            locks.add(key)
        elif mode == fake.LK_UNLCK:
            locks.remove(key)
        else:
            raise AssertionError("unexpected lock mode")

    fake.locking = locking
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    name = "_datumoj_schedule_no_fcntl_test"
    source = Path(__file__).resolve().parents[2] / "src" / "datumoj" / "rikolto" / "core" / "schedule.py"
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)

    assert module.fcntl is None
    with module._locked(tmp_path / "lock") as stream:
        assert stream is not None
        stream.seek(0)
        stream.write("{}")
        stream.flush()
        with module._locked(tmp_path / "lock", blocking=False) as busy:
            assert busy is None
    assert not locks
