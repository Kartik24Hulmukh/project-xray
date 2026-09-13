# Follow-up hardening — 2026-09-13

## Baseline and forensic correction

Clean checkout of `16b90af`; baseline unittest: **285 run, 277 passed, 8 PostgreSQL-gated skips, 0 failures**.
Prior reports called this “285 pass / 8 skipped”; unittest's total includes skips. The earlier
run artifacts are secondary evidence, not independently measured production traffic.
The provided ZIP contains only the previous report and benchmark JSON, not battle logs.
No new deadlock, async timeout, memory leak or context-exhaustion evidence was found.
Existing measured GIL-convoy/WAL-lock fixes are retained, not reattributed to this change.

Acceptance: canonical probes; real OTLP/HTTP span received without secrets and correlated
with response/log IDs; nonblocking bounded exporter; all existing tests and synthetic
end-to-end workflows retained; PostgreSQL CI must fail on skips and publish receipts.

## Structural changes

- `/healthz` joins canonical `/readyz`; old aliases retained; Docker and CI aligned.
- Opt-in SDK/OTLP HTTP spans with bounded batch export, parent-based sampling,
  two-second exporter timeout, normalized route/status attributes and no exception payloads.
- Access logs no longer leak query strings/raw URLs through stdlib request-line logging.
- CI preserves actual PostgreSQL version, logs and per-suite exit/skip gates as artifacts.
- New tests decode protobuf at a real loopback HTTP collector, verify 429/503 responses,
  privacy/correlation and 3000-span failed-exporter handling. Status injection is synthetic,
  not a claim that real upstream providers were contacted.

## Fresh benchmark receipt

Python 3.14.6, Linux, 64 logical CPUs; SQLite; 64 connection slots / 4 execution slots.
`PYTHONHASHSEED=0 python3 scripts/stress_local.py`; isolated baseline before code edits;
one before/after sample, **not a statistical speedup claim**. OTel disabled for both.

| Phase | Before rps | After rps | Before P50/P95/P99 ms | After P50/P95/P99 ms |
|---|---:|---:|---|---|
| baseline_reads | 401.61 | 400.53 | 2.21/3.18/3.38 | 2.23/3.33/3.39 |
| 100_client_reads | 501.09 | 600.39 | 198.48/329.59/427.09 | 164.52/265.84/365.57 |
| 100_client_writes | 253.21 | 281.69 | 341.53/415.59/486.36 | 328.1/357.22/401.52 |
| 100_client_idempotency_race | 506.23 | 384.77 | 59.59/148.02/150.72 | 78.64/201.26/206.1 |

Peak RSS: 48864 → 45772 KiB.
After-load server threads: 1 (not a measured peak thread count). Both safety gates true;
no 5xx, 301 persisted projects, valid audit/integrity, one idempotency success ID.
Both zero-rejection-capacity gates **false** (expected same-key 409 race conflicts).
Read P95 remains above 150ms. The idempotency tail regressed in this single sample;
do not hide that behind the read throughput improvement. 100 clients is a concurrency
simulation relative to one client, not proven 100x sustained production traffic.
No async event loop exists in this threaded HTTP server: event-loop latency is N/A;
HTTP percentiles include admission/handler time. No sustained soak/leak conclusion.

Seeded fault receipt (`20260913`): 60 held idle/partial-header sockets allowed 200/200
reads; after 30 body drops 300/300 reads succeeded; 50 shuffled identical-body replays
returned 49x201 + 1x409 with one ID. No 5xx, process alive, readiness/liveness 200.
The previous harness description “shuffled bodies” overstates coverage: only job labels
are shuffled; mixed-body conflicts are covered by separate idempotency tests.

## Premortem: top five at sustained 100x

| Failure | Mitigation / remaining gate |
|---|---|
| GIL convoy / CPU saturation | 4 execution slots; retain 64 connection slots; measure on target hardware |
| Slow headers or body holders exhaust admission | ingress header/body timeouts and per-client quotas; finite local bounds are not DDoS protection |
| SQLite writer/audit contention | busy fallback/Retry-After, idempotency and WAL-once; PostgreSQL scale gate and production SLO still required |
| Collector outage or sensitive/high-cardinality telemetry | bounded export queue/timeouts, route allowlist, no queries/baggage; stage collector credentials and retention |
| Unsafe launch: identity/storage/editorial/restore drift | execute managed deployment gates, backup/restore and two-person source review before public release |

## Validation and convergence

After change: **291 tests run, 283 passed, 8 PostgreSQL skips, 0 failures** locally.
One fix cycle: probe test compared dynamic timestamps; corrected comparison to stable
status/version fields. No failing production subsystem required repeated fix cycles.
Synthetic smoke: create-to-report/RTI/capsule/audit, static assets, restart and restore passed.
Production rehearsal (synthetic managed adapters): passed, backup and restored SHA match.
Full PostgreSQL, container and blocking-security evidence must be verified on this exact
new head in CI before merging. No bypass of review or branch protection is requested.

## Release runbook / launch gate

1. Review semantic diff and exact-head CI including retained `postgres-evidence` artifact.
2. See `docs/OTEL_RUNBOOK.md`; provision trusted collector via secret manager, validate TLS,
   enable trace sampling in staging and locate a synthetic span at the real sink.
3. Build immutable image; backup, apply migrations once, validate canonical probes and UI.
4. Execute smoke + persistence/restore against the target image/environment. Test alert
   destination, auth/MFA/OIDC, managed storage/scanner clearance and privacy policy.
5. Canary with capacity/latency/error/queue alerts; rollback image or disable tracing on
   regression. SIGTERM drain/export flush is not newly implemented by this patch.
6. Require human editorial/legal approval; no real unreviewed evidence publication.
7. Revoke/rotate the prompt-exposed Git credential; do not copy it into code, artifacts or logs.

This is mergeable engineering work pending review/CI, **not production certification**.
High traction or a 100x product-value guarantee cannot be established by synthetic tests.
