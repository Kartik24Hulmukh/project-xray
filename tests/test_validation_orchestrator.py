import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('validate', Path(__file__).resolve().parents[1] / 'scripts/session8_validate.py')
validate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validate)


class ValidationTests(unittest.TestCase):
    def test_nonzero_and_both_streams_preserved(self):
        r = validate.run(['-c', 'import sys; print("stdout marker"); print("stderr marker", file=sys.stderr); sys.exit(3)'])
        self.assertEqual(r['rc'], 3)
        self.assertIn('stdout marker', r['tail'])
        self.assertIn('stderr marker', r['tail'])

    def test_timeout_is_failure(self):
        r = validate.run(['-c', 'import time; time.sleep(30)'], timeout=.1)
        self.assertEqual(r['rc'], 124)
        self.assertTrue(r['timed_out'])
        self.assertLess(r['seconds'], 5)

    def test_skip_count_is_explicit(self):
        r = validate.run(['-c', 'import sys; print("Ran 12 tests in 1s\\nOK (skipped=2)", file=sys.stderr)'])
        self.assertEqual(r['tests_run'], 12)
        self.assertEqual(r['tests_skipped'], 2)
