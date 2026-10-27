"""Eltiro-owned matches; SQLite transactions never touch Rikolto state."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from pydantic import TypeAdapter

from datumoj.eltiro.models import Confirmation, Generation, Id, MatchRecord, SeriesIdentity, Version


def series_key(series: SeriesIdentity) -> str:
    checked = SeriesIdentity.model_validate(series.model_dump())
    return json.dumps(checked.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


@dataclass
class MatchResult:
    records: list[MatchRecord]
    missing_evidence: list[SeriesIdentity]
    vector_skipped: bool

    @property
    def recommendations(self) -> list[MatchRecord]:
        return [r for r in self.records if r.confirmation != "rejected"]

    @property
    def queue(self) -> list[MatchRecord]:
        return [r for r in self.records if r.confirmation == "pending" and not r.auto_bind]


class SQLiteMatchStore:
    """MatchStore plus atomic per-demand/generation matcher results.

    ``put`` remains an upsert as required by the contract. ``save_result`` pins a
    completed run, including empty results and missing evidence, for exact reruns.
    Matcher reruns only read the saved result, including subsequent human decisions.
    Explicit protocol-level put() calls can still upsert records.
    """

    def __init__(self, data_root: str | Path):
        self.path = Path(data_root) / "eltiro" / "matches.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS matches (
                position INTEGER PRIMARY KEY, demand TEXT NOT NULL, version INTEGER NOT NULL,
                indicator TEXT NOT NULL, series TEXT NOT NULL, generation INTEGER NOT NULL,
                payload TEXT NOT NULL, UNIQUE(demand, version, indicator, series, generation));
            CREATE TABLE IF NOT EXISTS match_runs (
                demand TEXT NOT NULL, version INTEGER NOT NULL, generation INTEGER NOT NULL,
                config TEXT NOT NULL, metadata TEXT NOT NULL,
                PRIMARY KEY(demand, version, generation));
        """)

    def close(self) -> None:
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _put(self, record: MatchRecord) -> None:
        key = (record.demand_id, record.demand_version, record.indicator_key,
               series_key(record.series), record.generation)
        old = self._db.execute("""SELECT payload FROM matches WHERE
            demand=? AND version=? AND indicator=? AND series=? AND generation=?""", key).fetchone()
        if old:
            status = MatchRecord.model_validate_json(old[0]).confirmation
            record = MatchRecord.model_validate({**record.model_dump(), "confirmation": status,
                                                "auto_bind": record.auto_bind and status != "rejected"})
        self._db.execute("""INSERT INTO matches(demand,version,indicator,series,generation,payload)
            VALUES(?,?,?,?,?,?) ON CONFLICT(demand,version,indicator,series,generation)
            DO UPDATE SET payload=excluded.payload""", (*key, record.model_dump_json()))

    def put(self, record: MatchRecord) -> None:
        record = MatchRecord.model_validate(record.model_dump())
        with self._db:
            self._put(record)

    def for_demand(self, demand_id: str, version: int, generation: int | None = None) -> list[MatchRecord]:
        sql = "SELECT payload FROM matches WHERE demand=? AND version=?"
        args = [demand_id, version]
        if generation is not None:
            sql += " AND generation=?"
            args.append(generation)
        return [MatchRecord.model_validate_json(row[0]) for row in self._db.execute(sql + " ORDER BY position", args)]

    def set_confirmation(self, demand_id, version, indicator_key, series, generation, confirmation) -> None:
        confirmation = TypeAdapter(Confirmation).validate_python(confirmation)
        key = (demand_id, version, indicator_key, series_key(series), generation)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._db.execute("""SELECT payload FROM matches WHERE
                demand=? AND version=? AND indicator=? AND series=? AND generation=?""", key).fetchone()
            if row is None:
                raise KeyError("unknown match")
            record = MatchRecord.model_validate_json(row[0])
            record = MatchRecord.model_validate({**record.model_dump(), "confirmation": confirmation,
                                                "auto_bind": record.auto_bind and confirmation != "rejected"})
            self._db.execute("""UPDATE matches SET payload=? WHERE
                demand=? AND version=? AND indicator=? AND series=? AND generation=?""",
                (record.model_dump_json(), *key))

    def load_result(self, demand_id: str, version: int, generation: int, config: str) -> MatchResult | None:
        row = self._db.execute("SELECT config,metadata FROM match_runs WHERE demand=? AND version=? AND generation=?",
                               (demand_id, version, generation)).fetchone()
        if row is None:
            return None
        if row[0] != config:
            raise ValueError("matching configuration changed for a completed generation")
        meta = json.loads(row[1])
        records = self.for_demand(demand_id, version, generation)
        # Decisions can change after matching; present confirmed first, rejected last.
        records.sort(key=lambda r: {"confirmed": 0, "pending": 1, "rejected": 2}[r.confirmation])
        return MatchResult(records, [SeriesIdentity.model_validate(s) for s in meta["missing_evidence"]],
                           meta["vector_skipped"])

    def save_result(self, demand_id: str, version: int, generation: int, config: str,
                    records: Sequence[MatchRecord], missing_evidence: Sequence[SeriesIdentity],
                    vector_skipped: bool) -> MatchResult:
        TypeAdapter(Generation).validate_python(generation)
        TypeAdapter(Id).validate_python(demand_id)
        TypeAdapter(Version).validate_python(version)
        if not isinstance(vector_skipped, bool):
            raise ValueError("invalid vector skipped flag")
        checked = [MatchRecord.model_validate(r.model_dump()) for r in records]
        keys = [(r.indicator_key, series_key(r.series)) for r in checked]
        key_set = set(keys)
        if len(key_set) != len(keys) or any(
            (r.demand_id, r.demand_version, r.generation) != (demand_id, version, generation) for r in checked
        ):
            raise ValueError("duplicate match or mismatched run identity")
        meta = json.dumps({"missing_evidence": [SeriesIdentity.model_validate(s.model_dump()).model_dump()
                                              for s in missing_evidence], "vector_skipped": vector_skipped})
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            existing = self.load_result(demand_id, version, generation, config)
            if existing is not None:
                return existing
            for position, indicator, series in self._db.execute(
                "SELECT position,indicator,series FROM matches WHERE demand=? AND version=? AND generation=?",
                (demand_id, version, generation),
            ).fetchall():
                if (indicator, series) not in key_set:
                    self._db.execute("DELETE FROM matches WHERE position=?", (position,))
            for record in checked:
                self._put(record)
            self._db.execute("INSERT INTO match_runs VALUES(?,?,?,?,?)",
                             (demand_id, version, generation, config, meta))
        return self.load_result(demand_id, version, generation, config)
