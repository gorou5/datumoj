"""Early raw deletion preserves all retained generations and protection vetoes."""

from contextlib import closing
from datetime import datetime, timedelta

import pytest

from datumoj.rikolto.core import state as state_module
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import PublishState
from datumoj.rikolto.core.nas_receipt import RECEIPT_KEY, parse_receipt
from datumoj.rikolto.core.state import StateError, raw_retention
from datumoj.rikolto.testing.samples import T0
from tests.core.test_nas_receipt import receipt_bytes
from tests.regressions.test_audit_131 import DAY, cleanup, publish, raw_key, record
from tests.regressions.test_audit_131 import archive as archive  # noqa: PLC0414 -- Synthetic fixture.

HOUR = timedelta(hours=1)


def test_one_hour_raw_deleted_by_receipt_and_all_survivors_restore(archive, monkeypatch):
    snap = record(archive, "entry")
    first, marked = publish(archive, T0, monkeypatch)
    assert marked == 1
    second, _ = publish(archive, T0 + HOUR, monkeypatch)
    archive.store.put(RECEIPT_KEY, receipt_bytes([snap.sha256], T0 + HOUR))
    report = cleanup(archive, T0 + HOUR, raw_enabled=True)
    assert report["receipt_status"] == "ok" and report["failures"] == 0
    assert report["raw_deleted"] == report["raw_deleted_by_receipt"] == 1
    assert report["raw_deleted_bytes"] == report["raw_deleted_by_receipt_bytes"] == snap.size
    assert report["raw_deleted_by_age"] == report["raw_deleted_by_age_bytes"] == 0
    assert not archive.store.exists(raw_key(snap))
    for generation in (first, second):
        for mode in ("local", "remote"):
            target = archive.root / f"restored-{generation.generation}-{mode}"
            assert archive.manager.restore(target, generation.generation, raw=mode)["verified"]
            with closing(SqliteManifest(target / "manifest.sqlite")) as manifest:
                assert manifest.raw_markers()[snap.sha256] == T0


@pytest.mark.parametrize("status", ["missing", "invalid", "stale"])
def test_unusable_receipt_falls_back_to_48_hours(archive, monkeypatch, status):
    snap = record(archive, "entry")
    publish(archive, T0, monkeypatch)
    if status != "missing":
        archive.store.put(RECEIPT_KEY, b"invalid" if status == "invalid" else receipt_bytes([snap.sha256], T0 - 3 * DAY))
    for when in (T0 + HOUR, T0 + 2 * DAY - timedelta(microseconds=1)):
        report = cleanup(archive, when, raw_enabled=True)
        assert report["receipt_status"] == status and report["failures"] == report["raw_deleted"] == 0
        assert archive.store.exists(raw_key(snap))
    publish(archive, T0 + 2 * DAY, monkeypatch)
    report = cleanup(archive, T0 + 2 * DAY, raw_enabled=True)
    assert report["receipt_status"] == status and report["failures"] == 0
    assert report["raw_deleted"] == report["raw_deleted_by_age"] == 1
    assert report["raw_deleted_bytes"] == report["raw_deleted_by_age_bytes"] == snap.size
    assert report["raw_deleted_by_receipt"] == 0


@pytest.mark.parametrize("protection", ["latest", "pending", "staged", "failed", "current"])
def test_receipt_cannot_override_protection(archive, monkeypatch, protection):
    snap = record(archive, protection, resource_type="feed" if protection == "latest" else "entry",
                  parsed=protection != "pending")
    if protection == "current":
        archive.manager.push()  # Required raw, with no optional marker.
    else:
        # Even a pre-existing marker cannot override latest/publication protection.
        archive.deps.manifest.mark_raw_optional(snap.sha256, T0)
        publish(archive, T0, monkeypatch)
    if protection in {"staged", "failed"}:
        archive.deps.manifest.record_publish_state(snap.snapshot_id, PublishState(protection))
    archive.store.put(RECEIPT_KEY, receipt_bytes([snap.sha256], T0 + HOUR))
    report = cleanup(archive, T0 + HOUR, raw_enabled=True)
    assert report["receipt_status"] == "ok" and report["failures"] == report["raw_deleted"] == 0
    assert archive.store.exists(raw_key(snap))


def test_legacy_unmarked_generation_vetoes_even_with_receipt(archive, monkeypatch):
    snap = record(archive, "entry")
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return T0

    monkeypatch.setattr(state_module, "datetime", Clock)
    legacy = archive.manager.push()
    publish(archive, T0 + HOUR, monkeypatch)
    archive.store.put(RECEIPT_KEY, receipt_bytes([snap.sha256], T0 + HOUR))
    assert cleanup(archive, T0 + HOUR, raw_enabled=True)["raw_deleted"] == 0
    assert archive.manager.restore(archive.root / "legacy", legacy.generation, raw="local")["verified"]
    when = T0 + DAY + timedelta(microseconds=1)
    publish(archive, when, monkeypatch)
    report = cleanup(archive, when, raw_enabled=True)
    assert report["raw_deleted_by_receipt"] == 1 and report["failures"] == 0


def test_expiry_is_minimum_of_age_and_confirmation(archive):
    snap = record(archive, "entry")
    _, receipt = parse_receipt(receipt_bytes([snap.sha256], T0 + HOUR), now=T0 + HOUR)
    manifest = archive.manager.data_dir / "manifest.sqlite"
    assert raw_retention(manifest, archive.specs, T0 + HOUR, receipt)[snap.sha256] == T0 + HOUR
    assert raw_retention(manifest, archive.specs, T0 + HOUR)[snap.sha256] == T0 + 2 * DAY


@pytest.mark.parametrize("enabled,raw_enabled", [(False, True), (True, False)])
def test_receipt_does_not_bypass_switches(archive, monkeypatch, enabled, raw_enabled):
    snap = record(archive, "entry")
    publish(archive, T0, monkeypatch)
    archive.store.put(RECEIPT_KEY, receipt_bytes([snap.sha256], T0 + HOUR))
    report = cleanup(archive, T0 + HOUR, enabled=enabled, raw_enabled=raw_enabled)
    assert report["reclaimable_raw"] == 1 and report["raw_deleted"] == 0
    assert report["raw_deleted_by_receipt"] == report["raw_deleted_by_age"] == 0
    assert archive.store.exists(raw_key(snap))


def test_receipt_counter_only_counts_confirmed_deletes(archive, monkeypatch):
    snaps = sorted([record(archive, name) for name in ("one", "two")], key=raw_key)
    publish(archive, T0, monkeypatch)
    archive.store.put(RECEIPT_KEY, receipt_bytes([s.sha256 for s in snaps], T0 + HOUR))
    original = archive.store.delete_if_match
    def fail_second(key, etag):
        if key == raw_key(snaps[1]):
            raise OSError("synthetic")
        original(key, etag)
    monkeypatch.setattr(archive.store, "delete_if_match", fail_second)
    report = cleanup(archive, T0 + HOUR, raw_enabled=True)
    assert report["failures"] == 1
    assert report["raw_deleted"] == report["raw_deleted_by_receipt"] == 1
    assert report["raw_deleted_by_receipt_bytes"] == snaps[0].size
    assert archive.store.exists(raw_key(snaps[1]))


def test_latest_raw_already_marked_in_first_generation_before_it_becomes_history(archive, monkeypatch):
    url = "https://www.data.jma.go.jp/protected-feed.xml"
    old = record(archive, "first-feed", resource_type="feed", url=url)
    first, marked = publish(archive, T0, monkeypatch)
    assert marked == 0  # Protected hashes retain the original raw_marked counting boundary.
    assert archive.deps.manifest.raw_markers()[old.sha256] == T0
    latest = record(archive, "next-feed", when=T0 + HOUR, resource_type="feed", url=url)
    publish(archive, T0 + HOUR, monkeypatch)
    archive.store.put(RECEIPT_KEY, receipt_bytes([old.sha256, latest.sha256], T0 + HOUR))
    report = cleanup(archive, T0 + HOUR, raw_enabled=True)
    assert report["raw_deleted_by_receipt"] == 1
    assert not archive.store.exists(raw_key(old)) and archive.store.exists(raw_key(latest))
    # A retained manifest still protects its own latest feed, even when a newer
    # manifest authorized deleting that raw. Restore must expose the missing body.
    with pytest.raises(StateError, match="^rollback_raw_expired$"):
        archive.manager.restore(archive.root / "first-feed-rollback", first.generation, raw="local")


def test_receipt_and_age_counters_partition_confirmed_deletions(archive, monkeypatch):
    old = record(archive, "aged", when=T0 - 3 * DAY)
    young = record(archive, "young")
    archive.deps.manifest.mark_raw_optional(old.sha256, T0 - 2 * DAY)
    publish(archive, T0, monkeypatch)
    archive.store.put(RECEIPT_KEY, receipt_bytes([old.sha256, young.sha256], T0 + HOUR))
    report = cleanup(archive, T0 + HOUR, raw_enabled=True)
    assert report["failures"] == 0 and report["raw_deleted"] == 2
    assert report["raw_deleted_by_receipt"] == report["raw_deleted_by_age"] == 1
    assert report["raw_deleted_by_age_bytes"] == old.size
    assert report["raw_deleted_by_receipt_bytes"] == young.size
    assert report["raw_deleted_bytes"] == old.size + young.size


def test_valid_receipt_only_confirms_listed_hashes(archive, monkeypatch):
    snap = record(archive, "unconfirmed")
    publish(archive, T0, monkeypatch)
    archive.store.put(RECEIPT_KEY, receipt_bytes(["a" * 64], T0 + HOUR))
    report = cleanup(archive, T0 + HOUR, raw_enabled=True)
    assert report["receipt_status"] == "ok" and report["failures"] == report["raw_deleted"] == 0
    assert archive.store.exists(raw_key(snap))
