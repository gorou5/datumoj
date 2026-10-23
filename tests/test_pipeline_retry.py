from datumoj.core.models import parse_context_fingerprint
from datumoj.core.contracts import ParseError, ParseStatus
from datumoj.pipeline import PipelineDeps, parse_pending
from datumoj.testing.fakes import (
    FakeCatalogStore, FakeChunkStore, FakeKeywordIndex, FakeManifest, FakeObservationStore,
    FakeParser, FakeRawStore, FakeRegistry,
)
from datumoj.testing.samples import make_snapshot, make_spec


class BrokenParser(FakeParser):
    calls = 0

    def parse(self, snapshot, data, meta=None):
        type(self).calls += 1
        raise ParseError("暂时无法解析")


class FixedParser(FakeParser):
    """与 BrokenParser 同名同版本，模拟修正了实现的解析器。"""

    calls = 0

    def parse(self, snapshot, data, meta=None):
        type(self).calls += 1
        return super().parse(snapshot, data, meta)


class CountingParser(FakeParser):
    calls = 0

    def parse(self, snapshot, data, meta=None):
        type(self).calls += 1
        return super().parse(snapshot, data, meta)


def build(parser):
    deps = PipelineDeps(
        manifest=FakeManifest(), raw=FakeRawStore(), registry=FakeRegistry([make_spec()]),
        chunks=FakeChunkStore(), keyword=FakeKeywordIndex(), observations=FakeObservationStore(),
        catalog=FakeCatalogStore(), parsers={(parser.source_kind, parser.content_type): parser},
    )
    data = "標題\n\n本文".encode()
    deps.raw.put(data)
    snap = make_snapshot(url="https://example.go.jp/retry.txt", data=data, snapshot_id="retry-1")
    deps.manifest.record_snapshot(snap)
    return deps, snap


def test_failed_snapshot_retries_with_fixed_parser_same_version():
    BrokenParser.calls = FixedParser.calls = 0
    deps, snap = build(BrokenParser)

    first = parse_pending(deps)
    assert first.failed == {snap.snapshot_id: "ParseError: 暂时无法解析"}
    assert first.newly_parsed == [] and first.retried == []
    failed = deps.manifest.get_parse(
        snap.sha256, "fake-text", "1",
        parse_context_fingerprint(snap, deps.manifest.snapshot_meta(snap.snapshot_id)),
    )
    assert failed.status is ParseStatus.failed

    deps.parsers = {(FixedParser.source_kind, FixedParser.content_type): FixedParser}
    second = parse_pending(deps)
    assert second.parsed == [snap.snapshot_id]
    assert second.newly_parsed == [] and second.retried == [snap.snapshot_id]
    assert not second.failed
    succeeded = deps.manifest.get_parse(
        snap.sha256, "fake-text", "1",
        parse_context_fingerprint(snap, deps.manifest.snapshot_meta(snap.snapshot_id)),
    )
    assert succeeded.status is ParseStatus.ok and succeeded.error is None
    assert BrokenParser.calls == FixedParser.calls == 1


def test_persistent_failure_is_reported_each_run():
    BrokenParser.calls = 0
    deps, snap = build(BrokenParser)

    first = parse_pending(deps)
    second = parse_pending(deps)

    expected = {snap.snapshot_id: "ParseError: 暂时无法解析"}
    assert first.failed == expected
    assert second.failed == expected
    assert BrokenParser.calls == 2


def test_successful_snapshot_is_not_parsed_again():
    CountingParser.calls = 0
    deps, snap = build(CountingParser)

    first = parse_pending(deps)
    second = parse_pending(deps)

    assert first.newly_parsed == [snap.snapshot_id] and first.retried == []
    assert second.parsed == [] and second.newly_parsed == [] and second.retried == []
    assert second.skipped == 1 and not second.failed
    assert CountingParser.calls == 1
