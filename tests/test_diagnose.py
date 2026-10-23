"""Read-only legacy inspection and replay guardrails; every write uses tmp_path."""

import hashlib
import json
import sqlite3
from datetime import UTC, timedelta

import pytest
from typer.testing import CliRunner

from datumoj import cli, diagnose, wiring
from datumoj.core.models import FetchOutcome
from datumoj.core.rawstore import FileRawStore
from datumoj.diagnose import evidence, replay
from datumoj.testing.samples import T0, make_chunk, make_document, make_snapshot


LEGACY_MANIFEST = """
CREATE TABLE schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version VALUES (1);
CREATE TABLE snapshots (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT NOT NULL UNIQUE,
 source_id TEXT NOT NULL, resource_url TEXT NOT NULL, fetched_at TEXT NOT NULL,
 outcome TEXT NOT NULL, sha256 TEXT, meta TEXT NOT NULL, snapshot TEXT NOT NULL);
CREATE TABLE parsed (sha256 TEXT NOT NULL, parser TEXT NOT NULL, version TEXT NOT NULL,
 status TEXT NOT NULL, error TEXT, PRIMARY KEY (sha256, parser, version));
"""
LEGACY_CHUNKS = """
CREATE TABLE schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version VALUES (1);
CREATE TABLE documents (doc_key TEXT, snapshot_sha256 TEXT, document_json TEXT,
 PRIMARY KEY (doc_key, snapshot_sha256));
CREATE TABLE current_versions (doc_key TEXT PRIMARY KEY, snapshot_sha256 TEXT);
CREATE TABLE chunks (doc_key TEXT, snapshot_sha256 TEXT, chunk_id TEXT, ord INTEGER,
 source_id TEXT, chunk_json TEXT, PRIMARY KEY (doc_key, snapshot_sha256, chunk_id));
"""


def fingerprint(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def legacy(root):
    root.mkdir()
    with sqlite3.connect(root / "manifest.sqlite") as db:
        db.executescript(LEGACY_MANIFEST)
    with sqlite3.connect(root / "chunks.sqlite") as db:
        db.executescript(LEGACY_CHUNKS)


def record(root, snapshot, meta=None, *, seq=None):
    with sqlite3.connect(root / "manifest.sqlite") as db:
        db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?)", (
            seq, snapshot.snapshot_id, snapshot.source_id, snapshot.resource_url,
            snapshot.fetched_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f"),
            snapshot.outcome.value, snapshot.sha256,
            json.dumps(meta or {}), snapshot.model_dump_json(),
        ))


def derived(root, result, *, publish=True):
    with sqlite3.connect(root / "chunks.sqlite") as db:
        for doc in result.documents:
            db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?)",
                       (doc.doc_key, doc.snapshot_sha256, doc.model_dump_json()))
            if publish:
                db.execute("INSERT OR REPLACE INTO current_versions VALUES (?,?)", (doc.doc_key, doc.snapshot_sha256))
        for i, chunk in enumerate(result.chunks):
            db.execute("INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?)", (
                chunk.doc_key, chunk.snapshot_sha256, chunk.chunk_id, i, chunk.source_id, chunk.model_dump_json(),
            ))


def test_cli_diagnose_legacy_never_builds_or_migrates(tmp_path, monkeypatch):
    root = tmp_path / "old"
    legacy(root)
    raw = FileRawStore(root)
    digest = raw.put(b"original")
    snap = make_snapshot(snapshot_id="one", data=b"original")
    record(root, snap)
    raw.path_for(digest).chmod(0o600)
    raw.path_for(digest).write_bytes(b"corrupted")
    before = fingerprint(root)

    def forbidden(*args, **kwargs):
        pytest.fail("diagnose must not build stores or read raw by default")

    monkeypatch.setattr(wiring, "build", forbidden)
    monkeypatch.setenv("DATUMOJ_DATA_DIR", str(root))
    runner = CliRunner()
    with monkeypatch.context() as patch:
        patch.setattr(diagnose, "_raw_failures", forbidden)
        first = runner.invoke(cli.app, ["diagnose", "evidence"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.output)["raw_failures"] == {"count": 0, "samples": [], "checked": False}
    second = runner.invoke(cli.app, ["diagnose", "evidence", "--data-dir", str(root), "--verify-raw"])
    assert second.exit_code == 0, second.output
    assert json.loads(second.output)["raw_failures"]["samples"][0]["reason"] == "hash_mismatch"
    assert fingerprint(root) == before
    with sqlite3.connect(root / "manifest.sqlite") as db:
        assert db.execute("SELECT version FROM schema_version").fetchone() == (1,)
    assert not (root / "raw/.quarantine").exists()


def test_readonly_sees_uncheckpointed_wal_without_touching_source(tmp_path):
    root = tmp_path / "old"
    legacy(root)
    db = sqlite3.connect(root / "manifest.sqlite")
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        record(root, make_snapshot(snapshot_id="in-wal"))
        before = fingerprint(root)
        assert evidence(root)["counts"]["snapshots"] == 1
        assert fingerprint(root) == before
    finally:
        db.close()


def test_categories_are_capped_and_order_uses_time_then_sequence(tmp_path):
    root = tmp_path / "old"
    legacy(root)
    for i in range(23):
        a = make_snapshot(snapshot_id=f"{i}-a", data=str(i).encode(), url=f"https://example.go.jp/{i}a")
        b = make_snapshot(snapshot_id=f"{i}-b", data=str(i).encode(), url=f"https://example.go.jp/{i}b")
        record(root, a)
        record(root, b)
    report = evidence(root)
    assert report["sha_multi_resource"]["count"] == 23
    assert len(report["sha_multi_resource"]["samples"]) == 20
    # Deliberately insert the newer observation first, with the smaller seq.
    new = make_snapshot(snapshot_id="new", data=b"new", fetched_at=T0 + timedelta(days=1))
    old = make_snapshot(snapshot_id="old", data=b"old")
    record(root, new)
    record(root, old)
    from datumoj.core.contracts import ParseResult
    for snap in (new, old):
        doc = make_document("jp-test:a.txt", snap.sha256)
        derived(root, ParseResult(documents=[doc], chunks=[make_chunk(doc.doc_key, snap.sha256, "row=1", "body")]))
    assert evidence(root)["current_mismatches"]["samples"][0]["expected"] == new.sha256


@pytest.mark.parametrize("relation", ["same", "child", "parent", "nonempty", "file"])
def test_replay_rejects_unsafe_target_without_changes(tmp_path, relation):
    source = tmp_path / "old"
    legacy(source)
    target = tmp_path / "new"
    if relation == "same":
        target = source
    elif relation == "child":
        target = source / "new"
    elif relation == "parent":
        target = tmp_path
    elif relation == "file":
        target.write_text("keep")
    else:
        target.mkdir()
        (target / "keep").write_text("keep")
    before = fingerprint(tmp_path)
    result = CliRunner().invoke(cli.app, ["replay", "--from", str(source), "--to", str(target)])
    assert result.exit_code == 2, result.output
    with pytest.raises(ValueError):
        replay(source, target)
    assert fingerprint(tmp_path) == before


def test_missing_directory_diagnosis_does_not_create_it(tmp_path):
    path = tmp_path / "missing"
    result = CliRunner().invoke(cli.app, ["diagnose", "evidence", "--data-dir", str(path)])
    assert result.exit_code == 1
    assert not path.exists()


def test_http_failure_followed_by_success_is_historical_inventory(tmp_path):
    root = tmp_path / "old"
    legacy(root)
    record(root, make_snapshot(snapshot_id="fail", outcome=FetchOutcome.http_error))
    record(root, make_snapshot(snapshot_id="success", fetched_at=T0 + timedelta(seconds=1)))
    assert evidence(root)["recovered_resources"]["count"] == 1


def test_parse_failure_does_not_leak_across_same_sha_resources(tmp_path):
    from datumoj.core.models import parse_context_fingerprint

    root = tmp_path / "old"
    legacy(root)
    a = make_snapshot(snapshot_id="a", data=b"shared", url="https://example.go.jp/a")
    b = make_snapshot(snapshot_id="b", data=b"shared", url="https://example.go.jp/b")
    later_b = make_snapshot(snapshot_id="b2", data=b"updated", url=b.resource_url, fetched_at=T0 + timedelta(days=1))
    for snap in (a, b, later_b):
        record(root, snap)
    with sqlite3.connect(root / "manifest.sqlite") as db:
        db.execute("ALTER TABLE parsed ADD COLUMN context_fingerprint TEXT NOT NULL DEFAULT ''")
        db.execute("INSERT INTO parsed VALUES (?,?,?,?,?,?)", (
            a.sha256, "fake-text", "1", "failed", "failure only at a", parse_context_fingerprint(a),
        ))
    assert evidence(root)["recovered_resources"]["count"] == 0


def test_replay_rejects_raw_symlinks_before_creating_target(tmp_path):
    source = tmp_path / "old"
    legacy(source)
    (source / "raw").mkdir()
    (source / "raw/link").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlinks"):
        replay(source, tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_raw_verification_includes_orphans_and_reports_unreadable_files(tmp_path, monkeypatch):
    from pathlib import Path

    root = tmp_path / "old"
    legacy(root)
    raw = FileRawStore(root)
    bad = raw.put(b"orphan content")
    denied = raw.put(b"unreadable content")
    raw.path_for(bad).chmod(0o600)
    raw.path_for(bad).write_bytes(b"corrupt orphan")
    original_open = Path.open

    def open_file(path, *args, **kwargs):
        if path == raw.path_for(denied):
            raise PermissionError("synthetic unreadable blob")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_file)
    report = evidence(root, verify_raw=True)
    assert report["raw_failures"]["count"] == 2
    by_hash = {r["sha256"]: r for r in report["raw_failures"]["samples"]}
    assert by_hash[bad]["reason"] == "hash_mismatch"
    assert by_hash[denied]["reason"] == "read_error"
