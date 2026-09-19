# Session 10 validation results

Scope: controlled synthetic technical preview, local SQLite only. No production certification, traction, or target-environment claim.

371 tests discovered: 352 passed, 19 target-gated skips, zero failures/errors. Seven-gate orchestrator passed; full release checker, IaC/workflow/waiver checks, production-named synthetic rehearsal, and Playwright UI acceptance passed. UI acceptance seeds synthetic data by API then exercises real Chromium dashboard/Refresh/dossier/export-link flow; not a real design-partner session. Separate desktop inspection confirmed empty-state and Refresh behavior.

## Frozen same-environment baseline → final run

| Phase | P50 ms | P95 ms | P99 ms | Requests/sec |
|---|---:|---:|---:|---:|
| baseline_reads | 1.04 → 1.25 | 1.76 → 2.46 | 2.58 → 2.79 | 811.69 → 684.09 |
| 100_client_reads | 104.53 → 106.86 | 183.47 → 189.16 | 254.88 → 261.93 | 878.31 → 843.99 |
| 100_client_writes | 232.29 → 203.7 | 434.36 → 268.67 | 450.48 → 289.35 | 320.41 → 438.94 |
| 100_client_idempotency_race | 60.9 → 82.21 | 89.58 → 139.42 | 104.02 → 142.36 | 659.78 → 572.75 |

RAM idle: 31896 → 31936 KiB. Peak: 56124 → 55648 KiB; enforced server ceiling 131072 KiB. These are process RSS, not total host/worker fleet memory.

Baseline plus two post-change runs; table reports the completed final run: differences are observational, NOT statistically established performance improvements. Browser/release activities may introduce host noise. Latency and throughput have no declared production SLO; measured 100-client read workload is 20,000 requests, plus 300 writes and 100 idempotent replays.

Stress verifies 301 persisted projects, 301 audit events, valid chain, one unique successful idempotent ID, process alive and zero tracebacks. SIGKILL/restart receipt measures harness recovery, not autonomous deployment recovery. Upstream collector tests cover synthetic exporter failure and local real OTLP sink; no target upstream timeout receipt is claimed.

## Evidence and reproducibility
- `baseline.json`: attachment SHA-256, frozen starting commit, baseline unit result/log hash.
- `baseline-stress.json`, `baseline-fault.json`, `baseline-gates.json`: untouched pre-source-change measurements.
- `validation.json`, `validation-stress.json`, `validation-fault.json`: final seven-gate evidence.
- `extra-gates.json`, `ui-acceptance.json`: additional execution results.
- Run `python3 scripts/session8_validate.py --output artifacts/hardening/validation.json`; `CHROMIUM_PATH=/usr/bin/chromium npm run ui-acceptance`; `python3 scripts/check_release.py` with Chromium configured.

Local Python: 3.14.6; platform: Linux x86_64. CI independently provisions Python 3.13 and PostgreSQL 16.4. Full target validation remains required. New receipt tests followed red → green; no module exceeded two remediation cycles.
