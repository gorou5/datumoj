"""807 audit: all providers are synthetic and record every invocation."""

from contextlib import ExitStack
from datetime import date
import json
import sqlite3

import pytest

from datumoj.eltiro.alerts import SQLiteAlertStore
from datumoj.eltiro.concept_notes import generate_concept_notes, list_concept_notes
from datumoj.eltiro.contracts import ProviderUnavailable, RerunCacheMiss, RouteInvalid
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.matching import match_demand
from datumoj.eltiro.model_cache import SQLiteModelCache
from datumoj.eltiro.models import ConsumerConfig, ConsumersConfig, RoutingPolicy, content_hash
from datumoj.eltiro.rematch import refresh
from datumoj.eltiro.routing import SQLiteModelRouter
from datumoj.eltiro.testing.fakes import FakeDemandRegistry, FakeModelProvider, FakeProfileStore
from datumoj.eltiro.testing.samples import make_demand, make_license, make_profile, make_routing_policy, make_series
from datumoj.rikolto.core.models import GenerationToken
from datumoj.rikolto.index.embed import HashEmbedder
from datumoj.rikolto.testing.fakes import FakeCatalogStore, FakeChunkStore, FakeObservationStore
from datumoj.rikolto.testing.samples import make_observation


@pytest.fixture
def system(tmp_path):
    class System:
        def __init__(self, stack):
            self.root, self.stack = tmp_path, stack
            self.consumer = ConsumerConfig.model_validate({
                "id": "example-consumer", "inbox": "inbox", "outbox": "outbox",
                "allowed_sources": ["intl-wdi", "jp-two"], "max_active_demands": 10,
                "max_calls_per_run": 20, "models": {"consumer": "example-consumer",
                "authorized_sources": ["intl-wdi", "jp-two"], "max_tier": "high",
                "allow_cloud": True, "allowed_providers": ["none", "ollama", "cloud_api"]}})
            self.demand = make_demand(purpose="PRIVATE PURPOSE", source_case="PRIVATE OTHER CONTEXT",
                                     models={"max_tier": "high", "allow_cloud": True, "max_calls_per_run": 20})
            self.registry, self.profiles = FakeDemandRegistry(), FakeProfileStore()
            self.registry.register(self.demand)
            self.store = stack.enter_context(SQLiteMatchStore(tmp_path))
            self.alerts = stack.enter_context(SQLiteAlertStore(tmp_path))
            self.router = stack.enter_context(SQLiteModelRouter(tmp_path, consumers=ConsumersConfig(
                consumers=[self.consumer])))
            self.cache = stack.enter_context(SQLiteModelCache(tmp_path))
            self.cloud = FakeModelProvider('{"verdict":"relevant","reason":"Same concept."}')
            self.local = FakeModelProvider(self.cloud.value)
            self.policy = make_routing_policy()
            self.licenses = {s: make_license() for s in self.consumer.allowed_sources}
            self.publish(1, [make_profile(unit="USD")])

        def publish(self, generation, profiles):
            self.generation = generation
            self.profiles.write_generation(generation, [p.model_copy(update={"generation": generation}) for p in profiles])
            self.observations = FakeObservationStore()
            grouped = {}
            for n, p in enumerate(profiles):
                s = p.series
                row = make_observation(str(n), "1", source_id=s.source_id, table_key=s.table_key,
                                       dataset_key=f"{s.source_id}:synthetic", indicator=s.indicator,
                                       indicator_code=s.indicator_code, dims=s.fixed_dimensions,
                                       unit=p.unit, survey=p.survey or "synthetic",
                                       snapshot_sha256="ab" * 32, locator=f"row={n}")
                grouped.setdefault((s.source_id, s.table_key), []).append(row)
            for (source, table), rows in grouped.items():
                self.observations.write_table(source, table, rows)
            self.views = dict(observations=self.observations, catalog=FakeCatalogStore(), chunks=FakeChunkStore())

        @property
        def options(self):
            return dict(router=self.router, cache=self.cache, licenses=self.licenses, policy=self.policy,
                        generation_token=GenerationToken(generation=self.generation, manifest_sha256="ab" * 32),
                        providers={"cloud_api": self.cloud, "ollama": self.local})

        def match(self, **changes):
            return match_demand(self.demand, self.generation, **{
                "profiles": self.profiles, "consumer": self.consumer, "store": self.store,
                "vector": False, "model": True, **self.views, **self.options, **changes})

        def notes(self):
            return generate_concept_notes(self.generation, demand=self.demand, profiles=self.profiles,
                                          consumer=self.consumer, data_root=self.root, **self.options)

        def refresh(self):
            return refresh(self.generation, self.generation - 1, registry=self.registry, profiles=self.profiles,
                           consumers={self.consumer.id: self.consumer}, store=self.store, alerts=self.alerts,
                           as_of=date(2026, 3, 1), model=True, vector=False, **self.views, **self.options)

    with ExitStack() as stack:
        yield System(stack)


def test_default_disabled_is_identical_and_ignores_model_dependencies(system):
    expected = system.match(model=False)
    actual = system.match(model=False, policy=None, generation_token=None, cache=None, router=None,
                          licenses=None, providers=None)
    assert expected == actual
    assert not system.cloud.calls and not system.local.calls
    assert expected.records[0].tier == "deterministic"
    assert not (system.root / "eltiro/model_tasks.sqlite").exists()


def test_only_existing_candidates_no_candidates_no_model_call(system):
    system.demand = make_demand(indicators=[{"key": "gdp", "concept": "unrelated employment"}],
                                models=system.demand.models)
    assert system.match().records == []
    assert not system.cloud.calls and not system.local.calls


def test_relevant_never_binds_and_prompt_contains_only_current_indicator(system):
    system.demand = make_demand(purpose="PRIVATE PURPOSE", source_case="PRIVATE OTHER CONTEXT",
                                models=system.demand.models, indicators=[{
                                    "key": "gdp", "concept": "Nominal GDP", "unit": "USD",
                                    "examples": {"positive": ["positive GDP"], "negative": ["not inflation"]}}])
    result = system.match()
    assert len(result.queue) == 1 and not result.records[0].auto_bind
    record = result.records[0]
    assert record.tier == "model" and record.confirmation == "pending"
    audit = json.loads(record.reason)
    assert audit["label"] == "模型生成" and audit["model_use"] == {
        "provider": "cloud_api", "model": "example-cloud", "version": "v1", "tier": "high"}
    decision = system.cloud.calls[0]
    from datumoj.eltiro.prompts import build_prompt
    prompt = build_prompt(decision.request)
    assert "positive GDP" in prompt and "not inflation" in prompt
    assert "PRIVATE PURPOSE" not in prompt and "PRIVATE OTHER CONTEXT" not in prompt
    assert "another-consumer" not in prompt and "other-demand" not in prompt
    assert len(decision.request.parts) == 1
    assert len(result.records) == len(system.cloud.calls) == 1


@pytest.mark.parametrize("status", ["rejected", "confirmed"])
def test_human_decisions_are_not_judged_or_overwritten(system, status):
    system.match(model=False)
    profile = system.profiles.list(1)[0]
    system.store.set_confirmation(system.demand.id, 1, "gdp", profile.series, 1, status)
    system.publish(2, [profile])
    result = system.match()
    assert result.records[0].confirmation == status
    assert not system.cloud.calls and not system.local.calls
    if status == "rejected":
        assert not result.recommendations and not result.records[0].auto_bind


def test_different_units_and_methodologies_remain_distinct(system):
    first = make_profile(unit="USD", methodology="definition A", survey="survey A")
    second = make_profile(unit="JPY", methodology="definition B", survey="survey B",
                          series=make_series(fixed_dimensions={"valuation": "constant"}))
    system.publish(1, [first, second])
    result = system.match()
    assert len(result.records) == len(result.queue) == 2
    assert {r.series.fixed_dimensions["valuation"] for r in result.records} == {"current", "constant"}
    assert any(r.compatibility.unit == "different" for r in result.records)
    assert all(not r.auto_bind for r in result.records)


@pytest.mark.parametrize("restricted", [make_license(outflow="no_outflow"), make_license(cloud_model_ok=False)])
def test_mixed_sources_split_before_cloud_prompt(system, restricted):
    first = make_profile(unit="USD", names={"en": "Nominal GDP PRIVATE SOURCE"})
    second = make_profile(unit="USD", names={"en": "Nominal GDP PUBLIC SOURCE"},
                          series=make_series(source_id="jp-two"))
    system.publish(1, [first, second])
    system.licenses["intl-wdi"] = restricted
    result = system.match()
    assert len(result.records) == 2 and len(system.local.calls) == len(system.cloud.calls) == 1
    assert "PRIVATE SOURCE" not in system.cloud.calls[0].request.model_dump_json()
    assert system.local.calls[0].request.parts[0].source_id == "intl-wdi"
    assert system.cloud.calls[0].request.parts[0].source_id == "jp-two"


@pytest.mark.parametrize("output", ["not JSON", '{"verdict":"relevant"}',
    '{"verdict":"relevant","reason":"Line one. Line two."}',
    '{"verdict":"relevant","reason":["invalid"]}',
    '{"verdict":["relevant"],"reason":"Invalid verdict."}',
    '{"verdict":"relevant","reason":"Fine.","new_series":"invented"}'])
def test_invalid_structure_is_uncertain_without_retry(system, output):
    system.cloud.value = output
    result = system.match()
    assert result.records[0].confidence == 0.5
    assert '"verdict": "uncertain"' in result.records[0].reason
    assert len(system.cloud.calls) == 1 and not system.local.calls


@pytest.mark.parametrize("cap", [0, 1])
def test_budget_exhaustion_and_none_preserve_candidates_in_queue(system, cap):
    system.demand = system.demand.model_copy(update={"models": system.demand.models.model_copy(
        update={"max_calls_per_run": cap})})
    system.publish(1, [make_profile(unit="USD"), make_profile(unit="USD", series=make_series(table_key="second"))])
    result = system.match()
    assert len(result.records) == len(result.queue) == 2
    assert len(system.cloud.calls) == cap and not system.local.calls
    assert len(system.router.list_manual()) == 2 - cap
    manual = [r for r in result.records if r.tier == "deterministic"]
    assert len(manual) == 2 - cap and all(r.confidence == 0.8 for r in manual)
    assert system.match() == result  # Budget-queued items need no nonexistent model cache.


def test_rerun_requires_cache_and_current_license_before_returning_saved_matches(system):
    result = system.match()
    assert system.match() == result and len(system.cloud.calls) == 1
    system.licenses["intl-wdi"] = make_license(cloud_model_ok=False)
    with pytest.raises(RouteInvalid):
        system.match()
    system.licenses["intl-wdi"] = make_license()
    with sqlite3.connect(system.cache.path) as db:
        db.execute("DELETE FROM outputs")
    with pytest.raises(RerunCacheMiss):
        system.match()
    assert len(system.cloud.calls) == 1 and not system.local.calls


def test_provider_failure_never_falls_back_on_retry(system):
    system.cloud.available = False
    with pytest.raises(ProviderUnavailable):
        system.match()
    system.cloud.available = True
    with pytest.raises(RerunCacheMiss):
        system.match()
    assert len(system.cloud.calls) == 1 and not system.local.calls
    assert system.store.for_demand(system.demand.id, 1) == []


def test_incremental_model_matches_full_and_retry_checks_cache(system):
    system.match()
    profile = system.profiles.list(1)[0]
    system.publish(2, [profile.model_copy(update={"methodology": "changed methodology"})])
    counts = system.refresh()
    assert counts.rematched == 1 and counts.carried == 0
    incremental = system.store.for_demand(system.demand.id, 1, 2)
    full_store = system.stack.enter_context(SQLiteMatchStore(system.root / "full"))
    expected = system.match(store=full_store)
    assert incremental == expected.records
    assert len(system.cloud.calls) == 2  # Full rerun reads generation-2 cache.
    assert system.refresh().rematched == 0
    with sqlite3.connect(system.cache.path) as db:
        db.execute("DELETE FROM outputs")
    with pytest.raises(RerunCacheMiss):
        system.refresh()
    assert len(system.cloud.calls) == 2


def test_concept_notes_separate_labeled_scoped_and_used_only_in_model_input(system):
    profile = system.profiles.list(1)[0]
    system.cloud.value = '{"note":"Synthetic GDP explanation."}'
    notes = system.notes()
    assert len(notes) == 1
    note = notes[0]
    assert note.label == "模型生成" and note.model_use.version == "v1"
    assert note.model_use.provider == "cloud_api"
    assert note.profile_sha256 == content_hash(profile.model_dump(mode="json", exclude={"generation"}))
    assert note.cache_key.generation_token == system.options["generation_token"]
    assert list_concept_notes(1, data_root=system.root, consumer=system.consumer.id) == notes
    assert list_concept_notes(1, data_root=system.root, consumer="another-consumer") == []
    assert system.profiles.list(1) == [profile]
    assert system.notes() == notes and len(system.cloud.calls) == 1
    system.cloud.value = '{"verdict":"relevant","reason":"Similar concept."}'
    system.match()
    assert "Synthetic GDP explanation." in system.cloud.calls[-1].request.parts[0].content
    other = system.stack.enter_context(SQLiteMatchStore(system.root / "deterministic"))
    det = system.match(model=False, store=other)
    assert det.records[0].tier == "deterministic" and det.records[0].confidence == 0.8
    assert len(system.cloud.calls) == 2


def test_source_description_and_none_do_not_generate_notes(system):
    system.publish(1, [make_profile(descriptions={"en": "Source explanation"})])
    assert system.notes() == [] and not system.cloud.calls
    system.publish(2, [make_profile()])
    system.policy = RoutingPolicy.model_validate({"routes": {step: {tier: {"provider": "none"}
        for tier in ("low", "medium", "high")} for step in ("match_judge", "concept_note")}})
    assert system.notes() == [] and list_concept_notes(2, data_root=system.root) == []
    assert not system.cloud.calls and not system.local.calls


def test_vector_model_switches_and_generation_validation(system):
    result = system.match(deterministic=False, vector=True, embedder=HashEmbedder())
    assert len(result.records) == 1 and result.records[0].tier == "model"
    assert not result.records[0].auto_bind
    with pytest.raises(ValueError, match="generation"):
        system.match(generation_token=GenerationToken(generation=2, manifest_sha256="ab" * 32))


@pytest.mark.parametrize("vector", [False, True])
def test_unchanged_generation_model_refresh_equals_full_with_optional_vectors(system, vector):
    options = {"vector": vector, "embedder": HashEmbedder()} if vector else {}
    system.match(**options)
    system.publish(2, system.profiles.list(1))
    counts = refresh(2, 1, registry=system.registry, profiles=system.profiles,
                     consumers={system.consumer.id: system.consumer}, store=system.store,
                     alerts=system.alerts, as_of=date(2026, 3, 1), model=True,
                     **system.views, **system.options, **{"vector": False, **options})
    assert counts.modified == counts.added == counts.deleted == 0
    assert counts.rematched == 1  # A new generation has a new 806 cache identity.
    full = system.stack.enter_context(SQLiteMatchStore(system.root / "full"))
    assert system.store.for_demand(system.demand.id, 1, 2) == system.match(store=full, **options).records
    assert len(system.cloud.calls) == 2


def test_same_generation_human_rejection_is_not_sent_to_model(system):
    from datumoj.eltiro.testing.samples import make_match
    system.store.put(make_match(confirmation="rejected"))
    result = system.match()
    assert result.records[0].confirmation == "rejected"
    assert not result.recommendations and not system.cloud.calls and not system.local.calls


def test_default_none_budget_and_routes_keep_803_results_exact(system):
    system.demand = make_demand()
    none = {tier: {"provider": "none"} for tier in ("low", "medium", "high")}
    system.policy = RoutingPolicy(routes={"match_judge": none, "concept_note": none})
    plain = system.match(model=False)
    original = system.stack.enter_context(SQLiteMatchStore(system.root / "original"))
    # Omit every 807 argument; compare every field, queue and missing-evidence state.
    default = match_demand(system.demand, 1, profiles=system.profiles, consumer=system.consumer,
                           store=original, vector=False, **system.views)
    assert plain == default and plain.queue == default.queue
    assert not system.cloud.calls and not system.local.calls


def test_concept_note_content_obeys_license_and_bad_hash_never_enters_judgment(system):
    system.licenses["intl-wdi"] = make_license(outflow="no_outflow")
    system.local.value = '{"note":"Private generated concept."}'
    notes = system.notes()
    assert len(notes) == 1 and not system.cloud.calls
    assert notes[0].model_use.provider == "ollama"
    assert notes[0].label == "模型生成"
    system.publish(2, [make_profile(unit="USD", names={"en": "Nominal GDP revised"})])
    system.match()
    assert "Private generated concept" not in system.local.calls[-1].request.parts[0].content
    assert not system.cloud.calls


def test_none_model_tier_never_keeps_automatic_binding(system):
    system.demand = make_demand(indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD", "unit": "USD"}])
    result = system.match()
    assert len(result.queue) == 1 and result.records[0].tier == "deterministic"
    assert result.records[0].confidence == 0.95 and not result.records[0].auto_bind
    assert not system.cloud.calls and not system.local.calls


def test_each_indicator_prompt_excludes_other_indicators_and_registered_demands(system):
    system.registry.register(make_demand(id="other-demand", purpose="OTHER DEMAND SECRET",
                                         consumer="another-consumer", indicators=[{
                                             "key": "gdp", "concept": "OTHER CONSUMER SECRET"}]))
    system.demand = make_demand(models=system.demand.models, indicators=[
        {"key": "gdp", "concept": "NY.GDP.MKTP.CD", "examples": {"positive": ["FIRST ONLY EXAMPLE"]}},
        {"key": "employment", "concept": "EMP.1", "examples": {"negative": ["SECOND ONLY EXAMPLE"]}},
    ])
    system.publish(1, [make_profile(names={"en": "Gross output"}), make_profile(
        names={"en": "Workforce"}, series=make_series(table_key="employment", indicator_code="EMP.1"))])
    result = system.match()
    assert len(result.records) == len(system.cloud.calls) == 2
    for decision in system.cloud.calls:
        content = decision.request.parts[0].content
        assert "OTHER DEMAND SECRET" not in content and "OTHER CONSUMER SECRET" not in content
        payload = json.loads(content)
        if payload["indicator"]["concept"] == "NY.GDP.MKTP.CD":
            assert "FIRST ONLY EXAMPLE" in content and "SECOND ONLY EXAMPLE" not in content
        else:
            assert "SECOND ONLY EXAMPLE" in content and "FIRST ONLY EXAMPLE" not in content
