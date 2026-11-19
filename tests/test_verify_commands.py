"""Runner evidence is inert; only the explicit unittest execution test runs fixture code."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_verify_commands', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
Project = runpy.run_path(str(REPO / 'scripts/project_data.py'))['ProjectData']
change = runpy.run_path(str(REPO / 'scripts/change_analysis.py'))
commands = runpy.run_path(str(REPO / 'scripts/test_commands.py'))


class VerifyCommands(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='cg-verify-command-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def put(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text)

    def index(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(self.root), full=False))

    def plan(self, name='work'):
        self.index()
        with Project(cg, self.root) as project:
            sid = cg.resolve_one(project.db, name)
            return change['plan'](project, [sid])

    def python(self):
        self.put('app.py', 'def work(x): return x + 1\n')
        self.put('tests/__init__.py', '')
        self.put('tests/test_app.py', 'import unittest\nfrom app import work\n'
                 'class Work(unittest.TestCase):\n'
                 '    def test_first(self): self.assertEqual(work(1), 2)\n'
                 '    def test_second(self): self.assertEqual(work(2), 3)\n'
                 '    def test_unrelated(self): self.fail("must not run")\n')

    def test_unittest_commands_execute_the_selected_tests_and_nothing_else(self):
        self.python()
        value = self.plan()
        self.assertFalse(value['execution_performed'])
        self.assertEqual(value['cwd'], '.')
        self.assertEqual(value['command'], ['python3', '-m', 'unittest',
                         'tests.test_app.Work.test_first', 'tests.test_app.Work.test_second'])
        for item in value['tests']:
            run = subprocess.run(item['command'], cwd=self.root, capture_output=True, text=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('Ran 1 test', run.stderr)
        run = subprocess.run(value['command'], cwd=self.root, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Ran 2 tests', run.stderr)

    def test_pytest_configuration_selects_nodes_without_importing_conftest(self):
        self.put('app.py', 'def work(x): return x\n')
        self.put('tests/test_app.py', 'from app import work\ndef test_first(): assert work(1) == 1\n')
        configs = [('pytest.ini', '[pytest]\n'), ('conftest.py', 'raise RuntimeError("not imported")\n'),
                   ('pyproject.toml', '[tool.pytest.ini_options]\ntestpaths=["tests"]\n'),
                   ('setup.cfg', '[tool:pytest]\n'), ('tox.ini', '[pytest]\n')]
        for path, text in configs:
            with self.subTest(path=path):
                self.put(path, text)
                value = self.plan()
                self.assertEqual(value['tests'][0]['command'],
                                 ['python3', '-m', 'pytest', './tests/test_app.py::test_first'])
                self.assertIn(path, value['tests'][0]['command_evidence'])
                (self.root / path).unlink()

    def test_unknown_runner_and_missing_package_init_have_reasons(self):
        self.put('app.py', 'def work(x): return x\n')
        self.put('tests/test_app.py', 'from app import work\ndef test_first(): return work(1)\n')
        value = self.plan()
        self.assertIsNone(value['command'])
        self.assertTrue(value['tests'][0]['command_reason'])
        self.python()
        (self.root / 'tests/__init__.py').unlink()
        value = self.plan()
        self.assertIsNone(value['tests'][0]['command'])
        self.assertIn('__init__.py', value['tests'][0]['command_reason'])

    def test_alias_and_local_unittest_inheritance_are_static(self):
        self.put('app.py', 'def work(x): return x\n')
        self.put('test_app.py', 'from unittest import TestCase as Case\nfrom app import work\n'
                 'class Base(Case): pass\nclass Work(Base):\n    def test_work(self): work(1)\n')
        value = self.plan()
        self.assertEqual(value['tests'][0]['command'], ['python3', '-m', 'unittest', 'test_app.Work.test_work'])

    def test_go_commands_select_packages_and_anchor_all_names(self):
        self.put('go.mod', 'module example.invalid/app\n\ngo 1.20\n')
        self.put('calc/app.go', 'package calc\nfunc Work(x int) int { return x }\n')
        self.put('calc/app_test.go', 'package calc\nimport "testing"\n'
                 'func TestWork(t *testing.T) { Work(1) }\nfunc TestOther(t *testing.T) { Work(2) }\n')
        value = self.plan('Work')
        self.assertEqual(value['tests'][0]['command'], ['go', 'test', './calc', '-run', '^TestWork$'])
        self.assertEqual(value['command'], ['go', 'test', './calc', '-run', '^(TestWork|TestOther)$'])
        self.put('go.mod', 'not a module declaration\n')
        self.assertIsNone(self.plan('Work')['command'])
        (self.root / 'go.mod').unlink()
        self.assertIsNone(self.plan('Work')['command'])

    def js(self, runner, extension='js'):
        self.put('package.json', json.dumps({'devDependencies': {runner: '*'}}))
        self.put('app.' + extension, 'export function work(x) { return x; }\n')
        self.put('tests/app.test.' + extension, 'import { work } from "../app";\n'
                 'function exercise() { return work(1); }\ntest("works", exercise);\n')

    def test_jest_and_vitest_propose_local_file_commands_and_never_run_them(self):
        for runner, extension in [('jest', 'js'), ('vitest', 'ts')]:
            with self.subTest(runner=runner):
                self.js(runner, extension)
                value = self.plan()
                expected = ['./node_modules/.bin/' + runner,
                            '--runTestsByPath' if runner == 'jest' else 'run', './tests/app.test.' + extension]
                self.assertEqual(value['command'], expected)
                self.assertEqual(value['tests'][0]['command_scope'], 'file')
                self.assertEqual(value['tests'][0]['role'], 'test_file')
                self.assertFalse((self.root / 'node_modules').exists())
                for path in ['app.' + extension, 'tests/app.test.' + extension]:
                    (self.root / path).unlink()

    def test_ambiguous_and_invalid_js_configs_do_not_guess(self):
        self.js('jest')
        for config in [{'devDependencies': {'jest': '*', 'vitest': '*'}}, {'scripts': {'test': 'echo jest'}}, None]:
            self.put('package.json', json.dumps(config))
            value = self.plan()
            self.assertIsNone(value['command'])
            self.assertIsNone(value['tests'][0]['command'])
            self.assertTrue(value['tests'][0]['command_reason'])
        self.put('package.json', json.dumps({'scripts': {'test': 'vitest run'}}))
        self.assertEqual(self.plan()['command'][0], './node_modules/.bin/vitest')

    def test_mixed_and_unrecognized_test_selections_have_no_aggregate(self):
        a = commands['proposal'](['python3', '-m', 'unittest', 'test_a.C.test_x'], runner='unittest')
        b = commands['proposal'](['go', 'test', '.', '-run', '^TestX$'], runner='go')
        self.assertIsNone(commands['aggregate']([a, b])['command'])
        self.assertIsNone(commands['aggregate']([a, commands['proposal'](reason='unknown')])['command'])
        self.assertIsNone(commands['aggregate']([])['command'])

    def test_runner_script_options_are_not_silently_dropped(self):
        self.js('jest')
        for script in ['jest --config config/unit.js', 'jest && echo done']:
            self.put('package.json', json.dumps({'devDependencies': {'jest': '*'}, 'scripts': {'test': script}}))
            value = self.plan()
            self.assertIsNone(value['command'])
            self.assertTrue(value['tests'][0]['command_reason'])

    def test_verify_never_starts_a_process_or_imports_project_source(self):
        self.python()
        self.index()
        with Project(cg, self.root) as project, patch('subprocess.run', side_effect=AssertionError('process started')):
            value = change['plan'](project, [cg.resolve_one(project.db, 'work')])
        self.assertIsNotNone(value['command'])
        self.assertFalse(value['execution_performed'])

    def test_uncertain_unittest_bindings_are_not_treated_as_the_standard_runner(self):
        self.put('app.py', 'def work(x): return x\n')
        for imports in ['from other_framework import TestCase\n',
                        'from unittest import TestCase\nTestCase = other_framework()\n']:
            self.put('test_app.py', imports + 'from app import work\n'
                     'class Work(TestCase):\n    def test_work(self): work(1)\n')
            value = self.plan()
            self.assertIsNone(value['tests'][0]['command'])
            self.assertTrue(value['tests'][0]['command_reason'])

    def test_nested_and_symlink_runner_evidence_is_not_used_for_a_root_command(self):
        self.js('jest')
        self.put('tests/package.json', '{"devDependencies":{"vitest":"*"}}')
        self.assertIsNone(self.plan()['command'])
        (self.root / 'tests/package.json').unlink()
        (self.root / 'package.json').unlink()
        with tempfile.TemporaryDirectory() as folder:
            outside = Path(folder) / 'package.json'
            outside.write_text('{"devDependencies":{"jest":"*"}}')
            (self.root / 'package.json').symlink_to(outside)
            value = self.plan()
            self.assertIsNone(value['command'])
            self.assertTrue(value['tests'][0]['command_reason'])

    def test_cli_and_mcp_share_the_proposed_command(self):
        self.python()
        self.index()
        cli = subprocess.run([str(REPO / 'scripts/cg'), '--root', str(self.root), 'verify', 'work', '--json'],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        requests = [dict(jsonrpc='2.0', id=1, method='initialize'),
                    dict(jsonrpc='2.0', id=2, method='tools/call', params=dict(name='cg_verify_plan', arguments=dict(target='work')))]
        rpc = subprocess.run([str(REPO / 'scripts/cg'), '--root', str(self.root), 'mcp'],
                             input=''.join(json.dumps(x) + '\n' for x in requests), capture_output=True, text=True, timeout=20)
        self.assertEqual(rpc.returncode, 0, rpc.stderr)
        reply = json.loads(rpc.stdout.splitlines()[1])['result']
        self.assertFalse(reply['isError'])
        value = json.loads(reply['content'][0]['text'])
        self.assertEqual(json.loads(cli.stdout), value)
        self.assertIsNotNone(value['command'])

    def test_project_python_is_preferred_and_recorded_without_detection_execution(self):
        self.python()
        for name in ('venv', '.venv'):
            binary = self.root / name / 'bin/python'
            binary.parent.mkdir(parents=True)
            binary.symlink_to(sys.executable)
            self.index()
            with Project(cg, self.root) as project, patch('subprocess.run', side_effect=AssertionError('detection executed a process')):
                value = change['plan'](project, [cg.resolve_one(project.db, 'work')])
            expected = name + '/bin/python'
            self.assertEqual(value['command'][0], expected)
            self.assertTrue(all(item['command'][0] == expected for item in value['tests']))
            self.assertIn(expected, value['command_evidence'])
        run = subprocess.run(value['command'], cwd=self.root, capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Ran 2 tests', run.stderr)

    def test_django_like_testcase_does_not_invent_a_unittest_command(self):
        self.put('app.py', 'def work(x): return x\n')
        self.put('tests/test_app.py', 'from django.test import TestCase\nfrom app import work\n'
                 'class App(TestCase):\n    def test_work(self): work(1)\n')
        value = self.plan()
        self.assertTrue(value['tests'])
        self.assertIsNone(value['command'])
        self.assertIsNone(value['tests'][0]['command'])
        self.assertTrue(value['tests'][0]['command_reason'])


if __name__ == '__main__':
    unittest.main()
