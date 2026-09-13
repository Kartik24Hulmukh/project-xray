"""W3C traceparent boundary sanitisation and propagation (OpenTelemetry-compatible)."""
import json
import os
import socket
import sys
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from app import server

ROOT = Path(__file__).resolve().parents[1]
VALID = '00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01'


class TraceContextUnit(unittest.TestCase):
    def handler(self, header):
        h = server.H.__new__(server.H)
        h._trace = None
        from email.message import Message
        h.headers = Message()
        if header is not None:
            h.headers['traceparent'] = header
        return h

    def test_valid_inbound_trace_is_continued_with_new_span(self):
        trace_id, span_id, tp = self.handler(VALID).trace_context()
        self.assertEqual(trace_id, '4bf92f3577b34da6a3ce929d0e0e4736')
        self.assertNotEqual(span_id, '00f067aa0ba902b7')
        self.assertEqual(tp, f'00-{trace_id}-{span_id}-01')

    def test_garbage_and_zero_ids_are_rejected_and_replaced(self):
        for bad in ('', 'nonsense', '00-' + '0' * 32 + '-00f067aa0ba902b7-01', '00-zz-yy-01',
                    '00-4bf92f3577b34da6a3ce929d0e0e4736-' + '0' * 16 + '-01', 'x' * 500,
                    '00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01\r\nInjected: 1'):
            trace_id, span_id, tp = self.handler(bad).trace_context()
            self.assertRegex(trace_id, r'^[0-9a-f]{32}$')
            self.assertRegex(span_id, r'^[0-9a-f]{16}$')
            self.assertEqual(tp, f'00-{trace_id}-{span_id}-01')
            self.assertNotIn('\r', tp)

    def test_absent_header_mints_trace(self):
        trace_id, _, _ = self.handler(None).trace_context()
        self.assertRegex(trace_id, r'^[0-9a-f]{32}$')

    def test_cached_per_request(self):
        h = self.handler(None)
        self.assertEqual(h.trace_context(), h.trace_context())


class TraceContextLive(unittest.TestCase):
    def test_header_echoed_and_logged(self):
        with tempfile.TemporaryDirectory() as d:
            with socket.socket() as s:
                s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
            env = {**os.environ, 'DB_PATH': str(Path(d) / 't.db'), 'APP_ENV': 'test', 'PORT': str(port),
                   'BIND_HOST': '127.0.0.1', 'ADMIN_TOKEN': 'trace-admin', 'PYTHONUNBUFFERED': '1'}
            env.pop('DATABASE_URL', None)
            proc = subprocess.Popen([sys.executable, 'app/server.py'], cwd=ROOT, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                for _ in range(100):
                    try:
                        urllib.request.urlopen(f'http://127.0.0.1:{port}/livez', timeout=1); break
                    except Exception:
                        time.sleep(.1)
                req = urllib.request.Request(f'http://127.0.0.1:{port}/api/projects', headers={'traceparent': VALID})
                with urllib.request.urlopen(req, timeout=5) as r:
                    tp = r.headers['traceparent']
                self.assertTrue(tp.startswith('00-4bf92f3577b34da6a3ce929d0e0e4736-'))
                self.assertNotEqual(tp, VALID)
            finally:
                proc.terminate()
                out, _ = proc.communicate(timeout=5)
            lines = [json.loads(l) for l in out.splitlines() if l.startswith('{')]
            traced = [l for l in lines if l.get('trace_id') == '4bf92f3577b34da6a3ce929d0e0e4736']
            self.assertTrue(traced, out)
            self.assertEqual(traced[0]['span_id'], tp.split('-')[2])


if __name__ == '__main__':
    unittest.main()
