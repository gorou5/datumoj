from __future__ import annotations

from datumoj.core.models import parse_context_fingerprint
from datumoj.core.contracts import NO_PARSER, ContentState, ParseResult, ParseStatus
from datumoj.pipeline import PipelineDeps, parse_pending, reindex
from datumoj.testing.fakes import (
    FakeCatalogStore, FakeChunkStore, FakeKeywordIndex, FakeManifest, FakeObservationStore, FakeParser,
    FakeRawStore, FakeRegistry, FakeVectorIndex, FakeEmbedder,
)
from datumoj.testing.samples import (
    T0, make_dataset, make_observation, make_resource, make_snapshot, sha,
)
from datetime import timedelta


class CsvParser(FakeParser):
    name = "fake-csv"
    content_type = "text/csv"


class V2Parser(FakeParser):
    version = "2"


class CatalogParser(FakeParser):
    """text/html：把正文当数据集目录 + 一个观测值，用来检查分发。"""

    name = "fake-catalog"
    content_type = "text/html"

    def parse(self, snapshot, data, meta=None):
        base = super().parse(snapshot, data, meta)
        ds = make_dataset("jp-test:ds", "数据集")
        res = make_resource("jp-test:ds", "r1", snapshot.resource_url)
        obs = make_observation(
            "o1", "1", source_id="jp-test", dataset_key="jp-test:ds", snapshot_sha256=snapshot.sha256, fetched_at=snapshot.fetched_at,
        )
        return ParseResult(
            documents=base.documents, chunks=base.chunks, observations=[obs], datasets=[ds], resources=[res],
        )


def build(parsers, vectors=False):
    deps = PipelineDeps(
        manifest=FakeManifest(), raw=FakeRawStore(), registry=FakeRegistry([_spec()]), chunks=FakeChunkStore(),
        keyword=FakeKeywordIndex(), observations=FakeObservationStore(), catalog=FakeCatalogStore(),
        vectors=FakeVectorIndex(FakeEmbedder()) if vectors else None,
        parsers={(p.source_kind, p.content_type): p for p in parsers},
    )
    return deps


def _spec():
    from datumoj.testing.samples import make_spec
    return make_spec()


def add(deps, data, url="https://example.go.jp/a.txt", meta=None, **kw):
    deps.raw.put(data)
    snap = make_snapshot(url=url, data=data, snapshot_id=f"s{len(deps.manifest._snaps)}", **kw)
    deps.manifest.record_snapshot(snap, meta)
    return snap


def test_second_run_does_nothing():
    deps = build([FakeParser])
    add(deps, "標題\n\n本文".encode())
    first = parse_pending(deps)
    assert len(first.parsed) == 1
    second = parse_pending(deps)
    assert second.parsed == [] and second.skipped == 1 and not second.failed
    assert deps.keyword.search("本文").items
    assert deps.chunks.get_document("jp-test:a.txt") is not None


def test_bad_snapshot_does_not_block_others():
    deps = build([FakeParser])
    bad = add(deps, b"\xff\xfe\x00bad", url="https://example.go.jp/bad.txt")
    good = add(deps, "標題\n\n本文".encode(), url="https://example.go.jp/good.txt", fetched_at=T0 + timedelta(1))
    summary = parse_pending(deps)
    assert list(summary.failed) == [bad.snapshot_id]
    assert summary.parsed == [good.snapshot_id]
    rec = deps.manifest.get_parse(
        bad.sha256, "fake-text", "1",
        parse_context_fingerprint(bad, deps.manifest.snapshot_meta(bad.snapshot_id)),
    )
    assert rec.status is ParseStatus.failed and rec.error
    assert parse_pending(deps).skipped == 2


def test_version_upgrade_reparses():
    deps = build([FakeParser])
    add(deps, "標題\n\n本文".encode())
    parse_pending(deps)
    deps.parsers = {("fake", "text/plain"): V2Parser}
    summary = parse_pending(deps)
    assert len(summary.parsed) == 1
    assert deps.manifest.get_parse(sha(b"x"), "fake-text", "2") is None
    assert parse_pending(deps).parsed == []


def test_no_parser_recorded_once():
    deps = build([FakeParser])
    snap = add(deps, b"a,b", content_type="application/pdf")
    first = parse_pending(deps)
    assert first.no_parser == [snap.snapshot_id]
    assert deps.manifest.get_parse(
        snap.sha256, NO_PARSER, "-",
        parse_context_fingerprint(snap, deps.manifest.snapshot_meta(snap.snapshot_id)),
    ).status is ParseStatus.no_parser
    second = parse_pending(deps)
    assert second.no_parser == [] and second.skipped == 1


def test_same_sha_different_content_type_runs_each_parser():
    deps = build([FakeParser, CsvParser])
    data = "標題\n\n本文".encode()
    a = add(deps, data, url="https://example.go.jp/a.txt")
    b = add(deps, data, url="https://example.go.jp/b.csv", content_type="text/csv", fetched_at=T0 + timedelta(1))
    summary = parse_pending(deps)
    assert summary.parsed == [a.snapshot_id, b.snapshot_id]
    assert deps.manifest.get_parse(
        a.sha256, "fake-text", "1",
        parse_context_fingerprint(a, deps.manifest.snapshot_meta(a.snapshot_id)),
    ) is not None
    assert deps.manifest.get_parse(
        a.sha256, "fake-csv", "1",
        parse_context_fingerprint(b, deps.manifest.snapshot_meta(b.snapshot_id)),
    ) is not None


def test_non_ok_snapshots_ignored_and_source_filter():
    deps = build([FakeParser])
    add(deps, b"x", outcome=__import__("datumoj.core.models", fromlist=["FetchOutcome"]).FetchOutcome.http_error)
    summary = parse_pending(deps, "jp-test")
    assert summary.parsed == [] and summary.skipped == 0
    try:
        parse_pending(deps, "nope")
    except ValueError:
        pass
    else:
        raise AssertionError("未知来源应抛 ValueError")


def test_latest_snapshot_is_current_version_and_dispatch():
    deps = build([FakeParser, CatalogParser], vectors=True)
    old = add(deps, "標題\n\n旧本文".encode(), fetched_at=T0)
    new = add(deps, "標題\n\n新本文".encode(), fetched_at=T0 + timedelta(1))
    parse_pending(deps)
    assert deps.chunks.get_document("jp-test:a.txt").snapshot_sha256 == new.sha256
    assert deps.chunks.get_document("jp-test:a.txt", old.sha256) is not None
    assert not deps.keyword.search("旧本文").items
    assert deps.keyword.search("新本文").items
    assert deps.vectors.search("新本文").items


def test_catalog_and_observations_and_content_state():
    deps = build([CatalogParser])
    url = "https://example.go.jp/page.html"
    add(deps, "標題\n\n本文".encode(), url=url, content_type="text/html")
    parse_pending(deps)
    assert deps.catalog.get_dataset("jp-test:ds") is not None
    assert deps.catalog.resources_for("jp-test:ds")[0].state is ContentState.observations_loaded
    assert deps.observations.describe().tables[0].rows == 1


def test_reindex_rebuilds():
    deps = build([FakeParser], vectors=True)
    add(deps, "標題\n\n本文".encode())
    parse_pending(deps)
    deps.keyword.clear()
    deps.vectors.clear()
    assert not deps.keyword.search("本文").items
    assert reindex(deps) == 2
    assert deps.keyword.search("本文").items and deps.vectors.search("本文").items
    assert reindex(deps, "jp-test") == 2
