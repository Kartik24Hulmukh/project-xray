# Runtime hardening: measured progress, not production certification

Requested planning date: 13 September 2026. Execution timestamps on this sandbox
are 12 September 2026 UTC (13 September IST). Base: `4a6fedc` on upstream main.

## Decision

**GO for a controlled synthetic research preview. NO-GO for unrestricted public
production or real-case publication.** This change fixes reproduced runtime
failures, not the entire product roadmap. The previous blueprint is a proposal,
not evidence of integrations, traction, legal safety or performance. No independent
AI-agent service was available: testing and inspection jobs ran in parallel under
one assistant. No claim of a multi-agent council or independent expert review.

## Reproduced failures → fixes

| Failure | Change | Evidence |
|---|---|---|
| PostgreSQL pool exhaustion and concurrent audit-chain forks | Bounded pool admission; shared transaction-level advisory lock for write transitions, audit append and verification | Before: 65/100 writes returned 500 at 30 clients and readiness failed. After: 300/300 writes returned 201 at 100 clients, readiness 200; 100 concurrent audit appends verified |
| Success response emitted before commit | Buffer transactional JSON response until commit completes | Regression simulates commit failure and asserts no success acknowledgement |
| Runtime container omitted dashboard and recovery import | Copy static assets and recovery.py | File-contract test; new actual-container CI job (not executed locally) |
| Adapter-to-adapter SQLite backup reversed direction | Copy source connection into destination | In-memory roundtrip test preserves source and restores target |
| JSON arrays/scalars, invalid encoding, NaN and deep nesting produced unsafe errors | Reject non-object/invalid JSON and ambiguous request framing | Input-boundary regressions |
| Arbitrary request ID reflected; exception text in logs could contain secrets | Conservative 64-character IDs; stack frame metadata without exception text | Header and log-sentinel regressions |
| Unbounded HTTP worker creation | 64 active handlers, bounded listen backlog and admission backpressure | Saturation/recovery regression; 100-client workload |
| Prototype overload rejection reset POST connections | Replaced early socket closure with accept-loop backpressure | First stress iteration failed; second passed safety invariants |
| Draft RTI could look file-ready | Explicit synthetic/do-not-file label; applicant/address placeholders and filing checks | Expanded smoke verifies RTI and report paths |

The audit lock deliberately trades write parallelism for a correct single global
chain. It is not a 100x scalability claim. All application writers must honor the
same advisory lock. Restrict direct database writes. Existing corrupted chains
are **not repaired** by this patch: verify and restore a trusted backup; never
rewrite history silently.

## Measurements

All data is synthetic. Local SQLite runs raise rate limits to measure application
behavior, not deployed abuse controls. Compare 1 client with 100 clients; this is
**100x client concurrency, not 100x traffic capacity or 100x value**.

| Workload | Requests | Results | p95 |
|---|---:|---|---:|
| SQLite, 1-client reads | 100 | 100 × 200 | 2.08 ms |
| SQLite, 100-client reads | 2,000 | 2,000 × 200 | 896.58 ms |
| SQLite, 100-client writes | 300 | 300 × 201 | 775.22 ms |
| SQLite, 100-client same-key race | 100 | 56 × 201 replay; 44 × 409 in-progress | 258.62 ms |
| PostgreSQL 16.4, 100-client writes | 300 | 300 × 201 | 783.31 ms |

SQLite: exactly 301 projects persisted (300 unique writes plus one replayed
request); all 301 audit events verified, database integrity `ok`, readiness 200.
The race produced exactly one resource ID. Zero-rejection capacity did **not**
pass because in-progress idempotency conflicts are intentional 409s. Clients
must retry the same key after an appropriate delay. Results vary by hardware,
load and dataset size; this small synthetic dataset is not a soak benchmark.

Validation on this branch:
- Standard discovery: 215 tests, 208 passed, 7 PostgreSQL-specific skips.
- Those 7 PostgreSQL tests separately executed against a real local 16.4 instance:
  6 live tests plus 1 concurrency test passed.
- Expanded SQLite end-to-end smoke: create/source/claim/two reviews/publication,
  gap/response, report, RTI, capsule verification, audit, assets, restart and
  backup restore passed.
- PostgreSQL end-to-end create/review/publish/export flow passed (separate receipt).
- Playwright Chromium UI acceptance passed; release checker and synthetic
  production-mode rehearsal passed. Storage, monitoring and identity in that
  rehearsal are local fixtures, not verified AWS integrations.
- npm dependency audit: no vulnerabilities reported by npm install.
- Docker was unavailable here. PostgreSQL test binaries came from Maven Central
  `io.zonky.test.postgres:embedded-postgres-binaries-linux-amd64:16.4.0`, used only
  for a disposable loopback test database, not deployed or added to the product.

Machine-readable receipts: `docs/validation/`. Reproduce SQLite load with
`python3 scripts/stress_local.py`. For a disposable PostgreSQL DB, initialize
`db/schema_postgres.sql`, set `DATABASE_URL`, then run:

```sh
python3 -m unittest discover -s tests -p 'test_postgres_live.py' -v
python3 -m unittest discover -s tests -p 'test_postgres_concurrency.py' -v
```

Do not run write tests against a production database. Use a fresh fixture DB.

## Premortem / launch gates

| Risk | Remaining gate / owner |
|---|---|
| Real evidence wrong or legally misleading | Two independent human source reviewers and Indian legal/editorial review. No legal guarantees from descriptive wording alone. Founder owns go/no-go. |
| Public hosting bypasses identity | Deploy TLS ingress, prove app port is unreachable except via gateway, validate real IdP, MFA and role mapping. Operator. |
| Readiness scan or slow clients exhaust capacity | `/ready` verifies the full audit chain; benchmark large ledgers and long soak; ingress absolute deadlines, per-IP limits and trusted proxy policy. SRE. |
| Interrupted idempotency reservation stays processing | Define operator reconciliation for abandoned keys; do not blindly expire potentially committed external side effects. API owner. |
| Database restore/managed storage recovery unproven | PostgreSQL backup/restore drill plus real S3 version/scan attestation and alert receipts. Operator. |
| Container/platform incompatibility | New container CI must pass; scan built image and pinned dependency/SBOM results before deploy. Maintainer. |
| Real documents never deliver product value | Evaluate 10 authorized public PDFs with page-level extraction precision, discrepancy false-positive rates and reviewer time. Product/evidence lead. |
| Startup has no customer pull | Observe 5 design partners complete dossier tasks; measure time saved and repeat use. No invented star, revenue or impact forecasts. Founder. |

## Product scope that is still missing

The blueprint's Graphify/Surya/OCR ingestion, automatic discrepancy engine,
1,500 pages in 60 seconds, validated PIO-address resolution, and traction claims
are **not implemented or validated by this change**. Existing document endpoints
track metadata; a deterministic extraction/BOQ comparison vertical slice needs
its own source-grounded acceptance dataset. Do not replace the current stack or
add heavyweight agents before proving that narrow workflow.

Next sprint: fix remaining P0 operational gates, then one source-preserving
PDF-to-candidate discrepancy flow, reviewed by real design partners. Keep the
human publication gate. Measure evidence accuracy and task completion rather
than claiming guaranteed growth.

## Rollout and rollback

1. Revoke/rotate the GitHub credential pasted into the conversation. It was used
   only for authorized GitHub actions, not written into the repo or deliverables.
2. Review this PR, run CI including the actual image, take and verify backups.
3. Deploy to private staging with synthetic records only. Check `/ready`, UI,
   two-person review, quarantine, exports, recovery and observed latency.
4. This patch has no schema migration. Roll back code/image if necessary, but
   remember older code reintroduces concurrency defects. Disable writes while
   investigating; preserve audit records. A broken existing chain must be
   handled as an incident, not silently repaired.
5. Open public access only after the operator/editorial gates above have owners
   and verified receipts. Never equate passing this test suite with certification.
