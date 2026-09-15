#!/usr/bin/env python3
"""Deterministic local synthetic signed-ledger readiness cost, not deployment load.

Fixture generation uses fixed IDs/timestamps and the production hash functions.
Measures cold and warm audit readiness helper, not total HTTP latency.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import audit


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--events', type=int, default=100000)
    args = ap.parse_args()
    if not 1000 <= args.events <= 1000000: ap.error('events must be in [1000,1000000]')
    with tempfile.TemporaryDirectory(prefix='xray-ledger-') as directory:
        os.environ.pop('DATABASE_URL', None)
        os.environ['DB_PATH'] = str(Path(directory)/'ledger.db')
        os.environ['APP_ENV'] = 'test'
        from app import server
        c = sqlite3.connect(os.environ['DB_PATH'])
        c.row_factory = sqlite3.Row
        c.executescript((ROOT/'db/schema.sql').read_text())
        previous = ''
        stamp = '2026-09-16T09:00:00+00:00'
        for i in range(1, args.events+1):
            eid = f'synthetic-{i}'
            head = audit.event_hash(previous, eid, 'synthetic', 'test', 'fixture', 'synthetic', '', stamp)
            c.execute('INSERT INTO audit_events(event_id,actor,action,object_type,object_id,detail,previous_hash,event_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (eid, 'synthetic', 'test', 'fixture', 'synthetic', '', previous, head, stamp))
            c.execute('INSERT INTO audit_checkpoints(event_id,event_count,head_hash,signature,created_at) VALUES(?,?,?,?,?)',
                (eid, i, head, audit.checkpoint_signature(eid, i, head, server.AUDIT_KEY), stamp))
            previous = head
        c.commit()
        with server._AUDIT_PROBE_LOCK:
            server._AUDIT_PROBE_CACHE.update(head=None, events=-1, verified_at=0)
        start = time.perf_counter(); result = server.readiness_verify_audit(c); cold = (time.perf_counter()-start)*1000
        warm = []
        for _ in range(100):
            start = time.perf_counter(); server.readiness_verify_audit(c); warm.append((time.perf_counter()-start)*1000)
        warm.sort()
        out = dict(scope='local synthetic SQLite readiness helper, fixed IDs and clock, no network', events=args.events,
            verified_events=result['events'], cold_ms=round(cold,3), warm_p50_ms=round(statistics.median(warm),3),
            warm_p95_ms=round(warm[94],3), warm_p99_ms=round(warm[98],3),
            note='Full revalidation occurs on head movement or cache expiry; COUNT(*) is not O(1) on PostgreSQL.')
        c.close()
    output = ROOT/'docs/validation/launch-v1-ledger.json'
    output.write_text(json.dumps(out, indent=2)+'\n');print(json.dumps(out, indent=2))
    return 0 if result['events'] == args.events else 1


if __name__ == '__main__': sys.exit(main())
