from pathlib import Path
import json,shutil,subprocess,sys,re
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
def ui_gate_preflight(root):
 """Return actionable problems that would make the browser acceptance gate
 fail for environment reasons rather than for a real UI regression.

 The required node packages are derived from package.json so the gate can
 never drift from the declared dependency set."""
 problems=[]
 if shutil.which('node') is None:
  problems.append("node runtime not found on PATH - install the Node.js version pinned in .github/workflows/ci.yml")
  return problems
 manifest=root/'package.json'
 if not manifest.exists():return problems
 declared=sorted(json.loads(manifest.read_text()).get('dependencies',{}))
 missing=[name for name in declared if not (root/'node_modules'/name).exists()]
 if missing:
  problems.append("browser acceptance dependencies missing from node_modules (%s) - run 'npm ci' in the repository root"%', '.join(missing))
 return problems
BROWSER_REMEDIATION="npx playwright install chromium"
BROWSER_PROBE="const fs=require('fs');const p=require('playwright');const e=p.chromium.executablePath();if(!e||!fs.existsSync(e)){console.error('XRAY_BROWSER_MISSING '+e);process.exit(3);}"
def browser_binary_preflight(root,runner=subprocess.run,which=shutil.which):
 """Complement ui_gate_preflight: node_modules can be complete while the
 Chromium *binary* is still unprovisioned (npm ci does not download it).

 Returns actionable environment problems; empty list means go. A declared
 but uninstalled browser is a provisioning fault (exit 2), never exit 1,
 which stays reserved for a genuine UI regression."""
 if which('node') is None:
  return ["node runtime not found on PATH - install the Node.js version pinned in .github/workflows/ci.yml"]
 probe=runner(['node','-e',BROWSER_PROBE],cwd=str(root),capture_output=True)
 if probe.returncode==0:return []
 err=probe.stderr or b''
 if isinstance(err,bytes):err=err.decode('utf-8','replace')
 if 'XRAY_BROWSER_MISSING' in err:
  return ["Playwright Chromium binary is declared but not installed (%s) - run '%s'"%(err.strip().split('XRAY_BROWSER_MISSING',1)[1].strip(),BROWSER_REMEDIATION)]
 return []
ui_problems=ui_gate_preflight(root)
if ui_problems:
 for problem in ui_problems:print('Release gate preflight failed:',problem)
 sys.exit(2)
browser_problems=browser_binary_preflight(root)
if browser_problems:
 for problem in browser_problems:print('Release gate preflight failed:',problem)
 sys.exit(2)
ui=subprocess.run(['node','scripts/ui_acceptance.mjs'],cwd=root)
if ui.returncode:sys.exit(1)
rehearsal=subprocess.run([sys.executable,'scripts/preflight_prod_env.py','--rehearsal-template','--output','artifacts/prod-rehearsal/preflight.json'],cwd=root)
if rehearsal.returncode:sys.exit(rehearsal.returncode)
rehearsal=subprocess.run([sys.executable,'scripts/rehearse_production.py'],cwd=root)
sys.exit(rehearsal.returncode)
