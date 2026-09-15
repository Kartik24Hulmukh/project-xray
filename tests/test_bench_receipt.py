"""A failed convergence gate must still emit a machine-readable failure."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class BenchmarkReceipt(unittest.TestCase):
    def test_exhausted_cold_probes_write_failed_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)/'failed.json'
            result = subprocess.run([sys.executable, 'scripts/bench_readyz_singleflight.py',
                '--events', '10', '--clients', '1', '--probes', '0', '--out', str(out)],
                cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertFalse(report['ready_reached'])
            self.assertFalse(report['pass'])
            self.assertEqual(report['steady_samples'], 0)
            self.assertIsNone(report['steady_probe_p99_ms'])
            self.assertEqual(json.loads(out.read_text()), report)
            self.assertNotIn('Traceback', result.stderr)
