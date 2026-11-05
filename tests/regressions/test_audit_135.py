"""Offline bulk GC boundaries, per-item accounting and safe continuation."""

import pytest

from datumoj.rikolto.core.lease import LeaseError
from datumoj.rikolto.core.state_s3 import S3StateObjectStore, delete_retired_objects
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0
from tests.regressions.test_audit_131 import DAY, cleanup, publish, raw_key, record
from tests.regressions.test_audit_131 import archive as archive  # noqa: PLC0414 -- Shared offline fixture.


class CountingS3Client:
    """DeleteObjects wire responses backed by the existing in-memory store."""

    def __init__(self, backend):
        self.backend = backend
        self.calls = []
        self.errors = set()
        self.after_delete = lambda: None

    def delete_objects(self, *, Bucket, Delete):
        assert Bucket == "synthetic-bucket"
        assert Delete["Quiet"] is False
        keys = [obj["Key"] for obj in Delete["Objects"]]
        assert 1 <= len(keys) <= 200
        self.calls.append(keys)
        response = {"Deleted": [], "Errors": []}
        for full_key in keys:
            assert full_key.startswith("synthetic/state/")
            key = full_key.removeprefix("synthetic/state/")
            if key in self.errors:
                response["Errors"].append({"Key": full_key, "Code": "AccessDenied",
                                           "Message": "synthetic private detail"})
                continue
            found = self.backend.get(key)
            if found is not None:
                self.backend.delete_if_match(key, found[1])
            response["Deleted"].append({"Key": full_key})
        self.after_delete()
        return response


def bulk_store(backend):
    # Construct only the adapter, with no credential lookup or network client.
    store = S3StateObjectStore.__new__(S3StateObjectStore)
    store._bucket = "synthetic-bucket"
    store._prefix = "synthetic/state"
    store._listed_keys = {obj.key for obj in backend.list_objects("")}
    store._client = CountingS3Client(backend)
    return store


def test_thousand_objects_need_five_requests_and_five_lease_checks():
    backend = FakeStateObjectStore()
    for index in range(1000):
        backend.put(f"raw/{index:04}", b"synthetic")
    backend.put("raw/unplanned", b"preserve")
    backend.put("current.json", b"preserve")
    store = bulk_store(backend)
    objects = [obj for obj in backend.list_objects("raw/") if obj.key != "raw/unplanned"]
    checks = []

    def check():
        checks.append(len(store._client.calls))

    batches = list(delete_retired_objects(store, objects, check))
    assert len(store._client.calls) == len(checks) == 5
    assert checks == list(range(5))
    assert sum(len(deleted) for deleted, _ in batches) == 1000
    assert sum(failures for _, failures in batches) == 0
    assert backend.list("") == ["current.json", "raw/unplanned"]
    assert store._listed_keys == {"current.json", "raw/unplanned"}


@pytest.mark.parametrize("bad_key", ["current.json", "write-lease.json", "reports/weekly/report.md",
                                     "copies/other", "generations/0.json", "raw/../current.json"])
@pytest.mark.parametrize("entry_point", ["store", "helper"])
def test_invalid_key_rejects_entire_batch(bad_key, entry_point):
    backend = FakeStateObjectStore()
    backend.put("raw/valid", b"preserve")
    # Build invalid metadata directly, since the fake may reject path traversal on PUT.
    from datumoj.rikolto.core.contracts import StateObject

    objects = [*backend.list_objects(""), StateObject(bad_key, 1, '"etag"')]
    store = bulk_store(backend)
    checks = []
    with pytest.raises(ValueError):
        if entry_point == "store":
            store.delete_objects([obj.key for obj in objects])
        else:
            list(delete_retired_objects(store, objects, lambda: checks.append(1)))
    assert store._client.calls == checks == []
    assert backend.get("raw/valid")[0] == b"preserve"


def test_fallback_checks_each_object_and_preserves_inventory_etags(monkeypatch):
    store = FakeStateObjectStore()
    for index in range(3):
        store.put(f"raw/{index}", b"synthetic")
    objects = store.list_objects("")
    original = store.delete_if_match
    deleted, checks = [], []

    def delete(key, etag):
        deleted.append((key, etag))
        original(key, etag)

    monkeypatch.setattr(store, "delete_if_match", delete)
    batches = list(delete_retired_objects(store, objects, lambda: checks.append(len(deleted))))
    assert deleted == [(obj.key, obj.etag) for obj in objects]
    assert checks == [0, 1, 2]
    assert batches == [([obj], 0) for obj in objects]


def test_errors_override_deleted_and_unconfirmed_keys_are_not_counted(monkeypatch):
    backend = FakeStateObjectStore()
    for key in ("raw/deleted", "raw/error", "raw/unconfirmed"):
        backend.put(key, b"synthetic")
    store = bulk_store(backend)
    response = {
        "Deleted": [{"Key": "synthetic/state/raw/deleted"}, {"Key": "synthetic/state/raw/error"},
                    {"Key": "other/namespace/raw/unplanned"}],
        "Errors": [{"Key": "synthetic/state/raw/error", "Code": "AccessDenied"}],
    }
    monkeypatch.setattr(store._client, "delete_objects", lambda **kw: response)
    objects = backend.list_objects("")
    assert list(delete_retired_objects(store, objects, lambda: None)) == [([objects[0]], 2)]
    assert store._listed_keys == {"raw/error", "raw/unconfirmed"}


@pytest.mark.parametrize("phase", ["generations", "copies", "raw"])
def test_partial_failure_counts_only_confirmed_deletions_and_stops(archive, monkeypatch, phase):
    snapshots = [record(archive, f"entry-{index}") for index in range(3)]
    for day in (0, 4, 8, 10):
        publish(archive, T0 + day * DAY, monkeypatch)
    for index in range(401):
        archive.store.put(f"copies/sha256/orphan-{index:03}", b"x" * (index + 1))
    archive.store.put("write-lease.json", b"synthetic coordination object")
    store = bulk_store(archive.store)
    monkeypatch.setattr(archive.store, "delete_objects", store.delete_objects, raising=False)
    errors = {"generations/1.json"} if phase == "generations" else (
        {"copies/sha256/orphan-000", "copies/sha256/orphan-001"} if phase == "copies"
        else {raw_key(snapshots[0])})
    store._client.errors = errors
    before = {obj.key: obj for obj in archive.store.list_objects("")}
    current_before = archive.store.get("current.json")
    report = cleanup(archive, T0 + 10 * DAY, raw_enabled=True)
    after = set(archive.store.list(""))
    deleted = before.keys() - after
    assert report["failures"] == len(errors)
    assert all(archive.store.exists(key) for key in errors)
    for kind, prefix in (("generations", "generations/"), ("copies", "copies/sha256/"), ("raw", "raw/")):
        keys = [key for key in deleted if key.startswith(prefix)]
        assert report[f"{kind}_deleted"] == len(keys)
        if kind != "generations":
            assert report[f"{kind}_deleted_bytes"] == sum(before[key].size_bytes for key in keys)
    assert report["total_bytes"] == sum(before[key].size_bytes for key in after)
    assert all(key.startswith(f"synthetic/state/{phase}/") for key in store._client.calls[-1])
    if phase == "generations":
        assert len(store._client.calls) == 1
        assert report["generations_deleted"] == 1
        assert report["copies_deleted"] == report["raw_deleted"] == 0
    elif phase == "copies":
        assert len(store._client.calls) == 2  # Descriptors, then the first content batch.
        assert report["copies_deleted"] == 198 and report["raw_deleted"] == 0
    else:
        assert report["raw_deleted"] == 2
    assert archive.store.get("current.json") == current_before
    assert archive.store.get("write-lease.json")[0] == b"synthetic coordination object"
    # Even descriptors whose deletion failed still have every required dependency.
    for generation in archive.manager.list():
        assert archive.manager.restore(archive.root / f"partial-{phase}-{generation.generation}",
                                       generation.generation, raw="local")["verified"]
    store._client.errors.clear()
    final = cleanup(archive, T0 + 10 * DAY, raw_enabled=True)
    assert final["failures"] == 0
    assert [g.generation for g in archive.manager.list()] == [4, 3]
    for generation in archive.manager.list():
        for mode in ("local", "remote"):
            assert archive.manager.restore(archive.root / f"final-{phase}-{generation.generation}-{mode}",
                                           generation.generation, raw=mode)["verified"]


@pytest.mark.parametrize("reason", ["lease", "time", "lease_check_time"])
def test_stop_before_next_batch_and_replan_next_round(archive, monkeypatch, reason):
    record(archive, "entry")
    publish(archive, T0 + 10 * DAY, monkeypatch)
    for index in range(450):
        archive.store.put(f"copies/sha256/orphan-{index:03}", b"orphan")
    store = bulk_store(archive.store)
    monkeypatch.setattr(archive.store, "delete_objects", store.delete_objects, raising=False)
    timer, checks = [0.0], []

    def check():
        checks.append(1)
        if reason == "lease_check_time" and len(checks) == 2:
            timer[0] = 600
        if reason == "lease" and store._client.calls:
            raise LeaseError("synthetic lease lost")

    def after_delete():
        if reason == "time":
            timer[0] = 600

    store._client.after_delete = after_delete
    report = archive.manager.cleanup(archive.specs, now=T0 + 10 * DAY, check=check,
                                     raw_enabled=True, clock=lambda: timer[0])
    expected = 0 if reason == "lease_check_time" else 200
    assert report["copies_deleted"] == expected
    assert report["copies_deleted_bytes"] == expected * len(b"orphan")
    assert len(store._client.calls) == (0 if reason == "lease_check_time" else 1)
    assert report["failures"] == (1 if reason == "lease" else 0)
    assert report["timed_out"] is (reason != "lease")
    assert len(checks) == (3 if reason == "lease" else 2)  # Includes the initial planning check.
    assert archive.manager.restore(archive.root / f"interrupted-{reason}", raw="local")["verified"]
    store._client.after_delete = lambda: None
    final = cleanup(archive, T0 + 10 * DAY, raw_enabled=True)
    assert final["failures"] == 0 and final["timed_out"] is False
    assert final["copies_deleted"] == 450 - expected
    assert archive.manager.restore(archive.root / f"continued-{reason}", raw="local")["verified"]
