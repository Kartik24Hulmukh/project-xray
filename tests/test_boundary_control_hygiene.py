"""Write-boundary hygiene (session 20).

Root causes closed: clean() accepted C0/DEL control characters (NUL was stored);
non-string source_id on responses reached the SQLite binder (500 + traceback);
gaps.status was not validated server-side (storage CHECK surfaced as 409);
verify_segment materialised its slice with fetchall().
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ADMIN = 'boundary-admin-token-[number]'
RA = 'boundary-reviewer-a-token'
RB = 'boundary-reviewer-b-token'


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def call(port, path, method='GET', body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(
        f'http://127.0.0.1:{port}{path}',
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        return exc.code, json.loads(payload) if payload else {}



class TestBoundaryUnit(unittest.TestCase):
    def setUp(self):
        from app import server
        self.server = server

    def test_clean_rejects_every_c0_and_del(self):
        for code in list(range(0, 9)) + [11, 12] + list(range(14, 32)) + [127]:
            with self.assertRaises(ValueError, msg=code):
                self.server.clean('A' + chr(code) + 'B', 100)

    def test_clean_keeps_ordinary_whitespace_behaviour(self):
        self.assertEqual(self.server.clean('  hello\tworld\n ', 100), 'hello\tworld')
        self.assertEqual(self.server.clean('Zoë – “quoted”', 100), 'Zoë – “quoted”')

    def test_gap_statuses_match_both_schemas(self):
        import re
        for name in ('db/schema.sql', 'db/schema_postgres.sql'):
            text = (ROOT / name).read_text()
            m = re.search(r"status TEXT NOT NULL DEFAULT 'not_located' CHECK\(status IN \(([^)]*)\)\)", text)
            self.assertIsNotNone(m, name)
            values = tuple(v.strip().strip("'") for v in m.group(1).split(','))
            self.assertEqual(values, self.server.GAP_STATUSES, name)

    def test_gap_status_validator(self):
        self.assertEqual(self.server.gap_status(None), 'not_located')
        for good in self.server.GAP_STATUSES:
            self.assertEqual(self.server.gap_status(good), good)
        for bad in ('bogus', '', 1, ['requested'], 'Requested'):
            with self.assertRaises(ValueError):
                self.server.gap_status(bad)

    def test_verify_segment_streams_without_fetchall(self):
        from app import audit
        calls = {'fetchall': 0, 'fetchmany': 0}

        class SpyCursor:
            def fetchall(self):
                calls['fetchall'] += 1
                return []

            def fetchmany(self, size):
                calls['fetchmany'] += 1
                self.size = size
                return []

        class SpyConn:
            def execute(self, sql, params=()):
                return SpyCursor()

        result = audit.verify_segment(SpyConn(), b'k', 0, 'genesis', 1000, 0)
        self.assertEqual(calls['fetchall'], 0)
        self.assertGreaterEqual(calls['fetchmany'], 1)
        self.assertEqual(result['scanned'], 0)
        self.assertTrue(result['complete'])
        self.assertLessEqual(audit._VERIFY_STREAM_BATCH, 1000)


class TestBoundaryHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / 'boundary.db'
        cls.port = free_port()
        env = {**os.environ, 'DB_PATH': str(cls.db), 'PORT': str(cls.port), 'ADMIN_TOKEN': ADMIN,
               'REVIEWER_TOKENS': f'reviewer-a:{RA},reviewer-b:{RB}', 'APP_ENV': 'test',
               'WRITE_RATE_LIMIT': '100000', 'PUBLIC_READ_RATE_LIMIT': '100000',
               'AUTH_READ_RATE_LIMIT': '100000', 'EXPENSIVE_WRITE_RATE_LIMIT': '100000'}
        env.pop('DATABASE_URL', None)
        cls.proc = subprocess.Popen([sys.executable, 'app/server.py'], cwd=ROOT, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                if call(cls.port, '/healthz')[0] == 200:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise RuntimeError(cls.proc.stderr.read().decode())

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        cls.proc.stderr.close()
        cls.tmp.cleanup()

    def project(self, authority='Synthetic Authority'):
        return call(self.port, '/api/projects', 'POST',
                    {'title': 'Boundary fixture', 'authority': authority, 'synthetic': True}, ADMIN)

    def test_control_characters_rejected_not_stored(self):
        for bad in ('A\u0000B', '\u0001\u0002', 'x\u007fy', 'x\u001by'):
            status, body = self.project(bad)
            self.assertEqual(status, 400, bad)
            self.assertIn('control characters', json.dumps(body))
        self.assertEqual(self.project()[0], 201)

    def test_response_source_id_type_confusion_is_400(self):
        pid = self.project()[1]['id']
        for bad in (['src_x'], 123, {'a': 1}, True):
            status, _ = call(self.port, f'/api/projects/{pid}/responses', 'POST',
                             {'responder': 'R', 'text': 'T', 'source_id': bad}, ADMIN)
            self.assertEqual(status, 400, bad)
        for ok in (None, ''):
            status, _ = call(self.port, f'/api/projects/{pid}/responses', 'POST',
                             {'responder': 'R', 'text': 'T', 'source_id': ok}, ADMIN)
            self.assertEqual(status, 201, ok)

    def test_claim_source_id_type_confusion_is_400(self):
        pid = self.project()[1]['id']
        status, _ = call(self.port, f'/api/projects/{pid}/claims', 'POST',
                         {'claim_type': 'fact', 'source_id': 123, 'text': 'T', 'passage': 'P'}, ADMIN)
        self.assertEqual(status, 400)

    def test_gap_status_closed_set(self):
        pid = self.project()[1]['id']
        base = {'document_name': 'Doc', 'search_scope': 'Scope', 'searched_at': '2026-09-25T00:00:00Z'}
        status, body = call(self.port, f'/api/projects/{pid}/gaps', 'POST', {**base, 'status': 'bogus_status'}, ADMIN)
        self.assertEqual(status, 400)
        for good in ('not_located', 'requested', 'received', 'not_held'):
            self.assertEqual(call(self.port, f'/api/projects/{pid}/gaps', 'POST', {**base, 'status': good}, ADMIN)[0], 201)
        self.assertEqual(call(self.port, f'/api/projects/{pid}/gaps', 'POST', base, ADMIN)[0], 201)

    def test_concurrent_hostile_writes_never_500(self):
        from concurrent.futures import ThreadPoolExecutor
        pid = self.project()[1]['id']
        payloads = []
        for i in range(24):
            payloads.append(('/api/projects', {'title': 'T', 'authority': 'A\u0000' + str(i), 'synthetic': True}))
            payloads.append((f'/api/projects/{pid}/responses', {'responder': 'R', 'text': 'T', 'source_id': [i]}))
            payloads.append((f'/api/projects/{pid}/gaps', {'document_name': 'D', 'search_scope': 'S', 'status': 'x' + str(i)}))
            payloads.append(('/api/projects', {'title': 'T' + str(i), 'authority': 'Clean', 'synthetic': True}))
        with ThreadPoolExecutor(32) as pool:
            codes = list(pool.map(lambda p: call(self.port, p[0], 'POST', p[1], ADMIN)[0], payloads))
        self.assertEqual(codes.count(400), 72, codes)
        self.assertEqual(codes.count(201), 24, codes)
        self.assertNotIn(500, codes)
        self.assertEqual(call(self.port, '/healthz')[0], 200)
        self.assertEqual(call(self.port, '/readyz')[0], 200)


if __name__ == '__main__':
    unittest.main()
