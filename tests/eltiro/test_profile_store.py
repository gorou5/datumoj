"""Persistent implementation runs the unmodified 001 qualification suite."""

import sqlite3

import pytest

from datumoj.eltiro.profile_store import SQLiteProfileStore
from datumoj.eltiro.testing.contract_suites import ProfileStoreSuite
from datumoj.eltiro.testing.samples import make_profile, make_series


class TestSQLiteProfileStore(ProfileStoreSuite):
    @pytest.fixture
    def make_profile_store(self, tmp_path):
        opened = []

        def make():
            store = SQLiteProfileStore(tmp_path / str(len(opened)))
            opened.append(store)
            return store

        yield make
        for store in opened:
            store.close()


def test_restart_identity_order_and_cache(tmp_path):
    series = make_series(fixed_dimensions={"valuation": "current", "sex": "all"})
    profile = make_profile(series=series)
    with SQLiteProfileStore(tmp_path) as store:
        assert store.path == tmp_path / "eltiro" / "profiles.sqlite"
        store.write_units(1, {"table": ("ab" * 32, [profile]), "empty": ("cd" * 32, [])})
    with SQLiteProfileStore(tmp_path) as store:
        reordered = make_series(fixed_dimensions={"sex": "all", "valuation": "current"})
        assert store.get(1, reordered) == profile
        assert store.unchanged_unit(1, "table", "ab" * 32) == [profile]
        assert store.unchanged_unit(1, "empty", "cd" * 32) == []
        assert store.unchanged_unit(1, "missing", "ab" * 32) is None
        assert store.unchanged_unit(1, "table", "cd" * 32) is None
        store.unchanged_unit(1, "table", "ab" * 32)[0].names.clear()
        assert store.list(1) == [profile]
        store.write_generation(1, [profile])
        assert store.unchanged_unit(1, "table", "ab" * 32) is None


def test_database_failure_rolls_back_profiles_and_fingerprints(tmp_path):
    profile = make_profile()
    with SQLiteProfileStore(tmp_path) as store:
        store.write_units(1, {"old": ("ab" * 32, [profile])})
        with sqlite3.connect(store.path) as connection:
            connection.execute("""CREATE TRIGGER fail_fingerprint BEFORE INSERT ON input_fingerprints
                WHEN NEW.unit_key = 'fail' BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="test failure"):
            store.write_units(1, {"fail": ("cd" * 32, [make_profile(names={"en": "replacement"})])})
        assert store.list(1) == [profile]
        assert store.unchanged_unit(1, "old", "ab" * 32) == [profile]
        assert store.unchanged_unit(1, "fail", "cd" * 32) is None


def test_invalid_units_preserve_generation(tmp_path):
    profile = make_profile()
    with SQLiteProfileStore(tmp_path) as store:
        store.write_units(1, {"table": ("ab" * 32, [profile])})
        for units in ({"table": ("invalid", [profile])},
                      {"one": ("ab" * 32, [profile]), "two": ("cd" * 32, [profile])}):
            with pytest.raises(ValueError):
                store.write_units(1, units)
            assert store.unchanged_unit(1, "table", "ab" * 32) == [profile]
