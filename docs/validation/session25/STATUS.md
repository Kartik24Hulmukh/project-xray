# Session 25 — pre-change status and work queue

Base: a16cb9cdfb5105c2590a38b9974d8be4dc10c796. Read session24 attachment, sampled attached replay, full repos.md, repository instructions, roadmap, acceptance criteria, backlog, CI and relevant harness/tests. Prior audit fix retained; no research restart.

## Status
- Done (prior receipts): atomic audit snapshot fix PR70. Fresh five-wave SQLite baseline passes; raw receipt retained here.
- Partial: bounded synthetic persona harness exists, but recovery latency and RSS sampling cover only first wave. PostgreSQL is deliberately stripped from harness environment. Nightly soak absent.
- Missing: actual production target receipts, independent editorial reviews, design partners. Readiness remains synthetic-preview only.
- Release PR62 open; fresh API reports clean mergeability, which alone does not establish required review or green checks.

## Ranked queue (criticality × impact / effort, relative 1–5)
1. P0 production/release approval and target evidence: 5×5/5; external owner, never fabricate or bypass.
2. P1 reliable full-wave recovery measurement and explicit isolated PostgreSQL harness: 4×5/2.
3. P1 20-wave nightly SQLite/PostgreSQL CI with immutable receipts: 4×4/2.
4. P2 roadmap features/connectors after evidence/governance approval: 2×3/5. repos.md intentionally defers external connectors.

## Pre-change acceptance / premortem (engineering perspectives, not real council participation)
- Research: fixed seed plus exact SHA/env/commands; no latency improvement claim from noisy single runs.
- Systems: bounded service workers and DB pool; every wave must recover in <200ms. Nightly job timeout prevents indefinite deadlocks.
- Founder: close reproducible evidence gap rather than claim production traction.
- Red team H1: later-wave starvation can evade first-wave timing. Test: injected slow later probe must fail the gate; measure every wave.
- Red team H2: inherited DATABASE_URL could mutate operator data. Mitigation: SQLite default scrubs all DB env; PG opt-in creates uniquely named disposable DB and drops it, refuses non-loopback host.
- Red team H3: state drift/readiness snapshot race. Mitigation: existing per-wave fixed-seed probe-count/reason invariant; exercise both dialects.
- Red team H4: sampler retains unbounded memory / process leaks on exceptions. Mitigation: constant-space min/max/count sampler and unconditional shutdown.

Integrations: reuse repos.md psycopg2 for real PG testing; stdlib harness, existing pinned GitHub Actions only. No new runtime connector. Disposable local PG installation, if available, is development tooling, not product integration.

Baseline receipt frozen before source edits. Unit suite completion recorded separately. Fresh baseline ran concurrently with unit tests, so not a clean performance comparison.
