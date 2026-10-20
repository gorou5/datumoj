"""Tests for the filesystem raw blob store."""

import hashlib
import stat
from pathlib import Path

import pytest

from datumoj.core.rawstore import FileRawStore
from datumoj.testing.contract_suites import RawStoreSuite


class TestFileRawStore(RawStoreSuite):
    @pytest.fixture
    def store(self, tmp_path):
        return FileRawStore(tmp_path)

    def corrupt(self, store, sha256):
        path = store.path_for(sha256)
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        path.write_bytes(b"tampered")

    def test_put_streams_more_than_ten_megabytes(self, store):
        length = 11 * 1024 * 1024 + 17

        class RepeatedByteStream:
            def __init__(self):
                self.remaining = length
                self.reads = 0

            def read(self, size):
                self.reads += 1
                block_size = min(size, self.remaining)
                self.remaining -= block_size
                return b"x" * block_size

        source = RepeatedByteStream()
        digest = store.put(source)
        assert digest == hashlib.sha256(b"x" * length).hexdigest()
        assert source.reads > 10
        assert store.path_for(digest).stat().st_size == length

    def test_root_from_environment_and_explicit_root(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DATUMOJ_DATA_DIR", raising=False)
        monkeypatch.chdir(tmp_path)
        assert FileRawStore().root == Path("data")

        env_root = tmp_path / "from-env"
        monkeypatch.setenv("DATUMOJ_DATA_DIR", str(env_root))
        assert FileRawStore().put(b"env") == hashlib.sha256(b"env").hexdigest()
        assert FileRawStore().path_for(hashlib.sha256(b"env").hexdigest()).is_relative_to(env_root)

        explicit_root = tmp_path / "explicit"
        store = FileRawStore(explicit_root)
        digest = store.put(b"explicit")
        assert store.path_for(digest).is_relative_to(explicit_root)

    def test_blob_is_read_only(self, store):
        digest = store.put(b"immutable")
        mode = store.path_for(digest).stat().st_mode
        assert mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH) == 0

    def test_existing_blob_is_not_rewritten(self, store):
        digest = store.put(b"same")
        self.corrupt(store, digest)
        assert store.put(b"same") == digest
        assert store.path_for(digest).read_bytes() == b"tampered"

