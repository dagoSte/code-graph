"""Static file-loader bindings: real indexing and impact, never execute the fixtures."""
import argparse
import contextlib
import importlib.util
import io
import json
import runpy
from pathlib import Path
import shutil
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_dynamic_import_test', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)


class DynamicImports(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cg-dynimport-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        shutil.copytree(REPO / 'tests/fixtures/dynamic_imports', self.root)

    def put(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)

    def index(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(self.root), full=False))

    def impact(self, target, confidence='low'):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cg.cmd_impact(argparse.Namespace(root=str(self.root), target=target, min_conf=confidence,
                                            depth=1, file=False, json=True, limit=100))
        return {item['symbol']['name']: item['chain'][0]['confidence'] for item in json.loads(output.getvalue())['items']}

    def test_literal_helper_appears_in_impact(self):
        self.index()
        self.assertEqual(self.impact('rel/build-policy.py:recipe').get('build'), 'medium')

    def test_parameterized_helper_appears_at_low_confidence(self):
        self.index()
        self.assertEqual(self.impact('rel/fs.py:inspect_roots').get('check'), 'low')
        self.assertNotIn('check', self.impact('rel/fs.py:inspect_roots', 'medium'))

    def test_inline_loader_appears_at_medium_confidence(self):
        self.index()
        self.assertEqual(self.impact('rel/build-policy.py:recipe').get('direct'), 'medium')
        self.assertNotIn('direct', self.impact('rel/build-policy.py:recipe', 'high'))

    def test_loaded_module_targets_survive_one_to_four_homonymous_methods(self):
        for count in range(1, 5):
            self.put(f'rel/decoy{count}.py', f'class Kitchen{count}:\n'
                     '    def recipe(self, config): return config\n'
                     '    def inspect_roots(self, paths): return paths\n')
            self.index()
            with self.subTest(decoys=count, target='loaded'):
                self.assertEqual(self.impact('rel/build-policy.py:recipe'),
                                 {'build': 'medium', 'direct': 'medium'})
                self.assertEqual(self.impact('rel/fs.py:inspect_roots'), {'check': 'low'})
            for number in range(1, count + 1):
                for method in ('recipe', 'inspect_roots'):
                    with self.subTest(decoys=count, target=f'Kitchen{number}.{method}'):
                        self.assertEqual(self.impact(f'rel/decoy{number}.py:Kitchen{number}.{method}'), {})

    def test_run_path_subscript_and_named_alias(self):
        self.put('rel/run.py', '''from pathlib import Path
from runpy import run_path as read_module

def direct_run():
    return read_module(str(Path(__file__).with_name('build-policy.py')))['recipe']({})

def alias_run():
    recipe = read_module(Path(__file__).parent / 'build-policy.py')['recipe']
    return recipe({})
''')
        self.index()
        callers = self.impact('rel/build-policy.py:recipe')
        self.assertEqual(callers.get('direct_run'), 'medium')
        self.assertEqual(callers.get('alias_run'), 'medium')

    def test_unknown_target_is_dynamic_without_a_guessed_edge(self):
        self.put('rel/unknown.py', '''import importlib.util as util

def unresolved(filename):
    spec = util.spec_from_file_location('unknown', filename)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.recipe()

class Decoy:
    def recipe(self): pass
''')
        self.index()
        with contextlib.closing(cg.connect(str(self.root))) as db:
            sid = cg.resolve_one(db, 'rel/unknown.py:unresolved')
            self.assertEqual(cg.neighbors(db, sid, 'out', 'low'), {})
            self.assertTrue(cg.dynamic_in(db, sid))
        self.assertNotIn('unresolved', self.impact('rel/build-policy.py:recipe'))

    def test_unexecuted_or_mismatched_spec_is_not_a_loaded_module(self):
        self.put('rel/unloaded.py', '''from importlib.util import spec_from_file_location as spec_file, module_from_spec
from pathlib import Path

def not_loaded():
    spec = spec_file('p', Path(__file__).with_name('build-policy.py'))
    module = module_from_spec(spec)
    return module.recipe()

def wrong_spec():
    spec = spec_file('p', Path(__file__).with_name('build-policy.py'))
    other = spec_file('q', Path(__file__).with_name('fs.py'))
    module = module_from_spec(spec)
    other.loader.exec_module(module)
    return module.recipe()
''')
        self.index()
        callers = self.impact('rel/build-policy.py:recipe')
        self.assertNotIn('not_loaded', callers)
        self.assertNotIn('wrong_spec', callers)

    def test_sources_are_not_executed_and_target_matching_is_exact(self):
        marker = self.root / 'EXECUTED'
        self.put('rel/build-policy.py', "raise RuntimeError('do not import')\nfrom pathlib import Path\nPath(%r).touch()\ndef recipe(config): return config\n" % str(marker))
        self.put('elsewhere/build-policy.py', 'def recipe(config): return config\n')
        self.index()
        self.assertFalse(marker.exists())
        self.assertIn('direct', self.impact('rel/build-policy.py:recipe'))
        self.assertEqual(self.impact('elsewhere/build-policy.py:recipe'), {})

    def test_reassignment_and_conditional_path_are_not_assumed(self):
        self.put('rel/rebound.py', '''import importlib.util as util
from pathlib import Path

def rebound():
    spec = util.spec_from_file_location('p', Path(__file__).with_name('build-policy.py'))
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module = object()
    return module.recipe()

def conditional(flag):
    filename = Path(__file__).with_name('build-policy.py')
    if flag:
        filename = 'elsewhere.py'
    spec = util.spec_from_file_location('p', filename)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.recipe()
''')
        self.index()
        callers = self.impact('rel/build-policy.py:recipe')
        self.assertNotIn('rebound', callers)
        self.assertNotIn('conditional', callers)

    def test_analyzing_a_function_does_not_execute_its_loader_for_other_functions(self):
        self.put('rel/not_executed.py', '''import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location('p', Path(__file__).with_name('build-policy.py'))
module = importlib.util.module_from_spec(spec)

def load_only_if_called():
    spec.loader.exec_module(module)

def not_loaded_yet():
    return module.recipe()
''')
        self.index()
        self.assertNotIn('not_loaded_yet', self.impact('rel/build-policy.py:recipe'))

    def test_shadowed_api_and_non_path_argument_are_not_interpreted(self):
        self.put('rel/shadowed.py', '''from pathlib import Path
import importlib.util as util

def shadowed(util):
    spec = util.spec_from_file_location('p', Path(__file__).with_name('build-policy.py'))
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.recipe()

def not_a_path():
    spec = util.spec_from_file_location('p', Path)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.recipe()
''')
        self.index()
        callers = self.impact('rel/build-policy.py:recipe')
        self.assertNotIn('shadowed', callers)
        self.assertNotIn('not_a_path', callers)

    def test_evidence_describes_file_binding_at_both_confidences(self):
        reason = runpy.run_path(str(REPO / 'scripts/evidence.py'))['edge_reason']
        for confidence in ('medium', 'low'):
            self.assertEqual(reason('loaded_' + confidence, confidence), 'dynamic_module_file')

    def test_long_unicode_line_keeps_the_resolved_alias_call_in_view(self):
        prefix = '    padding = ' + repr('è' * 400) + '; return '
        self.put('rel/window.py', "from runpy import run_path\nfrom pathlib import Path\n"
                 "invoke = run_path(Path(__file__).with_name('build-policy.py'))['recipe']\n"
                 "def caller():\n" + prefix + 'invoke({})\n')
        self.index()
        ProjectData = runpy.run_path(str(REPO / 'scripts/project_data.py'))['ProjectData']
        window = runpy.run_path(str(REPO / 'scripts/source_windows.py'))['window']
        with ProjectData(cg, self.root) as project:
            source = dict(file='rel/window.py', lines=[dict(number=5, text=prefix + 'invoke({})')], truncated=False)
            result = window(project, source, focus=dict(name='recipe', owner='caller'))
        self.assertIn('invoke({})', result['lines'][0]['text'])
        self.assertEqual(result['window']['focus_status'], 'unique')

    def test_incremental_retargeting_removes_the_previous_edge(self):
        self.put('rel/next.py', 'def recipe(config): return config\n')
        self.index()
        self.assertIn('direct', self.impact('rel/build-policy.py:recipe'))
        path = self.root / 'rel/build_release.py'
        path.write_text(path.read_text().replace('build-policy.py', 'next.py'))
        self.index()
        self.assertNotIn('direct', self.impact('rel/build-policy.py:recipe'))
        self.assertIn('direct', self.impact('rel/next.py:recipe'))

    def test_unevaluated_loader_does_not_make_the_module_callable(self):
        for expression in ('unused = lambda: spec.loader.exec_module(module)',
                           'False and spec.loader.exec_module(module)'):
            with self.subTest(expression=expression):
                self.put('rel/deferred.py', "import importlib.util as util\nfrom pathlib import Path\n"
                         "def deferred():\n"
                         "    spec = util.spec_from_file_location('p', Path(__file__).with_name('build-policy.py'))\n"
                         "    module = util.module_from_spec(spec)\n    " + expression +
                         "\n    return module.recipe({})\n")
                self.index()
                self.assertNotIn('deferred', self.impact('rel/build-policy.py:recipe'))

    def test_growing_path_expressions_are_bounded_and_declared_unknown(self):
        self.put('rel/large.py', "from runpy import run_path\ndef growing():\n    path = 'a'\n" +
                 '    path = path + path\n' * 16 + "    return run_path(path)['recipe']({})\n")
        self.index()
        with contextlib.closing(cg.connect(str(self.root))) as db:
            sid = cg.resolve_one(db, 'rel/large.py:growing')
            self.assertTrue(cg.dynamic_in(db, sid))


if __name__ == '__main__':
    unittest.main()
