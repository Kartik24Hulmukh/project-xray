# Risk acceptance - zlib HIGH advisory in the runtime image

**Date:** 2026-09-19 - **Expires:** 2026-10-03 - **Scope:** container image `project-xray:*` only
**Reviewers:** automated hardening session 12 (proposer) + **founder countersign REQUIRED** via approving review on PR #52. Until countersigned this document is a proposal, not an acceptance.

## Why this exists

The required `blocking-security` check on PR #52 (head `554de03`, run 35456013342) **failed** in the grype image scan:

| CVE | Package | Severity | Fixed in | EPSS | Action |
|---|---|---|---|---|---|
| CVE-2026-82049 | python 3.13.15 (binary) | High | 3.14.0b1 | 0.2% | **Structural fix:** base image bumped `python:3.13-alpine` -> `python:3.14-alpine` in both Dockerfile stages. No waiver. |
| CVE-2026-85091 | zlib 1.3.2-r0 (apk) | High | *(none published)* | 0.4% | Time-boxed, package-scoped waiver (this document). |

The four libuuid advisories accepted on 2026-09-13 no longer appear in the scan output (base image now ships util-linux 2.42.3); their entries are retained until the shared expiry and will be pruned once a green scan on `python:3.14-alpine` confirms absence.

## Remediation attempted first

- Both Dockerfile stages already run `apk upgrade --no-cache`; grype reports no `FIXED IN` version for CVE-2026-85091 on the Alpine branch served by the base image, so the package cannot be upgraded reproducibly today.
- Removing zlib is not possible: it is a hard dependency of CPython (`zlib`, `gzip`, `zipfile` modules) and of the Alpine base.

## Exposure analysis

- The HTTP layer is stdlib `http.server`; it performs **no** `Content-Encoding` decompression of request bodies. Compressed uploads are never inflated server-side; evidence files are stored as opaque bytes, SHA-256 hashed and placed in quarantine state.
- `MAX_BODY_BYTES=2MiB` bounds every request body; decompression-bomb fuzz cases already pass in `scripts/redteam_fuzz.py`.
- Capsule export uses `zipfile` **write** paths on operator-generated data only; no attacker-controlled zip is read.
- Container runs as non-root uid 10001 with a read-only application layer.

Residual risk: **Low** (no reachable attacker-controlled zlib inflate path). Accepted for 14 days, fail-closed on expiry by `scripts/check_grype_exceptions.py` and `tests/test_infra_plan_contract.py`.

## Exit criteria

1. Alpine publishes a fixed zlib apk -> rebuild with `docker build --pull`, remove the waiver and the `APPROVED_CVES` entry.
2. Or expiry 2026-10-03 passes -> CI fails closed; renewal requires a fresh founder-reviewed document.
