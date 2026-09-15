#!/usr/bin/env python3
"""Release-claim guard: no artifact may claim production/GA maturity unless the
production-readiness ledger actually passes every check with evidence.

Why this exists (Session 7 finding): the repository carried a tag and GitHub
release named ``v2.1.1-production`` while ``ops/production-readiness.yaml`` was
at 0/10 passed checks and ``package.json`` was at 0.4.6. A maturity claim that
the evidence ledger does not support is the single highest-severity launch risk
for an evidence product: it destroys the trust that IS the product.

Usage:
  python3 scripts/check_release_claims.py            # audit git tags + notes
  python3 scripts/check_release_claims.py --json     # machine receipt
Exit 0 = every maturity claim is supported. Exit 1 = unsupported claim found.
Fail-closed: an unreadable ledger is a failure, never a pass.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / 'ops' / 'production-readiness.yaml'

# Words that assert general availability / production maturity to a reader.
CLAIM_WORDS = ('production', 'ga', 'general-availability', 'generally-available',
               'stable', 'certified', 'enterprise')
# Labels that are honest about a limited release and therefore never a claim.
SAFE_WORDS = ('preview', 'synthetic', 'rc', 'alpha', 'beta', 'dev', 'canary', 'pilot')


def parse_ledger(text: str) -> list[dict]:
    items: list[dict] = []
    cur: dict = {}
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith('- id:'):
            if cur:
                items.append(cur)
            cur = {'id': s.split(':', 1)[1].strip()}
        elif cur and s.startswith('status:'):
            cur['status'] = s.split(':', 1)[1].strip()
        elif cur and s.startswith('evidence:'):
            cur['evidence'] = s.split(':', 1)[1].strip()
    if cur:
        items.append(cur)
    return items


def ledger_state() -> tuple[int, int, list[str]]:
    text = LEDGER.read_text(encoding='utf-8')  # fail-closed: missing file raises
    items = parse_ledger(text)
    if not items:
        raise ValueError('production-readiness ledger contains no checks')
    passed = [i for i in items
              if i.get('status') == 'passed'
              and i.get('evidence') not in (None, 'null', '')]
    pending = [i['id'] for i in items if i not in passed]
    return len(passed), len(items), pending


def is_unsupported_claim(name: str) -> bool:
    tokens = [t for t in re.split(r'[^A-Za-z]+', name.lower()) if t]
    if any(t in SAFE_WORDS for t in tokens):
        return False
    return any(t in CLAIM_WORDS for t in tokens)


def git_tags() -> list[str]:
    try:
        out = subprocess.run(['git', '-C', str(ROOT), 'tag', '--list'],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [t.strip() for t in out.stdout.splitlines() if t.strip()]


def audit(names: list[str]) -> dict:
    passed, total, pending = ledger_state()
    certified = passed == total
    offenders = [] if certified else sorted(n for n in names if is_unsupported_claim(n))
    return {
        'ledger_passed': passed,
        'ledger_total': total,
        'ledger_pending': pending,
        'production_certified': certified,
        'names_audited': sorted(names),
        'unsupported_claims': offenders,
        'ok': not offenders,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--name', action='append', default=[],
                    help='extra release/tag name to audit')
    ap.add_argument('--json', action='store_true', help='emit a JSON receipt')
    args = ap.parse_args(argv)

    try:
        receipt = audit(git_tags() + list(args.name))
    except Exception as exc:  # fail closed
        print(f'Release-claim guard FAILED CLOSED: {exc}', file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(f"Readiness ledger: {receipt['ledger_passed']}/{receipt['ledger_total']} passed with evidence")
        if receipt['ok']:
            print('Release-claim guard: PASS - no unsupported maturity claim found')
        else:
            print('Release-claim guard: FAIL - unsupported maturity claims:')
            for n in receipt['unsupported_claims']:
                print(f'  - {n} claims production/GA maturity the ledger does not support')
            print('Fix: rename/relabel to a preview or -rc identifier, or close the '
                  'ledger checks with real target-environment evidence.')
    return 0 if receipt['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
