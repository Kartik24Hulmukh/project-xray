"""Launch-prep regression tests for the 16-17 September 2026 synthetic preview.

Covers: accessible CSV dossier export (formula-injection safe, labelled, public-state only),
the site-wide synthetic-preview banner, and the presence of LIMITATIONS / corrections SLA /
privacy notice documents that the release gate requires.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

TMP = tempfile.TemporaryDirectory()
PORT = 18093
os.environ.update(
    {
        'DB_PATH': TMP.name + '/test.db',
        'ADMIN_TOKEN': 'test-admin-secret-long-enough',
        'REVIEWER_TOKENS': 'reviewer-a:review-token-a,reviewer-b:review-token-b',
        'SCANNER_TOKENS': 'scanner-a:scan-token-a',
        'PORT': str(PORT),
        'APP_ENV': 'test',
        'TOKEN_PEPPER': 'test-token-pepper-0123456789abcdef',
        'AUDIT_HMAC_KEY': 'test-audit-key-0123456789abcdef',
        'BACKUP_HMAC_KEY': 'test-backup-key-0123456789abcdef',
        'WRITE_RATE_LIMIT': '1000',
        'PUBLIC_READ_RATE_LIMIT': '1000',
        'AUTH_READ_RATE_LIMIT': '1000',
        'EXPENSIVE_WRITE_RATE_LIMIT': '1000',
    }
)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import server  # noqa: E402

ADMIN = 'test-admin-secret-long-enough'


class TestCsvCell(unittest.TestCase):
    def test_formula_prefixes_are_neutralised(self):
        for bad in ('=HYPERLINK("x")', '+1+1', '-2', '@SUM(A1)', '\tcmd', '\rcmd'):
            self.assertTrue(server.csv_cell(bad).startswith("'"), bad)

    def test_plain_values_untouched(self):
        self.assertEqual(server.csv_cell('Synthetic claim'), 'Synthetic claim')
        self.assertEqual(server.csv_cell(None), '')
        self.assertEqual(server.csv_cell(12), '12')


class TestLaunchPrepHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['DB_PATH'] = TMP.name + '/test.db'
        server.DB = Path(os.environ['DB_PATH'])
        try:
            from app import database
            database.DB_PATH = server.DB
            database.DATABASE_URL = ''
            database.IS_POSTGRES = False
        except Exception:
            pass
        server.init()
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', PORT), server.H)
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        TMP.cleanup()

    def setUp(self):
        with server._RATE_LOCK:
            server.RATE.clear()
        server.TRUST_PROXY_HEADERS = False

    def req(self, path, method='GET', data=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request(
            f'http://127.0.0.1:{PORT}' + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                body = json.loads(raw) if 'json' in r.headers.get('Content-Type', '') else raw.decode()
                return r.status, body, dict(r.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read()), dict(exc.headers)

    def make_project(self, claim_text):
        status, project, _ = self.req('/api/projects', 'POST', {'title': 'Synthetic bridge', 'authority': 'Example Authority', 'synthetic': True}, ADMIN)
        self.assertEqual(status, 201)
        pid = project['id']
        status, source, _ = self.req(
            f'/api/projects/{pid}/sources', 'POST',
            {'publisher': 'Synthetic Publisher', 'url': 'https://example.invalid/source', 'source_class': 'official',
             'retrieved_at': '2026-07-14T00:00:00Z', 'sha256': 'a' * 64, 'passage': 'Synthetic passage'}, ADMIN)
        self.assertEqual(status, 201)
        status, claim, _ = self.req(
            f'/api/projects/{pid}/claims', 'POST',
            {'claim_type': 'official_claim', 'text': claim_text, 'source_id': source['id'], 'passage': 'Synthetic passage'}, ADMIN)
        self.assertEqual(status, 201, claim)
        return pid, claim['id']

    def add_claim(self, pid, text):
        _, dossier, _ = self.req(f'/api/projects/{pid}?include_private=1', token=ADMIN)
        sid = dossier['claims'][0]['source_id']
        status, claim, _ = self.req(
            f'/api/projects/{pid}/claims', 'POST',
            {'claim_type': 'official_claim', 'text': text, 'source_id': sid, 'passage': 'Synthetic passage'}, ADMIN)
        self.assertEqual(status, 201, claim)
        return claim['id']

    def approve(self, pid, cid):
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-a')
        self.req(f'/api/projects/{pid}/claims/{cid}/reviews', 'POST', {'decision': 'approve'}, 'review-token-b')
        self.assertEqual(self.req(f'/api/projects/{pid}/claims/{cid}/publish', 'POST', {}, ADMIN)[0], 200)
        self.req(f'/api/projects/{pid}/gaps', 'POST', {'document_name': '=CMD()|synthetic gap', 'search_scope': 'Synthetic fixture only'}, ADMIN)
        self.assertEqual(self.req(f'/api/projects/{pid}/publish', 'POST', {}, ADMIN)[0], 200)

    def test_csv_export_is_labelled_downloadable_and_safe(self):
        pid, cid = self.make_project('=HYPERLINK("https://evil.invalid","click")')
        self.approve(pid, cid)
        status, body, headers = self.req(f'/api/projects/{pid}/claims.csv')
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('text/csv'))
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        lines = body.split('\r\n')
        self.assertTrue(lines[0].startswith('record_kind,data_label,project_id'))
        self.assertIn('claim,SYNTHETIC,' + pid, body)
        # formula-injection neutralised, original text still recoverable by a human reader
        self.assertIn("'=HYPERLINK", body)
        self.assertNotIn(',=HYPERLINK', body)
        self.assertIn('a' * 64, body)
        self.assertIn("record_not_located,SYNTHETIC,", body)
        self.assertIn("\'=CMD()", body)

    def test_csv_export_hides_unpublished_claims_from_public(self):
        pid, cid = self.make_project('Published synthetic statement')
        self.approve(pid, cid)
        self.add_claim(pid, 'Unreviewed synthetic statement')
        status, public_body, _ = self.req(f'/api/projects/{pid}/claims.csv')
        self.assertEqual(status, 200)
        status, private_body, _ = self.req(f'/api/projects/{pid}/claims.csv?include_private=1', token=ADMIN)
        self.assertEqual(status, 200)
        self.assertIn('Unreviewed synthetic statement', private_body)
        # public export must not leak more claim rows than the public JSON dossier does
        _, dossier, _ = self.req(f'/api/projects/{pid}')
        public_rows = [l for l in public_body.split('\r\n') if l.startswith('claim,')]
        self.assertEqual(len(public_rows), len(dossier['claims']))
        self.assertEqual(('Unreviewed synthetic statement' in public_body), any(c['text'] == 'Unreviewed synthetic statement' for c in dossier['claims']))

    def test_csv_export_unknown_project_404(self):
        status, body, _ = self.req('/api/projects/prj_00000000000000000000000000/claims.csv')
        self.assertEqual(status, 404)

    def test_index_has_synthetic_preview_banner(self):
        status, body, headers = self.req('/')
        self.assertEqual(status, 200)
        self.assertIn('class="preview-banner"', body)
        self.assertIn('role="status"', body)
        self.assertIn('Controlled synthetic preview', body)
        for link in ('docs/LIMITATIONS.md', 'CORRECTIONS_AND_APPEALS.md', 'PRIVACY_NOTICE.md'):
            self.assertIn(link, body)
        status, js, _ = self.req('/app.js')
        self.assertIn('claims.csv', js)
        status, css, _ = self.req('/styles.css')
        self.assertIn('.preview-banner', css)


class TestLaunchPrepDocs(unittest.TestCase):
    def test_required_documents_and_statements(self):
        lim = (ROOT / 'docs/LIMITATIONS.md').read_text()
        self.assertIn('no SSL support', lim)
        self.assertIn('verify-full', lim)
        self.assertIn('CWE-1236', lim)
        pol = (ROOT / 'docs/legal/CORRECTIONS_AND_APPEALS.md').read_text()
        self.assertIn('2 business days', pol)
        self.assertIn('10 business days', pol)
        self.assertIn('Owner', pol)
        priv = (ROOT / 'docs/legal/PRIVACY_NOTICE.md').read_text()
        self.assertIn('PII scan', priv)
        self.assertIn('fails closed', priv)


if __name__ == '__main__':
    unittest.main()
