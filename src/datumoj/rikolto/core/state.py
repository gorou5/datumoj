"""Consistent, incremental state generations and verified restore to a new directory.

All local entry points wired by ``wiring.build`` share ``local_write``. Embedded
writers must use that same boundary around complete multi-store operations.
Only immutable content is uploaded outside the frozen local boundary.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager, suppress
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from threading import Lock
from typing import Literal

import duckdb
import lancedb
import pyarrow as pa

from datumoj.rikolto.core.citations import document_snapshot
from datumoj.rikolto.core.contracts import ContractError, PreconditionFailed, SourceSpec, StateObjectStore
from datumoj.rikolto.core.manifest import SCHEMA_VERSION as MANIFEST_VERSION
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import (
    FetchOutcome,
    PublishState,
    Snapshot,
    SourceRun,
    StateGeneration,
    StateWriter,
    StoreCopy,
    VectorCoverage,
    parse_context_fingerprint,
)
from datumoj.rikolto.core.schedule import _locked
from datumoj.rikolto.core.state_s3 import check_key, delete_retired_object
from datumoj.rikolto.index.coverage import index_is_rebuilding
from datumoj.rikolto.index.fts import SqliteKeywordIndex
from datumoj.rikolto.index.vector import maintain_vectors
from datumoj.rikolto.store.chunks import SCHEMA_VERSION as CHUNKS_VERSION
from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.store.observations import DuckDBObservationStore

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
_LOG = logging.getLogger(__name__)
_VECTOR_STATE = "vectors/vectors-state.json"
_BLOCK_SIZE = 256 * 1024
_SQLITE_BLOCK = "sqlite-block-v1"
_SQLITE_BLOCKS = "sqlite-blocks-v1"
_CLEANUP_MAX_SECONDS = 600


class _CleanupTimeout(Exception):
    """The best-effort cleanup window has ended."""


def _sqlite_copies(path: Path) -> list[StoreCopy]:
    """Describe a backup in bounded memory; the final entry is checksum-only."""
    copies = []
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while data := stream.read(_BLOCK_SIZE):
            sha = _digest(data)
            copies.append(StoreCopy(name=f"{path.name}.blocks/{len(copies)}", key=f"copies/sha256/{sha}",
                                    sha256=sha, size_bytes=len(data), format=_SQLITE_BLOCK))
            digest.update(data)
            size += len(data)
    sha = digest.hexdigest()
    copies.append(StoreCopy(name=path.name, key=f"copies/sha256/{sha}", sha256=sha,
                            size_bytes=size, format=_SQLITE_BLOCKS))
    return copies


def _copy_data(root: Path, copy: StoreCopy) -> bytes:
    if copy.format == _SQLITE_BLOCK:
        name, index = copy.name.split(".blocks/")
        with (root / name).open("rb") as stream:
            stream.seek(int(index) * _BLOCK_SIZE)
            return stream.read(copy.size_bytes)
    return _copy_path(root, copy.name).read_bytes()


def _block_groups(stores: list[StoreCopy]) -> dict[str, list[StoreCopy]]:
    """Reject incomplete, mixed or out-of-order layouts before downloading."""
    files = {c.name: c for c in stores if c.format == _SQLITE_BLOCKS}
    groups: dict[str, list[StoreCopy]] = {name: [] for name in files}
    for copy in stores:
        if copy.format == _SQLITE_BLOCK:
            name, index = copy.name.split(".blocks/")
            if name not in groups or index != str(len(groups[name])):
                raise StateError("SQLite 分块清单缺失或顺序无效")
            groups[name].append(copy)
    for name, blocks in groups.items():
        size = files[name].size_bytes
        if len(blocks) != (size + _BLOCK_SIZE - 1) // _BLOCK_SIZE or any(
            block.size_bytes != min(_BLOCK_SIZE, size - i * _BLOCK_SIZE) for i, block in enumerate(blocks)
        ):
            raise StateError("SQLite 分块大小或数量无效")
    return groups


def _rebuild_keyword(root: Path) -> None:
    if not (root / "keyword.sqlite").exists() and (root / "chunks.sqlite").exists():
        with closing(SqliteChunkStore(root / "chunks.sqlite")) as chunks, closing(
            SqliteKeywordIndex(root / "keyword.sqlite")
        ) as keyword:
            current = chunks.iter_chunks()  # Only current_versions, never historical/staged chunks.
            while batch := list(islice(current, 500)):
                keyword.add(batch)


def _copy_path(root: Path, name: str) -> Path:
    # The published logical namespace is vectors/, while wiring keeps the
    # optional-index retry list beside the other stores.
    return root / ("vectors-state.json" if name == _VECTOR_STATE else name)


def _vector_coverage(root: Path) -> VectorCoverage | None:
    path = root / "vectors"
    meta_path = path / "_meta.json"
    if not meta_path.exists():
        if path.exists() and any(path.iterdir()):
            raise StateError("向量目录缺少元数据")
        return None
    meta = json.loads(meta_path.read_bytes())
    generation = meta.get("generation", 0)
    if type(generation) is not int or generation < 0:
        raise StateError("向量表代次无效")
    table_name = "chunks" if generation == 0 else f"chunks_g{generation}"
    db = lancedb.connect(str(path))
    if table_name not in db.list_tables().tables:
        raise StateError("向量当前代表缺失")
    table = db.open_table(table_name)
    vector_type = table.schema.field("vector").type
    if not pa.types.is_fixed_size_list(vector_type) or vector_type.list_size != meta.get("dim"):
        raise StateError("向量维度与元数据不符")
    rows = table.count_rows()
    searchable = sum(batch.num_rows for batch in table.search().select(["chunk_id"]).to_batches(batch_size=500))
    if rows != searchable:
        raise StateError("向量可检索行数不符")
    state_path = root / "vectors-state.json"
    pending = 0
    if state_path.exists():
        state = json.loads(state_path.read_bytes())
        if not isinstance(state, dict) or not isinstance(state.get("missing"), dict):
            raise StateError("向量待补清单无效")
        for item in state["missing"].values():
            count = item.get("chunks")
            if type(count) is not int or count < 0:
                raise StateError("向量待补块数无效")
            pending += count
    # Embedder's contract has no model revision. Never invent one for old meta.
    return VectorCoverage(model=meta["model"], version=meta.get("version", "unknown"), dim=meta["dim"],
                          embedded_chunks=rows, pending_chunks=pending)


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
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='raw_allowed_missing'").fetchone():
            refs -= {r[0] for r in db.execute("SELECT sha256 FROM raw_allowed_missing")}
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
        if db.execute("SELECT 1 FROM publish_states WHERE state = 'staged' LIMIT 1").fetchone():
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
        if path.is_symlink():
            raise StateError("状态文件不能是符号链接")
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


def _copy_boundary(root: Path, target: Path) -> tuple[list[StoreCopy], dict[str, SourceRun], VectorCoverage | None]:
    with _frozen(root):
        _check_schema(root)
        paths = []
        vectors = root / "vectors"
        if vectors.is_symlink() or any(p.is_symlink() for p in vectors.rglob("*")):
            raise StateError("向量目录不能包含符号链接")
        coverage = _vector_coverage(root)
        if coverage is not None:
            try:
                maintain_vectors(vectors)
            except Exception:  # noqa: BLE001 -- 可选向量维护失败后仍须校验已发布覆盖率。
                _LOG.warning("向量维护失败；继续校验并复制已发布状态")
            for source in sorted(vectors.rglob("*")):
                if source.is_file():
                    name = source.relative_to(root).as_posix()
                    if name == _VECTOR_STATE:
                        raise StateError("向量目录含保留的待补清单逻辑名")
                    check_key(name)
                    destination = target / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    paths.append((name, "bytes"))
            if (root / "vectors-state.json").exists():
                shutil.copyfile(root / "vectors-state.json", target / "vectors-state.json")
            else:
                (target / "vectors-state.json").write_text('{"missing":{},"last_error":null}')
            paths.append((_VECTOR_STATE, "bytes"))
        for name in _SQLITE:
            if name == "keyword.sqlite":
                continue  # Validated above, but rebuilt from current chunks on restore.
            source = root / name
            if source.exists():
                with _ro(source) as db, closing(sqlite3.connect(target / name)) as destination:
                    db.backup(destination)
                paths.append((name, "sqlite"))
        for name in sorted(_SMALL):
            if name == "vectors-state.json" and coverage is not None:
                continue
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
            if fmt == "sqlite":
                stores.extend(_sqlite_copies(target / name))
                continue
            data = _copy_path(target, name).read_bytes()
            sha = _digest(data)
            stores.append(StoreCopy(name=name, key=f"copies/sha256/{sha}", sha256=sha,
                                    size_bytes=len(data), format=fmt))
        frozen_coverage = _vector_coverage(target)
        if frozen_coverage != coverage:
            raise StateError("向量副本覆盖与一致性边界不符")
        return stores, _last_runs(target), frozen_coverage


def _validate_copy(copy: StoreCopy) -> None:
    check_key(copy.name)
    check_key(copy.key)
    if copy.key != f"copies/sha256/{copy.sha256}":
        raise StateError("副本对象键与哈希不符")
    if copy.name in _SQLITE:
        valid = copy.format == "sqlite" or (copy.name != "keyword.sqlite" and copy.format == _SQLITE_BLOCKS)
    elif ".blocks/" in copy.name:
        name, index = copy.name.split(".blocks/", 1)
        valid = (name in _SQLITE and name != "keyword.sqlite" and bool(re.fullmatch(r"0|[1-9][0-9]*", index))
                 and copy.format == _SQLITE_BLOCK)
    elif copy.name in _SMALL:
        valid = copy.format == "bytes"
    elif copy.name == "observations.duckdb":
        valid = copy.format in {"duckdb-empty", "duckdb"}
    elif copy.name.startswith("vectors/"):
        valid = copy.format == "bytes"
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
    _block_groups(generation.stores)
    vectors = {c.name for c in generation.stores if c.name.startswith("vectors/")}
    if generation.vector_coverage is None:
        if vectors:
            raise StateError("向量副本缺少覆盖清单")
    elif not {"vectors/_meta.json", _VECTOR_STATE} <= vectors or "vectors-state.json" in {c.name for c in generation.stores}:
        raise StateError("向量副本或待补清单缺失、重名")
    return generation


def _sample_provenance(root: Path, *, raw: str, refs: set[str]) -> dict[str, int | str]:
    """Resolve resource-scoped citations, without reading remote raw in remote mode."""
    blocks_checked = observations_checked = 0
    manifest = SqliteManifest(root / "manifest.sqlite")
    refs = refs | set(manifest.raw_markers())
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
                 writer: StateWriter | None = None, before_publish: Callable[[], None] | None = None,
                 transfer_workers: int = 1):
        if type(transfer_workers) is not int or transfer_workers < 1:
            raise ValueError("transfer_workers 必须为正整数")
        self.store = store
        self.data_dir = Path(data_dir)
        self.writer = writer or StateWriter(kind="local", run_id=uuid.uuid4().hex)
        self.before_publish = before_publish
        self.before_write: Callable[[], None] | None = None
        self.before_metadata_write: Callable[[], None] | None = None
        self.transfer_workers = transfer_workers
        self._existing_lock = Lock()
        self.cache_dir: Path | None = None
        self.last_timings: dict[str, float] = {}
        # Composition hooks may restore after push; preserve the push measurements.
        self.last_push_timings: dict[str, float] = {}

    @contextmanager
    def _timed(self, stage: str) -> Iterator[None]:
        started = time.monotonic()
        try:
            yield
        finally:
            self.last_timings[stage] = self.last_timings.get(stage, 0.0) + time.monotonic() - started

    @contextmanager
    def _temporary_stage(self, **kwargs) -> Iterator[Path]:
        directory = tempfile.TemporaryDirectory(**kwargs)
        try:
            yield Path(directory.name)
        finally:
            with self._timed("cleanup"):
                directory.cleanup()

    def _transfer(self, operation: Callable, items: Iterable) -> Iterator:
        if self.transfer_workers == 1:
            for item in items:
                yield operation(item)
            return
        # Bound pending objects and downloaded bytes; yield in input order so
        # SQLite blocks retain their numeric assembly order. Shutdown waits on failure.
        items = iter(items)
        with ThreadPoolExecutor(max_workers=self.transfer_workers) as pool:
            pending = deque(pool.submit(operation, item) for item in islice(items, self.transfer_workers))
            while pending:
                yield pending.popleft().result()
                if (item := next(items, None)) is not None:
                    pending.append(pool.submit(operation, item))

    def _check_write(self, *, content: bool = False) -> None:
        callback = self.before_write if content else (self.before_metadata_write or self.before_write)
        if callback:
            callback()

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
        self._check_write()
        data = json.dumps({"generation": candidate.generation}).encode()
        try:
            if etag is None:
                self.store.put_if_absent("current.json", data)
            else:
                self.store.put_if_match("current.json", data, etag)
        except PreconditionFailed:
            raise
        except Exception:  # noqa: BLE001 -- SDK 的 CAS 异常可能发生在成功切换之后，必须回查指针。
            # An ambiguous transport failure can occur after a successful CAS.
            if self._current()[0] != candidate.generation:
                raise StateError("代次发布失败，指针未切换") from None

    def _cache_path(self, sha: str) -> Path | None:
        if self.cache_dir is None:
            return None
        root = Path(self.cache_dir)
        if root.is_symlink():
            return None
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None  # Cache availability must not prevent a verified remote restore.
        return root / sha

    def _cache_write(self, sha: str, data: bytes) -> None:
        path = self._cache_path(sha)
        if path is not None:
            temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                temporary.write_bytes(data)
                temporary.replace(path)
            except OSError:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

    def _upload(self, key: str, data: bytes, existing: set[str]) -> None:
        if key.startswith('copies/sha256/'):
            if _digest(data) != key.rsplit('/', 1)[1]:
                raise StateError('上传副本 SHA256 校验失败')
            self._cache_write(key.rsplit('/', 1)[1], data)
        with self._existing_lock:
            if key in existing:
                return
        self._check_write(content=True)
        try:
            self.store.put_if_absent(key, data)
        except PreconditionFailed:
            # Content-addressed keys are immutable. Creation races require no
            # content download; the conditional conflict proves the key exists.
            pass
        with self._existing_lock:
            existing.add(key)

    def push(self, *, retain_local: bool = False) -> StateGeneration:
        self.last_timings = {}
        self.last_push_timings = self.last_timings
        try:
            with self._timed("total"):
                return self._push(retain_local=retain_local)
        except PreconditionFailed:
            raise PreconditionFailed("状态发布竞争失败，本轮作废") from None
        except StateError:
            raise
        except Exception:  # noqa: BLE001 -- 状态推送边界统一隐藏任意 SDK 或存储错误。
            raise StateError("状态推送失败") from None

    def _push(self, *, retain_local: bool = False) -> StateGeneration:
        with self._timed("prepare"):
            self._check_write()
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
            with self._timed("stage"), _frozen(self.data_dir):
                pass
            candidate = _validate_generation(orphan[0], number + 1)
            # Continue only this exact candidate, checking raw by key inventory.
            with self._temporary_stage(prefix="datumoj-state-") as stage:
                self._materialize(candidate, stage, raw="remote", raw_keys=raw_keys)
                with self._timed("publish"):
                    self._publish(candidate, etag)
                if retain_local:
                    self._verify_publication(candidate, stage)
            return candidate
        with self._temporary_stage(prefix="datumoj-state-") as stage:
            with self._timed("stage"):
                stores, runs, coverage = _copy_boundary(self.data_dir, stage)
            with self._timed("validate"):
                refs = _raw_refs(stage)
                for copy in stores:
                    _validate_copy(copy)
                created_at = datetime.now(UTC)
            with self._timed("upload_copies"), closing(self._transfer(
                lambda copy: self._upload(copy.key, _copy_data(stage, copy), copies),
                # Identical blocks share a key; concurrent conditional PUTs to one key can 409 on S3/R2.
                {copy.key: copy for copy in reversed(stores)
                 if copy.format != _SQLITE_BLOCKS and copy.key not in copies}.values(),
            )) as transfers:
                for _ in transfers:
                    pass
            uploaded_raw = {}
            with self._timed("upload_raw"):
                # raw is immutable. Read/hash only missing blobs, without copying a raw tree.
                local_refs = set(refs)
                for path in (self.data_dir / "raw").glob("*/*"):
                    if path.is_file() and _SHA.fullmatch(path.name) and path.parent.name == path.name[:2]:
                        local_refs.add(path.name)

                def upload_raw(sha):
                    key = _raw_key("raw", sha)
                    path = self.data_dir / key
                    if not path.is_file():
                        raise StateError("manifest 引用的 raw 在本机和远端均缺失")
                    data = path.read_bytes()
                    if _digest(data) != sha:
                        raise StateError("本机 raw 哈希校验失败")
                    self._upload(key, data, raw_keys)
                    with self._existing_lock:
                        uploaded_raw[key] = len(data)

                with closing(self._transfer(upload_raw, (
                    sha for sha in sorted(local_refs) if _raw_key("raw", sha) not in raw_keys
                ))) as transfers:
                    for _ in transfers:
                        pass
            with self._timed("sample_provenance"):
                # Descriptor bytes were uploaded; reconstruct only for validation.
                for copy in stores:
                    if copy.format == "duckdb-empty":
                        path = stage / copy.name
                        path.unlink()
                        duckdb.connect(str(path)).close()
                _sample_provenance(stage, raw="remote", refs=refs)
            with self._timed("write_generation"):
                candidate = StateGeneration(
                    generation=number + 1, previous_generation=number or None, created_at=created_at,
                    writer=self.writer, stores=stores, raw_prefix="raw", vector_coverage=coverage,
                    last_successful_runs=runs,
                )
                # Full lease check, never the content-transfer throttle.
                self._check_write()
                self.store.put_if_absent(f"generations/{number + 1}.json", candidate.model_dump_json().encode())
            with self._timed("publish"):
                self._publish(candidate, etag)
            if retain_local:
                self._verify_publication(candidate, stage, uploaded_raw=uploaded_raw)
            return candidate

    def _verify_publication(self, candidate: StateGeneration, stage: Path, *,
                            uploaded_raw: dict[str, int] | None = None) -> None:
        """Check object inventories and retain the already frozen publication."""
        from datumoj.rikolto.store.observations import retain_generation

        with self._timed('post_publish'):
            with self._timed('post_publish_inventory'):
                copies = {obj.key: obj.size_bytes for obj in self.store.list_objects('copies/sha256/')}
                for copy in candidate.stores:
                    if copy.format != _SQLITE_BLOCKS and copies.get(copy.key) != copy.size_bytes:
                        raise StateError('发布后副本缺失或大小不符')
                raw = {obj.key: obj.size_bytes for obj in self.store.list_objects(candidate.raw_prefix + '/')}
                for key, size in (uploaded_raw or {}).items():
                    if raw.get(key) != size:
                        raise StateError('发布后 raw 缺失或大小不符')
                refs = _raw_refs(stage)
                with _ro(stage / 'manifest.sqlite') as db:
                    for sha, text in db.execute('SELECT sha256,snapshot FROM snapshots WHERE sha256 IS NOT NULL'):
                        if sha in refs and raw.get(_raw_key(candidate.raw_prefix, sha)) != json.loads(text)['size']:
                            raise StateError('发布后 raw 缺失或大小不符')
            with self._timed('post_publish_sample_provenance'):
                _sample_provenance(stage, raw='remote', refs=refs)
            with self._timed('post_publish_retain'):
                retain_generation(self.data_dir.resolve(), candidate.model_dump_json().encode(), stage.resolve(),
                                  verified=True, remote_raw=True, immutable_root=self.data_dir)

    def raw_retention(self, specs: list[SourceSpec], now: datetime) -> dict[str, datetime]:
        """Eligible raw SHA → expiry; any snapshot's protection vetoes deletion."""
        policies = {spec.id: spec for spec in specs}
        with _ro(self.data_dir / "manifest.sqlite") as db:
            no_parser = set(db.execute(
                "SELECT sha256, context_fingerprint FROM parsed WHERE status = 'no_parser'"))
            snapshots = [(Snapshot.model_validate_json(snap), json.loads(meta),
                          PublishState(state) if state is not None else None)
                         for snap, meta, state in db.execute(
                             "SELECT s.snapshot,s.meta,p.state FROM snapshots s "
                             "LEFT JOIN publish_states p USING(snapshot_id) ORDER BY s.fetched_at,s.seq")]
            latest = {(snap.source_id, snap.resource_url): snap.snapshot_id
                      for snap, _, _ in snapshots if snap.outcome is FetchOutcome.ok}
            latest_fetch = {}
            retention = {}
            protected = set()
            for snap, meta, state in snapshots:
                if snap.sha256 is None:
                    continue
                spec = policies.get(snap.source_id)
                scope = spec.scope if spec else {}
                days = scope.get("raw_retention_days", 30)
                expire_types = scope.get("raw_expire_latest_types", [])
                sha = snap.sha256
                latest_fetch[sha] = max(latest_fetch.get(sha, snap.fetched_at), snap.fetched_at)
                retention[sha] = max(retention.get(sha, days), days)
                context = parse_context_fingerprint(snap, meta)
                # A reusable parse is not proof this observation was published.
                # Only a terminal no-parser result can dispense with publication.
                pending = (snap.outcome is FetchOutcome.ok and state is not PublishState.published
                           and (sha, context) not in no_parser)
                if (state in {PublishState.staged, PublishState.failed} or pending
                        or (latest.get((snap.source_id, snap.resource_url)) == snap.snapshot_id
                            and meta.get("resource_type") not in expire_types)):
                    protected.add(sha)
            return {sha: fetched + timedelta(days=retention[sha])
                    for sha, fetched in latest_fetch.items() if sha not in protected}

    def mark_raw(self, specs: list[SourceSpec], now: datetime) -> int:
        """Mark before taking the publication snapshot, independent of delete switches."""
        candidates = self.raw_retention(specs, now)
        with closing(SqliteManifest(self.data_dir / "manifest.sqlite")) as manifest:
            before = manifest.raw_markers()
            for sha, expires in candidates.items():
                if now >= expires - timedelta(hours=72):
                    manifest.mark_raw_optional(sha, now)
            return len(manifest.raw_markers()) - len(before)

    def cleanup(self, specs: list[SourceSpec], *, now: datetime, check: Callable[[], None],
                enabled: bool = True, raw_enabled: bool = False, marked: int = 0,
                deadline: float | None = None, clock: Callable[[], float] | None = None) -> dict:
        """Best effort GC, called only after publication and post-publication verification.

        Build the full deletion plan before mutating. A descriptor deletion failure
        stops content deletion so all surviving generations keep their dependencies.
        Every deletion checks the live lease and uses the inventory version's ETag.
        ``deadline`` uses the monotonic clock; less than 600s left skips planning.
        """
        report = {"enabled": enabled, "raw_enabled": raw_enabled, "generations_deleted": 0,
                  "copies_deleted": 0, "copies_deleted_bytes": 0, "raw_marked": marked,
                  "raw_deleted": 0, "raw_deleted_bytes": 0, "reclaimable_generations": 0,
                  "reclaimable_copies": 0, "reclaimable_copies_bytes": 0,
                  "reclaimable_raw": 0, "reclaimable_raw_bytes": 0,
                  "total_bytes": 0, "over_7gb": False, "failures": 0, "elapsed_s": 0.0, "timed_out": False}
        clock = clock or time.monotonic
        started = clock()
        stop = started + _CLEANUP_MAX_SECONDS

        def check_time():
            if clock() >= stop:
                raise _CleanupTimeout

        def check_delete():
            check()
            check_time()

        timings = self.last_timings
        self.last_timings = {}
        try:
            # Reserve a full cleanup window before doing any planning I/O.
            if deadline is not None and deadline < stop:
                report["timed_out"] = True
                return report
            check()
            check_time()
            objects = {obj.key: obj for obj in self.store.list_objects("")}
            report["total_bytes"] = sum(obj.size_bytes for obj in objects.values())
            report["over_7gb"] = report["total_bytes"] > 7_000_000_000
            check_time()
            current, _ = self._current()
            # The inventory already includes descriptors, including gaps left by GC.
            numbers = sorted((int(key.removeprefix("generations/").removesuffix(".json")) for key in objects
                              if re.fullmatch(r"generations/[1-9][0-9]*\.json", key)
                              and int(key.removeprefix("generations/").removesuffix(".json")) <= current), reverse=True)
            history = []
            for number in numbers:
                check_time()
                history.append(self._generation(number))
            if current and current not in numbers:
                raise StateError("当前代次清单缺失")
            check_time()
            retained = [g for g in history if g.generation == current or now - g.created_at <= timedelta(hours=72)]
            expired = [objects[f"generations/{g.generation}.json"] for g in history if g not in retained]
            # Plan against the generations that would survive enabled GC.
            copies = {c.key for g in retained for c in g.stores}
            required = {_raw_key("raw", sha) for sha in _raw_refs(self.data_dir)}
            with _ro(self.data_dir / "manifest.sqlite") as db:
                optional = {sha for sha, marked_at in db.execute("SELECT sha256,marked_at FROM raw_allowed_missing")
                            if now - datetime.fromisoformat(marked_at).replace(tzinfo=UTC) >= timedelta(hours=72)}
            # Retained non-current generations postdate the >=72h mark; current refs use this manifest.
            check_time()
            candidates = self.raw_retention(specs, now)
            check_time()
            raw_keys = {_raw_key("raw", sha) for sha in optional
                        if sha in candidates and now >= candidates[sha]}
            copy_objects = [obj for obj in objects.values()
                            if obj.key.startswith("copies/sha256/") and obj.key not in copies]
            raw_objects = [obj for obj in objects.values() if obj.key in raw_keys and obj.key not in required]
            report.update(reclaimable_generations=len(expired), reclaimable_copies=len(copy_objects),
                          reclaimable_copies_bytes=sum(obj.size_bytes for obj in copy_objects),
                          reclaimable_raw=len(raw_objects), reclaimable_raw_bytes=sum(obj.size_bytes for obj in raw_objects))
            if enabled:
                for kind, group in (("generations", expired), ("copies", copy_objects),
                                    ("raw", raw_objects if raw_enabled else [])):
                    for obj in group:
                        check_time()
                        delete_retired_object(self.store, obj, check_delete)
                        report[f"{kind}_deleted"] += 1
                        if kind != "generations":
                            report[f"{kind}_deleted_bytes"] += obj.size_bytes
                        report["total_bytes"] -= obj.size_bytes
        except _CleanupTimeout:
            report["timed_out"] = True
        except Exception:  # noqa: BLE001 -- GC must never invalidate a successful publication.
            report["failures"] += 1
        finally:
            elapsed = max(0.0, clock() - started)
            report["elapsed_s"] = round(elapsed, 3)
            report["timed_out"] |= elapsed >= _CLEANUP_MAX_SECONDS
            report["over_7gb"] = report["total_bytes"] > 7_000_000_000
            self.last_timings = timings
        return report

    def list(self) -> list[StateGeneration]:
        try:
            number, _ = self._current()
            if number:
                self._generation(number)
            generations = []
            while number:
                found = self._get(f"generations/{number}.json")
                if found is None:
                    # GC leaves gaps, without rewriting immutable generation numbers.
                    numbers = {int(key.removeprefix("generations/").removesuffix(".json"))
                               for key in self.store.list("generations/")
                               if re.fullmatch(r"generations/[1-9][0-9]*\.json", key)}
                    generations.extend(self._generation(n) for n in sorted(numbers, reverse=True) if n < number)
                    break
                generation = _validate_generation(found[0], number)
                generations.append(generation)
                number = generation.previous_generation or 0
            return generations
        except Exception:  # noqa: BLE001 -- 代次列表校验边界必须隐藏任意 SDK 错误。
            raise StateError("状态代次列表校验失败") from None

    def _materialize(self, candidate: StateGeneration, stage: Path, *, raw: str,
                     raw_keys: set[str] | None = None) -> dict:
        groups = _block_groups(candidate.stores)
        for copy in candidate.stores:
            _validate_copy(copy)

        cache_valid = True
        for copy in candidate.stores:
            if copy.format == _SQLITE_BLOCKS:
                continue
            cached = self._cache_path(copy.sha256)
            try:
                if (cached is not None and (cached.exists() or cached.is_symlink())
                        and (cached.is_symlink() or not cached.is_file() or cached.stat().st_size != copy.size_bytes
                             or _digest(cached.read_bytes()) != copy.sha256)):
                    cache_valid = False
            except OSError:
                cache_valid = False
            if not cache_valid:
                break  # A damaged cache is untrusted; fall back to the full remote generation.
        self.last_timings['downloaded_bytes'] = 0.0
        self.last_timings['reused_bytes'] = 0.0

        def download(copy):
            cached = self._cache_path(copy.sha256)
            if cache_valid and cached is not None and cached.is_file() and not cached.is_symlink():
                try:
                    data = cached.read_bytes()
                except OSError:
                    data = b''
                if len(data) == copy.size_bytes and _digest(data) == copy.sha256:
                    with self._existing_lock:
                        self.last_timings['reused_bytes'] += len(data)
                    return data
            found = self._get(copy.key)
            if found is None or len(found[0]) != copy.size_bytes or _digest(found[0]) != copy.sha256:
                if copy.format == _SQLITE_BLOCK:
                    raise StateError("分块 SHA256 或大小校验失败")
                raise StateError("副本 SHA256 或大小校验失败")
            self._cache_write(copy.sha256, found[0])
            with self._existing_lock:
                self.last_timings['downloaded_bytes'] += len(found[0])
            return found[0]

        ordered = (block for copy in candidate.stores if copy.format != _SQLITE_BLOCK
                   for block in (groups[copy.name] if copy.format == _SQLITE_BLOCKS else [copy]))
        with self._timed("download_copies"), closing(self._transfer(download, ordered)) as downloads:
            for copy in candidate.stores:
                if copy.format == _SQLITE_BLOCK:
                    continue
                path = _copy_path(stage, copy.name)
                path.parent.mkdir(parents=True, exist_ok=True)
                if copy.format == _SQLITE_BLOCKS:
                    digest = hashlib.sha256()
                    size = 0
                    with path.open("wb") as stream:
                        for _ in groups[copy.name]:
                            data = next(downloads)
                            stream.write(data)
                            digest.update(data)
                            size += len(data)
                    if digest.hexdigest() != copy.sha256 or size != copy.size_bytes:
                        raise StateError("整库 SHA256 或大小校验失败")
                    continue
                data = next(downloads)
                if copy.format == "duckdb-empty":
                    if data != _EMPTY_DUCK:
                        raise StateError("DuckDB 重建描述无效")
                    duckdb.connect(str(path)).close()
                else:
                    path.write_bytes(data)
        with self._timed("validate"):
            _check_schema(stage)
            _rebuild_keyword(stage)
            _check_schema(stage)
            if _vector_coverage(stage) != candidate.vector_coverage:
                raise StateError("向量恢复覆盖校验失败")
            refs = _raw_refs(stage)
        with self._timed("download_raw"):
            if raw == "remote":
                if raw_keys is None:
                    raw_keys = set(self.store.list(candidate.raw_prefix + "/"))
                if any(_raw_key(candidate.raw_prefix, sha) not in raw_keys for sha in refs):
                    raise StateError("远端缺少 manifest 引用的 raw")
            else:
                if raw_keys is None:
                    raw_keys = set(self.store.list(candidate.raw_prefix + "/"))
                with closing(SqliteManifest(stage / "manifest.sqlite")) as manifest:
                    available_optional = {sha for sha in manifest.raw_markers()
                                          if _raw_key(candidate.raw_prefix, sha) in raw_keys}

                def download_raw(sha):
                    found = self._get(_raw_key(candidate.raw_prefix, sha))
                    if found is None or _digest(found[0]) != sha:
                        raise StateError("raw SHA256 校验失败")
                    path = stage / _raw_key("raw", sha)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(found[0])

                with closing(self._transfer(download_raw, sorted(refs | available_optional))) as transfers:
                    for _ in transfers:
                        pass
        with self._timed("sample_provenance"):
            report = _sample_provenance(stage, raw=raw, refs=refs)
        report.update(generation=candidate.generation, copies=len(candidate.stores), raw_objects=len(refs),
                      size_bytes=sum(c.size_bytes for c in candidate.stores if c.format != _SQLITE_BLOCK),
                      raw_mode=raw, verified=True)
        return report

    def restore(self, target: str | Path, generation: int | None = None, *,
                raw: Literal["local", "remote"] = "local") -> dict:
        self.last_timings = {}
        with self._timed("total"):
            return self._restore(target, generation, raw=raw)

    def _restore(self, target: str | Path, generation: int | None, *, raw: str) -> dict:
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
            with self._temporary_stage(prefix=".datumoj-restore-", dir=target.parent) as directory:
                stage = directory / "data"
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
        except Exception:  # noqa: BLE001 -- 恢复边界兜住任意 SDK 或校验失败，不交出未验证目录。
            raise StateError("状态恢复校验失败，未交出可用目录") from None
