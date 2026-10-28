"""All registered operations, Decimal precision, strict missing propagation."""

from decimal import Decimal, localcontext

import pytest
from pydantic import TypeAdapter

from datumoj.eltiro.models import Operation, Scale
from datumoj.eltiro.operations import calculate


def operation(op, **kwargs):
    unary = {"rescale", "diff_lag", "growth_rate", "source_spread", "revision_diff", "moving_average"}
    inputs = {"input": "a"} if op in unary else {"left": "a", "right": "b"}
    return TypeAdapter(Operation).validate_python({"id": "result", "op": op, **inputs, **kwargs})


CASES = [
    ("rescale", {"target_scale": 1}, ["2"], "2000"),
    ("diff", {}, ["8", "3"], "5"),
    ("diff", {"sign": "plus"}, ["8", "-3"], "5"),
    ("diff_lag", {"lag": 1}, ["8", "3"], "5"),
    ("ratio", {}, ["8", "4"], "2"),
    ("growth_rate", {}, ["6", "4"], "50"),
    ("source_spread", {}, ["3", "1", "8"], "0"),
    ("revision_diff", {}, ["8", "3"], "5"),
    ("mirror_diff", {}, ["8", "3"], "5"),
    ("residual", {}, ["8", "3"], "5"),
    ("moving_average", {"window": 2}, ["2", "4"], "3"),
    ("lead_lag_corr", {"lag": 1, "window": 3}, ["1", "2", "2", "4", "3", "6"], "1"),
]


@pytest.mark.parametrize("name,kwargs,values,expected", CASES)
def test_every_operation_and_missing(name, kwargs, values, expected):
    op = operation(name, **kwargs)
    inputs = list(map(Decimal, values))
    scale = Scale(multiplier=1000, evidence="Synthetic source multiplier")
    result = calculate(op, inputs, scale=scale)
    assert result.value == Decimal(expected)
    assert result.reason is None
    inputs[0] = None
    assert calculate(op, inputs, scale=scale).value is None
    assert calculate(op, inputs, scale=scale).reason == "missing_input"


def test_zero_denominators_unconfirmed_scale_and_spread_details():
    for name in ("ratio", "growth_rate"):
        result = calculate(operation(name), [Decimal(1), Decimal(0)])
        assert result.value is None and result.reason == "zero_denominator"
    result = calculate(operation("rescale", target_scale=1), [Decimal(2)], scale=Scale())
    assert result.reason == "unconfirmed_scale"
    result = calculate(operation("source_spread"), [Decimal(1), Decimal(3), Decimal(8)])
    assert result.value == -2 and result.details == {"median": "3", "range": "7"}
    result = calculate(operation("lead_lag_corr", lag=0, window=2), list(map(Decimal, [1, 2, 1, 3])))
    assert result.reason == "zero_variance"
    assert calculate(operation("moving_average", window=2), [Decimal(1)]).reason == "incomplete_window"
    with pytest.raises(ValueError):
        calculate(operation("ratio"), [1.0, Decimal(2)])
    with localcontext() as context:
        context.prec = 4  # Library precision is independent of callers' context.
        result = calculate(operation("diff"), [Decimal("123456789.0001"), Decimal("123456789")])
        assert result.value == Decimal("0.0001")
