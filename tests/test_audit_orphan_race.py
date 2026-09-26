"""Regression: the audit orphan guard must not fire on concurrent appends.

Root cause found in session 22 by the new /readyz determinism gate: under the
100-persona torture, 1-6 of 30 readiness probes answered 503 with no reason.
After the probe started reporting a machine-readable reason, the failure was
`RuntimeError: orphan audit checkpoint detected` raised by `verify_orphans`,
which compared COUNT(audit_checkpoints) against the number of events the
streaming verification had scanned. Writers append an (event, checkpoint) pair
while the verification is in flight, so a healthy instance reported a tamper and
fell out of the load balancer. The guard is now an anti-join.
"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import audit  # noqa: E402

KEY = "test-audit-key"


class OrphanGuardConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.c = sqlite3.connect(str(Path(self.dir.name) / "audit.db"))
        self.c.row_factory = sqlite3.Row
        self.c.execute(
            "CREATE TABLE audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE,"
            " actor TEXT, action TEXT, object_type TEXT, object_id TEXT, detail TEXT,"
            " previous_hash TEXT, event_hash TEXT, created_at TEXT)")
        self.c.execute(
            "CREATE TABLE audit_checkpoints(event_id TEXT PRIMARY KEY, event_count INTEGER,"
            " head_hash TEXT, signature TEXT, created_at TEXT)")
        for i in range(50):
            audit.append(self.c, f"evt_{i:04d}", "actor", "create", "project", f"prj_{i}",
                         "", "2026-09-26T00:00:00+00:00", KEY)
        self.c.commit()
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(self.c.close)

    def _walk(self):
        cursor = {"last_id": 0, "previous": "", "count": 0}
        while True:
            seg = audit.verify_segment(self.c, KEY, cursor["last_id"], cursor["previous"], 16, cursor["count"])
            cursor = {"last_id": seg["last_id"], "previous": seg["previous"], "count": seg["count"]}
            if seg["complete"]:
                return cursor

    def test_concurrent_append_during_verification_is_not_a_tamper(self):
        """Fails on the pre-fix cardinality guard with 'orphan audit checkpoint detected'."""
        cursor = self._walk()
        # A concurrent writer commits an event + checkpoint after the scan passed the tail.
        audit.append(self.c, "evt_concurrent", "actor", "create", "project", "prj_race",
                     "", "2026-09-26T00:00:01+00:00", KEY)
        self.c.commit()
        audit.verify_orphans(self.c, cursor["count"])  # must not raise

    def test_many_concurrent_appends_still_clean(self):
        cursor = self._walk()
        for i in range(25):
            audit.append(self.c, f"evt_race_{i}", "actor", "update", "project", "prj_race",
                         "", "2026-09-26T00:00:02+00:00", KEY)
        self.c.commit()
        audit.verify_orphans(self.c, cursor["count"])

    def test_deleted_event_is_still_detected_as_orphan(self):
        cursor = self._walk()
        self.c.execute("DELETE FROM audit_events WHERE event_id='evt_0025'")
        self.c.commit()
        with self.assertRaises(RuntimeError):
            audit.verify_orphans(self.c, cursor["count"])

    def test_injected_checkpoint_is_still_detected_as_orphan(self):
        self.c.execute("INSERT INTO audit_checkpoints(event_id,event_count,head_hash,signature,created_at)"
                       " VALUES('evt_forged',999,'x','y','2026-09-26T00:00:00+00:00')")
        self.c.commit()
        with self.assertRaises(RuntimeError):
            audit.verify_orphans(self.c, 50)

    def test_full_verify_still_detects_orphan_after_deletion(self):
        self.c.execute("INSERT INTO audit_checkpoints(event_id,event_count,head_hash,signature,created_at)"
                       " VALUES('evt_forged',999,'x','y','2026-09-26T00:00:00+00:00')")
        self.c.commit()
        with self.assertRaises(RuntimeError):
            audit.verify(self.c, KEY)

    def test_full_verify_clean_chain_passes(self):
        result = audit.verify(self.c, KEY)
        self.assertEqual(result["events"], 50)


if __name__ == "__main__":
    unittest.main()
