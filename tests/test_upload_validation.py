"""Upload-boundary validation: every malformed document registration must be a
clean 4xx (never a 500), filenames must be inert, and the declared extension
must agree with the declared media type before a document enters quarantine."""
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

_STANDALONE_ENV = {
    'APP_ENV': 'test',
    'ADMIN_TOKEN': 'test-admin-secret-long-enough',
    'TOKEN_PEPPER': 'test-token-pepper-0123456789abcdef',
    'AUDIT_HMAC_KEY': 'test-audit-key-0123456789abcdef',
    'BACKUP_HMAC_KEY': 'test-backup-key-0123456789abcdef',
    'WRITE_RATE_LIMIT': '100000',
    'EXPENSIVE_WRITE_RATE_LIMIT': '100000',
    'AUTH_READ_RATE_LIMIT': '100000',
    'PUBLIC_READ_RATE_LIMIT': '100000',
}
PORT = 18097


def _server():
    if 'app.server' not in sys.modules:
        for key, value in _STANDALONE_ENV.items():
            os.environ.setdefault(key, value)
    from app import server
    return server


class TestUploadValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = server = _server()
        cls.tmp = tempfile.TemporaryDirectory()
        cls._saved_db = server.DB
        server.DB = Path(cls.tmp.name) / 'upload.db'
        try:
            from app import database
            cls._saved_database = (database.DB_PATH, database.DATABASE_URL, database.IS_POSTGRES)
            database.DB_PATH, database.DATABASE_URL, database.IS_POSTGRES = server.DB, '', False
        except Exception:
            cls._saved_database = None
        server.init()
        with server._RATE_LOCK:
            cls._rate_backup = dict(server.RATE)
            server.RATE.clear()
        # Production listener (backlog 128 + bounded admission). The stdlib
        # ThreadingHTTPServer has a listen backlog of 5, which resets bursts of
        # 32 concurrent connects on CI runners -- a harness artefact, not the
        # product behaviour under test.
        cls.http = server.BoundedHTTPServer(('127.0.0.1', PORT), server.H, max_workers=64)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.admin = server.ADMIN_TOKEN
        status, body = cls.req('/api/projects', 'POST', {'title': 'Upload boundary', 'authority': 'Example', 'synthetic': True})
        assert status == 201, (status, body)
        cls.pid = body['id']

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        server = cls.server
        server.DB = cls._saved_db
        if cls._saved_database:
            from app import database
            database.DB_PATH, database.DATABASE_URL, database.IS_POSTGRES = cls._saved_database
        with server._RATE_LOCK:
            server.RATE.clear()
            server.RATE.update(cls._rate_backup)
        cls.tmp.cleanup()

    def setUp(self):
        with self.server._RATE_LOCK:
            self.server.RATE.clear()

    @classmethod
    def req(cls, path, method='GET', data=None, raw=None):
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + cls.admin}
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        r = urllib.request.Request('http://127.0.0.1:%d%s' % (PORT, path), data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b'{}')
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            exc.close()
            try:
                return exc.code, json.loads(payload)
            except ValueError:
                return exc.code, {}

    def doc(self, **over):
        d = {'filename': 'report.pdf', 'media_type': 'application/pdf', 'size_bytes': 128, 'sha256': os.urandom(32).hex()}
        d.update(over)
        return self.req('/api/projects/%s/documents' % self.pid, 'POST', d)

    def assertRejected(self, **over):
        status, body = self.doc(**over)
        self.assertEqual(status, 400, (over, status, body))
        self.assertIn('error', body)

    def test_valid_document_enters_quarantine(self):
        status, body = self.doc()
        self.assertEqual(status, 201, body)
        self.assertEqual(body['storage_state'], 'quarantined')

    def test_size_must_be_a_real_integer(self):
        for bad in ('abc', None, True, False, 1.5, [], {}, '128', -1, 10 ** 30):
            with self.subTest(size=bad):
                self.assertRejected(size_bytes=bad)

    def test_zero_byte_document_rejected(self):
        self.assertRejected(size_bytes=0)

    def test_non_string_fields_rejected_not_500(self):
        for field in ('sha256', 'media_type', 'filename', 'storage_uri', 'source_id'):
            for bad in (123, ['x'], {'a': 1}, True):
                with self.subTest(field=field, value=bad):
                    self.assertRejected(**{field: bad})

    def test_path_traversal_and_control_chars_rejected(self):
        for bad in ('\x2e\x2e/\x2e\x2e/etc/passwd.pdf', '\x2e\x2e\\win.pdf', '/abs/report.pdf', 'a/b.pdf', 'nul\x00.pdf', 'line\nbreak.pdf', '.pdf', '', ' ', 'x' * 256 + '.pdf'):
            with self.subTest(filename=bad):
                self.assertRejected(filename=bad)

    def test_extension_must_match_media_type(self):
        for name, media in (('evil.exe', 'application/pdf'), ('report.pdf.exe', 'application/pdf'), ('data.csv', 'image/png'), ('noext', 'text/plain'), ('shell.php', 'text/plain')):
            with self.subTest(filename=name, media=media):
                self.assertRejected(filename=name, media_type=media)

    def test_allowed_pairs_accepted(self):
        for name, media in (('a.pdf', 'application/pdf'), ('b.TXT', 'text/plain'), ('c.csv', 'text/csv'), ('d.json', 'application/json'), ('e.png', 'image/png'), ('f.jpg', 'image/jpeg'), ('g.jpeg', 'image/jpeg')):
            with self.subTest(filename=name):
                status, body = self.doc(filename=name, media_type=media)
                self.assertEqual(status, 201, body)

    def test_uppercase_sha_normalised(self):
        status, body = self.doc(sha256=os.urandom(32).hex().upper())
        self.assertEqual(status, 201, body)

    def test_body_must_be_object(self):
        for raw in (b'[]', b'"x"', b'1', b'null', b'{bad json'):
            with self.subTest(raw=raw):
                status, _ = self.req('/api/projects/%s/documents' % self.pid, 'POST', raw=raw)
                self.assertEqual(status, 400)

    def test_scan_result_type_confusion_rejected(self):
        status, body = self.doc()
        did = body['id']
        for bad in (None, 1, ['clean'], {'r': 'clean'}, 'CLEAN'):
            with self.subTest(result=bad):
                s, _ = self.req('/api/projects/%s/documents/%s/scan' % (self.pid, did), 'POST', {'result': bad})
                self.assertIn(s, (400, 403))

    def test_hostile_concurrent_uploads_never_500(self):
        payloads = []
        for i in range(200):
            payloads.append([
                {'filename': 'r%d.pdf' % i, 'media_type': 'application/pdf', 'size_bytes': i + 1, 'sha256': '%064x' % i},
                {'filename': '\x2e\x2e/x%d.pdf' % i, 'media_type': 'application/pdf', 'size_bytes': 1, 'sha256': 'c' * 64},
                {'filename': 'r.pdf', 'media_type': 'application/pdf', 'size_bytes': 'NaN', 'sha256': 'c' * 64},
                {'filename': 'r.exe', 'media_type': 'application/pdf', 'size_bytes': 5, 'sha256': 'c' * 64},
            ][i % 4])
        url = '/api/projects/%s/documents' % self.pid
        with ThreadPoolExecutor(max_workers=32) as pool:
            statuses = list(pool.map(lambda d: self.req(url, 'POST', d)[0], payloads))
        self.assertNotIn(500, statuses)
        self.assertEqual(statuses.count(201), 50)
        self.assertEqual(statuses.count(400), 150)


if __name__ == '__main__':
    unittest.main()
