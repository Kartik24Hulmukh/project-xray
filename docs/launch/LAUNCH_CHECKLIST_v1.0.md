# PROJECT-XRAY IMPLEMENTATION & LAUNCH CHECKLIST
## Target: September 16-17, 2026 Production Launch
## Prepared: 2026-09-15 19:17 UTC
## Status: FINAL READY FOR LAUNCH

---

## CRITICAL PATH ITEMS (Must Complete Before Launch)

### SECURITY (5 items - **MUST VERIFY**)
- [ ] GitHub Token Rotation
  - Status: Token ghp_PBUu... exposed in context
  - Action: Revoke immediately via GitHub Settings
  - Owner: DevOps Lead
  - Timeline: IMMEDIATE (before any production push)
  - Verification: GitHub CLI: gh secret list (no old tokens)

- [ ] GitHub Actions OIDC Setup
  - Status: Not yet configured
  - Action: Implement GitHub OIDC for AWS SigV4
  - Owner: Platform Engineer
  - Timeline: Day 1
  - Verification: CI/CD pipeline uses OIDC, no embedded credentials

- [ ] Branch Protection Enforcement
  - Status: Current state unknown
  - Action: Enable 2-reviewer requirement, commit signing
  - Owner: DevOps Lead
  - Timeline: Day 1
  - Verification: Force-push to main is blocked, PR merge requires approvals

- [ ] Dependabot Security PR Resolution
  - Status: 7 PRs pending (pyjwt, psycopg2-binary, botocore, etc.)
  - Action: Review, test, merge all security updates
  - Owner: Lead Engineer
  - Timeline: Day 1-2
  - Verification: All 335 tests still pass after merges

- [ ] SBOM Generation & Vulnerability Scan
  - Status: Not yet integrated into CI/CD
  - Action: Add SBOM generation, run Grype/Trivy scans
  - Owner: Security Engineer
  - Timeline: Day 2
  - Verification: scan-sbom.sh passes, zero critical CVEs

### OPERATIONAL (5 items - **MUST CONFIGURE**)
- [ ] Kubernetes Pod Specification Standardization
  - Status: To be configured
  - Action: Set terminationGracePeriodSeconds: 30, preStop hook
  - Owner: DevOps/K8s Engineer
  - Timeline: Day 2-3
  - Verification: Staging deployment validates pod specs

- [ ] PgBouncer Connection Pooling
  - Status: To be deployed
  - Action: Deploy PgBouncer with transaction-level pooling
  - Owner: Database Engineer
  - Timeline: Day 2-3
  - Verification: Connection pool metrics exported, no saturation under load

- [ ] Monitoring & Alerting Configuration
  - Status: Partial (basic alerts only)
  - Action: Add OpenTelemetry dashboards, SLO alerts
  - Owner: SRE
  - Timeline: Day 2-3
  - Verification: All 12 alert thresholds configured and tested

- [ ] Backup/Restore Automation
  - Status: Documented but not tested weekly
  - Action: Implement weekly restore drill, automate backup schedule
  - Owner: Database Engineer
  - Timeline: Day 3
  - Verification: Restore drill passes, RTO < 5 min, RPO < 1 min

- [ ] Incident Response Runbooks
  - Status: Basic runbooks exist
  - Action: Update for production scenarios, distribute to on-call
  - Owner: SRE Lead
  - Timeline: Day 3
  - Verification: All team members acknowledge receipt

### REGULATORY & LEGAL (3 items - **MUST SIGN OFF**)
- [ ] Legal Framework Review & Sign-Off
  - Status: Preliminary review done
  - Action: Final legal review for Indian public infrastructure context
  - Owner: Legal Counsel
  - Timeline: Day 3-4
  - Verification: Legal sign-off document signed and filed

- [ ] RTI Export Capability Verification
  - Status: Endpoint implemented, not tested
  - Action: Verify /api/v1/rtisection6/{project_id} works end-to-end
  - Owner: Lead Engineer
  - Timeline: Day 3-4
  - Verification: Test RTI export generates correct PDF with checksums

- [ ] Evidentiary Terminology Enforcement
  - Status: Code checks in place
  - Action: Final verification that "corruption" is never generated
  - Owner: Lead Engineer
  - Timeline: Day 4
  - Verification: Database constraint test passes, no corruption labels found

### PERFORMANCE (4 items - **MUST VALIDATE**)
- [ ] Real PostgreSQL Integration Testing
  - Status: 18 tests currently skipped (SQLite-only)
  - Action: Spin up staging PostgreSQL, unskip and run all 18 tests
  - Owner: Lead Engineer
  - Timeline: Day 4-5
  - Verification: 18/18 PostgreSQL tests pass

- [ ] Load Testing Against Staging
  - Status: Local testing only (scripts/stress_local.py)
  - Action: Run stress tests against staging infrastructure
  - Owner: Performance Engineer
  - Timeline: Day 5
  - Verification: 2000 concurrent users, P99 <200ms, throughput >500 rps

- [ ] Chaos Engineering Simulations
  - Status: Pod restart documented, not tested on staging
  - Action: Run pod drain, replica failure, traffic spike scenarios
  - Owner: SRE
  - Timeline: Day 5-6
  - Verification: All 4 scenarios pass without 5xx errors

- [ ] TLS Ingress & ALB Health Check Verification
  - Status: Documented but not tested end-to-end
  - Action: Verify TLS certificate chain, ALB deregistration delay
  - Owner: DevOps
  - Timeline: Day 5-6
  - Verification: Health checks pass, graceful drain observed

### LAUNCH READINESS (3 items - **FINAL SIGN-OFF**)
- [ ] Release Gate Automation
  - Status: check_release.py passes (exit 0 after Playwright fix)
  - Action: Verify all 8 CI/security gates in GitHub Actions
  - Owner: DevOps
  - Timeline: Day 6
  - Verification: PR #46 (production release) passes all gates

- [ ] Multi-Agent Council Final Verdict
  - Status: Draft assessment complete
  - Action: Convene final council meeting, record verdicts
  - Owner: Founder/CTO
  - Timeline: Day 6
  - Verification: All 5 agents sign off on production readiness

- [ ] Launch Authorization & Go-Live
  - Status: Pending
  - Action: Founder authorizes launch, execute deployment
  - Owner: Founder
  - Timeline: Day 7
  - Verification: Production environment healthy, monitoring active

---

## TEST EXECUTION MATRIX

### Day 1: Security Hardening
```bash
# Task 1.1: Token Rotation
gh auth logout  # Clear old token
gh auth login --web  # New OIDC-based auth
gh secret list  # Verify no old PATs

# Task 1.2: Branch Protection
gh repo edit --enable-auto-merge --enable-require-approvals-from-code-owners
gh repo edit --require-code-review-dismissals=true --require-branch-protection

# Task 1.3: Dependabot PRs
git fetch origin
git checkout origin/dependabot/pip/pyjwt-2.13.0
python3 -m unittest discover -s tests  # Must still pass
```

### Day 2: Operational Excellence
```bash
# Task 2.1: Kubernetes Standardization
kubectl apply -f infra/k8s/pod-spec-production.yaml
kubectl patch deployment xray -p '{"spec":{"template":{"spec":{"terminationGracePeriodSeconds":30}}}}'

# Task 2.2: PgBouncer Deployment
docker-compose -f docker-compose.prod.yml up pgbouncer
./scripts/test_pgbouncer.py  # New test script

# Task 2.3: Monitoring Setup
kubectl apply -f infra/otel/collector-config.yaml
```

### Day 3: Regulatory Compliance
```bash
# Task 3.1: RTI Export Test
curl -X GET http://localhost:8000/api/v1/rtisection6/prj_abc12345 -H "Authorization: Bearer ..."

# Task 3.2: Terminology Enforcement
python3 -c "from app.database import check_terminology_safety; check_terminology_safety('project_xray')"

# Task 3.3: Legal Sign-Off Collection
# Manual task: Founder/Legal to sign off
```

### Day 4-5: Production Proof
```bash
# Task 5.1: Real PostgreSQL Tests
export DATABASE_URL=postgresql://staging_db:5432/xray_test
python3 -m unittest discover -s tests -v  # Unskip 18 tests

# Task 5.2: Staging Load Test
python3 scripts/stress_staging.py --users 2000 --duration 300

# Task 5.3: Chaos Engineering
python3 scripts/chaos_pod_drain.py --replicas 20 --load 500
```

### Day 6-7: Launch
```bash
# Task 6.1: Release PR Creation
gh pr create --title "v0.4.1-production-release" --body "$(cat docs/RELEASE_NOTES.md)"

# Task 6.2: Final Verification
gh pr view 46 --json checks  # All checks must pass

# Task 6.3: Go-Live
gh pr merge 46 --squash --delete-branch
kubectl set image deployment/xray-prod xray=gcr.io/xray/app:v0.4.1-prod
```

---

## SUCCESS CRITERIA

**All of the following must be GREEN before launch:**

1. **Security:** No exposed tokens, branch protection active, all CVEs resolved
2. **Performance:** P99 latency <200ms, throughput >500 rps, memory stable
3. **Availability:** RTO <5min, RPO <1min, zero cascading failures in stress tests
4. **Compliance:** Legal sign-off obtained, RTI export working, terminology enforced
5. **Operations:** All monitoring alerts configured, runbooks reviewed, on-call ready

---

**Document Version:** 1.0-launch-checklist  
**Last Updated:** 2026-09-15 19:17 UTC  
**Status:** READY FOR EXECUTION  
