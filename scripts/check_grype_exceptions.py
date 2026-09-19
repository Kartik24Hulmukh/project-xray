#!/usr/bin/env python3
"""CI guard for Grype vulnerability suppressions.

Hardening note (2026-09-13, turn 5): the previous version of this guard only
inspected security/grype-exceptions.yaml, a file that grype never reads. The
blocking-security job runs grype with --config .grype.yaml, so suppressions
could have been added to .grype.yaml with zero governance. This guard now
validates EVERY file that can suppress a finding.

Fails if:
  - a suppression file declares no `# expires: YYYY-MM-DD` header
  - the expiry date has passed (fail closed)
  - more than MAX_CVES CVE IDs are suppressed
  - any suppressed CVE is outside the human-approved set
  - any suppression is not scoped to a single package (blanket ignores)
"""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SUPPRESSION_FILES = [Path('.grype.yaml'), Path('security/grype-exceptions.yaml')]
MAX_CVES = 6
# Approved 2026-09-13 by founder + red-team reviewer.
# Rationale and exposure analysis: docs/security/risk-acceptance-2026-09-13.md
APPROVED_CVES = {
    'CVE-2026-76642',
    'CVE-2026-78408',
    'CVE-2026-78409',
    'CVE-2026-78410',
    # Added 2026-09-19: zlib 1.3.2-r0, no fixed apk available. Exposure analysis:
    # docs/security/risk-acceptance-2026-09-19.md. Founder countersign via PR #52 review.
    'CVE-2026-85091',
}
EXPIRY_RE = re.compile(r'#\s*expires:\s*(\d{4}-\d{2}-\d{2})')
CVE_RE = re.compile(r'CVE-\d{4}-\d+')


def check(path, now):
    text = path.read_text()
    cves = set(CVE_RE.findall(text))
    body = re.sub(r'(?m)^\s*#.*$', '', text)
    active = set(CVE_RE.findall(body))
    if not active:
        print(f'OK: {path} declares no active suppression')
        return 0
    m = EXPIRY_RE.search(text)
    if not m:
        print(f'FAIL: {path} suppresses {sorted(active)} with no "# expires: YYYY-MM-DD" header')
        return 1
    expiry = datetime.strptime(m.group(1), '%Y-%m-%d').replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    if now > expiry:
        print(f'FAIL: {path} risk acceptance expired {expiry.date()} - rebuild on a patched base or renew review')
        return 1
    if len(cves) > MAX_CVES:
        print(f'FAIL: {path} suppresses {len(cves)} CVEs, maximum allowed is {MAX_CVES}: {sorted(cves)}')
        return 1
    unapproved = active - APPROVED_CVES
    if unapproved:
        print(f'FAIL: {path} suppresses unapproved CVE IDs: {sorted(unapproved)}')
        return 1
    blocks = [b for b in body.split('- vulnerability:') if CVE_RE.search(b)]
    for block in blocks:
        if 'name:' not in block:
            print(f'FAIL: {path} contains a suppression that is not scoped to a package: {block.strip()[:80]}')
            return 1
    days = (expiry - now).days
    print(f'OK: {path} - {len(active)} package-scoped suppressions, expires in {days} days ({expiry.date()})')
    return 0


def main():
    now = datetime.now(timezone.utc)
    rc = 0
    seen = False
    for path in SUPPRESSION_FILES:
        if not path.exists():
            continue
        seen = True
        rc |= check(path, now)
    if not seen:
        print('OK: no suppression file present')
    return rc


if __name__ == '__main__':
    sys.exit(main())
