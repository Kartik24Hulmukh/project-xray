"""Tests for the 100-Persona Human Torture & Concurrency Stress Suite."""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.human_persona_torture import generate_personas, ARCHETYPES


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
