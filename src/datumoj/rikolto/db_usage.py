"""Read-only SQLite/Lance accounting; public results contain aggregates only.

SQLite table bytes include owned indexes with dbstat, or column payload lengths
as a fallback. Version/snapshot bytes always use column payload lengths; these
are deletion estimates, not predictions of compacted files or R2 reclamation.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from contextlib import ExitStack, closing
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path

from pydantic import TypeAdapter

from datumoj.rikolto.core.models import SourceId

_FILES = ("manifest.sqlite", "chunks.sqlite", "changes.sqlite", "catalog.sqlite")
_TABLES = {
    "schema_version", "sqlite_sequence", "snapshots", "parsed", "parse_writers",
    "publish_states", "raw_allowed_missing", "documents", "current_versions",
    "published_orders", "chunks", "changes", "datasets", "resources", "catalog_fts",
    "catalog_fts_data", "catalog_fts_idx", "catalog_fts_content", "catalog_fts_docsize",
    "catalog_fts_config",
}
_OLD = ("lt_48h", "2_7d", "7_30d", "ge_30d", "unknown")
_AGE = ("lt_7d", "7_30d", "ge_30d", "unknown")
_SOURCE = TypeAdapter(SourceId)


def _ro(path):
    # Only standalone, frozen copies are accepted; immutable avoids sidecars.
    db = sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA query_only=ON")
    return db


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _lengths(db, table, alias=""):
    prefix = alias + "." if alias else ""
    return " + ".join(
        f"coalesce(length(cast({prefix}{_quote(row[1])} AS BLOB)),0)"
        for row in db.execute(f"PRAGMA table_info({_quote(table)})")
    ) or "0"


def _dbstat(db):
    return dict(db.execute("SELECT name, sum(pgsize) FROM dbstat GROUP BY name"))


def sqlite_usage(path: Path) -> dict:
    """Measure a supported standalone SQLite file without opening a writer."""
    with closing(_ro(path)) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        if set(tables) - _TABLES:
            raise ValueError("unsupported_table")
        try:
            pages = _dbstat(db)
        except sqlite3.OperationalError:
            pages = None
        result = {"file_bytes": path.stat().st_size,
                  "page_count": db.execute("PRAGMA page_count").fetchone()[0],
                  "page_size": db.execute("PRAGMA page_size").fetchone()[0],
                  "freelist_count": db.execute("PRAGMA freelist_count").fetchone()[0],
                  "bytes_method": "dbstat" if pages is not None else "column_lengths", "tables": {}}
        for table in tables:
            expression = _lengths(db, table) if pages is None else "0"
            count, payload = db.execute(
                f"SELECT count(*), coalesce(sum({expression}),0) FROM {_quote(table)}"
            ).fetchone()
            owners = [table] + [r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,),
            )]
            size = sum(pages.get(name, 0) for name in owners) if pages is not None else payload
            result["tables"][table] = {"rows": count, "bytes": size}
        return result


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Manifest stores UTC timestamps without a suffix.
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _bucket(value, now, *, old=False):
    stamp = _time(value)
    if stamp is None or stamp > now:
        return "unknown"
    days = (now - stamp).total_seconds() / 86400
    if old and days < 2:
        return "lt_48h"
    if days < 7:
        return "2_7d" if old else "lt_7d"
    return "7_30d" if days < 30 else "ge_30d"


def _counts():
    return {"documents": 0, "chunks": 0, "bytes": 0}


def _add(target, row):
    for field in target:
        target[field] += row[field]


def _manifest(db, now):
    result = {"bytes_method": "column_lengths", "sources": {}}
    expression = _lengths(db, "snapshots")
    db.create_function("age_bucket", 1, lambda value: _bucket(value, now), deterministic=True)
    for source, outcome, age, count, size in db.execute(
        "SELECT source_id, CASE WHEN outcome IN ('ok','not_modified') THEN outcome ELSE 'failed' END AS result, "
        f"age_bucket(fetched_at) AS age, count(*), sum({expression}) "
        "FROM snapshots GROUP BY source_id,result,age ORDER BY source_id,result,age"
    ):
        _SOURCE.validate_python(source)
        stats = result["sources"].setdefault(source, {
            "latest_successful_resources": 0,
            "outcomes": {outcome: {age: {"rows": 0, "bytes": 0} for age in _AGE}
                         for outcome in ("ok", "not_modified", "failed")},
        })
        stats["outcomes"][outcome][age] = {"rows": count, "bytes": size}
    # last_success() uses outcome=ok; not_modified does not carry a new body.
    for source, count in db.execute(
        "SELECT source_id,count(DISTINCT resource_url) FROM snapshots WHERE outcome='ok' GROUP BY source_id"
    ):
        result["sources"][source]["latest_successful_resources"] = count
    return result


def _versions(db):
    # Union also accounts for documents with no chunks and orphan chunks.
    return db.execute(
        "WITH c AS (SELECT doc_key,snapshot_sha256,min(source_id) AS source_id,count(*) AS chunks, "
        f"sum({_lengths(db, 'chunks')}) AS bytes FROM chunks GROUP BY doc_key,snapshot_sha256), "
        "keys AS (SELECT doc_key,snapshot_sha256 FROM documents UNION SELECT doc_key,snapshot_sha256 FROM c) "
        "SELECT k.doc_key,k.snapshot_sha256,coalesce(c.source_id,json_extract(d.document_json,'$.source_id')), "
        "v.snapshot_sha256,CASE WHEN d.doc_key IS NULL THEN 0 ELSE 1 END,coalesce(c.chunks,0), "
        f"coalesce(c.bytes,0)+CASE WHEN d.doc_key IS NULL THEN 0 ELSE {_lengths(db, 'documents', 'd')} END "
        "FROM keys k LEFT JOIN documents d USING(doc_key,snapshot_sha256) "
        "LEFT JOIN c USING(doc_key,snapshot_sha256) LEFT JOIN current_versions v ON v.doc_key=k.doc_key "
        "ORDER BY k.doc_key,k.snapshot_sha256"
    )


def _chunks(db, manifest, now, bulletin_sources):
    runs = defaultdict(list)
    for source, sha, url, stamp in manifest.execute(
        "WITH successful AS (SELECT source_id,resource_url,sha256,fetched_at, "
        "lag(sha256) OVER (PARTITION BY source_id,resource_url ORDER BY fetched_at,seq) AS previous "
        "FROM snapshots WHERE outcome='ok' AND sha256 IS NOT NULL) "
        "SELECT source_id,sha256,resource_url,fetched_at FROM successful "
        "WHERE previous IS NULL OR previous<>sha256"
    ):
        runs[source, sha].append((url, _time(stamp)))
    # Retain every change of content, including returns to a previous hash.
    # A hash shared across resources cannot safely date a particular document.
    times = {key: [stamp for _, stamp in rows]
             if len({url for url, _ in rows}) == 1 and all(stamp is not None for _, stamp in rows) else []
             for key, rows in runs.items()}
    starts = {key: max(values) for key, values in times.items() if values}
    result = {"bytes_method": "column_lengths", "sources": {}}
    plans = {"A": _counts(), "B": _counts()}
    for _, group in groupby(_versions(db), key=lambda row: row[0]):
        versions = list(group)
        for _, sha, source, current, documents, chunks, size in versions:
            _SOURCE.validate_python(source)
            stats = result["sources"].setdefault(source, {
                "current": _counts(), "old": _counts(),
                "old_superseded_age": {age: _counts() for age in _OLD},
                "current_fetched_age": {age: _counts() for age in _AGE},
            })
            counts = {"documents": documents, "chunks": chunks, "bytes": size}
            is_current = sha == current
            _add(stats["current" if is_current else "old"], counts)
            if is_current:
                if source in bulletin_sources:
                    stamp = starts.get((source, sha))
                    age = _bucket(stamp.isoformat() if stamp else None, now)
                    _add(stats["current_fetched_age"][age], counts)
                    if age in {"7_30d", "ge_30d"}:
                        _add(plans["B"], counts)
                continue
            stamp = starts.get((source, sha))
            current_stamp = starts.get((source, current))
            # The first later stored version supersedes this one, not necessarily
            # today's current version. Missing/ambiguous order stays unknown.
            later = [value for _, next_sha, next_source, *_ in versions
                     for value in times.get((next_source, next_sha), [])
                     if stamp is not None and value > stamp]
            replacement = min(later) if later and current_stamp is not None and stamp < current_stamp else None
            age = _bucket(replacement.isoformat() if replacement else None, now, old=True)
            _add(stats["old_superseded_age"][age], counts)
            if age in {"2_7d", "7_30d", "ge_30d"}:
                _add(plans["A"], counts)
    return result, plans


def _chunk_hash(blob):
    row = json.loads(blob)
    fields = [row["text"], row.get("published_at"), row.get("effective_at")]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _vectors(path, chunks):
    result = {"rows": 0, "current_chunk_rows": 0, "old_or_missing_chunk_rows": 0, "directory_bytes": 0,
              "active_table_rows": 0, "retained_table_rows": 0}
    if path is None:
        return result
    import lancedb

    result["directory_bytes"] = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    meta = json.loads((path / "_meta.json").read_bytes())
    generation = meta.get("generation", 0)
    if type(generation) is not int or generation < 0:
        raise ValueError("invalid_vector_generation")
    active = "chunks" if generation == 0 else f"chunks_g{generation}"
    db = lancedb.connect(str(path))
    names = []
    page = db.list_tables()
    while True:
        names.extend(page.tables)
        if page.page_token is None:
            break
        page = db.list_tables(page_token=page.page_token)
    if active not in names:
        raise ValueError("missing_vector_table")
    if chunks is not None:
        chunks.create_function("chunk_hash", 1, _chunk_hash, deterministic=True)
    for name in names:
        # Names are input data and never become public output.
        if name != "chunks" and not (name.startswith("chunks_g") and name[8:].isdigit()):
            raise ValueError("unsupported_vector_table")
        table = db.open_table(name)
        for batch in table.search().select(["chunk_id", "doc_key", "text_hash"]).to_batches(batch_size=500):
            rows = batch.to_pylist()
            result["rows"] += len(rows)
            result["active_table_rows" if name == active else "retained_table_rows"] += len(rows)
            for row in rows:
                found = chunks is not None and chunks.execute(
                    "SELECT 1 FROM chunks c JOIN current_versions v "
                    "ON v.doc_key=c.doc_key AND v.snapshot_sha256=c.snapshot_sha256 "
                    "WHERE c.chunk_id=? AND c.doc_key=? AND chunk_hash(c.chunk_json)=? LIMIT 1",
                    (row["chunk_id"], row["doc_key"], row["text_hash"]),
                ).fetchone()
                result["current_chunk_rows" if found else "old_or_missing_chunk_rows"] += 1
    return result


def measure_usage(*, manifest: Path, chunks: Path | None = None, changes: Path | None = None,
                  catalog: Path | None = None, vectors: Path | None = None,
                  now: datetime | None = None, bulletin_sources=("jp-jma",)) -> dict:
    """Aggregate frozen local files. No leases, network, migrations or compaction.

    Errors propagate to the caller; cloud usage converts them to fixed codes.
    Bulletin sources explicitly opt in; only JMA is included by default.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("aware_now_required")
    paths = dict(zip(_FILES, (manifest, chunks, changes, catalog), strict=True))
    result = {"sqlite": {name: sqlite_usage(path) for name, path in paths.items() if path is not None}}
    with ExitStack() as stack:
        mdb = stack.enter_context(closing(_ro(manifest)))
        cdb = stack.enter_context(closing(_ro(chunks))) if chunks is not None else None
        result["manifest"] = _manifest(mdb, now)
        result["chunks"], result["estimates"] = _chunks(cdb, mdb, now, bulletin_sources) if cdb is not None else (
            {"bytes_method": "column_lengths", "sources": {}}, {"A": _counts(), "B": _counts()},
        )
        result["estimates"]["bytes_method"] = "column_lengths"
        result["vectors"] = _vectors(vectors, cdb)
    return result
