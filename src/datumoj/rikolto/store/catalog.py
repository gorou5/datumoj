"""SQLite catalog storage with an FTS5 trigram index."""

from __future__ import annotations

import sqlite3
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from datumoj.rikolto.core.contracts import (
    CatalogFilter,
    CatalogHit,
    ContentState,
    ResourceEntry,
    max_state,
)
from datumoj.rikolto.core.models import Dataset, Resource

SCHEMA_VERSION = 1


def _norm_text(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold().strip()


def _norm_format(value: str | None) -> str:
    value = _norm_text(value).lstrip(".").strip()
    return value or "未知"


def _norm_publisher(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").strip()


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _json_search_text(value: str | None) -> str:
    return _norm_text(value)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS datasets (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_key TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL,
    publisher_norm TEXT NOT NULL,
    modified_at TEXT,
    dataset_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS resources (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_key TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    url TEXT NOT NULL,
    format_norm TEXT NOT NULL,
    license_norm TEXT NOT NULL,
    state TEXT NOT NULL,
    resource_json TEXT NOT NULL,
    UNIQUE(dataset_key, resource_id)
);
CREATE INDEX IF NOT EXISTS resources_dataset_seq ON resources(dataset_key, seq);
CREATE INDEX IF NOT EXISTS resources_url ON resources(url);
CREATE INDEX IF NOT EXISTS resources_format ON resources(format_norm);
CREATE VIRTUAL TABLE IF NOT EXISTS catalog_fts USING fts5(
    title, description, tags, tokenize='trigram'
);
"""


class SqliteCatalogStore:
    """Persistent catalog store. ``path`` names the SQLite database file."""

    def __init__(self, path: str | Path, timeout: float = 30.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout 必须 > 0")
        self._readonly = False
        db_path = str(path)
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, timeout=timeout, isolation_level=None)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    @classmethod
    def open_readonly(cls, path: str | Path, timeout: float = 30.0):
        """Open a standalone snapshot without initialization, migration or sidecars."""
        if timeout <= 0:
            raise ValueError("timeout 必须 > 0")
        path = Path(path).resolve(strict=True)
        store = cls.__new__(cls)
        store._readonly = True
        store._db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True,
                                    timeout=timeout, isolation_level=None)
        try:
            store._db.execute("PRAGMA query_only = ON")
            tables = {r[0] for r in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            rows = store._db.execute("SELECT version FROM schema_version").fetchall() if "schema_version" in tables else []
            if rows != [(SCHEMA_VERSION,)]:
                raise ValueError("catalog schema 版本不符")
        except BaseException:
            store.close()
            raise
        return store

    def _require_writable(self) -> None:
        if self._readonly:
            raise PermissionError("只读视图禁止写入")

    def close(self) -> None:
        self._db.close()

    def _init_schema(self) -> None:
        tables = {row[0] for row in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            self._db.executescript(_SCHEMA)
            self._db.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
            return
        row = self._db.execute("SELECT version FROM schema_version").fetchone() if "schema_version" in tables else None
        if row is None or row[0] != SCHEMA_VERSION:
            found = "没有版本记录" if row is None else f"版本 {row[0]}"
            raise ValueError(f"catalog schema 版本不符：代码要求 {SCHEMA_VERSION}，库里{found}")
        # IF NOT EXISTS allows opening a database created by an interrupted older initialization.
        self._db.executescript(_SCHEMA)

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        self._require_writable()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        else:
            self._db.execute("COMMIT")

    def _save_dataset(self, db: sqlite3.Connection, dataset: Dataset) -> int:
        db.execute(
            "INSERT INTO datasets(dataset_key, source_id, publisher_norm, modified_at, dataset_json) "
            "VALUES(?,?,?,?,?) ON CONFLICT(dataset_key) DO UPDATE SET "
            "source_id=excluded.source_id, publisher_norm=excluded.publisher_norm, "
            "modified_at=excluded.modified_at, dataset_json=excluded.dataset_json",
            (dataset.dataset_key, dataset.source_id, _norm_publisher(dataset.publisher),
             _timestamp(dataset.modified_at), dataset.model_dump_json()),
        )
        return db.execute("SELECT seq FROM datasets WHERE dataset_key=?", (dataset.dataset_key,)).fetchone()[0]

    def _index_dataset(self, db: sqlite3.Connection, dataset: Dataset, rowid: int) -> None:
        db.execute("DELETE FROM catalog_fts WHERE rowid=?", (rowid,))
        db.execute(
            "INSERT INTO catalog_fts(rowid, title, description, tags) VALUES(?,?,?,?)",
            (rowid, _json_search_text(dataset.title), _json_search_text(dataset.description),
             _json_search_text(" ".join(dataset.tags))),
        )

    def upsert(self, datasets: Sequence[Dataset] = (), resources: Sequence[Resource] = ()) -> None:
        self._require_writable()
        incoming = {d.dataset_key for d in datasets}
        with self._tx() as db:
            known = {row[0] for row in db.execute("SELECT dataset_key FROM datasets")} | incoming
            missing = sorted({r.dataset_key for r in resources} - known)
            if missing:
                raise ValueError(f"资源所属的数据集不存在：{missing[0]}")
            for dataset in datasets:
                rowid = self._save_dataset(db, dataset)
                self._index_dataset(db, dataset, rowid)
            for resource in resources:
                db.execute(
                    "INSERT INTO resources(dataset_key, resource_id, url, format_norm, license_norm, state, resource_json) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(dataset_key, resource_id) DO UPDATE SET "
                    "url=excluded.url, format_norm=excluded.format_norm, license_norm=excluded.license_norm, "
                    "resource_json=excluded.resource_json",
                    (resource.dataset_key, resource.resource_id, resource.url, _norm_format(resource.format),
                     _norm_text(resource.license) or "未知", ContentState.catalog_only.value, resource.model_dump_json()),
                )

    def get_dataset(self, dataset_key: str) -> Dataset | None:
        row = self._db.execute("SELECT dataset_json FROM datasets WHERE dataset_key=?", (dataset_key,)).fetchone()
        return None if row is None else Dataset.model_validate_json(row[0])

    def resources_for(self, dataset_key: str) -> list[ResourceEntry]:
        rows = self._db.execute(
            "SELECT resource_json, state FROM resources WHERE dataset_key=? ORDER BY seq", (dataset_key,)
        ).fetchall()
        return [ResourceEntry(resource=Resource.model_validate_json(raw), state=ContentState(state)) for raw, state in rows]

    def set_content_state(self, resource_url: str, state: ContentState) -> int:
        self._require_writable()
        try:
            state = ContentState(state)
        except ValueError as exc:
            raise ValueError(f"无效的正文状态：{state!r}") from exc
        with self._tx() as db:
            cursor = db.execute("UPDATE resources SET state=? WHERE url=?", (state.value, resource_url))
            return cursor.rowcount

    def _passes(self, dataset_key: str, source_id: str, publisher_norm: str, modified_at: str | None,
                filters: CatalogFilter) -> bool:
        if filters.source_ids is not None and source_id not in filters.source_ids:
            return False
        if filters.publishers is not None and publisher_norm not in {_norm_publisher(x) for x in filters.publishers}:
            return False
        if filters.formats is not None:
            wanted = {_norm_format(value) for value in filters.formats}
            if not wanted:
                return False
            row = self._db.execute(
                "SELECT 1 FROM resources WHERE dataset_key=? AND format_norm IN (" +
                ",".join("?" for _ in wanted) + ") LIMIT 1",
                (dataset_key, *sorted(wanted)),
            ).fetchone()
            if row is None:
                return False
        if filters.modified_since is not None:
            since = _timestamp(filters.modified_since)
            if modified_at is None or modified_at < since:
                return False
        return True

    def _hit(self, row, score: float) -> CatalogHit:
        dataset = Dataset.model_validate_json(row[4])
        entries = self.resources_for(dataset.dataset_key)
        return CatalogHit(dataset=dataset, resources=entries,
                          state=max_state(entry.state for entry in entries), score=score)

    def search(self, query: str, filters: CatalogFilter | None = None, k: int = 20) -> list[CatalogHit]:
        if k < 1:
            raise ValueError("k 必须 ≥ 1")
        query = _norm_text(query)
        filters = filters or CatalogFilter()
        if query and len(query) >= 3:
            match = '"' + query.replace('"', '""') + '"'
            candidate_rows = self._db.execute(
                "SELECT d.seq, d.dataset_key, d.source_id, d.publisher_norm, d.dataset_json, "
                "d.modified_at FROM datasets d JOIN catalog_fts f ON f.rowid=d.seq WHERE catalog_fts MATCH ?",
                (match,),
            ).fetchall()
        else:
            # FTS5 trigram cannot match strings shorter than three characters.
            candidate_rows = self._db.execute(
                "SELECT seq, dataset_key, source_id, publisher_norm, dataset_json, modified_at FROM datasets"
            ).fetchall()

        hits: list[tuple[float, str, CatalogHit]] = []
        for row in candidate_rows:
            dataset = Dataset.model_validate_json(row[4])
            title, description = _norm_text(dataset.title), _norm_text(dataset.description)
            tags = [_norm_text(tag) for tag in dataset.tags]
            if query:
                count_title = title.count(query)
                count_description = description.count(query)
                count_tags = sum(tag.count(query) for tag in tags)
                score = float(3 * count_title + count_description + 2 * count_tags)
                if score == 0:
                    continue
            else:
                score = 0.0
            if not self._passes(row[1], row[2], row[3], row[5], filters):
                continue
            hit = self._hit(row, score)
            hits.append((score, dataset.dataset_key, hit))
        hits.sort(key=lambda item: (-item[0], item[1]))
        return [hit for _, _, hit in hits[:k]]

