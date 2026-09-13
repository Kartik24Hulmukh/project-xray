import ast,json,os,shutil,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts'/'check_release.py'
SOURCE=SCRIPT.read_text()
TREE=ast.parse(SOURCE)


def _load_preflight():
    for node in TREE.body:
        if isinstance(node,ast.FunctionDef) and node.name=='ui_gate_preflight':
            namespace={'shutil':shutil,'Path':Path,'json':json}
            exec(compile(ast.Module(body=[node],type_ignores=[]),str(SCRIPT),'exec'),namespace)
            return namespace['ui_gate_preflight']
    raise AssertionError('ui_gate_preflight is missing from scripts/check_release.py')


class ReleaseGatePreflightContract(unittest.TestCase):
    def test_preflight_runs_before_browser_acceptance(self):
        preflight_line=None
        ui_line=None
        for node in ast.walk(TREE):
            if isinstance(node,ast.Call) and getattr(node.func,'id','')=='ui_gate_preflight':
                preflight_line=node.lineno
            if isinstance(node,ast.Constant) and node.value=='scripts/ui_acceptance.mjs':
                ui_line=node.lineno
        self.assertIsNotNone(preflight_line,'release gate never calls ui_gate_preflight')
        self.assertIsNotNone(ui_line,'release gate no longer runs the browser acceptance suite')
        self.assertLess(preflight_line,ui_line,'dependency preflight must run before spawning node')

    def test_missing_browser_dependencies_are_reported_actionably(self):
        preflight=_load_preflight()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'package.json').write_text(json.dumps({'dependencies':{'playwright':'^1.40.0'}}))
            problems=preflight(Path(tmp))
            self.assertTrue(problems,'a clone without node_modules must fail preflight, not crash inside node')
            self.assertTrue(any('npm ci' in p or 'node runtime' in p for p in problems),problems)

    def test_installed_dependencies_pass_preflight(self):
        if shutil.which('node') is None:
            self.skipTest('node runtime not available in this environment')
        preflight=_load_preflight()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'package.json').write_text(json.dumps({'dependencies':{'playwright':'^1.40.0'}}))
            (Path(tmp)/'node_modules'/'playwright').mkdir(parents=True)
            self.assertEqual(preflight(Path(tmp)),[])

    def test_required_packages_track_the_real_manifest(self):
        if shutil.which('node') is None:
            self.skipTest('node runtime not available in this environment')
        declared=sorted(json.loads((ROOT/'package.json').read_text()).get('dependencies',{}))
        self.assertTrue(declared,'package.json must declare the browser acceptance dependencies')
        preflight=_load_preflight()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'package.json').write_text((ROOT/'package.json').read_text())
            for name in declared:
                (Path(tmp)/'node_modules'/name).mkdir(parents=True)
            self.assertEqual(preflight(Path(tmp)),[])


def _load_browser_preflight():
    import subprocess as _sp
    for node in TREE.body:
        if isinstance(node,ast.FunctionDef) and node.name=='browser_binary_preflight':
            ns={'shutil':shutil,'Path':Path,'json':json,'os':os,'subprocess':_sp,'BROWSER_REMEDIATION':'npx playwright install chromium','BROWSER_PROBE':''}
            exec(compile(ast.Module(body=[node],type_ignores=[]),str(SCRIPT),'exec'),ns)
            return ns['browser_binary_preflight']
    raise AssertionError('browser_binary_preflight is missing from scripts/check_release.py')


class _Completed:
    def __init__(self,returncode,stderr=b''):
        self.returncode=returncode
        self.stderr=stderr
        self.stdout=b''


class BrowserBinaryPreflightContract(unittest.TestCase):
    """npm ci can succeed while the Chromium binary is still missing; that is an
    environment fault (exit 2), not a UI regression (exit 1)."""

    def test_provisioned_browser_is_go(self):
        preflight=_load_browser_preflight()
        self.assertEqual(preflight(ROOT,runner=lambda *a,**k:_Completed(0)),[])

    def test_missing_binary_is_actionable_and_names_the_install_command(self):
        preflight=_load_browser_preflight()
        problems=preflight(ROOT,runner=lambda *a,**k:_Completed(3,b'XRAY_BROWSER_MISSING /home/u/.cache/ms-playwright/chromium-1140/chrome\n'))
        self.assertEqual(len(problems),1)
        self.assertIn('ms-playwright',problems[0])
        self.assertIn('playwright install chromium',problems[0])

    def test_chromium_path_contract_matches_ui_acceptance(self):
        """scripts/ui_acceptance.mjs launches CHROMIUM_PATH when set, so the
        preflight must judge that binary, not Playwright's download cache."""
        preflight=_load_browser_preflight()
        def explode(*a,**k):
            raise AssertionError('Playwright cache probe must not run when CHROMIUM_PATH is set')
        with tempfile.NamedTemporaryFile() as binary:
            self.assertEqual(preflight(ROOT,runner=explode,env={'CHROMIUM_PATH':binary.name}),[])
        problems=preflight(ROOT,runner=explode,env={'CHROMIUM_PATH':'/nonexistent/chromium'})
        self.assertEqual(len(problems),1)
        self.assertIn('/nonexistent/chromium',problems[0])
        self.assertIn('playwright install chromium',problems[0])
        # Empty/whitespace CHROMIUM_PATH falls back to the Playwright probe.
        self.assertEqual(preflight(ROOT,runner=lambda *a,**k:_Completed(0),env={'CHROMIUM_PATH':'  '}),[])

    def test_ci_provisions_the_browser_the_gate_demands(self):
        workflow=(ROOT/'.github'/'workflows'/'ci.yml').read_text()
        self.assertIn('npx playwright install --with-deps chromium',workflow)
        self.assertLess(workflow.index('npx playwright install'),workflow.index('scripts/check_release.py'))

    def test_playwright_undeclared_is_not_an_environment_fault(self):
        preflight=_load_browser_preflight()
        self.assertEqual(preflight(ROOT,runner=lambda *a,**k:_Completed(1,b"Error: Cannot find module 'playwright'")),[])

    def test_missing_node_short_circuits_before_the_probe(self):
        preflight=_load_browser_preflight()
        def explode(*a,**k):
            raise AssertionError('probe must not spawn when node is absent')
        problems=preflight(ROOT,runner=explode,which=lambda n:None)
        self.assertEqual(len(problems),1)
        self.assertIn('node runtime',problems[0])

    def test_binary_preflight_runs_before_browser_acceptance(self):
        call=SOURCE.index('browser_problems=browser_binary_preflight(root)')
        ui=SOURCE.index('scripts/ui_acceptance.mjs',call)
        self.assertLess(call,ui)
        self.assertIn('sys.exit(2)',SOURCE[call:ui])

    def test_real_ui_failure_still_exits_one(self):
        idx=SOURCE.index('ui=subprocess.run')
        self.assertIn('if ui.returncode:sys.exit(1)',SOURCE[idx:idx+200])


if __name__=='__main__':
    unittest.main()
