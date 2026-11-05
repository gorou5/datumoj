"""Synthetic offline maintenance, per-run allowance and production wiring regressions."""

import json
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, wiring
from datumoj.rikolto.index.vector import LanceVectorIndex, maintain_vectors
from datumoj.rikolto.pipeline import PipelineDeps, parse_pending, reindex
from datumoj.rikolto.testing.contract_suites import VectorIndexSuite
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeEmbedder,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
)
from datumoj.rikolto.testing.samples import make_chunk, make_snapshot, make_spec


class TestMaintainedVectorContract(VectorIndexSuite):
    @pytest.fixture
    def make_index(self, tmp_path):
        def make(embedder):
            index = LanceVectorIndex(tmp_path / "vectors", embedder)
            index.maintain(timedelta(0))
            return index
        return make


def test_compaction_prunes_versions_and_files_without_changing_search(tmp_path):
    index = LanceVectorIndex(tmp_path / "vectors", FakeEmbedder(), batch_size=1)
    for i in range(24):
        index.add([make_chunk(f"jp-test:d{i}", "ab" * 32, "section=1", f"統計資料番号 {i}")])
    before = index.search("統計資料番号 7", k=24)
    report = index.maintain(timedelta(0))
    assert report["before"]["versions"] >= 24
    assert report["after"]["versions"] == 1
    assert report["after"]["data_files"] < report["before"]["data_files"] / 2
    assert report["after"]["bytes"] < report["before"]["bytes"]
    after = index.search("統計資料番号 7", k=24)
    assert {r.chunk_id: r.score for r in after.items} == pytest.approx(
        {r.chunk_id: r.score for r in before.items})


def test_recent_versions_and_other_generation_tables_survive(tmp_path):
    index = LanceVectorIndex(tmp_path / "vectors", FakeEmbedder())
    chunks = [make_chunk(f"jp-test:d{i}", "ab" * 32, "section=1", f"資料 {i}") for i in range(5)]
    for chunk in chunks:
        index.add([chunk])
    versions = {v["version"] for v in index._table.list_versions()}
    index.maintain(timedelta(hours=1))
    assert versions <= {v["version"] for v in index._table.list_versions()}
    index.begin_generation(copy_current=True)
    index.finish_generation(len(chunks))
    names = index._db.list_tables().tables
    index.maintain(timedelta(0))
    assert index._db.list_tables().tables == names
    for name in names:
        table = index._db.open_table(name)
        assert table.count_rows() == len(chunks)
        assert len(table.list_versions()) == 1
    assert len(index.search("資料", k=10).items) == 5


def test_missing_vectors_noop_and_cli_json(tmp_path):
    report = maintain_vectors(tmp_path / "absent")
    assert report["before"] == report["after"] == {"versions": 0, "data_files": 0, "bytes": 0}
    assert not (tmp_path / "absent").exists()
    result = CliRunner().invoke(cli.app, ["index", "maintain", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == report


def test_retention_environment_and_invalid_retention(tmp_path, monkeypatch):
    index = LanceVectorIndex(tmp_path / "vectors", FakeEmbedder())
    for i in range(4):
        index.add([make_chunk(f"jp-test:d{i}", "ab" * 32, "section=1", f"資料 {i}")])
    monkeypatch.setenv("DATUMOJ_VECTOR_RETAIN_HOURS", "0")
    assert index.maintain()["after"]["versions"] == 1
    with pytest.raises(ValueError):
        index.maintain(timedelta(seconds=-1))


def pipeline(tmp_path, limit=1):
    embedder = FakeEmbedder()
    deps = PipelineDeps(
        manifest=FakeManifest(), raw=FakeRawStore(), registry=FakeRegistry([make_spec()]),
        chunks=FakeChunkStore(), keyword=FakeKeywordIndex(), observations=FakeObservationStore(),
        catalog=FakeCatalogStore(), vectors=LanceVectorIndex(tmp_path / "vectors", embedder),
        parsers={("fake", "text/plain"): FakeParser}, vector_limit=limit,
        vector_state_path=tmp_path / "vectors-state.json",
    )
    return deps, embedder


def add(deps, body, name="a.txt"):
    body = body.encode()
    snap = make_snapshot(data=body, url=f"https://example.go.jp/{name}")
    deps.raw.put(body)
    deps.manifest.record_snapshot(snap)
    return snap


def test_limit_and_next_run_prioritizes_pending_chunks(tmp_path, monkeypatch):
    deps, embedder = pipeline(tmp_path, limit=2)
    add(deps, "標題\n\n第一資料\n\n第二資料")
    calls = []
    original = deps.vectors.maintain

    def maintain():
        calls.append(True)
        return original()

    monkeypatch.setattr(deps.vectors, "maintain", maintain)
    first = parse_pending(deps)
    assert embedder.embedded == 2 and first.vector_remaining == 0
    state = json.loads(deps.vector_state_path.read_text())
    assert state["missing"]["jp-test:a.txt"]["chunks"] == 1
    assert deps.keyword.search("第二資料").items
    assert len(calls) == 1
    add(deps, "新標題\n\n新資料", "b.txt")
    parse_pending(deps)
    assert embedder.embedded == 4
    # The old document was completed before the new one consumed its allowance.
    assert deps.vectors._existing_hashes(["jp-test:a.txt#section=3"])
    assert json.loads(deps.vector_state_path.read_text())["missing"]["jp-test:b.txt"]["chunks"] == 1
    parse_pending(deps)
    assert embedder.embedded == 5
    assert not json.loads(deps.vector_state_path.read_text())["missing"]
    assert deps.vectors.count_rows() == 5
    assert len(calls) == 3
    parse_pending(deps)
    assert len(calls) == 3


def test_maintenance_failure_only_warns_after_publication(tmp_path, monkeypatch, caplog):
    deps, _ = pipeline(tmp_path, limit=None)
    snap = add(deps, "資料")

    def fail():
        raise OSError("synthetic-private-configuration")

    monkeypatch.setattr(deps.vectors, "maintain", fail)
    summary = parse_pending(deps)
    assert summary.parsed == [snap.snapshot_id] and not summary.failed
    assert "向量维护失败" in caplog.text
    assert "synthetic-private-configuration" not in caplog.text


def test_failed_embedding_attempts_consume_allowance(tmp_path, monkeypatch):
    deps, embedder = pipeline(tmp_path, limit=1)
    add(deps, "第一\n\n第二")
    add(deps, "第三", "b.txt")
    calls = []

    def fail(texts):
        calls.extend(texts)
        raise OSError("synthetic failure")

    monkeypatch.setattr(embedder, "embed", fail)
    summary = parse_pending(deps)
    assert len(calls) == 1 and not summary.failed
    assert sum(item["chunks"] for item in json.loads(deps.vector_state_path.read_text())["missing"].values()) == 3


def test_limited_reindex_and_vectors_only_resume(tmp_path):
    deps, embedder = pipeline(tmp_path, limit=1)
    add(deps, "標題\n\n第一資料\n\n第二資料")
    # Populate canonical chunks with vector building disabled first.
    vectors = deps.vectors
    deps.vectors = None
    parse_pending(deps)
    deps.vectors = vectors
    assert reindex(deps) == 3
    assert embedder.embedded == 1 and vectors.count_rows() == 1
    assert reindex(deps, vectors_only=True) == 1
    assert embedder.embedded == 2 and vectors.count_rows() == 2
    assert reindex(deps, vectors_only=True) == 1
    assert embedder.embedded == 3 and vectors.count_rows() == 3
    assert not json.loads(deps.vector_state_path.read_text())["missing"]


@pytest.mark.parametrize("limit", [1, 4])
def test_index_parse_and_rebuild_share_allowance_through_real_wiring(tmp_path, monkeypatch, limit):
    monkeypatch.setenv("DATUMOJ_VECTOR_LIMIT", str(limit))
    monkeypatch.setattr(wiring, "_connected_parsers", lambda **kw: {("fake", "text/plain"): FakeParser})
    embedder = FakeEmbedder()
    vectors = LanceVectorIndex(tmp_path / "vectors", embedder)
    deps = wiring.build(tmp_path, {"registry": FakeRegistry([make_spec()]), "vectors": vectors})
    try:
        assert deps.pipeline_deps.parsers is None  # exercises wiring's dataclasses.replace
        add(deps.pipeline_deps, "標題\n\n第一資料\n\n第二資料")
        monkeypatch.setattr(cli, "_build_wiring", lambda root: deps)
        result = CliRunner().invoke(cli.app, ["--data-dir", str(tmp_path), "index", "--rebuild"])
        assert result.exit_code == 0, result.output
        assert embedder.embedded == limit
        assert vectors.count_rows() == 1
        assert sum(item["chunks"] for item in json.loads((tmp_path / "vectors-state.json").read_text())["missing"].values()) == 2
        assert deps.keyword.search("第二資料").items
        # A separate invocation gets a fresh allowance.
        result = CliRunner().invoke(cli.app, ["--data-dir", str(tmp_path), "index"])
        assert result.exit_code == 0, result.output
        assert embedder.embedded == (2 if limit == 1 else 6)
        assert vectors.count_rows() == (2 if limit == 1 else 3)
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
            getattr(deps, name).close()


@pytest.mark.parametrize("value", ["-1", "invalid"])
def test_invalid_limit_is_rejected(tmp_path, monkeypatch, value):
    monkeypatch.setenv("DATUMOJ_VECTOR_LIMIT", value)
    with pytest.raises(ValueError, match="DATUMOJ_VECTOR_LIMIT"):
        PipelineDeps(
            manifest=FakeManifest(), raw=FakeRawStore(), registry=FakeRegistry(), chunks=FakeChunkStore(),
            keyword=FakeKeywordIndex(), observations=FakeObservationStore(), catalog=FakeCatalogStore())


def test_zero_limit_publishes_keywords_and_records_all_pending(tmp_path):
    deps, embedder = pipeline(tmp_path, limit=0)
    snap = add(deps, "第一資料\n\n第二資料")
    summary = parse_pending(deps)
    assert summary.parsed == [snap.snapshot_id] and not summary.failed
    assert embedder.embedded == deps.vectors.count_rows() == 0
    assert deps.keyword.search("第二資料").items
    assert json.loads(deps.vector_state_path.read_text())["missing"]["jp-test:a.txt"]["chunks"] == 2
    deps.vector_limit = None
    parse_pending(deps)
    assert embedder.embedded == deps.vectors.count_rows() == 2


def test_limited_full_rebuild_removes_stale_vectors_and_failure_keeps_current(tmp_path, monkeypatch):
    deps, embedder = pipeline(tmp_path, limit=1)
    stale = make_chunk("jp-test:stale", "ab" * 32, "section=1", "stale")
    deps.vectors.add([stale])
    vectors = deps.vectors
    deps.vectors = None
    add(deps, "標題\n\n資料")
    parse_pending(deps)
    deps.vectors = vectors
    assert reindex(deps) == 2
    assert vectors.count_rows() == 1
    assert not vectors._existing_hashes([stale.chunk_id])
    before = vectors._current_generation
    current = vectors.search("標題", k=10)

    def fail(texts):
        raise OSError("synthetic failure")

    monkeypatch.setattr(embedder, "embed", fail)
    with pytest.raises(RuntimeError, match="候选向量代写入失败"):
        reindex(deps)
    assert vectors._current_generation == before
    assert vectors.count_rows() == 1
    monkeypatch.undo()
    assert vectors.search("標題", k=10) == current


@pytest.mark.parametrize('next_batch', ['other_source', 'zero_limit', 'exclude', 'deadline', 'failure_limit'])
@pytest.mark.parametrize('restart', [False, True])
def test_failed_shared_commit_is_not_acknowledged_by_unrelated_batch(tmp_path, monkeypatch, next_batch, restart):
    from dataclasses import replace

    from datumoj.rikolto.pipeline import ParseSummary, _load_vector_state, _retry_vectors, _start_vectors

    deps, _ = pipeline(tmp_path, limit=None)
    add(deps, '資料')
    original = deps.vectors._table.add

    def fail(*args, **kwargs):
        raise OSError('commit failure')

    monkeypatch.setattr(deps.vectors._table, 'add', fail)
    assert parse_pending(deps).vectors_missing
    assert deps.vectors.count_rows() == 0
    monkeypatch.setattr(deps.vectors._table, 'add', original)
    if restart:
        deps = replace(deps, vector_state={})
    key = 'jp-test:a.txt'
    expected = 0
    if next_batch == 'other_source':
        deps.registry = FakeRegistry([make_spec(), make_spec('jp-other')])
        body = b'Other source'
        snap = make_snapshot('jp-other', data=body)
        deps.raw.put(body)
        deps.manifest.record_snapshot(snap)
        parse_pending(deps, 'jp-other')
        expected = 1
    elif next_batch in ('zero_limit', 'deadline'):
        parse_pending(replace(deps, **({'vector_limit': 0} if next_batch == 'zero_limit'
                                       else {'deadline': 0})))
    else:
        _start_vectors(deps)
        if next_batch == 'failure_limit':
            state = _load_vector_state(deps)
            state['missing'][key]['failures'] = 3
            deps.vector_state_path.write_text(json.dumps(state))
        _retry_vectors(deps, None, ParseSummary(), exclude={key} if next_batch == 'exclude' else None)
    state = json.loads(deps.vector_state_path.read_text())
    assert state['missing'][key]['reset_document'] is True
    assert deps.vectors.count_rows() == expected
    state['missing'][key]['failures'] = 0
    deps.vector_state_path.write_text(json.dumps(state))
    assert not parse_pending(deps).vectors_missing
    assert deps.vectors.count_rows() == expected + 1
    assert not json.loads(deps.vector_state_path.read_text())['missing']


def test_failed_replacement_commit_keeps_previous_vectors(tmp_path, monkeypatch):
    deps, _ = pipeline(tmp_path, limit=None)
    add(deps, '旧資料')
    parse_pending(deps)
    old = deps.vectors._table.search().to_list()
    from datumoj.rikolto.pipeline import ParseSummary, _begin_vector_batch, _finish_vector_batch, _write_vector_document

    _begin_vector_batch(deps)
    chunks = [make_chunk('jp-test:a.txt', 'cd' * 32, 'section=2', '新資料')]
    assert _write_vector_document(deps, 'jp-test:a.txt', chunks, None) is None

    def fail(*args, **kwargs):
        raise OSError('commit failure')

    monkeypatch.setattr(deps.vectors._table, 'add', fail)
    summary = ParseSummary()
    _finish_vector_batch(deps, summary)
    assert summary.vectors_missing
    assert deps.vectors._table.search().to_list() == old


def test_automatic_compaction_waits_for_fragment_threshold_or_seven_days(tmp_path, monkeypatch):
    from datumoj.rikolto.index import vector

    index = LanceVectorIndex(tmp_path / 'vectors', FakeEmbedder())
    now = [1000000.0]
    monkeypatch.setattr(vector.time, 'time', lambda: now[0])
    for i in range(5):
        index.add([make_chunk(f'jp-test:d{i}', 'ab' * 32, 'section=1', f'資料 {i}')])
        report = index.maintain()
        assert report['after']['data_files'] == report['before']['data_files'] == i + 1
    before = {r.chunk_id: r.score for r in index.search('資料', k=10).items}
    now[0] += timedelta(days=7).total_seconds()
    index.maintain()  # elapsed interval alone triggers automatic compaction
    assert index._table.stats()['fragment_stats']['num_fragments'] == 1
    assert {r.chunk_id: r.score for r in index.search('資料', k=10).items} == pytest.approx(before)
    index.add([make_chunk('jp-test:new', 'ab' * 32, 'section=1', '追加資料')])
    monkeypatch.setattr(vector, '_COMPACT_FRAGMENTS', 2)
    index.maintain()
    assert index._table.stats()['fragment_stats']['num_fragments'] == 1
    (tmp_path / 'vectors' / '_maintenance.json').write_text('broken json')
    index.maintain()  # malformed history starts a new clock, without failing the round
    assert isinstance(json.loads((tmp_path / 'vectors' / '_maintenance.json').read_text()), dict)


def test_process_exit_before_flush_keeps_full_document_pending(tmp_path, monkeypatch):
    from dataclasses import replace

    deps, embedder = pipeline(tmp_path, limit=None)
    add(deps, '未提交資料')

    def crash(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(deps.vectors._table, 'add', crash)
    with pytest.raises(KeyboardInterrupt):
        parse_pending(deps)
    key = 'jp-test:a.txt'
    assert deps.vectors.count_rows() == 0
    assert json.loads(deps.vector_state_path.read_text())['missing'][key]['reset_document']
    restarted = replace(deps, vectors=LanceVectorIndex(tmp_path / 'vectors', embedder), vector_state={})
    parse_pending(replace(restarted, vector_limit=0))
    assert json.loads(deps.vector_state_path.read_text())['missing'][key]['reset_document']
    parse_pending(restarted)
    assert restarted.vectors.count_rows() == 1
    assert not json.loads(deps.vector_state_path.read_text())['missing']


def test_parse_batches_bound_lance_commits_and_keep_all_documents(tmp_path, monkeypatch):
    import datumoj.rikolto.pipeline as pipeline_module

    deps, _ = pipeline(tmp_path, limit=None)
    monkeypatch.setattr(pipeline_module, '_BATCH', 2)
    for i in range(5):
        add(deps, f'資料 {i}', f'{i}.txt')
    result = parse_pending(deps)
    assert len(result.parsed) == 5 and not result.vectors_missing
    assert deps.vectors.count_rows() == 5
    assert deps.vectors._table.stats()['fragment_stats']['num_fragments'] == 3
    assert len(deps.vectors._table.list_versions()) == 4  # initial empty table plus three commits
    assert not json.loads(deps.vector_state_path.read_text())['missing']


def _stored_hash(index, chunk):
    return index._existing_hashes([chunk.chunk_id])[chunk.chunk_id]


def test_batched_change_to_stored_chunk_replaces_it(tmp_path):
    from datumoj.rikolto.index.vector import _chunk_hash

    index = LanceVectorIndex(tmp_path / "vectors", FakeEmbedder())
    index.add([make_chunk("jp-test:d", "a" * 64, "section=1", "old")])
    changed = make_chunk("jp-test:d", "a" * 64, "section=1", "new")
    index.begin_batch()
    assert index.add([changed]) == 1
    index.flush()
    assert index.count_rows() == 1 and _stored_hash(index, changed) == _chunk_hash(changed)


def test_resubmitting_stored_chunk_cancels_queued_change(tmp_path):
    from datumoj.rikolto.index.vector import _chunk_hash

    index = LanceVectorIndex(tmp_path / "vectors", FakeEmbedder())
    stored = make_chunk("jp-test:d", "a" * 64, "section=1", "old")
    index.add([stored])
    index.begin_batch()
    index.add([make_chunk("jp-test:d", "a" * 64, "section=1", "new")])
    assert index.add([stored]) == 0
    index.flush()
    assert index.count_rows() == 1 and _stored_hash(index, stored) == _chunk_hash(stored)


def test_retry_failure_closes_the_batch_it_opened(tmp_path, monkeypatch):
    from datumoj.rikolto.pipeline import ParseSummary, _retry_vectors

    deps, _ = pipeline(tmp_path, limit=0)
    add(deps, "text")
    parse_pending(deps)
    deps._vector_remaining = None

    def fail(key):
        raise ValueError("synthetic")

    monkeypatch.setattr(deps.chunks, "chunks_for", fail)
    with pytest.raises(ValueError, match="synthetic"):
        _retry_vectors(deps, None, ParseSummary())
    assert deps._vector_commits is None and deps.vectors._pending_rows is None
