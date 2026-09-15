#!/usr/bin/env python3
"""Large-ledger readiness benchmark for /readyz single-flight verification.

Builds a synthetic tamper-evident ledger, then measures:
  * baseline: the legacy full-chain walk executed inside a single probe;
  * hardened: bounded streaming segments behind the single-flight coordinator,
    under a concurrent cold-start probe storm.
Reports P50/P95/P99 probe latency, convergence probe count, duplicate-scan
count and peak RSS. Deterministic: no randomness, fixed synthetic corpus.
"""
import argparse
import json
import os
import resource
import sqlite3
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import audit  # noqa: E402
from app import server  # noqa: E402

KEY = os.getenv('AUDIT_HMAC_KEY', 'development-audit-key-not-for-production')
SCHEMA = '''CREATE TABLE audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, actor TEXT, action TEXT, object_type TEXT, object_id TEXT, detail TEXT, previous_hash TEXT, event_hash TEXT, created_at TEXT);
CREATE TABLE audit_checkpoints(id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, event_count INTEGER, head_hash TEXT, signature TEXT, created_at TEXT);
CREATE INDEX idx_audit_checkpoints_event ON audit_checkpoints(event_id);'''
CREATED = '2026-09-15T00:00:00+00:00'


def build(path, events):
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    previous = ''
    rows = []
    checkpoints = []
    for i in range(events):
        eid = f'evt_{i:09d}'
        head = audit.event_hash(previous, eid, 'bench', 'create', 'claim', f'clm_{i}', '', CREATED)
        rows.append((eid, 'bench', 'create', 'claim', f'clm_{i}', '', previous, head, CREATED))
        checkpoints.append((eid, i + 1, head, audit.checkpoint_signature(eid, i + 1, head, KEY), CREATED))
        previous = head
    c.executemany('INSERT INTO audit_events(event_id,actor,action,object_type,object_id,detail,previous_hash,event_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)', rows)
    c.executemany('INSERT INTO audit_checkpoints(event_id,event_count,head_hash,signature,created_at) VALUES(?,?,?,?,?)', checkpoints)
    c.commit()
    c.close()
    return previous


def conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def pct(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((len(ordered) - 1) * q)))
    return round(ordered[idx], 3)


def rss_mib():
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--events', type=int, default=100000)
    ap.add_argument('--clients', type=int, default=32)
    ap.add_argument('--probes', type=int, default=40)
    ap.add_argument('--budget-ms', type=float, default=120.0)
    ap.add_argument('--out', default='')
    args = ap.parse_args()

    server.AUDIT_KEY = KEY
    tmp = tempfile.TemporaryDirectory()
    path = os.path.join(tmp.name, 'ledger.db')
    t0 = time.monotonic()
    build(path, args.events)
    build_ms = round((time.monotonic() - t0) * 1000.0, 3)
    rss_start = rss_mib()

    baseline_conn = conn(path)
    t0 = time.monotonic()
    baseline_state = audit.verify(baseline_conn, KEY)
    baseline_ms = round((time.monotonic() - t0) * 1000.0, 3)
    baseline_conn.close()
    rss_baseline = rss_mib()

    server.reset_readiness_verifier()
    before = server.metrics_snapshot()
    latencies = []
    lock = threading.Lock()
    ready_flag = threading.Event()
    start_gate = threading.Barrier(args.clients)
    errors = []

    def client():
        c = conn(path)
        local = []
        try:
            start_gate.wait()
            for _ in range(args.probes):
                t = time.monotonic()
                try:
                    server.readiness_verify_audit(c, budget_ms=args.budget_ms)
                    ready_flag.set()
                except server.AuditVerificationPending:
                    pass
                except Exception as exc:  # pragma: no cover
                    errors.append(repr(exc))
                local.append((time.monotonic() - t) * 1000.0)
                if ready_flag.is_set() and len(local) > 3:
                    break
        finally:
            c.close()
            with lock:
                latencies.extend(local)

    threads = [threading.Thread(target=client) for _ in range(args.clients)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall_ms = round((time.monotonic() - t0) * 1000.0, 3)
    after = server.metrics_snapshot()
    rss_peak = rss_mib()

    scanned = after['readyz_verify_events_scanned'] - before['readyz_verify_events_scanned']
    steady_conn = conn(path)
    steady = []
    for _ in range(200):
        t = time.monotonic()
        server.readiness_verify_audit(steady_conn, budget_ms=args.budget_ms)
        steady.append((time.monotonic() - t) * 1000.0)
    steady_conn.close()

    report = {
        'events': args.events,
        'clients': args.clients,
        'budget_ms': args.budget_ms,
        'batch': server.READYZ_VERIFY_BATCH,
        'build_ms': build_ms,
        'baseline_full_walk_ms': baseline_ms,
        'baseline_head': baseline_state['head'][:16],
        'cold_probe_p50_ms': pct(latencies, 0.50),
        'cold_probe_p95_ms': pct(latencies, 0.95),
        'cold_probe_p99_ms': pct(latencies, 0.99),
        'cold_probe_max_ms': round(max(latencies), 3) if latencies else 0.0,
        'cold_probes': len(latencies),
        'convergence_wall_ms': wall_ms,
        'steady_probe_p50_ms': pct(steady, 0.50),
        'steady_probe_p99_ms': pct(steady, 0.99),
        'events_scanned_total': scanned,
        'duplicate_scan_ratio': round(scanned / float(args.events), 3),
        'coalesced_probes': after['readyz_verify_coalesced'] - before['readyz_verify_coalesced'],
        'verifications_started': after['readyz_verify_inflight'] - before['readyz_verify_inflight'],
        'verifications_completed': after['readyz_verify_completed'] - before['readyz_verify_completed'],
        'budget_exhausted': after['readyz_verify_budget_exhausted'] - before['readyz_verify_budget_exhausted'],
        'rss_start_mib': rss_start,
        'rss_after_baseline_mib': rss_baseline,
        'rss_peak_mib': rss_peak,
        'errors': errors,
        'ready_reached': ready_flag.is_set(),
    }
    report['pass'] = bool(
        not errors
        and report['ready_reached']
        and report['cold_probe_max_ms'] < 200.0
        and report['steady_probe_p99_ms'] < 200.0
        and report['duplicate_scan_ratio'] <= 1.5
        and report['rss_peak_mib'] <= 128.0
    )
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, 'w') as fh:
            json.dump(report, fh, indent=2)
            fh.write('\n')
    tmp.cleanup()
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
