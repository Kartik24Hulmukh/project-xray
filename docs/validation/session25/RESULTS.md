# Session 25 checkpoint — NOT production-ready

## Implemented
- Nightly/manual/PR-triggered 20-wave CI matrix for SQLite and live PostgreSQL; SHA-pinned Actions, read-only permissions, 20-minute job timeout, always-retained logs/receipts.
- PostgreSQL torture now explicitly opts in via `--backend postgres` and a loopback-only `XRAY_TORTURE_PG_ADMIN_URL`. Each invocation creates/drops a unique disposable DB. Inherited DB configuration cannot silently redirect default SQLite runs.
- Recovery probes now cover **every wave**, not just wave one. RSS sampling spans the whole load window and retains at most 4096 samples with lifetime min/max/count. Child server and database cleanup occurs on failures.
- Eight new failure-oriented unit contracts. No external data connector introduced: reuse `repos.md` psycopg2 and standard library; no new runtime dependency.

## Measured results
Baseline a16cb9c: 446 tests run, 19 skipped, zero failures/errors. After initial change: 454 tests run, 19 skipped, zero failures/errors. These are NOT 100% execution of all deployment gates.
Live PostgreSQL 17.11: five existing suites ran 29 tests, zero skips/failures; receipts retained. Browser acceptance, synthetic E2E (restart/restore/report/RTI/capsule/audit/assets) and production rehearsal passed locally. Rehearsal RPO is explicitly not measured.

| Run | P50/P95/P99 ms | Throughput rps | RAM floor/ceiling MiB | Verdict |
|---|---|---:|---|---|
| Baseline SQLite, 5 waves | 107.12 / 203.93 / 290.62 | 643.0 | 37.4 / 59.1 | pass |
| Initial SQLite, 20 waves | 95.05 / 178.75 / 221.11 | 777.9 | 37.6 / 60.7 | pass |
| Default PostgreSQL, 20 waves | 110.75 / 257.11 / 326.00 | 588.7 | 37.7 / 51.8 | **FAIL RSS growth** |
| PostgreSQL diagnostic, 50 waves | 160.94 / 325.44 / 455.61 | 433.1 | 38.0 / 53.3 | **FAIL RSS growth** |
| PostgreSQL, explicit MALLOC_ARENA_MAX=2 | 110.57 / 240.36 / 321.14 | 597.4 | 37.7 / 43.0 | pass (configuration-specific) |

No controlled speedup claim: runs used different wave counts and overlapping unit/load activity. The added evidence gate, not speculative performance, is the improvement. PG baseline did not exist because original harness stripped DATABASE_URL. No production-traffic rate was supplied, so 100 workers is **not evidence of 100× production load**. Personas are synthetic profiles, not real human validation. Recovery measures post-wave endpoint response times, not injected infrastructure-fault recovery.

## Newly discovered blocker and convergence log
Default PostgreSQL RSS first-to-last growth: 11.2 MiB (20 waves), 8.9 MiB (50 waves), exceeding unchanged 8 MiB gate. All 1500 readiness probes in the 50-wave diagnostic answered 200, zero server tracebacks, worst health/readiness probes 1.63/3.42 ms. A plateau late in the run is evidence against simple unbounded leakage, not proof.
1. Retain entire warm PostgreSQL connection pool: 12.4 MiB growth, FAIL. Experiment reverted.
2. Explicitly close all adapter-owned PostgreSQL cursors: 11.8 MiB growth, FAIL. Experiment reverted.
3. Diagnostic glibc arena cap (`MALLOC_ARENA_MAX=2`): 3.6 MiB growth, PASS; evidence supports allocator fragmentation/arena retention. Not silently enabled; not a proven root-cause fix and not portable to Alpine/musl.
Checkpoint rather than weaken RSS threshold. Recommended next: profile native allocations and compare persistent bounded worker-pool reuse against per-request thread creation, across CPython 3.13/3.14 and glibc/musl. Retain default-config failing receipt. Remaining attempts before mandated escalation: two.

## Release state
Release PR62 still requires one independent human approval; zero reviews observed. Main protection requires blocking-security, postgres and unit-and-rehearsal, with admin enforcement. Do not bypass, self-approve or merge on clean mergeability alone. Original a16cb9c checks are green, not proof that new code is green.
Production ledger: 0/10 evidenced checks, production_certified=false. Target deploy/PITR/OIDC-MFA/live human alert/privacy/editorial/design-partner gates remain external blockers. No release tag or production-readiness claim changed.

## Reproduction
```sh
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests
python3 scripts/human_persona_torture.py --seed 20260925 --concurrency 100 --waves 20 --output artifacts/soak/sqlite.json
# Disposable local PG admin, never production. Keep credentials outside git.
XRAY_TORTURE_PG_ADMIN_URL='postgresql://USER@127.0.0.1:PORT/postgres' python3 scripts/human_persona_torture.py --backend postgres --seed 20260925 --concurrency 100 --waves 20 --output artifacts/soak/postgres.json
npm ci
CHROMIUM_PATH=/usr/bin/chromium node scripts/ui_acceptance.mjs
python3 scripts/smoke_e2e.py
python3 scripts/rehearse_production.py
python3 scripts/check_workflow_security.py
python3 scripts/check_release_claims.py --json
```
Local environment: Linux, CPython 3.14; PostgreSQL 17.11 extracted from Debian packages. CI targets CPython 3.13 / PostgreSQL 16.4. Local pgserver package unavailable; no fake PG substitute used. User-level pip reported pre-existing unrelated logfire/OTEL version conflicts; use a dedicated virtual environment for reproducibility. Full dependency capture supplied separately.

Security: supplied GitHub token is exposed in prompt/replay. Rotate it immediately. No token written into repository, remote URL, receipts or git config.
