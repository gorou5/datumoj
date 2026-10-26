"""Evidence state and SQL input metadata for read-only observation queries."""

from datetime import date, timedelta

import pytest

from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.contracts import QueryError, QueryResult
from datumoj.rikolto.store.observations import DuckDBObservationStore
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeManifest,
    FakeRawStore,
    FakeRegistry,
    FakeRetriever,
)
from datumoj.rikolto.testing.samples import T0, make_observation, make_snapshot, make_spec


@pytest.fixture
def tools(tmp_path):
    store = DuckDBObservationStore(tmp_path / "observations")
    manifest = FakeManifest()
    chunks = FakeChunkStore()
    registry = FakeRegistry([make_spec("jp-stat")])
    snapshots = [
        make_snapshot("jp-stat", "https://example.go.jp/table-a.csv", b"table-a", fetched_at=T0,
                      snapshot_id="snapshot-a"),
        make_snapshot("jp-stat", "https://example.go.jp/table-b.csv", b"table-b", fetched_at=T0 + timedelta(days=1),
                      snapshot_id="snapshot-b"),
    ]
    for snapshot in snapshots:
        manifest.record_snapshot(snapshot)
    store.write_table("jp-stat", "table-a", [
        make_observation(
            "obs-a", "10", table_key="table-a", snapshot_sha256=snapshots[0].sha256,
            fetched_at=snapshots[0].fetched_at,
        ),
    ])
    store.write_table("jp-stat", "table-b", [
        make_observation(
            "obs-b", "20", table_key="table-b", area_code="27000", area_label="大阪府",
            time_start=date(2021, 1, 1), time_end=date(2021, 12, 31), time_label="2021年",
            snapshot_sha256=snapshots[1].sha256, fetched_at=snapshots[1].fetched_at,
        ),
    ])
    return AnalysisTools(
        retriever=FakeRetriever(chunks, manifest, registry), catalog=FakeCatalogStore(),
        observations=store, chunks=chunks, manifest=manifest, raw=FakeRawStore(tmp_path), registry=registry,
        data_dir=tmp_path,
    )


def test_complete_rows_include_table_snapshot_and_raw_filters(tools):
    result = tools.query_sql(
        "SELECT source_id, table_key, obs_id FROM observations "
        "WHERE table_key = 'table-a' AND time_start BETWEEN DATE '2020-01-01' AND DATE '2020-12-31' "
        "AND area_code = '13000'"
    )

    assert result["gap_state"] == "ok"
    assert result["evidence_state"] == "complete"
    assert result["evidence_inputs"]["filters"] == {
        "time": ["time_start BETWEEN DATE '2020-01-01' AND DATE '2020-12-31'"],
        "area": ["area_code = '13000'"],
    }
    (table,) = result["evidence_inputs"]["tables"]
    assert (table["source_id"], table["table_key"]) == ("jp-stat", "table-a")
    assert table["snapshots"] == [{
        "sha256": tools.manifest.snapshots_for("jp-stat")[0].sha256,
        "snapshot_ids": [{"snapshot_id": "snapshot-a", "fetched_at": T0.isoformat()}],
    }]
    assert table["fetched_at_range"] == {"from": T0.isoformat(), "to": T0.isoformat()}


def test_partial_and_unavailable_rows(tools):
    partial = tools.query_sql(
        "SELECT source_id, table_key, obs_id FROM observations WHERE obs_id = 'obs-a' "
        "UNION ALL SELECT 'jp-stat', 'table-a', NULL"
    )
    assert partial["evidence_state"] == "partial"
    assert any("1/2 行" in warning and "source_id、table_key、obs_id" in warning for warning in partial["warnings"])

    aggregate = tools.query_sql("SELECT count(*) AS n FROM observations")
    assert aggregate["evidence_state"] == "unavailable"
    assert len(aggregate["evidence_inputs"]["tables"]) == 2
    assert any("逐行出处" in warning and "source_id、table_key、obs_id" in warning for warning in aggregate["warnings"])


def test_unrecognized_time_and_area_filters_are_explicit(tools):
    result = tools.query_sql("SELECT obs_id FROM observations WHERE value > 0")
    assert result["evidence_state"] == "complete"
    assert result["evidence_inputs"]["filters"] == {"time": "未识别", "area": "未识别"}


def test_metadata_filter_error_does_not_break_query(tools, monkeypatch):
    query_readonly = tools.observations.query_readonly

    def fail_metadata_filter(sql, *args, **kwargs):
        if sql.startswith("SELECT DISTINCT source_id, table_key FROM observations WHERE"):
            raise QueryError("synthetic metadata query failure")
        return query_readonly(sql, *args, **kwargs)

    monkeypatch.setattr(tools.observations, "query_readonly", fail_metadata_filter)
    result = tools.query_sql("SELECT source_id, table_key, obs_id FROM observations WHERE value > 0")

    assert result["evidence_state"] == "complete"
    assert len(result["evidence_inputs"]["tables"]) == 2


def test_input_inventory_is_paged_past_the_result_limit(tools, monkeypatch):
    query_readonly = tools.observations.query_readonly

    def inventory_pages(sql, *args, **kwargs):
        if sql.startswith("SELECT source_id, table_key, snapshot_sha256,"):
            offset = int(sql.rsplit(" OFFSET ", 1)[1])
            stop = min(offset + 1000, 1001)
            rows = [
                ("jp-stat", f"table-{index:04d}", "cd" * 32, T0, T0)
                for index in range(offset, stop)
            ]
            return QueryResult(
                columns=["source_id", "table_key", "snapshot_sha256", "min(fetched_at)", "max(fetched_at)"],
                rows=rows,
            )
        return query_readonly(sql, *args, **kwargs)

    monkeypatch.setattr(tools.observations, "query_readonly", inventory_pages)
    inputs = tools._query_evidence_inputs("SELECT count(*) FROM observations")

    assert len(inputs["tables"]) == 1001
