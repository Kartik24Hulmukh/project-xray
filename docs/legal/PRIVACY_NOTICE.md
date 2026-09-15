# Privacy notice — Project X-Ray

**Status:** controlled synthetic preview, 16–17 September 2026. This notice complements `PRIVACY_AND_TAKEDOWN_DRAFT.md` and `DISCLAIMER.md`.

## What data the preview processes

- **Dossier content:** synthetic fixtures only. No real personal data is intentionally ingested during the preview.
- **Operational data:** request identifiers, timestamps, client IP for rate limiting, and the tamper-evident audit log (actor role, action, object id). Audit entries are retained for the life of the deployment because they are the integrity record.
- **Reviewer identities:** reviewer names/ids appear in audit and review records. Reviewers consent to this by accepting a reviewer token.

## Exports and PII

- Markdown, plain-text RTI and CSV exports contain only public publication states unless an authenticated admin/reviewer explicitly requests private material.
- Before any **real-case** export is enabled, each export path must pass a PII scan and redaction review (names of private individuals, contact details, identifiers). Until that receipt exists, real-case exports remain disabled.
- RTI drafts deliberately leave applicant name and address as placeholders to be completed privately; the service never stores them.

## Uploads

- Uploaded documents are quarantined until scanned; if the scanner is unavailable the upload fails closed. Size and media-type limits are enforced.

## Your rights and takedown

Requests to correct, remove, or explain data are handled under `CORRECTIONS_AND_APPEALS.md` with the same SLA (acknowledgement within 2 business days).

## Contact

See `SECURITY.md` for the private contact channel.
