import ast,shutil,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts'/'check_release.py'
SOURCE=SCRIPT.read_text()
TREE=ast.parse(SOURCE)


def _load_preflight():
    for node in TREE.body:
        if isinstance(node,ast.FunctionDef) and node.name=='ui_gate_preflight':
            namespace={'shutil':shutil,'Path':Path}
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
            problems=preflight(Path(tmp))
            self.assertTrue(problems,'a clone without node_modules must fail preflight, not crash inside node')
            self.assertTrue(any('npm ci' in p or 'node runtime' in p for p in problems),problems)

    def test_installed_dependencies_pass_preflight(self):
        if shutil.which('node') is None:
            self.skipTest('node runtime not available in this environment')
        preflight=_load_preflight()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'node_modules'/'puppeteer').mkdir(parents=True)
            self.assertEqual(preflight(Path(tmp)),[])


if __name__=='__main__':
    unittest.main()
