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
            server.IS_POSTGRES = False
        except Exception:
            pass
        server.init()
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 18092), server.H)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
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

class _FencingCases:  # merged into TestIdempotencyRecovery below (shares its server)
    """A reclaimed lease must not let the original (slow) worker win.

    Scenario: worker A reserves key K, stalls past the stuck TTL, retry B
    reclaims K and commits.  A must then abort *before* commit (409, no
    duplicate row) and must not delete/complete B's reservation.
    """

    def test_stale_worker_cannot_complete_after_reclaim(self):
        key = 'k-fenced'
        payload = {'title': 'Fenced once', 'synthetic': True}
        real_db = server.db
        state = {'done': False, 'b': None}
        from contextlib import contextmanager

        def run_retry_b():
            state['b'] = self.req('/api/projects', 'POST', payload, ADMIN,
                                  headers={'Idempotency-Key': key})

        @contextmanager
        def stall_then_continue(write=False):
            reserved = False
            if write and not state['done']:
                with real_db() as c:
                    reserved = c.execute("SELECT 1 AS x FROM idempotency_keys WHERE principal='admin' AND key=? AND state='processing'", (key,)).fetchone() is not None
            if reserved:
                state['done'] = True
                # Worker A is about to open its write tx: age its reservation
                # past the TTL and let retry B run to completion first.
                old = (datetime.now(timezone.utc) - timedelta(seconds=server.IDEMPOTENCY_STUCK_SECONDS + 100)).isoformat()
                with real_db(write=True) as c:
                    c.execute("UPDATE idempotency_keys SET created_at=? WHERE principal='admin' AND key=?", (old, key))
                server.db = real_db
                t = threading.Thread(target=run_retry_b)
                t.start(); t.join(10)
                # A's lease token is now stale (its created_at changed) -> fenced
            with real_db(write=write) as c:
                yield c

        server.db = stall_then_continue
        try:
            code_a, body_a, _ = self.req('/api/projects', 'POST', payload, ADMIN,
                                         headers={'Idempotency-Key': key})
        finally:
            server.db = real_db
        code_b, body_b, _ = state['b']
        self.assertEqual(code_b, 201, body_b)
        self.assertEqual(code_a, 409, body_a)
        self.assertIn('reclaimed', body_a['error'])
        self.assertIn('request_id', body_a)
        with server.db() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM projects WHERE title=?", ('Fenced once',)).fetchone()['n']
            row = c.execute("SELECT state,response_code FROM idempotency_keys WHERE principal='admin' AND key=?", (key,)).fetchone()
        self.assertEqual(n, 1, 'stale worker created a duplicate resource')
        self.assertEqual(dict(row)['state'], 'completed')
        self.assertEqual(dict(row)['response_code'], 201)
        # Replay with the same key returns B's stored response verbatim.
        code_r, body_r, _ = self.req('/api/projects', 'POST', payload, ADMIN,
                                     headers={'Idempotency-Key': key})
        self.assertEqual(code_r, 201)
        self.assertEqual(body_r['id'], body_b['id'])

    def test_reclaim_delete_is_fenced_statement_level(self):
        source = Path(server.__file__).read_text()
        self.assertIn("AND state='processing' AND created_at=?", source)
        self.assertIn("if deleted.rowcount == 1:", source)


    def test_stuck_ttl_is_clamped_to_safe_floor(self):
        self.assertGreaterEqual(server.IDEMPOTENCY_STUCK_SECONDS, server.IDEMPOTENCY_STUCK_FLOOR_SECONDS)
        for raw in ('0', '-5', '1', 'garbage', None, ''):
            self.assertGreaterEqual(server.resolve_stuck_seconds(raw), server.IDEMPOTENCY_STUCK_FLOOR_SECONDS, raw)
        self.assertEqual(server.resolve_stuck_seconds('600'), 600)
        self.assertEqual(server.resolve_stuck_seconds(None), 300)

    def test_release_is_fenced_to_own_lease(self):
        # A reservation held by another live worker must survive a stale release.
        key = 'k-release-fence'
        live_at = datetime.now(timezone.utc).isoformat()
        with server.db(True) as c:
            c.execute("INSERT INTO idempotency_keys(principal,key,request_hash,state,created_at) VALUES(?,?,?,?,?)",
                      ('admin', key, '1' * 64, 'processing', live_at))
        h = server.H.__new__(server.H)
        h.idem = ('admin', key, '1999-01-01T00:00:00+00:00')
        h.release_idempotency()
        with server.db() as c:
            row = c.execute("SELECT state FROM idempotency_keys WHERE principal='admin' AND key=?", (key,)).fetchone()
        self.assertIsNotNone(row, 'stale release deleted a live reservation')
        h.idem = ('admin', key, live_at)
        h.release_idempotency()
        with server.db() as c:
            row = c.execute("SELECT state FROM idempotency_keys WHERE principal='admin' AND key=?", (key,)).fetchone()
        self.assertIsNone(row)


for _name in [n for n in dir(_FencingCases) if n.startswith('test_')]:
    setattr(TestIdempotencyRecovery, _name, getattr(_FencingCases, _name))


if __name__ == '__main__':
    unittest.main()
