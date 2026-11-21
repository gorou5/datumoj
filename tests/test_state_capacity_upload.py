"""StateManager.upload_estimate: bytes a push would upload, counted from keys the remote lacks."""

import hashlib

import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.core import state
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.testing.fakes import FakeParser, FakeRegistry, FakeStateObjectStore
from datumoj.rikolto.testing.samples import make_snapshot, make_spec


class CountingStore(FakeStateObjectStore):
    def __init__(self):
        super().__init__()
        self.puts = []

    def put_if_absent(self, key, data):
        self.puts.append(key)
        return super().put_if_absent(key, data)


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "source"
    deps = wiring.build(root, {"registry": FakeRegistry([make_spec()]), "vectors": None,
                              "parsers": {("fake", "text/plain"): FakeParser}})
    body = "題名\n\n公開資料の検索".encode()
    snapshot = make_snapshot(data=body)
    deps.raw.put(body)
    deps.manifest.record_snapshot(snapshot)
    deps.parse_pending(deps.pipeline_deps)
    store = CountingStore()
    yield root, StateManager(store, root), store
    for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
        getattr(deps, name).close()


def stored_bytes(store):
    return sum(obj.size_bytes for obj in store.list_objects("copies/sha256/")) + sum(
        obj.size_bytes for obj in store.list_objects("raw/"))


def test_first_estimate_equals_what_push_uploads_and_writes_nothing(corpus):
    _, manager, store = corpus
    estimate = manager.upload_estimate()
    assert estimate > 0 and store.puts == []
    manager.push()
    assert estimate == stored_bytes(store)


def test_unchanged_state_needs_no_upload(corpus):
    _, manager, _ = corpus
    manager.push()
    assert manager.upload_estimate() == 0


def test_only_new_raw_counts_after_a_push(corpus):
    root, manager, store = corpus
    manager.push()
    before = stored_bytes(store)
    data = b"synthetic new raw bytes"
    sha = hashlib.sha256(data).hexdigest()
    path = root / "raw" / sha[:2] / sha
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    assert manager.upload_estimate() == len(data)
    manager.push()
    assert stored_bytes(store) - before == len(data)


def test_same_key_counts_once(corpus, monkeypatch):
    _, manager, _ = corpus
    expected = manager.upload_estimate()
    boundary = state._copy_boundary

    def doubled(root, target):
        stores, runs, coverage = boundary(root, target)
        return [*stores, *stores], runs, coverage

    monkeypatch.setattr(state, "_copy_boundary", doubled)
    assert manager.upload_estimate() == expected
