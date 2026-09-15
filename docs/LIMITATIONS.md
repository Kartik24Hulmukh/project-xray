# Limitations — controlled synthetic preview (16–17 September 2026)

This page is the single honest statement of what Project X-Ray does **not** do or prove as of the
16–17 September 2026 preview. It supersedes nothing in `KNOWN_LIMITATIONS.md`; it consolidates the
open items that matter to a reader, reviewer, or operator today.

## Product scope

- Project X-Ray produces **source-linked claims for human investigation**. It does not determine
  corruption, guilt, causation, or intent.
- Every dossier on the preview surface is a **synthetic fixture**. Real-case publication is blocked
  by policy and by the two-independent-reviewer publication gate; no real person, project or
  authority is the subject of any preview claim.
- "Not located in the searched sources" is never "does not exist".

## Security and infrastructure — open items

| Item | Status | Consequence |
|---|---|---|
| Bundled local PostgreSQL build has **no SSL support** | Open | Local rehearsals cannot exercise TLS. Production **requires** managed or self-built PostgreSQL 16 with `sslmode=verify-full`; a certificate-rejection drill receipt is a release gate. |
| Real RPO / RTO on target environment | Not yet measured | Published recovery numbers are from isolated local drills only. |
| Malware scanner production receipt | Pending | Scanner role and fail-closed behaviour exist; production engine attestation still required. |
| High availability | Single-task / single-AZ | Planned maintenance implies downtime. |
| Target OIDC/MFA tenant proof | Partial | Bearer-token fallback must be rejected in production ingress; receipt outstanding. |

## Exports

- `GET /api/projects/{id}/report` (Markdown), `GET /api/projects/{id}/rti` (plain text) and
  `GET /api/projects/{id}/claims.csv` (CSV) expose **only public publication states** unless the
  caller is an authenticated admin/reviewer using `include_private=1`.
- CSV cells beginning with `=`, `+`, `-`, `@`, tab or carriage return are prefixed with `'` to
  neutralise spreadsheet formula execution (CWE-1236). Exports carry a `data_label` column
  (`SYNTHETIC` or `REVIEWED`).

## Traction and impact

- No design partners, paid pilots, or real-world impact are claimed. Metric definitions are in
  `docs/metrics/TRACTION_DEFINITIONS.md`; receipts are published only when observed.

## Corrections

See `docs/legal/CORRECTIONS_AND_APPEALS.md` for the named SLA.
