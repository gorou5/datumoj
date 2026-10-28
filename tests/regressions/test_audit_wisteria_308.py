"""Offline audit of standalone read-only stores and fixed-generation release views."""

import json
import os
import sqlite3
from datetime import date, timedelta

import pytest

from datumoj.rikolto import wiring
from datumoj.rikolto.core.contracts import (
    AmbiguousRelease, ContentState, GenerationUnavailable, InvalidGenerationToken, QueryTimeout,
)
from datumoj.rikolto.core.models import GenerationToken, SnapshotOrder, StoreCopy
from datumoj.rikolto.store import observations as module
from datumoj.rikolto.store.catalog import SqliteCatalogStore
from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.store.observations import DuckDBObservationStore, retain_generation
from datumoj.rikolto.testing.samples import (
    T0, make_chunk, make_dataset, make_document, make_observation, make_resource, make_state_generation, sha,
)
from tests.core.test_contracts import (
    TestDuckDBObservationReleases as _DuckReleases, TestFakeObservationReleases as _FakeReleases,
)


class TestDuckReleaseEnumeration(_DuckReleases):
    """Run the release contract suite against real retained Parquet files."""


class TestFakeReleaseEnumeration(_FakeReleases):
    """Keep the fake and production contract behavior aligned."""


def inventory(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


def populate(root, number):
    root.mkdir(parents=True, exist_ok=True)
    dataset = make_dataset('jp-stat:pop', f'population version {number}')
    resource = make_resource(dataset.dataset_key, 'r', 'https://example.go.jp/data')
    catalog = wiring._CatalogQueries(root / 'catalog.sqlite')
    catalog.upsert([dataset], [resource])
    catalog.set_content_state(resource.url, ContentState.content_fetched)
    catalog.close()
    digest = sha(str(number).encode())
    document = make_document('jp-stat:doc', digest, title=f'version {number}')
    chunk = make_chunk(document.doc_key, digest, 'page=1', f'body {number}')
    chunks = SqliteChunkStore(root / 'chunks.sqlite')
    chunks.put_document(document, [chunk])
    chunks.publish_current(document.doc_key, digest, SnapshotOrder(fetched_at=T0, seq=number))
    chunks.close()
    store = DuckDBObservationStore(root)
    obs = make_observation('same', str(number), release_id=f'R{number}', released_at=date(2026, 4, 1),
                           snapshot_sha256=digest)
    store.write_table('jp-stat', 't1', [obs], SnapshotOrder(fetched_at=T0, seq=number))
    return store, dataset, document, chunk


def publish(store, number):
    copies = []
    paths = [store.root / 'catalog.sqlite', store.root / 'chunks.sqlite',
             *sorted((store.root / 'parquet').glob('*/*.parquet'))]
    for path in paths:
        digest = module._file_digest(path)
        copies.append(StoreCopy(name=path.relative_to(store.root).as_posix(), key=f'copies/sha256/{digest}',
                                sha256=digest, size_bytes=path.stat().st_size,
                                format='sqlite' if path.suffix == '.sqlite' else 'parquet'))
    payload = make_state_generation(number).model_copy(update={'stores': copies}).model_dump_json().encode()
    return retain_generation(store.root, payload, store.root)


@pytest.mark.parametrize('store_type,filename', [(SqliteCatalogStore, 'catalog.sqlite'),
                                                (SqliteChunkStore, 'chunks.sqlite')])
def test_readonly_stores_do_not_initialize_or_write(tmp_path, monkeypatch, store_type, filename):
    _, dataset, document, chunk = populate(tmp_path, 1)
    path = tmp_path / filename
    before = inventory(tmp_path)
    path.chmod(0o444)
    statements = []
    connect = sqlite3.connect

    def tracked(*args, **kwargs):
        assert 'mode=ro' in args[0] and kwargs['uri']
        db = connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(sqlite3, 'connect', tracked)
    monkeypatch.setattr(store_type, '_init_schema', lambda *a: pytest.fail('schema initialization'))
    reader = store_type.open_readonly(path)
    try:
        if store_type is SqliteCatalogStore:
            assert reader.get_dataset(dataset.dataset_key) == dataset
            assert reader.resources_for(dataset.dataset_key)[0].state == ContentState.content_fetched
            assert reader.search('population')[0].dataset == dataset
            methods = [lambda: reader.upsert(),
                       lambda: reader.set_content_state('https://example.go.jp/data', ContentState.catalog_only)]
        else:
            assert reader.get_document(document.doc_key) == document
            assert reader.get_document(document.doc_key, document.snapshot_sha256) == document
            assert reader.get_chunk(chunk.chunk_id) == chunk
            assert reader.get_chunk(chunk.chunk_id, chunk.snapshot_sha256) == chunk
            assert reader.chunks_for(document.doc_key) == [chunk]
            assert list(reader.iter_chunks()) == [chunk]
            assert list(reader.iter_chunks(['jp-other'])) == []
            assert reader.current_order(document.doc_key).seq == 1
            methods = [lambda: reader.put_document(document, [chunk]),
                       lambda: reader.publish_current(document.doc_key, document.snapshot_sha256,
                                                      SnapshotOrder(fetched_at=T0, seq=2))]
        for write in methods:
            with pytest.raises(PermissionError):
                write()
    finally:
        reader.close()
        path.chmod(0o644)
    assert inventory(tmp_path) == before  # Includes absence of WAL/journal/SHM files.
    assert not any(sql.split()[0].upper() in {'BEGIN', 'CREATE', 'INSERT', 'UPDATE', 'DELETE', 'ALTER'}
                   for sql in statements)


@pytest.mark.parametrize('store_type', [SqliteCatalogStore, SqliteChunkStore])
@pytest.mark.parametrize('version', [None, 0, 1, 99])
def test_missing_and_incompatible_readonly_databases_are_not_created_or_migrated(tmp_path, store_type, version):
    missing = tmp_path / 'absent' / 'missing.sqlite'
    with pytest.raises((OSError, sqlite3.Error)):
        store_type.open_readonly(missing)
    assert not missing.parent.exists()
    path = tmp_path / 'legacy.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE original (value TEXT)')
        if version is not None:
            db.execute('CREATE TABLE schema_version (version INTEGER NOT NULL)')
            db.execute('INSERT INTO schema_version VALUES (?)', (version,))
    if version == 1 and store_type is SqliteCatalogStore:
        # An incomplete current schema is not repaired by opening it.
        before = inventory(tmp_path)
        reader = store_type.open_readonly(path)
        reader.close()
        assert inventory(tmp_path) == before
        return
    before = inventory(tmp_path)
    with pytest.raises(ValueError, match='schema'):
        store_type.open_readonly(path)
    assert inventory(tmp_path) == before


def test_three_views_stay_on_one_generation_and_release_all_connections(tmp_path):
    store, dataset, document, chunk = populate(tmp_path, 1)
    first = publish(store, 1)
    before = inventory(tmp_path / '.observation-generations')
    with wiring.open_generation_views(tmp_path) as views:
        assert views.generation_token == first == views.observations.pin_generation()
        populate(tmp_path, 2)
        second = publish(store, 2)
        assert second != first
        assert views.catalog.get_dataset(dataset.dataset_key) == dataset
        assert {h.dataset.dataset_key for h in views.catalog.search('population missing')} == {dataset.dataset_key}
        assert views.chunks.get_document(document.doc_key) == document
        assert list(views.chunks.iter_chunks()) == [chunk]
        assert views.observations.query_readonly('SELECT value FROM observations').rows == [(1,)]
        assert views.observations.describe().tables[0].rows == 1
        assert views.observations.table_order('jp-stat', 't1').seq == 1
        assert [e.release_key for e in views.observations.list_releases(source_id='jp-stat')] == ['R1']
        for method in [lambda: views.catalog.upsert(), lambda: views.chunks.put_document(document, [chunk]),
                       lambda: views.observations.write_table('jp-stat', 't1', []),
                       lambda: views.observations.import_release('jp-stat', 't1', [], None)]:
            with pytest.raises(PermissionError):
                method()
        with pytest.raises(InvalidGenerationToken):
            views.observations.query_readonly('SELECT 1', generation_token=second)
    assert {name: inventory(tmp_path / '.observation-generations')[name] for name in before if name != 'current.json'} == {
        name: value for name, value in before.items() if name != 'current.json'}
    with pytest.raises(sqlite3.ProgrammingError):
        views.catalog.get_dataset(dataset.dataset_key)
    with pytest.raises(sqlite3.ProgrammingError):
        views.chunks.get_document(document.doc_key)
    with pytest.raises(RuntimeError, match='关闭'):
        views.observations.query_readonly('SELECT 1')
    views.close()  # Idempotent.
    with wiring.open_generation_views(tmp_path, first) as old:
        assert old.observations.query_readonly('SELECT value FROM observations').rows == [(1,)]
    with wiring.open_generation_views(tmp_path) as latest:
        assert latest.observations.query_readonly('SELECT value FROM observations').rows == [(2,)]


@pytest.mark.parametrize('filename', ['catalog.sqlite', 'chunks.sqlite'])
@pytest.mark.parametrize('change', ['bytes', 'mtime', 'missing', 'record'])
def test_modified_or_missing_retained_sqlite_never_falls_back(tmp_path, filename, change):
    store, _, _, _ = populate(tmp_path, 1)
    first = publish(store, 1)
    populate(tmp_path, 2)
    publish(store, 2)
    base = tmp_path / '.observation-generations/1'
    path = base / 'data' / filename
    if change == 'bytes':
        with path.open('ab') as stream:
            stream.write(b'synthetic corruption')
    elif change == 'mtime':
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    elif change == 'missing':
        path.unlink()
    else:
        record_path = base / 'verified.json'
        record = json.loads(record_path.read_bytes())
        del record['files'][filename]
        record_path.write_text(json.dumps(record))
    before = inventory(tmp_path)
    with pytest.raises(GenerationUnavailable):
        wiring.open_generation_views(tmp_path, first)
    assert inventory(tmp_path) == before


def test_generation_views_validate_token_without_initializing_live_data(tmp_path):
    empty = tmp_path / 'absent'
    with pytest.raises(GenerationUnavailable):
        wiring.open_generation_views(empty)
    assert not empty.exists()
    store, _, _, _ = populate(tmp_path, 1)
    token = publish(store, 1)
    for invalid in ['broken', str(GenerationToken(generation=1, manifest_sha256='0' * 64))]:
        with pytest.raises(InvalidGenerationToken):
            wiring.open_generation_views(tmp_path, invalid)
    with pytest.raises(GenerationUnavailable):
        wiring.open_generation_views(tmp_path, str(GenerationToken(generation=99, manifest_sha256='0' * 64)))
    # The entry point must work with no online stores at all.
    for name in ['catalog.sqlite', 'chunks.sqlite', 'observations.duckdb']:
        (tmp_path / name).unlink()
    before = inventory(tmp_path)
    with wiring.open_generation_views(tmp_path, token) as views:
        assert views.observations.query_readonly('SELECT value FROM observations').rows == [(1,)]
    assert inventory(tmp_path) == before


def test_table_enumeration_only_scans_requested_history_and_honors_timeout(tmp_path, monkeypatch):
    store, _, _, _ = populate(tmp_path, 1)
    for n in range(2, 5):
        obs = make_observation('same', str(n), table_key='other', release_id=f'other-{n}',
                               released_at=date(2026, 4, n))
        store.write_table('jp-stat', 'other', [obs], SnapshotOrder(fetched_at=T0, seq=n))
    token = publish(store, 1)
    scan = store._scan
    scanned = []

    def tracked(files, root, **kwargs):
        scanned.extend(files)
        assert all(p.name == store.path_for('jp-stat', 't1').name for p in files)
        return scan(files, root, **kwargs)

    monkeypatch.setattr(DuckDBObservationStore, '_scan', staticmethod(tracked))
    monkeypatch.setattr(store, '_stored_rows', lambda *a: pytest.fail('loaded history into Python'))
    assert len(store.list_releases(generation_token=token, source_id='jp-stat')) == 4
    assert not scanned  # Source-wide enumeration needs only the compact release index.
    assert len(store.list_releases(generation_token=token, source_id='jp-stat', table_key='t1')) == 1
    assert scanned
    real_clock = module._monotonic
    ticks = iter([real_clock(), float('inf')])
    monkeypatch.setattr(module, '_monotonic', lambda: next(ticks, float('inf')))
    with pytest.raises(QueryTimeout):
        store.list_releases(generation_token=token, source_id='jp-stat', timeout_s=0.1)


def test_legacy_enumeration_keeps_repeated_sha_on_different_days(tmp_path):
    store, _, _, _ = populate(tmp_path, 1)
    for n in [1, 2]:
        obs = make_observation('legacy', str(n), table_key='old', fetched_at=T0 + timedelta(days=n))
        store.write_table('jp-stat', 'old', [obs], SnapshotOrder(fetched_at=obs.fetched_at, seq=n + 1))
    token = publish(store, 1)
    entries = store.list_releases(generation_token=token, source_id='jp-stat', table_key='old')
    assert len(entries) == 2 and entries[0].release_key == entries[1].release_key
    with pytest.raises(AmbiguousRelease):
        store.query_readonly('SELECT value FROM observations', generation_token=token,
                             as_of_release=entries[0].release_key)
    for n, entry in enumerate(entries, 1):
        assert store.query_readonly('SELECT value FROM observations', generation_token=token,
                                    as_of_release=entry).rows == [(n,)]


def test_generation_views_close_first_store_if_second_open_fails(tmp_path, monkeypatch):
    store, _, _, _ = populate(tmp_path, 1)
    publish(store, 1)
    opened = []
    original = wiring._CatalogQueries.open_readonly

    def catalog(path):
        result = original(path)
        opened.append(result)
        return result

    def fail(path):
        raise sqlite3.DatabaseError("synthetic corrupt chunks")

    monkeypatch.setattr(wiring._CatalogQueries, 'open_readonly', catalog)
    monkeypatch.setattr(SqliteChunkStore, 'open_readonly', fail)
    with pytest.raises(GenerationUnavailable):
        wiring.open_generation_views(tmp_path)
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].get_dataset('jp-stat:pop')


def test_views_cannot_switch_tokens_and_revalidate_each_observation_query(tmp_path, monkeypatch):
    store, _, _, _ = populate(tmp_path, 1)
    token = publish(store, 1)
    # Generation opens may stat large data, but must never rehash it.
    monkeypatch.setattr(module, '_file_digest', lambda *a: pytest.fail('rehashing at open'))
    with wiring.open_generation_views(tmp_path, token) as views:
        with pytest.raises(AttributeError):
            views.observations.generation_token = 'changed'
        with pytest.raises(AttributeError):
            views.generation_token = 'changed'
        path = tmp_path / '.observation-generations/1/data/catalog.sqlite'
        with path.open('ab') as stream:
            stream.write(b'synthetic mutation')
        for read in [lambda: views.observations.query_readonly('SELECT 1 WHERE false'),
                     lambda: views.observations.list_releases(source_id='jp-stat'),
                     views.observations.describe]:
            with pytest.raises(GenerationUnavailable):
                read()


def test_readonly_uri_handles_reserved_path_characters(tmp_path):
    path = tmp_path / 'catalog ? #.sqlite'
    writer = SqliteCatalogStore(path)
    dataset = make_dataset('jp-stat:pop', 'synthetic population')
    writer.upsert([dataset])
    writer.close()
    before = inventory(tmp_path)
    reader = SqliteCatalogStore.open_readonly(path)
    try:
        assert reader.get_dataset(dataset.dataset_key) == dataset
    finally:
        reader.close()
    assert inventory(tmp_path) == before
