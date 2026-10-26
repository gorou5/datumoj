"""SQLite 变化日志；事件只追加，重复 event_id 保留首次记录。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import AwareDatetime, TypeAdapter

from datumoj.rikolto.core.models import ChangeEvent, ChangeKind


def _timestamp(value: datetime) -> str:
    value = TypeAdapter(AwareDatetime).validate_python(value)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class SqliteChangeLog:
    """path 是库文件路径，或 :memory:；接线默认使用 data_dir/changes.sqlite。"""

    def __init__(self, path: str | Path, timeout: float = 30.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout 必须 > 0")
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), timeout=timeout)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS changes (
                seq INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE,
                detected_at TEXT NOT NULL,
                source_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                event_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS changes_time ON changes(detected_at, seq);
        """)

    def close(self) -> None:
        self._db.close()

    def append(self, events: Iterable[ChangeEvent]) -> None:
        with self._db:
            self._db.executemany(
                "INSERT INTO changes(event_id, detected_at, source_id, kind, event_json) VALUES(?,?,?,?,?) "
                "ON CONFLICT(event_id) DO NOTHING",
                ((e.event_id, _timestamp(e.detected_at), e.source_id, e.kind, e.model_dump_json()) for e in events),
            )

    def list(self, since: datetime | None = None, until: datetime | None = None,
             source_ids: Sequence[str] | None = None, kinds: Sequence[ChangeKind] | None = None,
             limit: int = 1000) -> list[ChangeEvent]:
        if limit < 0:
            raise ValueError("limit 必须非负")
        clauses, params = [], []
        for operator, value in ((">=", since), ("<=", until)):
            if value is not None:
                clauses.append(f"detected_at {operator} ?")
                params.append(_timestamp(value))
        for column, values in (("source_id", source_ids), ("kind", kinds)):
            if values is not None:
                clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
                params.extend(values)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._db.execute(
            "SELECT event_json FROM changes" + where + " ORDER BY detected_at, seq LIMIT ?", [*params, limit]
        ).fetchall()
        return [ChangeEvent.model_validate_json(row[0]) for row in rows]
