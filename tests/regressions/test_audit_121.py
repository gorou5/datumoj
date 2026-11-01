"""R2 ignores DELETE If-Match; leases must fence every change with conditional PUT."""

import ast
from datetime import timedelta
from pathlib import Path

import pytest
from moto import mock_aws

from datumoj.rikolto.core.lease import LEASE_KEY, LeaseError, LeaseManager
from datumoj.rikolto.core.state_s3 import S3StateObjectStore
from datumoj.rikolto.core.storage import StorageConfig
from datumoj.rikolto.testing.samples import T0


def test_production_code_never_calls_conditional_delete():
    root = Path(__file__).resolve().parents[2] / "src/datumoj/rikolto"
    excluded = {"core/state_s3.py", "core/state_fs.py", "core/contracts.py"}
    calls = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative in excluded or relative.startswith("testing/"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Attribute) and node.func.attr == "delete_if_match"
                or isinstance(node.func, ast.Name) and node.func.id == "delete_if_match"
            ):
                calls.append(f"{relative}:{node.lineno}")
    assert calls == []


@pytest.mark.parametrize("race", [False, True])
def test_old_owner_cannot_remove_successor_when_delete_ignores_match(monkeypatch, race):
    monkeypatch.setenv("DATUMOJ_S3_ACCESS_KEY_ID", "synthetic-audit-121-access")
    monkeypatch.setenv("DATUMOJ_S3_SECRET_ACCESS_KEY", "synthetic-audit-121-secret")
    with mock_aws():
        store = S3StateObjectStore(StorageConfig(
            s3_endpoint="https://s3.amazonaws.com", s3_bucket="synthetic-audit-121",
            s3_region="us-east-1", s3_prefix="synthetic-project",
        ))
        try:
            store._client.create_bucket(Bucket="synthetic-audit-121")
            delete = store._client.delete_object
            deletes = []

            def ignores_match(**params):
                deletes.append(params.copy())
                params.pop("IfMatch", None)
                return delete(**params)

            monkeypatch.setattr(store._client, "delete_object", ignores_match)
            clock = [T0]
            manager = LeaseManager(store, now=lambda: clock[0], ttl=timedelta(minutes=1))
            owner = manager.acquire("local/old")
            clock[0] = owner.expires_at
            successor_objects = []

            def takeover():
                successor = manager.acquire("nas/new", force=True)
                successor_objects.append(store.get(LEASE_KEY))
                return successor

            if race:
                owned = manager._owned

                def interleave(*args, **kwargs):
                    found = owned(*args, **kwargs)
                    takeover()
                    return found

                with monkeypatch.context() as patch:
                    patch.setattr(manager, "_owned", interleave)
                    with pytest.raises(LeaseError, match="租约释放竞争失败"):
                        manager.release(owner.holder, owner.token)
            else:
                takeover()
                with pytest.raises(LeaseError, match="不属于本次持有者"):
                    manager.release(owner.holder, owner.token)
            assert store.get(LEASE_KEY) == successor_objects[0]
            with pytest.raises(LeaseError, match="不属于本次持有者"):
                manager.renew(owner.holder, owner.token)
            assert store.get(LEASE_KEY) == successor_objects[0]
            assert manager.status()["active"] is True
            assert deletes == []
        finally:
            store._client.close()
