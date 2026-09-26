"""Failure-oriented contracts for full-wave soak evidence and DB isolation."""
import contextlib
import io
import importlib.util
import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from scripts import human_persona_torture as h


class RecoveryTests(unittest.TestCase):
    def wave(self, latency=1, status=200):
        return {"healthz": {"status": 200, "latency_ms": 1},
                "readyz": {"status": status, "latency_ms": latency}}

    def test_late_wave_failure_is_not_hidden(self):
        self.assertFalse(h.recovery_passes([self.wave(), self.wave(201)]))
        self.assertFalse(h.recovery_passes([self.wave(), self.wave(status=503)]))

    def test_boundary_empty_and_nan_fail_closed(self):
        self.assertFalse(h.recovery_passes([]))
        self.assertFalse(h.recovery_passes([self.wave(200)]))
        self.assertFalse(h.recovery_passes([self.wave(float("nan"))]))
        self.assertTrue(h.recovery_passes([self.wave(199.9)]))

    def test_transport_failure_is_a_failed_measurement(self):
        with patch.object(h.urllib.request, "urlopen", side_effect=OSError("offline")):
            self.assertEqual(h.recovery_probe("http://localhost", "/readyz")["status"], -1)


class DatabaseIsolationTests(unittest.TestCase):
    def test_default_ignores_inherited_database(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://remote/prod"}):
            with h.isolated_database("sqlite") as url:
                self.assertIsNone(url)

    def test_pg_requires_explicit_loopback_admin(self):
        for url in ("", "postgresql://example.com/prod", "host=/tmp dbname=prod"):
            with patch.dict(os.environ, {"XRAY_TORTURE_PG_ADMIN_URL": url}):
                with self.assertRaises(ValueError):
                    with h.isolated_database("postgres"):
                        self.fail("unsafe DSN accepted")

    def test_pg_rejects_remote_dsn_without_a_driver_installed(self):
        """Fail closed even if psycopg2 is absent: no driver import before the host check."""
        with patch.dict(sys.modules, {"psycopg2": None, "psycopg2.extensions": None}):
            with patch.dict(os.environ, {"XRAY_TORTURE_PG_ADMIN_URL": "postgresql://example.com/prod"}):
                with self.assertRaises(ValueError):
                    with h.isolated_database("postgres"):
                        self.fail("unsafe DSN accepted")

    @unittest.skipUnless(importlib.util.find_spec("psycopg2"), "psycopg2 not installed")
    def test_pg_database_dropped_even_if_run_raises(self):
        import psycopg2
        with patch.dict(os.environ, {"XRAY_TORTURE_PG_ADMIN_URL": "postgresql://xray@127.0.0.1/postgres"}):
            with patch.object(psycopg2, "connect") as connect:
                with self.assertRaisesRegex(RuntimeError, "injected"):
                    with h.isolated_database("postgres") as url:
                        self.assertIn("xray_torture_", url)
                        raise RuntimeError("injected")
                self.assertEqual(connect.return_value.cursor.return_value.__enter__.return_value.execute.call_count, 2)
                connect.return_value.close.assert_called_once()

    def test_sampler_retention_is_bounded(self):
        sampler = h.RssSampler(os.getpid())
        for value in range(10000):
            sampler.samples_kb.append(value)
        self.assertEqual(len(sampler.samples_kb), 4096)


class NightlyContractTests(unittest.TestCase):
    def test_both_dialects_seed_and_receipts(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/nightly-soak.yml").read_text()
        for expected in ("schedule:", "workflow_dispatch:", "[sqlite, postgres]",
                         "--waves ${{ inputs.waves || '20' }}", "--seed 20260925", "timeout-minutes: 20",
                         "if: always()", "contents: read"):
            self.assertIn(expected, text)

    def test_manual_wave_choices_preserve_default_gate(self):
        text = (Path(__file__).resolve().parents[1] /
                ".github/workflows/nightly-soak.yml").read_text()
        dispatch = text.split("  workflow_dispatch:\n", 1)[1].split("  pull_request:", 1)[0]
        self.assertIn("    inputs:\n      waves:", dispatch)
        self.assertIn("        type: choice", dispatch)
        self.assertIn("        default: '20'", dispatch)
        self.assertIn("        options: ['20', '60']", dispatch)
        # Scheduled/PR events have no inputs: the shared invocation falls back
        # to 20, while manual runs may request the longer investigation.
        commands = [line.strip() for line in text.splitlines()
                    if line.strip().startswith("python3 scripts/human_persona_torture.py ")]
        self.assertEqual(len(commands), 1)
        self.assertIn("--waves ${{ inputs.waves || '20' }}", commands[0])
        self.assertIn("--concurrency 100", commands[0])
        self.assertIn("--backend ${{ matrix.backend }}", commands[0])


class TortureCliTests(unittest.TestCase):
    def invoke(self, *args):
        with patch.object(sys, "argv", ["human_persona_torture.py", *args]):
            with patch.object(h, "isolated_database") as database, patch.object(h, "run") as run:
                with contextlib.redirect_stderr(io.StringIO()):
                    h.main()
                return database, run

    def test_workflow_wave_options_reach_runner(self):
        for waves in ("20", "60"):
            with self.subTest(waves=waves):
                database, run = self.invoke("--waves", waves, "--concurrency", "100")
                database.assert_called_once_with("sqlite")
                run.assert_called_once()
                self.assertEqual(run.call_args.args[0].waves, int(waves))

    def test_cli_bounds_fail_before_database_or_runner(self):
        for args in (("--waves", "0"), ("--waves", "61"), ("--waves", "-1"),
                     ("--waves", "invalid"), ("--concurrency", "0"),
                     ("--concurrency", "1001")):
            with self.subTest(args=args), patch.object(sys, "argv", ["torture", *args]):
                with patch.object(h, "isolated_database") as database, patch.object(h, "run") as run:
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                        h.main()
                    self.assertEqual(error.exception.code, 2)
                    database.assert_not_called()
                    run.assert_not_called()

    def test_cli_default_and_boundaries(self):
        _, run = self.invoke()
        self.assertEqual(run.call_args.args[0].waves, 1)
        for waves, concurrency in (("1", "1"), ("60", "1000")):
            _, run = self.invoke("--waves", waves, "--concurrency", concurrency)
            self.assertEqual(run.call_args.args[0].waves, int(waves))
            self.assertEqual(run.call_args.args[0].concurrency, int(concurrency))
