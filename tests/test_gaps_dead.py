"""dead and references from the 1.6.3 survey (gaps-163): registered functions, callbacks, dynamic access, start files."""
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
spec = importlib.util.spec_from_file_location('cg_gaps_dead_test', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)


class GapFixture(unittest.TestCase):
    def project(self, name):
        temp = tempfile.TemporaryDirectory(prefix='cg-gaps-dead-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'project'
        shutil.copytree(FIXTURES / name, root)
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=False))
        return root

    def dead(self, root):
        db = cg.connect(str(root))
        try:
            return {db.execute('SELECT name FROM symbols WHERE id=?', (sid,)).fetchone()[0]: verdict
                    for verdict, sid, _, _ in cg.dead_candidates(db, str(root))}
        finally:
            db.close()

    def impact(self, root, target, confidence='low'):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cg.cmd_impact(argparse.Namespace(root=str(root), target=target, min_conf=confidence,
                                            depth=1, file=False, json=True, limit=100))
        return {item['symbol']['name']: item['chain'][0]['confidence'] for item in json.loads(output.getvalue())['items']}

    def impact_text(self, root, target):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cg.cmd_impact(argparse.Namespace(root=str(root), target=target, min_conf='medium',
                                            depth=1, file=False, json=False, limit=100))
        return output.getvalue()


class RegisteredByDecorator(GapFixture):
    def assertNotProvablyDead(self, verdicts, name):
        self.assertNotIn(verdicts.get(name), ('VERIFIED_DEAD', 'LIKELY_DEAD'), name)

    def test_click_typer_flask_blueprint_and_registry(self):
        for fixture, name in (('P32', 'sync_all'), ('P36', 'sync_all'), ('P35', 'index_page'),
                              ('P35c', 'index_page'), ('P12b', 'start_cmd')):
            with self.subTest(fixture=fixture):
                self.assertNotProvablyDead(self.dead(self.project(fixture)), name)

    def test_pure_decorators_do_not_hide_dead_code(self):
        verdicts = self.dead(self.project('D01'))
        self.assertEqual(verdicts.get('cached_unused'), 'VERIFIED_DEAD')
        self.assertEqual(verdicts.get('plain_unused'), 'VERIFIED_DEAD')
        self.assertIn(verdicts.get('static_unused'), ('VERIFIED_DEAD', 'LIKELY_DEAD'))


class CallbackReferences(GapFixture):
    def test_python_thread_target(self):
        root = self.project('P08')
        self.assertEqual(self.impact(root, 'tools.py:polish'), {'drive': 'medium'})
        self.assertIn('references', self.impact_text(root, 'tools.py:polish'))

    def test_javascript_callbacks(self):
        root = self.project('J10')
        self.assertEqual(self.impact(root, 'tools.js:polish'), {'drive': 'medium'})

    def test_php_callable_array(self):
        root = self.project('H05')
        self.assertEqual(self.impact(root, 'svc.php:Svc.fmt'), {'Svc.all': 'medium'})

    def test_a_parameter_is_not_a_reference_to_a_homonym(self):
        root = self.project('D03')
        self.assertEqual(self.impact(root, 'app.py:polish'), {})

    def test_registered_route_appears_as_reference(self):
        root = self.project('P35b')
        self.assertEqual(self.impact(root, 'web.py:index_page'), {'web.py': 'medium'})


class PhpStringCallables(GapFixture):
    """A string callable 'name' always means the global function \\name, whatever the caller's namespace."""
    def files(self, files):
        temp = tempfile.TemporaryDirectory(prefix='cg-gaps-php-callable-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'project'
        for path, text in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=False))
        return root

    CALLER = "<?php\nnamespace Fx\\App;\nfunction drive($xs) {\n    return array_map('polish', $xs);\n}\n"

    def test_never_a_function_of_another_namespace(self):
        root = self.files({'app.php': self.CALLER,
                           'n4.php': "<?php\nnamespace Fx\\N4;\nfunction polish() {\n    return 4;\n}\n"})
        self.assertEqual(self.impact(root, 'n4.php:polish'), {})

    def test_never_the_homonym_of_the_callers_own_namespace(self):
        root = self.files({'app.php': self.CALLER + "function polish() {\n    return 1;\n}\n"})
        self.assertEqual(self.impact(root, 'app.php:polish'), {})

    def test_the_global_function_keeps_its_reference(self):
        root = self.files({'app.php': self.CALLER,
                           'global.php': "<?php\nfunction polish() {\n    return 0;\n}\n",
                           'n4.php': "<?php\nnamespace Fx\\N4;\nfunction polish() {\n    return 4;\n}\n"})
        self.assertEqual(self.impact(root, 'global.php:polish'), {'drive': 'medium'})
        self.assertEqual(self.impact(root, 'n4.php:polish'), {})


    def test_first_class_callable_resolves_like_a_call(self):
        caller = "<?php\nnamespace Fx\\App;\nfunction drive() {\n    $c = polish(...);\n    return $c();\n}\n"
        root = self.files({'app.php': caller, 'local.php': "<?php\nnamespace Fx\\App;\nfunction polish() {\n    return 1;\n}\n",
                           'global.php': "<?php\nfunction polish() {\n    return 0;\n}\n"})
        self.assertEqual(self.impact(root, 'local.php:polish'), {'drive': 'medium'})
        self.assertEqual(self.impact(root, 'global.php:polish'), {})
        root = self.files({'app.php': caller, 'global.php': "<?php\nfunction polish() {\n    return 0;\n}\n"})
        self.assertEqual(self.impact(root, 'global.php:polish'), {'drive': 'medium'})


class DynamicAccessAcrossFiles(GapFixture):
    def test_getattr_on_an_imported_module(self):
        verdicts = self.dead(self.project('P24b'))
        self.assertNotIn(verdicts.get('on_save'), ('VERIFIED_DEAD', 'LIKELY_DEAD'))

    def test_getattr_does_not_protect_other_modules(self):
        verdicts = self.dead(self.project('D02'))
        self.assertNotIn(verdicts.get('on_save'), ('VERIFIED_DEAD', 'LIKELY_DEAD'))
        self.assertEqual(verdicts.get('unrelated_unused'), 'VERIFIED_DEAD')

    def test_php_variable_method_on_a_typed_object(self):
        verdicts = self.dead(self.project('H08'))
        self.assertNotIn(verdicts.get('spin'), ('VERIFIED_DEAD', 'LIKELY_DEAD'))


class StartFiles(GapFixture):
    def test_procfile_counts_as_a_use(self):
        verdicts = self.dead(self.project('P39'))
        self.assertNotIn(verdicts.get('build_app'), ('VERIFIED_DEAD', 'LIKELY_DEAD'))


if __name__ == '__main__':
    unittest.main()
