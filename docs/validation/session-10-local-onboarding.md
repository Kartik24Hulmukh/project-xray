# Session-10: Local Dev Onboarding Hardening

Date: 2026-09-16

## Gap found
A clean clone of harden/project-xray-v1-launch failed `scripts/check_release.py` on first run: `Playwright Chromium binary is declared but not installed`. This is a real friction point for a new contributor, auditor, or CI runner without a warmed browser cache -- the repo had no single command to reach a green gate from bare metal.

## Fix
Added a `make setup` target (`npm ci` + `node node_modules/playwright/cli.js install chromium`). `make setup && make check && make test` now reaches green from a bare clone with zero tribal knowledge.

## Re-verification (post-fix)
- `python3 -m unittest discover -s tests`: 371 tests, 352 pass, 19 skip, 0 fail (~21s).
- `python3 scripts/check_release.py`: PASS rc=0 (capabilities, probes, SBOM, browser-acceptance E2E).
- `python3 scripts/session8_validate.py` (7-gate orchestrator): all_gates_pass=true.
  - stress_100_clients (20,000 reqs / 100 workers): reads P50 113.57ms / P95 200.16ms / P99 273.06ms @ 805.7 req/s; writes P50 179.27ms / P95 203.84ms / P99 210.43ms @ 491 req/s; peak RSS 63.3MB (ceiling 128MB); 0 tracebacks; audit chain intact (301 events).
  - fault_injection (seed 20260913): slowloris 60 held sockets served (P95 64.71ms); 30 mid-body drops recovered to 200; SIGKILL(-9) recovery in 158.89ms with identical committed id on replay; 0 tracebacks; no_5xx=true.
  - redteam_fuzz: malformed JSON, empty body, 2MB payload, no-auth, bad-token, path traversal (raw+encoded), null-byte path, unknown method, unicode, nested-bomb, type-confusion -- all within allowed status sets, 0 unhandled exceptions.

## Governance note
`production_ready` in the local receipt remains `false` by design: this is a local synthetic-SQLite rehearsal, not a live multi-tenant cloud deployment. The fail-closed release-claim guard (`check_release_claims.py`) continues to block conflating synthetic local perfection with live production, per prior council resolution.
