"""Calendar boundaries and negative evidence cases for local alerts."""

from datetime import date

import pytest

from datumoj.eltiro.alerts import SQLiteAlertStore, generate_alerts
from datumoj.eltiro.match_store import MatchResult, series_key
from datumoj.eltiro.testing.samples import make_demand, make_match, make_profile


def alerts_for(tmp_path, old, new, *, record=None, as_of=date(2026, 1, 1), notify=None):
    demand = make_demand(run={"notify_on": notify if notify is not None else [
        "binding_stale", "definition_changed", "new_candidate", "gap_filled"]})
    record = record or make_match(confirmation="confirmed")
    before = MatchResult([record], [], False)
    after = MatchResult([record.model_copy(update={"generation": 2})], [], False)
    with SQLiteAlertStore(tmp_path) as store:
        generate_alerts(demand, 2, before, after, old_profiles={series_key(old.series): old},
                        new_profiles={series_key(new.series): new}, as_of=as_of, store=store)
        return store.list_alerts()


@pytest.mark.parametrize("frequency,end,deadline", [
    ("annual", date(2024, 2, 29), date(2025, 2, 28)),
    ("quarterly", date(2024, 11, 30), date(2025, 2, 28)),
    ("monthly", date(2025, 1, 31), date(2025, 2, 28)),
    ("weekly", date(2025, 2, 21), date(2025, 2, 28)),
    ("daily", date(2025, 2, 27), date(2025, 2, 28)),
])
def test_stale_strict_calendar_deadline(tmp_path, frequency, end, deadline):
    profile = make_profile(frequency=frequency, time_end=end)
    assert alerts_for(tmp_path / "boundary", profile, profile, as_of=deadline) == []
    assert [a.kind for a in alerts_for(tmp_path / "late", profile, profile, as_of=date(2025, 3, 1))] == ["binding_stale"]


@pytest.mark.parametrize("case", ["advanced", "unknown", "irregular", "pending", "rejected", "unsubscribed"])
def test_no_unsupported_stale_alert(tmp_path, case):
    old = make_profile(time_end=date(2020, 12, 31))
    new = old.model_copy(update={"time_end": date(2021, 12, 31)}) if case == "advanced" else old
    if case == "unknown":
        old = new = make_profile()
    if case == "irregular":
        old = new = make_profile(time_end=date(2020, 12, 31), frequency="irregular")
    record = make_match(confirmation=case if case in ("pending", "rejected") else "confirmed")
    assert alerts_for(tmp_path, old, new, record=record, notify=[] if case == "unsubscribed" else None) == []


def test_scale_only_multiplier_change_is_a_definition_change(tmp_path):
    old = make_profile(scale={"multiplier": 1, "evidence": "metadata"})
    new = make_profile(scale={"multiplier": 1000, "evidence": "metadata"})
    alerts = alerts_for(tmp_path / "changed", old, new)
    assert len(alerts) == 1 and alerts[0].details == ("scale.multiplier",)
    new = make_profile(scale={"multiplier": 1, "evidence": "updated citation"})
    assert alerts_for(tmp_path / "citation", old, new) == []


@pytest.mark.parametrize("case", ["equal", "rejected", "already_observed", "catalog_only"])
def test_no_new_candidate_or_gap_without_evidence(tmp_path, case):
    old = make_profile(availability="catalog_only" if case == "catalog_only" else "observations")
    new = old.model_copy(update={"generation": 2})
    assert alerts_for(tmp_path, old, new, record=make_match(
        confirmation="rejected" if case == "rejected" else "confirmed", confidence=0.8)) == []


def test_date_must_be_explicit_date(tmp_path):
    with pytest.raises(ValueError, match="as_of"):
        alerts_for(tmp_path, make_profile(), make_profile(), as_of="2026-01-01")
