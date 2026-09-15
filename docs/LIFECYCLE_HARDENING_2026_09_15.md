# Lifecycle hardening — September 15, 2026

## Decision: synthetic preview only; public production launch remains NO-GO

The attachments disagree: the September 15 report calls every technical gate complete, but also lists unexecuted identity/storage/collector/recovery tasks. Those are release gates, not optional cleanup. The older deliverable says PR #40 is unmerged. Live GitHub now shows #40 merged at `59f5af7cbf14537b15b41275ddc53103c5954487` on September 15 at 08:48:42 UTC. This continuation starts from that merge; it does not claim to have performed it.

## Forensic postmortem: verified new defect, not invented previous logs

No raw last-turn failure logs were attached (the ZIP has three Markdown reports). Prior GIL/WAL/idempotency claims are historical reports, not independently reconstructed incidents. A new executable regression **does** reproduce at the frozen main SHA: SIGTERM exits -15 without cleanup. The CLI directly called `serve_forever()` without signals or finally cleanup. Daemon handlers and queued telemetry could disappear at deployment. Additionally, admission used an unconditional semaphore acquire: saturation could trap the accept loop while shutdown waited for it.

Acceptance tests, now passing: admitted response completes before exporter shutdown; saturated admission does not deadlock termination; held sockets and exporter outage have bounded cleanup; invalid timeout rejected; repeated close harmless; SIGTERM exits zero and emits a receipt; SDK atexit cannot bypass our bound; readiness is false during drain. Baseline SIGTERM failure is retained alongside the fix evidence.

### Structural changes

- SIGTERM/SIGINT stop new admission via a shared draining event; shutdown runs on a separate thread, never on its own serve loop.
- Track admitted sockets with a condition; close waits up to ten seconds for in-flight work, then interrupts remaining sockets. No busy-spin.
- Flush telemetry only after draining and only within the remaining deadline. Report unfinished work and exporter completion honestly. Disable SDK auto-atexit join so it cannot bypass this bound.
- `/readyz` and `/ready` reject already-admitted probes during drain. Existing privacy, tracing, input, idempotency and canonical health contracts remain intact.
- Seeded, local-only same-process soak emits per-batch latency/resource samples and shutdown receipt. CI runs a 30-second smoke soak and retains artifacts.

Python cannot safely kill a blocked application thread. Interrupted work at deadline is **not** certified committed or rolled back; clients must reconcile using idempotency keys. A slow external dependency still requires its own deadline and an orchestrator hard kill. Telemetry may drop at timeout by design.

## Frozen baseline and measured deltas

Same machine, Python 3.14.6, `PYTHONHASHSEED=0`, fresh disposable SQLite DBs, default 64 admission / 4 execution slots. Baseline was frozen before code changes. Timing is observational, not deterministic; seed controls input order, not thread scheduling, UUIDs or timestamps. Runs are single samples and not proof of causality.

| 100-client phase | Before P50/P95/P99 ms | After P50/P95/P99 ms | Before → after req/s |
|---|---|---|---|
| Reads, 2,000 | 104.45 / 179.17 / 202.04 | 104.90 / 178.55 / 212.95 | 917.05 → 917.94 |
| Writes, 300 | 162.71 / 181.23 / 199.57 | 177.53 / 215.29 / 231.68 | 560.75 → 459.68 |
| Same-key replay, 100 | 68.47 / 95.10 / 128.88 | 61.47 / 111.99 / 115.41 | 615.04 → 502.72 |

Peak RSS: 58,812 → 59,064 KiB. Write P95 regressed 18.8% in this sample; throughput fell 18.0%. Do not advertise a speedup. Read P95 still exceeds the 150ms aspiration. Before/after each persisted exactly 301 projects with a valid 301-event audit chain. Both `safety_pass` true; both `zero_rejection_capacity_pass` false (intentional 409 conflicts). This is 100x **client concurrency relative to one client**, not 100x capacity, product value or traction.

Baseline suite: 293 run, 285 passed, 8 PostgreSQL-gated skips. Updated suite: 300 run, 292 passed, same 8 skips; isolated venv rerun also green. Existing OTLP tests exercise decoded live loopback export, API 429/503, trace correlation and private logs. Collector failure is exercised by a failing exporter, not a production collector outage. PostgreSQL and built-container verification must pass in PR CI; local Docker is unavailable.

Chaos seed 20260913: 60 held sockets + 200/200 successful reads (P95 45.95ms); 30 mid-body drops followed by 300/300 successful reads; shuffled replay 48 successes, two 409s, one unique ID. Readiness/liveness 200 after load, zero 5xx in this synthetic run. Deliberate exception tests do emit expected 500s; these are not hidden as production panics.

Same-process soak: 109,000 requests over approximately 121 seconds, 100 clients, no request errors, readiness 200, SIGTERM exit zero and zero unfinished requests. This is a **two-minute read-only smoke soak**, not a day-long mixed-load leak proof or target-hardware capacity certification. See raw samples for RSS, threads and latency. Server is synchronous/threaded: async event-loop latency is not applicable; HTTP tail latency is measured instead. Initial stress/soak preceded the final readiness and SDK-atexit refinements; final unit suite covers those refinements, with additional CI reruns required.

End-to-end local smoke passes create/review/publish/export, restart, capsule/audit verification and backup restore. Synthetic production rehearsal passes against local fixture integrations, not real AWS/IdP/storage/collector services.

## Top five sustained-load premortem risks

| Failure mode | Protection / remaining evidence | Owner |
|---|---|---|
| Saturated/slow clients trap shutdown or exhaust sockets | New interruptible admission and drain; prove ingress absolute deadlines, per-client quotas, 30-second termination grace | SRE |
| GIL/SQLite/audit-chain write contention grows tails | Retain execution limit 4; mixed-load large-ledger PostgreSQL test and declared capacity SLO still required | Backend |
| Collector outage loses traces or blocks exit | Bounded queue and bounded lifecycle; verify real collector TLS, privacy, retention and loss alerts | SRE/security |
| Dependency hang or memory/FD creep beyond short soak | External call deadlines; hour-plus mixed fault soak on target hardware, RSS/FD plateau and rollback drill | SRE |
| Wrong identity/evidence/recovery permits harmful publication | Real MFA/role mapping, two independent editorial reviews, scanner receipts and backup restore on managed target | Founder/operator |

## Release runbook and rollback

1. Rotate the prompt-exposed GitHub credential; store replacement only in approved secret storage. This patch does not contain it. Do not paste credentials into commands, reports or repository files.
2. Obtain independent review of this follow-up PR and green required CI for the **exact head SHA**. Do not override branch protection or self-approve. Merge only through the normal protected flow.
3. Build an immutable artifact from merged main; run dependency/image/secret scans, retain SBOM/digest and verify deployed config. No version tag is invented here.
4. Before rollout, take and verify managed DB backup; test restore in isolation. Preserve audit history. Verify actual IdP MFA, ingress-only app access, scanner quarantine, role separation and editorial/legal signoff.
5. Canary behind TLS with synthetic records. Verify `/healthz`, `/readyz`, create→review→export→restart, collector delivery/retention, alert delivery and kill switch. Keep ingress request/body deadlines and per-client quotas enforced.
6. Configure orchestrator termination grace at least 30 seconds, exceeding ten-second app drain plus ingress deregistration. Send SIGTERM during active requests and inspect `shutdown` JSON: zero unfinished requests; exporter completion true when collector healthy. Test duplicate SIGTERM and timeout/collector-down paths in staging. Do not send SIGKILL as routine deployment.
7. Abort on audit mismatch, unsupported publication, auth bypass, failed readiness, lost restore, or SLO breach. Stop ingress/activate kill switch and roll back to the previous verified immutable artifact. No schema change in this patch; preserve database and reconcile pending idempotency entries rather than blindly replaying writes.
8. Founder signs GO only with target deployment receipts. Until then invite synthetic evaluators only. Track verified reviewer-time savings, citation accuracy, repeat usage and willingness to pay; no guaranteed traction/100x promises.

## Convergence / scope

One lifecycle implementation cycle, followed by bounded review refinements (SDK atexit, readiness) and green reruns; fewer than five fixes for this subsystem. No speculative rewrite of the data model or concurrency defaults. Performance tuning and target deployment are explicit checkpoints, not hidden successes.

Raw receipts: `docs/validation/lifecycle-2026-09-15/`. Reproduce using a fresh venv, requirements installation, `PYTHONHASHSEED=0 python -m unittest discover -s tests -v`, `python scripts/stress_local.py`, `python scripts/fault_injection.py --seed 20260913`, `python scripts/soak_local.py --seconds 120`, `python scripts/smoke_e2e.py` and `CHROMIUM_PATH=/usr/bin/chromium python scripts/check_release.py`. Write tests are local-only or disposable CI databases; never point them at production.
