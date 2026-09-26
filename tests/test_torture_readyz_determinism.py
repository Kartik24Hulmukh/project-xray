"""Fixed-seed determinism gate for /readyz in the persona torture harness.

Session 21 left an open P1: 0-5 of the 30 readiness probes answered 503
(audit_verification_in_progress) from run to run, which made the receipt
non-deterministic even though 200 + 503 stayed constant. The harness now
classifies every readiness answer by its machine-readable reason and gates the
run on the invariant. These tests pin that logic.
"""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "human_persona_torture", ROOT / "scripts" / "human_persona_torture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClassifyReasonTests(unittest.TestCase):
    def setUp(self):
        self.h = _load_harness()

    def test_200_is_ok(self):
        self.assertEqual(self.h.classify_reason(200, b"{}"), "ok")

    def test_503_reports_reason(self):
        body = json.dumps({"status": "not_ready", "reason": "audit_verification_in_progress"}).encode()
        self.assertEqual(self.h.classify_reason(503, body), "audit_verification_in_progress")

    def test_non_json_body_is_unparseable_not_an_exception(self):
        self.assertEqual(self.h.classify_reason(503, b"<html>502 gateway</html>"), "unparseable")

    def test_non_object_json_is_unparseable(self):
        self.assertEqual(self.h.classify_reason(503, b"[1, 2, 3]"), "unparseable")

    def test_missing_reason_key_is_unknown(self):
        self.assertEqual(self.h.classify_reason(503, b"{}"), "unknown")

    def test_undecodable_bytes_do_not_raise(self):
        self.assertEqual(self.h.classify_reason(503, b"\xff\xfe\x00"), "unparseable")


class ReadyzInvariantTests(unittest.TestCase):
    def setUp(self):
        self.h = _load_harness()

    def test_all_200_holds(self):
        got = self.h.readyz_invariant({200: 30}, {"ok": 30}, 30)
        self.assertTrue(got["holds"])
        self.assertEqual(got["probes_total"], 30)
        self.assertEqual(got["status_breakdown"], {"200": 30})

    def test_mixed_200_and_allowed_503_holds(self):
        got = self.h.readyz_invariant(
            {200: 27, 503: 3}, {"ok": 27, "audit_verification_in_progress": 3}, 30)
        self.assertTrue(got["holds"], got)

    def test_lost_probe_breaks_invariant(self):
        got = self.h.readyz_invariant({200: 28}, {"ok": 28}, 30)
        self.assertFalse(got["holds"])
        self.assertEqual(got["probes_total"], 28)

    def test_unexpected_status_breaks_invariant(self):
        got = self.h.readyz_invariant({200: 29, 500: 1}, {"ok": 29, "unknown": 1}, 30)
        self.assertFalse(got["holds"])
        self.assertEqual(got["unexpected_status"], [500])

    def test_connection_error_status_breaks_invariant(self):
        got = self.h.readyz_invariant({200: 29, -1: 1}, {"ok": 29, "unparseable": 1}, 30)
        self.assertFalse(got["holds"])
        self.assertEqual(got["unexpected_status"], [-1])

    def test_unlisted_503_reason_breaks_invariant(self):
        got = self.h.readyz_invariant(
            {200: 29, 503: 1}, {"ok": 29, "database_unreachable": 1}, 30)
        self.assertFalse(got["holds"])
        self.assertEqual(got["unexpected_reasons"], ["database_unreachable"])

    def test_empty_run_is_not_silently_green(self):
        self.assertFalse(self.h.readyz_invariant({}, {}, 30)["holds"])


class HarnessWiringTests(unittest.TestCase):
    """The gate is worthless unless the run actually fails on it."""

    def test_gate_is_part_of_the_pass_verdict(self):
        src = (ROOT / "scripts" / "human_persona_torture.py").read_text(encoding="utf-8")
        self.assertIn('operational_baselines["readyz_determinism_invariant"]', src)
        self.assertIn('"probe": "readyz"', src)


if __name__ == "__main__":
    unittest.main()
