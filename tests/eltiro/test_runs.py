import json

import pyarrow.parquet as pq
import pytest

from datumoj.eltiro.runs import RunStore


def test_atomic_failure_removes_staging_and_preserves_other_runs(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    first = store.write({"demand_id": "example"}, [], [], [], validate=lambda: None)
    assert store.list() == [first] and store.show(first["run_id"]) == first
    assert pq.read_table(store.path / first["run_id"] / "data.parquet").num_rows == 0
    original = pq.write_table

    def partial(*args):
        original(*args)
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(pq, "write_table", partial)
    with pytest.raises(OSError):
        store.write({}, [], [], [], validate=lambda: None)
    assert store.list() == [first]
    assert sorted(p.name for p in store.path.iterdir()) == [first["run_id"]]
    monkeypatch.setattr(pq, "write_table", original)

    def invalid():
        raise ValueError("generation changed")

    with pytest.raises(ValueError):
        store.write({}, [], [], [], validate=invalid)
    assert sorted(p.name for p in store.path.iterdir()) == [first["run_id"]]


@pytest.mark.parametrize("name", ["eltiro", "runs"])
def test_runs_reject_directory_symlinks(tmp_path, name):
    external = tmp_path / "elsewhere"
    external.mkdir()
    target = tmp_path / "eltiro" if name == "eltiro" else tmp_path / "eltiro/runs"
    target.parent.mkdir(exist_ok=True)
    target.symlink_to(external, target_is_directory=True)
    with pytest.raises(OSError):
        RunStore(tmp_path).write({}, [], [], [], validate=lambda: None)
    assert list(external.iterdir()) == []


def test_runs_reject_traversal_and_symlinked_records(tmp_path):
    store = RunStore(tmp_path)
    for bad in ("../outbox", "/tmp/run", "run-" + "f" * 33):
        with pytest.raises(ValueError):
            store.show(bad)
    record = store.write({}, [], [], [], validate=lambda: None)
    file = store.path / record["run_id"] / "record.json"
    file.unlink()
    target = tmp_path / "private.json"
    target.write_text(json.dumps({"secret": "private"}))
    file.symlink_to(target)
    with pytest.raises(OSError):
        store.show(record["run_id"])
