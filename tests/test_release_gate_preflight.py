"""Contract tests for the release-gate browser provisioning preflight.

Regression guard for the exit-code misclassification: a missing Chromium
binary is an ENVIRONMENT fault (exit 2), never a UI regression (exit 1).
The gate module executes work at import time, so these tests load only the
preflight function out of the source via ast instead of importing it.
"""
import ast
import subprocess
import types
import unittest
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / "scripts" / "check_release.py"
SOURCE = GATE.read_text(encoding="utf-8")


def load_preflight():
    tree = ast.parse(SOURCE)
    ns = {"subprocess": subprocess, "shutil": types.SimpleNamespace(which=lambda n: n)}
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "").startswith("BROWSER_"):
            exec(compile(ast.Module([node], []), str(GATE), "exec"), ns)
        if isinstance(node, ast.FunctionDef) and node.name == "browser_acceptance_preflight":
            exec(compile(ast.Module([node], []), str(GATE), "exec"), ns)
    return ns


class Completed:
    def __init__(self, returncode, stderr=b""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = b""


class ReleaseGatePreflightContract(unittest.TestCase):
    def setUp(self):
        self.ns = load_preflight()
        self.preflight = self.ns["browser_acceptance_preflight"]

    def test_provisioned_browser_is_go(self):
        self.assertEqual(self.preflight(".", runner=lambda *a, **k: Completed(0)), [])

    def test_missing_chromium_binary_is_actionable_environment_fault(self):
        problems = self.preflight(
            ".",
            runner=lambda *a, **k: Completed(3, b"XRAY_BROWSER_MISSING /root/.cache/ms-playwright/chromium-1140/chrome\n"),
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("ms-playwright", problems[0])

    def test_playwright_undeclared_is_not_an_environment_fault(self):
        problems = self.preflight(
            ".",
            runner=lambda *a, **k: Completed(1, b"Error: Cannot find module 'playwright'"),
        )
        self.assertEqual(problems, [])

    def test_missing_node_is_reported_without_running_probe(self):
        def explode(*a, **k):
            raise AssertionError("probe must not run when node is absent")

        problems = self.preflight(".", runner=explode, which=lambda n: None)
        self.assertEqual(len(problems), 1)
        self.assertIn("node", problems[0])

    def test_preflight_runs_before_ui_acceptance_and_exits_two(self):
        call = SOURCE.index("provisioning=browser_acceptance_preflight(root)")
        ui = SOURCE.index("scripts/ui_acceptance.mjs", call)
        self.assertLess(call, ui, "preflight must be evaluated before the UI acceptance run")
        self.assertIn("sys.exit(2)", SOURCE[call:ui])

    def test_real_ui_failure_still_exits_one(self):
        idx = SOURCE.index("ui=subprocess.run")
        self.assertIn("if ui.returncode:sys.exit(1)", SOURCE[idx:idx + 200])


if __name__ == "__main__":
    unittest.main()
