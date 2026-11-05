"""WISTERIA checkpoint m2 (903), offline part: two generations, rematch, alerts, gaps, model tiers.

Same recorded inputs as m1. Generation 1 holds only Eurostat; generation 2 adds the
recorded WDI GDP. The #002 existing-data demand then gains candidates by itself:
``refresh`` rematches it, raises ``gap_filled`` and the gap report changes.

Model tiers use a synthetic local provider (``FakeModelProvider``); no model is called
for real and no network is used. The real registry licenses (cloud_model_ok false,
no_outflow) decide routing: a cloud route is refused and the candidate stays with a
human, while a local route returns a judgment labelled as model-generated.
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
import yaml

from datumoj.eltiro.alerts import SQLiteAlertStore
from datumoj.eltiro.gaps import gap_report
from datumoj.eltiro.intake import IntakeService, load_consumers
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.matching import match_demand
from datumoj.eltiro.models import RoutingPolicy
from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.eltiro.profiles import generate_profiles
from datumoj.eltiro.registry_store import SQLiteDemandRegistry
from datumoj.eltiro.rematch import refresh
from datumoj.eltiro.routing import SQLiteModelRouter
from datumoj.eltiro.testing.fakes import FakeModelCache, FakeModelProvider
from datumoj.rikolto.core.models import GenerationToken
from datumoj.rikolto.core.registry import load_registry
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.wiring import open_generation_views
from tests.integration.test_wisteria_m1 import CONSUMER, REGISTERED, ROOT, _existing_part, _parsed, _publish

DEMAND = "three-gdp-reconciliation"
AS_OF = date(2026, 11, 5)


def _views_profiles(root, token, generation, registry, previous=None):
    with open_generation_views(root, token) as views, SQLiteProfileStore(root) as profiles:
        assert GenerationToken.parse(views.generation_token).generation == generation
        return generate_profiles(generation, observations=views.observations, catalog=views.catalog,
                                 chunks=views.chunks, registry=registry, store=profiles,
                                 previous_generation=previous)


def _consumer_config(root, *, max_tier="low"):
    (root / "eltiro").mkdir(exist_ok=True)
    (root / "eltiro" / "consumers.yaml").write_text(yaml.safe_dump({"consumers": [{
        "id": CONSUMER, "inbox": "consumer/inbox", "outbox": "consumer/outbox",
        "allowed_sources": list(REGISTERED), "accept_outflow": ["raw_ok", "derived_only"],
        "models": {"consumer": CONSUMER, "authorized_sources": list(REGISTERED), "max_tier": max_tier,
                   "allow_cloud": True, "allowed_providers": ["none", "ollama", "cloud_api"]},
        "max_active_demands": 10, "max_calls_per_run": 200}]}), encoding="utf-8")


@pytest.fixture(scope="module")
def grown(tmp_path_factory):
    def forbidden(*args, **kwargs):
        pytest.fail("checkpoint m2 must never access the network")

    root = tmp_path_factory.mktemp("wisteria-m2").resolve()
    registry_all = load_registry(ROOT / "sources")
    specs = {sid: registry_all.get(sid) for sid in REGISTERED}
    registry = FakeRegistry(list(specs.values()))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
        patch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
        parsed, _ = _parsed(specs)
        eurostat = [item for item in parsed if item[0] == "eu-eurostat"]
        wdi = [item for item in parsed if item[0] == "intl-wdi"]
        token1 = _publish(root, eurostat)
        profiles1 = _views_profiles(root, token1, 1, registry)
        _consumer_config(root)
        inbox = root / "consumer" / "inbox"
        inbox.mkdir(parents=True)
        (inbox / f"{DEMAND}.yaml").write_bytes(_existing_part(f"{DEMAND}.yaml"))
        with SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands:
            assert IntakeService(root, demands, registry).scan() == {"accepted": 1, "rejected": 0}
            consumer = IntakeService(root, demands, registry).consumer(CONSUMER)
            demand = demands.get(DEMAND, 1)
            with (open_generation_views(root, token1) as views, SQLiteProfileStore(root) as profiles,
                  SQLiteMatchStore(root) as matches):
                first = match_demand(demand, 1, profiles=profiles, consumer=consumer,
                                     observations=views.observations, catalog=views.catalog, chunks=views.chunks,
                                     store=matches, vector=False)
            gaps1 = _gaps(root, demand, 1, registry)
            # Generation 2: the store grows by the recorded WDI GDP tables.
            token2 = _publish(root, wdi, generation=2)
            profiles2 = _views_profiles(root, token2, 2, registry, previous=1)
            consumers = {c.id: c for c in load_consumers(root).consumers}
            with (open_generation_views(root, token2) as views, SQLiteProfileStore(root) as profiles,
                  SQLiteMatchStore(root) as matches, SQLiteAlertStore(root) as alerts):
                counts = refresh(2, 1, registry=demands, profiles=profiles, consumers=consumers, store=matches,
                                 alerts=alerts, observations=views.observations, catalog=views.catalog,
                                 chunks=views.chunks, as_of=AS_OF, vector=False, model=False)
                second = matches.for_demand(DEMAND, 1, 2)
                raised = alerts.list_alerts()
                again = refresh(2, 1, registry=demands, profiles=profiles, consumers=consumers, store=matches,
                                alerts=alerts, observations=views.observations, catalog=views.catalog,
                                chunks=views.chunks, as_of=AS_OF, vector=False, model=False)
                raised_again = alerts.list_alerts()
            gaps2 = _gaps(root, demand, 2, registry)
        yield {"root": root, "registry": registry, "specs": specs, "tokens": (token1, token2),
               "profiles": (profiles1, profiles2), "first": first, "second": second, "counts": counts,
               "again": again, "alerts": raised, "alerts_again": raised_again, "gaps": (gaps1, gaps2)}


def _gaps(root, demand, generation, registry):
    consumers = {c.id: c for c in load_consumers(root).consumers}
    with SQLiteProfileStore(root) as profiles, SQLiteMatchStore(root) as matches:
        report = gap_report(demand, generation, consumer=consumers[CONSUMER], matches=matches,
                            profiles=profiles, registry=registry, data_dir=root)
    return {item["key"]: sorted(g["kind"] for g in item["gaps"]) for item in report["indicators"]}


def test_generation_one_has_no_usable_candidate_and_reports_the_gap(grown):
    profiles1, _ = grown["profiles"]
    assert {p.series.source_id for p in profiles1} == {"eu-eurostat"}
    assert [r for r in grown["first"].records if r.series.source_id == "intl-wdi"] == []
    assert "missing" in grown["gaps"][0]["gdp_usd"] and "missing" in grown["gaps"][0]["gdp_lcu"]


def test_growth_rematches_raises_gap_filled_once_and_updates_gaps(grown):
    counts = grown["counts"]
    _, profiles2 = grown["profiles"]
    wdi_keys = {p.series.indicator_code for p in profiles2 if p.series.source_id == "intl-wdi"}
    assert wdi_keys == {"NY.GDP.MKTP.CD", "NY.GDP.MKTP.CN"}
    assert counts.added == 2 and counts.deleted == 0 and counts.rematched == 1
    assert {(r.indicator_key, r.series.indicator_code) for r in grown["second"]
            if r.series.source_id == "intl-wdi"} >= {("gdp_usd", "NY.GDP.MKTP.CD"), ("gdp_lcu", "NY.GDP.MKTP.CN")}
    # The demand subscribes to gap_filled: observations now exist where there were none.
    kinds = {a.kind for a in grown["alerts"]}
    assert kinds == {"gap_filled"} and len(grown["alerts"]) == counts.alerts > 0
    assert all(a.generation == 2 and a.demand == DEMAND for a in grown["alerts"])
    # An interrupted/retried refresh reuses the saved run and never duplicates alerts.
    assert grown["again"].alerts == 0 and len(grown["alerts_again"]) == len(grown["alerts"])
    assert "missing" in grown["gaps"][0]["gdp_usd"]
    assert "missing" not in grown["gaps"][1]["gdp_usd"]
    assert "pending_confirmation" in grown["gaps"][1]["gdp_usd"]


def _model_run(grown, policy, tmp_path):
    root = grown["root"]
    _, token2 = grown["tokens"]
    _, profiles2 = grown["profiles"]
    consumers = load_consumers(root)
    consumer = next(c for c in consumers.consumers if c.id == CONSUMER)
    licenses = {sid: spec.license for sid, spec in grown["specs"].items()}  # Real registry licenses.
    provider_local = FakeModelProvider('{"verdict":"relevant","reason":"Same current-price GDP concept."}')
    provider_cloud = FakeModelProvider('{"verdict":"relevant","reason":"Should never be called."}')
    with (SQLiteDemandRegistry(root / "eltiro" / "demands.sqlite3") as demands,
          SQLiteModelRouter(tmp_path, consumers=consumers) as router,
          open_generation_views(root, token2) as views, SQLiteProfileStore(root) as profiles):
        base = demands.get(DEMAND, 1)
        demand = type(base).model_validate(base.model_dump(by_alias=True) | {
            "version": 2, "models": {"max_tier": "high", "allow_cloud": True, "max_calls_per_run": 20}})
        store = SQLiteMatchStore(tmp_path)
        try:
            result = match_demand(
                demand, 2, profiles=profiles, consumer=consumer, observations=views.observations,
                catalog=views.catalog, chunks=views.chunks, store=store, vector=False, model=True, router=router,
                cache=FakeModelCache(), licenses=licenses, policy=policy,
                generation_token=GenerationToken.parse(views.generation_token),
                providers={"ollama": provider_local, "cloud_api": provider_cloud})
        finally:
            store.close()
    assert {p.generation for p in profiles2} == {2}
    return result, provider_local, provider_cloud


LOCAL = {"provider": "ollama", "identity": {"model": "synthetic-local", "version": "v1"}}
CLOUD = {"provider": "cloud_api", "identity": {"model": "synthetic-cloud", "version": "v1"},
         "credential_env": "SYNTHETIC_MODEL_API_KEY"}


def _policy(low, medium, high):
    routes = {step: {"low": low, "medium": medium, "high": high} for step in ("match_judge", "concept_note")}
    return RoutingPolicy.model_validate({"routes": routes})


def test_cloud_route_is_refused_by_registry_licenses_and_downgrades_to_local(grown, tmp_path):
    _consumer_config(grown["root"], max_tier="high")
    result, local, cloud = _model_run(grown, _policy(LOCAL, LOCAL, CLOUD), tmp_path)
    # Profiles lack methodology/survey → "high"; its cloud route is blocked by the real
    # licenses (cloud_model_ok false, no_outflow), so the router steps down to local medium.
    assert cloud.calls == [] and local.calls
    assert all(call.downgraded and call.tier == "medium" for call in local.calls)
    assert all({"cloud_model_forbidden", "no_outflow"} <= set(call.reasons) for call in local.calls)
    assert all(not r.auto_bind for r in result.records)


def test_no_eligible_route_keeps_candidates_with_a_human(grown, tmp_path):
    _consumer_config(grown["root"], max_tier="high")
    result, local, cloud = _model_run(grown, _policy(CLOUD, CLOUD, CLOUD), tmp_path)
    assert cloud.calls == [] and local.calls == []
    assert result.records and all(r.tier == "deterministic" and not r.auto_bind for r in result.records)


def test_local_route_returns_labelled_model_judgment_without_binding(grown, tmp_path):
    _consumer_config(grown["root"], max_tier="high")
    result, local, cloud = _model_run(grown, _policy(LOCAL, LOCAL, LOCAL), tmp_path)
    assert cloud.calls == [] and len(local.calls) == len(result.records) > 0
    assert all(not call.downgraded and call.tier == "high" for call in local.calls)
    judged = [r for r in result.records if r.tier == "model"]
    assert judged and all(not r.auto_bind and r.confirmation == "pending" for r in judged)
    audit = json.loads(judged[0].reason)
    assert audit["label"] == "模型生成"
    assert audit["model_use"]["provider"] == "ollama" and audit["model_use"]["model"] == "synthetic-local"
