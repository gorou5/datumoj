"""Implementations inherit a suite and provide its factory fixture (no Rikolto storage).

For example, ``class TestBackend(DemandRegistrySuite)`` supplies ``make_registry``.
Factories return a fresh empty store, so isolation, versioning and failed writes are tested.
"""

import pytest

from datumoj.eltiro.contracts import DemandRegistry, MatchStore, ProfileStore
from datumoj.eltiro.models import DemandSpec, MatchRecord, SeriesProfile
from datumoj.eltiro.testing.samples import make_demand, make_match, make_profile, make_series


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
