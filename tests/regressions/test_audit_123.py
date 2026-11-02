"""Immutable transfers avoid per-object lease reads and keep safe progress fields."""

import io
import json
import sys
from threading import Lock

from datumoj.rikolto import cloud
from datumoj.rikolto.core import state as state_module
from datumoj.rikolto.core.lease import LEASE_KEY, LeaseManager
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0
from tests.core.test_state import corpus as corpus  # noqa: PLC0414 -- Shared offline corpus.


class LeaseCountingStore(FakeStateObjectStore):
    def __init__(self):
        super().__init__()
        self.reads = 0
        self.writes = []
        self.count_lock = Lock()

    def get(self, key):
        if key == LEASE_KEY:
            with self.count_lock:
                self.reads += 1
        return super().get(key)

    def put_if_absent(self, key, data):
        with self.count_lock:
            self.writes.append((key, self.reads))
        return super().put_if_absent(key, data)


def test_many_copies_need_only_bounded_remote_lease_reads(corpus, monkeypatch):
    # Many real SQLite blocks, not invalid synthetic database/parquet files.
    monkeypatch.setattr(state_module, "_BLOCK_SIZE", 512)
    store = LeaseCountingStore()
    leases = LeaseManager(store, now=lambda: T0)
    lease = leases.acquire("local/synthetic-123")
    store.reads = 0
    ticks = [0.0]
    check = lambda: leases.check(lease)
    manager = StateManager(store, corpus[0], transfer_workers=4, before_publish=check)
    manager.before_write = cloud._throttled_check(check, monotonic=lambda: ticks[0])
    manager.before_metadata_write = check
    manager.push()
    copies = [(key, reads) for key, reads in store.writes if key.startswith("copies/")]
    assert len(copies) > 20
    # During immutable uploads: one initial full check and one throttled check.
    assert max(reads for _, reads in copies) <= 2 + ticks[0] / 30
    # Generation and pointer boundaries remain fully checked, independently.
    generation_reads = next(reads for key, reads in store.writes if key == "generations/1.json")
    pointer_reads = next(reads for key, reads in store.writes if key == "current.json")
    assert generation_reads == 3 and pointer_reads == store.reads == 5
    assert store.reads < len(copies) / 4


def test_timing_progress_fields_remain_whitelisted(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", stream)
    record = {"publish_timings": {"upload_copies": 1.25, "cleanup": 0.5},
              "restore_timings": {"download_copies": 0.75},
              "unexpected": "synthetic private content"}
    cloud._progress("publish_end", cloud.time.monotonic(), record)
    row = json.loads(stream.getvalue())
    assert set(row) == {"event", "source_id", "elapsed_s", "new", "unchanged", "parsed", "failures",
                        "stop_reason", "publish_timings", "restore_timings"}
    for name in ("publish_timings", "restore_timings"):
        assert row[name] == record[name]
        assert all(type(key) is str and type(seconds) in (int, float) for key, seconds in row[name].items())
    assert "private" not in stream.getvalue()
