# Launch runbook — graceful drain (v2.1.1, Sept 16-17 2026)

## What changed
`app/server.py` now installs bounded SIGTERM/SIGINT drain handlers **before** the
`startup` event is printed, and `BoundedHTTPServer.drain()` performs:

1. `shutdown()` - stop the accept loop (no new admissions).
2. Wait up to `XRAY_DRAIN_SECONDS` (default 15) for **in-flight requests** (idle
   keep-alive sockets are not counted and cannot stall the drain).
3. `server_close()` - close the listener and flush/stop the bounded OTLP exporter.
4. Emit `{"event":"shutdown","drained":true|false,"timeout_seconds":N}` on stdout.

Exit code is 0 on SIGTERM (previously `-15`). `drained:false` means a handler was
wedged past the bound - alert on it; the bound is intentional so a restart can
never hang forever.

## Operator settings
| Setting | Value | Why |
|---|---|---|
| `XRAY_DRAIN_SECONDS` | 15 (<= 25) | must exceed slowest write P99 (0.44 s measured) with margin |
| Kubernetes `preStop` | `sleep 20` (>= drain + 5 s) | lets the ALB deregister before the pod stops accepting |
| `terminationGracePeriodSeconds` | 30 | must exceed preStop + drain |
| ALB deregistration delay | 20 s | prevents in-flight 502s |
| `HTTP_EXEC_PARALLELISM` | 4 (unchanged) | raising it reintroduces the GIL convoy; see `scripts/bench_sweep.py` receipts |

## Verification (all run on this change set, seed 20260915)
- `python3 -m pytest tests -q` -> **292 passed / 8 skipped (PostgreSQL-gated) / 0 failed**, 21 subtests.
- `scripts/fault_injection.py --seed 20260915` -> `all_pass=true, no_5xx=true, server_alive=true`.
- `scripts/stress_local.py` (100 clients) -> `safety_pass=true`, zero 5xx, `idempotency_unique_success_ids=1`, peak RSS 44.7 MiB.
- Real-signal proof: `tests/test_launch_hardening_20260915.py::TestSigtermDrainSubprocess` spawns the entrypoint, sends SIGTERM, asserts rc==0 and `drained:true`.

## Rollback
Redeploy the previous tag. No schema migration is included in this change set,
so rollback is a pure image swap.

## Open operator gates (not closed by code)
1. Rotate the PAT exposed in the task prompt; move CI to OIDC.
2. Collector TLS + OTLP retention acceptance.
3. RDS PostgreSQL provisioning + `scripts/rehearse_production.py` against staging.
4. Alerting: P95 > 250 ms, any 5xx, any `drained:false` shutdown event.
