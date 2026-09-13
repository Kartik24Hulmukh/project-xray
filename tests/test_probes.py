#!/usr/bin/env python3
"""Liveness/readiness probe contract: /healthz, /livez and /readyz are first-class aliases of /health and /ready."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


class TestProbeAliases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._prev = {k: os.environ.get(k) for k in ('DB_PATH', 'DATABASE_URL')}
        os.environ['DB_PATH'] = str(Path(cls.tmp.name) / 'probes.db')
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

    def _get(self, path):
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}')
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or '{}')

    def test_probe_path_sets_are_disjoint_and_complete(self):
        s = self.server
        self.assertEqual(s.LIVENESS_PATHS, frozenset({'/health', '/healthz', '/livez'}))
        self.assertEqual(s.READINESS_PATHS, frozenset({'/ready', '/readyz'}))
        self.assertFalse(s.LIVENESS_PATHS & s.READINESS_PATHS)
        self.assertEqual(s.PROBE_PATHS, s.LIVENESS_PATHS | s.READINESS_PATHS)

    def test_healthz_mirrors_health(self):
        a, ba = self._get('/health')
        b, bb = self._get('/healthz')
        self.assertEqual((a, b), (200, 200))
        self.assertEqual(ba['status'], bb['status'])
        self.assertEqual(ba['version'], bb['version'])

    def test_canonical_runbook_probe_pair_is_served(self):
        # The release runbook and orchestrator manifests poll exactly these two paths.
        self.assertEqual({self._get('/healthz')[0], self._get('/readyz')[0]}, {200})
        self.assertIn('/healthz', self.server.LIVENESS_PATHS)
        self.assertIn('/readyz', self.server.READINESS_PATHS)

    def test_livez_mirrors_health(self):
        a, ba = self._get('/health')
        b, bb = self._get('/livez')
        self.assertEqual((a, b), (200, 200))
        self.assertEqual(ba['status'], bb['status'])
        self.assertEqual(ba['version'], bb['version'])

    def test_readyz_mirrors_ready(self):
        a, ba = self._get('/ready')
        b, bb = self._get('/readyz')
        self.assertEqual((a, b), (200, 200))
        self.assertTrue(ba['ready'] and bb['ready'])
        self.assertEqual(ba['status'], bb['status'])

    def test_probes_are_unauthenticated_and_not_rate_limited(self):
        # 50 rapid unauthenticated probe hits must never trip the limiter (orchestrators poll aggressively).
        codes = {self._get(p)[0] for p in ('/healthz', '/livez', '/readyz') for _ in range(25)}
        self.assertEqual(codes, {200})


if __name__ == '__main__':
    unittest.main()
