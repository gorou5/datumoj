"""NAS receipt validation and publication use synthetic local files and storage."""

import hashlib
import json
from datetime import UTC, timedelta
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from datumoj.rikolto import cli
from datumoj.rikolto.core.nas_receipt import RECEIPT_KEY, parse_receipt, publish_receipt, read_receipt
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0


def receipt_bytes(hashes=(), when=T0):
    return json.dumps({"version": 1, "archived_at": when.astimezone(UTC).isoformat(), "sha256": list(hashes)}).encode()


@pytest.mark.parametrize("change", [
    {"path": "/private/raw"}, {"version": True}, {"version": 2},
    {"sha256": ["A" * 64]}, {"sha256": ["a" * 63]}, {"sha256": ["g" * 64]},
    {"sha256": "a" * 64}, {"sha256": [None]}, {"archived_at": None},
    {"archived_at": "2026-01-01T00:00:00"}, {"archived_at": "2026-01-01T09:00:00+09:00"},
    {"archived_at": (T0 + timedelta(seconds=1)).isoformat()},
])
def test_invalid_receipt_rejects_whole_payload(change):
    value = json.loads(receipt_bytes(["b" * 64]))
    value.update(change)
    assert parse_receipt(json.dumps(value).encode(), now=T0) == ("invalid", None)


@pytest.mark.parametrize("data", [b"{", b"[]", b"null", b"\xff", b'{"version":1}',
                                  b'{"version":1,"version":1,"archived_at":"2026-01-01T00:00:00Z","sha256":[]}'])
def test_malformed_or_missing_fields_are_invalid(data):
    assert parse_receipt(data, now=T0) == ("invalid", None)


def test_missing_stale_and_exact_freshness_boundary():
    assert parse_receipt(None, now=T0) == ("missing", None)
    data = receipt_bytes(["a" * 64])
    status, receipt = parse_receipt(data, now=T0 + timedelta(hours=48))
    assert status == "ok" and receipt.sha256 == {"a" * 64} and receipt.archived_at == T0
    assert parse_receipt(data, now=T0 + timedelta(hours=48, microseconds=1)) == ("stale", None)


def test_storage_read_error_disables_early_deletion(monkeypatch):
    store = FakeStateObjectStore()
    monkeypatch.setattr(store, "get", lambda key: (_ for _ in ()).throw(OSError("synthetic")))
    assert read_receipt(store, now=T0) == ("unreadable", None)


def local_raw(tmp_path):
    root = tmp_path / "raw"
    data = b"synthetic NAS archive"
    sha = hashlib.sha256(data).hexdigest()
    path = root / sha[:2] / sha
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    (path.parent / (sha[:2] + "0" * 62)).write_bytes(b"hash mismatch")
    (root / "ff").mkdir()
    (root / "ff" / sha).write_bytes(data)  # Wrong prefix.
    (path.parent / "README").write_text("ignored")
    (path.parent / (sha[:2] + "1" * 62)).symlink_to(path)
    return root, sha


def test_hashes_verified_and_single_put_contains_no_paths(tmp_path, monkeypatch):
    root, sha = local_raw(tmp_path)
    store = FakeStateObjectStore()
    puts = []
    put = store.put
    get = store.get
    monkeypatch.setattr(store, "put", lambda key, data: (puts.append(key), put(key, data)))
    monkeypatch.setattr(store, "get", lambda key: pytest.fail("receipt publisher must not read storage"))
    monkeypatch.setattr(store, "list", lambda key: pytest.fail("receipt publisher must not list storage"))
    value = publish_receipt(store, root, now=T0)
    assert puts == [RECEIPT_KEY]
    assert json.loads(get(RECEIPT_KEY)[0])["sha256"] == [sha]
    assert value == {"count": 1, "archived_at": T0.astimezone(UTC).isoformat()}
    assert str(root) not in json.dumps(value)


def test_cli_uses_same_single_key_boundary(tmp_path, monkeypatch):
    root, sha = local_raw(tmp_path)
    store = FakeStateObjectStore()
    monkeypatch.setattr(cli.wiring, "build_state", lambda root: SimpleNamespace(store=store))
    result = CliRunner().invoke(cli.app, ["nas", "receipt", str(root)])
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert value["count"] == 1 and sha not in result.output
    assert store.list("") == [RECEIPT_KEY]
    assert json.loads(store.get(RECEIPT_KEY)[0]) == {
        "version": 1, "archived_at": value["archived_at"], "sha256": [sha],
    }


def test_missing_local_directory_never_replaces_receipt(tmp_path):
    store = FakeStateObjectStore()
    store.put(RECEIPT_KEY, b"existing receipt")
    with pytest.raises(ValueError):
        publish_receipt(store, tmp_path / "missing", now=T0)
    assert store.get(RECEIPT_KEY)[0] == b"existing receipt"
