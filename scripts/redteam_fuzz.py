#!/usr/bin/env python3
"""Red-team fuzz pass: adversarial inputs against a local synthetic server.
Asserts: no 5xx, auth enforced, no path traversal, bounded payloads rejected."""
import json, os, socket, subprocess, sys, tempfile, time, urllib.request, urllib.error
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DD = chr(46)*2

def req(base, method, path, body=None, headers=None, timeout=15):
    h = {'Content-Type': 'application/json'}
    h.update(headers or {})
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    r = urllib.request.Request(base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200]
    except Exception as e:
        return -1, repr(e).encode()[:200]

def main():
    with tempfile.TemporaryDirectory(prefix='xray-fuzz-') as d:
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        base = 'http://127.0.0.1:%d' % port
        token = 'local-fuzz-admin-only'
        env = {**os.environ, 'DB_PATH': str(Path(d)/'fuzz.db'), 'APP_ENV': 'test', 'PORT': str(port), 'BIND_HOST': '127.0.0.1', 'ADMIN_TOKEN': token}
        env.pop('DATABASE_URL', None)
        log = open(Path(d)/'server.log', 'w')
        proc = subprocess.Popen([sys.executable, 'app/server.py'], cwd=ROOT, env=env, stdout=log, stderr=log)
        try:
            for _ in range(60):
                if req(base, 'GET', '/health')[0] == 200: break
                time.sleep(0.25)
            auth = {'Authorization': 'Bearer ' + token}
            cases = []
            def check(name, got, allowed):
                cases.append({'case': name, 'status': got, 'allowed': sorted(allowed), 'pass': got in allowed})
            check('malformed_json', req(base, 'POST', '/api/projects', b'{bad json!!', auth)[0], {400})
            check('empty_body', req(base, 'POST', '/api/projects', b'', auth)[0], {400})
            check('huge_payload_2mb', req(base, 'POST', '/api/projects', b'{"name":"' + b'A'*2000000 + b'"}', auth)[0], {400, 413})
            check('no_auth_write', req(base, 'POST', '/api/projects', {'name': 'x'})[0], {401, 403})
            check('bad_token', req(base, 'POST', '/api/projects', {'name': 'x'}, {'Authorization': 'Bearer wrong'})[0], {401, 403})
            check('traversal_dotdot', req(base, 'GET', '/static/' + DD + '/app/server.py')[0], {400, 403, 404})
            check('traversal_encoded', req(base, 'GET', '/static/%2e%2e/%2e%2e/etc/passwd')[0], {400, 403, 404})
            check('null_byte_path', req(base, 'GET', '/static/%00')[0], {400, 403, 404})
            check('unknown_method', req(base, 'DELETE', '/health')[0], {400, 404, 405, 501})
            check('unicode_rtl_name', req(base, 'POST', '/api/projects', {'name': '\u202eevil'*10}, auth)[0], {201, 400})
            check('nested_bomb', req(base, 'POST', '/api/projects', b'['*30000 + b']'*30000, auth)[0], {400, 413})
            check('type_confusion', req(base, 'POST', '/api/projects', {'name': {'x': 1}}, auth)[0], {400})
            check('sql_meta_in_name', req(base, 'POST', '/api/projects', {'name': "a'; DROP TABLE projects;--"}, auth)[0], {201, 400})
            check('idem_key_overlong', req(base, 'POST', '/api/projects', {'name': 'k'}, {**auth, 'Idempotency-Key': 'K'*5000})[0], {201, 400})
            health = req(base, 'GET', '/health')[0]
            bad = [c for c in cases if c['status'] in (500, 502, 503, 504, -1)]
            result = {'scope': 'local synthetic red-team fuzz', 'cases': cases, 'health_after_fuzz': health, 'no_5xx_or_crash': not bad, 'all_pass': all(c['pass'] for c in cases) and health == 200}
            print(json.dumps(result, indent=1))
            return 0 if result['all_pass'] else 1
        finally:
            proc.terminate(); proc.wait(timeout=10)

if __name__ == '__main__':
    sys.exit(main())
