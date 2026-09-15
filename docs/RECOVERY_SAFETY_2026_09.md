# Recovery safety review — September 2026 launch

## Verdict
**NO-GO for unrestricted production; controlled synthetic preview only.**
Prior main `370de1a` has green post-merge CI and Security checks. That closes the
previous queued-CI item, not the target-environment/legal/adoption gates.

## New verified defects and fixes
1. PostgreSQL restore skipped authenticated-manifest verification entirely.
   Both backends now authenticate the archive before invoking restore.
2. PostgreSQL restore ignored `force` and always used destructive `--clean`.
   Explicit `--force` is now required; `DATABASE_URL` is the target, not the
   positional destination (which remains a compatibility placeholder).
3. Automatic PostgreSQL recovery evidence restored into the source URL.
   It now refuses before backup. Use separate processes and a disposable target.
4. Nonzero `pg_restore` could become success if the old database passed integrity.
   All nonzero exits now fail; single-transaction/exit-on-error are mandatory.
5. URI-encoded passwords were not decoded, TLS query settings were discarded,
   and ECS split credentials were unsupported in recovery. Reuse the runtime
   DSN builder, decode credentials, preserve supported libpq TLS settings and
   reject unknown query options rather than silently dropping them.
6. Recovery evidence hardcoded RPO=0 and pass=true. Local roundtrips now report
   `rpo_seconds: null`, `rpo_pass: false`, `rpo_status: not_measured`.

## Evidence
- Eleven new focused safety tests pass. Against the unchanged old modules,
  the same suite fails with 4 failures and 6 errors (one test passes).
- A real, isolated PostgreSQL 16.2 instance was provisioned in this sandbox.
  Five existing PostgreSQL test suites passed without the prior live-PG skips.
- New live recovery test: separate source and target, authenticated restore,
  equal project rows/audit head, missing force refused, tampered archive refused,
  authenticated invalid archive refused; existing target unchanged after refusals.
- CI now creates two additional disposable databases and runs that live regression.
- Full release gate, browser acceptance, rehearsal, 2,600-request stress,
  fault injection, rate-limit shock, fuzz, workflow/IaC contracts passed locally.
- One 100k-ledger benchmark run hit an unhandled `AuditVerificationPending`
  after its finite cold-probe loop; an isolated rerun passed (cold p95 25.624ms,
  max 131.990ms, steady p99 0.087ms, scan ratio 1.0, RSS 94.38MiB).
  The benchmark now emits a failed JSON receipt (rather than a traceback) when
  cold probes do not converge; a deterministic regression pins that behavior.
  Convergence under every load remains unproven; no thresholds were loosened.
- `scripts/readiness_status.py` still returns 1: 0/10 target gates passed.

## Safe PostgreSQL rehearsal (operator-owned)
Use only authorized databases and secret injection, not credentials in CLI arguments.
1. Quiesce the source for deterministic snapshot/audit comparisons. Take an
   authenticated backup with `DATABASE_URL` set to the source and secure HMAC keys.
2. In a NEW process, set `DATABASE_URL` to a freshly provisioned isolated target.
   Run `python3 scripts/recovery.py restore backup.dump DATABASE_URL --force`.
   Never reuse a production URL for a drill. The positional destination does not
   select the PostgreSQL target. Confirm target identity independently first.
3. Compare project/evidence counts, audit head, hashes, application read/write
   behavior and correction/publish permissions. Retain commands with DSNs redacted,
   signed manifest, image digest, PostgreSQL/client version and timestamped results.
4. Inject an outage, recover from an aged backup/PITR boundary, measure actual lost
   committed writes (RPO) and full service unavailability (RTO), including ingress,
   credentials, object storage, DNS and operator notification—not just pg_restore.
5. Repeat against production-equivalent PostgreSQL version, TLS verify-full,
   production-size synthetic load, and real authorized backup storage. Sandbox
   PostgreSQL 16.2 used loopback/trust and sslmode=disable; it is NOT TLS proof.

## Premortem: multi-perspective review, not independent agents
No independent-agent tool was available. Security, SRE, editorial and product
perspectives were applied by one assistant; test processes ran independently.

| Failure | Countermeasure | Release evidence needed |
|---|---|---|
| Recovery drill wipes source | Refuse automatic PG drill; explicit isolated target | Target identity + restore receipt |
| Tampered backup accepted | Manifest verification before restore | Negative/live regression |
| Partial restore called success | Transaction + fail on every nonzero exit | Corrupt-archive refusal and target invariance |
| TLS bypass during disaster | Preserve libpq TLS policy | Real verify-full/certificate-failure rehearsal |
| False zero-data-loss claim | Unknown RPO stays not-measured | Timestamped outage/PITR experiment |
| Exposed GitHub token merges hostile code | Revoke/replace token; enforce admin protection | Owner revocation and reviewer proof |
| Legal harm from AI candidate | Two-person source review; synthetic-only until approval | Signed editorial/legal gates |
| New frontier model commoditizes extraction | Sell reproducible evidence workflow, not model novelty | Design-partner time-to-reviewed-dossier study |

## Product experiment (hypotheses, not traction claims)
Ignore unverified “GPT-6 Astra” launch assumptions. Keep extraction provider-neutral
and candidate-only. Focus one launch wedge: a reviewable, source-linked public-work
change dossier with missing-evidence list and an RTI draft. Invite three design
partners with the same synthetic task. Record baseline analyst minutes and assisted
minutes, source-link correctness, reviewer disagreement, correction burden and
repeat use in week two. Proposed success thresholds: >=50% lower median preparation
time without lower citation correctness, all public claims independently reviewed,
and at least two partners voluntarily returning. These are targets, not results.
No guaranteed 100x value or traction claim is supported.

## Still required before unrestricted launch on 16–17 September
Owner: revoke exposed PAT and supply scoped expiring replacement; security: admin
protection and independent review; operations: production-equivalent TLS/OIDC/MFA,
S3 quarantine/redaction, image/SBOM scan, backup/PITR/rollback, alert-to-human;
editorial/legal: publication approvals and source terms; product: real design-partner
receipts. Do not turn these ledger entries green based on local tests alone.

## Primary references consulted
- https://www.postgresql.org/docs/16/app-pgrestore.html — default continues after
  SQL errors; --exit-on-error and --single-transaction semantics.
- https://www.postgresql.org/docs/16/libpq-envars.html — TLS/connection environment.
- Repository runtime and tests at https://github.com/Kartik24Hulmukh/project-xray
