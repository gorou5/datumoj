"""Persistence, metadata and transaction checks beyond the shared qualification suite."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from datumoj.eltiro.registry_store import DemandConflict, DemandLimitExceeded, SQLiteDemandRegistry
from datumoj.eltiro.testing.samples import make_demand


def test_reopen_preserves_versions_activity_and_original_registration_metadata(tmp_path):
    database = tmp_path / "registry.sqlite3"
    one, two = make_demand(), make_demand(version=2)
    with SQLiteDemandRegistry(database) as registry:
        registry.register(one, file_sha256="ab" * 32)
        before = registry.list_records()[0]
        registry.register(one, file_sha256="cd" * 32)
        assert registry.list_records()[0] == before
        registry.register(two, active=False)
    with SQLiteDemandRegistry(database) as registry:
        assert registry.get(one.id, 1) == one
        assert registry.get(two.id, 2) == two
        assert registry.list_active() == [one]
        assert before["consumer"] == one.consumer
        assert before["file_sha256"] == "ab" * 32
        assert datetime.fromisoformat(before["registered_at"]).tzinfo is not None
        registry.set_active(one.id, 1, False)
    with SQLiteDemandRegistry(database) as registry:
        assert registry.list_active() == []
        assert registry.get(one.id, 1) == one


def test_failed_registration_rolls_back_and_preserves_active_version(tmp_path):
    with SQLiteDemandRegistry(tmp_path / "registry.sqlite3") as registry:
        one = make_demand()
        registry.register(one, max_active_demands=1)
        with pytest.raises(DemandLimitExceeded):
            registry.register(make_demand(id="over-limit"), max_active_demands=1)
        with pytest.raises(DemandConflict):
            registry.register(make_demand(purpose="Changed"))
        with pytest.raises(DemandConflict):
            registry.register(make_demand(version=2, consumer="other-consumer"))
        for invalid in ("invalid", ""):
            with pytest.raises(ValueError):
                registry.register(make_demand(version=2), file_sha256=invalid)
        assert registry.list_active() == [one]
        assert len(registry.list_records()) == 1
        registry.register(make_demand(version=2), max_active_demands=1)
        assert registry.list_active()[0].version == 2


def test_concurrent_registration_cannot_bypass_active_limit(tmp_path):
    database = tmp_path / "registry.sqlite3"
    with SQLiteDemandRegistry(database):
        pass

    def register(index):
        with SQLiteDemandRegistry(database) as registry:
            try:
                registry.register(make_demand(id=f"request-{index}"), max_active_demands=1)
            except DemandLimitExceeded:
                return False
            return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sum(executor.map(register, range(2))) == 1
    with SQLiteDemandRegistry(database) as registry:
        assert len(registry.list_active()) == len(registry.list_records()) == 1
