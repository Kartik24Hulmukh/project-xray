# Session 7 — Validation Receipts (2026-09-15, post-sandbox-recovery)

Every line below is a direct observation from this session in a fresh environment.
Historical results from earlier sessions are context, not receipts.

## Source-control gates (GitHub API, `main` = `50b7897`)

| Gate | Observation |
|---|---|
| `main` HEAD | `50b7897b45b99d136b1c3ed0dceab7b7bab06a0d` — "fix(recovery): authenticate PostgreSQL restores..." (#48) |
| CI on HEAD | run #265 — `completed / success` |
| **Security on HEAD** | **run #135 — `completed / success`** (this was the last unverified machine-checkable gate from Session 6; it is now CLOSED) |
| Open PRs | 0 (before this branch) |
| Open issues | 3 — #1 evidence domain/policy compiler/versioned API, #2 safe intake/preservation/exports, #3 reviewer/public workflow and release evidence |

## Local test and smoke receipts (clean venv, Python 3.14)

```
pytest       : 330 passed, 19 skipped, 30 subtests passed  (25.9s)
unittest     : Ran 349 tests — OK (skipped=19)
smoke_e2e.py : {"status":"ok","restart":"passed","restore":"passed","report":"passed",
                "rti":"passed","capsule":"passed","audit":"passed","static_assets":"passed"}
```

Note on skips: 19 skipped tests are **not** passes. They are the PostgreSQL-live /
TLS-dependent cases that require the target environment; they remain the core of the
open readiness gap.

## Honest failures observed (not hidden)

| Check | Result | Meaning |
|---|---|---|
| `scripts/readiness_status.py` | exit 1 — **0/10 passed with evidence** | Intentionally failing ledger. No target-environment evidence exists. Correct behaviour. |
| `scripts/check_release.py` | exit 2 — playwright missing from `node_modules` | Browser acceptance cannot run in this sandbox; it *does* run in CI (`npx playwright install`). Environment limitation, not a product defect. |
| `scripts/check_release_claims.py` (new) | exit 1 on entry — see below | Caught a real, live governance defect. |

## Defect found and fixed this session (P0, governance)

**Finding.** The repository published a git tag *and* a public GitHub release named
`v2.1.1-production`, not marked pre-release, while:

- `ops/production-readiness.yaml` stood at **0/10** checks passed with evidence,
- `package.json` declared version **0.4.6**,
- no target-environment TLS `verify-full`, real RPO/RTO, or editorial sign-off receipts existed.

For an evidence product whose entire value proposition is *verifiable, source-linked,
honestly-scoped claims*, shipping an unsupported maturity claim about itself is the
highest-severity possible failure: it invalidates the product thesis in one word.

**Remediation executed (verified via API):**

1. Created tag `v2.1.1-synthetic-preview` at the same commit `e9db027` (contents unchanged).
2. Published a corrected release under that tag, marked **pre-release**, with a visible
   correction notice explaining the relabel and the 0/10 ledger state.
3. Deleted the release `v2.1.1-production` and its tag ref.
4. Result: all 4 releases are now `prerelease: true`; no artifact claims production maturity.

**Recurrence prevention (code, not a promise):** `scripts/check_release_claims.py` plus
`tests/test_release_claims.py` (8 tests) and a CI step. The guard fails closed on an
unreadable or empty ledger, treats `preview`/`rc`/`alpha`/`beta`/`pilot`/`canary` labels as
honest, and blocks `production`/`ga`/`stable`/`certified`/`enterprise` names until the
ledger is 10/10 with evidence.
