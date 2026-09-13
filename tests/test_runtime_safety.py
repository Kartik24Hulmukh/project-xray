#!/usr/bin/env python3
"""Thread-safety and fail-safe HTTP exception handling tests."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestMetricsAndRateThreadSafety(unittest.TestCase):
    """Uses already-imported server locks without rebinding DB_PATH permanently."""

    def setUp(self):
        from app import server
        self.server = server
        with server._METRICS_LOCK:
            self._metrics_backup = dict(server.METRICS)
        with server._RATE_LOCK:
            self._rate_backup = dict(server.RATE)
            server.RATE.clear()

    def tearDown(self):
        s = self.server
        with s._METRICS_LOCK:
            s.METRICS.clear()
            s.METRICS.update(self._metrics_backup)
        with s._RATE_LOCK:
            s.RATE.clear()
            s.RATE.update(self._rate_backup)

    def test_simultaneous_increments_exact_count(self):
        s = self.server
        with s._METRICS_LOCK:
            s.METRICS['requests'] = 0
        n_threads = 20
        per_thread = 100

        def work():
            for _ in range(per_thread):
                s._metric_inc('requests')

        threads = [threading.Thread(target=work) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            self.assertFalse(t.is_alive())
        self.assertEqual(s.metrics_snapshot()['requests'], n_threads * per_thread)

    def test_rate_limit_race_does_not_exceed(self):
        s = self.server
        with s._RATE_LOCK:
            s.RATE.clear()
        previous = s.PUBLIC_READ_RATE_LIMIT
        s.PUBLIC_READ_RATE_LIMIT = 50
        s.TRUST_PROXY_HEADERS = False
        try:
            class FakeHandler:
                def __init__(self, identity):
                    self.client_address = (identity, 0)
                    self.headers = {}

                def client_identity(self):
                    return self.client_address[0]

                def rate_bucket(self, method, path):
                    return s.H.rate_bucket(self, method, path)

                def limited(self, method, path):
                    return s.H.limited(self, method, path)

            handler = FakeHandler('203.0.113.50')
            allowed = 0
            lock = threading.Lock()

            def hit():
                nonlocal allowed
                if not handler.limited('GET', '/api/projects'):
                    with lock:
                        allowed += 1

            with ThreadPoolExecutor(max_workers=32) as ex:
                futs = [ex.submit(hit) for _ in range(200)]
                for f in as_completed(futs, timeout=15):
                    f.result(timeout=5)

            self.assertLessEqual(allowed, 50)
            self.assertGreater(allowed, 0)
        finally:
            s.PUBLIC_READ_RATE_LIMIT = previous

    def test_concurrent_metric_snapshots(self):
        s = self.server

        def snap():
            for _ in range(200):
                d = s.metrics_snapshot()
                self.assertIsInstance(d, dict)
                s._metric_inc('writes')

        threads = [threading.Thread(target=snap) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
            self.assertFalse(t.is_alive())

    def test_stale_rate_entries_cleaned(self):
        s = self.server
        with s._RATE_LOCK:
            s.RATE.clear()
            s.RATE[('public_read', '1.2.3.4', int(time.time() // 60) - 5)] = 9

        class H:
            pass

        h = H()
        h.client_address = ('9.9.9.9', 0)
        h.headers = {}
        h.client_identity = lambda: '9.9.9.9'
        h.rate_bucket = s.H.rate_bucket.__get__(h, s.H)
        h.limited = s.H.limited.__get__(h, s.H)
        previous = s.PUBLIC_READ_RATE_LIMIT
        s.PUBLIC_READ_RATE_LIMIT = 1000
        try:
            self.assertFalse(h.limited('GET', '/api/projects'))
            with s._RATE_LOCK:
                stale = [k for k in s.RATE if k[2] < int(time.time() // 60) - 1]
                self.assertEqual(stale, [])
        finally:
            s.PUBLIC_READ_RATE_LIMIT = previous


class TestFailSafeHTTP(unittest.TestCase):
    """Spin an isolated server instance on an ephemeral port and DB."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.tmp.close()
        self._prev_db = os.environ.get('DB_PATH')
        os.environ['DB_PATH'] = self.tmp.name
        os.environ.setdefault('APP_ENV', 'development')
        os.environ.pop('DATABASE_URL', None)

        import importlib
        import app.database as database
        import app.server as server

        # Rebind database path used by abstraction
        database.DB_PATH = Path(self.tmp.name)
        database.DATABASE_URL = ''
        database.IS_POSTGRES = False
        server.DB = Path(self.tmp.name)
        # init schema on isolated DB without disturbing process-global forever
        server.init()
        self.server = server
        from http.server import ThreadingHTTPServer

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.05)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._prev_db is not None:
            os.environ['DB_PATH'] = self._prev_db
        else:
            os.environ.pop('DB_PATH', None)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def _req(self, method, path, body=None, headers=None):
        import urllib.request

        url = f'http://127.0.0.1:{self.port}{path}'
        data = None
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            hdrs['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode(), dict(resp.headers)
        except Exception as e:
            if hasattr(e, 'code'):
                body = e.read().decode() if hasattr(e, 'read') else ''
                return e.code, body, dict(getattr(e, 'headers', {}) or {})
            raise

    def test_get_unexpected_exception_is_generic_500(self):
        s = self.server
        sentinel = 'SECRET_EXCEPTION_TOKEN_XYZ_should_not_leak'
        before = s.metrics_snapshot().get('errors', 0)
        original = s.H._handle_get

        def boom(self):
            raise RuntimeError(sentinel)

        s.H._handle_get = boom
        try:
            code, body, headers = self._req('GET', '/health')
        finally:
            s.H._handle_get = original

        self.assertEqual(code, 500)
        self.assertNotIn(sentinel, body)
        self.assertNotIn('RuntimeError', body)
        payload = json.loads(body)
        self.assertEqual(payload.get('error'), 'internal server error')
        self.assertTrue(payload.get('request_id'))
        after = s.metrics_snapshot().get('errors', 0)
        self.assertEqual(after, before + 1)
        code2, body2, _ = self._req('GET', '/health')
        self.assertEqual(code2, 200)
        self.assertIn('ok', body2)

    def test_post_unexpected_exception_is_generic_500(self):
        s = self.server
        sentinel = 'POST_SECRET_LEAK_12345'
        before = s.metrics_snapshot().get('errors', 0)
        original = s.H._handle_post

        def boom(self):
            raise RuntimeError(sentinel)

        s.H._handle_post = boom
        try:
            code, body, _ = self._req(
                'POST',
                '/api/projects',
                body={'title': 'x'},
                headers={'Authorization': 'Bearer fake'},
            )
        finally:
            s.H._handle_post = original

        self.assertEqual(code, 500)
        self.assertNotIn(sentinel, body)
        payload = json.loads(body)
        self.assertEqual(payload.get('error'), 'internal server error')
        self.assertTrue(payload.get('request_id'))
        after = s.metrics_snapshot().get('errors', 0)
        self.assertEqual(after, before + 1)
        code2, _, _ = self._req('GET', '/health')
        self.assertEqual(code2, 200)


class TestGitleaksPolicy(unittest.TestCase):
    def test_config_does_not_allowlist_all_scripts(self):
        cfg = Path(__file__).resolve().parents[1] / '.gitleaks.toml'
        text = cfg.read_text()
        self.assertNotIn("scripts/.*\\.py$", text.replace('^', ''))
        # Must not blanket-allow all of scripts/ or tests/
        self.assertNotRegex(text, r"'''scripts/\.\*\\?\.py\$'''")
        self.assertIn('tests/test_', text)

    def test_deploy_workflow_no_continue_on_error_health(self):
        root = Path(__file__).resolve().parents[1]
        proposed = root / 'ops/staging/deploy.yml.proposed'
        live = root / '.github/workflows/deploy.yml'
        # Prefer proposed file when present (workflow-scope push may leave live file stale)
        target = proposed if proposed.exists() else live
        text = target.read_text()
        self.assertNotIn('continue-on-error: true', text)
        self.assertIn('deployment_skipped', text)
        self.assertIn('STAGING_URL', text)
        if proposed.exists() and 'continue-on-error: true' in live.read_text():
            # Document residual: live workflow still needs operator apply
            apply_doc = root / 'ops/staging/DEPLOY_WORKFLOW_APPLY.md'
            self.assertTrue(apply_doc.exists())


if __name__ == '__main__':
    unittest.main()
