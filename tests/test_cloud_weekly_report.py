"""Each cloud round records one capacity row under the lease, before publication."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.testing.fakes import FakeRegistry, FakeStateObjectStore
from datumoj.rikolto.testing.samples import make_spec
from tests.test_cloud_reports import _deps

NOW = datetime(2026, 11, 16, 1, 0, tzinfo=UTC)
KEYS = ("other/private-object-key-4711", "copies/sha256/private-copy-key-4711")
BUCKET = "private-bucket-name-4711"


def old_row(days, total=1_000):
    return {"recorded_at": (NOW - timedelta(days=days)).isoformat(), "total_bytes": total,
            "raw_bytes_by_source": {}, "db_store_bytes": 0}


@pytest.fixture
def world(tmp_path, monkeypatch):
    store = FakeStateObjectStore()
    for index, key in enumerate(KEYS):
        store.put(key, b"x" * (100 + index))
    spec = make_spec("jp-test")
    seen = {"held": False, "at_push": None, "restore_held": None}
    previous = []  # Rows the restored generation already carries.
    generation = SimpleNamespace(generation=1, created_at=NOW, stores=[], last_successful_runs={})

    def restore(root, *_args, **_kwargs):
        seen["restore_held"] = seen["held"]
        root.mkdir(parents=True, exist_ok=True)
        (root / "capacity.jsonl").write_text("".join(json.dumps(r) + "\n" for r in previous), encoding="utf-8")

    def push(*_args, **_kwargs):
        seen["at_push"] = (seen["held"], (tmp_path / "work" / "capacity.jsonl").read_text()
                           if (tmp_path / "work" / "capacity.jsonl").exists() else None)
        return generation

    manager = SimpleNamespace(
        store=store, writer=SimpleNamespace(kind="nas"), list=lambda: [generation] if previous else [],
        restore=restore, push=push, mark_raw=lambda *_a: 0, before_write=None, before_publish=None,
        cleanup=lambda *_a, **_k: {"failures": 0, "timed_out": False, "total_bytes": 0},
    )

    class Leases:
        def __init__(self, _store):
            pass

        def acquire(self, *_a, **_k):
            seen["held"] = True
            return SimpleNamespace(holder="t", token="t")

        @contextmanager
        def maintain(self, _lease):
            yield lambda: None

        def renew(self, *_a, **_k):
            return None

        def release(self, *_a, **_k):
            seen["held"] = False

    deps = SimpleNamespace(registry=FakeRegistry([spec]), require=lambda _f: None)
    monkeypatch.setattr(cloud, "probe_conditional_writes", lambda _s: {"supported": True, "check": "complete"})
    monkeypatch.setattr(cloud.wiring, "build_state", lambda _root: manager)
    monkeypatch.setattr(cloud.wiring, "build", lambda _root: deps)
    monkeypatch.setattr(cloud, "LeaseManager", Leases)
    monkeypatch.setattr(cloud, "Scheduler", lambda *_a: SimpleNamespace(sources={spec.id: spec}))
    monkeypatch.setattr(cloud, "_due", lambda *_a: [])
    monkeypatch.setattr(cloud, "_recover", lambda *_a: 0)
    monkeypatch.setattr(cloud, "_backfill", lambda *_a, **_k: 0)
    monkeypatch.setattr(cloud, "maintain_vectors", lambda *_a: None)
    monkeypatch.setattr(cloud, "_source_health", lambda *_a: [])
    monkeypatch.setattr(cloud, "publish_previous_week_report", lambda *_a: "reports/weekly/x.md")
    monkeypatch.setattr(cloud, "utcnow", lambda: NOW)
    return SimpleNamespace(seen=seen, previous=previous, store=store, root=tmp_path / "work")


def rows(world):
    return [json.loads(line) for line in (world.root / "capacity.jsonl").read_text().splitlines()]


def test_round_appends_one_row_under_the_lease_before_publication(world):
    result = cloud.run(work_dir=world.root)
    assert result["status"] == "completed"
    held, content = world.seen["at_push"]
    assert held is True and content is not None  # Written inside the lease, before push.
    [row] = rows(world)
    assert row == {"recorded_at": NOW.isoformat(), "total_bytes": 201, "raw_bytes_by_source": {},
                   "db_store_bytes": 0}
    assert not any(key.startswith("capacity") for key in (o.key for o in world.store.list_objects("")))
    assert all(name not in content for name in (*KEYS, BUCKET))


def test_failed_row_does_not_stop_the_round(world, monkeypatch):
    def broken(*_args):
        raise ValueError("synthetic inventory failure")

    monkeypatch.setattr(cloud, "capacity_row", broken)
    result = cloud.run(work_dir=world.root)
    assert result["status"] == "completed" and result["generation"] == 1
    assert world.seen["at_push"] == (True, None)
    assert not (world.root / "capacity.jsonl").exists()


def test_rows_older_than_16_days_are_pruned(world):
    world.previous.extend([old_row(20), old_row(16.5), old_row(16), old_row(1)])
    cloud.run(work_dir=world.root)
    # Exactly 16 days old is kept; 16.5 and 20 days are dropped.
    assert [r["recorded_at"] for r in rows(world)] == [
        old_row(16)["recorded_at"], old_row(1)["recorded_at"], NOW.isoformat(),
    ]
    assert world.seen["restore_held"] is True


def test_published_weekly_report_has_capacity_section_without_identifiers(tmp_path):
    store = FakeStateObjectStore()
    for key in KEYS:
        store.put(key, b"x")
    start = datetime(2026, 11, 9, 12, tzinfo=UTC)
    (tmp_path / "capacity.jsonl").write_text("".join(json.dumps({
        "recorded_at": (start + timedelta(days=day)).isoformat(), "total_bytes": 1_000_000_000 * (day + 1),
        "raw_bytes_by_source": {"jp-test": 1_000_000 * day}, "db_store_bytes": 5_000_000,
    }) + "\n" for day in range(5)), encoding="utf-8")
    generation = SimpleNamespace(generation=3, created_at=NOW, last_successful_runs={})
    key = cloud.publish_previous_week_report(store, _deps(tmp_path), NOW, generation)
    report = store.get(key)[0].decode()
    assert "## 容量" in report and "摘要覆盖 4.0 天（5 行），不足 7 天" in report
    assert "| jp\\-test | 4.00 | +4.00 |" in report
    for text in (report, (tmp_path / "capacity.jsonl").read_text()):
        assert all(name not in text for name in (*KEYS, BUCKET))
