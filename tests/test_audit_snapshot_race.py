"""Regression: audit reads must be single-snapshot; append counts must be positional.

Session 23 root cause of the residual dependency_check_failed /readyz 503s in
the session-22 5-wave soak: verify_head() and verify() took TWO or THREE
separate statement snapshots (head row, checkpoint row, COUNT(*)), and
append() minted event_count from a bare COUNT(*) of the whole table in its
own snapshot. On autocommit SQLite read handles (and READ COMMITTED on
PostgreSQL) each statement is its own snapshot, so a writer committing an
(event, checkpoint) pair in between made a HEALTHY chain look tampered, and
two interleaved writers could mint duplicate counts. Every read here is now
one statement = one snapshot, and event_count is the event's position in the
chain (COUNT over id <= own id), which concurrent appends cannot change.

Each test below injects the interleaved writer at the exact statement the
PRE-FIX code issued. The fixed code never issues those statements, so the
interleave never fires and the test passes; on the pre-fix code the
interleave fires and the unhealthy verdict raises - i.e. these tests fail
before the fix and pass after it (verified by swapping app/audit.py).
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
STAMP = "2026-09-26T00:00:00+00:00"


class InterleavedConn:
    """Connection wrapper that runs a side-write once, at a chosen statement."""

    def __init__(self, real, trigger, side):
        self._real = real
        self._trigger = trigger
        self._side = side
        self.fired = 0

    def execute(self, query, params=()):
        if query == self._trigger and not self.fired:
            self.fired += 1
            self._side()
        return self._real.execute(query, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


def make_db(directory, n=20):
    path = Path(directory) / "audit.db"
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE,"
        " actor TEXT, action TEXT, object_type TEXT, object_id TEXT, detail TEXT,"
        " previous_hash TEXT, event_hash TEXT, created_at TEXT)")
    c.execute(
        "CREATE TABLE audit_checkpoints(event_id TEXT PRIMARY KEY, event_count INTEGER,"
        " head_hash TEXT, signature TEXT, created_at TEXT)")
    for i in range(n):
        audit.append(c, f"evt_{i:04d}", "actor", "create", "project", f"prj_{i}", "", STAMP, KEY)
    c.commit()
    return c, path


class AuditSnapshotRaceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.c, self.path = make_db(self.dir.name)
        self.addCleanup(self.c.close)
        self.side = sqlite3.connect(str(self.path))
        self.side.row_factory = sqlite3.Row
        self.addCleanup(self.side.close)

    def _side_append(self, tag):
        audit.append(self.side, f"evt_side_{tag}", "actor", "create", "project", "prj_side",
                     "", STAMP, KEY)
        self.side.commit()

    def _positions(self):
        rows = self.c.execute(
            "SELECT e.event_id AS event_id, e.id AS id, cp.event_count AS n"
            " FROM audit_events e JOIN audit_checkpoints cp ON cp.event_id=e.event_id"
            " ORDER BY e.id").fetchall()
        return [(r["event_id"], r["n"]) for r in rows], [i + 1 for i in range(len(rows))]

    def test_append_mints_positional_counts_under_interleaved_writer(self):
        """Two interleaved writers must not mint duplicate event_counts.

        Pre-fix append() read COUNT(*) of the whole table in its own snapshot
        after its insert; an interleave between the insert and the count made
        two events share one count. The trigger string is exactly the pre-fix
        count statement; the fixed append counts id <= own id instead.
        """
        wrapped = InterleavedConn(
            self.c, "SELECT COUNT(*) n FROM audit_events",
            lambda: self._side_append("a"))
        audit.append(wrapped, "evt_late", "actor", "create", "project", "prj_late", "", STAMP, KEY)
        self.c.commit()
        # The fixed append must not read a bare whole-table COUNT(*) at all:
        # event_count is positional (COUNT over id <= own id), one snapshot.
        self.assertEqual(wrapped.fired, 0)
        counts, expected = self._positions()
        self.assertEqual([n for _, n in counts], expected)
        self.assertEqual(len(set(n for _, n in counts)), len(counts))

    def test_verify_head_strict_count_is_single_snapshot(self):
        """strict_count verify_head must not compare across two snapshots.

        Pre-fix: head row, checkpoint row and COUNT(*) were three statements;
        an append between checkpoint and COUNT made a healthy chain raise.
        """
        wrapped = InterleavedConn(
            self.c, "SELECT COUNT(*) n FROM audit_events",
            lambda: self._side_append("h"))
        state = audit.verify_head(wrapped, KEY, strict_count=True)
        self.assertEqual(wrapped.fired, 0)
        self.assertEqual(state["events"], 20)

    def test_verify_walk_is_single_snapshot(self):
        """verify() must not materialise checkpoints in a separate snapshot.

        Pre-fix: checkpoints were read in one statement, events walked in a
        second; an append in between produced an event with no checkpoint in
        the stale dict -> false 'checkpoint missing or inconsistent'.
        """
        wrapped = InterleavedConn(
            self.c, "SELECT * FROM audit_events ORDER BY id",
            lambda: self._side_append("v"))
        state = audit.verify(wrapped, KEY)
        self.assertEqual(wrapped.fired, 0)
        self.assertEqual(state["events"], 20)

    def test_tamper_still_detected_after_fix(self):
        """The structural rewrite must not weaken tamper detection."""
        self.c.execute("PRAGMA foreign_keys=OFF")
        # triggers block DELETE; drop the row the way an attacker with
        # database access would, bypassing the immutability triggers.
        self.c.execute("DROP TRIGGER IF EXISTS audit_no_delete")
        self.c.execute("DELETE FROM audit_events WHERE id=(SELECT MAX(id) FROM audit_events)")
        self.c.commit()
        with self.assertRaises(RuntimeError):
            audit.verify(self.c, KEY)
        with self.assertRaises(RuntimeError):
            audit.verify_orphans(self.c, 20)


if __name__ == "__main__":
    unittest.main()
