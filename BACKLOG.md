# Prioritized backlog

## P0 — must ship
- [ ] Evidence-state taxonomy enforced server-side.
- [ ] Human review gate before public claim status.
- [ ] Source URL, publisher, retrieval time, passage/page and hash fields.
- [ ] Projects, claims, documents, gaps, responses and audit events persisted.
- [ ] Public dossier with verified/disputed/missing distinctions.
- [ ] Evidence report and RTI draft export.
- [ ] Authentication for reviewer/admin writes in deployed mode.
- [ ] Upload type/size validation and malware-scanning integration point.
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
- [ ] Rate limits and abuse dashboard.

## P2 — post-launch
- [ ] Portal-specific collectors.
- [ ] Contractor entity resolution with FollowTheMoney.
- [ ] Defect-liability alerts.
- [ ] Trusted-monitor mobile capture.
- [ ] PFMS/treasury reference integration through authorized APIs.
- [ ] Satellite/BIM integrations.

## Measured September hardening delta
- [x] Reproduce/fix PostgreSQL concurrent audit forks and pool exhaustion.
- [x] Verify transactional response ordering and SQLite adapter backup direction.
- [x] Harden request framing/logging; bound handler admission.
- [x] Expand synthetic export/restart/restore smoke; add container CI contract.
- [ ] Pass actual container CI and target-environment operational/editorial gates.
- [x] Reconcile abandoned idempotency reservations safely (bounded dry-run-first CLI; SQLite + live PostgreSQL regressions; see docs/IDEMPOTENCY_MAINTENANCE.md).
- [ ] Benchmark large-ledger readiness cost and sustained deployment load.
See docs/HARDENING_2026_09.md for receipts and production no-go conditions.

### Launch-v1 measured blocker
100,000-event signed SQLite ledger: cold readiness audit verification 697.866 ms; warm helper P99 3.941 ms. Full chain revalidation remains synchronous and materializes checkpoints. This is not a 100x target-deployment certification; sustained deployment load and large-ledger memory ceilings remain open. See docs/validation/launch-v1-ledger.json.
