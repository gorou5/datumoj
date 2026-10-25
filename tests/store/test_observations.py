import time
from datetime import date
from decimal import Decimal

import duckdb
import pytest

from datumoj.core.contracts import QueryError, QueryRejected, QueryTimeout
from datumoj.core.models import SnapshotOrder
from datumoj.store.observations import DuckDBObservationStore
from datumoj.testing.contract_suites import ObservationStoreSuite
from datumoj.testing.samples import T0, make_observation


class TestDuckDBObservationStore(ObservationStoreSuite):
    @pytest.fixture
    def store(self, tmp_path):
        return DuckDBObservationStore(tmp_path / "obs")


@pytest.fixture
def store(tmp_path):
    s = DuckDBObservationStore(tmp_path / "obs")
    s.write_table("jp-stat", "t1", [
        make_observation("o1", "13960000", locator="row=1"),
        make_observation("o2", None, note="x", area_code="47000", locator="row=2"),
    ])
    return s


# ---------- 第一层：白名单的绕过尝试 ----------


@pytest.mark.parametrize("sql", [
    # 大小写、引号、注释、限定名包装的读文件函数
    "SELECT * FROM READ_CSV('/etc/passwd')",
    "SELECT * FROM \"read_csv\"('/etc/passwd')",
    "SELECT * FROM read_csv/* x */('/etc/passwd')",
    "SELECT * FROM main.read_csv_auto('/etc/passwd')",
    "SELECT * FROM read_text('/etc/passwd')",
    "SELECT * FROM read_blob('/etc/passwd')",
    "SELECT * FROM read_json('/etc/passwd')",
    "SELECT * FROM parquet_scan('x.parquet')",
    "SELECT * FROM parquet_metadata('x.parquet')",
    "SELECT * FROM glob('/etc/*')",
    "SELECT * FROM sqlite_scan('x.db', 't')",
    "SELECT obs_id FROM observations WHERE obs_id IN (SELECT column0 FROM read_csv('/etc/passwd'))",
    # 把 SQL 串交给 query() 执行，绕开分词检查
    "SELECT * FROM query('SELECT * FROM read_csv(''/etc/passwd'')')",
    # 多语句藏在注释、字符串、转义字符串、美元引号后面
    "-- 注释\nSELECT 1; DROP TABLE observations",
    "SELECT 1 /* ; */; SET enable_external_access = true",
    "SELECT ';'; SET enable_external_access = true",
    "SELECT E'it\\'s'; SET enable_external_access = true",
    "SELECT $$;$$; SET enable_external_access = true",
    "SELECT 1;;",
    "SELECT (1); SET enable_external_access = true",
    # 不是 SELECT/WITH 开头，或解析后不是 SELECT
    "WITH x AS (SELECT 1 AS a) INSERT INTO observations (obs_id) SELECT a FROM x",
    "FROM observations",
    "DESCRIBE observations",
    "SUMMARIZE observations",
    "CALL pragma_version()",
    "EXPORT DATABASE '/tmp/x'",
    "INSTALL httpfs",
    "LOAD httpfs",
    "RESET enable_external_access",
    "SET lock_configuration = false",
    "SET VARIABLE x = 1",
    "DETACH observations",
    "CHECKPOINT",
    "USE memory",
    "CREATE TEMP TABLE t AS SELECT 1",
    "CREATE OR REPLACE TEMP VIEW observations AS SELECT 1 AS obs_id",
    "UPDATE observations SET value = 0",
    "   ",
    "",
])
def test_bypass_attempts_rejected(store, sql):
    with pytest.raises(QueryRejected):
        store.query_readonly(sql)
    assert [r[0] for r in store.query_readonly("SELECT obs_id FROM observations ORDER BY 1").rows] == ["o1", "o2"]


def test_string_literal_path_is_not_readable(store, tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    with pytest.raises(QueryRejected):
        store.query_readonly(f"SELECT * FROM '{secret}'")


def test_comment_or_string_mentioning_forbidden_words_is_fine(store):
    r = store.query_readonly("SELECT obs_id, 'read_csv; DROP' AS s FROM observations /* read_csv */ ORDER BY obs_id;")
    assert r.rows == [("o1", "read_csv; DROP"), ("o2", "read_csv; DROP")]


def test_syntax_error_is_query_error_not_rejection(store):
    with pytest.raises(QueryError) as info:
        store.query_readonly("SELECT FROM WHERE")
    assert not isinstance(info.value, QueryRejected)


# ---------- 第二层：引擎配置（即使白名单被绕过） ----------


@pytest.mark.parametrize("sql", [
    "SET enable_external_access = true",
    "RESET enable_external_access",
    "SET lock_configuration = false",
    "SET allowed_paths = ['/etc/passwd']",
    "SET autoload_known_extensions = true",
])
def test_locked_configuration_cannot_be_reopened(store, sql):
    con = store._connect()
    try:
        with pytest.raises(duckdb.Error, match="locked"):
            con.execute(sql)
        assert con.execute("SELECT current_setting('enable_external_access')").fetchone() == (False,)
        with pytest.raises(duckdb.PermissionException):
            con.execute("SELECT * FROM read_csv('/etc/passwd')").fetchall()
    finally:
        con.close()


def test_engine_blocks_writes_and_file_access(store, tmp_path):
    con = store._connect()
    try:
        with pytest.raises(duckdb.Error, match="read-only"):
            con.execute("CREATE TABLE t (a INTEGER)")
        with pytest.raises(duckdb.PermissionException):
            con.execute(f"COPY observations TO '{tmp_path / 'out.csv'}'")
        with pytest.raises(duckdb.PermissionException):
            con.execute(f"COPY observations TO '{store.path_for('jp-stat', 't1')}'")
        with pytest.raises(duckdb.Error):
            con.execute(f"ATTACH '{tmp_path / 'other.duckdb'}' AS other")
    finally:
        con.close()
    assert not (tmp_path / "out.csv").exists() and not (tmp_path / "other.duckdb").exists()
    assert len(store.query_readonly("SELECT obs_id FROM observations").rows) == 2


# ---------- 超时、行数、参数 ----------


def test_timeout(store):
    start = time.monotonic()
    with pytest.raises(QueryTimeout):
        store.query_readonly("SELECT count(*) FROM range(100000) a, range(100000) b, range(100) c", timeout_s=0.3)
    assert time.monotonic() - start < 5
    assert len(store.query_readonly("SELECT obs_id FROM observations").rows) == 2


def test_bad_limits(store):
    with pytest.raises(ValueError):
        store.query_readonly("SELECT 1", max_rows=0)
    with pytest.raises(ValueError):
        store.query_readonly("SELECT 1", timeout_s=0)


# ---------- 空值和注记 ----------


NOTES = ["-", "…", "x", "秘匿", "***"]


@pytest.fixture
def noted(tmp_path):
    s = DuckDBObservationStore(tmp_path / "obs")
    obs = [make_observation(f"n{i}", None, note=n, locator=f"row={i}") for i, n in enumerate(NOTES)]
    obs.append(make_observation("zero", "0", locator="row=99"))
    s.write_table("jp-stat", "t1", obs)
    return s, obs


def test_notes_kept_and_values_stay_null(noted):
    store, obs = noted
    r = store.query_readonly("SELECT obs_id, value, note FROM observations WHERE value IS NULL ORDER BY obs_id")
    assert r.rows == [(f"n{i}", None, n) for i, n in enumerate(NOTES)]
    assert r.provenance == obs[:-1]
    assert all(p.value is None for p in r.provenance)


def test_nulls_never_become_zero(noted):
    store, _ = noted
    r = store.query_readonly(
        "SELECT count(*), count(value), sum(value) FILTER (WHERE value IS NULL), "
        "count(*) FILTER (WHERE value = 0) FROM observations"
    )
    assert r.rows == [(6, 1, None, 1)]
    (zero,) = store.query_readonly("SELECT obs_id, value, note FROM observations WHERE obs_id = 'zero'").rows
    assert zero == ("zero", Decimal(0), None)


# ---------- 出处与存储 ----------


def test_provenance_roundtrips_every_field(tmp_path):
    store = DuckDBObservationStore(tmp_path / "obs")
    obs = [
        make_observation("a", "0.0000000001", dims={"性別": "女", "年齢": "15〜64歳"}, unit=None,
                         indicator_code="A1101", area_code=None, area_label=None, time_start=None, time_end=None,
                         time_label="令和2年度", locator="table=1;cell=B2"),
        make_observation("b", "-123456789012345678901234567.5", locator="row=2"),
        make_observation("c", "1E+5", locator="row=3"),
    ]
    store.write_table("jp-stat", "t1", obs)
    r = store.query_readonly("SELECT obs_id, value FROM observations ORDER BY obs_id DESC")
    assert r.provenance == obs[::-1]
    assert r.rows == [("c", Decimal("100000")), ("b", Decimal("-123456789012345678901234567.5")),
                      ("a", Decimal("1E-10"))]
    assert str(r.rows[0][1]) == "100000"
    dims = store.query_readonly("SELECT dims FROM observations WHERE obs_id = 'a'").rows[0][0]
    assert dims == '{"年齢": "15〜64歳", "性別": "女"}'


def test_provenance_dedup_in_first_seen_order(store):
    r = store.query_readonly("SELECT obs_id FROM observations, range(3) ORDER BY obs_id DESC")
    assert [p.obs_id for p in r.provenance] == ["o2", "o1"]
    assert r.warnings == []
    agg = store.query_readonly("SELECT sum(value) AS s FROM observations")
    assert agg.rows == [(Decimal("13960000"),)] and agg.provenance == [] and agg.warnings


@pytest.mark.parametrize("value", ["0.00000000001", "1E+28", "-1E+28"])
def test_value_that_cannot_be_stored_exactly_is_rejected(tmp_path, value):
    store = DuckDBObservationStore(tmp_path / "obs")
    with pytest.raises(ValueError, match="DECIMAL"):
        store.write_table("jp-stat", "t1", [make_observation("o1", value)])


def test_persists_and_rewrites_atomically(store, tmp_path):
    path = store.path_for("jp-stat", "t1")
    assert path.is_file() and path.is_relative_to(tmp_path / "obs" / "parquet" / "jp-stat")
    again = DuckDBObservationStore(tmp_path / "obs")
    assert len(again.query_readonly("SELECT obs_id FROM observations").rows) == 2
    with pytest.raises(ValueError):
        again.write_table("jp-stat", "t1", [make_observation("o1", "1"), make_observation("o1", "2")])
    assert len(again.query_readonly("SELECT obs_id FROM observations").rows) == 2  # 校验失败不动旧数据
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # 没有残留的临时文件


def test_table_keys_are_case_sensitive_and_cannot_escape(tmp_path):
    store = DuckDBObservationStore(tmp_path / "obs")
    for key in ["T1", "t1", "../../x", "a/b"]:
        store.write_table("jp-stat", key, [make_observation(f"{key}-1", "1", table_key=key)])
        assert store.path_for("jp-stat", key).parent == tmp_path / "obs" / "parquet" / "jp-stat"
    r = store.query_readonly("SELECT table_key FROM observations ORDER BY table_key")
    assert r.rows == [("../../x",), ("T1",), ("a/b",), ("t1",)]
    with pytest.raises(ValueError):
        store.write_table("../etc", "t1", [])


def test_empty_store(tmp_path):
    store = DuckDBObservationStore(tmp_path / "obs")
    r = store.query_readonly("SELECT obs_id, value, fetched_at FROM observations")
    assert r.columns == ["obs_id", "value", "fetched_at"] and r.rows == []
    assert store.describe().tables == []


def test_describe_several_tables(tmp_path):
    store = DuckDBObservationStore(tmp_path / "obs")
    store.write_table("jp-stat", "t2", [
        make_observation("p1", "1", table_key="t2", survey="労働力調査", indicator="就業者数", area_code=None,
                         time_start=date(2021, 1, 1), time_end=date(2021, 1, 31)),
        make_observation("p2", None, note="-", table_key="t2", survey="労働力調査", indicator="失業者数",
                         area_code=None, time_start=None, time_end=None),
    ])
    store.write_table("jp-stat", "t1", [make_observation("o1", "1")])
    t1, t2 = store.describe().tables
    assert (t1.table_key, t2.table_key) == ("t1", "t2")
    assert (t2.surveys, t2.indicators, t2.area_count, t2.rows) == (["労働力調査"], ["失業者数", "就業者数"], 0, 2)
    assert (t2.time_start, t2.time_end) == (date(2021, 1, 1), date(2021, 1, 31))


@pytest.mark.parametrize("empty", [False, True])
def test_table_and_watermark_share_atomic_replace(tmp_path, monkeypatch, empty):
    import pyarrow.parquet as pq

    import datumoj.store.observations as module

    store = DuckDBObservationStore(tmp_path / "obs")
    older = SnapshotOrder(fetched_at=T0, seq=1)
    newer = SnapshotOrder(fetched_at=T0, seq=2)
    store.write_table("jp-stat", "t1", [make_observation("old", "1")], order=older)
    assert store.describe().tables[0].rows == 1
    path = store.path_for("jp-stat", "t1")
    original = path.read_bytes()

    def fail_replace(src, dst):
        assert dst == path
        pending = pq.read_table(src)
        assert pending.num_rows == (0 if empty else 1)
        assert SnapshotOrder.model_validate_json(pending.schema.metadata[b"datumoj.snapshot_order"]) == newer
        assert path.read_bytes() == original
        assert store.table_order("jp-stat", "t1") == older
        raise OSError("synthetic replace failure")

    with monkeypatch.context() as patch:
        patch.setattr(module.os, "replace", fail_replace)
        with pytest.raises(OSError, match="synthetic"):
            store.write_table("jp-stat", "t1", [] if empty else [make_observation("new", "2")], order=newer)
    assert path.read_bytes() == original
    assert store.table_order("jp-stat", "t1") == older
    assert list(path.parent.iterdir()) == [path]
    assert store.write_table("jp-stat", "t1", [] if empty else [make_observation("new", "2")], order=newer)
    reopened = DuckDBObservationStore(tmp_path / "obs")
    assert reopened.table_order("jp-stat", "t1") == newer
    assert reopened.query_readonly("SELECT obs_id FROM observations").rows == ([] if empty else [("new",)])
    assert bool(store.describe().tables) == (not empty)
    assert not reopened.write_table("jp-stat", "t1", [make_observation("old", "1")], order=older)


def test_corrupt_watermark_does_not_silently_overwrite(tmp_path):
    import pyarrow.parquet as pq

    store = DuckDBObservationStore(tmp_path / "obs")
    store.write_table("jp-stat", "t1", [make_observation("old", "1")])
    path = store.path_for("jp-stat", "t1")
    table = pq.read_table(path).replace_schema_metadata({b"datumoj.snapshot_order": b"invalid"})
    pq.write_table(table, path)
    original = path.read_bytes()
    with pytest.raises(ValueError):
        store.write_table("jp-stat", "t1", [], order=SnapshotOrder(fetched_at=T0, seq=1))
    assert path.read_bytes() == original
