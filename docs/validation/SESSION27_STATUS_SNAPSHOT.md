# Session 27 — Status Snapshot and Remaining Work

**Date:** 2026-09-26. **Branch:** `harden/project-xray-v1-launch`.

## Verified baseline

- Latest merged hardening stack is `e5b9fc8`; dual-backend 20-wave soak is green in CI.
- Local suite previously measured 460 tests, 0 failures, 0 errors, 19 skips; torture contracts pass locally (3/3).
- CI evidence: 100 workers, 120 personas, 20 waves, 5,700 requests; zero crashes/tracebacks; bounded sampling; recovery below 200 ms; PostgreSQL settled RSS growth 7.3 MiB and SQLite 4.2 MiB in the reported run.
- `repos.md` is present and explicitly defers external connectors for the controlled synthetic-preview scope; no connector is fabricated.

## Done / partial / missing

- **Done:** boundary hardening, deterministic readiness invariants, dual-backend soak gate, loopback PostgreSQL safety check, idle glibc heap trim, pinned CI actions and security checks.
- **Partial:** residual PostgreSQL growth remains below the 8 MiB gate but is leak-shaped; a longer measurement is warranted.
- **Missing / external:** independent approval and merge of release PR #62, live deployment, OIDC/MFA enrollment, PITR/RPO/RTO drill, human alert delivery, dossier review, and design partners. These require operators/live infrastructure and are not claimed as complete.

## Remaining work queue

1. **P0 / external:** obtain independent approval, merge PR #62, tag release.
2. **P0 / engineering:** provide a manual 60-wave soak option to test hypothesis H6 (slow PostgreSQL RSS growth) without changing the 20-wave gate.
3. **P0 / environment:** execute production deployment and recovery/identity/alerting drills.
4. **P0 / product:** complete two-reviewer dossier review and sign three design partners.
5. **P1:** verify the scheduled nightly soak after release merge.
6. **P2:** profile any residual allocation growth if the 60-wave run breaches 8 MiB.

## Premortem hypotheses

- **H6:** PostgreSQL settled RSS climbs 0.3–0.4 MiB per wave; 60-wave run either refutes or confirms it. Mitigation: profile per wave; only then consider arena cap/jemalloc.
- **Workflow drift:** manual and scheduled inputs could diverge. Mitigation: both default to 20 waves and share the same command; choice input permits only 20 or 60.
- **False completion:** human and live-environment gates cannot be simulated honestly. Mitigation: keep them explicitly external and unclaimed.

## Change in this cycle

`.github/workflows/nightly-soak.yml` now exposes a `workflow_dispatch` `waves` choice (`20` default, `60` investigation) and uses it in the shared soak command. Pull requests and schedules retain the 20-wave default.
