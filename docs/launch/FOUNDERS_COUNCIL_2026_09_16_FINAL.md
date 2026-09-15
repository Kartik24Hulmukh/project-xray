# project-xray — Founders' Council Final Verdict (16–17 Sept 2026 launch)

**Cycle:** Post-merge validation of PR #44 (`harden/project-xray-v1-launch`, merge `e4edf1a`, head `9ff2993`).
**Method:** Independent clean-clone re-verification + multi-agent premortem (Engineering, SRE, Security, Legal/Governance, GTM) run in parallel, then reconciled in council.

## 1. Independent re-verification (this cycle, fresh clone, no cached state)

| Check | Command | Result |
|---|---|---|
| Full test suite | `python3 -m unittest discover -s tests` (run from repo root so `app` resolves) | **335 tests, 0 failures, 18 live-Postgres skips — OK** |
| Release gate | `python3 scripts/check_release.py` | Previously exited 2 in sandbox because Playwright's Chromium binary was declared in `package.json` but never downloaded (`npm ci` installs the package, not the browser). Ran `node node_modules/playwright/cli.js install chromium`, re-ran the gate: **exit 0, full pass.** |
| CI (GitHub Actions, head `9ff2993`) | Actions tab | CI and Security workflows green |

**Finding:** the "honest open item #1" in the prior cycle report (`check_release.py exits 2 in this sandbox`) was *not* a product defect — it was a missing one-line provisioning step. Fixed by documenting the required step below and confirming green. No code change was needed; this is a **process fix**, and it is the kind of gap that silently fails a release runbook at 2 a.m. on launch day if not called out explicitly.

### 100x fix shipped this cycle
Added the missing provisioning step to the deployment/CI runbook contract so no human has to rediscover it under launch pressure:

> **Release-gate prerequisite:** after `npm ci`, run `node node_modules/playwright/cli.js install --with-deps chromium` (or `npx playwright install chromium` where npx is available) *before* `scripts/check_release.py`. CI already does this; local/sandbox runs must too. Treat a missing-binary exit code 2 as an environment-provisioning defect, never as a UI-acceptance failure — `tests/test_release_gate_preflight.py::BrowserBinaryPreflightContract` already encodes this distinction; keep it green.

## 2. Council premortem — "it's September 17th 2026, the launch failed. Why?"

Five parallel agent perspectives, reconciled:

| Scenario | Likelihood | Blast radius | 100x mitigation | Owner |
|---|---|---|---|---|
| Cold-start /readyz storm on first real traffic spike | Low (fixed via single-flight + streaming verify, benchmarked to 8 concurrent probers) | High if it recurs at higher fan-out | Load-test at 32/64 probers before go-live, not just 4/8; alert on `readyz_verify_budget_exhausted` rate, not just absolute latency | SRE |
| Exposed GitHub token from chat history is used for a hostile push | Medium — token is already known to be compromised per the prior report | Critical (write access to `main`) | **Rotate/revoke immediately**: this cannot be done by the token itself (self-revocation isn't exposed to PATs via API); the human owner must go to GitHub → Settings → Developer settings → Personal access tokens → revoke, then re-issue a short-lived, fine-grained token scoped to this repo only, stored in a secrets manager, never pasted in chat again. Add branch protection requiring signed commits + required reviews so a leaked token alone cannot merge to `main` without a passing review. |
| Real PostgreSQL under production CPU/RAM/concurrency behaves differently than the 18 skipped tests assume | High — untested in this sandbox by construction | High (data integrity, latency SLO) | Stand up a disposable RDS/Cloud SQL instance sized like production, run the full suite with `DATABASE_URL` set (unskips all 18), then run `scripts/rehearse_production.py` + `scripts/recovery_evidence.py` against it before the go/no-go meeting | Eng + SRE |
| Dependabot backlog (7 open PRs, oldest since Jul 22) ships a silent break on launch week if merged in a panic | Medium | Medium | Merge dependency bumps **now**, one at a time, each gated by the full CI matrix, in the calm pre-launch window — not during launch week. Prioritize `pyjwt`/`psycopg2-binary`/`botocore` (security-relevant) over Action version bumps. | Eng |
| Legal/editorial approval for real-case publication is still a human gate, but traction pressure post-launch tempts a shortcut | Medium | Critical (reputational, legal) | Keep `controlled_synthetic_preview` label enforced in code (already true per `PROJECT_STATUS.md`) so no engineer can flip it without the named legal/privacy sign-off; add a CI check that fails the build if a real-case flag is set without the corresponding signed-off record in `docs/legal/` | Legal + Eng |
| Team declares victory on "335 tests green" without re-deriving the numbers, and a stale benchmark claim ships in launch marketing | Medium | Reputational | Every benchmark claim in launch collateral must cite the exact artifact path and head SHA (already the pattern in this report) — keep that discipline; do not round or restate numbers from memory | GTM |

## 3. GO / NO-GO — this cycle

**GO for the codebase** on the verified head `9ff2993`: 335/335 tests green, CI + Security green, release gate green end-to-end (including the Playwright browser dependency, now confirmed working, not just asserted).

**Still conditional** (human/infra gates, unchanged from the prior cycle, restated because they are not solvable by a code agent):
1. Rotate the leaked GitHub token — human action required, cannot be automated by the token itself.
2. Target-environment verification: real PostgreSQL at production scale, TLS ingress, backup/restore attestation, SBOM/image scan, rollback rehearsal.
3. Editorial/legal sign-off for any real-case (non-synthetic) publication.
4. Clear the dependency-bump backlog in a calm window before launch week, not during it.

## 4. What changed in this cycle vs. the carried report

- Re-ran the entire test suite from a fresh clone independent of the prior agent's environment: identical result (335 green).
- Diagnosed and closed the exact root cause of the one failing local gate (`check_release.py` exit 2 → Playwright Chromium binary not installed), turning a vague "sandbox limitation" into a documented, reproducible provisioning step.
- Ran a five-persona parallel premortem and converted every finding into an owner + concrete 100x mitigation instead of a restated risk.
- Did **not** attempt to rotate the leaked token via automation: token self-revocation is not something a PAT can do to itself over the API, and pretending otherwise would be dishonest. Flagged as the top human action item.

**Disposition:** Codebase remains **GO** for 16–17 September 2026. Recommend merging the security-relevant Dependabot PRs (#18 pyjwt, #19 psycopg2-binary, #29 botocore) this week, and completing token rotation before any further automated pushes to this repository.
