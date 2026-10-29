"""Synthetic offline retained generations; execution never reads online stores."""

import json
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pyarrow.parquet as pq
import pytest

from datumoj.eltiro import executor
from datumoj.eltiro.executor import ExecutionError, execute_demand
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.models import ConsumerConfig
from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.eltiro.testing.samples import make_demand, make_match, make_series
from datumoj.rikolto.core.models import SnapshotOrder, StoreCopy
from datumoj.rikolto.store.catalog import SqliteCatalogStore
from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.store.observations import DuckDBObservationStore, retain_generation
from datumoj.rikolto.testing.fakes import FakeRegistry
from datumoj.rikolto.testing.samples import T0, make_dataset, make_observation, make_spec, make_state_generation, sha


def observation(value="10", *, source="intl-wdi", table="gdp", year=2020, release="R1", **changes):
    return make_observation(
        changes.pop("obs_id", f"obs-{year}"), value, source_id=source, table_key=table,
        dataset_key=f"{source}:{table}", indicator=changes.pop("indicator", "Nominal GDP"),
        indicator_code=changes.pop("indicator_code", "NY.GDP.MKTP.CD"), dims=changes.pop("dims", {"valuation": "current"}),
        unit=changes.pop("unit", "USD"), area_code=changes.pop("area_code", "JPN"),
        time_start=date(year, 1, 1), time_end=date(year, 12, 31), release_id=release,
        released_at=changes.pop("released_at", date(2026, 1, 1)),
        snapshot_sha256=changes.pop("snapshot_sha256", sha(f"{source}-{table}-{release}".encode())), **changes)


def seed(root, batches, *, metadata=None):
    """Each batch is one exact table publication, in persisted SnapshotOrder."""
    root.mkdir(parents=True, exist_ok=True)
    store = DuckDBObservationStore(root)
    datasets = {}
    for seq, batch in enumerate(batches, 1):
        first = batch[0]
        datasets[first.dataset_key] = make_dataset(first.dataset_key, first.indicator, extras={
            "area_code_scheme": "ISO3166-1-alpha3", "language": "en",
            "scale": {"multiplier": 1, "evidence": "Synthetic source units"}, **(metadata or {}),
        })
        store.write_table(first.source_id, first.table_key, batch, SnapshotOrder(fetched_at=T0, seq=seq))
    catalog = SqliteCatalogStore(root / "catalog.sqlite")
    catalog.upsert(list(datasets.values()))
    catalog.close()
    SqliteChunkStore(root / "chunks.sqlite").close()
    sources = sorted({batch[0].source_id for batch in batches})
    return store, FakeRegistry([make_spec(s) for s in sources])


def publish(store, generation=1):
    paths = [store.root / "catalog.sqlite", store.root / "chunks.sqlite",
             *sorted((store.root / "parquet").glob("*/*.parquet"))]
    copies = []
    for path in paths:
        digest = sha(path.read_bytes())
        copies.append(StoreCopy(name=path.relative_to(store.root).as_posix(), key=f"copies/sha256/{digest}",
                                sha256=digest, size_bytes=path.stat().st_size,
                                format="sqlite" if path.suffix == ".sqlite" else "parquet"))
    generation = make_state_generation(generation).model_copy(update={"stores": copies})
    return retain_generation(store.root, generation.model_dump_json().encode(), store.root)


def consumer(sources=("intl-wdi",), **changes):
    return ConsumerConfig.model_validate({
        "id": "example-consumer", "inbox": "consumer/inbox", "outbox": "consumer/outbox",
        "allowed_sources": sources, "models": {"consumer": "example-consumer", "authorized_sources": sorted(sources)},
        "max_active_demands": 10, **changes,
    })


def run(root, registry, demand=None, **kwargs):
    sources = tuple(s.id for s in registry.all())
    return execute_demand(demand or make_demand(indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD", "unit": "USD"}]),
                          data_root=root, registry=registry, consumer=consumer(sources), **kwargs)


def outputs(root, record):
    path = root / "eltiro/runs" / record["run_id"]
    return (pq.read_table(path / "data.parquet").to_pylist(), json.loads((path / "citations.json").read_bytes()),
            json.loads((path / "gaps.json").read_bytes()))


def test_end_to_end_numeric_values_come_only_from_checked_sql_and_no_write_interfaces(tmp_path, monkeypatch):
    store, registry = seed(tmp_path, [[observation("123.45")]])
    token = publish(store)
    real_open = executor.open_generation_views
    calls = []

    @contextmanager
    def opened(root, fixed):
        with real_open(root, fixed) as views:
            query = views.observations.query_readonly

            def tracked(sql, *args, **kwargs):
                assert kwargs["generation_token"] == token
                calls.append(sql)
                result = query(sql, *args, **kwargs)
                # Poison the metadata/provenance numeric values. Results must use SQL rows.
                return result.model_copy(update={"provenance": [r.model_copy(update={"value": Decimal(999999)})
                                                                 for r in result.provenance]})

            monkeypatch.setattr(views.observations, "query_readonly", tracked)
            for name in ("write_table", "import_release"):
                monkeypatch.setattr(views.observations, name, lambda *a, **k: pytest.fail("Rikolto write"))
            monkeypatch.setattr(views.catalog, "upsert", lambda *a, **k: pytest.fail("Rikolto write"))
            monkeypatch.setattr(views.chunks, "put_document", lambda *a, **k: pytest.fail("Rikolto write"))
            yield views

    monkeypatch.setattr(executor, "open_generation_views", opened)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    record = run(tmp_path, registry)
    rows, citations, gaps = outputs(tmp_path, record)
    assert [r["value"] for r in rows] == [Decimal("123.45")]
    assert not gaps and calls and record["generation_token"] == token
    assert citations[0]["citation"]["inputs"][0] == {
        "source_id": "intl-wdi", "table_key": "gdp", "obs_id": "obs-2020",
        "snapshot_sha256": observation().snapshot_sha256, "release_id": "R1"}
    assert all(p.read_bytes() == contents for p, contents in before.items())
    assert not (tmp_path / "consumer").exists()


def test_full_series_identity_filters_same_obs_id_across_tables_indicators_and_dimensions(tmp_path):
    batch = [observation("10", obs_id="same"), observation("999", obs_id="other-dim", dims={"valuation": "constant"}),
             observation("888", obs_id="other-code", indicator_code="OTHER")]
    store, registry = seed(tmp_path, [batch, [observation("777", table="other", obs_id="same", indicator="Other")]])
    publish(store)
    # Carry a human rejection for each otherwise lexical candidate.
    with SQLiteMatchStore(tmp_path) as matches:
        matches.put(make_match(series=make_series(table_key="other", indicator="Other"), confirmation="rejected"))
        matches.put(make_match(series=make_series(fixed_dimensions={"valuation": "constant"}), confirmation="rejected"))
    record = run(tmp_path, registry)
    rows, citations, _ = outputs(tmp_path, record)
    assert [r["value"] for r in rows] == [Decimal(10)]
    assert all(c["citation"]["inputs"][0]["table_key"] == "gdp" for c in citations)


def test_pending_and_rejected_never_compute_confirmed_can_compute(tmp_path):
    store, registry = seed(tmp_path, [[observation()]])
    publish(store)
    demand = make_demand()  # lexical match alone needs human confirmation
    first = run(tmp_path, registry, demand)
    assert outputs(tmp_path, first)[0] == []
    assert "pending_confirmation" in {g["reason"] for g in outputs(tmp_path, first)[2]}
    with SQLiteMatchStore(tmp_path) as matches:
        matches.set_confirmation(demand.id, 1, "gdp", make_series(), 1, "confirmed")
    second = run(tmp_path, registry, demand)
    assert len(outputs(tmp_path, second)[0]) == 1
    with SQLiteMatchStore(tmp_path) as matches:
        matches.set_confirmation(demand.id, 1, "gdp", make_series(), 1, "rejected")
    third = run(tmp_path, registry, demand)
    assert outputs(tmp_path, third)[0] == []


def test_all_releases_exact_same_day_dedup_and_revision_against_first(tmp_path):
    batches = [[observation(value, release=release)] for value, release in [("10", "R1"), ("12", "R2"),
                                                                                        ("12", "R3"), ("15", "R4")]]
    store, registry = seed(tmp_path, batches)
    token = publish(store)
    demand = make_demand(revisions="all", indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD", "unit": "USD"}],
                         align={"keys": ["country", "year", "release"], "require_same_unit": True},
                         operations=[{"id": "revision", "op": "revision_diff", "input": "gdp"}])
    record = run(tmp_path, registry, demand, generation_token=token)
    rows, citations, _ = outputs(tmp_path, record)
    assert [(r["release_id"], r["value"]) for r in rows if r["symbol"] == "revision"] == [
        ("R1", Decimal(0)), ("R2", Decimal(2)), ("R4", Decimal(5))]
    assert {obs["release_id"] for obs in citations[-1]["citation"]["inputs"]} == {"R1", "R4"}
    assert citations[-1]["citation"]["operation_chain"][0]["op"] == "revision_diff"


@pytest.mark.parametrize("metadata,changes,reason", [
    ({"scale": None}, {}, "unconfirmed_scale"),
    ({"area_code_scheme": None}, {"area_code": "ZZZ"}, "unknown_area_scheme"),
    ({"area_code_scheme": "ISO3166-1-alpha3"}, {"area_code": "ZZZ"}, "no_matching_observations"),
    ({}, {"granularity": "month"}, "frequency_mismatch"),
])
def test_safe_gaps(tmp_path, metadata, changes, reason):
    store, registry = seed(tmp_path, [[observation(**changes)]], metadata=metadata)
    publish(store)
    demand = make_demand(indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD", "unit": "USD"}],
                         operations=[{"id": "units", "op": "rescale", "input": "gdp", "target_scale": 1}])
    rows, _, gaps = outputs(tmp_path, run(tmp_path, registry, demand))
    assert all(r["value"] is None for r in rows if r["symbol"] == "units")
    assert reason in {g["reason"] for g in gaps}


def test_explicit_rescale_and_source_spread_with_full_citations(tmp_path):
    store, registry = seed(tmp_path, [[observation("10")], [observation("20", source="intl-oecd")]])
    publish(store)
    demand = make_demand(indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD", "unit": "USD", "min_sources": 2}],
                         operations=[{"id": "units", "op": "rescale", "input": "gdp", "target_scale": 1},
                                     {"id": "spread", "op": "source_spread", "input": "units"}])
    record = run(tmp_path, registry, demand)
    rows, citations, gaps = outputs(tmp_path, record)
    assert not gaps
    spreads = [r for r in rows if r["symbol"] == "spread"]
    assert {r["value"] for r in spreads} == {Decimal(-5), Decimal(5)}
    assert {r["spread_range"] for r in spreads} == {Decimal(10)}
    assert len(citations[-1]["citation"]["inputs"]) == 2
    assert all(c["steps"][0]["details"]["evidence"] == "Synthetic source units" for c in citations if c["steps"])


def test_units_not_aligned_and_source_notes_retained(tmp_path):
    store, registry = seed(tmp_path, [[observation(None, note="秘匿")], [observation("20", source="intl-oecd", unit="EUR")]])
    publish(store)
    demand = make_demand(indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD"}],
                         operations=[{"id": "spread", "op": "source_spread", "input": "gdp"}])
    with SQLiteMatchStore(tmp_path) as matches:
        for source in ("intl-wdi", "intl-oecd"):
            matches.put(make_match(series=make_series(source_id=source), confirmation="confirmed",
                                   evidence=[{"profile_field": "series", "source_id": source,
                                              "snapshot_sha256": "ab" * 32, "locator": "row=1"}]))
    rows, _, gaps = outputs(tmp_path, run(tmp_path, registry, demand))
    assert any(r["note"] == "秘匿" and r["value"] is None for r in rows)
    assert all(r["value"] is None for r in rows if r["symbol"] == "spread")
    assert "unit_mismatch" in {g["reason"] for g in gaps}


def test_temporal_operations_calendar_windows_forward_references_and_binary_chains(tmp_path):
    store, registry = seed(tmp_path, [[observation(str(v), year=y) for y, v in [(2018, 1), (2019, 2), (2020, 4), (2022, 8)]]])
    publish(store)
    operations = [
        {"id": "twice", "op": "diff", "left": "delta", "right": "delta", "sign": "plus"},
        {"id": "delta", "op": "diff_lag", "input": "gdp", "lag": 1},
        {"id": "growth", "op": "growth_rate", "input": "gdp", "lag": 1},
        {"id": "average", "op": "moving_average", "input": "gdp", "window": 2},
        {"id": "corr", "op": "lead_lag_corr", "left": "gdp", "right": "gdp", "lag": 1, "window": 2},
        {"id": "mirror", "op": "mirror_diff", "left": "gdp", "right": "average"},
        {"id": "residual", "op": "residual", "left": "gdp", "right": "average"},
        {"id": "ratio", "op": "ratio", "left": "gdp", "right": "delta"},
    ]
    demand = make_demand(indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD", "unit": "USD"}], operations=operations)
    rows, citations, _gaps = outputs(tmp_path, run(tmp_path, registry, demand))
    values = {(r["symbol"], r["period"].year): r["value"] for r in rows}
    assert values["delta", 2020] == 2 and values["growth", 2020] == 100
    assert values["average", 2020] == 3 and values["corr", 2020] == 1
    assert values["twice", 2020] == 4 and values["ratio", 2020] == 2
    assert values["mirror", 2020] == values["residual", 2020] == 1
    assert values["delta", 2022] is None  # Do not bridge the absent 2021.
    assert values["average", 2022] is None and values["corr", 2022] is None
    row = next(r for r in rows if r["symbol"] == "twice" and r["period"].year == 2020)
    citation = next(c["citation"] for c in citations if c["citation_id"] == row["citation_id"])
    assert {i["obs_id"] for i in citation["inputs"]} == {"obs-2019", "obs-2020"}
    assert [o["id"] for o in citation["operation_chain"]] == ["delta", "twice"]


def test_latest_revision_diff_still_uses_first_release_as_baseline(tmp_path):
    store, registry = seed(tmp_path, [[observation("10")], [observation("12", release="R2")],
                                    [observation("12", release="R3")]])
    publish(store)
    demand = make_demand(indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD", "unit": "USD"}],
                         operations=[{"id": "revision", "op": "revision_diff", "input": "gdp"}])
    rows, citations, _ = outputs(tmp_path, run(tmp_path, registry, demand))
    assert [(r["symbol"], r["release_id"], r["value"]) for r in rows] == [
        ("gdp", "R3", Decimal(12)), ("revision", "R3", Decimal(2))]
    assert {i["release_id"] for i in citations[-1]["citation"]["inputs"]} == {"R1", "R3"}


@pytest.mark.parametrize("code,scheme", [("JP", "ISO3166-1-alpha2"), ("392", "ISO3166-1-numeric")])
def test_declared_iso_schemes_align_to_demand_alpha3(tmp_path, code, scheme):
    store, registry = seed(tmp_path, [[observation(area_code=code)]], metadata={"area_code_scheme": scheme})
    publish(store)
    rows, _, gaps = outputs(tmp_path, run(tmp_path, registry))
    assert len(rows) == 1 and rows[0]["country"] == "JPN" and not gaps


def test_model_suggested_scale_is_not_source_confirmation(tmp_path):
    store, registry = seed(tmp_path, [[observation()]])
    publish(store)
    demand = make_demand(indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD", "unit": "USD"}],
                         operations=[{"id": "units", "op": "rescale", "input": "gdp", "target_scale": 1}])
    run(tmp_path, registry, demand)
    with SQLiteProfileStore(tmp_path) as profiles:
        profiles.write_generation(1, [p.model_copy(update={"model_generated_fields": ["scale"]}) for p in profiles.list(1)])
    rows, _, gaps = outputs(tmp_path, run(tmp_path, registry, demand))
    assert next(r["value"] for r in rows if r["symbol"] == "units") is None
    assert "unconfirmed_scale" in {g["reason"] for g in gaps}


def test_large_window_fails_without_dropping_required_citations(tmp_path):
    # Contract conflict: MovingAverage.window <= 1200 but CitationEntry.inputs <= 256.
    # Preserve fail-closed behavior while the task owner resolves the contract.
    store, registry = seed(tmp_path, [[observation("1", year=y) for y in range(1700, 1957)]])
    publish(store)
    demand = make_demand(period={"from": "1700", "to": "1956", "frequency": "annual"},
                         indicators=[{"key": "gdp", "concept": "NY.GDP.MKTP.CD", "unit": "USD"}],
                         operations=[{"id": "average", "op": "moving_average", "input": "gdp", "window": 257}])
    with pytest.raises(ExecutionError):
        run(tmp_path, registry, demand)
    assert not (tmp_path / "eltiro/runs").exists()
