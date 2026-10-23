"""MAPLE-002 身份、状态和存储契约；审核业务回归由各负责任务接入。"""

import json
import sqlite3
from datetime import UTC, timedelta

import pytest
from pydantic import ValidationError

from datumoj.core.contracts import (
    AmbiguousCitation,
    ContractError,
    ParseRecord,
    ParseStatus,
    citation_for_chunk,
    citation_for_observation,
)
from datumoj.core.manifest import SqliteManifest
from datumoj.core.models import (
    Citation,
    EvidenceState,
    FetchOutcome,
    Freshness,
    GapState,
    ObservationRef,
    PipelineStatus,
    PublishState,
    SnapshotOrder,
    ToolResult,
    parse_context_fingerprint,
)
from datumoj.store.chunks import SqliteChunkStore
from datumoj.testing.contract_suites import ChunkStoreSuite, EvidenceIdentitySuite, ManifestSuite, _doc_v
from datumoj.testing.fakes import FakeChunkStore, FakeManifest
from datumoj.testing.samples import T0, make_chunk, make_observation, make_snapshot, sha


class TestEvidenceIdentity(EvidenceIdentitySuite):
    pass


class TestManifestIdentity(ManifestSuite):
    @pytest.fixture(params=["fake", "sqlite"])
    def manifest(self, request, tmp_path):
        store = FakeManifest() if request.param == "fake" else SqliteManifest(tmp_path / "manifest.db")
        yield store
        if request.param == "sqlite":
            store.close()


class TestChunkPublication(ChunkStoreSuite):
    @pytest.fixture(params=["fake", "sqlite"])
    def store(self, request, tmp_path):
        self.path = tmp_path / "chunks.db"
        self.opened = []
        store = FakeChunkStore() if request.param == "fake" else SqliteChunkStore(self.path)
        if request.param == "sqlite":
            self.opened.append(store)
        yield store
        for connection in self.opened:
            connection.close()

    def reopen_store(self, store):
        if isinstance(store, FakeChunkStore):
            return store
        store.close()
        reopened = SqliteChunkStore(self.path)
        self.opened.append(reopened)
        return reopened


def test_snapshot_order_comparison_and_validation():
    a = SnapshotOrder(fetched_at=T0, seq=1)
    same_instant = T0.astimezone(UTC)
    assert a == SnapshotOrder(fetched_at=same_instant, seq=1)
    assert a <= SnapshotOrder(fetched_at=T0, seq=2)
    assert SnapshotOrder(fetched_at=T0, seq=999) < SnapshotOrder(fetched_at=T0 + timedelta(seconds=1), seq=1)
    assert a >= SnapshotOrder(fetched_at=same_instant, seq=1)
    for kw in [{"seq": 0}, {"seq": True}, {"fetched_at": T0.replace(tzinfo=None)}]:
        with pytest.raises(ValidationError):
            SnapshotOrder(**({"fetched_at": T0, "seq": 1} | kw))
    with pytest.raises(TypeError):
        _ = a < 1


@pytest.mark.parametrize("text", ["", "{}", '["jp-test","t"]', '["jp-test","t","id","extra"]',
                                 '["jp-test",1,"id"]', '["jp-test","", "id"]'])
def test_observation_ref_rejects_malformed_strings(text):
    with pytest.raises(ValueError):
        ObservationRef.parse(text)


def test_citations_bind_to_snapshot_and_legacy_json_remains_valid():
    snap = make_snapshot(snapshot_id="observation-one")
    chunk = make_chunk("jp-test:doc", snap.sha256, "art=1", "本文")
    obs = make_observation("id", "1", source_id="jp-test", dataset_key="jp-test:d", snapshot_sha256=snap.sha256)
    for legacy_citation in (citation_for_chunk(chunk, snap), citation_for_observation(obs, snap)):
        # 既有助手保留兼容输出，404 负责把真实关联的快照身份接入调用链。
        assert legacy_citation.snapshot_id == snap.snapshot_id
        citation = Citation(**(legacy_citation.model_dump() | {"snapshot_id": snap.snapshot_id}))
        assert citation.snapshot_id == snap.snapshot_id
        assert Citation.model_validate_json(citation.model_dump_json()) == citation
        legacy = citation.model_dump()
        del legacy["snapshot_id"]
        assert Citation.model_validate(legacy).snapshot_id is None
    assert issubclass(AmbiguousCitation, ContractError)
    with pytest.raises(AmbiguousCitation):
        raise AmbiguousCitation("SHA 对应多个资源，需 snapshot_id")


def test_evidence_and_pipeline_status_are_independent_of_hits():
    assert {s.value for s in PublishState} == {"staged", "published", "failed"}
    assert {s.value for s in EvidenceState} == {"complete", "partial", "unavailable"}
    pipeline = PipelineStatus(parseable_snapshots=4, parsed=2, parse_failed=1, needs_ocr=1,
                              searchable_chunks=0, index_generation="generation-7")
    inputs = {"tables": [{"source_id": "jp-test", "table_key": "t:1"}],
              "snapshots": ["s1"], "filters": {"start": "2020-01-01", "area": None}}
    for state in EvidenceState:
        # 聚合有值与证据完整性是两个维度；管线未建索引也可以有聚合结果。
        result = ToolResult(data={"count": 3}, gap_state=GapState.not_indexed, freshness=Freshness(),
                            evidence_state=state, evidence_inputs=inputs, pipeline=pipeline)
        assert ToolResult.model_validate_json(result.model_dump_json()) == result
        assert json.loads(result.model_dump_json())["evidence_inputs"] == inputs
    legacy = ToolResult(data=[], gap_state=GapState.zero_hits, freshness=Freshness())
    assert legacy.pipeline is None and legacy.evidence_state is None and legacy.evidence_inputs is None
    for field in ("parseable_snapshots", "parsed", "parse_failed", "needs_ocr", "searchable_chunks"):
        with pytest.raises(ValidationError):
            PipelineStatus(**(pipeline.model_dump() | {field: -1}))
    with pytest.raises(ValidationError):
        ToolResult(data=[], gap_state=GapState.ok, freshness=Freshness(), evidence_inputs={"x": object()})


def _manifest_v1(path):
    """现场构建旧 schema，含重复 blob、失败快照、seq 间隙和旧解析状态。"""
    snaps = [make_snapshot(snapshot_id="one"), make_snapshot(snapshot_id="repeat"),
             make_snapshot(snapshot_id="failed", outcome=FetchOutcome.network_error)]
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version VALUES (1);
            CREATE TABLE snapshots (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT NOT NULL UNIQUE,
                source_id TEXT NOT NULL, resource_url TEXT NOT NULL, fetched_at TEXT NOT NULL,
                outcome TEXT NOT NULL, sha256 TEXT, meta TEXT NOT NULL, snapshot TEXT NOT NULL
            );
            CREATE TABLE parsed (
                sha256 TEXT NOT NULL, parser TEXT NOT NULL, version TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT, PRIMARY KEY (sha256, parser, version)
            );
        """)
        for seq, snap in zip((2, 7, 9), snaps, strict=True):
            db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?)",
                       (seq, snap.snapshot_id, snap.source_id, snap.resource_url,
                        snap.fetched_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f"),
                        snap.outcome.value, snap.sha256, '{"a":1}', snap.model_dump_json()))
        db.execute("INSERT INTO parsed VALUES (?,?,?,?,?)", (snaps[0].sha256, "p", "1", "ok", None))
        db.execute("INSERT INTO parsed VALUES (?,?,?,?,?)", (sha(b"bad"), "p", "1", "failed", "bad format"))
    return snaps


def test_manifest_v1_migration_preserves_all_snapshots_and_invalidates_old_context(tmp_path):
    path = tmp_path / "v1.db"
    snaps = _manifest_v1(path)
    m = SqliteManifest(path)
    assert m.snapshots_for("jp-test") == snaps
    assert [m.snapshot_order(s.snapshot_id).seq for s in snaps] == [2, 7, 9]
    assert m.snapshot_meta("one") == {"a": 1}
    fp = parse_context_fingerprint(snaps[0], {"a": 1})
    assert m.get_parse(snaps[0].sha256, "p", "1").context_fingerprint == ""
    assert m.get_parse(snaps[0].sha256, "p", "1", fp) is None
    assert m.get_parse(sha(b"bad"), "p", "1").error == "bad format"
    record = ParseRecord(sha256=snaps[0].sha256, parser="p", version="1", context_fingerprint=fp,
                         status=ParseStatus.ok)
    m.record_parse(record)
    m.record_snapshot(make_snapshot(snapshot_id="after-migration"))
    assert m.snapshot_order("after-migration").seq == 10
    m.close()
    reopened = SqliteManifest(path)
    assert reopened.get_parse(snaps[0].sha256, "p", "1", fp) == record
    assert reopened.snapshot_order("repeat") == SnapshotOrder(fetched_at=T0, seq=7)
    assert reopened.snapshot_order("after-migration").seq == 10
    reopened.close()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone() == (3,)
        assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone() == (4,)
        assert db.execute("SELECT COUNT(*) FROM parsed").fetchone() == (3,)


def test_manifest_migration_failure_rolls_back(tmp_path):
    path = tmp_path / "rollback.db"
    snaps = _manifest_v1(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER abort_upgrade BEFORE UPDATE ON schema_version "
                   "BEGIN SELECT RAISE(ABORT, 'migration failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="migration failed"):
        SqliteManifest(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone() == (len(snaps),)
        assert db.execute("SELECT COUNT(*) FROM parsed").fetchone() == (2,)
        assert "context_fingerprint" not in {r[1] for r in db.execute("PRAGMA table_info(parsed)")}


def test_invalid_context_fingerprint_is_rejected():
    with pytest.raises(ValidationError):
        ParseRecord(sha256=sha(b"x"), parser="p", version="1", context_fingerprint="not-a-digest", status=ParseStatus.ok)


def _chunk_v1(path):
    old_doc, old_chunks = _doc_v(sha(b"old"), ["旧版"])
    current_doc, current_chunks = _doc_v(sha(b"current"), ["当前版"])
    with sqlite3.connect(path) as db:
        db.executescript("""
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
        """)
        for doc, chunks in ((old_doc, old_chunks), (current_doc, current_chunks)):
            db.execute("INSERT INTO documents VALUES (?,?,?)", (doc.doc_key, doc.snapshot_sha256, doc.model_dump_json()))
            for n, chunk in enumerate(chunks):
                db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)", (doc.doc_key, doc.snapshot_sha256, chunk.chunk_id,
                                                                      n, chunk.source_id, chunk.model_dump_json()))
        db.execute("INSERT INTO current_versions VALUES (?,?)", (current_doc.doc_key, current_doc.snapshot_sha256))
    return old_doc, old_chunks, current_doc, current_chunks


def test_v1_migration_keeps_history_and_first_publication_can_replace_current(tmp_path):
    path = tmp_path / "v1.db"
    old_doc, old_chunks, current_doc, current_chunks = _chunk_v1(path)
    store = SqliteChunkStore(path)
    assert store.get_document(current_doc.doc_key) == current_doc
    assert store.chunks_for(current_doc.doc_key) == current_chunks
    assert store.get_document(old_doc.doc_key, old_doc.snapshot_sha256) == old_doc
    assert store.chunks_for(old_doc.doc_key, old_doc.snapshot_sha256) == old_chunks
    assert store.current_order(old_doc.doc_key) is None
    order = SnapshotOrder(fetched_at=T0, seq=1)
    assert store.publish_current(old_doc.doc_key, old_doc.snapshot_sha256, order)
    store.close()
    reopened = SqliteChunkStore(path)
    assert reopened.get_document(old_doc.doc_key) == old_doc
    assert reopened.current_order(old_doc.doc_key) == order
    assert not reopened.publish_current(current_doc.doc_key, current_doc.snapshot_sha256, order)
    reopened.close()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone() == (2,)
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone() == (2,)
        assert db.execute("SELECT COUNT(*) FROM chunks").fetchone() == (2,)


def test_v1_migration_failure_is_atomic(tmp_path):
    path = tmp_path / "v1.db"
    _chunk_v1(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER abort_upgrade BEFORE UPDATE ON schema_version "
                   "BEGIN SELECT RAISE(ABORT, 'migration failed'); END")
    with pytest.raises(sqlite3.IntegrityError, match="migration failed"):
        SqliteChunkStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_version").fetchone() == (1,)
        assert db.execute("SELECT COUNT(*) FROM current_versions").fetchone() == (1,)
        assert db.execute("SELECT name FROM sqlite_master WHERE name='published_orders'").fetchone() is None


