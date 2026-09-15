"""Tests for the release-claim guard (scripts/check_release_claims.py).

Regression: a tag named v2.1.1-production existed while the readiness ledger
was 0/10. The guard must fail in that situation and must fail closed when the
ledger cannot be read.
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'check_release_claims', ROOT / 'scripts' / 'check_release_claims.py')
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)

FULL_LEDGER = """version: 1
checks:
  - id: a.one
    status: passed
    evidence: artifacts/a.json
  - id: b.two
    status: passed
    evidence: artifacts/b.json
"""
OPEN_LEDGER = """version: 1
checks:
  - id: a.one
    status: passed
    evidence: artifacts/a.json
  - id: b.two
    status: pending
    evidence: null
"""


class TestClaimDetection(unittest.TestCase):
    def test_production_and_ga_names_are_claims(self):
        for name in ('v2.1.1-production', 'v1.0.0-GA', 'v3-stable',
                     'release-certified', 'v2-enterprise-ready'):
            self.assertTrue(guard.is_unsupported_claim(name), name)

    def test_honest_preview_names_are_not_claims(self):
        for name in ('v0.4.5-synthetic-preview', 'v0.9.0-rc1', 'v0.4.7',
                     'v1.0.0-beta.2', 'v2.1.1-production-preview'):
            self.assertFalse(guard.is_unsupported_claim(name), name)


class TestAudit(unittest.TestCase):
    def _ledger(self, text):
        path = Path(self.enterContext(__import__('tempfile').TemporaryDirectory())) / 'ledger.yaml'
        path.write_text(text, encoding='utf-8')
        guard.LEDGER = path
        return path

    def tearDown(self):
        guard.LEDGER = ROOT / 'ops' / 'production-readiness.yaml'

    def test_open_ledger_blocks_production_tag(self):
        self._ledger(OPEN_LEDGER)
        receipt = guard.audit(['v0.4.7', 'v2.1.1-production'])
        self.assertFalse(receipt['ok'])
        self.assertEqual(receipt['unsupported_claims'], ['v2.1.1-production'])
        self.assertFalse(receipt['production_certified'])
        self.assertIn('b.two', receipt['ledger_pending'])

    def test_open_ledger_allows_preview_tags(self):
        self._ledger(OPEN_LEDGER)
        self.assertTrue(guard.audit(['v0.9.0-rc1', 'v0.4.5-synthetic-preview'])['ok'])

    def test_closed_ledger_allows_production_tag(self):
        self._ledger(FULL_LEDGER)
        receipt = guard.audit(['v2.1.1-production'])
        self.assertTrue(receipt['ok'])
        self.assertTrue(receipt['production_certified'])

    def test_missing_ledger_fails_closed(self):
        guard.LEDGER = ROOT / 'ops' / 'does-not-exist.yaml'
        self.assertEqual(guard.main(['--name', 'v1.0.0']), 1)

    def test_empty_ledger_fails_closed(self):
        self._ledger('version: 1\\nchecks: []\\n')
        self.assertEqual(guard.main(['--name', 'v1.0.0-production']), 1)


class TestLiveRepository(unittest.TestCase):
    def test_repo_ledger_parses_and_is_not_silently_certified(self):
        passed, total, pending = guard.ledger_state()
        self.assertEqual(total, 10)
        self.assertEqual(len(pending), total - passed)
        if passed < total:
            self.assertTrue(pending)


if __name__ == '__main__':
    unittest.main()
