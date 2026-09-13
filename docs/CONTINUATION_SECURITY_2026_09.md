# Continuation: security gate closure, 13 September 2026

Base: `b8aefc913bdb39424e7daf9f66f416a7ede96bfe`.

## Newly reproduced defects and acceptance tests

1. **Authentication replay cache evicts live entries.** At 4,096 unique valid assertions, an earlier still-valid signed assertion can be accepted again. This is not signature forgery, but violates consume-once semantics under legitimate high load. Acceptance: saturation rejects new admissions, preserves every live replay record, reclaims expired entries out of order, and 1,000 concurrent attempts with a 32-entry test cap admit exactly 32. Test the actual gateway verifier as well as cache helper. Fixed by bounded, locked, fail-closed admission.
2. **Strict JSON accepts exponent overflow.** `1e400`, `-1e400`, and nested values parse to infinities despite NaN/Infinity literal rejection. Acceptance: each raises ValueError, normal finite numbers remain accepted. Fixed via finite parse_float.
3. **The previously pending main security job actually failed.** Job [103718178626](https://github.com/Kartik24Hulmukh/project-xray/actions/runs/34755157691/job/103718178626) completed at 11:45 UTC. Grype found HIGH CVE-2026-76642, CVE-2026-78409, CVE-2026-78408 and CVE-2026-78410 in inherited `libuuid 2.42.1-r0`; reported fixed versions include `2.42.3-r1`. Installing dependencies alone does not upgrade inherited packages. Refresh both Alpine stages with `apk upgrade --no-cache`. Acceptance requires **remote container build and blocking-security success**, not a Dockerfile string test alone. No vulnerability suppressions added or severity thresholds lowered.

The first regression run on unchanged application code failed six assertions/subtests; the fixes passed all initial regressions. Expanded suite: 222 discovered, 215 passed, seven PostgreSQL-only tests skipped locally. Smoke includes report, RTI, capsule, audit, static assets, restart and restore.

## Remaining boundaries

The replay cache remains process-local and resets on restart. Before multi-replica production, use a shared atomic consume-once store with TTL and fail-closed outages, or prove the gateway/application network trust and replay threat model independently. At capacity authentication denies valid new assertions temporarily; measure this and alert rather than silently accepting replays. This change does not claim production capacity at 100 authenticated requests/second.

No real operator gate is marked passed. The production readiness ledger remains controlled_synthetic_preview. Target TLS/private networking, IdP/MFA/roles, managed PostgreSQL/object-store restore, scanner attestations, human alert delivery, privacy/editorial/legal approval and partner validation still need target evidence. No deployment, real-case publication or production tag is authorized by a passing local suite.

## Reproduction

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -v
python3 scripts/smoke_e2e.py
PYTHONPATH=. CHROMIUM_PATH=/usr/bin/chromium python3 scripts/check_release.py
python3 scripts/stress_local.py --requests 5000 --output artifacts/continuation/stress.json
```

Use disposable local data only. The 100-client load is synthetic with raised rate limits, not 100x throughput/value or a prolonged production soak. Independent subagent execution is unavailable; separate test/security/stress work streams run as parallel processes, not independent expert review.

## Second red-team loop: publication integrity

Three additional HTTP regressions failed before these fixes: project creation accepted `status=published` without the publication gate (also bypassing the publication kill switch); a new reviewer could silently move an already-public claim back to reviewed/candidate; a claim could attach a source belonging to another project. New projects now accept only research/review, public-claim review requires explicit correction first, and claims/documents/responses require project-scoped sources. A current-version rejection also blocks publication even after two other approvals. Rejected candidates remain blocked; do not erase a rejecting review to publish—create a new corrected candidate and retain the rejected record until a versioned candidate-revision workflow exists.

Acceptance tests exercise HTTP writes, persistent state, cross-project documents/responses and rejection alongside two approvals. No schema migration; historical rows are not rewritten. Operators must inspect existing public projects and cross-project references before real-case publication; these fixes prevent new bad writes, not certify old data.
