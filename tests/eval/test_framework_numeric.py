from decimal import Decimal

from datumoj.rikolto.eval import Question, evaluate
from datumoj.rikolto.testing.fakes import (
    FakeChunkStore,
    FakeManifest,
    FakeObservationStore,
    FakeRegistry,
    FakeRetriever,
)
from datumoj.rikolto.testing.samples import make_chunk, make_document, make_observation, make_snapshot, make_spec


def _fixture(row_value=Decimal(42)):
    manifest = FakeManifest()
    document_snapshot = make_snapshot("jp-stat", "https://example.go.jp/population.txt", b"document")
    observation_snapshot = make_snapshot("jp-stat", "https://example.go.jp/population.csv", b"table")
    manifest.record_snapshot(document_snapshot)
    manifest.record_snapshot(observation_snapshot)

    document = make_document("jp-stat:population", document_snapshot.sha256)
    chunk = make_chunk(
        document.doc_key,
        document_snapshot.sha256,
        "table=population;row=overview",
        "東京都の人口調査",
    )
    chunks = FakeChunkStore()
    chunks.put_document(document, [chunk])
    retriever = FakeRetriever(chunks, manifest, FakeRegistry([make_spec("jp-stat")]))

    observations = FakeObservationStore()
    observations.write_table(
        "jp-stat",
        "t1",
        [
            make_observation(
                "tokyo-population",
                row_value,
                source_id="jp-stat",
                dataset_key="jp-stat:population",
                table_key="t1",
                snapshot_sha256=observation_snapshot.sha256,
                locator="table=population;row=tokyo",
            )
        ],
    )
    question = Question.model_validate(
        {
            "id": "population-number",
            "question": "東京都の人口はいくらですか？",
            "lang": "ja",
            "kind": "numeric",
            "queries": ["人口"],
            "expect": {
                "hits": [{"doc_key": "jp-stat:population", "locator_prefix": "table=population"}],
                "value": {"sql": "SELECT value, obs_id FROM observations", "equals": 42},
            },
        }
    )

    def cite(citation):
        if citation.snapshot_sha256 == document_snapshot.sha256:
            return "東京都の人口調査"
        if citation.snapshot_sha256 == observation_snapshot.sha256:
            return "東京都の人口は42人"
        return None

    return question, retriever, observations, manifest, document_snapshot, observation_snapshot, cite


def test_numeric_occurrence_keeps_retrieved_document_evidence():
    question, retriever, observations, _, document_snapshot, _, _ = _fixture()

    def cite(citation):
        return "東京都の人口は42人" if citation.snapshot_sha256 == document_snapshot.sha256 else None

    report = evaluate([question], retriever=retriever, observation_store=observations, cite=cite)

    assert report.overall["numeric_precision"] == 1.0
    assert report.overall["numeric_occurrence"] == 1.0


def test_numeric_occurrence_accepts_restorable_sql_provenance():
    question, retriever, observations, manifest, document_snapshot, observation_snapshot, _ = _fixture()
    cited = []

    def cite(citation):
        cited.append(citation)
        if citation.snapshot_sha256 == document_snapshot.sha256:
            return "東京都の人口調査"
        if citation.snapshot_sha256 == observation_snapshot.sha256:
            return "東京都の人口は42人"
        return None

    report = evaluate(
        [question], retriever=retriever, observation_store=observations, cite=cite, manifest=manifest
    )

    assert report.overall["numeric_occurrence"] == 1.0
    sql_citation = next(c for c in cited if c.snapshot_sha256 == observation_snapshot.sha256)
    assert sql_citation.resource_url == observation_snapshot.resource_url
    assert sql_citation.locator == "table=population;row=tokyo"


def test_numeric_occurrence_rejects_sql_value_when_citation_cannot_restore():
    question, retriever, observations, manifest, document_snapshot, observation_snapshot, _ = _fixture()

    def cite(citation):
        if citation.snapshot_sha256 == document_snapshot.sha256:
            return "東京都の人口調査"
        if citation.snapshot_sha256 == observation_snapshot.sha256:
            return None
        return None

    report = evaluate(
        [question], retriever=retriever, observation_store=observations, cite=cite, manifest=manifest
    )

    assert report.overall["numeric_precision"] == 1.0
    assert report.overall["numeric_occurrence"] == 0.0
    assert "文档检索前 3 条引用与 SQL 出处行中均未找到可还原的期望数值" in report.failures[0]["reasons"]


def test_numeric_occurrence_rejects_sql_value_mismatch():
    question, retriever, observations, manifest, _, _, cite = _fixture(Decimal(7))

    report = evaluate(
        [question], retriever=retriever, observation_store=observations, cite=cite, manifest=manifest
    )

    assert report.overall["numeric_precision"] == 0.0
    assert report.overall["numeric_occurrence"] == 0.0
    assert any("SQL 数值与 expect.value.equals 不符" in reason for reason in report.failures[0]["reasons"])
