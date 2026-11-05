"""Marked, reusable snapshots must not cause one raw HEAD per snapshot."""

from datumoj.rikolto.pipeline import parse_pending
from datumoj.rikolto.testing.fakes import FakeParser
from datumoj.rikolto.testing.samples import T0
from tests.test_pipeline import CountingRawStore, add, build


def test_thousand_marked_parsed_snapshots_need_no_raw_exists():
    deps = build([FakeParser])
    deps.raw = CountingRawStore()
    for i in range(1000):
        snap = add(deps, f"本文 {i}".encode(), url=f"https://example.go.jp/{i}.txt")
        deps.manifest.mark_raw_optional(snap.sha256, T0)
    first = parse_pending(deps)
    assert len(first.parsed) == 1000 and not first.failed
    assert deps.raw.exists_calls == 1000
    deps.raw.blobs.clear()
    deps.raw.exists_calls = deps.raw.get_calls = 0

    summary = parse_pending(deps)

    assert summary.skipped == 1000 and summary.parsed == [] and summary.failed == {}
    assert deps.raw.exists_calls == deps.raw.get_calls == 0
