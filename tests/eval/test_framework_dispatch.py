from datetime import date
from decimal import Decimal

from datumoj.core.contracts import CatalogFilter
from datumoj.eval import Question, evaluate
from datumoj.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeManifest,
    FakeObservationStore,
    FakeRegistry,
    FakeRetriever,
)
from datumoj.testing.samples import (
    make_chunk,
    make_dataset,
    make_document,
    make_observation,
    make_snapshot,
    make_spec,
)


class RecordingCatalog(FakeCatalogStore):
    def __init__(self):
        super().__init__()
        self.search_calls = []

    def search(self, query: str, filters: CatalogFilter | None = None, k: int = 20):
        self.search_calls.append((query, filters, k))
        return super().search(query, filters, k)


def _question(question_id, *, kind, hits, queries=("人口",), source_ids=None, value=None):
    expect = {"hits": hits}
    if value is not None:
        expect["value"] = value
    return Question.model_validate(
        {
            "id": question_id,
            "question": "資料を確認してください。",
            "lang": "ja",
            "kind": kind,
            "queries": list(queries),
            "source_ids": source_ids,
            "expect": expect,
        }
    )


def _retriever(manifest, sources, documents=()):
    chunks = FakeChunkStore()
    for document, document_chunks in documents:
        chunks.put_document(document, document_chunks)
    registry = FakeRegistry(make_spec(source_id) for source_id in sources)
    return FakeRetriever(chunks, manifest, registry)


def test_catalog_search_can_satisfy_dataset_retrieval_and_uses_source_filter():
    manifest = FakeManifest()
    retriever = _retriever(manifest, ["jp-data"])
    catalog = RecordingCatalog()
    catalog.upsert(datasets=[make_dataset("jp-data:population", "人口統計")])
    question = _question(
        "catalog-only",
        kind="retrieval",
        hits=[{"doc_key": "jp-data:population"}],
        queries=("人口", "人口統計"),
        source_ids=["jp-data"],
    )

    report = evaluate(
        [question],
        retriever=retriever,
        observation_store=FakeObservationStore(),
        cite=None,
        catalog=catalog,
    )

    assert report.overall["recall_at_10"] == 1.0
    assert report.overall["mrr"] == 1.0
    assert [query for query, _, _ in catalog.search_calls] == list(question.queries)
    assert all(filters == CatalogFilter(source_ids=["jp-data"]) for _, filters, _ in catalog.search_calls)


def test_catalog_hit_cannot_satisfy_locator_specific_expected_hit():
    manifest = FakeManifest()
    retriever = _retriever(manifest, ["jp-data"])
    catalog = FakeCatalogStore()
    catalog.upsert(datasets=[make_dataset("jp-data:population", "人口統計")])
    question = _question(
        "catalog-locator-specific",
        kind="retrieval",
        hits=[{"doc_key": "jp-data:population", "locator_prefix": "row=tokyo"}],
        source_ids=["jp-data"],
    )

    report = evaluate(
        [question],
        retriever=retriever,
        observation_store=FakeObservationStore(),
        cite=None,
        catalog=catalog,
    )

    assert report.overall["recall_at_10"] == 0.0
    assert report.overall["mrr"] == 0.0


def test_cross_source_recall_combines_document_and_catalog_hits():
    manifest = FakeManifest()
    snapshot = make_snapshot("jp-doc", "https://example.go.jp/notice", b"notice")
    manifest.record_snapshot(snapshot)
    document = make_document("jp-doc:notice", snapshot.sha256)
    chunk = make_chunk(document.doc_key, snapshot.sha256, "section=1", "個人情報に関する資料")
    retriever = _retriever(manifest, ["jp-doc", "jp-data"], [(document, [chunk])])
    catalog = FakeCatalogStore()
    catalog.upsert(datasets=[make_dataset("jp-data:privacy", "個人情報データ")])
    question = _question(
        "cross-source-dispatch",
        kind="cross_source",
        hits=[
            {"doc_key": "jp-doc:notice", "locator_prefix": "section=1"},
            {"doc_key": "jp-data:privacy"},
        ],
        source_ids=["jp-doc", "jp-data"],
        queries=("個人情報",),
    )

    report = evaluate(
        [question],
        retriever=retriever,
        observation_store=FakeObservationStore(),
        cite=lambda citation: "個人情報に関する資料",
        catalog=catalog,
    )

    assert report.overall["recall_at_10"] == 1.0
    assert report.by_source["jp-doc"]["recall_at_10"] == 1.0
    assert report.by_source["jp-data"]["recall_at_10"] == 1.0


def test_catalog_is_not_used_when_not_passed():
    manifest = FakeManifest()
    retriever = _retriever(manifest, ["jp-data"])
    catalog = FakeCatalogStore()
    catalog.upsert(datasets=[make_dataset("jp-data:population", "人口統計")])
    question = _question(
        "catalog-optional",
        kind="retrieval",
        hits=[{"doc_key": "jp-data:population"}],
        source_ids=["jp-data"],
    )

    report = evaluate(
        [question], retriever=retriever, observation_store=FakeObservationStore(), cite=None
    )

    assert report.overall["recall_at_10"] == 0.0
    assert report.overall["mrr"] == 0.0


def test_numeric_value_column_and_sql_provenance_are_used():
    manifest = FakeManifest()
    document_snapshot = make_snapshot("jp-stat", "https://example.go.jp/population", b"document")
    observation_snapshot = make_snapshot("jp-stat", "https://example.go.jp/table", b"table")
    manifest.record_snapshot(document_snapshot)
    manifest.record_snapshot(observation_snapshot)
    document = make_document("jp-stat:population", document_snapshot.sha256)
    chunk = make_chunk(document.doc_key, document_snapshot.sha256, "row=overview", "東京都の人口統計")
    retriever = _retriever(manifest, ["jp-stat"], [(document, [chunk])])
    observations = FakeObservationStore()
    observations.write_table(
        "jp-stat",
        "population",
        [
            make_observation(
                "population-2020",
                Decimal("42"),
                source_id="jp-stat",
                dataset_key="jp-stat:population",
                table_key="population",
                snapshot_sha256=observation_snapshot.sha256,
                locator="row=tokyo",
            )
        ],
    )
    question = _question(
        "numeric-provenance",
        kind="numeric",
        hits=[{"doc_key": "jp-stat:population", "locator_prefix": "row=overview"}],
        source_ids=["jp-stat"],
        value={
            "sql": "SELECT obs_id, value FROM observations WHERE obs_id = 'population-2020'",
            "equals": 42,
        },
    )

    def cite(citation):
        if citation.snapshot_sha256 == observation_snapshot.sha256:
            return "東京都の人口は42人です。"
        return "東京都の人口統計資料"

    report = evaluate(
        [question], retriever=retriever, observation_store=observations, cite=cite, manifest=manifest
    )

    assert report.overall["numeric_precision"] == 1.0
    assert report.overall["numeric_occurrence"] == 1.0


def test_effective_time_basis_counts_as_time_semantics():
    manifest = FakeManifest()
    snapshot = make_snapshot("jp-law", "https://example.go.jp/law", b"law")
    manifest.record_snapshot(snapshot)
    document = make_document("jp-law:ordinance", snapshot.sha256)
    chunk = make_chunk(
        document.doc_key,
        snapshot.sha256,
        "art=1",
        "施行日を定める告示",
        effective_at=date(2025, 4, 1),
    )
    retriever = _retriever(manifest, ["jp-law"], [(document, [chunk])])
    question = _question(
        "effective-date",
        kind="time",
        hits=[{"doc_key": "jp-law:ordinance", "locator_prefix": "art=1"}],
        source_ids=["jp-law"],
        queries=("告示",),
    )

    report = evaluate(
        [question],
        retriever=retriever,
        observation_store=FakeObservationStore(),
        cite=lambda citation: "施行日を定める告示",
    )

    assert report.overall["time_semantics_accuracy"] == 1.0
