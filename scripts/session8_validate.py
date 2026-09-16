#!/usr/bin/env python3
"""Session-8 launch validation orchestrator: one command, one receipt.

Runs, in order: unit/integration suite, 100x local stress, fault injection,
red-team fuzz and the fail-closed release-claim guard; writes a single JSON
receipt and exits non-zero if ANY gate fails. LOCAL synthetic only.
"""
import json, subprocess, sys, time, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
def run(cmd):
    t0 = time.time()
    r = subprocess.run([sys.executable] + cmd, cwd=ROOT, capture_output=True, text=True)
    return {'cmd': ' '.join(cmd), 'rc': r.returncode, 'seconds': round(time.time()-t0, 2), 'tail': (r.stdout or r.stderr).strip()[-400:]}
gates = {}
gates['unit_integration'] = run(['-m', 'unittest', 'discover', '-s', 'tests'])
gates['stress_100x'] = run(['scripts/stress_local.py', '--requests', '20000', '--output', 'artifacts/hardening/stress_session8_validate.json'])
gates['fault_injection'] = run(['scripts/fault_injection.py', '--output', 'artifacts/hardening/fault_session8_validate.json'])
gates['redteam_fuzz'] = run(['scripts/redteam_fuzz.py'])
gates['release_claim_guard'] = run(['scripts/check_release_claims.py'])
ok = all(g['rc'] == 0 for g in gates.values())
receipt = {'service': 'project-xray', 'scope': 'local synthetic SQLite; no external target', 'all_gates_pass': ok, 'gates': gates}
out = ROOT / 'docs/validation/session8/session8_validate_receipt.json'
out.write_text(json.dumps(receipt, indent=1))
print(json.dumps({'all_gates_pass': ok, 'receipt': str(out.relative_to(ROOT))}))
sys.exit(0 if ok else 1)
