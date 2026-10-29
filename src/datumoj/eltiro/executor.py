"""Fixed-generation execution: SQL inputs, explicit arithmetic, private runs.

Only open_generation_views opens Rikolto storage. Never access online stores or
fall back after a failed token. A checked adapter also covers 802/803 SQL calls.
"""

from __future__ import annotations

import json
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from datumoj.eltiro.areas import BY_SCHEME, country, detect_scheme
from datumoj.eltiro.intake import load_consumers
from datumoj.eltiro.match_store import SQLiteMatchStore, series_key
from datumoj.eltiro.matching import match_demand
from datumoj.eltiro.models import CitationEntry, DemandSpec, InputObservation, operation_inputs
from datumoj.eltiro.operations import calculate
from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.eltiro.profiles import generate_profiles
from datumoj.eltiro.runs import RunStore
from datumoj.rikolto.core.contracts import OBSERVATION_RELEASE_COLUMNS, ObservationCatalog, TableSummary
from datumoj.rikolto.core.models import GenerationToken
from datumoj.rikolto.wiring import open_generation_views


class ExecutionError(ValueError):
    """Fixed, content-free errors at the public execution boundary."""


class CheckedObservations:
    """Expose only reads and force/check the token on every SQL query."""

    def __init__(self, observations, token):
        self.observations, self.token = observations, token

    def query_readonly(self, sql, max_rows=1000, timeout_s=10, *, generation_token=None, as_of_release=None):
        if generation_token is not None and generation_token != self.token:
            raise ExecutionError("generation token mismatch")
        result = self.observations.query_readonly(sql, max_rows, timeout_s,
                                                  generation_token=self.token, as_of_release=as_of_release)
        if result.generation_token != self.token:
            raise ExecutionError("generation token mismatch")
        return result

    def list_releases(self, *, source_id, table_key):
        return self.observations.list_releases(generation_token=self.token, source_id=source_id, table_key=table_key)

    def describe(self):
        # Do not delegate describe(): its internal queries must also be checked.
        result = self.query_readonly(
            "SELECT source_id,table_key,list(DISTINCT dataset_key),list(DISTINCT survey),"
            "list(DISTINCT indicator),count(DISTINCT area_code),min(time_start),max(time_end),count(*) "
            "FROM observations GROUP BY source_id,table_key ORDER BY source_id,table_key", max_rows=2**63 - 2)
        return ObservationCatalog(columns=dict(OBSERVATION_RELEASE_COLUMNS), tables=[
            TableSummary(source_id=sid, table_key=table, dataset_keys=sorted(dks), surveys=sorted(surveys),
                         indicators=sorted(inds), area_count=areas, time_start=start, time_end=end, rows=n)
            for sid, table, dks, surveys, inds, areas, start, end, n in result.rows])

    def validate(self):
        self.query_readonly("SELECT 1 WHERE false")


@dataclass(frozen=True)
class Cell:
    entity: tuple[str, str]  # country alpha-3, or (declared region scheme, code)
    period: date
    source: str
    series: str
    release_id: str
    released_at: date | None
    position: int
    unit: str | None
    value: Decimal | None
    citation: CitationEntry
    scale: object = None
    note: str | None = None
    steps: tuple[dict, ...] = ()
    spread_range: Decimal | None = None


def _literal(value):
    return "'" + value.replace("'", "''") + "'"


def _bounds(period):
    start = date.fromisoformat(period.start + {4: "-01-01", 7: "-01"}.get(len(period.start), ""))
    end = period.end
    if len(end) == 4:
        end += "-12-31"
    elif len(end) == 7:
        end += f"-{monthrange(int(end[:4]), int(end[5:]))[1]}"
    return start, date.fromisoformat(end)


def _shift(period, n, frequency):
    if frequency in {"annual", "quarterly", "monthly"}:
        months = n * {"annual": 12, "quarterly": 3, "monthly": 1}[frequency]
        index = period.year * 12 + period.month - 1 + months
        year, month = divmod(index, 12)
        if not 1 <= year <= 9999:
            return None
        return date(year, month + 1, min(period.day, monthrange(year, month + 1)[1]))
    if frequency in {"daily", "weekly"}:
        try:
            return period + timedelta(days=n * (7 if frequency == "weekly" else 1))
        except OverflowError:
            return None
    return None


def _period_valid(row, frequency):
    start, end = row["time_start"], row["time_end"]
    if start is None or end is None:
        return False
    kinds = {"annual": {"year"}, "quarterly": {"quarter"}, "monthly": {"month"},
             "weekly": {"week"}, "daily": {"day"}, "irregular": {"irregular"}}
    if row["granularity"] not in kinds[frequency]:
        return False
    if frequency == "irregular":
        return True
    next_period = _shift(start, 1, frequency)
    if next_period is None or end != next_period - timedelta(days=1):
        return False
    if frequency == "annual":
        return (start.month, start.day) == (1, 1)
    if frequency == "quarterly":
        return start.month in {1, 4, 7, 10} and start.day == 1
    if frequency == "monthly":
        return start.day == 1
    return frequency != "weekly" or start.weekday() == 0


def _gap(gaps, symbol, reason, **context):
    item = {"symbol": symbol, "reason": reason, **context}
    if item not in gaps:
        gaps.append(item)


def _fetch(demand, record, profile, reader, catalog, registry, tables, gaps):
    symbol, series = record.indicator_key, record.series
    if profile.availability != "observations":
        _gap(gaps, symbol, "no_observations")
        return []
    if profile.frequency != demand.period.frequency or "frequency" in profile.model_generated_fields:
        _gap(gaps, symbol, "frequency_mismatch")
        return []
    table = tables.get((series.source_id, series.table_key))
    if table is None:
        _gap(gaps, symbol, "table_unavailable")
        return []
    declared = []
    spec = registry.get(series.source_id)
    if spec is not None and spec.scope.get("area_code_scheme"):
        declared.append(spec.scope["area_code_scheme"])
    for key in table.dataset_keys:
        dataset = catalog.get_dataset(key)
        if dataset is not None and dataset.extras.get("area_code_scheme"):
            declared.append(dataset.extras["area_code_scheme"])
    scheme = detect_scheme(profile.areas, declared)
    if scheme is None or "areas" in profile.model_generated_fields:
        _gap(gaps, symbol, "unknown_area_scheme")
        return []
    desired = {code for code, alpha3 in BY_SCHEME.get(scheme, {}).items() if alpha3 in demand.entities.countries}
    desired.update(region.code for region in demand.entities.regions if region.scheme == scheme)
    if not desired:
        _gap(gaps, symbol, "area_unmapped")
        return []
    start, end = _bounds(demand.period)
    where = [f"source_id = {_literal(series.source_id)}", f"table_key = {_literal(series.table_key)}",
             f"indicator = {_literal(series.indicator)}",
             "indicator_code IS NULL" if series.indicator_code is None else
             f"indicator_code = {_literal(series.indicator_code)}",
             f"json_array_length(json_keys(dims)) = {len(series.fixed_dimensions)}",
             "area_code IN (" + ",".join(_literal(c) for c in sorted(desired)) + ")",
             f"time_start >= DATE {_literal(start.isoformat())}", f"time_end <= DATE {_literal(end.isoformat())}"]
    for key, value in sorted(series.fixed_dimensions.items()):
        pointer = "/" + key.replace("~", "~0").replace("/", "~1")
        where.append(f"json_extract_string(dims, {_literal(pointer)}) = {_literal(value)}")
    sql = "SELECT * FROM observations WHERE " + " AND ".join(where) + " ORDER BY time_start,area_code,obs_id"
    releases = (reader.list_releases(source_id=series.source_id, table_key=series.table_key)
                if demand.revisions == "all" or any(op.op == "revision_diff" for op in demand.operations) else [None])
    output, last = [], {}
    generation = GenerationToken.parse(reader.token).generation
    for position, release in enumerate(releases):
        result = reader.query_readonly(sql, max_rows=2**63 - 2, as_of_release=release)
        if len(result.row_evidence) != len(result.rows):
            raise ExecutionError("observation identity unavailable")
        unique, points = set(), set()
        for values, evidence in zip(result.rows, result.row_evidence):
            row = dict(zip(result.columns, values, strict=True))
            if evidence.generation != generation or len(evidence.identities) != 1:
                raise ExecutionError("observation identity unavailable")
            identity = evidence.identities[0]
            if any(row.get(k) != getattr(identity, k) for k in type(identity).model_fields):
                raise ExecutionError("observation identity mismatch")
            dims = json.loads(row["dims"]) if isinstance(row["dims"], str) else row["dims"]
            if ((row["source_id"], row["table_key"], row["indicator"], row["indicator_code"], dims) !=
                    (series.source_id, series.table_key, series.indicator, series.indicator_code, series.fixed_dimensions)):
                raise ExecutionError("series identity mismatch")
            if row["area_code"] not in desired or not (start <= row["time_start"] <= row["time_end"] <= end):
                raise ExecutionError("query filter mismatch")
            if not _period_valid(row, demand.period.frequency):
                _gap(gaps, symbol, "calendar_mismatch")
                continue
            alpha3 = country(row["area_code"], scheme)
            entity = ("country", alpha3) if alpha3 in demand.entities.countries else (scheme, row["area_code"])
            value = row["value"]
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                raise ExecutionError("SQL value is not a finite Decimal")
            if value is None:
                _gap(gaps, symbol, "missing_input")
            ref = (identity.source_id, identity.table_key, identity.obs_id)
            signature = (value, row["note"], row["unit"], entity, row["time_start"], row["time_end"])
            if ref in unique:
                raise ExecutionError("ambiguous observation identity")
            unique.add(ref)
            point = (entity, row["time_start"])
            if point in points:
                raise ExecutionError("ambiguous series observation")
            points.add(point)
            if demand.revisions == "all" and ref in last and last[ref] == signature:
                continue
            last[ref] = signature
            citation = CitationEntry(inputs=[InputObservation(
                source_id=identity.source_id, table_key=identity.table_key, obs_id=identity.obs_id,
                snapshot_sha256=identity.snapshot_sha256, release_id=identity.release_id or identity.snapshot_sha256)])
            output.append(Cell(entity, row["time_start"], series.source_id, series_key(series),
                               identity.release_id or identity.snapshot_sha256, row["released_at"], position,
                               row["unit"], value, citation,
                               None if "scale" in profile.model_generated_fields else profile.scale, row["note"]))
    if not output:
        _gap(gaps, symbol, "no_matching_observations")
    return output


def _join_key(cell, demand, *, release=True):
    key = [cell.entity, cell.period]
    for field in demand.align.keys:
        if field == "source":
            key.append(cell.source)
        elif release and field in {"release", "release_id"}:
            key.append((cell.release_id, cell.released_at))
        elif field == "unit":
            key.append(cell.unit)
    return tuple(key)


def _at_time(items, anchor, period):
    candidates = [c for c in items if c.entity == anchor.entity and c.series == anchor.series and c.period == period
                  and c.position <= anchor.position]
    if not candidates:
        return None
    latest = max(c.position for c in candidates)
    selected = [c for c in candidates if c.position == latest]
    if len(selected) != 1:
        raise ExecutionError("ambiguous temporal alignment")
    return selected[0]


def _derived(operation, anchor, inputs, demand, gaps, *, absent=False):
    reason = None
    if absent:
        reason = "incomplete_window"
    if (demand.align.require_same_unit and len(inputs) > 1
            and (any(c.unit is None for c in inputs) or len({c.unit for c in inputs}) > 1)):
        reason = "unit_mismatch"
    scales = {str(c.scale.multiplier) for c in inputs if c.scale is not None and c.scale.multiplier is not None}
    if operation.op != "rescale" and len(scales) > 1:
        reason = "scale_mismatch"
    calculation = calculate(operation, [c.value for c in inputs], scale=anchor.scale)
    reason = reason or calculation.reason
    if reason:
        _gap(gaps, operation.id, reason, country=anchor.entity[1], period=anchor.period.isoformat())
    all_inputs, chain, steps = {}, {}, {}
    for cell in inputs:
        for obs in cell.citation.inputs:
            all_inputs[obs.model_dump_json()] = obs
        for op in cell.citation.operation_chain:
            chain[op.id] = op
        for step in cell.steps:
            steps[json.dumps(step, sort_keys=True)] = step
    chain[operation.id] = operation
    step = {"operation": operation.model_dump(mode="json"), "details": calculation.details}
    if reason:
        step["reason"] = reason
    steps[json.dumps(step, sort_keys=True)] = step
    citation = CitationEntry(inputs=list(all_inputs.values()), operation_chain=list(chain.values()))
    unit = ("percent" if operation.op == "growth_rate" else "1" if operation.op in {"ratio", "lead_lag_corr"}
            else anchor.unit)
    scale = anchor.scale
    if operation.op == "rescale" and reason is None:
        # Result scale is explicit and confirmed; future rescaling records this chain.
        from datumoj.eltiro.models import Scale

        scale = Scale(multiplier=operation.target_scale, evidence=anchor.scale.evidence)
    return replace(anchor, value=None if reason else calculation.value, citation=citation, unit=unit, scale=scale,
                   steps=tuple(steps.values()),
                   spread_range=Decimal(calculation.details["range"]) if not reason and "range" in calculation.details
                   else None, note=anchor.note)


def _operate(operation, columns, demand, gaps):
    first = columns[operation_inputs(operation)[0]]
    output = []
    op = operation.op
    for anchor in first:
        if op == "rescale":
            output.append(_derived(operation, anchor, [anchor], demand, gaps))
        elif op == "revision_diff":
            group = [c for c in first if (c.entity, c.period, c.series) == (anchor.entity, anchor.period, anchor.series)]
            baseline = min(group, key=lambda c: c.position)
            output.append(_derived(operation, anchor, [anchor, baseline], demand, gaps))
        elif op in {"diff_lag", "growth_rate", "moving_average"}:
            offsets = [0, -operation.lag] if op != "moving_average" else list(range(1 - operation.window, 1))
            inputs = [_at_time(first, anchor, _shift(anchor.period, offset, demand.period.frequency)) for offset in offsets]
            output.append(_derived(operation, anchor, [c for c in inputs if c is not None], demand, gaps,
                                   absent=any(c is None for c in inputs)))
        elif op == "source_spread":
            group = [c for c in first if _join_key(c, demand) == _join_key(anchor, demand)]
            if len({c.source for c in group}) != len(group):
                _gap(gaps, operation.id, "ambiguous_source_alignment")
                output.append(_derived(operation, anchor, [anchor], demand, gaps, absent=True))
            else:
                output.append(_derived(operation, anchor, [anchor, *(c for c in group if c is not anchor)], demand, gaps))
        else:
            right = columns[operation.right]
            target = _shift(anchor.period, -operation.lag, demand.period.frequency) if op == "lead_lag_corr" else anchor.period
            candidates = [c for c in right if _join_key(replace(c, period=anchor.period), demand) == _join_key(anchor, demand)
                          and c.period == target]
            if not candidates:
                _gap(gaps, operation.id, "alignment_missing")
                output.append(_derived(operation, anchor, [anchor], demand, gaps, absent=True))
            for other in candidates:
                if op == "lead_lag_corr":
                    inputs = []
                    absent = False
                    for offset in range(1 - operation.window, 1):
                        a = _at_time(first, anchor, _shift(anchor.period, offset, demand.period.frequency))
                        b = _at_time(right, other, _shift(other.period, offset, demand.period.frequency))
                        absent |= a is None or b is None
                        inputs.extend(c for c in (a, b) if c is not None)
                else:
                    inputs, absent = [anchor, other], False
                cell = _derived(operation, anchor, inputs, demand, gaps, absent=absent)
                cell = replace(cell, series=json.dumps([anchor.series, other.series], ensure_ascii=False))
                output.append(cell)
    return output


def _execute(demand, *, data_root, registry, consumer, generation_token):
    demand = DemandSpec.model_validate(demand.model_dump())
    if demand.consumer != consumer.id:
        raise ExecutionError("consumer mismatch")
    supported = {"country", "region", "year", "quarter", "month", "week", "day", "time", "period", "source",
                 "release", "release_id", "unit"}
    if set(demand.align.keys) - supported:
        raise ExecutionError("unsupported alignment key")
    internal = Path(data_root).absolute() / "eltiro"
    for path in (consumer.inbox, consumer.outbox):
        external = (Path(data_root) / path).resolve()
        if external.is_relative_to(internal) or internal.is_relative_to(external):
            raise ExecutionError("consumer directory overlaps internal state")
    started = datetime.now(UTC).isoformat()
    gaps = []
    with open_generation_views(data_root, generation_token) as views:
        token = views.generation_token
        if generation_token is not None and token != generation_token:
            raise ExecutionError("generation token mismatch")
        generation = GenerationToken.parse(token).generation
        reader = CheckedObservations(views.observations, token)
        reader.validate()
        with SQLiteProfileStore(data_root) as profiles, SQLiteMatchStore(data_root) as matches:
            if not profiles.list(generation):
                # The previous numeric generation may not have profiles; 802 safely rebuilds on a miss.
                generate_profiles(generation, observations=reader, catalog=views.catalog, chunks=views.chunks,
                                  registry=registry, store=profiles,
                                  previous_generation=generation - 1 if generation > 1 else None)
            match_demand(demand, generation, profiles=profiles, consumer=consumer, observations=reader,
                         catalog=views.catalog, chunks=views.chunks, store=matches, vector=False)
            records = matches.for_demand(demand.id, demand.version, generation)
            bindings = [r for r in records if r.confirmation != "rejected" and (r.confirmation == "confirmed" or r.auto_bind)]
            columns = {i.key: [] for i in demand.indicators}
            tables = {(t.source_id, t.table_key): t for t in reader.describe().tables}
            for indicator in demand.indicators:
                candidates = [r for r in records if r.indicator_key == indicator.key]
                if any(r.confirmation == "pending" and not r.auto_bind for r in candidates):
                    _gap(gaps, indicator.key, "pending_confirmation")
                selected = [r for r in bindings if r.indicator_key == indicator.key]
                if not selected:
                    _gap(gaps, indicator.key, "unbound_indicator")
                for record in selected:
                    if record.series.source_id not in consumer.allowed_sources:
                        raise ExecutionError("unauthorized binding")
                    profile = profiles.get(generation, record.series)
                    if profile is None or profile.generation != generation:
                        raise ExecutionError("binding profile unavailable")
                    columns[indicator.key].extend(_fetch(demand, record, profile, reader, views.catalog, registry, tables, gaps))
                groups = defaultdict(set)
                for cell in columns[indicator.key]:
                    groups[(cell.entity, cell.period)].add(cell.source)
                requested = {("country", c) for c in demand.entities.countries} | {
                    (r.scheme, r.code) for r in demand.entities.regions}
                for entity in sorted(requested - {key[0] for key in groups}):
                    _gap(gaps, indicator.key, "entity_unavailable", entity=entity[1])
                if len({r.series.source_id for r in selected}) < indicator.min_sources or any(
                        len(sources) < indicator.min_sources for sources in groups.values()):
                    _gap(gaps, indicator.key, "insufficient_sources")
            pending = list(demand.operations)
            while pending:
                ready = [op for op in pending if all(key in columns for key in operation_inputs(op))]
                for operation in ready:
                    columns[operation.id] = _operate(operation, columns, demand, gaps)
                    if not columns[operation.id]:
                        _gap(gaps, operation.id, "no_aligned_inputs")
                    pending.remove(operation)
            rows, citations = [], []
            for symbol, cells in columns.items():
                if demand.revisions == "latest":
                    latest = {}
                    for cell in cells:
                        key = (cell.entity, cell.period, cell.series)
                        if key not in latest or cell.position > latest[key].position:
                            latest[key] = cell
                    cells = list(latest.values())
                for cell in cells:
                    citation_id = f"row-{len(rows)}"
                    rows.append({"citation_id": citation_id, "symbol": symbol,
                                 "country": cell.entity[1] if cell.entity[0] == "country" else None,
                                 "region_scheme": cell.entity[0] if cell.entity[0] != "country" else None,
                                 "region": cell.entity[1] if cell.entity[0] != "country" else None,
                                 "period": cell.period, "source": cell.source, "series": cell.series,
                                 "release_id": cell.release_id, "released_at": cell.released_at,
                                 "release_position": cell.position, "unit": cell.unit, "value": cell.value,
                                 "spread_range": cell.spread_range, "note": cell.note})
                    citations.append({"citation_id": citation_id, "citation": cell.citation.model_dump(mode="json"),
                                      "steps": list(cell.steps)})
            record = {"demand_id": demand.id, "demand_version": demand.version, "generation": generation,
                      "generation_token": token, "started_at": started,
                      "finished_at": datetime.now(UTC).isoformat(),
                      "bindings": [r.model_dump(mode="json") for r in bindings], "layout": "long/v1"}
            return RunStore(data_root).write(record, rows, citations, gaps, validate=reader.validate)


def execute_demand(demand, *, data_root, registry, consumer, generation_token=None):
    """Execute a registered DemandSpec; host supplies trusted registry/consumer.

    Public errors intentionally omit demand text, SQL, source metadata and values.
    No model client is created, even if the demand permits models.
    """
    try:
        return _execute(demand, data_root=data_root, registry=registry, consumer=consumer,
                        generation_token=generation_token)
    except Exception:
        raise ExecutionError("Eltiro execution failed; check fixed generation and local configuration.") from None


def run_registered(demand_id, *, version=None, generation_token=None, data_root, registry, demands):
    if version is None:
        demand = next((d for d in demands.list_active() if d.id == demand_id), None)
    else:
        demand = demands.get(demand_id, version)
    if demand is None:
        raise ExecutionError("registered demand unavailable")
    consumers = load_consumers(data_root)
    consumer = next((c for c in consumers.consumers if c.id == demand.consumer), None)
    if consumer is None:
        raise ExecutionError("consumer unavailable")
    return execute_demand(demand, data_root=data_root, registry=registry, consumer=consumer,
                          generation_token=generation_token)
