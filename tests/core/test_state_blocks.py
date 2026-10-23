"""Offline block generations, incremental uploads and keyword reconstruction."""

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest
from typer.testing import CliRunner

from datumoj import cli, wiring
from datumoj.core.models import StoreCopy
from datumoj.core.state import StateError, StateManager
from datumoj.index.fts import SqliteKeywordIndex
from datumoj.index.vector import LanceVectorIndex
from datumoj.testing.fakes import FakeEmbedder, FakeParser, FakeRegistry, FakeStateObjectStore
from datumoj.testing.samples import make_chunk, make_snapshot, make_spec

BLOCK_SIZE = 256 * 1024


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
    deps = wiring.build(root, {"registry": FakeRegistry([make_spec()]), "vectors": None,
                              "parsers": {("fake", "text/plain"): FakeParser}})
    body = "題名\n\n公開資料の検索".encode()
    snapshot = make_snapshot(data=body)
    deps.raw.put(body)
    deps.manifest.record_snapshot(snapshot)
    assert deps.parse_pending(deps.pipeline_deps).parsed == [snapshot.snapshot_id]
    store = CountingStore()
    yield root, deps, StateManager(store, root), store
    for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
        getattr(deps, name).close()


def enlarge(deps):
    chunk = next(deps.chunks.iter_chunks())
    doc = deps.chunks.get_document(chunk.doc_key)
    extra = [make_chunk(doc.doc_key, doc.snapshot_sha256, f"para=bulk-{i}", f"資料{i:04d}" * 2000)
             for i in range(240)]
    deps.chunks.put_document(doc, deps.chunks.chunks_for(doc.doc_key) + extra)
    deps.keyword.add(extra)


def hits(index, query):
    return {item.chunk_id: item.score for item in index.search(query, k=1000).items}


def test_backups_split_and_restore_byte_for_byte(corpus, tmp_path):
    root, deps, manager, store = corpus
    enlarge(deps)
    candidate = manager.push()
    assert not any(c.name.startswith("keyword.sqlite") for c in candidate.stores)
    files = [c for c in candidate.stores if c.format == "sqlite-blocks-v1"]
    assert {c.name for c in files} == {"manifest.sqlite", "chunks.sqlite", "catalog.sqlite", "changes.sqlite"}
    assert len([c for c in candidate.stores if c.name.startswith("chunks.sqlite.blocks/")]) >= 20
    expected = {}
    for file in files:
        backup = tmp_path / file.name
        with closing(sqlite3.connect(root / file.name)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
        data = backup.read_bytes()
        blocks = [c for c in candidate.stores if c.name.startswith(file.name + ".blocks/")]
        assert [c.name for c in blocks] == [f"{file.name}.blocks/{i}" for i in range(len(blocks))]
        assert [c.size_bytes for c in blocks] == [len(data[i:i + BLOCK_SIZE]) for i in range(0, len(data), BLOCK_SIZE)]
        assert b"".join(store.get(c.key)[0] for c in blocks) == data
        assert file.sha256 == hashlib.sha256(data).hexdigest() and file.size_bytes == len(data)
        assert all(c.key == f"copies/sha256/{c.sha256}" for c in blocks)
        expected[file.name] = data
    target = tmp_path / "restored"
    report = manager.restore(target)
    assert report["verified"]
    assert report["size_bytes"] == sum(c.size_bytes for c in candidate.stores if c.format != "sqlite-block-v1")
    for name, data in expected.items():
        assert (target / name).read_bytes() == data
    with closing(SqliteKeywordIndex(target / "keyword.sqlite")) as index:
        assert hits(index, "公開資料") == hits(deps.keyword, "公開資料")


def test_small_sqlite_update_uploads_only_changed_blocks(corpus):
    root, deps, manager, store = corpus
    enlarge(deps)
    first = manager.push()
    with sqlite3.connect(root / "chunks.sqlite") as db:
        old = db.execute("SELECT doc_key,snapshot_sha256,document_json FROM documents LIMIT 1").fetchone()
        document = json.loads(old[2])
        document["title"] = "別名"
        db.execute("UPDATE documents SET document_json=? WHERE doc_key=? AND snapshot_sha256=?",
                   (json.dumps(document, ensure_ascii=False, separators=(",", ":")), *old[:2]))
    store.puts.clear()
    store.gets.clear()
    second = manager.push()
    blocks = [c for c in second.stores if c.format == "sqlite-block-v1"]
    uploaded = [key for key in store.puts if key.startswith("copies/")]
    changed = {c.key for c in blocks} - {c.key for c in first.stores if c.format == "sqlite-block-v1"}
    assert set(uploaded) == changed
    assert 0 < len(uploaded) < len(blocks) / 10
    assert not any(key.startswith(("copies/", "raw/")) for key in store.gets)
    # Multi-block checksum entries describe bytes; no whole database is uploaded.
    whole = next(c for c in second.stores if c.name == "chunks.sqlite")
    assert not store.exists(whole.key)


@pytest.mark.parametrize("tamper", ["block", "whole", "missing", "order", "size", "mixed", "orphan"])
def test_corrupt_or_invalid_blocks_never_deliver_directory(corpus, tmp_path, tamper):
    _, deps, manager, store = corpus
    enlarge(deps)
    document = manager.push().model_dump(mode="json")
    blocks = [c for c in document["stores"] if c["name"].startswith("chunks.sqlite.blocks/")]
    whole = next(c for c in document["stores"] if c["name"] == "chunks.sqlite")
    if tamper == "block":
        store.put(blocks[0]["key"], b"synthetic corruption")
    elif tamper == "whole":
        whole.update(sha256="a" * 64, key="copies/sha256/" + "a" * 64)
    elif tamper == "missing":
        document["stores"].remove(blocks[1])
    elif tamper == "order":
        blocks[0]["name"], blocks[1]["name"] = blocks[1]["name"], blocks[0]["name"]
    elif tamper == "size":
        blocks[0]["size_bytes"] -= 1
    elif tamper == "mixed":
        whole["format"] = "sqlite"
    else:
        document["stores"].remove(whole)
    store.put("generations/1.json", json.dumps(document).encode())
    target = tmp_path / "restored"
    target.mkdir()
    with pytest.raises(StateError):
        manager.restore(target)
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("raw", ["local", "remote"])
@pytest.mark.parametrize("local_keyword", [True, False])
def test_rebuild_uses_only_current_published_chunks(corpus, tmp_path, raw, local_keyword):
    root, deps, manager, store = corpus
    current = next(deps.chunks.iter_chunks())
    doc = deps.chunks.get_document(current.doc_key)
    # Retain an unselected version containing text absent from the published index.
    sha = hashlib.sha256(b"synthetic unpublished version").hexdigest()
    staged = make_chunk(doc.doc_key, sha, "para=unpublished", "未公開秘密資料")
    deps.chunks.put_document(doc.model_copy(update={"snapshot_sha256": sha}), [staged], publish=False)
    expected = {q: hits(deps.keyword, q) for q in ("公開資料", "題名", "未公開秘密")}
    expected_ids = {c.chunk_id for c in deps.chunks.iter_chunks()}
    if not local_keyword:
        deps.keyword.close()
        (root / "keyword.sqlite").unlink()
    candidate = manager.push()
    assert not any(c.name.startswith("keyword.sqlite") for c in candidate.stores)
    store.gets.clear()
    target = tmp_path / "restored"
    assert manager.restore(target, raw=raw)["verified"]
    with closing(SqliteKeywordIndex(target / "keyword.sqlite")) as index:
        assert {q: hits(index, q) for q in expected} == expected
    with sqlite3.connect(target / "keyword.sqlite") as db:
        assert {row[0] for row in db.execute("SELECT chunk_id FROM entries")} == expected_ids
    if raw == "remote":
        assert not (target / "raw").exists()
        assert not any(key.startswith("raw/") for key in store.gets)


def test_push_still_rejects_unpublished_keyword_entries(corpus):
    _, deps, manager, store = corpus
    current = next(deps.chunks.iter_chunks())
    deps.keyword.add([current.model_copy(update={"chunk_id": "synthetic-unpublished-id"})])
    with pytest.raises(StateError, match="未发布"):
        manager.push()
    assert not store.puts


@pytest.mark.parametrize("with_vectors", [False, True])
def test_legacy_whole_file_generation_with_keyword_restores(corpus, tmp_path, with_vectors):
    root, deps, manager, store = corpus
    if with_vectors:
        vectors = LanceVectorIndex(root / "vectors", FakeEmbedder())
        vectors.add(list(deps.chunks.iter_chunks()))
    candidate = manager.push()
    legacy = []
    for copy in candidate.stores:
        if copy.format == "sqlite-block-v1":
            continue
        if copy.format == "sqlite-blocks-v1":
            blocks = [c for c in candidate.stores if c.name.startswith(copy.name + ".blocks/")]
            store.put(copy.key, b"".join(store.get(c.key)[0] for c in blocks))
            copy = copy.model_copy(update={"format": "sqlite"})
        legacy.append(copy)
    backup = tmp_path / "legacy-keyword.sqlite"
    with closing(sqlite3.connect(root / "keyword.sqlite")) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    data = backup.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    keyword = StoreCopy(name="keyword.sqlite", key=f"copies/sha256/{sha}", sha256=sha,
                        size_bytes=len(data), format="sqlite")
    legacy.append(keyword)
    store.put(keyword.key, data)
    store.put("generations/1.json", candidate.model_copy(update={"stores": legacy}).model_dump_json().encode())
    target = tmp_path / "restored"
    assert manager.restore(target)["verified"]
    assert (target / "keyword.sqlite").read_bytes() == data
    with closing(SqliteKeywordIndex(target / "keyword.sqlite")) as index:
        assert hits(index, "公開資料") == hits(deps.keyword, "公開資料")
    if with_vectors:
        assert LanceVectorIndex(target / "vectors", FakeEmbedder()).count_rows() == vectors.count_rows()
    assert all(c.format != "sqlite" for c in manager.push().stores)


def test_rebuild_or_provenance_failure_does_not_deliver_directory(corpus, tmp_path, monkeypatch):
    from datumoj.core import state

    _, _, manager, _ = corpus
    manager.push()

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    with monkeypatch.context() as patch:
        patch.setattr(SqliteKeywordIndex, "add", fail)
        with pytest.raises(StateError):
            manager.restore(tmp_path / "bad-index")
        assert not (tmp_path / "bad-index").exists()
    monkeypatch.setattr(state, "_sample_provenance", fail)
    with pytest.raises(StateError):
        manager.restore(tmp_path / "bad-provenance")
    assert not (tmp_path / "bad-provenance").exists()


def test_cli_output_and_logs_hide_configuration_on_rebuild_failure(corpus, tmp_path, monkeypatch, caplog):
    _, _, manager, _ = corpus
    secrets = {"R2_ACCESS_KEY_ID": "synthetic-block-id", "R2_SECRET_ACCESS_KEY": "synthetic-block-secret",
               "R2_ENDPOINT": "https://synthetic-block.invalid", "DATUMOJ_S3_BUCKET": "synthetic-block-bucket"}
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(wiring, "build_state", lambda root: manager)
    monkeypatch.setattr(cli, "remote_configured", lambda: False)
    runner = CliRunner()
    pushed = runner.invoke(cli.app, ["state", "push", "--data-dir", str(manager.data_dir)])
    assert pushed.exit_code == 0, pushed.output
    restored = runner.invoke(cli.app, ["state", "restore", "--to", str(tmp_path / "remote"), "--raw", "remote"])
    assert restored.exit_code == 0, restored.output
    listed = runner.invoke(cli.app, ["state", "list"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)[0]["size_bytes"] == json.loads(restored.output)["size_bytes"]

    def fail(*args):
        raise RuntimeError(" ".join(secrets.values()))

    monkeypatch.setattr(SqliteKeywordIndex, "add", fail)
    failed = runner.invoke(cli.app, ["state", "restore", "--to", str(tmp_path / "failed")])
    assert failed.exit_code == 1
    assert not (tmp_path / "failed").exists()
    output = pushed.output + restored.output + listed.output + failed.output + caplog.text
    assert all(secret not in output for secret in secrets.values())
