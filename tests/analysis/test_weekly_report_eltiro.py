"""Weekly report Eltiro section (WISTERIA 601)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from typer.testing import CliRunner

from datumoj.eltiro.registry_store import SQLiteDemandRegistry
from datumoj.eltiro.testing.samples import make_demand
from datumoj.rikolto import cli
from datumoj.rikolto.analysis.reports import weekly_report
from datumoj.rikolto.testing.fakes import FakeChangeLog, FakeChunkStore, FakeManifest, FakeRegistry
from datumoj.rikolto.testing.samples import make_spec

SERIES = {"source_id": "intl-wdi", "table_key": "gdp", "indicator_code": None}


def _deps(tmp_path):
    return SimpleNamespace(
        data_dir=tmp_path, registry=FakeRegistry([make_spec("jp-test")]), manifest=FakeManifest(),
        chunks=FakeChunkStore(), change_log=FakeChangeLog(), require=lambda feature: None,
    )


def _run(run_id, finished_at):
    return {"run_id": run_id, "demand_id": "example-demand", "demand_version": 1, "generation": 2,
            "started_at": finished_at, "finished_at": finished_at, "row_count": 5, "gap_count": 0}


OVERVIEW = {
    "schema": "eltiro.overview/v1",
    "demands": [{"demand_id": "example-demand", "demand_version": 1, "consumer": "example-consumer",
                 "registered_at": "2026-10-01T00:00:00+00:00"}],
    "runs": [_run("run-" + "a" * 32, "2026-10-21T08:00:00+00:00"),
             _run("run-" + "b" * 32, "2026-10-12T08:00:00+00:00")],
    "pending": [{"demand_id": "example-demand", "demand_version": 1, "indicator_key": "gdp", "generation": 2,
                 "tier": "vector", "confidence": 0.5, "series": SERIES}],
    "pending_count": 1,
    "alerts": [{"alert_id": "e" * 64, "consumer": "example-consumer", "demand_id": "example-demand",
                "demand_version": 1, "indicator_key": "gdp", "kind": "gap_filled", "generation": 2,
                "series": SERIES}],
    "alert_count": 1, "alerts_by_kind": {"gap_filled": 1},
    "gaps": {"generation": 2, "file": "eltiro/reports/gaps-g2.json", "active_demands": 1, "indicator_count": 1,
             "by_kind": {"missing": 1},
             "indicators": [{"consumer": "example-consumer", "demand_id": "example-demand", "demand_version": 1,
                             "indicator_key": "gdp", "kinds": ["missing"]}],
             "sources": [{"source_id": "intl-imf", "indicators": 1, "reasons": ["preferred_unregistered"]}]},
    "errors": [],
}


def test_default_report_is_unchanged_without_eltiro(tmp_path):
    deps = _deps(tmp_path)
    plain = weekly_report(deps, date(2026, 10, 19), date(2026, 10, 25))
    assert "## Eltiro" not in plain
    with_section = weekly_report(deps, date(2026, 10, 19), date(2026, 10, 25), (), OVERVIEW)
    assert with_section.startswith(plain.rstrip("\n"))


def test_section_lists_week_runs_queue_alerts_and_gaps(tmp_path):
    report = weekly_report(_deps(tmp_path), date(2026, 10, 19), date(2026, 10, 25), (), OVERVIEW)
    section = report.split("## Eltiro", 1)[1]
    assert "### 在用需求（1）" in section
    assert "example\\-demand v1；使用方：example\\-consumer" in section
    assert "### 本周运行（1）" in section
    assert "run\\-" + "a" * 32 in section and "run\\-" + "b" * 32 not in section
    assert "### 待确认匹配（1）" in section and "候选：intl\\-wdi · gdp；第 2 代" in section
    assert "### 提醒（未确认 1）：缺口转为可得 1" in section
    assert "第 2 代汇总（eltiro/reports/gaps\\-g2\\.json）：有缺口的指标 1 个；没有候选 1" in section
    assert "- intl\\-imf：1 个指标；preferred\\_unregistered" in section


def test_missing_state_failed_parts_and_markdown_injection(tmp_path):
    deps = _deps(tmp_path)
    assert "本机没有 Eltiro 数据目录，未启用。" in weekly_report(deps, date(2026, 10, 19), date(2026, 10, 25), (), None)
    hostile = {**OVERVIEW, "errors": ["pending"],
               "demands": [{**OVERVIEW["demands"][0], "consumer": "[x](javascript:run)\n# heading"}]}
    report = weekly_report(deps, date(2026, 10, 19), date(2026, 10, 25), (), hostile)
    assert "以下部分读取失败，显示为空不代表没有：待确认匹配。" in report
    assert "[x](javascript:run)" not in report and "\n# heading" not in report


def test_local_cli_report_includes_eltiro_from_data_dir(tmp_path, monkeypatch):
    deps = _deps(tmp_path)
    (tmp_path / "eltiro").mkdir()
    with SQLiteDemandRegistry(tmp_path / "eltiro" / "demands.sqlite3") as registry:
        registry.register(make_demand())
    monkeypatch.setattr(cli.wiring, "build", lambda _data_dir: deps)
    monkeypatch.setattr(cli.scheduling.Scheduler, "status", lambda _scheduler: {"sources": []})
    target = tmp_path / "weekly.md"

    result = CliRunner().invoke(
        cli.app, ["report", "weekly", "--since", "2026-10-19", "--until", "2026-10-25", "--out", str(target)],
    )

    assert result.exit_code == 0, result.output
    content = target.read_text(encoding="utf-8")
    assert "## Eltiro" in content and "### 在用需求（1）" in content
    assert "Compare annual GDP" not in content and "Nominal GDP" not in content
