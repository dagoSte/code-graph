"""Real flow, CLI adapter, MCP, Git, tests and HTTP; only model binaries are fake."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cg = load('cg_integration', 'cg.py')
agents = load('agents_integration', 'agents.py')
flow_module = load('flow_integration', 'change_flow.py')
workbench = load('workbench_integration', 'workbench.py')


class FixtureRunner:
    """Inject only executable; parsing, subprocesses, timeout and results stay real."""
    def __init__(self, executable):
        self.executable = str(executable)
        self.runs = []
        self.calls = []

    def detect(self):
        return [dict(client=client, available=True, path=self.executable,
                     version='e2e-fixture-1', reason=None) for client in agents.CLIENTS]

    def start(self, client, **kwargs):
        self.calls.append(dict(client=client, **kwargs))
        run = agents.start(client, executable=self.executable, **kwargs)
        self.runs.append(run)
        return run

    def close(self):
        for run in self.runs:
            run.cancel()
        for run in self.runs:
            run.wait(5)


class IntegrationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cg-integration-')
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.root = self.workspace / 'project with spaces'
        self.root.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Integration fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.root / '.gitignore').write_text('__pycache__/\n*.pyc\n')
        (self.root / 'app.py').write_text('def value():\n    return 1\n')
        (self.root / 'tests').mkdir()
        (self.root / 'tests/test_app.py').write_text(
            'import unittest\nfrom app import value\nclass TestApp(unittest.TestCase):\n'
            '    def test_positive(self):\n'
            '        print("observed value=" + str(value()))\n'
            '        self.assertGreater(value(), 0)\n')
        self.git('add', '.')
        self.git('commit', '-m', 'Fixture baseline')
        self.original_head = self.git('rev-parse', 'HEAD')
        executable = self.workspace / 'fake model client'
        source = (REPO / 'tests/fixtures/e2e_client.py').read_text()
        executable.write_text('#!' + sys.executable + '\n' + source.split('\n', 1)[1])
        executable.chmod(0o700)
        self.runner = FixtureRunner(executable)
        self.addCleanup(self.runner.close)
        self.flow = flow_module.ChangeFlow(cg, self.root, runner=self.runner)
        self.test_argv = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests']

    def git(self, *args):
        result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                                 '-c', 'commit.gpgSign=false', '-C', str(self.root), *args],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def step(self, name, cid):
        initial = getattr(self.flow, name)(cid)
        self.assertIn(initial['state'], flow_module.ACTIVE)
        final = self.flow.wait(cid, 20)
        self.assertNotIn(final['state'], flow_module.ACTIVE, final)
        self.assertIsNone(final['error'], self.diagnostics(final))
        expected = dict(plan='planned', edit='edited', verify='verified', review='reviewed')
        self.assertEqual(final['state'], expected[name])
        return final

    def diagnostics(self, record):
        return dict(error=record.get('error'), state=record.get('state'),
                    runs=[dict(status=run.status, events=run.events()) for run in self.runner.runs])

    def through_review(self, intent):
        cid = self.flow.create(intent, 'codex', 'claude')['id']
        planned = self.step('plan', cid)
        self.assertEqual(planned['plan']['checked']['unresolved'], [])
        self.assertTrue(planned['plan']['graph']['tests'])
        approved = self.flow.approve(cid, self.test_argv, touch=['app.py'])
        self.assertEqual(approved['state'], 'approved')
        self.step('edit', cid)
        self.step('verify', cid)
        return self.step('review', cid)

    def assert_agent_evidence(self, record, events):
        self.assertEqual(len(self.runner.runs), 3)
        self.assertEqual([call['client'] for call in self.runner.calls], ['codex', 'codex', 'claude'])
        self.assertEqual([call['mode'] for call in self.runner.calls], ['read', 'write', 'read'])
        for run, call in zip(self.runner.runs, self.runner.calls):
            self.assertIsInstance(run, agents.AgentRun)
            self.assertEqual(run.status, 'done', run.events())
            self.assertEqual(call['cwd'], record['worktree'])
            self.assertEqual(call['root'], record['worktree'])
            self.assertEqual(run.usage, dict(input_tokens=120, output_tokens=24,
                                            cached_input_tokens=30, cache_write_input_tokens=None, measured=True))
            folder = self.root / '.code-graph/changes' / record['id'] / 'runs' / run.id
            self.assertEqual(json.loads((folder / 'run.json').read_text())['status'], 'done')
            self.assertTrue((folder / 'raw.jsonl').stat().st_size)
            self.assertEqual([json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()], run.events())
            self.assertNotIn(call['prompt'], json.loads((folder / 'run.json').read_text())['argv'])
        agent_events = [event['data'] for event in events if event['kind'] == 'agent']
        self.assertEqual({event['run'] for event in agent_events}, {run.id for run in self.runner.runs})
        self.assertEqual(len([event for event in agent_events if event['kind'] == 'usage']), 3)
        self.assertTrue(all(event['data']['measured'] for event in agent_events if event['kind'] == 'usage'))
        self.assertEqual({event['run'] for event in agent_events
                          if event['kind'] == 'tool' and event['data']['name'].endswith('cg_read')
                          and event['data']['status'] == 'ok'}, {run.id for run in self.runner.runs})
        self.assertEqual({event['run'] for event in agent_events
                          if event['kind'] == 'tool' and event['data']['name'].endswith('cg_search')
                          and event['data']['status'] == 'ok'}, {run.id for run in self.runner.runs})

    def assert_successful_merge(self, record):
        self.assertEqual(record['state'], 'integrated')
        self.assertEqual(record['integration']['mode'], 'merge')
        self.assertEqual((self.root / 'app.py').read_text(), 'def value():\n    return 2\n')
        self.assertFalse(Path(record['worktree']).exists())
        self.assertNotEqual(self.git('rev-parse', 'HEAD'), self.original_head)
        self.assertEqual(len(self.git('rev-list', '--parents', '-n', '1', 'HEAD').split()), 3)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_real_flow_agents_mcp_tests_and_merge(self):
        reviewed = self.through_review('Increase the positive value')
        self.assertTrue(reviewed['verify']['ok'], reviewed['verify'])
        self.assertEqual(reviewed['verify']['blocking'], [])
        self.assertEqual(reviewed['verify']['tests']['newly_failing'], [])
        self.assertEqual(reviewed['tests']['baseline']['status'], 'passed')
        self.assertEqual(reviewed['tests']['after']['status'], 'passed')
        self.assertEqual(reviewed['tests']['baseline']['summary']['total'], 1)
        self.assertEqual(reviewed['tests']['after']['summary']['total'], 1)
        self.assertIn('observed value=1', reviewed['tests']['baseline']['stdout_tail'])
        self.assertIn('observed value=2', reviewed['tests']['after']['stdout_tail'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.original_head)
        self.assertIn('return 1', (self.root / 'app.py').read_text())
        integrated = self.flow.integrate(reviewed['id'], mode='merge')
        self.assert_successful_merge(integrated)
        events = self.flow.events(integrated['id'])['events']
        self.assert_agent_evidence(integrated, events)
        self.assertEqual([event['seq'] for event in events], list(range(1, len(events) + 1)))
        self.assertEqual([event['data']['state'] for event in events if event['kind'] == 'state'],
                         ['draft', 'planning', 'planned', 'approved', 'baseline', 'editing', 'edited',
                          'verifying', 'verified', 'reviewing', 'reviewed', 'integrated'])

    def assert_blocked(self, intent, reason):
        record = self.through_review(intent)
        self.assertFalse(record['verify']['ok'])
        self.assertIn(reason, record['verify']['blocking'])
        self.assertEqual(record['review']['verdict'], 'accept')
        with self.assertRaises(flow_module.FlowError) as caught:
            self.flow.integrate(record['id'], mode='merge')
        self.assertEqual(caught.exception.code, 'bad_state')
        self.assertEqual(self.flow.get(record['id'])['state'], 'reviewed')
        self.assertTrue(Path(record['worktree']).is_dir())
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.original_head)
        self.assertIn('return 1', (self.root / 'app.py').read_text())
        return record

    def test_real_regression_blocks_merge_even_when_reviewer_accepts(self):
        record = self.assert_blocked('E2E_BREAK_TEST: deliberately return a negative value', 'New failing tests')
        self.assertEqual(record['tests']['baseline']['status'], 'passed')
        self.assertEqual(record['tests']['after']['status'], 'failed')
        self.assertTrue(record['verify']['tests']['newly_failing'])

    def test_outside_perimeter_blocks_merge_with_green_tests(self):
        record = self.assert_blocked('E2E_OUTSIDE_SCOPE: deliberately add an undeclared file',
                                     'Files outside the approved perimeter')
        self.assertEqual(record['tests']['after']['status'], 'passed')
        self.assertEqual(record['verify']['perimeter']['outside'], ['extra.txt'])
        self.assertFalse((self.root / 'extra.txt').exists())

    def dashboard(self):
        server = workbench.create_server(cg, self.workspace, runner=self.runner)
        self.assertIs(server.session.flow_factory, workbench.default_flow_factory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.addCleanup(cleanup)
        base = 'http://127.0.0.1:' + str(server.server_port)

        def request(path, body=None, auth=True):
            headers = {'X-CG-Token': server.session.token} if auth else {}
            if body is not None:
                headers['Content-Type'] = 'application/json'
            req = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers, method='POST' if body is not None else 'GET')
            try:
                with urlopen(req, timeout=10) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as error:
                with error:
                    return error.code, json.loads(error.read())

        self.assertEqual(request('/api/agents', auth=False)[0], 403)
        self.assertEqual(request('/api/open', dict(path=str(self.root)))[0], 202)
        deadline = time.monotonic() + 10
        while True:
            code, status = request('/api/status')
            self.assertEqual(code, 200)
            self.assertNotEqual(status['status']['phase'], 'error', status)
            if status['status']['phase'] == 'ready':
                generation = status['generation']
                break
            self.assertLess(time.monotonic(), deadline, status)
            time.sleep(.02)
        return request, generation

    def test_real_dashboard_drives_complete_flow_and_polls_events(self):
        request, generation = self.dashboard()
        body = dict(generation=generation, intent='Increase the positive value', author='codex', reviewer='claude')
        self.assertEqual(request('/api/changes', body)[0], 403)
        self.assertEqual(self.runner.runs, [])
        self.assertEqual(request('/api/agents/consent', dict(consent=True)), (200, dict(consent=True)))
        code, created = request('/api/changes', body)
        self.assertEqual(code, 202, created)
        cid = created['data']['id']
        events, cursor = [], 0

        def poll(expected):
            nonlocal cursor
            deadline = time.monotonic() + 20
            while True:
                code, batch = request('/api/change/events?' + urlencode(dict(generation=generation, id=cid, since=cursor)))
                self.assertEqual(code, 200, batch)
                chunk = batch['data']['events']
                self.assertTrue(all(event['seq'] > cursor for event in chunk))
                events.extend(chunk)
                cursor = batch['data']['next']
                code, value = request('/api/change?' + urlencode(dict(generation=generation, id=cid)))
                self.assertEqual(code, 200, value)
                record = value['data']
                self.assertIsNone(record['error'], self.diagnostics(record))
                # Observe the terminal state in the event stream too, not just GET.
                if record['state'] == expected and any(event['kind'] == 'state' and
                        event['data']['state'] == expected for event in events):
                    return record
                self.assertLess(time.monotonic(), deadline, record)
                time.sleep(.02)

        steps = [('plan', 'planned', {}),
                 ('approve', 'approved', dict(test_argv=self.test_argv, touch=['app.py'])),
                 ('edit', 'edited', {}), ('verify', 'verified', {}), ('review', 'reviewed', {}),
                 ('integrate', 'integrated', dict(mode='merge'))]
        for action, expected, params in steps:
            code, response = request('/api/change/action', dict(generation=generation, id=cid, action=action, **params))
            self.assertEqual(code, 200, response)
            record = poll(expected)
        self.assert_successful_merge(record)
        self.assert_agent_evidence(record, events)
        self.assertEqual([event['seq'] for event in events], list(range(1, cursor + 1)))


    def test_real_dashboard_ask_uses_consent_and_readonly_mcp(self):
        request, generation = self.dashboard()
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*.py')}
        for client in ('codex', 'claude'):
            with self.subTest(client=client):
                self.assertEqual(request('/api/agents/consent', dict(consent=False))[0], 200)
                body = dict(generation=generation, client=client, question='What does value return?',
                            symbol=dict(file='app.py', name='value', line=1))
                count = len(self.runner.runs)
                self.assertEqual(request('/api/ask', body, auth=False)[0], 403)
                code, denied = request('/api/ask', body)
                self.assertEqual((code, denied['code']), (403, 'consent_required'))
                self.assertEqual(len(self.runner.runs), count)
                self.assertEqual(request('/api/agents/consent', dict(consent=True))[0], 200)
                code, started = request('/api/ask', body)
                self.assertEqual(code, 202, started)
                self.assertEqual((started['client'], started['mode'], started['cwd']),
                                 (client, 'read', str(self.root)))
                events, cursor = [], 0
                deadline = time.monotonic() + 20
                while True:
                    code, batch = request('/api/ask/events?' + urlencode(dict(run=started['run'], since=cursor)))
                    self.assertEqual(code, 200, batch)
                    self.assertTrue(all(event['seq'] > cursor for event in batch['events']))
                    events.extend(batch['events'])
                    cursor = events[-1]['seq'] if events else 0
                    if batch['status'] not in workbench.ASK_ACTIVE and any(e['kind'] == 'end' for e in events):
                        break
                    self.assertLess(time.monotonic(), deadline, batch)
                    time.sleep(.02)
                self.assertEqual(batch['status'], 'done', batch)
                self.assertIn('app.py:1', batch['result']['text'])
                self.assertIn('return 1', batch['result']['text'])
                self.assertEqual(batch['usage'], dict(input_tokens=120, output_tokens=24,
                                                     cached_input_tokens=30, cache_write_input_tokens=None, measured=True))
                run, call = self.runner.runs[-1], self.runner.calls[-1]
                self.assertIsInstance(run, agents.AgentRun)
                self.assertEqual(call['mode'], 'read')
                self.assertEqual(call['cwd'], str(self.root))
                self.assertEqual(call['root'], str(self.root))
                self.assertIn(body['question'], call['prompt'])
                self.assertIn('Selected symbol:', call['prompt'])
                self.assertEqual(events, run.events())
                self.assertEqual([e['seq'] for e in events], list(range(1, cursor + 1)))
                self.assertEqual([e['data']['status'] for e in events if e['kind'] == 'end'], ['done'])
                self.assertTrue(any(e['kind'] == 'tool' and e['data']['name'].endswith('cg_read')
                                    and e['data']['status'] == 'ok' for e in events))
                self.assertTrue(any(e['kind'] == 'tool' and e['data']['name'].endswith('cg_search')
                                    and e['data']['status'] == 'ok' for e in events))
                self.assertEqual(request('/api/agents/consent', dict(consent=False))[0], 200)
                self.assertEqual(request('/api/ask', body)[0], 403)
                self.assertEqual(len(self.runner.runs), count + 1)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*.py')})
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.original_head)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertFalse((self.root / '.code-graph/worktrees').exists())


if __name__ == '__main__':
    unittest.main()
