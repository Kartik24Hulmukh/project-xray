# Launch v1 premortem — frozen before code changes

Baseline: e9db027; see validation/launch-v1-baseline*.json. Prior attachments are reports, not recoverable code or raw failure logs. Their claims of 100% green include skipped PostgreSQL tests and are not production certification.

| Catastrophic mode | Acceptance test / patch plan |
|---|---|
| Abandoned reservations permanently poison keys | Offline reconciliation deletes only expired processing leases; live/future/malformed timestamps survive. |
| Maintenance races delete rotated leases or replay receipts | DELETE is fenced against complete observed lease snapshot; completed receipts retained by default; explicit retention ages from completed_at. |
| Maintenance consumes unbounded RAM or starves behind malformed rows | Primary-key keyset pagination, maximum 5000 inspected rows, resumable cursor, no whole-table materialization; caller uses existing serialized DB write transaction. |
| Load gate silently accepts overload, missing RAM or slow recovery | Reject any 5xx, require measured RSS <=128 MiB and health/readiness recovery <200 ms; inject failing receipts in regression tests. |
| Chaos report claims rate-limit coverage without injecting it | Launch separate local rate-limited server, observe actual 429 and Retry-After while probes remain healthy; count unexpected replay statuses and process errors. |

Do not duplicate the existing integrated app/telemetry.py, JSON request logging, or /healthz and /readyz with unconnected no-op shims. Do not swallow BaseException to claim zero crashes. Keep stdout redirected to files in subprocess harnesses to avoid PIPE deadlock. Deterministic inputs and seeds do not imply deterministic timing.

Merge requires green remote checks, no missing target-environment gates. Maximum five remediation cycles per subsystem. No evidence of production throughput or commercial traction is inferred from synthetic load.
