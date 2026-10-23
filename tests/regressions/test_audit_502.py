"""D-07: date filtering must reach qualifying chunks below the unfiltered top-k."""

from datetime import date, timedelta

from datumoj.core.models import GapState
from datumoj.index.fts import SqliteKeywordIndex
from datumoj.index.hybrid import HybridRetriever
from datumoj.testing.fakes import FakeChunkStore, FakeManifest, FakeRegistry
from datumoj.testing.samples import T0, make_chunk, make_document, make_snapshot, make_spec


def test_date_filter_finds_a_document_beyond_unfiltered_top_k():
    chunks, manifest = FakeChunkStore(), FakeManifest()
    index = SqliteKeywordIndex(":memory:")
    target_id = None
    for number in range(4):
        doc_key = f"jp-test:audit-{number}"
        body = f"audit fixture {number}".encode()
        snapshot = make_snapshot(
            "jp-test", f"https://example.go.jp/audit-{number}.txt", body,
            fetched_at=T0 + timedelta(minutes=number),
        )
        manifest.record_snapshot(snapshot)
        doc = make_document(doc_key, snapshot.sha256)
        chunk = make_chunk(
            doc_key, snapshot.sha256, "section=1", "地方自治体の監査手続を定める。",
            title_path=[] if number == 0 else ["地方自治体の監査に関する概要"],
            published_at=date(2026, 5, 1) if number == 0 else date(2025, 5, 1),
        )
        chunks.put_document(doc, [chunk])
        index.add([chunk])
        if number == 0:
            target_id = chunk.chunk_id

    retriever = HybridRetriever(chunks, manifest, FakeRegistry([make_spec("jp-test")]), index)
    unfiltered = retriever.search(["地方自治体の監査"], k=1)
    assert unfiltered.data and unfiltered.data[0].chunk_id != target_id

    filtered = retriever.search(
        ["地方自治体の監査"], k=1, date_range=(date(2026, 1, 1), None),
    )

    assert [hit.chunk_id for hit in filtered.data] == [target_id]
    assert filtered.gap_state is GapState.ok
    assert not any("zero_hits" in warning for warning in filtered.warnings)
    index.close()
