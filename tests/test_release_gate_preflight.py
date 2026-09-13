import ast,json,shutil,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts'/'check_release.py'
SOURCE=SCRIPT.read_text()
TREE=ast.parse(SOURCE)

def _load_function(name):
 for node in TREE.body:
  if isinstance(node,ast.FunctionDef) and node.name==name:
   namespace={'shutil':shutil,'Path':Path,'json':json,'subprocess':__import__('subprocess')}
   exec(compile(ast.Module(body=[node],type_ignores=[]),str(SCRIPT),'exec'),namespace)
   return namespace[name]
 raise AssertionError(name+' is missing from scripts/check_release.py')


def _load_preflight():
    return _load_function('ui_gate_preflight')


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


    def test_browser_preflight_precedes_browser_acceptance(self):
        browser_line=None
        ui_line=None
        for node in ast.walk(TREE):
            if isinstance(node,ast.Call) and getattr(node.func,'id','')=='browser_acceptance_preflight':
                browser_line=node.lineno
            if isinstance(node,ast.Constant) and node.value=='scripts/ui_acceptance.mjs':
                ui_line=node.lineno
        self.assertIsNotNone(browser_line,'release gate never checks the Playwright browser executable')
        self.assertLess(browser_line,ui_line,'browser preflight must run before UI acceptance')

    def test_missing_playwright_browser_is_actionable(self):
        browser_preflight=_load_function('browser_acceptance_preflight')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'package.json').write_text(json.dumps({'dependencies':{'playwright':'^1.40.0'}}))
            (root/'node_modules'/'playwright').mkdir(parents=True)
            problems=browser_preflight(root)
            self.assertTrue(problems)
            self.assertTrue(any('playwright install chromium' in p for p in problems),problems)

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


if __name__=='__main__':
    unittest.main()
