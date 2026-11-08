"""Read numeric ABR status from an existing, quiescent Cloud #42 manifest copy.

Standard library only. No network, production SDK, migrations or source writes.
The caller must provide a completed standalone copy, never a live state database.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from collections import Counter
from pathlib import Path

SOURCE_ID = 'jp-abr'
START = '2026-11-08T19:02:31.339380'
END = '2026-11-08T19:02:36.696003'
MAX_SNAPSHOT_BYTES = 65536
MAX_MANIFEST_BYTES = 1024 * 1024 * 1024
MAX_MATCHES = 1000


class DiagnosticError(ValueError):
    """Fixed public reason, never source data or underlying exception text."""


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _check_copy(path):
    try:
        info = path.lstat()
    except OSError:
        raise DiagnosticError('not_regular_copy') from None
    if not stat.S_ISREG(info.st_mode):
        raise DiagnosticError('not_regular_copy')
    if info.st_size > MAX_MANIFEST_BYTES:
        raise DiagnosticError('manifest_too_large')
    if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise DiagnosticError('sqlite_sidecar_present')
    return info


def inspect_manifest(path, *, quiescent_copy=False):
    try:
        return _inspect_manifest(path, quiescent_copy=quiescent_copy)
    except OSError:
        raise DiagnosticError('local_io_failed') from None


def _inspect_manifest(path, *, quiescent_copy=False):
    if not quiescent_copy:
        raise DiagnosticError('quiescent_copy_required')
    path = Path(path)
    initial = _check_copy(path)
    # Copy bytes before asking SQLite to read them; immutable applies only to
    # this private temporary copy. Do not use SqliteManifest, which migrates.
    with tempfile.TemporaryDirectory(prefix='abr-status-') as folder:
        local = Path(folder) / 'manifest.sqlite'
        try:
            with path.open('rb') as source, local.open('xb') as destination:
                if _signature(os.fstat(source.fileno())) != _signature(initial):
                    raise DiagnosticError('copy_changed')
                shutil.copyfileobj(source, destination)
                if _signature(os.fstat(source.fileno())) != _signature(initial):
                    raise DiagnosticError('copy_changed')
            if _signature(_check_copy(path)) != _signature(initial):
                raise DiagnosticError('copy_changed')
        except OSError:
            raise DiagnosticError('copy_read_failed') from None
        try:
            db = sqlite3.connect(local.as_uri() + '?mode=ro&immutable=1', uri=True)
            try:
                db.execute('PRAGMA query_only=ON')
                db.execute('PRAGMA trusted_schema=OFF')
                # Refuse a view or virtual table supplied instead of the known table.
                schema = db.execute("SELECT type, sql FROM sqlite_master WHERE name='snapshots'").fetchone()
                if (schema is None or schema[0] != 'table'
                        or not schema[1].lstrip().upper().startswith('CREATE TABLE ')):
                    raise DiagnosticError('manifest_schema_or_read_failed')
                rows = db.execute(
                    'SELECT length(CAST(snapshot AS BLOB)), '
                    'CASE WHEN length(CAST(snapshot AS BLOB)) <= ? THEN snapshot ELSE NULL END '
                    'FROM snapshots WHERE source_id COLLATE BINARY=? AND fetched_at COLLATE BINARY>=? AND fetched_at COLLATE BINARY<=? '
                    'AND outcome COLLATE BINARY=? LIMIT ?',
                    (MAX_SNAPSHOT_BYTES, SOURCE_ID, START, END, 'http_error', MAX_MATCHES + 1),
                ).fetchall()
            finally:
                db.close()
        except sqlite3.Error:
            raise DiagnosticError('manifest_schema_or_read_failed') from None
    if len(rows) > MAX_MATCHES:
        raise DiagnosticError('too_many_matching_snapshots')
    counts = Counter()
    unknown = 0
    for size, raw in rows:
        if size is None or size > MAX_SNAPSHOT_BYTES:
            raise DiagnosticError('snapshot_too_large')
        try:
            snapshot = json.loads(raw)
        except (TypeError, ValueError, RecursionError):
            raise DiagnosticError('invalid_snapshot_json') from None
        if not isinstance(snapshot, dict):
            raise DiagnosticError('invalid_snapshot_json')
        code = snapshot.get('http_status')
        # Non-success response can be an unhandled 3xx. Do not assume 4xx/5xx.
        if type(code) is int and 100 <= code <= 599 and not 200 <= code <= 299 and code != 304:
            counts[str(code)] += 1
        else:
            unknown += 1
    return {
        'source_id': SOURCE_ID,
        'window_start_utc': START + '+00:00',
        'window_end_utc': END + '+00:00',
        'evidence': 'matching_http_error_snapshots' if rows else 'no_matching_snapshot',
        'http_error_snapshots': len(rows),
        'http_status_counts': dict(sorted(counts.items())),
        'unknown_http_status': unknown,
    }


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise DiagnosticError('invalid_arguments')


def main(argv=None):
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument('--manifest-copy', required=True, type=Path)
    parser.add_argument('--quiescent-copy', action='store_true',
                        help='Confirm this is a standalone completed copy, not a live database.')
    try:
        args = parser.parse_args(argv)
        result = inspect_manifest(args.manifest_copy, quiescent_copy=args.quiescent_copy)
    except DiagnosticError as exc:
        print(json.dumps({'error': str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
