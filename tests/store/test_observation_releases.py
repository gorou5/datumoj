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


def _releases(store, source, table):
    return sorted(r["release_id"] for r in store._stored_rows(store.path_for(source, table)))


def test_delete_release_drops_only_that_historical_release(tmp_path):
    import pytest

    store, source, table = _release_store(tmp_path)
    watermark = store.table_order(source, table)
    path = store.path_for(source, table)
    assert _releases(store, source, table) == ["R1", "R2", "R3"]
    assert store.delete_release(source, "R1", dry_run=True) == 1
    assert _releases(store, source, table) == ["R1", "R2", "R3"]
    with pytest.raises(ValueError, match="replace_current_release"):
        store.delete_release(source, "R3")
    assert _releases(store, source, table) == ["R1", "R2", "R3"]
    assert store.delete_release(source, "R1") == 1
    assert store.delete_release(source, "R1") == 0
    assert _releases(store, source, table) == ["R2", "R3"]
    assert store.table_order(source, table) == watermark
    assert store.query_readonly("SELECT release_id FROM observations").rows == [("R3",)]
    assert [p.name for p in (tmp_path / "parquet-history" / source / path.stem).glob("*.parquet")]
    store.import_release(source, table, [_again(source, table)], _order(9))  # history stays writable afterwards
    assert _releases(store, source, table) == ["R1", "R2", "R3"]
    with pytest.raises(PermissionError):
        type(store)(tmp_path, readonly=True).delete_release(source, "R2")


def _order(seq):
    from datetime import UTC, datetime

    from datumoj.rikolto.core.models import SnapshotOrder

    return SnapshotOrder(fetched_at=datetime(2026, 2, seq, tzinfo=UTC), seq=seq)


def _again(source, table):
    from datetime import UTC, date, datetime

    from tests.core.test_models import make_observation

    return make_observation(release_id="R1", released_at=date(2021, 1, 1), snapshot_sha256=f"{9:064x}",
                            fetched_at=datetime(2026, 2, 9, tzinfo=UTC))
