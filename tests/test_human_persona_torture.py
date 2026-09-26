"""Tests for the 100-Persona Human Torture & Concurrency Stress Suite."""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import subprocess
import time

from scripts.human_persona_torture import generate_personas, ARCHETYPES, wait_ready


class TestHumanPersonaTorture(unittest.TestCase):
    def test_personas_generator_spec(self):
        personas = generate_personas()
        self.assertGreaterEqual(len(personas), 100)
        roles = {p["role"] for p in personas}
        expected_roles = {r[0] for r in ARCHETYPES}
        self.assertEqual(roles, expected_roles)
        for p in personas:
            self.assertIn("persona_id", p)
            self.assertIn("role", p)
            self.assertIn("name", p)
            self.assertIn("chaos_level", p)


class TestWaitReady(unittest.TestCase):
    def test_fails_fast_when_process_already_exited(self):
        proc = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
        proc.wait(timeout=5)
        t0 = time.perf_counter()
        with self.assertRaisesRegex(RuntimeError, "exited with code 3"):
            wait_ready("http://127.0.0.1:9", deadline_s=5.0, proc=proc)
        self.assertLess(time.perf_counter() - t0, 1.0)

    def test_deadline_is_bounded_when_nothing_listens(self):
        t0 = time.perf_counter()
        with self.assertRaisesRegex(RuntimeError, "failed to reach ready"):
            wait_ready("http://127.0.0.1:9", deadline_s=0.3)
        self.assertLess(time.perf_counter() - t0, 2.0)


if __name__ == "__main__":
    unittest.main()
