"""统计观测值库（DATUMOJ-IVY-303）：每张表一个 Parquet 文件，DuckDB 只读视图 ``observations``。

目录布局（root 下）：
- ``parquet/<source_id>/<sha256(table_key) 前 32 位>.parquet``：一张表一个文件，整体替换。
  文件名用哈希而不用 table_key 原文：大小写不敏感的文件系统上 ``T1`` 和 ``t1`` 不会互相覆盖，
  ``/``、``..`` 也走不出目录。真正的键在文件里的 table_key 列。
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
import tempfile
import threading
import time
from collections.abc import Sequence
from decimal import Context, Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import TypeAdapter

from datumoj.core.contracts import (
    OBSERVATION_COLUMNS,
    ObservationCatalog,
    QueryError,
    QueryRejected,
    QueryResult,
    QueryTimeout,
    TableSummary,
    TooManyRows,
)
from datumoj.core.models import Observation, SourceId, Text

SCALE = 10
"""value 列存成 DECIMAL(38, 10)：所有文件同一类型，视图合并时不会被隐式转换截断。"""
_MAX_ABS = Decimal(10) ** (38 - SCALE)
_CTX = Context(prec=38)  # 默认上下文只有 28 位，normalize/quantize 会把 38 位的值舍入

_SCHEMA = pa.schema([
    (c, pa.date32() if c in ("time_start", "time_end")
     else pa.decimal128(38, SCALE) if c == "value"
     else pa.timestamp("us", tz="UTC") if c == "fetched_at"
     else pa.string())
    for c in OBSERVATION_COLUMNS
])
_SQL_TYPES = {"time_start": "DATE", "time_end": "DATE", "value": f"DECIMAL(38, {SCALE})", "fetched_at": "TIMESTAMPTZ"}
_EMPTY_VIEW = "SELECT " + ", ".join(
    f"CAST(NULL AS {_SQL_TYPES.get(c, 'VARCHAR')}) AS {c}" for c in OBSERVATION_COLUMNS
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


class DuckDBObservationStore:
    """``ObservationStore`` 的实现。数据只经 write_table 写入；查询连接一律只读。"""

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

    # ---------- 写 ----------

    def path_for(self, source_id: str, table_key: str) -> Path:
        _source_id.validate_python(source_id)
        _text.validate_python(table_key)
        return self._parquet / source_id / f"{sha256(table_key.encode()).hexdigest()[:32]}.parquet"

    def write_table(self, source_id: str, table_key: str, observations: Sequence[Observation]) -> None:
        path = self.path_for(source_id, table_key)
        ids: set[str] = set()
        for o in observations:
            if o.source_id != source_id or o.table_key != table_key:
                raise ValueError(f"观测值 {o.obs_id} 不属于表 {source_id}/{table_key}")
            if o.obs_id in ids:
                raise ValueError(f"obs_id 重复：{o.obs_id}")
            ids.add(o.obs_id)
            if o.value is not None and not _fits(o.value):
                raise ValueError(f"观测值 {o.obs_id} 的 value={o.value} 超出 DECIMAL(38, {SCALE})，无法无损保存")
        if not observations:
            path.unlink(missing_ok=True)
            self._invalidate_describe()
            return
        table = pa.Table.from_pylist([_row(o) for o in observations], schema=_SCHEMA)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".", suffix=".tmp")
        os.close(fd)
        try:
            pq.write_table(table, tmp)
            os.replace(tmp, path)
            self._invalidate_describe()
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ---------- 读 ----------

    def query_readonly(self, sql: str, max_rows: int = 1000, timeout_s: float = 10) -> QueryResult:
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
            con = self._connect()
            connection.append(con)
            _check_timeout(deadline, timeout_s, timed_out)
            columns, rows = _fetch(con.sql(sql).limit(max_rows + 1))
            if len(rows) > max_rows:
                raise TooManyRows(f"结果超过 {max_rows} 行，请加条件或聚合")
            provenance, provenance_warnings = self._provenance(con, columns, rows)
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
        return QueryResult(columns=columns, rows=rows, provenance=provenance, warnings=warnings)

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
                    catalog = ObservationCatalog(tables=[
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
        for path in sorted(self._parquet.glob("*/*.parquet")):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            generation.append((str(path), stat.st_size, stat.st_mtime_ns))
        return tuple(generation)

    def _connect(self) -> duckdb.DuckDBPyConnection:
        """只读连接：视图 ``observations`` 已建好，外部访问已关闭且配置已锁定。"""
        files = sorted(self._parquet.glob("*/*.parquet"))
        con = duckdb.connect(str(self._db), read_only=True)
        try:
            if files:
                paths = "[" + ", ".join(_quote(str(f)) for f in files) + "]"
                cols = ", ".join(OBSERVATION_COLUMNS)
                con.execute(f"CREATE TEMP VIEW observations AS SELECT {cols} FROM read_parquet({paths})")
                con.execute(f"SET allowed_paths = {paths}")
            else:
                con.execute(f"CREATE TEMP VIEW observations AS {_EMPTY_VIEW}")
            con.execute("SET autoinstall_known_extensions = false")
            con.execute("SET autoload_known_extensions = false")
            con.execute("SET enable_external_access = false")
            con.execute("SET lock_configuration = true")
        except BaseException:
            con.close()
            raise
        return con

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
