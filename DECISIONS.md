# Architecture decision log

## ADR-001 — Evidence engine, not corruption detector
**Date:** 2026-07-12
**Decision:** Classify and connect evidence; do not predict guilt.
**Reason:** Reduces defamation, hallucination and false-positive risk.

## ADR-002 — No real payment movement in v0.1
**Date:** 2026-07-12
**Decision:** Generate payment-readiness evidence only in later phases.
**Reason:** Payment authority, financial regulation and oracle verification are outside a 72-hour scope.

## ADR-003 — Standards-first
**Date:** 2026-07-12
**Decision:** Plan compatibility with OCDS, OC4IDS and FollowTheMoney.
**Reason:** Avoid a closed India-only ontology.

## ADR-004 — Dependency-light reference slice
**Date:** 2026-07-12
**Decision:** Ship an offline-runnable SQLite/stdlib reference implementation with migration path.
**Reason:** Guarantees reproducible startup while Codex builds the hardened target stack.

## ADR-005 — Independent review is a state transition
**Date:** 2026-07-14
**Decision:** Every claim starts as a candidate and requires two distinct reviewer approvals before publication; corrections invalidate old approvals.
**Reason:** Publication safety must be enforced by data and API rules, not documentation.

## ADR-006 — Audit and recovery are release gates
**Date:** 2026-07-14
**Decision:** Use append-only hash-chained audit events, online database backups and a clean-directory restore smoke test.
**Reason:** Evidence provenance is not credible without tamper evidence and demonstrated recovery.

## ADR-007 — Public beta until operator gates pass
**Date:** 2026-07-14
**Decision:** Do not label the reference SQLite/token deployment v1 production.
**Reason:** OIDC/MFA, managed storage, deployment security, human editorial review and legal/operational ownership require real operator evidence.

## ADR-008 — Version-bound transactional publication
**Date:** 2026-07-14
**Decision:** Serialize state transitions with `BEGIN IMMEDIATE`; bind approvals to claim versions and make publication idempotent.
**Reason:** Concurrent corrections and approvals must never publish stale or partially reviewed evidence.

## ADR-009 — External trust anchors
**Date:** 2026-07-14
**Decision:** Authenticate audit checkpoints, backups, monitoring events and recovery evidence with keys held outside the database.
**Reason:** An internally rehashed database fork is not detectable without an external secret or signature.

## ADR-010 — Production identity and storage boundaries
**Date:** 2026-07-14
**Decision:** Disable bearer authentication in production, require fresh MFA-marked gateway assertions, verify managed-object metadata, and keep evidence quarantined until an independent scanner clears it.
**Reason:** Production safety depends on fail-closed boundaries, not operator convention.

## September 2026 runtime hardening
- Serialize PostgreSQL transitions and global audit verification with transaction advisory lock 1481785689. Correctness before throughput; see docs/HARDENING_2026_09.md.
- Bound HTTP handlers and PostgreSQL admission; use ingress for total deadlines and distributed abuse controls.
- Acknowledge JSON mutations after commit, not before. Preserve existing publication and evidence gates.
