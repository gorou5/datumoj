"""D-03: shared bytes at two URLs must not redirect a chunk citation."""

from datetime import timedelta

from datumoj.core.models import parse_context_fingerprint
from datumoj.core.contracts import ParseRecord, ParseStatus
from datumoj.index.hybrid import HybridRetriever
from datumoj.testing.fakes import FakeChunkStore, FakeKeywordIndex, FakeManifest, FakeRegistry
from datumoj.testing.samples import T0, make_chunk, make_document, make_snapshot, make_spec


def test_shared_sha_citation_points_to_chunks_own_resource():
    manifest, chunks, index = FakeManifest(), FakeChunkStore(), FakeKeywordIndex()
    snapshots = [
        make_snapshot(url=f"https://example.go.jp/{name}.pdf", data=b"same bytes",
                      fetched_at=T0 + timedelta(hours=i))
        for i, name in enumerate(("original", "other"))
    ]
    for snap in snapshots:
        manifest.record_snapshot(snap)
    for i, snap in enumerate(snapshots):
        doc = make_document(f"jp-test:opaque-{i}", snap.sha256)
        chunk = make_chunk(doc.doc_key, snap.sha256, "page=1", "needle")
        chunks.put_document(doc, [chunk])
        index.add([chunk])
        manifest.record_parse(ParseRecord(
            sha256=snap.sha256, parser="synthetic", version="1", status=ParseStatus.ok,
            context_fingerprint=parse_context_fingerprint(snap), doc_keys=[doc.doc_key],
        ))
    retriever = HybridRetriever(chunks, manifest, FakeRegistry([make_spec()]), index)
    hits = retriever.search(["needle"]).data
    assert len(hits) == 2
    for hit in hits:
        snap = snapshots[int(hit.doc_key.rsplit("-", 1)[-1])]
        assert hit.citation.resource_url == snap.resource_url
        assert hit.citation.snapshot_id == snap.snapshot_id
