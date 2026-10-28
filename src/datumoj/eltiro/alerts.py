"""Evidence-only alerts, stored locally without demand text or outbound delivery."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3

from datumoj.eltiro.match_store import MatchResult, series_key
from datumoj.eltiro.models import DemandSpec, SeriesIdentity, content_hash


@dataclass(frozen=True)
class Alert:
    alert_id: str
    consumer: str
    demand: str
    demand_version: int
    indicator_key: str
    series: SeriesIdentity
    kind: str
    generation: int
    details: tuple[str, ...]
    acked: bool = False


class SQLiteAlertStore:
    """Append/deduplicate alerts; acknowledgements survive retries and reopen."""

    def __init__(self, data_root: str | Path):
        self.path = Path(data_root) / "eltiro" / "alerts.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.execute("""CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY, consumer TEXT NOT NULL, demand TEXT NOT NULL,
            payload TEXT NOT NULL, acked INTEGER NOT NULL DEFAULT 0 CHECK(acked IN (0,1)))""")

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _add(self, demand, indicator, series, kind, generation, details):
        if kind not in demand.run.notify_on:
            return 0
        identity = dict(consumer=demand.consumer, demand=demand.id, demand_version=demand.version,
                        indicator_key=indicator, series=series.model_dump(mode="json"),
                        kind=kind, generation=generation)
        alert_id = content_hash(identity)
        # Only opaque demand identifiers and fixed rule/field names, never concept,
        # purpose, examples, matching reasons or source prose.
        payload = json.dumps(dict(identity, alert_id=alert_id, details=details), sort_keys=True)
        with self._db:
            cursor = self._db.execute("INSERT OR IGNORE INTO alerts(id,consumer,demand,payload) VALUES(?,?,?,?)",
                                      (alert_id, demand.consumer, demand.id, payload))
        return cursor.rowcount

    def list_alerts(self, *, consumer=None, demand=None, include_acked=False) -> list[Alert]:
        if type(include_acked) is not bool:
            raise ValueError("include_acked must be boolean")
        clauses, args = [], []
        for column, value in (("consumer", consumer), ("demand", demand)):
            if value is not None:
                clauses.append(f"{column}=?")
                args.append(value)
        if not include_acked:
            clauses.append("acked=0")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        result = []
        for payload, acked in self._db.execute("SELECT payload,acked FROM alerts" + where + " ORDER BY rowid", args):
            data = json.loads(payload)
            data["series"] = SeriesIdentity.model_validate(data["series"])
            data["details"] = tuple(data["details"])
            result.append(Alert(**data, acked=bool(acked)))
        return result

    def ack_alert(self, alert_id: str) -> None:
        with self._db:
            cursor = self._db.execute("UPDATE alerts SET acked=1 WHERE id=?", (alert_id,))
            if not cursor.rowcount:
                raise KeyError("unknown alert")


def list_alerts(data_root, *, consumer=None, demand=None, include_acked=False):
    with SQLiteAlertStore(data_root) as store:
        return store.list_alerts(consumer=consumer, demand=demand, include_acked=include_acked)


def ack_alert(alert_id, *, data_root):
    with SQLiteAlertStore(data_root) as store:
        store.ack_alert(alert_id)


def _next_period(end, frequency):
    if frequency in ("daily", "weekly"):
        return end + timedelta(days=1 if frequency == "daily" else 7)
    months = {"monthly": 1, "quarterly": 3, "annual": 12}.get(frequency)
    if months is None:
        return None
    year, month = divmod(end.year * 12 + end.month - 1 + months, 12)
    return date(year, month + 1, min(end.day, monthrange(year, month + 1)[1]))


def _bound(record):
    return record.confirmation != "rejected" and (record.confirmation == "confirmed" or record.auto_bind)


def generate_alerts(
    demand: DemandSpec, generation: int, previous: MatchResult, current: MatchResult,
    *, old_profiles, new_profiles, as_of: date, store: SQLiteAlertStore,
) -> int:
    """Compare evidenced candidates/bindings; caller supplies dates and frozen profiles."""
    if type(as_of) is not date:
        raise ValueError("as_of must be a date")
    count = 0

    def emit(record, kind, *details):
        return store._add(demand, record.indicator_key, record.series, kind, generation, details)

    for record in previous.records:
        if not _bound(record):
            continue
        key = series_key(record.series)
        old, new = old_profiles.get(key), new_profiles.get(key)
        if old is None or new is None:
            continue  # A deletion alone proves neither staleness nor a new definition.
        fields = [field for field in ("unit", "methodology", "survey")
                  if getattr(old, field) != getattr(new, field)]
        if old.scale.multiplier != new.scale.multiplier:
            fields.append("scale.multiplier")
        if fields:
            count += emit(record, "definition_changed", *fields)
        if (old.time_end is not None and new.time_end is not None and new.time_end <= old.time_end):
            deadline = _next_period(new.time_end, new.frequency)
            if deadline is not None and as_of > deadline:
                count += emit(record, "binding_stale", "time_end", "frequency")

    for indicator in demand.indicators:
        old = [r for r in previous.records if r.indicator_key == indicator.key and r.confirmation != "rejected"]
        new = [r for r in current.records if r.indicator_key == indicator.key and r.confirmation != "rejected"]
        bindings = [r for r in old if _bound(r)]
        bound_keys = {series_key(r.series) for r in bindings}
        old_by_key = {series_key(r.series): r for r in old}
        for record in new:
            key = series_key(record.series)
            prior = old_by_key.get(key)
            if bindings:
                baseline = max(r.confidence for r in bindings)
                better = (key not in bound_keys and record.confidence > baseline
                          and (prior is None or prior.confidence <= baseline or record.confidence > prior.confidence))
            else:
                better = record.auto_bind and (prior is None or not prior.auto_bind)
            if better:
                count += emit(record, "new_candidate", "higher_confidence" if bindings else "auto_bind_available")
        # Unknown old availability is not evidence of a gap.
        if all(series_key(r.series) in old_profiles
               and old_profiles[series_key(r.series)].availability in ("catalog_only", "document_only") for r in old):
            for record in new:
                profile = new_profiles.get(series_key(record.series))
                if profile is not None and profile.availability == "observations":
                    count += emit(record, "gap_filled", "observations_available")
    return count
