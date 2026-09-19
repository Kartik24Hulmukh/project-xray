#!/usr/bin/env python3
"""Bounded local synthetic validation; skipped target tests are NOT production proof."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def run(cmd, timeout=180):
    start = time.monotonic()
    proc = subprocess.Popen([sys.executable] + cmd, cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        # Include descendants: an orphaned stress server can retain pipes/ports.
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate()
    text = stdout + '\n' + stderr
    result = {'cmd': cmd, 'rc': 124 if timed_out else proc.returncode,
              'timed_out': timed_out, 'seconds': round(time.monotonic()-start, 2),
              'tail': text.strip()[-2000:]}
    match = re.search(r'Ran (\d+) tests?', stderr)
    if match:
        skipped = re.search(r'skipped=(\d+)', stderr)
        result.update(tests_run=int(match[1]), tests_skipped=int(skipped[1]) if skipped else 0)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/hardening/validation.json')
    parser.add_argument('--requests', type=int, default=20000)
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args(argv)
    if not 100 <= args.requests <= 20000 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('requests must be 100..20000 and timeout must be positive')
    out = ROOT / args.output
    out.parent.mkdir(parents=True, exist_ok=True)
    commands = {
        'unit_integration': ['-m', 'unittest', 'discover', '-s', 'tests'],
        'stress_100_clients': ['scripts/stress_local.py', '--requests', str(args.requests), '--output', str(out.with_name(out.stem+'-stress.json'))],
        'fault_injection': ['scripts/fault_injection.py', '--seed', '20260913', '--output', str(out.with_name(out.stem+'-fault.json'))],
        'redteam_fuzz': ['scripts/redteam_fuzz.py'],
        'rate_limit_shock': ['scripts/rate_limit_shock.py'],
        'release_claim_guard': ['scripts/check_release_claims.py'],
        'smoke_e2e': ['scripts/smoke_e2e.py'],
    }
    gates = {}
    for name, cmd in commands.items():
        gates[name] = run(cmd, args.timeout)
        complete = len(gates) == len(commands)
        receipt = {'service': 'project-xray', 'scope': 'local synthetic SQLite; no external target',
                   'complete': complete, 'all_gates_pass': complete and all(g['rc'] == 0 for g in gates.values()),
                   'production_ready': False, 'gates': gates}
        # Checkpoint after every gate, without leaving a partially written JSON.
        temp = out.with_suffix('.tmp')
        temp.write_text(json.dumps(receipt, indent=2)+'\n')
        temp.replace(out)
    print(json.dumps({'all_gates_pass': receipt['all_gates_pass'], 'receipt': str(out)}))
    return 0 if receipt['all_gates_pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
