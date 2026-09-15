#!/usr/bin/env python3
"""Bounded same-process synthetic read soak; never accepts an external target.
Seed controls submission order, not OS scheduling or generated server IDs.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=120)
    parser.add_argument('--seed', type=int, default=20260913)
    parser.add_argument('--output', default='artifacts/hardening/soak.json')
    args = parser.parse_args()
    if not 5 <= args.seconds <= 3600:
        parser.error('--seconds must be between 5 and 3600')
    rng = random.Random(args.seed)
    out = {'seed': args.seed, 'python': platform.python_version(),
           'scope': 'same-process loopback SQLite synthetic read soak; 100 clients',
           'async_event_loop_latency': None,
           'async_event_loop_note': 'Threaded synchronous HTTP server, no async event loop',
           'samples': [], 'errors': {}, 'requests': 0}
    with tempfile.TemporaryDirectory(prefix='xray-soak-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, DB_PATH=str(Path(directory)/'soak.db'),
                   APP_ENV='test', PORT=str(port), BIND_HOST='127.0.0.1',
                   MAX_HTTP_WORKERS='64', HTTP_EXEC_PARALLELISM='4',
                   PUBLIC_READ_RATE_LIMIT='10000000', PYTHONHASHSEED='0')
        env.pop('DATABASE_URL', None)
        env.pop('XRAY_MAINTENANCE_MODE', None)
        base = f'http://127.0.0.1:{port}'
        def get(path):
            start = time.monotonic()
            try:
                with urllib.request.urlopen(base+path, timeout=5) as response:
                    response.read()
                    status = str(response.status)
            except Exception as error:
                status = type(error).__name__
            return status, (time.monotonic()-start)*1000
        with open(Path(directory)/'server.log', 'w+') as log:
            process = subprocess.Popen([sys.executable, 'app/server.py'], cwd=ROOT,
                                       env=env, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError('server exited before readiness')
                    if get('/readyz')[0] == '200':
                        break
                    time.sleep(.05)
                else:
                    raise RuntimeError('readiness timeout')
                start = time.monotonic()
                order_hash = hashlib.sha256()
                errors = Counter()
                with ThreadPoolExecutor(max_workers=100) as pool:
                    while time.monotonic()-start < args.seconds:
                        paths = ['/api/projects']*990 + ['/healthz']*10
                        rng.shuffle(paths)
                        order_hash.update(json.dumps(paths).encode())
                        batch_start = time.monotonic()
                        results = list(pool.map(get, paths))
                        statuses = Counter(status for status, _ in results)
                        errors.update({k: v for k, v in statuses.items() if k != '200'})
                        lat = sorted(ms for _, ms in results)
                        status = Path(f'/proc/{process.pid}/status').read_text().splitlines()
                        resources = {line.split(':')[0]: int(line.split()[1]) for line in status
                                     if line.startswith(('VmRSS:', 'VmHWM:', 'Threads:'))}
                        out['samples'].append({'elapsed_s': round(time.monotonic()-start, 3),
                            'p50_ms': lat[499], 'p95_ms': lat[949], 'p99_ms': lat[989],
                            'rps': 1000/(time.monotonic()-batch_start), **resources})
                        out['requests'] += 1000
                out['elapsed_s'] = time.monotonic()-start
                out['submission_order_sha256'] = order_hash.hexdigest()
                out['errors'] = dict(errors)
                out['ready_after'] = get('/readyz')[0]
                stopped = time.monotonic()
                process.terminate()
                process.wait(timeout=15)
                out['shutdown_seconds'] = time.monotonic()-stopped
                out['exit_code'] = process.returncode
                log.flush(); log.seek(0)
                lines = log.read().splitlines()
                out['shutdown_receipt'] = next((json.loads(line) for line in reversed(lines)
                    if '"event": "shutdown"' in line), None)
                out['all_pass'] = (not errors and out['ready_after'] == '200'
                    and out['exit_code'] == 0 and out['shutdown_receipt'] is not None
                    and out['shutdown_receipt']['unfinished_requests'] == 0)
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2)+'\n')
    print(json.dumps({k: v for k, v in out.items() if k != 'samples'}, indent=2))
    return 0 if out['all_pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
