"""Synthetic, offline matching inputs; no recorded fixtures are modified."""

import sqlite3

import pytest

from datumoj.eltiro.eval import GoldSet, Thresholds, score_matches
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.matching import MAX_K, match_demand
from datumoj.eltiro.models import ConsumerConfig, SeriesProfile
from datumoj.eltiro.testing.fakes import FakeProfileStore
from datumoj.eltiro.testing.samples import make_demand, make_profile, make_series
from datumoj.rikolto.index.embed import HashEmbedder
from datumoj.rikolto.testing.fakes import FakeCatalogStore, FakeChunkStore, FakeObservationStore
from datumoj.rikolto.testing.samples import make_chunk, make_dataset, make_document, make_observation


def consumer(**changes):
    return ConsumerConfig.model_validate({
        "id": "example-consumer", "inbox": "consumer/inbox", "outbox": "consumer/outbox",
        "allowed_sources": ["intl-wdi"], "models": {"consumer": "example-consumer",
        "authorized_sources": ["intl-wdi"]}, "max_active_demands": 5, **changes,
    })


def demand(concept="NY.GDP.MKTP.CD", **indicator):
    return make_demand(indicators=[{"key": "gdp", "concept": concept, "unit": "USD", **indicator}])


class ReadCalls:
    """Expose protocol reads, record ANY attempted write without hiding it."""

    def __init__(self, backing):
        self.backing, self.calls = backing, []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            assert name in {"describe", "query_readonly", "get_dataset", "iter_chunks", "get_document"}
            return getattr(self.backing, name)(*args, **kwargs)
        return call


@pytest.fixture
def matching(tmp_path):
    observations, catalog, chunks = FakeObservationStore(), FakeCatalogStore(), FakeChunkStore()
    with SQLiteMatchStore(tmp_path) as store:
        options = dict(profiles=FakeProfileStore(), consumer=consumer(), store=store,
                       observations=ReadCalls(observations), catalog=ReadCalls(catalog), chunks=ReadCalls(chunks))

        def run(request=None, profiles=None, generation=1, **changes):
            if profiles is not None:
                options["profiles"].write_generation(generation, profiles)
            return match_demand(request or demand(), generation, **{**options, **changes})

        def seed(profiles):
            grouped = {}
            for n, p in enumerate(profiles):
                s = p.series
                row = make_observation(str(n), "1", source_id=s.source_id, dataset_key=f"{s.source_id}:synthetic",
                                       table_key=s.table_key, indicator=s.indicator, indicator_code=s.indicator_code,
                                       dims=s.fixed_dimensions, survey=p.survey or "synthetic", unit=p.unit,
                                       snapshot_sha256=f"{n + 1:064x}", locator=f"row={n + 1}")
                grouped.setdefault((s.source_id, s.table_key), []).append(row)
            for (source, table), rows in grouped.items():
                observations.write_table(source, table, rows)

        run.options, run.seed = options, seed
        run.observations, run.catalog, run.chunks = observations, catalog, chunks
        yield run


def test_exact_code_auto_binding_and_positive_example(matching):
    profile = make_profile(unit=" usd ")
    matching.seed([profile])
    result = matching(demand("Nominal GDP", examples={"positive": ["NY.GDP.MKTP.CD"]}), [profile])
    record = result.records[0]
    assert record.auto_bind and record.confirmation == "pending"
    assert set(record.compatibility.model_dump().values()) == {"same"}
    assert record.confidence == 0.95 and result.queue == [] and result.vector_skipped
    assert record.evidence[0].snapshot_sha256 == "0" * 63 + "1"
    assert record.evidence[0].locator == "row=1"


@pytest.mark.parametrize("case", ["unit", "unknown", "negative", "vector"])
def test_unsafe_candidates_never_auto_bind(matching, case):
    profile = make_profile(unit="JPY" if case == "unit" else "USD")
    matching.seed([profile])
    request = demand("Nominal GDP" if case == "unknown" else "NY.GDP.MKTP.CD",
                     examples={"negative": ["Nominal GDP"]} if case == "negative" else {})
    options = {"deterministic": False, "embedder": HashEmbedder()} if case == "vector" else {}
    result = matching(request, [profile], **options)
    assert len(result.queue) == 1 and not result.records[0].auto_bind
    if case == "unit":
        assert result.records[0].compatibility.unit == "different"
    if case == "unknown":
        assert result.records[0].compatibility.survey == result.records[0].compatibility.methodology == "unknown"
    if case == "negative":
        assert "counterexample #1" in result.records[0].reason
    if case == "vector":
        assert result.records[0].tier == "vector"


def test_full_identity_separation_allowed_sources_and_preference(matching):
    first = make_profile(unit="USD", survey="survey-one", series=make_series(fixed_dimensions={"survey": "one"}))
    second = make_profile(unit="EUR", survey="survey-two", series=make_series(fixed_dimensions={"survey": "two"}))
    third = make_profile(unit="USD", series=make_series(source_id="jp-other"))
    fourth = make_profile(unit="USD", series=make_series(source_id="jp-allowed"))
    profiles = [first, second, third, fourth]
    matching.seed(profiles)
    result = matching(demand("Nominal GDP", preferred_sources=["intl-wdi"],
                            keep_separate_by=["source", "release", "survey", "unit", "methodology"]), profiles,
                      consumer=consumer(allowed_sources=["intl-wdi", "jp-allowed"]))
    assert len(result.records) == 3
    assert {r.series.source_id for r in result.records} == {"intl-wdi", "jp-allowed"}
    assert len({str(r.series) for r in result.records}) == 3
    assert all(not r.auto_bind for r in result.records)
    assert result.records[-1].series.source_id == "jp-allowed"
    assert result.records[0].confidence == 0.82 and result.records[-1].confidence == 0.8


def test_missing_evidence_is_reported_and_unrelated_rows_do_not_prove_series(matching):
    missing = make_profile(series=make_series(table_key="missing"), unit="USD")
    unrelated = make_profile(series=make_series(indicator="other"), unit="USD")
    matching.seed([unrelated])
    profile = make_profile(unit="USD")
    result = matching(profiles=[profile, missing])
    assert not result.records and set(map(str, result.missing_evidence)) == {str(profile.series), str(missing.series)}


def test_catalog_and_document_evidence_is_from_frozen_read_views(matching):
    catalog = make_profile(availability="catalog_only", series=make_series(table_key="catalog:intl-wdi:synthetic"))
    document = make_profile(availability="document_only", series=make_series(table_key="document:pdf"))
    missing = make_profile(availability="catalog_only", series=make_series(table_key="catalog:intl-wdi:no-evidence"))
    matching.catalog.upsert([make_dataset("intl-wdi:synthetic", "GDP", snapshot_sha256="ab" * 32, locator="row=7"),
                             make_dataset("intl-wdi:no-evidence", "GDP")])
    doc = make_document("intl-wdi:report", "cd" * 32, kind="pdf")
    chunk = make_chunk(doc.doc_key, doc.snapshot_sha256, "page=3", "synthetic")
    matching.chunks.put_document(doc, [chunk])
    result = matching(profiles=[catalog, document, missing])
    assert {(r.evidence[0].snapshot_sha256, r.evidence[0].locator) for r in result.records} == {
        ("ab" * 32, "row=7"), ("cd" * 32, "page=3")}
    assert result.missing_evidence == [missing.series]
    assert all(r.evidence[0].source_id == r.series.source_id for r in result.records)


def test_decisions_carry_only_within_version_and_existing_series(matching):
    first = make_profile(unit="USD")
    second = make_profile(unit="USD", series=make_series(table_key="second"))
    disappeared = make_profile(unit="USD", series=make_series(table_key="gone"))
    matching.seed([first, second, disappeared])
    matching(profiles=[first, second, disappeared])
    store = matching.options["store"]
    for profile, status in ((first, "rejected"), (second, "confirmed"), (disappeared, "confirmed")):
        store.set_confirmation("example-demand", 1, "gdp", profile.series, 1, status)
    next_profiles = [SeriesProfile.model_validate({**p.model_dump(), "generation": 2}) for p in (first, second)]
    # With deterministic disabled, only the actual vector tier can recall decisions.
    result = matching(profiles=next_profiles, generation=2, deterministic=False, embedder=HashEmbedder())
    assert [r.confirmation for r in result.records] == ["confirmed", "rejected"]
    assert [r.series for r in result.recommendations] == [second.series]
    assert not result.records[-1].auto_bind
    request = make_demand(version=2, indicators=demand().indicators)
    fresh = matching(request, next_profiles, generation=2)
    assert all(r.confirmation == "pending" for r in fresh.records)


def test_same_generation_rerun_uses_persisted_result_without_reads(matching):
    profile = make_profile(unit="USD")
    matching.seed([profile])
    first = matching(profiles=[profile])
    matching.observations.write_table("intl-wdi", "gdp", [])
    before = list(matching.options["observations"].calls)
    assert matching() == first
    assert matching.options["observations"].calls == before
    with pytest.raises(ValueError, match="configuration changed"):
        matching(consumer=consumer(allowed_sources=["intl-wdi", "jp-other"]))


def test_tier_switches_no_embedder_and_bounded_k(matching):
    with pytest.raises(ValueError, match="at least one"):
        matching(deterministic=False, vector=False)
    for k in (0, MAX_K + 1, True):
        with pytest.raises(ValueError, match="limit"):
            matching(k=k)
    with pytest.raises(ValueError, match="Embedder"):
        matching(deterministic=False)


def test_vector_model_identity_cache_and_top_k(matching, tmp_path):
    profiles = [make_profile(unit="USD", series=make_series(table_key=f"table-{i}")) for i in range(4)]
    matching.seed(profiles)
    result = matching(profiles=profiles, deterministic=False, embedder=HashEmbedder(), k=2)
    assert len(result.records) == 2 and all(r.tier == "vector" and not r.auto_bind for r in result.records)
    for embedder in (HashEmbedder(model="different-model"), HashEmbedder(dim=32)):
        with pytest.raises(ValueError, match="configuration changed"):
            matching(deterministic=False, embedder=embedder, k=2)
    path = next((tmp_path / "eltiro/vectors/generation-1").glob("*.sqlite"))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == 2
        db.execute('UPDATE model SET identity=?', ('{"generation":1,"model":"other","dim":64}',))
    with pytest.raises(ValueError, match="model mismatch"):
        matching(make_demand(id="second-demand"), deterministic=False, embedder=HashEmbedder(), k=2)


def test_no_rikolto_writes(matching):
    profile = make_profile(unit="USD")
    matching.seed([profile])
    matching(profiles=[profile], embedder=HashEmbedder())
    assert matching.options["observations"].calls == ["describe", "query_readonly"]
    assert matching.options["catalog"].calls == matching.options["chunks"].calls == []


def test_exact_concept_codes_and_multilingual_keywords(matching):
    profile = make_profile(concept_ids=[{"scheme": "synthetic", "code": "000012"}], unit="USD")
    matching.seed([profile])
    result = matching(demand("Concept 000012"), [profile])
    assert result.records[0].auto_bind and result.records[0].confidence == 0.85
    with SQLiteMatchStore(matching.options["store"].path.parent / "other") as store:
        assert matching(demand("名目GDP"), [profile], store=store).records[0].tier == "deterministic"


def test_synthetic_score_matches_perfect_missing_wrong(matching):
    profile = make_profile(unit="USD")
    wrong = make_profile(unit="USD", series=make_series(table_key="wrong"))
    matching.seed([profile, wrong])
    records = matching(profiles=[profile, wrong]).records
    gold = GoldSet.model_validate({"schema": "eltiro.eval.gold/v1", "demand_id": "example-demand",
                                  "demand_version": 1, "entries": [{
        "id": "gdp", "indicator_key": "gdp", "series": profile.series, "expected": "positive",
        "status": "verified", "evidence": {"reference": "synthetic", "locator": "row=1", "note": "synthetic"},
    }]})
    thresholds = Thresholds.model_validate({"schema": "eltiro.eval.thresholds/v1", "deterministic": {
        "precision": 1, "recall": 1}, "overall": {"precision": 1, "recall": 1},
        "max_counterexample_bindings": 0, "rationale": "synthetic"})
    perfect = score_matches([gold], records[:1], thresholds)
    missing = score_matches([gold], [], thresholds)
    wrong = score_matches([gold], records[1:], thresholds)
    for metrics in (perfect.overall, perfect.deterministic):
        assert (metrics.true_positives, metrics.false_positives, metrics.false_negatives) == (1, 0, 0)
        assert metrics.precision == metrics.recall == 1 and metrics.passed
    assert missing.overall.false_negatives == 1 and missing.overall.recall == 0 and not missing.passed
    assert wrong.overall.false_positives == wrong.overall.false_negatives == 1
    assert wrong.overall.precision == wrong.overall.recall == 0 and not wrong.passed


@pytest.mark.parametrize("concept,auto_bind", [
    ("Official NY.GDP.MKTP.CD.", True), ("NY.GDP.MKTP.CD.EXTRA", False),
    ("ny.gdp.mktp.cd", False), ("XNY.GDP.MKTP.CD", False),
])
def test_codes_are_exact_tokens_not_substrings(matching, concept, auto_bind):
    profile = make_profile(unit="USD")
    matching.seed([profile])
    result = matching(demand(concept), [profile], embedder=HashEmbedder())
    assert result.records and result.records[0].auto_bind is auto_bind


def test_model_generated_concept_is_not_official_evidence(matching):
    profile = make_profile(unit="USD", concept_ids=[{"scheme": "synthetic", "code": "000012"}],
                           model_generated_fields=["concept_ids"])
    matching.seed([profile])
    result = matching(demand("Nominal GDP 000012"), [profile])
    assert not result.records[0].auto_bind
    assert result.records[0].compatibility.methodology == "unknown"


@pytest.mark.parametrize("bad", [[], [[1.0]], [[float("nan")] * 64], [[float("inf")] * 64]])
def test_invalid_embeddings_leave_matches_unpublished(matching, bad):
    class InvalidEmbedder(HashEmbedder):
        def embed(self, texts):
            return bad

    profile = make_profile(unit="USD")
    matching.seed([profile])
    with pytest.raises(ValueError, match="embedding"):
        matching(profiles=[profile], embedder=InvalidEmbedder())
    assert matching.options["store"].for_demand("example-demand", 1) == []


def test_generation_mismatch_fails_instead_of_using_latest_evidence(matching):
    from datumoj.rikolto.core.contracts import QueryResult
    from datumoj.rikolto.core.models import EvidenceState, GenerationToken

    profile = make_profile(unit="USD")
    matching.seed([profile])
    result = matching.observations.query_readonly("SELECT * FROM observations")
    wrong_generation = QueryResult(
        columns=result.columns, rows=result.rows, provenance=result.provenance,
        generation_token=str(GenerationToken(generation=2, manifest_sha256="ab" * 32)),
        row_evidence=[{"generation": 2, "identities": []} for _ in result.rows],
        evidence_state=EvidenceState.complete, evidence_inputs={})
    matching.observations.query_readonly = lambda *args, **kwargs: wrong_generation
    with pytest.raises(ValueError, match="generation mismatch"):
        matching(profiles=[profile])
