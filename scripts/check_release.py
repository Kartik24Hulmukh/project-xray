from pathlib import Path
import subprocess,sys,re
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
ui=subprocess.run(['node','scripts/ui_acceptance.mjs'],cwd=root)
if ui.returncode:sys.exit(ui.returncode)
rehearsal=subprocess.run([sys.executable,'scripts/preflight_prod_env.py','--rehearsal-template','--output','artifacts/prod-rehearsal/preflight.json'],cwd=root)
if rehearsal.returncode:sys.exit(rehearsal.returncode)
rehearsal=subprocess.run([sys.executable,'scripts/rehearse_production.py'],cwd=root)
sys.exit(rehearsal.returncode)
