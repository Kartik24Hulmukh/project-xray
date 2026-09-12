#!/usr/bin/env python3
"""Bounded LOCAL-ONLY synthetic workload; no external target option.
Compares 1 versus 100 concurrent clients, not 100x production capacity.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/hardening/stress.json')
    parser.add_argument('--requests', type=int, default=2000)
    args = parser.parse_args()
    if not 100 <= args.requests <= 20000:
        parser.error('--requests must be between 100 and 20000')
    with tempfile.TemporaryDirectory(prefix='xray-stress-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        token = 'local-stress-admin-only'
        env = {**os.environ, 'DB_PATH': str(Path(directory)/'stress.db'),
               'APP_ENV': 'test', 'PORT': str(port), 'BIND_HOST': '127.0.0.1',
               'ADMIN_TOKEN': token, 'MAX_HTTP_WORKERS': '64',
               'PUBLIC_READ_RATE_LIMIT': '100000', 'WRITE_RATE_LIMIT': '100000',
               'AUTH_READ_RATE_LIMIT': '100000', 'EXPENSIVE_WRITE_RATE_LIMIT': '100000'}
        env.pop('DATABASE_URL', None)
        env.pop('XRAY_MAINTENANCE_MODE', None)
        log = open(Path(directory)/'server.log', 'w')
        proc = subprocess.Popen([sys.executable, 'app/server.py'], cwd=ROOT, env=env,
                                stdout=log, stderr=log)
        def request(path, body=None, key=None):
            headers = {'Content-Type': 'application/json'}
            if body is not None:
                headers['Authorization'] = 'Bearer '+token
            if key:
                headers['Idempotency-Key'] = key
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base+path, data=data, headers=headers)
            start = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    raw = response.read()
                    return response.status, time.perf_counter()-start, raw
            except urllib.error.HTTPError as error:
                return error.code, time.perf_counter()-start, error.read()
            except Exception as error:
                return type(error).__name__, time.perf_counter()-start, b''
        results = {'scope': 'local synthetic SQLite; rate limits raised for throughput; no external services',
                   'worker_limit': 64, 'phases': []}
        def phase(name, clients, count, work):
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=clients) as pool:
                responses = list(pool.map(work, range(count)))
            elapsed = time.perf_counter()-start
            latencies = sorted(item[1]*1000 for item in responses)
            statuses = dict(Counter(str(item[0]) for item in responses))
            record = {'name': name, 'clients': clients, 'requests': count,
                      'seconds': round(elapsed,3), 'requests_per_second': round(count/elapsed,2),
                      'p50_ms': round(statistics.median(latencies),2),
                      'p95_ms': round(latencies[int((count-1)*.95)],2),
                      'p99_ms': round(latencies[int((count-1)*.99)],2), 'statuses': statuses}
            results['phases'].append(record)
            return responses
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError('server exited before readiness')
                if request('/ready')[0] == 200:
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('server readiness timeout')
            phase('baseline_reads', 1, 100, lambda _: request('/api/projects'))
            phase('100_client_reads', 100, args.requests, lambda _: request('/api/projects'))
            writes = phase('100_client_writes', 100, 300, lambda i: request('/api/projects',
                {'title': f'SYNTHETIC stress {i}', 'authority': 'Synthetic Authority',
                 'synthetic': True}, f'write-{i}'))
            idem_body = {'title': 'SYNTHETIC replay', 'authority': 'Synthetic Authority', 'synthetic': True}
            replays = phase('100_client_idempotency_race', 100, 100,
                            lambda _: request('/api/projects', idem_body, 'same-key'))
            successful_ids = {json.loads(raw)['id'] for code, _, raw in replays if code == 201}
            results['idempotency_unique_success_ids'] = len(successful_ids)
            results['readiness_after_load'] = request('/ready')[0]
            # Direct database verification is local-only and checks every audit checkpoint.
            import sqlite3
            sys.path.insert(0, str(ROOT))
            from app.audit import verify
            connection = sqlite3.connect(env['DB_PATH'])
            connection.row_factory = sqlite3.Row
            results['integrity_check'] = connection.execute('PRAGMA integrity_check').fetchone()[0]
            results['audit'] = verify(connection, env.get('AUDIT_HMAC_KEY', 'development-audit-key-not-for-production'))
            results['persisted_projects'] = connection.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
            results['expected_projects'] = sum(code == 201 for code, _, _ in writes) + len(successful_ids)
            connection.close()
            unexpected = any(set(p['statuses']) - {'200','201','409','503'} for p in results['phases'])
            results['safety_pass'] = (not unexpected and len(successful_ids) == 1
                and results['readiness_after_load'] == 200 and results['integrity_check'] == 'ok'
                and results['persisted_projects'] == results['expected_projects'])
            results['zero_rejection_capacity_pass'] = all(set(p['statuses']) <= {'200','201'} for p in results['phases'])
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            log.close()
        output = ROOT/args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(results, indent=2)+'\n')
        print(json.dumps(results, indent=2))
        return 0 if results['safety_pass'] else 1

if __name__ == '__main__':
    sys.exit(main())
