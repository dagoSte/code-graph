"""Human summaries are bounded views; JSON and command argv remain the authoritative data."""
import copy
import json
from pathlib import Path
import runpy
import shlex
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
summary = runpy.run_path(str(REPO / 'scripts/summaries.py'))
commands = runpy.run_path(str(REPO / 'scripts/test_commands.py'))


def plan(count=3):
    tests = []
    for index in range(count):
        direct = index < 2
        node = dict(file='tests/test_app.py', line=index + 1, name=f'App.test_{index}')
        item = dict(symbol=node, distance=1 if direct else 2, priority='direct' if direct else 'transitive',
                    role='test_case', chain=[dict(file=node['file'], line=index + 1)] +
                    ([] if direct else [dict(file='api.py', line=9)]))
        item.update(commands['proposal'](['python3', '-m', 'unittest', f'tests.test_app.App.test_{index}'],
                                        runner='unittest', scope='test', evidence=[node['file']]))
        tests.append(item)
    return dict(tests=tests, unknown_calls=[], broader_verification_required=False, execution_performed=False,
                **commands['aggregate'](tests))


class Summaries(unittest.TestCase):
    def test_verify_groups_tests_shows_safe_command_and_never_mutates_json(self):
        value = plan()
        before = json.dumps(value, sort_keys=True)
        text = summary['verify'](value, 'work')
        self.assertIn('3 tests reach work (2 direct, 1 transitive) · runner unittest', text)
        self.assertIn('transitive tests/test_app.py:3 App.test_2 via api.py:9', text)
        line = next(line for line in text.splitlines() if line.startswith('run all: '))
        self.assertEqual(shlex.split(line.removeprefix('run all: ').removesuffix(' (cwd: .)')), value['command'])
        self.assertNotIn('Broader verification required', text)
        self.assertNotIn('Unknown/unresolved call records', text)
        self.assertIn('no tests executed', text)
        self.assertEqual(json.dumps(value, sort_keys=True), before)

    def test_large_selection_has_direct_only_command_and_explicit_command_preview(self):
        value = plan(40)
        original = copy.deepcopy(value)
        text = summary['verify'](value, 'work')
        self.assertIn('… 20 more tests', text)
        self.assertIn('(preview; complete argv in --json)', text)
        line = next(line for line in text.splitlines() if line.startswith('run direct only: '))
        argv = shlex.split(line.removeprefix('run direct only: ').removesuffix(' (cwd: .)'))
        self.assertEqual(argv, commands['aggregate'](value['tests'][:2])['command'])
        self.assertLess(len(text.splitlines()), 30)
        self.assertEqual(value, original)

    def test_unknown_runner_empty_selection_and_frontier_are_explicit(self):
        value = plan(0)
        value.update(unknown_calls=[dict(status='unresolved')], broader_verification_required=True)
        text = summary['verify'](value)
        self.assertIn('run all: unavailable', text)
        self.assertIn('An empty list does not prove there are no tests', text)
        self.assertIn('Unknown/unresolved call records: 1', text)
        self.assertIn('Broader verification required', text)
        value = plan(1)
        value['tests'][0].update(command=None, command_reason='Test selected from baseline.', basis='baseline')
        value.update(commands['aggregate'](value['tests']))
        text = summary['verify'](value)
        self.assertIn('[baseline] [command unavailable]', text)
        self.assertIn('Test selected from baseline.', text)

    def test_command_quotes_and_control_characters_are_not_misrepresented(self):
        value = dict(command=['runner', 'path with spaces', 'a;b', "a'b"], cwd='.')
        text = summary['command_line']('run all', value)
        self.assertEqual(shlex.split(text.removeprefix('run all: ').removesuffix(' (cwd: .)')), value['command'])
        value['command'] = ['runner', 'path\nwith newline']
        text = summary['command_line']('run all', value)
        self.assertEqual(len(text.splitlines()), 1)
        self.assertIn('(JSON argv)', text)
        argv = text.split(': ', 1)[1].removesuffix(' (cwd: .)')
        self.assertEqual(json.loads(argv), value['command'])

    def test_target_tests_are_not_described_as_transitive(self):
        value = plan(1)
        value['tests'][0].update(distance=0, priority='transitive')
        text = summary['verify'](value)
        self.assertIn('(1 target, 0 direct, 0 transitive)', text)
        self.assertIn('  target ', text)
        self.assertEqual(value['tests'][0]['priority'], 'transitive')

    def test_via_names_use_current_symbols_and_never_reuse_baseline_ids(self):
        value = plan()
        value['tests'][2]['chain'][1]['caller'] = 17
        original = copy.deepcopy(value)
        text = summary['verify'](value, caller_names={17: 'handle_cancel'})
        self.assertIn('via api.py:9 handle_cancel', text)
        self.assertEqual(value, original)
        value['tests'][2]['basis'] = 'baseline'
        self.assertNotIn('handle_cancel', summary['verify'](value, caller_names={17: 'handle_cancel'}))

    def test_staged_review_uses_staged_caller_names_instead_of_working_tree_ids(self):
        with tempfile.TemporaryDirectory(prefix='cg-summary-stage-') as folder:
            root = Path(folder)
            (root / 'tests').mkdir()
            (root / 'tests/__init__.py').write_text('')
            def write(name, changed=False):
                (root / 'app.py').write_text('def work(x):\n' + ('    # edited\n' if changed else '') + '    return x\n')
                (root / 'api.py').write_text(f'from app import work\ndef {name}(x):\n    return work(x)\n')
                (root / 'tests/test_app.py').write_text(f'import unittest\nfrom api import {name}\nclass App(unittest.TestCase):\n    def test_api(self): self.assertEqual({name}(1),1)\n')
            def git(*args):
                subprocess.run(['git', '-C', str(root), '-c', 'core.hooksPath=/dev/null', '-c', 'commit.gpgsign=false',
                                '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', *args], capture_output=True, check=True)
            cg = [str(REPO / 'scripts/cg'), '--root', str(root)]
            write('staged_name'); git('init', '-q'); git('add', '.'); git('commit', '-qm', 'base')
            write('staged_name', True); git('add', '.')
            write('unstaged_name', True)
            subprocess.run(cg + ['index'], capture_output=True, check=True)
            text = subprocess.run(cg + ['review', '--base', 'HEAD', '--staged'], capture_output=True, text=True, check=True).stdout
            self.assertIn('via api.py:3 staged_name', text)
            self.assertNotIn('unstaged_name', text)
            text = subprocess.run(cg + ['verify', 'work'], capture_output=True, text=True, check=True).stdout
            self.assertIn('via api.py:3 unstaged_name', text)

    def test_review_reports_baseline_findings_definitions_and_budget_omissions(self):
        value = dict(baseline=dict(status='available', commit='abc123'),
                     symbols=[dict(file='app.py', line=n, end_line=n+1, name=f'work_{n}', signature=f'def work_{n}(x):') for n in range(12)],
                     summary=dict(new=7, persistent=2, resolved=1, uncompared=0),
                     comparison=dict(new=[dict(file='app.py', line=n, severity='warning', rule_id='arity', message='wrong arity') for n in range(7)]),
                     verification=plan(), output=dict(status='partial', omitted_records=22, max_chars=2000))
        original = copy.deepcopy(value)
        text = summary['review'](value)
        self.assertIn('Baseline: available · abc123', text)
        self.assertIn('app.py:0-1 work_0 · def work_0(x):', text)
        self.assertIn('… 4 more definitions', text)
        self.assertIn('Findings: new 7 · persistent 2 · resolved 1 · uncompared 0', text)
        self.assertIn('new warning app.py:0 [arity] wrong arity', text)
        self.assertIn('… 2 more new findings', text)
        self.assertIn('Omissions: 22 evidence records', text)
        self.assertIn('3 tests listed for the changed definitions', text)
        self.assertEqual(value, original)
        value['comparison']['new'] = []  # the budget may have removed all new-finding details
        self.assertIn('… 7 more new findings', summary['review'](value))

    def test_review_unavailable_baseline_and_insufficient_budget_are_honest(self):
        value = dict(baseline=dict(status='unavailable', reason='No Git base'), symbols=[],
                     summary=dict(new=0, uncompared=1), comparison=dict(uncompared=[
                         dict(file='app.py', line=1, severity='warning', message='candidate', rule_id='arity')]))
        text = summary['review'](value)
        self.assertIn('Baseline: unavailable · No Git base', text)
        self.assertIn('uncompared warning app.py:1', text)
        self.assertNotIn('  new warning', text)
        self.assertIn('including deletions', text)
        text = summary['review'](dict(status='budget_insufficient', max_chars=128))
        self.assertIn('Increase --max-chars', text)

    def test_cli_defaults_are_text_but_json_and_mcp_false_remain_structured(self):
        with tempfile.TemporaryDirectory(prefix='cg-summary-') as folder:
            root = Path(folder)
            (root / 'tests').mkdir()
            (root / 'app.py').write_text('def work(x): return x\n')
            (root / 'tests/__init__.py').write_text('')
            (root / 'tests/test_app.py').write_text('import unittest\nfrom app import work\nclass App(unittest.TestCase):\n    def test_work(self): self.assertEqual(work(1),1)\n')
            cg = [str(REPO / 'scripts/cg'), '--root', str(root)]
            subprocess.run(cg + ['index'], capture_output=True, check=True)
            for name, args in [('verify', ['verify', 'work']), ('review', ['review'])]:
                text = subprocess.run(cg + args, capture_output=True, text=True, check=True).stdout
                with self.assertRaises(json.JSONDecodeError):
                    json.loads(text)
                self.assertIn('no tests executed', text)
                value = json.loads(subprocess.run(cg + args + ['--json'], capture_output=True, text=True, check=True).stdout)
                request = dict(jsonrpc='2.0', id=1, method='tools/call', params=dict(name='cg_verify_plan' if name == 'verify' else 'cg_review',
                               arguments=dict(target='work', json=False) if name == 'verify' else dict(json=False)))
                rpc = subprocess.run(cg + ['mcp'], input=json.dumps(request)+'\n', capture_output=True, text=True, check=True)
                result = json.loads(rpc.stdout)['result']
                self.assertFalse(result['isError'])
                self.assertEqual(json.loads(result['content'][0]['text']), value)


if __name__ == '__main__':
    unittest.main()
