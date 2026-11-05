"""Read-only Eltiro overview for the admin page and weekly report (WISTERIA 601)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from datumoj.eltiro.alerts import SQLiteAlertStore
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.overview import MAX_ITEMS, eltiro_overview
from datumoj.eltiro.registry_store import SQLiteDemandRegistry
from datumoj.eltiro.testing.samples import make_demand, make_match, make_series

CONCEPT = "Concept text that must stay private"
PURPOSE = "Purpose text that must stay private"
RUN_A = "run-" + "a" * 32
RUN_B = "run-" + "b" * 32


def _demand(**changes):
    return make_demand(
        purpose=PURPOSE,
        indicators=[{"key": "gdp", "concept": CONCEPT, "unit": "USD"}],
        run={"notify_on": ["binding_stale", "definition_changed", "new_candidate", "gap_filled"]},
        **changes,
    )


def _run(root, run_id, finished_at, **changes):
    directory = root / "eltiro" / "runs" / run_id
    directory.mkdir(parents=True)
    record = {"demand_id": "example-demand", "demand_version": 1, "generation": 3,
              "generation_token": "token", "started_at": finished_at, "finished_at": finished_at,
              "bindings": [], "layout": "long/v1", "run_id": run_id, "row_count": 12, "gap_count": 1,
              **changes}
    (directory / "record.json").write_text(json.dumps(record), encoding="utf-8")


def _gap_report(root, generation, concept=CONCEPT):
    reports = root / "eltiro" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    ref = {"consumer": "example-consumer", "demand_id": "example-demand", "demand_version": 1,
           "indicator_key": "gdp", "min_sources": 2, "bound_sources": ["intl-wdi"],
           "gaps": [{"kind": "below_min_sources", "reason": "fixed"}, {"kind": "pending_confirmation",
                                                                        "reason": "fixed"}]}
    report = {
        "schema": "eltiro.gaps.aggregate/v1", "generation": generation, "active_demands": 1,
        "planning_note": "leads",
        "by_concept": [{"concept": concept, "demands": [{**ref, "possible_sources": []}]}],
        "by_source": [{"source_id": "intl-imf", "demands": [{**ref, "concept": concept,
                                                             "reasons": ["preferred_unregistered"]}]}],
    }
    (reports / f"gaps-g{generation}.json").write_text(json.dumps(report), encoding="utf-8")


def _state(root):
    (root / "eltiro").mkdir()
    with SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as registry:
        registry.register(_demand())
        registry.register(_demand(id="other-demand"), active=False)
    with SQLiteMatchStore(root) as matches:
        matches.put(make_match(confidence=0.6))
        matches.put(make_match(series=make_series(table_key="gdp-confirmed"), confirmation="confirmed"))
        matches.put(make_match(series=make_series(table_key="gdp-rejected"), confirmation="rejected"))
        matches.put(make_match(demand_id="other-demand"))
    with SQLiteAlertStore(root) as alerts:
        demand = _demand()
        alerts._add(demand, "gdp", make_series(), "new_candidate", 3, ("higher_confidence",))
        alerts._add(demand, "gdp", make_series(), "binding_stale", 3, ("time_end", "frequency"))
        acked = alerts.list_alerts()[1].alert_id
        alerts.ack_alert(acked)
    _run(root, RUN_A, "2026-10-20T00:00:00+00:00")
    _run(root, RUN_B, "2026-10-27T00:00:00+00:00", row_count=7)
    (root / "eltiro" / "runs" / (".run-" + "c" * 32)).mkdir()  # Unpublished staging directory.
    _gap_report(root, 1, concept="older concept")
    _gap_report(root, 2)


def _tree(root):
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
    }


def test_missing_state_directory_is_none_and_creates_nothing(tmp_path):
    assert eltiro_overview(tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_empty_state_directory_reads_as_empty_without_creating_state(tmp_path):
    (tmp_path / "eltiro").mkdir()
    overview = eltiro_overview(tmp_path)
    assert overview == {"schema": "eltiro.overview/v1", "demands": [], "runs": [], "pending": [],
                        "pending_count": 0, "alerts": [], "alert_count": 0, "alerts_by_kind": {},
                        "gaps": None, "errors": []}
    assert list((tmp_path / "eltiro").iterdir()) == []


def test_overview_lists_active_demands_runs_queue_alerts_and_latest_gaps(tmp_path):
    _state(tmp_path)
    overview = eltiro_overview(tmp_path)

    assert overview["errors"] == []
    assert [(d["demand_id"], d["demand_version"], d["consumer"]) for d in overview["demands"]] == [
        ("example-demand", 1, "example-consumer"),
    ]
    assert [run["run_id"] for run in overview["runs"]] == [RUN_B, RUN_A]
    assert overview["runs"][0]["row_count"] == 7
    # Confirmed, rejected and inactive-demand candidates are not waiting for this queue.
    assert overview["pending_count"] == 1
    assert overview["pending"] == [{
        "demand_id": "example-demand", "demand_version": 1, "indicator_key": "gdp", "generation": 1,
        "tier": "deterministic", "confidence": 0.6,
        "series": {"source_id": "intl-wdi", "table_key": "gdp", "indicator_code": "NY.GDP.MKTP.CD"},
    }]
    assert overview["alert_count"] == 1
    assert overview["alerts_by_kind"] == {"new_candidate": 1}
    assert overview["alerts"][0]["kind"] == "new_candidate"
    assert overview["gaps"]["generation"] == 2
    assert overview["gaps"]["file"] == "eltiro/reports/gaps-g2.json"
    assert overview["gaps"]["by_kind"] == {"below_min_sources": 1, "pending_confirmation": 1}
    assert overview["gaps"]["indicators"] == [{
        "consumer": "example-consumer", "demand_id": "example-demand", "demand_version": 1,
        "indicator_key": "gdp", "kinds": ["below_min_sources", "pending_confirmation"],
    }]
    assert overview["gaps"]["sources"] == [
        {"source_id": "intl-imf", "indicators": 1, "reasons": ["preferred_unregistered"]},
    ]


def test_overview_never_carries_demand_text_or_match_reasons(tmp_path):
    _state(tmp_path)
    dumped = json.dumps(eltiro_overview(tmp_path), ensure_ascii=False)
    for private in (CONCEPT, PURPOSE, "older concept", "Source concept identifier agrees", "Nominal GDP",
                    "row=1", "ab" * 32):
        assert private not in dumped


def test_overview_is_read_only(tmp_path):
    _state(tmp_path)
    before = _tree(tmp_path)
    eltiro_overview(tmp_path)
    eltiro_overview(tmp_path)
    assert _tree(tmp_path) == before


def test_parts_fail_independently_with_fixed_names(tmp_path):
    _state(tmp_path)
    (tmp_path / "eltiro" / "alerts.sqlite").write_bytes(b"not a database: secret-detail")
    (tmp_path / "eltiro" / "reports" / "gaps-g3.json").write_text('{"schema": "other"}', encoding="utf-8")

    overview = eltiro_overview(tmp_path)

    assert overview["errors"] == ["alerts", "gaps"]
    assert overview["alerts"] == [] and overview["gaps"] is None
    assert overview["pending_count"] == 1 and len(overview["runs"]) == 2
    assert "secret-detail" not in json.dumps(overview)


def test_symlinked_state_is_refused(tmp_path):
    _state(tmp_path)
    outside = tmp_path / "outside.sqlite"
    os.replace(tmp_path / "eltiro" / "matches.sqlite", outside)
    (tmp_path / "eltiro" / "matches.sqlite").symlink_to(outside)
    real_run = tmp_path / "elsewhere"
    os.replace(tmp_path / "eltiro" / "runs", real_run)
    (tmp_path / "eltiro" / "runs").symlink_to(real_run, target_is_directory=True)

    overview = eltiro_overview(tmp_path)

    assert overview["errors"] == ["runs", "pending"]
    assert overview["pending"] == [] and overview["runs"] == []


def test_lists_are_bounded_but_counts_are_complete(tmp_path):
    (tmp_path / "eltiro").mkdir()
    with SQLiteDemandRegistry(tmp_path / "eltiro" / "demands.sqlite3") as registry:
        registry.register(_demand())
    with SQLiteMatchStore(tmp_path) as matches:
        for index in range(MAX_ITEMS + 5):
            matches.put(make_match(series=make_series(table_key=f"t{index:03d}")))
    overview = eltiro_overview(tmp_path)
    assert overview["pending_count"] == MAX_ITEMS + 5
    assert len(overview["pending"]) == MAX_ITEMS


def test_unreadable_demands_make_the_queue_unknown_not_empty(tmp_path):
    _state(tmp_path)
    (tmp_path / "eltiro" / "demands.sqlite3").write_bytes(b"not a database")
    overview = eltiro_overview(tmp_path)
    assert overview["errors"][:2] == ["demands", "pending"]
    assert overview["pending"] == [] and overview["pending_count"] == 0


def test_runs_since_returns_every_run_in_the_window(tmp_path):
    (tmp_path / "eltiro").mkdir()
    for index in range(60):
        _run(tmp_path, f"run-{index:032x}", f"2026-10-{1 + index % 28:02d}T00:00:00+00:00")
    assert len(eltiro_overview(tmp_path)["runs"]) == 50
    since = datetime(2026, 10, 20, tzinfo=UTC)
    runs = eltiro_overview(tmp_path, runs_since=since)["runs"]
    assert len(runs) == sum(1 for index in range(60) if 1 + index % 28 >= 20)
    assert all(run["finished_at"] >= "2026-10-20" for run in runs)


def test_gap_sources_carry_a_full_count(tmp_path):
    _state(tmp_path)
    assert eltiro_overview(tmp_path)["gaps"]["source_count"] == 1
