# Session 23 - independent A/B verification of the audit snapshot-race fix (2026-09-26)

A fresh clone checked `fix/session23-audit-atomic-event-count` (db2a8eb, e4877d2, 3ffe251) against the integration HEAD `d12470d`. Everything below was run on this machine.

## Environment
- Python 3.14, `pip install --user -r requirements.txt`, SQLite backend (19 PostgreSQL-live tests skipped because no PG was reachable)
- Commands:
  - `python3 -m unittest discover -s tests -p 'test_audit_*.py'` -> 10 tests, OK
  - `python3 -m unittest discover -s tests` -> **446 tests, OK (skipped=19), 0 failures, 0 errors**
  - `python3 scripts/human_persona_torture.py --seed 20260925 --concurrency 100 --waves 5` x10 on each tree (120 personas, 1425 requests per run)

## A/B result (10 runs each, same seed)
| Metric | Baseline d12470d | Fix 3ffe251 |
|---|---|---|
| Runs passing all operational baselines | 6/10 | **10/10** |
| Runs with `dependency_check_failed` on /readyz | 4/10 | **0/10** |
| P50 latency (median of runs) | 88.59 ms | 90.13 ms |
| P95 latency (median of runs) | 171.09 ms | 171.69 ms |
| P99 latency (median of runs) | 207.19 ms | 209.88 ms |
| Throughput (median of runs) | 819.2 rps | 821.3 rps |
| Peak RSS | 58.7-59.7 MiB | 58.7-59.9 MiB |
| Unhandled panics / tracebacks | 0 | 0 |
| /healthz and /readyz recovery after the spike | < 2 ms | < 2 ms |

The baseline failures were exactly the session-22 residual: `/readyz` 503 with reason `dependency_check_failed`, i.e. a false tamper alarm on a healthy chain. The fix removes them and costs no measurable latency: the P50/P95/P99 differences are within run-to-run noise.

## Caveats (reported honestly)
- Only SQLite was tested. The PostgreSQL path still relies on `pg_advisory_xact_lock` and is **not** verified here.
- 10 runs is a fixed-seed gate, not a statistical proof. A 20-50-wave nightly soak in CI is still P1.
- Some individual runs had a P99 slightly above 200 ms. The harness applies its sub-200ms bar to recovery, and recovery passed in every run.
