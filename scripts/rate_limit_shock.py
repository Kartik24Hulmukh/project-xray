#!/usr/bin/env python3
"""Local-only rate-limit shock. Real HTTP 429, backoff metadata, probe recovery."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='xray-rate-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        env = {**os.environ, 'APP_ENV': 'test', 'BIND_HOST': '127.0.0.1', 'PORT': str(port),
               'DB_PATH': str(Path(directory)/'rate.db'), 'PUBLIC_READ_RATE_LIMIT': '1',
               'ADMIN_TOKEN': 'local-synthetic-rate-test', 'MAX_HTTP_WORKERS': '64'}
        env.pop('DATABASE_URL', None); env.pop('XRAY_MAINTENANCE_MODE', None)
        with open(Path(directory)/'server.log', 'w') as log:
            proc = subprocess.Popen([sys.executable, 'app/server.py'], cwd=ROOT, env=env, stdout=log, stderr=log)
            def get(path):
                start = time.perf_counter()
                try:
                    response = urllib.request.urlopen(f'http://127.0.0.1:{port}'+path, timeout=5)
                except urllib.error.HTTPError as error:
                    response = error
                except OSError as error:
                    return dict(status=type(error).__name__, latency_ms=(time.perf_counter()-start)*1000)
                with response:
                    response.read()
                    return dict(status=response.code, retry_after=response.headers.get('Retry-After'),
                                latency_ms=(time.perf_counter()-start)*1000)
            try:
                for _ in range(100):
                    if get('/readyz')['status'] == 200: break
                    if proc.poll() is not None: raise RuntimeError('server exited')
                    time.sleep(.05)
                else: raise RuntimeError('readiness timeout')
                with ThreadPoolExecutor(max_workers=100) as pool:
                    responses = list(pool.map(lambda _: get('/api/projects'), range(200)))
                throttled = [r for r in responses if r['status'] == 429]
                probes = [get('/healthz'), get('/readyz')]
                log.flush()
                tracebacks = Path(directory, 'server.log').read_text().count('Traceback (most recent call last)')
                result = dict(scope='local synthetic SQLite, limit=1/minute, 100 clients, 200 reads',
                    statuses={str(s): sum(r['status'] == s for r in responses) for s in set(r['status'] for r in responses)},
                    probes=probes, tracebacks=tracebacks, server_alive=proc.poll() is None,
                    retry_after_valid=bool(throttled) and all(str(r.get('retry_after', '')).isdigit()
                        and 1 <= int(r['retry_after']) <= 60 for r in throttled))
                result['all_pass'] = (result['server_alive'] and tracebacks == 0 and result['retry_after_valid']
                    and all(r['status'] in (200, 429) for r in responses)
                    and all(r['status'] == 200 and r['latency_ms'] < 200 for r in probes))
            finally:
                proc.terminate()
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait()
    output = ROOT/'docs/validation/launch-v1-rate-shock.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
    return 0 if result['all_pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
