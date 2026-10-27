"""Implementations inherit a suite and provide its factory fixture (no Rikolto storage).

For example, ``class TestBackend(DemandRegistrySuite)`` supplies ``make_registry``.
Factories return a fresh empty store, so isolation, versioning and failed writes are tested.
"""

import pytest

from datumoj.eltiro.contracts import (
    DemandRegistry,
    MatchStore,
    ModelCache,
    ModelProviderClient,
    ModelRouter,
    ProfileStore,
    ProviderUnavailable,
    RerunCacheMiss,
    RouteInvalid,
)
from datumoj.eltiro.models import DemandSpec, ManualQueueItem, MatchRecord, ModelBudget, RouteDecision, SeriesProfile
from datumoj.eltiro.testing.fakes import FakeModelCache, FakeModelProvider
from datumoj.eltiro.testing.samples import (
    make_consumer,
    make_demand,
    make_license,
    make_match,
    make_model_input,
    make_profile,
    make_route_decision,
    make_routing_context,
    make_routing_policy,
    make_series,
)


def changed(model, **changes):
    return type(model).model_validate({**model.model_dump(by_alias=True), **changes})


class DemandRegistrySuite:
    def test_validate_and_register(self, make_registry):
        registry = make_registry()
        assert isinstance(registry, DemandRegistry)
        demand = make_demand()
        assert registry.validate(demand.model_dump_json(by_alias=True).encode()) == demand
        assert registry.list_active() == []
        assert registry.get(demand.id, 1) is None
        registry.register(demand)
        registry.register(demand)
        assert registry.get(demand.id, 1) == demand
        assert registry.list_active() == [demand]
        assert make_registry().list_active() == []

    def test_versions_and_activation(self, make_registry):
        registry = make_registry()
        one, two = make_demand(), make_demand(version=2)
        registry.register(one)
        registry.register(two, active=False)
        assert registry.list_active() == [one]
        registry.set_active(one.id, 2, True)
        assert registry.list_active() == [two]
        registry.set_active(one.id, 1, False)
        assert registry.list_active() == [two]
        registry.set_active(one.id, 2, False)
        assert registry.list_active() == []
        assert registry.get(one.id, 1) == one
        assert registry.get(one.id, 2) == two
        with pytest.raises(KeyError):
            registry.set_active(one.id, 3, True)
        registry.register(make_demand(id="zzz"))
        registry.register(make_demand(id="aaa"))
        assert [item.id for item in registry.list_active()] == ["aaa", "zzz"]

    def test_immutable_version_and_detached_state(self, make_registry):
        registry, demand = make_registry(), make_demand()
        registry.register(demand)
        with pytest.raises(ValueError):
            registry.register(changed(demand, purpose="changed"))
        assert registry.get(demand.id, 1) == demand
        demand.entities.countries.clear()
        assert registry.get(demand.id, 1).entities.countries == ["JPN"]
        fetched = registry.list_active()[0]
        fetched.entities.countries.clear()
        assert registry.get(demand.id, 1).entities.countries == ["JPN"]
        with pytest.raises(ValueError):
            registry.register(DemandSpec.model_construct(id="bad"))


class ProfileStoreSuite:
    def test_generation_identity_and_replacement(self, make_profile_store):
        store = make_profile_store()
        assert isinstance(store, ProfileStore)
        profile = make_profile()
        assert store.get(1, profile.series) is None
        assert store.list(1) == []
        other = make_profile(series=make_series(fixed_dimensions={"valuation": "constant"}))
        store.write_generation(1, [profile, other])
        assert store.list(1) == [profile, other]
        assert store.get(1, other.series) == other
        for series in (make_series(source_id="intl-oecd"), make_series(table_key="other"),
                       make_series(indicator="other"), make_series(indicator_code="other")):
            assert store.get(1, series) is None
        newer = changed(profile, generation=2)
        store.write_generation(2, [newer])
        store.write_generation(1, [other])
        assert store.list(1) == [other]
        assert store.list(2) == [newer]
        store.write_generation(1, [])
        assert store.list(1) == [] and store.list(2) == [newer]
        assert make_profile_store().list(2) == []

    def test_atomic_validation_and_detached_state(self, make_profile_store):
        store, profile = make_profile_store(), make_profile()
        store.write_generation(1, [profile])
        for invalid in ([profile, profile], [profile, changed(profile, generation=2)],
                        [profile, SeriesProfile.model_construct(generation=1)]):
            with pytest.raises(ValueError):
                store.write_generation(1, invalid)
            assert store.list(1) == [profile]
        with pytest.raises(ValueError):
            store.write_generation(0, [])
        profile.names["en"] = "mutated input"
        assert store.get(1, profile.series).names["en"] == "Nominal GDP"
        store.list(1)[0].names["en"] = "mutated output"
        assert store.get(1, profile.series).names["en"] == "Nominal GDP"


class MatchStoreSuite:
    def test_keys_generations_and_confirmation(self, make_match_store):
        store, record = make_match_store(), make_match()
        assert isinstance(store, MatchStore)
        assert store.for_demand(record.demand_id, 1) == []
        variants = [record, changed(record, demand_version=2), changed(record, indicator_key="gdp_lcu"),
                    changed(record, series=make_series(table_key="other")), changed(record, generation=2)]
        for item in variants:
            store.put(item)
        assert store.for_demand(record.demand_id, 1) == [variants[i] for i in (0, 2, 3, 4)]
        assert store.for_demand(record.demand_id, 1, 2) == [variants[4]]
        assert store.for_demand(record.demand_id, 2) == [variants[1]]
        for state in ("confirmed", "rejected", "pending"):
            store.set_confirmation(record.demand_id, 1, "gdp", record.series, 1, state)
            assert store.for_demand(record.demand_id, 1)[0].confirmation == state
        with pytest.raises(KeyError):
            store.set_confirmation("missing", 1, "gdp", record.series, 1, "confirmed")
        assert make_match_store().for_demand(record.demand_id, 1) == []

    def test_updates_preserve_decisions_and_validate(self, make_match_store):
        store = make_match_store()
        record = make_match(auto_bind=True, compatibility={"methodology": "same", "unit": "same", "survey": "same"})
        store.put(record)
        store.set_confirmation(record.demand_id, 1, "gdp", record.series, 1, "rejected")
        rejected = store.for_demand(record.demand_id, 1)[0]
        assert rejected.confirmation == "rejected" and rejected.auto_bind is False
        store.put(changed(record, reason="updated matcher evidence"))
        updated = store.for_demand(record.demand_id, 1)[0]
        assert updated.confirmation == "rejected" and updated.auto_bind is False
        assert updated.reason == "updated matcher evidence"
        with pytest.raises(ValueError):
            store.set_confirmation(record.demand_id, 1, "gdp", record.series, 1, "automatic")
        with pytest.raises(ValueError):
            store.put(MatchRecord.model_construct(demand_id="bad"))
        assert store.for_demand(record.demand_id, 1) == [updated]
        updated.evidence.clear()
        assert store.for_demand(record.demand_id, 1)[0].evidence


def invoke_planned(router, plan, context, index=0, **changes):
    """Only protocol calls; the implementation under test always comes from make_router."""
    options = {"consumer": context["consumer"], "licenses": context["licenses"],
               "providers": {}, "cache": FakeModelCache(), **changes}
    return router.invoke(plan.run_id, index, **options)


class ModelRouterSuite:
    """806 and other routers inherit ALL hard rules, supplying only make_router().

    Factories create an empty router. Offline provider/cache collaborators expose calls;
    these tests never depend on router implementation details or its private state.
    """

    def test_planning_pins_routes_without_calls(self, make_router):
        router, context = make_router(), make_routing_context()
        assert isinstance(router, ModelRouter)
        plan = router.plan(**context)
        decision = plan.items[0]
        assert isinstance(decision, RouteDecision)
        assert decision.target.provider == "cloud_api" and decision.tier == "high"
        assert decision.target.identity.version == "v1"
        assert decision.policy_sha256 == context["policy"].content_hash
        assert not decision.downgraded and not decision.reasons
        assert router.plan(**context) == plan
        with pytest.raises(RouteInvalid):
            router.plan(**{**context, "policy": make_routing_policy(cloud_provider="claude_plan")})
        with pytest.raises(KeyError):
            invoke_planned(make_router(), plan, context)

    def test_step_and_difficulty_select_their_configured_targets(self, make_router):
        router, context = make_router(), make_routing_context()
        data = context["policy"].model_dump()
        inputs = []
        for step, routes in data["routes"].items():
            for tier, target in routes.items():
                target["identity"]["model"] = f"example-{step}-{tier}"
                inputs.append(make_model_input(id=f"{step}_{tier}", step=step, difficulty=tier))
        context.update(policy=type(context["policy"]).model_validate(data), inputs=inputs)
        plan = router.plan(**context)
        assert len(plan.items) == len(inputs)
        for item, request in zip(plan.items, inputs, strict=True):
            assert item.request == request
            assert item.tier == request.difficulty
            assert item.target == getattr(context["policy"].routes[request.step], request.difficulty)

    @pytest.mark.parametrize("cloud", ["claude_plan", "cloud_api"])
    @pytest.mark.parametrize("outflow,cloud_ok", [
        ("no_outflow", True), ("no_outflow", False), ("derived_only", False),
    ])
    def test_registry_license_blocks_every_cloud_provider(self, make_router, cloud, outflow, cloud_ok):
        router = make_router()
        context = make_routing_context(policy=make_routing_policy(cloud_provider=cloud), licenses={
            "jp-one": make_license(outflow=outflow, cloud_model_ok=cloud_ok),
        })
        plan = router.plan(**context)
        decision = plan.items[0]
        assert isinstance(decision, RouteDecision)
        assert decision.target.provider == "ollama" and decision.tier == "low"
        assert decision.downgraded and decision.reasons
        local, remote = FakeModelProvider(), FakeModelProvider()
        result = invoke_planned(router, plan, context, providers={"ollama": local, cloud: remote})
        result.validate_route(decision)
        assert len(local.calls) == 1 and remote.calls == []

    @pytest.mark.parametrize("side", ["budget", "consumer"])
    @pytest.mark.parametrize("cloud", ["claude_plan", "cloud_api"])
    def test_allow_cloud_is_enforced_for_both_configuration_layers(self, make_router, side, cloud):
        router = make_router()
        context = make_routing_context(policy=make_routing_policy(cloud_provider=cloud))
        context[side] = changed(context[side], allow_cloud=False)
        plan = router.plan(**context)
        assert plan.items[0].target.provider == "ollama"
        remote, local = FakeModelProvider(), FakeModelProvider()
        invoke_planned(router, plan, context, providers={cloud: remote, "ollama": local})
        assert not remote.calls and len(local.calls) == 1

    @pytest.mark.parametrize("side", ["budget", "consumer"])
    @pytest.mark.parametrize("cloud", ["claude_plan", "cloud_api"])
    @pytest.mark.parametrize("max_tier", ["none", "low", "medium", "high"])
    def test_max_tier_caps_both_configuration_layers(self, make_router, side, cloud, max_tier):
        router = make_router()
        context = make_routing_context(policy=make_routing_policy(cloud_provider=cloud))
        context[side] = changed(context[side], max_tier=max_tier)
        plan = router.plan(**context)
        if max_tier == "none":
            assert isinstance(plan.items[0], ManualQueueItem)
            assert plan.items[0].decision.target.provider == "none"
        else:
            assert plan.items[0].tier == max_tier
            assert plan.items[0].downgraded == (max_tier != "high")

    def test_consumer_can_only_tighten_provider_permissions(self, make_router):
        router = make_router()
        context = make_routing_context(consumer=make_consumer(allowed_providers=["none", "ollama"]))
        plan = router.plan(**context)
        assert plan.items[0].target.provider == "ollama"

    @pytest.mark.parametrize("splittable", [True, False])
    def test_mixed_sources_split_before_routing_or_queue_without_calls(self, make_router, splittable):
        router = make_router()
        context = make_routing_context(inputs=[make_model_input(splittable=splittable, parts=[
            {"source_id": "jp-one", "content": "public synthetic part"},
            {"source_id": "jp-two", "content": "restricted synthetic part"},
        ])], licenses={"jp-one": make_license(), "jp-two": make_license(outflow="no_outflow")})
        plan = router.plan(**context)
        remote, local = FakeModelProvider(), FakeModelProvider()
        for index in range(len(plan.items)):
            invoke_planned(router, plan, context, index, providers={"cloud_api": remote, "ollama": local})
        if splittable:
            assert len(plan.items) == 2
            assert {item.target.provider for item in plan.items} == {"cloud_api", "ollama"}
            assert len(remote.calls) == len(local.calls) == 1
            assert [p.source_id for p in remote.calls[0].request.parts] == ["jp-one"]
            assert [p.source_id for p in local.calls[0].request.parts] == ["jp-two"]
        else:
            assert len(plan.items) == 1 and isinstance(plan.items[0], ManualQueueItem)
            assert not remote.calls and not local.calls

    @pytest.mark.parametrize("reason", ["license", "configured_none", "zero_budget", "missing_license", "unauthorized"])
    def test_no_eligible_route_ends_in_none_and_manual_queue(self, make_router, reason):
        router, context = make_router(), make_routing_context()
        if reason == "license":
            context["consumer"] = make_consumer(allowed_providers=["none", "cloud_api", "claude_plan"])
            context["licenses"] = {"jp-one": make_license(outflow="no_outflow")}
        elif reason == "configured_none":
            context["policy"] = make_routing_policy(routes={
                step: {tier: {"provider": "none"} for tier in ("low", "medium", "high")}
                for step in ("match_judge", "concept_note")})
        elif reason == "zero_budget":
            context["budget"] = changed(context["budget"], max_calls_per_run=0)
        elif reason == "missing_license":
            context["licenses"] = {}
        else:
            context["consumer"] = make_consumer(authorized_sources=["jp-two"])
        plan = router.plan(**context)
        item = plan.items[0]
        assert isinstance(item, ManualQueueItem)
        assert item.decision.tier == "none" and item.decision.target.provider == "none"
        assert item.decision.downgraded and item.decision.reasons
        provider = FakeModelProvider()
        assert invoke_planned(router, plan, context, providers={"cloud_api": provider, "ollama": provider}) == item
        assert not provider.calls

    def test_provider_failure_and_retry_never_change_route(self, make_router):
        router, context = make_router(), make_routing_context()
        plan = router.plan(**context)
        remote, local, alternative = FakeModelProvider(), FakeModelProvider(), FakeModelProvider()
        remote.available = False
        providers = {"cloud_api": remote, "ollama": local, "claude_plan": alternative}
        cache = FakeModelCache()
        for _ in range(2):
            with pytest.raises(ProviderUnavailable):
                invoke_planned(router, plan, context, providers=providers, cache=cache)
        assert len(remote.calls) == 2 and not local.calls and not alternative.calls
        assert all(call == plan.items[0] for call in remote.calls)
        assert router.plan(**context) == plan
        remote.available = True
        result = invoke_planned(router, plan, context, providers=providers, cache=cache)
        result.validate_route(plan.items[0])
        assert remote.calls[-1] == plan.items[0]

    def test_budget_exhaustion_queues_remaining_calls_and_counts_failed_attempts(self, make_router):
        for failure in (False, True):
            router = make_router()
            context = make_routing_context(
                budget=ModelBudget(max_tier="high", allow_cloud=True, max_calls_per_run=1),
                inputs=[make_model_input(), make_model_input(id="note", step="concept_note")])
            plan = router.plan(**context)
            provider, cache = FakeModelProvider(), FakeModelCache()
            provider.available = not failure
            if failure:
                with pytest.raises(ProviderUnavailable):
                    invoke_planned(router, plan, context, providers={"cloud_api": provider}, cache=cache)
            else:
                invoke_planned(router, plan, context, providers={"cloud_api": provider}, cache=cache)
            assert router.plan(**context) == plan  # Re-reading a plan must not reset its call count.
            item = invoke_planned(router, plan, context, 1, providers={"cloud_api": provider}, cache=cache)
            assert isinstance(item, ManualQueueItem) and item.reason == "budget_exhausted"
            if failure:
                retry = invoke_planned(router, plan, context, providers={"cloud_api": provider}, cache=cache)
                assert isinstance(retry, ManualQueueItem)
            else:
                # Cached work remains reusable after budget exhaustion.
                invoke_planned(router, plan, context, cache=cache)
            assert len(provider.calls) == 1

    def test_cache_reuse_and_same_generation_rerun_never_invoke_provider(self, make_router):
        router, context = make_router(), make_routing_context()
        plan = router.plan(**context)
        provider, cache = FakeModelProvider(), FakeModelCache()
        result = invoke_planned(router, plan, context, providers={"cloud_api": provider}, cache=cache)
        provider.available = False
        assert invoke_planned(router, plan, context, cache=cache, rerun=True) == result
        assert invoke_planned(router, plan, context, cache=cache) == result
        assert len(provider.calls) == 1
        with pytest.raises(RerunCacheMiss):
            invoke_planned(router, plan, context, providers={"cloud_api": provider}, rerun=True)
        assert len(provider.calls) == 1

    @pytest.mark.parametrize("change", ["consumer", "scope", "generation", "policy", "input", "prompt", "model_version"])
    def test_cache_context_changes_never_reuse_old_results(self, make_router, change):
        router, context = make_router(), make_routing_context()
        old_plan = router.plan(**context)
        cache, provider = FakeModelCache(), FakeModelProvider()
        invoke_planned(router, old_plan, context, providers={"cloud_api": provider}, cache=cache)
        newer = {**context, "run_id": "another-run"}
        if change == "consumer":
            newer["consumer"] = make_consumer(consumer="another-consumer")
        elif change == "scope":
            newer["consumer"] = make_consumer(authorized_sources=["jp-one"])
        elif change == "generation":
            newer["generation_token"] = changed(context["generation_token"], generation=2)
        elif change in ("policy", "model_version"):
            data = context["policy"].model_dump()
            if change == "policy":
                data["routes"]["concept_note"]["low"]["identity"]["version"] = "v2"
            else:
                data["routes"]["match_judge"]["high"]["identity"]["version"] = "v2"
            newer["policy"] = type(context["policy"]).model_validate(data)
        elif change == "input":
            newer["inputs"] = [make_model_input(parts=[{"source_id": "jp-one", "content": "different data"}])]
        else:
            newer["inputs"] = [make_model_input(prompt_version="v2")]
        plan = router.plan(**newer)
        assert plan.items[0].cache_key() != old_plan.items[0].cache_key()
        with pytest.raises(RerunCacheMiss):
            invoke_planned(router, plan, newer, providers={"cloud_api": provider}, cache=cache, rerun=True)
        assert len(provider.calls) == 1

    @pytest.mark.parametrize("change", ["license", "missing_license", "cloud_model_ok", "scope", "consumer", "allow_cloud", "max_tier"])
    def test_current_permissions_revalidated_before_reading_cache(self, make_router, change):
        router, context = make_router(), make_routing_context()
        plan = router.plan(**context)
        cache, provider = FakeModelCache(), FakeModelProvider()
        invoke_planned(router, plan, context, providers={"cloud_api": provider}, cache=cache)
        current = dict(context)
        if change == "license":
            current["licenses"] = {"jp-one": make_license(outflow="no_outflow", cloud_model_ok=True)}
        elif change == "missing_license":
            current["licenses"] = {}
        elif change == "cloud_model_ok":
            current["licenses"] = {"jp-one": make_license(cloud_model_ok=False)}
        elif change == "scope":
            current["consumer"] = make_consumer(authorized_sources=["jp-one"])
        elif change == "consumer":
            current["consumer"] = make_consumer(consumer="another-consumer")
        elif change == "allow_cloud":
            current["consumer"] = make_consumer(allow_cloud=False)
        else:
            current["consumer"] = make_consumer(max_tier="low")

        class UnreadableCache:
            def get(self, key):
                pytest.fail("permissions must be checked before cache access")

        for rerun in (False, True):
            with pytest.raises(RouteInvalid):
                invoke_planned(router, plan, current, cache=UnreadableCache(), rerun=rerun)
        assert len(provider.calls) == 1


class ModelProviderSuite:
    """make_provider returns an offline configured provider with a synthetic response."""

    def test_pinned_model_and_every_field_provenance(self, make_provider):
        provider = make_provider()
        assert isinstance(provider, ModelProviderClient)
        # Fixture must target this synthetic route; no real network in qualification tests.
        decision = make_route_decision()
        result = provider.invoke(decision)
        assert result.fields
        result.validate_route(decision)


class ModelCacheSuite:
    def test_exact_identity_detachment_and_immutable_output(self, make_cache):
        cache = make_cache()
        assert isinstance(cache, ModelCache)
        decision = make_route_decision()
        key = decision.cache_key()
        result = FakeModelProvider().invoke(decision)
        assert cache.get(key) is None
        cache.put(key, result)
        cache.put(key, result)
        assert cache.get(key) == result
        variants = {
            "consumer": "another-consumer", "authorized_sources": ["jp-one"],
            "generation_token": changed(key.generation_token, manifest_sha256="cd" * 32),
            "input_sha256": "cd" * 32, "provider": "claude_plan",
            "identity": changed(key.identity, version="v2"),
            "policy_sha256": "cd" * 32, "prompt_version": "v2",
        }
        for field, value in variants.items():
            assert cache.get(changed(key, **{field: value})) is None
        with pytest.raises(ValueError):
            cache.put(key, FakeModelProvider(value="conflicting result").invoke(decision))
        result.fields.clear()
        assert cache.get(key).fields
        cache.get(key).fields.clear()
        assert cache.get(key).fields
        assert make_cache().get(key) is None
