"""Change-flow and agent endpoints (contract 1.6 § D): real local HTTP, fake ChangeFlow and fake agent runner.

No model, network or project code is ever executed: the runner and the flow are in-process doubles.
"""
import importlib.util
import json
from pathlib import Path
import re
import runpy
import secrets
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_workbench_change_test', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
workbench = runpy.run_path(str(REPO / 'scripts/workbench.py'))


class FakeFlowError(ValueError):
    def __init__(self, code, message=''):
        super().__init__(message or code)
        self.code = code


class FakeRun:
    def __init__(self, client, mode, cwd, prompt, root, timeout, budget_usd=None):
        self.id = secrets.token_hex(8)
        self.budget_usd = budget_usd
        self.client, self.mode, self.cwd, self.root, self.timeout, self.prompt = client, mode, cwd, root, timeout, prompt
        self.status, self.session, self.result, self.usage, self.cancelled = 'running', None, None, None, False
        self._events = [dict(v=1, run=self.id, seq=1, t='2026-11-06T09:00:00Z', kind='start', text='avvio', data={})]

    def events(self, since=0):
        return [event for event in self._events if event['seq'] > since]

    def finish(self, text):
        seq = len(self._events)
        self._events += [dict(v=1, run=self.id, seq=seq + 1, t='2026-11-06T09:00:01Z', kind='message', text=text, data={}),
                         dict(v=1, run=self.id, seq=seq + 2, t='2026-11-06T09:00:02Z', kind='end', text='', data=dict(status='done', duration_s=1.0))]
        self.status, self.result = 'done', dict(text=text)
        self.usage = dict(input_tokens=10, output_tokens=5, cached_input_tokens=None, measured=True)

    def cancel(self):
        self.cancelled, self.status = True, 'cancelled'


class FakeRunner:
    def __init__(self):
        self.runs = []

    def detect(self, timeout=5):
        return [dict(client='codex', available=True, path='/fake/codex', version='fake-1', reason=None),
                dict(client='claude', available=False, path=None, version=None, reason='non installato')]

    def start(self, client, *, mode, cwd, prompt, root, output_schema=None, timeout=900, model=None, budget_usd=None, log_dir=None, executable=None):
        run = FakeRun(client, mode, cwd, prompt, root, timeout, budget_usd)
        self.runs.append(run)
        return run


class FakeFlow:
    raise_next = None

    def __init__(self, engine, root, runner):
        self.root, self.runner, self.calls, self.changes = root, runner, [], {}

    def _check(self, name, change_id=None):
        self.calls.append((name, change_id))
        if FakeFlow.raise_next:
            code, FakeFlow.raise_next = FakeFlow.raise_next, None
            raise FakeFlowError(code, 'errore finto ' + code)
        if change_id is not None and change_id not in self.changes:
            raise FakeFlowError('not_found', 'modifica sconosciuta')

    def _set(self, change_id, state, **extra):
        change = self.changes[change_id]
        change.update(state=state, **extra)
        change['events'].append(dict(v=1, change=change_id, seq=len(change['events']) + 1, t='2026-11-06T09:00:00Z', kind='state', text=state, data={}))
        return self.get(change_id)

    def create(self, intent, author, reviewer, allow_dirty=False, budget_usd=None):
        self._check('create')
        change_id = 'c-20261106-090000-%04x' % len(self.changes)
        self.changes[change_id] = dict(format='cg-change/1', id=change_id, intent=intent, project=str(self.root), author=author, reviewer=reviewer, state='draft', round=0, events=[], approved=None, budget_usd=budget_usd)
        return self._set(change_id, 'draft')

    def list(self):
        self._check('list')
        return [dict(id=c['id'], intent=c['intent'], state=c['state'], updated='2026-11-06T09:00:00Z', author=c['author'], reviewer=c['reviewer']) for c in self.changes.values()]

    def get(self, change_id):
        if change_id not in self.changes:
            raise FakeFlowError('not_found', 'modifica sconosciuta')
        return {key: value for key, value in self.changes[change_id].items() if key != 'events'}

    def events(self, change_id, since=0, limit=500):
        self._check('events', change_id)
        events = [event for event in self.changes[change_id]['events'] if event['seq'] > since][:limit]
        return dict(events=events, next=events[-1]['seq'] if events else since)

    def plan(self, change_id):
        self._check('plan', change_id)
        return self._set(change_id, 'planning')

    def approve(self, change_id, test_argv, touch=None, out_of_scope=None, no_tests_reason=None):
        self._check('approve', change_id)
        return self._set(change_id, 'approved', approved=dict(test_argv=test_argv, touch=touch, out_of_scope=out_of_scope, no_tests_reason=no_tests_reason))

    def edit(self, change_id, feedback=None):
        self._check('edit', change_id)
        return self._set(change_id, 'baseline', feedback=feedback)

    def verify(self, change_id):
        self._check('verify', change_id)
        return self._set(change_id, 'verifying')

    def review(self, change_id, same_client_reason=None):
        self._check('review', change_id)
        return self._set(change_id, 'reviewing', same_client_reason=same_client_reason)

    def integrate(self, change_id, mode='branch', force_reason=None):
        self._check('integrate', change_id)
        return self._set(change_id, 'integrated', integration=dict(mode=mode, force_reason=force_reason))

    def discard(self, change_id):
        self._check('discard', change_id)
        return self._set(change_id, 'discarded')

    def cancel(self, change_id):
        self._check('cancel', change_id)
        return self._set(change_id, 'draft')


class ChangeEndpointTests(unittest.TestCase):
    def setUp(self):
        FakeFlow.raise_next = None
        self.temp = tempfile.TemporaryDirectory(prefix='cg-change-ui-test-')
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.root = self.workspace / 'project'
        (self.root / 'pkg').mkdir(parents=True)
        (self.root / 'pkg/core.py').write_text('def work(): return 1\n')
        (self.root / 'pkg/test_core.py').write_text('from pkg.core import work\ndef test_work(): return work()\n')
        self.runner, self.flows = FakeRunner(), []
        def factory(engine, root, runner):
            flow = FakeFlow(engine, root, runner)
            self.flows.append(flow)
            return flow
        self.server = workbench['create_server'](cg, self.workspace, runner=self.runner, flow_factory=factory)
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.token = self.server.session.token

    def request(self, path, body=None, auth=True, raw=None, extra=None):
        headers = {'X-CG-Token': self.token} if auth else {}
        if body is not None or raw is not None:
            headers['Content-Type'] = 'application/json'
        headers.update(extra or {})
        payload = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        req = Request(self.base + path, data=payload, headers=headers, method='POST' if payload is not None else 'GET')
        try:
            with urlopen(req, timeout=10) as response:
                content = response.read()
                return response.status, json.loads(content) if response.headers.get_content_type() == 'application/json' else content, response.headers
        except HTTPError as error:
            return error.code, json.loads(error.read()), error.headers

    def ready(self):
        code, _, _ = self.request('/api/open', {'path': str(self.root)})
        self.assertEqual(code, 202)
        for _ in range(200):
            _, status, _ = self.request('/api/status')
            if status['status']['phase'] == 'ready':
                self.generation = status['generation']
                return
            if status['status']['phase'] == 'error':
                self.fail(status['status'])
            time.sleep(.02)
        self.fail('index did not finish')

    def get(self, route, **params):
        return self.request(route + '?' + urlencode(dict(generation=self.generation, **params)))

    def post(self, route, **body):
        return self.request(route, dict(generation=self.generation, **body))

    def consent(self):
        code, value, _ = self.request('/api/agents/consent', {'consent': True})
        self.assertEqual((code, value), (200, {'consent': True}))

    def create(self, intent='Rinomina work'):
        code, value, _ = self.post('/api/changes', intent=intent, author='codex', reviewer='claude')
        self.assertEqual(code, 202, value)
        return value['data']['id']

    def test_every_new_endpoint_requires_the_session_token(self):
        self.ready()
        gets = ['/api/agents', '/api/changes?generation=' + self.generation, '/api/change?id=x', '/api/change/events?id=x', '/api/ask/events?run=x']
        posts = ['/api/agents/consent', '/api/changes', '/api/change/action', '/api/ask']
        for path in gets:
            self.assertEqual(self.request(path, auth=False)[0], 403, path)
            self.assertEqual(self.request(path, extra={'X-CG-Token': 'wrong'})[0], 403, path)
        for path in posts:
            self.assertEqual(self.request(path, {'consent': True, 'generation': self.generation}, auth=False)[0], 403, path)
        for path in posts[1:]:
            self.assertEqual(self.request(path, {'generation': self.generation}, extra={'Origin': 'https://evil.example'})[0], 403, path)
        self.assertFalse(self.server.session.consent)
        self.assertEqual(self.runner.runs, [])
        self.assertTrue(all(call[0] in ('list',) for flow in self.flows for call in flow.calls))

    def test_agents_listing_and_session_consent(self):
        code, value, _ = self.request('/api/agents')
        self.assertEqual(code, 200)
        self.assertFalse(value['consent'])
        self.assertEqual([client['client'] for client in value['clients']], ['codex', 'claude'])
        for bad in ('yes', 1, None, []):
            self.assertEqual(self.request('/api/agents/consent', {'consent': bad})[0], 400)
        self.consent()
        self.assertTrue(self.request('/api/agents')[1]['consent'])
        self.assertEqual(self.request('/api/agents/consent', {'consent': False})[1], {'consent': False})

    def test_no_agent_starts_without_consent(self):
        self.ready()
        code, error, _ = self.post('/api/changes', intent='x', author='codex', reviewer='claude')
        self.assertEqual((code, error['code']), (403, 'consent_required'))
        code, error, _ = self.post('/api/ask', client='codex', question='Chi chiama work?')
        self.assertEqual((code, error['code']), (403, 'consent_required'))
        self.consent()
        change_id = self.create()
        self.request('/api/agents/consent', {'consent': False})
        for action in ('plan', 'edit', 'review'):
            code, error, _ = self.post('/api/change/action', id=change_id, action=action)
            self.assertEqual((code, error['code']), (403, 'consent_required'), action)
        self.assertEqual(self.runner.runs, [])
        self.assertNotIn('plan', [name for name, _ in self.flows[0].calls])
        # Stopping or discarding never needs consent.
        self.assertEqual(self.post('/api/change/action', id=change_id, action='cancel')[0], 200)

    def test_full_change_lifecycle_through_the_api(self):
        self.ready()
        self.consent()
        change_id = self.create('Rinomina work in compute')
        code, listing, _ = self.get('/api/changes')
        self.assertEqual((code, [item['id'] for item in listing['data']]), (200, [change_id]))
        code, change, _ = self.get('/api/change', id=change_id)
        self.assertEqual((code, change['data']['intent'], change['data']['state']), (200, 'Rinomina work in compute', 'draft'))
        steps = [dict(action='plan'),
                 dict(action='approve', test_argv=['python3', '-m', 'unittest'], touch=['pkg/core.py'], out_of_scope=['docs']),
                 dict(action='edit'), dict(action='verify'), dict(action='review'),
                 dict(action='edit', feedback='Aggiorna anche il test'), dict(action='integrate', mode='merge', force_reason='verdetto accettato a voce')]
        for step in steps:
            code, value, _ = self.post('/api/change/action', id=change_id, **step)
            self.assertEqual(code, 200, (step, value))
        change = self.flows[0].changes[change_id]
        self.assertEqual(change['approved'], dict(test_argv=['python3', '-m', 'unittest'], touch=['pkg/core.py'], out_of_scope=['docs'], no_tests_reason=None))
        self.assertEqual(change['feedback'], 'Aggiorna anche il test')
        self.assertEqual(change['integration'], dict(mode='merge', force_reason='verdetto accettato a voce'))
        self.assertEqual([name for name, _ in self.flows[0].calls if name not in ('list', 'events')], ['create', 'plan', 'approve', 'edit', 'verify', 'review', 'edit', 'integrate'])
        code, events, _ = self.get('/api/change/events', id=change_id, since=2)
        self.assertEqual(code, 200)
        self.assertEqual(events['data']['events'][0]['seq'], 3)
        self.assertEqual(events['data']['next'], events['data']['events'][-1]['seq'])
        self.assertEqual(self.post('/api/change/action', id=change_id, action='discard')[0], 200)

    def test_request_validation(self):
        self.ready()
        self.consent()
        change_id = self.create()
        for body in (dict(id=change_id, action='push'), dict(id=change_id, action='approve', test_argv='python -m unittest'),
                     dict(id=change_id, action='approve', test_argv=[]), dict(id=change_id, action='approve', test_argv=['', 'x']),
                     dict(id=change_id, action='integrate', mode='rebase'), dict(id=None, action='plan'), dict(id=change_id, action='edit', feedback=7)):
            code, error, _ = self.post('/api/change/action', **body)
            self.assertEqual((code, error['code']), (400, 'invalid'), body)
        self.assertEqual(self.post('/api/change/action', id=change_id, action='approve', test_argv=[], no_tests_reason='Solo documentazione')[0], 200)
        for body in (dict(intent='', author='codex', reviewer='claude'), dict(intent='x', author='gpt', reviewer='claude'), dict(intent='x' * 20001, author='codex', reviewer='claude')):
            self.assertEqual(self.post('/api/changes', **body)[0], 400, body)
        for body in (dict(client='gemini', question='x'), dict(client='codex', question=''), dict(client='codex', question='x', symbol=5)):
            self.assertEqual(self.post('/api/ask', **body)[0], 400, body)
        self.assertEqual(self.get('/api/change/events', id=change_id, since='-1')[0], 400)

    def test_flow_errors_map_to_http_status_and_code(self):
        self.ready()
        self.consent()
        change_id = self.create()
        expected = dict(not_found=404, bad_state=409, busy=409, conflict=409, dirty=409, invalid=400, not_git=400)
        for code, status in expected.items():
            FakeFlow.raise_next = code
            got, error, _ = self.post('/api/change/action', id=change_id, action='verify')
            self.assertEqual((got, error['code']), (status, code))
            self.assertEqual(error['error'], 'errore finto ' + code)
        got, error, _ = self.get('/api/change', id='c-missing')
        self.assertEqual((got, error['code']), (404, 'not_found'))
        FakeFlow.raise_next = 'dirty'
        got, error, _ = self.post('/api/changes', intent='x', author='codex', reviewer='claude')
        self.assertEqual((got, error['code']), (409, 'dirty'))

    def test_changes_follow_the_current_generation(self):
        self.ready()
        self.consent()
        self.assertEqual(self.request('/api/changes?generation=wrong')[0], 409)
        code, error, _ = self.request('/api/change/action', dict(generation='wrong', id='x', action='plan'))
        self.assertEqual((code, error['code']), (409, 'busy'))
        self.assertEqual(self.flows, [])

    def test_body_limit_is_larger_only_for_change_agent_and_ask_routes(self):
        self.ready()
        self.consent()
        change_id = self.create()
        feedback = 'f' * 15000
        code, _, _ = self.post('/api/change/action', id=change_id, action='edit', feedback=feedback)
        self.assertEqual(code, 200)
        self.assertEqual(self.flows[0].changes[change_id]['feedback'], feedback)
        oversized = json.dumps(dict(generation=self.generation, id=change_id, action='edit', feedback='f' * 66000)).encode()
        for path in ('/api/change/action', '/api/changes', '/api/ask', '/api/agents/consent'):
            self.assertEqual(self.request(path, raw=oversized)[0], 413, path)
        self.assertEqual(self.request('/api/open', raw=json.dumps({'path': 'x' * 9000}).encode())[0], 413)
        self.assertEqual(self.request('/api/prepare', raw=json.dumps({'intent': 'x' * 9000}).encode())[0], 413)

    def test_ask_runs_read_only_on_project_root_at_most_three_at_once(self):
        self.ready()
        self.consent()
        runs = []
        for number in range(3):
            code, value, _ = self.post('/api/ask', client='codex', question=f'Domanda {number}: chi chiama work?', symbol='pkg/core.py:1 work')
            self.assertEqual(code, 202, value)
            self.assertEqual((value['mode'], value['cwd'], value['status']), ('read', str(self.root.resolve()), 'running'))
            runs.append(value['run'])
        code, error, _ = self.post('/api/ask', client='claude', question='Quarta')
        self.assertEqual((code, error['code']), (409, 'busy'))
        first = self.runner.runs[0]
        self.assertEqual((first.mode, first.root, first.cwd), ('read', str(self.root.resolve()), str(self.root.resolve())))
        self.assertIn('Domanda 0: chi chiama work?', first.prompt)
        self.assertIn('cg_search', first.prompt)
        self.assertIn('cg_source', first.prompt)
        self.assertIn('pkg/core.py:1 work', first.prompt)
        self.assertLessEqual(first.timeout, 900)
        code, events, _ = self.request('/api/ask/events?run=' + runs[0] + '&since=0')
        self.assertEqual((code, events['status'], [e['kind'] for e in events['events']], events['result']), (200, 'running', ['start'], None))
        first.finish('work è chiamata da test_work (pkg/test_core.py:2)')
        _, events, _ = self.request('/api/ask/events?run=' + runs[0] + '&since=1')
        self.assertEqual([e['kind'] for e in events['events']], ['message', 'end'])
        self.assertEqual((events['status'], events['result']['text'], events['usage']['measured']), ('done', 'work è chiamata da test_work (pkg/test_core.py:2)', True))
        self.assertEqual(self.post('/api/ask', client='claude', question='Ora c’è posto')[0], 202)
        self.assertEqual(self.request('/api/ask/events?run=unknown')[0], 404)
        self.server.server_close()
        self.assertTrue(all(run.cancelled for run in self.runner.runs if run is not first))
        self.assertFalse(first.cancelled)

    def test_claude_spending_cap_is_validated_and_forwarded(self):
        # Rinvio 4 (dec-cucitura-budget): budget_usd is a finite number > 0, or absent; 400 otherwise.
        self.ready()
        self.consent()
        code, value, _ = self.post('/api/changes', intent='Con tetto', author='codex', reviewer='claude', budget_usd=0.5)
        self.assertEqual(code, 202, value)
        self.assertEqual(self.flows[0].changes[value['data']['id']]['budget_usd'], 0.5)
        self.assertIsNone(self.flows[0].changes[self.create('Senza tetto')]['budget_usd'])
        for bad in (0, -1, 'abc', True, [], float('inf'), float('nan')):
            code, error, _ = self.post('/api/changes', intent='x', author='codex', reviewer='claude', budget_usd=bad)
            self.assertEqual((code, error['code']), (400, 'invalid'), bad)
            code, error, _ = self.post('/api/ask', client='claude', question='x', budget_usd=bad)
            self.assertEqual((code, error['code']), (400, 'invalid'), bad)
        self.assertEqual(len(self.flows[0].changes), 2)
        self.assertEqual(self.runner.runs, [])
        self.assertEqual(self.post('/api/ask', client='claude', question='Chi chiama work?', budget_usd=0.25)[0], 202)
        self.assertEqual(self.post('/api/ask', client='codex', question='Chi chiama work?', budget_usd=0.25)[0], 202)
        self.assertEqual([(run.client, run.budget_usd) for run in self.runner.runs], [('claude', 0.25), ('codex', None)])

    def test_huge_integer_cap_is_invalid_not_an_internal_error(self):
        # F5 / rinvio 16: math.isfinite(10**400) raises OverflowError; the answer must be 400 invalid, as in ChangeFlow.create.
        self.ready()
        self.consent()
        for huge in (10**400, -10**400):
            for path, body in (('/api/changes', dict(intent='x', author='codex', reviewer='claude')), ('/api/ask', dict(client='claude', question='x'))):
                code, error, _ = self.post(path, budget_usd=huge, **body)
                self.assertEqual((code, error.get('code')), (400, 'invalid'), (path, huge > 0))
        self.assertEqual(self.flows[0].changes if self.flows else {}, {})
        self.assertEqual(self.runner.runs, [])

    def test_concurrent_asset_requests_are_all_served(self):
        # Rinvio 7, root cause: with the default backlog of 5 some of the page's parallel asset loads were reset,
        # so workbench.js (and the bootstrap it starts) could be missing under load.
        statuses, lock = [], threading.Lock()
        def fetch():
            try:
                with urlopen(self.base + '/workbench.css', timeout=10) as response:
                    status = response.status
            except Exception as error:
                status = type(error).__name__
            with lock:
                statuses.append(status)
        threads = [threading.Thread(target=fetch) for _ in range(48)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(statuses, [200] * 48)

    def test_change_asset_is_served_and_page_keeps_the_same_policy(self):
        code, page, headers = self.request('/')
        self.assertEqual(code, 200)
        policy = headers['Content-Security-Policy']
        self.assertIn("default-src 'none'", policy)
        self.assertIn("connect-src 'self'", policy)
        self.assertNotRegex(policy, r'https?://')
        self.assertIn(b'/workbench-change.js', page)
        code, script, headers = self.request('/workbench-change.js', auth=False)
        self.assertEqual((code, headers['Content-Security-Policy']), (200, policy))
        text = script.decode()
        self.assertNotIn('innerHTML', text)
        self.assertNotIn('insertAdjacentHTML', text)
        self.assertNotRegex(text, r'https?://(?!127\.0\.0\.1)')
        for name in ('workbench.html', 'workbench.css', 'workbench-i18n.js', 'workbench.js'):
            self.assertNotRegex((REPO / 'assets' / name).read_text(), r'(src|href)=["\']https?://', name)


class ConsentRaceTests(unittest.TestCase):
    """A revocation racing an agent start (F2): consent is re-checked under the session lock right before the start."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cg-consent-race-')
        self.addCleanup(self.temp.cleanup)
        self.runner, self.flows = FakeRunner(), []
        def factory(engine, root, runner):
            flow = FakeFlow(engine, root, runner)
            self.flows.append(flow)
            return flow
        self.session = workbench['Session'](cg, self.temp.name, runner=self.runner, flow_factory=factory)
        self.session.root, self.session.generation = Path(self.temp.name).resolve(), 'g'
        self.session.status, self.session.profile = dict(phase='ready'), dict(revision='r')
        self.addCleanup(self.session.close)

    def suspended(self, call):
        """Run call() in a thread that blocks inside project() until released, as a slow request would."""
        entered, release, outcome = threading.Event(), threading.Event(), []
        original = self.session.project
        def project(generation):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(generation)
        self.session.project = project
        def target():
            try:
                outcome.append(call())
            except Exception as error:
                outcome.append(error)
        thread = threading.Thread(target=target)
        thread.start()
        self.assertTrue(entered.wait(5))
        return release, thread, outcome

    def test_revocation_during_a_suspended_ask_starts_no_agent(self):
        self.session.set_consent(True)
        release, thread, outcome = self.suspended(lambda: self.session.ask('g', dict(client='codex', question='Chi chiama work?')))
        self.assertEqual(self.session.set_consent(False), dict(consent=False))
        release.set()
        thread.join(5)
        self.assertIsInstance(outcome[0], workbench['ConsentRequired'])
        self.assertEqual(self.runner.runs, [])

    def test_revocation_during_a_suspended_flow_action_is_refused(self):
        self.session.set_consent(True)
        change_id = self.session.flow('g').create('Rinomina work', 'codex', 'claude')['id']
        release, thread, outcome = self.suspended(lambda: self.session.change_action('g', dict(id=change_id, action='plan')))
        self.session.set_consent(False)
        release.set()
        thread.join(5)
        self.assertIsInstance(outcome[0], workbench['ConsentRequired'])
        self.assertNotIn(('plan', change_id), self.flows[0].calls)

    def test_flow_agents_start_only_through_the_consent_gate(self):
        # ChangeFlow starts its agents later, in its own thread (and after the baseline tests for an edit).
        self.session.set_consent(True)
        self.session.flow('g')
        runner = self.flows[0].runner
        self.session.set_consent(False)
        with self.assertRaises(workbench['ConsentRequired']):
            runner.start('codex', mode='write', cwd='/w', root='/w', prompt='edit', timeout=900)
        self.assertEqual(self.runner.runs, [])
        self.session.set_consent(True)
        runner.start('claude', mode='read', cwd='/w', root='/w', prompt='review', timeout=900)
        self.assertEqual([run.client for run in self.runner.runs], ['claude'])
        self.assertEqual(runner.detect(), self.runner.detect())


class ScopePathRuleTests(unittest.TestCase):
    def test_engine_path_rule_matches_the_shared_table(self):
        # E1: the dashboard excludes the paths ChangeFlow._relative refuses; the same table is checked on the JS side in change_browser.py.
        table = runpy.run_path(str(REPO / 'tests/change_browser.py'))['EDITABLE_PATHS']
        flow = runpy.run_path(str(REPO / 'scripts/change_flow.py'))
        for path, expected in table:
            try:
                flow['ChangeFlow']._relative(None, path)
                accepted = True
            except flow['FlowError']:
                accepted = False
            self.assertEqual(accepted, expected, repr(path))


class ChangeTranslationTests(unittest.TestCase):
    def test_verify_step_has_its_own_label(self):
        # Rinvio 13: the shared Italian key "Verifica" means "Verifies" elsewhere; the change flow uses a keyed label.
        script = (REPO / 'assets/workbench-change.js').read_text()
        i18n = (REPO / 'assets/workbench-i18n.js').read_text()
        self.assertNotRegex(script, r"\b[TL]\('Verifica'\)")
        self.assertIn("K('change.verify')", script)
        self.assertIn("'change.verify':{it:'Verifica',en:'Verify'}", i18n)
        for key in set(re.findall(r"\bK\('([^']+)'\)", script)) | {'change.verify'}:
            self.assertRegex(i18n, r"'" + re.escape(key) + r"':\{it:'[^']+',en:'[^']+'\}", key)

    def test_impact_chain_is_separated_from_the_distance(self):
        # Rinvio 14: the first chain step must not run into "distance N" ("distance 2tests/...").
        workbench_js = (REPO / 'assets/workbench.js').read_text()
        self.assertIn("el('small',` → ${step.file}:${step.line} [${step.confidence}]`)", workbench_js)


    def test_every_change_ui_string_has_an_english_translation(self):
        script = (REPO / 'assets/workbench-change.js').read_text()
        page = (REPO / 'assets/workbench.html').read_text()
        i18n = (REPO / 'assets/workbench-i18n.js').read_text()
        strings = set(re.findall(r"\b[TL]\('((?:[^'\\]|\\.)*)'\)", script)) | set(re.findall(r'data-i18n="([^"]+)"', page))
        self.assertGreater(len(strings), 30)
        missing = sorted(text for text in strings if json.dumps(text, ensure_ascii=False) + ':' not in i18n.replace('": ', '":'))
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
