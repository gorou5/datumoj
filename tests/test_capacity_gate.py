"""Pure gate boundaries, including uncertain and malformed capacity inputs."""

import pytest

from datumoj.rikolto.capacity import LIMITS, CapacityLimits, gate


@pytest.mark.parametrize("total,tier", [
    (0, "normal"), (6_999_999_999, "normal"), (7_000_000_000, "report"),
    (8_499_999_999, "report"), (8_500_000_000, "freeze"),
    (8_999_999_999, "freeze"), (9_000_000_000, "stop"), (9_100_000_000, "stop"),
])
@pytest.mark.parametrize("category", ["essential", "optional", "reclaim"])
def test_allowance_table(total, tier, category):
    decision = gate(total, 0, category)
    assert decision["tier"] == tier
    assert decision["allowed"] is (category == "reclaim" or tier in {"normal", "report"}
                                   or (tier == "freeze" and category == "essential"))
    assert decision["reason"] == f"capacity_{tier}"


def test_growth_and_reserve_both_count():
    assert gate(8_400_000_000, 700_000_000, "essential")["tier"] == "stop"
    result = gate(8_000_000_000, 500_000_000, "essential", reserve_bytes=LIMITS.crawl_reserve)
    assert result["projected_bytes"] == 9_000_000_000 and not result["allowed"]


@pytest.mark.parametrize("sizes", [(None, 0), (0, None), (-1, 0), (0, -1),
                                  (True, 0), (0, float("nan")), ("secret", 0)])
@pytest.mark.parametrize("category", ["essential", "optional", "reclaim"])
def test_unknown_fails_closed_except_reclamation(sizes, category):
    decision = gate(*sizes, category)
    assert decision["reason"] == "capacity_unknown" and decision["tier"] == "stop"
    assert decision["allowed"] is (category == "reclaim")
    assert "secret" not in str(decision)


@pytest.mark.parametrize("changes", [
    {"report": 0}, {"report": -1}, {"report": True}, {"freeze": 7_000_000_000},
    {"stop": 8_000_000_000}, {"crawl_reserve": 0}, {"crawl_reserve": float("inf")},
])
def test_invalid_limits_are_rejected(changes):
    with pytest.raises(ValueError, match="invalid_capacity_limits"):
        CapacityLimits(**changes)


def test_category_and_reserve_validation():
    with pytest.raises(ValueError, match="invalid_write_category"):
        gate(0, 0, "arbitrary")
    assert gate(0, 0, "essential", reserve_bytes=-1)["reason"] == "capacity_unknown"
