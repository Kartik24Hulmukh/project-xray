"""Contract tests for the HTTP execution-parallelism bound and one-shot WAL switch."""
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from app import database, server



def _journal_mode(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute('PRAGMA journal_mode').fetchone()[0]
    finally:
        conn.close()

class _Probe(server.BaseHTTPRequestHandler):
    active = 0
    peak = 0
    lock = threading.Lock()
    hold = .15

    def do_GET(self):
        cls = type(self)
        with cls.lock:
            cls.active += 1
            cls.peak = max(cls.peak, cls.active)
        try:
            time.sleep(cls.hold)
        finally:
            with cls.lock:
                cls.active -= 1
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'ok')

    def log_message(self, *args):
        pass


GatedProbe = type('GatedProbe', (_Probe,), {'do_GET': server._bound_execution(_Probe.do_GET)})


class ExecParallelismBound(unittest.TestCase):
    def test_default_and_env_override(self):
        httpd = server.BoundedHTTPServer(('127.0.0.1', 0), _Probe, max_workers=64)
        try:
            self.assertEqual(httpd.exec_parallelism, server.DEFAULT_EXEC_PARALLELISM)
            self.assertEqual(httpd.max_workers, 64)
        finally:
            httpd.server_close()
        with mock.patch.dict(os.environ, {'HTTP_EXEC_PARALLELISM': '2'}):
            httpd = server.BoundedHTTPServer(('127.0.0.1', 0), _Probe, max_workers=64)
        try:
            self.assertEqual(httpd.exec_parallelism, 2)
        finally:
            httpd.server_close()

    def test_exec_bound_never_exceeds_connection_bound(self):
        httpd = server.BoundedHTTPServer(('127.0.0.1', 0), _Probe, max_workers=3, exec_parallelism=16)
        try:
            self.assertEqual(httpd.exec_parallelism, 3)
        finally:
            httpd.server_close()

    def test_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            server.BoundedHTTPServer(('127.0.0.1', 0), _Probe, max_workers=4, exec_parallelism=0)
        with mock.patch.dict(os.environ, {'HTTP_EXEC_PARALLELISM': 'lots'}):
            with self.assertRaises(ValueError):
                server.BoundedHTTPServer(('127.0.0.1', 0), _Probe, max_workers=4)

    def test_live_runnable_handlers_capped_at_exec_bound(self):
        GatedProbe.active = GatedProbe.peak = 0
        httpd = server.BoundedHTTPServer(('127.0.0.1', 0), GatedProbe, max_workers=32, exec_parallelism=2)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        import urllib.request
        url = 'http://%s:%d/' % httpd.server_address
        try:
            with ThreadPoolExecutor(max_workers=16) as pool:
                codes = list(pool.map(lambda _: urllib.request.urlopen(url, timeout=10).status, range(16)))
            self.assertEqual(codes, [200] * 16)
            self.assertEqual(GatedProbe.peak, 2, 'execution bound bypassed')
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(2)

    def test_all_app_handlers_are_gated(self):
        for name in ('do_GET', 'do_POST', 'do_OPTIONS'):
            self.assertEqual(getattr(server.H, name).__wrapped__.__name__, name)


class WalSwitchOnce(unittest.TestCase):
    def test_journal_mode_pragma_issued_once_per_file(self):
        database.reset_wal_cache()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'once.db')
            real = sqlite3.connect
            issued = []

            def spy(*args, **kwargs):
                conn = real(*args, **kwargs)
                original = conn.execute

                class Wrapped:
                    def __getattr__(self, item):
                        return getattr(conn, item)

                    def execute(self, sql, *a):
                        if 'journal_mode' in sql:
                            issued.append(sql)
                        return original(sql, *a)
                return Wrapped()

            with mock.patch.object(database, 'IS_POSTGRES', False), mock.patch.object(database.sqlite3, 'connect', spy):
                for _ in range(5):
                    database.connect(path).close()
            self.assertEqual(len(issued), 1)
            self.assertEqual(_journal_mode(path), 'wal')
            # Recreating the file re-arms the switch (identity is path+inode).
            os.remove(path)
            database.connect(path).close()
            self.assertEqual(len(issued), 1)  # spy no longer patched; only checks no crash
            self.assertEqual(_journal_mode(path), 'wal')
        database.reset_wal_cache()

    def test_recreated_file_with_reused_inode_is_switched_to_wal(self):
        # Root cause of a CI-only failure: a deleted database's (dev, inode)
        # can be reused by the recreated file, so the identity cache claimed
        # WAL for a fresh rollback-journal file. Force identical identities.
        database.reset_wal_cache()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'reuse.db')
            with mock.patch.object(database, 'IS_POSTGRES', False), \
                    mock.patch.object(database, '_wal_identity', lambda p: ('fixed', 1, 1)):
                database.connect(path).close()
                self.assertEqual(_journal_mode(path), 'wal')
                os.remove(path)
                database.connect(path).close()
                self.assertEqual(_journal_mode(path), 'wal')
        database.reset_wal_cache()

    def test_header_probe_is_lock_free_and_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'hdr.db')
            self.assertFalse(database._header_is_wal(path))  # missing file
            conn = sqlite3.connect(path)
            conn.execute('CREATE TABLE t(x)')
            conn.commit()
            conn.close()
            self.assertFalse(database._header_is_wal(path))  # rollback journal
            conn = sqlite3.connect(path)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.close()
            self.assertTrue(database._header_is_wal(path))


if __name__ == '__main__':
    unittest.main()
