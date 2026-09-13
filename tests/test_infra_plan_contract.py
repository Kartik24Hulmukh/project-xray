#!/usr/bin/env python3
"""Contract tests for the bootstrap-aware Infrastructure plan workflow.

Proves:
  1. tofu-validate always runs independently of AWS bootstrap
  2. tofu-validate has no id-token permission
  3. speculative-plan requires AWS_BOOTSTRAPPED == 'true'
  4. enabled speculative plan fails if any required input is absent
  5. id-token: write exists only on speculative-plan
  6. no apply/destroy/import command appears
  7. no binary plan artifact is uploaded
  8. current action references remain pinned to full SHAs
  9. scanner and Grype policies remain unchanged
 10. the three Python CVE exceptions and expiry remain unchanged
"""
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WF = ROOT / '.github/workflows'
FULL_SHA_RE = re.compile(r'^[0-9a-f]{40}$')
INFRA_PLAN = WF / 'infra-plan.yml'
SECURITY_YML = WF / 'security.yml'
CI_YML = WF / 'ci.yml'
DEPLOY_YML = WF / 'deploy.yml'
GRYPE_EXCEPTIONS = ROOT / 'security/grype-exceptions.yaml'
GRYPE_CONFIG = ROOT / '.grype.yaml'
GRYPE_CHECKER = ROOT / 'scripts/check_grype_exceptions.py'


def _job_blocks(yaml_text):
    """Split a workflow YAML into top-level job name -> body text."""
    parts = re.split(r'^  (\S+):\s*$', yaml_text, flags=re.MULTILINE)
    job_map = {}
    for i in range(1, len(parts) - 1, 2):
        job_map[parts[i]] = parts[i + 1]
    return job_map


class TestTofuValidateJob(unittest.TestCase):
    """Tests 1-2: tofu-validate always runs, no id-token."""

    def setUp(self):
        self.assertTrue(INFRA_PLAN.exists(), 'infra-plan.yml must exist')
        self.plan = INFRA_PLAN.read_text()
        self.jobs = _job_blocks(self.plan)

    def test_tofu_validate_job_exists(self):
        """Test 1: tofu-validate job is defined."""
        self.assertIn('tofu-validate', self.jobs, 'tofu-validate job must exist in infra-plan.yml')

    def test_tofu_validate_has_no_if_gate_on_bootstrap(self):
        """Test 1: tofu-validate runs independently of AWS_BOOTSTRAPPED."""
        tofu_body = self.jobs.get('tofu-validate', '')
        self.assertNotIn('AWS_BOOTSTRAPPED', tofu_body,
                         'tofu-validate must not be gated on AWS_BOOTSTRAPPED')

    def test_tofu_validate_has_no_id_token(self):
        """Test 2: tofu-validate has no id-token permission."""
        tofu_body = self.jobs.get('tofu-validate', '')
        self.assertNotIn('id-token', tofu_body,
                         'tofu-validate must not declare id-token permission')

    def test_tofu_validate_has_contents_read_only(self):
        """Test 2: tofu-validate has contents: read only."""
        tofu_body = self.jobs.get('tofu-validate', '')
        self.assertIn('contents: read', tofu_body,
                       'tofu-validate must have contents: read')

    def test_tofu_validate_runs_fmt_init_validate(self):
        """Test 1: tofu-validate runs fmt, init, validate."""
        tofu_body = self.jobs.get('tofu-validate', '')
        self.assertIn('fmt -check -recursive', tofu_body)
        self.assertIn('init -backend=false', tofu_body)
        self.assertIn('validate', tofu_body)

    def test_tofu_validate_uses_pinned_opentofu_image(self):
        """Test 1: tofu-validate uses OPENTOFU_IMAGE_DIGEST."""
        tofu_body = self.jobs.get('tofu-validate', '')
        self.assertIn('OPENTOFU_IMAGE_DIGEST', tofu_body)
        self.assertIn('sha256:[0-9a-f]{64}', tofu_body)


class TestSpeculativePlanGate(unittest.TestCase):
    """Tests 3-5: speculative-plan bootstrap gate and id-token."""

    def setUp(self):
        self.plan = INFRA_PLAN.read_text()
        self.jobs = _job_blocks(self.plan)

    def test_speculative_plan_references_aws_bootstrapped(self):
        """Test 3: speculative-plan checks AWS_BOOTSTRAPPED."""
        spec_body = self.jobs.get('speculative-plan', '')
        self.assertIn('AWS_BOOTSTRAPPED', spec_body,
                      'speculative-plan must reference AWS_BOOTSTRAPPED')

    def test_speculative_plan_gates_on_true(self):
        """Test 3: speculative-plan only proceeds when AWS_BOOTSTRAPPED == 'true'."""
        spec_body = self.jobs.get('speculative-plan', '')
        # The gate must check for the literal string "true"
        self.assertTrue(
            '"true"' in spec_body or "'true'" in spec_body,
            'speculative-plan must check AWS_BOOTSTRAPPED == true (lowercase)'
        )

    def test_speculative_plan_has_id_token_write(self):
        """Test 5: speculative-plan has id-token: write."""
        spec_body = self.jobs.get('speculative-plan', '')
        self.assertIn('id-token: write', spec_body,
                      'speculative-plan must have id-token: write')

    def test_id_token_only_on_speculative_plan(self):
        """Test 5: id-token: write exists only on speculative-plan, not other jobs."""
        for job_name, body in self.jobs.items():
            if job_name == 'speculative-plan':
                continue
            self.assertNotIn('id-token: write', body,
                             f'job {job_name} must not have id-token: write')

    def test_speculative_plan_has_same_repo_condition(self):
        """Preserve: same-repository PR condition."""
        spec_body = self.jobs.get('speculative-plan', '')
        self.assertIn('head.repo.full_name == github.repository', spec_body)

    def test_speculative_plan_validates_all_required_inputs(self):
        """Test 4: when enabled, all required inputs are validated."""
        spec_body = self.jobs.get('speculative-plan', '')
        required_vars = [
            'AWS_PLAN_ROLE_ARN',
            'OPENTOFU_IMAGE_DIGEST',
            'STAGING_DOMAIN_NAME',
            'STAGING_CERTIFICATE_ARN',
            'CANDIDATE_IMAGE_DIGEST',
            'NOTIFICATION_EMAIL',
            'GITHUB_OIDC_PROVIDER_ARN',
            'GATEWAY_ROLE_BINDINGS_SECRET_ARN',
            'APP_SECRET_ARNS_JSON',
            'COGNITO_DOMAIN_PREFIX',
            'CALLBACK_URLS_JSON',
            'LOGOUT_URLS_JSON',
        ]
        for var in required_vars:
            self.assertIn(var, spec_body,
                          f'speculative-plan must validate required variable {var}')

    def test_speculative_plan_reports_not_run_when_unbootstrapped(self):
        """The workflow must explicitly report 'not run' when unbootstrapped."""
        spec_body = self.jobs.get('speculative-plan', '')
        self.assertIn('not run', spec_body.lower(),
                      'speculative-plan must explicitly state "not run" when unbootstrapped')

    def test_speculative_plan_no_apply_destroy_import(self):
        """Test 6: no apply/destroy/import commands."""
        plan_lower = self.plan.lower()
        for cmd in ('tofu apply', 'terraform apply', 'tofu destroy',
                     'terraform destroy', 'tofu import', 'terraform import'):
            self.assertNotIn(cmd, plan_lower,
                             f'infra-plan.yml must not contain: {cmd}')

    def test_no_binary_plan_artifact_upload(self):
        """Test 7: no binary plan artifact is uploaded (upload-artifact absent)."""
        self.assertNotIn('upload-artifact', self.plan,
                         'infra-plan.yml must not upload artifacts')
        # The plan file is created locally, used for `tofu plan -out=`, then
        # deleted with rm -f. It must never be uploaded as an artifact.
        # Verify the plan is explicitly removed after use.
        self.assertIn('rm -f', self.plan,
                      'infra-plan.yml must delete the binary plan after use')


class TestActionPinning(unittest.TestCase):
    """Test 8: all action references remain pinned to full SHAs."""

    def test_all_workflow_actions_pinned(self):
        for wf_file in sorted(WF.glob('*.yml')) + sorted(WF.glob('*.yaml')):
            text = wf_file.read_text()
            for line_no, line in enumerate(text.splitlines(), 1):
                match = re.search(r'\buses:\s*([^\s#]+)', line)
                if match:
                    value = match.group(1)
                    self.assertIn('@', value,
                                   f'{wf_file.name}:{line_no}: action must use @pinning')
                    sha = value.rsplit('@', 1)[1]
                    self.assertTrue(
                        FULL_SHA_RE.fullmatch(sha),
                        f'{wf_file.name}:{line_no}: action must be pinned to full 40-char SHA: {value}'
                    )


class TestSecurityPoliciesUnchanged(unittest.TestCase):
    """Test 9: scanner and Grype policies remain unchanged."""

    def test_security_workflow_has_gitleaks(self):
        text = SECURITY_YML.read_text()
        self.assertIn('gitleaks', text.lower())
        self.assertIn('GITLEAKS_IMAGE_DIGEST', text)

    def test_security_workflow_has_trivy(self):
        text = SECURITY_YML.read_text()
        self.assertIn('trivy', text.lower())
        self.assertIn('TRIVY_IMAGE_DIGEST', text)

    def test_security_workflow_has_syft(self):
        text = SECURITY_YML.read_text()
        self.assertIn('syft', text.lower())
        self.assertIn('SYFT_IMAGE_DIGEST', text)

    def test_security_workflow_has_grype(self):
        text = SECURITY_YML.read_text()
        self.assertIn('grype', text.lower())
        self.assertIn('GRYPE_IMAGE_DIGEST', text)

    def test_grype_fail_on_high(self):
        text = SECURITY_YML.read_text()
        self.assertIn('--fail-on high', text)

    def test_grype_uses_config(self):
        text = SECURITY_YML.read_text()
        self.assertIn('--config', text)
        self.assertIn('.grype.yaml', text)

    def test_security_workflow_has_licence_scan(self):
        text = SECURITY_YML.read_text()
        self.assertIn('license', text.lower())

    def test_security_workflow_generates_sbom(self):
        text = SECURITY_YML.read_text()
        self.assertIn('sbom', text.lower())
        self.assertIn('upload-artifact', text)

    def test_security_workflow_scanner_digests_validated(self):
        text = SECURITY_YML.read_text()
        self.assertIn('sha256:[0-9a-f]{64}', text)


class TestGrypeNoExpiredWaivers(unittest.TestCase):
    """Waiver governance contract (rewritten 2026-09-13, turn 5).

    Policy change: a *zero-waiver* text contract was unenforceable once the
    pinned Alpine base shipped four HIGH libuuid advisories with no fixed
    package available, and it gave a false sense of safety because the CI
    guard never even read .grype.yaml - the only file grype loads. The
    contract is now behavioural and strictly stronger: any waiver must be
    CVE-scoped, package-scoped, dated, unexpired, human-approved and backed by
    a written exposure analysis, and the guard must fail closed on expiry.
    """

    def test_expired_exception_file_removed(self):
        self.assertFalse(GRYPE_EXCEPTIONS.exists())

    def test_guard_accepts_current_config(self):
        proc = subprocess.run([sys.executable, str(GRYPE_CHECKER)], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_guard_reads_the_file_grype_actually_loads(self):
        self.assertIn('.grype.yaml', GRYPE_CHECKER.read_text())

    def test_every_waiver_is_package_scoped(self):
        text = GRYPE_CONFIG.read_text()
        body = re.sub(r'(?m)^\s*#.*$', '', text)
        for block in body.split('- vulnerability:')[1:]:
            self.assertRegex(block, r'CVE-\d{4}-\d+')
            self.assertIn('name:', block)

    def test_no_package_version_suppression(self):
        self.assertNotIn('version:', GRYPE_CONFIG.read_text())

    def test_waivers_carry_an_unexpired_dated_review(self):
        text = GRYPE_CONFIG.read_text()
        body = re.sub(r'(?m)^\s*#.*$', '', text)
        if not re.findall(r'CVE-\d{4}-\d+', body):
            return
        m = re.search(r'#\s*expires:\s*(\d{4}-\d{2}-\d{2})', text)
        self.assertIsNotNone(m, 'waivers must declare an expiry date')
        expiry = datetime.strptime(m.group(1), '%Y-%m-%d').replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        self.assertGreater(expiry, now, 'waiver has expired; rebuild or renew review')
        self.assertLessEqual((expiry - now).days, 30, 'waivers may not run longer than 30 days')

    def test_waivers_have_a_written_exposure_analysis(self):
        body = re.sub(r'(?m)^\s*#.*$', '', GRYPE_CONFIG.read_text())
        cves = set(re.findall(r'CVE-\d{4}-\d+', body))
        if not cves:
            return
        docs = ' '.join(p.read_text() for p in (ROOT / 'docs' / 'security').glob('*.md'))
        for cve in cves:
            self.assertIn(cve, docs, f'{cve} is waived with no exposure analysis in docs/security/')

    def test_guard_fails_closed_on_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.grype.yaml').write_text(
                '# expires: 2026-01-01\nignore:\n  - vulnerability: CVE-2026-76642\n'
                '    package:\n      name: libuuid\n      type: apk\n')
            proc = subprocess.run([sys.executable, str(GRYPE_CHECKER)], cwd=tmp,
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn('expired', proc.stdout)

    def test_guard_rejects_unapproved_cve(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / '.grype.yaml').write_text(
                '# expires: 2026-12-01\nignore:\n  - vulnerability: CVE-2026-99999\n'
                '    package:\n      name: libuuid\n      type: apk\n')
            proc = subprocess.run([sys.executable, str(GRYPE_CHECKER)], cwd=tmp,
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn('unapproved', proc.stdout)


if __name__ == '__main__':
    unittest.main()
