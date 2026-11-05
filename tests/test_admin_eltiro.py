"""Admin page Eltiro section (WISTERIA 601): identifiers and counts, never demand text."""

from __future__ import annotations

from types import SimpleNamespace

from starlette.testclient import TestClient

from datumoj.rikolto.admin import create_app
from datumoj.rikolto.admin.web import _eltiro_section
from datumoj.rikolto.core.schedule import Scheduler
from datumoj.rikolto.testing.fakes import FakeManifest, FakeRawStore, FakeRegistry
from datumoj.rikolto.testing.samples import T0, make_spec

SERIES = {"source_id": "intl-wdi", "table_key": "gdp", "indicator_code": "NY.GDP.MKTP.CD"}
OVERVIEW = {
    "schema": "eltiro.overview/v1",
    "demands": [{"demand_id": "example-demand", "demand_version": 2, "consumer": "example-consumer",
                 "registered_at": "2026-10-28T00:00:00+00:00"}],
    "runs": [{"run_id": "run-" + "b" * 32, "demand_id": "example-demand", "demand_version": 2, "generation": 4,
              "started_at": "2026-10-29T00:00:00+00:00", "finished_at": "2026-10-29T00:01:00+00:00",
              "row_count": 12, "gap_count": 1}],
    "pending": [{"demand_id": "example-demand", "demand_version": 2, "indicator_key": "gdp", "generation": 4,
                 "tier": "vector", "confidence": 0.7, "series": SERIES}],
    "pending_count": 3,
    "alerts": [{"alert_id": "f" * 64, "consumer": "example-consumer", "demand_id": "example-demand",
                "demand_version": 2, "indicator_key": "gdp", "kind": "definition_changed", "generation": 4,
                "series": SERIES}],
    "alert_count": 1,
    "alerts_by_kind": {"definition_changed": 1},
    "gaps": {"generation": 4, "file": "eltiro/reports/gaps-g4.json", "active_demands": 1, "indicator_count": 1,
             "by_kind": {"below_min_sources": 1},
             "indicators": [{"consumer": "example-consumer", "demand_id": "example-demand", "demand_version": 2,
                             "indicator_key": "gdp", "kinds": ["below_min_sources"]}],
             "sources": [{"source_id": "intl-imf", "indicators": 1, "reasons": ["preferred_unregistered"]}]},
    "errors": [],
}


def _client(tmp_path, **kwargs):
    specs = [make_spec("jp-one")]
    deps = SimpleNamespace(registry=FakeRegistry(specs), manifest=FakeManifest(), raw=FakeRawStore(tmp_path))
    scheduler = Scheduler(tmp_path, specs, lambda spec: None, now=lambda: T0)
    return TestClient(create_app(tmp_path, deps, scheduler, token="t", **kwargs))


def test_page_without_provider_has_no_eltiro_section(tmp_path):
    page = _client(tmp_path).get("/").text
    assert 'aria-label="Eltiro"' not in page


def test_page_shows_demands_runs_queue_alerts_and_gaps(tmp_path):
    page = _client(tmp_path, eltiro_overview=lambda: OVERVIEW).get("/").text
    assert 'aria-label="Eltiro"' in page
    assert "在用需求（1）" in page
    assert "example-demand v2" in page and "使用方 example-consumer" in page
    assert "run-" + "b" * 32 in page and "第 4 代　12 行，缺口 1" in page
    assert "待确认匹配（3）" in page and "intl-wdi · gdp / NY.GDP.MKTP.CD" in page
    assert "另有 2 项未列出" in page
    assert "提醒（未确认 1）" in page and "口径变化" in page
    assert "第 4 代汇总（eltiro/reports/gaps-g4.json）" in page and "来源数不足 1" in page


def test_missing_state_and_failed_parts_are_explicit(tmp_path):
    assert "未启用" in _client(tmp_path, eltiro_overview=lambda: None).get("/").text
    page = _client(tmp_path, eltiro_overview=lambda: {**OVERVIEW, "errors": ["alerts", "gaps"]}).get("/").text
    assert "以下部分读取失败，显示为空不代表没有：提醒、缺口报告。" in page


def test_provider_failure_keeps_the_crawl_page_and_hides_the_error(tmp_path):
    def broken():
        raise RuntimeError("secret path /home/someone/data")

    response = _client(tmp_path, eltiro_overview=broken).get("/")
    assert response.status_code == 200
    assert "Eltiro 状态读取失败。" in response.text
    assert "secret path" not in response.text
    assert "jp-one" in response.text


def test_provider_is_read_on_every_render(tmp_path):
    calls = []
    client = _client(tmp_path, eltiro_overview=lambda: calls.append(1) or None)
    client.get("/")
    client.get("/")
    assert len(calls) == 2


def test_values_are_escaped():
    hostile = {**OVERVIEW, "demands": [{**OVERVIEW["demands"][0], "consumer": "<script>x</script>"}]}
    html = _eltiro_section(hostile)
    assert "<script>" not in html and "&lt;script&gt;" in html
