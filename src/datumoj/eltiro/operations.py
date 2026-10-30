"""Bounded Decimal arithmetic. Ordering/alignment belongs to the executor.

No expression evaluation, I/O, implicit conversion or missing-value imputation.
"""

from dataclasses import dataclass, field
from decimal import Decimal, localcontext

from datumoj.eltiro.models import Operation, Scale


@dataclass(frozen=True)
class Calculation:
    value: Decimal | None
    reason: str | None = None
    details: dict = field(default_factory=dict)


def calculate(operation: Operation, values: list[Decimal | None], *, scale: Scale | None = None) -> Calculation:
    """Current value comes first; binary/baseline second; correlation pairs interleave.

    source_spread receives the current source followed by the other sources exactly
    once. Correlation receives an already aligned, complete window of pairs.
    """
    if any(v is not None and (not isinstance(v, Decimal) or not v.is_finite()) for v in values):
        raise ValueError("arithmetic requires finite Decimal inputs")
    op = operation.op
    expected = (operation.window * 2 if op == "lead_lag_corr" else
                operation.window if op == "moving_average" else
                1 if op == "rescale" else None if op == "source_spread" else 2)
    if not values or (expected is not None and len(values) != expected):
        return Calculation(None, "incomplete_window")
    if op == "rescale" and (scale is None or scale.multiplier is None or not scale.evidence):
        return Calculation(None, "unconfirmed_scale")
    if any(v is None for v in values):
        return Calculation(None, "missing_input")
    with localcontext() as context:
        context.prec = 50
        a = values[0]
        if op == "rescale":
            multiplier = Decimal(str(scale.multiplier))
            target = Decimal(str(operation.target_scale))
            return Calculation(a * multiplier / target, details={
                "multiplier": str(multiplier), "target_scale": str(target), "evidence": scale.evidence,
            })
        if op == "diff":
            return Calculation(a + values[1] if operation.sign == "plus" else a - values[1])
        if op in {"diff_lag", "mirror_diff", "residual", "revision_diff"}:
            return Calculation(a - values[1])
        if op in {"ratio", "growth_rate"}:
            if values[1] == 0:
                return Calculation(None, "zero_denominator")
            ratio = a / values[1]
            return Calculation((ratio - 1) * 100 if op == "growth_rate" else ratio)
        if op == "moving_average":
            return Calculation(sum(values, Decimal(0)) / Decimal(len(values)))
        if op == "source_spread":
            ordered = sorted(values)
            n = len(ordered)
            if n < 2:
                return Calculation(None, "insufficient_sources")
            median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
            return Calculation(a - median, details={"median": str(median), "range": str(ordered[-1] - ordered[0])})
        if op == "lead_lag_corr":
            left, right = values[::2], values[1::2]
            n = Decimal(len(left))
            xbar, ybar = sum(left, Decimal(0)) / n, sum(right, Decimal(0)) / n
            xy = sum(((x - xbar) * (y - ybar) for x, y in zip(left, right)), Decimal(0))
            xx = sum(((x - xbar) ** 2 for x in left), Decimal(0))
            yy = sum(((y - ybar) ** 2 for y in right), Decimal(0))
            if xx == 0 or yy == 0:
                return Calculation(None, "zero_variance")
            return Calculation(xy / (xx * yy).sqrt())
    raise ValueError("unsupported operation")
