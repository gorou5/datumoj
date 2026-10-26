"""D-02: publishing after a v1 ChunkStore migration preserves snapshot order."""

import sqlite3
from datetime import timedelta

from datumoj.rikolto.core.contracts import ParseRecord, ParseStatus
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import PublishState, parse_context_fingerprint
from datumoj.rikolto.pipeline import PipelineDeps, parse_pending
from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeKeywordIndex,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
)
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec

KEY = "jp-test:version.txt"
URL = "https://example.go.jp/version.txt"


class UpdatedParser(FakeParser):
    version = "2"


def _parse(parser, snapshot, data):
    result = parser().parse(snapshot, data)
    return result.documents[0], result.chunks


def _create_v1_store(path, versions, current_id):
    current = next(doc for snapshot_id, doc, _ in versions if snapshot_id == current_id)
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version VALUES (1);
            CREATE TABLE documents (
                doc_key TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, document_json TEXT NOT NULL,
                PRIMARY KEY (doc_key, snapshot_sha256)
            );
            CREATE TABLE current_versions (doc_key TEXT PRIMARY KEY, snapshot_sha256 TEXT NOT NULL);
            CREATE TABLE chunks (
                doc_key TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, chunk_id TEXT NOT NULL,
                ord INTEGER NOT NULL, source_id TEXT NOT NULL, chunk_json TEXT NOT NULL,
                PRIMARY KEY (doc_key, snapshot_sha256, chunk_id)
            );
            """
        )
        for _, doc, chunks in versions:
            db.execute(
                "INSERT INTO documents VALUES (?,?,?)",
                (doc.doc_key, doc.snapshot_sha256, doc.model_dump_json()),
            )
            db.executemany(
                "INSERT INTO chunks VALUES (?,?,?,?,?,?)",
                [
                    (doc.doc_key, doc.snapshot_sha256, chunk.chunk_id, i, chunk.source_id, chunk.model_dump_json())
                    for i, chunk in enumerate(chunks)
                ],
            )
        db.execute("INSERT INTO current_versions VALUES (?,?)", (current.doc_key, current.snapshot_sha256))


def _record_parse(manifest, snapshot, parser, doc_key):
    manifest.record_parse(ParseRecord(
        sha256=snapshot.sha256,
        parser=parser.name,
        version=parser.version,
        status=ParseStatus.ok,
        context_fingerprint=parse_context_fingerprint(snapshot, manifest.snapshot_meta(snapshot.snapshot_id)),
        doc_keys=[doc_key],
    ))


def _snapshot(snapshot_id, data, seconds):
    return make_snapshot(
        snapshot_id=snapshot_id,
        data=data,
        url=URL,
        fetched_at=T0 + timedelta(seconds=seconds),
    )


def _migrated_pipeline(tmp_path, manifest_snapshots, legacy_snapshots, data_by_id):
    manifest = SqliteManifest(tmp_path / "manifest.db")
    raw = FakeRawStore()
    for snapshot in manifest_snapshots:
        manifest.record_snapshot(snapshot)
    for data in data_by_id.values():
        raw.put(data)

    legacy_versions = [
        (snapshot.snapshot_id, *_parse(FakeParser, snapshot, data_by_id[snapshot.snapshot_id]))
        for snapshot in legacy_snapshots
    ]
    _create_v1_store(tmp_path / "chunks.db", legacy_versions, "B")
    chunks = SqliteChunkStore(tmp_path / "chunks.db")
    deps = PipelineDeps(
        manifest=manifest,
        raw=raw,
        registry=FakeRegistry([make_spec()]),
        chunks=chunks,
        keyword=FakeKeywordIndex(),
        observations=FakeObservationStore(),
        catalog=FakeCatalogStore(),
        parsers={("fake", "text/plain"): UpdatedParser},
    )
    return deps


def _close(deps):
    deps.manifest.close()
    deps.chunks.close()


def test_v1_current_watermark_is_restored_before_reparsing_old_snapshot(tmp_path):
    data = {"A": b"snapshot A", "B": b"snapshot B", "C": b"snapshot C"}
    a, b, c = (_snapshot(sid, data[sid], i) for i, sid in enumerate(("A", "B", "C")))
    deps = _migrated_pipeline(tmp_path, [a, b], [a, b], data)
    try:
        assert deps.chunks.get_document(KEY).snapshot_sha256 == b.sha256
        assert deps.chunks.current_order(KEY) is None
        _record_parse(deps.manifest, a, FakeParser, KEY)
        _record_parse(deps.manifest, b, UpdatedParser, KEY)
        deps.manifest.record_publish_state("B", PublishState.published)

        first = parse_pending(deps, "jp-test")
        assert first.parsed == ["A"]
        assert deps.chunks.get_document(KEY).snapshot_sha256 == b.sha256
        assert deps.chunks.current_order(KEY) == deps.manifest.snapshot_order("B")

        deps.raw.put(data["C"])
        deps.manifest.record_snapshot(c)
        second = parse_pending(deps, "jp-test")
        assert second.parsed == ["C"]
        assert deps.chunks.get_document(KEY).snapshot_sha256 == c.sha256
        assert deps.chunks.current_order(KEY) == deps.manifest.snapshot_order("C")
    finally:
        _close(deps)


def test_missing_current_manifest_record_skips_old_snapshot_but_allows_latest(tmp_path, monkeypatch, caplog):
    data = {"A": b"snapshot A", "B": b"snapshot B", "C": b"snapshot C"}
    a, b, c = (_snapshot(sid, data[sid], i) for i, sid in enumerate(("A", "B", "C")))
    # v1 current B exists in ChunkStore, while the manifest only knows A and newer C.
    deps = _migrated_pipeline(tmp_path, [a, c], [a, b], data)
    try:
        assert deps.manifest.get_snapshot("B") is None
        assert deps.chunks.get_document(KEY).snapshot_sha256 == b.sha256
        assert deps.chunks.current_order(KEY) is None
        _record_parse(deps.manifest, a, FakeParser, KEY)
        original_publish = deps.chunks.publish_current
        attempts = []

        def track_publish(doc_key, sha256, order):
            attempts.append((sha256, deps.chunks.get_document(doc_key).snapshot_sha256))
            return original_publish(doc_key, sha256, order)

        monkeypatch.setattr(deps.chunks, "publish_current", track_publish)
        summary = parse_pending(deps, "jp-test")

        assert not summary.failed
        assert attempts == [(c.sha256, b.sha256)]
        assert deps.chunks.get_document(KEY).snapshot_sha256 == c.sha256
        assert deps.chunks.get_document(KEY, a.sha256) is not None
        assert deps.chunks.current_order(KEY) == deps.manifest.snapshot_order("C")
        assert any(KEY in record.message and "跳过发布" in record.message for record in caplog.records)
    finally:
        _close(deps)
