# Prioritized backlog

## P0 — must ship
- [ ] Evidence-state taxonomy enforced server-side.
- [ ] Human review gate before public claim status.
- [ ] Source URL, publisher, retrieval time, passage/page and hash fields.
- [ ] Projects, claims, documents, gaps, responses and audit events persisted.
- [ ] Public dossier with verified/disputed/missing distinctions.
- [ ] Evidence report and RTI draft export.
- [ ] Authentication for reviewer/admin writes in deployed mode.
- [x] Upload type/size validation and malware-scanning integration point. (Session 17: `validate_document_metadata` enforces strict int size, inert basenames, extension↔media-type agreement and typed source_id; scanning integration point is the pre-existing quarantine → scanner-role `/scan` gate.)
- [ ] Database backup and tested restore.
- [ ] HTTPS deployment, security headers, structured logs and health checks.
- [ ] Factual review checklist for launch cases.
- [ ] Accessibility and mobile smoke test.

## P1 — ship only after P0
- [ ] PDF text extraction with page anchors.
- [ ] OCR queue and retry/dead-letter states.
- [ ] OCDS/OC4IDS export.
- [ ] Duplicate media/document detection.
- [ ] Marathi/Hindi human-reviewed summaries.
- [ ] Correction submission workflow.
- [x] Rate limits and abuse dashboard. (session 16: admin-only `GET /api/admin/abuse`, bounded HMAC-fingerprinted offender ledger)

## P2 — post-launch
- [ ] Portal-specific collectors.
- [ ] Contractor entity resolution with FollowTheMoney.
- [ ] Defect-liability alerts.
- [ ] Trusted-monitor mobile capture.
- [ ] PFMS/treasury reference integration through authorized APIs.
- [ ] Satellite/BIM integrations.

## Session 15 continuation (2026-09-19)
- [x] Closed docstring/code gap in `scripts/human_persona_torture.py`: module docstring already promised a "mobile field monitor" persona archetype; `ARCHETYPES` only had 7. Added the 8th archetype (`mobile_field_monitor`, 15 profiles), bringing the harness to 120 personas across 100 workers.
- [x] Re-ran full unit/integration/E2E suite after the change: 372 OK, 19 explicit live-DB/TLS skips, 0 failures.
- [x] Re-ran `scripts/check_release_claims.py`: PASS, readiness ledger honestly still 0/10 evidenced production checks (no external gate manufactured in sandbox).
- [x] Fresh persona-torture receipt: `docs/validation/session15-human-torture-2026-09-19.json` (120 personas, 100 workers, 0 tracebacks, peak RSS 38.0 MiB, health recovery 1.48 ms, ready recovery 2.73 ms).
- [ ] PR #60 (session 14, adds the harness to `main`) remains open: branch protection on `Kartik24Hulmukh/project-xray` requires "at least 1 approving review" and blocks self-approval by the PR author's own token — confirmed via the GitHub REST API (`422 Can not approve your own pull request`, then `405` on merge). This is an intentional human governance gate, not a bug; it is not something an autonomous agent should bypass. Opened session-15 PR on top of the same branch with the same gate.
- [ ] External/human gates unchanged and still correctly pending: real target deployment, live PostgreSQL PITR, real OIDC/MFA ingress, object-store quarantine, legal/editorial sign-off, design-partner adoption. `repos.md` remains absent from the repository (checked again, 7th consecutive session).

## Measured September hardening delta
- [x] Reproduce/fix PostgreSQL concurrent audit forks and pool exhaustion.
- [x] Verify transactional response ordering and SQLite adapter backup direction.
- [x] Harden request framing/logging; bound handler admission.
- [x] Expand synthetic export/restart/restore smoke; add container CI contract.
- [ ] Pass actual container CI and target-environment operational/editorial gates.
- [x] Reconcile abandoned idempotency reservations safely (bounded dry-run-first CLI; SQLite + live PostgreSQL regressions; see docs/IDEMPOTENCY_MAINTENANCE.md).
- [x] Benchmark large-ledger readiness cost (cold 697.866 ms; warm helper P99 3.941 ms; docs/validation/launch-v1-ledger.json). Sustained target-deployment load remains open (Gate B).
See docs/HARDENING_2026_09.md for receipts and production no-go conditions.

### Launch-v1 measured blocker
100,000-event signed SQLite ledger: cold readiness audit verification 697.866 ms; warm helper P99 3.941 ms. Full chain revalidation remains synchronous and materializes checkpoints. This is not a 100x target-deployment certification; sustained deployment load and large-ledger memory ceilings remain open. See docs/validation/launch-v1-ledger.json.
