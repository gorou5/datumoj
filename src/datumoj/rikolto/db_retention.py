"""Bound the local databases: superseded versions, expired bulletins, manifest rows, free pages.

Input is a local data directory and a clock; output is a plan that can be executed. Public
results hold only counts, bytes and source IDs. Execute only while holding the write
lease and ``local_write``; the planner itself never writes.

Version age uses db_usage's rule: a version is superseded when the first later stored
version of the same document was fetched. Unknown times are never deleted. Bulletin
expiry uses the same fetch time of the current version.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from datetime import datetime, timedelta
from itertools import groupby
from pathlib import Path

from datumoj.rikolto.core.contracts import SourceSpec
from datumoj.rikolto.core.state import _raw_policy
from datumoj.rikolto.db_usage import _SOURCE, _bucket, _time, _versions
from datumoj.rikolto.index.vector import delete_vectors, maintain_vectors
from datumoj.rikolto.store.chunks import SqliteChunkStore

RECENT = timedelta(hours=48)
DEFAULT_INCREMENTAL_PAGES = 5000
MAX_INCREMENTAL_PAGES = 50000
_BATCH = 500


def settings(env: Mapping[str, str] = os.environ) -> dict:
    """``db_retention.enabled`` (default off) and the incremental-vacuum page cap."""
    try:
        pages = int(env.get("R2_DB_INCREMENTAL_PAGES", ""))
    except ValueError:
        pages = DEFAULT_INCREMENTAL_PAGES
    return {"enabled": env.get("R2_DB_RETENTION", "off").lower() == "on",
            "incremental_pages": min(max(pages, 1), MAX_INCREMENTAL_PAGES)}


def _open(path: Path, *, write: bool) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError("database_missing")
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    if not write:
        db.execute("PRAGMA query_only=ON")
    return db


def _batches(items: Sequence, size: int = _BATCH) -> Iterable[Sequence]:
    for offset in range(0, len(items), size):
        yield items[offset:offset + size]


def _fetch_times(manifest: sqlite3.Connection) -> tuple[dict, dict]:
    """Fetch time of each (source, sha) as db_usage dates it; shared or unparsable hashes are absent."""
    runs = defaultdict(list)
    for source, sha, url, stamp in manifest.execute(
        "WITH successful AS (SELECT source_id,resource_url,sha256,fetched_at, "
        "lag(sha256) OVER (PARTITION BY source_id,resource_url ORDER BY fetched_at,seq) AS previous "
        "FROM snapshots WHERE outcome='ok' AND sha256 IS NOT NULL) "
        "SELECT source_id,sha256,resource_url,fetched_at FROM successful "
        "WHERE previous IS NULL OR previous<>sha256"
    ):
        runs[source, sha].append((url, _time(stamp)))
    times = {key: [stamp for _, stamp in rows]
             if len({url for url, _ in rows}) == 1 and all(stamp is not None for _, stamp in rows) else []
             for key, rows in runs.items()}
    return {key: max(values) for key, values in times.items() if values}, times


def observation_shas(root: Path) -> set[str]:
    """Snapshot hashes cited by observation Parquet files; their manifest rows keep provenance."""
    import pyarrow.parquet as pq

    shas: set[str] = set()
    for path in (*root.glob("parquet/*/*.parquet"), *root.glob("parquet-history/*/*/*.parquet")):
        if "snapshot_sha256" in pq.ParquetFile(path).schema_arrow.names:
            shas.update(v for v in pq.read_table(path, columns=["snapshot_sha256"]).column(0).to_pylist() if v)
    return shas


def _plan_versions(cdb, mdb, now: datetime, index_hours: Mapping[str, int], held: set[str]) -> dict:
    """Which (doc_key, sha) pairs go. Same supersession rule as db_usage's estimate A."""
    starts, times = _fetch_times(mdb)
    delete: list[tuple[str, str]] = []
    expired_keys: list[str] = []
    superseded = defaultdict(lambda: {"versions": 0, "chunks": 0, "bytes": 0})
    expired = defaultdict(lambda: {"documents": 0, "chunks": 0, "bytes": 0})
    counts = {"waiting_superseded": 0, "unknown_superseded": 0, "unknown_index_age": 0, "held": 0}
    kept_shas: set[str] = set()
    for doc_key, group in groupby(_versions(cdb), key=lambda row: row[0]):
        versions = list(group)
        source = next((row[2] for row in versions if row[2]), None)
        hours = index_hours.get(source)
        current = versions[0][3]
        current_stamp = starts.get((source, current))
        if hours is not None and current is not None:
            if current_stamp is None or current_stamp > now:
                counts["unknown_index_age"] += 1
            elif now - current_stamp >= timedelta(hours=hours):
                if any(row[1] in held for row in versions):
                    counts["held"] += 1
                else:
                    _SOURCE.validate_python(source)
                    expired_keys.append(doc_key)
                    for _, sha, _, _, documents, chunks, size in versions:
                        delete.append((doc_key, sha))
                        _add(expired[source], documents=documents, chunks=chunks, bytes=size)
                    continue
        for _, sha, _, _, documents, chunks, size in versions:
            if sha != current:
                stamp = starts.get((source, sha))
                later = [value for _, next_sha, next_source, *_ in versions
                         for value in times.get((next_source, next_sha), [])
                         if stamp is not None and value > stamp]
                replacement = min(later) if later and current_stamp is not None and stamp < current_stamp else None
                age = _bucket(replacement.isoformat() if replacement else None, now, old=True)
                if age == "lt_48h":
                    counts["waiting_superseded"] += 1
                elif age == "unknown":
                    counts["unknown_superseded"] += 1
                elif sha in held:
                    counts["held"] += 1
                else:
                    _SOURCE.validate_python(source)
                    delete.append((doc_key, sha))
                    _add(superseded[source], versions=1, chunks=chunks, bytes=size)
                    continue
            kept_shas.add(sha)
    return {"delete": delete, "expired_keys": expired_keys, "superseded": superseded, "expired": expired,
            "counts": counts, "document_shas": kept_shas}


def _add(target: dict, **values: int) -> None:
    for field, value in values.items():
        target[field] += value


def _plan_manifest(mdb, now: datetime, specs: Sequence[SourceSpec], keep_shas: set[str],
                   raw_present: Callable[[str], bool] | None, manifest_path: Path, deleted: set) -> dict:
    """Snapshot rows to drop; everything else in the manifest follows from which hashes lose all rows."""
    protected = _raw_policy(manifest_path, specs)[1]
    held_states = {r[0] for r in mdb.execute(
        "SELECT snapshot_id FROM publish_states WHERE state IN ('staged','failed')")}
    rows = mdb.execute("SELECT seq,snapshot_id,source_id,resource_url,fetched_at,outcome,sha256 "
                       "FROM snapshots ORDER BY fetched_at,seq").fetchall()
    newest: dict = {}
    for seq, _, source, url, _, outcome, _ in rows:
        key = (source, url)
        newest[key, "any"] = seq
        if outcome in ("ok", "not_modified"):
            newest[key, "success"] = seq
        if outcome == "ok":
            newest[key, "ok"] = seq
    latest = set(newest.values())
    drop, kept = [], set()
    for seq, snapshot_id, _, _, fetched_at, outcome, sha in rows:
        stamp = _time(fetched_at)
        keep = (seq in latest or stamp is None or now - stamp < RECENT or snapshot_id in held_states
                or (sha is not None and (sha in protected or sha in keep_shas
                                         or raw_present is None or raw_present(sha))))
        (kept.add(sha) if keep else drop.append((snapshot_id, sha)))
    kept.discard(None)
    gone = {sha for _, sha in drop if sha is not None} - kept
    by_sha = defaultdict(set)
    for doc_key, sha in deleted:
        by_sha[sha].add(doc_key)
    trims = []
    for sha, parser, version, context, doc_keys in mdb.execute(
        "SELECT sha256,parser,version,context_fingerprint,doc_keys FROM parsed WHERE doc_keys IS NOT NULL"
    ):
        if sha in by_sha and sha not in gone:
            names = json.loads(doc_keys)
            left = [name for name in names if name not in by_sha[sha]]
            if left != names:
                trims.append((json.dumps(left, ensure_ascii=False), sha, parser, version, context))
    return {"drop": [snapshot_id for snapshot_id, _ in drop], "gone": sorted(gone), "trims": trims,
            "kept": len(rows) - len(drop)}


def _apply_manifest(path: Path, plan: dict) -> dict:
    result = {"parsed_deleted": 0, "parse_writers_deleted": 0, "raw_markers_deleted": 0,
              "publish_states_deleted": 0}
    with closing(_open(path, write=True)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.executemany("UPDATE parsed SET doc_keys=? WHERE sha256=? AND parser=? AND version=? "
                           "AND context_fingerprint=?", plan["trims"])
            for batch in _batches(plan["drop"]):
                marks = ",".join("?" * len(batch))
                result["publish_states_deleted"] += db.execute(
                    f"DELETE FROM publish_states WHERE snapshot_id IN ({marks})", batch).rowcount
                db.execute(f"DELETE FROM snapshots WHERE snapshot_id IN ({marks})", batch)
            for batch in _batches(plan["gone"]):
                marks = ",".join("?" * len(batch))
                for table, field in (("parsed", "parsed_deleted"), ("parse_writers", "parse_writers_deleted"),
                                     ("raw_allowed_missing", "raw_markers_deleted")):
                    result[field] += db.execute(f"DELETE FROM {table} WHERE sha256 IN ({marks})", batch).rowcount
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    return result


def _page_stats(db) -> tuple[int, int, int]:
    return (db.execute("PRAGMA page_size").fetchone()[0], db.execute("PRAGMA page_count").fetchone()[0],
            db.execute("PRAGMA freelist_count").fetchone()[0])


def _maintain_files(root: Path, *, execute: bool, gate: Callable[[int], dict] | None, pages: int) -> dict:
    """One-off VACUUM (needs the capacity gate) converts a file to incremental auto-vacuum;
    afterwards only a capped ``incremental_vacuum``."""
    report = {"files": {}, "incremental": {}, "one_off": {"pending": [], "estimated_bytes": 0, "done": [],
                                                         "skipped_reason": None}}
    pending = []
    for name in ("manifest", "chunks"):
        path = root / f"{name}.sqlite"
        if not path.is_file():
            continue
        with closing(_open(path, write=False)) as db:
            size, count, free = _page_stats(db)
            mode = db.execute("PRAGMA auto_vacuum").fetchone()[0]
        report["files"][name] = {"before_bytes": path.stat().st_size, "free_bytes": free * size}
        if mode == 2:
            report["incremental"][name] = {"pages": 0}
        else:
            pending.append((name, path, (count - free) * size))
    one_off = report["one_off"]
    one_off.update(pending=[name for name, *_ in pending], estimated_bytes=sum(size for *_, size in pending))
    if pending:
        decision = gate(one_off["estimated_bytes"]) if gate is not None else {"allowed": True}
        if not decision["allowed"]:
            one_off["skipped_reason"] = decision["reason"]
        elif execute:
            for name, path, _ in pending:
                with closing(_open(path, write=True)) as db:
                    db.execute("PRAGMA auto_vacuum=INCREMENTAL")
                    db.execute("VACUUM")
                one_off["done"].append(name)
                report["incremental"][name] = {"pages": 0}
    if execute:
        for name in report["incremental"]:
            if name in one_off["done"]:
                continue
            with closing(_open(root / f"{name}.sqlite", write=True)) as db:
                free = db.execute("PRAGMA freelist_count").fetchone()[0]
                db.execute(f"PRAGMA incremental_vacuum({int(pages)})").fetchall()
                report["incremental"][name]["pages"] = free - db.execute("PRAGMA freelist_count").fetchone()[0]
        for name, row in report["files"].items():
            row["after_bytes"] = (root / f"{name}.sqlite").stat().st_size
    return report


def run(root: str | Path, now: datetime, specs: Sequence[SourceSpec], *, execute: bool = False,
        raw_present: Callable[[str], bool] | None = None, gate: Callable[[int], dict] | None = None,
        incremental_pages: int = DEFAULT_INCREMENTAL_PAGES, observations: set[str] | None = None) -> dict:
    """Plan (and with ``execute`` apply) the retention rules for the databases under ``root``.

    ``raw_present(sha)`` says whether a raw object still exists; a hash-bearing snapshot row
    is dropped only when its raw is known gone (None = unknown, keep). ``gate(bytes)`` is the
    capacity decision for the one-off VACUUM (None = local, always allowed).
    """
    root = Path(root)
    if now.tzinfo is None:
        raise ValueError("aware_now_required")
    pages = min(max(int(incremental_pages), 1), MAX_INCREMENTAL_PAGES)
    index_hours = {spec.id: spec.scope["index_retention_hours"]
                   for spec in specs if "index_retention_hours" in spec.scope}
    shas = observation_shas(root) if observations is None else observations
    report = {"mode": "applied" if execute else "dry_run"}
    with closing(_open(root / "manifest.sqlite", write=False)) as mdb:
        staged = {r[0] for r in mdb.execute(
            "SELECT s.sha256 FROM snapshots s JOIN publish_states p USING(snapshot_id) "
            "WHERE p.state IN ('staged','failed') AND s.sha256 IS NOT NULL")}
        if (root / "chunks.sqlite").is_file():
            with closing(_open(root / "chunks.sqlite", write=False)) as cdb:
                versions = _plan_versions(cdb, mdb, now, index_hours, staged)
        else:
            versions = {"delete": [], "expired_keys": [], "superseded": {}, "expired": {},
                        "counts": {}, "document_shas": set()}
        manifest = _plan_manifest(mdb, now, specs, versions["document_shas"] | shas, raw_present,
                                  root / "manifest.sqlite", set(versions["delete"]))
    report["superseded"] = {"sources": {k: dict(v) for k, v in sorted(versions["superseded"].items())},
                            **{key: versions["counts"].get(key, 0) for key in (
                                "waiting_superseded", "unknown_superseded", "held")}}
    report["index_expired"] = {"sources": {k: dict(v) for k, v in sorted(versions["expired"].items())},
                               "unknown_index_age": versions["counts"].get("unknown_index_age", 0)}
    report["manifest"] = {"snapshots_dropped": len(manifest["drop"]), "snapshots_kept": manifest["kept"],
                          "hashes_released": len(manifest["gone"]), "parse_records_trimmed": len(manifest["trims"])}
    report["vectors"] = {"deleted": 0}
    if execute:
        # Manifest first: a failure after it leaves orphan documents (retried next round),
        # never a snapshot whose parse record still claims documents that are gone.
        report["manifest"].update(_apply_manifest(root / "manifest.sqlite", manifest))
        if versions["delete"]:
            with closing(SqliteChunkStore(root / "chunks.sqlite")) as store:
                ids = store.delete_versions(versions["delete"], versions["expired_keys"])
            if (root / "keyword.sqlite").is_file():
                from datumoj.rikolto.index.fts import SqliteKeywordIndex

                with closing(SqliteKeywordIndex(root / "keyword.sqlite")) as keyword:
                    for doc_key in versions["expired_keys"]:
                        keyword.delete(doc_key)
            report["vectors"]["deleted"] = delete_vectors(root / "vectors", ids)
            if report["vectors"]["deleted"]:
                maintain_vectors(root / "vectors", timedelta(hours=1))
    report["vacuum"] = _maintain_files(root, execute=execute, gate=gate, pages=pages)
    return report
