"""Content-addressed vector copies and verified, offline state restoration."""

import json

import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.core.state import StateError, StateManager, local_write
from datumoj.rikolto.index.vector import LanceVectorIndex
from datumoj.rikolto.testing.fakes import FakeEmbedder, FakeParser, FakeRegistry, FakeStateObjectStore
from datumoj.rikolto.testing.samples import make_snapshot, make_spec


class CountingStore(FakeStateObjectStore):
    def __init__(self):
        super().__init__()
        self.puts = []
        self.gets = []

    def put_if_absent(self, key, data):
        self.puts.append(key)
        return super().put_if_absent(key, data)

    def get(self, key):
        self.gets.append(key)
        return super().get(key)


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "source"
    embedder = FakeEmbedder()
    vectors = LanceVectorIndex(root / "vectors", embedder)
    deps = wiring.build(root, {"registry": FakeRegistry([make_spec()]), "vectors": vectors,
                              "parsers": {("fake", "text/plain"): FakeParser}})

    def add(text, name):
        body = text.encode()
        snapshot = make_snapshot(data=body, url=f"https://example.go.jp/{name}")
        deps.raw.put(body)
        deps.manifest.record_snapshot(snapshot)
        assert not deps.parse_pending(deps.pipeline_deps).failed
        return snapshot

    add("標題\n\n第一資料\n\n第二資料", "a.txt")
    store = CountingStore()
    yield root, deps, StateManager(store, root), store, add
    for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
        getattr(deps, name).close()


def test_two_generations_upload_only_changed_vector_files_and_restore_search(corpus, tmp_path):
    _, deps, manager, store, add = corpus
    first = manager.push()
    assert first.vector_coverage.model == deps.vectors.embedder.model
    assert first.vector_coverage.version == "unknown"
    assert first.vector_coverage.dim == 64
    assert first.vector_coverage.embedded_chunks == 3
    assert first.vector_coverage.pending_chunks == 0
    old = {c.name: c for c in first.stores if c.name.startswith("vectors/")}
    assert {"vectors/_meta.json", "vectors/vectors-state.json"} <= old.keys()
    assert any(name.startswith("vectors/chunks.lance/data/") for name in old)
    add("追記資料", "b.txt")
    store.puts.clear()
    store.gets.clear()
    second = manager.push()
    new = {c.name: c for c in second.stores if c.name.startswith("vectors/")}
    unchanged = set(old) & set(new)
    unchanged = {name for name in unchanged if old[name] == new[name]}
    assert any("/data/" in name for name in unchanged)
    assert all(new[name].key not in store.puts for name in unchanged)
    changed = [copy for copy in new.values() if copy.key not in {c.key for c in first.stores}]
    assert changed and all(copy.key in store.puts for copy in changed)
    assert not any(key.startswith("copies/") for key in store.gets)
    assert second.vector_coverage.embedded_chunks == 4
    original = deps.vectors.search("第二資料", k=10)
    target = tmp_path / "restored"
    report = manager.restore(target)
    assert report["verified"]
    restored = LanceVectorIndex(target / "vectors", FakeEmbedder())
    actual = restored.search("第二資料", k=10)
    assert [(r.chunk_id, r.score) for r in actual.items] == [(r.chunk_id, r.score) for r in original.items]
    assert restored.count_rows() == 4
    assert (target / "vectors-state.json").is_file()
    old_target = tmp_path / "old"
    manager.restore(old_target, generation=1, raw="remote")
    assert LanceVectorIndex(old_target / "vectors", FakeEmbedder()).count_rows() == 3


def test_unchanged_push_does_not_upload_any_vector_copy(corpus):
    _, _, manager, store, _ = corpus
    first = manager.push()
    store.puts.clear()
    second = manager.push()
    assert first.stores == second.stores
    assert store.puts == ["generations/2.json"]


@pytest.mark.parametrize("kind", ["data", "meta", "pending"])
def test_tampered_vector_copy_never_becomes_usable(corpus, tmp_path, kind):
    _, _, manager, store, _ = corpus
    generation = manager.push()
    copies = {c.name: c for c in generation.stores}
    if kind == "data":
        copy = next(c for c in generation.stores if c.name.startswith("vectors/") and "/data/" in c.name)
    else:
        copy = copies["vectors/_meta.json" if kind == "meta" else "vectors/vectors-state.json"]
    _, etag = store.get(copy.key)
    store.put_if_match(copy.key, b"synthetic corruption", etag)
    target = tmp_path / "restore"
    with pytest.raises(StateError, match="SHA256"):
        manager.restore(target)
    assert not target.exists()


@pytest.mark.parametrize("field,value", [("embedded_chunks", 500), ("pending_chunks", 1), ("dim", 10), ("model", "wrong")])
def test_coverage_mismatch_fails_even_with_valid_file_hashes(corpus, tmp_path, field, value):
    _, _, manager, store, _ = corpus
    manager.push()
    key = "generations/1.json"
    data, etag = store.get(key)
    document = json.loads(data)
    document["vector_coverage"][field] = value
    store.put_if_match(key, json.dumps(document).encode(), etag)
    target = tmp_path / "restore"
    with pytest.raises(StateError, match="向量恢复覆盖"):
        manager.restore(target)
    assert not target.exists()


def test_pending_coverage_restores_and_resumes_with_fresh_budget(corpus, tmp_path):
    _, deps, manager, _, add = corpus
    deps.pipeline_deps.vector_limit = 1
    add("第三資料\n\n第四資料\n\n第五資料", "b.txt")
    generation = manager.push()
    assert generation.vector_coverage.embedded_chunks == 4
    assert generation.vector_coverage.pending_chunks == 2
    target = tmp_path / "partial"
    manager.restore(target)
    restored = wiring.build(target, {"registry": FakeRegistry([make_spec()]),
                                     "vectors": LanceVectorIndex(target / "vectors", FakeEmbedder()),
                                     "parsers": {("fake", "text/plain"): FakeParser}})
    try:
        restored.pipeline_deps.vector_limit = 2
        assert not restored.parse_pending(restored.pipeline_deps).failed
        assert restored.vectors.count_rows() == 6
        assert not json.loads((target / "vectors-state.json").read_text())["missing"]
    finally:
        for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
            getattr(restored, name).close()


def test_push_maintains_inside_same_local_write_boundary(corpus, monkeypatch):
    root, _, manager, _, _ = corpus
    from datumoj.rikolto.core import state
    original = state.maintain_vectors
    calls = []

    def maintain(path):
        from datumoj.rikolto.core.schedule import _locked
        with _locked(root / "state.lock", blocking=False) as acquired:
            assert acquired is None
        calls.append(True)
        return original(path)

    monkeypatch.setattr(state, "maintain_vectors", maintain)
    manager.push()
    assert calls == [True]
    with local_write(root), pytest.raises(StateError, match="本地写入"):
        manager.push()
    assert calls == [True]


def test_push_maintenance_failure_preserves_published_state(corpus, monkeypatch, caplog, tmp_path):
    _, _, manager, _, _ = corpus
    from datumoj.rikolto.core import state

    def fail(path):
        raise OSError("private synthetic path")

    monkeypatch.setattr(state, "maintain_vectors", fail)
    generation = manager.push()
    assert generation.vector_coverage.embedded_chunks == 3
    assert "向量维护失败" in caplog.text and "private synthetic path" not in caplog.text
    assert manager.restore(tmp_path / "restore")["verified"]


def test_vector_symlink_rejected_before_copying(corpus, tmp_path):
    root, _, manager, store, _ = corpus
    private = tmp_path / "private-synthetic"
    private.write_text("synthetic-private-content")
    (root / "vectors" / "external").symlink_to(private)
    with pytest.raises(StateError, match="符号链接"):
        manager.push()
    assert not store.puts


def test_missing_vector_metadata_or_table_rejects_push(corpus):
    root, _, manager, _, _ = corpus
    meta = root / "vectors" / "_meta.json"
    data = json.loads(meta.read_text())
    data["generation"] = 99
    meta.write_text(json.dumps(data))
    with pytest.raises(StateError, match="当前代表缺失"):
        manager.push()
    meta.unlink()
    with pytest.raises(StateError, match="缺少元数据"):
        manager.push()
