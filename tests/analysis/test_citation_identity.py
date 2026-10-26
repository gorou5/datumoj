"""Resource identity stays attached to shared bytes, including offline restoration."""

from datetime import timedelta

import pytest

from datumoj.rikolto.analysis.reports import weekly_report
from datumoj.rikolto.analysis.tools import AnalysisTools
from datumoj.rikolto.core.citations import document_snapshot
from datumoj.rikolto.core.contracts import ParseRecord, ParseStatus
from datumoj.rikolto.core.models import ChangeEvent, ChangeKind, FetchOutcome, parse_context_fingerprint
from datumoj.rikolto.index.hybrid import HybridRetriever
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore, FakeChangeLog, FakeChunkStore, FakeKeywordIndex, FakeManifest,
    FakeObservationStore, FakeParser, FakeRawStore, FakeRegistry,
)
from datumoj.rikolto.testing.samples import T0, make_chunk, make_document, make_observation, make_snapshot, make_spec


@pytest.fixture
def identity_env(tmp_path):
    manifest, chunks, keyword = FakeManifest(), FakeChunkStore(), FakeKeywordIndex()
    registry = FakeRegistry([make_spec()])
    raw = FakeRawStore(tmp_path)
    body = b"needle shared document"
    raw.put(body)
    snapshots = []
    for i, name in enumerate(("a", "b")):
        snap = make_snapshot(url=f"https://example.go.jp/{name}.txt", data=body, fetched_at=T0 + timedelta(hours=i))
        meta = {"doc_id": name, "table_key": name}
        manifest.record_snapshot(snap, meta)
        parsed = FakeParser().parse(snap, body, meta)
        chunks.put_document(parsed.documents[0], parsed.chunks)
        keyword.add(parsed.chunks)
        snapshots.append(snap)
    tools = AnalysisTools(
        retriever=HybridRetriever(chunks, manifest, registry, keyword), chunks=chunks, manifest=manifest,
        registry=registry, raw=raw, catalog=FakeCatalogStore(), observations=FakeObservationStore(),
        change_log=FakeChangeLog(), parsers={("fake", "text/plain"): FakeParser}, data_dir=tmp_path,
    )
    return tools, snapshots


def test_search_and_get_chunk_bind_each_resource(identity_env):
    tools, snaps = identity_env
    hits = tools.search_docs("needle")["data"]["hits"]
    assert len(hits) == 2
    expected = {f"jp-test:{name}": snap for name, snap in zip(("a", "b"), snaps)}
    for hit in hits:
        snap = expected[hit["doc_key"]]
        for citation in (hit["citation"], tools.get_chunk(hit["chunk_id"], context=0)["data"]["chunks"][0]["citation"]):
            assert citation["snapshot_id"] == snap.snapshot_id
            assert citation["resource_url"] == snap.resource_url
            assert citation["fetched_at"] == snap.fetched_at.isoformat()


def test_cite_exact_identity_and_shared_sha_ambiguity(identity_env, monkeypatch):
    tools, snaps = identity_env
    first, second = snaps
    with monkeypatch.context() as patch:
        patch.setattr(tools.raw, "verify", lambda _: pytest.fail("ambiguous citation must not read raw"))
        result = tools.cite(first.sha256, "section=1")
    assert result["data"]["status"] == "ambiguous"
    assert result["data"]["passages"] == []
    assert result["data"]["candidates"] == [
        {"snapshot_id": s.snapshot_id, "source_id": s.source_id, "resource_url": s.resource_url} for s in snaps
    ]
    for snap in snaps:
        for kwargs in ({"snapshot_id": snap.snapshot_id}, {"resource_url": snap.resource_url},
                       {"snapshot_id": snap.snapshot_id, "resource_url": snap.resource_url}):
            restored = tools.cite(snap.sha256, "section=1", **kwargs)
            assert restored["data"]["status"] == "ok"
            citation = restored["data"]["passages"][0]["citation"]
            assert citation["snapshot_id"] == snap.snapshot_id
            assert citation["resource_url"] == snap.resource_url
    for kwargs in ({"snapshot_id": "absent"}, {"resource_url": "https://example.go.jp/absent"},
                   {"snapshot_id": first.snapshot_id, "resource_url": second.resource_url}):
        assert tools.cite(first.sha256, "section=1", **kwargs)["data"]["status"] == "not_found"


def test_unique_resource_sha_and_exact_old_snapshot(identity_env):
    tools, snaps = identity_env
    snap = make_snapshot(data=b"unique needle", snapshot_id="unique", fetched_at=T0)
    newer = snap.model_copy(update={"snapshot_id": "unique-new", "fetched_at": T0 + timedelta(days=1)})
    tools.raw.put(b"unique needle")
    tools.manifest.record_snapshot(snap)
    tools.manifest.record_snapshot(newer)
    latest = tools.cite(snap.sha256, "section=1")["data"]["passages"][0]["citation"]
    old = tools.cite(snap.sha256, "section=1", snapshot_id=snap.snapshot_id)["data"]["passages"][0]["citation"]
    assert latest["snapshot_id"] == newer.snapshot_id
    assert old["snapshot_id"] == snap.snapshot_id


@pytest.mark.parametrize("kwargs", [{"snapshot_id": ""}, {"resource_url": 1}, {"snapshot_id": False}])
def test_cite_validates_new_identity_parameters(identity_env, kwargs):
    tools, snaps = identity_env
    with pytest.raises(ValueError):
        tools.cite(snaps[0].sha256, "section=1", **kwargs)


def test_unknown_document_resource_warns_instead_of_guessing(identity_env):
    tools, snaps = identity_env
    doc = make_document("jp-test:unknown", snaps[0].sha256)
    chunk = make_chunk(doc.doc_key, doc.snapshot_sha256, "section=1", "unknown needle")
    tools.chunks.put_document(doc, [chunk])
    tools.retriever.keyword.add([chunk])
    assert tools.get_chunk(chunk.chunk_id, context=0)["data"]["chunks"][0]["citation"] is None
    result = tools.retriever.search(["unknown"])
    assert result.data == []
    assert any("歧义" in warning for warning in result.warnings)


def test_metadata_and_parse_context_resolve_resource(identity_env):
    tools, snaps = identity_env
    first = snaps[0]
    doc = make_document("jp-test:opaque", first.sha256, metadata={"resource_url": first.resource_url})
    assert document_snapshot(tools.manifest, first.sha256, "jp-test", doc.doc_key, doc) == first
    doc = doc.model_copy(update={"metadata": {}})
    tools.manifest.record_parse(ParseRecord(
        sha256=first.sha256, parser="test", version="1", status=ParseStatus.ok, doc_keys=[doc.doc_key],
        context_fingerprint=parse_context_fingerprint(first, tools.manifest.snapshot_meta(first.snapshot_id)),
    ))
    assert document_snapshot(tools.manifest, first.sha256, "jp-test", doc.doc_key, doc) == first
    latest = first.model_copy(update={"snapshot_id": "same-time-last-write"})
    tools.manifest.record_snapshot(latest, tools.manifest.snapshot_meta(first.snapshot_id))
    assert document_snapshot(tools.manifest, first.sha256, "jp-test", doc.doc_key, doc) == latest
    failure = make_snapshot(url=first.resource_url, outcome=FetchOutcome.http_error, snapshot_id="failed")
    tools.manifest.record_snapshot(failure)
    assert document_snapshot(tools.manifest, first.sha256, "jp-test", doc.doc_key, doc) == latest


@pytest.mark.parametrize("has_chunks", [True, False])
def test_query_sql_uses_source_and_table_snapshot(identity_env, has_chunks):
    tools, snaps = identity_env
    for name, snap in zip(("a", "b"), snaps):
        obs = make_observation(
            name, "1", source_id="jp-test", dataset_key=f"jp-test:{name}", table_key=name,
            snapshot_sha256=snap.sha256, fetched_at=snap.fetched_at,
        )
        tools.observations.write_table("jp-test", name, [obs])
    if not has_chunks:
        tools.chunks = None
    rows = tools.query_sql("SELECT source_id, table_key, obs_id FROM observations")["data"]["provenance"]
    assert {row["citation"]["snapshot_id"] for row in rows} == {snap.snapshot_id for snap in snaps}
    for row in rows:
        assert row["citation"]["resource_url"].endswith(f'{row["table_key"]}.txt')


@pytest.mark.parametrize("event_count", [1, 8])
def test_list_changes_builds_fallback_index_at_most_once(identity_env, monkeypatch, event_count):
    tools, snaps = identity_env
    calls = []
    original = tools.chunks.iter_chunks

    def counted(source_ids=None):
        calls.append(source_ids)
        return original(source_ids)

    monkeypatch.setattr(tools.chunks, "iter_chunks", counted)
    tools.change_log.append([
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.added, doc_key=f"jp-test:missing-{i}",
            resource_url=snaps[i % 2].resource_url, new_sha256=snaps[0].sha256,
            detected_at=T0, summary="added",
        ) for i in range(event_count)
    ])
    rows = tools.list_changes(since=T0)["data"]
    assert len(calls) <= 1
    assert len(rows) == event_count
    for row in rows:
        i = int(row["doc_key"].rsplit("-", 1)[-1])
        assert row["citation"]["snapshot_id"] == snaps[i % 2].snapshot_id


def test_report_uses_event_resource_and_reports_ambiguity(identity_env):
    tools, snaps = identity_env
    tools.change_log.append([
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.added, doc_key="jp-test:a", resource_url=snaps[0].resource_url,
            new_sha256=snaps[0].sha256, detected_at=T0, summary="first",
        ),
        ChangeEvent.make(
            source_id="jp-test", kind=ChangeKind.added, doc_key="jp-test:unknown",
            new_sha256=snaps[0].sha256, detected_at=T0, summary="unknown",
        ),
    ])
    report = weekly_report(tools, since=T0, until=T0 + timedelta(days=1))
    assert snaps[0].resource_url.replace(".", r"\.") in report
    assert snaps[1].resource_url.replace(".", r"\.") not in report
    assert "歧义" in report
    assert "snapshot_id：" + snaps[0].snapshot_id.replace("-", r"\-").replace(".", r"\.").replace("+", r"\+") in report
