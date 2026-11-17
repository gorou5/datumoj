"""Retention inside a cloud round, on the offline moto world: off by default, leased when on."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from datumoj.rikolto import cloud, db_retention
from datumoj.rikolto.capacity import WEEKLY_REPORT_RESERVE
from datumoj.rikolto.core.lease import LeaseManager
from datumoj.rikolto.core.state import StateManager
from datumoj.rikolto.testing.fakes import FakeRegistry
from tests.test_cloud_run import LocalFetcher, invoke, released, remote
from tests.test_cloud_run import world as world  # noqa: PLC0414 -- Shared offline fixture.


class AgedFetcher(LocalFetcher):
    """Report every fetch as made 100 hours ago: the clock cannot jump (the ledger rejects the future)."""

    def fetch(self, *args, **kwargs):
        result = super().fetch(*args, **kwargs)
        return result.model_copy(update={"fetched_at": datetime.now(UTC) - timedelta(hours=100)})


def expiring(world, hours=48):
    world.overrides["fetcher"] = AgedFetcher()
    world.overrides["registry"] = FakeRegistry([
        spec.model_copy(update={"scope": {**spec.scope, "index_retention_hours": hours}}) for spec in world.specs])


def restored(world, name):
    target = world.tmp / name
    StateManager(remote(world), world.tmp / f"{name}-stage").restore(target)
    return target


def count(root, table, name="chunks.sqlite"):
    with closing(sqlite3.connect(root / name)) as db:
        return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def autovac(path):
    with closing(sqlite3.connect(path)) as db:
        return db.execute("PRAGMA auto_vacuum").fetchone()[0]


def test_switch_defaults_to_off_and_changes_nothing(world, monkeypatch):
    monkeypatch.delenv("R2_DB_RETENTION", raising=False)
    expiring(world)

    def forbidden(*args, **kwargs):
        raise AssertionError("retention ran with the switch off")

    monkeypatch.setattr(db_retention, "run", forbidden)
    first, root = invoke(world, "first")
    second, _ = invoke(world, "second")
    for result in (first, second):
        assert result["status"] == "completed" and "db_retention" not in result
    # Everything stays: three documents, one version each, no VACUUM mode change.
    kept = restored(world, "kept")
    assert count(kept, "documents") == 3 and count(kept, "current_versions") == 3
    assert autovac(root / "chunks.sqlite") == autovac(root / "manifest.sqlite") == 0
    released(world)


def test_explicit_off_is_the_same_as_unset(world, monkeypatch):
    monkeypatch.setenv("R2_DB_RETENTION", "off")
    result, _ = invoke(world)
    assert "db_retention" not in result and result["generation"] == 1


def test_enabled_round_runs_in_the_lease_before_publish_and_never_ingests_again(world, monkeypatch):
    expiring(world)
    first, _ = invoke(world, "first")
    assert first["generation"] == 1
    monkeypatch.setenv("R2_DB_RETENTION", "on")
    seen = []
    real = db_retention.run

    def spy(root, now, specs, **kw):
        seen.append((kw["execute"], LeaseManager(remote(world)).status()["held"],
                     remote(world).get("current.json")))
        return real(root, now, specs, **kw)

    monkeypatch.setattr(db_retention, "run", spy)
    second, root = invoke(world, "second")
    assert second["status"] == "completed" and second["generation"] == 2
    # Executed once, only while the lease was held and before the new generation was published.
    assert [row[:2] for row in seen] == [(True, True)] and json.loads(seen[0][2][0]) == {"generation": 1}
    report = second["db_retention"]
    assert sum(row["documents"] for row in report["index_expired"]["sources"].values()) == 3
    assert report["vacuum"]["one_off"]["done"] == ["manifest", "chunks"]
    assert autovac(root / "chunks.sqlite") == autovac(root / "manifest.sqlite") == 2
    gen2 = restored(world, "gen2")
    assert count(gen2, "documents") == count(gen2, "chunks") == count(gen2, "current_versions") == 0
    assert count(gen2, "parsed", "manifest.sqlite") >= 3 and count(gen2, "snapshots", "manifest.sqlite") >= 3
    # The published (backup-copied, block-restored) files keep incremental mode, so later rounds
    # never VACUUM the whole file again.
    assert autovac(gen2 / "chunks.sqlite") == autovac(gen2 / "manifest.sqlite") == 2
    # Next round: the same sources are fetched again, nothing is parsed or indexed again.
    third, _ = invoke(world, "third")
    assert third["status"] == "completed" and third["parsed"] == 0
    assert third["db_retention"]["vacuum"]["one_off"]["pending"] == []
    assert third["db_retention"]["vacuum"]["one_off"]["done"] == []
    assert count(restored(world, "gen3"), "documents") == 0
    released(world)


def test_usage_db_before_and_after_a_round_keeps_its_format(world, monkeypatch):
    """107's `cloud usage --db` on the fake store; the pair is kept as task evidence."""
    expiring(world)
    invoke(world, "first")
    before = cloud.usage(db=True, work_dir=world.tmp / "usage-before")
    monkeypatch.setenv("R2_DB_RETENTION", "on")
    invoke(world, "second")
    after = cloud.usage(db=True, work_dir=world.tmp / "usage-after")
    assert "db" in before and "db" in after, (before, after)
    assert before["db"].keys() == after["db"].keys() and before["db"]["sqlite"].keys() == after["db"]["sqlite"].keys()
    def current(report):
        return sum(s["current"]["documents"] for s in report["db"]["chunks"]["sources"].values())

    assert (current(before), current(after)) == (3, 0)
    assert after["db"]["sqlite"]["chunks.sqlite"]["tables"]["documents"]["rows"] == 0


def test_refused_gate_skips_the_one_off_vacuum_but_still_deletes(world, monkeypatch):
    expiring(world)
    invoke(world, "first")
    monkeypatch.setenv("R2_DB_RETENTION", "on")
    original = cloud.gate

    def gate(current, estimated, category, **kw):
        decision = original(current, estimated, category, **kw)
        if category == "optional" and estimated != WEEKLY_REPORT_RESERVE:
            decision = {**decision, "allowed": False, "reason": "capacity_freeze"}
        return decision

    monkeypatch.setattr(cloud, "gate", gate)
    result, root = invoke(world, "second")
    one_off = result["db_retention"]["vacuum"]["one_off"]
    assert result["status"] == "completed"
    assert one_off["skipped_reason"] == "capacity_freeze" and one_off["done"] == []
    assert autovac(root / "chunks.sqlite") == 0
    assert count(restored(world, "gen2"), "documents") == 0
    released(world)


def test_retention_failure_fails_the_round_before_publication(world, monkeypatch):
    expiring(world)
    invoke(world, "first")
    monkeypatch.setenv("R2_DB_RETENTION", "on")

    def broken(*args, **kwargs):
        raise OSError("https://synthetic.invalid/private?key=synthetic-secret")

    monkeypatch.setattr(db_retention, "run", broken)
    result, _ = invoke(world, "second")
    assert result["status"] == "failed" and result["reason"] == "db_retention_failed"
    assert "synthetic" not in json.dumps(result)
    assert remote(world).get("current.json")[0] == b'{"generation": 1}'
    released(world)


def test_embed_rounds_never_run_retention(world, monkeypatch):
    from tests.test_cloud_embed import invoke_embed, seed

    seed(world)  # an ordinary run round: the switch is still off
    monkeypatch.setenv("R2_DB_RETENTION", "on")
    monkeypatch.setattr(db_retention, "run", lambda *a, **k: pytest.fail("retention in an embed round"))
    assert "db_retention" not in invoke_embed(world)
