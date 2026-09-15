import sqlite3
import unittest
from datetime import datetime, timezone, timedelta
from app.idempotency_reconcile import reconcile

NOW = datetime(2026, 9, 16, 9, tzinfo=timezone.utc)


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.execute("CREATE TABLE idempotency_keys(principal TEXT,key TEXT,state TEXT,created_at TEXT,completed_at TEXT,request_hash TEXT,PRIMARY KEY(principal,key))")
        self.addCleanup(self.c.close)

    def add(self, key, age=301, state='processing', completed_age=0, stamp=None):
        self.c.execute('INSERT INTO idempotency_keys VALUES(?,?,?,?,?,?)',
                       ('p', key, state, stamp or (NOW-timedelta(seconds=age)).isoformat(),
                        (NOW-timedelta(seconds=completed_age)).isoformat() if state == 'completed' else '', 'hash'))

    def run_sweep(self, **kw):
        return reconcile(self.c, reference=NOW, **kw)

    def test_dry_run_default_does_not_delete(self):
        self.add('old')
        self.assertEqual(self.run_sweep()['eligible'], 1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) AS n FROM idempotency_keys').fetchone()['n'], 1)

    def test_only_old_processing_deleted(self):
        for key, age in [('old', 301), ('live', 299), ('boundary', 300), ('future', -1)]:
            self.add(key, age)
        self.assertEqual(self.run_sweep(dry_run=False)['reservations_reconciled'], 1)
        self.assertEqual(self.c.execute('SELECT COUNT(*) AS n FROM idempotency_keys').fetchone()['n'], 3)

    def test_completed_default_kept_forever(self):
        self.add('receipt', 999999, 'completed', 999999)
        self.assertEqual(self.run_sweep(dry_run=False)['eligible'], 0)

    def test_receipt_age_uses_completion_not_creation(self):
        self.add('fresh', 999999, 'completed', 1)
        self.add('expired', 999999, 'completed', 3601)
        self.assertEqual(self.run_sweep(dry_run=False, retention_seconds=3600)['receipts_expired'], 1)
        self.assertEqual(self.c.execute('SELECT key FROM idempotency_keys').fetchone()['key'], 'fresh')

    def test_invalid_and_naive_timestamps_survive(self):
        for i, stamp in enumerate(['garbage', '2020-01-01T00:00:00']):
            self.add(str(i), stamp=stamp)
        self.assertEqual(self.run_sweep(dry_run=False)['skipped_unparsable'], 2)

    def test_cursor_does_not_starve_behind_live_or_malformed_rows(self):
        self.add('a', stamp='garbage'); self.add('b', age=0); self.add('c')
        first = self.run_sweep(batch=2, dry_run=False)
        self.assertEqual(first['inspected'], 2)
        second = self.run_sweep(batch=2, after=first['next_cursor'], dry_run=False)
        self.assertEqual(second['reservations_reconciled'], 1)
        self.assertIsNone(second['next_cursor'])

    def test_strict_boundaries(self):
        for kw in [dict(batch=0), dict(batch=5001), dict(batch=True), dict(batch=2.5),
                   dict(stuck_seconds=29), dict(retention_seconds=3599), dict(after=['x']),
                   dict(dry_run='false'), dict(reference=datetime(2026, 9, 16))]:
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                reconcile(self.c, **kw)

    def test_rotated_snapshot_is_fenced(self):
        self.add('old')
        real = self.c
        class Rotating:
            def execute(self, query, params):
                if query.startswith('DELETE'):
                    real.execute("UPDATE idempotency_keys SET created_at=?", (NOW.isoformat(),))
                return real.execute(query, params)
        result = reconcile(Rotating(), reference=NOW, dry_run=False)
        self.assertEqual(result['fence_lost'], 1)
        self.assertEqual(real.execute('SELECT COUNT(*) AS n FROM idempotency_keys').fetchone()['n'], 1)

    def test_caller_rollback_restores_deletion(self):
        self.add('old'); self.c.commit()
        self.run_sweep(dry_run=False); self.c.rollback()
        self.assertEqual(self.c.execute('SELECT COUNT(*) AS n FROM idempotency_keys').fetchone()['n'], 1)

    def test_fixed_clock_reproducible(self):
        self.add('old')
        self.assertEqual(self.run_sweep(), self.run_sweep())
