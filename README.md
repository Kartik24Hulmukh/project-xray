# Project X-Ray India

**What was promised. What changed. What was built. Show the evidence.**

Open-source evidence-workflow reference for Indian public-infrastructure research.

| Field | Value |
|---|---|
| Preview mode | **Controlled synthetic technical preview** |
| Code package | v0.4.1 — repository-verified controlled-beta; production gates pending |
| Operator readiness ledger | `controlled_synthetic_preview` / alpha gates in `ops/production-readiness.yaml` |
| Package tag | `v0.4.1-synthetic-preview` |
| Licence | Apache-2.0 |

## Honest scope

Project X-Ray helps humans assemble **source-linked dossiers**, record missing evidence, run **two-person review**, and export review/RTI material.

It does **not**:

- determine corruption, guilt, intent, legality, or fitness for office;
- move public funds or replace PFMS/GeM/PAIMANA;
- treat news reports as proof;
- authorize real-case publication merely because the software runs.

## Monday 20 July 2026 launch mode

**GO: controlled synthetic technical preview** for invited evaluators.

- Synthetic data only
- No real-person/project allegations
- No traction/impact claims
- Kill switch required before any hosted surface

Read:

1. `docs/KNOWN_LIMITATIONS.md`
2. `docs/launch/POSITIONING.md`
3. `docs/launch/GO_NO_GO.md`
4. `docs/launch/FOUNDERS_COUNCIL_VERDICT.md`
5. `docs/legal/DISCLAIMER.md`
6. `docs/ops/KILL_SWITCH_RUNBOOK.md`
7. `docs/SYNTHETIC_PREVIEW.md`

## What works in this package

- Create and list projects
- Source-linked candidate claims with explicit evidence states
- Two distinct reviewers before publication and after corrections
- Document metadata with SHA-256 and quarantine state
- Missing-document gaps and authority responses
- Evidence report and draft RTI output
- SQLite local path and PostgreSQL schema/runtime path
- JSON API and public dashboard
- Race-safe review/publish transitions
- Production-oriented OIDC gateway assertion verification paths
- Fail-closed quarantine with separate scanner role
- Audit checkpoints, backup/restore tooling, capsule export/verify
- Unit tests, browser acceptance, smoke and release checks

## Quick start

```bash
cp .env.example .env
# Optional PostgreSQL: set DATABASE_URL=postgresql://...
# and apply db/schema_postgres.sql before first start.
# The server does not load .env automatically. Export its values first:
set -a; . ./.env; set +a
python3 app/server.py
# open http://localhost:8080
```

Run verification:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_release.py
python3 scripts/smoke_e2e.py
python3 scripts/rehearse_production.py
python3 scripts/external_evaluator.py
python3 scripts/verify_capsule.py capsule.json
```

Docker:

```bash
docker compose up --build
```

## Production and deployment

Do not label a deployment production-ready merely because it boots. Target gates live in:

- `docs/PRODUCTION_READINESS.md`
- `docs/PRODUCTION_DEPLOYMENT.md`
- `ops/production-readiness.yaml`
- `docs/roadmap/GUMLOOP_AWS_NEXT_STEPS.md`

## No-fake-claims rule

- Synthetic records must display `SYNTHETIC` promisently.
- “Not found” means “not located in searched sources,” never “does not exist.”
- An official statement is an official claim, not independent verification.
- A risk indicator is a review prompt, not evidence of corruption.
- No real case may be public until two-person source review and operator legal/editorial gates pass.

## Start here

1. `AGENTS.md`
2. `docs/SYNTHETIC_PREVIEW.md`
3. `docs/ACCEPTANCE_CRITERIA.md`
4. `docs/EVIDENCE_POLICY.md`
5. `docs/metrics/TRACTION_DEFINITIONS.md`
6. `docs/roadmap/MONDAY_TO_90_DAY_ROADMAP.md`

## Verified runtime hardening (September 2026)

See `docs/HARDENING_2026_09.md` for measured results, remaining blockers,
and why passing synthetic tests is **not production certification**.
Local CLI startup binds to `127.0.0.1` by default; the container explicitly
sets `BIND_HOST=0.0.0.0`. Keep its port private behind an authenticated TLS ingress.

```bash
npm ci
# If using a system Chromium:
CHROMIUM_PATH=/usr/bin/chromium python3 scripts/check_release.py
python3 scripts/smoke_e2e.py
python3 scripts/stress_local.py  # disposable local synthetic database only
```

`MAX_HTTP_WORKERS` defaults to 64 and bounds *held* connections (memory and
file descriptors). `HTTP_EXEC_PARALLELISM` (default 4) bounds how many admitted
handlers *execute* at once; it is taken after headers are parsed, so slow
clients cannot starve it. CPython threads contending for the GIL on SQLite-bound
handlers collapse throughput ~8x when 64 are runnable at once, so keep this
small: `2` on CPU-only SQLite nodes, `4` (default) when handlers do network I/O
(managed-storage HEADs, alert webhooks). PostgreSQL connections are bounded by
`DB_POOL_MAX` (default 10); admission waits up to 5 seconds before a retryable
503. Global audit-chain writes are serialized for correctness, not advertised
as unlimited write throughput. Ingress must enforce total request deadlines,
body limits, rate limits and trusted forwarding headers.

## Health probes

| Path | Kind | Checks | Auth | Rate limited |
|---|---|---|---|---|
| `/healthz` | liveness (canonical) | process alive, version reported | none | no |
| `/health`, `/livez` | liveness (compatibility aliases) | identical body to `/healthz` | none | no |
| `/readyz` | readiness (canonical) | database reachable + audit chain verified | none | no |
| `/ready` | readiness (compatibility alias) | identical body to `/readyz` | none | no |

Orchestrators should poll `/healthz` (liveness) and `/readyz` (readiness); the other paths exist only so already-deployed manifests keep working. Probe paths are exempt from the rate limiter so aggressive kubelet polling can never mark a healthy pod unready. Contract is pinned by `tests/test_probes.py`.
