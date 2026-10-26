"""D-02：旧快照先失败，新快照发布后旧重试成功，仅补历史。"""

from datetime import timedelta
from unittest.mock import Mock

from datumoj.rikolto.core.contracts import ParseError, ParseStatus
from datumoj.rikolto.core.models import ChangeKind, PublishState, parse_context_fingerprint
from datumoj.rikolto.pipeline import PipelineDeps, parse_pending
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore, FakeChangeLog, FakeChunkStore, FakeEmbedder, FakeKeywordIndex,
    FakeManifest, FakeObservationStore, FakeParser, FakeRawStore, FakeRegistry, FakeVectorIndex,
)
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec


def test_old_failed_snapshot_retry_does_not_reverse_current_or_indexes():
    class IntermittentParser(FakeParser):
        fail_old = True

        def parse(self, snapshot, data, meta=None):
            if snapshot.snapshot_id == "old" and self.fail_old:
                raise ParseError("synthetic old snapshot failure")
            return super().parse(snapshot, data, meta)

    deps = PipelineDeps(
        manifest=FakeManifest(), raw=FakeRawStore(), registry=FakeRegistry([make_spec()]),
        chunks=FakeChunkStore(), keyword=FakeKeywordIndex(), observations=FakeObservationStore(),
        catalog=FakeCatalogStore(), vectors=FakeVectorIndex(FakeEmbedder()), change_log=FakeChangeLog(),
        parsers={("fake", "text/plain"): IntermittentParser},
    )
    snaps = []
    for index, (sid, data) in enumerate([("old", b"oldcontent"), ("new", b"newcontent")]):
        deps.raw.put(data)
        snap = make_snapshot(snapshot_id=sid, data=data, url="https://example.go.jp/a.txt",
                             fetched_at=T0 + timedelta(seconds=index))
        deps.manifest.record_snapshot(snap)
        snaps.append(snap)
    old, new = snaps
    first = parse_pending(deps)
    assert "old" in first.failed and first.parsed == ["new"]
    assert deps.manifest.publish_state("old") is PublishState.failed
    assert deps.manifest.publish_state("new") is PublishState.published
    events = deps.change_log.list(kinds=[ChangeKind.added, ChangeKind.modified])
    deps.keyword.add = Mock(wraps=deps.keyword.add)
    deps.vectors.add = Mock(wraps=deps.vectors.add)

    IntermittentParser.fail_old = False
    second = parse_pending(deps)
    assert not second.failed and second.retried == ["old"]
    assert deps.chunks.get_document("jp-test:a.txt").snapshot_sha256 == new.sha256
    assert deps.chunks.current_order("jp-test:a.txt") == deps.manifest.snapshot_order("new")
    assert deps.chunks.get_document("jp-test:a.txt", old.sha256) is not None
    assert deps.manifest.publish_state("old") is PublishState.published
    rec = deps.manifest.get_parse(old.sha256, "fake-text", "1", parse_context_fingerprint(old, {}))
    assert rec.status is ParseStatus.ok
    assert deps.keyword.search("newcontent").items and not deps.keyword.search("oldcontent").items
    deps.keyword.add.assert_not_called()
    deps.vectors.add.assert_not_called()
    assert deps.change_log.list(kinds=[ChangeKind.added, ChangeKind.modified]) == events
