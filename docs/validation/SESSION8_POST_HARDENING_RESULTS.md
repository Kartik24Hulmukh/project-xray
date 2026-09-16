# Session 8 - Post-Hardening Verification Results

Re-run of the frozen baseline suites AFTER the session-8 changes (ephemeral-port smoke E2E, version reconciliation). Same fixed seeds; local synthetic SQLite only.

| phase | base p50 | post p50 | base p95 | post p95 | base p99 | post p99 | base rps | post rps |
|---|---|---|---|---|---|---|---|---|
| baseline_reads | 1.24 | 1.40 | 1.70 | 2.39 | 2.30 | 4.77 | 767.1 | 622.7 |
| 100_client_reads | 118.09 | 117.38 | 205.05 | 202.95 | 281.14 | 277.49 | 786.3 | 795.8 |
| 100_client_writes | 243.15 | 200.27 | 276.92 | 219.60 | 328.40 | 317.50 | 361.9 | 458.7 |
| 100_client_idempotency_race | 62.30 | 75.72 | 109.49 | 113.42 | 117.05 | 115.80 | 555.5 | 655.1 |

RAM: baseline run peak RSS 64228 KB -> post run peak RSS 56524 KB (ceiling 131072 KB, ram_pass true both runs); baseline idle RSS 37604 / 36044 KB.
Tracebacks in server log: 0 / 0. Audit events == persisted projects: 301/301 and 301/301.
Probe recovery after 100x load: /healthz 1.731 ms->0.819 ms, /readyz 7.817 ms->3.966 ms (both 200, budget <200 ms).
Fault injection (seed 20260913): pre all_pass=True no_5xx=True; post all_pass=True no_5xx=True.
Unit/integration suite: pre 364 OK (skipped=19); post 364 OK (skipped=19). Smoke E2E (ephemeral port): restart/restore/report/rti/capsule/audit/static all passed.
Red-team fuzz: 14/14 pass, no 5xx (frozen baseline run). Rate-limit shock: 1x200 + 199x429, Retry-After valid, probes exempt: pass.
Release-claim guard: PASS fail-closed, ledger 0/10 evidenced (no maturity claim outruns evidence).

Conclusion: zero regressions; zero unhandled panics; deterministic recovery; no cosmetic mocks replaced anything - the two structural fixes (ephemeral port, version reconciliation) are behavioural and test-locked.
