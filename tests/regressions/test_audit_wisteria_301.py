"""Release semantics and bounded, offline generation reads (WISTERIA-301)."""

import asyncio
import builtins
import json
import os
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from mcp import Client
from typer.testing import CliRunner

from datumoj.rikolto import cli, mcp_server, wiring
from datumoj.rikolto.core.contracts import (
    OBSERVATION_COLUMNS,
    GenerationUnavailable,
    InvalidGenerationToken,
    QueryTimeout,
)
from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.models import GenerationToken, SnapshotOrder, StoreCopy
from datumoj.rikolto.store import observations as module
from datumoj.rikolto.store.observations import DuckDBObservationStore, retain_generation
from datumoj.rikolto.testing.fakes import FakeStateObjectStore
from datumoj.rikolto.testing.samples import T0, make_observation, make_snapshot, make_state_generation
from tests.core.test_contracts import TestDuckDBObservationReleases as _ReleaseSuite
from tests.store.test_observation_releases import publish_fixture


class TestReleaseRegressions(_ReleaseSuite):
    """Includes A→B→A, replacements/deletions, same-day order, namespaces and tokens."""


def test_unpublished_legacy_directory_and_nullable_columns(tmp_path):
    store = DuckDBObservationStore(tmp_path)
    obs = make_observation('legacy', None, note='秘匿')
    path = store.path_for('jp-stat', 't1')
    path.parent.mkdir(parents=True)
    old_schema = pa.schema([field for field in module._SCHEMA if field.name in OBSERVATION_COLUMNS])
    pq.write_table(pa.Table.from_pylist([module._row(obs)], schema=old_schema), path)
    result = store.query_readonly('SELECT obs_id,value,note,release_id,released_at FROM observations')
    assert result.rows == [('legacy', None, '秘匿', None, None)]
    assert result.provenance == [obs] and result.generation_token is None
    with pytest.raises(GenerationUnavailable):
        store.pin_generation()
    with pytest.raises(GenerationUnavailable):
        store.query_readonly('SELECT 1', as_of_release=T0.date())
    with pytest.raises(InvalidGenerationToken):
        store.query_readonly('SELECT 1', generation_token='broken')
    assert pq.read_schema(path).names == list(OBSERVATION_COLUMNS)


def test_legacy_snapshot_order_restored_from_original_manifest(tmp_path):
    store = DuckDBObservationStore(tmp_path)
    manifest = SqliteManifest(tmp_path / 'manifest.sqlite')
    snap = make_snapshot('jp-stat', data=b'old')
    manifest.record_snapshot(snap)
    obs = make_observation('old', '7', snapshot_sha256=snap.sha256, fetched_at=snap.fetched_at)
    path = store.path_for('jp-stat', 't1')
    path.parent.mkdir(parents=True)
    schema = pa.schema([f for f in module._SCHEMA if f.name in OBSERVATION_COLUMNS])
    pq.write_table(pa.Table.from_pylist([module._row(obs)], schema=schema), path)
    # Fixture retains the original ordering manifest along with old Parquet.
    generation = make_state_generation()
    token = publish_fixture(store, generation)
    result = store.query_readonly('SELECT * FROM observations', generation_token=token, as_of_release=snap.sha256)
    assert result.provenance == [obs]
    manifest.close()


def large_generation(tmp_path):
    store = DuckDBObservationStore(tmp_path)
    manifest = SqliteManifest(tmp_path / 'manifest.sqlite')
    for table in range(6):
        # More than 36 MiB of raw, with multiple releases per table.
        body = bytes([table]) * (6 * 1024 * 1024)
        snap = make_snapshot('jp-stat', data=body, fetched_at=T0 + timedelta(hours=table))
        manifest.record_snapshot(snap)
        raw = tmp_path / f'raw/{snap.sha256[:2]}/{snap.sha256}'
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(body)
        for release in range(4):
            day = date(2026, 4, release + 1)
            obs = [make_observation(f'o-{i}', str(i + release), table_key=f't{table}',
                                   snapshot_sha256=snap.sha256, fetched_at=snap.fetched_at,
                                   release_id=f'r{release}', released_at=day) for i in range(200)]
            store.write_table('jp-stat', f't{table}', obs, SnapshotOrder(fetched_at=snap.fetched_at, seq=release + 1))
    manifest.close()
    stores = []
    for path in [tmp_path / 'manifest.sqlite', *sorted((tmp_path / 'parquet').glob('*/*.parquet'))]:
        digest = module._file_digest(path)
        stores.append(StoreCopy(name=path.relative_to(tmp_path).as_posix(), key=f'copies/sha256/{digest}',
                                sha256=digest, size_bytes=path.stat().st_size,
                                format='sqlite' if path.suffix == '.sqlite' else 'parquet'))
    generation = make_state_generation().model_copy(update={'stores': stores})
    token = retain_generation(tmp_path, generation.model_dump_json().encode(), tmp_path)
    return store, token


def test_large_generation_queries_do_not_read_raw_or_rehash_dependencies(tmp_path, monkeypatch):
    store, token = large_generation(tmp_path)
    reads = []
    original_open, original_builtin = Path.open, builtins.open

    def tracked(path, *args, **kwargs):
        reads.append(Path(path))
        return original_open(path, *args, **kwargs)

    def builtin_tracked(path, *args, **kwargs):
        if isinstance(path, (str, os.PathLike)):
            reads.append(Path(path))
        return original_builtin(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', tracked)
    monkeypatch.setattr(builtins, 'open', builtin_tracked)
    monkeypatch.setattr(module, '_file_digest', lambda path: pytest.fail('queries must not hash copies/raw'))
    monkeypatch.setattr(pq, 'read_table', lambda *args, **kwargs: pytest.fail('query eagerly read Parquet into Python'))
    # Detect loading observation history into Python as well as raw reads.
    monkeypatch.setattr(store, '_stored_rows', lambda path: pytest.fail('query loaded history into Python'))
    for options in [{}, {'generation_token': token}, {'generation_token': token, 'as_of_release': 'r0'}]:
        result = store.query_readonly("SELECT value FROM observations WHERE table_key='t0' AND obs_id='o-1'", **options)
        assert result.rows == [(1 if 'as_of_release' in options else 4,)]
    assert reads and not any('raw' in path.parts for path in reads)
    assert all(path.name in {'current.json', 'manifest.json', 'verified.json'} for path in reads)
    record = json.loads((tmp_path / '.observation-generations/1/verified.json').read_bytes())
    assert record['storage'] == 'copy' and record['size_bytes'] >= 36 * 1024 * 1024


@pytest.mark.parametrize('dependency', ['raw', 'parquet', 'manifest.sqlite'])
def test_dependency_changes_fail_without_fallback(tmp_path, dependency):
    store, token = large_generation(tmp_path)
    base = tmp_path / '.observation-generations/1/data'
    path = next((base / dependency).rglob('*.*' if dependency == 'parquet' else '*'), None) if dependency != 'manifest.sqlite' else base / dependency
    if path.is_dir():
        path = next(p for p in path.rglob('*') if p.is_file())
    stat = path.stat()
    if dependency == 'raw':
        with path.open('ab') as stream:
            stream.write(b'changed')
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # size alone must detect this.
    else:
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
    for options in [{}, {'generation_token': token}]:
        with pytest.raises(GenerationUnavailable):
            store.query_readonly('SELECT 1 WHERE false', **options)


@pytest.mark.parametrize('stage', ['load', 'selection'])
def test_timeout_covers_generation_loading_and_selection(tmp_path, monkeypatch, stage):
    store, token = large_generation(tmp_path)
    now = [0.0]
    monkeypatch.setattr(module, '_monotonic', lambda: now[0])
    if stage == 'load':
        original = module._dependency
        def delayed(*args):
            result = original(*args)
            now[0] = 11.0
            return result
        monkeypatch.setattr(module, '_dependency', delayed)
    else:
        original = store._scan
        def delayed(*args, **kwargs):
            result = original(*args, **kwargs)
            now[0] = 11.0
            return result
        monkeypatch.setattr(store, '_scan', delayed)
    with pytest.raises(QueryTimeout):
        store.query_readonly('SELECT 1', generation_token=token, as_of_release='r0', timeout_s=10)


def test_import_existing_source_fixture_and_real_publication(tmp_path, monkeypatch):
    monkeypatch.setenv('DATUMOJ_VECTORS', 'off')
    deps = wiring.build(tmp_path)
    fixture = Path('tests/fixtures/estat_files/000013168605_jinsui_pref_population_2000-2020.xlsx')
    runner = CliRunner()
    monkeypatch.setattr(cli, '_build_wiring', lambda root: deps)
    command = ['--data-dir', str(tmp_path), 'observations', 'import-release', '--source', 'jp-estat-files',
               '--file', str(fixture), '--release-id', 'archive-2022', '--released-at', '2022-07-20']
    imported = runner.invoke(cli.app, command)
    assert imported.exit_code == 0, imported.output
    report = json.loads(imported.output)
    rows = deps.observations.query_readonly('SELECT * FROM observations LIMIT 2').provenance
    assert len(rows) == 2
    assert all(o.release_id == 'archive-2022' and o.released_at == date(2022, 7, 20) for o in rows)
    snapshot = deps.manifest.get_snapshot(report['snapshot_id'])
    assert snapshot.resource_url.startswith('https://www.e-stat.go.jp/stat-search/file-download?')
    assert snapshot.sha256 == sha256(fixture.read_bytes()).hexdigest()
    assert snapshot.fetched_at.date() != date(2022, 7, 20)
    assert deps.raw.get(snapshot.sha256) == fixture.read_bytes()
    # Importing an older explicit publication preserves current and its watermark.
    table = rows[0].table_key
    watermark = deps.observations.table_order('jp-estat-files', table)
    older_command = command[:-3] + ['archive-2020', '--released-at', '2020-07-20']
    imported_old = runner.invoke(cli.app, older_command)
    assert imported_old.exit_code == 0, imported_old.output
    assert deps.observations.table_order('jp-estat-files', table) == watermark
    assert deps.observations.query_readonly('SELECT DISTINCT release_id FROM observations').rows == [('archive-2022',)]
    remote = FakeStateObjectStore()
    manager = wiring._ReleaseStateManager(remote, tmp_path)
    generation = manager.push()
    token = deps.observations.pin_generation()
    assert GenerationToken.parse(token).generation == generation.generation
    historical = deps.observations.query_readonly('SELECT * FROM observations ORDER BY obs_id LIMIT 2', as_of_release='archive-2020')
    assert all(o.release_id == 'archive-2020' and o.released_at == date(2020, 7, 20) for o in historical.provenance)
    analysis = deps.tools.query_sql('SELECT * FROM observations LIMIT 2', generation_token=token,
                                    as_of_release='archive-2020')
    assert analysis['data']['generation_token'] == token
    assert analysis['evidence_inputs']['as_of_release'] == 'archive-2020'
    assert all(p['release_id'] == 'archive-2020' for p in analysis['data']['provenance'])
    legacy = deps.tools.query_sql('SELECT source_id,table_key,obs_id FROM observations ORDER BY obs_id DESC LIMIT 2')
    assert 'generation_token' not in legacy['data'] and 'row_evidence' not in legacy['data']
    assert [p['obs_id'] for p in legacy['data']['provenance']] == [row[2]['content'] for row in legacy['data']['rows']]
    # A second publication keeps the previous token and restores exact release history.
    manager.push()
    assert deps.observations.query_readonly('SELECT 1', generation_token=token).generation_token == token
    restored = tmp_path / 'restored'
    manager.restore(restored)
    replica = DuckDBObservationStore(restored)
    assert replica.query_readonly('SELECT * FROM observations ORDER BY obs_id LIMIT 2', as_of_release='archive-2020').provenance == historical.provenance
    from datumoj.rikolto.core.pull import pull
    pulled = tmp_path / 'pulled'
    monkeypatch.setattr(cli, '_pull_state', lambda root, number: pull(root, number, store=remote))
    monkeypatch.setattr(wiring, 'build_state', lambda root: manager)
    cli.pull_state(pulled, 1)
    pulled_store = DuckDBObservationStore(pulled)
    first_token = pulled_store.pin_generation()
    cli.pull_state(pulled, 2)
    assert GenerationToken.parse(pulled_store.pin_generation()).generation == 2
    assert pulled_store.query_readonly('SELECT 1', generation_token=first_token).generation_token == first_token
    # Re-pulling an already retained generation must switch the local pointer too.
    cli.pull_state(pulled, 1)
    assert pulled_store.pin_generation() == first_token
    for name in ('manifest', 'chunks', 'keyword', 'catalog', 'change_log'):
        getattr(deps, name).close()


def test_mcp_defaults_and_release_parameters():
    class Tools:
        calls = []
        def query_sql(self, sql, **kwargs):
            self.calls.append((sql, kwargs))
            return {'data': 'unchanged'}
    tools = Tools()
    server = mcp_server.create_server(lambda: tools)
    async def run():
        async with Client(server) as client:
            default = await client.call_tool('query_sql', {'sql': 'SELECT 1'})
            pinned = await client.call_tool('query_sql', {'sql': 'SELECT 1', 'as_of_release': {'date': '2026-04-01'},
                                                         'generation_token': 'opaque'})
            return default, pinned
    default, pinned = asyncio.run(run())
    assert not default.is_error and not pinned.is_error
    assert tools.calls == [('SELECT 1', {'max_rows': 1000}),
                           ('SELECT 1', {'max_rows': 1000, 'as_of_release': date(2026, 4, 1), 'generation_token': 'opaque'})]


def test_failed_history_write_preserves_current_and_removes_partial_file(tmp_path, monkeypatch):
    store = DuckDBObservationStore(tmp_path)
    first = make_observation('same', '1')
    store.write_table('jp-stat', 't1', [first], SnapshotOrder(fetched_at=T0, seq=1))
    path = store.path_for('jp-stat', 't1')
    before = path.read_bytes()
    original = pq.write_table
    def fail_history(table, target, *args, **kwargs):
        if module._HISTORY_ONLY in (table.schema.metadata or {}):
            Path(target).write_bytes(b'incomplete synthetic parquet')
            raise OSError('synthetic history write failure')
        return original(table, target, *args, **kwargs)
    monkeypatch.setattr(pq, 'write_table', fail_history)
    with pytest.raises(OSError, match='synthetic history'):
        store.write_table('jp-stat', 't1', [make_observation('new', '2')], SnapshotOrder(fetched_at=T0, seq=2))
    assert path.read_bytes() == before and list(path.parent.iterdir()) == [path]
    assert store.query_readonly('SELECT * FROM observations').provenance == [first]


def test_fixed_query_uses_retained_catalog_not_live_database(tmp_path):
    store = DuckDBObservationStore(tmp_path)
    store.write_table('jp-stat', 't1', [make_observation('one', '1')], SnapshotOrder(fetched_at=T0, seq=1))
    token = publish_fixture(store, make_state_generation())
    store._db.unlink()
    assert store.query_readonly('SELECT value FROM observations', generation_token=token).rows == [(1,)]
