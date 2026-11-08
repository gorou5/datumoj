import contextlib
import hashlib
import io
import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

import inspect_abr_status as target


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = pathlib.Path(self.temp.name) / 'manifest.sqlite'
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE snapshots (source_id TEXT, fetched_at TEXT, outcome TEXT, snapshot TEXT)')

    def add(self, code=404, source='jp-abr', time='2026-11-08T19:02:33.000000', outcome='http_error', payload=None):
        if payload is None:
            payload = json.dumps({'http_status': code, 'resource_url': 'https://secret.invalid/?token=SECRET',
                                  'error': 'SECRET-ERROR-BODY'})
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO snapshots VALUES (?, ?, ?, ?)', (source, time, outcome, payload))

    def inspect(self):
        return target.inspect_manifest(self.path, quiescent_copy=True)

    def test_requires_explicit_quiescent_copy_acknowledgment(self):
        with self.assertRaisesRegex(target.DiagnosticError, '^quiescent_copy_required$'):
            target.inspect_manifest(self.path)

    def test_numeric_status_only_and_original_unchanged(self):
        self.add(403)
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = self.inspect()
        self.assertEqual(result['http_status_counts'], {'403': 1})
        self.assertEqual(result['http_error_snapshots'], 1)
        self.assertEqual(result['unknown_http_status'], 0)
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertNotIn('invalid', json.dumps(result))
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ['manifest.sqlite'])

    def test_only_exact_source_window_and_http_errors(self):
        self.add(404)
        self.add(503)
        self.add(429, source='unrelated')
        self.add(500, time='2026-11-01T16:00:24.000000')
        self.add(502, time='2026-11-08T19:02:36.696004')
        self.add(200, outcome='ok')
        self.assertEqual(self.inspect()['http_status_counts'], {'404': 1, '503': 1})

    def test_window_boundaries_are_inclusive(self):
        self.add(404, time='2026-11-08T19:02:31.339380')
        self.add(500, time='2026-11-08T19:02:36.696003')
        self.assertEqual(self.inspect()['http_error_snapshots'], 2)

    def test_empty_is_unknown_not_success(self):
        result = self.inspect()
        self.assertEqual(result['http_error_snapshots'], 0)
        self.assertEqual(result['evidence'], 'no_matching_snapshot')

    def test_invalid_status_not_echoed_or_misclassified(self):
        for value in (None, True, 'SECRET', '404', 404.0, 200, 304, 600, -1):
            self.add(value)
        result = self.inspect()
        self.assertEqual(result['http_status_counts'], {})
        self.assertEqual(result['unknown_http_status'], 9)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_malformed_json_fails_safely(self):
        self.add(payload='{SECRET')
        with self.assertRaisesRegex(target.DiagnosticError, '^invalid_snapshot_json$'):
            self.inspect()

    def test_deep_json_fails_safely(self):
        self.add(payload='[' * 32000 + '0' + ']' * 32000)
        with self.assertRaisesRegex(target.DiagnosticError, '^invalid_snapshot_json$'):
            self.inspect()

    def test_unhandled_redirect_status_is_reported(self):
        self.add(302)
        self.assertEqual(self.inspect()['http_status_counts'], {'302': 1})

    def test_view_cannot_replace_snapshot_table(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE snapshots')
            db.execute("CREATE VIEW snapshots AS SELECT 'SECRET' AS snapshot")
        with self.assertRaisesRegex(target.DiagnosticError, '^manifest_schema_or_read_failed$'):
            self.inspect()

    def test_non_object_json_fails_safely(self):
        self.add(payload='["SECRET"]')
        with self.assertRaisesRegex(target.DiagnosticError, '^invalid_snapshot_json$'):
            self.inspect()

    def test_oversized_snapshot_fails_closed(self):
        self.add(payload='x' * (target.MAX_SNAPSHOT_BYTES + 1))
        with self.assertRaisesRegex(target.DiagnosticError, '^snapshot_too_large$'):
            self.inspect()

    def test_sidecars_rejected(self):
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = pathlib.Path(str(self.path) + suffix)
            sidecar.write_text('synthetic')
            with self.assertRaisesRegex(target.DiagnosticError, '^sqlite_sidecar_present$'):
                self.inspect()
            sidecar.unlink()

    def test_symlink_rejected(self):
        link = self.path.parent / 'alias.sqlite'
        link.symlink_to(self.path)
        with self.assertRaisesRegex(target.DiagnosticError, '^not_regular_copy$'):
            target.inspect_manifest(link, quiescent_copy=True)

    def test_missing_file_not_created(self):
        missing = self.path.parent / 'missing.sqlite'
        with self.assertRaisesRegex(target.DiagnosticError, '^not_regular_copy$'):
            target.inspect_manifest(missing, quiescent_copy=True)
        self.assertFalse(missing.exists())

    def test_wrong_schema_fails_closed(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE snapshots')
        with self.assertRaisesRegex(target.DiagnosticError, '^manifest_schema_or_read_failed$'):
            self.inspect()

    def test_non_database_fails_closed(self):
        self.path.write_text('SECRET not sqlite')
        with self.assertRaisesRegex(target.DiagnosticError, '^manifest_schema_or_read_failed$'):
            self.inspect()

    def test_cli_failure_no_paths_or_exception_text(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = target.main(['--manifest-copy', str(self.path)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue()), {'error': 'quiescent_copy_required'})
        self.assertNotIn(str(self.path), out.getvalue())

    def test_cli_unknown_arguments_are_not_echoed(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = target.main(['--manifest-copy', str(self.path), '--SECRET-CREDENTIAL'])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue()), {'error': 'invalid_arguments'})
        self.assertEqual(err.getvalue(), '')

    def test_temp_failure_is_sanitized(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(target.tempfile, 'TemporaryDirectory', side_effect=PermissionError('SECRET-PATH')):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = target.main(['--manifest-copy', str(self.path), '--quiescent-copy'])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue()), {'error': 'local_io_failed'})
        self.assertEqual(err.getvalue(), '')

    def test_schema_collation_cannot_relax_exact_source_or_outcome(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE snapshots')
            db.execute('CREATE TABLE snapshots (source_id TEXT COLLATE NOCASE, fetched_at TEXT, '
                       'outcome TEXT COLLATE NOCASE, snapshot TEXT)')
        self.add(404, source='JP-ABR')
        self.add(403, outcome='HTTP_ERROR')
        self.assertEqual(self.inspect()['http_error_snapshots'], 0)

    def test_copy_change_is_rejected(self):
        original = target.shutil.copyfileobj
        def changing_copy(source, destination):
            original(source, destination)
            with self.path.open('ab') as file:
                file.write(b'changed')
        with mock.patch.object(target.shutil, 'copyfileobj', side_effect=changing_copy):
            with self.assertRaisesRegex(target.DiagnosticError, '^copy_changed$'):
                self.inspect()

    def test_manifest_size_limit(self):
        with mock.patch.object(target, 'MAX_MANIFEST_BYTES', 1):
            with self.assertRaisesRegex(target.DiagnosticError, '^manifest_too_large$'):
                self.inspect()

    def test_match_limit(self):
        self.add(404)
        self.add(503)
        with mock.patch.object(target, 'MAX_MATCHES', 1):
            with self.assertRaisesRegex(target.DiagnosticError, '^too_many_matching_snapshots$'):
                self.inspect()

    def test_virtual_table_rejected(self):
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE snapshots')
            try:
                db.execute('CREATE VIRTUAL TABLE snapshots USING fts5(source_id, fetched_at, outcome, snapshot)')
            except sqlite3.OperationalError:
                self.skipTest('SQLite lacks FTS5')
        with self.assertRaisesRegex(target.DiagnosticError, '^manifest_schema_or_read_failed$'):
            self.inspect()

    def test_cli_success(self):
        self.add(429)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = target.main(['--manifest-copy', str(self.path), '--quiescent-copy'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())['http_status_counts'], {'429': 1})


if __name__ == '__main__':
    unittest.main()
