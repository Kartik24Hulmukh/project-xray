"""Real socket lifecycle regression tests; disposable loopback only."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from unittest.mock import patch

from app.server import BoundedHTTPServer


class ShutdownTests(unittest.TestCase):
    def test_admitted_response_drains_before_exporter(self):
        entered, release = threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                entered.set()
                release.wait(2)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'committed')
            def log_message(self, *args):
                pass
        server = BoundedHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        client = socket.create_connection(server.server_address)
        client.settimeout(3)
        client.sendall(b'GET / HTTP/1.0\r\n\r\n')
        self.assertTrue(entered.wait(2))
        class Observer:
            def shutdown(inner):
                self.assertFalse(server._requests)
        server.telemetry = Observer()
        try:
            server.begin_shutdown()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            timer = threading.Timer(.1, release.set)
            timer.start()
            result = server.server_close(drain_timeout=2)
            response = b''
            while chunk := client.recv(4096):
                response += chunk
            self.assertIn(b'committed', response)
            self.assertEqual(result['unfinished_requests'], 0)
            self.assertTrue(result['telemetry_shutdown_finished'])
            self.assertIsNone(server.server_close())
        finally:
            release.set()
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_saturated_admission_does_not_deadlock_shutdown(self):
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(2)
        server = BoundedHTTPServer(('127.0.0.1', 0), Handler, max_workers=1)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        clients = [socket.create_connection(server.server_address) for _ in range(2)]
        try:
            deadline = time.monotonic() + 2
            while not server._requests and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(len(server._requests), 1)
            time.sleep(.1)  # second connection enters admission wait
            server.begin_shutdown()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            start = time.monotonic()
            result = server.server_close(drain_timeout=.1)
            self.assertLess(time.monotonic() - start, .8)
            self.assertEqual(result['unfinished_requests'], 1)
        finally:
            for client in clients:
                client.close()
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_exporter_outage_has_bounded_cleanup(self):
        server = BoundedHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        release = threading.Event()
        class Observer:
            def shutdown(self):
                release.wait(2)
        server.telemetry = Observer()
        try:
            start = time.monotonic()
            result = server.server_close(drain_timeout=.1)
            self.assertLess(time.monotonic() - start, .8)
            self.assertFalse(result['telemetry_shutdown_finished'])
        finally:
            release.set()

    def test_telemetry_provider_does_not_register_unbounded_atexit(self):
        from app.telemetry import Telemetry
        from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
        class Exporter(SpanExporter):
            def export(self, spans):
                return SpanExportResult.SUCCESS
        observer = Telemetry(exporter=Exporter())
        try:
            self.assertIsNone(observer.provider._atexit_handler)
        finally:
            observer.shutdown()

    def test_readiness_fails_closed_during_drain(self):
        import urllib.request
        import urllib.error
        from app.server import H
        server = BoundedHTTPServer(('127.0.0.1', 0), H)
        loop = threading.Thread(target=server.serve_forever)
        loop.start()
        # The request is admitted before draining; the handler must still reject
        # readiness. Override admission only for this deterministic boundary test.
        from socketserver import ThreadingMixIn
        server.process_request = lambda request, address: ThreadingMixIn.process_request(server, request, address)
        server.process_request_thread = lambda request, address: ThreadingMixIn.process_request_thread(server, request, address)
        server._draining.set()
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/readyz', timeout=2)
            self.assertEqual(caught.exception.code, 503)
            caught.exception.close()
        finally:
            server.shutdown(); server.server_close(); loop.join(2)

    def test_invalid_deadline_rejected(self):
        server = BoundedHTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler)
        try:
            for value in (-1, float('nan'), float('inf')):
                with self.assertRaises(ValueError):
                    server.server_close(drain_timeout=value)
        finally:
            server.server_close()

    @unittest.skipUnless(os.name == 'posix', 'POSIX signals')
    def test_sigterm_cli_exits_cleanly_and_emits_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0))
                port = probe.getsockname()[1]
            env = dict(os.environ, DB_PATH=str(Path(directory)/'test.db'),
                       APP_ENV='test', PORT=str(port), BIND_HOST='127.0.0.1',
                       XRAY_OTEL_ENABLED='0')
            env.pop('DATABASE_URL', None)
            process = subprocess.Popen([sys.executable, 'app/server.py'],
                cwd=Path(__file__).resolve().parents[1], env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                for _ in range(100):
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=.1):
                            break
                    except OSError:
                        if process.poll() is not None:
                            self.fail('server exited before startup')
                        time.sleep(.03)
                else:
                    self.fail('startup timeout')
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=4)
                self.assertEqual(process.returncode, 0, stderr)
                receipt = [json.loads(line) for line in stdout.splitlines()
                           if '"event": "shutdown"' in line]
                self.assertEqual(len(receipt), 1)
                self.assertEqual(receipt[0]['unfinished_requests'], 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == '__main__':
    unittest.main()
