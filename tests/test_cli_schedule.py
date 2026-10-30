"""CLI 全程使用假抓取依赖与假的 launchctl。"""

import json
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli
from datumoj.rikolto.core import schedule
from datumoj.rikolto.testing.fakes import FakeCrawler, FakeHttpFetcher, FakeManifest, FakeRawStore, FakeRegistry
from datumoj.rikolto.testing.samples import T0, make_spec


@pytest.fixture
def offline(tmp_path, monkeypatch):
    specs = [make_spec("jp-hourly", schedule="hourly"), make_spec("jp-manual", schedule="manual")]
    calls, builds = [], []
    fetcher = FakeHttpFetcher({})

    def run_crawl(spec, crawler, deps, *, limit, dry_run):
        assert isinstance(crawler, FakeCrawler)
        assert deps.fetcher is fetcher and limit is None and dry_run is False
        calls.append(spec.id)
        return SimpleNamespace(new=1, unchanged=2, failures={}, stop_reason=None)

    deps = SimpleNamespace(
        registry=FakeRegistry(specs), require=lambda _: None,
        crawler_classes={"fake": FakeCrawler}, fetcher_for=lambda _: fetcher,
        raw=FakeRawStore(), manifest=FakeManifest(), crawl_deps_type=SimpleNamespace,
        run_crawl=run_crawl,
    )

    def build(data_dir):
        builds.append(Path(data_dir))
        return deps

    monkeypatch.setattr(cli.wiring, "build", build)
    # Fixed clock is injected via the same factory that a management page can use.
    scheduler_type = schedule.Scheduler
    monkeypatch.setattr(cli.scheduling, "Scheduler", lambda *a, **kw: scheduler_type(*a, **kw, now=lambda: T0))
    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(tmp_path))

    def no_real_launchctl(*a, **kw):
        pytest.fail("测试不得执行真实 launchctl")

    monkeypatch.setattr(schedule.subprocess, "run", no_real_launchctl)
    return CliRunner(), calls, builds, deps


def invoke(runner, *args, code=0):
    result = runner.invoke(cli.app, ["schedule", *args])
    assert result.exit_code == code, result.output
    return result


def test_default_closed_and_master_source_workflow(offline, tmp_path):
    runner, calls, _, _ = offline
    state = json.loads(invoke(runner, "status").output)
    assert state["enabled"] is False
    assert len(state["sources"]) == 2
    assert all(row["enabled"] is False and row["running"] is False for row in state["sources"])
    assert state["sources"][0]["schedule"] == "hourly"
    assert state["sources"][0]["last_run"] is None
    assert state["sources"][0]["next_due"] == T0.isoformat()
    assert "总开关关闭" in invoke(runner, "tick").output
    assert "总开关关闭" in invoke(runner, "run", "jp-hourly", code=1).output
    invoke(runner, "enable", "jp-hourly")
    assert "总开关关闭" in invoke(runner, "run", "jp-hourly", code=1).output
    invoke(runner, "master", "on")
    records = json.loads(invoke(runner, "tick").output)
    assert records[0]["new"] == 1 and records[0]["unchanged"] == 2
    assert records[1]["reason"] == "来源开关关闭"
    assert "尚未到期" in invoke(runner, "tick").output
    invoke(runner, "run", "jp-hourly")
    invoke(runner, "enable", "jp-manual")
    assert "不自动运行" in invoke(runner, "tick").output
    invoke(runner, "run", "jp-manual")
    assert calls == ["jp-hourly", "jp-hourly", "jp-manual"]
    status = json.loads(invoke(runner, "status").output)
    assert status["sources"][0]["last_run"]["exit_status"] == 0
    invoke(runner, "disable", "jp-hourly")
    assert "来源开关关闭" in invoke(runner, "run", "jp-hourly", code=1).output
    invoke(runner, "master", "off")
    assert schedule.read_control(tmp_path)["enabled"] is False


def test_all_only_changes_source_switches(offline, tmp_path):
    runner, calls, _, _ = offline
    invoke(runner, "enable", "--all")
    state = schedule.read_control(tmp_path)
    assert state["enabled"] is False
    assert all(row["enabled"] for row in state["sources"].values())
    invoke(runner, "tick")
    assert not calls
    invoke(runner, "master", "on")
    invoke(runner, "disable", "--all")
    state = schedule.read_control(tmp_path)
    assert state["enabled"] is True
    assert all(row["enabled"] is False for row in state["sources"].values())


@pytest.mark.parametrize("args", [("enable",), ("disable",), ("enable", "jp-hourly", "--all"),
                                  ("disable", "unknown"), ("master", "yes"), ("run", "unknown")])
def test_bad_arguments_report_clear_error(offline, args):
    runner, calls, _, _ = offline
    assert "参数错误" in invoke(runner, *args, code=2).output
    assert not calls


def test_bad_control_never_runs(offline, tmp_path):
    runner, calls, _, _ = offline
    (tmp_path / "control.json").write_text('{"enabled":"on"}')
    for args in [("status",), ("tick",), ("run", "jp-hourly"), ("master", "on")]:
        assert "control.json" in invoke(runner, *args, code=2).output
    assert not calls


def test_data_dir_option_overrides_environment(offline, tmp_path):
    runner, _, builds, _ = offline
    explicit = tmp_path / "explicit"
    result = runner.invoke(cli.app, ["--data-dir", str(explicit), "schedule", "master", "on"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli.app, ["--data-dir", str(explicit), "schedule", "enable", "--all"])
    assert result.exit_code == 0 and builds[-1] == explicit
    assert schedule.read_control(explicit)["enabled"] is True
    assert not (tmp_path / "control.json").exists()
    group_dir = tmp_path / "group"
    invoke(runner, "--data-dir", str(group_dir), "master", "on")
    assert schedule.read_control(group_dir)["enabled"] is True
    # CliRunner invocations must not leak the previous --data-dir.
    invoke(runner, "master", "off")
    assert (tmp_path / "control.json").exists()


def test_data_dir_defaults_to_local_data(offline, tmp_path, monkeypatch):
    runner, _, _, _ = offline
    monkeypatch.delenv("DATUMOJ_DATA_DIR")
    monkeypatch.chdir(tmp_path)
    invoke(runner, "master", "on")
    assert schedule.read_control(tmp_path / "data")["enabled"] is True
    assert not (tmp_path / "control.json").exists()


def test_fetch_failures_propagate_exit_status_and_saved_result(offline):
    runner, _, _, deps = offline
    deps.run_crawl = lambda *a, **kw: SimpleNamespace(
        new=0, unchanged=0, failures={"network_error": 2}, stop_reason="max_files",
    )
    invoke(runner, "master", "on")
    invoke(runner, "enable", "--all")
    result = invoke(runner, "tick", code=1)
    assert json.loads(result.output)[0]["failures"] == {"network_error": 2}
    invoke(runner, "run", "jp-manual", code=1)
    result = json.loads(invoke(runner, "status").output)
    assert result["sources"][1]["last_run"]["stop_reason"] == "max_files"


def test_fetcher_closed_even_when_run_crawl_raises(offline, monkeypatch):
    runner, _, _, deps = offline
    closed = []
    monkeypatch.setattr(deps, "fetcher_for", lambda _: SimpleNamespace(close=lambda: closed.append(True)))

    def fail(*a, **kw):
        raise OSError("disk full")

    deps.run_crawl = fail
    invoke(runner, "master", "on")
    invoke(runner, "enable", "--all")
    record = json.loads(invoke(runner, "run", "jp-hourly", code=1).output)
    assert closed == [True]
    assert record["failures"] == {"OSError": 1}


def test_launchd_cli_show_install_uninstall_use_injected_functions(offline, tmp_path, monkeypatch):
    runner, _, builds, _ = offline
    monkeypatch.setenv("UV", "/custom/uv")
    plist = plistlib.loads(invoke(runner, "launchd", "show").output.encode())
    assert plist["Label"] == "com.datumoj.rikolto.schedule"
    assert plist["ProgramArguments"][0] == "/custom/uv"
    assert plist["ProgramArguments"][-4:] == ["--data-dir", str(tmp_path), "schedule", "tick"]
    assert plist["WorkingDirectory"] == str(Path(cli.__file__).resolve().parents[3])
    assert not builds and not (tmp_path / "logs").exists()
    path = tmp_path / "LaunchAgents" / "injected.plist"
    calls = []
    install, uninstall = schedule.install_launchd, schedule.uninstall_launchd

    def fake_launchctl(args, **kw):
        assert kw == {"check": True} and path.exists()
        calls.append(args)

    monkeypatch.setattr(cli.scheduling, "install_launchd", lambda root, data: install(
        root, data, install_path=path, launchctl=fake_launchctl,
    ))
    monkeypatch.setattr(cli.scheduling, "uninstall_launchd", lambda: uninstall(
        install_path=path, launchctl=fake_launchctl,
    ))
    invoke(runner, "launchd", "install")
    assert path.exists()
    invoke(runner, "launchd", "uninstall")
    assert not path.exists()
    assert calls == [["launchctl", "load", str(path)], ["launchctl", "unload", str(path)]]
