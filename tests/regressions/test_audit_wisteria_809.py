"""809 audit: independent gap rules, source provenance and host/consumer isolation."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from datumoj.eltiro.gaps import aggregate_gaps, gap_report
from datumoj.eltiro.models import ConsumerConfig, load_demand
from datumoj.eltiro.testing.fakes import FakeDemandRegistry, FakeMatchStore, FakeProfileStore
from datumoj.eltiro.testing.samples import make_demand, make_match, make_profile, make_series
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.testing.samples import make_spec


def consumer(identifier="example-consumer", allowed=("intl-wdi", "intl-oecd"), **changes):
    return ConsumerConfig.model_validate({
        "id": identifier, "inbox": f"{identifier}/inbox", "outbox": f"{identifier}/outbox",
        "allowed_sources": allowed, "models": {"consumer": identifier, "authorized_sources": sorted(allowed)},
        "max_active_demands": 5, **changes,
    })


class ReadOnly:
    def __init__(self, backing, allowed):
        self.backing, self.allowed, self.calls = backing, allowed, []

    def __getattr__(self, name):
        assert name in self.allowed, f"Unexpected store access: {name}"

        def read(*args, **kwargs):
            self.calls.append(name)
            return getattr(self.backing, name)(*args, **kwargs)
        return read


@pytest.fixture
def system(tmp_path):
    profiles, matches, demands = FakeProfileStore(), FakeMatchStore(), FakeDemandRegistry()
    registry = FakeRegistry([make_spec(source) for source in ("intl-wdi", "intl-oecd", "jp-private")])
    options = {"profiles": ReadOnly(profiles, {"get"}), "matches": ReadOnly(matches, {"for_demand"}),
               "registry": ReadOnly(registry, {"all", "get"}), "data_dir": tmp_path}
    all_profiles = {}

    def seed(request, *, key="gdp", source="intl-wdi", availability="observations",
             status="pending", auto=False, generation=1, dimensions=None):
        series = make_series(source_id=source, table_key=f"table-{len(all_profiles)}",
                             fixed_dimensions=dimensions or {})
        record = make_match(demand_id=request.id, demand_version=request.version, indicator_key=key,
                            series=series, confirmation=status, auto_bind=auto, generation=generation,
                            compatibility={"unit": "same", "survey": "same", "methodology": "same"},
                            reason="DO NOT COPY THIS MATCH REASON",
                            evidence=[{"profile_field": "series", "source_id": source,
                                       "snapshot_sha256": "ab" * 32, "locator": "row=1"}])
        profile = make_profile(series=series, generation=generation, availability=availability,
                               descriptions={"en": "DO NOT COPY THIS PROFILE TEXT"})
        all_profiles[(generation, str(series))] = profile
        profiles.write_generation(generation, [p for p in all_profiles.values() if p.generation == generation])
        matches.put(record)
        return record

    def run(request=None, config=None, generation=1):
        return gap_report(request or make_demand(), generation, consumer=config or consumer(), **options)

    return SimpleNamespace(seed=seed, run=run, profiles=profiles, matches=matches, demands=demands,
                           options=options, root=tmp_path)


@pytest.mark.parametrize("candidates,expected", [
    ([], {"missing", "below_min_sources"}),
    ([("catalog_only", "pending", False)], {"catalog_only", "below_min_sources"}),
    ([("document_only", "pending", False)], {"document_only", "below_min_sources"}),
    ([("catalog_only", "pending", False), ("document_only", "confirmed", False)],
     {"catalog_only", "document_only", "below_min_sources"}),
    ([("observations", "pending", False)], {"pending_confirmation", "below_min_sources"}),
    ([("observations", "rejected", False)], {"rejected_only", "below_min_sources"}),
    ([("catalog_only", "rejected", False)], {"catalog_only", "rejected_only", "below_min_sources"}),
    ([("observations", "confirmed", False)], set()),
    ([("observations", "pending", True)], set()),
    ([("observations", "rejected", False), ("observations", "pending", False)],
     {"pending_confirmation", "below_min_sources"}),
    ([("observations", "confirmed", False), ("observations", "pending", False)], set()),
    ([("observations", "confirmed", False), ("catalog_only", "pending", False)], set()),
    ([("observations", "rejected", False), ("document_only", "pending", False)], {"below_min_sources"}),
])
def test_gap_kinds_are_independent_and_absent_when_not_applicable(system, candidates, expected):
    request = make_demand()
    for availability, status, auto in candidates:
        system.seed(request, availability=availability, status=status, auto=auto)
    indicator = system.run(request)["indicators"][0]
    assert {gap["kind"] for gap in indicator["gaps"]} == expected
    assert all(gap["reason"] for gap in indicator["gaps"])


def test_minimum_counts_distinct_sources_not_series_or_fixed_dimensions(system):
    request = make_demand(indicators=[{"key": "gdp", "concept": "GDP", "min_sources": 2}])
    system.seed(request, status="confirmed", dimensions={"release": "first"})
    system.seed(request, auto=True, dimensions={"release": "second"})
    indicator = system.run(request)["indicators"][0]
    assert indicator["bound_sources"] == ["intl-wdi"]
    assert [gap["kind"] for gap in indicator["gaps"]] == ["below_min_sources"]
    system.seed(request, source="intl-oecd", status="confirmed")
    assert system.run(request)["indicators"][0]["gaps"] == []


def test_possible_sources_are_exactly_evidenced_and_consumer_content_is_private(system):
    request = make_demand(purpose="PRIVATE DEMAND ORIGINAL", indicators=[{
        "key": "gdp", "concept": "GDP", "preferred_sources": ["intl-planned", "jp-private", "intl-wdi"],
    }])
    other = make_demand(id="other-demand", consumer="other-consumer", purpose="OTHER PRIVATE PURPOSE")
    system.seed(request, availability="catalog_only")
    system.seed(request, source="intl-oecd", availability="document_only")
    system.seed(request, source="jp-private", auto=True)
    system.seed(other, source="jp-private", status="confirmed")
    report = system.run(request)
    assert report["indicators"][0]["possible_sources"] == [
        {"source_id": "intl-oecd", "reasons": ["document_only"]},
        {"source_id": "intl-planned", "reasons": ["preferred_unregistered"]},
        {"source_id": "intl-wdi", "reasons": ["catalog_only"]},
    ]
    path = system.root / "eltiro/gaps/example-demand-v1-g1.json"
    assert json.loads(path.read_text()) == report
    for secret in ("other-demand", "other-consumer", "OTHER PRIVATE PURPOSE", "PRIVATE DEMAND ORIGINAL",
                   "jp-private", "DO NOT COPY"):
        assert secret not in path.read_text()
    assert report["indicators"][0]["bound_sources"] == []


def test_host_aggregation_only_active_demands_and_no_outbox_writes_or_store_writes(system):
    first = make_demand(indicators=[{"key": "gdp", "concept": "GDP"}])
    second = make_demand(id="second-demand", consumer="second-consumer",
                         indicators=[{"key": "gdp", "concept": "GDP"}])
    inactive = make_demand(id="inactive-demand")
    system.demands.register(first)
    system.demands.register(second)
    system.demands.register(inactive, active=False)
    configs = {config.id: config for config in (consumer(), consumer("second-consumer", ("jp-private",)))}
    for config in configs.values():
        outbox = system.root / config.outbox
        outbox.mkdir(parents=True)
        (outbox / "sentinel").write_text("unchanged")
    before = (system.matches.for_demand(first.id, 1), system.profiles.list(1))
    report = aggregate_gaps(1, demands=ReadOnly(system.demands, {"list_active"}), consumers=configs, **system.options)
    assert report["active_demands"] == 2
    assert len(report["by_concept"]) == 1
    assert len(report["by_concept"][0]["demands"]) == 2
    assert {item["source_id"] for item in report["by_source"]} == {"jp-private", "intl-oecd", "intl-wdi"}
    assert all(row["reasons"] == ["registered_not_allowed"]
               for item in report["by_source"] for row in item["demands"])
    assert "inactive-demand" not in json.dumps(report)
    assert before == (system.matches.for_demand(first.id, 1), system.profiles.list(1))
    assert sorted(str(p.relative_to(system.root)) for p in system.root.rglob("*") if p.is_file()) == [
        "eltiro/reports/gaps-g1.json", "eltiro/reports/gaps-g1.md",
        "example-consumer/outbox/sentinel", "second-consumer/outbox/sentinel",
    ]
    assert json.loads((system.root / "eltiro/reports/gaps-g1.json").read_text()) == report
    assert "registered_not_allowed" in (system.root / "eltiro/reports/gaps-g1.md").read_text()
    assert all((system.root / config.outbox / "sentinel").read_text() == "unchanged" for config in configs.values())


def test_generation_and_demand_version_are_isolated(system):
    first = make_demand()
    system.seed(first, status="confirmed")
    assert system.run(first)["indicators"][0]["gaps"] == []
    for request, generation in ((first, 2), (make_demand(version=2), 1)):
        assert {gap["kind"] for gap in system.run(request, generation=generation)["indicators"][0]["gaps"]} == {
            "missing", "below_min_sources",
        }


def test_two_sample_demands_on_synthetic_candidates(system):
    directory = Path("docs/plans/0.4-wisteria-samples")
    gdp = load_demand(directory / "three-gdp-reconciliation.yaml")
    greece = load_demand(directory / "greece-deficit-revision-chain.yaml")
    config = consumer(gdp.consumer, ("intl-wdi", "eu-eurostat"))
    # Two USD series still provide only one source. Planned IMF/OECD remain absent.
    system.seed(gdp, key="gdp_usd", status="confirmed")
    system.seed(gdp, key="gdp_usd", auto=True)
    system.seed(gdp, key="gdp_lcu", availability="catalog_only")
    system.seed(greece, key="deficit_pct_gdp", source="eu-eurostat")
    system.seed(greece, key="debt_eur", source="eu-eurostat", availability="document_only")
    system.seed(greece, key="deficit_eur", source="eu-eurostat", status="rejected")
    # Replace the source read view with the synthetic installed registry for these samples.
    options = {**system.options, "registry": ReadOnly(FakeRegistry([
        make_spec("intl-wdi"), make_spec("eu-eurostat"),
    ]), {"all", "get"})}
    gdp_report = gap_report(gdp, 1, consumer=config, **options)
    greece_report = gap_report(greece, 1, consumer=config, **options)
    assert [{gap["kind"] for gap in item["gaps"]} for item in gdp_report["indicators"]] == [
        {"below_min_sources"}, {"catalog_only", "below_min_sources"},
    ]
    assert [{gap["kind"] for gap in item["gaps"]} for item in greece_report["indicators"]] == [
        {"pending_confirmation", "below_min_sources"}, {"document_only", "below_min_sources"},
        {"rejected_only", "below_min_sources"},
    ]
    assert gdp_report["indicators"][0]["bound_sources"] == ["intl-wdi"]
    assert {s["source_id"] for s in gdp_report["indicators"][0]["possible_sources"]} == {
        "intl-imf-weo", "intl-oecd",
    }
    assert greece_report["indicators"][1]["possible_sources"] == [
        {"source_id": "eu-eurostat", "reasons": ["document_only"]},
    ]
    system.demands.register(gdp)
    system.demands.register(greece)
    aggregate = aggregate_gaps(1, demands=ReadOnly(system.demands, {"list_active"}),
                               consumers={config.id: config}, **options)
    assert len(aggregate["by_concept"]) == 5
    assert {item["source_id"] for item in aggregate["by_source"]} == {
        "eu-eurostat", "intl-imf-weo", "intl-oecd", "intl-wdi",
    }
