"""Local-disk contract, process races and path/I/O failure boundaries."""

import ctypes
import multiprocessing
import os
from pathlib import Path

import pytest

from datumoj.rikolto.core import state_fs
from datumoj.rikolto.core.contracts import PreconditionFailed
from datumoj.rikolto.core.state_fs import DirStateObjectStore
from datumoj.rikolto.core.state_s3 import probe_conditional_writes
from datumoj.rikolto.testing.contract_suites import StateObjectStoreSuite


@pytest.fixture
def state_store(tmp_path):
    return DirStateObjectStore(tmp_path / "state")


class TestDirStateObjectStore(StateObjectStoreSuite):
    @pytest.fixture
    def state_store(self, tmp_path):
        return DirStateObjectStore(tmp_path / "state")


def test_probe_and_same_byte_rewrites(state_store):
    assert probe_conditional_writes(state_store) == {"supported": True, "check": "complete"}
    assert state_store.list() == []
    first = state_store.put_if_absent("key", b"same")
    second = state_store.put_if_match("key", b"same", first)
    state_store.put("key", b"same")
    third = state_store.get("key")[1]
    assert len({first, second, third}) == 3
    state_store.delete_if_match("key", third)
    fourth = state_store.put_if_absent("key", b"same")
    assert fourth not in {first, second, third}
    state_store.put("key/child", b"child")
    assert state_store.list("key") == ["key", "key/child"]


def _compete(root, barrier, results, operation, etag):
    store = DirStateObjectStore(root)
    barrier.wait(timeout=15)
    try:
        if operation == "create":
            store.put_if_absent("race", b"winner")
        elif operation == "update":
            store.put_if_match("race", b"updated", etag)
        else:
            store.delete_if_match("race", etag)
        results.put("won")
    except PreconditionFailed:
        results.put("lost")


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_two_processes_have_one_winner(state_store, operation, monkeypatch):
    # pytest importlib mode does not otherwise expose this module to spawn.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    etag = state_store.put_if_absent("race", b"initial") if operation != "create" else None
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(2), context.Queue()
    processes = [context.Process(target=_compete, args=(state_store.root, barrier, results, operation, etag))
                 for _ in range(2)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=25)
            assert process.exitcode == 0
        assert sorted(results.get(timeout=5) for _ in processes) == ["lost", "won"]
        assert state_store.exists("race") == (operation != "delete")
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()


@pytest.mark.parametrize("key", ["../escape", "/absolute", "a/../b", "a/./b", "a//b", "a\\b", ""])
def test_invalid_keys_rejected(state_store, key):
    for operation in (lambda: state_store.get(key), lambda: state_store.put(key, b"data"),
                      lambda: state_store.put_if_absent(key, b"data"),
                      lambda: state_store.put_if_match(key, b"data", "etag"),
                      lambda: state_store.delete_if_match(key, "etag"), lambda: state_store.exists(key)):
        with pytest.raises(ValueError):
            operation()
    if key:
        with pytest.raises(ValueError):
            state_store.list(key)


@pytest.mark.parametrize("name", ["k-escape", "k-key/value", "k-key/lock"])
def test_symlinks_cannot_escape(state_store, tmp_path, name):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"untouched")
    (state_store.root / "k-key").mkdir()
    (state_store.root / name).symlink_to(outside if name == "k-escape" else sentinel)
    key = "escape/child" if name == "k-escape" else "key"
    for operation in (lambda: state_store.put(key, b"unexpected"),
                      lambda: state_store.put_if_absent(key, b"unexpected"),
                      lambda: state_store.put_if_match(key, b"unexpected", "etag"),
                      lambda: state_store.delete_if_match(key, "etag")):
        with pytest.raises(OSError):
            operation()
    assert sentinel.read_bytes() == b"untouched"
    if not name.endswith("lock"):
        with pytest.raises(OSError):
            state_store.get(key)
        with pytest.raises(OSError):
            state_store.list()


def test_symlink_root_rejected(tmp_path):
    (tmp_path / "actual").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "actual")
    with pytest.raises(ValueError):
        DirStateObjectStore(tmp_path / "link")


def test_replace_io_failure_preserves_version(state_store, monkeypatch):
    etag = state_store.put_if_absent("key", b"original")

    def fail(*args, **kwargs):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="synthetic disk failure"):
        state_store.put_if_match("key", b"replacement", etag)
    assert state_store.get("key") == (b"original", etag)
    assert not list(state_store.root.rglob("tmp-*"))


@pytest.mark.parametrize(("platform", "kind", "flags"), [
    ("linux", 0x6969, 0),  # NFS
    ("linux", 0xFF534D42, 0),  # CIFS
    ("linux", 0x65735546, 0),  # FUSE may be remote
    ("darwin", b"smbfs", 0),
    ("darwin", b"nfs", 0),
    ("darwin", b"macfuse", 0x1000),
])
def test_network_mounts_rejected(tmp_path, monkeypatch, platform, kind, flags):
    class Libc:
        @staticmethod
        def fstatfs(fd, pointer):
            if platform == "linux":
                ctypes.cast(pointer, ctypes.POINTER(ctypes.c_long))[0] = kind
            else:
                address = ctypes.addressof(pointer._obj)
                ctypes.c_uint32.from_address(address + 64).value = flags
                ctypes.memmove(address + 72, kind, len(kind))
            return 0

    monkeypatch.setattr(state_fs.sys, "platform", platform)
    monkeypatch.setattr(state_fs.ctypes, "CDLL", lambda *args, **kwargs: Libc())
    with pytest.raises(ValueError, match="本机磁盘|网络"):
        DirStateObjectStore(tmp_path / "network")


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_probe_public_cleanup_on_failure(state_store, monkeypatch, cleanup_fails):
    original = state_store.put_if_absent

    def put_then_fail(key, data):
        original(key, data)
        raise OSError("synthetic probe failure after write")

    def fail_delete(*args):
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(state_store, "put_if_absent", put_then_fail)
    if cleanup_fails:
        monkeypatch.setattr(state_store, "delete_if_match", fail_delete)
    result = probe_conditional_writes(state_store)
    assert not result["supported"]
    assert result.get("cleanup_failed", False) == cleanup_fails
    assert bool(state_store.list()) == cleanup_fails
