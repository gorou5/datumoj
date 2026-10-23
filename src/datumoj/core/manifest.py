"""抓取记录库（SQLite）：实现 ``datumoj.core.contracts.Manifest``。

每次抓取（含失败的）原样记一行；解析状态另放一张表。写入都在 ``BEGIN IMMEDIATE`` 事务里，
多个连接同时写会排队（等锁超时见 ``timeout``）。schema 版本记在 ``schema_version`` 表里，v1 原子迁移到 v2，未知版本报错。
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import AwareDatetime, JsonValue, TypeAdapter

from datumoj.core.contracts import (
    DuplicateError,
    FailureSummary,
    NotFoundError,
    ParseRecord,
    ParseStatus,
    SchemaVersionError,
)
from datumoj.core.models import FetchOutcome, Freshness, Snapshot, SnapshotOrder, parse_context_fingerprint, redact_url

SCHEMA_VERSION = 2
_SUCCESS = {FetchOutcome.ok, FetchOutcome.not_modified}
_JSON_DICT = TypeAdapter(dict[str, JsonValue])

_SCHEMA = """
CREATE TABLE schema_version (version INTEGER NOT NULL);
CREATE TABLE snapshots (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL,
    resource_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,   -- UTC，定宽 ISO，字符串序即时间序
    outcome TEXT NOT NULL,
    sha256 TEXT,
    meta TEXT NOT NULL,
    snapshot TEXT NOT NULL      -- Snapshot 的 JSON，原样还原
);
CREATE INDEX snapshots_url ON snapshots (resource_url, fetched_at, seq);
CREATE INDEX snapshots_source ON snapshots (source_id, fetched_at, seq);
CREATE INDEX snapshots_sha ON snapshots (sha256);
CREATE TABLE parsed (
    sha256 TEXT NOT NULL,
    parser TEXT NOT NULL,
    version TEXT NOT NULL,
    context_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    PRIMARY KEY (sha256, parser, version, context_fingerprint)
);
"""


def _ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")


def _from_ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=UTC)


class SqliteManifest:
    def __init__(self, path: str | Path, timeout: float = 30.0) -> None:
        self._db = sqlite3.connect(str(path), timeout=timeout, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        try:
            self._init_schema()
        except BaseException:
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def _init_schema(self) -> None:
        with self._tx() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                for stmt in _SCHEMA.split(";\n"):
                    if stmt.strip():
                        db.execute(stmt)
                db.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
                return
            row = db.execute("SELECT version FROM schema_version").fetchone() if "schema_version" in tables else None
            if row is not None and row[0] == 1:
                db.execute("ALTER TABLE parsed RENAME TO parsed_v1")
                db.execute("CREATE TABLE parsed (sha256 TEXT NOT NULL, parser TEXT NOT NULL, version TEXT NOT NULL, "
                           "context_fingerprint TEXT NOT NULL, status TEXT NOT NULL, error TEXT, "
                           "PRIMARY KEY (sha256, parser, version, context_fingerprint))")
                db.execute("INSERT INTO parsed SELECT sha256, parser, version, '', status, error FROM parsed_v1 "
                           "ORDER BY rowid")
                db.execute("DROP TABLE parsed_v1")
                db.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
                return
            if row is None or row[0] != SCHEMA_VERSION:
                found = "没有版本记录" if row is None else f"版本 {row[0]}"
                raise SchemaVersionError(f"manifest schema 版本不符：代码要求 {SCHEMA_VERSION}，库里{found}")

    # ---------- 抓取记录 ----------

    def record_snapshot(self, snapshot: Snapshot, meta: Mapping[str, JsonValue] | None = None) -> None:
        checked = _JSON_DICT.validate_python(dict(meta or {}))  # 不是 JSON 就抛 ValueError
        meta_text = json.dumps(checked, ensure_ascii=False)
        with self._tx() as db:
            try:
                db.execute(
                    "INSERT INTO snapshots (snapshot_id, source_id, resource_url, fetched_at, outcome, sha256, meta, snapshot)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (snapshot.snapshot_id, snapshot.source_id, snapshot.resource_url, _ts(snapshot.fetched_at),
                     snapshot.outcome.value, snapshot.sha256, meta_text, snapshot.model_dump_json()),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateError(f"snapshot_id 重复：{snapshot.snapshot_id}") from exc

    def snapshot_meta(self, snapshot_id: str) -> dict[str, JsonValue]:
        row = self._db.execute("SELECT meta FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"没有快照 {snapshot_id}")
        return json.loads(row[0])

    def snapshot_order(self, snapshot_id: str) -> SnapshotOrder | None:
        row = self._db.execute("SELECT fetched_at, seq FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        return None if row is None else SnapshotOrder(fetched_at=_from_ts(row[0]), seq=row[1])

    def _query(self, where: str, args: Iterable[object] = (), tail: str = "") -> list[Snapshot]:
        rows = self._db.execute(
            f"SELECT snapshot FROM snapshots WHERE {where} ORDER BY fetched_at, seq {tail}", tuple(args)
        ).fetchall()
        return [Snapshot.model_validate_json(r[0]) for r in rows]

    def latest_snapshot(self, resource_url: str) -> Snapshot | None:
        found = self._query("resource_url=?", [redact_url(resource_url)])
        return found[-1] if found else None

    def last_success(self, resource_url: str) -> Snapshot | None:
        found = self._query("resource_url=? AND outcome=?", [redact_url(resource_url), FetchOutcome.ok.value])
        return found[-1] if found else None

    def snapshots_for(self, source_id: str) -> list[Snapshot]:
        return self._query("source_id=?", [source_id])

    def snapshots_by_sha256(self, sha256: str) -> list[Snapshot]:
        return self._query("sha256=?", [sha256])

    def failure_summary(self, source_id: str) -> FailureSummary:
        snaps = self.snapshots_for(source_id)
        counts = Counter(s.outcome for s in snaps if s.outcome not in _SUCCESS)
        latest: dict[str, Snapshot] = {}
        for s in snaps:  # 升序，后写的覆盖先写的
            latest[s.resource_url] = s
        failing = sorted((s for s in latest.values() if s.outcome not in _SUCCESS),
                         key=lambda s: s.fetched_at, reverse=True)
        return FailureSummary(source_id=source_id, counts=dict(counts), latest_failures=failing)

    def freshness(self, source_id: str) -> Freshness:
        rows = self._db.execute(
            "SELECT resource_url, MAX(fetched_at) FROM snapshots WHERE source_id=? AND outcome IN (?,?)"
            " GROUP BY resource_url",
            (source_id, FetchOutcome.ok.value, FetchOutcome.not_modified.value),
        ).fetchall()
        if not rows:
            return Freshness()
        times = [_from_ts(r[1]) for r in rows]
        as_of = max(times)
        return Freshness(as_of=as_of, oldest_fetched_at=min(times), by_source={source_id: as_of})

    # ---------- 解析状态 ----------

    def record_parse(self, record: ParseRecord) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO parsed (sha256, parser, version, context_fingerprint, status, error) "
                "VALUES (?,?,?,?,?,?)",
                (record.sha256, record.parser, record.version, record.context_fingerprint, record.status.value, record.error),
            )

    def get_parse(self, sha256: str, parser: str, version: str, context_fingerprint: str = "") -> ParseRecord | None:
        row = self._db.execute(
            "SELECT sha256, parser, version, status, error, context_fingerprint FROM parsed "
            "WHERE sha256=? AND parser=? AND version=? AND context_fingerprint=?",
            (sha256, parser, version, context_fingerprint),
        ).fetchone()
        return None if row is None else ParseRecord(sha256=row[0], parser=row[1], version=row[2], status=row[3], error=row[4],
                                                    context_fingerprint=row[5])

    def parse_stats(self, source_id: str, since: datetime | None = None) -> dict[ParseStatus, int]:
        args: list[object] = [source_id]
        boundary = ""
        if since is not None:
            args.append(_ts(TypeAdapter(AwareDatetime).validate_python(since)))
            boundary = " AND s.fetched_at >= ?"
        rows = self._db.execute(
            "SELECT snapshot, meta FROM snapshots s WHERE s.source_id=?" + boundary, args,
        ).fetchall()
        latest = {
            (digest, context): ParseStatus(status)
            for digest, context, status in self._db.execute(
                "SELECT sha256, context_fingerprint, status FROM parsed WHERE sha256 IN "
                "(SELECT sha256 FROM snapshots WHERE source_id=?) ORDER BY rowid", (source_id,),
            )
        }
        counts: Counter[ParseStatus] = Counter()
        for snapshot_json, meta_json in rows:
            snap = Snapshot.model_validate_json(snapshot_json)
            context = parse_context_fingerprint(snap, json.loads(meta_json))
            status = latest.get((snap.sha256, context), latest.get((snap.sha256, "")))
            if status is not None:
                counts[status] += 1
        return dict(counts)
