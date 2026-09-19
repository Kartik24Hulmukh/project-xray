# Session 12 - merge-blocker resolution & re-validation (2026-09-19)

**Branch:** `harden/project-xray-v1-launch` (base `554de03`) - **PR:** #52 - **Scope:** LOCAL synthetic SQLite only; `production_ready=false`, ledger 0/10. GO for isolated synthetic preview (<=3 design partners), NO-GO for production/GA.

## Frozen baseline (before edits)
- `unittest discover -s tests` -> 371 tests, OK (skipped=19 PostgreSQL-live/TLS). Sandbox interpreter: CPython 3.14.
- PR #52 `mergeable_state=blocked`: required check **`blocking-security` FAILED** (run 35456013342) - grype image scan: `python 3.13.15` CVE-2026-82049 High (fixed 3.14.0b1) and `zlib 1.3.2-r0` CVE-2026-85091 High (no fix). `postgres`, `unit-and-rehearsal`, `container-smoke`, GitGuardian all green. 0 approving reviews.
- `repos.md`: NOT FOUND in tree (`find . -name repos.md`) - confirmed for the 4th session; no catalog to ingest. `jittest`: no file/module/reference anywhere in repo or attachments - interpreted as the test/hardening loop itself.

## Council premortem -> resolutions this session
| Seat | Vector | Resolution |
|---|---|---|
| Systems Architect | CI red on required check silently tolerated -> unmergeable launch branch | Structural: base image `python:3.13-alpine` -> `python:3.14-alpine` (both stages) removes CVE-2026-82049 without a waiver; 371 tests already proven on 3.14 |
| Red Team | blanket CVE suppression to force green | Refused. zlib waiver is CVE-scoped + package-scoped + 14-day expiry + written exposure analysis (`docs/security/risk-acceptance-2026-09-19.md`); guard `check_grype_exceptions.py` fails closed on expiry (5/6 max CVEs) |
| SF Founder | README says v0.4.1 while package is 0.4.8 -> buyer trust loss (GPT-6 Astra memo P0) | README header now `v0.4.8-synthetic-preview-rc1`, cross-references RELEASE_NOTES/package.json; release-claim guard still PASS |
| Research Scientist | non-reproducible receipts | Seed 20260913, raw JSON committed: `stress_100x_5k_session12.json`, `fault_injection_session12.json` |
| 100-Persona Swarm | governance bypass | Merge still requires human approving review + green required checks; NOT force-merged |

## Re-validation (post-edit, same sandbox)
- Unit/integration/E2E: **371 tests OK (skipped=19)**, 0 unhandled exceptions.
- `check_grype_exceptions.py`: OK - 5 package-scoped suppressions, expires in 14 days (2026-10-03).
- `check_release_claims.py`: PASS - ledger 0/10, no unsupported maturity claim.

| Phase (100 clients) | P50 | P95 | P99 | rps | statuses | vs session-11 (20k) |
|---|---|---|---|---|---|---|
| baseline_reads 1c x100 | 1.25 ms | 1.63 ms | 1.92 ms | 752 | 100x200 | 1.96/2.44/2.61 |
| 100c reads x5000 | 104.22 ms | 190.03 ms | 295.91 ms | 883 | 5000x200 | 119.92/209.53/294.37 (-9% P95, host noise) |
| 100c writes x300 | 174.48 ms | 338.84 ms | 352.68 ms | 401 | 300x201 | 277.99/309.04/343.20 |
| idempotency race 100c x100 | 57.82 ms | 116.84 ms | 118.64 ms | 606 | 97x201 + 3x409, **1 unique id** | 54.72/104.55/111.61 |

RAM floor 31.4 MiB -> ceiling 53.0 MiB (128 MiB gate, pass). 301 projects == 301 audit events, integrity ok, 0 tracebacks, healthz 0.82 ms / readyz 4.03 ms after load. All launch gates pass.

Fault/chaos (seed 20260913): slowloris 60 held sockets 200x200 (p95 45.75 ms, served_while_held); 30 mid-body drops -> 300x200; out-of-order idempotent replay 49x201 + 1x409, 1 unique id; **SIGKILL recovery 105.04 ms**, replay same committed id; ready/live 200; no 5xx. Red-team fuzz 14/14 pass, health 200 after.

No statistically significant perf claim (5k vs 20k sample, single host).

## Remaining blockers (unchanged, honest)
Production PG16 verify-full, PITR RPO/RTO receipts, real ingress MFA, object-store quarantine, 2x human-reviewed dossiers, legal/privacy, design-partner traction -> all `pending` (0/10). CI must re-run `blocking-security` on the new head to confirm the grype scan is green; zlib waiver requires founder countersign on the PR review.
