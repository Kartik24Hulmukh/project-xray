# Release-Claim Governance (binding)

**Rule.** No git tag, GitHub release, README badge, landing page, deck or press line may
assert `production`, `GA`, `generally available`, `stable`, `certified` or `enterprise-ready`
maturity for Project-Xray until `ops/production-readiness.yaml` shows **10/10 checks
`passed` with a non-null evidence path**.

**Why this is a product rule, not a process rule.** Project-Xray sells *inspectable
evidence*. A claim about our own maturity that our own ledger contradicts is the same
failure mode we exist to expose. One unsupported word on a release page costs more trust
than a month of shipped features earns.

**Enforcement (automated, fail-closed).**

```sh
make claims                                  # JSON receipt
python3 scripts/check_release_claims.py      # human-readable, exit 1 on violation
```

The guard runs in CI on every push and pull request after `git fetch --tags`. It fails
closed when the ledger is missing, empty or unparseable — an unknown ledger is never a pass.
Allowed honest labels: `-preview`, `-synthetic-preview`, `-rc<N>`, `-alpha`, `-beta`,
`-pilot`, `-canary`, or a bare semver below 1.0.0.

**Approved vocabulary for the 16–17 September 2026 window**

| Say this | Never say this |
|---|---|
| "controlled synthetic preview" | "production release" |
| "source-linked claims for human review" | "corruption detection" / "exposes corrupt officials" |
| "349 automated tests green on this commit" | "fully validated" / "battle-tested" |
| "recovery drill receipts on SQLite; PostgreSQL target rehearsal outstanding" | "disaster-recovery ready" |
| "0/10 production-readiness checks closed — tracked publicly" | silence about the ledger |

**Escalation.** To close a ledger check, attach the machine receipt path in the same PR that
flips the status. A status flip with `evidence: null` is a guard violation by construction.

**History.** 2026-09-15: `v2.1.1-production` (release + tag) was retracted and relabelled
`v2.1.1-synthetic-preview`, marked pre-release, with a public correction notice. See
`docs/validation/SESSION7_VALIDATION_RECEIPTS_2026-09-15.md`.
