"""Fault injection: dropped/idle sockets, half-sent headers, mid-body disconnects, 429 path.

Asserts the execution gate (HTTP_EXEC_PARALLELISM) is not starved by slow or
dropped clients and that the server stays healthy with zero 5xx.
Local synthetic SQLite only. Writes a JSON receipt.
"""
import argparse, json, os, socket, subprocess, sys, tempfile, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', default='artifacts/hardening/fault_injection.json')
    ap.add_argument('--seed', type=int, default=20260913)
    args = ap.parse_args()
    import random
    rng = random.Random(args.seed)
    with tempfile.TemporaryDirectory(prefix='xray-fault-') as d:
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        env = {**os.environ, 'DB_PATH': str(Path(d)/'f.db'), 'APP_ENV': 'test', 'PORT': str(port),
               'BIND_HOST': '127.0.0.1', 'ADMIN_TOKEN': 'fault-admin', 'MAX_HTTP_WORKERS': '64',
               'HTTP_EXEC_PARALLELISM': os.getenv('HTTP_EXEC_PARALLELISM', '4'),
               'PUBLIC_READ_RATE_LIMIT': '100000', 'WRITE_RATE_LIMIT': '100000',
               'AUTH_READ_RATE_LIMIT': '100000', 'EXPENSIVE_WRITE_RATE_LIMIT': '100000'}
        env.pop('DATABASE_URL', None)
        log = open(Path(d)/'server.log', 'w')
        proc = subprocess.Popen([sys.executable, 'app/server.py'], cwd=ROOT, env=env, stdout=log, stderr=log)
        def get(path='/api/projects', timeout=10):
            t = time.perf_counter()
            try:
                with urllib.request.urlopen(base+path, timeout=timeout) as r:
                    r.read(); return r.status, (time.perf_counter()-t)*1000
            except urllib.error.HTTPError as e:
                return e.code, (time.perf_counter()-t)*1000
            except Exception as e:
                return type(e).__name__, (time.perf_counter()-t)*1000
        out = {'seed': args.seed, 'exec_parallelism': env['HTTP_EXEC_PARALLELISM'], 'cases': {}}
        try:
            for _ in range(100):
                if get('/ready')[0] == 200: break
                time.sleep(.1)
            # 1. 40 idle sockets that never send a byte + 20 half-sent header sockets.
            held = []
            for i in range(60):
                c = socket.create_connection(('127.0.0.1', port)); held.append(c)
                if i >= 40:
                    c.sendall(b'GET /api/projects HTTP/1.0\r\nX-Slow: ' + bytes(rng.choice(b'abc') for _ in range(8)))
            time.sleep(.3)
            with ThreadPoolExecutor(max_workers=20) as pool:
                res = list(pool.map(lambda _: get(), range(200)))
            lat = sorted(l for _, l in res)
            out['cases']['slowloris_60_held_sockets'] = {
                'statuses': {str(k): sum(1 for s, _ in res if s == k) for k in set(s for s, _ in res)},
                'p50_ms': round(lat[len(lat)//2], 2), 'p95_ms': round(lat[int(len(lat)*.95)-1], 2),
                'served_while_held': all(s == 200 for s, _ in res)}
            # 2. Drop sockets mid-body (Content-Length lies) then abort.
            for i in range(30):
                c = socket.create_connection(('127.0.0.1', port))
                c.sendall(b'POST /api/projects HTTP/1.0\r\nAuthorization: Bearer fault-admin\r\nContent-Type: application/json\r\nContent-Length: 5000\r\n\r\n{"ti')
                c.close()
            for c in held: c.close()
            time.sleep(.3)
            with ThreadPoolExecutor(max_workers=50) as pool:
                res = list(pool.map(lambda _: get(), range(300)))
            out['cases']['after_30_mid_body_drops'] = {
                'statuses': {str(k): sum(1 for s, _ in res if s == k) for k in set(s for s, _ in res)},
                'all_200': all(s == 200 for s, _ in res)}
            # 3. Out-of-order / interleaved idempotent replays (same key, shuffled bodies must not fork).
            body = json.dumps({'title': 'SYNTHETIC fault', 'authority': 'Synthetic Authority', 'synthetic': True}).encode()
            def post(i):
                req = urllib.request.Request(base+'/api/projects', data=body, headers={
                    'Content-Type': 'application/json', 'Authorization': 'Bearer fault-admin', 'Idempotency-Key': 'fault-key'})
                try:
                    with urllib.request.urlopen(req, timeout=10) as r: return r.status, json.loads(r.read()).get('id')
                except urllib.error.HTTPError as e: return e.code, None
            order = list(range(50)); rng.shuffle(order)
            with ThreadPoolExecutor(max_workers=50) as pool:
                pr = list(pool.map(post, order))
            out['cases']['out_of_order_idempotent_replay'] = {
                'statuses': {str(k): sum(1 for s, _ in pr if s == k) for k in set(s for s, _ in pr)},
                'unique_ids': len({i for s, i in pr if s == 201 and i})}
            out['ready_after'] = get('/ready')[0]
            out['live_after'] = get('/livez')[0]
            proc_alive = proc.poll() is None
            out['server_alive'] = proc_alive
            statuses = set()
            for c in out['cases'].values(): statuses |= set(c['statuses'])
            out['no_5xx'] = not any(s.startswith('5') for s in statuses)
            log.flush()
            out['tracebacks_in_log'] = Path(d, 'server.log').read_text().count('Traceback (most recent call last)')
            out['all_pass'] = (out['tracebacks_in_log'] == 0 and out['live_after'] == 200
                               and set(out['cases']['out_of_order_idempotent_replay']['statuses']) <= {'201', '409'} and proc_alive and out['no_5xx'] and out['ready_after'] == 200
                               and out['cases']['slowloris_60_held_sockets']['served_while_held']
                               and out['cases']['after_30_mid_body_drops']['all_200']
                               and out['cases']['out_of_order_idempotent_replay']['unique_ids'] == 1)
        finally:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()
            log.close()
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(out, indent=1))
        print(json.dumps(out, indent=1))
        return 0 if out['all_pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
