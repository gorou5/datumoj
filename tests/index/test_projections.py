"""Projection publication, shared body keys and embedding failure accounting."""

from dataclasses import replace
from datetime import timedelta

import pytest

from datumoj.rikolto.core.contracts import ParseResult
from datumoj.rikolto.core.models import ChangeKind
from datumoj.rikolto.index.projections import is_projection
from datumoj.rikolto.pipeline import PipelineDeps, backfill_projections, parse_pending, reindex, vector_status
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChangeLog,
    FakeChunkStore,
    FakeEmbedder,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
    FakeVectorIndex,
)
from datumoj.rikolto.testing.samples import (
    T0,
    make_chunk,
    make_dataset,
    make_document,
    make_observation,
    make_snapshot,
    make_spec,
)

KEY = "jp-test:dataset"


class ProjectionParser(FakeParser):
    def parse(self, snapshot, data, meta=None):
        result = ParseResult()
        if meta.get("body"):
            result.documents.append(make_document(KEY, snapshot.sha256))
            result.chunks.append(make_chunk(KEY, snapshot.sha256, "section=1", "body evidence"))
        result.datasets.append(make_dataset(
            KEY, "Catalog title", snapshot_sha256=snapshot.sha256, locator="entry=1",
            description="Parser description", tags=["tag"],
        ))
        if meta.get("row", True):
            result.observations.append(make_observation(
                "o1", None, source_id="jp-test", dataset_key=KEY, snapshot_sha256=snapshot.sha256,
                locator="member=data.csv;row=1", note="-", fetched_at=snapshot.fetched_at,
            ))
            if meta.get("shared_locator"):
                result.observations.append(result.observations[0].model_copy(update={"obs_id": "o2", "indicator": "別指標"}))
        return result


@pytest.fixture
def deps():
    return PipelineDeps(
        manifest=FakeManifest(), raw=FakeRawStore(), chunks=FakeChunkStore(),
        keyword=FakeKeywordIndex(), catalog=FakeCatalogStore(), observations=FakeObservationStore(),
        registry=FakeRegistry([make_spec()]), vectors=FakeVectorIndex(FakeEmbedder()),
        parsers={("fake", "text/plain"): ProjectionParser},
    )


def ingest(deps, sid, *, body=False, row=True, seconds=0, shared_locator=False):
    data = sid.encode()
    snap = make_snapshot(data=data, snapshot_id=sid, fetched_at=T0 + timedelta(seconds=seconds))
    deps.raw.put(data)
    deps.manifest.record_snapshot(snap, {"body": body, "row": row, "shared_locator": shared_locator})
    summary = parse_pending(deps)
    assert not summary.failed
    return snap


def test_same_snapshot_keeps_body_and_missing_note(deps):
    snap = ingest(deps, "both", body=True)
    chunks = deps.chunks.chunks_for(KEY)
    assert len(chunks) == 3
    assert {chunk.snapshot_sha256 for chunk in chunks} == {snap.sha256}
    row = next(chunk for chunk in chunks if "#projection:row:" in chunk.chunk_id)
    assert '"value":null' in row.text and '"note":"-"' in row.text
    assert len(deps.vectors.search("body").items) == 1
    assert vector_status(deps) == {"state": "available", "blocks": 3}
    reindex(deps)
    assert len(deps.chunks.chunks_for(KEY)) == 3
    assert len(deps.vectors.search("body").items) == 1


def test_different_snapshot_cannot_replace_body_with_projection(deps):
    body = ingest(deps, "body", body=True)
    newer = ingest(deps, "new-row", seconds=1)
    assert deps.chunks.get_document(KEY).snapshot_sha256 == body.sha256
    assert any(not is_projection(chunk) for chunk in deps.chunks.chunks_for(KEY))
    assert deps.chunks.get_document(KEY, newer.sha256) is not None
    backfill_projections(deps)
    assert deps.chunks.get_document(KEY).snapshot_sha256 == body.sha256


def test_projected_snapshot_fields_do_not_report_body_changes(deps):
    deps.change_log = FakeChangeLog()
    ingest(deps, "first", body=True)
    ingest(deps, "second", body=True, seconds=1)
    assert [event.kind for event in deps.change_log.list()] == [ChangeKind.added]


def test_metadata_refresh_keeps_row_snapshot(deps):
    row = ingest(deps, "rows")
    ingest(deps, "metadata", row=False, seconds=1)
    assert deps.chunks.get_document(KEY).snapshot_sha256 == row.sha256
    assert len(deps.chunks.chunks_for(KEY)) == 2
    assert not deps.vectors.search("Catalog").items
    reindex(deps)
    assert deps.chunks.get_document(KEY).snapshot_sha256 == row.sha256
    assert backfill_projections(deps) == 0


@pytest.mark.parametrize("limit", [None, 0, 1])
def test_projections_do_not_consume_embedding_allowance_or_pending(deps, limit, monkeypatch):
    deps = replace(deps, vector_limit=limit)
    monkeypatch.setattr(deps.vectors, "add", lambda chunks: (_ for _ in ()).throw(RuntimeError("offline")))
    ingest(deps, "body-and-rows", body=True)
    assert vector_status(deps)["missing_blocks"] == 1
    assert deps.vector_state["missing"][KEY]["chunks"] == 1
    if limit == 1:
        with pytest.raises(RuntimeError, match="候选向量代写入失败"):
            reindex(deps)
    else:
        reindex(deps)
    assert deps.vector_state["missing"][KEY]["chunks"] == 1
    if limit == 1:
        with pytest.raises(RuntimeError, match="候选向量代写入失败"):
            reindex(deps, vectors_only=True)
    else:
        reindex(deps, vectors_only=True)
    assert deps.vector_state["missing"][KEY]["chunks"] == 1


def test_distinct_observations_sharing_a_locator_are_preserved(deps):
    ingest(deps, "shared-locator", shared_locator=True)
    rows = [chunk for chunk in deps.chunks.chunks_for(KEY) if "#projection:row:" in chunk.chunk_id]
    assert len(rows) == 2 and rows[0].locator == rows[1].locator
    assert rows[0].text != rows[1].text
    assert backfill_projections(deps) == 0


def test_common_row_labels_do_not_hide_missing_query_subject(deps):
    from datumoj.rikolto.index.fts import SqliteKeywordIndex

    keyword = SqliteKeywordIndex(":memory:")
    try:
        deps = replace(deps, keyword=keyword)
        ingest(deps, "population", shared_locator=True)
        assert keyword.search("東京都 国勢調査").items
        assert not keyword.search("東京都 国勢調査 補助金 申請件数").items
    finally:
        keyword.close()


def test_row_multiplicity_does_not_change_query_concept_weights():
    from datumoj.rikolto.index.fts import SqliteKeywordIndex

    keyword = SqliteKeywordIndex(":memory:")
    try:
        row = make_chunk(KEY, "ab" * 32, "row=1", "大阪府 人口")
        row = row.model_copy(update={"chunk_id": f"{KEY}#projection:row:1"})
        keyword.add([make_chunk("jp-test:forecast", "cd" * 32, "section=1", "大阪府 天気予報"), row])
        concepts = [("大阪府",), ("天気予報",), ("対象期間",)]
        before = keyword.concept_weights(concepts)
        keyword.add([row.model_copy(update={"chunk_id": f"{KEY}#projection:row:{i}", "locator": f"row={i}"})
                     for i in range(2, 102)])
        assert keyword.concept_weights(concepts) == before
    finally:
        keyword.close()


def test_common_row_labels_remain_weak_evidence_among_body_blocks():
    from datumoj.rikolto.index.fts import SqliteKeywordIndex

    keyword = SqliteKeywordIndex(":memory:")
    try:
        row = make_chunk(KEY, "ab" * 32, "row=1", "東京都 国勢調査 人口")
        keyword.add([make_chunk(f"jp-test:body{i}", "ab" * 32, "section=1", "一般的な本文")
                     for i in range(100)])
        keyword.add([row.model_copy(update={"chunk_id": f"{KEY}#projection:row:{i}", "locator": f"row={i}"})
                     for i in range(100)])
        assert not keyword.search("東京都 国勢調査 人口 補助金").items
    finally:
        keyword.close()


def test_legacy_backfill_selects_rows_before_newer_catalog_metadata(deps):
    row = ingest(deps, "rows")
    ingest(deps, "metadata", row=False, seconds=1)
    deps.chunks = FakeChunkStore()
    assert backfill_projections(deps) > 0
    assert deps.chunks.get_document(KEY).snapshot_sha256 == row.sha256
    assert any("#projection:row:" in chunk.chunk_id for chunk in deps.chunks.chunks_for(KEY))
    assert backfill_projections(deps) == 0
