#!/usr/bin/env python3
"""Contract: /readyz audit verification is O(1) in steady state and stays tamper-evident.

The full O(n) chain walk runs only when the audit head moves past the last
fully verified state (or the periodic re-verify window lapses); every probe
still cryptographically validates the signed head checkpoint."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


class TestReadyzAuditCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._prev = {k: os.environ.get(k) for k in ('DB_PATH', 'DATABASE_URL')}
        os.environ['DB_PATH'] = str(Path(cls.tmp.name) / 'readyz_cache.db')
        os.environ.pop('DATABASE_URL', None)
        import app.database as database
        import app.server as server
        database.DB_PATH = Path(os.environ['DB_PATH'])
        database.DATABASE_URL = ''
        database.IS_POSTGRES = False
        server.DB = database.DB_PATH
        server.init()
        cls.server = server
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.H)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for k, v in cls._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def setUp(self):
        s = self.server
        with s._AUDIT_PROBE_LOCK:
            s._AUDIT_PROBE_CACHE.update(head=None, events=-1, verified_at=0.0)
        s.reset_readiness_verifier()
        self._interval = s.READYZ_FULL_VERIFY_INTERVAL
        self._orig_verify = s.verify_audit
        self._orig_segment = s.verify_audit_segment
        self.calls = {'full': 0}

        def counting_segment(c, key, start_id, previous, limit, count_offset):
            # A streaming walk always restarts at cursor 0, so counting
            # segments that begin at the chain root counts full verifications.
            if start_id == 0:
                self.calls['full'] += 1
            return self._orig_segment(c, key, start_id, previous, limit, count_offset)

        s.verify_audit_segment = counting_segment

    def tearDown(self):
        self.server.verify_audit = self._orig_verify
        self.server.verify_audit_segment = self._orig_segment
        self.server.READYZ_FULL_VERIFY_INTERVAL = self._interval
        self.server.reset_readiness_verifier()

    def _get(self, path):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}')
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read().decode() or '{}')

    def test_steady_state_probes_run_full_verify_once(self):
        for _ in range(5):
            code, body = self._get('/readyz')
            self.assertEqual(code, 200)
            self.assertTrue(body['ready'])
        self.assertEqual(self.calls['full'], 1)

    def test_head_movement_invalidates_cache(self):
        self._get('/readyz')
        with self.server.db(True) as c:
            self.server.audit(c, 'admin', 'test', 'probe', 'obj_cache', 'head moved')
        code, body = self._get('/readyz')
        self.assertEqual(code, 200)
        self.assertEqual(self.calls['full'], 2)

    def test_zero_interval_forces_full_verify_every_probe(self):
        self.server.READYZ_FULL_VERIFY_INTERVAL = 0.0
        self._get('/readyz')
        self._get('/readyz')
        self.assertEqual(self.calls['full'], 2)

    def test_tampered_head_detected_out_of_band(self):
        # The app schema makes audit rows immutable via triggers, so simulate
        # out-of-band tampering in a trigger-free store: verify_head must fail.
        import sqlite3
        from app import audit
        raw = sqlite3.connect(':memory:')
        raw.row_factory = sqlite3.Row
        raw.execute(
            'CREATE TABLE audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT,'
            ' event_id TEXT, actor TEXT, action TEXT, object_type TEXT,'
            ' object_id TEXT, detail TEXT, previous_hash TEXT, event_hash TEXT,'
            ' created_at TEXT)'
        )
        raw.execute(
            'CREATE TABLE audit_checkpoints(id INTEGER PRIMARY KEY AUTOINCREMENT,'
            ' event_id TEXT, event_count INTEGER, head_hash TEXT, signature TEXT,'
            ' created_at TEXT)'
        )
        audit.append(raw, 'evt_1', 'admin', 'create', 'obj', 'obj_1', 'd', '2026-01-01T00:00:00Z', 'k')
        audit.append(raw, 'evt_2', 'admin', 'create', 'obj', 'obj_2', 'd', '2026-01-01T00:00:01Z', 'k')
        self.assertEqual(audit.verify_head(raw, 'k'), audit.verify(raw, 'k'))
        raw.execute("UPDATE audit_events SET detail='tampered' WHERE event_id='evt_2'")
        with self.assertRaises(RuntimeError):
            audit.verify_head(raw, 'k')

    def test_probe_fails_closed_when_head_check_raises(self):
        self._get('/readyz')  # warm cache: failure must not be masked by it
        orig = self.server.verify_audit_head

        def boom(c, key, strict_count=True):
            raise RuntimeError('audit head checkpoint signature invalid')

        self.server.verify_audit_head = boom
        try:
            code, body = self._get('/readyz')
        finally:
            self.server.verify_audit_head = orig
        self.assertEqual(code, 503)
        self.assertFalse(body['ready'])

    def test_verify_head_matches_full_verify(self):
        with self.server.db() as c:
            full = self._orig_verify(c, self.server.AUDIT_KEY)
            head = self.server.verify_audit_head(c, self.server.AUDIT_KEY)
        self.assertEqual(full, head)


if __name__ == '__main__':
    unittest.main()
