# Session-9 Launch Re-Validation Receipts (2026-09-16)

Re-execution of the full fail-closed gate stack on `harden/project-xray-v1-launch` after ingest of the session-8 handoff report. Gate list = session-8 orchestrator plus rate-limit shock and smoke E2E. LOCAL synthetic SQLite scope only; NO production/GA claims.

## Gate results (all rc=0)
| Gate | Seconds | Result |
|---|---|---|
| unit_integration (unittest discover) | 21.92 | Ran 364 tests - OK (skipped=19) |
| stress_100x (20k req @100 clients) | 26.19 | pass |
| fault_injection (seed 20260913) | 2.12 | pass |
| redteam_fuzz | 0.93 | pass |
| rate_limit_shock | 0.9 | pass |
| release_claim_guard (fail-closed) | 0.05 | pass |
| smoke_e2e | 2.42 | pass |

all_gates_pass = True

## 100x benchmark snapshot (session 9)
- reads 20k@100c: p50 111.74 / p95 193.32 / p99 269.08 ms, 820.54 rps, all 200
- writes: p50 204.61 / p95 294.37 / p99 308.63 ms, 407.0 rps, all 201
- idempotency race: p50 59.27 / p95 117.75 / p99 119.56 ms; unique success ids = 1 (deterministic single-write)
- RAM: idle 38152 KB, peak 62992 KB (ceiling 131072), ram_pass=true
- resilience: tracebacks_in_log=0, server_alive=True, /healthz 1.812 ms, /readyz 4.023 ms, audit 301 == persisted 301

Zero unhandled panics; deterministic auto-recovery preserved. Scope label unchanged: Controlled Synthetic Technical Preview.
