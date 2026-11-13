"""Mid-round capacity decisions use successful raw growth, without bucket scans."""

import hashlib
import io
import json
from types import SimpleNamespace

import pytest

from datumoj.rikolto import cloud
from datumoj.rikolto.core.contracts import StateObject
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.testing.contract_suites import RawStoreSuite
from datumoj.rikolto.testing.fakes import FakeRawStore, FakeRegistry, FakeStateObjectStore
from datumoj.rikolto.testing.samples import make_spec
from tests.regressions.test_audit_katabami_102 import BASELINES, stable_result
from tests.test_cloud_capacity import capacity_inventory
from tests.test_cloud_run import BODY, assert_pulled, invoke, released, remote
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Offline fixture.


class LogicalBytes(bytes):
    """Small synthetic body with a logical size; never allocate hundreds of MB."""

    def __len__(self):
        return 150_000_000


class TestRawUploadContract(RawStoreSuite):
    @pytest.fixture
    def store(self):
        return cloud._RawUploads(FakeRawStore())

    @pytest.fixture
    def supports_corruption_recovery(self):
        return False  # Same limitation as the underlying FakeRawStore.

    def corrupt(self, store, sha256):
        store.raw.blobs[sha256] = b"synthetic corruption"


@pytest.mark.parametrize("initial,expected", [(8_300_000_000, 1), (6_000_000_000, 3)])
def test_sources_stop_only_between_sources_and_still_publish(tmp_path, monkeypatch, initial, expected):
    store, raw = FakeStateObjectStore(), FakeRawStore()
    specs = [make_spec(f"jp-capacity{i}") for i in range(3)]
    events, scans, gates = [], [], []
    raw_sizes = {}
    original_put, original_objects, original_gate = raw.put, store.list_objects, cloud.gate

    def put(data):
        sha = original_put(data)
        raw_sizes[sha] = len(data)
        return sha

    def objects(prefix=""):
        scans.append((prefix, tuple(events)))
        rows = original_objects(prefix)
        if not prefix:
            rows.append(StateObject("other/synthetic", initial - sum(row.size_bytes for row in rows)
                                    + sum(raw_sizes.values()), '"synthetic"'))
        return rows

    def gate(*args, **kwargs):
        decision = original_gate(*args, **kwargs)
        gates.append((args, kwargs))
        return decision

    def source(deps, spec, _scheduler, remaining, *_args):
        events.append(spec.id)
        # A source may cross the stop line while running: both writes must finish.
        for part in range(2):
            deps.raw.put(LogicalBytes(f"{spec.id}-{part}".encode()))
        return {"source_id": spec.id, "exit_status": 0, "failures": {},
                "new": 2, "unchanged": 0, "parsed": 2}, remaining

    def mark_raw(*_args):
        events.append("mark_raw")
        return 0

    def push():
        assert events[-1] == "mark_raw"
        events.append("publish")
        return SimpleNamespace(generation=1)

    def cleanup(*_args, **_kwargs):
        events.append("cleanup")
        return {"failures": 0, "timed_out": False, "total_bytes": initial + sum(raw_sizes.values())}

    manager = SimpleNamespace(store=store, writer=SimpleNamespace(kind="nas"), list=list,
                              mark_raw=mark_raw, push=push, cleanup=cleanup)
    deps = SimpleNamespace(raw=raw, registry=FakeRegistry(specs), require=lambda _feature: None)
    scheduler = SimpleNamespace(sources={spec.id: spec for spec in specs})
    monkeypatch.setattr(raw, "put", put)
    monkeypatch.setattr(store, "list_objects", objects)
    monkeypatch.setattr(cloud, "gate", gate)
    monkeypatch.setattr(cloud.wiring, "build_state", lambda _root: manager)
    monkeypatch.setattr(cloud.wiring, "build", lambda _root: deps)
    monkeypatch.setattr(cloud, "Scheduler", lambda *_args: scheduler)
    monkeypatch.setattr(cloud, "_due", lambda *_args: [spec.id for spec in specs])
    monkeypatch.setattr(cloud, "_source", source)
    monkeypatch.setattr(cloud, "_recover", lambda *_args: 0)
    monkeypatch.setattr(cloud, "maintain_vectors", lambda *_args: None)
    monkeypatch.setattr(cloud, "_source_health", lambda *_args: [])
    monkeypatch.setattr(cloud, "publish_previous_week_report", lambda *_args: "reports/weekly/synthetic.md")
    result = cloud.run(work_dir=tmp_path / "work")

    assert events == [spec.id for spec in specs[:expected]] + ["mark_raw", "publish", "cleanup"]
    assert result["status"] == "completed" and result["exit_status"] == 0 and result["generation"] == 1
    assert result["new"] == result["parsed"] == expected * 2
    assert result["due"] == 3 and not result["budget_exhausted"]
    capacity = result["capacity"]
    assert capacity["uploaded_raw_bytes"] == expected * 300_000_000
    # Full inventories may occur in preflight or after publication, never between sources.
    assert all(not history or history[-1] == "cleanup" for prefix, history in scans if not prefix)
    source_gates = [(args, kwargs) for args, kwargs in gates
                    if len(args) == 3 and args[0] == initial and args[2] == "essential"]
    assert [args[1] for args, _ in source_gates] == ([0, 0, 300_000_000] if expected == 1 else
                                                   [0, 0, 300_000_000, 600_000_000])
    assert all(args[0] == initial and kwargs == {"reserve_bytes": 500_000_000}
               for args, kwargs in source_gates)
    if expected == 1:
        assert capacity["tier"] == "stop" and capacity["projected_bytes"] == 9_100_000_000
        assert capacity["next_round"] == "cleanup_only"
        assert capacity["skipped_sources"] == [
            {"source_id": spec.id, "reason": "capacity_stop_mid_round"} for spec in specs[1:]
        ]
        assert capacity["skipped_steps"] == ["crawl", "backfill", "weekly_report"]
        assert result["weekly_report_key"] is None
    else:
        assert capacity["skipped_sources"] == [] and capacity["skipped_steps"] == []
        assert result["weekly_report_key"] is not None
    (tmp_path / "result.json").write_text(json.dumps(result, indent=2))


def test_upload_count_excludes_existing_duplicates_and_failed_writes(monkeypatch):
    raw = FakeRawStore()
    raw.put(b"existing")
    uploads = cloud._RawUploads(raw)
    uploads.put(b"existing")
    uploads.put(b"new")
    uploads.put(b"new")
    stream = io.BytesIO(b"prefix-stream")
    stream.seek(7)
    sha = uploads.put(stream)
    assert sha == hashlib.sha256(b"stream").hexdigest()
    assert not stream.closed and raw.get(sha) == b"stream"
    assert uploads.bytes == len(b"newstream")

    def fail(_data):
        raise OSError("synthetic upload failure")

    monkeypatch.setattr(raw, "put", fail)
    with pytest.raises(OSError, match="synthetic upload failure"):
        uploads.put(b"failed")
    assert uploads.bytes == len(b"newstream")


def test_below_7gb_preserves_previous_result(world):
    result, _ = invoke(world)
    capacity = result.pop("capacity")
    assert capacity["tier"] == "normal" and capacity["skipped_sources"] == []
    assert capacity["uploaded_raw_bytes"] > 0
    for key in ("by_source", "reclaimable_orphan_raw", "reclaimable_orphan_raw_bytes", "orphan_raw_enabled",
                "orphan_raw_deleted", "orphan_raw_deleted_bytes", "orphan_raw_skip_reason"):
        result["cleanup"].pop(key, None)
    assert stable_result(result) == BASELINES["run"]


@pytest.mark.parametrize("publish_fails", [False, True])
def test_real_publication_after_mid_round_stop(world, monkeypatch, publish_fails):
    # Real small bodies hit exactly 9 GB including reserve; storage remains moto.
    capacity_inventory(monkeypatch, 8_500_000_000 - len(BODY))
    if publish_fails:
        def fail(*_args, **_kwargs):
            raise OSError("synthetic publication failure")

        monkeypatch.setattr(StateManager, "push", fail)
    result, work = invoke(world)
    assert [source["source_id"] for source in result["sources"]] == ["jp-cloud0"]
    assert result["capacity"]["projected_bytes"] == 9_000_000_000
    assert result["capacity"]["next_round"] == "cleanup_only"
    assert result["capacity"]["uploaded_raw_bytes"] == len(BODY)
    if publish_fails:
        assert result["status"] == "failed" and result["reason"] == "publish_failed"
        assert result["generation"] is None and remote(world).get("current.json") is None
    else:
        assert result["status"] == "completed" and result["generation"] == 1
        assert result["cleanup"]["failures"] == 0
        assert set(world.managers[-1].list()[0].last_successful_runs) == {"jp-cloud0"}
        assert_pulled(world, work, 1)
    released(world)
