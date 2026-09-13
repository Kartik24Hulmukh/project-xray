"""Contract tests for scripts/bench_sweep.py (deterministic benchmark sweep)."""
import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bench_sweep.py"


def _load():
    spec = importlib.util.spec_from_file_location("bench_sweep", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BenchSweepContractTest(unittest.TestCase):
    def test_script_exists_and_compiles(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertIsNotNone(_load())

    def test_phases_cover_the_reported_slos(self):
        module = _load()
        self.assertIn("100_client_reads", module.PHASES)
        self.assertIn("100_client_writes", module.PHASES)

    def test_rejects_non_positive_values(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--values", "0"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("positive", proc.stderr)

    def test_run_one_reports_errors_instead_of_raising(self):
        module = _load()
        module.STRESS = ROOT / "scripts" / "does_not_exist_bench.py"
        result = module.run_one(2, 1)
        self.assertEqual(result["exec_parallelism"], 2)
        self.assertIn("error", result)

    def test_checked_in_receipt_is_valid_and_deterministic_shape(self):
        receipt = ROOT / "docs" / "validation" / "bench-sweep-harden-prod-2026-09-13.json"
        if not receipt.is_file():
            self.skipTest("receipt not generated in this checkout")
        data = json.loads(receipt.read_text())
        self.assertEqual(data["tool"], "bench_sweep")
        self.assertTrue(data["results"])
        for row in data["results"]:
            self.assertIn("exec_parallelism", row)


if __name__ == "__main__":
    unittest.main()
