"""Wisteria 804 offline audit; re-run the focused contracts plus boundary attacks."""

from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

from datumoj.eltiro import executor
from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.models import load_demand
from datumoj.eltiro.testing.samples import make_match, make_series
from datumoj.rikolto.core.models import GenerationToken
from tests.eltiro.test_areas import (
    test_crosswalk_complete_bijective_and_leading_zeroes,
    test_scheme_detection_never_guesses_unknown_mixed_or_conflicting_codes,
)
from tests.eltiro.test_executor import (
    consumer,
    observation,
    outputs,
    publish,
    run,
    seed,
    test_all_releases_exact_same_day_dedup_and_revision_against_first,
    test_declared_iso_schemes_align_to_demand_alpha3,
    test_end_to_end_numeric_values_come_only_from_checked_sql_and_no_write_interfaces,
    test_explicit_rescale_and_source_spread_with_full_citations,
    test_full_series_identity_filters_same_obs_id_across_tables_indicators_and_dimensions,
    test_large_window_fails_without_dropping_required_citations,
    test_latest_revision_diff_still_uses_first_release_as_baseline,
    test_model_suggested_scale_is_not_source_confirmation,
    test_pending_and_rejected_never_compute_confirmed_can_compute,
    test_safe_gaps,
    test_temporal_operations_calendar_windows_forward_references_and_binary_chains,
    test_units_not_aligned_and_source_notes_retained,
)
from tests.eltiro.test_operations import (
    test_every_operation_and_missing,
    test_zero_denominators_unconfirmed_scale_and_spread_details,
)
from tests.eltiro.test_runs import test_atomic_failure_removes_staging_and_preserves_other_runs


def test_new_generation_during_run_does_not_mix_views(tmp_path, monkeypatch):
    store, registry = seed(tmp_path, [[observation("10")]])
    old = publish(store)
    real_open = executor.open_generation_views
    tokens = []

    @contextmanager
    def opened(root, token):
        with real_open(root, token) as views:
            query = views.observations.query_readonly
            published = False

            def tracked(sql, *args, **kwargs):
                nonlocal published
                tokens.append(kwargs["generation_token"])
                if not published:
                    store.write_table("intl-wdi", "gdp", [observation("777", release="R2")])
                    publish(store, 2)
                    published = True
                return query(sql, *args, **kwargs)

            monkeypatch.setattr(views.observations, "query_readonly", tracked)
            yield views

    monkeypatch.setattr(executor, "open_generation_views", opened)
    record = run(tmp_path, registry)
    assert record["generation"] == 1 and set(tokens) == {old}
    assert outputs(tmp_path, record)[0][0]["value"] == Decimal(10)


@pytest.mark.parametrize("failure", ["invalid", "missing", "modified", "wrong_returned_token", "no_identity", "multiple_identities", "final_validation"])
def test_token_and_identity_failure_never_fallback_or_publish(tmp_path, monkeypatch, failure):
    store, registry = seed(tmp_path, [[observation("10")]])
    token = publish(store)
    if failure == "invalid":
        token = "INVALID TOKEN PRIVATE CONTENT"
    elif failure == "missing":
        token = str(GenerationToken(generation=99, manifest_sha256="a" * 64))
    elif failure == "modified":
        path = tmp_path / ".observation-generations/1/data/catalog.sqlite"
        with path.open("ab") as stream:
            stream.write(b"synthetic mutation")
    else:
        real_open = executor.open_generation_views

        @contextmanager
        def opened(root, token):
            with real_open(root, token) as views:
                query = views.observations.query_readonly
                checkpoints = 0

                def broken(sql, *args, **kwargs):
                    nonlocal checkpoints
                    result = query(sql, *args, **kwargs)
                    if failure == "wrong_returned_token":
                        return result.model_copy(update={"generation_token": str(GenerationToken(generation=2, manifest_sha256="a" * 64))})
                    if sql == "SELECT 1 WHERE false":
                        checkpoints += 1
                        if failure == "final_validation" and checkpoints == 2:
                            raise ValueError("PRIVATE VALUES")
                    if "json_array_length" in sql and result.rows:
                        evidence = result.row_evidence[0]
                        if failure == "no_identity":
                            result.row_evidence[0] = evidence.model_copy(update={"identities": []})
                        elif failure == "multiple_identities":
                            result.row_evidence[0] = evidence.model_copy(update={"identities": evidence.identities * 2})
                    return result

                monkeypatch.setattr(views.observations, "query_readonly", broken)
                yield views

        monkeypatch.setattr(executor, "open_generation_views", opened)
    with pytest.raises(executor.ExecutionError) as exc:
        run(tmp_path, registry, generation_token=token)
    assert "PRIVATE" not in str(exc.value)
    runs = tmp_path / "eltiro/runs"
    assert not runs.exists() or list(runs.iterdir()) == []


def _confirm(root, demand, source, table, indicator, *, code):
    series = make_series(source_id=source, table_key=table, indicator=indicator, indicator_code=code)
    with SQLiteMatchStore(root) as matches:
        matches.put(make_match(demand_id=demand.id, indicator_key=table, series=series, confirmation="confirmed",
                               evidence=[{"profile_field": "series", "source_id": source,
                                          "snapshot_sha256": "ab" * 32, "locator": "synthetic row"}]))


def sample_result(root, name):
    demand = load_demand(Path("docs/plans/0.4-wisteria-samples") / name)
    batches = []
    is_gdp = demand.revisions == "latest"
    sources = ("intl-imf-weo", "intl-oecd", "intl-wdi") if is_gdp else ("eu-eurostat", "intl-imf-weo")
    confirmations = []
    for n, source in enumerate(sources):
        for indicator in demand.indicators:
            if not is_gdp and source == "intl-imf-weo" and indicator.key != "deficit_pct_gdp":
                continue
            unit = indicator.unit or "JPY"
            for release in (["R1"] if is_gdp else ["R1", "R2"]):
                rows = []
                for year in (2008, 2009):
                    value = (Decimal(100 + 10 * n + (year - 2008) * 20) if is_gdp else
                             Decimal("-5") if indicator.key == "deficit_eur" else
                             Decimal(100 + (year - 2008) * 20) if indicator.key == "debt_eur" else
                             Decimal("-3") if release == "R1" else Decimal("-5"))
                    rows.append(observation(str(value), source=source, table=indicator.key, year=year, release=release,
                                            indicator=indicator.concept, indicator_code=indicator.key, unit=unit,
                                            area_code="JPN" if is_gdp else "GRC"))
                batches.append(rows)
            confirmations.append((source, indicator))
    store, registry = seed(root, batches)
    publish(store)
    for source, indicator in confirmations:
        _confirm(root, demand, source, indicator.key, indicator.concept, code=indicator.key)
    config = consumer(sources, id=demand.consumer, models={"consumer": demand.consumer, "authorized_sources": sorted(sources)})
    record = executor.execute_demand(demand, data_root=root, registry=registry, consumer=config)
    return outputs(root, record), record


@pytest.mark.parametrize("sample", ["three-gdp-reconciliation.yaml", "greece-deficit-revision-chain.yaml"])
def test_sample_demands_end_to_end_on_synthetic_data(tmp_path, sample):
    (rows, citations, gaps), record = sample_result(tmp_path, sample)
    assert rows and citations and record["bindings"]
    if "gdp" in sample:
        spreads = [r for r in rows if r["symbol"] == "spread_usd"]
        assert {r["value"] for r in spreads} == {Decimal(-10), Decimal(0), Decimal(10)}
        assert {r["spread_range"] for r in spreads} == {Decimal(20)}
    else:
        revisions = [r for r in rows if r["symbol"] == "revision_delta"]
        assert {r["value"] for r in revisions} == {Decimal(0), Decimal(-2)}
        adjustments = [r for r in rows if r["symbol"] == "stock_flow_adjustment" and r["period"].year == 2009]
        assert adjustments and {r["value"] for r in adjustments} == {Decimal(15)}
        assert all(len(c["citation"]["inputs"]) >= 1 for c in citations)
    assert not (tmp_path / "consumer/outbox").exists()
