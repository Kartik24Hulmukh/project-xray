# Launch Decision Record — 16–17 September 2026

**Decision: GO for a controlled synthetic preview on 16 Sept 2026. NO-GO for any
production claim, and NO-GO for publishing real named cases, until the gates below close.**

Decided 2026-09-15 on Session 7 evidence (`docs/validation/SESSION7_VALIDATION_RECEIPTS_2026-09-15.md`).
This record supersedes any earlier "production ready" language in this repository.

## 1. What is actually true on `main` (`50b7897`)

- CI #265 green, **Security #135 green** — both machine gates on HEAD are now closed.
- 349 automated tests OK, 19 skipped (all PostgreSQL-live / TLS-dependent).
- Nine-step synthetic smoke path green end to end, including restart, restore, report,
  capsule and audit verification.
- Production-readiness ledger: **0/10 closed with evidence** — by design, because none of
  the ten checks can be closed from a laptop-equivalent environment.
- Every published release artifact is now correctly labelled pre-release.

**Therefore:** the software is *functionally end-to-end working* on synthetic data, and it is
*not* production-certified. Both halves of that sentence are load-bearing.

## 2. Premortem — the five ways this launch actually kills the company

Written as "it is 1 October 2026 and Project-Xray has failed because…", each with the
structural fix rather than a resolution to be careful.

**P1. "…we called it production and one person checked."** The `v2.1.1-production` tag was
real and public. A journalist or a defendant's lawyer only has to screenshot it next to the
0/10 ledger to end the project's credibility permanently.
*Fix, shipped this session:* tag retracted and relabelled with a public correction notice;
`scripts/check_release_claims.py` now makes the mistake impossible to merge (fail-closed,
8 tests, CI-enforced). The correction notice itself becomes a trust asset — we demonstrate
the behaviour we demand of institutions.

**P2. "…someone treated a synthetic dossier as a real allegation."** Screenshots outlive
context. A synthetic claim reposted without the banner is indistinguishable from an accusation.
*Fix:* synthetic-preview labelling must be server-side and inside the artifact — watermark
in the PDF/CSV/plain-text export body and in the dossier JSON as a non-strippable field,
not only a CSS banner. Review-state enforcement stays server-side. Gate: `evidence.unsupported_publication_blocked`.

**P3. "…the first restore we ever did for real was during the incident."** All recovery
receipts to date are SQLite-local; the bundled PostgreSQL build has no SSL, so
`verify-full` has never been exercised. Real RPO/RTO are unmeasured numbers.
*Fix:* target rehearsal on managed PostgreSQL 16 with TLS `verify-full`, a deliberate
certificate-rejection drill, disk-full and rollback drills, PITR and object-store restore,
and published RPO/RTO receipts per release. Gate: `recovery.backup_restore_rpo_rto`.

**P4. "…the gateway assertion was bypassable."** Bearer-token fallback plus unverified
replay/staleness handling at the target ingress is the single most likely real breach path.
*Fix:* reject bearer fallback in production config; test missing / invalid / stale / replayed
assertions against the real ingress; require fresh MFA-marked assertions. Gate: `auth.oidc_mfa_roles`.

**P5. "…launch-day attention converted to zero repeat use."** A research product that wins
one news cycle and no second session is dead on arrival, especially in a month saturated by
frontier-model launches.
*Fix:* instrument three metrics before the window opens — time-to-first-useful-dossier,
independent provenance-check rate, and week-2 repeat use from three named design partners.
Gate: `adoption.three_design_partners`. No paid or broad distribution until week-2 repeat
use is observed from at least two partners.

## 3. Council positions (multidisciplinary review, single operator — not independent agents)

| Seat | Verdict | Binding condition |
|---|---|---|
| Security | Conditional GO | Exposed PAT revoked and replaced by a fine-grained, short-lived, repo-scoped token; enable secret scanning + push protection (currently **disabled**) |
| Reliability | NO-GO for production | Target PostgreSQL 16 + `verify-full` rehearsal with receipts |
| Data integrity | GO for synthetic | Restores stay on disposable targets; identity checks enforced (#48 shipped) |
| AppSec | NO-GO for production | Bearer fallback rejected; replay/staleness tests at real ingress |
| Privacy | GO for synthetic | Public allowlist + export redaction review before any real case |
| Editorial/legal | NO-GO for real cases | Two independent source reviews per published dossier; named correction owner and SLA |
| Product | GO for synthetic | Three design partners instrumented; repeat use measured, not assumed |
| Release governance | GO with guard | Claim guard green; every artifact pre-release; branch protection preserved |

## 4. Launch-window runbook (16–17 Sept)

1. **T-12h** — owner revokes the exposed PAT; enables secret scanning and push protection.
2. **T-8h** — rerun `make test`, `make smoke`, `make claims`; attach receipts to issue #3.
3. **T-4h** — deploy preview with the synthetic watermark in artifact bodies; verify the
   kill switch and the documented shutdown path with a named on-call human.
4. **T-0** — open the preview to the three design partners only. Publish the ledger page
   showing 0/10 and what each check requires. Radical honesty is the differentiator.
5. **T+24h** — review time-to-first-dossier and provenance-check telemetry; decide whether
   to widen. Hold real-case publication regardless.

## 5. The 100x levers, sequenced after the window

1. **Agent-native API with signed receipts.** In a world of autonomous research agents, the
   scarce good is not another summary — it is a machine-verifiable provenance chain an agent
   can cite and a human can audit. Every dossier operation callable, every response signed.
2. **Publish the adversarial-evidence pack.** The red-team gate already runs in CI; making
   its output public turns trust into the product surface.
3. **RPO/RTO and correction-SLA published per release, uptime-badge style.** No competitor
   shows these numbers; showing them reframes the category.
4. **Ledger-as-landing-page.** The 0/10 ledger, published and shrinking in public, is a more
   credible growth engine for an evidence product than any launch post.
