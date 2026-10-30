"""解析缓存按上下文隔离，快照发布按持久化顺序单调推进。"""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock

import pytest

from datumoj.rikolto.core.contracts import ParseRecord, ParseStatus
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import FetchOutcome, parse_context_fingerprint
from datumoj.rikolto.parsers.xml_law import LawXMLParser
from datumoj.rikolto.pipeline import PipelineDeps, _record_fetch_changes, parse_pending
from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChangeLog,
    FakeChunkStore,
    FakeEmbedder,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
    FakeVectorIndex,
)
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec


@pytest.fixture(params=["fake", "sqlite"])
def deps(request, tmp_path):
    class CountingParser(FakeParser):
        calls = 0

        def parse(self, snapshot, data, meta=None):
            type(self).calls += 1
            result = super().parse(snapshot, data, meta)
            # 同 doc_key、同 SHA 的上下文仍能改变产物。
            if meta and meta.get("label"):
                result = result.model_copy(update={
                    "chunks": [c.model_copy(update={"text": meta["label"]}) for c in result.chunks],
                })
            return result

    persistent = request.param == "sqlite"
    value = PipelineDeps(
        manifest=SqliteManifest(tmp_path / "manifest.db") if persistent else FakeManifest(),
        chunks=SqliteChunkStore(tmp_path / "chunks.db") if persistent else FakeChunkStore(),
        raw=FakeRawStore(), registry=FakeRegistry([make_spec()]), keyword=FakeKeywordIndex(),
        observations=FakeObservationStore(), catalog=FakeCatalogStore(), change_log=FakeChangeLog(),
        vectors=FakeVectorIndex(FakeEmbedder()), parsers={("fake", "text/plain"): CountingParser},
    )
    yield value
    if persistent:
        value.manifest.close()
        value.chunks.close()


def add(deps, sid, data=b"title\n\nbody", *, url="https://example.go.jp/a.txt", meta=None, **kw):
    deps.raw.put(data)
    snap = make_snapshot(snapshot_id=sid, data=data, url=url, **kw)
    deps.manifest.record_snapshot(snap, meta)
    return snap


def record(deps, snap):
    parser = next(iter(deps.parsers.values()))
    return deps.manifest.get_parse(snap.sha256, parser.name, parser.version,
                                   parse_context_fingerprint(snap, deps.manifest.snapshot_meta(snap.snapshot_id)))


def calls(deps):
    return next(iter(deps.parsers.values())).calls


def test_same_bytes_different_urls_are_both_parsed_and_searchable(deps):
    a = add(deps, "a")
    b = add(deps, "b", url="https://example.go.jp/b.txt")
    summary = parse_pending(deps)
    assert summary.parsed == ["a", "b"] and summary.skipped == 0
    assert calls(deps) == 2
    assert record(deps, a).doc_keys == ["jp-test:a.txt"]
    assert record(deps, b).doc_keys == ["jp-test:b.txt"]
    assert {hit.doc_key for hit in deps.keyword.search("body").items} == {"jp-test:a.txt", "jp-test:b.txt"}
    assert {c.doc_key for c in deps.chunks.iter_chunks()} == {"jp-test:a.txt", "jp-test:b.txt"}


def test_a_b_a_reuses_parse_and_publishes_last_observation(deps):
    first = add(deps, "a1", b"title\n\noldbody")
    add(deps, "b", b"title\n\nnewbody", fetched_at=T0 + timedelta(seconds=1))
    parse_pending(deps)
    last = add(deps, "a2", b"title\n\noldbody", fetched_at=T0 + timedelta(seconds=2))
    deps.vectors.add = Mock(wraps=deps.vectors.add)
    summary = parse_pending(deps)
    assert calls(deps) == 2 and summary.parsed == [] and summary.skipped == 3
    assert deps.chunks.get_document("jp-test:a.txt").snapshot_sha256 == first.sha256 == last.sha256
    assert deps.chunks.current_order("jp-test:a.txt") == deps.manifest.snapshot_order("a2")
    assert deps.keyword.search("oldbody").items and not deps.keyword.search("newbody").items
    assert len(deps.change_log.list()) == 2  # 复用已有版本不再产生变化事件。
    assert {c.text for c in deps.vectors.add.call_args.args[0]} == {"title", "oldbody"}


def test_duplicate_fetch_advances_order_without_parse_or_change(deps):
    add(deps, "first")
    parse_pending(deps)
    before = deps.change_log.list()
    add(deps, "repeat")  # 同时间，依赖持久化 seq 决定顺序。
    publish = Mock(wraps=deps.chunks.publish_current)
    deps.chunks.publish_current = publish
    summary = parse_pending(deps)
    assert calls(deps) == 1 and summary.parsed == [] and summary.skipped == 2
    assert publish.call_count == 2  # 包含缓存命中的每个成功快照。
    assert deps.chunks.current_order("jp-test:a.txt") == deps.manifest.snapshot_order("repeat")
    assert deps.change_log.list() == before


def test_different_fingerprints_can_produce_different_document_keys(deps):
    a = add(deps, "a", meta={"doc_id": "one"})
    b = add(deps, "b", meta={"doc_id": "two"})
    assert parse_pending(deps).parsed == ["a", "b"]
    assert calls(deps) == 2
    assert record(deps, a).doc_keys == ["jp-test:one"]
    assert record(deps, b).doc_keys == ["jp-test:two"]
    assert {hit.doc_key for hit in deps.keyword.search("body").items} == {"jp-test:one", "jp-test:two"}


def test_shared_version_slot_requires_the_matching_last_writer(deps):
    a = add(deps, "a", meta={"label": "alpha"})
    b = add(deps, "b", meta={"label": "beta"})
    parse_pending(deps)
    assert calls(deps) == 2
    # 再观察 alpha，缓存记录仍在，但槽位已由 beta 重写，必须重新解析。
    add(deps, "a2", meta={"label": "alpha"})
    result = parse_pending(deps)
    assert result.parsed == ["a", "b", "a2"] and calls(deps) == 5
    assert deps.manifest.last_writer("jp-test:a.txt", a.sha256) == record(deps, a).context_fingerprint
    assert record(deps, a).context_fingerprint != record(deps, b).context_fingerprint
    assert {c.text for c in deps.chunks.chunks_for("jp-test:a.txt")} == {"alpha"}
    assert deps.keyword.search("alpha").items and not deps.keyword.search("beta").items
    # 相等水位的最新快照重写，也必须把索引恢复到 alpha。
    parse_pending(deps)
    assert deps.keyword.search("alpha").items and not deps.keyword.search("beta").items


def test_unknown_outputs_are_reparsed(deps):
    snap = add(deps, "unknown")
    parser = next(iter(deps.parsers.values()))
    deps.manifest.record_parse(ParseRecord(
        sha256=snap.sha256, parser=parser.name, version=parser.version, status=ParseStatus.ok,
        context_fingerprint=parse_context_fingerprint(snap, {}), doc_keys=None,
    ))
    assert parse_pending(deps).parsed == ["unknown"]
    assert calls(deps) == 1 and record(deps, snap).doc_keys == ["jp-test:a.txt"]


def test_law_same_bytes_different_roles_do_not_share_empty_result(deps):
    class CountingLaw(LawXMLParser):
        calls = 0

        def parse(self, snapshot, data, meta=None):
            type(self).calls += 1
            return super().parse(snapshot, data, meta)

    deps.registry = FakeRegistry([make_spec("jp-egov-law", kind="egov_law")])
    deps.parsers = {("egov_law", "*"): CountingLaw}
    xml = ('<Law><LawNum>令和一年法律第一号</LawNum><LawBody><LawTitle>合成法</LawTitle>'
           '<MainProvision><Article Num="1"><Paragraph Num="1"><ParagraphSentence>'
           '<Sentence>合成本文</Sentence></ParagraphSentence></Paragraph></Article></MainProvision>'
           '</LawBody></Law>').encode()
    listing = add(deps, "list", xml, source_id="jp-egov-law", meta={"role": "law_list"})
    body = add(deps, "body", xml, source_id="jp-egov-law", meta={"role": "law", "law_id": "L1"})
    assert parse_pending(deps).parsed == ["list", "body"]
    assert CountingLaw.calls == 2
    assert record(deps, listing).doc_keys == []
    assert record(deps, body).doc_keys == ["jp-egov-law:L1"]
    assert deps.keyword.search("合成本文").items
    assert parse_pending(deps).skipped == 2 and CountingLaw.calls == 2


@pytest.mark.parametrize("count", [1, 50])
def test_dead_link_history_and_chunk_reads_do_not_grow_with_url_count(deps, count):
    for i in range(count):
        url = f"https://example.go.jp/{i}.txt"
        add(deps, f"ok{i}", url=url)
        add(deps, f"dead{i}", url=url, fetched_at=T0 + timedelta(seconds=1),
            outcome=FetchOutcome.http_error, http_status=404 if i % 2 else 410)
    parse_pending(deps)
    history = Mock(wraps=deps.change_log.list)
    chunks = Mock(wraps=deps.chunks.iter_chunks)
    deps.change_log.list = history
    deps.chunks.iter_chunks = chunks
    _record_fetch_changes(deps, deps.manifest.snapshots_for("jp-test"))
    assert history.call_count == chunks.call_count == 1
    from datumoj.rikolto.core.models import ChangeKind
    assert len(history(kinds=[ChangeKind.removed])) == count
    assert len(history(kinds=[ChangeKind.fetch_failed])) == count


def test_reopen_manifest_can_reuse_outputs_and_publish_a_again(deps, tmp_path):
    add(deps, "a", b"alpha")
    add(deps, "b", b"beta", fetched_at=T0 + timedelta(seconds=1))
    parse_pending(deps)
    if isinstance(deps.manifest, SqliteManifest):
        deps.manifest.close()
        deps.chunks.close()
        deps.manifest = SqliteManifest(tmp_path / "manifest.db")
        deps.chunks = SqliteChunkStore(tmp_path / "chunks.db")
    deps = replace(deps)  # 新管线实例，解析产物关联只来自 manifest。
    add(deps, "a2", b"alpha", fetched_at=T0 + timedelta(seconds=2))
    assert parse_pending(deps).parsed == [] and calls(deps) == 2
    assert deps.keyword.search("alpha").items and not deps.keyword.search("beta").items
    assert deps.chunks.current_order("jp-test:a.txt") == deps.manifest.snapshot_order("a2")


def test_reuse_checks_every_output_including_documents_without_chunks(deps):
    class MultiDocumentParser(FakeParser):
        calls = 0

        def parse(self, snapshot, data, meta=None):
            type(self).calls += 1
            base = super().parse(snapshot, data, meta)
            empty = base.documents[0].model_copy(update={"doc_key": "jp-test:unchunked"})
            return base.model_copy(update={"documents": [*base.documents, empty]})

    deps.parsers = {("fake", "text/plain"): MultiDocumentParser}
    snap = add(deps, "multi")
    assert parse_pending(deps).parsed == ["multi"]
    cached = record(deps, snap)
    assert cached.doc_keys == ["jp-test:a.txt", "jp-test:unchunked"]
    assert deps.chunks.current_order("jp-test:unchunked") == deps.manifest.snapshot_order("multi")
    deps.manifest.record_parse(cached.model_copy(update={
        "context_fingerprint": "ab" * 32, "doc_keys": ["jp-test:unchunked"],
    }))
    # 第一个文档的写入者仍匹配，第二个不匹配也必须让整个解析记录失效。
    assert parse_pending(deps).parsed == ["multi"] and MultiDocumentParser.calls == 2
    assert deps.manifest.last_writer("jp-test:unchunked", snap.sha256) == cached.context_fingerprint
    assert parse_pending(deps).parsed == [] and MultiDocumentParser.calls == 2
