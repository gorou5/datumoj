"""Eltiro-owned SQLite profiles and private input fingerprints (no Rikolto writes).

The caller supplies the data root; only ``eltiro/profiles.sqlite`` is opened.
Generations persist until explicitly replaced; replacing also removes obsolete cache
entries. SQLite/I/O errors propagate and failed replacements leave the old generation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
import sqlite3

from pydantic import TypeAdapter

from datumoj.eltiro.models import Generation, SeriesIdentity, SeriesProfile


def _identity(series: SeriesIdentity) -> str:
    checked = SeriesIdentity.model_validate(series.model_dump())
    return json.dumps(checked.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


class SQLiteProfileStore:
    """Implements ProfileStore; use close() or a context manager to release SQLite."""

    def __init__(self, data_root: str | Path):
        self.path = Path(data_root) / "eltiro" / "profiles.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        with self._db:
            self._db.execute("""CREATE TABLE IF NOT EXISTS profiles (
                generation INTEGER NOT NULL, position INTEGER NOT NULL,
                identity TEXT NOT NULL, payload TEXT NOT NULL, unit_key TEXT,
                PRIMARY KEY (generation, identity), UNIQUE (generation, position))""")
            self._db.execute("""CREATE TABLE IF NOT EXISTS input_fingerprints (
                generation INTEGER NOT NULL, unit_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
                PRIMARY KEY (generation, unit_key))""")

    def close(self) -> None:
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def write_generation(self, generation: int, profiles: Sequence[SeriesProfile]) -> None:
        self._replace(generation, [(None, p) for p in profiles], {})

    def write_units(
        self, generation: int, units: Mapping[str, tuple[str, Sequence[SeriesProfile]]],
    ) -> None:
        """Atomically replace profiles AND their private incremental cache metadata."""
        fingerprints = {}
        profiles = []
        for key, (fingerprint, items) in units.items():
            if (not isinstance(key, str) or not key or not isinstance(fingerprint, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)):
                raise ValueError("invalid input unit or fingerprint")
            fingerprints[key] = fingerprint
            profiles.extend((key, item) for item in items)
        self._replace(generation, profiles, fingerprints)

    def _replace(self, generation, profiles, fingerprints):
        TypeAdapter(Generation).validate_python(generation)
        rows, seen = [], set()
        for position, (unit_key, profile) in enumerate(profiles):
            profile = SeriesProfile.model_validate(profile.model_dump())
            identity = _identity(profile.series)
            if profile.generation != generation or identity in seen:
                raise ValueError("duplicate series or mismatched generation")
            seen.add(identity)
            rows.append((generation, position, identity, profile.model_dump_json(), unit_key))
        with self._db:
            self._db.execute("DELETE FROM profiles WHERE generation = ?", (generation,))
            self._db.execute("DELETE FROM input_fingerprints WHERE generation = ?", (generation,))
            self._db.executemany("INSERT INTO profiles VALUES (?, ?, ?, ?, ?)", rows)
            self._db.executemany("INSERT INTO input_fingerprints VALUES (?, ?, ?)",
                                 [(generation, key, value) for key, value in fingerprints.items()])

    def get(self, generation: int, series: SeriesIdentity) -> SeriesProfile | None:
        row = self._db.execute("SELECT payload FROM profiles WHERE generation = ? AND identity = ?",
                               (generation, _identity(series))).fetchone()
        return SeriesProfile.model_validate_json(row[0]) if row else None

    def list(self, generation: int) -> list[SeriesProfile]:
        return [SeriesProfile.model_validate_json(row[0]) for row in self._db.execute(
            "SELECT payload FROM profiles WHERE generation = ? ORDER BY position", (generation,),
        )]

    def unchanged_unit(self, generation: int, key: str, fingerprint: str) -> list[SeriesProfile] | None:
        """None is a cache miss; an empty list is a known unit producing no profiles."""
        rows = self._db.execute(
            """SELECT f.fingerprint, p.payload FROM input_fingerprints f LEFT JOIN profiles p
               ON p.generation = f.generation AND p.unit_key = f.unit_key
               WHERE f.generation = ? AND f.unit_key = ? ORDER BY p.position""",
            (generation, key),
        ).fetchall()
        if not rows or rows[0][0] != fingerprint:
            return None
        return [SeriesProfile.model_validate_json(row[1]) for row in rows if row[1] is not None]
