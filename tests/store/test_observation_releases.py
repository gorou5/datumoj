"""Offline publication fixture for the reusable release-store contract suite."""

import json
import os
import shutil
import sqlite3
from hashlib import sha256

from datumoj.rikolto.core.models import GenerationToken


def publish_fixture(store, manifest):
    """Simulate a successful publication; fixture manifests use synthetic copies.

    Production validation is tested separately through the real StateManager.
    Keep the supplied manifest bytes intact to exercise the token hash contract.
    """
    payload = manifest.model_dump_json().encode()
    directory = store.root / '.observation-generations'
    base = directory / str(manifest.generation)
    data = base / 'data'
    data.mkdir(parents=True)
    shutil.copytree(store.root / 'parquet', data / 'parquet')
    if (store.root / 'parquet-history').exists():
        shutil.copytree(store.root / 'parquet-history', data / 'parquet-history')
    shutil.copyfile(store.root / 'observations.duckdb', data / 'observations.duckdb')
    if (store.root / 'manifest.sqlite').exists():
        with sqlite3.connect(store.root / 'manifest.sqlite') as source, sqlite3.connect(data / 'manifest.sqlite') as target:
            source.backup(target)
    query_files = store.prepare_release_index(data)
    files = {}
    for path in data.rglob('*'):
        if path.is_file():
            stat = path.stat()
            files[path.relative_to(data).as_posix()] = [stat.st_size, stat.st_mtime_ns]
    digest = sha256(payload).hexdigest()
    (base / 'manifest.json').write_bytes(payload)
    (base / 'verified.json').write_text(json.dumps({
        'manifest_sha256': digest, 'generation': manifest.generation, 'files': files,
        'parquet_files': [p.relative_to(data).as_posix() for p in query_files],
    }))
    token = str(GenerationToken(generation=manifest.generation, manifest_sha256=digest))
    temp = directory / '.current'
    temp.write_text(token)
    os.replace(temp, directory / 'current.json')
    return token


def _release_store(tmp_path):
    from datetime import UTC, date, datetime

    from datumoj.rikolto.core.models import SnapshotOrder
    from datumoj.rikolto.store.observations import DuckDBObservationStore
    from tests.core.test_models import make_observation

    store = DuckDBObservationStore(tmp_path)
    for seq, (release, year) in enumerate([("R3", 2023), ("R1", 2021), ("R2", 2022)], start=1):
        obs = make_observation(release_id=release, released_at=date(year, 1, 1), snapshot_sha256=f"{seq:064x}",
                               fetched_at=datetime(2026, 1, seq, tzinfo=UTC))
        order = SnapshotOrder(fetched_at=obs.fetched_at, seq=seq)
        store.import_release(obs.source_id, obs.table_key, [obs], order)
    return store, obs.source_id, obs.table_key


TABLES = ("tbl-a", "tbl-b")


def _fresh(release, year, table, snapshot, indicator="old", obs_id="o1", seq=1):
    from datetime import UTC, date, datetime

    from tests.core.test_models import make_observation

    obs = make_observation(release_id=release, released_at=date(year, 1, 1), table_key=table, obs_id=obs_id,
                           dataset_key=f"jp-stat-dashboard:{table}", indicator=indicator,
                           snapshot_sha256=f"{snapshot:064x}", fetched_at=datetime(2026, 1, seq, tzinfo=UTC))
    return obs


def _two_tables(tmp_path):
    """R2 is the current release of tbl-a (R1 history) and a history row of tbl-b (R3 current)."""
    from datumoj.rikolto.core.models import SnapshotOrder
    from datumoj.rikolto.store.observations import DuckDBObservationStore

    store = DuckDBObservationStore(tmp_path)
    plan = [("R1", 2021, "tbl-a"), ("R2", 2022, "tbl-a"), ("R3", 2023, "tbl-b"), ("R2", 2022, "tbl-b")]
    for seq, (release, year, table) in enumerate(plan, start=1):
        rows = [_fresh(release, year, table, seq, obs_id=obs_id, seq=seq) for obs_id in ("o1", "o2")]
        store.import_release("jp-stat-dashboard", table, rows, SnapshotOrder(fetched_at=rows[0].fetched_at, seq=seq))
    return store, "jp-stat-dashboard"


def _stored(store, source):
    """Every stored row per table (history scan) plus which of them sit in the current file."""
    import pyarrow.parquet as pq

    out = {}
    for table in TABLES:
        path = store.path_for(source, table)
        current = {(r["obs_id"], r["release_id"]) for r in pq.read_table(path).to_pylist()}
        out[table] = ([{**r, "in_current_file": (r["obs_id"], r["release_id"]) in current}
                       for r in sorted(store._stored_rows(path), key=lambda r: (r["release_id"], r["obs_id"]))],
                      store.table_order(source, table))
    return out


def _files(tmp_path):
    return sorted((p.relative_to(tmp_path).as_posix(), p.read_bytes() if p.suffix == ".parquet" else b"")
                  for p in tmp_path.rglob("*.parquet"))


def test_replace_release_swaps_current_and_history_rows_in_place(tmp_path):
    import pytest

    store, source = _two_tables(tmp_path)
    before = _stored(store, source)
    assert [(r["release_id"], r["in_current_file"]) for r in before["tbl-a"][0]] == [
        ("R1", False), ("R1", False), ("R2", True), ("R2", True)]
    assert [(r["release_id"], r["in_current_file"]) for r in before["tbl-b"][0]] == [
        ("R2", False), ("R2", False), ("R3", True), ("R3", True)]
    new = [_fresh("R2", 2022, table, 90, indicator="new", obs_id=obs_id, seq=9)
           for table in TABLES for obs_id in ("o1", "o2")]
    files = _files(tmp_path)
    assert store.replace_release(source, "R2", new, dry_run=True) == {"current": 2, "history": 2}
    assert _files(tmp_path) == files and _stored(store, source) == before  # preview changed nothing
    assert store.replace_release(source, "R2", new) == {"current": 2, "history": 2}

    after = _stored(store, source)
    for table in TABLES:
        assert after[table][1] == before[table][1]  # watermark
        assert len(after[table][0]) == len(before[table][0]) == 4  # no duplicate, no leftover
        for old, now in zip(before[table][0], after[table][0], strict=True):
            swapped = old["release_id"] == "R2"
            assert now["indicator"] == ("new" if swapped else "old")
            assert now["snapshot_sha256"] == (f"{90:064x}" if swapped else old["snapshot_sha256"])
            keep = ("obs_id", "release_id", "_current", "_order_at", "_order_seq", "in_current_file")
            assert {k: now[k] for k in keep} == {k: old[k] for k in keep}  # same membership and sort key
            assert swapped or now == old  # other releases are not touched
    assert store.query_readonly("SELECT table_key, release_id, indicator FROM observations ORDER BY table_key, obs_id").rows == [
        ("tbl-a", "R2", "new"), ("tbl-a", "R2", "new"), ("tbl-b", "R3", "old"), ("tbl-b", "R3", "old")]
    store.import_release(source, "tbl-b", [_fresh("R1", 2021, "tbl-b", 77, seq=7)], _order(7))  # still writable
    assert len(store._stored_rows(store.path_for(source, "tbl-b"))) == 5
    with pytest.raises(PermissionError):
        type(store)(tmp_path, readonly=True).replace_release(source, "R2", new)


def test_replace_release_refuses_other_identities_and_changes_nothing(tmp_path):
    import pytest

    store, source = _two_tables(tmp_path)
    full = [_fresh("R2", 2022, table, 90, indicator="new", obs_id=obs_id, seq=9)
            for table in TABLES for obs_id in ("o1", "o2")]
    wrong_id = [_fresh("R2", 2022, "tbl-a", 90, obs_id="o3", seq=9)]
    wrong_table = [_fresh("R2", 2022, "tbl-c", 90, obs_id=o, seq=9) for o in ("o1", "o2")]
    for rows in (full + [_fresh("R2", 2022, "tbl-a", 90, obs_id="o3", seq=9)],  # one more row
                 full[:-1],  # one fewer
                 full[:3] + wrong_id,  # same count, different identity
                 full[:2] + wrong_table,  # a table the release was never in
                 []):
        files, before = _files(tmp_path), _stored(store, source)
        for dry_run in (True, False):
            with pytest.raises(ValueError, match="replace_identity_mismatch"):
                store.replace_release(source, "R2", rows, dry_run=dry_run)
            assert _files(tmp_path) == files and _stored(store, source) == before
    with pytest.raises(ValueError, match="replace_identity_mismatch"):
        store.replace_release(source, "R9", [_fresh("R9", 2020, "tbl-a", 90)])  # not stored at all
    with pytest.raises(ValueError, match="ObservationIdentity"):
        store.replace_release(source, "R2", [*full, full[0]])
    with pytest.raises(ValueError, match="不属于该来源发布"):
        store.replace_release(source, "R2", [*full[:3], _fresh("R3", 2023, "tbl-b", 90, obs_id="o2")])


def test_replace_release_refuses_inline_pre_301_history(tmp_path):
    from datetime import date

    import pyarrow as pa
    import pyarrow.parquet as pq
    import pytest

    from tests.core.test_models import make_observation

    store, source, table = _release_store(tmp_path)
    path = store.path_for(source, table)
    rows = store._stored_rows(path)  # R1, R2 history and R3 current, all in one inline file as before 301
    assert {(r["release_id"], r["_current"]) for r in rows} == {("R1", False), ("R2", False), ("R3", True)}
    legacy = pa.Table.from_pylist(rows)
    meta = {k: v for k, v in (pq.read_schema(path).metadata or {}).items() if k != b"datumoj.history_segments"}
    pq.write_table(legacy.replace_schema_metadata(meta), path)
    assert b"datumoj.history_segments" not in (pq.read_schema(path).metadata or {})
    fresh = type(store)(tmp_path)
    for release, year in (("R1", 2021), ("R3", 2023)):  # inline history and the inline current row alike
        row = make_observation(release_id=release, released_at=date(year, 1, 1), snapshot_sha256=f"{9:064x}")
        with pytest.raises(ValueError, match="replace_legacy_table"):
            fresh.replace_release(source, release, [row])
    row = make_observation(release_id="R9", released_at=date(2020, 1, 1), snapshot_sha256=f"{9:064x}")
    with pytest.raises(ValueError, match="replace_identity_mismatch"):
        fresh.replace_release(source, "R9", [row])


def _order(seq):
    from datetime import UTC, datetime

    from datumoj.rikolto.core.models import SnapshotOrder

    return SnapshotOrder(fetched_at=datetime(2026, 2, seq, tzinfo=UTC), seq=seq)
