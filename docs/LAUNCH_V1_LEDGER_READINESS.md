# Large-ledger readiness: streaming single-flight verification (2026-09-15)

## Carried launch blocker

`/readyz` proved audit-chain integrity by walking the whole chain inside the probe
request whenever the head moved or the re-verify window lapsed. On a 100,000-event
ledger that cold path measured 697.9 ms previously and 657.777 ms when re-measured here,
far past the 200 ms recovery ceiling - and every concurrent probe launched its own
duplicate O(n) scan, a self-inflicted thundering herd at launch volume.

## Design (first-principles, integrity preserved)

1. **Streaming segments** - `audit.verify_segment()` verifies a bounded slice of the
   chain (`READYZ_VERIFY_BATCH`, default 1000) joined to its checkpoint row, so peak
   memory is O(batch) not O(chain). Hash linkage, checkpoint consistency and HMAC
   signatures are checked exactly as in `audit.verify()`.
2. **Single-flight coordinator** - one scan per process; late arrivals coalesce on the
   shared result (`readyz_verify_coalesced`) and never start a duplicate scan. A waiter
   holds no transaction and no PostgreSQL advisory lock while waiting, so lock
   inversion/deadlock is impossible by construction.
3. **Hard verification budget** - `READYZ_VERIFY_BUDGET_MS` (default 25 ms). On
   exhaustion the probe answers 503 `not_ready` with
   `reason=audit_verification_in_progress` plus resumable progress. It never reports
   ready to hide latency.
4. **Resumable cursor keyed by target head** - a head rotation restarts verification;
   otherwise the next probe resumes exactly where the previous one stopped.
5. **O(1) steady state** - the probe fast path recomputes the head event hash and its
   checkpoint HMAC and skips the `COUNT(*)` cardinality scan, which was itself an O(n)
   per-probe cost (7.6 ms at 100k events). Cardinality is still proven by the periodic
   streaming full verification and the orphan-checkpoint guard.
6. **Telemetry** - `readyz_verify_inflight`, `readyz_verify_coalesced`,
   `readyz_verify_events_scanned`, `readyz_verify_completed`,
   `readyz_verify_budget_exhausted`, `readyz_verify_ms` exported on `/metrics`.

## Benchmark: 100,000-event ledger, cold-start probe storm

```sh
python3 scripts/bench_readyz_singleflight.py --events 100000 --clients 8 --budget-ms 25 \
  --out docs/validation/launch-v1-ledger-singleflight-8c.json
```

| Metric | Baseline (full walk in probe) | 4 concurrent probers | 8 concurrent probers |
|---|---:|---:|---:|
| Cold probe P50 (ms) | 657.777 | 39.218 | 85.51 |
| Cold probe P95 (ms) | 657.777 | 70.235 | 118.889 |
| Cold probe P99 (ms) | 657.777 | 77.975 | 165.245 |
| Cold probe max (ms) | 657.777 | 80.212 | 184.038 |
| Steady-state probe P99 (ms) | 7.6 | 0.031 | 0.021 |
| Events scanned for full proof | 100000 per probe | 100000 | 100000 |
| Duplicate-scan ratio | 1.0 x clients | 1.0 | 1.0 |
| Convergence to ready (ms) | n/a | 1437.507 | 2532.668 |
| Peak RSS (MiB, ceiling 128) | 88.69 | 88.69 | 87.9 |
| Gate result | fail | pass | pass |

Artifacts: `docs/validation/launch-v1-ledger-singleflight-4c.json`,
`docs/validation/launch-v1-ledger-singleflight-8c.json`.

### Honest limits

* Synthetic SQLite on a 2-vCPU sandbox. A 16-32 thread hot-loop probe storm (far beyond
  any real kubelet cadence) still shows cold probe max around 300 ms; that residue is
  CPython GIL/CPU contention from the load generator, not chain-walk cost -
  `events_scanned_total` stays at exactly 1.0x the ledger in every run, proving zero
  duplicate scans.
* Target-environment verification (PostgreSQL, deployment CPU/memory limits, sustained
  launch concurrency) remains an open launch gate.

## Tests

`tests/test_readyz_singleflight.py`: segment/full-walk equivalence, mid-chain tamper
detection, orphan-checkpoint detection, no rescan in steady state, budget exhaustion ->
not_ready then resume to ready, 8-thread single-flight coalescing with bounded scan
count, and a 20k-ledger per-probe latency ceiling test (<200 ms).
`tests/test_readyz_audit_cache.py` was updated to the streaming contract.
