"""Change workflow on real disposable Git repositories; no agent binaries or network."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cg = load('cg_flow_tests', 'cg.py')
flow_module = load('change_flow_tests', 'change_flow.py')
ChangeFlow = flow_module.ChangeFlow
FlowError = flow_module.FlowError
test_runner = load('test_runner_flow_tests', 'change_tests.py')

PLAN = dict(summary='Improve value', touch=[dict(path='app.py', symbol='value', line=1, why='Implementation')],
            affected=[], tests=[dict(path='tests/test_app.py', name='TestApp.test_positive', why='Regression')],
            out_of_scope=['Other modules'], risks=[], unknowns=[], test_command=['python', '-m', 'unittest'])
REVIEW = dict(verdict='accept', summary='Accepted on the supplied evidence', findings=[])


class FakeRun:
    def __init__(self, client, mode, cwd, result, hold=False, status='done'):
        self.id = 'fake-' + str(time.monotonic_ns())
        self.client, self.mode, self.cwd = client, mode, cwd
        self.result = copy.deepcopy(result)
        self.status = 'running' if hold else status
        self.cancelled = False

    def events(self, since=0):
        events = [dict(v=1, run=self.id, seq=1, t='2026-11-06T00:00:00Z', kind='message', text='fake output', data={})]
        return [event for event in events if event['seq'] > since]

    def cancel(self):
        self.cancelled = True
        self.status = 'cancelled'

    def wait(self, timeout=None):
        return self.status


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.runs = []
        self.plan = copy.deepcopy(PLAN)
        self.review = copy.deepcopy(REVIEW)
        self.hold = None
        self.write = lambda root: (root / 'app.py').write_text('def value():\n    return 2\n')
        self.status = 'done'

    def detect(self):
        return [dict(client=name, available=True) for name in ('codex', 'claude')]

    def start(self, client, **kwargs):
        self.calls.append(dict(client=client, **kwargs))
        step = 'edit' if kwargs['mode'] == 'write' else ('plan' if 'touch' in kwargs['output_schema']['properties'] else 'review')
        if step == 'edit':
            self.write(Path(kwargs['cwd']))
        result = {'text': 'Changed app.py'} if step == 'edit' else getattr(self, step)
        run = FakeRun(client, kwargs['mode'], kwargs['cwd'], result, hold=self.hold == step, status=self.status)
        self.runs.append(run)
        return run


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cg-flow-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@localhost')
        (self.root / 'app.py').write_text('def value():\n    return 1\n')
        (self.root / '.gitignore').write_text('__pycache__/\n*.pyc\n')
        (self.root / 'tests').mkdir()
        (self.root / 'tests/test_app.py').write_text(
            'import unittest\nfrom app import value\nclass TestApp(unittest.TestCase):\n'
            '    def test_positive(self): self.assertGreater(value(), 0)\n')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.runner = FakeRunner()
        self.flow = ChangeFlow(cg, self.root, self.runner)
        self.argv = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests']

    def git(self, *args, root=None, check=True):
        result = subprocess.run(['git', '-C', str(root or self.root), *args], capture_output=True, text=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def make(self, author='codex', reviewer='claude'):
        return self.flow.create('Improve value', author, reviewer)['id']

    def step(self, name, cid, **kwargs):
        initial = getattr(self.flow, name)(cid, **kwargs)
        self.assertIn(initial['state'], flow_module.ACTIVE)
        result = self.flow.wait(cid, 10)
        self.assertNotIn(result['state'], flow_module.ACTIVE)
        self.assertIsNone(result['error'], result['error'])
        return result

    def through(self, target='reviewed', cid=None, argv=None):
        cid = cid or self.make()
        self.step('plan', cid)
        self.flow.approve(cid, self.argv if argv is None else argv, no_tests_reason='No suite' if argv == [] else None)
        if target == 'approved':
            return self.flow.get(cid)
        self.step('edit', cid)
        if target == 'edited':
            return self.flow.get(cid)
        self.step('verify', cid)
        if target == 'verified':
            return self.flow.get(cid)
        return self.step('review', cid)

    def error(self, code, callable, *args, **kwargs):
        with self.assertRaises(FlowError) as caught:
            callable(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def wait_runner(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.runner.runs and self.runner.runs[-1].status == 'running':
                return self.runner.runs[-1]
            time.sleep(.02)
        self.fail('Fake runner did not start')

    def test_complete_flow_preserves_checkout_and_keeps_branch(self):
        original = self.git('rev-parse', 'HEAD')
        reviewed = self.through()
        cid = reviewed['id']
        self.assertTrue(reviewed['verify']['ok'], reviewed['verify'])
        self.assertEqual(reviewed['tests']['baseline']['summary']['total'], 1)
        self.assertEqual(reviewed['tests']['after']['status'], 'passed')
        self.assertEqual(reviewed['edit']['files'], [dict(path='app.py', change='update')])
        self.assertEqual(reviewed['round'], 1)
        self.assertTrue((self.root / '.code-graph/changes' / cid / 'runs' / reviewed['edit']['run']).is_dir())
        self.assertTrue(reviewed['plan']['graph']['tests'])
        self.assertFalse(reviewed['plan']['checked']['unresolved'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), original)
        self.assertIn('return 1', (self.root / 'app.py').read_text())
        for call in self.runner.calls:
            self.assertEqual(call['cwd'], reviewed['worktree'])
            self.assertEqual(call['root'], reviewed['worktree'])
        result = self.flow.integrate(cid)
        self.assertEqual(result['state'], 'integrated')
        self.assertFalse(Path(result['worktree']).exists())
        self.assertEqual(self.git('show', result['branch'] + ':app.py'), 'def value():\n    return 2')
        self.assertIn('return 2', (self.root / '.code-graph/changes' / cid / 'diff.patch').read_text())
        self.assertEqual(self.flow.list()[0]['id'], cid)
        events = self.flow.events(cid)['events']
        self.assertEqual([e['seq'] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual([e['data']['state'] for e in events if e['kind'] == 'state'],
                         ['draft', 'planning', 'planned', 'approved', 'baseline', 'editing', 'edited',
                          'verifying', 'verified', 'reviewing', 'reviewed', 'integrated'])
        self.assertEqual(len([e for e in events if e['kind'] == 'agent']), 3)
        first = self.flow.events(cid, limit=2)
        second = self.flow.events(cid, since=first['next'], limit=2)
        self.assertEqual(second['events'][0]['seq'], 3)

    def test_python_tests_without_gitignore_do_not_block_merge(self):
        self.git('rm', '.gitignore')
        self.git('commit', '-m', 'No bytecode exclusions')
        with patch.dict(os.environ):
            os.environ.pop('PYTHONDONTWRITEBYTECODE', None)
            record = self.through(argv=[sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'])
        self.assertTrue(record['verify']['ok'], record['verify'])
        self.assertEqual(record['edit']['files'], [dict(path='app.py', change='update')])
        self.assertNotIn('.pyc', self.git('ls-files', root=record['worktree']))
        result = self.flow.integrate(record['id'], mode='merge')
        self.assertEqual(result['state'], 'integrated')
        self.assertIsNone(result['integration']['force_reason'])

    def test_generated_test_files_are_archived_not_committed(self):
        script = ('from pathlib import Path; '
                  'Path("test-output").mkdir(exist_ok=True); '
                  'Path("test-output/result.bin").write_bytes(b"test evidence\\x00")')
        record = self.through(argv=[sys.executable, '-c', script])
        self.assertTrue(record['verify']['ok'], record['verify'])
        for stage in ('baseline', 'after'):
            artifacts = record['tests'][stage]['artifacts']
            self.assertEqual(len(artifacts), 1)
            saved = self.flow._archive(record['id'], artifacts[0]['archive'])
            self.assertEqual(artifacts[0]['path'], 'test-output/result.bin')
            self.assertEqual(saved.read_bytes(), b'test evidence\x00')
        self.assertNotIn('test-output', self.git('ls-files', root=record['worktree']))
        self.assertEqual(self.flow.integrate(record['id'])['state'], 'integrated')

    def test_generated_test_symlinks_are_archived_without_following_targets(self):
        with tempfile.TemporaryDirectory(prefix='cg-flow-target-') as directory:
            outside = Path(directory).resolve()
            (outside / 'file.txt').write_text('external file')
            (outside / 'folder').mkdir()
            (outside / 'folder/keep.txt').write_text('external directory')
            links = {'test-output/file-link': str(outside / 'file.txt'),
                     'test-output/dir-link': str(outside / 'folder'),
                     'test-output/broken-link': str(outside / 'missing'),
                     'test-output/internal-link': '../app.py',
                     'test-output/loop-link': 'loop-link'}
            script = ('import json, os, sys; from pathlib import Path; '
                      'Path("test-output").mkdir(exist_ok=True); '
                      '[os.symlink(target, path) for path, target in json.loads(sys.argv[1]).items()]')
            record = self.through(argv=[sys.executable, '-c', script, json.dumps(links)])
            self.assertTrue(record['verify']['ok'], record['verify'])
            for stage in ('baseline', 'after'):
                artifacts = record['tests'][stage]['artifacts']
                self.assertEqual({entry['path'] for entry in artifacts}, set(links))
                for entry in artifacts:
                    self.assertEqual(entry['kind'], 'symlink')
                    saved = self.flow._archive(record['id'], entry['archive'])
                    self.assertFalse(saved.is_symlink())
                    self.assertEqual(json.loads(saved.read_text()), {'symlink': links[entry['path']]})
                    self.assertFalse((Path(record['worktree']) / entry['path']).is_symlink())
            self.assertEqual((outside / 'file.txt').read_text(), 'external file')
            self.assertEqual((outside / 'folder/keep.txt').read_text(), 'external directory')
            self.assertFalse((outside / 'missing').exists())
            self.assertNotIn('test-output', self.git('ls-files', root=record['worktree']))
            self.assertEqual(self.flow.integrate(record['id'], mode='merge')['state'], 'integrated')

    def test_test_cleanup_keeps_preexisting_symlinks_visible(self):
        record = self.through('edited', argv=[sys.executable, '-c', 'pass'])
        link = Path(record['worktree']) / 'user-link'
        link.symlink_to(self.root / 'app.py')
        result = self.step('verify', record['id'])
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(self.root / 'app.py'))
        self.assertEqual(result['verify']['perimeter']['outside'], ['user-link'])
        self.assertEqual(result['tests']['after']['artifacts'], [])

    def test_test_cleanup_preserves_existing_and_authored_files(self):
        self.git('rm', '.gitignore')
        self.git('commit', '-m', 'No artifact exclusions')
        def write(root):
            (root / 'app.py').write_text('def value(): return 2\n')
            (root / 'authored.pyc').write_bytes(b'agent-created')
        self.runner.write = write
        record = self.through('edited', argv=[sys.executable, '-c',
                              'from pathlib import Path; Path("generated.txt").write_text("test")'])
        root = Path(record['worktree'])
        (root / 'user-note.txt').write_text('keep me')
        result = self.step('verify', record['id'])
        self.assertEqual((root / 'user-note.txt').read_text(), 'keep me')
        self.assertEqual((root / 'authored.pyc').read_bytes(), b'agent-created')
        self.assertEqual(result['verify']['perimeter']['outside'], ['authored.pyc', 'user-note.txt'])
        self.assertFalse((root / 'generated.txt').exists())

    def test_test_cleanup_does_not_hide_changes_to_tracked_files(self):
        script = ('from pathlib import Path; '
                  'p=Path("app.py"); p.write_text(p.read_text()+"# test side effect\\n")')
        record = self.through('verified', argv=[sys.executable, '-c', script])
        self.assertIn('Worktree changed after the edit commit', record['verify']['blocking'])
        self.assertEqual(record['tests']['after']['artifacts'], [])
        self.assertIn('# test side effect', (Path(record['worktree']) / 'app.py').read_text())

    def test_cancelled_tests_preserve_generated_evidence_in_archive(self):
        script = ('from pathlib import Path; import time; '
                  'Path("cancel-evidence.txt").write_text("before cancellation"); time.sleep(30)')
        record = self.through('approved', argv=[sys.executable, '-c', script])
        cid = record['id']
        self.flow.edit(cid)
        path = Path(record['worktree']) / 'cancel-evidence.txt'
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(path.exists())
        self.flow.cancel(cid)
        self.assertEqual(self.flow.wait(cid, 5)['state'], 'approved')
        self.assertFalse(path.exists())
        saved = list(self.flow._archive(cid, 'test-artifacts').rglob('cancel-evidence.txt'))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_text(), 'before cancellation')

    def test_budget_is_persisted_and_passed_only_to_claude(self):
        for author, reviewer in [('claude', 'codex'), ('codex', 'claude')]:
            with self.subTest(author=author):
                self.runner.calls.clear()
                record = self.flow.create('Budgeted change', author, reviewer, budget_usd=.75)
                self.assertEqual(ChangeFlow(cg, self.root).get(record['id'])['budget_usd'], .75)
                self.through(cid=record['id'])
                for call in self.runner.calls:
                    if call['client'] == 'claude':
                        self.assertEqual(call['budget_usd'], .75)
                    else:
                        self.assertNotIn('budget_usd', call)
                self.flow.discard(record['id'])

    def test_budget_rejects_invalid_values_before_creating_change(self):
        for value in (0, -1, True, False, '1', [], float('nan'), float('inf'), -float('inf'), 10**400):
            with self.subTest(value=value):
                self.error('invalid', self.flow.create, 'Budget', 'codex', 'claude', budget_usd=value)
        self.assertEqual(self.flow.list(), [])
        record = self.flow.create('Default budget', 'codex', 'claude')
        self.assertIsNone(record['budget_usd'])
        self.flow.discard(record['id'])

    def test_old_change_without_budget_still_runs(self):
        record = self.flow.create('Legacy change', 'codex', 'claude')
        path = self.flow._archive(record['id'], 'change.json')
        saved = json.loads(path.read_text())
        saved.pop('budget_usd', None)
        path.write_text(json.dumps(saved))
        self.through(cid=record['id'])
        self.assertTrue(all(call.get('budget_usd') is None for call in self.runner.calls))

    def test_merge_and_advanced_base(self):
        record = self.through()
        (self.root / 'unrelated.txt').write_text('New base commit\n')
        self.git('add', '.')
        self.git('commit', '-m', 'advance base')
        result = self.flow.integrate(record['id'], mode='merge')
        self.assertEqual(result['state'], 'integrated')
        self.assertIn('return 2', (self.root / 'app.py').read_text())
        self.assertTrue((self.root / 'unrelated.txt').exists())
        self.assertEqual(len(self.git('rev-list', '--parents', '-n', '1', 'HEAD').split()), 3)

    def test_merge_rejects_dirty_changed_branch_and_missing_base(self):
        record = self.through()
        cid = record['id']
        (self.root / 'dirty.txt').write_text('unsaved')
        self.error('dirty', self.flow.integrate, cid, mode='merge', force_reason='Override')
        (self.root / 'dirty.txt').unlink()
        self.git('checkout', '-b', 'other')
        self.error('invalid', self.flow.integrate, cid, mode='merge')
        self.git('checkout', 'main')
        self.git('checkout', '--orphan', 'unrelated')
        self.git('commit', '-m', 'unrelated root')
        self.git('branch', '-D', 'main')
        self.git('branch', '-m', 'main')
        self.error('invalid', self.flow.integrate, cid, mode='merge')
        self.assertEqual(self.flow.get(cid)['state'], 'reviewed')

    def test_conflict_aborts_and_retains_reviewed(self):
        record = self.through()
        (self.root / 'app.py').write_text('def value():\n    return 99\n')
        self.git('add', '.')
        self.git('commit', '-m', 'conflicting change')
        head = self.git('rev-parse', 'HEAD')
        self.error('conflict', self.flow.integrate, record['id'], mode='merge')
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertFalse((self.root / '.git/MERGE_HEAD').exists())
        result = self.flow.get(record['id'])
        self.assertEqual(result['state'], 'reviewed')
        self.assertEqual(result['error']['code'], 'conflict')

    def test_new_test_failure_blocks_integration(self):
        self.runner.write = lambda root: (root / 'app.py').write_text('def value():\n    return -1\n')
        record = self.through()
        self.assertEqual(record['tests']['baseline']['status'], 'passed')
        self.assertEqual(record['tests']['after']['status'], 'failed')
        self.assertTrue(record['verify']['tests']['newly_failing'])
        self.error('bad_state', self.flow.integrate, record['id'])
        result = self.flow.integrate(record['id'], force_reason='Keep branch for investigation')
        self.assertEqual(result['history'][-1]['note'], 'Keep branch for investigation')

    def test_perimeter_includes_untracked_files_and_planned_untouched(self):
        def write(root):
            (root / 'app.py').write_text('def value(): return 2\n')
            (root / 'extra file.py').write_text('def surprise(): pass\n')
        self.runner.write = write
        record = self.through('verified')
        self.assertEqual(record['verify']['perimeter']['outside'], ['extra file.py'])
        self.assertEqual(record['verify']['perimeter']['planned_untouched'], ['tests/test_app.py'])
        self.assertFalse(record['verify']['ok'])
        self.assertIn('extra file.py', self.git('show', '--pretty=', '--name-only', 'HEAD', root=record['worktree']))

    def test_rounds_and_stale_review_are_invalidated(self):
        record = self.through()
        cid = record['id']
        for round_number in (2, 3):
            edited = self.step('edit', cid, feedback='Refine the change')
            self.assertEqual(edited['round'], round_number)
            self.assertIsNone(edited['verify'])
            self.assertIsNone(edited['review'])
            self.error('bad_state', self.flow.integrate, cid, force_reason='Too early')
            self.step('verify', cid)
        self.error('bad_state', self.flow.edit, cid)

    def test_same_client_requires_reason_and_reject_requires_override(self):
        cid = self.make(reviewer='codex')
        self.runner.review['verdict'] = 'reject'
        self.through('verified', cid)
        self.error('invalid', self.flow.review, cid)
        record = self.step('review', cid, same_client_reason='Other client unavailable')
        self.assertEqual(record['review']['same_client_reason'], 'Other client unavailable')
        self.error('bad_state', self.flow.integrate, cid)

    def test_invalid_transitions_and_inputs(self):
        cid = self.make()
        for method in ('edit', 'verify', 'review', 'integrate', 'cancel'):
            self.error('bad_state', getattr(self.flow, method), cid)
        self.error('bad_state', self.flow.approve, cid, self.argv)
        self.error('invalid', self.flow.get, '../../outside')
        self.error('not_found', self.flow.get, 'c-20260101-010101-aaaa')
        self.step('plan', cid)
        self.error('invalid', self.flow.approve, cid, [])
        self.error('invalid', self.flow.approve, cid, 'python -m unittest')
        self.error('invalid', self.flow.approve, cid, self.argv, touch=['../other.py'])
        result = self.flow.approve(cid, [], no_tests_reason='No tests available', touch=['app.py'])
        self.assertEqual(result['approved']['touch'][0]['path'], 'app.py')

    def test_non_git_dirty_and_explicit_dirty_override(self):
        folder = self.root / 'nongit'
        folder.mkdir()
        self.error('not_git', ChangeFlow(cg, folder, self.runner).create, 'x', 'codex', 'claude')
        (self.root / 'uncommitted.txt').write_text('WIP')
        self.error('dirty', self.flow.create, 'x', 'codex', 'claude')
        record = self.flow.create('x', 'codex', 'claude', allow_dirty=True)
        self.assertFalse((Path(record['worktree']) / 'uncommitted.txt').exists())
        self.assertTrue(any(e['kind'] == 'warning' for e in self.flow.events(record['id'])['events']))

    def test_busy_across_instances_and_project_edit_reservation(self):
        one = self.through('approved')
        two = self.through('approved')
        self.runner.hold = 'edit'
        self.flow.edit(one['id'])
        self.wait_runner()
        other = ChangeFlow(cg, self.root, self.runner)
        self.error('busy', other.edit, one['id'])
        self.error('busy', other.edit, two['id'])
        other.cancel(one['id'])
        self.assertEqual(other.wait(one['id'], 5)['state'], 'approved')
        self.assertTrue(self.runner.runs[-1].cancelled)
        self.runner.hold = None
        self.step('edit', two['id'])

    def test_cancel_plan_and_discard_active(self):
        cid = self.make()
        self.runner.hold = 'plan'
        self.flow.plan(cid)
        run = self.wait_runner()
        self.flow.cancel(cid)
        result = self.flow.wait(cid, 5)
        self.assertEqual(result['state'], 'draft')
        self.assertIsNone(result['error'])
        self.assertTrue(run.cancelled)
        self.flow.plan(cid)
        self.wait_runner()
        self.assertEqual(self.flow.discard(cid)['state'], 'discarded')

    def killed_worker_case(self, stage):
        child = '''
import runpy, sys, time
module = runpy.run_path(sys.argv[1])
runner = module['FakeRunner']()
flow = module['ChangeFlow'](module['cg'], sys.argv[2], runner)
cid, stage = sys.argv[3:]
runner.hold = 'plan' if stage == 'planning' else 'edit'
if stage == 'baseline':
    def blocked_test(record):
        print('ready', flush=True)
        time.sleep(60)
    flow._test = blocked_test
getattr(flow, 'plan' if stage == 'planning' else 'edit')(cid)
if stage != 'baseline':
    while not runner.runs:
        time.sleep(.01)
    print('ready', flush=True)
time.sleep(60)
'''
        cid = self.make() if stage == 'planning' else self.through('approved')['id']
        previous = self.flow.get(cid)['state']
        another = self.through('approved')['id']
        process = subprocess.Popen([sys.executable, '-I', '-B', '-c', child,
                                    str(Path(__file__).resolve()), str(self.root), cid, stage],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                self.assertTrue(ready.select(10), 'Child did not enter the active step')
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            # A live worker in another process must retain its reservation.
            self.assertEqual(self.flow.get(cid)['state'], stage)
            self.error('busy', self.flow.plan, cid)
            if stage != 'planning':
                self.error('busy', self.flow.edit, another)
            process.kill()
            process.wait(timeout=5)
            record = self.flow.cancel(cid) if stage == 'baseline' else self.flow.wait(cid, .1)
            self.assertEqual(record['state'], previous)
            self.assertIn('step interrupted: the process ended', record['history'][-1]['note'])
            self.assertEqual(self.flow.wait(cid, .1)['state'], previous)
            if stage == 'planning':
                self.step('plan', cid)
            else:
                self.step('edit', another)
            self.assertEqual(self.flow.discard(cid)['state'], 'discarded')
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_killed_worker_planning_recovers_and_retries(self):
        self.killed_worker_case('planning')

    def test_killed_worker_baseline_cancel_releases_project(self):
        self.killed_worker_case('baseline')

    def test_killed_worker_editing_wait_releases_project(self):
        self.killed_worker_case('editing')

    def test_reused_pid_and_missing_worker_are_recovered_by_actions(self):
        for action in ('plan', 'cancel', 'discard'):
            with self.subTest(action=action):
                cid = self.make()
                with self.flow._locked():
                    record = self.flow._load(cid)
                    # The PID exists, but no worker owns this old process nonce.
                    record['step'] = dict(previous='draft', owner=dict(pid=os.getpid(), nonce='old-process'))
                    self.flow._state(record, 'planning')
                result = getattr(self.flow, action)(cid)
                if action == 'plan':
                    result = self.flow.wait(cid, 5)
                self.assertEqual(result['state'], {'plan': 'planned', 'cancel': 'draft', 'discard': 'discarded'}[action])
                self.assertTrue(any('step interrupted' in entry['note'] for entry in result['history']))

    def test_reimport_does_not_recover_a_live_worker_in_same_process(self):
        cid = self.make()
        self.runner.hold = 'plan'
        self.flow.plan(cid)
        run = self.wait_runner()
        fresh = load('flow_reimport', 'change_flow.py').ChangeFlow(cg, self.root, self.runner)
        self.assertEqual(fresh.get(cid)['state'], 'planning')
        fresh.cancel(cid)
        self.assertEqual(fresh.wait(cid, 5)['state'], 'draft')
        self.assertTrue(run.cancelled)

    def test_discard_only_own_worktree_branch_and_preserves_archive(self):
        first = self.make()
        second = self.make()
        record = self.flow.get(first)
        other = self.flow.get(second)
        (Path(record['worktree']) / 'scratch.txt').write_text('uncommitted')
        self.flow.discard(first)
        self.assertFalse(Path(record['worktree']).exists())
        self.assertTrue(Path(other['worktree']).exists())
        self.assertTrue((self.root / '.code-graph/changes' / first / 'change.json').exists())
        self.assertIn(other['branch'], self.git('branch', '--list'))
        self.assertNotIn(record['branch'], self.git('branch', '--list'))

    def test_tampered_worktree_and_symlink_archive_refused(self):
        cid = self.make()
        path = self.root / '.code-graph/changes' / cid / 'change.json'
        record = json.loads(path.read_text())
        record['worktree'] = str(self.root)
        path.write_text(json.dumps(record))
        self.error('invalid', self.flow.discard, cid)
        self.assertTrue((self.root / 'app.py').exists())
        path.unlink()
        path.symlink_to(self.root / 'app.py')
        self.error('invalid', self.flow.get, cid)

    def test_agent_failure_and_bad_schema_restore_stable_state(self):
        cid = self.make()
        self.runner.plan['unexpected'] = True
        self.flow.plan(cid)
        record = self.flow.wait(cid, 5)
        self.assertEqual(record['state'], 'draft')
        self.assertEqual(record['error']['code'], 'invalid')
        self.runner.plan = copy.deepcopy(PLAN)
        self.runner.status = 'failed'
        self.flow.plan(cid)
        self.assertEqual(self.flow.wait(cid, 5)['state'], 'draft')

    def test_graph_unresolved_is_explicit_and_replan_allowed(self):
        cid = self.make()
        self.runner.plan['affected'] = [dict(path='app.py', symbol='missing', line=1, why='Uncertain', confidence='low')]
        first = self.step('plan', cid)
        self.assertEqual(first['plan']['checked']['unresolved'][0]['symbol'], 'missing')
        self.step('plan', cid)

    def test_invalid_plan_paths_remain_explicitly_unresolved(self):
        cid = self.make()
        invalid = ('/absolute.py', '../outside.py', '')
        for role in ('touch', 'affected', 'tests'):
            for path in invalid:
                item = dict(path=path, why='Uncertain')
                item.update(name='test_missing') if role == 'tests' else item.update(symbol='missing', line=1)
                if role == 'affected':
                    item['confidence'] = 'low'
                self.runner.plan[role].append(item)
        result = self.step('plan', cid)
        unresolved = {(item['role'], item['path']) for item in result['plan']['checked']['unresolved']}
        self.assertTrue({(role, path) for role in ('touch', 'affected', 'tests') for path in invalid} <= unresolved)

    def test_invalid_plan_paths_do_not_break_default_approval_or_verify(self):
        for role in ('touch', 'affected', 'tests'):
            template = self.runner.plan[role][0] if self.runner.plan[role] else dict(
                path='app.py', symbol='value', line=1, why='Uncertain', confidence='low')
            for path in ('/absolute.py', '../outside.py', ''):
                self.runner.plan[role].append(dict(template, path=path))
        record = self.through()
        self.assertTrue(record['verify']['ok'], record['verify'])
        self.assertEqual([item['path'] for item in record['approved']['touch']], ['app.py'])
        unresolved = {(item['role'], item['path']) for item in record['plan']['checked']['unresolved']}
        self.assertTrue({(role, path) for role in ('touch', 'affected', 'tests')
                         for path in ('/absolute.py', '../outside.py', '')} <= unresolved)
        self.assertEqual(self.flow.integrate(record['id'])['state'], 'integrated')

    def test_invalid_plan_test_paths_do_not_break_verify_with_explicit_touch(self):
        self.runner.plan['tests'].append(dict(path='../outside.py', name='test_missing', why='Uncertain'))
        cid = self.make()
        self.step('plan', cid)
        self.flow.approve(cid, self.argv, touch=['app.py'])
        self.step('edit', cid)
        record = self.step('verify', cid)
        self.assertTrue(record['verify']['ok'], record['verify'])
        self.assertIn('../outside.py', [item['path'] for item in record['plan']['checked']['unresolved']])

    def test_static_review_json_is_not_cut_by_test_output_tail(self):
        record = self.flow.get(self.make())
        roots = list(range(1, 1000))
        payload = flow_module._ANALYSIS['bounded'](dict(verification=dict(
            roots=roots, baseline_plans=[dict(plan=dict(roots=roots)) for _ in range(4)])), 20000)
        printed = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False, separators=(',', ':'))), 20000)
        self.assertGreater(len(printed), 24000)
        # Match cmd_analysis's JSON printer in a child process, through the actual
        # analysis subprocess transport (including run_tests' bounded tail).
        script = self.flow._archive(record['id'], 'analysis_fixture.py')
        script.write_text('import json\nprint(json.dumps(' + repr(payload) + ', ensure_ascii=False, sort_keys=True))\n')
        with patch.object(cg, '__file__', str(script)):
            actual = self.flow._analysis_command(record, 'review', '--max-chars', '20000', '--json')
        self.assertEqual(json.loads(actual), payload)

    def test_no_tests_and_test_error_timeout(self):
        record = self.through('reviewed', argv=[])
        self.assertTrue(record['tests']['baseline']['skipped'])
        self.assertTrue(record['verify']['ok'])
        record = self.through('edited', argv=['/nonexistent-test-runner'])
        result = self.step('verify', record['id'])
        self.assertIn('Test execution error', result['verify']['blocking'])
        with patch.object(flow_module, 'TEST_TIMEOUT', .1):
            record = self.through('edited', argv=[sys.executable, '-c', 'import time; time.sleep(10)'])
            result = self.step('verify', record['id'])
        self.assertIn('Test execution timeout', result['verify']['blocking'])

    def test_cancel_baseline_terminates_test_process(self):
        record = self.through('approved', argv=[sys.executable, '-c', 'import time; time.sleep(30)'])
        self.flow.edit(record['id'])
        time.sleep(.1)
        self.flow.cancel(record['id'])
        result = self.flow.wait(record['id'], 5)
        self.assertEqual(result['state'], 'approved')
        self.assertIsNone(result['tests']['baseline'])

    def test_modified_candidate_cannot_integrate_even_with_force(self):
        record = self.through()
        (Path(record['worktree']) / 'app.py').write_text('unreviewed content')
        self.error('dirty', self.flow.integrate, record['id'], force_reason='Override')

    def test_baseline_rejects_preexisting_worktree_edits(self):
        record = self.through('approved')
        (Path(record['worktree']) / 'app.py').write_text('def value(): return 9\n')
        self.error('dirty', self.flow.edit, record['id'])
        self.assertIsNone(self.flow.get(record['id'])['tests']['baseline'])

    def test_event_cursor_handles_large_test_results(self):
        cid = self.make()
        with self.flow._locked():
            record = self.flow._load(cid)
            self.flow._event(record, 'test', 'Large report', dict(failing=['x' * 110000]))
            self.flow._event(record, 'step', 'Next event')
        self.assertEqual(self.flow.events(cid)['next'], 3)

    def test_baseline_known_failure_is_fixed(self):
        (self.root / 'app.py').write_text('def value(): return -1\n')
        self.git('add', '.')
        self.git('commit', '-m', 'Known baseline failure')
        record = self.through('verified')
        self.assertEqual(record['tests']['baseline']['status'], 'failed')
        self.assertEqual(record['tests']['after']['status'], 'passed')
        self.assertTrue(record['verify']['tests']['fixed'])
        self.assertTrue(record['verify']['ok'])

    def test_simultaneous_edit_starts_allow_one_writer(self):
        records = [self.through('approved'), self.through('approved')]
        self.runner.hold = 'edit'
        barrier = threading.Barrier(2)
        results = []
        def start(record):
            barrier.wait()
            try:
                results.append(self.flow.edit(record['id']))
            except FlowError as error:
                results.append(error.code)
        threads = [threading.Thread(target=start, args=(record,)) for record in records]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(results.count('busy'), 1)
        active = next(result for result in results if isinstance(result, dict))
        self.flow.cancel(active['id'])
        self.assertEqual(self.flow.wait(active['id'], 5)['state'], 'approved')

    def test_cli_waits_for_async_steps_with_injected_runner(self):
        cid = self.make()
        with patch.object(flow_module, 'ChangeFlow', return_value=self.flow), contextlib.redirect_stdout(io.StringIO()) as out:
            result = flow_module.cli(cg, self.root, ['plan', cid, '--yes'])
        self.assertEqual(result['state'], 'planned')
        self.assertEqual(json.loads(out.getvalue())['state'], 'planned')

    def test_cli_requires_yes_without_terminal_before_any_agent_start(self):
        cid = self.make()
        for action in ('plan', 'edit', 'review'):
            with self.subTest(action=action), patch.object(flow_module, 'ChangeFlow', return_value=self.flow), \
                    patch.object(sys, 'stdin', io.StringIO('yes\n')), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit) as caught:
                    flow_module.cli(cg, self.root, [action, cid])
                self.assertEqual(caught.exception.code, 2)
                self.assertIn('--yes', err.getvalue())
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.flow.get(cid)['state'], 'draft')

    def test_cli_terminal_requires_affirmative_answer(self):
        cid = self.make()
        for answer in ('\n', 'no\n', ''):
            terminal = io.StringIO(answer)
            with self.subTest(answer=answer), patch.object(flow_module, 'ChangeFlow', return_value=self.flow), \
                    patch.object(sys, 'stdin', terminal), patch.object(terminal, 'isatty', return_value=True), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    flow_module.cli(cg, self.root, ['plan', cid])
                self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.runner.calls, [])
        terminal = io.StringIO('yes\n')
        with patch.object(flow_module, 'ChangeFlow', return_value=self.flow), patch.object(sys, 'stdin', terminal), \
                patch.object(terminal, 'isatty', return_value=True), contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            result = flow_module.cli(cg, self.root, ['plan', cid])
        self.assertEqual(result['state'], 'planned')
        self.assertEqual(json.loads(out.getvalue())['state'], 'planned')
        self.assertIn('codex', err.getvalue())
        self.assertIn('provider', err.getvalue())
        self.assertIn(str(self.root), err.getvalue())

    def test_cli_yes_allows_edit_and_review_and_budget_new(self):
        with patch.object(flow_module, 'ChangeFlow', return_value=self.flow), contextlib.redirect_stdout(io.StringIO()):
            record = flow_module.cli(cg, self.root, ['new', '--intent', 'CLI budget', '--author', 'codex',
                                                   '--reviewer', 'claude', '--budget-usd', '1.25'])
            self.assertEqual(record['budget_usd'], 1.25)
            cid = record['id']
            flow_module.cli(cg, self.root, ['plan', cid, '--yes'])
            flow_module.cli(cg, self.root, ['approve', cid, '--test-argv', json.dumps(self.argv)])
            flow_module.cli(cg, self.root, ['edit', cid, '--yes'])
            flow_module.cli(cg, self.root, ['verify', cid])
            result = flow_module.cli(cg, self.root, ['review', cid, '--yes'])
        self.assertEqual(result['state'], 'reviewed')
        self.assertEqual(self.runner.calls[-1]['budget_usd'], 1.25)

    def test_cli_cg_new_show_list_discard(self):
        def cli(*args, expected=0):
            result = subprocess.run([sys.executable, '-I', str(REPO / 'scripts/cg.py'), '--root', str(self.root), 'change', *args],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, expected, result.stderr + result.stdout)
            return json.loads(result.stdout)
        record = cli('new', '--intent', 'CLI test', '--author', 'codex', '--reviewer', 'claude')
        cid = record['id']
        self.assertEqual(cli('show', cid)['id'], cid)
        self.assertEqual(cli('list')[0]['state'], 'draft')
        self.assertEqual(cli('verify', cid, expected=1)['code'], 'bad_state')
        self.assertEqual(cli('discard', cid)['state'], 'discarded')


class TestExecutionTests(unittest.TestCase):
    def test_framework_parsers(self):
        samples = [
            ('unittest', 'FAIL: test_bad (test_app.TestApp.test_bad)\nRan 4 tests in 0.001s\nFAILED (failures=1, errors=0, skipped=1)', 4, 1),
            ('pytest', 'FAILED tests/test_app.py::test_bad - AssertionError\n======= 1 failed, 2 passed, 1 skipped in 0.12s =======', 4, 1),
            ('phpunit', 'PHPUnit 11.0\n1) AppTest::testBad\nFAILURES!\nTests: 4, Assertions: 6, Failures: 1, Skipped: 1.', 4, 1),
            ('jest', 'FAIL tests/app.test.js\n  ● bad value\nTests: 1 failed, 1 skipped, 2 passed, 4 total\n', 4, 1),
        ]
        for framework, text, total, failed in samples:
            with self.subTest(framework=framework):
                summary, names = test_runner.parse_output(text)
                self.assertEqual(summary['framework'], framework)
                self.assertEqual(summary['total'], total)
                self.assertEqual(summary['failed'], failed)
                self.assertEqual(summary['skipped'], 1)
                self.assertTrue(names)

    def test_execution_is_argv_not_shell_and_output_bounded(self):
        with tempfile.TemporaryDirectory() as cwd:
            argument = '$(touch SHOULD_NOT_EXIST); echo dangerous'
            result = test_runner.run_tests([sys.executable, '-c', 'import sys; print(sys.argv[1]); print("a"*20000)', argument], cwd)
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(len(result['stdout_tail']), 8000)
            self.assertFalse((Path(cwd) / 'SHOULD_NOT_EXIST').exists())

    def test_comparison_known_failures_fixed_and_unidentified(self):
        before = dict(status='failed', failing=['known', 'fixed'], summary=dict(failed=2, errors=0))
        after = dict(status='failed', failing=['known', 'new'], summary=dict(failed=2, errors=0))
        result = test_runner.compare_tests(before, after)
        self.assertEqual(result['newly_failing'], ['new'])
        self.assertEqual(result['fixed'], ['fixed'])
        self.assertEqual(result['still_failing'], ['known'])
        after['failing'] = []
        self.assertTrue(test_runner.compare_tests(before, after)['newly_failing'])

    def test_jest_identifies_same_named_tests_in_different_files(self):
        _, first = test_runner.parse_output('FAIL a.test.js\n ● same name\nTests: 1 failed, 1 total')
        _, second = test_runner.parse_output('FAIL b.test.js\n ● same name\nTests: 1 failed, 1 total')
        self.assertNotEqual(first, second)


if __name__ == '__main__':
    unittest.main()
