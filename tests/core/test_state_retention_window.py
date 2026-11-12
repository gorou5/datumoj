"""Retention boundaries with synthetic manifests, storage and publication clocks."""

from contextlib import closing
from datetime import datetime, timedelta

import pytest

from datumoj.rikolto.core import state as state_module
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import PublishState
from datumoj.rikolto.core.state import RETENTION_WINDOW, StateManager, plan_cleanup
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0, make_snapshot, make_spec
from tests.regressions.test_audit_131 import DAY, cleanup, publish, raw_key, record
from tests.regressions.test_audit_131 import archive as archive  # noqa: PLC0414 -- Shared offline fixture.
from tests.test_capacity import copy, generation, obj


def test_generation_window_and_old_current(tmp_path):
    manifest = tmp_path / "manifest.sqlite"
    with closing(SqliteManifest(manifest)):
        pass
    stores = [copy("control.json", b"synthetic")]
    history = [generation(1, stores, T0 - timedelta(hours=25)),
               generation(2, stores, T0 - timedelta(hours=23)),
               generation(3, stores, T0 - timedelta(days=90))]
    objects = [obj(f"generations/{g.generation}.json", 1) for g in history]
    plan = plan_cleanup(objects, history, 3, manifest, [], now=T0)
    assert plan["generations"] == [objects[0]]


@pytest.mark.parametrize("age,expired,deleted", [
    (timedelta(hours=24), True, True),
    (timedelta(hours=23), True, False),
    (timedelta(hours=24) - timedelta(microseconds=1), True, False),
    (timedelta(hours=25), False, False),
])
def test_raw_waits_for_marker_window_and_expiry(tmp_path, age, expired, deleted):
    path = tmp_path / "manifest.sqlite"
    snap = make_snapshot(fetched_at=T0 - (2 * DAY if expired else DAY / 2))
    spec = make_spec(scope={"raw_retention_days": 1, "raw_expire_latest_types": ["entry"]})
    with closing(SqliteManifest(path)) as manifest:
        manifest.record_snapshot(snap, {"resource_type": "entry"})
        manifest.record_publish_state(snap.snapshot_id, PublishState.published)
        manifest.mark_raw_optional(snap.sha256, T0 - age)
    raw = obj(raw_key(snap), 5)
    current = generation(1, [copy("control.json", b"synthetic")])
    plan = plan_cleanup([raw], [current], 1, path, [spec], now=T0)
    assert plan["raw"] == ([raw] if deleted else [])


def test_retained_generation_required_raw_survives_until_generation_expires(archive, monkeypatch):
    snap = record(archive, "entry")
    marked_at = T0 + 8 * DAY  # Already expired before marking starts.
    # This publication predates marking and really requires the raw on restore.
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return marked_at - timedelta(hours=1)

    monkeypatch.setattr(state_module, "datetime", Clock)
    old = archive.manager.push()  # Simulate a writer that did not mark before publication.
    publish(archive, marked_at, monkeypatch)
    now = marked_at + timedelta(hours=23)
    assert now - old.created_at == RETENTION_WINDOW
    assert cleanup(archive, now, raw_enabled=True)["raw_deleted"] == 0
    assert archive.manager.restore(archive.root / "required-raw", old.generation, raw="local")["verified"]
    assert (archive.root / "required-raw" / raw_key(snap)).exists()
    ready = marked_at + RETENTION_WINDOW
    publish(archive, ready, monkeypatch)
    assert cleanup(archive, ready, raw_enabled=True)["raw_deleted"] == 1
    for retained in archive.manager.list():
        assert retained.created_at >= marked_at
        assert archive.manager.restore(archive.root / f"optional-{retained.generation}",
                                       retained.generation, raw="local")["verified"]


def test_legacy_marker_keeps_timestamp_and_waits_a_full_new_window(archive, monkeypatch):
    # Under the former three-day advance policy, this already-expired raw was
    # first encountered and marked at day eight. Migration must not backdate it.
    snap = record(archive, "legacy-entry")
    marked_at = T0 + 8 * DAY
    archive.deps.manifest.mark_raw_optional(snap.sha256, marked_at)
    publish(archive, marked_at, monkeypatch)
    assert archive.deps.manifest.raw_markers()[snap.sha256] == marked_at
    before = marked_at + RETENTION_WINDOW - timedelta(microseconds=1)
    assert cleanup(archive, before, raw_enabled=True)["raw_deleted"] == 0
    assert archive.store.exists(raw_key(snap))
    assert cleanup(archive, marked_at + RETENTION_WINDOW, raw_enabled=True)["raw_deleted"] == 1


def test_advance_marking_uses_window_without_rewriting_existing_marks(tmp_path):
    path = tmp_path / "manifest.sqlite"
    snap = make_snapshot()
    spec = make_spec(scope={"raw_retention_days": 7, "raw_expire_latest_types": ["entry"]})
    with closing(SqliteManifest(path)) as manifest:
        manifest.record_snapshot(snap, {"resource_type": "entry"})
        manifest.record_publish_state(snap.snapshot_id, PublishState.published)
    manager = StateManager(FakeStateObjectStore(), tmp_path)
    marked_at = T0 + 7 * DAY - RETENTION_WINDOW
    assert manager.mark_raw([spec], marked_at - timedelta(microseconds=1)) == 0
    assert manager.mark_raw([spec], marked_at) == 1
    assert manager.mark_raw([spec], marked_at + DAY) == 0
    with closing(SqliteManifest(path)) as manifest:
        assert manifest.raw_markers() == {snap.sha256: marked_at}
