import os
import sqlite3
import tempfile
import threading
import time
import unittest

os.environ.setdefault('AUDIT_HMAC_KEY', 'test-audit-key')

from app import audit
from app import server


def build_ledger(path, events, key):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript('''CREATE TABLE audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, actor TEXT, action TEXT, object_type TEXT, object_id TEXT, detail TEXT, previous_hash TEXT, event_hash TEXT, created_at TEXT);
    CREATE TABLE audit_checkpoints(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, event_count INTEGER, head_hash TEXT, signature TEXT, created_at TEXT);
    CREATE INDEX idx_cp_event ON audit_checkpoints(event_id);''')
    for i in range(events):
        audit.append(c, f'evt_{i:08d}', 'tester', 'create', 'claim', f'clm_{i}', '', '2026-09-15T00:00:00+00:00', key)
    c.commit()
    return c


class StreamingVerificationTest(unittest.TestCase):
    key = 'test-audit-key'

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'ledger.db')
        self.c = build_ledger(self.path, 500, self.key)
        server.AUDIT_KEY = self.key
        server.reset_readiness_verifier()
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(self.c.close)

    def test_segments_match_full_walk(self):
        full = audit.verify(self.c, self.key)
        cursor = {'last_id': 0, 'previous': '', 'count': 0}
        scanned = 0
        while True:
            seg = audit.verify_segment(self.c, self.key, cursor['last_id'], cursor['previous'], 64, cursor['count'])
            scanned += seg['scanned']
            cursor = {'last_id': seg['last_id'], 'previous': seg['previous'], 'count': seg['count']}
            if seg['complete']:
                break
        audit.verify_orphans(self.c, cursor['count'])
        self.assertEqual(scanned, full['events'])
        self.assertEqual(cursor['previous'], full['head'])

    def test_segment_detects_tamper(self):
        self.c.execute("UPDATE audit_events SET detail='tampered' WHERE id=250")
        self.c.commit()
        with self.assertRaises(RuntimeError):
            cursor = {'last_id': 0, 'previous': '', 'count': 0}
            while True:
                seg = audit.verify_segment(self.c, self.key, cursor['last_id'], cursor['previous'], 64, cursor['count'])
                cursor = {'last_id': seg['last_id'], 'previous': seg['previous'], 'count': seg['count']}
                if seg['complete']:
                    break

    def test_segment_detects_orphan_checkpoint(self):
        self.c.execute("INSERT INTO audit_checkpoints(event_id,event_count,head_hash,signature,created_at) VALUES('evt_orphan',9999,'x','y','2026-09-15T00:00:00+00:00')")
        self.c.commit()
        with self.assertRaises(RuntimeError):
            audit.verify_orphans(self.c, 500)

    def test_readiness_completes_and_caches(self):
        result = server.readiness_verify_audit(self.c, budget_ms=10000)
        self.assertEqual(result['events'], 500)
        before = server.metrics_snapshot()['readyz_verify_events_scanned']
        server.readiness_verify_audit(self.c, budget_ms=10000)
        after = server.metrics_snapshot()['readyz_verify_events_scanned']
        self.assertEqual(before, after, 'steady-state probe must not rescan the chain')

    def test_budget_exhaustion_reports_not_ready_and_resumes(self):
        server.READYZ_VERIFY_BATCH = 8
        try:
            with self.assertRaises(server.AuditVerificationPending) as ctx:
                server.readiness_verify_audit(self.c, budget_ms=0.0)
            self.assertGreaterEqual(ctx.exception.progress['events_scanned'], 8)
            first_cursor = ctx.exception.progress['cursor_id']
            self.assertGreater(first_cursor, 0)
            result = server.readiness_verify_audit(self.c, budget_ms=10000)
            self.assertEqual(result['events'], 500)
        finally:
            server.READYZ_VERIFY_BATCH = 2000

    def test_single_flight_coalesces_concurrent_probes(self):
        server.reset_readiness_verifier()
        baseline = server.metrics_snapshot()
        errors = []
        barrier = threading.Barrier(8)

        def probe():
            conn = sqlite3.connect(self.path)
            conn.row_factory = sqlite3.Row
            try:
                barrier.wait()
                server.readiness_verify_audit(conn, budget_ms=5000)
            except server.AuditVerificationPending:
                pass
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(repr(exc))
            finally:
                conn.close()

        threads = [threading.Thread(target=probe) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        snapshot = server.metrics_snapshot()
        scanned = snapshot['readyz_verify_events_scanned'] - baseline['readyz_verify_events_scanned']
        coalesced = snapshot['readyz_verify_coalesced'] - baseline['readyz_verify_coalesced']
        self.assertLessEqual(scanned, 500 * 2,
                             'concurrent probes must not each rescan the chain')
        self.assertGreaterEqual(coalesced, 1)

    def test_probe_latency_bounded_on_large_ledger(self):
        big_dir = tempfile.TemporaryDirectory()
        self.addCleanup(big_dir.cleanup)
        path = os.path.join(big_dir.name, 'big.db')
        conn = build_ledger(path, 20000, self.key)
        self.addCleanup(conn.close)
        server.reset_readiness_verifier()
        worst = 0.0
        ready = False
        for _ in range(200):
            start = time.monotonic()
            try:
                server.readiness_verify_audit(conn)
                ready = True
            except server.AuditVerificationPending:
                ready = False
            worst = max(worst, (time.monotonic() - start) * 1000.0)
            if ready:
                break
        self.assertTrue(ready, 'verification never converged')
        self.assertLess(worst, 200.0, f'probe exceeded 200 ms ceiling: {worst:.2f} ms')


if __name__ == '__main__':
    unittest.main()
