"""Regression tests for the RAM floor/ceiling/settled instrumentation in the persona torture harness.

Sessions 17-20 recorded only the kernel high-water mark (VmHWM). These tests pin the
structural fix: the harness now measures the resident floor directly (VmRSS), samples
the load window, and records the settled value after recovery.
"""
import importlib.util
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_harness():
    spec = importlib.util.spec_from_file_location(
        "human_persona_torture", ROOT / "scripts" / "human_persona_torture.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(os.path.exists("/proc/self/status"), "requires Linux /proc")
class TortureHarnessRssTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.harness = _load_harness()

    def test_current_rss_is_positive_and_not_above_peak(self):
        pid = os.getpid()
        current = self.harness.get_current_rss_kb(pid)
        peak = self.harness.get_peak_rss_kb(pid)
        self.assertGreater(current, 0)
        self.assertGreater(peak, 0)
        self.assertLessEqual(current, peak, "VmRSS can never exceed VmHWM")

    def test_unknown_pid_reads_zero_not_exception(self):
        self.assertEqual(self.harness.get_current_rss_kb(2 ** 22 - 1), 0)
        self.assertEqual(self.harness.get_peak_rss_kb(2 ** 22 - 1), 0)

    def test_sampler_collects_bounded_samples_and_stops(self):
        sampler = self.harness.RssSampler(os.getpid(), interval=0.002)
        sampler.start()
        time.sleep(0.05)
        result = sampler.stop()
        self.assertFalse(sampler.is_alive(), "sampler thread must terminate on stop()")
        self.assertGreaterEqual(result["samples"], 3)
        self.assertGreater(result["min_kb"], 0)
        self.assertLessEqual(result["min_kb"], result["max_kb"])
        self.assertEqual(result["samples"], len(sampler.samples_kb))

    def test_sampler_on_dead_pid_reports_no_samples(self):
        sampler = self.harness.RssSampler(2 ** 22 - 1, interval=0.002)
        sampler.start()
        time.sleep(0.02)
        result = sampler.stop()
        self.assertEqual(result, {"samples": 0, "min_kb": 0, "max_kb": 0})

    def test_receipt_keys_and_real_timestamp_are_emitted(self):
        source = (ROOT / "scripts" / "human_persona_torture.py").read_text(encoding="utf-8")
        for key in ("rss_floor_kb", "rss_ceiling_kb", "rss_settled_kb",
                    "rss_growth_after_recovery_kb", "rss_settled_bounded_128mb", "rss_samples"):
            self.assertIn(f'"{key}"', source)
        self.assertNotIn('"timestamp": "2026-09-19T19:00:00Z"', source,
                         "hard-coded receipt timestamp must not return")
        self.assertIn("datetime.now(timezone.utc)", source)


if __name__ == "__main__":
    unittest.main()
