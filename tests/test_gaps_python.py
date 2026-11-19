"""Python resolution gaps from the 1.6.3 survey (gaps-163): real indexing and impact on small fixtures."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / 'tests/fixtures/gaps'
spec = importlib.util.spec_from_file_location('cg_gaps_python_test', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)


class GapFixture(unittest.TestCase):
    def project(self, name, source=None):
        temp = tempfile.TemporaryDirectory(prefix='cg-gaps-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'project'
        shutil.copytree(source or FIXTURES / name, root)
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=False))
        return root

    def impact(self, root, target, confidence='low'):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cg.cmd_impact(argparse.Namespace(root=str(root), target=target, min_conf=confidence,
                                            depth=1, file=False, json=True, limit=100))
        return {item['symbol']['name']: item['chain'][0]['confidence'] for item in json.loads(output.getvalue())['items']}


class Inline(GapFixture):
    def files(self, files):
        temp = tempfile.TemporaryDirectory(prefix='cg-gaps-inline-')
        self.addCleanup(temp.cleanup)
        source = Path(temp.name) / 'source'
        for path, text in files.items():
            (source / path).parent.mkdir(parents=True, exist_ok=True)
            (source / path).write_text(text)
        return self.project(None, source)


LOADER = ("import importlib.util as util\nfrom pathlib import Path\ndef drive(flag=None):\n"
          "    spec = util.spec_from_file_location('p', Path(__file__).with_name('target.py'))\n"
          "    module = util.module_from_spec(spec)\n")
CLASSES = 'class Service:\n    def run(self): return 1\nclass Worker:\n    def run(self): return 2\nservice = Service()\n'


class F10Imports(Inline):
    def test_a_later_import_replaces_an_earlier_one(self):
        root = self.files({'app.py': 'from first import f\nfrom second import f\ndef drive(): return f()\n',
                           'first.py': 'def f(): return 1\n', 'second.py': 'def f(): return 2\n'})
        self.assertEqual(self.impact(root, 'first.py:f'), {})
        self.assertEqual(self.impact(root, 'second.py:f'), {'drive': 'high'})

    def test_a_package_reexports_its_last_import(self):
        root = self.files({'app.py': 'from pkg import f\ndef drive(): return f()\n',
                           'pkg/__init__.py': 'from .first import f\nfrom .second import f\n',
                           'pkg/first.py': 'def f(): return 1\n', 'pkg/second.py': 'def f(): return 2\n'})
        self.assertEqual(self.impact(root, 'pkg/first.py:f'), {})
        self.assertEqual(self.impact(root, 'pkg/second.py:f'), {'drive': 'high'})

    def test_an_import_inside_a_function_is_not_a_reexport(self):
        root = self.files({'app.py': 'from pkg import f\ndef drive(): return f()\n',
                           'pkg/__init__.py': 'def unused():\n    from .impl import f\n', 'pkg/impl.py': 'def f(): return 1\n'})
        self.assertEqual(self.impact(root, 'pkg/impl.py:f'), {})

    def test_try_except_arms_reexported_by_a_package(self):
        root = self.files({'app.py': 'from pkg import f\ndef drive(): return f()\n',
                           'pkg/__init__.py': 'try:\n    from .fast import f\nexcept ImportError:\n    from .slow import f\n',
                           'pkg/fast.py': 'def f(): return 1\n', 'pkg/slow.py': 'def f(): return 2\n'})
        self.assertIn('drive', self.impact(root, 'pkg/fast.py:f'))
        self.assertIn('drive', self.impact(root, 'pkg/slow.py:f'))


class F10LoaderBranches(Inline):
    def loaded(self, body):
        root = self.files({'app.py': LOADER + body, 'target.py': 'def f(): return 1\n'})
        return self.impact(root, 'target.py:f')

    def test_a_loader_executed_in_one_branch_does_not_count_in_another(self):
        self.assertEqual(self.loaded('    if flag:\n        spec.loader.exec_module(module)\n    else:\n        return module.f()\n'), {})

    def test_an_assert_message_is_not_an_execution(self):
        self.assertEqual(self.loaded('    assert True, spec.loader.exec_module(module)\n    return module.f()\n'), {})

    def test_a_chained_comparison_stops_early(self):
        self.assertEqual(self.loaded('    1 < 0 < spec.loader.exec_module(module)\n    return module.f()\n'), {})

    def test_loader_and_call_in_the_same_branch(self):
        self.assertEqual(self.loaded('    if flag:\n        spec.loader.exec_module(module)\n        return module.f()\n'), {'drive': 'medium'})

    def test_a_match_capture_hides_the_module(self):
        root = self.files({'app.py': "from runpy import run_path\nfrom pathlib import Path\n"
                                     "module = run_path(Path(__file__).with_name('target.py'))\n"
                                     "def drive(value):\n    match value:\n        case {'module': module}:\n"
                                     "            return module['f']()\n", 'target.py': 'def f(): return 1\n'})
        self.assertEqual(self.impact(root, 'target.py:f'), {})


class F10ChainedResults(Inline):
    def test_a_method_on_a_loaded_result_is_dynamic_not_guessed(self):
        root = self.files({'app.py': "import importlib.util as util\nimport os\n"
                                     "spec = util.spec_from_file_location('m', os.environ.get('ENGINE', 'x.py'))\n"
                                     "mod = util.module_from_spec(spec)\nspec.loader.exec_module(mod)\n"
                                     "def drive():\n    return mod.counts().get('k')\n",
                           'store.py': 'class Store:\n    def get(self, key): return key\n',
                           'cache.py': 'class Cache:\n    def get(self, key): return key\n'})
        self.assertEqual(self.impact(root, 'store.py:Store.get'), {})
        self.assertEqual(self.impact(root, 'cache.py:Cache.get'), {})


class F10bImports(Inline):
    FILES = {'first.py': 'def f(): return 1\n', 'second.py': 'def f(): return 2\n'}

    def only_second(self, app):
        root = self.files(dict(self.FILES, **{'app.py': app}))
        self.assertEqual(self.impact(root, 'first.py:f'), {})
        self.assertEqual(self.impact(root, 'second.py:f'), {'drive': 'high'})

    def test_an_import_after_the_try_replaces_its_arms(self):
        self.only_second('try:\n    from first import f\nexcept ImportError:\n    from second import f\n'
                         'from second import f\ndef drive(): return f()\n')

    def test_sequential_imports_inside_one_arm(self):
        self.only_second('try:\n    from first import f\n    from second import f\nexcept ImportError:\n    pass\n'
                         'def drive(): return f()\n')

    def test_sequential_imports_inside_an_except_arm(self):
        self.only_second('try:\n    pass\nexcept ImportError:\n    from first import f\n    from second import f\n'
                         'def drive(): return f()\n')


class F10dSameLineOrder(Inline):
    """On one line the later import still wins: source order, never names, breaks the tie."""
    def test_same_line_reexport(self):
        root = self.files({'app.py': 'from pkg import f\ndef drive(): return f()\n',
                           'pkg/__init__.py': 'from .z import f; from .a import f\n',
                           'pkg/z.py': 'def f(): return 1\n', 'pkg/a.py': 'def f(): return 2\n'})
        self.assertEqual(self.impact(root, 'pkg/a.py:f'), {'drive': 'high'})
        self.assertEqual(self.impact(root, 'pkg/z.py:f'), {})

    def test_same_line_imports(self):
        root = self.files({'app.py': 'from z import f; from a import f\ndef drive(): return f()\n',
                           'z.py': 'def f(): return 1\n', 'a.py': 'def f(): return 2\n'})
        self.assertEqual(self.impact(root, 'a.py:f'), {'drive': 'high'})
        self.assertEqual(self.impact(root, 'z.py:f'), {})

    def test_same_line_imports_in_a_try_arm(self):
        root = self.files({'app.py': 'try:\n    from z import f; from a import f\nexcept ImportError:\n    from b import f\n'
                                     'def drive(): return f()\n',
                           'z.py': 'def f(): return 1\n', 'a.py': 'def f(): return 2\n', 'b.py': 'def f(): return 3\n'})
        self.assertEqual(self.impact(root, 'z.py:f'), {})
        self.assertEqual(self.impact(root, 'a.py:f'), {'drive': 'medium'})
        self.assertEqual(self.impact(root, 'b.py:f'), {'drive': 'medium'})


class F10bUnreachable(Inline):
    def test_nothing_after_a_raise_loads_a_module(self):
        root = self.files({'app.py': "import importlib.util as util\nfrom pathlib import Path\nfrom contextlib import suppress\n"
                                     "def drive():\n    spec = util.spec_from_file_location('x', Path(__file__).with_name('target.py'))\n"
                                     "    module = util.module_from_spec(spec)\n    with suppress(ValueError):\n"
                                     "        raise ValueError()\n        spec.loader.exec_module(module)\n    return module.f()\n",
                           'target.py': 'def f(): return 1\n'})
        self.assertEqual(self.impact(root, 'target.py:f'), {})


class F10bLoadedConstructor(Inline):
    def test_method_of_a_loaded_class_instance(self):
        root = self.files({'app.py': "from runpy import run_path\nfrom pathlib import Path\n"
                                     "Engine = run_path(Path(__file__).with_name('engine.py'))['Engine']\n"
                                     "def drive(): return Engine().run()\n",
                           'engine.py': 'class Engine:\n    def run(self): return 1\n',
                           'decoy.py': 'class Decoy:\n    def run(self): return 2\n'})
        self.assertEqual(self.impact(root, 'engine.py:Engine.run'), {'drive': 'medium'})
        self.assertEqual(self.impact(root, 'engine.py:Engine'), {'drive': 'medium'})
        self.assertEqual(self.impact(root, 'decoy.py:Decoy.run'), {})

    def test_method_on_what_a_loaded_function_returns_stays_unguessed(self):
        root = self.files({'app.py': "from runpy import run_path\nfrom pathlib import Path\n"
                                     "make = run_path(Path(__file__).with_name('factory.py'))['make']\n"
                                     "def drive(): return make().run()\n",
                           'factory.py': 'def make(): return object()\n',
                           'decoy.py': 'class Decoy:\n    def run(self): return 2\n'})
        self.assertEqual(self.impact(root, 'factory.py:make'), {'drive': 'medium'})
        self.assertEqual(self.impact(root, 'decoy.py:Decoy.run'), {})


class F10Shadowing(Inline):
    def test_loop_and_with_targets_hide_a_typed_global(self):
        for name, body in (('for', 'def drive(items):\n    for service in items:\n        service.run()\n'),
                           ('with', 'def drive(context):\n    with context as service:\n        service.run()\n')):
            with self.subTest(form=name):
                root = self.files({'app.py': CLASSES + body})
                self.assertEqual(self.impact(root, 'app.py:Service.run'), {'drive': 'low'})
                self.assertEqual(self.impact(root, 'app.py:Worker.run'), {'drive': 'low'})

    def test_a_global_instance_without_shadowing(self):
        root = self.files({'app.py': CLASSES + 'def drive():\n    service.run()\n'})
        self.assertEqual(self.impact(root, 'app.py:Service.run'), {'drive': 'high'})
        self.assertEqual(self.impact(root, 'app.py:Worker.run'), {})


class ReExports(GapFixture):
    def test_function_reexported_by_package_init(self):
        root = self.project('P13')
        self.assertEqual(self.impact(root, 'pkg/impl.py:polish'), {'drive': 'high'})

    def test_qualified_call_through_reexporting_package(self):
        root = self.project('P13b')
        self.assertEqual(self.impact(root, 'pkg/impl.py:polish'), {'drive': 'high'})

    def test_unrelated_homonym_stays_without_callers(self):
        root = self.project('P13c')
        self.assertEqual(self.impact(root, 'pkg/impl.py:polish'), {'drive': 'high'})
        self.assertEqual(self.impact(root, 'other/impl.py:polish'), {})

    def test_methods_of_a_reexported_class(self):
        root = self.project('P50')
        self.assertIn('drive', self.impact(root, 'pkg/engine.py:Engine.start'))

    def test_star_reexport_is_followed(self):
        root = self.project('X03')
        self.assertEqual(self.impact(root, 'pkg/impl.py:polish'), {'drive': 'high'})

    def test_unfollowable_project_import_falls_back_to_name_at_low(self):
        root = self.project('X02')
        self.assertEqual(self.impact(root, 'pkg/impl.py:polish'), {'drive': 'low'})

    def test_external_import_never_links_a_project_homonym(self):
        root = self.project('X01')
        self.assertEqual(self.impact(root, 'utils.py:get'), {})


class StarImports(GapFixture):
    def test_single_definition_is_unchanged(self):
        root = self.project('P15')
        self.assertEqual(self.impact(root, 'tools.py:polish'), {'drive': 'medium'})

    def test_star_import_picks_the_imported_module_over_a_homonym(self):
        root = self.project('P15b')
        self.assertEqual(self.impact(root, 'tools.py:polish'), {'drive': 'medium'})
        self.assertEqual(self.impact(root, 'legacy.py:polish'), {})


class AlternativeImports(GapFixture):
    def test_both_try_except_import_error_bindings(self):
        root = self.project('P16')
        self.assertEqual(self.impact(root, 'fastimpl.py:polish'), {'drive': 'medium'})
        self.assertEqual(self.impact(root, 'slowimpl.py:polish'), {'drive': 'medium'})


class QualifiedClass(GapFixture):
    def test_module_class_method(self):
        root = self.project('P19c')
        self.assertEqual(self.impact(root, 'models.py:Box.make'), {'drive': 'high'})


class Receivers(GapFixture):
    def test_attribute_typed_from_constructor_parameter(self):
        root = self.project('P27b')
        self.assertEqual(self.impact(root, 'repo.py:Repo.save'), {'Service.go': 'high'})
        self.assertEqual(self.impact(root, 'other.py:Cache.save'), {})

    def test_attribute_type_survives_many_homonyms(self):
        root = self.project('P27c')
        self.assertEqual(self.impact(root, 'repo.py:Repo.save'), {'Service.go': 'high'})
        for number in range(5):
            self.assertEqual(self.impact(root, f'stores.py:Store{number}.save'), {})

    def test_module_level_instance_used_inside_a_function(self):
        root = self.project('P18c')
        self.assertEqual(self.impact(root, 'app.py:Service.run'), {'drive': 'high'})
        for number in range(5):
            self.assertEqual(self.impact(root, f'jobs.py:Job{number}.run'), {})


class LoadedModulesInBlocks(GapFixture):
    def test_calls_inside_if_try_for_with(self):
        root = self.project('B13')
        callers = self.impact(root, 'rel/fs.py:inspect_roots')
        for caller in ('in_if', 'in_try', 'in_for', 'in_with'):
            self.assertEqual(callers.get(caller), 'low', caller)
        self.assertNotIn('guessed', callers)

    def test_repo_root_from_path_parents(self):
        root = self.project('L01')
        self.assertEqual(self.impact(root, 'scripts/store.py:helper'), {'test_helper': 'medium'})

    def test_loaded_class_call_keeps_class_and_init_and_the_inner_helper(self):
        root = self.project('L01')
        self.assertEqual(self.impact(root, 'scripts/store.py:Store'), {'open_store': 'medium'})
        self.assertEqual(self.impact(root, 'scripts/store.py:Store.__init__'), {'open_store': 'medium'})
        self.assertEqual(self.impact(root, 'scripts/main.py:store_class'), {'open_store': 'high'})

    def test_code_graph_sees_its_own_callers(self):
        temp = tempfile.TemporaryDirectory(prefix='cg-gaps-self-')
        self.addCleanup(temp.cleanup)
        source = Path(temp.name) / 'source'
        shutil.copytree(REPO / 'scripts', source / 'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        root = self.project(None, source)
        expected = {'scripts/project_data.py:ProjectData': 'cmd_impact',
                    'scripts/change_flow.py:ChangeFlow': 'default_flow_factory',
                    'scripts/features.py:catalog': 'ProjectData.feature_catalog',
                    'scripts/checks.py:findings': 'cmd_check'}
        for target, caller in expected.items():
            with self.subTest(target=target):
                self.assertEqual(self.impact(root, target).get(caller), 'medium')


if __name__ == '__main__':
    unittest.main()
