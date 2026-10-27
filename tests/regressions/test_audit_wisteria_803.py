"""803 audit: binding gates, real view evidence, private storage and decisions."""

import pytest

from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.matching import match_demand
from datumoj.eltiro.models import ConsumerConfig, SeriesProfile
from datumoj.eltiro.testing.contract_suites import MatchStoreSuite
from datumoj.eltiro.testing.fakes import FakeProfileStore
from datumoj.eltiro.testing.samples import make_demand, make_profile, make_series
from datumoj.rikolto.index.embed import HashEmbedder
from datumoj.rikolto.testing.fakes import FakeCatalogStore, FakeChunkStore, FakeObservationStore
from datumoj.rikolto.testing.samples import make_observation


class TestPersistentMatchAudit(MatchStoreSuite):
    @pytest.fixture
    def make_match_store(self, tmp_path):
        opened = []

        def make():
            store = SQLiteMatchStore(tmp_path / str(len(opened)))
            opened.append(store)
            return store

        yield make
        for store in opened:
            store.close()


class RecordedView:
    def __init__(self, delegate):
        self.delegate, self.calls = delegate, []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            assert name in {"describe", "query_readonly", "get_dataset", "iter_chunks", "get_document"}
            return getattr(self.delegate, name)(*args, **kwargs)
        return call


@pytest.fixture
def system(tmp_path):
    consumer = ConsumerConfig.model_validate({
        "id": "example-consumer", "inbox": "inbox", "outbox": "outbox", "allowed_sources": ["intl-wdi"],
        "models": {"consumer": "example-consumer", "authorized_sources": ["intl-wdi"]}, "max_active_demands": 3,
    })
    profiles, obs = FakeProfileStore(), FakeObservationStore()
    first = make_profile(unit="USD", survey="survey-one")
    other = make_profile(unit="EUR", survey="survey-two", series=make_series(table_key="second"))
    forbidden = make_profile(unit="USD", series=make_series(source_id="jp-forbidden"))
    missing = make_profile(unit="USD", series=make_series(table_key="missing"))
    all_profiles = [first, other, forbidden, missing]
    for p in all_profiles[:-1]:
        s = p.series
        obs.write_table(s.source_id, s.table_key, [make_observation(
            "synthetic", "1", source_id=s.source_id, dataset_key=f"{s.source_id}:synthetic",
            table_key=s.table_key, survey=p.survey or "synthetic", indicator=s.indicator,
            indicator_code=s.indicator_code, dims=s.fixed_dimensions, unit=p.unit,
            snapshot_sha256="ef" * 32, locator="row=9")])
    views = dict(observations=RecordedView(obs), catalog=RecordedView(FakeCatalogStore()),
                 chunks=RecordedView(FakeChunkStore()))
    with SQLiteMatchStore(tmp_path) as store:
        def run(generation=1, concept="NY.GDP.MKTP.CD", examples=None, **options):
            profiles.write_generation(generation, [SeriesProfile.model_validate({**p.model_dump(), "generation": generation})
                                                   for p in all_profiles])
            demand = make_demand(indicators=[{"key": "gdp", "concept": concept, "unit": "USD",
                                             "examples": examples or {}, "keep_separate_by": ["survey", "unit"]}])
            return match_demand(demand, generation, profiles=profiles, consumer=consumer, store=store, **views, **options)
        yield run, store, all_profiles, views


def test_official_code_binding_source_filter_separation_and_evidence(system):
    run, _, profiles, views = system
    result = run()
    assert len(result.records) == 2
    by_series = {r.series.table_key: r for r in result.records}
    assert by_series["gdp"].auto_bind
    assert set(by_series["gdp"].compatibility.model_dump().values()) == {"same"}
    assert not by_series["second"].auto_bind and by_series["second"].compatibility.unit == "different"
    assert result.missing_evidence == [profiles[-1].series]
    assert all(r.series.source_id == "intl-wdi" and r.evidence[0].source_id == "intl-wdi"
               and r.evidence[0].snapshot_sha256 == "ef" * 32 and r.evidence[0].locator == "row=9"
               for r in result.records)
    assert views["observations"].calls.count("describe") == 3
    assert views["observations"].calls.count("query_readonly") == 2
    assert views["catalog"].calls == views["chunks"].calls == []


@pytest.mark.parametrize("case", ["unknown", "counterexample", "vector"])
def test_unsafe_matching_never_auto_binds(system, case):
    run, _, _, _ = system
    options = {"concept": "Nominal GDP"} if case == "unknown" else {
        "examples": {"negative": ["Nominal GDP"]}} if case == "counterexample" else {
        "deterministic": False, "embedder": HashEmbedder()}
    result = run(**options)
    assert result.records and all(not r.auto_bind and r.confirmation == "pending" for r in result.records)
    assert result.queue == result.records
    if case == "unknown":
        assert all(r.compatibility.methodology == r.compatibility.survey == "unknown" for r in result.records)
    if case == "counterexample":
        assert all("counterexample #1" in r.reason for r in result.records)
    if case == "vector":
        assert all(r.tier == "vector" for r in result.records)


def test_human_decisions_carry_and_reruns_are_identical(system):
    run, store, profiles, _ = system
    first = run()
    assert run() == first
    for p, status in zip(profiles, ("rejected", "confirmed")):
        store.set_confirmation("example-demand", 1, "gdp", p.series, 1, status)
    second = run(generation=2)
    assert [r.confirmation for r in second.records] == ["confirmed", "rejected"]
    assert [r.series for r in second.recommendations] == [profiles[1].series]
    assert not second.records[-1].auto_bind
    assert run(generation=2) == second


def test_tier_switches_and_embedding_models_cannot_mix(system):
    run, _, _, _ = system
    with pytest.raises(ValueError, match="at least one"):
        run(deterministic=False, vector=False)
    result = run(deterministic=False, embedder=HashEmbedder())
    assert result.records
    for embedder in (HashEmbedder(model="other"), HashEmbedder(dim=32)):
        with pytest.raises(ValueError, match="configuration changed"):
            run(deterministic=False, embedder=embedder)
