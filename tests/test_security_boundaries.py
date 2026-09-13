"""Adversarial regressions for bounded authentication and strict JSON."""
import concurrent.futures
import unittest
from unittest.mock import patch

from app import security
from app.server import strict_json


class ReplayCapacityTests(unittest.TestCase):
    def setUp(self):
        security.clear_replay_cache()
        self.addCleanup(security.clear_replay_cache)

    def test_saturation_never_evicts_live_assertion(self):
        with patch.object(security, '_REPLAY_MAX', 2), patch.object(security.time, 'time', return_value=100):
            self.assertTrue(security._remember_assertion('first', 130))
            self.assertTrue(security._remember_assertion('second', 130))
            self.assertFalse(security._remember_assertion('overflow', 130))
            self.assertFalse(security._remember_assertion('first', 130))
            self.assertEqual(len(security._replay_seen), 2)

    def test_expiry_reclaims_space_even_out_of_order(self):
        with patch.object(security, '_REPLAY_MAX', 2), patch.object(security.time, 'time', return_value=100):
            self.assertTrue(security._remember_assertion('long', 130))
            self.assertTrue(security._remember_assertion('short', 105))
        with patch.object(security, '_REPLAY_MAX', 2), patch.object(security.time, 'time', return_value=110):
            self.assertTrue(security._remember_assertion('new', 140))
            self.assertNotIn('short', security._replay_seen)
            self.assertFalse(security._remember_assertion('long', 130))

    def test_concurrent_saturation_preserves_all_admitted_signatures(self):
        with patch.object(security, '_REPLAY_MAX', 32), patch.object(security.time, 'time', return_value=100):
            def remember(i):
                return security._remember_assertion(str(i), 130)
            with concurrent.futures.ThreadPoolExecutor(max_workers=100) as pool:
                results = list(pool.map(remember, range(1000)))
            self.assertEqual(sum(results), 32)
            for i, admitted in enumerate(results):
                if admitted:
                    self.assertFalse(remember(i))

    def test_gateway_verifier_denies_replay_after_capacity_attack(self):
        from app.gateway import mint_gateway_assertion
        secret = 'synthetic-capacity-test-secret-0000000000'
        def mint(i):
            return mint_gateway_assertion(
                issuer='https://synthetic.invalid', subject='reviewer', role='reviewer',
                secret=secret, key_id='test', audience='xray', issued_at=100,
                nonce=f'synthetic-nonce-{i:016d}')
        def verify(headers):
            return security.verify_gateway_assertion(headers, {'test': secret},
                'xray', {'https://synthetic.invalid'}, now_epoch=100)
        with patch.object(security, '_REPLAY_MAX', 2), patch.object(security.time, 'time', return_value=100):
            first = mint(1)
            self.assertIsNotNone(verify(first))
            self.assertIsNotNone(verify(mint(2)))
            self.assertIsNone(verify(mint(3)))
            self.assertIsNone(verify(first))


class ContainerRefreshTests(unittest.TestCase):
    def test_each_alpine_stage_refreshes_inherited_packages(self):
        from pathlib import Path
        dockerfile = (Path(__file__).resolve().parents[1] / 'Dockerfile').read_text()
        stages = dockerfile.split('FROM python:')[1:]
        self.assertEqual(len(stages), 2)
        for stage in stages:
            self.assertIn('apk upgrade --no-cache', stage)
            self.assertLess(stage.index('apk upgrade --no-cache'), stage.index('apk add --no-cache'))


class StrictNumberTests(unittest.TestCase):
    def test_exponent_overflow_rejected_at_any_depth(self):
        for raw in (b'{"x":1e400}', b'{"x":-1e400}', b'{"x":[{"y":1e400}]}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                strict_json(raw)

    def test_finite_numbers_remain_accepted(self):
        self.assertEqual(strict_json(b'{"x":[1.25,1e3,-2]}'), {'x': [1.25,1000.0,-2]})


if __name__ == '__main__':
    unittest.main()
