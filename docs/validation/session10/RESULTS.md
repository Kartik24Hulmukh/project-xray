# Session 10 validation results

Scope: controlled synthetic technical preview, local SQLite only. No production certification, traction, or target-environment claim.

371 tests discovered: 352 passed, 19 target-gated skips, zero failures/errors. Seven-gate orchestrator passed; full release checker, IaC/workflow/waiver checks, production-named synthetic rehearsal, and Playwright UI acceptance passed. UI acceptance seeds synthetic data by API then exercises real Chromium dashboard/Refresh/dossier/export-link flow; not a real design-partner session. Separate desktop inspection confirmed empty-state and Refresh behavior.

## Frozen same-environment baseline → final run

| Phase | P50 ms | P95 ms | P99 ms | Requests/sec |
|---|---:|---:|---:|---:|
| baseline_reads | 1.04 → 1.18 | 1.76 → 2.27 | 2.58 → 2.69 | 811.69 → 750.35 |
| 100_client_reads | 104.53 → 106.13 | 183.47 → 191.38 | 254.88 → 260.22 | 878.31 → 862.56 |
| 100_client_writes | 232.29 → 183.65 | 434.36 → 222.28 | 450.48 → 240.76 | 320.41 → 503.49 |
| 100_client_idempotency_race | 60.9 → 101.76 | 89.58 → 172.7 | 104.02 → 177.1 | 659.78 → 473.41 |

RAM idle: 31896 → 31776 KiB. Peak: 56124 → 56272 KiB; enforced server ceiling 131072 KiB. These are process RSS, not total host/worker fleet memory.

One run per baseline/final measurement: differences are observational, NOT statistically established performance improvements. Browser/release activities may introduce host noise. Latency and throughput have no declared production SLO; measured 100-client read workload is 20,000 requests, plus 300 writes and 100 idempotent replays.

Stress verifies 301 persisted projects, 301 audit events, valid chain, one unique successful idempotent ID, process alive and zero tracebacks. SIGKILL/restart receipt measures harness recovery, not autonomous deployment recovery. Upstream collector tests cover synthetic exporter failure and local real OTLP sink; no target upstream timeout receipt is claimed.

## Evidence and reproducibility
- `baseline.json`: attachment SHA-256, frozen starting commit, baseline unit result/log hash.
- `baseline-stress.json`, `baseline-fault.json`, `baseline-gates.json`: untouched pre-source-change measurements.
- `validation.json`, `validation-stress.json`, `validation-fault.json`: final seven-gate evidence.
- `extra-gates.json`, `ui-acceptance.json`: additional execution results.
- Run `python3 scripts/session8_validate.py --output artifacts/hardening/validation.json`; `CHROMIUM_PATH=/usr/bin/chromium npm run ui-acceptance`; `python3 scripts/check_release.py` with Chromium configured.

Local Python: 3.14.6; platform: Linux x86_64. CI independently provisions Python 3.13 and PostgreSQL 16.4. Full target validation remains required. New receipt tests followed red → green; no module exceeded two remediation cycles.
