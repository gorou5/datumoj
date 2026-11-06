"""State boundaries, incremental transfer, publication failures and verified restore."""

import json
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import duckdb
import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli, wiring
from datumoj.rikolto.core.contracts import PreconditionFailed
from datumoj.rikolto.core.models import PublishState, StateWriter
from datumoj.rikolto.core.schedule import _locked
from datumoj.rikolto.core.state import StateError, StateManager, local_write
from datumoj.rikolto.testing.fakes import FakeParser, FakeRegistry, FakeStateObjectStore
from datumoj.rikolto.testing.samples import make_snapshot, make_spec


class CountingStore(FakeStateObjectStore):
    def __init__(self):
        super().__init__()
        self.gets = []
        self.puts = []
        self.lists = []
        self.fail = None

    def get(self, key):
        self.gets.append(key)
        return super().get(key)

    def list(self, prefix):
        self.lists.append(prefix)
        return super().list(prefix)

    def put_if_absent(self, key, data):
        if self.fail and self.fail(key):
            raise OSError("synthetic failure")
        self.puts.append(key)
        return super().put_if_absent(key, data)

    def reset(self):
        self.gets.clear()
        self.puts.clear()
        self.lists.clear()


def close(deps):
    for name in ("manifest", "chunks", "keyword", "catalog", "change_log"):
        getattr(deps, name).close()


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "source"
    deps = wiring.build(root, {"registry": FakeRegistry([make_spec()]), "vectors": None,
                               "parsers": {("fake", "text/plain"): FakeParser}})
    body = "題名\n\n状態代次のテスト".encode()
    snapshot = make_snapshot(data=body)
    deps.raw.put(body)
    deps.manifest.record_snapshot(snapshot)
    assert deps.parse_pending(deps.pipeline_deps).parsed == [snapshot.snapshot_id]
    yield root, deps, snapshot
    close(deps)


@pytest.fixture
def state(corpus):
    root, _, _ = corpus
    store = CountingStore()
    return StateManager(store, root, writer=StateWriter(kind="local", run_id="synthetic-run")), store


def test_no_change_push_lists_once_without_content_download_or_raw_read(state, corpus, monkeypatch):
    manager, store = state
    first = manager.push()
    assert first.generation == 1 and first.vector_coverage is None
    assert {c.name for c in first.stores} == {
        "manifest.sqlite", "manifest.sqlite.blocks/0", "chunks.sqlite", "chunks.sqlite.blocks/0",
        "catalog.sqlite", "catalog.sqlite.blocks/0", "changes.sqlite", "changes.sqlite.blocks/0",
        "observations.duckdb",
    }
    assert all(c.format == ("sqlite-block-v1" if ".blocks/" in c.name else "sqlite-blocks-v1")
               for c in first.stores if c.name != "observations.duckdb")
    store.reset()
    original = type(manager.data_dir).read_bytes

    def read(path):
        if "raw" in path.parts:
            pytest.fail("existing raw must not be read, hashed or copied")
        return original(path)

    monkeypatch.setattr(type(manager.data_dir), "read_bytes", read)
    second = manager.push()
    assert second.generation == 2
    assert first.stores == second.stores
    assert store.lists == ["copies/sha256/", "raw/"]
    assert not any(k.startswith(("copies/", "raw/")) for k in store.gets)
    assert store.puts == ["generations/2.json"]  # pointer is a CAS, not an unconditional PUT


def test_local_and_remote_restore(state, corpus, tmp_path, monkeypatch):
    manager, store = state
    candidate = manager.push()
    local = tmp_path / "local"
    report = manager.restore(local)
    assert report["verified"] and report["sampled_chunks"] == 2
    assert report["raw_verification"] == "sha256"
    assert (local / "raw" / corpus[2].sha256[:2] / corpus[2].sha256).exists()
    store.reset()
    remote = tmp_path / "remote"
    report = manager.restore(remote, raw="remote")
    assert report["raw_verification"] == "remote_keys_only"
    assert not (remote / "raw").exists()
    assert store.lists == ["raw/"]
    assert not any(key.startswith("raw/") for key in store.gets)
    assert report["copies"] == len(candidate.stores)
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "synthetic-id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "synthetic-secret")
    monkeypatch.setenv("R2_ENDPOINT", "https://synthetic.invalid")
    monkeypatch.setenv("DATUMOJ_S3_BUCKET", "synthetic-bucket")
    deps = wiring.build(remote, {"vectors": None})
    try:
        from datumoj.rikolto.core.rawstore_s3 import S3RawStore
        assert isinstance(deps.raw, S3RawStore)
    finally:
        close(deps)
        deps.raw._client.close()


def test_remote_missing_raw_fails_before_target_is_usable(state, corpus, tmp_path):
    manager, store = state
    manager.push()
    key = f"raw/{corpus[2].sha256[:2]}/{corpus[2].sha256}"
    _, etag = store.get(key)
    store.delete_if_match(key, etag)
    store.reset()
    target = tmp_path / "restore"
    target.mkdir()
    with pytest.raises(StateError, match="远端缺少"):
        manager.restore(target, raw="remote")
    assert not list(target.iterdir())
    assert not any(key.startswith("raw/") for key in store.gets)


@pytest.mark.parametrize("failure", ["copy", "manifest", "cas"])
def test_failed_generations_never_become_restore_entries_and_next_push_recovers(state, tmp_path, failure):
    manager, store = state
    first = manager.push()
    previous = store.get("current.json")
    # Force a changed small object so the copy fault really occurs mid-upload.
    (manager.data_dir / "control.json").write_text('{"enabled":false,"sources":{}}')
    if failure == "copy":
        store.fail = lambda key: key.startswith("copies/")
    elif failure == "manifest":
        store.fail = lambda key: key == "generations/2.json"
    else:
        original = store.put_if_match

        def lose(key, data, etag):
            raise PreconditionFailed("another writer won")

        store.put_if_match = lose
    with pytest.raises((StateError, PreconditionFailed)):
        manager.push()
    assert store.get("current.json") == previous
    assert [g.generation for g in manager.list()] == [1]
    with pytest.raises(StateError):
        manager.restore(tmp_path / "invalid", generation=2)
    store.fail = None
    if failure == "cas":
        store.put_if_match = original
    second = manager.push()
    assert second.generation == 2
    assert second.previous_generation == first.generation
    assert manager.restore(tmp_path / "valid")["verified"]


def test_lease_check_and_candidate_recheck(state):
    manager, store = state
    manager.push()
    previous = store.get("current.json")

    def lost():
        raise RuntimeError("lease lost with sensitive SDK detail")

    manager.before_publish = lost
    with pytest.raises(StateError):
        manager.push()
    assert store.get("current.json") == previous
    manager.before_publish = None
    orphan = store.get("generations/2.json")[0]
    continued = manager.push()
    assert continued.writer.run_id == "synthetic-run"
    assert store.get("generations/2.json")[0] == orphan


@pytest.mark.parametrize("part", ["copy", "raw"])
def test_tampered_object_is_never_handed_out(state, corpus, tmp_path, part):
    manager, store = state
    candidate = manager.push()
    key = candidate.stores[0].key if part == "copy" else f"raw/{corpus[2].sha256[:2]}/{corpus[2].sha256}"
    store.put(key, b"corruption")
    target = tmp_path / "restore"
    with pytest.raises(StateError, match="校验失败"):
        manager.restore(target)
    assert not target.exists()
    assert not list(tmp_path.glob(".datumoj-restore-*"))


def test_active_source_lock_refuses_snapshot_stale_file_is_ok(state):
    manager, _ = state
    with _locked(manager.data_dir / "locks" / "jp-test.lock"), pytest.raises(StateError, match="正在运行的抓取"):
        manager.push()
    assert manager.push().generation == 1


def test_local_write_gate_refuses_snapshot_and_wiring_waits(state, corpus):
    manager, _ = state
    started = Event()
    finished = Event()
    with ThreadPoolExecutor(1) as pool:
        with local_write(manager.data_dir):
            with pytest.raises(StateError, match="本地写入"):
                manager.push()

            def build():
                started.set()
                deps = wiring.build(manager.data_dir, {"vectors": None})
                finished.set()
                close(deps)

            future = pool.submit(build)
            assert started.wait(2)
            assert not finished.wait(0.05)
        future.result(timeout=10)
    assert finished.is_set()


def test_backup_includes_uncheckpointed_wal(state, corpus, tmp_path):
    manager, _ = state
    deps = corpus[1]
    body = b"new raw in WAL"
    snap = make_snapshot(data=body, url="https://example.go.jp/second.txt", snapshot_id="wal-snapshot")
    deps.raw.put(body)
    deps.manifest.record_snapshot(snap)
    assert (manager.data_dir / "manifest.sqlite-wal").stat().st_size > 0
    manager.push()
    target = tmp_path / "wal-restore"
    manager.restore(target)
    with sqlite3.connect(target / "manifest.sqlite") as db:
        assert db.execute("SELECT snapshot_id FROM snapshots WHERE snapshot_id='wal-snapshot'").fetchone()


def test_all_sqlite_writers_are_reserved_before_first_backup(state, monkeypatch):
    manager, _ = state
    from datumoj.rikolto.core import state as module
    original = module._check_schema

    def check(root):
        # Independent SQLite connections cannot mutate any later database while
        # an earlier one is being copied, including WAL-enabled manifest.
        for name in module._SQLITE:
            db = sqlite3.connect(root / name, timeout=0)
            try:
                with pytest.raises(sqlite3.OperationalError, match="locked"):
                    db.execute("BEGIN IMMEDIATE")
            finally:
                db.close()
        original(root)

    monkeypatch.setattr(module, "_check_schema", check)
    assert manager.push().generation == 1


def test_unfinished_local_publication_is_rejected(state, corpus):
    corpus[1].manifest.record_publish_state(corpus[2].snapshot_id, PublishState.staged)
    with pytest.raises(StateError, match="未完成"):
        state[0].push()
    assert state[1].get("current.json") is None


def test_failed_snapshot_is_terminal_and_can_be_pushed(state, corpus, tmp_path):
    _, deps, _ = corpus
    failed = make_snapshot(data=b"synthetic failed parse", snapshot_id="synthetic-failed")
    deps.raw.put(b"synthetic failed parse")
    deps.manifest.record_snapshot(failed)
    deps.manifest.record_publish_state(failed.snapshot_id, PublishState.failed)
    generation = state[0].push()
    assert generation.generation == 1
    target = tmp_path / "failed-restored"
    assert state[0].restore(target)["verified"]
    with sqlite3.connect(target / "manifest.sqlite") as db:
        assert db.execute("SELECT state FROM publish_states WHERE snapshot_id=?",
                          (failed.snapshot_id,)).fetchone() == ("failed",)


@pytest.mark.parametrize("name,fmt", [("../escape", "bytes"), ("/absolute", "sqlite"),
                                      ("manifest.sqlite", "unknown"), ("arbitrary", "bytes")])
def test_unsafe_manifest_paths_formats_are_rejected(state, tmp_path, name, fmt):
    manager, store = state
    candidate = manager.push().model_dump(mode="json")
    candidate["stores"][0].update(name=name, format=fmt)
    store.put("generations/1.json", json.dumps(candidate).encode())
    with pytest.raises(StateError):
        manager.restore(tmp_path / "target")
    assert not (tmp_path / "target").exists()


def test_nonempty_restore_target_is_untouched(state, tmp_path):
    manager, _ = state
    manager.push()
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep").write_bytes(b"keep")
    with pytest.raises(StateError):
        manager.restore(target)
    assert (target / "keep").read_bytes() == b"keep"


def test_raw_available_remotely_does_not_need_local_copy(state, corpus, tmp_path):
    manager, store = state
    manager.push()
    shutil.rmtree(manager.data_dir / "raw")
    store.reset()
    assert manager.push().generation == 2
    assert not any(key.startswith("raw/") for key in store.gets + store.puts)
    assert manager.restore(tmp_path / "restored")["verified"]


def test_manifest_raw_missing_in_both_places_stops_publication(state, corpus):
    manager, store = state
    shutil.rmtree(manager.data_dir / "raw")
    with pytest.raises(StateError, match="均缺失"):
        manager.push()
    assert store.get("current.json") is None


def test_duckdb_tables_are_logically_copied(state, tmp_path):
    manager, _ = state
    with duckdb.connect(str(manager.data_dir / "observations.duckdb")) as db:
        db.execute("CREATE TABLE important AS SELECT 42 AS answer")
    candidate = manager.push()
    assert next(c.format for c in candidate.stores if c.name == "observations.duckdb") == "duckdb"
    assert manager.push().stores == candidate.stores
    target = tmp_path / "duckdb"
    manager.restore(target)
    with duckdb.connect(str(target / "observations.duckdb"), read_only=True) as db:
        assert db.execute("SELECT answer FROM important").fetchall() == [(42,)]


def test_duckdb_observations_view_is_rebuilt(state, tmp_path):
    manager, _ = state
    with duckdb.connect(str(manager.data_dir / "observations.duckdb")) as db:
        db.execute("CREATE VIEW observations AS SELECT 99 AS old_machine_only")
    candidate = manager.push()
    assert next(c.format for c in candidate.stores if c.name == "observations.duckdb") == "duckdb-empty"
    target = tmp_path / "views"
    manager.restore(target)
    with duckdb.connect(str(target / "observations.duckdb"), read_only=True) as db:
        assert not db.execute("SELECT table_name FROM information_schema.views "
                              "WHERE table_schema='main' AND table_catalog=current_database()").fetchall()


def test_publish_checks_exact_manifest_after_callback(state):
    manager, store = state
    manager.push()
    previous = store.get("current.json")

    def replace():
        store.put("generations/2.json", b"invalid")

    manager.before_publish = replace
    with pytest.raises(StateError):
        manager.push()
    assert store.get("current.json") == previous


def test_ambiguous_cas_success_is_reconciled(state):
    manager, store = state
    manager.push()
    original = store.put_if_match

    def timeout(key, data, etag):
        original(key, data, etag)
        raise TimeoutError("response lost after commit")

    store.put_if_match = timeout
    assert manager.push().generation == 2
    assert [g.generation for g in manager.list()] == [2, 1]


def test_real_competing_pointer_write_never_refreshes_etag(state):
    manager, store = state
    manager.push()
    original = store.put_if_match
    calls = []

    def compete(key, data, etag):
        calls.append(etag)
        # Another client changes the version after our original read. Keep its
        # generation at one so the next independent invocation can resume two.
        store.put("current.json", b'{"generation": 1}')
        return original(key, data, etag)

    store.put_if_match = compete
    with pytest.raises(PreconditionFailed):
        manager.push()
    assert len(calls) == 1
    assert json.loads(store.get("current.json")[0]) == {"generation": 1}
    store.put_if_match = original
    assert manager.push().generation == 2


def test_orphan_must_validate_before_continuation(state):
    manager, store = state
    manager.push()
    previous = store.get("current.json")
    manager.before_publish = lambda: (_ for _ in ()).throw(RuntimeError("lost lease"))
    with pytest.raises(StateError):
        manager.push()
    orphan = json.loads(store.get("generations/2.json")[0])
    store.put(orphan["stores"][0]["key"], b"corrupted orphan")
    manager.before_publish = None
    with pytest.raises(StateError, match="校验失败"):
        manager.push()
    assert store.get("current.json") == previous


def test_successful_runs_and_small_files_share_snapshot_boundary(state):
    manager, _ = state
    row = {"source_id": "jp-test", "run_id": "success", "state": "final", "status": "completed",
           "exit_status": 0, "ended_at": "2026-10-20T01:00:00+00:00", "failures": {}}
    (manager.data_dir / "runs.jsonl").write_text(json.dumps(row) + "\n")
    (manager.data_dir / "control.json").write_bytes(b'{"enabled":false,"sources":{}}')
    candidate = manager.push()
    assert candidate.last_successful_runs["jp-test"].run_id == "success"
    assert candidate.last_successful_runs["jp-test"].finished_at <= candidate.created_at
    assert {"runs.jsonl", "control.json"}.issubset({c.name for c in candidate.stores})


def test_corrupt_sqlite_with_matching_object_hash_still_fails_schema(state, tmp_path):
    from hashlib import sha256

    manager, store = state
    candidate = manager.push().model_dump(mode="json")
    invalid = b"this is not a database"
    copy = next(c for c in candidate["stores"] if c["name"] == "manifest.sqlite")
    digest = sha256(invalid).hexdigest()
    copy.update(key=f"copies/sha256/{digest}", sha256=digest, size_bytes=len(invalid))
    store.put(copy["key"], invalid)
    store.put("generations/1.json", json.dumps(candidate).encode())
    with pytest.raises(StateError):
        manager.restore(tmp_path / "bad-schema")
    assert not (tmp_path / "bad-schema").exists()


def test_initial_copy_failure_leaves_no_manifest_or_pointer(state):
    manager, store = state
    count = 0

    def fail_after_two(key):
        nonlocal count
        if key.startswith("copies/"):
            count += 1
            return count == 3
        return False

    store.fail = fail_after_two
    with pytest.raises(StateError):
        manager.push()
    assert len(store.list("copies/")) == 2
    assert store.get("generations/1.json") is None and store.get("current.json") is None
    store.fail = None
    assert manager.push().generation == 1


def test_cli_json_and_errors_hide_configuration(state, monkeypatch, tmp_path, caplog):
    manager, _ = state
    monkeypatch.setattr(wiring, "build_state", lambda root: manager)
    runner = CliRunner()
    pushed = runner.invoke(cli.app, ["state", "push", "--data-dir", str(manager.data_dir)])
    assert pushed.exit_code == 0, pushed.output
    assert json.loads(pushed.output)["generation"] == 1
    listed = runner.invoke(cli.app, ["state", "list"])
    assert json.loads(listed.output)[0]["copies"] > 0
    restored = runner.invoke(cli.app, ["state", "restore", "--to", str(tmp_path / "restore"), "--raw", "remote"])
    assert restored.exit_code == 0, restored.output
    assert json.loads(restored.output)["raw_mode"] == "remote"
    secrets = ["synthetic-state-id", "synthetic-state-secret", "https://synthetic.invalid", "synthetic-bucket"]

    def fail(root):
        raise RuntimeError(" ".join(secrets))

    monkeypatch.setattr(wiring, "build_state", fail)
    failed = runner.invoke(cli.app, ["state", "push"])
    assert failed.exit_code == 1
    assert "error" in json.loads(failed.output)
    output = pushed.output + listed.output + restored.output + failed.output + caplog.text
    assert all(secret not in output for secret in secrets)


@pytest.mark.parametrize("workers", [0, -1, 1.5, "4", True, None])
def test_transfer_workers_must_be_positive_integer(tmp_path, workers):
    with pytest.raises(ValueError, match="transfer_workers"):
        StateManager(CountingStore(), tmp_path, transfer_workers=workers)


def test_parallel_transfers_match_serial_bytes(corpus, tmp_path, monkeypatch):
    from datetime import datetime

    from datumoj.rikolto.core import state as state_module
    from datumoj.rikolto.testing.samples import T0

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return T0

    monkeypatch.setattr(state_module, "datetime", FrozenDateTime)
    # Force multi-block databases, including repeated content-addressed blocks.
    monkeypatch.setattr(state_module, "_BLOCK_SIZE", 4096)
    root, _, _ = corpus
    writer = StateWriter(kind="local", run_id="synthetic-parallel")
    trees, objects = [], []
    for workers in (1, 4):
        store = CountingStore()
        manager = StateManager(store, root, writer=writer, transfer_workers=workers)
        candidate = manager.push()
        assert any(c.name.endswith(".blocks/1") for c in candidate.stores)
        objects.append({key: store.get(key)[0] for key in store.list("")})
        assert {"total", "stage", "validate", "upload_copies", "upload_raw", "sample_provenance",
                "write_generation", "publish", "cleanup"} <= manager.last_timings.keys()
        target = tmp_path / f"restore-{workers}"
        manager.restore(target)
        trees.append({str(path.relative_to(target)): path.read_bytes()
                      for path in target.rglob("*") if path.is_file()})
        assert {"total", "download_copies", "download_raw", "validate", "sample_provenance", "cleanup"} \
            <= manager.last_timings.keys()
        assert all(type(key) is str and type(value) is float and value >= 0
                   for key, value in manager.last_timings.items())
    assert objects[0] == objects[1]
    assert trees[0] == trees[1]


def test_parallel_upload_puts_each_copy_key_once(corpus, monkeypatch):
    from datumoj.rikolto.core import state as state_module

    # Small blocks give repeated content-addressed keys within one push.
    monkeypatch.setattr(state_module, "_BLOCK_SIZE", 4096)
    root, _, _ = corpus
    store = CountingStore()
    candidate = StateManager(store, root, transfer_workers=4).push()
    keys = [c.key for c in candidate.stores if c.format != state_module._SQLITE_BLOCKS]
    assert len(keys) > len(set(keys))
    copies = [key for key in store.puts if key.startswith("copies/")]
    assert len(copies) == len(set(copies)) == len(set(keys))


@pytest.mark.parametrize("namespace", ["copies/", "raw/"])
def test_parallel_upload_failure_waits_and_does_not_publish(state, namespace):
    import hashlib
    from threading import Lock

    manager, store = state
    manager.push()
    previous = store.get("current.json")
    manager.transfer_workers = 4
    # Add raw objects to exercise parallel failure independently of copy failure.
    for index in range(4):
        data = f"synthetic new raw {index}".encode()
        sha = hashlib.sha256(data).hexdigest()
        path = manager.data_dir / "raw" / sha[:2] / sha
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    (manager.data_dir / "control.json").write_text('{"enabled":false,"sources":{}}')
    # New copies can include the changed control alone; raw has four parallel jobs.
    original = store.put_if_absent
    entered, finished = [], []
    lock = Lock()
    gate = Event()

    def put(key, data):
        if not key.startswith(namespace):
            return original(key, data)
        with lock:
            entered.append(key)
            first = len(entered) == 1
        if first:
            gate.set()
            raise OSError("synthetic private upload failure")
        assert gate.wait(5)
        try:
            return original(key, data)
        finally:
            with lock:
                finished.append(key)

    store.put_if_absent = put
    with pytest.raises(StateError, match="^状态推送失败$"):
        manager.push()
    assert entered and set(finished) == set(entered[1:])
    assert store.get("generations/2.json") is None
    assert store.get("current.json") == previous
    assert "cleanup" in manager.last_timings


def test_transfers_actually_overlap(state, tmp_path):
    from threading import Barrier, Lock

    manager, store = state
    manager.transfer_workers = 4
    barrier = Barrier(4, timeout=5)
    lock = Lock()
    seen = []
    original_put, original_get = store.put_if_absent, store.get

    def overlap(key):
        if key.startswith("copies/"):
            with lock:
                seen.append(key)
                wait = len(seen) <= 4
            if wait:
                barrier.wait()

    def put(key, data):
        overlap(key)
        return original_put(key, data)

    def get(key):
        overlap(key)
        return original_get(key)

    store.put_if_absent = put
    manager.push()
    assert len(seen) >= 4
    seen.clear()
    store.get = get
    manager.restore(tmp_path / "parallel")
    assert len(seen) >= 4


def test_timings_include_slow_cleanup_and_reset(state, tmp_path, monkeypatch):
    from datumoj.rikolto.core import state as state_module

    manager, _ = state
    ticks = [0.0]
    cleanup = state_module.tempfile.TemporaryDirectory.cleanup

    def slow_cleanup(directory):
        ticks[0] += 120
        cleanup(directory)

    monkeypatch.setattr(state_module.time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(state_module.tempfile.TemporaryDirectory, "cleanup", slow_cleanup)
    manager.push()
    assert manager.last_timings["cleanup"] == manager.last_timings["total"] == 120
    manager.restore(tmp_path / "restored")
    assert manager.last_timings["cleanup"] == manager.last_timings["total"] == 120
    assert "upload_copies" not in manager.last_timings


def test_parallel_orphan_continuation_keeps_remote_raw_mode(state, monkeypatch):
    manager, store = state
    manager.transfer_workers = 4
    manager.before_publish = lambda: (_ for _ in ()).throw(OSError("synthetic CAS boundary failure"))
    with pytest.raises(StateError):
        manager.push()
    orphan = store.get("generations/1.json")[0]
    assert store.get("current.json") is None
    (manager.data_dir / "control.json").write_text('{"enabled":false,"sources":{}}')
    manager.before_publish = None
    store.reset()
    candidate = manager.push()
    assert candidate.model_dump_json().encode() == orphan
    assert store.puts == ["current.json"]
    assert not any(key.startswith("raw/") for key in store.gets)
    assert manager.last_timings["download_copies"] >= 0
