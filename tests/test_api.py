import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

TMP = tempfile.TemporaryDirectory()
os.environ.update(
    {
        'DB_PATH': TMP.name + '/test.db',
        'ADMIN_TOKEN': 'test-admin-secret-long-enough',
        'REVIEWER_TOKENS': 'reviewer-a:review-token-a,reviewer-b:review-token-b',
        'SCANNER_TOKENS': 'scanner-a:scan-token-a',
        'PORT': '18081',
        'APP_ENV': 'test',
        'TOKEN_PEPPER': 'test-token-pepper-12345678901234567890',
        'AUDIT_HMAC_KEY': 'test-audit-key-123456789012345678901',
        'BACKUP_HMAC_KEY': 'test-backup-key-12345678901234567890',
        'WRITE_RATE_LIMIT': '1000',
        'PUBLIC_READ_RATE_LIMIT': '1000',
        'AUTH_READ_RATE_LIMIT': '1000',
        'EXPENSIVE_WRITE_RATE_LIMIT': '1000',
    }
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import server


class TestCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Re-assert isolation if other suites mutated process env/module state.
        os.environ['DB_PATH'] = TMP.name + '/test.db'
        server.DB = Path(os.environ['DB_PATH'])
        try:
            from app import database as database
            database.DB_PATH = server.DB
            database.DATABASE_URL = ''
            database.IS_POSTGRES = False
        except Exception:
            pass
        server.init()
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 18081), server.H)
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
        server.TRUST_PROXY_HEADERS = False
        server.PUBLIC_READ_RATE_LIMIT = 1000
        server.AUTH_READ_RATE_LIMIT = 1000
        server.WRITE_RATE_LIMIT = 1000
        server.EXPENSIVE_WRITE_RATE_LIMIT = 1000

    def req(self, path, method='GET', data=None, token=None, ctype='application/json', headers=None):
        request_headers = {'Content-Type': ctype, **(headers or {})}
        if token:
            request_headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(
            'http://127.0.0.1:18081' + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req) as response:
                body = (
                    json.loads(response.read())
                    if 'json' in response.headers.get('Content-Type', '')
                    else response.read().decode()
                )
                return response.status, body, dict(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read()), dict(exc.headers)

    def create_project_source_claim(self):
        status, project, _ = self.req(
            '/api/projects',
            'POST',
            {'title': 'Synthetic bridge', 'authority': 'Example Authority', 'synthetic': True},
            'test-admin-secret-long-enough',
        )
        self.assertEqual(status, 201)
        pid = project['id']
        status, source, _ = self.req(
            f'/api/projects/{pid}/sources',
            'POST',
            {
                'publisher': 'Synthetic Publisher',
                'url': 'https://example.invalid/source',
                'source_class': 'official',
                'retrieved_at': '2026-07-14T00:00:00Z',
                'sha256': 'a' * 64,
                'passage': 'Synthetic passage',
            },
            'test-admin-secret-long-enough',
        )
        self.assertEqual(status, 201)
        status, claim, _ = self.req(
            f'/api/projects/{pid}/claims',
            'POST',
            {
                'claim_type': 'official_claim',
                'text': 'Synthetic claim',
                'source_id': source['id'],
                'passage': 'Synthetic passage',
            },
            'test-admin-secret-long-enough',
        )
        self.assertEqual(status, 201)
        return pid, source['id'], claim['id']

    def publish_claim(self, pid, cid):
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-a')
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-b')
        return self.req(f'/api/projects/{pid}/claims/{cid}/publish', 'POST', {}, 'test-admin-secret-long-enough')

    def test_creation_cannot_bypass_project_publication_gate(self):
        status, body, _ = self.req('/api/projects', 'POST',
            {'title': 'SYNTHETIC bypass', 'synthetic': True, 'status': 'published'},
            'test-admin-secret-long-enough')
        self.assertEqual(status, 400)

    def test_review_cannot_silently_unpublish_a_public_claim(self):
        pid, _, cid = self.create_project_source_claim()
        self.assertEqual(self.publish_claim(pid, cid)[0], 200)
        status, token, _ = self.req('/api/auth/tokens', 'POST',
            {'principal': 'reviewer-c', 'role': 'reviewer'}, 'test-admin-secret-long-enough')
        self.assertEqual(status, 201)
        status, _, _ = self.req(f'/api/projects/{pid}/claims/{cid}/reviews',
            'POST', {'decision': 'reject'}, token['token'])
        self.assertEqual(status, 409)
        with server.db() as c:
            claim = c.execute('SELECT publication_state FROM claims WHERE id=?', (cid,)).fetchone()
        self.assertEqual(claim['publication_state'], 'published')

    def test_cross_project_source_cannot_be_used_for_claim(self):
        _, source_id, _ = self.create_project_source_claim()
        other, _, _ = self.create_project_source_claim()
        status, _, _ = self.req(f'/api/projects/{other}/claims', 'POST',
            {'claim_type': 'official_claim', 'text': 'SYNTHETIC cross-project',
             'source_id': source_id, 'passage': 'SYNTHETIC anchor'},
            'test-admin-secret-long-enough')
        self.assertEqual(status, 400)

    def test_cross_project_sources_rejected_for_documents_and_responses(self):
        _, sid, _ = self.create_project_source_claim()
        pid, _, _ = self.create_project_source_claim()
        for kind, body in (
            ('documents', {'filename': 'synthetic.pdf', 'media_type': 'application/pdf',
                           'size_bytes': 10, 'sha256': 'b' * 64}),
            ('responses', {'responder': 'Synthetic authority', 'text': 'Synthetic response'}),
        ):
            with self.subTest(kind=kind):
                status, _, _ = self.req(f'/api/projects/{pid}/{kind}', 'POST',
                    {**body, 'source_id': sid}, 'test-admin-secret-long-enough')
                self.assertEqual(status, 400)

    def test_rejection_blocks_publication_despite_two_approvals(self):
        pid, _, cid = self.create_project_source_claim()
        status, token, _ = self.req('/api/auth/tokens', 'POST',
            {'principal': 'reviewer-d', 'role': 'reviewer'}, 'test-admin-secret-long-enough')
        self.assertEqual(status, 201)
        self.assertEqual(self.req(f'/api/projects/{pid}/claims/{cid}/reviews',
            'POST', {'decision': 'reject'}, token['token'])[0], 201)
        self.assertEqual(self.publish_claim(pid, cid)[0], 409)
        with server.db() as c:
            claim = c.execute('SELECT publication_state FROM claims WHERE id=?', (cid,)).fetchone()
        self.assertEqual(claim['publication_state'], 'candidate')

    def test_complete_publication_path(self):
        pid, src, cid = self.create_project_source_claim()
        self.assertEqual(
            self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-a')[1][
                'approvals'
            ],
            1,
        )
        reviewed = self.req(
            f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-b'
        )[1]
        self.assertEqual(reviewed['publication_state'], 'reviewed')
        self.assertEqual(self.req(f'/api/projects/{pid}/claims/{cid}/publish', 'POST', {}, 'test-admin-secret-long-enough')[0], 200)
        self.req(
            f'/api/projects/{pid}/gaps',
            'POST',
            {'document_name': 'Synthetic test report', 'search_scope': 'Synthetic fixture only'},
            'test-admin-secret-long-enough',
        )
        self.req(
            f'/api/projects/{pid}/responses',
            'POST',
            {'responder': 'Example Authority', 'text': 'Synthetic response', 'source_id': src},
            'test-admin-secret-long-enough',
        )
        self.assertEqual(self.req(f'/api/projects/{pid}/publish', 'POST', {}, 'test-admin-secret-long-enough')[0], 200)
        status, body, _ = self.req('/api/projects/' + pid)
        self.assertEqual(status, 200)
        self.assertEqual(len(body['claims']), 1)
        self.assertEqual(len(body['gaps']), 1)
        self.assertIn('SHA-256', self.req('/api/projects/' + pid + '/report')[1])
        self.assertIn('not legal advice', self.req('/api/projects/' + pid + '/rti')[1])
        events = self.req('/api/projects/' + pid + '/audit', token='test-admin-secret-long-enough')[1]['events']
        self.assertGreaterEqual(len(events), 6)

    def test_public_api_hides_research_and_candidate(self):
        pid, _, _ = self.create_project_source_claim()
        self.assertEqual(self.req('/api/projects/' + pid)[0], 404)
        self.assertFalse(any(x['id'] == pid for x in self.req('/api/projects')[1]['projects']))
        self.assertEqual(self.req('/api/projects/' + pid + '?include_private=1', token='review-token-a')[0], 200)

    def test_capsule_export_and_verifier(self):
        pid, src, cid = self.create_project_source_claim()
        doc = {'source_id': src, 'filename': 'capsule.pdf', 'media_type': 'application/pdf', 'size_bytes': 128, 'sha256': 'f' * 64}
        did = self.req(f'/api/projects/{pid}/documents', 'POST', doc, 'test-admin-secret-long-enough')[1]['id']
        self.req(f'/api/projects/{pid}/documents/{did}/scan', 'POST', {'result': 'clean'}, 'scan-token-a')
        self.publish_claim(pid, cid)
        self.req(f'/api/projects/{pid}/publish', 'POST', {}, 'test-admin-secret-long-enough')
        status, capsule, _ = self.req(f'/api/projects/{pid}/capsule')
        self.assertEqual(status, 200)
        self.assertEqual(capsule['kind'], 'project_xray_public_dossier_capsule')
        self.assertEqual(len(capsule['claims']), 1)
        self.assertEqual(len(capsule['evidence_envelopes']), 1)
        with tempfile.NamedTemporaryFile('w+', suffix='.json', delete=False) as handle:
            json.dump(capsule, handle)
            path = handle.name
        try:
            verified = subprocess.run([sys.executable, 'scripts/verify_capsule.py', path], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
            payload = json.loads(verified.stdout)
            self.assertEqual(payload['status'], 'ok')
            self.assertEqual(payload['claims'], 1)
        finally:
            os.unlink(path)

    def test_public_projection_allowlist_blocks_private_fields(self):
        pid, src, cid = self.create_project_source_claim()
        doc = {
            'source_id': src,
            'filename': 'public-fixture.pdf',
            'media_type': 'application/pdf',
            'size_bytes': 128,
            'sha256': 'e' * 64,
        }
        did = self.req(f'/api/projects/{pid}/documents', 'POST', doc, 'test-admin-secret-long-enough')[1]['id']
        self.req(f'/api/projects/{pid}/documents/{did}/scan', 'POST', {'result': 'clean'}, 'scan-token-a')
        self.publish_claim(pid, cid)
        self.req(
            f'/api/projects/{pid}/gaps',
            'POST',
            {'document_name': 'Tender minutes', 'search_scope': 'Portal, RTI ledger'},
            'test-admin-secret-long-enough',
        )
        self.req(
            f'/api/projects/{pid}/responses',
            'POST',
            {'responder': 'Authority', 'text': 'We deny the allegation', 'source_id': src},
            'test-admin-secret-long-enough',
        )
        self.req(f'/api/projects/{pid}/publish', 'POST', {}, 'test-admin-secret-long-enough')

        status, public_bundle, _ = self.req(f'/api/projects/{pid}')
        self.assertEqual(status, 200)
        self.assertNotIn('sources', public_bundle)
        self.assertNotIn('documents', public_bundle)
        self.assertEqual(
            set(public_bundle['project'].keys()),
            {'id', 'title', 'authority', 'location', 'summary', 'status', 'synthetic', 'updated_at'},
        )
        claim = public_bundle['claims'][0]
        for forbidden in ('project_id', 'source_id', 'created_by', 'version', 'created_at', 'updated_at'):
            self.assertNotIn(forbidden, claim)
        response = public_bundle['responses'][0]
        self.assertNotIn('project_id', response)
        self.assertNotIn('source_id', response)

        private_bundle = self.req(f'/api/projects/{pid}?include_private=1', token='review-token-a')[1]
        self.assertIn('sources', private_bundle)
        self.assertIn('documents', private_bundle)
        self.assertEqual(private_bundle['documents'][0]['id'], did)

    def test_two_person_gate_and_role_separation(self):
        pid, _, cid = self.create_project_source_claim()
        self.assertEqual(self.req(f'/api/projects/{pid}/claims/{cid}/publish', 'POST', {}, 'test-admin-secret-long-enough')[0], 409)
        self.assertEqual(self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'test-admin-secret-long-enough')[0], 403)
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-a')
        self.assertEqual(self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-a')[0], 409)

    def test_validation_security_headers_and_auth(self):
        self.assertEqual(self.req('/api/projects', 'POST', {'title': 'x'})[0], 401)
        self.assertEqual(self.req('/api/projects', 'POST', {'title': 'x'}, 'test-admin-secret-long-enough', 'text/plain')[0], 400)
        status, _, headers = self.req('/health')
        self.assertEqual(status, 200)
        self.assertEqual(headers['X-Frame-Options'], 'DENY')
        self.assertTrue(headers['X-Request-ID'].startswith('req_'))

    def test_audit_immutability_and_chain(self):
        pid, _, _ = self.create_project_source_claim()
        c = sqlite3.connect(server.DB)
        row = c.execute('SELECT previous_hash,event_hash FROM audit_events ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual(len(row[1]), 64)
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute('DELETE FROM audit_events')
        c.close()

    def test_document_quarantine_and_deduplication(self):
        pid, src, _ = self.create_project_source_claim()
        doc = {'source_id': src, 'filename': 'fixture.pdf', 'media_type': 'application/pdf', 'size_bytes': 128, 'sha256': 'b' * 64}
        status, body, _ = self.req(f'/api/projects/{pid}/documents', 'POST', doc, 'test-admin-secret-long-enough')
        self.assertEqual(status, 201)
        self.assertEqual(body['storage_state'], 'quarantined')
        self.assertEqual(self.req(f'/api/projects/{pid}/documents', 'POST', doc, 'test-admin-secret-long-enough')[0], 409)

    def test_correction_requires_fresh_two_person_review(self):
        pid, _, cid = self.create_project_source_claim()
        self.publish_claim(pid, cid)
        corrected = self.req(
            f'/api/projects/{pid}/claims/{cid}/correct',
            'POST',
            {'text': 'Corrected synthetic claim', 'reason': 'Synthetic correction fixture'},
            'test-admin-secret-long-enough',
        )[1]
        self.assertEqual(corrected['publication_state'], 'candidate')
        self.assertEqual(self.req(f'/api/projects/{pid}/claims/{cid}/publish', 'POST', {}, 'test-admin-secret-long-enough')[0], 409)
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-a')
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-b')
        self.assertEqual(
            self.req(f'/api/projects/{pid}/claims/{cid}/publish', 'POST', {}, 'test-admin-secret-long-enough')[1]['publication_state'],
            'corrected',
        )

    def test_quarantine_fails_closed_until_scanner_clears(self):
        pid, src, cid = self.create_project_source_claim()
        doc = {'source_id': src, 'filename': 'evidence.pdf', 'media_type': 'application/pdf', 'size_bytes': 128, 'sha256': 'd' * 64}
        did = self.req(f'/api/projects/{pid}/documents', 'POST', doc, 'test-admin-secret-long-enough')[1]['id']
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-a')
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-b')
        self.assertEqual(self.req(f'/api/projects/{pid}/claims/{cid}/publish', 'POST', {}, 'test-admin-secret-long-enough')[0], 409)
        self.assertEqual(self.req(f'/api/projects/{pid}/documents/{did}/scan', 'POST', {'result': 'clean'}, 'scan-token-a')[0], 200)
        self.assertEqual(self.req(f'/api/projects/{pid}/claims/{cid}/publish', 'POST', {}, 'test-admin-secret-long-enough')[0], 200)

    def test_idempotency_replay_and_conflict(self):
        headers = {'Idempotency-Key': 'create-project-once'}
        body = {'title': 'Idempotent fixture', 'synthetic': True}
        first = self.req('/api/projects', 'POST', body, 'test-admin-secret-long-enough', headers=headers)
        second = self.req('/api/projects', 'POST', body, 'test-admin-secret-long-enough', headers=headers)
        self.assertEqual(first[0], 201)
        self.assertEqual(second[0], 201)
        self.assertEqual(first[1]['id'], second[1]['id'])
        self.assertEqual(self.req('/api/projects', 'POST', {'title': 'Different'}, 'test-admin-secret-long-enough', headers=headers)[0], 409)

    def test_token_expiry_revocation_and_rotation(self):
        created = self.req(
            '/api/auth/tokens',
            'POST',
            {'principal': 'temporary-reviewer', 'role': 'reviewer', 'ttl_seconds': 600},
            'test-admin-secret-long-enough',
        )[1]
        old_token, old_id = created['token'], created['id']
        self.assertEqual(server.auth({'Authorization': 'Bearer ' + old_token}), ('reviewer', 'temporary-reviewer'))
        rotated = self.req(
            '/api/auth/tokens',
            'POST',
            {'principal': 'temporary-reviewer', 'role': 'reviewer', 'ttl_seconds': 600, 'rotated_from': old_id},
            'test-admin-secret-long-enough',
        )[1]
        self.assertEqual(server.auth({'Authorization': 'Bearer ' + old_token}), (None, None))
        self.assertEqual(server.auth({'Authorization': 'Bearer ' + rotated['token']}), ('reviewer', 'temporary-reviewer'))
        with server.db(True) as c:
            c.execute("UPDATE auth_tokens SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rotated['id'],))
        self.assertEqual(server.auth({'Authorization': 'Bearer ' + rotated['token']}), (None, None))

    def test_concurrent_publication_is_single_transition(self):
        pid, _, cid = self.create_project_source_claim()
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-a')
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-b')
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(
                    lambda _: self.req(f'/api/projects/{pid}/claims/{cid}/publish', 'POST', {}, 'test-admin-secret-long-enough')[0],
                    range(8),
                )
            )
        self.assertTrue(all(x == 200 for x in results))
        with server.db() as c:
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) n FROM audit_events WHERE action='publish' AND object_type='claim' AND object_id=?",
                    (cid,),
                ).fetchone()['n'],
                1,
            )

    def test_concurrent_correction_allows_one_version_advance(self):
        pid, _, cid = self.create_project_source_claim()
        self.publish_claim(pid, cid)
        bodies = [{'text': f'Correction {i}', 'reason': 'concurrency fixture'} for i in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            codes = list(
                pool.map(
                    lambda body: self.req(f'/api/projects/{pid}/claims/{cid}/correct', 'POST', body, 'test-admin-secret-long-enough')[0],
                    bodies,
                )
            )
        self.assertEqual(codes.count(200), 1)
        self.assertEqual(codes.count(409), 5)
        with server.db() as c:
            self.assertEqual(c.execute('SELECT version FROM claims WHERE id=?', (cid,)).fetchone()['version'], 2)

    def test_external_audit_checkpoint_detects_rehashed_fork(self):
        self.create_project_source_claim()
        copy = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        copy.close()
        with server.db() as src, sqlite3.connect(copy.name) as dst:
            src.backup(dst)
        c = sqlite3.connect(copy.name)
        c.execute('DROP TRIGGER checkpoint_no_update')
        c.execute("UPDATE audit_checkpoints SET signature=? WHERE id=(SELECT max(id) FROM audit_checkpoints)", ('0' * 64,))
        c.commit()
        c.close()
        from app.audit import verify

        bad = server.connect_db(copy.name)
        with self.assertRaises(RuntimeError):
            verify(bad, server.AUDIT_KEY)
        bad.close()
        os.unlink(copy.name)

    def test_oidc_proxy_requires_mfa_freshness_and_signature(self):
        from app.security import proxy_signature, verify_proxy

        stamp = str(int(time.time()))
        secret = 'proxy-secret-1234567890123456789012'
        headers = {
            'X-Auth-Subject': 'reviewer@example.org',
            'X-Auth-Roles': 'reviewer',
            'X-Auth-MFA': 'true',
            'X-Auth-Timestamp': stamp,
        }
        headers['X-Auth-Signature'] = proxy_signature(
            headers['X-Auth-Subject'], headers['X-Auth-Roles'], headers['X-Auth-MFA'], stamp, secret
        )
        self.assertEqual(verify_proxy(headers, secret), ('reviewer', 'reviewer@example.org'))
        headers['X-Auth-MFA'] = 'false'
        self.assertIsNone(verify_proxy(headers, secret))

    def test_proxy_aware_public_rate_limit(self):
        server.RATE.clear()
        server.TRUST_PROXY_HEADERS = True
        server.PUBLIC_READ_RATE_LIMIT = 1
        self.assertEqual(self.req('/api/projects', headers={'X-Forwarded-For': '203.0.113.10'})[0], 200)
        self.assertEqual(self.req('/api/projects', headers={'X-Forwarded-For': '203.0.113.10'})[0], 429)
        self.assertEqual(self.req('/api/projects', headers={'X-Forwarded-For': '203.0.113.11'})[0], 200)

    def test_health_and_ready(self):
        self.assertEqual(self.req('/health')[0], 200)
        self.assertEqual(self.req('/ready')[0], 200)

    def test_read_only_mode_blocks_writes_but_keeps_public_reads(self):
        os.environ['READ_ONLY_MODE'] = 'true'
        status, body, headers = self.req(
            '/api/projects',
            'POST',
            {'title': 'Must not be created', 'synthetic': True},
            'test-admin-secret-long-enough',
        )
        self.assertEqual(status, 503)
        self.assertEqual(body['code'], 'capability_temporarily_unavailable')
        self.assertEqual(headers.get('Retry-After'), '60')
        self.assertEqual(self.req('/api/projects')[0], 200)
        ready_status, ready, _ = self.req('/ready')
        self.assertEqual(ready_status, 200)
        self.assertEqual(ready['status'], 'degraded')
        self.assertFalse(ready['capabilities']['writes'])

    def test_disable_publication_does_not_block_candidate_work(self):
        pid, _, cid = self.create_project_source_claim()
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-a')
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-b')
        os.environ['DISABLE_PUBLICATION'] = 'true'
        self.assertEqual(
            self.req(
                f'/api/projects/{pid}/claims/{cid}/publish',
                'POST',
                {},
                'test-admin-secret-long-enough',
            )[0],
            503,
        )

    def test_disable_uploads_blocks_document_registration_only(self):
        pid, source_id, _ = self.create_project_source_claim()
        os.environ['DISABLE_UPLOADS'] = 'true'
        status, _, _ = self.req(
            f'/api/projects/{pid}/documents',
            'POST',
            {
                'source_id': source_id,
                'filename': 'synthetic.pdf',
                'media_type': 'application/pdf',
                'size_bytes': 1,
                'sha256': 'b' * 64,
            },
            'test-admin-secret-long-enough',
        )
        self.assertEqual(status, 503)
        status, _, _ = self.req(
            f'/api/projects/{pid}/gaps',
            'POST',
            {'document_name': 'Synthetic record', 'search_scope': 'Synthetic source set'},
            'test-admin-secret-long-enough',
        )
        self.assertEqual(status, 201)

    def test_disable_public_reads_allows_authenticated_private_read(self):
        os.environ['DISABLE_PUBLIC_READS'] = 'true'
        self.assertEqual(self.req('/api/projects')[0], 503)
        self.assertEqual(
            self.req('/api/projects?include_private=1', token='test-admin-secret-long-enough')[0],
            200,
        )

    def test_maintenance_keeps_health_live_and_readiness_not_ready(self):
        os.environ['MAINTENANCE_MODE'] = 'true'
        self.assertEqual(self.req('/health')[0], 200)
        ready_status, ready, _ = self.req('/ready')
        self.assertEqual(ready_status, 503)
        self.assertFalse(ready['ready'])
        self.assertEqual(self.req('/api/projects')[0], 503)


if __name__ == '__main__':
    unittest.main()
