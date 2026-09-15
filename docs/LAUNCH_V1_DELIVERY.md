# Project X-Ray — launch hardening delivery / merge hold

**Target:** Kartik24Hulmukh/project-xray  
**Launch window:** September 16–17, 2026  
**PR:** https://github.com/Kartik24Hulmukh/project-xray/pull/43  
**Branch:** `harden/project-xray-v1-launch`  
**Decision: NO-GO for unconditional launch. Code pushed; PR open; not merged.**

## Executive outcome

Restored missing prior-turn implementation as reviewed, testable code rather than treating report claims as recoverable patches. Authenticated Git delivery now works. Kept the integrated tracing, JSON request logging, probes and strict API boundaries. Added bounded idempotency maintenance, fail-closed load acceptance, real rate-limit shocks and PostgreSQL coverage. All final local release/stress/chaos/rate commands exit 0.

**Merge hold:** main requires one approving review with stale approvals dismissed. Do not use the owner's credential to bypass that protection. Production operational/editorial approvals and sustained target load remain absent. A newly measured 100,000-event cold readiness path takes **697.866 ms**, exceeding 200 ms; the warm path is fast but does not remove cold/head-change risk.

## Forensic post-mortem and five-point premortem

Inputs were the two attached Markdown receipts, not raw failure logs or the reported bundles/patch series. Remote main was still `e9db027`; neither attachment's local-only implementation was recoverable here. Their differing filenames/test counts cannot be treated as a single verified code state.

| Failure / catastrophic risk | Actual remediation and evidence |
|---|---|
| Prior local-only work lost and push blocked | Rebuilt on required isolated branch; atomic semantic commits pushed; release PR #43 opened. Credentials used only for GitHub authentication, not committed. |
| Abandoned leases or unsafe receipt expiration | Bounded keyset maintenance; default dry-run; completed receipts kept indefinitely unless retention explicitly approved; completion age from completed_at. |
| Retry vs sweep race; unbounded scan | Exact state/timestamp/hash DELETE fencing; existing serialized write transaction; maximum 5000 inspected rows; private resumable cursor avoids starvation behind malformed/live rows. SQLite and live PG tests cover rotation, rollback and boundaries. |
| False-green load safety | Original harness allowed 503 and ignored RAM/recovery. New launch gates reject unexpected statuses, enforce <=128 MiB peak, health/readiness <200ms recovery, process survival and zero logged tracebacks. Negative regression receipts prove failures are rejected. |
| Claimed rate shock not actually executed | Separate 100-client local shock produces genuine 429; GET/POST now include Retry-After. Health/readiness remain healthy. Chaos additionally checks unexpected replay statuses and tracebacks. |

Frozen baseline and premortem were committed before implementation. No arbitrary broad BaseException swallowing, duplicate no-op telemetry shim, or unbounded retry loop was added. Harness logs go to files, not an undrained PIPE. Historical deadlock/context-exhaustion claims are not independently diagnosed without the raw logs.

## Benchmark deltas — frozen baseline to final pinned-dependency run

| Phase | P50 ms | P95 ms | P99 ms | Throughput requests/s |
|---|---:|---:|---:|---:|
| baseline_reads | 1.30 → 0.97 | 2.44 → 2.08 | 2.64 → 2.75 | 627.62 → 828.99 |
| 100_client_reads | 98.98 → 97.45 | 178.46 → 163.85 | 230.38 → 204.95 | 957.97 → 985.83 |
| 100_client_writes | 172.09 → 171.37 | 194.65 → 204.85 | 215.27 → 225.31 | 498.98 → 509.67 |
| 100_client_idempotency_race | 58.31 → 58.75 | 84.96 → 90.76 | 86.69 → 91.91 | 821.65 → 823.45 |

- Workload: 100 single-client reads, 2,600 reads / 300 writes / 100 same-key replays at 100 clients; 64 server workers; synthetic SQLite only.
- Final statuses: reads all 200, writes all 201, same-key race 99×201 + 1×409, exactly one unique success ID. Expected conflict is not a capacity rejection.
- Baseline peak RSS: **51.68 MiB**; final startup RSS: **36.85 MiB**, peak **57.95 MiB**, ceiling **128 MiB**. Baseline floor was not measured; startup RSS is not a continuously sampled minimum.
- Final immediate health recovery **0.702 ms**, readiness **3.890 ms**, both 200; audit/integrity checks pass; 301 persisted projects match 301 expected.
- Zero 5xx, zero logged tracebacks and process survival in these runs. This is observed bounded workload behavior, not a universal zero-panic proof.
- First post-change run is also retained (`launch-v1-stress.json`): read P99 265.15 ms and throughput 857.20/s. Final run is not selected to hide it. Timing varies; baseline preceded installation of pinned dependencies, so causal speedup claims are unsupported.
- Steady-state final read P99 **204.95 ms**, write P99 **225.31 ms**. The small-fixture recovery ceiling passes; a steady-state <200ms P99 promise would not.

## Chaos and research reproducibility

Seed **20260915** exercises 60 held idle/partial-header sockets, 30 mid-body disconnects and 50 interleaved identical-key replays. This tests scheduling/interleaving, not differing-body ordering semantics. Final rate shock: 200 reads, 100 clients, limit 1/minute; allowed HTTP 200/429 only, valid Retry-After, no traceback, healthy sub-200ms probes. JSON raw receipts are under `docs/validation/launch-v1-*`.

Reconciliation unit tests use a fixed timezone-aware September 16 clock. The ledger fixture uses fixed IDs/timestamps and production cryptographic functions. Timings, UUIDs and concurrent response order are inherently nondeterministic; fixed seeds do not guarantee identical latency or byte-identical load receipts.

## Validation status

| Gate | Evidence / status |
|---|---|
| Full local discovery | **328 discovered: 310 pass, 18 PG-specific skips**, 13.654s, no failures. |
| Separate live PostgreSQL | **20 tests pass** on local PostgreSQL 16.14: live 6, concurrency 1, fencing 3, reconciliation 10. The fencing module is counted as one skip by SQLite discovery, explaining the count difference. |
| Final release checker | Exit 0: tests, browser acceptance using Chromium, environment rehearsal, synthetic publish/export/capsule/backup/restore. Not target production infrastructure. |
| Stress / seeded chaos / real rate shock | All final commands exit 0; receipts retained. New chaos/rate gates added to CI with artifact retention. |
| Repository workflow / IaC / waiver contracts | Pass. OpenTofu absent locally; actual plan/deployment not claimed. |
| Remote intermediate head `71bf236` | CI unit-and-rehearsal, postgres, container-smoke, blocking-security and GitGuardian all success. Final-head checks must be verified separately. |
| Large-ledger readiness | **OPEN risk:** 100,000 signed events; cold helper 697.866 ms, warm P50/P95/P99 2.545/3.102/3.941 ms. Not HTTP latency. Full scan materializes checkpoint rows; concurrency and RAM ceilings at this size not proven. |
| Merge approval | **BLOCKED:** one independent approving review required; no bypass. |
| Target launch / commercial outcomes | **NOT VERIFIED:** deployment, sustained load, legal/editorial reviews, two-person dossier approvals, operational ownership and traction. |

## Unresolved architecture risk — next remediation cycle

`readiness_verify_audit` releases its cache lock before full verification, so simultaneous cold probes can duplicate a full scan. `verify_head` still uses COUNT(*) (not O(1) on PostgreSQL), and full verification materializes all checkpoints. The attached report's standalone probe registry would not fix this integrated path.

Before claiming September launch readiness: build streaming/batched full verification and bounded single-flight verification without deadlocking PostgreSQL advisory-lock holders; retain fail-closed tamper detection. Benchmark cold, head-changing and concurrent readiness on a representative ledger, plus peak memory, with deadlines and explicit acceptance. Do not silently turn full integrity checks off to make probes look fast. This subsystem is identified, not claimed fixed.

## Operator and release actions

1. Review PR #43 and obtain the required independent approval. Confirm checks on the exact final head, not an earlier green commit. Keep merge on hold until required validation is satisfied.
2. Resolve and remeasure the large-ledger cold-readiness risk; test sustained target-environment load with declared production baseline and payload mix.
3. Complete operational/editorial/legal launch gates. Existing broad P0 backlog items were not automatically marked complete.
4. Run maintenance with the same DB and lease-timeout configuration as the server. Start without --apply; privately advance next_cursor until null. Explicit receipt expiry changes client retry guarantees.
5. Rollback: disable the external maintenance schedule first; do not attempt to reconstruct deleted leases/expired receipts from logs. No schema migration or automatic maintenance job is deployed by this PR. Revert semantic commits through a reviewed rollback PR if necessary.
6. **Rotate the GitHub token pasted into the conversation.** It was valid; no secret is included in repository receipts or this report.

Remediation accounting: one PostgreSQL test-adapter correction and one CLI environment-validation correction; no subsystem exceeded the maximum five cycles. Large-ledger redesign remains an explicit blocker rather than an unsafe rushed patch.
