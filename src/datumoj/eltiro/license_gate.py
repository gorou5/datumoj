"""Registry-authoritative, fail-closed row/column export policy (no I/O)."""

from __future__ import annotations

from decimal import Decimal

from datumoj.eltiro.models import CitationEntry, LicenseSummary
from datumoj.rikolto.core.contracts import SourceSpec
from datumoj.rikolto.core.models import Outflow

_RANK = {Outflow.raw_ok: 0, Outflow.derived_only: 1, Outflow.no_outflow: 2}
# An allowlist prevents future source-text columns from silently becoming exportable.
IDENTIFIERS = (
    "citation_id", "symbol", "country", "region_scheme", "region", "period", "source",
    "release_id", "released_at", "release_position",
)
NUMBERS = ("value", "spread_range")
RAW_TEXT = ("series", "unit", "note")


class LicenseGateError(ValueError):
    """Public diagnostics contain only a fixed code and validated source ids."""

    def __init__(self, code: str, sources=()):
        self.code = code
        self.sources = tuple(sorted(set(sources)))
        super().__init__(code)


def source_level(registry, source_id):
    spec = registry.get(source_id)
    if spec is None:
        return Outflow.no_outflow
    spec = SourceSpec.model_validate(spec.model_dump())
    return Outflow.no_outflow if spec.id != source_id else Outflow(spec.license.outflow)


def check_rows(rows, citations, *, registry, demand, consumer):
    """Validate complete lineage and sanitize every selected cell before any write.

    Citations retain only the typed identity and operation chain, never executor
    steps, profiles, evidence snippets, cached licenses or source attachments.
    """
    indexed = {}
    for item in citations:
        key = item["citation_id"]
        if key in indexed:
            raise LicenseGateError("invalid_lineage")
        indexed[key] = item["citation"]
    clean, index, levels = [], {}, {}
    for row in rows:
        key = row["citation_id"]
        if key in index or key not in indexed:
            raise LicenseGateError("invalid_lineage")
        try:
            citation = CitationEntry.model_validate(indexed[key])
        except ValueError:
            raise LicenseGateError("invalid_lineage") from None
        row_levels = {obs.source_id: source_level(registry, obs.source_id) for obs in citation.inputs}
        levels.update(row_levels)
        forbidden = [s for s, level in row_levels.items() if level == Outflow.no_outflow]
        if forbidden:
            raise LicenseGateError("no_outflow", forbidden)
        unauthorized = set(row_levels) - set(consumer.allowed_sources)
        if unauthorized:
            raise LicenseGateError("source_forbidden", unauthorized)
        level = max(row_levels.values(), key=_RANK.__getitem__)
        if level not in demand.accept_outflow:
            raise LicenseGateError("demand_outflow_forbidden", row_levels)
        if level not in consumer.accept_outflow:
            raise LicenseGateError("consumer_outflow_forbidden", row_levels)
        if any(row.get(column) is not None and (
                not isinstance(row[column], Decimal) or not row[column].is_finite()) for column in NUMBERS):
            raise LicenseGateError("invalid_numeric_type", row_levels)
        columns = IDENTIFIERS + NUMBERS + (RAW_TEXT if level == Outflow.raw_ok else ())
        clean.append({column: row.get(column) for column in columns if column in row})
        index[key] = citation
    summary = LicenseSummary(outflow=max(levels.values(), key=_RANK.__getitem__) if levels else Outflow.no_outflow,
                             by_source=dict(sorted(levels.items())))
    return clean, index, summary
