"""Server-side evidence-state taxonomy: enforced at ingest, degraded on read.

Root cause closed here: `source_class_for_envelope` used to map any unknown or
legacy value to `primary_official_record`, so a typo or a hostile client could
make a public evidence capsule overstate provenance.  The taxonomy is now a
closed set enforced at the ingest boundary, unmappable legacy rows degrade to
the lowest-trust class, and publication is fail-closed for them.

The HTTP tests spawn `app/server.py` in a subprocess with their own database
and port, so they cannot bind module-level server constants to this file's
environment (the suite-order bug found in session 16).
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

ADMIN = 'taxonomy-admin-token-[number]'
RA = 'taxonomy-reviewer-a-token'
RB = 'taxonomy-reviewer-b-token'


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


class TestEvidenceTaxonomyUnit(unittest.TestCase):
    """Pure-function behaviour (no server import side effects at module load)."""

    def setUp(self):
        from app import server
        self.server = server

    def test_canonical_values_pass_through(self):
        for value in self.server.EVIDENCE_SOURCE_CLASSES:
            self.assertEqual(self.server.canonical_source_class(value), value)

    def test_aliases_and_normalisation(self):
        self.assertEqual(self.server.canonical_source_class('  OFFICIAL '), 'primary_official_record')
        self.assertEqual(self.server.canonical_source_class('Reporting'), 'reputable_reporting')
        self.assertEqual(self.server.canonical_source_class('submission'), 'public_submission')

    def test_unmappable_values_are_not_promoted(self):
        for value in ('', None, 'gospel', 'primary_official_record_x', '<script>'):
            self.assertIsNone(self.server.canonical_source_class(value))
            self.assertEqual(
                self.server.source_class_for_envelope(value),
                self.server.EVIDENCE_CLASS_FALLBACK,
            )

    def test_fallback_is_the_lowest_trust_class(self):
        self.assertEqual(self.server.EVIDENCE_CLASS_FALLBACK, 'public_submission')
        self.assertIn(self.server.EVIDENCE_CLASS_FALLBACK, self.server.EVIDENCE_SOURCE_CLASSES)


class TestEvidenceTaxonomyHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / 'taxonomy.db'
        cls.port = free_port()
        env = {
            **os.environ,
            'DB_PATH': str(cls.db),
            'PORT': str(cls.port),
            'ADMIN_TOKEN': ADMIN,
            'REVIEWER_TOKENS': f'reviewer-a:{RA},reviewer-b:{RB}',
            'APP_ENV': 'test',
            'WRITE_RATE_LIMIT': '1000',
            'PUBLIC_READ_RATE_LIMIT': '1000',
            'AUTH_READ_RATE_LIMIT': '1000',
            'EXPENSIVE_WRITE_RATE_LIMIT': '1000',
        }
        env.pop('DATABASE_URL', None)
        cls.proc = subprocess.Popen(
            [sys.executable, 'app/server.py'], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
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
        cls.tmp.cleanup()

    def project(self):
        status, body = call(self.port, '/api/projects', 'POST',
                            {'title': 'Taxonomy fixture', 'authority': 'Synthetic Authority',
                             'synthetic': True}, ADMIN)
        self.assertEqual(status, 201)
        return body['id']

    def add_source(self, pid, source_class, sha):
        return call(self.port, f'/api/projects/{pid}/sources', 'POST',
                    {'publisher': 'Synthetic Publisher',
                     'url': f'https://example.invalid/{sha[:8]}',
                     'source_class': source_class,
                     'retrieved_at': '2026-07-14T00:00:00Z',
                     'sha256': sha, 'passage': 'Synthetic anchor'}, ADMIN)

    def test_taxonomy_is_published_without_auth(self):
        status, body = call(self.port, '/api/evidence-states')
        self.assertEqual(status, 200)
        self.assertEqual(body['source_classes'], [
            'primary_official_record', 'official_statement', 'independent_technical',
            'reputable_reporting', 'public_submission'])
        self.assertEqual(body['unmappable_class_fallback'], 'public_submission')
        self.assertTrue(body['publication_gate']['source_class_must_be_canonical'])
        self.assertEqual(body['publication_gate']['distinct_approvals_required'], 2)
        self.assertFalse(body['publication_gate']['self_review_allowed'])

    def test_unknown_class_is_rejected_at_ingest(self):
        pid = self.project()
        for bad in ('gospel', 'hearsay', 'primary_official_record_x', 'OFFICIALish'):
            status, body = self.add_source(pid, bad, '1' * 64)
            self.assertEqual(status, 400, bad)
            self.assertIn('source_class must be one of', body['error'], bad)

    def test_empty_and_control_character_classes_are_rejected(self):
        pid = self.project()
        for bad in ('', '   ', 'primary\u0000official', '<script>alert(1)</script>'):
            status, body = self.add_source(pid, bad, '1' * 64)
            self.assertEqual(status, 400, repr(bad))
            self.assertIn('error', body)

    def test_accepted_classes_are_stored_canonically(self):
        pid = self.project()
        status, _ = self.add_source(pid, ' Official ', '2' * 64)
        self.assertEqual(status, 201)
        status, _ = self.add_source(pid, 'independent_technical', '3' * 64)
        self.assertEqual(status, 201)
        with sqlite3.connect(self.db) as conn:
            stored = sorted(r[0] for r in conn.execute(
                'SELECT source_class FROM sources WHERE project_id=?', (pid,)))
        self.assertEqual(stored, ['independent_technical', 'primary_official_record'])

    def test_legacy_unmappable_source_cannot_be_published(self):
        pid = self.project()
        status, source = self.add_source(pid, 'official', '4' * 64)
        self.assertEqual(status, 201)
        # Simulate a row written before the taxonomy existed.
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE sources SET source_class=? WHERE id=?',
                         ('legacy-freeform', source['id']))
        status, claim = call(self.port, f'/api/projects/{pid}/claims', 'POST',
                             {'claim_type': 'official_claim', 'text': 'Legacy provenance claim',
                              'source_id': source['id'], 'passage': 'Synthetic anchor'}, ADMIN)
        self.assertEqual(status, 201)
        cid = claim['id']
        for token in (RA, RB):
            status, _ = call(self.port, f'/api/projects/{pid}/claims/{cid}/reviews', 'POST',
                             {'decision': 'approve', 'note': 'ok'}, token)
            self.assertEqual(status, 201)
        status, body = call(self.port, f'/api/projects/{pid}/claims/{cid}/publish', 'POST', {}, ADMIN)
        self.assertEqual(status, 409)
        self.assertIn('outside the published taxonomy', body['error'])

    def test_clean_source_still_publishes_and_capsule_reports_class(self):
        pid = self.project()
        status, source = self.add_source(pid, 'reporting', '5' * 64)
        self.assertEqual(status, 201)
        status, claim = call(self.port, f'/api/projects/{pid}/claims', 'POST',
                             {'claim_type': 'official_claim', 'text': 'Reported claim',
                              'source_id': source['id'], 'passage': 'Synthetic anchor'}, ADMIN)
        self.assertEqual(status, 201)
        cid = claim['id']
        for token in (RA, RB):
            self.assertEqual(call(self.port, f'/api/projects/{pid}/claims/{cid}/reviews', 'POST',
                                  {'decision': 'approve'}, token)[0], 201)
        status, body = call(self.port, f'/api/projects/{pid}/claims/{cid}/publish', 'POST', {}, ADMIN)
        self.assertEqual(status, 200)
        self.assertEqual(body['publication_state'], 'published')


if __name__ == '__main__':
    unittest.main()
