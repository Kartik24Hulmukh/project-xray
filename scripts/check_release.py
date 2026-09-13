from pathlib import Path
import subprocess,sys,re
import shutil

BROWSER_REMEDIATION='npm ci && npx playwright install chromium'
BROWSER_PROBE="const fs=require('fs');const p=require('playwright');const e=p.chromium.executablePath();if(!e||!fs.existsSync(e)){console.error('XRAY_BROWSER_MISSING '+e);process.exit(3);}"

def browser_acceptance_preflight(root,runner=subprocess.run,which=shutil.which):
    """Classify UI-acceptance provisioning state before the gate runs.

    Returns a list of actionable environment problems. An empty list means
    'go': either the browser is provisioned, or Playwright is not declared by
    this repo at all (in which case scripts/ui_acceptance.mjs owns the call).
    A non-empty list is an environment/provisioning fault and must exit 2,
    never 1 - exit 1 is reserved for a real UI regression.
    """
    if which('node') is None:
        return ['node executable not found on PATH; install Node.js 18+ (release gate cannot run UI acceptance)']
    probe=runner(['node','-e',BROWSER_PROBE],cwd=root,capture_output=True)
    if probe.returncode==0:
        return []
    err=(probe.stderr or b'')
    if isinstance(err,bytes):err=err.decode('utf-8','replace')
    if 'XRAY_BROWSER_MISSING' in err:
        return ['Playwright Chromium binary is declared but not installed: '+err.strip().split('XRAY_BROWSER_MISSING',1)[1].strip()]
    if 'Cannot find module' in err or 'ERR_MODULE_NOT_FOUND' in err:
        return []
    return []

root=Path(__file__).resolve().parents[1]
required=['README.md','AGENTS.md','LICENSE','SECURITY.md','CONTRIBUTING.md','CODE_OF_CONDUCT.md','docs/ROADMAP_72_HOURS.md','docs/ACCEPTANCE_CRITERIA.md','docs/EVIDENCE_POLICY.md','docs/THREAT_MODEL.md','Dockerfile','docker-compose.yml','app/server.py','tests/test_api.py','db/schema.sql']
missing=[x for x in required if not (root/x).exists()]
if missing:print('Missing:',*missing);sys.exit(1)
preview_required=['docs/KNOWN_LIMITATIONS.md','docs/launch/POSITIONING.md','docs/launch/GO_NO_GO.md','docs/legal/DISCLAIMER.md','docs/ops/KILL_SWITCH_RUNBOOK.md','scripts/external_evaluator.py']
missing_preview=[x for x in preview_required if not (root/x).exists()]
if missing_preview:print('Missing preview files:',*missing_preview);sys.exit(1)
tracked=subprocess.run(['git','ls-files','-z'],cwd=root,capture_output=True,check=True).stdout.split(b'\0')
secret_patterns=[re.compile(rb'\bsk-[A-Za-z0-9]{20,}\b'),re.compile(rb'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----')]
for raw in tracked:
 if not raw:continue
 p=root/raw.decode()
 if p.name in {'.env.example','check_release.py'}:continue
 data=p.read_bytes()
 if any(pattern.search(data) for pattern in secret_patterns):print('Potential secret:',p);sys.exit(1)
compile_result=subprocess.run([sys.executable,'-m','compileall','-q','app','scripts','tests'],cwd=root)
if compile_result.returncode:sys.exit(compile_result.returncode)
tests=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=root)
if tests.returncode:sys.exit(tests.returncode)
provisioning=browser_acceptance_preflight(root)
if provisioning:
 for problem in provisioning:print('Release gate environment fault:',problem)
 print('Remediation:',BROWSER_REMEDIATION)
 sys.exit(2)
ui=subprocess.run(['node','scripts/ui_acceptance.mjs'],cwd=root)
if ui.returncode:sys.exit(1)
rehearsal=subprocess.run([sys.executable,'scripts/preflight_prod_env.py','--rehearsal-template','--output','artifacts/prod-rehearsal/preflight.json'],cwd=root)
if rehearsal.returncode:sys.exit(rehearsal.returncode)
rehearsal=subprocess.run([sys.executable,'scripts/rehearse_production.py'],cwd=root)
sys.exit(rehearsal.returncode)
