"""capacity.jsonl is a small state file: published with the generation and restored unchanged."""

import json

import pytest

from datumoj.rikolto.core import state as state_module
from datumoj.rikolto.core.state import StateError
from tests.core.test_state import corpus as corpus  # noqa: PLC0414 -- Offline fixture.
from tests.core.test_state import state as state  # noqa: PLC0414 -- Offline fixture.

ROW = {"recorded_at": "2026-11-10T00:00:00+00:00", "total_bytes": 123,
       "raw_bytes_by_source": {"jp-test": 100}, "db_store_bytes": 20}


def test_capacity_log_is_a_small_state_file():
    assert "capacity.jsonl" in state_module._SMALL


def test_published_and_restored_unchanged(state, tmp_path):
    manager, store = state
    content = (json.dumps(ROW, sort_keys=True) + "\n").encode()
    (manager.data_dir / "capacity.jsonl").write_bytes(content)
    generation = manager.push()
    copy = next(c for c in generation.stores if c.name == "capacity.jsonl")
    assert store.get(copy.key)[0] == content
    target = tmp_path / "pulled"
    manager.restore(target)
    assert (target / "capacity.jsonl").read_bytes() == content


def test_without_the_file_publication_is_unchanged(state, tmp_path):
    manager, _ = state
    generation = manager.push()
    assert "capacity.jsonl" not in {c.name for c in generation.stores}
    target = tmp_path / "pulled"
    manager.restore(target)
    assert not (target / "capacity.jsonl").exists()


def test_corrupt_capacity_log_is_rejected_like_other_jsonl_state(state):
    manager, _ = state
    (manager.data_dir / "capacity.jsonl").write_bytes(b"not json\n")
    with pytest.raises((StateError, ValueError)):
        manager.push()
