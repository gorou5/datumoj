"""Regression for audit D-04: duplicate obs_id values must keep table provenance."""

from datumoj.analysis.tools import AnalysisTools
from datumoj.core.models import ObservationRef
from datumoj.store.observations import DuckDBObservationStore
from datumoj.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeManifest,
    FakeRawStore,
    FakeRegistry,
    FakeRetriever,
)
from datumoj.testing.samples import make_observation, make_spec


def test_query_sql_provenance_matches_table_for_duplicate_obs_id(tmp_path):
    observations = DuckDBObservationStore(tmp_path / "observations")
    observations.write_table("jp-stat", "table-a", [
        make_observation("same-id", "11", table_key="table-a"),
    ])
    observations.write_table("jp-stat", "table-b", [
        make_observation("same-id", "22", table_key="table-b"),
    ])

    manifest = FakeManifest()
    chunks = FakeChunkStore()
    registry = FakeRegistry([make_spec("jp-stat")])
    tools = AnalysisTools(
        retriever=FakeRetriever(chunks, manifest, registry),
        catalog=FakeCatalogStore(),
        observations=observations,
        chunks=chunks,
        manifest=manifest,
        raw=FakeRawStore(tmp_path),
        registry=registry,
        data_dir=tmp_path,
    )

    result = tools.query_sql(
        "SELECT source_id, table_key, obs_id, value FROM observations ORDER BY table_key"
    )

    rows = result["data"]["rows"]
    provenance = result["data"]["provenance"]
    assert [(row[1]["content"], row[2]["content"]) for row in rows] == [
        ("table-a", "same-id"), ("table-b", "same-id"),
    ]
    assert [(item["table_key"], item["obs_id"]) for item in provenance] == [
        ("table-a", "same-id"), ("table-b", "same-id"),
    ]
    assert [item["observation_ref"] for item in provenance] == [
        str(ObservationRef(source_id="jp-stat", table_key="table-a", obs_id="same-id")),
        str(ObservationRef(source_id="jp-stat", table_key="table-b", obs_id="same-id")),
    ]
