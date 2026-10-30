"""Persistent MatchStore qualification and transactional result publication."""

import sqlite3

import pytest

from datumoj.eltiro.match_store import SQLiteMatchStore
from datumoj.eltiro.testing.contract_suites import MatchStoreSuite
from datumoj.eltiro.testing.samples import make_match, make_series


class TestSQLiteMatchStore(MatchStoreSuite):
    @pytest.fixture
    def make_match_store(self, tmp_path):
        opened = []

        def make():
            store = SQLiteMatchStore(tmp_path / str(len(opened)))
            opened.append(store)
            return store

        yield make
        for store in opened:
            store.close()


def test_result_restart_empty_result_and_configuration_pin(tmp_path):
    record = make_match()
    with SQLiteMatchStore(tmp_path) as store:
        assert store.path == tmp_path / "eltiro/matches.sqlite"
        result = store.save_result(record.demand_id, 1, 1, "config", [record], [make_series(table_key="missing")], True)
        store.save_result(record.demand_id, 1, 2, "empty", [], [], False)
    with SQLiteMatchStore(tmp_path) as store:
        assert store.load_result(record.demand_id, 1, 1, "config") == result
        assert store.load_result(record.demand_id, 1, 2, "empty").records == []
        assert store.load_result(record.demand_id, 1, 3, "new") is None
        with pytest.raises(ValueError, match="configuration changed"):
            store.load_result(record.demand_id, 1, 1, "other")
        store.set_confirmation(record.demand_id, 1, "gdp", record.series, 1, "confirmed")
        repeated = store.save_result(record.demand_id, 1, 1, "config", [], [], False)
        assert repeated.records[0].confirmation == "confirmed"
        assert repeated.missing_evidence == result.missing_evidence


def test_atomic_failure_keeps_records_and_run_metadata(tmp_path):
    record = make_match()
    with SQLiteMatchStore(tmp_path) as store:
        with sqlite3.connect(store.path) as db:
            db.execute("""CREATE TRIGGER fail_run BEFORE INSERT ON match_runs
                BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="synthetic failure"):
            store.save_result(record.demand_id, 1, 1, "config", [record], [], True)
        assert store.for_demand(record.demand_id, 1) == []
        assert store.load_result(record.demand_id, 1, 1, "config") is None


def test_batch_revalidates_before_writing(tmp_path):
    record = make_match()
    with SQLiteMatchStore(tmp_path) as store:
        for records in ([record, record], [make_match(generation=2)], [record.model_copy(update={"evidence": []})]):
            with pytest.raises(ValueError):
                store.save_result(record.demand_id, 1, 1, "config", records, [], True)
        assert store.for_demand(record.demand_id, 1) == []


def test_full_identity_order_and_rejection_survive_restart(tmp_path):
    first = make_match()
    second = make_match(series=make_series(fixed_dimensions={"valuation": "constant"}))
    with SQLiteMatchStore(tmp_path) as store:
        store.put(first)
        store.put(second)
        store.set_confirmation(first.demand_id, 1, "gdp", first.series, 1, "rejected")
        store.put(first)
    with SQLiteMatchStore(tmp_path) as store:
        records = store.for_demand(first.demand_id, 1)
        assert len(records) == 2 and records[0].confirmation == "rejected"
        assert records[1] == second
