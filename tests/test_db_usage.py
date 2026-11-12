"""Small standalone SQLite/Lance snapshots, with no services or live HTTP."""

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import lancedb
import pyarrow as pa
import pytest

from datumoj.rikolto import db_usage
from datumoj.rikolto.core.models import FetchOutcome
from datumoj.rikolto.testing.samples import make_chunk, make_document, make_snapshot

NOW = datetime(2026, 11, 12, tzinfo=UTC)
SECRET = "SENSITIVE文書本文"
URL = "https://secret.invalid/private-document"


def payload(values):
    return sum(0 if value is None else len(value if isinstance(value, bytes) else str(value).encode())
               for value in values)


def create_databases(root):
    root.mkdir(exist_ok=True)
    with closing(sqlite3.connect(root / "manifest.sqlite")) as db:
        db.executescript("""
            CREATE TABLE snapshots (seq INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT UNIQUE,
                source_id TEXT,resource_url TEXT,fetched_at TEXT,outcome TEXT,sha256 TEXT,meta TEXT,snapshot TEXT);
            CREATE INDEX snapshots_url ON snapshots(resource_url,fetched_at,seq);
            CREATE INDEX snapshots_sha ON snapshots(sha256);
            CREATE TABLE parsed (sha256 TEXT,context_fingerprint TEXT,status TEXT);
            CREATE TABLE publish_states (snapshot_id TEXT PRIMARY KEY,state TEXT,error TEXT);
            CREATE TABLE raw_allowed_missing (sha256 TEXT PRIMARY KEY,marked_at TEXT);
        """)
    with closing(sqlite3.connect(root / "chunks.sqlite")) as db:
        db.executescript("""
            CREATE TABLE documents (doc_key TEXT,snapshot_sha256 TEXT,document_json TEXT,
                PRIMARY KEY(doc_key,snapshot_sha256));
            CREATE TABLE current_versions (doc_key TEXT PRIMARY KEY,snapshot_sha256 TEXT);
            CREATE TABLE chunks (doc_key TEXT,snapshot_sha256 TEXT,chunk_id TEXT,ord INTEGER,
                source_id TEXT,chunk_json TEXT,PRIMARY KEY(doc_key,snapshot_sha256,chunk_id));
            CREATE INDEX chunks_chunk_id ON chunks(chunk_id);
        """)
    return SimpleNamespace(root=root, sizes={}, chunks={}, snapshots=[])


def snapshot(world, source, sha, when, *, outcome=FetchOutcome.ok, url=URL):
    snap = make_snapshot(source, url, fetched_at=when or NOW, outcome=outcome,
                         snapshot_id=f"snapshot-{len(world.snapshots)}", **({"sha256": sha} if sha else {}))
    values = (snap.snapshot_id, source, url, when.isoformat() if when else "invalid", outcome.value,
              snap.sha256, json.dumps({"secret": SECRET}), snap.model_dump_json())
    with closing(sqlite3.connect(world.root / "manifest.sqlite")) as db, db:
        seq = db.execute("INSERT INTO snapshots VALUES(NULL,?,?,?,?,?,?,?,?)", values).lastrowid
    world.snapshots.append((seq, *values))


def version(world, name, label, when, *, source="jp-test", current=False, count=2, record=True):
    key = f"{source}:{name}"
    sha = hashlib.sha256(f"{key}-{label}".encode()).hexdigest()
    doc = make_document(key, sha, title=SECRET)
    doc_values = (key, sha, doc.model_dump_json())
    size = payload(doc_values)
    rows = []
    with closing(sqlite3.connect(world.root / "chunks.sqlite")) as db, db:
        db.execute("INSERT INTO documents VALUES(?,?,?)", doc_values)
        if current:
            db.execute("INSERT INTO current_versions VALUES(?,?)", (key, sha))
        for index in range(count):
            chunk = make_chunk(key, sha, f"page={index + 1}", SECRET + label)
            values = (key, sha, chunk.chunk_id, index, source, chunk.model_dump_json())
            db.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?)", values)
            size += payload(values)
            rows.append(chunk)
    if record:
        snapshot(world, source, sha, when, url=f"{URL}/{name}")
    world.sizes[name, label] = {"documents": 1, "chunks": count, "bytes": size}
    world.chunks[name, label] = rows
    return sha


@pytest.fixture
def db_world(tmp_path):
    world = create_databases(tmp_path / "seed")
    for name, days in (("recent", 1), ("two", 2), ("seven", 7), ("thirty", 30)):
        version(world, name, "old", NOW - timedelta(days=days + 10))
        version(world, name, "new", NOW - timedelta(days=days), current=True)
    version(world, "missing", "old", None, record=False)
    version(world, "missing", "new", NOW, current=True)
    version(world, "chain", "old", NOW - timedelta(days=60))
    version(world, "chain", "middle", NOW - timedelta(days=40))
    version(world, "chain", "new", NOW - timedelta(days=1), current=True)
    for name, days in (("fresh", 1), ("week", 7), ("month", 30)):
        version(world, name, "only", NOW - timedelta(days=days), source="jp-jma", current=True)
    version(world, "unknown", "only", None, source="jp-jma", current=True, record=False)
    # A document without chunks must still be counted and attributed.
    version(world, "empty", "only", NOW, current=True, count=0)
    snapshot(world, "jp-test", None, NOW, outcome=FetchOutcome.not_modified)
    snapshot(world, "jp-test", None, NOW - timedelta(days=10), outcome=FetchOutcome.network_error)
    for name in ("changes.sqlite", "catalog.sqlite"):
        with closing(sqlite3.connect(world.root / name)) as db:
            db.executescript("CREATE TABLE schema_version(version INTEGER); INSERT INTO schema_version VALUES(1);")
    return world


def measure(world):
    return db_usage.measure_usage(**{name: world.root / (name + ".sqlite")
                                    for name in ("manifest", "chunks", "catalog", "changes")}, now=NOW)


def summed(*rows):
    return {field: sum(row[field] for row in rows) for field in ("documents", "chunks", "bytes")}


def test_current_old_age_boundaries_and_estimates(db_world):
    world = db_world
    report = measure(world)
    stats = report["chunks"]["sources"]["jp-test"]
    assert stats["current"] == summed(*(world.sizes[name, label] for name, label in world.sizes
                                        if label == "new" or name == "empty"))
    assert stats["old"] == summed(*(world.sizes[name, label] for name, label in world.sizes
                                    if label in {"old", "middle"}))
    ages = stats["old_superseded_age"]
    assert ages["lt_48h"] == summed(world.sizes["recent", "old"], world.sizes["chain", "middle"])
    assert ages["2_7d"] == world.sizes["two", "old"]
    assert ages["7_30d"] == world.sizes["seven", "old"]
    assert ages["ge_30d"] == summed(world.sizes["thirty", "old"], world.sizes["chain", "old"])
    assert ages["unknown"] == world.sizes["missing", "old"]
    assert report["estimates"]["A"] == summed(ages["2_7d"], ages["7_30d"], ages["ge_30d"])
    jma = report["chunks"]["sources"]["jp-jma"]
    for age, name in (("lt_7d", "fresh"), ("7_30d", "week"), ("ge_30d", "month"), ("unknown", "unknown")):
        assert jma["current_fetched_age"][age] == world.sizes[name, "only"]
    assert jma["old"] == {"documents": 0, "chunks": 0, "bytes": 0}
    assert report["estimates"]["B"] == summed(world.sizes["week", "only"], world.sizes["month", "only"])
    assert report["estimates"]["bytes_method"] == "column_lengths"


def test_manifest_dimensions_and_latest_success(db_world):
    # Repeat a hash recently: neither bulletin age nor supersession may reset.
    sha = db_world.chunks["month", "only"][0].snapshot_sha256
    snapshot(db_world, "jp-jma", sha, NOW, url=f"{URL}/month")
    report = measure(db_world)
    expected = {}
    for row in db_world.snapshots:
        source, stamp, outcome = row[2], row[4], row[5]
        days = (NOW - datetime.fromisoformat(stamp)).days
        age = "lt_7d" if days < 7 else "7_30d" if days < 30 else "ge_30d"
        key = (source, outcome if outcome in {"ok", "not_modified"} else "failed", age)
        count, size = expected.get(key, (0, 0))
        expected[key] = count + 1, size + payload(row)
    for (source, outcome, age), (count, size) in expected.items():
        assert report["manifest"]["sources"][source]["outcomes"][outcome][age] == {"rows": count, "bytes": size}
    for source, stats in report["manifest"]["sources"].items():
        assert stats["latest_successful_resources"] == len({
            row[3] for row in db_world.snapshots if row[2] == source and row[5] == "ok"
        })
    assert report["chunks"]["sources"]["jp-jma"]["current_fetched_age"]["ge_30d"]["documents"] == 1


def test_dbstat_and_fallback_count_utf8_and_owned_indexes(db_world, monkeypatch):
    path = db_world.root / "chunks.sqlite"
    result = db_usage.sqlite_usage(path)
    with closing(sqlite3.connect(path)) as db:
        assert result["file_bytes"] == result["page_count"] * result["page_size"] == path.stat().st_size
        assert result["freelist_count"] == db.execute("PRAGMA freelist_count").fetchone()[0]
        try:
            pages = db.execute("SELECT sum(pgsize) FROM dbstat WHERE name IN "
                               "('chunks','chunks_chunk_id','sqlite_autoindex_chunks_1')").fetchone()[0]
        except sqlite3.OperationalError:
            pages = None
        if pages is not None:
            assert result["bytes_method"] == "dbstat"
            assert result["tables"]["chunks"]["bytes"] == pages
        expected = sum(payload(row) for row in db.execute("SELECT * FROM chunks"))
    def unavailable(db):
        raise sqlite3.OperationalError("no such table: dbstat")
    monkeypatch.setattr(db_usage, "_dbstat", unavailable)
    fallback = db_usage.sqlite_usage(path)
    assert fallback["bytes_method"] == "column_lengths"
    assert fallback["tables"]["chunks"]["bytes"] == expected
    assert fallback["tables"]["chunks"]["rows"] == sum(len(rows) for rows in db_world.chunks.values())


def make_vectors(world):
    path = world.root / "vectors"
    path.mkdir()
    (path / "_meta.json").write_text(json.dumps({"generation": 2}))
    db = lancedb.connect(str(path))
    def row(chunk):
        fields = [chunk.text, None, None]
        digest = hashlib.sha256(json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        return {"doc_key": chunk.doc_key, "chunk_id": chunk.chunk_id, "text_hash": digest, "vector": [1.0, 0.0]}
    current = row(world.chunks["recent", "new"][0])
    # Same ID, old text: must not be classified as the current chunk.
    stale = row(world.chunks["two", "old"][0])
    orphan = {**current, "chunk_id": "orphan"}
    schema = pa.schema([("doc_key", pa.string()), ("chunk_id", pa.string()), ("text_hash", pa.string()),
                        ("vector", pa.list_(pa.float32(), 2))])
    db.create_table("chunks_g2", [current, stale, orphan], schema=schema)
    db.create_table("chunks_g1", [current], schema=schema)
    return path


def test_vectors_cover_all_retained_tables_and_current_content(db_world):
    path = make_vectors(db_world)
    before = {p.relative_to(db_world.root): p.read_bytes() for p in db_world.root.rglob("*") if p.is_file()}
    report = db_usage.measure_usage(manifest=db_world.root / "manifest.sqlite",
                                    chunks=db_world.root / "chunks.sqlite", vectors=path, now=NOW)
    assert report["vectors"] == {"rows": 4, "current_chunk_rows": 2, "old_or_missing_chunk_rows": 2,
                                  "directory_bytes": sum(p.stat().st_size for p in path.rglob("*") if p.is_file()),
                                  "active_table_rows": 3, "retained_table_rows": 1}
    after = {p.relative_to(db_world.root): p.read_bytes() for p in db_world.root.rglob("*") if p.is_file()}
    assert before == after


def test_sqlite_connections_are_mode_ro_and_query_only(db_world, monkeypatch):
    original = sqlite3.connect
    connections = []
    def connect(database, **kwargs):
        assert database.endswith("?mode=ro&immutable=1") and kwargs["uri"] is True
        db = original(database, **kwargs)
        connections.append(db)
        return db
    monkeypatch.setattr(db_usage.sqlite3, "connect", connect)
    before = {p.name: p.read_bytes() for p in db_world.root.iterdir()}
    measure(db_world)
    assert connections
    assert before == {p.name: p.read_bytes() for p in db_world.root.iterdir()}
    with closing(db_usage._ro(db_world.root / "chunks.sqlite")) as db:
        assert db.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM chunks")


@pytest.mark.parametrize("value", [None, "invalid", "2027-01-01T00:00:00Z"])
def test_unknown_and_future_dates(value):
    assert db_usage._bucket(value, NOW, old=True) == "unknown"


def test_current_pointer_wins_over_fetch_time_and_orphans_are_counted(db_world):
    world = db_world
    old_sha = world.chunks["recent", "old"][0].snapshot_sha256
    with closing(sqlite3.connect(world.root / "chunks.sqlite")) as db, db:
        db.execute("UPDATE current_versions SET snapshot_sha256=? WHERE doc_key='jp-test:recent'", (old_sha,))
        db.execute("DELETE FROM current_versions WHERE doc_key='jp-test:two'")
        db.execute("DELETE FROM documents WHERE doc_key='jp-test:seven'")
        current_count = db.execute(
            "SELECT count(*) FROM chunks c JOIN current_versions v "
            "ON c.doc_key=v.doc_key AND c.snapshot_sha256=v.snapshot_sha256 WHERE c.source_id='jp-test'"
        ).fetchone()[0]
        total_chunks = db.execute("SELECT count(*) FROM chunks WHERE source_id='jp-test'").fetchone()[0]
    stats = measure(world)["chunks"]["sources"]["jp-test"]
    assert stats["current"]["chunks"] == current_count
    assert stats["current"]["chunks"] + stats["old"]["chunks"] == total_chunks
    assert stats["old_superseded_age"]["unknown"]["chunks"] >= 6


def test_invalid_fetched_dates_are_separate_and_do_not_enter_plans(db_world):
    with closing(sqlite3.connect(db_world.root / "manifest.sqlite")) as db, db:
        db.execute("UPDATE snapshots SET fetched_at='not-a-date' WHERE source_id='jp-jma'")
    result = measure(db_world)
    assert result["manifest"]["sources"]["jp-jma"]["outcomes"]["ok"]["unknown"]["rows"] == 3
    assert result["chunks"]["sources"]["jp-jma"]["current_fetched_age"]["unknown"]["documents"] == 4
    assert result["estimates"]["B"] == {"documents": 0, "chunks": 0, "bytes": 0}


def test_missing_optional_stores_and_missing_required_file(db_world, tmp_path):
    result = db_usage.measure_usage(manifest=db_world.root / "manifest.sqlite", now=NOW)
    assert set(result["sqlite"]) == {"manifest.sqlite"}
    assert result["vectors"]["rows"] == result["estimates"]["A"]["bytes"] == 0
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(FileNotFoundError):
        db_usage.sqlite_usage(missing)
    assert not missing.exists()


def test_returning_to_previous_content_restarts_supersession_clock(tmp_path):
    world = create_databases(tmp_path / "seed")
    sha = version(world, "reverted", "a", NOW - timedelta(days=60))
    version(world, "reverted", "b", NOW - timedelta(days=40))
    snapshot(world, "jp-test", sha, NOW - timedelta(days=2), url=f"{URL}/reverted")
    version(world, "reverted", "c", NOW - timedelta(days=1), current=True)
    result = db_usage.measure_usage(manifest=world.root / "manifest.sqlite",
                                    chunks=world.root / "chunks.sqlite", now=NOW)
    ages = result["chunks"]["sources"]["jp-test"]["old_superseded_age"]
    assert ages["lt_48h"] == world.sizes["reverted", "a"]
    assert ages["2_7d"] == world.sizes["reverted", "b"]
    assert result["estimates"]["A"] == world.sizes["reverted", "b"]


def test_shared_hash_across_resources_has_unknown_age(db_world):
    sha = db_world.chunks["month", "only"][0].snapshot_sha256
    snapshot(db_world, "jp-jma", sha, NOW, url=f"{URL}/different-resource")
    stats = measure(db_world)["chunks"]["sources"]["jp-jma"]
    assert stats["current_fetched_age"]["ge_30d"]["documents"] == 0
    assert stats["current_fetched_age"]["unknown"]["documents"] == 2


def test_reused_successor_hash_preserves_earlier_supersession(tmp_path):
    world = create_databases(tmp_path / "seed")
    version(world, "reused", "a", NOW - timedelta(days=60))
    sha = version(world, "reused", "b", NOW - timedelta(days=40), current=True)
    version(world, "reused", "c", NOW - timedelta(days=10))
    snapshot(world, "jp-test", sha, NOW - timedelta(days=1), url=f"{URL}/reused")
    result = db_usage.measure_usage(manifest=world.root / "manifest.sqlite",
                                    chunks=world.root / "chunks.sqlite", now=NOW)
    ages = result["chunks"]["sources"]["jp-test"]["old_superseded_age"]
    assert ages["ge_30d"] == world.sizes["reused", "a"]
    assert ages["lt_48h"] == world.sizes["reused", "c"]
