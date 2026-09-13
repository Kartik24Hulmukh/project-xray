"""Regressions for packaging, input boundaries, logs and connection admission."""
import io
import json
import socket
import threading
import time
import unittest
from contextlib import redirect_stdout
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from app import server


class InputBoundaries(unittest.TestCase):
    def test_json_requires_object(self):
        for raw in [b'null', b'[]', b'1', b'"text"', b'true']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                server.strict_json(raw)

    def test_json_invalid_values_and_encoding(self):
        for raw in [b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}',
                    b'{"x":1,"x":2}', b'{"x":"\xff"}', b'[' * 2000]:
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError):
                server.strict_json(raw)
        self.assertEqual(server.strict_json(b'{"x":1}'), {'x': 1})

    def handler(self, headers, raw=b'{}'):
        h = object.__new__(server.H)
        h.headers = Message()
        for k, v in headers:
            h.headers[k] = v
        h.rfile = io.BytesIO(raw)
        return h

    def test_ambiguous_framing_rejected(self):
        for headers in [ [('Content-Length','2'),('Content-Length','2')],
                         [('Content-Length','2'),('Transfer-Encoding','chunked')],
                         [('Content-Length','+2')], [('Content-Length','-2')], [] ]:
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                self.handler(headers).body()

    def test_truncated_body_rejected(self):
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.handler([('Content-Length','5'),('Content-Type','application/json')]).body()

    def test_request_ids_bounded(self):
        for value in ['abc\r\nInjected: yes', 'x'*65, 'with spaces', '']:
            h = self.handler([('X-Request-ID', value)])
            self.assertRegex(h.safe_request_id(), r'^req_[a-f0-9]{16}$')
        self.assertEqual(self.handler([('X-Request-ID','safe_123-abc')]).safe_request_id(), 'safe_123-abc')

    def test_exception_text_absent_from_logs(self):
        h = self.handler([])
        h.path = '/health'
        h.request_id = 'test'
        h._xray_headers_sent = True
        output = io.StringIO()
        with redirect_stdout(output):
            try:
                raise RuntimeError('sensitive-sentinel-must-not-be-logged')
            except RuntimeError as exc:
                h._fail_safe(exc)
        record = json.loads(output.getvalue())
        self.assertNotIn('sensitive-sentinel-must-not-be-logged', output.getvalue())
        self.assertEqual(record['exception_class'], 'RuntimeError')
        self.assertTrue(record['stack'])


class WorkerAdmission(unittest.TestCase):
    def test_saturation_backpressures_then_recovers(self):
        entered = threading.Event()
        second_entered = threading.Event()
        release = threading.Event()
        calls = []
        class Handler(server.BaseHTTPRequestHandler):
            def handle(self):
                calls.append(1)
                if len(calls) == 1:
                    entered.set()
                    release.wait(3)
                else:
                    second_entered.set()
            def log_message(self, *args):
                pass
        httpd = server.BoundedHTTPServer(('127.0.0.1', 0), Handler, max_workers=1)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        first = socket.create_connection(httpd.server_address, timeout=2)
        try:
            self.assertTrue(entered.wait(2))
            with socket.create_connection(httpd.server_address, timeout=2):
                self.assertFalse(second_entered.wait(.1), 'worker limit bypassed')
                release.set()
                self.assertTrue(second_entered.wait(2), 'admission did not recover')
        finally:
            release.set()
            first.close()
            httpd.shutdown()
            httpd.server_close()
            thread.join(2)

    def test_invalid_worker_limit(self):
        with self.assertRaises(ValueError):
            server.BoundedHTTPServer(('127.0.0.1',0), server.H, max_workers=0)


class ImageContents(unittest.TestCase):
    def test_image_copies_dashboard_and_recovery_dependency(self):
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root/'Dockerfile').read_text()
        self.assertIn('COPY static/ ./static/', dockerfile)
        self.assertIn('COPY scripts/recovery.py ./scripts/recovery.py', dockerfile)
        for path in ['static/index.html','static/styles.css','static/app.js','scripts/recovery.py']:
            self.assertTrue((root/path).is_file())

if __name__ == '__main__':
    unittest.main()


class DurableResponses(unittest.TestCase):
    def handler(self):
        h = object.__new__(server.H)
        h.headers = Message()
        h.wfile = io.BytesIO()
        h.idem = None
        h.tx = None
        h._xray_headers_sent = False
        h.common = lambda code, ctype, extra_headers=None: setattr(h, 'sent_code', code)
        return h

    def test_commit_failure_never_sends_success(self):
        h = self.handler()
        def fail():
            h.tx = object()
            h.out({'id': 'not-committed'}, 201)
            self.assertEqual(h.wfile.getvalue(), b'')
            h.tx = None
            raise RuntimeError('simulated commit failure')
        h._handle_post = fail
        h._fail_safe = lambda exc, method: setattr(h, 'failed', True)
        h.do_POST()
        self.assertTrue(h.failed)
        self.assertFalse(hasattr(h, 'sent_code'))
        self.assertEqual(h.wfile.getvalue(), b'')

    def test_success_written_after_transaction(self):
        h = self.handler()
        def success():
            h.tx = object()
            h.out({'id': 'durable'}, 201)
            self.assertEqual(h.wfile.getvalue(), b'')
            h.tx = None
        h._handle_post = success
        h.do_POST()
        self.assertEqual(h.sent_code, 201)
        self.assertEqual(json.loads(h.wfile.getvalue()), {'id': 'durable'})


class BackupDirection(unittest.TestCase):
    def test_adapter_backup_copies_source_into_target(self):
        import sqlite3
        from app.database import ConnectionAdapter
        source = sqlite3.connect(':memory:')
        target = sqlite3.connect(':memory:')
        try:
            source.execute('CREATE TABLE marker (value TEXT)')
            source.execute("INSERT INTO marker VALUES ('keep-source')")
            source.commit()
            src, dst = ConnectionAdapter(source), ConnectionAdapter(target)
            src._is_pg = dst._is_pg = False
            src.backup(dst)
            self.assertEqual(target.execute('SELECT value FROM marker').fetchone()[0], 'keep-source')
            self.assertEqual(source.execute('SELECT value FROM marker').fetchone()[0], 'keep-source')
        finally:
            source.close()
            target.close()
