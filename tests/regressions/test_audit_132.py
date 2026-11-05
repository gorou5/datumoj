"""Offline transfer measurements; all data and remote objects are synthetic."""

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from datumoj.rikolto.core.manifest import SqliteManifest
from datumoj.rikolto.core.state import _sqlite_copies
from datumoj.rikolto.index.vector import LanceVectorIndex
from datumoj.rikolto.pipeline import PipelineDeps, parse_pending
from datumoj.rikolto.store.catalog import SqliteCatalogStore
from datumoj.rikolto.store.chunks import SqliteChunkStore
from datumoj.rikolto.testing.fakes import (
    FakeCatalogStore,
    FakeChunkStore,
    FakeEmbedder,
    FakeKeywordIndex,
    FakeManifest,
    FakeObservationStore,
    FakeParser,
    FakeRawStore,
    FakeRegistry,
    FakeStateObjectStore,
)
from datumoj.rikolto.testing.samples import (
    make_chunk,
    make_dataset,
    make_document,
    make_snapshot,
    make_spec,
)


def inventory(root):
    return {hashlib.sha256(p.read_bytes()).hexdigest(): p.stat().st_size
            for p in Path(root).rglob('*') if p.is_file()}


def sqlite_inventory(path):
    backup = path.with_suffix('.backup')
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    result = {c.sha256: c.size_bytes for c in _sqlite_copies(backup) if c.format == 'sqlite-block-v1'}
    backup.unlink()
    return result


def added(before, after):
    return sum(size for sha, size in after.items() if sha not in before)


def measure_sqlite(root, existing, new):
    root.mkdir(parents=True)
    catalog = SqliteCatalogStore(root / 'catalog.sqlite')
    chunks = SqliteChunkStore(root / 'chunks.sqlite')
    manifest = SqliteManifest(root / 'manifest.sqlite')
    datasets = [make_dataset(f'jp-test:d{i:06}', f'資料 {i}', description='公開資料' * 300)
                for i in range(existing + new)]

    def write(i):
        text = f'資料 {i:06} ' + '公開資料' * 1000
        snap = make_snapshot(data=text.encode(), url=f'https://example.go.jp/{i:06}.txt')
        manifest.record_snapshot(snap)
        doc = make_document(f'jp-test:d{i:06}', snap.sha256)
        chunks.put_document(doc, [make_chunk(doc.doc_key, snap.sha256, 'section=1', text)])

    catalog.upsert(datasets[:existing])
    for i in range(existing):
        write(i)
    paths = {name: root / f'{name}.sqlite' for name in ('catalog', 'chunks', 'manifest')}
    before = {name: sqlite_inventory(path) for name, path in paths.items()}
    # Catalog crawls repeat unchanged entries alongside the new ones.
    catalog.upsert(datasets)
    for i in range(existing, existing + new):
        write(i)
    for store in (catalog, chunks, manifest):
        store.close()
    result = {name: {'added': added(before[name], sqlite_inventory(path)),
                     'total': path.stat().st_size} for name, path in paths.items()}
    return result


def vector_pipeline(root):
    return PipelineDeps(
        manifest=FakeManifest(), raw=FakeRawStore(), registry=FakeRegistry([make_spec()]),
        chunks=FakeChunkStore(), keyword=FakeKeywordIndex(), observations=FakeObservationStore(),
        catalog=FakeCatalogStore(), vectors=LanceVectorIndex(root, FakeEmbedder()),
        parsers={('fake', 'text/plain'): FakeParser}, vector_state_path=root.parent / 'vectors-state.json',
    )


def measure_vectors(root, existing, new):
    deps = vector_pipeline(root)
    # Reproduce old production fragmentation: one add/commit per document.
    for i in range(existing):
        deps.vectors.add([make_chunk(f'jp-test:old{i:06}', 'ab' * 32, 'section=1', f'旧資料 {i}')])
    fragments = deps.vectors._table.stats()['fragment_stats']['num_fragments']
    before = inventory(root)
    for i in range(new):
        body = f'新資料 {i:06}'.encode()
        snap = make_snapshot(data=body, url=f'https://example.go.jp/new{i:06}.txt')
        deps.raw.put(body)
        deps.manifest.record_snapshot(snap)
    result = parse_pending(deps)
    assert len(result.parsed) == new and not result.failed and not result.vectors_missing
    after = inventory(root)
    return {'added': added(before, after), 'total_before': sum(before.values()),
            'fragments_before': fragments,
            'fragments_after': deps.vectors._table.stats()['fragment_stats']['num_fragments']}


def test_measure_incremental_libraries(tmp_path):
    report = {}
    for existing in (1000, 2000):
        report[existing] = measure_sqlite(tmp_path / str(existing), existing, 20)
        report[existing]['vectors'] = measure_vectors(tmp_path / f'vectors{existing}', existing, 200)
    print('AUDIT132 ' + json.dumps(report, sort_keys=True))
    for existing, value in report.items():
        vectors = value['vectors']
        assert vectors['fragments_before'] == existing
        assert vectors['fragments_after'] == existing + 1
        assert vectors['added'] < vectors['total_before'] / 100
        for name in ('catalog', 'chunks', 'manifest'):
            assert 0 < value[name]['added'] < 20 * 256 * 1024
    for name in ('vectors', 'catalog', 'chunks'):
        assert report[2000][name]['added'] <= report[1000][name]['added'] * 1.6
    # Random SHA/URL indexes spread manifest inserts across 256 KiB blocks;
    # the storage layout remains compatible, and this residual is measured.
    assert report[2000]['manifest']['added'] < 2.1 * report[1000]['manifest']['added']


class MeteredStore(FakeStateObjectStore):
    """Count real StateManager object transfers using the contract fake."""

    def __init__(self):
        super().__init__()
        self.uploaded = self.downloaded = 0

    def put_if_absent(self, key, data):
        result = super().put_if_absent(key, data)
        self.uploaded += len(data)
        return result

    def get(self, key):
        result = super().get(key)
        if result is not None and key.startswith(('copies/', 'raw/')):
            self.downloaded += len(result[0])
        return result


def measure_transfers(root, existing, new):
    from datumoj.rikolto.core.state import StateManager

    live = root / 'live'
    live.mkdir(parents=True)
    deps = PipelineDeps(
        manifest=SqliteManifest(live / 'manifest.sqlite'), raw=FakeRawStore(),
        registry=FakeRegistry([make_spec()]), chunks=SqliteChunkStore(live / 'chunks.sqlite'),
        keyword=FakeKeywordIndex(), observations=FakeObservationStore(),
        catalog=SqliteCatalogStore(live / 'catalog.sqlite'),
        parsers={('fake', 'text/plain'): FakeParser},
    )
    store = MeteredStore()
    manager = StateManager(store, live)
    manager.cache_dir = root / 'cache'

    def write(start, count):
        for i in range(start, start + count):
            body = (f'資料{i:06} ' + hashlib.sha256(str(i).encode()).hexdigest() * 100).encode()
            snap = make_snapshot(data=body, url=f'https://example.go.jp/{i:06}.txt')
            deps.raw.put(body)
            path = live / 'raw' / snap.sha256[:2] / snap.sha256
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            deps.manifest.record_snapshot(snap)
        summary = parse_pending(deps)
        assert len(summary.parsed) == count and not summary.failed

    try:
        write(0, existing)
        manager.push()
        cached_before = {p.name for p in manager.cache_dir.iterdir()}
        store.uploaded = store.downloaded = 0
        write(existing, new)
        candidate = manager.push()
        uploaded = store.uploaded
        # The Actions cache carries only the prior generation's content. Remove
        # objects first seen in this round to reproduce a fresh runner.
        for path in manager.cache_dir.iterdir():
            if path.name not in cached_before:
                path.unlink()
        assert manager.restore(root / 'restored', raw='remote')['verified']
        assert manager.last_timings['downloaded_bytes'] == store.downloaded
        return {'uploaded': uploaded, 'downloaded': store.downloaded,
                'reused': manager.last_timings['reused_bytes'],
                'total': sum(c.size_bytes for c in candidate.stores if c.format != 'sqlite-blocks-v1')}

    finally:
        for name in ('manifest', 'chunks', 'catalog'):
            getattr(deps, name).close()


def test_upload_download_scales_with_new_documents_not_existing_corpus(tmp_path):
    results = {(existing, new): measure_transfers(tmp_path / f'{existing}-{new}', existing, new)
               for existing, new in ((400, 10), (800, 10), (400, 20))}
    print('AUDIT132_TRANSFERS ' + json.dumps({str(key): value for key, value in results.items()}, sort_keys=True))
    for value in results.values():
        assert 0 < value['downloaded'] < value['uploaded']
        assert value['reused'] > 0
        assert value['downloaded'] < value['total'] * 0.75
    for kind in ('uploaded', 'downloaded'):
        assert results[800, 10][kind] <= results[400, 10][kind] * 1.6
        assert results[400, 10][kind] < results[400, 20][kind] <= results[400, 10][kind] * 2.5
