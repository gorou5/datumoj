"""Request-wide observation query budget and describe cache regressions."""

import os
from datetime import date

import pytest

import datumoj.rikolto.analysis.tools as analysis_tools_module
import datumoj.rikolto.store.observations as observations_module
from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.contracts import QueryTimeout
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


def test_describe_cache_hits_and_invalidates_for_writes_and_file_changes(tmp_path, monkeypatch):
    store = DuckDBObservationStore(tmp_path / "observations")
    store.write_table("jp-stat", "table-a", [make_observation("obs-a", "1", table_key="table-a")])

    assert [table.table_key for table in store.describe().tables] == ["table-a"]
    connect = store._connect
    calls = 0

    def count_connect():
        nonlocal calls
        calls += 1
        return connect()

    monkeypatch.setattr(store, "_connect", count_connect)
    assert [table.table_key for table in store.describe().tables] == ["table-a"]
    assert calls == 0

    store.write_table("jp-stat", "table-b", [make_observation("obs-b", "2", table_key="table-b")])
    assert [table.table_key for table in store.describe().tables] == ["table-a", "table-b"]
    assert calls == 1

    path = store.path_for("jp-stat", "table-b")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert [table.table_key for table in store.describe().tables] == ["table-a", "table-b"]
    assert calls == 2


def test_query_timeout_includes_connection_setup(tmp_path, monkeypatch):
    store = DuckDBObservationStore(tmp_path / "observations")
    connect = store._connect
    now = [0.0]

    def slow_connect():
        con = connect()
        now[0] = 10.1
        return con

    monkeypatch.setattr(observations_module, "_monotonic", lambda: now[0])
    monkeypatch.setattr(store, "_connect", slow_connect)
    with pytest.raises(QueryTimeout, match="含连接"):
        store.query_readonly("SELECT 1", timeout_s=10)


@pytest.fixture
def query_tools(tmp_path):
    store = DuckDBObservationStore(tmp_path / "observations")
    manifest = FakeManifest()
    chunks = FakeChunkStore()
    registry = FakeRegistry([make_spec("jp-stat")])
    snapshot = make_snapshot("jp-stat", "https://example.go.jp/table.csv", b"table", fetched_at=T0)
    manifest.record_snapshot(snapshot)
    store.write_table("jp-stat", "table-a", [
        make_observation(
            "obs-a", "10", table_key="table-a", snapshot_sha256=snapshot.sha256,
            fetched_at=snapshot.fetched_at, time_start=date(2020, 1, 1),
        ),
    ])
    tools = AnalysisTools(
        retriever=FakeRetriever(chunks, manifest, registry), catalog=FakeCatalogStore(), observations=store,
        chunks=chunks, manifest=manifest, raw=FakeRawStore(tmp_path), registry=registry, data_dir=tmp_path,
    )
    return tools, store


@pytest.mark.parametrize("slow_stage", ["query", "provenance", "metadata", "describe"])
def test_query_sql_has_one_budget_and_later_request_succeeds(query_tools, monkeypatch, slow_stage):
    tools, store = query_tools
    now = [0.0]
    monkeypatch.setattr(analysis_tools_module, "_monotonic", lambda: now[0])
    query = store.query_readonly
    fired = False

    def advance_once():
        nonlocal fired
        if not fired:
            fired = True
            now[0] = 10.1

    if slow_stage in ("query", "metadata"):
        def slow_query(sql, *args, **kwargs):
            result = query(sql, *args, **kwargs)
            if slow_stage == "query" and sql.startswith("SELECT source_id, table_key, obs_id") or slow_stage == "metadata" and sql.startswith("SELECT source_id, table_key, snapshot_sha256,"):
                advance_once()
            return result

        monkeypatch.setattr(store, "query_readonly", slow_query)
    elif slow_stage == "provenance":
        citation = tools._obs_citation

        def slow_citation(observation, warnings):
            result = citation(observation, warnings)
            advance_once()
            return result

        monkeypatch.setattr(tools, "_obs_citation", slow_citation)
    else:
        describe = store.describe

        def slow_describe(timeout_s=10):
            result = describe(timeout_s=timeout_s)
            advance_once()
            return result

        monkeypatch.setattr(store, "describe", slow_describe)

    sql = "SELECT source_id, table_key, obs_id FROM observations"
    with pytest.raises(QueryTimeout, match="整个请求"):
        tools.query_sql(sql, timeout_s=10)

    result = tools.query_sql(sql, timeout_s=10)
    assert result["gap_state"] == "ok"
    assert [row[2]["content"] for row in result["data"]["rows"]] == ["obs-a"]
