"""Reconciliation contracts on actual PostgreSQL when configured."""
import os
import unittest
import test_idempotency_reconcile


@unittest.skipUnless(os.getenv('DATABASE_URL'), 'DATABASE_URL not set')
class PostgresReconcileTests(test_idempotency_reconcile.ReconcileTests):
    def setUp(self):
        from app.database import ConnectionAdapter
        import psycopg2
        raw = psycopg2.connect(os.environ['DATABASE_URL'])
        self.c = ConnectionAdapter(raw)
        self.c.execute('CREATE TEMP TABLE idempotency_keys(principal TEXT,key TEXT,state TEXT,created_at TEXT,completed_at TEXT,request_hash TEXT,PRIMARY KEY(principal,key))')
        self.c.commit()
        self.addCleanup(raw.close)
