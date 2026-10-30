"""SQLite DemandRegistry: immutable versions and transactional active-demand limits."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from datumoj.eltiro.models import DemandSpec, Sha256, content_hash, load_demand


class DemandConflict(ValueError):
    """The version or demand owner cannot be replaced."""


class DemandLimitExceeded(ValueError):
    """Activating another demand would exceed the consumer limit."""


class SQLiteDemandRegistry:
    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS demands (
                id TEXT NOT NULL,
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                consumer TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                file_sha256 TEXT NOT NULL,
                active INTEGER NOT NULL CHECK (active IN (0, 1)),
                PRIMARY KEY (id, version)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_version
                ON demands(id) WHERE active = 1;
        """)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def validate(self, path_or_bytes: str | Path | bytes) -> DemandSpec:
        return load_demand(path_or_bytes)

    def register(
        self, demand: DemandSpec, *, active: bool = True, file_sha256: str | None = None,
        max_active_demands: int | None = None,
    ) -> None:
        demand = DemandSpec.model_validate(demand.model_dump(by_alias=True))
        content = json.dumps(demand.model_dump(mode="json", by_alias=True), sort_keys=True, ensure_ascii=False)
        digest = TypeAdapter(Sha256).validate_python(
            file_sha256 if file_sha256 is not None else content_hash(demand.model_dump(mode="json")),
        )
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute(
                "SELECT content FROM demands WHERE id = ? AND version = ?", (demand.id, demand.version),
            ).fetchone()
            owner = self.connection.execute("SELECT consumer FROM demands WHERE id = ? LIMIT 1", (demand.id,)).fetchone()
            if (existing and existing["content"] != content) or (owner and owner["consumer"] != demand.consumer):
                raise DemandConflict("demand identity is immutable")
            if active and max_active_demands is not None:
                count = self.connection.execute(
                    "SELECT COUNT(*) FROM demands WHERE consumer = ? AND active = 1 AND id != ?",
                    (demand.consumer, demand.id),
                ).fetchone()[0]
                if count >= max_active_demands:
                    raise DemandLimitExceeded("active demand limit reached")
            if active:
                self.connection.execute("UPDATE demands SET active = 0 WHERE id = ?", (demand.id,))
            if existing:
                if active:
                    self.connection.execute(
                        "UPDATE demands SET active = 1 WHERE id = ? AND version = ?", (demand.id, demand.version),
                    )
            else:
                self.connection.execute(
                    "INSERT INTO demands VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (demand.id, demand.version, content, demand.consumer, datetime.now(UTC).isoformat(),
                     digest, int(active)),
                )

    def get(self, demand_id: str, version: int) -> DemandSpec | None:
        row = self.connection.execute(
            "SELECT content FROM demands WHERE id = ? AND version = ?", (demand_id, version),
        ).fetchone()
        return DemandSpec.model_validate_json(row[0]) if row else None

    def set_active(self, demand_id: str, version: int, active: bool) -> None:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if self.connection.execute(
                "SELECT 1 FROM demands WHERE id = ? AND version = ?", (demand_id, version),
            ).fetchone() is None:
                raise KeyError((demand_id, version))
            if active:
                self.connection.execute("UPDATE demands SET active = 0 WHERE id = ?", (demand_id,))
            self.connection.execute(
                "UPDATE demands SET active = ? WHERE id = ? AND version = ?", (int(active), demand_id, version),
            )

    def list_active(self) -> list[DemandSpec]:
        return [DemandSpec.model_validate_json(row[0]) for row in self.connection.execute(
            "SELECT content FROM demands WHERE active = 1 ORDER BY id",
        )]

    def list_records(self) -> list[dict]:
        """Local operator metadata, without demand text."""
        return [dict(row) for row in self.connection.execute(
            "SELECT id, version, consumer, registered_at, file_sha256, active FROM demands ORDER BY id, version",
        )]
