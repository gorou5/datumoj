"""Scorer boundaries using synthetic identities/values; no network or real originals."""

from decimal import Decimal

import pytest
import yaml

from datumoj.eltiro.eval import (
    GoldSet,
    Thresholds,
    ValueTable,
    check_license_cases,
    check_values,
    load_gold,
    load_license_cases,
    load_values,
    score_matches,
)
from datumoj.eltiro.models import MAX_FILE_BYTES
from datumoj.eltiro.testing.samples import make_match, make_series


def thresholds():
    return Thresholds.model_validate({"schema": "eltiro.eval.thresholds/v1", "deterministic": {
        "precision": 0.95, "recall": 0.8}, "overall": {"precision": 0.95, "recall": 0.8},
        "max_counterexample_bindings": 0, "rationale": "synthetic"})


def gold():
    return GoldSet.model_validate({"schema": "eltiro.eval.gold/v1", "demand_id": "example-demand",
                                   "demand_version": 1, "entries": [{
        "id": "gdp", "indicator_key": "gdp", "series": make_series(), "expected": "positive",
        "status": "verified", "evidence": {"reference": "synthetic", "locator": "one", "note": "synthetic"},
    }]})


def test_tier_recall_uses_all_verified_targets_and_rejected_predictions_do_not_count():
    record = make_match(tier="model")
    report = score_matches([gold()], [record], thresholds())
    assert report.overall.passed and not report.deterministic.passed
    assert report.deterministic.precision is None and report.deterministic.recall == 0
    rejected = make_match(confirmation="rejected")
    report = score_matches([gold()], [rejected], thresholds())
    assert report.overall.false_negatives == 1 and not report.passed


def test_complete_identity_deduplication_and_validation():
    record = make_match()
    report = score_matches([gold()], [record, record], thresholds())
    assert report.overall.true_positives == 1 and report.passed
    changed = make_match(series=make_series(fixed_dimensions={"valuation": "constant"}))
    report = score_matches([gold()], [changed], thresholds())
    assert report.overall.false_positives == report.overall.false_negatives == 1
    for records in ([record, make_match(tier="vector")], [record, make_match(generation=2)],
                    [make_match(demand_version=2)], [make_match(demand_id="unknown")]):
        with pytest.raises(ValueError):
            score_matches([gold()], records, thresholds())
    with pytest.raises(ValueError):
        score_matches([gold(), gold()], [], thresholds())


def value_table(**changes):
    point = {"id": "one", "status": "verified", "selector": {
        "demand_id": "example-demand", "country": "JPN", "period": "2023", "indicator_key": "gdp",
        "source_id": "intl-wdi", "release_id": "synthetic-v1",
    }, "unit": "USD", "source_value": "1.234", "source_multiplier": "1000000000",
        "evidence": {"reference": "synthetic", "locator": "one", "note": "Test-only value"},
        "checked_at": "2026-10-27", **changes}
    return ValueTable.model_validate({"schema": "eltiro.eval.values/v1", "demand_id": "example-demand",
                                     "points": [point]})


def row(table, **changes):
    return {**table.points[0].selector, "unit": "USD", "scale": "1", "value": "1234000000", **changes}


def test_values_exact_scale_conversion_and_missing_or_duplicate_rows():
    table = value_table()
    assert check_values([table], [row(table)]).passed
    for rows in ([], [row(table), row(table)], [row(table, value="1234000001")],
                 [row(table, source_id="other-source")], [row(table, release_id="synthetic-v2")],
                 [row(table, unit="JPY")], [row(table, scale="1000000")]):
        report = check_values([table], rows)
        assert not report.passed and report.checks[0].id == "example-demand:one"
    converted = value_table(output_multiplier="1000000")
    assert check_values([converted], [row(converted, scale="1000000", value="1234")]).passed


def test_significant_digits_and_explicit_absolute_tolerance():
    table = value_table(significant_digits=4)
    assert check_values([table], [row(table, value="1234400000")]).passed
    # At the exact tie, HALF_EVEN keeps the even final digit 4.
    assert check_values([table], [row(table, value="1234500000")]).passed
    assert not check_values([table], [row(table, value="1234500001")]).passed
    table = value_table(absolute_tolerance="0.01")
    assert check_values([table], [row(table, value="1234000000.01")]).passed
    assert not check_values([table], [row(table, value="1234000000.02")]).passed
    assert check_values([table], [row(table)]).model_dump()["passed"] is True


@pytest.mark.parametrize("value", [None, True, False, "NaN", "Infinity", "-Infinity", "x", "-", "…"])
def test_values_do_not_accept_non_numeric_outputs(value):
    table = value_table()
    assert not check_values([table], [row(table, value=value)]).passed


@pytest.mark.parametrize("note", ["-", "…", "x", "秘匿"])
def test_source_missing_value_and_annotation_are_preserved(note):
    table = value_table(source_value=None, source_note=note)
    assert check_values([table], [row(table, value=None, note=note)]).passed
    assert not check_values([table], [row(table, value="0", note=note)]).passed
    assert not check_values([table], [row(table, value=None, note="changed")]).passed
    with pytest.raises(ValueError):
        value_table(source_value="0", source_note=note)


def test_unverified_point_needs_reason_and_verified_point_needs_evidence():
    for changes in ({"checked_at": None}, {"source_value": None}, {"source_value": "NaN"},
                    {"source_multiplier": "0"}, {"absolute_tolerance": "-1"},
                    {"status": "pending_h2", "pending_reason": None}):
        with pytest.raises(ValueError):
            value_table(**changes)
    table = value_table(status="pending_h2", source_value=None, checked_at=None, pending_reason="not recorded")
    report = check_values([table], [])
    assert not report.passed and report.skipped == ["example-demand:one"]


def test_safe_loaders_roundtrip_and_reject_invalid_or_oversized_files(tmp_path):
    path = tmp_path / "gold.yaml"
    data = gold().model_dump(mode="json", by_alias=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert load_gold(path) == gold()
    data["entries"] *= 2
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError):
        load_gold(path)
    for content in (b"!!python/object/apply:eval ['1+1']", b"a: &a [*a]", b"\xff", b"x" * (MAX_FILE_BYTES + 1)):
        path.write_bytes(content)
        with pytest.raises(ValueError):
            load_gold(path)
    table = value_table()
    path.write_text(yaml.safe_dump(table.model_dump(mode="json", by_alias=True)), encoding="utf-8")
    assert load_values(path).points[0].source_value == Decimal("1.234")


def test_gate_exceptions_and_non_boolean_returns_fail_per_case(tmp_path):
    path = tmp_path / "license.yaml"
    path.write_text(yaml.safe_dump({"schema": "eltiro.eval.license/v1", "cases": [{
        "id": "one", "description": "synthetic", "inputs": [{"source_id": "synthetic-restricted",
        "registry_outflow": "no_outflow", "payload": "raw"}], "consumer_outflow": "raw_ok",
        "expected_allowed": False, "evidence": "synthetic contract test",
    }]}), encoding="utf-8")
    cases = load_license_cases(path)

    def broken(case):
        raise RuntimeError("synthetic failure")

    for gate in (broken, lambda case: None, lambda case: 0):
        report = check_license_cases(cases, gate)
        assert not report.passed and report.checks[0].id == "one"
    assert "RuntimeError" in check_license_cases(cases, broken).checks[0].reason
