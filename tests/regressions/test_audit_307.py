"""Regression for audit D-05: aggregate queries must report unavailable row evidence."""

from datumoj.analysis.tools import AnalysisTools
from datumoj.store.observations import DuckDBObservationStore
from datumoj.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeManifest,
    FakeRawStore,
    FakeRegistry,
    FakeRetriever,
)
from datumoj.testing.samples import make_observation, make_snapshot, make_spec


def test_aggregate_without_obs_id_is_not_complete(tmp_path):
    observations = DuckDBObservationStore(tmp_path / "observations")
    snapshot = make_snapshot("jp-stat", "https://example.go.jp/table.csv", b"synthetic data", snapshot_id="snapshot-307")
    manifest = FakeManifest()
    manifest.record_snapshot(snapshot)
    observations.write_table("jp-stat", "table-307", [
        make_observation(
            "obs-307", "1", table_key="table-307", snapshot_sha256=snapshot.sha256,
            fetched_at=snapshot.fetched_at,
        ),
    ])
    chunks = FakeChunkStore()
    registry = FakeRegistry([make_spec("jp-stat")])
    tools = AnalysisTools(
        retriever=FakeRetriever(chunks, manifest, registry), catalog=FakeCatalogStore(),
        observations=observations, chunks=chunks, manifest=manifest, raw=FakeRawStore(tmp_path),
        registry=registry, data_dir=tmp_path,
    )

    result = tools.query_sql("SELECT count(*) AS n FROM observations")

    assert result["data"]["rows"] == [[1]]
    assert result["gap_state"] == "ok"
    assert result["evidence_state"] == "unavailable"
