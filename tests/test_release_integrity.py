#!/usr/bin/env python3
"""Release integrity tests: verify version consistency across all artifacts."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestReleaseIntegrity(unittest.TestCase):
    """Fail if any version or artifact is inconsistent."""

    def test_package_json_version(self):
        pkg = json.loads((ROOT / "package.json").read_text())
        self.assertEqual(pkg["version"], "0.4.6")

    def test_sbom_version_matches_package(self):
        pkg_version = json.loads((ROOT / "package.json").read_text())["version"]
        sbom_src = (ROOT / "scripts/generate_sbom.py").read_text()
        # SBOM script must embed the same version
        self.assertIn(f"'version':'{pkg_version}'", sbom_src.replace(" ", ""))

    def test_server_version_matches_package(self):
        pkg_version = json.loads((ROOT / "package.json").read_text())["version"]
        server_src = (ROOT / "app/server.py").read_text()
        self.assertIn(f"'version': '{pkg_version}'", server_src)
        # Ensure no stale versions remain
        self.assertNotIn("'version': '0.4.0'", server_src)
        self.assertNotIn("'version': '0.2.0'", server_src)

    def test_release_notes_contain_version(self):
        notes = (ROOT / "RELEASE_NOTES.md").read_text()
        self.assertIn("0.4.6", notes)

    def test_requirements_txt_exists(self):
        self.assertTrue((ROOT / "requirements.txt").exists())

    def test_dockerfile_installs_requirements(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("requirements.txt", dockerfile)
        self.assertIn("pip install", dockerfile)

    def test_dockerignore_exists(self):
        self.assertTrue((ROOT / ".dockerignore").exists())

    def test_schema_postgres_exists(self):
        self.assertTrue((ROOT / "db/schema_postgres.sql").exists())

    def test_migration_scripts_refuse_database_url(self):
        for script in ("scripts/migrate_legacy.py", "scripts/migrate_v2_to_v3.py"):
            src = (ROOT / script).read_text()
            self.assertIn("DATABASE_URL", src)
            self.assertIn("SQLite-only", src)

    def test_recovery_uses_pg_env_not_url_cli(self):
        """pg_dump/pg_restore must not receive DATABASE_URL as a CLI argument."""
        src = (ROOT / "scripts/recovery.py").read_text()
        # Should use --host/--port/--username/--dbname, not raw URL
        self.assertIn("--host", src)
        self.assertIn("--username", src)
        # Should NOT pass url directly to pg_dump/pg_restore
        self.assertNotIn("['pg_dump', url", src)
        self.assertNotIn("['pg_restore', f'--dbname={url}'", src)

    def test_recovery_redacts_credentials_in_errors(self):
        src = (ROOT / "scripts/recovery.py").read_text()
        self.assertIn("REDACTED", src)


if __name__ == "__main__":
    unittest.main()
