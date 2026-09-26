# Session 29 — baseline and work queue

Source baseline: `1990cd1d659bbd86e61860899d6c0814ddc4b561`. Both attached session handoffs and full `repos.md` reviewed; branch, recent commits, open PRs, tests, backlog and roadmap inspected. Session 28 uncommitted changes did not persist into this sandbox; not represented as recovered.

## Status before source changes
- Done: existing bounded HTTP admission, health/readiness, JSON logs, optional OTEL, deterministic dual-backend harness. Current head SQLite/PostgreSQL soak CI passed.
- Broken: both current-head unit-and-rehearsal jobs fail `NightlyContractTests.test_both_dialects_seed_and_receipts`: obsolete literal `--waves 20` expectation after selectable waves landed. CI baseline: 460 run, 1 failure, 19 skipped. Raw CI log retained locally.
- Partial: H6 allocator growth investigation. No measured support for session-28 reaper change, which is unavailable here.
- Missing/external: production deployment/identity/restore/alert/editorial/partner receipts; release PR #62 unmerged. Backlog includes historical unchecked entries, not evidence of absence.

## Ranked remaining queue (criticality × impact / effort)
1. P0: restore truthful CI workflow contract with default 20 and selectable 60 tests (high/high/small).
2. P0: measure existing 60-wave dual-backend soak before allocator changes (high/high/medium). H6 has four historical attempts; a fifth failing remediation must be escalated.
3. P0 external: independent approval, release and operator ledger (high/high/external).
4. P1: optional connectors remain deferred per repos.md; no integration added and no connector claims.

## Acceptance and premortem
Acceptance: default schedule/PR still uses 20; manual options exactly 20/60; same deterministic command and unchanged gates; targeted and full suites must run.
Research lens: compare same seed/concurrency/waves; do not compare session-28 three-wave figures as controlled deltas. Local baseline suite and soak ran concurrently, so latency is observational, not a controlled performance experiment.
Systems lens: no runtime change needed to repair contract drift.
Founder lens: prioritize restoring verified release gate over speculative features. These are engineering review lenses, not independent human council participation.
Red team: async deadlock hypothesis—bounded shutdown/recovery must pass torture; worker starvation—readyz invariants and recovery <200ms under 100 workers; state drift—fixed-seed requests plus DB/audit invariant checks. Existing tests remain gates; synthetic personas are not real-human validation.
H6 hypothesis: continuous waves retain glibc arena memory. Test longer unchanged workload; adding quiet sleeps would change the experiment and is not an accepted fix. No memory-gain claim without controlled measurements.

Baseline receipts frozen under artifacts/session29 before edits; no source edited yet.

## Additional reproduced blocker before harness edit
`--waves 60` exits 2: waves must be 1..50. Thus the session-27 selectable investigation could not execute. Extend the bounded CLI maximum to 60, retain concurrency/RSS/recovery gates, test 20/60, invalid ranges, and no DB effects on rejection. No arbitrary sleeps or allocator changes.

## Local verification after repair
- Isolated venv, requirements installed; pip check passes. Full suite: 464 run, zero failures/errors, 19 explicit live-PostgreSQL skips. Baseline without installed PG dependency had 20 skips; the extra skip is the mocked PG cleanup test.
- Torture contracts: 33 pass, zero skips. Smoke E2E: restart, restore, report, RTI, capsule, audit, static assets pass. IaC/workflow/grype contracts pass. Release claim guard passes with ledger still 0/10.
- Actual 60-wave local SQLite run now executes: 17,100 requests, 120 personas, 100 workers; zero tracebacks/crashes, settled growth 3.4 MiB, worst health/ready recovery 48.67/3.10 ms. Runtime application unchanged. This is not proof of 100x production load.
- Baseline 6-wave and after 60-wave are different workloads/environments, both overlap a suite; no performance delta claimed. Baseline P50/P95/P99 109.44/204.62/279.69 ms, throughput 639.0 rps, RAM floor/ceiling 32.0/53.5 MiB. After 60-wave 120.07/234.47/306.84 ms, 556.7 rps, 38.2/65.7 MiB.
- Initial remote 60-wave dispatch fails before workload (CLI cap), confirming blocker on both backends. Re-dispatch on repaired head required.
- No session-28 reaper reconstructed: baseline runtime can pass the longer SQLite gate; PG evidence still required.
