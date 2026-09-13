#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestVersionConsistency(unittest.TestCase):
    def test_package_and_sbom_and_runtime_versions_align(self):
        pkg = json.loads((ROOT / 'package.json').read_text())
        self.assertEqual(pkg['version'], '0.4.6')
        sbom_src = (ROOT / 'scripts/generate_sbom.py').read_text()
        self.assertIn("'version':'0.4.6'", sbom_src.replace(' ', ''))
        server = (ROOT / 'app/server.py').read_text()
        self.assertIn("'version': '0.4.6'", server)
        self.assertNotIn("'version': '0.4.0'", server)
        self.assertNotIn("'version':'0.2.0'", sbom_src.replace(' ', ''))

    def test_legacy_migrators_refuse_database_url(self):
        env = {**os.environ, 'DATABASE_URL': 'postgresql://xray:xray@localhost:5432/xray'}
        for script in ('scripts/migrate_legacy.py', 'scripts/migrate_v2_to_v3.py'):
            r = subprocess.run(
                [sys.executable, str(ROOT / script), 'unused.db'],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(r.returncode, 0, script)
            payload = json.loads(r.stdout.strip() or r.stderr.strip() or '{}')
            self.assertEqual(payload.get('status'), 'error')
            self.assertIn('SQLite-only', payload.get('error', ''))


class TestVerifyAuditUsesAbstraction(unittest.TestCase):
    def test_verify_audit_sqlite_path(self):
        # Create a minimal empty sqlite file is not enough for chain verify;
        # just ensure CLI reports a structured error without ImportError.
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'empty.db'
            db.write_bytes(b'')
            env = {k: v for k, v in os.environ.items() if k != 'DATABASE_URL'}
            env['AUDIT_HMAC_KEY'] = 'audit-test-key-1234567890123456789012'
            r = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/verify_audit.py'), str(db)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertIn('status', r.stdout)
            payload = json.loads(r.stdout.strip())
            self.assertIn(payload['status'], ('ok', 'error'))


if __name__ == '__main__':
    unittest.main()
