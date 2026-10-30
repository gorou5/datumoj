import sqlite3
import threading

import pytest

from datumoj.rikolto.core.contracts import NO_PARSER, ParseRecord, ParseStatus, SchemaVersionError
from datumoj.rikolto.core.manifest import SCHEMA_VERSION, SqliteManifest
from datumoj.rikolto.core.models import FetchOutcome, PublishState
from datumoj.rikolto.testing.contract_suites import ManifestSuite
from datumoj.rikolto.testing.samples import T0, make_snapshot, sha

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


def test_parse_outputs_and_writer_persist_across_connections(tmp_path):
    path = tmp_path / "outputs.db"
    first = SqliteManifest(path)
    second = SqliteManifest(path)
    rec = ParseRecord(sha256=sha(b"bytes"), parser="p", version="1", status=ParseStatus.ok,
                      context_fingerprint=sha(b"context"), doc_keys=["jp-test:a", "jp-test:empty"])
    first.record_parse(rec)
    assert second.get_parse(rec.sha256, "p", "1", rec.context_fingerprint) == rec
    assert second.last_writer("jp-test:empty", rec.sha256) == rec.context_fingerprint
    first.close()
    second.close()
    reopened = SqliteManifest(path)
    assert reopened.get_parse(rec.sha256, "p", "1", rec.context_fingerprint) == rec
    assert reopened.last_writer("jp-test:a", rec.sha256) == rec.context_fingerprint
    reopened.close()


def _v2_manifest(path):
    snap = make_snapshot(snapshot_id="v2-snapshot")
    from datumoj.rikolto.core.models import parse_context_fingerprint
    fp = parse_context_fingerprint(snap, {"role": "body"})
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version VALUES (2);
            CREATE TABLE snapshots (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT NOT NULL UNIQUE,
                source_id TEXT NOT NULL, resource_url TEXT NOT NULL, fetched_at TEXT NOT NULL,
                outcome TEXT NOT NULL, sha256 TEXT, meta TEXT NOT NULL, snapshot TEXT NOT NULL
            );
            CREATE TABLE parsed (
                sha256 TEXT NOT NULL, parser TEXT NOT NULL, version TEXT NOT NULL,
                context_fingerprint TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
                PRIMARY KEY (sha256, parser, version, context_fingerprint)
            );
        """)
        from datetime import UTC
        db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?)",
                   (7, snap.snapshot_id, snap.source_id, snap.resource_url,
                    snap.fetched_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f"),
                    snap.outcome.value, snap.sha256, '{"role":"body"}', snap.model_dump_json()))
        db.execute("INSERT INTO parsed VALUES (?,?,?,?,?,?)", (snap.sha256, "p", "1", fp, "ok", None))
        db.execute("INSERT INTO parsed VALUES (?,?,?,?,?,?)", (sha(b"bad"), "p", "1", fp, "failed", "bad"))
    return snap, fp


def test_v2_migration_preserves_snapshots_and_marks_outputs_unknown(tmp_path):
    path = tmp_path / "v2.db"
    snap, fp = _v2_manifest(path)
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT * FROM snapshots").fetchall()
    manifest = SqliteManifest(path)
    assert manifest.snapshots_for(snap.source_id) == [snap]
    assert manifest.snapshot_meta(snap.snapshot_id) == {"role": "body"}
    assert manifest.get_parse(snap.sha256, "p", "1", fp).doc_keys is None
    assert manifest.get_parse(sha(b"bad"), "p", "1", fp).error == "bad"
    assert manifest.last_writer("jp-test:a.txt", snap.sha256) is None
    assert manifest.get_snapshot(snap.snapshot_id) == snap
    assert manifest.publish_state(snap.snapshot_id) is None
    assert manifest.snapshots_in_publish_state(PublishState.staged) == []
    manifest.record_snapshot(make_snapshot(snapshot_id="next"))
    assert manifest.snapshot_order("next").seq == 8
    manifest.close()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (snap.snapshot_id,)).fetchall() == before
        assert db.execute("SELECT version FROM schema_version").fetchone() == (SCHEMA_VERSION,)


def test_v2_migration_failure_rolls_back(tmp_path):
    path = tmp_path / "v2-rollback.db"
    _v2_manifest(path)
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT * FROM snapshots").fetchall()
        db.execute("CREATE TRIGGER fail_upgrade BEFORE UPDATE ON schema_version "
                   "BEGIN SELECT RAISE(ABORT, 'failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="failed"):
        SqliteManifest(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM snapshots").fetchall() == before
        assert db.execute("SELECT version FROM schema_version").fetchone() == (2,)
        assert "doc_keys" not in {r[1] for r in db.execute("PRAGMA table_info(parsed)")}
        assert db.execute("SELECT name FROM sqlite_master WHERE name='parse_writers'").fetchone() is None
        assert db.execute("SELECT name FROM sqlite_master WHERE name='publish_states'").fetchone() is None


def test_publish_state_persists_and_clears_error(tmp_path):
    path = tmp_path / "publish.db"
    first, second = SqliteManifest(path), SqliteManifest(path)
    snap = make_snapshot(snapshot_id="publish")
    first.record_snapshot(snap)
    first.record_publish_state(snap.snapshot_id, PublishState.failed, "observation failed")
    assert second.get_snapshot(snap.snapshot_id) == snap
    assert second.publish_state(snap.snapshot_id) is PublishState.failed
    first.record_publish_state(snap.snapshot_id, PublishState.staged)
    assert second.snapshots_in_publish_state(PublishState.staged) == [snap.snapshot_id]
    first.close()
    second.close()
    reopened = SqliteManifest(path)
    assert reopened.publish_state(snap.snapshot_id) is PublishState.staged
    reopened.record_publish_state(snap.snapshot_id, PublishState.published)
    reopened.close()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT state, error FROM publish_states").fetchone() == ("published", None)


def test_v3_migration_keeps_outputs_and_uses_indexed_recovery_queries(tmp_path):
    path = tmp_path / "v3.db"
    original = SqliteManifest(path)
    snap = make_snapshot(snapshot_id="v3")
    original.record_snapshot(snap, {"role": "body"})
    rec = ParseRecord(sha256=snap.sha256, parser="p", version="1", status=ParseStatus.ok,
                      context_fingerprint=sha(b"context"), doc_keys=["jp-test:a"])
    original.record_parse(rec)
    original.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE publish_states")
        db.execute("UPDATE schema_version SET version=3")
        before = db.execute("SELECT * FROM snapshots").fetchall()
    migrated = SqliteManifest(path)
    assert migrated.get_snapshot("v3") == snap
    assert migrated.get_parse(snap.sha256, "p", "1", rec.context_fingerprint) == rec
    assert migrated.last_writer("jp-test:a", snap.sha256) == rec.context_fingerprint
    assert migrated.publish_state("v3") is None
    assert migrated.snapshots_in_publish_state(PublishState.staged) == []
    migrated.close()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM snapshots").fetchall() == before
        assert db.execute("SELECT version FROM schema_version").fetchone() == (4,)
        for sql, args in [
            ("SELECT snapshot FROM snapshots WHERE snapshot_id=?", ("v3",)),
            ("SELECT snapshot_id FROM publish_states WHERE state=?", ("staged",)),
        ]:
            plan = db.execute("EXPLAIN QUERY PLAN " + sql, args).fetchall()
            assert any("SEARCH" in row[3] and "INDEX" in row[3] for row in plan)


def test_v3_migration_failure_rolls_back(tmp_path):
    path = tmp_path / "v3-rollback.db"
    original = SqliteManifest(path)
    original.record_snapshot(make_snapshot(snapshot_id="v3"))
    original.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE publish_states")
        db.execute("UPDATE schema_version SET version=3")
        db.execute("CREATE TRIGGER fail_upgrade BEFORE UPDATE ON schema_version "
                   "BEGIN SELECT RAISE(ABORT, 'failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="failed"):
        SqliteManifest(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone() == (3,)
        assert db.execute("SELECT name FROM sqlite_master WHERE name='publish_states'").fetchone() is None
        assert db.execute("SELECT snapshot_id FROM snapshots").fetchall() == [("v3",)]
