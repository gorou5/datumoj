"""m0: synthesize v1 audit failures, inspect read-only, replay real local stores."""

import json
import os
import sqlite3
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, wiring
from datumoj.rikolto.core.rawstore import FileRawStore
from datumoj.rikolto.diagnose import evidence, replay
from datumoj.rikolto.parsers.pdf import PdfParser
from datumoj.rikolto.store.observations import DuckDBObservationStore
from datumoj.rikolto.testing.fakes import FakeEmbedder, FakeParser, FakeRegistry, FakeVectorIndex
from datumoj.rikolto.testing.samples import T0, make_observation, make_snapshot, make_spec
from tests.test_diagnose import derived, fingerprint, legacy, record

PDF = Path(__file__).parents[1] / "fixtures/gov_web/html/synthetic/site/policy/startup/guide.pdf"


class SyntheticParser(FakeParser):
    """Synthetic text/table input; PDF still goes through the production parser."""

    def parse(self, snapshot, data, meta=None):
        meta = meta or {}
        if snapshot.content_type == "application/pdf":
            return PdfParser().parse(snapshot, data, meta)
        result = super().parse(snapshot, data, meta)
        if meta.get("table_key"):
            result.observations.append(make_observation(
                "same-id", "7", source_id=snapshot.source_id, dataset_key=f"{snapshot.source_id}:dataset",
                table_key=meta["table_key"], snapshot_sha256=snapshot.sha256, fetched_at=snapshot.fetched_at,
            ))
        return result


@pytest.fixture
def local_wiring(monkeypatch):
    real_build = wiring.build
    calls = []
    vectors = FakeVectorIndex(FakeEmbedder())

    def build(path, overrides=None):
        options = {"registry": FakeRegistry([make_spec()]), "parsers": {("fake", "*"): SyntheticParser},
                   "vectors": vectors, **(overrides or {})}
        calls.append(options)
        return real_build(path, options)

    def forbidden(*args, **kwargs):
        pytest.fail("m0 must not access the network")

    monkeypatch.delenv("DATUMOJ_VECTORS", raising=False)
    monkeypatch.setattr(wiring, "build", build)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    return calls


def seed_old(root):
    legacy(root)
    raw = FileRawStore(root)
    snapshots = []
    observations = DuckDBObservationStore(root)

    def add(sid, name, data, *, write=True, meta=None, media="text/plain"):
        raw.put(data)
        snap = make_snapshot(snapshot_id=sid, url=f"https://example.go.jp/{name}", data=data,
                             fetched_at=T0 + timedelta(seconds=len(snapshots)), content_type=media)
        snapshots.append(snap)
        record(root, snap, meta)
        result = SyntheticParser().parse(snap, data, meta)
        if sid == "pdf":
            result = result.model_copy(update={"chunks": [
                c.model_copy(update={"published_at": None, "effective_at": None}) for c in result.chunks
            ]})
        if write:
            derived(root, result)
        for obs in result.observations:
            observations.write_table(obs.source_id, obs.table_key, [obs])
        return snap, result

    # D-01/D-03: the legacy SHA cache omitted the second resource entirely.
    add("shared-one", "one.txt", b"shared notice")
    add("shared-two", "two.txt", b"shared notice", write=False)
    _a, _ = add("cycle-a1", "cycle.txt", b"alpha")
    add("cycle-b", "cycle.txt", b"beta")
    last_a, _ = add("cycle-a2", "cycle.txt", b"alpha", write=False)
    # D-02: retrying the older parse after the newer success reversed current.
    old, old_result = add("retry-old", "retry.txt", b"old version")
    new, _ = add("retry-new", "retry.txt", b"new version")
    derived(root, old_result)
    with sqlite3.connect(root / "manifest.sqlite") as db:
        db.execute("INSERT INTO parsed VALUES (?,?,?,?,?)", (old.sha256, "fake-text", "0", "failed", "old failure"))
    # D-04: obs_id is intentionally identical in two distinct tables.
    add("table-one", "table-one.txt", b"first table", meta={"table_key": "t1"})
    add("table-two", "table-two.txt", b"second table", meta={"table_key": "t2"})
    # D-06: source date exists on the document but was lost from its chunks.
    _pdf, pdf_result = add("pdf", "guide.pdf", PDF.read_bytes(), meta={"published_at": "2025-04-01"},
                          media="application/pdf")
    assert pdf_result.chunks
    # D-09: the old parser published a document despite subsequently corrupt raw.
    bad, _ = add("bad", "bad.txt", b"authentic body")
    raw.path_for(bad.sha256).chmod(0o600)
    raw.path_for(bad.sha256).write_bytes(b"tampered body")
    return snapshots, last_a, new, bad, pdf_result.documents[0]


def test_legacy_diagnose_and_replay(tmp_path, local_wiring):
    source, target = tmp_path / "old", tmp_path / "new"
    _snapshots, last_a, new, bad, pdf = seed_old(source)
    before_files = fingerprint(source)
    old = evidence(source, verify_raw=True)
    assert fingerprint(source) == before_files
    for name in ("sha_multi_resource", "recovered_resources", "duplicate_obs_ids", "current_mismatches",
                 "pdf_missing_dates", "raw_failures"):
        assert old[name]["count"] > 0, name
    assert old["current_mismatches"]["count"] == 2
    result = replay(source, target)
    assert local_wiring[0]["vectors"] is None
    assert fingerprint(source) == before_files
    assert result["status"] == "partial"  # Original corrupt bytes cannot be reconstructed offline.
    assert set(result["failed_snapshots"]) == {bad.snapshot_id}
    assert "CorruptedBlob" in result["failed_snapshots"][bad.snapshot_id]
    assert result["after"]["observation_tables"] == 2
    assert result["current_differences"]["count"] == 4  # Added second resource, fixed two currents, removed bad.
    fixed = result["diagnosis"]
    assert fixed["current_mismatches"]["count"] == 0
    assert fixed["unresolved_document_resources"]["count"] == 0
    assert fixed["pdf_missing_dates"]["count"] == 0
    assert fixed["recovered_resources"]["count"] == 0
    # These are legitimate retained identities, not evidence defects to delete.
    assert fixed["sha_multi_resource"]["count"] == 1
    assert fixed["duplicate_obs_ids"]["count"] == 1
    assert fixed["quarantined_raw"]["count"] == 1
    assert fixed["raw_failures"]["samples"] == [{"sha256": bad.sha256, "reason": "missing"}]
    with sqlite3.connect(source / "manifest.sqlite") as db, sqlite3.connect(target / "manifest.sqlite") as other:
        assert db.execute("SELECT * FROM snapshots ORDER BY seq").fetchall() == other.execute(
            "SELECT * FROM snapshots ORDER BY seq").fetchall()
    from datumoj.rikolto.store.chunks import SqliteChunkStore
    store = SqliteChunkStore(target / "chunks.sqlite")
    try:
        assert store.get_document("jp-test:cycle.txt").snapshot_sha256 == last_a.sha256
        assert store.current_order("jp-test:cycle.txt").seq == 5
        assert store.get_document("jp-test:retry.txt").snapshot_sha256 == new.sha256
        assert store.get_document("jp-test:bad.txt") is None
        assert store.chunks_for(pdf.doc_key)[0].published_at == pdf.published_at
    finally:
        store.close()
    deps = wiring.build(target, {"vectors": None})
    try:
        for name in ("one.txt", "two.txt"):
            chunk = deps.chunks.chunks_for(f"jp-test:{name}")[0]
            citation = deps.tools.get_chunk(chunk.chunk_id)["data"]["chunks"][0]["citation"]
            assert citation["resource_url"].endswith(name)
            assert citation["snapshot_id"] == ("shared-one" if name == "one.txt" else "shared-two")
        query = deps.tools.query_sql("SELECT source_id, table_key, obs_id, value FROM observations ORDER BY table_key")
        assert query["evidence_state"] == "complete"
        assert len(query["data"]["rows"]) == 2
        assert deps.keyword.search("alpha").items
        assert not deps.keyword.search("beta").items
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
            getattr(deps, name).close()
    output = Path(os.environ["DATUMOJ_EVIDENCE_DIR"]) if os.environ.get("DATUMOJ_EVIDENCE_DIR") else tmp_path / "evidence"
    output.mkdir(parents=True, exist_ok=True)
    (output / "maple-m0.json").write_text(json.dumps({"before": old, "replay": result}, ensure_ascii=False, indent=2))


def test_cli_clean_replay_and_vectors_opt_in(tmp_path, local_wiring):
    source = tmp_path / "old"
    legacy(source)
    data = b"synthetic searchable text"
    FileRawStore(source).put(data)
    record(source, make_snapshot(snapshot_id="only", data=data))
    result = CliRunner().invoke(cli.app, ["replay", "--from", str(source), "--to", str(tmp_path / "new")])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "complete"
    assert local_wiring[0]["vectors"] is None
    assert not (tmp_path / "new/vectors").exists()
    with_vectors = replay(source, tmp_path / "vectors-new", vectors=True)
    assert with_vectors["status"] == "complete"
    assert local_wiring[-1]["vectors"] is not None


def test_unknown_source_is_explicit_partial_result(tmp_path, local_wiring):
    source = tmp_path / "old"
    legacy(source)
    record(source, make_snapshot(source_id="jp-unregistered", snapshot_id="unknown"))
    result = CliRunner().invoke(cli.app, ["replay", "--from", str(source), "--to", str(tmp_path / "new")])
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report["status"] == "partial"
    assert report["unknown_sources"] == ["jp-unregistered"]


def test_replay_preserves_sequence_gaps_and_same_time_tiebreak(tmp_path, local_wiring):
    source = tmp_path / "old"
    legacy(source)
    raw = FileRawStore(source)
    for seq, data in ((11, b"later sequence"), (7, b"earlier sequence")):
        raw.put(data)
        record(source, make_snapshot(snapshot_id=f"s{seq}", data=data), seq=seq)
    result = replay(source, tmp_path / "new")
    assert result["status"] == "complete"
    with sqlite3.connect(tmp_path / "new/chunks.sqlite") as db:
        order = json.loads(db.execute("SELECT order_json FROM published_orders").fetchone()[0])
        assert order["seq"] == 11
    with sqlite3.connect(tmp_path / "new/manifest.sqlite") as db:
        assert db.execute("SELECT seq FROM snapshots ORDER BY seq").fetchall() == [(7,), (11,)]


def test_replay_with_production_registry_and_html_pdf_parsers(tmp_path, monkeypatch):
    """Exercise public CLI with real wiring, parser discovery, stores and citations."""
    source = tmp_path / "old"
    legacy(source)
    raw = FileRawStore(source)
    shared = ("<html><body><main><h1>架空の公示</h1><p>" + "架空市の支援事業の公示です。" * 30 + "</p></main></body></html>").encode()
    for sid, name, data, media, meta in (
        ("one", "one.html", shared, "text/html", {}),
        ("two", "two.html", shared, "text/html", {}),
        ("pdf", "guide.pdf", PDF.read_bytes(), "application/pdf", {"published_at": "2025-04-01"}),
    ):
        raw.put(data)
        record(source, make_snapshot(source_id="jp-gov-web", snapshot_id=sid, url=f"https://example.go.jp/{name}",
                                     data=data, content_type=media), meta)

    def forbidden(*args, **kwargs):
        pytest.fail("replay must be offline by default")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    result = CliRunner().invoke(cli.app, ["replay", "--from", str(source), "--to", str(tmp_path / "new")])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["after"]["documents"] == 3
    assert report["diagnosis"]["unresolved_document_resources"]["count"] == 0
    assert report["diagnosis"]["pdf_missing_dates"]["count"] == 0
    deps = wiring.build(tmp_path / "new", {"vectors": None})
    try:
        for chunk in deps.chunks.iter_chunks():
            citation = deps.tools.get_chunk(chunk.chunk_id)["data"]["chunks"][0]["citation"]
            assert citation["snapshot_id"] in {"one", "two", "pdf"}
            restored = deps.tools.cite(citation["snapshot_sha256"], citation["locator"],
                                       snapshot_id=citation["snapshot_id"], resource_url=citation["resource_url"])
            assert restored["data"]["status"] == "ok"
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
            getattr(deps, name).close()
