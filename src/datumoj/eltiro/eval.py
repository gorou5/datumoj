"""Offline checkpoint scoring, independent of matching/execution/gate implementations.

Local files are bounded safe YAML. Malformed inputs raise ValueError; evaluation
failures are returned in reports. See eval/eltiro/README.md for the row/gate adapters.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from datumoj.eltiro.models import MAX_FILE_BYTES, MatchRecord, SeriesIdentity


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Evidence(_Model):
    reference: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    note: str = Field(min_length=1)


class GoldEntry(_Model):
    id: str = Field(min_length=1)
    indicator_key: str = Field(min_length=1)
    series: SeriesIdentity
    expected: Literal["positive", "negative"]
    status: Literal["verified", "pending_h2"]
    evidence: Evidence
    pending_reason: str | None = None

    @model_validator(mode="after")
    def _pending(self):
        if self.status == "pending_h2" and not self.pending_reason:
            raise ValueError("pending entry needs a reason")
        return self


def _series_key(series: SeriesIdentity) -> tuple:
    # Display label participates: this is the complete contract identity, not a fuzzy match.
    return (series.source_id, series.table_key, series.indicator, series.indicator_code,
            tuple(sorted(series.fixed_dimensions.items())))


class GoldSet(_Model):
    schema_version: Literal["eltiro.eval.gold/v1"] = Field(alias="schema")
    demand_id: str
    demand_version: int = Field(gt=0, strict=True)
    entries: list[GoldEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self):
        keys = [(entry.indicator_key, _series_key(entry.series)) for entry in self.entries]
        if len(set(keys)) != len(keys) or len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("duplicate or contradictory gold entry")
        return self


class MetricLimit(_Model):
    precision: float = Field(ge=0, le=1, allow_inf_nan=False)
    recall: float = Field(ge=0, le=1, allow_inf_nan=False)


class Thresholds(_Model):
    schema_version: Literal["eltiro.eval.thresholds/v1"] = Field(alias="schema")
    deterministic: MetricLimit
    overall: MetricLimit
    max_counterexample_bindings: Literal[0]
    rationale: str = Field(min_length=1)


def _load(path: str | Path, model: type[_Model]):
    with Path(path).open("rb") as stream:
        content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("evaluation file exceeds MAX_FILE_BYTES")
    try:
        text = content.decode("utf-8")
        if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(text)):
            raise ValueError("YAML aliases are not allowed")
        return model.model_validate(yaml.safe_load(text))
    except (UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise ValueError("invalid evaluation YAML") from exc


def load_gold(path: str | Path) -> GoldSet:
    return _load(path, GoldSet)


def load_thresholds(path: str | Path) -> Thresholds:
    return _load(path, Thresholds)


class MatchMetrics(_Model):
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float | None
    recall: float | None
    counterexample_bindings: int
    evaluated: bool
    passed: bool
    failures: list[str]


class MatchReport(_Model):
    overall: MatchMetrics
    deterministic: MatchMetrics
    skipped_gold: list[str]
    skipped_predictions: int
    passed: bool


def score_matches(gold: Sequence[GoldSet], records: Iterable[MatchRecord], thresholds: Thresholds) -> MatchReport:
    """Score unique non-rejected recommendations in exactly one generation.

    Exact pending identities are ignored; unlisted predictions are false positives.
    All verified positives form both recall denominators. Counterexample automatic
    bindings count even if a human has not yet confirmed the recommendation.
    """
    scopes = [(item.demand_id, item.demand_version) for item in gold]
    if len(set(scopes)) != len(scopes):
        raise ValueError("duplicate demand/version in gold")
    entries = {
        (item.demand_id, item.demand_version, entry.indicator_key, _series_key(entry.series)): entry
        for item in gold for entry in item.entries
    }
    pending = {key for key, entry in entries.items() if entry.status == "pending_h2"}
    positives = {key for key, entry in entries.items() if entry.status == "verified" and entry.expected == "positive"}
    negatives = {key for key, entry in entries.items() if entry.status == "verified" and entry.expected == "negative"}
    predictions = {}
    generations = set()
    skipped = 0
    seen = {}
    for supplied in records:
        record = MatchRecord.model_validate(supplied.model_dump())
        if (record.demand_id, record.demand_version) not in scopes:
            raise ValueError("prediction demand/version is outside supplied gold")
        generations.add(record.generation)
        key = (record.demand_id, record.demand_version, record.indicator_key, _series_key(record.series))
        if key in seen:
            if seen[key] != record:
                raise ValueError("conflicting duplicate prediction")
            continue
        seen[key] = record
        if key in pending:
            skipped += 1
        elif record.confirmation != "rejected":
            predictions[key] = record
    if len(generations) > 1:
        raise ValueError("score each generation separately")

    def metrics(tier: str | None, limit: MetricLimit) -> MatchMetrics:
        selected = {key for key, record in predictions.items() if tier is None or record.tier == tier}
        tp = len(selected & positives)
        fp = len(selected - positives)
        missing = positives - selected
        bindings = sum(predictions[key].auto_bind for key in selected & negatives)
        precision = tp / len(selected) if selected else None
        recall = tp / len(positives) if positives else None
        failures = [f"missing {key[0]} v{key[1]}: {entries[key].id}" for key in sorted(missing, key=repr)]
        for key in sorted(selected - positives, key=repr):
            entry = entries.get(key)
            failures.append(f"unexpected {key[0]}/{key[2]}: {entry.id if entry else key[3]!s}")
        if bindings:
            failures.append(f"counterexample auto-bindings: {bindings}")
        evaluated = bool(positives)
        passed = (evaluated and precision is not None and precision >= limit.precision
                  and recall >= limit.recall and bindings <= thresholds.max_counterexample_bindings)
        if not evaluated:
            failures.append("no verified positives; checkpoint is not evaluable")
        return MatchMetrics(true_positives=tp, false_positives=fp, false_negatives=len(missing),
                            precision=precision, recall=recall, counterexample_bindings=bindings,
                            evaluated=evaluated, passed=passed, failures=failures)

    overall = metrics(None, thresholds.overall)
    deterministic = metrics("deterministic", thresholds.deterministic)
    return MatchReport(overall=overall, deterministic=deterministic,
                       skipped_gold=[f"{key[0]}:{entries[key].id}" for key in sorted(pending, key=repr)],
                       skipped_predictions=skipped, passed=overall.passed and deterministic.passed)


Finite = Annotated[Decimal, Field(allow_inf_nan=False)]
Multiplier = Annotated[Finite, Field(gt=0)]


class ValuePoint(_Model):
    id: str = Field(min_length=1)
    status: Literal["verified", "pending_h2"]
    selector: dict[str, str] = Field(min_length=1)
    unit: str
    source_value: Finite | None
    source_note: str | None = None
    source_multiplier: Multiplier = Decimal(1)
    output_multiplier: Multiplier = Decimal(1)
    absolute_tolerance: Annotated[Finite, Field(ge=0)] = Decimal(0)
    significant_digits: int | None = Field(default=None, ge=1, le=28, strict=True)
    evidence: Evidence
    checked_at: date | None
    pending_reason: str | None = None

    @model_validator(mode="after")
    def _evidence(self):
        if not {"demand_id", "country", "period", "indicator_key", "source_id", "release_id"} <= self.selector.keys():
            raise ValueError("value selector requires demand, country, period, indicator, source and release")
        if self.source_note in {"-", "…", "x", "秘匿"} and self.source_value is not None:
            raise ValueError("missing/suppressed original must have a null value")
        if self.status == "verified":
            if self.checked_at is None or (self.source_value is None and not self.source_note):
                raise ValueError("verified value needs a check date and an original value or missing-value note")
        elif not self.pending_reason:
            raise ValueError("pending value needs a reason")
        return self


class ValueTable(_Model):
    schema_version: Literal["eltiro.eval.values/v1"] = Field(alias="schema")
    demand_id: str
    points: list[ValuePoint] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self):
        if len({point.id for point in self.points}) != len(self.points):
            raise ValueError("duplicate value point")
        if any(point.selector["demand_id"] != self.demand_id for point in self.points):
            raise ValueError("value demand does not match table")
        return self


def load_values(path: str | Path) -> ValueTable:
    return _load(path, ValueTable)


class CheckResult(_Model):
    id: str
    passed: bool
    reason: str


class CheckReport(_Model):
    checks: list[CheckResult]
    skipped: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)


def _round(value: Decimal, digits: int | None) -> Decimal:
    if digits is None or value == 0:
        return value
    return value.quantize(Decimal(1).scaleb(value.adjusted() - digits + 1), rounding=ROUND_HALF_EVEN)


def check_values(tables: Sequence[ValueTable], rows: Iterable[Mapping[str, Any]]) -> CheckReport:
    """Compare decoded output rows only with the same source/release original.

    Unit and scale are mandatory output fields. No cross-source consensus, exchange
    rate inference or implicit zero. Tolerance is in single source currency units.
    """
    rows = list(rows)
    checks, skipped = [], []
    for table in tables:
        for point in table.points:
            label = f"{table.demand_id}:{point.id}"
            if point.status == "pending_h2":
                skipped.append(label)
                continue
            candidates = [row for row in rows if all(row.get(key) == value for key, value in point.selector.items())]
            reason = "equal to source original"
            if len(candidates) != 1:
                reason = f"expected one output row, found {len(candidates)}"
            else:
                row = candidates[0]
                try:
                    scale = Decimal(str(row["scale"]))
                    if not scale.is_finite() or scale != point.output_multiplier or row["unit"] != point.unit:
                        reason = "output unit or scale differs"
                    elif point.source_value is None:
                        if row["value"] is not None or row.get("note") != point.source_note:
                            reason = "missing source value/note was not preserved"
                    elif row["value"] is None or isinstance(row["value"], bool):
                        reason = "output numeric value is missing or boolean"
                    elif point.source_note is not None and row.get("note") != point.source_note:
                        reason = "source annotation was not preserved"
                    else:
                        actual = Decimal(str(row["value"]))
                        if not actual.is_finite():
                            reason = "output value is not finite"
                        else:
                            actual = _round(actual * scale, point.significant_digits)
                            expected = _round(point.source_value * point.source_multiplier, point.significant_digits)
                            if abs(actual - expected) > point.absolute_tolerance:
                                reason = f"output {actual} != source {expected}; tolerance {point.absolute_tolerance}"
                except (KeyError, ValueError, ArithmeticError):
                    reason = "invalid output value, unit or scale"
            checks.append(CheckResult(id=label, passed=reason == "equal to source original", reason=reason))
    return CheckReport(checks=checks, skipped=skipped)


class LicenseInput(_Model):
    source_id: str
    registry_outflow: Literal["raw_ok", "derived_only", "no_outflow"] | None
    payload: Literal["derived", "raw", "snippet"]


class LicenseCase(_Model):
    id: str
    description: str
    inputs: list[LicenseInput] = Field(min_length=1)
    consumer_outflow: Literal["raw_ok", "derived_only", "no_outflow"]
    expected_allowed: Literal[False]
    evidence: str = Field(min_length=1)


class LicenseCases(_Model):
    schema_version: Literal["eltiro.eval.license/v1"] = Field(alias="schema")
    cases: list[LicenseCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate license case")
        return self


def load_license_cases(path: str | Path) -> LicenseCases:
    return _load(path, LicenseCases)


def check_license_cases(cases: LicenseCases, gate: Callable[[LicenseCase], bool]) -> CheckReport:
    """Adapter must return True for allow, False for deny. Exceptions are failures.

    805 supplies the adapter to its actual gate API; no gate is implemented here.
    """
    checks = []
    for case in cases.cases:
        try:
            allowed = gate(case.model_copy(deep=True))
            passed = type(allowed) is bool and allowed is case.expected_allowed
            reason = "denied as required" if passed else "expected explicit deny (False)"
        except Exception as exc:
            passed, reason = False, f"gate raised {type(exc).__name__}"
        checks.append(CheckResult(id=case.id, passed=passed, reason=f"{case.description}: {reason}"))
    return CheckReport(checks=checks)
