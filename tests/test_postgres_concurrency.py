"""Run only against a disposable PostgreSQL database initialized by the app."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4
from app.database import db, IS_POSTGRES
from app.audit import append, verify

@unittest.skipUnless(os.getenv('DATABASE_URL'), 'DATABASE_URL not set')
class PostgresConcurrency(unittest.TestCase):
    def test_concurrent_appends_preserve_single_chain(self):
        self.assertTrue(IS_POSTGRES)
        key = os.getenv('AUDIT_HMAC_KEY', 'development-audit-key-not-for-production')
        with db() as c:
            before = verify(c, key)['events']
        def write(i):
            with db(True) as c:
                append(c, 'evt_'+uuid4().hex[:16], 'synthetic-test', 'test', 'fixture',
                       'local', str(i), datetime.now(timezone.utc).isoformat(), key)
        with ThreadPoolExecutor(max_workers=30) as pool:
            list(pool.map(write, range(100)))
        with db() as c:
            self.assertEqual(verify(c, key)['events'], before+100)
