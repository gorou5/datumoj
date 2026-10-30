"""Offline evidence inspection and replay into a separate, empty local directory.

Inspection never constructs stores (their constructors migrate schemas). SQLite
files and any WAL are read into a temporary directory, then opened with mode=ro;
SQLite may create shared-memory files there, never in the inspected directory.
Stop writers before inspection/replay: this is not a live backup protocol.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from contextlib import closing, contextmanager
from dataclasses import asdict, replace
from pathlib import Path

import pyarrow.parquet as pq

from datumoj.rikolto.core.citations import document_snapshot
from datumoj.rikolto.core.contracts import AmbiguousCitation
from datumoj.rikolto.core.errors import safe_reason
from datumoj.rikolto.core.models import Document, FetchOutcome, Snapshot, parse_context_fingerprint

DESCRIPTION = {
    "id": "datumoj-evidence-maintenance", "name": "Evidence diagnosis and replay", "version": "1",
    "description": "Read-only evidence/storage inspection; offline replay into an empty local directory",
    "input": "local data directory; verify_raw or vectors flags", "output": "JSON report",
    "runtime": "Python; local filesystem", "permissions": "read source; write replay target and temporary files",
    "data_sources": "local file sizes, Lance metadata, manifest, raw, chunks and observation Parquet", "license": "unknown",
    "errors": "exceptions; partial replay reported with failed/unsupported snapshots", "maintenance": "active",
    "tests": ["tests/test_diagnose.py", "tests/test_diagnose_storage.py", "tests/integration/test_maple_m0.py"],
}
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def storage(data_dir: str | Path) -> dict:
    """Inspect an existing, quiescent local directory without constructing stores.

    Sizes are logical file bytes, not filesystem allocated blocks. No row content,
    model metadata or absolute paths are returned. Symlinks are refused so reads
    cannot escape the chosen directory. LanceDB metadata reads do not optimize,
    migrate, restore, or clean up tables. Missing metadata means unknown, not zero.
    """
    import lancedb

    root = Path(data_dir)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("expected existing local directory")
    files, directories = {}, {}

    def walk_error(error):
        raise error

    for directory, names, filenames in os.walk(root, onerror=walk_error, followlinks=False):
        base = Path(directory)
        for name in names + filenames:
            if (base / name).is_symlink():
                raise ValueError("symlinks are not supported")
        directories[base.relative_to(root).as_posix()] = 0
        for name in filenames:
            path = base / name
            if not path.is_file():
                raise ValueError("expected regular file")
            files[path.relative_to(root)] = path.stat().st_size
    for path, size in files.items():
        for parent in path.parents:
            directories[parent.as_posix()] += size
    entries = {p.name: directories[p.name] if p.is_dir() else files[Path(p.name)]
               for p in sorted(root.iterdir())}
    tables = []
    vector_root = root / "vectors"
    for path in sorted(vector_root.rglob("*.lance")):
        if not path.is_dir():
            continue
        relative = path.relative_to(root)
        physical = {p.relative_to(relative).as_posix(): size for p, size in files.items()
                    if relative in p.parents}
        report = {"table": path.stem, "relative_path": relative.as_posix(),
                  "bytes": directories[relative.as_posix()]}
        match = re.fullmatch(r"chunks(?:_g(\d+))?", path.stem)
        report["generation"] = int(match.group(1) or 0) if match else None
        try:
            table = lancedb.connect(str(path.parent)).open_table(path.stem)
            versions = table.list_versions()
            version = table.version
            metadata = next(v.get("metadata", {}) for v in versions if v["version"] == version)
            stats = table.stats()
            data_bytes = sum(s for p, s in physical.items() if p.startswith("data/"))
            active_bytes = metadata.get("total_files_size")
            active_bytes = int(active_bytes) if active_bytes is not None else None
            # This delta includes unreferenced files from interrupted writes. It
            # is not the sum of version sizes: versions share immutable files.
            history_bytes = (data_bytes - active_bytes
                             if active_bytes is not None and 0 <= active_bytes <= data_bytes else None)
            stored_rows = metadata.get("total_data_file_rows")
            deleted_rows = metadata.get("total_deletion_file_rows")
            stored_rows = int(stored_rows) if stored_rows is not None else None
            deleted_rows = int(deleted_rows) if deleted_rows is not None else None
            deleted_bytes = None
            if (active_bytes is not None and stored_rows is not None and deleted_rows is not None
                    and 0 <= deleted_rows <= stored_rows):
                deleted_bytes = round(active_bytes * deleted_rows / stored_rows) if stored_rows else 0
            current_manifests = {f"_versions/{version}.manifest",
                                 f"_versions/{2**64 - 1 - version:020d}.manifest"}
            old_manifest_bytes = (sum(s for p, s in physical.items()
                                      if p.startswith("_versions/") and p.endswith(".manifest")
                                      and p not in current_manifests)
                                  if current_manifests.intersection(physical) else None)
            estimates = [history_bytes, deleted_bytes, old_manifest_bytes]
            saving = sum(estimates) if all(n is not None for n in estimates) else None
            report.update(
                status="ok", rows=table.count_rows(), versions=len(versions), latest_version=version,
                data_files=sum(p.startswith("data/") for p in physical), data_bytes=data_bytes,
                latest_referenced_data_bytes=active_bytes,
                historical_or_unreferenced_data_bytes_estimate=history_bytes,
                historical_manifest_bytes=old_manifest_bytes, deleted_rows=deleted_rows,
                deleted_data_bytes_estimate=deleted_bytes,
                deletion_file_bytes=sum(s for p, s in physical.items() if p.startswith("_deletions/")),
                index_bytes=sum(s for p, s in physical.items() if p.startswith("_indices/")),
                small_fragments=stats["fragment_stats"]["num_small_fragments"],
                reclaimable_bytes_estimate=saving,
                retained_bytes_estimate=report["bytes"] - saving if saving is not None else None,
            )
        except Exception as exc:  # noqa: BLE001 -- isolate corrupt tables without exposing their data
            report.update(status="unavailable", error=safe_reason(exc))
        tables.append(report)
    return {"read_only": True, "lancedb_version": lancedb.__version__,
            "total_bytes": directories["."], "entries": entries,
            "directory_bytes": dict(sorted(directories.items())), "vector_tables": tables,
            "estimate_method": "physical data minus latest metadata bytes; deleted rows proportional to latest data; "
                               "older manifests; excludes index/deletion/transaction cleanup and encoding changes",
            "estimate_caveats": ["stop all writers first", "unknown metadata produces null",
                                 "history includes orphan data; cleanup retention may protect it",
                                 "row sizes vary; compaction savings are approximate",
                                 "generation tables remain; no table removal is estimated"]}


@contextmanager
def _readonly(path: Path):
    # Do not use immutable=1 on a WAL database: it would silently ignore the WAL.
    with tempfile.TemporaryDirectory(prefix="datumoj-inspect-") as directory:
        copied = Path(directory) / path.name
        shutil.copyfile(path, copied)
        wal = path.with_name(path.name + "-wal")
        if wal.exists():
            shutil.copyfile(wal, copied.with_name(copied.name + "-wal"))
        db = sqlite3.connect(copied.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            yield db
        finally:
            db.close()


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _rows(db, table):
    # Names are fixed by this module, never supplied by users or source text.
    return [dict(r) for r in db.execute(f"SELECT * FROM {table}")] if table in _tables(db) else []


def _category(items):
    return {"count": len(items), "samples": items[:20]}


class _ManifestView:
    """The citation resolver's read interface, without a migrating store."""

    def __init__(self, rows, writers):
        self.rows = sorted(rows, key=lambda r: (r["snap"].fetched_at, r["seq"]))
        self.writers = {(r["doc_key"], r["sha256"]): r["context_fingerprint"] for r in writers}
        self.meta = {r["snap"].snapshot_id: r["meta"] for r in rows}

    def snapshots_by_sha256(self, digest):
        return [r["snap"] for r in self.rows if r["snap"].sha256 == digest]

    def snapshot_meta(self, snapshot_id):
        return self.meta[snapshot_id]

    def last_writer(self, doc_key, digest):
        return self.writers.get((doc_key, digest))


def _manifest(root):
    with _readonly(root / "manifest.sqlite") as db:
        if "snapshots" not in _tables(db):
            raise ValueError("manifest has no snapshots table")
        rows = [{"seq": r["seq"], "snap": Snapshot.model_validate_json(r["snapshot"]),
                 "meta": json.loads(r["meta"])} for r in _rows(db, "snapshots")]
        parsed, states = _rows(db, "parsed"), _rows(db, "publish_states")
        view = _ManifestView(rows, _rows(db, "parse_writers"))
    return view, parsed, {r["snapshot_id"]: r["state"] for r in states}


def _chunks(root):
    path = root / "chunks.sqlite"
    if not path.exists():
        return [], [], {}
    with _readonly(path) as db:
        return (_rows(db, "documents"), _rows(db, "chunks"),
                {r["doc_key"]: r["snapshot_sha256"] for r in _rows(db, "current_versions")})


def _observations(root):
    ids, tables = defaultdict(set), set()
    for path in sorted((root / "parquet").glob("*/*.parquet")):
        for batch in pq.ParquetFile(path).iter_batches(columns=["source_id", "table_key", "obs_id"]):
            for row in batch.to_pylist():
                identity = (row["source_id"], row["table_key"])
                tables.add(identity)
                ids[row["obs_id"]].add(identity)
    duplicates = [{"obs_id": key, "tables": sorted(values)} for key, values in sorted(ids.items()) if len(values) > 1]
    return len(tables), duplicates


def _current_mismatches(view, documents, current, states):
    resources = defaultdict(set)
    unknown = []
    for row in documents:
        doc = Document.model_validate_json(row["document_json"])
        try:
            snap = document_snapshot(view, doc.snapshot_sha256, doc.source_id, doc.doc_key, doc)
        except AmbiguousCitation:
            snap = None
        if snap is None:
            unknown.append({"doc_key": doc.doc_key, "sha256": doc.snapshot_sha256})
        else:
            resources[doc.doc_key].add((snap.source_id, snap.resource_url))
    different = []
    for key, identities in sorted(resources.items()):
        candidates = [r for r in view.rows
                      if (r["snap"].source_id, r["snap"].resource_url) in identities
                      and r["snap"].outcome is FetchOutcome.ok and r["snap"].sha256
                      and states.get(r["snap"].snapshot_id) != "failed"]
        if not candidates:
            continue
        expected = candidates[-1]["snap"]
        if current.get(key) != expected.sha256:
            different.append({"doc_key": key, "current": current.get(key), "expected": expected.sha256,
                              "snapshot_id": expected.snapshot_id})
    return different, unknown


def _raw_failures(root, view):
    digests = {r["snap"].sha256 for r in view.rows if r["snap"].sha256}
    digests.update(p.name for p in (root / "raw").glob("*/*") if _SHA.fullmatch(p.name))
    failures = []
    for digest in sorted(digests):
        path = root / "raw" / digest[:2] / digest
        try:
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
        except FileNotFoundError:
            failures.append({"sha256": digest, "reason": "missing"})
        except OSError as exc:
            failures.append({"sha256": digest, "reason": "read_error", "error": safe_reason(exc)})
        else:
            if actual != digest:
                failures.append({"sha256": digest, "reason": "hash_mismatch", "actual": actual})
    return failures


def evidence(data_dir: str | Path, *, verify_raw: bool = False) -> dict:
    """Return counts and at most 20 examples per category, including legacy v1.

    Shared SHA, repeated obs_id and recovered failures are historical risk
    inventories, not proof of incorrect evidence. Replay preserves that history.
    """
    root = Path(data_dir)
    view, parsed, states = _manifest(root)
    documents, chunks, current = _chunks(root)
    by_sha, by_resource = defaultdict(list), defaultdict(list)
    for row in view.rows:
        snap = row["snap"]
        if snap.sha256:
            by_sha[snap.sha256].append(snap)
        by_resource[(snap.source_id, snap.resource_url)].append(snap)
    shared = [{"sha256": digest, "resources": sorted({(s.source_id, s.resource_url) for s in snaps}),
               "snapshot_ids": [s.snapshot_id for s in snaps]}
              for digest, snaps in sorted(by_sha.items()) if len({(s.source_id, s.resource_url) for s in snaps}) > 1]
    failed_parses = defaultdict(set)
    for row in parsed:
        if row["status"] == "failed":
            failed_parses[row["sha256"]].add(row.get("context_fingerprint", ""))
    recovered = []
    for (source, url), snaps in sorted(by_resource.items()):
        failures = []
        for snap in snaps:
            contexts = failed_parses[snap.sha256]
            failed_parse = states.get(snap.snapshot_id) == "failed" or (
                states.get(snap.snapshot_id) != "published"
                and ("" in contexts or parse_context_fingerprint(snap, view.snapshot_meta(snap.snapshot_id)) in contexts)
            )
            if snap.outcome not in (FetchOutcome.ok, FetchOutcome.not_modified) or failed_parse:
                failures.append(snap.snapshot_id)
            elif snap.outcome is FetchOutcome.ok and failures:
                recovered.append({"source_id": source, "resource_url": url,
                                  "old_failures": failures, "new_success": snap.snapshot_id})
                break
    different, unknown = _current_mismatches(view, documents, current, states)
    by_doc = defaultdict(list)
    for row in chunks:
        by_doc[(row["doc_key"], row["snapshot_sha256"])].append(json.loads(row["chunk_json"]))
    pdf_dates = []
    for row in documents:
        doc = json.loads(row["document_json"])
        if doc.get("kind") != "pdf":
            continue
        if any(not (c.get("published_at") or c.get("effective_at"))
               or any(doc.get(field) and c.get(field) != doc[field] for field in ("published_at", "effective_at"))
               for c in by_doc[(row["doc_key"], row["snapshot_sha256"])]):
            pdf_dates.append({"doc_key": row["doc_key"], "sha256": row["snapshot_sha256"],
                              "document_has_date": bool(doc.get("published_at") or doc.get("effective_at"))})
    table_count, duplicates = _observations(root)
    raw = _raw_failures(root, view) if verify_raw else []
    return {
        "format_version": 1,
        "notes": ["Stop writers before inspection/replay.",
                  "Shared SHA, recovered resources and duplicate obs_id are risk inventories; history is retained.",
                  "PDF documents with unknown source dates legitimately retain no_data_date."],
        "counts": {"snapshots": len(view.rows), "documents": len(documents), "chunks": len(chunks),
                   "current_documents": len(current), "observation_tables": table_count},
        "sha_multi_resource": _category(shared), "recovered_resources": _category(recovered),
        "duplicate_obs_ids": _category(duplicates), "current_mismatches": _category(different),
        "unresolved_document_resources": _category(unknown), "pdf_missing_dates": _category(pdf_dates),
        "raw_failures": {**_category(raw), "checked": verify_raw},
        "quarantined_raw": _category(sorted(p.name for p in (root / "raw" / ".quarantine").glob("*") if p.is_file())),
    }


def _inventory(root):
    documents, chunks, current = _chunks(root)
    return {"documents": len(documents), "chunks": len(chunks),
            "observation_tables": _observations(root)[0]}, current


class _ReplayManifest:
    """Expose one original observation at a time to the existing pipeline."""

    def __init__(self, manifest):
        self.manifest = manifest
        self.snapshot = None

    def __getattr__(self, name):
        return getattr(self.manifest, name)

    def snapshots_for(self, source_id):
        return [self.snapshot] if self.snapshot is not None and self.snapshot.source_id == source_id else []


def replay(source: str | Path, target: str | Path, *, vectors: bool = False) -> dict:
    """Copy manifest/raw, reset only derived parse state in the copy, and replay.

    A partial result retains the new directory for review. Never switches paths,
    deletes source data, or repairs corrupted raw by inventing original bytes.
    """
    from datumoj.rikolto import wiring
    from datumoj.rikolto.core.rawstore import FileRawStore

    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError("source and target directories must be separate and must not contain one another")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError("replay target must be an empty directory")
    view, _, _ = _manifest(source)
    before, old_current = _inventory(source)
    # Reject symlinks rather than following paths outside the local raw tree.
    raw = source / "raw"
    if raw.is_symlink() or any(p.is_symlink() for p in raw.rglob("*")):
        raise ValueError("raw symlinks are not supported")
    target.mkdir(parents=True, exist_ok=True)
    with _readonly(source / "manifest.sqlite") as old, closing(sqlite3.connect(target / "manifest.sqlite")) as new:
        old.backup(new)
    if raw.exists():
        shutil.copytree(raw, target / "raw")
    # Store constructors migrate only the new copy; snapshots and seq stay intact.
    deps = wiring.build(target, {"raw": FileRawStore(target), **({} if vectors else {"vectors": None})})
    try:
        deps.require("parse")
        deps.require("index")
        vectors_unavailable = vectors and deps.vectors is None
        with closing(sqlite3.connect(target / "manifest.sqlite")) as db, db:
            for table in ("parsed", "parse_writers", "publish_states"):
                db.execute(f"DELETE FROM {table}")
        adapter = _ReplayManifest(deps.manifest)
        pipeline = replace(deps.pipeline_deps, manifest=adapter)
        summaries, unknown_sources = [], set()
        for row in view.rows:
            snap = row["snap"]
            if deps.registry.get(snap.source_id) is None:
                unknown_sources.add(snap.source_id)
                continue
            adapter.snapshot = snap
            summaries.append(asdict(deps.parse_pending(pipeline, snap.source_id)))
        deps.reindex(deps.pipeline_deps)
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog", "observations", "vectors", "change_log"):
            close = getattr(getattr(deps, name), "close", None)
            if close is not None:
                close()
    after, new_current = _inventory(target)
    keys = sorted(key for key in old_current.keys() | new_current.keys() if old_current.get(key) != new_current.get(key))
    failed = {key: value for summary in summaries for key, value in summary["failed"].items()}
    no_parser = sorted({key for summary in summaries for key in summary["no_parser"]})
    missing_vectors = {key: value for summary in summaries for key, value in summary["vectors_missing"].items()}
    incomplete = bool(failed or no_parser or unknown_sources or missing_vectors or vectors_unavailable)
    return {
        "status": "partial" if incomplete else "complete",
        "before": before, "after": after, "current_differences": _category([
            {"doc_key": key, "old": old_current.get(key), "new": new_current.get(key)} for key in keys]),
        "failed_snapshots": failed, "no_parser": no_parser, "unknown_sources": sorted(unknown_sources),
        "vectors_requested": vectors, "vectors_unavailable": vectors_unavailable, "vectors_missing": missing_vectors,
        "diagnosis": evidence(target, verify_raw=True),
    }
