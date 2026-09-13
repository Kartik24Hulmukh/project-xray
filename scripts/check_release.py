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
def browser_acceptance_preflight(root):
 """Check that Playwright's declared Chromium executable is installed.

 npm ci installs the JavaScript package but deliberately does not always
 download its browser binary; report that provisioning fault before the UI
 suite so exit 1 remains reserved for an acceptance regression.
 """
 if 'playwright' not in json.loads((root/'package.json').read_text()).get('dependencies',{}):
  return []
 probe=subprocess.run(['node','-e',"import('playwright').then(({chromium})=>process.stdout.write(chromium.executablePath())).catch(()=>process.exit(1))"],cwd=root,capture_output=True,text=True)
 executable=Path(probe.stdout.strip())
 if probe.returncode or not executable.is_file():
  return ["Playwright Chromium executable is missing - run 'npx playwright install chromium' in the repository root"]
 return []
ui_problems=ui_gate_preflight(root)
if not ui_problems:
 ui_problems=browser_acceptance_preflight(root)
if ui_problems:
 for problem in ui_problems:print('Release gate preflight failed:',problem)
 sys.exit(2)
ui=subprocess.run(['node','scripts/ui_acceptance.mjs'],cwd=root)
if ui.returncode:sys.exit(ui.returncode)
rehearsal=subprocess.run([sys.executable,'scripts/preflight_prod_env.py','--rehearsal-template','--output','artifacts/prod-rehearsal/preflight.json'],cwd=root)
if rehearsal.returncode:sys.exit(rehearsal.returncode)
rehearsal=subprocess.run([sys.executable,'scripts/rehearse_production.py'],cwd=root)
sys.exit(rehearsal.returncode)
