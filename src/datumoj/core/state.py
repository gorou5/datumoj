"""Consistent, incremental state generations and verified restore to a new directory.

All local entry points wired by ``wiring.build`` share ``local_write``. Embedded
writers must use that same boundary around complete multi-store operations.
Only immutable content is uploaded outside the frozen local boundary.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import duckdb

from datumoj.core.citations import document_snapshot
from datumoj.core.contracts import ContractError, PreconditionFailed, StateObjectStore
from datumoj.core.manifest import SCHEMA_VERSION as MANIFEST_VERSION, SqliteManifest
from datumoj.core.models import SourceRun, StateGeneration, StateWriter, StoreCopy
from datumoj.core.schedule import _locked
from datumoj.core.state_s3 import check_key
from datumoj.index.coverage import index_is_rebuilding
from datumoj.store.chunks import SCHEMA_VERSION as CHUNKS_VERSION, SqliteChunkStore
from datumoj.store.observations import DuckDBObservationStore

_SQLITE = {
    "manifest.sqlite": (MANIFEST_VERSION, {
        "snapshots": "snapshot_id,source_id,sha256,snapshot,meta", "parsed": "sha256,parser,version,status,doc_keys",
        "parse_writers": "doc_key,sha256,context_fingerprint", "publish_states": "snapshot_id,state",
    }),
    "chunks.sqlite": (CHUNKS_VERSION, {
        "documents": "doc_key,snapshot_sha256,document_json", "current_versions": "doc_key,snapshot_sha256",
        "published_orders": "doc_key,order_json", "chunks": "chunk_id,doc_key,snapshot_sha256,chunk_json",
    }),
    "keyword.sqlite": (None, {"entries": "chunk_id,doc_key,source_id", "chunks_fts": "title_path,text"}),
    "catalog.sqlite": (1, {"datasets": "dataset_key,dataset_json", "resources": "resource_id,resource_json,state",
                           "catalog_fts": "title,description,tags"}),
    "changes.sqlite": (None, {"changes": "event_id,event_json,detected_at"}),
}
_SMALL = {"runs.jsonl", "control.json", "vectors-state.json"}
_EMPTY_DUCK = b'{"format":"duckdb-empty-v1"}\n'
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class StateError(ContractError):
    """A sanitized state error, never including storage configuration or source text."""


@contextmanager
def local_write(data_dir: str | Path) -> Iterator[None]:
    """Serialize complete local writes with snapshot creation (also for embedders)."""
    with _locked(Path(data_dir) / "state.lock"):
        yield


@contextmanager
def _ro(path: Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=0)
    try:
        yield db
    finally:
        db.close()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _raw_key(prefix: str, sha: str) -> str:
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise StateError("manifest raw 哈希无效")
    check_key(prefix)
    return f"{prefix.rstrip('/')}/{sha[:2]}/{sha}"


def _raw_refs(root: Path) -> set[str]:
    if not (root / "manifest.sqlite").exists():
        raise StateError("状态缺少 manifest")
    with _ro(root / "manifest.sqlite") as db:
        refs = {r[0] for r in db.execute("SELECT DISTINCT sha256 FROM snapshots WHERE sha256 IS NOT NULL")}
    for sha in refs:
        _raw_key("raw", sha)
    return refs


def _check_schema(root: Path) -> None:
    for name, (version, tables) in _SQLITE.items():
        path = root / name
        if not path.exists():
            continue
        with _ro(path) as db:
            if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise StateError("SQLite 完整性校验失败")
            if version is not None and db.execute("SELECT version FROM schema_version").fetchall() != [(version,)]:
                raise StateError("SQLite schema 版本不支持")
            for table, columns in tables.items():
                db.execute(f"SELECT {columns} FROM {table} LIMIT 0")
    with _ro(root / "manifest.sqlite") as db:
        # An interrupted publication cannot be upgraded into a usable current by
        # restoring its partially updated indexes / Parquet / catalog.
        if db.execute("SELECT 1 FROM publish_states WHERE state != 'published' LIMIT 1").fetchone():
            raise StateError("存在未完成的本地发布，先修复后再推送")
    if (root / "keyword.sqlite").exists() and not (root / "chunks.sqlite").exists():
        raise StateError("关键词库缺少 chunks")
    if (root / "keyword.sqlite").exists():
        with _ro(root / "keyword.sqlite") as db, _ro(root / "chunks.sqlite") as chunks:
            current = {r[0] for r in chunks.execute(
                "SELECT c.chunk_id FROM chunks c JOIN current_versions v USING(doc_key,snapshot_sha256)"
            )}
            indexed = {r[0] for r in db.execute("SELECT chunk_id FROM entries")}
            if not indexed.issubset(current):
                raise StateError("关键词索引包含未发布的块")
    for name in _SMALL:
        path = root / name
        if path.exists():
            if name.endswith(".jsonl"):
                for line in path.read_bytes().splitlines():
                    json.loads(line)
            else:
                json.loads(path.read_bytes())
    if index_is_rebuilding(root):
        raise StateError("关键词索引正在重建")


def _last_runs(root: Path) -> dict[str, SourceRun]:
    path = root / "runs.jsonl"
    runs = {}
    if path.exists():
        for line in path.read_bytes().splitlines():
            row = json.loads(line)
            if (row.get("state") == "final" and row.get("status") == "completed"
                    and row.get("exit_status") == 0 and not row.get("failures") and row.get("ended_at")):
                run = SourceRun(run_id=row["run_id"], finished_at=row["ended_at"])
                old = runs.get(row["source_id"])
                if old is None or run.finished_at > old.finished_at:
                    runs[row["source_id"]] = run
    return runs


@contextmanager
def _frozen(root: Path) -> Iterator[None]:
    """Gate wiring writers; hold scheduler and small-file locks until copies finish."""
    with ExitStack() as stack:
        if stack.enter_context(_locked(root / "state.lock", blocking=False)) is None:
            raise StateError("有正在运行的本地写入，拒绝推送")
        # Scheduler source locks are persistent files: stale PID metadata is not
        # a running crawl. Acquiring them prevents new crawls during the boundary.
        for path in sorted((root / "locks").glob("*.lock")):
            if stack.enter_context(_locked(path, blocking=False)) is None:
                raise StateError("有正在运行的抓取，拒绝推送")
        for name in ("runs.lock", "control.lock"):
            stack.enter_context(_locked(root / name))
        # Also stop individual SQLite writers. Separate read connections below
        # use backup(); backing up the connection with an active write transaction
        # would wait on itself. All reservations stay held through the last copy.
        for name in _SQLITE:
            path = root / name
            if path.exists():
                db = sqlite3.connect(path, isolation_level=None, timeout=0)
                stack.callback(db.close)
                db.execute("BEGIN IMMEDIATE")
        yield


def _copy_boundary(root: Path, target: Path) -> tuple[list[StoreCopy], dict[str, SourceRun]]:
    with _frozen(root):
        _check_schema(root)
        paths = []
        for name in _SQLITE:
            source = root / name
            if source.exists():
                with _ro(source) as db, closing(sqlite3.connect(target / name)) as destination:
                    db.backup(destination)
                paths.append((name, "sqlite"))
        for name in sorted(_SMALL):
            if (root / name).exists():
                shutil.copyfile(root / name, target / name)
                paths.append((name, "bytes"))
        for source in sorted((root / "parquet").glob("*/*.parquet")):
            name = source.relative_to(root).as_posix()
            check_key(name)
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            paths.append((name, "parquet"))
        source = root / "observations.duckdb"
        if source.exists():
            with duckdb.connect(str(source), read_only=True) as db:
                tables = db.execute("SELECT table_name FROM information_schema.tables "
                                    "WHERE table_schema='main' AND table_catalog=current_database() "
                                    "AND table_type='BASE TABLE'").fetchall()
                if not tables:
                    views = {r[0] for r in db.execute(
                        "SELECT table_name FROM information_schema.views "
                        "WHERE table_schema='main' AND table_catalog=current_database()"
                    ).fetchall()}
                    if views - {"observations"}:
                        raise StateError("未知的 DuckDB 视图，无法安全重建")
                    (target / source.name).write_bytes(_EMPTY_DUCK)
                    fmt = "duckdb-empty"
                else:
                    # DuckDB supports a logical, transaction-consistent database
                    # copy. Never copy a live database or omit its WAL.
                    quoted = str(target / source.name).replace("'", "''")
                    original = str(source).replace("'", "''")
                    with duckdb.connect() as exporter:
                        exporter.execute(f"ATTACH '{original}' AS state_source (READ_ONLY)")
                        exporter.execute(f"ATTACH '{quoted}' AS state_copy")
                        exporter.execute("COPY FROM DATABASE state_source TO state_copy")
                    fmt = "duckdb"
            paths.append((source.name, fmt))
        stores = []
        for name, fmt in sorted(paths):
            data = (target / name).read_bytes()
            sha = _digest(data)
            stores.append(StoreCopy(name=name, key=f"copies/sha256/{sha}", sha256=sha,
                                    size_bytes=len(data), format=fmt))
        return stores, _last_runs(target)


def _validate_copy(copy: StoreCopy) -> None:
    check_key(copy.name)
    check_key(copy.key)
    if copy.key != f"copies/sha256/{copy.sha256}":
        raise StateError("副本对象键与哈希不符")
    if copy.name in _SQLITE:
        valid = copy.format == "sqlite"
    elif copy.name in _SMALL:
        valid = copy.format == "bytes"
    elif copy.name == "observations.duckdb":
        valid = copy.format in {"duckdb-empty", "duckdb"}
    else:
        valid = bool(re.fullmatch(r"parquet/[a-z]{2,4}(?:-[a-z0-9]+)+/[0-9a-f]{32}\.parquet", copy.name))
        valid = valid and copy.format == "parquet"
    if not valid:
        raise StateError("未知的副本逻辑名或格式")


def _validate_generation(value: bytes, number: int) -> StateGeneration:
    generation = StateGeneration.model_validate_json(value)
    if generation.generation != number or generation.raw_prefix != "raw":
        raise StateError("无效的代次或 raw 前缀")
    if "manifest.sqlite" not in {c.name for c in generation.stores}:
        raise StateError("状态缺少 manifest")
    for copy in generation.stores:
        _validate_copy(copy)
    return generation


def _sample_provenance(root: Path, *, raw: str, refs: set[str]) -> dict[str, int | str]:
    """Resolve resource-scoped citations, without reading remote raw in remote mode."""
    blocks_checked = observations_checked = 0
    manifest = SqliteManifest(root / "manifest.sqlite")
    chunks = None
    try:
        if (root / "chunks.sqlite").exists():
            chunks = SqliteChunkStore(root / "chunks.sqlite")
            # Reservoir sampling avoids materializing a large corpus.
            sampled = []
            for count, chunk in enumerate(chunks.iter_chunks(), start=1):
                if len(sampled) < 5:
                    sampled.append(chunk)
                elif (index := random.randrange(count)) < 5:
                    sampled[index] = chunk
            for chunk in sampled:
                doc = chunks.get_document(chunk.doc_key, chunk.snapshot_sha256)
                snap = document_snapshot(manifest, chunk.snapshot_sha256, chunk.source_id, chunk.doc_key, doc)
                if snap is None or snap.sha256 not in refs:
                    raise StateError("块的出处无法还原到快照和 raw")
                blocks_checked += 1
        if (root / "observations.duckdb").exists() or (root / "parquet").exists():
            store = DuckDBObservationStore(root)
            result = store.query_readonly("SELECT * FROM observations ORDER BY random() LIMIT 5", max_rows=5)
            for obs in result.provenance:
                key = f"{obs.source_id}:{obs.table_key}"
                doc = chunks.get_document(key, obs.snapshot_sha256) if chunks else None
                snap = document_snapshot(manifest, obs.snapshot_sha256, obs.source_id, key, doc)
                if snap is None or snap.sha256 not in refs:
                    raise StateError("观测值出处无法还原到快照和 raw")
                observations_checked += 1
    finally:
        manifest.close()
        if chunks is not None:
            chunks.close()
    return {"sampled_chunks": blocks_checked, "sampled_observations": observations_checked,
            "raw_verification": "sha256" if raw == "local" else "remote_keys_only"}


class StateManager:
    """Push/restore state through StateObjectStore. Exceptions contain no SDK detail.

    ``before_publish`` must raise if a caller's write lease has been lost. It is
    called immediately before each pointer CAS, including orphan continuation.
    """

    def __init__(self, store: StateObjectStore, data_dir: str | Path, *,
                 writer: StateWriter | None = None, before_publish: Callable[[], None] | None = None):
        self.store = store
        self.data_dir = Path(data_dir)
        self.writer = writer or StateWriter(kind="local", run_id=uuid.uuid4().hex)
        self.before_publish = before_publish

    def _get(self, key: str) -> tuple[bytes, str] | None:
        return self.store.get(key)

    def _current(self) -> tuple[int, str | None]:
        current = self._get("current.json")
        if current is None:
            return 0, None
        value = json.loads(current[0])
        if (not isinstance(value, dict) or set(value) != {"generation"}
                or type(value["generation"]) is not int or value["generation"] < 1):
            raise StateError("当前代次指针无效")
        return value["generation"], current[1]

    def _generation(self, number: int) -> StateGeneration:
        found = self._get(f"generations/{number}.json")
        if found is None:
            raise StateError("代次清单缺失")
        return _validate_generation(found[0], number)

    def _publish(self, candidate: StateGeneration, etag: str | None) -> None:
        key = f"generations/{candidate.generation}.json"
        if self.before_publish:
            self.before_publish()
        # A callback can fail or pause. Recheck the exact immutable candidate
        # after it returns; never refresh the current ETag to force an old CAS.
        found = self._get(key)
        if found is None or _validate_generation(found[0], candidate.generation) != candidate:
            raise StateError("发布前代次清单已改变")
        data = json.dumps({"generation": candidate.generation}).encode()
        try:
            if etag is None:
                self.store.put_if_absent("current.json", data)
            else:
                self.store.put_if_match("current.json", data, etag)
        except PreconditionFailed:
            raise
        except Exception:
            # An ambiguous transport failure can occur after a successful CAS.
            if self._current()[0] != candidate.generation:
                raise StateError("代次发布失败，指针未切换") from None

    def _upload(self, key: str, data: bytes, existing: set[str]) -> None:
        if key in existing:
            return
        try:
            self.store.put_if_absent(key, data)
        except PreconditionFailed:
            # Content-addressed keys are immutable. Creation races require no
            # content download; the conditional conflict proves the key exists.
            pass
        existing.add(key)

    def push(self) -> StateGeneration:
        try:
            return self._push()
        except PreconditionFailed:
            raise PreconditionFailed("状态发布竞争失败，本轮作废") from None
        except StateError:
            raise
        except Exception:
            raise StateError("状态推送失败") from None

    def _push(self) -> StateGeneration:
        if not self.data_dir.is_dir():
            raise StateError("数据目录不存在")
        number, etag = self._current()
        if number:
            self._generation(number)
        # One paginated listing per content namespace, never HEAD/GET per blob.
        copies = set(self.store.list("copies/sha256/"))
        raw_keys = set(self.store.list("raw/"))
        orphan = self._get(f"generations/{number + 1}.json")
        if orphan is not None:
            with _frozen(self.data_dir):
                pass
            candidate = _validate_generation(orphan[0], number + 1)
            # A separate invocation may continue only this exact old candidate.
            # Copy SHA checks and evidence resolution happen in a private stage;
            # raw is verified by key inventory (never a full raw download).
            with tempfile.TemporaryDirectory(prefix="datumoj-state-") as directory:
                self._materialize(candidate, Path(directory), raw="remote", raw_keys=raw_keys)
            self._publish(candidate, etag)
            return candidate
        with tempfile.TemporaryDirectory(prefix="datumoj-state-") as directory:
            stage = Path(directory)
            stores, runs = _copy_boundary(self.data_dir, stage)
            refs = _raw_refs(stage)
            # Validate frozen data, not mutable live databases, before upload.
            # Empty DuckDB is represented by a deterministic rebuild descriptor.
            for copy in stores:
                _validate_copy(copy)
            created_at = datetime.now(UTC)
            for copy in stores:
                if copy.key not in copies:
                    self._upload(copy.key, (stage / copy.name).read_bytes(), copies)
            # raw is immutable. Only missing blobs are read, hashed and uploaded;
            # no 4.7GB duplicate tree is made in the temporary directory.
            local_refs = set(refs)
            for path in (self.data_dir / "raw").glob("*/*"):
                if path.is_file() and _SHA.fullmatch(path.name) and path.parent.name == path.name[:2]:
                    local_refs.add(path.name)
            for sha in sorted(local_refs):
                key = _raw_key("raw", sha)
                if key in raw_keys:
                    continue
                path = self.data_dir / key
                if not path.is_file():
                    raise StateError("manifest 引用的 raw 在本机和远端均缺失")
                data = path.read_bytes()
                if _digest(data) != sha:
                    raise StateError("本机 raw 哈希校验失败")
                self._upload(key, data, raw_keys)
            # Check evidence against this frozen generation before publishing.
            # Uploads above already used the descriptor bytes; the following
            # database is only a private, disposable validation reconstruction.
            for copy in stores:
                if copy.format == "duckdb-empty":
                    path = stage / copy.name
                    path.unlink()
                    duckdb.connect(str(path)).close()
            _sample_provenance(stage, raw="remote", refs=refs)
            candidate = StateGeneration(
                generation=number + 1, previous_generation=number or None, created_at=created_at,
                writer=self.writer, stores=stores, raw_prefix="raw", vector_coverage=None, last_successful_runs=runs,
            )
            # Never overwrite a conflicting manifest or jump over its number.
            self.store.put_if_absent(f"generations/{number + 1}.json", candidate.model_dump_json().encode())
            self._publish(candidate, etag)
            return candidate

    def list(self) -> list[StateGeneration]:
        try:
            number, _ = self._current()
            generations = []
            while number:
                generation = self._generation(number)
                generations.append(generation)
                number = generation.previous_generation or 0
            return generations
        except Exception:
            raise StateError("状态代次列表校验失败") from None

    def _materialize(self, candidate: StateGeneration, stage: Path, *, raw: str,
                     raw_keys: set[str] | None = None) -> dict:
        for copy in candidate.stores:
            _validate_copy(copy)
            found = self._get(copy.key)
            if found is None or len(found[0]) != copy.size_bytes or _digest(found[0]) != copy.sha256:
                raise StateError("副本 SHA256 或大小校验失败")
            path = stage / copy.name
            path.parent.mkdir(parents=True, exist_ok=True)
            if copy.format == "duckdb-empty":
                if found[0] != _EMPTY_DUCK:
                    raise StateError("DuckDB 重建描述无效")
                duckdb.connect(str(path)).close()
            else:
                path.write_bytes(found[0])
        _check_schema(stage)
        refs = _raw_refs(stage)
        if raw == "remote":
            if raw_keys is None:
                raw_keys = set(self.store.list(candidate.raw_prefix + "/"))
            if any(_raw_key(candidate.raw_prefix, sha) not in raw_keys for sha in refs):
                raise StateError("远端缺少 manifest 引用的 raw")
        else:
            for sha in sorted(refs):
                key = _raw_key(candidate.raw_prefix, sha)
                found = self._get(key)
                if found is None or _digest(found[0]) != sha:
                    raise StateError("raw SHA256 校验失败")
                path = stage / _raw_key("raw", sha)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(found[0])
        report = _sample_provenance(stage, raw=raw, refs=refs)
        report.update(generation=candidate.generation, copies=len(candidate.stores), raw_objects=len(refs),
                      size_bytes=sum(c.size_bytes for c in candidate.stores), raw_mode=raw, verified=True)
        return report

    def restore(self, target: str | Path, generation: int | None = None, *,
                raw: Literal["local", "remote"] = "local") -> dict:
        try:
            if raw not in {"local", "remote"}:
                raise StateError("raw 模式必须是 local 或 remote")
            if generation is not None and (type(generation) is not int or generation < 1):
                raise StateError("代次号必须为正整数")
            target = Path(target)
            if target.is_symlink() or (target.exists() and (not target.is_dir() or any(target.iterdir()))):
                raise StateError("恢复目标必须是空的新目录")
            # Only generations reachable from current are valid restore entries.
            history = self.list()
            candidate = next((g for g in history if generation is None or g.generation == generation), None)
            if candidate is None:
                raise StateError("没有指定的有效代次")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".datumoj-restore-", dir=target.parent) as directory:
                stage = Path(directory) / "data"
                stage.mkdir()
                report = self._materialize(candidate, stage, raw=raw)
                # No credentials, endpoint or bucket are persisted. Wiring loads
                # connection configuration from the existing environment.
                (stage / "state-storage.json").write_text(json.dumps({"raw_backend": "s3" if raw == "remote" else "local"}))
                (stage / "state-verification.json").write_text(json.dumps(report))
                # rmdir refuses a target populated concurrently. The stage only
                # becomes the requested directory after every verification passes.
                if target.exists():
                    target.rmdir()
                stage.rename(target)
            return report
        except StateError:
            raise
        except Exception:
            raise StateError("状态恢复校验失败，未交出可用目录") from None
