from __future__ import annotations

import hashlib

import pytest

from datumoj.core.models import parse_context_fingerprint
from datumoj.core.contracts import CorruptedBlob, ParseStatus
from datumoj.core.rawstore import FileRawStore
from datumoj.pipeline import PipelineDeps, parse_pending
from datumoj.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRegistry,
)
from datumoj.testing.samples import make_snapshot, make_spec


@pytest.mark.parametrize("method", ["get", "open"])
def test_corrupted_blob_reads_raise_and_quarantine(tmp_path, method):
    store = FileRawStore(tmp_path)
    data = b"original raw bytes"
    digest = store.put(data)
    tampered = b"tampered raw bytes"
    assert len(tampered) == len(data)
    store.path_for(digest).chmod(0o600)
    store.path_for(digest).write_bytes(tampered)

    with pytest.raises(CorruptedBlob):
        if method == "get":
            store.get(digest)
        else:
            with store.open(digest) as source:
                source.read()

    assert not store.path_for(digest).exists()
    quarantined = list((tmp_path / "raw" / ".quarantine").glob(f"{digest}-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == tampered


def test_put_after_corruption_quarantines_and_restores_blob(tmp_path):
    store = FileRawStore(tmp_path)
    data = b"recover me"
    digest = store.put(data)
    store.path_for(digest).chmod(0o600)
    store.path_for(digest).write_bytes(b"bad content")

    assert store.put(data) == digest
    assert store.get(digest) == data
    assert hashlib.sha256(store.path_for(digest).read_bytes()).hexdigest() == digest
    quarantined = list((tmp_path / "raw" / ".quarantine").glob(f"{digest}-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"bad content"


def test_pipeline_records_corrupt_raw_as_failed_without_writing_chunks(tmp_path):
    raw = FileRawStore(tmp_path)
    original = "標題\n\n正しい本文".encode()
    digest = raw.put(original)
    snap = make_snapshot(data=original, snapshot_id="corrupt-raw")
    raw.path_for(digest).chmod(0o600)
    raw.path_for(digest).write_bytes("改ざん\n\n別の本文".encode())
    manifest = FakeManifest()
    manifest.record_snapshot(snap)
    chunks = FakeChunkStore()
    deps = PipelineDeps(
        manifest=manifest,
        raw=raw,
        registry=FakeRegistry([make_spec()]),
        chunks=chunks,
        keyword=FakeKeywordIndex(),
        observations=FakeObservationStore(),
        catalog=FakeCatalogStore(),
        parsers={("fake", "text/plain"): FakeParser},
    )

    summary = parse_pending(deps)

    assert list(summary.failed) == [snap.snapshot_id]
    record = manifest.get_parse(
        digest, FakeParser.name, FakeParser.version,
        parse_context_fingerprint(snap, manifest.snapshot_meta(snap.snapshot_id)),
    )
    assert record.status is ParseStatus.failed
    assert "CorruptedBlob" in record.error
    assert chunks.get_document("jp-test:a.txt") is None
    assert list(chunks.iter_chunks()) == []
