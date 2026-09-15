"""Opt-in live recovery tests. Both URLs MUST name disposable databases.

CI creates dedicated source and restore target, separate from runtime tests.
Neither database is reset by this suite; source schema must already be applied.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = os.getenv('XRAY_RECOVERY_TEST_SOURCE_URL', '')
TARGET = os.getenv('XRAY_RECOVERY_TEST_TARGET_URL', '')


@unittest.skipUnless(SOURCE and TARGET, 'two disposable PostgreSQL recovery URLs required')
class LivePostgresRecovery(unittest.TestCase):
    def test_authenticated_roundtrip_and_tamper_rejection(self):
        self.assertNotEqual(SOURCE.split('?')[0], TARGET.split('?')[0])
        import psycopg2
        from app.manifest import create
        with tempfile.TemporaryDirectory() as d:
            archive = Path(d)/'backup.dump'
            env = dict(os.environ, BACKUP_HMAC_KEY='synthetic-recovery-backup-key',
                       AUDIT_HMAC_KEY='development-audit-key-not-for-production')

            def run(url, *args):
                return subprocess.run([sys.executable, 'scripts/recovery.py', *map(str,args)],
                    cwd=ROOT, env=dict(env, DATABASE_URL=url), capture_output=True, text=True)

            result = run(SOURCE, 'backup', 'DATABASE_URL', archive)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            backup = json.loads(result.stdout)
            result = run(TARGET, 'restore', archive, 'DATABASE_URL', '--force')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            restored = json.loads(result.stdout)
            self.assertEqual(backup['audit_head'], restored['audit_head'])
            self.assertEqual(backup['audit_events'], restored['audit_events'])
            self.assertEqual(restored['integrity'], 'ok')

            def tables(url):
                conn = psycopg2.connect(url)
                try:
                    with conn.cursor() as cur:
                        cur.execute('SELECT id,title FROM projects ORDER BY id')
                        return cur.fetchall()
                finally:
                    conn.close()

            before = tables(TARGET)
            self.assertEqual(tables(SOURCE), before)
            refused = run(TARGET, 'restore', archive, 'DATABASE_URL')
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(tables(TARGET), before)
            archive.write_bytes(b'tampered non-archive')
            refused = run(TARGET, 'restore', archive, 'DATABASE_URL', '--force')
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('manifest', refused.stdout)
            self.assertEqual(tables(TARGET), before)
            # Authenticated but invalid archive must not be laundered into success
            # merely because the existing target has valid tables and audit chain.
            create(archive.with_suffix('.dump.manifest.json'), archive, env['BACKUP_HMAC_KEY'])
            refused = run(TARGET, 'restore', archive, 'DATABASE_URL', '--force')
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn('pg_restore failed', refused.stdout)
            self.assertEqual(tables(TARGET), before)
