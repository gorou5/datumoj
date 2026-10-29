"""808 offline audit: selective recall, full-run equivalence, privacy and alerts."""

import json
from contextlib import ExitStack
from dataclasses import asdict
from datetime import date

import pytest

from datumoj.eltiro import rematch
from datumoj.eltiro.alerts import SQLiteAlertStore, ack_alert, list_alerts
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.matching import match_demand
from datumoj.eltiro.models import ConsumerConfig, SeriesProfile
from datumoj.eltiro.testing.fakes import FakeDemandRegistry, FakeProfileStore
from datumoj.eltiro.testing.samples import make_demand, make_profile, make_series
from datumoj.rikolto.index.embed import HashEmbedder
from datumoj.rikolto.testing.fakes import FakeCatalogStore, FakeChunkStore, FakeObservationStore
from datumoj.rikolto.testing.samples import make_chunk, make_dataset, make_document, make_observation

KINDS = ["binding_stale", "definition_changed", "new_candidate", "gap_filled"]


class ReadOnly:
    def __init__(self, backing):
        self.backing, self.calls = backing, []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            assert name in {"describe", "query_readonly", "get_dataset", "iter_chunks", "get_document"}, name
            return getattr(self.backing, name)(*args, **kwargs)
        return call


@pytest.fixture
def system(tmp_path, monkeypatch):
    class System:
        def __init__(self, stack):
            self.root = tmp_path
            self.store = stack.enter_context(SQLiteMatchStore(tmp_path / "incremental"))
            self.full = stack.enter_context(SQLiteMatchStore(tmp_path / "full"))
            self.alerts = stack.enter_context(SQLiteAlertStore(tmp_path / "incremental"))
            self.registry, self.profiles = FakeDemandRegistry(), FakeProfileStore()
            self.consumer = ConsumerConfig.model_validate({
                "id": "example-consumer", "inbox": "inbox", "outbox": "outbox",
                "allowed_sources": ["intl-wdi"], "models": {"consumer": "example-consumer",
                "authorized_sources": ["intl-wdi"]}, "max_active_demands": 20,
            })
            self.options = dict(deterministic=True, vector=False)
            self.calls = []
            self.views = {}

        def register(self, concept="NY.GDP.MKTP.CD", *, id="example-demand", notify=KINDS, **changes):
            request = make_demand(id=id, purpose="PRIVATE PURPOSE never include this text",
                                  indicators=[dict(key="gdp", concept=concept, unit="USD")],
                                  run=dict(notify_on=notify), **changes)
            self.registry.register(request)
            return request

        def publish(self, generation, profiles, *, evidence=True, digest="ab" * 32):
            profiles = [SeriesProfile.model_validate({**p.model_dump(), "generation": generation}) for p in profiles]
            self.profiles.write_generation(generation, profiles)
            observations, catalog, chunks = FakeObservationStore(), FakeCatalogStore(), FakeChunkStore()
            grouped = {}
            if evidence:
                for n, profile in enumerate(profiles):
                    s = profile.series
                    if profile.availability == "observations":
                        row = make_observation(str(n), "1", source_id=s.source_id,
                                               dataset_key=f"{s.source_id}:synthetic", table_key=s.table_key,
                                               indicator=s.indicator, indicator_code=s.indicator_code,
                                               dims=s.fixed_dimensions, unit=profile.unit,
                                               survey=profile.survey or "synthetic", snapshot_sha256=digest,
                                               locator=f"row={n}")
                        grouped.setdefault((s.source_id, s.table_key), []).append(row)
                    elif profile.availability == "catalog_only":
                        catalog.upsert([make_dataset(s.table_key.removeprefix("catalog:"), "synthetic",
                                                      snapshot_sha256=digest, locator="row=1")])
                    else:
                        doc = make_document(f"{s.source_id}:synthetic", digest, kind="pdf")
                        chunk = make_chunk(doc.doc_key, digest, "page=1", "synthetic")
                        chunks.put_document(doc, [chunk])
            for (source, table), rows in grouped.items():
                observations.write_table(source, table, rows)
            self.views = dict(observations=ReadOnly(observations), catalog=ReadOnly(catalog), chunks=ReadOnly(chunks))

        def match(self, request, generation, store):
            return match_demand(request, generation, profiles=self.profiles, consumer=self.consumer,
                                store=store, **self.views, **self.options)

        def initial(self):
            for request in self.registry.list_active():
                assert self.match(request, 1, self.store) == self.match(request, 1, self.full)

        def confirm(self, profile, status="confirmed", generation=1, version=1):
            for store in (self.store, self.full):
                store.set_confirmation("example-demand", version, "gdp", profile.series, generation, status)

        def refresh(self, new=2, old=1, **kwargs):
            return rematch.refresh(new, old, registry=self.registry, profiles=self.profiles,
                                   consumers={self.consumer.id: self.consumer}, store=self.store,
                                   alerts=self.alerts, as_of=kwargs.pop("as_of", date(2026, 3, 1)),
                                   **self.views, **self.options, **kwargs)

        def assert_full(self, generation):
            for request in self.registry.list_active():
                expected = self.match(request, generation, self.full)
                actual = self.match(request, generation, self.store)
                assert actual == expected  # Includes order, reasons, provenance and missing evidence.

    with ExitStack() as stack:
        instance = System(stack)

        def recorded(request, *args, **kwargs):
            instance.calls.append(request.id)
            return match_demand(request, *args, **kwargs)

        monkeypatch.setattr(rematch, "match_demand", recorded)
        yield instance


def test_selective_rematch_carries_unaffected_and_ignores_inactive(system):
    system.register()
    other = system.register("unrelated employment", id="unaffected")
    inactive = system.register(id="inactive")
    system.registry.set_active(inactive.id, inactive.version, False)
    first = make_profile(unit="USD")
    untouched = make_profile(unit="USD", names={"en": "unrelated employment"},
                             series=make_series(table_key="employment", indicator_code="EMP.1"))
    system.publish(1, [first, untouched])
    system.initial()
    before = system.store.for_demand(other.id, 1, 1)
    changed = first.model_copy(update={"methodology": "revised definition"})
    system.publish(2, [changed, untouched])
    counts = system.refresh()
    assert system.calls == ["example-demand"]
    assert (counts.added, counts.modified, counts.deleted, counts.rematched, counts.carried) == (0, 1, 0, 1, 1)
    assert system.store.for_demand(other.id, 1, 2) == [r.model_copy(update={"generation": 2}) for r in before]
    system.assert_full(2)


@pytest.mark.parametrize("status", ["confirmed", "rejected"])
def test_historical_decision_after_deletion_and_renamed_return(system, status):
    system.register("Nominal GDP")  # No official code or preferred source.
    first = make_profile(unit="USD")
    system.publish(1, [first])
    system.initial()
    system.confirm(first, status)
    system.publish(2, [])
    assert system.refresh().rematched == 1
    system.assert_full(2)
    assert system.store.for_demand("example-demand", 1, 2) == []
    renamed = first.model_copy(update={"names": {"en": "Output total"}})
    system.publish(3, [renamed])
    system.calls.clear()
    assert system.refresh(3, 2).rematched == 1
    assert system.calls == ["example-demand"]
    system.assert_full(3)
    records = system.store.for_demand("example-demand", 1, 3)
    assert len(records) == 1 and records[0].confirmation == status
    assert "carried human decision" in records[0].reason


def test_deletion_removes_auto_binding(system):
    system.register()
    system.publish(1, [make_profile(unit="USD")])
    system.initial()
    assert system.store.for_demand("example-demand", 1, 1)[0].auto_bind
    system.publish(2, [])
    assert system.refresh().deleted == 1
    system.assert_full(2)
    assert system.store.for_demand("example-demand", 1, 2) == []
    assert system.alerts.list_alerts() == []


@pytest.mark.parametrize("available", ["none", "catalog_only", "document_only"])
def test_gap_filled_and_first_auto_candidate(system, available):
    system.register()
    old = [] if available == "none" else [make_profile(unit="USD", availability=available, series=make_series(
        table_key="catalog:intl-wdi:synthetic" if available == "catalog_only" else "document:pdf"))]
    system.publish(1, old)
    system.initial()
    observed = make_profile(unit="USD")
    system.publish(2, [*old, observed])
    counts = system.refresh()
    system.assert_full(2)
    kinds = {a.kind for a in system.alerts.list_alerts()}
    assert "gap_filled" in kinds
    assert ("new_candidate" in kinds) == (available == "none")
    assert counts.alerts == len(system.alerts.list_alerts())
    assert not {"binding_stale", "definition_changed"} & kinds
    assert system.refresh().alerts == 0


def test_stale_definition_notify_privacy_ack_and_dedup(system):
    request = system.register("PRIVATE INDICATOR phrasing NY.GDP.MKTP.CD",
                              notify=["binding_stale", "definition_changed"])
    first = make_profile(unit="USD", time_end=date(2023, 12, 31))
    system.publish(1, [first])
    system.initial()
    changed = first.model_copy(update={"unit": "JPY", "survey": "new-survey", "methodology": "new-definition"})
    system.publish(2, [changed, make_profile(unit="USD", series=make_series(table_key="extra"))])
    assert system.refresh().alerts == 2
    system.assert_full(2)
    alerts = system.alerts.list_alerts(consumer=request.consumer, demand=request.id)
    assert {a.kind for a in alerts} == {"binding_stale", "definition_changed"}
    assert set(next(a.details for a in alerts if a.kind == "definition_changed")) == {"unit", "survey", "methodology"}
    serialized = json.dumps([asdict(a) | {"series": a.series.model_dump()} for a in alerts])
    assert request.purpose not in serialized and request.source_case not in serialized
    assert request.indicators[0].concept not in serialized
    assert system.alerts.list_alerts(consumer="unrelated-consumer") == []
    assert system.alerts.list_alerts(demand="unrelated-demand") == []
    assert system.refresh().alerts == 0
    alert_id = alerts[0].alert_id
    root = system.root / "incremental"
    ack_alert(alert_id, data_root=root)
    ack_alert(alert_id, data_root=root)
    assert len(list_alerts(root)) == 1
    assert len(list_alerts(root, include_acked=True)) == 2
    assert any(a.acked for a in list_alerts(root, include_acked=True))
    assert system.refresh().alerts == 0
    with pytest.raises(KeyError):
        system.alerts.ack_alert("unknown")
    assert not (root / "outbox").exists()


def test_better_candidate_only_when_it_improves(system):
    system.register("Nominal GDP NY.GDP.MKTP.CD")
    first = make_profile(unit="USD", series=make_series(indicator_code="OTHER.1"))
    system.publish(1, [first])
    system.initial()
    system.confirm(first)
    better = make_profile(unit="USD", series=make_series(table_key="better"))
    system.publish(2, [first, better])
    system.refresh()
    system.assert_full(2)
    alerts = system.alerts.list_alerts()
    assert [a.kind for a in alerts] == ["new_candidate"]
    assert alerts[0].series == better.series
    system.publish(3, [first, better])
    assert system.refresh(3, 2).alerts == 0
    system.assert_full(3)


@pytest.mark.parametrize("deterministic", [True, False])
def test_vector_recall_and_deleted_top_k_replacement(system, deterministic):
    system.options = dict(deterministic=deterministic, vector=True, embedder=HashEmbedder(), k=1)
    system.register("economic output")
    unrelated = make_profile(unit="USD", names={"en": "rainfall"}, series=make_series(table_key="a"))
    exact = make_profile(unit="USD", names={"en": "economic output"}, series=make_series(table_key="b"))
    system.publish(1, [unrelated])
    system.initial()
    system.publish(2, [unrelated, exact])
    assert system.refresh().rematched == 1
    system.assert_full(2)
    system.publish(3, [unrelated])
    assert system.refresh(3, 2).rematched == 1
    system.assert_full(3)
    assert system.store.for_demand("example-demand", 1, 3)[0].series == unrelated.series


def test_evidence_change_without_profile_change_and_human_decision(system):
    system.register("Nominal GDP")
    first = make_profile(unit="USD")
    system.publish(1, [first], evidence=False)
    system.initial()
    system.publish(2, [first])
    assert system.refresh().rematched == 1
    system.assert_full(2)
    system.confirm(first, generation=2)
    system.publish(3, [first], digest="cd" * 32)
    assert system.refresh(3, 2).rematched == 1
    system.assert_full(3)


@pytest.mark.parametrize("code,auto_bind", [("GDP", False), ("CPI", False), ("NY.GDP.MKTP.CD", True)])
def test_abbreviations_are_not_official_codes(system, code, auto_bind):
    request = system.register(code)
    system.publish(1, [make_profile(unit="USD", names={"en": code}, series=make_series(indicator_code=code))])
    result = system.match(request, 1, system.store)
    assert len(result.records) == 1
    assert result.records[0].auto_bind is auto_bind


def test_vector_only_without_embedder_errors(system):
    request = system.register()
    system.publish(1, [])
    system.options = dict(deterministic=False, vector=True)
    with pytest.raises(ValueError, match="Embedder"):
        system.match(request, 1, system.store)
    with pytest.raises(ValueError, match="Embedder"):
        system.refresh()


def test_carried_decision_requires_an_actual_tier(system):
    request = system.register("economic output")
    selected = make_profile(unit="USD", names={"en": "economic output"}, series=make_series(table_key="selected"))
    excluded = make_profile(unit="USD", names={"en": "unrelated rainfall"}, series=make_series(table_key="excluded"))
    system.publish(1, [selected, excluded])
    system.options = dict(deterministic=False, vector=True, embedder=HashEmbedder(), k=2)
    system.initial()
    system.confirm(excluded)
    system.publish(2, [selected, excluded])
    system.options["k"] = 1
    result = system.match(request, 2, system.store)
    assert len(result.records) == 1 and result.records[0].series == selected.series
    assert result.records[0].tier == "vector"
    system.publish(3, [excluded])
    result = system.match(request, 3, system.store)
    assert result.records[0].confirmation == "confirmed" and result.records[0].tier == "vector"


def test_read_views_never_receive_writes(system):
    system.register()
    system.publish(1, [make_profile(unit="USD")])
    system.initial()
    system.publish(2, [make_profile(unit="USD", methodology="revision")])
    system.refresh()
    assert system.views["observations"].calls
    # ReadOnly fails on *every* method outside the explicit read allowlist.
    assert all(set(v.calls) <= {"describe", "query_readonly", "get_dataset", "iter_chunks", "get_document"}
               for v in system.views.values())


def test_unchanged_vector_results_are_carried_without_embedding(system, monkeypatch):
    embedder = HashEmbedder()
    system.options = dict(deterministic=False, vector=True, embedder=embedder)
    system.register("economic output")
    first = make_profile(unit="USD", names={"en": "economic output"})
    system.publish(1, [first])
    system.initial()
    system.publish(2, [first])
    with monkeypatch.context() as patch:
        def unexpected(*args, **kwargs):
            pytest.fail("unchanged demand was embedded again")
        patch.setattr(embedder, "embed", unexpected)
        assert system.refresh().carried == 1
    assert system.calls == []
    system.assert_full(2)


def test_stale_can_appear_when_profiles_do_not_change(system):
    system.register()
    first = make_profile(unit="USD", frequency="monthly", time_end=date(2025, 1, 31))
    system.publish(1, [first])
    system.initial()
    system.publish(2, [first])
    counts = system.refresh(as_of=date(2025, 3, 1))
    assert counts.rematched == 0 and counts.carried == 1
    assert [a.kind for a in system.alerts.list_alerts()] == ["binding_stale"]
    system.assert_full(2)


def test_history_is_isolated_by_demand_version(system):
    system.register("Nominal GDP")
    first = make_profile(unit="USD")
    system.publish(1, [first])
    system.initial()
    system.confirm(first)
    request = system.register("unrelated rainfall", version=2)
    system.publish(2, [first])
    assert system.refresh().rematched == 1
    system.assert_full(2)
    assert system.store.for_demand(request.id, 2, 2) == []


def test_trusted_configuration_change_forces_rematch(system):
    system.register()
    first = make_profile(unit="USD")
    system.publish(1, [first])
    system.initial()
    system.publish(2, [first])
    system.consumer = system.consumer.model_copy(update={"allowed_sources": ("jp-other",), "models":
        system.consumer.models.model_copy(update={"authorized_sources": ("jp-other",)})})
    assert system.refresh().rematched == 1
    system.assert_full(2)
    assert system.store.for_demand("example-demand", 1, 2) == []


def test_unrelated_changed_vector_candidates_below_top_k_are_not_rematched(system):
    system.options = dict(deterministic=False, vector=True, embedder=HashEmbedder(), k=1)
    system.register("economic output")
    exact = make_profile(unit="USD", names={"en": "economic output"}, series=make_series(table_key="exact"))
    unrelated = make_profile(unit="USD", names={"en": "rainfall"}, series=make_series(table_key="unrelated"))
    system.publish(1, [exact, unrelated])
    system.initial()
    system.publish(2, [exact, unrelated.model_copy(update={"names": {"en": "precipitation"}})])
    assert system.refresh().carried == 1
    assert system.calls == []
    system.assert_full(2)


def test_vector_similarity_selects_without_code_keyword_or_source_overlap(system):
    system.options = dict(deterministic=False, vector=True, embedder=HashEmbedder(), k=1)
    system.register("economy")
    old = make_profile(unit="USD", names={"en": "rainfall"}, series=make_series(table_key="old"))
    added = make_profile(unit="USD", names={"en": "economic"}, series=make_series(table_key="new"))
    system.publish(1, [old])
    system.initial()
    system.publish(2, [old, added])
    assert system.refresh().rematched == 1
    assert system.calls == ["example-demand"]
    system.assert_full(2)
    assert system.store.for_demand("example-demand", 1, 2)[0].series == added.series
