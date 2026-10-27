"""统计观测值库：current 原子提交历史段引用，DuckDB 提供只读 ``observations``。

目录布局（root 下）：
- ``parquet/<source_id>/<sha256(table_key) 前 32 位>.parquet``：一张表一个文件，整体替换。
  文件名用哈希而不用 table_key 原文：大小写不敏感的文件系统上 ``T1`` 和 ``t1`` 不会互相覆盖，
  ``/``、``..`` 也走不出目录。真正的键在文件里的 table_key 列。
- ``parquet-history/<source_id>/<表哈希>/<随机段名>.parquet``：只追加本批事件。
  current footer 保存已提交段名；旧 301 历史在首次写入前校验并迁移。
- ``.observation-generations/``：已验证代次副本、实际清单字节与轻量核对记录。
- ``observations.duckdb``：空的 DuckDB 库，只用来以 read_only 方式打开查询连接。

查询有两层防护：
1. 白名单：只允许单条 SELECT/WITH（用 DuckDB 自己的分词器和解析器判断），拒绝读文件、执行 SQL 串的函数；
2. 引擎：read_only 打开；视图建好后只放行本库的 Parquet 文件（``allowed_paths``），
   ``enable_external_access=false``、禁止自动装载扩展，再 ``lock_configuration=true``，查询里改不回来。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Context, Decimal
from hashlib import sha256
from pathlib import Path
from stat import S_ISREG
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import TypeAdapter

from datumoj.rikolto.core.contracts import (
    OBSERVATION_RELEASE_COLUMNS,
    AmbiguousRelease,
    GenerationUnavailable,
    InvalidGenerationToken,
    ReleaseDateUnavailable,
    ReleaseNotFound,
    ObservationCatalog,
    QueryError,
    QueryRejected,
    QueryResult,
    QueryTimeout,
    TableSummary,
    TooManyRows,
)
from datumoj.rikolto.core.models import (
    EvidenceState, GenerationToken, Observation, ObservationIdentity, ObservationRowEvidence,
    SnapshotOrder, SourceId, StateGeneration, Text,
)

SCALE = 10
"""value 列存成 DECIMAL(38, 10)：所有文件同一类型，视图合并时不会被隐式转换截断。"""
_MAX_ABS = Decimal(10) ** (38 - SCALE)
_CTX = Context(prec=38)  # 默认上下文只有 28 位，normalize/quantize 会把 38 位的值舍入

_SCHEMA = pa.schema([
    (c, pa.date32() if c in ("time_start", "time_end", "released_at")
     else pa.decimal128(38, SCALE) if c == "value"
     else pa.timestamp("us", tz="UTC") if c == "fetched_at"
     else pa.string())
    for c in OBSERVATION_RELEASE_COLUMNS
])
_HISTORY_SCHEMA = pa.schema([*_SCHEMA, ("_order_at", pa.timestamp("us", tz="UTC")),
                             ("_order_seq", pa.int64()), ("_current", pa.bool_())])
_SQL_TYPES = {"released_at": "DATE", "time_start": "DATE", "time_end": "DATE", "value": f"DECIMAL(38, {SCALE})", "fetched_at": "TIMESTAMPTZ"}
_EMPTY_VIEW = "SELECT " + ", ".join(
    f"CAST(NULL AS {_SQL_TYPES.get(c, 'VARCHAR')}) AS {c}" for c in OBSERVATION_RELEASE_COLUMNS
) + " LIMIT 0"

# 读文件、连外部库、把字符串当 SQL 执行的函数（按名字拒绝，引擎层另有 enable_external_access=false 兜底）
_FORBIDDEN_NAMES = re.compile(
    r"read_\w*|\w*_scan|glob|sniff_csv|parquet_\w+|query|query_table|json_execute_serialized_sql|getenv"
    r"|\w*_attach|iceberg_\w+|delta_\w+|sqlite_\w+|postgres_\w+|mysql_\w+|duckdb_secrets|which_secret"
    r"|load_\w+|install_\w+|checkpoint|force_checkpoint",
    re.I,
)
_WORD = re.compile(r'"((?:[^"]|"")*)"|(\w+)')
_WORD_TOKENS = (duckdb.token_type.identifier, duckdb.token_type.keyword)

_source_id = TypeAdapter(SourceId)
_text = TypeAdapter(Text)
_monotonic = time.monotonic
_ORDER_KEY = b"datumoj.snapshot_order"
_HISTORY_FILE = b"datumoj.history_file"
_HISTORY_ONLY = b"datumoj.history_only"
_SEGMENTS = b"datumoj.history_segments"
_SEGMENT_TABLE = b"datumoj.history_table"
_MIGRATION = b"datumoj.history_migration"
_PUBLICATION_SOURCE = "zz-observation-history"


def _current_files(root, ignored=()):
    return sorted(p for directory in (root / 'parquet').glob('*')
                  if directory.is_dir() and directory.name != _PUBLICATION_SOURCE
                  for p in directory.glob('*.parquet') if p not in ignored)


def _segment_paths(root, path, metadata):
    names = json.loads(metadata.get(_SEGMENTS, b'[]'))
    if not isinstance(names, list) or any(not isinstance(n, str) or not re.fullmatch(r'[0-9a-f]{32}\.parquet', n) for n in names):
        raise QueryError('历史段引用无效')
    return [root / 'parquet-history' / path.parent.name / path.stem / n for n in names]


def _events(table):
    # _current is membership in a particular current file, not an event field.
    return Counter(tuple(row.get(c) for c in _HISTORY_SCHEMA.names if c != '_current')
                   for row in table.to_pylist())


def check_history_migration(root):
    """Read-only 301 inventory; never creates a store or changes files."""
    root = Path(root)
    tables, removable = 0, set()
    if not (root / 'parquet').exists():
        return {'tables': 0, 'reclaimable_bytes': 0}
    for path in _current_files(root):
        metadata = pq.read_schema(path).metadata or {}
        if _HISTORY_ONLY in metadata:
            continue
        if _HISTORY_FILE in metadata:
            tables += 1
            name = metadata[_HISTORY_FILE].decode()
            if not re.fullmatch(r'[0-9a-f]{32}\.parquet', name):
                raise QueryError('历史文件引用无效')
            events = _events(pq.read_table(path.with_name(name)))
            for candidate in path.parent.glob('*.parquet'):
                if _HISTORY_ONLY in (pq.read_schema(candidate).metadata or {}) and _events(pq.read_table(candidate)) <= events:
                    removable.add(candidate)
        elif _MIGRATION in metadata:
            tables += 1
            removable.update(path.with_name(n) for n in json.loads(metadata[_MIGRATION])['files'] if path.with_name(n).exists())
    inodes, links = {}, Counter()
    for path in removable:
        stat = path.stat()
        key = stat.st_dev, stat.st_ino
        inodes[key] = stat
        links[key] += 1
    # A retained generation may still own a link to a redundant 301 file.
    reclaimable = sum(stat.st_size for key, stat in inodes.items() if links[key] == stat.st_nlink)
    return {'tables': tables, 'reclaimable_bytes': reclaimable}


def _link_or_copy(source, destination):
    try:
        os.link(source, destination)
        return 'link'
    except OSError:
        shutil.copy2(source, destination)
        return 'copy'


def _install_published_segments(root):
    directory = root / 'parquet' / _PUBLICATION_SOURCE
    for path in directory.glob('*.parquet'):
        path = _dependency(root, path.relative_to(root).as_posix())
        metadata = pq.read_schema(path).metadata or {}
        value = metadata.get(_SEGMENT_TABLE, b'').decode()
        if not re.fullmatch(r'[a-z]{2,4}(?:-[a-z0-9]+)+/[0-9a-f]{32}\.parquet', value) or _HISTORY_ONLY not in metadata:
            raise QueryError('发布历史段路径无效')
        source, table = value.split('/')
        destination = _dependency(root, f'parquet-history/{source}/{Path(table).stem}/{path.name}')
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if _file_digest(destination) != _file_digest(path):
                raise QueryError('发布历史段冲突')
        else:
            _link_or_copy(path, destination)


class DuckDBObservationStore:
    """``ObservationReleaseStore`` 实现；写入保留历史，查询连接一律只读。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._parquet = self.root / "parquet"
        self._parquet.mkdir(parents=True, exist_ok=True)
        self._db = self.root / "observations.duckdb"
        self._describe_lock = threading.RLock()
        self._describe_generation: tuple[tuple[str, int, int], ...] | None = None
        self._describe_cache: ObservationCatalog | None = None
        if not self._db.exists():
            duckdb.connect(str(self._db)).close()
        metadata = {p: pq.read_schema(p).metadata or {} for p in _current_files(self.root)}
        self._ignored_history = {p for p, m in metadata.items() if _HISTORY_ONLY in m}
        self._needs_migration = any(_HISTORY_FILE in m or _MIGRATION in m
                                    for p, m in metadata.items() if p not in self._ignored_history)
        self._needs_install = any(not segment.exists() for p, m in metadata.items()
                                  for segment in _segment_paths(self.root, p, m))

    # ---------- 写 ----------

    def path_for(self, source_id: str, table_key: str) -> Path:
        _source_id.validate_python(source_id)
        _text.validate_python(table_key)
        return self._parquet / source_id / f"{sha256(table_key.encode()).hexdigest()[:32]}.parquet"

    def table_order(self, source_id: str, table_key: str) -> SnapshotOrder | None:
        path = self.path_for(source_id, table_key)
        try:
            metadata = pq.read_metadata(path).metadata or {}
        except FileNotFoundError:
            return None
        value = metadata.get(_ORDER_KEY)
        return SnapshotOrder.model_validate_json(value) if value is not None else None

    def write_table(
        self, source_id: str, table_key: str, observations: Sequence[Observation], order: SnapshotOrder | None = None,
    ) -> bool:
        # 同一实例上的比较与替换串行；跨进程写入沿用管线的 local_write 边界。
        with self._describe_lock:
            return self._write_table(source_id, table_key, observations, order)

    def _write_table(
        self, source_id: str, table_key: str, observations: Sequence[Observation], order: SnapshotOrder | None, *, check_watermark: bool = True,
    ) -> bool:
        path = self.path_for(source_id, table_key)
        if self._needs_install:
            _install_published_segments(self.root)
            self._needs_install = False
        if self._needs_migration:
            self._migrate_history()
        ids: set[ObservationIdentity] = set()
        for o in observations:
            if o.source_id != source_id or o.table_key != table_key:
                raise ValueError(f"观测值 {o.obs_id} 不属于表 {source_id}/{table_key}")
            if o.identity in ids:
                raise ValueError(f"ObservationIdentity 重复：{o.identity}")
            ids.add(o.identity)
            if o.value is not None and not _fits(o.value):
                raise ValueError(f"观测值 {o.obs_id} 的 value={o.value} 超出 DECIMAL(38, {SCALE})，无法无损保存")
        if order is not None and check_watermark:
            current = self.table_order(source_id, table_key)
            if current is not None and order < current:
                return False
        incoming = [{**_row(o), "_order_at": order.fetched_at if order else None,
                     "_order_seq": order.seq if order else None, "_current": True} for o in observations]
        metadata = {_ORDER_KEY: order.model_dump_json().encode()} if order is not None else {}
        self._append(path, incoming, pa.Table.from_pylist(incoming, schema=_HISTORY_SCHEMA), metadata)
        self._invalidate_describe()
        return True

    def _append(self, path, incoming, current, metadata):
        old_metadata = pq.read_schema(path).metadata or {} if path.exists() else {}
        segments = _segment_paths(self.root, path, old_metadata)
        created = []
        try:
            # Pre-301 tables have no segments yet. Preserve their original ordering.
            if path.exists() and _SEGMENTS not in old_metadata:
                old = self._stored_rows(path)
                if old:
                    created.append(self._new_segment(path, old))
            if incoming:
                created.append(self._new_segment(path, incoming))
            metadata = dict(metadata)
            metadata[_SEGMENTS] = json.dumps([p.name for p in [*segments, *created]]).encode()
            _atomic_parquet(current.replace_schema_metadata(metadata), path)
        except BaseException:
            for segment in created:
                self._discard_segment(segment)
            raise

    def _discard_segment(self, segment):
        alias = self._parquet / _PUBLICATION_SOURCE / segment.name
        alias.unlink(missing_ok=True)
        segment.unlink(missing_ok=True)

    def _new_segment(self, path, rows):
        segment = _dependency(self.root, f'parquet-history/{path.parent.name}/{path.stem}/{os.urandom(16).hex()}.parquet')
        table = pa.Table.from_pylist(rows, schema=_HISTORY_SCHEMA).replace_schema_metadata({
            _HISTORY_ONLY: b'1', _SEGMENT_TABLE: f'{path.parent.name}/{path.name}'.encode(),
        })
        _atomic_parquet(table, segment, immutable=True)
        alias = _dependency(self.root, f'parquet/{_PUBLICATION_SOURCE}/{segment.name}')
        alias.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(segment, alias)
        except BaseException:
            segment.unlink(missing_ok=True)
            raise
        return segment

    def _migrate_history(self):
        files = _current_files(self.root)
        self._ignored_history = {p for p in files if _HISTORY_ONLY in (pq.read_schema(p).metadata or {})}
        for path in files:
            if path in self._ignored_history:
                continue
            table = pq.read_table(path)
            metadata = dict(table.schema.metadata or {})
            if _HISTORY_FILE in metadata:
                name = metadata[_HISTORY_FILE].decode()
                if not re.fullmatch(r'[0-9a-f]{32}\.parquet', name):
                    raise QueryError('历史文件引用无效')
                original = pq.read_table(path.with_name(name))
                segment = self._new_segment(path, original.to_pylist())
                try:
                    if _events(pq.read_table(segment)) != _events(original):
                        raise QueryError('历史迁移核对不一致')
                    events = _events(original)
                    redundant = [p.name for p in path.parent.glob('*.parquet')
                                 if _HISTORY_ONLY in (pq.read_schema(p).metadata or {})
                                 and _events(pq.read_table(p)) <= events]
                    metadata.pop(_HISTORY_FILE)
                    metadata[_SEGMENTS] = json.dumps([segment.name]).encode()
                    metadata[_MIGRATION] = json.dumps({'files': redundant, 'sha256': _file_digest(segment)}).encode()
                    _atomic_parquet(table.replace_schema_metadata(metadata), path)
                except BaseException:
                    self._discard_segment(segment)
                    raise
            if _MIGRATION in metadata:
                pending = json.loads(metadata[_MIGRATION])
                segments = _segment_paths(self.root, path, metadata)
                if len(segments) != 1 or _file_digest(segments[0]) != pending['sha256']:
                    raise QueryError('历史迁移核对不一致')
                events = _events(pq.read_table(segments[0]))
                candidates = []
                for name in pending['files']:
                    if not re.fullmatch(r'[0-9a-f]{32}\.parquet', name):
                        raise QueryError('历史迁移路径无效')
                    candidate = path.with_name(name)
                    if candidate.exists():
                        if _HISTORY_ONLY not in (pq.read_schema(candidate).metadata or {}) or not _events(pq.read_table(candidate)) <= events:
                            raise QueryError('历史迁移核对不一致')
                        candidates.append(candidate)
                for candidate in candidates:
                    candidate.unlink()
                metadata.pop(_MIGRATION)
                _atomic_parquet(table.replace_schema_metadata(metadata), path)
        self._needs_migration = False

    # ---------- 读 ----------

    def query_readonly(
        self, sql: str, max_rows: int = 1000, timeout_s: float = 10, *,
        generation_token: str | None = None, as_of_release: date | str | None = None,
    ) -> QueryResult:
        if max_rows < 1 or timeout_s <= 0:
            raise ValueError("max_rows 必须 ≥ 1，timeout_s 必须 > 0")
        deadline = _monotonic() + timeout_s
        timed_out = threading.Event()
        connection: list[duckdb.DuckDBPyConnection] = []

        def interrupt() -> None:
            timed_out.set()
            if connection:
                try:
                    connection[0].interrupt()
                except duckdb.Error:
                    pass  # The connection may have closed as the timer fired.

        timer = threading.Timer(timeout_s, interrupt)
        timer.start()
        con: duckdb.DuckDBPyConnection | None = None
        try:
            _guard(sql)
            _check_timeout(deadline, timeout_s, timed_out)
            cutoff = TypeAdapter(date | str | None).validate_python(as_of_release, strict=True)
            if isinstance(cutoff, str) and not cutoff.strip():
                raise ValueError("as_of_release 不能是空发布标识")
            token, generation_root = self._generation(generation_token, deadline, timeout_s, timed_out)
            _check_timeout(deadline, timeout_s, timed_out)
            if token is None and cutoff is not None:
                raise GenerationUnavailable("截至查询需要已发布代次")
            if token is None:
                con = self._connect()
                connection.append(con)
            else:
                con = duckdb.connect(str(generation_root[0] / "observations.duckdb"), read_only=True)
                connection.append(con)
                self._release_view(con, generation_root, cutoff, deadline, timeout_s, timed_out)

            _check_timeout(deadline, timeout_s, timed_out)
            columns, rows = _fetch(con.sql(sql).limit(max_rows + 1))
            if len(rows) > max_rows:
                raise TooManyRows(f"结果超过 {max_rows} 行，请加条件或聚合")
            if token is None:
                provenance, provenance_warnings = self._provenance(con, columns, rows)
                evidence = []
            else:
                provenance, evidence = self._release_evidence(con, sql, columns, rows, token.generation)
                provenance_warnings = []
            _check_timeout(deadline, timeout_s, timed_out)
        except duckdb.InterruptException as exc:
            raise QueryTimeout(f"查询超过 {timeout_s} 秒") from exc
        except duckdb.PermissionException as exc:
            raise QueryRejected(f"查询试图访问外部资源：{exc}") from exc
        except duckdb.Error as exc:
            raise QueryError(str(exc)) from exc
        finally:
            timer.cancel()
            if con is not None:
                con.close()
        _check_timeout(deadline, timeout_s, timed_out)
        warnings = list(provenance_warnings)
        if "obs_id" not in columns:
            warnings.append("结果里没有 obs_id 列，未附出处；需要出处时请在 SELECT 里保留 obs_id")
        if token is None:
            return QueryResult(columns=columns, rows=rows, provenance=provenance, warnings=warnings)
        linked = sum(bool(row.identities) for row in evidence)
        return QueryResult(
            columns=columns, rows=rows, provenance=provenance, warnings=warnings,
            generation_token=str(token), row_evidence=evidence,
            evidence_state=(EvidenceState.complete if not rows or linked == len(rows) else
                            EvidenceState.partial if linked else EvidenceState.unavailable),
            evidence_inputs={"sql": sql, "generation": token.generation,
                             "as_of_release": cutoff.isoformat() if isinstance(cutoff, date) else cutoff},
        )

    def describe(self, timeout_s: float = 10) -> ObservationCatalog:
        if timeout_s <= 0:
            raise ValueError("timeout_s 必须 > 0")
        deadline = _monotonic() + timeout_s
        timed_out = threading.Event()
        connection: list[duckdb.DuckDBPyConnection] = []

        def interrupt() -> None:
            timed_out.set()
            if connection:
                try:
                    connection[0].interrupt()
                except duckdb.Error:
                    pass

        timer = threading.Timer(timeout_s, interrupt)
        timer.start()
        con: duckdb.DuckDBPyConnection | None = None
        try:
            remaining = max(0.0, deadline - _monotonic())
            if not self._describe_lock.acquire(timeout=remaining):
                raise QueryTimeout(f"describe 超过 {timeout_s:g} 秒")
            try:
                generation = self._parquet_generation()
                _check_timeout(deadline, timeout_s, timed_out)
                if generation == self._describe_generation and self._describe_cache is not None:
                    catalog = self._describe_cache
                else:
                    con = self._connect()
                    connection.append(con)
                    _check_timeout(deadline, timeout_s, timed_out)
                    _, rows = _fetch(con.sql(
                        "SELECT source_id, table_key, list(DISTINCT dataset_key), list(DISTINCT survey),"
                        " list(DISTINCT indicator), count(DISTINCT area_code), min(time_start), max(time_end), count(*)"
                        " FROM observations GROUP BY source_id, table_key"
                    ))
                    _check_timeout(deadline, timeout_s, timed_out)
                    catalog = ObservationCatalog(columns=dict(OBSERVATION_RELEASE_COLUMNS), tables=[
                        TableSummary(
                            source_id=sid, table_key=tkey, dataset_keys=sorted(dks), surveys=sorted(surveys),
                            indicators=sorted(inds), area_count=areas, time_start=start, time_end=end, rows=n,
                        )
                        for sid, tkey, dks, surveys, inds, areas, start, end, n
                        in sorted(rows, key=lambda r: (r[0], r[1]))
                    ])
                    if generation == self._parquet_generation():
                        self._describe_generation = generation
                        self._describe_cache = catalog
            finally:
                self._describe_lock.release()
        except duckdb.InterruptException as exc:
            raise QueryTimeout(f"describe 超过 {timeout_s:g} 秒") from exc
        finally:
            timer.cancel()
            if con is not None:
                con.close()
        _check_timeout(deadline, timeout_s, timed_out)
        return catalog.model_copy(deep=True)

    def _invalidate_describe(self) -> None:
        with self._describe_lock:
            self._describe_generation = None
            self._describe_cache = None

    def _parquet_generation(self) -> tuple[tuple[str, int, int], ...]:
        generation = []
        for path in _current_files(self.root, self._ignored_history):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            generation.append((str(path), stat.st_size, stat.st_mtime_ns))
        return tuple(generation)

    def _connect(self) -> duckdb.DuckDBPyConnection:
        """只读连接：视图 ``observations`` 已建好，外部访问已关闭且配置已锁定。"""
        files = _current_files(self.root, self._ignored_history)
        con = duckdb.connect(str(self._db), read_only=True)
        try:
            scan = self._scan(files, self.root)
            con.execute(f"CREATE TEMP VIEW observations AS SELECT {', '.join(OBSERVATION_RELEASE_COLUMNS)} "
                        f"FROM ({scan}) WHERE _current")
            _lock_connection(con, files)
        except BaseException:
            con.close()
            raise
        return con

    def _stored_rows(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        with duckdb.connect() as con:
            return con.sql(self._scan([path], self.root, history=True)).to_arrow_table().to_pylist()

    @staticmethod
    def _scan(files: list[Path], root: Path, *, history: bool = False, check=None) -> str:
        """Lazy columnar scans; only old files need per-file schema/order adaptation."""
        scans = []
        modern = []
        for path in files:
            if check is not None:
                check()
            schema = pq.read_schema(path)
            metadata = schema.metadata or {}
            if _HISTORY_ONLY in metadata:
                continue
            if history and _SEGMENTS in metadata:
                segments = _segment_paths(root, path, metadata)
                if segments:
                    parts = [f"SELECT *, {i} AS _segment FROM read_parquet({_quote(str(p))})" for i, p in enumerate(segments)]
                    scans.append("SELECT * EXCLUDE (_segment) FROM (" + " UNION ALL ".join(parts) + ") QUALIFY row_number() OVER ("
                                 "PARTITION BY source_id,table_key,obs_id,snapshot_sha256,release_id,_order_at,_order_seq "
                                 "ORDER BY _segment DESC)=1")
                continue
            if history and _HISTORY_FILE in metadata:
                name = metadata[_HISTORY_FILE].decode()
                if not re.fullmatch(r'[0-9a-f]{32}\.parquet', name):
                    raise QueryError('历史文件引用无效')
                path = path.with_name(name)
                schema = pq.read_schema(path)
            if set(_HISTORY_SCHEMA.names) <= set(schema.names):
                modern.append(path)
                continue
            cols = [c if c in schema.names else f"CAST(NULL AS {_SQL_TYPES.get(c, 'VARCHAR')}) AS {c}"
                    for c in OBSERVATION_RELEASE_COLUMNS]
            metadata = schema.metadata or {}
            raw_order = metadata.get(_ORDER_KEY)
            if raw_order is not None:
                order = SnapshotOrder.model_validate_json(raw_order)
                at = f"TIMESTAMPTZ {_quote(order.fetched_at.isoformat())}"
                seq = str(order.seq)
            else:
                at, seq = "fetched_at", "NULL::BIGINT"
                manifest = root / "manifest.sqlite"
                if history and manifest.exists():
                    # Upgrade-free lookup in the original ordering domain. Match the
                    # actual observation event, never the latest repeat of its SHA.
                    with sqlite3.connect(f"file:{manifest}?mode=ro", uri=True) as db:
                        events = db.execute("SELECT source_id, sha256, fetched_at, seq FROM snapshots").fetchall()
                    cases = []
                    for sid, sha, fetched, number in events:
                        stamp = datetime.fromtimestamp(fetched, UTC) if isinstance(fetched, (int, float)) else datetime.fromisoformat(fetched).replace(tzinfo=UTC)
                        cases.append(f"WHEN source_id={_quote(sid)} AND snapshot_sha256={_quote(sha or '')} "
                                     f"AND fetched_at=TIMESTAMPTZ {_quote(stamp.isoformat())} THEN {number}")
                    if cases:
                        seq = "CASE " + " ".join(cases) + " END"
            scans.append(f"SELECT {', '.join(cols)}, {at} AS _order_at, {seq} AS _order_seq, "
                         f"true AS _current FROM read_parquet({_quote(str(path))})")
        if modern:
            paths = '[' + ','.join(_quote(str(p)) for p in modern) + ']'
            scans.append(f"SELECT * FROM read_parquet({paths}, union_by_name=true)")
        return " UNION ALL BY NAME ".join(scans) if scans else (
            f"SELECT *, NULL::TIMESTAMPTZ AS _order_at, NULL::BIGINT AS _order_seq, true AS _current FROM ({_EMPTY_VIEW})"
        )

    @classmethod
    def prepare_release_index(cls, root):
        """Build small release/error metadata once, at the retention boundary."""
        files = _current_files(root)
        # Adapt legacy copies once. Original generation files stay byte-identical;
        # queries never reload the entire ordering manifest into Python.
        with duckdb.connect() as con:
            prepared = []
            for path in files:
                schema = pq.read_schema(path)
                if not set(_HISTORY_SCHEMA.names) <= set(schema.names):
                    derived = root / '.release-legacy' / path.parent.name / path.name
                    derived.parent.mkdir(parents=True, exist_ok=True)
                    con.execute(f"COPY ({cls._scan([path], root, history=True)}) TO {_quote(str(derived))} (FORMAT PARQUET)")
                    prepared.append(derived)
                else:
                    prepared.append(path)
            files = prepared
            scan = cls._scan(files, root, history=True)
            con.execute("CREATE TEMP VIEW h AS SELECT *, CASE WHEN release_id IS NULL "
                        "THEN CAST(timezone('UTC', fetched_at) AS DATE) ELSE released_at END AS _day "
                        f"FROM ({scan})")
            con.execute("CREATE TEMP VIEW release_meta AS SELECT 'release' AS kind, source_id, "
                        "coalesce(release_id,snapshot_sha256) AS release_key, _day, "
                        "max(struct_pack(t := _order_at,s := _order_seq)) AS ord "
                        "FROM h GROUP BY source_id,release_key,_day")
            query = (
                "SELECT kind,source_id,release_key,_day,ord.t AS _order_at,ord.s AS _order_seq FROM release_meta "
                "UNION ALL SELECT 'order',NULL,NULL,NULL,NULL,NULL WHERE EXISTS "
                "(SELECT 1 FROM h WHERE _order_at IS NULL OR _order_seq IS NULL) "
                "UNION ALL SELECT 'date',NULL,NULL,NULL,NULL,NULL WHERE EXISTS "
                "(SELECT 1 FROM h WHERE release_id IS NOT NULL AND released_at IS NULL) "
                "UNION ALL SELECT 'ambiguous',source_id,NULL,_day,_order_at,_order_seq FROM h "
                "GROUP BY source_id,table_key,obs_id,_day,_order_at,_order_seq "
                "HAVING count(DISTINCT (snapshot_sha256,release_id))>1"
            )
            con.execute(f"COPY ({query}) TO {_quote(str(root / '.release-index.parquet'))} (FORMAT PARQUET)")
            con.execute(f"CREATE TEMP VIEW current_rows AS {cls._scan(files, root)}")
            latest = (f"SELECT {', '.join(OBSERVATION_RELEASE_COLUMNS)} FROM h WHERE EXISTS "
                      "(SELECT 1 FROM current_rows c WHERE "
                      "(c.source_id,c.table_key,c.obs_id)=(h.source_id,h.table_key,h.obs_id)) "
                      "QUALIFY row_number() OVER (PARTITION BY source_id,table_key,obs_id "
                      "ORDER BY _day DESC,_order_at DESC,_order_seq DESC)=1 ORDER BY source_id,table_key,obs_id")
            con.execute(f"COPY ({latest}) TO {_quote(str(root / '.release-latest.parquet'))} (FORMAT PARQUET)")
        return files

    def pin_generation(self) -> str:
        token, _ = self._generation(None, float('inf'), 10, threading.Event())
        if token is None:
            raise GenerationUnavailable("没有已发布代次")
        return str(token)

    def _generation(self, raw_token, deadline, timeout_s, timed_out):
        directory = self.root / ".observation-generations"
        explicit = raw_token is not None
        try:
            token = GenerationToken.parse(raw_token) if explicit else None
        except (ValueError, TypeError) as exc:
            raise InvalidGenerationToken("generation_token 格式无效") from exc
        try:
            if token is None:
                pointer = directory / "current.json"
                if not pointer.exists():
                    # Once a publication is known, missing retention is an error.
                    if directory.exists() or (self.root / "state-pull.json").exists() or (self.root / "state-observation-generation.json").exists():
                        raise GenerationUnavailable("已发布代次的保留记录不可用")
                    return None, None
                token = GenerationToken.parse(pointer.read_text())
            base = directory / str(token.generation)
            manifest_bytes = (base / "manifest.json").read_bytes()
            digest = sha256(manifest_bytes).hexdigest()
            if digest != token.manifest_sha256:
                raise InvalidGenerationToken("generation_token 清单哈希不匹配")
            record = json.loads((base / "verified.json").read_bytes())
            if record["manifest_sha256"] != digest or record["generation"] != token.generation:
                raise GenerationUnavailable("代次校验记录不匹配")
            for name, expected in record["files"].items():
                _check_timeout(deadline, timeout_s, timed_out)
                path = _dependency(base / "data", name)
                stat = path.stat()
                if not S_ISREG(stat.st_mode) or [stat.st_size, stat.st_mtime_ns] != expected:
                    raise GenerationUnavailable("代次依赖已改变")
            query_files = record['parquet_files']
            if any(name not in record['files'] for name in query_files):
                raise GenerationUnavailable('代次查询文件没有校验记录')
            return token, (base / "data", [base / "data" / name for name in query_files])
        except (GenerationUnavailable, InvalidGenerationToken, QueryTimeout):
            raise
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise GenerationUnavailable("代次或其必需依赖不可用") from exc

    def _release_view(self, con, root, cutoff, deadline, timeout_s, timed_out):
        root, files = root
        index = root / '.release-index.parquet'
        con.execute(f"CREATE TEMP VIEW release_index AS SELECT * FROM read_parquet({_quote(str(index))})")
        if con.execute("SELECT 1 FROM release_index WHERE kind='order' LIMIT 1").fetchone():
            raise QueryError("发布感知读取需要持久化 SnapshotOrder")
        if con.execute("SELECT 1 FROM release_index WHERE kind='date' LIMIT 1").fetchone():
            raise ReleaseDateUnavailable("显式来源发布缺少 released_at")
        condition = "true"
        if isinstance(cutoff, date):
            condition = f"_day <= DATE {_quote(cutoff.isoformat())}"
        elif isinstance(cutoff, str):
            match = "kind='release' AND release_key=" + _quote(cutoff)
            _, boundary = _fetch(con.sql("SELECT source_id, _day, max(struct_pack(t := _order_at, s := _order_seq)) "
                                       f"FROM release_index WHERE {match} GROUP BY source_id, _day"))
            if not boundary:
                raise ReleaseNotFound("该代次没有指定发布标识")
            if len(boundary) != 1:
                raise AmbiguousRelease("发布标识跨来源或对应多个日期")
            _, day, order = boundary[0]
            condition = (f"(_day, _order_at, _order_seq) <= (DATE {_quote(day.isoformat())}, "
                         f"TIMESTAMPTZ {_quote(order['t'].isoformat())}, {order['s']})")
        if con.execute(f"SELECT 1 FROM release_index WHERE kind='ambiguous' AND {condition} LIMIT 1").fetchone():
            raise AmbiguousRelease("同一引用的不同身份具有相同发布排序键")
        if cutoff is None:
            # Latest rows are derived once at retention. Legacy tool metadata
            # queries must not rescan every table's history on every request.
            latest = root / '.release-latest.parquet'
            con.execute(f"CREATE TEMP VIEW observations AS SELECT {', '.join(OBSERVATION_RELEASE_COLUMNS)} "
                        f"FROM read_parquet({_quote(str(latest))})")
            allowed = [index, latest]
        else:
            scan = self._scan(files, root, history=True,
                              check=lambda: _check_timeout(deadline, timeout_s, timed_out))
            con.execute("CREATE TEMP VIEW release_history AS SELECT *, "
                        "CASE WHEN release_id IS NULL THEN CAST(timezone('UTC', fetched_at) AS DATE) "
                        "ELSE released_at END AS _day FROM (" + scan + ")")
            con.execute(f"CREATE TEMP VIEW observations AS SELECT {', '.join(OBSERVATION_RELEASE_COLUMNS)} "
                        f"FROM release_history WHERE {condition} QUALIFY row_number() OVER "
                        "(PARTITION BY source_id,table_key,obs_id ORDER BY _day DESC,_order_at DESC,_order_seq DESC)=1")
            allowed = [*files, index]
        _check_timeout(deadline, timeout_s, timed_out)
        _lock_connection(con, allowed, root)

    @staticmethod
    def _release_evidence(con, sql, columns, rows, generation):
        identity_cols = ("source_id", "table_key", "obs_id", "snapshot_sha256", "release_id")
        # Conservative lineage: computed/aggregate identity aliases are not proof.
        projection = re.match(r"\s*select\s+(.*?)\s+from\s+observations\b", sql, re.I | re.S)
        simple = False
        if projection:
            fields = [p.strip().lower() for p in projection[1].split(',')]
            raw_fields = []
            for field in fields:
                match = re.fullmatch(r'(?:[a-z_]\w*\.)?"?([a-z_]\w*)"?(?:\s+as\s+"?\1"?)?', field)
                if match:
                    raw_fields.append(match[1])
            simple = (any(re.fullmatch(r'(?:[a-z_]\w*\.)?\*', f) for f in fields)
                      or all(c in raw_fields for c in identity_cols))
            simple = simple and len(re.findall(r'\bselect\b', sql, re.I)) == 1 and not re.search(
                r'\b(sum|avg|count|min|max|list|array_agg|string_agg|first|last|any_value)\s*\('
                r'|\bover\s*\(|\bgroup\s+by\b|\b(union|intersect|except)\b', sql, re.I)
        evidence, found = [], {}
        for row in rows:
            identities = []
            if simple and all(columns.count(c) == 1 for c in identity_cols):
                key = tuple(row[columns.index(c)] for c in identity_cols)
                match = con.execute(
                    "SELECT * FROM observations WHERE " + " AND ".join(f"{c} IS NOT DISTINCT FROM ?" for c in identity_cols),
                    key,
                ).to_arrow_table().to_pylist()
                if len(match) == 1:
                    obs = _observation(match[0])
                    identities = [obs.identity]
                    found.setdefault(obs.identity, obs)
            evidence.append(ObservationRowEvidence(identities=identities, generation=generation))
        return list(found.values()), evidence

    def import_release(self, source_id, table_key, observations, order):
        """Archive an explicitly labelled release; advance current only if newer."""
        with self._describe_lock:
            if self._needs_install:
                _install_published_segments(self.root)
                self._needs_install = False
            if self._needs_migration:
                self._migrate_history()
            path = self.path_for(source_id, table_key)
            with duckdb.connect() as con:
                current = con.sql(self._scan([path], self.root)).to_arrow_table().to_pylist() if path.exists() else []
            if not current or min(_release_key(_row(o), order) for o in observations) > max(
                _release_key(row, SnapshotOrder(fetched_at=row['_order_at'], seq=row['_order_seq'])
                             if row['_order_seq'] is not None else None) for row in current
            ):
                # Import chronology is independent of the table's ingestion watermark.
                return self._write_table(source_id, table_key, observations, order, check_watermark=False)
            # Archive without touching current membership or its ingestion watermark.
            incoming = [{**_row(o), '_order_at': order.fetched_at, '_order_seq': order.seq, '_current': False}
                        for o in observations]
            for o in observations:
                if o.source_id != source_id or o.table_key != table_key or (o.value is not None and not _fits(o.value)):
                    raise ValueError("历史发布观测值不属于表或数值无法无损保存")
            if len({o.identity for o in observations}) != len(observations):
                raise ValueError("ObservationIdentity 重复")
            table = pq.read_table(path)
            self._append(path, incoming, table, table.schema.metadata or {})
            self._invalidate_describe()
            return True


    def _provenance(
        self, con: duckdb.DuckDBPyConnection, columns: list[str], rows: list[tuple[Any, ...]]
    ) -> tuple[list[Observation], list[str]]:
        if "obs_id" not in columns:
            return [], []
        indexes = {name: columns.index(name) for name in ("source_id", "table_key", "obs_id") if name in columns}
        obs_idx = indexes["obs_id"]
        wanted = list(dict.fromkeys(r[obs_idx] for r in rows if isinstance(r[obs_idx], str)))
        if not wanted:
            return [], []
        rel = con.execute(
            "SELECT * FROM observations WHERE obs_id IN (SELECT unnest(?::VARCHAR[]))"
            " ORDER BY source_id, table_key",
            [wanted],
        )
        by_id: dict[str, list[Observation]] = {}
        for d in rel.to_arrow_table().to_pylist():
            obs = _observation(d)
            by_id.setdefault(obs.obs_id, []).append(obs)

        found: dict[tuple[str, str, str], Observation] = {}
        ambiguous: list[str] = []
        identity_columns = [name for name in ("source_id", "table_key") if name in indexes]
        for row in rows:
            obs_id = row[obs_idx]
            if not isinstance(obs_id, str):
                continue
            matches = by_id.get(obs_id, [])
            if identity_columns:
                matches = [
                    obs for obs in matches
                    if all(getattr(obs, name) == row[indexes[name]] for name in identity_columns)
                ]
            if len(matches) == 1:
                obs = matches[0]
                ref = (obs.source_id, obs.table_key, obs.obs_id)
                found.setdefault(ref, obs)
            elif len(matches) > 1 and obs_id not in ambiguous:
                ambiguous.append(obs_id)

        warnings = []
        if ambiguous:
            warnings.append(
                "以下 obs_id 在多张表中有歧义，未附出处："
                f"{json.dumps(ambiguous, ensure_ascii=False)}；请在 SELECT 里带上 source_id、table_key、obs_id"
            )
        return list(found.values()), warnings


def _guard(sql: str) -> None:
    """白名单：单条 SELECT/WITH，不调用读文件、执行 SQL 串的函数。分词和解析都用 DuckDB 自己的。"""
    sql_bytes = sql.encode("utf-8")
    tokens = duckdb.tokenize(sql)
    first = _word(sql_bytes, tokens[0][0]) if tokens and tokens[0][1] == duckdb.token_type.keyword else ""
    if first not in ("select", "with"):
        raise QueryRejected("只允许单条 SELECT / WITH 查询")
    if any(sql_bytes[pos] == ord(";") for pos, _ in tokens[:-1]):
        raise QueryRejected("不允许多条语句")
    for pos, kind in tokens:
        if kind in _WORD_TOKENS and _FORBIDDEN_NAMES.fullmatch(name := _word(sql_bytes, pos)):
            raise QueryRejected(f"不允许使用 {name!r}（读文件或执行 SQL 串）")
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as exc:
        raise QueryError(f"SQL 无法解析：{exc}") from exc
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise QueryRejected("只允许单条 SELECT / WITH 查询")


def _check_timeout(deadline: float, timeout_s: float, timed_out: threading.Event) -> None:
    if timed_out.is_set() or _monotonic() >= deadline:
        raise QueryTimeout(f"查询（含连接、查询和出处回查）超过 {timeout_s:g} 秒")


def _word(sql: bytes, pos: int) -> str:
    """按 DuckDB 给出的 UTF-8 字节偏移取词，去掉双引号，小写。"""
    mt = _WORD.match(sql[pos:].decode("utf-8"))
    if mt is None:
        return ""
    return (mt.group(2) if mt.group(1) is None else mt.group(1).replace('""', '"')).lower()


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _fetch(rel: duckdb.DuckDBPyRelation) -> tuple[list[str], list[tuple[Any, ...]]]:
    """经 Arrow 取结果（DuckDB 直接转 TIMESTAMPTZ 要 pytz，Arrow 用 zoneinfo）。"""
    table = rel.to_arrow_table()
    cols = [[_plain(v) for v in c.to_pylist()] for c in table.columns]
    return table.column_names, list(zip(*cols, strict=True))


def _fits(v: Decimal) -> bool:
    return v.normalize(_CTX).as_tuple().exponent >= -SCALE and abs(v) < _MAX_ABS


def _plain(v: Any) -> Any:
    """DECIMAL(38, 10) 取回时带着 10 位小数；去掉多余的 0（数值不变）。"""
    if not isinstance(v, Decimal):
        return v
    return v.quantize(Decimal(1), context=_CTX) if v == v.to_integral_value() else v.normalize(_CTX)


def _row(o: Observation) -> dict[str, Any]:
    d = o.model_dump()
    d["granularity"] = o.granularity.value
    d["dims"] = json.dumps(o.dims, ensure_ascii=False, sort_keys=True)
    return d


def _observation(d: dict[str, Any]) -> Observation:
    return Observation(**{**d, "dims": json.loads(d["dims"]), "value": _plain(d["value"])})


def _event_key(row):
    return tuple(row.get(c) for c in ('source_id', 'table_key', 'obs_id', 'snapshot_sha256',
                                       'release_id', '_order_at', '_order_seq'))


def _release_key(row, order):
    day = row.get('released_at') if row.get('release_id') is not None else row['fetched_at'].astimezone(UTC).date()
    if day is None:
        raise ReleaseDateUnavailable('显式来源发布缺少 released_at')
    return day, order.fetched_at if order else row['fetched_at'], order.seq if order else -1


def _atomic_parquet(table, path, *, immutable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.', suffix='.tmp')
    os.close(fd)
    try:
        pq.write_table(table, tmp)
        if immutable:
            os.link(tmp, path)  # Publish complete bytes without clobbering another immutable file.
        else:
            os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _lock_connection(con, files, root=None):
    if files:
        for path in list(files):
            metadata = pq.read_schema(path).metadata or {}
            if root is not None and _SEGMENTS in metadata:
                files = [*files, *_segment_paths(root, path, metadata)]
            if root is not None and _HISTORY_FILE in metadata:
                name = metadata[_HISTORY_FILE].decode()
                if not re.fullmatch(r'[0-9a-f]{32}\.parquet', name):
                    raise QueryError('历史文件引用无效')
                files = [*files, path.with_name(name)]
        con.execute('SET allowed_paths = [' + ','.join(_quote(str(p)) for p in files) + ']')
    con.execute('SET autoinstall_known_extensions = false')
    con.execute('SET autoload_known_extensions = false')
    con.execute('SET enable_external_access = false')
    con.execute('SET lock_configuration = true')


def _dependency(root: Path, name: str) -> Path:
    path = root / name
    if not name or Path(name).is_absolute() or '..' in Path(name).parts:
        raise GenerationUnavailable('代次依赖路径无效')
    if not path.resolve().is_relative_to(root.resolve()) or any(p.is_symlink() for p in (path, *path.parents)):
        raise GenerationUnavailable('代次依赖不能是符号链接')
    return path


def _file_digest(path):
    digest = sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def retain_generation(root: Path, manifest_bytes: bytes, snapshot_root: Path, *, verified: bool = False, immutable_root: Path | None = None) -> str:
    """Retain a published boundary. Call only after validating its effective chain.

    ``verified`` is for the state restore/pull boundary, which has already checked
    every copy, raw SHA and publication state. Queries never call this function.
    Mutable live files are copied; immutable files and verified previous copies
    can share inodes. ``immutable_root`` permits push to reuse checked live blobs.
    """
    from datumoj.rikolto.core.state import _copy_path

    manifest = StateGeneration.model_validate_json(manifest_bytes)
    _install_published_segments(Path(snapshot_root))
    digest = sha256(manifest_bytes).hexdigest()
    token = str(GenerationToken(generation=manifest.generation, manifest_sha256=digest))
    directory = Path(root) / '.observation-generations'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / str(manifest.generation)
    if target.exists():
        if (target / 'manifest.json').read_bytes() != manifest_bytes:
            raise GenerationUnavailable('保留代次编号发生冲突')
        # Never repair an unavailable token from mutable live data.
        record = json.loads((target / 'verified.json').read_bytes())
        if record['manifest_sha256'] != digest:
            raise GenerationUnavailable('代次校验记录无效')
        for name, expected in record['files'].items():
            stat = _dependency(target / 'data', name).stat()
            if not S_ISREG(stat.st_mode) or [stat.st_size, stat.st_mtime_ns] != expected:
                raise GenerationUnavailable('保留代次依赖已改变')
        _write_generation_pointer(directory, token)
        return token
    previous_data, previous_record = None, {}
    pointer = directory / 'current.json'
    if pointer.exists():
        previous_token = GenerationToken.parse(pointer.read_text())
        previous_base = directory / str(previous_token.generation)
        previous_record = json.loads((previous_base / 'verified.json').read_bytes())
        previous_data = previous_base / 'data'
    previous_hashes = {digest: name for name, digest in previous_record.get('store_hashes', {}).items()}
    with tempfile.TemporaryDirectory(prefix='.retain-', dir=directory) as temp:
        stage = Path(temp)
        data = stage / 'data'
        data.mkdir()
        expected, modes, hashes = {}, {}, {}

        def preserve(source, name, *, immutable=False, digest=None):
            destination = _dependency(data, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            previous = None
            if digest is not None and previous_data is not None and digest in previous_hashes:
                previous_name = previous_hashes[digest]
                candidate = _dependency(previous_data, previous_name)
                stat = candidate.stat()
                if [stat.st_size, stat.st_mtime_ns] != previous_record['files'][previous_name]:
                    raise GenerationUnavailable('前代依赖已改变')
                previous = candidate
            if previous is not None:
                modes[name] = _link_or_copy(previous, destination)
            elif immutable:
                candidate = _dependency(Path(immutable_root), name) if immutable_root is not None else source
                if candidate != source and (not candidate.exists() or _file_digest(candidate) != digest):
                    candidate = source
                modes[name] = _link_or_copy(candidate, destination)
            else:
                shutil.copyfile(source, destination)
                modes[name] = 'copy'
            expected[name] = destination
            if digest is not None:
                hashes[name] = digest
            return destination

        for copy in manifest.stores:
            if copy.format == 'sqlite-block-v1':
                continue  # The reconstructed whole database checksum covers all blocks.
            # Reuse the state format's logical-to-local path mapping (notably
            # vectors/vectors-state.json lives beside the other local stores).
            name = _copy_path(data, copy.name).relative_to(data).as_posix()
            source = _dependency(Path(snapshot_root), name)
            immutable = False
            if copy.format == 'parquet':
                metadata = pq.read_schema(source).metadata or {}
                immutable = _HISTORY_ONLY in metadata
                if _SEGMENT_TABLE in metadata:
                    value = metadata[_SEGMENT_TABLE].decode()
                    if not re.fullmatch(r'[a-z]{2,4}(?:-[a-z0-9]+)+/[0-9a-f]{32}\.parquet', value):
                        raise GenerationUnavailable('发布历史段路径无效')
                    sid, table = value.split('/')
                    name = f'parquet-history/{sid}/{Path(table).stem}/{source.name}'
            destination = preserve(source, name, immutable=immutable, digest=copy.sha256 if copy.format != 'duckdb-empty' else _file_digest(source))
            if not verified:
                if copy.format == 'duckdb-empty':
                    descriptor = b'{"format":"duckdb-empty-v1"}\n'
                    if sha256(descriptor).hexdigest() != copy.sha256 or len(descriptor) != copy.size_bytes:
                        raise GenerationUnavailable('DuckDB 重建描述不匹配')
                    with duckdb.connect(str(destination), read_only=True) as con:
                        if con.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='main' LIMIT 1").fetchone():
                            raise GenerationUnavailable('DuckDB 重建副本非空')
                elif destination.stat().st_size != copy.size_bytes or _file_digest(destination) != copy.sha256:
                    raise GenerationUnavailable('代次存储副本校验失败')
            expected[name] = destination
        for path in _current_files(data):
            metadata = pq.read_schema(path).metadata or {}
            for segment in _segment_paths(data, path, metadata):
                name = segment.relative_to(data).as_posix()
                if name not in expected:
                    source = _dependency(Path(snapshot_root), name)
                    preserve(source, name, immutable=True, digest=_file_digest(source))
        manifest_path = data / 'manifest.sqlite'
        if manifest_path.exists():
            with sqlite3.connect(f'file:{manifest_path}?mode=ro', uri=True) as db:
                if db.execute("SELECT 1 FROM publish_states WHERE state='staged' LIMIT 1").fetchone():
                    raise GenerationUnavailable('代次包含未完成发布')
                refs = [r[0] for r in db.execute('SELECT DISTINCT sha256 FROM snapshots WHERE sha256 IS NOT NULL')]
            for sha in refs:
                if not re.fullmatch('[0-9a-f]{64}', sha):
                    raise GenerationUnavailable('raw 哈希无效')
                name = f'raw/{sha[:2]}/{sha}'
                source = _dependency(Path(snapshot_root), name)
                destination = preserve(source, name, immutable=True, digest=sha)
                if not verified and _file_digest(destination) != sha:
                    raise GenerationUnavailable('代次 raw 校验失败')
                expected[name] = destination
        if not (data / 'observations.duckdb').exists():
            # Old publications may have only Parquet. This is a derived empty
            # query catalog, never the mutable live database.
            duckdb.connect(str(data / 'observations.duckdb')).close()
            expected['observations.duckdb'] = data / 'observations.duckdb'
        query_files = DuckDBObservationStore.prepare_release_index(data)
        for path in (data / '.release-legacy').rglob('*.parquet'):
            expected[path.relative_to(data).as_posix()] = path
        expected['.release-index.parquet'] = data / '.release-index.parquet'
        expected['.release-latest.parquet'] = data / '.release-latest.parquet'
        for name in expected:
            modes.setdefault(name, 'copy')  # Derived indexes and reconstructed catalogs.
        files = {name: [p.stat().st_size, p.stat().st_mtime_ns] for name, p in expected.items()}
        (stage / 'manifest.json').write_bytes(manifest_bytes)
        (stage / 'verified.json').write_text(json.dumps({
            'generation': manifest.generation, 'manifest_sha256': digest, 'files': files,
            'size_bytes': sum(s[0] for s in files.values()), 'storage': 'copy',
            'file_storage': modes, 'store_hashes': hashes,
            'parquet_files': [p.relative_to(data).as_posix() for p in query_files],
        }))
        os.replace(stage, target)
    _write_generation_pointer(directory, token)
    return token


def _write_generation_pointer(directory, token):
    pointer = directory / 'current.json'
    fd, temp = tempfile.mkstemp(dir=directory, prefix='.current-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(token)
        os.replace(temp, pointer)
        (directory.parent / 'state-observation-generation.json').write_text(token)
    finally:
        Path(temp).unlink(missing_ok=True)
