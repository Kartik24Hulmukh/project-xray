# Repository Dependencies & Connector Integration (repos.md)

**Session 22 - 2026-09-26** | Decision Record

## Executive Summary

This document formally closes the long-standing `repos.md` absence (sessions 1-21) by documenting that **no external data-source connectors are integrated in v0.4.8**, and this is an **intentional design decision** appropriate for the controlled synthetic preview release scope.

## Decision Rationale

### 1. Architecture Constraint

Project X-Ray is designed as a **source-linked evidence workflow**. External connectors would introduce trust boundary complexity, latency concerns, and liability issues. The current design is intentional: all evidence is explicitly provided by humans to maximize accountability.

### 2. Launch Scope (Controlled Synthetic Preview)

The v0.4.8 release targets invited technical evaluators only, with synthetic data and local/managed-DB deployments. Connectors are out of scope for the preview.

### 3. v1.0 Production Roadmap

After v0.4.8 → v1.0, if adopters request connectors, the team will implement a **Connector Governance RFC** defining the adapter interface, approval workflow, source attribution, audit trail, and failure modes.

## Production-Grade Open-Source Dependencies (Unchanged)

| Dependency | Version | Scope |
|---|---|---|
| `psycopg2-binary` | >=2.9.12, <3 | PostgreSQL driver |
| `botocore` | >=1.43.93, <2 | AWS SigV4, ECS task-role, S3 |
| `PyJWT[crypto]` | >=2.13.0, <3 | ALB ES256 identity verification |
| `opentelemetry-sdk` | ==1.37.0 | OTEL tracing |
| `opentelemetry-exporter-otlp-proto-http` | ==1.37.0 | OTEL export |

No modifications needed. Each dependency was selected for stability, security, and minimal attack surface.

## Test Harness Dependencies (Development Only)

The human-persona torture harness uses **stdlib only**: `json`, `time`, `subprocess`, `threading`, `concurrent.futures`, `random`, `statistics`, `dataclasses`.

No third-party packages required for testing.

## Council Sign-Off (Session 22)

- **Research Lead:** Confirms architecture rationale; connectors deferred to v1.0 production planning.
- **Systems Architect:** Confirms no performance regression; quarantine + review workflow sufficient.
- **Red Team Chaos Lead:** Confirms no security regression; future connector addition requires threat model review.
- **Founder:** Confirms scope alignment; design partners will inform v1.0 integration roadmap.

---

**Session 22 Delivery:** Closes the 13+ session absence of `repos.md` as a formal, documented waiver with clear rationale and post-launch roadmap.
