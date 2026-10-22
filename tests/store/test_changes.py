from datetime import timedelta, timezone

import pytest

from datumoj.store.changes import SqliteChangeLog
from datumoj.testing.contract_suites import ChangeLogSuite
from datumoj.testing.samples import T0


class TestSqliteChangeLog(ChangeLogSuite):
    @pytest.fixture(params=["memory", "file"])
    def change_log(self, request, tmp_path):
        store = SqliteChangeLog(":memory:" if request.param == "memory" else tmp_path / "changes.sqlite")
        yield store
        store.close()


def test_reopen_preserves_payload_and_tie_order(tmp_path):
    path = tmp_path / "nested" / "changes.sqlite"
    events = [ChangeLogSuite.event(str(i)) for i in range(3)]
    store = SqliteChangeLog(path)
    store.append(events)
    store.close()
    store = SqliteChangeLog(path)
    try:
        store.append(reversed(events))
        assert store.list() == events
    finally:
        store.close()


def test_failed_batch_rolls_back(tmp_path):
    store = SqliteChangeLog(tmp_path / "changes.sqlite")

    def broken_batch():
        yield ChangeLogSuite.event()
        raise RuntimeError("broken iterator")

    try:
        with pytest.raises(RuntimeError, match="broken iterator"):
            store.append(broken_batch())
        assert store.list() == []
    finally:
        store.close()


def test_utc_order_across_timezones(tmp_path):
    earlier = ChangeLogSuite.event("earlier", detected_at=T0.astimezone(timezone(timedelta(hours=9))))
    later = ChangeLogSuite.event("later", detected_at=T0 + timedelta(hours=1))
    store = SqliteChangeLog(tmp_path / "changes.sqlite")
    try:
        store.append([later, earlier])
        assert store.list() == [earlier, later]
        assert store.list(since=T0, until=T0) == [earlier]
    finally:
        store.close()
