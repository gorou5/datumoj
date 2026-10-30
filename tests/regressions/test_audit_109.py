"""Regression for audit D-09: valid-looking tampered bytes must not parse as a snapshot."""

from datumoj.rikolto.core.contracts import ParseStatus
from datumoj.rikolto.core.models import parse_context_fingerprint
from datumoj.rikolto.core.rawstore import FileRawStore
from datumoj.rikolto.pipeline import PipelineDeps, parse_pending
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRegistry,
)
from datumoj.rikolto.testing.samples import make_snapshot, make_spec


def test_tampered_raw_does_not_publish_parse_result(tmp_path):
    raw = FileRawStore(tmp_path)
    fetched_bytes = "公示の題名\n\n取得時の本文".encode()
    sha256 = raw.put(fetched_bytes)
    snapshot = make_snapshot(data=fetched_bytes, snapshot_id="audit-109-synthetic")
    # This replacement is valid UTF-8 that the parser accepts, but it is not the
    # content addressed by the snapshot's SHA-256.
    raw.path_for(sha256).chmod(0o600)
    raw.path_for(sha256).write_bytes("改ざんした題名\n\n改ざんした本文".encode())

    manifest = FakeManifest()
    manifest.record_snapshot(snapshot)
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

    assert summary.parsed == []
    assert list(summary.failed) == [snapshot.snapshot_id]
    record = manifest.get_parse(
        sha256, FakeParser.name, FakeParser.version,
        parse_context_fingerprint(snapshot, manifest.snapshot_meta(snapshot.snapshot_id)),
    )
    assert record.status is ParseStatus.failed
    assert record.error and "CorruptedBlob" in record.error
    assert chunks.get_document("jp-test:a.txt") is None
    assert list(chunks.iter_chunks()) == []
