"""Regression tests for fail-closed recovery; no production database touched."""
import json
import os
import tempfile
import secrets
from urllib.parse import quote
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from scripts import recovery, recovery_evidence
from app.manifest import create


class RecoverySafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.archive = Path(self.tmp.name) / 'backup.dump'
        self.archive.write_bytes(b'synthetic-dump')
        self.manifest = self.archive.with_suffix('.dump.manifest.json')
        create(self.manifest, self.archive, 'backup-test')
        self.pg = patch.object(recovery, 'IS_POSTGRES', True)
        self.pg.start()
        self.addCleanup(self.pg.stop)
        self.password = secrets.token_hex(12) + '@/'
        self.env = patch.dict(os.environ, {'DATABASE_URL':
            'postgresql://user:' + quote(self.password, safe='') + '@localhost/test?sslmode=verify-full&sslrootcert=%2Ftmp%2Fca.pem'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def restore(self, **kw):
        return recovery.restore(self.archive, 'ignored', key='backup-test', **kw)

    def test_tampered_dump_never_invokes_pg_restore(self):
        self.archive.write_bytes(b'tampered')
        with patch.object(recovery.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError):
                self.restore(force=True)
            run.assert_not_called()

    def test_tampered_manifest_never_invokes_pg_restore(self):
        doc = json.loads(self.manifest.read_text())
        doc['payload']['size_bytes'] += 1
        self.manifest.write_text(json.dumps(doc))
        with patch.object(recovery.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError):
                self.restore(force=True)
            run.assert_not_called()

    def test_missing_manifest_never_invokes_pg_restore(self):
        self.manifest.unlink()
        with patch.object(recovery.subprocess, 'run') as run:
            with self.assertRaises(FileNotFoundError):
                self.restore(force=True)
            run.assert_not_called()

    def test_pg_restore_requires_explicit_force(self):
        with patch.object(recovery.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'force'):
                self.restore()
            run.assert_not_called()

    def test_error_is_not_laundered_by_existing_valid_database(self):
        with patch.object(recovery.subprocess, 'run', return_value=Mock(returncode=1, stderr=(self.password + ' failed').encode())) as run, patch.object(recovery, 'integrity', return_value={'integrity': 'ok'}) as check:
            with self.assertRaises(RuntimeError) as error:
                self.restore(force=True)
            self.assertNotIn(self.password, str(error.exception))
            check.assert_not_called()
            argv = run.call_args.args[0]
            self.assertIn('--single-transaction', argv)
            self.assertIn('--exit-on-error', argv)
            self.assertNotIn(os.environ['DATABASE_URL'], argv)

    def test_success_checks_restored_database(self):
        with patch.object(recovery.subprocess, 'run', return_value=Mock(returncode=0)), patch.object(recovery, 'integrity', return_value={'integrity': 'ok'}) as check:
            self.assertEqual(self.restore(force=True)['integrity'], 'ok')
            check.assert_called_once()

    def test_tls_policy_and_uri_password_preserved(self):
        env = recovery._pg_env()
        self.assertEqual(env['PGPASSWORD'], self.password)
        self.assertEqual(env['PGSSLMODE'], 'verify-full')
        self.assertEqual(env['PGSSLROOTCERT'], '/tmp/ca.pem')

    def test_ecs_split_credentials_supported(self):
        with patch.dict(os.environ, {'DB_HOST':'localhost', 'DB_NAME':'test', 'DB_USERNAME':'user', 'DB_PASSWORD':self.password, 'DB_SSLMODE':'verify-full'}, clear=True):
            self.assertEqual(recovery._pg_env()['PGPASSWORD'], self.password)
            self.assertEqual(recovery._pg_env()['PGSSLMODE'], 'verify-full')

    def test_unknown_connection_option_fails_closed(self):
        with patch.dict(os.environ, {'DATABASE_URL':'postgresql://user@localhost/test?sslmode=require&unknown=1'}):
            with self.assertRaises(ValueError):
                recovery._pg_env()

    def test_automatic_pg_drill_refuses_before_backup(self):
        with patch.object(recovery_evidence, 'IS_POSTGRES', True), patch.object(recovery_evidence, 'backup') as backup:
            with self.assertRaisesRegex(RuntimeError, 'isolated'):
                recovery_evidence.collect('DATABASE_URL', Path(self.tmp.name)/'receipt.json')
            backup.assert_not_called()

    def test_roundtrip_does_not_claim_measured_rpo(self):
        import sqlite3
        db = Path(self.tmp.name)/'source.db'
        c = sqlite3.connect(db)
        c.executescript((Path(__file__).resolve().parents[1]/'db/schema.sql').read_text())
        c.close()
        with patch.object(recovery_evidence, 'IS_POSTGRES', False), patch.object(recovery, 'IS_POSTGRES', False):
            doc = recovery_evidence.collect(db, Path(self.tmp.name)/'receipt.json')
        self.assertIsNone(doc['payload']['rpo_seconds'])
        self.assertFalse(doc['payload']['rpo_pass'])
        self.assertEqual(doc['payload']['rpo_status'], 'not_measured')
