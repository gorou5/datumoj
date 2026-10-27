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
