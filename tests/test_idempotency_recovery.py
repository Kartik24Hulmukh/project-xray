import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

TMP = tempfile.TemporaryDirectory()
os.environ.update(
    {
        'DB_PATH': TMP.name + '/idem.db',
        'ADMIN_TOKEN': 'test-admin-secret-long-enough',
        'REVIEWER_TOKENS': 'reviewer-a:review-token-a,reviewer-b:review-token-b',
        'SCANNER_TOKENS': 'scanner-a:scan-token-a',
        'PORT': '18092',
        'APP_ENV': 'test',
        'TOKEN_PEPPER': 'test-token-pepper-[number]',
        'AUDIT_HMAC_KEY': 'test-audit-key-[number]',
        'BACKUP_HMAC_KEY': 'test-backup-key-[number]',
        'WRITE_RATE_LIMIT': '1000',
        'PUBLIC_READ_RATE_LIMIT': '1000',
        'AUTH_READ_RATE_LIMIT': '1000',
        'EXPENSIVE_WRITE_RATE_LIMIT': '1000',
    }
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import server

ADMIN = 'test-admin-secret-long-enough'


class TestIdempotencyRecovery(unittest.TestCase):
    """Regression guards for idempotency-reservation recovery paths.

    A reservation that never reaches an outcome (worker crash, commit
    failure, rejected write) must not poison the client key forever.
    """

    @classmethod
    def setUpClass(cls):
        os.environ['DB_PATH'] = TMP.name + '/idem.db'
        server.DB = Path(os.environ['DB_PATH'])
        try:
            from app import database as database
            database.DB_PATH = server.DB
            database.DATABASE_URL = ''
            database.IS_POSTGRES = False
        except Exception:
            pass
        server.init()
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 18092), server.H)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        TMP.cleanup()

    def setUp(self):
        for name in (
            'DISABLE_WRITES',
            'DISABLE_UPLOADS',
            'DISABLE_PUBLICATION',
            'READ_ONLY_MODE',
            'MAINTENANCE_MODE',
            'DISABLE_PUBLIC_READS',
        ):
            os.environ.pop(name, None)
        with server._RATE_LOCK:
            server.RATE.clear()

    def req(self, path, method='GET', data=None, token=None, headers=None):
        request_headers = {'Content-Type': 'application/json', **(headers or {})}
        if token:
            request_headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(
            'http://127.0.0.1:18092' + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                body = (
                    json.loads(response.read())
                    if 'json' in response.headers.get('Content-Type', '')
                    else response.read().decode()
                )
                return response.status, body, dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read()), dict(exc.headers)

    def test_commit_failure_returns_500_and_retry_succeeds(self):
        real_db = server.db
        calls = {'n': 0}

        from contextlib import contextmanager

        @contextmanager
        def flaky(write=False):
            calls['n'] += 1
            if write and calls['n'] == 2:
                raise RuntimeError('simulated commit failure')
            with real_db(write=write) as c:
                yield c

        server.db = flaky
        try:
            code, body, _ = self.req(
                '/api/projects',
                'POST',
                {'title': 'Retryable', 'synthetic': True},
                ADMIN,
                headers={'Idempotency-Key': 'k-commit-fail'},
            )
        finally:
            server.db = real_db
        self.assertEqual(code, 500)
        self.assertIn('request_id', body)
        code2, body2, _ = self.req(
            '/api/projects',
            'POST',
            {'title': 'Retryable', 'synthetic': True},
            ADMIN,
            headers={'Idempotency-Key': 'k-commit-fail'},
        )
        self.assertEqual(code2, 201)
        self.assertIn('id', body2)

    def test_rejected_write_releases_reservation_for_corrected_retry(self):
        code, body, _ = self.req(
            '/api/projects',
            'POST',
            {'synthetic': True},
            ADMIN,
            headers={'Idempotency-Key': 'k-bad-payload'},
        )
        self.assertEqual(code, 400)
        code2, body2, _ = self.req(
            '/api/projects',
            'POST',
            {'title': 'Corrected payload', 'synthetic': True},
            ADMIN,
            headers={'Idempotency-Key': 'k-bad-payload'},
        )
        self.assertEqual(code2, 201)

    def test_stuck_reservation_reclaimed_after_ttl(self):
        key = 'k-stuck'
        old = (
            datetime.now(timezone.utc)
            - timedelta(seconds=server.IDEMPOTENCY_STUCK_SECONDS + 100)
        ).isoformat()
        with server.db(True) as c:
            c.execute(
                "INSERT INTO idempotency_keys(principal,key,request_hash,state,created_at) "
                "VALUES(?,?,?,?,?)",
                ('admin', key, '0' * 64, 'processing', old),
            )
        code, body, _ = self.req(
            '/api/projects',
            'POST',
            {'title': 'Reclaimed', 'synthetic': True},
            ADMIN,
            headers={'Idempotency-Key': key},
        )
        self.assertEqual(code, 201)
        with server.db() as c:
            events = [
                dict(r)
                for r in c.execute(
                    "SELECT action,object_type FROM audit_events WHERE object_id=?", (key,)
                )
            ]
        self.assertTrue(any(e['action'] == 'reclaim' for e in events))

    def test_fresh_processing_reservation_still_conflicts(self):
        import hashlib

        key = 'k-fresh'
        data = {'title': 'In flight', 'synthetic': True}
        request_hash = hashlib.sha256(
            (
                'POST|/api/projects|'
                + json.dumps(data, sort_keys=True, separators=(',', ':'))
            ).encode()
        ).hexdigest()
        with server.db(True) as c:
            c.execute(
                "INSERT INTO idempotency_keys(principal,key,request_hash,state,created_at) "
                "VALUES(?,?,?,?,?)",
                ('admin', key, request_hash, 'processing', datetime.now(timezone.utc).isoformat()),
            )
        code, body, _ = self.req(
            '/api/projects', 'POST', data, ADMIN, headers={'Idempotency-Key': key},
        )
        self.assertEqual(code, 409)
        self.assertIn('processing', body['error'])
        # A different payload under the same in-flight key is a hard conflict.
        code2, body2, _ = self.req(
            '/api/projects',
            'POST',
            {'title': 'Different payload', 'synthetic': True},
            ADMIN,
            headers={'Idempotency-Key': key},
        )
        self.assertEqual(code2, 409)
        self.assertIn('reused with different request', body2['error'])

    def test_completed_replay_still_returns_stored_response(self):
        headers = {'Idempotency-Key': 'k-replay'}
        body = {'title': 'Replay fixture', 'synthetic': True}
        first = self.req('/api/projects', 'POST', body, ADMIN, headers=headers)
        second = self.req('/api/projects', 'POST', body, ADMIN, headers=headers)
        self.assertEqual(first[0], 201)
        self.assertEqual(second[0], 201)
        self.assertEqual(first[1]['id'], second[1]['id'])
        conflict = self.req('/api/projects', 'POST', {'title': 'Other'}, ADMIN, headers=headers)
        self.assertEqual(conflict[0], 409)


if __name__ == '__main__':
    unittest.main()
