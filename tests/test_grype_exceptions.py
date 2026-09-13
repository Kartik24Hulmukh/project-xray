import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "check_grype_exceptions.py"
APPROVED = "CVE-2026-76642"
ENTRY = "ignore:\n  - vulnerability: %s\n    package:\n      name: libuuid\n      type: apk\n"


def run_guard(grype_yaml):
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / ".grype.yaml").write_text(grype_yaml)
        proc = subprocess.run([sys.executable, str(GUARD)], cwd=tmp,
                              capture_output=True, text=True, timeout=60)
        return proc.returncode, proc.stdout + proc.stderr


class GrypeExceptionGuardTest(unittest.TestCase):
    def test_repo_config_passes(self):
        proc = subprocess.run([sys.executable, str(GUARD)], cwd=str(REPO),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_empty_config_passes(self):
        rc, out = run_guard("ignore: []\n")
        self.assertEqual(rc, 0, out)

    def test_missing_expiry_header_fails(self):
        rc, out = run_guard(ENTRY % APPROVED)
        self.assertEqual(rc, 1, out)
        self.assertIn("expires", out)

    def test_expired_acceptance_fails_closed(self):
        rc, out = run_guard("# expires: 2026-08-01\n" + ENTRY % APPROVED)
        self.assertEqual(rc, 1, out)
        self.assertIn("expired", out)

    def test_unapproved_cve_fails(self):
        rc, out = run_guard("# expires: 2026-09-27\n" + ENTRY % "CVE-2026-99999")
        self.assertEqual(rc, 1, out)
        self.assertIn("unapproved", out)

    def test_blanket_unscoped_suppression_fails(self):
        rc, out = run_guard("# expires: 2026-09-27\nignore:\n  - vulnerability: %s\n" % APPROVED)
        self.assertEqual(rc, 1, out)
        self.assertIn("not scoped", out)

    def test_valid_acceptance_passes(self):
        rc, out = run_guard("# expires: 2026-09-27\n" + ENTRY % APPROVED)
        self.assertEqual(rc, 0, out)


if __name__ == "__main__":
    unittest.main()
