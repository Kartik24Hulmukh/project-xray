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

def launch_gates(results):
    """Fail closed: old safety_pass permitted 503 and ignored resource ceilings."""
    allowed = {'baseline_reads': {'200'}, '100_client_reads': {'200'},
               '100_client_writes': {'201'}, '100_client_idempotency_race': {'201', '409'}}
    capacity = len(results['phases']) == 4 and all(
        p['name'] in allowed and set(p['statuses']) <= allowed[p['name']]
        and sum(p['statuses'].values()) == p['requests'] for p in results['phases'])
    rss = results.get('server_peak_rss_kb')
    return dict(statuses_pass=capacity,
                ram_pass=isinstance(rss, int) and 0 < rss <= 128 * 1024,
                recovery_pass=all(results.get(k, {}).get('status') == 200
                    and 0 <= results[k]['latency_ms'] < 200 for k in ('health_recovery', 'ready_recovery')),
                process_pass=results.get('server_alive') is True and results.get('tracebacks_in_log') == 0)


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
                   'worker_limit': 64, 'exec_parallelism': os.getenv('HTTP_EXEC_PARALLELISM', 'default'), 'phases': []}
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
            status = Path(f'/proc/{proc.pid}/status').read_text()
            results['server_baseline_rss_kb'] = int(next(l for l in status.splitlines() if l.startswith('VmRSS:')).split()[1])
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
            for key, path in [('health_recovery', '/healthz'), ('ready_recovery', '/readyz')]:
                code, elapsed, _ = request(path)
                results[key] = {'status': code, 'latency_ms': round(elapsed * 1000, 3)}
            results['readiness_after_load'] = results['ready_recovery']['status']
            results['server_alive'] = proc.poll() is None
            log.flush()
            results['tracebacks_in_log'] = (Path(directory)/'server.log').read_text().count('Traceback (most recent call last)')
            try:  # peak resident memory of the server process (Linux only)
                status = Path(f'/proc/{proc.pid}/status').read_text()
                results['server_peak_rss_kb'] = int(next(l for l in status.splitlines() if l.startswith('VmHWM:')).split()[1])
                results['server_threads'] = int(next(l for l in status.splitlines() if l.startswith('Threads:')).split()[1])
            except (OSError, StopIteration):
                results['server_peak_rss_kb'] = None
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
            results['launch_gates'] = launch_gates(results)
            results['safety_pass'] = results['safety_pass'] and all(results['launch_gates'].values())
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
