# Session 21 validation receipt: RAM floor measured in the persona torture harness (2026-09-25)

**Branch:** `harden/project-xray-v1-launch` (base `0d50ed3`) · **Seed:** 20260925 · **Concurrency:** 100 · **Personas:** 120 (285 requests/run)

## What changed
Sessions 17-20 recorded only the kernel high-water mark (`VmHWM`), so the RAM *floor* was inferred, never measured. `scripts/human_persona_torture.py` now:
- reads `VmRSS` right after `/readyz` first answers 200 (**floor**, before any load);
- runs a daemon `RssSampler` thread (10 ms cadence) across the load window and records min/max/sample count;
- reads `VmRSS` after the `/healthz` and `/readyz` recovery probes (**settled**) and reports `rss_growth_after_recovery_kb`;
- gates on `rss_settled_bounded_128mb` in addition to the existing peak gate;
- writes a real UTC timestamp instead of the hard-coded `2026-09-19T19:00:00Z` (a cosmetic mock removed).

No server source changed in this session. The measurement is instrumentation only, so no latency/throughput delta is claimed.

## Frozen baseline (before the edit) vs. after
| Run | Throughput | P50 (ms) | P95 (ms) | P99 (ms) | RAM floor | RAM ceiling | RAM settled | Health rec. | Ready rec. | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| base-1 | 970.1 rps | 59.67 | 133.98 | 183.06 | not measured | 47.5 MiB | not measured | 0.77 ms | 1.54 ms | PASS |
| base-2 | 939.2 rps | 81.29 | 141.66 | 176.86 | not measured | 47.6 MiB | not measured | 0.78 ms | 1.42 ms | PASS |
| base-3 | 903.0 rps | 68.50 | 149.64 | 209.95 | not measured | 47.5 MiB | not measured | 0.71 ms | 1.43 ms | PASS |
| post-1 | 952.8 rps | 63.23 | 146.66 | 187.28 | 31.7 MiB | 47.8 MiB | 47.8 MiB | 0.69 ms | 1.26 ms | PASS |
| post-2 | 869.8 rps | 64.67 | 141.18 | 205.52 | 31.8 MiB | 48.1 MiB | 48.1 MiB | 0.87 ms | 1.61 ms | PASS |
| post-3 | 790.9 rps | 100.58 | 190.03 | 226.48 | 32.0 MiB | 47.7 MiB | 47.7 MiB | 0.77 ms | 1.46 ms | PASS |

Medians, baseline -> after: throughput 939.2 -> 869.8 rps; P50 68.50 -> 64.67 ms; P95 141.66 -> 146.66 ms; P99 183.06 -> 205.52 ms; RAM ceiling 47.5 -> 47.8 MiB.

Runs last ~0.3 s on a shared sandbox; the spread is run-to-run noise. Zero tracebacks, zero crashes, health and readiness recovered in < 2 ms in all 6 runs.

## Reading the RAM numbers honestly
- **Floor ~31.8 MiB**: the server process at readiness with the SQLite schema applied and audit chain verified, before any request.
- **Ceiling ~47.8 MiB**: first-wave warm-up of the 64-thread HTTP worker pool, per-thread SQLite connections and Python allocator arenas.
- **Settled ~47.8 MiB = ceiling**: CPython does not return arena memory to the OS after a burst, so a settled value equal to the ceiling after ONE wave is expected and is **not** evidence of a leak. Proving no leak needs a multi-wave run in the same process (settled after wave N must not keep climbing). That is the next P1 and is now measurable because the floor and settled values exist.

## Test suite
- Before edit: `Ran 412 tests ... OK (skipped=19)` (25.2 s)
- After edit: `Ran 417 tests ... OK (skipped=19)` (24.6 s); +5 tests in `tests/test_torture_harness_rss.py`

## Reproduce
```
python3 -m unittest discover -s tests -q
python3 scripts/human_persona_torture.py --seed 20260925 --concurrency 100 --output docs/validation/session21-ram-floor-torture-2026-09-25.json
```
JSON receipt of post-edit run 1: `docs/validation/session21-ram-floor-torture-2026-09-25.json`.
