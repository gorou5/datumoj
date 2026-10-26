"""D-01 合成离线复现：资源身份和解析缓存不能只按 blob 身份判断。"""

from datetime import timedelta

from datumoj.rikolto.pipeline import PipelineDeps, parse_pending
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore, FakeChunkStore, FakeKeywordIndex, FakeManifest, FakeObservationStore,
    FakeParser, FakeRawStore, FakeRegistry,
)
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec


def pipeline():
    class CountingParser(FakeParser):
        calls = 0

        def parse(self, snapshot, data, meta=None):
            type(self).calls += 1
            return super().parse(snapshot, data, meta)

    return PipelineDeps(
        manifest=FakeManifest(), raw=FakeRawStore(), registry=FakeRegistry([make_spec()]),
        chunks=FakeChunkStore(), keyword=FakeKeywordIndex(), observations=FakeObservationStore(),
        catalog=FakeCatalogStore(), parsers={("fake", "text/plain"): CountingParser},
    ), CountingParser


def test_identical_bytes_at_two_urls_produce_two_searchable_resources():
    deps, parser = pipeline()
    data = b"Synthetic notice\n\nShared body"
    deps.raw.put(data)
    for name in ("one", "two"):
        deps.manifest.record_snapshot(make_snapshot(
            snapshot_id=name, url=f"https://example.go.jp/{name}.txt", data=data,
        ))
    summary = parse_pending(deps)
    assert summary.parsed == ["one", "two"] and parser.calls == 2
    assert {hit.doc_key for hit in deps.keyword.search("Shared body").items} == {"jp-test:one.txt", "jp-test:two.txt"}
    for name in ("one", "two"):
        assert deps.chunks.get_document(f"jp-test:{name}.txt") is not None
        assert deps.chunks.chunks_for(f"jp-test:{name}.txt")


def test_a_b_a_makes_last_a_current_without_parsing_a_twice():
    deps, parser = pipeline()
    snapshots = []
    for i, data in enumerate((b"alpha", b"beta", b"alpha")):
        deps.raw.put(data)
        snapshot = make_snapshot(snapshot_id=f"s{i}", data=data, fetched_at=T0 + timedelta(seconds=i))
        snapshots.append(snapshot)
        deps.manifest.record_snapshot(snapshot)
    summary = parse_pending(deps)
    assert summary.parsed == ["s0", "s1"] and summary.skipped == 1 and parser.calls == 2
    assert deps.chunks.get_document("jp-test:a.txt").snapshot_sha256 == snapshots[-1].sha256
    assert deps.chunks.current_order("jp-test:a.txt") == deps.manifest.snapshot_order("s2")
    assert deps.keyword.search("alpha").items and not deps.keyword.search("beta").items
