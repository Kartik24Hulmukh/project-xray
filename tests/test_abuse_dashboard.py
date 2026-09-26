"""Tests for the bounded, privacy-preserving abuse read model (/api/admin/abuse)."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# NOTE: `app.server` is imported lazily (inside fixtures) on purpose.  Module
# constants such as ADMIN_TOKEN / TOKEN_PEPPER are read at import time, and
# unittest discovery imports every test module before running any test; a
# module-level import here would bind the server to this file's environment
# and silently change the fixtures of every suite that sorts after it.


_STANDALONE_ENV = {
    'APP_ENV': 'test',
    'ADMIN_TOKEN': 'test-admin-secret-long-enough',
    'TOKEN_PEPPER': 'test-token-pepper-0000000000000000',
    'AUDIT_HMAC_KEY': 'test-audit-key-0000000000000000',
    'BACKUP_HMAC_KEY': 'test-backup-key-0000000000000000',
}


def _server():
    if 'app.server' not in sys.modules:
        # Only when this file runs on its own: give the server test fixtures.
        for key, value in _STANDALONE_ENV.items():
            os.environ.setdefault(key, value)
    from app import server
    return server


def _stub_handler(ip):
    server = _server()
    h = server.H.__new__(server.H)  # bypass socket setup
    h.client_address = (ip, 0)
    h.headers = {}
    return h


class TestAbuseReadModel(unittest.TestCase):
    def setUp(self):
        global server
        server = _server()
        server.reset_abuse_state()
        with server._RATE_LOCK:
            self._rate_backup = dict(server.RATE)
            server.RATE.clear()

    def tearDown(self):
        server.reset_abuse_state()
        with server._RATE_LOCK:
            server.RATE.clear()
            server.RATE.update(self._rate_backup)

    def test_identity_is_never_stored_raw(self):
        server.record_abuse('write', '203.0.113.7')
        snap = server.abuse_snapshot()
        self.assertNotIn('203.0.113.7', json.dumps(snap))
        self.assertEqual(len(snap['top_offenders'][0]['fingerprint']), 16)

    def test_fingerprint_is_deterministic(self):
        self.assertEqual(server._abuse_fingerprint('198.51.100.1'), server._abuse_fingerprint('198.51.100.1'))
        self.assertNotEqual(server._abuse_fingerprint('198.51.100.1'), server._abuse_fingerprint('198.51.100.2'))

    def test_top_offenders_sorted_and_truncated(self):
        for i in range(30):
            for _ in range(i + 1):
                server.record_abuse('public_read', f'10.0.0.{i}')
        snap = server.abuse_snapshot()
        totals = [o['total'] for o in snap['top_offenders']]
        self.assertEqual(len(totals), server.ABUSE_TOP_N)
        self.assertEqual(totals, sorted(totals, reverse=True))
        self.assertEqual(totals[0], 30)
        self.assertEqual(snap['rate_limited_by_category']['public_read'], sum(range(1, 31)))

    def test_ledger_is_bounded_under_hostile_cardinality(self):
        cap = server.ABUSE_MAX_OFFENDERS
        for i in range(cap * 3):
            server.record_abuse('write', f'2001:db8::{i:x}')
        snap = server.abuse_snapshot()
        self.assertEqual(snap['tracked_offenders'], cap)
        self.assertEqual(snap['tracked_offenders_cap'], cap)

    def test_concurrent_recording_is_exact(self):
        def hammer(n):
            for _ in range(200):
                server.record_abuse('write', f'192.0.2.{n % 8}')
        with ThreadPoolExecutor(max_workers=32) as pool:
            list(pool.map(hammer, range(64)))
        snap = server.abuse_snapshot(top_n=100)
        self.assertEqual(sum(o['total'] for o in snap['top_offenders']), 64 * 200)
        self.assertEqual(snap['rate_limited_by_category']['write'], 64 * 200)

    def test_limiter_feeds_abuse_ledger(self):
        old = server.WRITE_RATE_LIMIT
        server.WRITE_RATE_LIMIT = 2
        try:
            h = _stub_handler('192.0.2.55')
            results = [h.limited('POST', '/api/projects') for _ in range(5)]
        finally:
            server.WRITE_RATE_LIMIT = old
        self.assertEqual(results, [False, False, True, True, True])
        snap = server.abuse_snapshot()
        self.assertEqual(snap['top_offenders'][0]['total'], 3)
        self.assertEqual(snap['top_offenders'][0]['categories'], {'write': 3})
        self.assertGreaterEqual(snap['active_clients_current_minute'].get('write', 0), 1)


class TestAbuseEndpointAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global server
        server = _server()
        cls.tmp = tempfile.TemporaryDirectory()
        cls._db_backup = server.DB
        server.DB = Path(cls.tmp.name) / 'abuse.db'
        try:
            from app import database
            cls._database_backup = (database.DB_PATH, database.DATABASE_URL, database.IS_POSTGRES)
            database.DB_PATH = server.DB
            database.DATABASE_URL = ''
            database.IS_POSTGRES = False
        except Exception:
            cls._database_backup = None
        server.init()
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.H)
        cls.port = cls.http.server_address[1]
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        server.DB = cls._db_backup
        if cls._database_backup:
            from app import database
            database.DB_PATH, database.DATABASE_URL, database.IS_POSTGRES = cls._database_backup
        cls.tmp.cleanup()

    def _get(self, token=None):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/api/admin/abuse')
        if token:
            req.add_header('Authorization', 'Bearer ' + token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read()
            e.close()
            return e.code, body

    def test_anonymous_is_rejected(self):
        status, _ = self._get()
        self.assertIn(status, (401, 403))

    def test_non_admin_token_is_rejected(self):
        status, _ = self._get('not-a-real-token')
        self.assertIn(status, (401, 403))

    def test_admin_gets_read_model(self):
        status, body = self._get(server.ADMIN_TOKEN)
        self.assertEqual(status, 200)
        for key in ('limits_per_minute', 'rate_limited_total', 'top_offenders', 'tracked_offenders_cap'):
            self.assertIn(key, body)


if __name__ == '__main__':
    unittest.main()
