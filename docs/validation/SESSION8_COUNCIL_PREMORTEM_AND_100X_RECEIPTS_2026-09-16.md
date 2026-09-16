# Session 8 - Agent Council Premortem, Frozen Baseline and 100x Concurrency Receipts

Date: 2026-09-16 (launch window 16-17 September 2026)
Branch: harden/project-xray-v1-launch (unified with origin/main at 75e9a6f)
Scope: LOCAL synthetic SQLite only. No external target, no AWS rehearsal. Claims stay inside the synthetic-preview label.

## 1. Council premortem (parallel personas)

| Persona | Catastrophic failure mode premortemed | First-principles resolution shipped / verified this session |
|---|---|---|
| SF Founder | Launch language outruns evidence; credibility death spiral | Release-claim guard re-run: PASS fail-closed, ledger 0/10 evidenced; all version sources reconciled to 0.4.8 with explicit synthetic-preview RC naming; RELEASE_NOTES header states scope |
| Lead Research Scientist | Non-deterministic benchmarks; unreproducible receipts | Fixed seeds (fault_injection seed=20260913), PYTHONHASHSEED=0 in bench_sweep, receipts committed as JSON under docs/validation/session8; idempotency race yields exactly 1 unique success id |
| Principal Systems Architect | Async deadlock / worker starvation under 100x; /readyz O(n) audit revalidation; fd and port leaks | Single-flight + segmented /readyz verification with yield (server.py:393) re-verified under 20k-request 100-client load; smoke_e2e fixed-port 18123 collision replaced with ephemeral bind; sqlite connections closed via contextlib.closing (v0.4.7); bounded graceful drain on SIGTERM verified |
| Red Team Chaos Lead | Socket-drop / slowloris starvation; out-of-order replay double-writes; fuzz panics; rate-limit probe lockout | fault_injection: 60 held slowloris sockets served 200/200 while held, 30 mid-body drops -> 300x200, out-of-order replay 24x201 + 26x409 with 1 unique id; redteam_fuzz 14/14 no 5xx; rate_limit_shock 1x200 + 199x429 with valid Retry-After and exempt probes 200 |

## 2. Frozen baseline (pre-modification, post-merge of origin/main)

- Unit/integration/E2E: 364 tests, OK (skipped=19; skips are PostgreSQL/live-target gated).
- stress_local 20,000 requests @100 clients: reads p50 118.09 / p95 205.05 / p99 281.14 ms, 786.3 rps; writes p50 243.15 / p95 276.92 / p99 328.4 ms; idempotency race p50 62.3 / p95 109.49 / p99 117.05 ms; 1 unique success id; peak RSS 64,228 KB (ceiling 131,072 KB); baseline RSS 37,604 KB; tracebacks_in_log 0; audit events 301 == persisted projects 301; /healthz and /readyz recovery 200 in <8 ms.
- fault_injection (seed 20260913): all_pass true, no_5xx true, tracebacks 0.
- redteam_fuzz: 14/14 pass, health_after_fuzz 200.
- rate_limit_shock: all_pass true.
- Release-claim guard: PASS (fail-closed, 0/10 ledger).
Receipts: docs/validation/session8/stress_100x_20k_requests.json, docs/validation/session8/fault_injection.json.

## 3. Post-hardening verification (same suites re-run after session-8 changes)

See PR body and docs/validation/SESSION8_POST_HARDENING_RESULTS.md (appended after re-runs).

## 4. Honest open gates (NOT claimed closed)

- Gate B (managed PG16 verify-full, PITR, measured RPO/RTO): requires AWS target; 19 live tests remain skipped locally.
- Gate C real-ingress MFA/replay rehearsal: requires deployed gateway.
- Gate E adoption evidence: requires named design partners.
- Credential hygiene: the PAT embedded in task history must be revoked by the founder; this session used it only for repo-scoped delivery and never echoed it into committed files.
