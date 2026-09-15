#!/usr/bin/env python3
"""Launch-hardening regressions (2026-09-15): bounded graceful drain on SIGTERM.

Root cause pinned here: the production entrypoint parked in ``serve_forever()``
under the *default* SIGTERM disposition, so a rolling restart killed the process
instantly (rc=-15) with SQLite transactions in flight and OTLP spans queued.
These tests are deterministic (no sleeps longer than the bound, fixed seeds) and
fail loudly if the drain path regresses.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestDrainUnit(unittest.TestCase):
    """In-process contract for BoundedHTTPServer.drain()."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._prev = {k: os.environ.get(k) for k in ('DB_PATH', 'DATABASE_URL')}
        os.environ['DB_PATH'] = str(Path(cls.tmp.name) / 'drain.db')
        os.environ.pop('DATABASE_URL', None)
        import app.database as database
        import app.server as server
        database.DB_PATH = Path(os.environ['DB_PATH'])
        database.DATABASE_URL = ''
        database.IS_POSTGRES = False
        server.DB = database.DB_PATH
        server.init()
        cls.server_mod = server

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def _serve(self):
        httpd = self.server_mod.BoundedHTTPServer(('127.0.0.1', 0), self.server_mod.H)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, thread

    def test_drain_is_bounded_and_reports_completion(self):
        httpd, thread = self._serve()
        port = httpd.server_address[1]
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=5) as r:
            self.assertEqual(r.status, 200)
        # Handler teardown is concurrent with the client's socket close, so
        # poll (bounded) rather than asserting on an instantaneous sample.
        deadline = time.monotonic() + 2
        while httpd.inflight() > 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(httpd.inflight(), 0, 'request accounting must return to zero')
        started = time.monotonic()
        self.assertTrue(httpd.drain(timeout=5))
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0, 'idle drain must return immediately, not wait out the bound')
        self.assertTrue(httpd.draining)
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), 'accept loop must stop before close')

    def test_drain_stops_accepting_new_connections(self):
        httpd, thread = self._serve()
        port = httpd.server_address[1]
        httpd.drain(timeout=5)
        thread.join(timeout=5)
        with self.assertRaises((urllib.error.URLError, ConnectionError, OSError)):
            urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=2)

    def test_drain_is_idempotent(self):
        httpd, thread = self._serve()
        self.assertTrue(httpd.drain(timeout=5))
        self.assertTrue(httpd.drain(timeout=5))  # must not raise on a closed server
        thread.join(timeout=5)


    def test_drain_waits_for_an_in_flight_request(self):
        """A request already executing must complete before the listener closes."""
        httpd, thread = self._serve()
        httpd.request_started()  # simulate one handler mid-transaction
        result = {}

        def _drain():
            result['completed'] = httpd.drain(timeout=3)

        t = threading.Thread(target=_drain, daemon=True)
        started = time.monotonic()
        t.start()
        time.sleep(0.3)
        self.assertTrue(t.is_alive(), 'drain must not abandon an in-flight request')
        httpd.request_finished()
        t.join(timeout=5)
        self.assertTrue(result.get('completed'), 'drain must report success once work finished')
        self.assertLess(time.monotonic() - started, 3.0, 'drain must not wait out the full bound')
        thread.join(timeout=5)

    def test_drain_bound_is_enforced_when_work_never_finishes(self):
        httpd, thread = self._serve()
        httpd.request_started()  # never finishes: a wedged handler
        started = time.monotonic()
        self.assertFalse(httpd.drain(timeout=0.5), 'wedged handler must report an incomplete drain')
        self.assertLess(time.monotonic() - started, 5.0, 'drain must stay bounded, never hang a restart')
        httpd.request_finished()
        thread.join(timeout=5)

    def test_handlers_installed_before_startup_announcement(self):
        source = (ROOT / 'app/server.py').read_text()
        main_body = source[source.index('def main():'):]
        self.assertLess(
            main_body.index('install_drain_handlers'),
            main_body.index("'event': 'startup'"),
            'signal handlers must be installed before startup is announced, '
            'otherwise a fast orchestrator SIGTERM lands on the default disposition',
        )


class TestSigtermDrainSubprocess(unittest.TestCase):
    """Real process, real signal: the only test that can prove rc != -15."""

    def test_sigterm_drains_and_exits_cleanly(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = dict(os.environ)
        env.update({
            'DB_PATH': str(Path(tmp.name) / 'sigterm.db'),
            'PORT': '0',
            'BIND_HOST': '127.0.0.1',
            'XRAY_DRAIN_SECONDS': '5',
            'PYTHONHASHSEED': '0',
            'PYTHONPATH': str(ROOT),
            'PYTHONUNBUFFERED': '1',
        })
        env.pop('DATABASE_URL', None)
        proc = subprocess.Popen([sys.executable, str(ROOT / 'app/server.py')], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                cwd=str(ROOT))
        try:
            deadline = time.monotonic() + 30
            startup = None
            while time.monotonic() < deadline:
                line = proc.stdout.readline()
                if not line:
                    break
                if '\"event\": \"startup\"' in line or '"startup"' in line:
                    startup = json.loads(line)
                    break
            self.assertIsNotNone(startup, 'server did not announce startup')
            self.assertIn('drain_seconds', startup)
            proc.send_signal(signal.SIGTERM)
            out, _err = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        self.assertEqual(proc.returncode, 0,
                         f'SIGTERM must drain, not kill (rc={proc.returncode})')
        shutdown = [json.loads(l) for l in out.splitlines()
                    if l.startswith('{') and '"shutdown"' in l]
        self.assertTrue(shutdown, f'no structured shutdown event emitted; stdout={out[-500:]!r}')
        self.assertTrue(shutdown[-1]['drained'])


if __name__ == '__main__':
    unittest.main()
