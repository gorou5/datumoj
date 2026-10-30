"""Output-only persistence and the unmodified cache contract suite."""

import sqlite3

import pytest

from datumoj.eltiro.model_cache import SQLiteModelCache
from datumoj.eltiro.testing.contract_suites import ModelCacheSuite
from datumoj.eltiro.testing.fakes import FakeModelProvider
from datumoj.eltiro.testing.samples import make_route_decision


class TestSQLiteCache(ModelCacheSuite):
    @pytest.fixture
    def make_cache(self, tmp_path):
        opened = []

        def make():
            cache = SQLiteModelCache(tmp_path / str(len(opened)))
            opened.append(cache)
            return cache

        yield make
        for cache in opened:
            cache.close()


def test_restart_stores_only_output_and_digest(tmp_path):
    decision = make_route_decision()
    key = decision.cache_key()
    result = FakeModelProvider().invoke(decision)
    with SQLiteModelCache(tmp_path) as cache:
        cache.put(key, result)
        path = cache.path
    with SQLiteModelCache(tmp_path) as cache:
        assert cache.get(key) == result
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT digest, result FROM outputs").fetchall() == [(key.digest, result.model_dump_json())]
    assert decision.request.parts[0].content.encode() not in path.read_bytes()
    assert key.authorized_sources[0].encode() not in path.read_bytes()


def test_provenance_mismatch_and_constructed_invalid_result_never_written(tmp_path):
    decision = make_route_decision()
    key = decision.cache_key()
    result = FakeModelProvider().invoke(decision)
    wrong = result.model_copy(update={"fields": {}})
    with SQLiteModelCache(tmp_path) as cache:
        with pytest.raises(ValueError):
            cache.put(key, wrong)
        other_key = key.model_copy(update={"provider": "claude_plan"})
        with pytest.raises(ValueError, match="provenance"):
            cache.put(other_key, result)
        assert cache.get(key) is None
