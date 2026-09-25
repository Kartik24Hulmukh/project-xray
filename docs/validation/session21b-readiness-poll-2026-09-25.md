# Session 21b receipt: bounded readiness poll + test SQLite hygiene (2026-09-25)

Base: `101b200` (after PR #66 merged the RAM floor/ceiling/settled sampler). Only work that does not duplicate #66 is included.

## Commands
```
python3 -W always::ResourceWarning -m unittest discover -s tests
python3 scripts/human_persona_torture.py --seed 20260925 --concurrency 100 --output <path>
```
Python 3.14, SQLite, MAX_HTTP_WORKERS=64, 120 personas, 285 requests per run.

## Frozen baseline (measured before edits, `0d50ed3`)
- 412 tests OK (19 skipped). 5 `unclosed database` ResourceWarnings, all opened by tests: test_evidence_taxonomy x2, test_exec_parallelism x2, test_readyz_audit_cache x1.
- Harness startup: fixed `time.sleep(0.4)`, then a readiness loop that only slept on exceptions.
- Torture (3 runs): 920.8 / 901.5 / 899.0 rps; P50 83.65 / 88.84 / 86.47 ms; P95 133.29 / 143.46 / 138.35 ms; P99 190.29 / 196.15 / 209.14 ms; VmHWM 47.4 / 47.6 / 47.7 MiB; all PASS.

## After (this branch on `101b200`)
- **419 tests OK (19 skipped), 0 unclosed-database warnings.**
- `wait_ready()`: deadline-bounded poll with capped exponential backoff; fails fast if the server exits; kills the child on failure. Startup-to-ready is measured at 0.161 s in 3 of 3 runs, against at least 0.40 s before.

| Run | rps | P50 | P95 | P99 | RAM floor MiB | VmHWM MiB | startup s | health/ready ms | 503s | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| s21b-1 | 961.7 | 70.43 | 144.2 | 168.8 | 32.0 | 48.0 | 0.161 | 0.71 / 1.32 | 0 | PASS |
| s21b-2 | 893.6 | 90.23 | 152.71 | 186.38 | 32.1 | 47.8 | 0.161 | 0.64 / 1.32 | 3 | PASS |
| s21b-3 | 924.8 | 84.43 | 138.36 | 186.92 | 31.4 | 47.0 | 0.161 | 0.69 / 1.24 | 2 | PASS |

Latency and throughput changes are within noise (each run is about 0.3 s). No performance claim is made beyond harness startup time.

## Logged hypothesis (not fixed)
Non-deterministic outcome split under a fixed seed: 0-5 `/readyz` 503s (`audit_verification_in_progress` under single-flight contention), while 200+503 = 135 stays constant. This is fail-closed by design, but the receipt distribution depends on timing. Next: classify 503s by reason and assert the invariant.

## Caveat
Under `-X tracemalloc=8`, `test_readyz_singleflight.test_probe_latency_bounded_on_large_ledger` exceeds its 200 ms ceiling (280 ms) because of profiler overhead. It passes in normal runs.

## Addendum: CI-only failure root-caused and fixed (`fix(database)`)
The first CI run of this PR failed `test_journal_mode_pragma_issued_once_per_file` with `'delete' != 'wal'`. Root cause: `_ensure_wal` cached WAL state by `(path, dev, inode)`. After the hygiene commit closed the leaked test connection, the deleted database's inode was freed and **reused** by the recreated file. The cache then claimed WAL for a fresh rollback-journal file. That is a real production hazard: a restored or recreated DB would silently lose WAL concurrency. The leaked connection had been masking it.

Fix: a cache hit must also be confirmed by the lock-free on-disk header probe (`_header_is_wal`, bytes 18/19 == 2, about 10 us). On a mismatch the key is dropped and the PRAGMA is re-issued. The PRAGMA-once property is kept (the spy test still counts 1). Regression test: `test_recreated_file_with_reused_inode_is_switched_to_wal` forces identical identities. It **fails on the old code** (`'delete' != 'wal'`) and passes on the new code.

Interleaved A/B torture (same seed, same sandbox, alternating):

| Build | rps | P50 | P95 | P99 | Verdict |
|---|---|---|---|---|---|
| fix | 863.5 | 95.45 | 141.72 | 201.53 | PASS |
| no-fix | 918.0 | 78.06 | 151.34 | 193.44 | PASS |
| fix | 919.6 | 79.19 | 147.72 | 196.33 | PASS |
| no-fix | 920.1 | 80.11 | 154.77 | 185.19 | PASS |
| fix | 902.6 | 79.98 | 146.35 | 179.66 | PASS |
| no-fix | 619.8 | 85.54 | 283.75 | 321.59 | PASS (sandbox outlier) |

Medians: fix 902.6 rps / P50 79.98 / P95 146.35 / P99 196.33; no-fix 918.0 / 80.11 / 154.77 / 193.44. There is no measurable difference, and outliers occur on both builds, so they come from sandbox noise. Full suite: **421 OK (19 skipped), 0 unclosed-database warnings.**
