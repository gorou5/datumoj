import sqlite3
import threading

import pytest

from datumoj.core.contracts import NO_PARSER, ParseRecord, ParseStatus, SchemaVersionError
from datumoj.core.manifest import SCHEMA_VERSION, SqliteManifest
from datumoj.core.models import FetchOutcome
from datumoj.testing.contract_suites import ManifestSuite
from datumoj.testing.samples import T0, make_snapshot, sha

URL = "https://example.go.jp/a"


class TestSqliteManifest(ManifestSuite):
    @pytest.fixture
    def manifest(self, tmp_path):
        m = SqliteManifest(tmp_path / "manifest.db")
        yield m
        m.close()


def test_persists_across_connections(tmp_path):
    path = tmp_path / "m.db"
    first = SqliteManifest(path)
    snap = make_snapshot(url=URL, snapshot_id="s1")
    first.record_snapshot(snap, {"k": 1})
    first.close()
    second = SqliteManifest(path)
    assert second.latest_snapshot(URL) == snap
    assert second.snapshot_meta("s1") == {"k": 1}
    second.close()


def test_failures_of_every_kind_are_recorded(tmp_path):
    m = SqliteManifest(tmp_path / "m.db")
    for i, outcome in enumerate([FetchOutcome.network_error, FetchOutcome.blocked_by_robots, FetchOutcome.too_large]):
        m.record_snapshot(make_snapshot(url=URL, outcome=outcome, snapshot_id=f"f{i}", error=f"err {outcome.value}"))
    assert [s.error for s in m.snapshots_for("jp-test")] == [
        "err network_error", "err blocked_by_robots", "err too_large"]
    assert m.freshness("jp-test").as_of is None
    m.close()


def test_duplicate_does_not_leave_partial_write(tmp_path):
    m = SqliteManifest(tmp_path / "m.db")
    m.record_snapshot(make_snapshot(snapshot_id="s1"), {"a": 1})
    with pytest.raises(Exception):
        m.record_snapshot(make_snapshot(snapshot_id="s1", data=b"x"), {"a": 2})
    assert m.snapshot_meta("s1") == {"a": 1}
    assert len(m.snapshots_for("jp-test")) == 1
    m.close()


def test_two_connections_concurrent_writes(tmp_path):
    path = tmp_path / "m.db"
    SqliteManifest(path).close()
    errors = []

    def work(idx):  # sqlite3 连接不能跨线程用：每个线程自己开一个连接
        m = SqliteManifest(path)
        try:
            for i in range(30):
                m.record_snapshot(make_snapshot(url=URL, data=b"%d-%d" % (idx, i), snapshot_id=f"c{idx}-{i}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            m.close()

    threads = [threading.Thread(target=work, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    check = SqliteManifest(path)
    assert len(check.snapshots_for("jp-test")) == 60
    check.close()


def test_duplicate_across_connections(tmp_path):
    path = tmp_path / "m.db"
    a, b = SqliteManifest(path), SqliteManifest(path)
    a.record_snapshot(make_snapshot(snapshot_id="same"))
    with pytest.raises(Exception) as info:
        b.record_snapshot(make_snapshot(snapshot_id="same", data=b"y"))
    assert type(info.value).__name__ == "DuplicateError"
    a.close()
    b.close()


def test_schema_version_mismatch(tmp_path):
    path = tmp_path / "m.db"
    SqliteManifest(path).close()
    db = sqlite3.connect(path)
    db.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION + 1,))
    db.commit()
    db.close()
    with pytest.raises(SchemaVersionError, match=f"代码要求 {SCHEMA_VERSION}.*版本 {SCHEMA_VERSION + 1}"):
        SqliteManifest(path)


def test_foreign_database_without_version_table(tmp_path):
    path = tmp_path / "other.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE t (x)")
    db.commit()
    db.close()
    with pytest.raises(SchemaVersionError, match="没有版本记录"):
        SqliteManifest(path)


def test_no_parser_record(tmp_path):
    m = SqliteManifest(tmp_path / "m.db")
    rec = ParseRecord(sha256=sha(b"z"), parser=NO_PARSER, version="0", status=ParseStatus.no_parser)
    m.record_parse(rec)
    assert m.get_parse(sha(b"z"), NO_PARSER, "0") == rec
    m.close()


def test_timestamps_with_offsets_order_by_instant(tmp_path):
    from datetime import timedelta, timezone

    jst = timezone(timedelta(hours=9))
    m = SqliteManifest(tmp_path / "m.db")
    early = make_snapshot(url=URL, snapshot_id="e", data=b"e", fetched_at=T0.astimezone(jst))
    late = make_snapshot(url=URL, snapshot_id="l", data=b"l", fetched_at=T0 + timedelta(hours=1))
    m.record_snapshot(late)
    m.record_snapshot(early)
    assert m.latest_snapshot(URL).snapshot_id == "l"
    assert m.latest_snapshot(URL).fetched_at == late.fetched_at
    assert m.snapshots_for("jp-test")[0].fetched_at.utcoffset() == jst.utcoffset(None)
    m.close()
