"""Offline append-only history, migration and generation storage regressions."""

import builtins
import errno
import json
import os
from collections import Counter
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.core.contracts import GenerationUnavailable, QueryError
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import SnapshotOrder, StoreCopy
from datumoj.rikolto.store import observations as module
from datumoj.rikolto.store.observations import (
    DuckDBObservationStore,
    check_history_migration,
    retain_generation,
)
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0, make_observation, make_snapshot, make_state_generation
from tests.store.test_observation_releases import publish_fixture


def observation(i, **changes):
    return make_observation('same', str(i), fetched_at=T0 + timedelta(hours=i),
                            snapshot_sha256=f'{i % 3:064x}', release_id=f'r{i}',
                            released_at=date(2026, 4, 1) + timedelta(days=i // 3), **changes)


def write(store, i, rows=None, *, archive=False):
    rows = [observation(i)] if rows is None else rows
    method = store.import_release if archive else store.write_table
    return method('jp-stat', 't1', rows, SnapshotOrder(fetched_at=T0, seq=i + 1))


def legacy_write(store, i, rows=None, *, archive=False):
    """301 algorithm oracle: cumulative files, event replacement, current membership."""
    path = store.path_for('jp-stat', 't1')
    rows = [observation(i)] if rows is None else rows
    old = store._stored_rows(path)
    order = SnapshotOrder(fetched_at=T0, seq=i + 1)
    current = [r for r in old if r['_current']]
    advance = not archive or not current or min(module._release_key(module._row(o), order) for o in rows) > max(
        module._release_key(r, SnapshotOrder(fetched_at=r['_order_at'], seq=r['_order_seq'])) for r in current)
    if advance:
        for row in old:
            row['_current'] = False
    incoming = [{**module._row(o), '_order_at': order.fetched_at, '_order_seq': order.seq,
                 '_current': advance} for o in rows]
    keys = {module._event_key(r) for r in incoming}
    history = pa.Table.from_pylist([r for r in old if module._event_key(r) not in keys] + incoming,
                                  schema=module._HISTORY_SCHEMA)
    metadata = {module._ORDER_KEY: order.model_dump_json().encode()} if advance else dict(pq.read_schema(path).metadata)
    previous = bool(old)
    if previous:
        target = path.with_name(os.urandom(16).hex() + '.parquet')
        module._atomic_parquet(history.replace_schema_metadata({module._HISTORY_ONLY: b'1'}), target, immutable=True)
        metadata[module._HISTORY_FILE] = target.name.encode()
    table = pa.Table.from_pylist(incoming, schema=module._HISTORY_SCHEMA) if advance else pq.read_table(path)
    module._atomic_parquet(table.replace_schema_metadata(metadata), path)


def segments(root):
    return sorted((root / 'parquet-history').glob('*/*/*.parquet'))


def test_linear_history_and_no_old_segment_reads_on_write(tmp_path, monkeypatch):
    store, legacy = DuckDBObservationStore(tmp_path / 'new'), DuckDBObservationStore(tmp_path / 'old')
    n, batch_size = 24, 128
    # Vary payloads across releases so compression cannot hide cumulative copies.
    batches = [[observation(i).model_copy(update={'obs_id': f'o-{j}', 'value': observation(i).value + j,
                                                 'dims': {'synthetic': sha256(f'{i}:{j}'.encode()).hexdigest()}})
                for j in range(batch_size)] for i in range(n)]
    write(store, 0, batches[0])
    original = pq.read_schema

    def schema(path, *args, **kwargs):
        assert 'parquet-history' not in Path(path).parts
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(pq, 'read_schema', schema)
        patch.setattr(store, '_stored_rows', lambda *a: pytest.fail('write loaded old history'))
        for i in range(1, n):
            write(store, i, batches[i])
    for i in range(n):
        legacy_write(legacy, i, batches[i])
    files = segments(store.root)
    sizes = [p.stat().st_size for p in files]
    assert len(files) == n and all(pq.read_metadata(p).num_rows == batch_size for p in files)
    assert sum(sizes) <= n * sizes[0] + 4096
    old_size = sum(p.stat().st_size for p in (legacy.root / 'parquet').glob('*/*.parquet')
                   if module._HISTORY_ONLY in (pq.read_schema(p).metadata or {}))
    assert sum(pq.read_metadata(p).num_rows for p in (legacy.root / 'parquet').glob('*/*.parquet')
               if module._HISTORY_ONLY in (pq.read_schema(p).metadata or {})) == batch_size * sum(range(2, n + 1))
    print('DISK', json.dumps({'writes': n, 'events': n * batch_size, 'segment_bytes': sum(sizes),
                              'single_segment_bytes': sizes[0], 'legacy_history_bytes': old_size}))


def test_current_query_never_opens_or_lists_history(tmp_path, monkeypatch):
    store = DuckDBObservationStore(tmp_path)
    for i in range(4):
        write(store, i)
    opened = []
    path_open, builtin_open, schema, glob = Path.open, builtins.open, pq.read_schema, Path.glob

    def record(path):
        if isinstance(path, (str, os.PathLike)):
            opened.append(Path(path))
            assert 'parquet-history' not in Path(path).parts
            assert module._PUBLICATION_SOURCE not in Path(path).parts

    def tracked(path, *args, **kwargs):
        record(path)
        return path_open(path, *args, **kwargs)

    def tracked_builtin(path, *args, **kwargs):
        record(path)
        return builtin_open(path, *args, **kwargs)

    def tracked_schema(path, *args, **kwargs):
        record(path)
        return schema(path, *args, **kwargs)

    def tracked_glob(path, *args, **kwargs):
        assert 'parquet-history' not in path.parts
        assert module._PUBLICATION_SOURCE not in path.parts
        return glob(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', tracked)
    monkeypatch.setattr(builtins, 'open', tracked_builtin)
    monkeypatch.setattr(pq, 'read_schema', tracked_schema)
    monkeypatch.setattr(Path, 'glob', tracked_glob)
    # DuckDB opens via native code: removing access to segments proves its scan
    # does not need them either; schema/open recording checks the Python layer.
    history = tmp_path / 'parquet-history'
    hidden = tmp_path / 'hidden-history'
    history.rename(hidden)
    try:
        store = DuckDBObservationStore(tmp_path)
        assert store.query_readonly('SELECT value FROM observations').rows == [(3,)]
        assert store.describe().tables[0].rows == 1
    finally:
        hidden.rename(history)
    assert opened


def test_results_match_cumulative_301_row_for_row(tmp_path):
    new, old = DuckDBObservationStore(tmp_path / 'new'), DuckDBObservationStore(tmp_path / 'old')
    operations = [(0, None, False), (4, None, False), (2, None, True), (4, None, False),
                  (6, [], False), (7, None, False), (1, None, True), (8, None, False)]
    tokens = []
    for number, (i, rows, archive) in enumerate(operations, 1):
        write(new, i, rows, archive=archive)
        legacy_write(old, i, rows, archive=archive)
        manifest = make_state_generation(number)
        tokens.append((publish_fixture(new, manifest), publish_fixture(old, manifest)))
    for new_token, old_token in tokens:
        for cutoff in (None, date(2026, 4, 1), date(2026, 4, 2), date(2026, 4, 9), 'r0'):
            options = {'as_of_release': cutoff}
            a = new.query_readonly('SELECT * FROM observations ORDER BY source_id,table_key,obs_id', generation_token=new_token, **options)
            b = old.query_readonly('SELECT * FROM observations ORDER BY source_id,table_key,obs_id', generation_token=old_token, **options)
            assert a.columns == b.columns and a.rows == b.rows and a.provenance == b.provenance


def legacy_directory(tmp_path):
    store = DuckDBObservationStore(tmp_path)
    for i in range(5):
        legacy_write(store, i)
    path = store.path_for('jp-stat', 't1')
    old = store._stored_rows(path)
    histories = [p for p in path.parent.glob('*.parquet') if p != path]
    other = path.with_name('unrelated.txt')
    other.write_bytes(b'synthetic unrelated file')
    # A marked file not contained in this table's history must survive.
    foreign = path.with_name('f' * 32 + '.parquet')
    rows = [{**old[0], 'table_key': 'other'}]
    module._atomic_parquet(pa.Table.from_pylist(rows, schema=module._HISTORY_SCHEMA).replace_schema_metadata({module._HISTORY_ONLY: b'1'}), foreign)
    return store, path, old, histories, other, foreign


def open_writer(root):
    store = DuckDBObservationStore(root)
    store._migrate_history()
    return store


def test_migration_is_lossless_readonly_inspection_and_idempotent(tmp_path):
    store, path, old, histories, other, foreign = legacy_directory(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    report = check_history_migration(tmp_path)
    assert report == {'tables': 1, 'reclaimable_bytes': sum(p.stat().st_size for p in histories)}
    assert before == {p: p.read_bytes() for p in before}
    migrated = open_writer(tmp_path)
    assert module._events(pa.Table.from_pylist(migrated._stored_rows(path), schema=module._HISTORY_SCHEMA)) == module._events(pa.Table.from_pylist(old, schema=module._HISTORY_SCHEMA))
    assert all(not p.exists() for p in histories) and other.exists() and foreign.exists()
    assert len(segments(tmp_path)) == 1
    open_writer(tmp_path)
    assert len(segments(tmp_path)) == 1 and check_history_migration(tmp_path)['tables'] == 0
    write(migrated, 5)
    assert len(segments(tmp_path)) == 2 and len(migrated._stored_rows(path)) == 6


@pytest.mark.parametrize('failure', ['segment', 'current', 'cleanup', 'finish'])
def test_migration_retry_after_interruption(tmp_path, monkeypatch, failure):
    store, path, old, histories, other, foreign = legacy_directory(tmp_path)
    original_atomic, original_unlink = module._atomic_parquet, Path.unlink
    failed = False

    def atomic(table, destination, **kwargs):
        nonlocal failed
        metadata = table.schema.metadata or {}
        should_fail = (failure == 'segment' and kwargs.get('immutable') or
                       failure == 'current' and destination == path and module._MIGRATION in metadata or
                       failure == 'finish' and destination == path and module._MIGRATION not in metadata)
        if should_fail and not failed:
            failed = True
            raise OSError('synthetic interruption')
        return original_atomic(table, destination, **kwargs)

    def unlink(target, *args, **kwargs):
        nonlocal failed
        if failure == 'cleanup' and target == histories[1] and not failed:
            failed = True
            raise OSError('synthetic interruption')
        return original_unlink(target, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module, '_atomic_parquet', atomic)
        patch.setattr(Path, 'unlink', unlink)
        with pytest.raises(OSError, match='synthetic interruption'):
            open_writer(tmp_path)
    retried = open_writer(tmp_path)
    assert module._events(pa.Table.from_pylist(retried._stored_rows(path), schema=module._HISTORY_SCHEMA)) == module._events(pa.Table.from_pylist(old, schema=module._HISTORY_SCHEMA))
    assert len(segments(tmp_path)) == 1 and all(not p.exists() for p in histories)
    assert other.exists() and foreign.exists()


def test_migration_mismatch_never_deletes_old_files(tmp_path, monkeypatch):
    store, path, old, histories, other, foreign = legacy_directory(tmp_path)
    before = {p: p.read_bytes() for p in path.parent.iterdir()}
    original = DuckDBObservationStore._new_segment

    def corrupt(self, current, rows):
        # Keep the same count, but change a complete identity and ordering key.
        rows = [dict(r) for r in rows]
        rows[0]['snapshot_sha256'] = 'e' * 64
        rows[0]['_order_seq'] += 100
        return original(self, current, rows)

    monkeypatch.setattr(DuckDBObservationStore, '_new_segment', corrupt)
    with pytest.raises(QueryError, match='核对不一致'):
        open_writer(tmp_path)
    assert before == {p: p.read_bytes() for p in path.parent.iterdir()}


def retain_fixture(tmp_path, *, generation=1):
    store = DuckDBObservationStore(tmp_path)
    manifest_path = tmp_path / 'manifest.sqlite'
    if not manifest_path.exists():
        manifest = SqliteManifest(manifest_path)
        snapshot = make_snapshot('jp-stat', data=b'synthetic immutable raw')
        manifest.record_snapshot(snapshot)
        manifest.close()
        raw = tmp_path / f'raw/{snapshot.sha256[:2]}/{snapshot.sha256}'
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b'synthetic immutable raw')
        write(store, 0)
        write(store, 1)
    copies = []
    for p in [manifest_path, *sorted((tmp_path / 'parquet').glob('*/*.parquet'))]:
        digest = module._file_digest(p)
        copies.append(StoreCopy(name=p.relative_to(tmp_path).as_posix(), key=f'copies/sha256/{digest}',
                                sha256=digest, size_bytes=p.stat().st_size,
                                format='sqlite' if p == manifest_path else 'parquet'))
    payload = make_state_generation(generation).model_copy(update={'stores': copies}).model_dump_json().encode()
    return store, retain_generation(tmp_path, payload, tmp_path)


def test_generations_share_inodes_and_copy_mutable_live_files(tmp_path):
    store, token = retain_fixture(tmp_path)
    data1 = tmp_path / '.observation-generations/1/data'
    store, token2 = retain_fixture(tmp_path, generation=2)
    data2 = tmp_path / '.observation-generations/2/data'
    for source in [*segments(tmp_path), *list((tmp_path / 'raw').glob('*/*'))]:
        name = source.relative_to(tmp_path)
        assert source.stat().st_ino == (data1 / name).stat().st_ino == (data2 / name).stat().st_ino
    for source in [tmp_path / 'manifest.sqlite', store.path_for('jp-stat', 't1')]:
        name = source.relative_to(tmp_path)
        assert source.stat().st_ino != (data1 / name).stat().st_ino
        assert (data1 / name).stat().st_ino == (data2 / name).stat().st_ino
    record = json.loads((data2.parent / 'verified.json').read_bytes())
    counts = Counter(record['file_storage'].values())
    amounts = {mode: sum(record['files'][n][0] for n, m in record['file_storage'].items() if m == mode) for mode in counts}
    assert counts['link'] >= 5 and counts['copy'] >= 3
    first = json.loads((data1.parent / 'verified.json').read_bytes())
    print('GENERATION', json.dumps({'first_files': dict(Counter(first['file_storage'].values())),
                                    'second_files': dict(counts), 'second_logical_bytes': amounts}))
    assert store.query_readonly('SELECT value FROM observations', generation_token=token).rows == [(1,)]


def test_link_failure_falls_back_and_records_each_file(tmp_path, monkeypatch):
    retain_fixture(tmp_path)

    def cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, 'synthetic different filesystem')

    monkeypatch.setattr(os, 'link', cross_device)
    store, token = retain_fixture(tmp_path, generation=2)
    base = tmp_path / '.observation-generations/2'
    record = json.loads((base / 'verified.json').read_bytes())
    assert set(record['file_storage']) == set(record['files'])
    assert set(record['file_storage'].values()) == {'copy'}
    for source in [*segments(tmp_path), *list((tmp_path / 'raw').glob('*/*'))]:
        assert source.stat().st_ino != (base / 'data' / source.relative_to(tmp_path)).stat().st_ino
    assert store.query_readonly('SELECT value FROM observations', generation_token=token).rows == [(1,)]


@pytest.mark.parametrize('dependency', ['raw', 'history'])
def test_changed_linked_file_invalidates_all_dependent_tokens(tmp_path, dependency):
    store, token = retain_fixture(tmp_path)
    store, token2 = retain_fixture(tmp_path, generation=2)
    source = segments(tmp_path)[0] if dependency == 'history' else next((tmp_path / 'raw').glob('*/*'))
    with source.open('ab') as stream:
        stream.write(b'synthetic corruption')
    for fixed in [token, token2]:
        with pytest.raises(GenerationUnavailable):
            store.query_readonly('SELECT 1 WHERE false', generation_token=fixed)


def test_failed_current_replace_unpublishes_segment(tmp_path, monkeypatch):
    store = DuckDBObservationStore(tmp_path)
    write(store, 0)
    before = set(segments(tmp_path))
    aliases = set((store._parquet / module._PUBLICATION_SOURCE).glob('*.parquet'))
    path = store.path_for('jp-stat', 't1')
    current = path.read_bytes()
    replace = os.replace

    def fail(source, destination):
        if destination == path:
            raise OSError('synthetic current failure')
        return replace(source, destination)

    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError, match='synthetic current'):
        write(store, 1)
    assert set(segments(tmp_path)) == before and path.read_bytes() == current
    assert set((store._parquet / module._PUBLICATION_SOURCE).glob('*.parquet')) == aliases


def test_real_push_reuses_online_immutable_files(tmp_path):
    # Empty current avoids unrelated chunk/evidence fixtures in the state validator.
    store, token = retain_fixture(tmp_path)
    write(store, 3, [])
    remote = FakeStateObjectStore()
    manager = wiring._ReleaseStateManager(remote, tmp_path)
    # Retained synthetic fixture generation is local only; real remote starts at 1.
    import shutil
    shutil.rmtree(tmp_path / '.observation-generations')
    manager.push()
    manager.push()
    for source in [*segments(tmp_path), *list((tmp_path / 'raw').glob('*/*'))]:
        name = source.relative_to(tmp_path)
        for i in [1, 2]:
            assert source.stat().st_ino == (tmp_path / f'.observation-generations/{i}/data' / name).stat().st_ino
    restored = tmp_path / 'restored'
    manager.restore(restored)
    replica = DuckDBObservationStore(restored)
    assert replica.query_readonly('SELECT value FROM observations', as_of_release='r0').rows == [(0,)]


def test_first_write_migrates_301_and_readonly_open_preserves_original(tmp_path):
    store, path, old, histories, other, foreign = legacy_directory(tmp_path)
    before = path.read_bytes()
    reopened = DuckDBObservationStore(tmp_path)
    assert path.read_bytes() == before and all(p.exists() for p in histories)
    write(reopened, 5)
    assert all(not p.exists() for p in histories) and other.exists() and foreign.exists()
    assert len(reopened._stored_rows(path)) == len(old) + 1


def test_real_restore_of_301_publication_preserves_history(tmp_path):
    store, token = retain_fixture(tmp_path)
    # Recreate a 301 publication with empty current and preserved old releases.
    import shutil
    shutil.rmtree(tmp_path / 'parquet')
    shutil.rmtree(tmp_path / 'parquet-history')
    for i in range(3):
        legacy_write(store, i)
    legacy_write(store, 3, [])
    shutil.rmtree(tmp_path / '.observation-generations')
    manager = wiring._ReleaseStateManager(FakeStateObjectStore(), tmp_path)
    manager.push()
    restored = tmp_path / 'restored'
    manager.restore(restored)
    replica = DuckDBObservationStore(restored)
    assert replica.query_readonly('SELECT value FROM observations', as_of_release='r1').rows == [(1,)]
    write(replica, 5, [])
    assert replica.query_readonly('SELECT value FROM observations', as_of_release='r1').rows == [(1,)]


def test_empty_observation_generation_can_be_retained(tmp_path):
    path = tmp_path / 'manifest.sqlite'
    SqliteManifest(path).close()
    digest = module._file_digest(path)
    copy = StoreCopy(name='manifest.sqlite', key=f'copies/sha256/{digest}', sha256=digest,
                     size_bytes=path.stat().st_size, format='sqlite')
    payload = make_state_generation().model_copy(update={'stores': [copy]}).model_dump_json().encode()
    token = retain_generation(tmp_path, payload, tmp_path)
    store = DuckDBObservationStore(tmp_path)
    assert store.query_readonly('SELECT count(*) FROM observations', generation_token=token).rows == [(0,)]


def test_inspection_excludes_content_still_owned_by_other_hardlinks(tmp_path):
    store, path, old, histories, other, foreign = legacy_directory(tmp_path)
    held = histories[0]
    retained = tmp_path / 'synthetic-retained-file'
    os.link(held, retained)
    expected = sum(p.stat().st_size for p in histories if p != held)
    assert check_history_migration(tmp_path) == {'tables': 1, 'reclaimable_bytes': expected}
    open_writer(tmp_path)
    assert retained.exists() and not held.exists()
