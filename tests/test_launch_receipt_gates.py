import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('stress_local', Path(__file__).resolve().parents[1]/'scripts/stress_local.py')
stress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stress)


class LaunchGateTests(unittest.TestCase):
    def setUp(self):
        self.r = dict(phases=[dict(name=n, statuses={s: 100}, requests=100) for n, s in
            [('baseline_reads', '200'), ('100_client_reads', '200'), ('100_client_writes', '201'),
             ('100_client_idempotency_race', '201')]], server_peak_rss_kb=50000,
            health_recovery=dict(status=200, latency_ms=1), ready_recovery=dict(status=200, latency_ms=2),
            server_alive=True, tracebacks_in_log=0)

    def test_clean_receipt_passes(self):
        self.assertTrue(all(stress.launch_gates(self.r).values()))

    def test_503_is_not_success(self):
        self.r['phases'][1]['statuses'] = {'503': 100}
        self.assertFalse(stress.launch_gates(self.r)['statuses_pass'])

    def test_ram_missing_or_excessive_fails(self):
        for rss in [None, 0, 131073]:
            self.r['server_peak_rss_kb'] = rss
            self.assertFalse(stress.launch_gates(self.r)['ram_pass'])

    def test_recovery_ceiling_is_enforced(self):
        self.r['health_recovery']['latency_ms'] = 200
        self.assertFalse(stress.launch_gates(self.r)['recovery_pass'])

    def test_traceback_or_dead_process_fails(self):
        self.r['tracebacks_in_log'] = 1
        self.assertFalse(stress.launch_gates(self.r)['process_pass'])
        self.r['tracebacks_in_log'] = 0; self.r['server_alive'] = False
        self.assertFalse(stress.launch_gates(self.r)['process_pass'])

    def test_missing_phase_and_requests_fail(self):
        self.r['phases'][0]['requests'] = 101
        self.assertFalse(stress.launch_gates(self.r)['statuses_pass'])
        self.r['phases'].pop()
        self.assertFalse(stress.launch_gates(self.r)['statuses_pass'])
