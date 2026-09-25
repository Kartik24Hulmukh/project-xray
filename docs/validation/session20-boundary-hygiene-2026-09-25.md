# Session 20 validation receipt — write-boundary hygiene (2026-09-25)

Env: Python 3.14, SQLite, shared sandbox. Commands run from repo root.

## Suite
| Stage | Command | Result |
|---|---|---|
| Baseline (e31246c, pre-edit) | `python3 -m unittest discover -s tests` | Ran 402 tests in 23.2 s — OK (skipped=19) |
| Post-edit | same | Ran 412 tests in 27.4 s — OK (skipped=19) |
| New file | `python3 -m unittest tests.test_boundary_control_hygiene` | Ran 10 tests — OK |

Skips = documented live-PostgreSQL/TLS gates (unchanged).

## Torture: `python3 scripts/human_persona_torture.py --seed 20260925 --concurrency 100` (120 personas, 285 requests)
| Run | Throughput | P50 | P95 | P99 | Peak RSS | Health rec. | Ready rec. | Verdict |
|---|---|---|---|---|---|---|---|---|
| base-1 | 994.0 rps | 63.99 | 137.60 | 180.71 | 47.9 MiB | 0.65 ms | 1.32 ms | PASS |
| base-2 | 996.5 rps | 63.37 | 136.93 | 178.13 | 47.7 MiB | 0.61 ms | 1.49 ms | PASS |
| base-3 | 966.7 rps | 72.56 | 140.04 | 183.52 | 47.7 MiB | 0.67 ms | 1.37 ms | PASS |
| post-1 | 870.8 rps | 74.01 | 148.25 | 187.11 | 47.3 MiB | 0.67 ms | 1.35 ms | PASS |
| post-2 | 961.0 rps | 64.50 | 144.08 | 206.23 | 47.9 MiB | 0.69 ms | 1.64 ms | PASS |
| post-3 | 1005.6 rps | 67.11 | 131.23 | 170.82 | 47.8 MiB | 0.65 ms | 1.35 ms | PASS |

Medians base→post: throughput 994.0→961.0 rps, P50 63.99→67.11 ms, P95 137.60→144.08 ms, P99 180.71→187.11 ms, RSS ceiling 47.7→47.8 MiB. All deltas are within observed run-to-run noise (~0.3 s runs); no performance claim is made either way. RAM floor still not measured separately.

## Root causes closed
- `clean()` accepted C0/DEL (NUL stored with 201) → now 400.
- `responses.source_id` list/int/dict/bool reached SQLite binder (500) → now 400.
- `claims.source_id` non-string → explicit 400.
- `gaps.status` unvalidated (storage CHECK surfaced as 409) → 400; set asserted equal to both schema CHECKs.
- `verify_segment` `.fetchall()` → streaming `fetchmany(256)`.

Concurrent hostile test: 96 writes / 32 threads → exactly 72×400 + 24×201, zero 500s, /healthz and /readyz 200 afterwards.
