"""Issue #73 remediation attempt 4: idle heap trim contract."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app import server  # noqa: E402


class IdleHeapTrimTests(unittest.TestCase):
    def setUp(self):
        server._last_trim[0] = 0.0

    def test_trim_is_rate_limited(self):
        calls = []
        with mock.patch.object(server, '_MALLOC_TRIM', lambda n: calls.append(n) or 1):
            self.assertTrue(server._trim_heap_when_idle())
            self.assertFalse(server._trim_heap_when_idle())
        self.assertEqual(calls, [0])

    def test_missing_allocator_is_noop(self):
        with mock.patch.object(server, '_MALLOC_TRIM', None):
            self.assertFalse(server._trim_heap_when_idle())

    def test_allocator_error_never_raises(self):
        def boom(_):
            raise OSError('x')
        with mock.patch.object(server, '_MALLOC_TRIM', boom):
            self.assertFalse(server._trim_heap_when_idle())
        self.assertFalse(server._trim_lock.locked())

    def test_disable_switch(self):
        with mock.patch.dict(os.environ, {'XRAY_MALLOC_TRIM': 'off'}):
            self.assertIsNone(server._load_malloc_trim())

    def test_request_finished_trims_only_when_idle(self):
        srv = server.BoundedHTTPServer.__new__(server.BoundedHTTPServer)
        import threading
        srv._inflight = 2
        srv._inflight_lock = threading.Lock()
        srv._idle = threading.Event()
        with mock.patch.object(server, '_trim_heap_when_idle') as trim:
            srv.request_finished()
            trim.assert_not_called()
            srv.request_finished()
            trim.assert_called_once()


    def _make_server(self):
        srv = server.BoundedHTTPServer.__new__(server.BoundedHTTPServer)
        import threading
        srv._inflight = 0
        srv._inflight_lock = threading.Lock()
        srv._idle = threading.Event()
        srv._idle.set()
        srv._last_request_start = server.time.monotonic()
        srv._reap_stop = threading.Event()
        srv._reap_thread = None
        return srv

    def test_reaper_trims_after_quiet_window_elapses(self):
        srv = self._make_server()
        srv._last_request_start = server.time.monotonic() - 10.0
        with mock.patch.dict(os.environ, {
            'XRAY_HEAP_REAP_INTERVAL_S': '0.05',
            'XRAY_HEAP_REAP_QUIET_S': '0.05',
        }):
            with mock.patch.object(server, '_MALLOC_TRIM', lambda n: 1):
                srv._start_heap_reaper()
                import time as _time
                deadline = _time.monotonic() + 2.0
                while server._last_trim[0] == 0.0 and _time.monotonic() < deadline:
                    _time.sleep(0.05)
                srv._reap_stop.set()
                srv._reap_thread.join(timeout=1.0)
        self.assertGreater(server._last_trim[0], 0.0)

    def test_reaper_never_trims_while_request_in_flight(self):
        srv = self._make_server()
        srv._inflight = 1  # a request is in flight for the whole window
        with mock.patch.dict(os.environ, {
            'XRAY_HEAP_REAP_INTERVAL_S': '0.05',
            'XRAY_HEAP_REAP_QUIET_S': '0.05',
        }):
            with mock.patch.object(server, '_trim_heap_when_idle') as trim:
                srv._start_heap_reaper()
                import time as _time
                _time.sleep(0.3)
                srv._reap_stop.set()
                srv._reap_thread.join(timeout=1.0)
        trim.assert_not_called()


if __name__ == '__main__':
    unittest.main()
