"""701 offline corpus and checkpoint-scoring acceptance audit."""

from pathlib import Path

import pytest

from datumoj.eltiro.eval import (
    GoldSet,
    check_license_cases,
    check_values,
    load_gold,
    load_license_cases,
    load_thresholds,
    load_values,
    score_matches,
)
from datumoj.eltiro.models import load_demand
from datumoj.eltiro.testing.samples import make_match, make_series

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "eval/eltiro"
PILOTS = {
    "three-gdp-reconciliation", "greece-deficit-revision-chain",
    "japan-monthly-earnings-revisions", "trade-mirror",
}


def test_four_demands_validate_and_samples_are_byte_identical():
    paths = list((CORPUS / "demands").glob("*.yaml"))
    assert {path.stem for path in paths} == PILOTS
    for path in paths:
        assert load_demand(path).id == path.stem
    for name in ("three-gdp-reconciliation", "greece-deficit-revision-chain"):
        assert (CORPUS / "demands" / f"{name}.yaml").read_bytes() == (
            ROOT / "docs/plans/0.4-wisteria-samples" / f"{name}.yaml"
        ).read_bytes()


def test_gold_covers_every_indicator_with_evidenced_positive_and_negative():
    assert {path.stem for path in (CORPUS / "gold").glob("*.yaml")} == PILOTS
    for path in (CORPUS / "gold").glob("*.yaml"):
        gold = load_gold(path)
        demand = load_demand(CORPUS / "demands" / path.name)
        assert (gold.demand_id, gold.demand_version) == (demand.id, demand.version)
        assert {entry.indicator_key for entry in gold.entries} == {item.key for item in demand.indicators}
        for indicator in demand.indicators:
            assert {entry.expected for entry in gold.entries if entry.indicator_key == indicator.key} == {
                "positive", "negative",
            }
        for entry in gold.entries:
            assert entry.evidence.reference.strip()
            assert entry.evidence.locator.strip()
            assert entry.evidence.note.strip()
            if entry.status == "pending_h2":
                assert entry.pending_reason
        # Never turn an all-pending corpus into a green checkpoint.
        report = score_matches([gold], [], load_thresholds(CORPUS / "thresholds.yaml"))
        assert not report.passed and not report.overall.evaluated
        assert len(report.skipped_gold) == len(gold.entries)


@pytest.fixture
def synthetic_gold():
    entries = []
    for name, expected, status in [
        ("first", "positive", "verified"), ("second", "positive", "verified"),
        ("wrong", "negative", "verified"), ("future", "positive", "pending_h2"),
        ("future-wrong", "negative", "pending_h2"),
    ]:
        entries.append({
            "id": name, "indicator_key": "gdp", "series": make_series(table_key=name),
            "expected": expected, "status": status,
            "evidence": {"reference": "synthetic", "locator": name, "note": "Test-only identity, no official value"},
            "pending_reason": "H2 unresolved" if status == "pending_h2" else None,
        })
    return GoldSet.model_validate({"schema": "eltiro.eval.gold/v1", "demand_id": "example-demand",
                                   "demand_version": 1, "entries": entries})


def records_for(gold, names):
    return [make_match(series=entry.series, auto_bind=True,
                       compatibility={"methodology": "same", "unit": "same", "survey": "same"})
            for entry in gold.entries if entry.id in names]


def test_perfect_missing_wrong_and_counterexample_bindings(synthetic_gold):
    gold = synthetic_gold
    thresholds = load_thresholds(CORPUS / "thresholds.yaml")
    perfect = score_matches([gold], records_for(gold, ["first", "second"]), thresholds)
    assert perfect.passed
    assert perfect.overall.precision == perfect.overall.recall == 1
    missing = score_matches([gold], records_for(gold, ["first"]), thresholds)
    assert missing.overall.precision == 1 and missing.overall.recall == 0.5
    assert missing.overall.false_negatives == 1 and not missing.passed
    assert any("second" in failure for failure in missing.overall.failures)
    wrong = make_match(series=make_series(table_key="unlisted"))
    report = score_matches([gold], [*records_for(gold, ["first", "second"]), wrong], thresholds)
    assert report.overall.precision == pytest.approx(2 / 3)
    assert report.overall.recall == 1 and report.overall.false_positives == 1
    assert not report.passed
    counter = score_matches([gold], records_for(gold, ["first", "second", "wrong"]), thresholds)
    assert counter.overall.counterexample_bindings == 1
    assert any("wrong" in failure for failure in counter.overall.failures)
    assert not counter.passed


def test_pending_h2_excluded_from_all_counts(synthetic_gold):
    report = score_matches([synthetic_gold], records_for(
        synthetic_gold, ["first", "second", "future", "future-wrong"],
    ), load_thresholds(CORPUS / "thresholds.yaml"))
    assert report.passed and report.overall.true_positives == 2
    assert report.overall.false_positives == report.overall.false_negatives == 0
    assert report.overall.counterexample_bindings == 0
    assert report.skipped_predictions == len(report.skipped_gold) == 2


def test_license_cases_reject_allow_all_and_accept_deny_all():
    cases = load_license_cases(CORPUS / "license_cases.yaml")
    assert len(cases.cases) >= 8
    allowed = check_license_cases(cases, lambda case: True)
    denied = check_license_cases(cases, lambda case: False)
    assert not allowed.passed and all(not check.passed for check in allowed.checks)
    assert denied.passed and all(check.passed for check in denied.checks)
    assert {check.id for check in allowed.checks} == {case.id for case in cases.cases}
    assert all(check.reason for check in allowed.checks)


def test_values_have_official_locators_and_do_not_claim_unverified_originals():
    assert {path.stem for path in (CORPUS / "values").glob("*.yaml")} == PILOTS
    tables = [load_values(path) for path in (CORPUS / "values").glob("*.yaml")]
    for table in tables:
        assert len(table.points) >= 2
        for point in table.points:
            assert point.evidence.reference.startswith("https://")
            assert point.evidence.locator and point.pending_reason
            assert point.status == "pending_h2"
            assert point.source_value is None and point.checked_at is None
    report = check_values(tables, [])
    assert not report.passed and not report.checks
    assert len(report.skipped) == sum(len(table.points) for table in tables)
