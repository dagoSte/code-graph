"""«Modifica» section end to end in real Chromium, with a scripted fake ChangeFlow and fake agents.

Outside the unit proof: needs Playwright and a Chromium executable. No model, network or project code runs.

    python tests/change_browser.py --browser /path/to/chromium [--server-python .venv/bin/python] [--screenshot out.png]

The server half (``--serve``) runs with the project interpreter (tree-sitter); the browser half can run
with any interpreter that has Playwright.
"""
import argparse
import json
from pathlib import Path
import re
import secrets
import selectors
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
# E1: one table for the scope-path rule, checked against ChangeFlow._relative (unit suite) and the dashboard's JS (browser).
EDITABLE_PATHS = [
    ('pkg/core.py', True), ('./pkg/core.py', True), ('pkg//core.py', True), ('src/.gitignore', True), ('a b/c.py', True),
    ('', False), ('/abs/core.py', False), ('../fuori.py', False), ('pkg/../x.py', False), ('a\\b.py', False),
    ('a\x00b.py', False), ('.git/config', False), ('.code-graph/graph.db', False), ('.', False),
]
HOSTILE = '<img src=x onerror="document.title=\'pwned\'">'


def serve(workspace, bootstrap_delay=0.0):
    import importlib.util
    import runpy
    spec = importlib.util.spec_from_file_location('cg_change_browser', REPO / 'scripts/cg.py')
    cg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cg)
    workbench = runpy.run_path(str(REPO / 'scripts/workbench.py'))

    class FlowError(ValueError):
        def __init__(self, code, message):
            super().__init__(message)
            self.code = code

    class Run:
        def __init__(self, client, prompt):
            self.id, self.client, self.mode, self.cwd = secrets.token_hex(8), client, 'read', ''
            self.status, self.session, self.result, self.usage, self.prompt = 'running', None, None, None, prompt
            self._events = [dict(v=1, run=self.id, seq=1, t=now(), kind='start', text='avvio ' + client, data={})]
            threading.Timer(.4, self.finish).start()

        def finish(self):
            if self.status != 'running':
                return
            self._events += [dict(v=1, run=self.id, seq=2, t=now(), kind='tool', text='cg_callers work', data=dict(name='cg_callers', status='ok')),
                             dict(v=1, run=self.id, seq=3, t=now(), kind='message', text='work è chiamata da view (pkg/site.py:5) e da test_work.', data={})]
            self.result, self.usage, self.status = dict(text='work è chiamata da view (pkg/site.py:5) e da test_work.'), dict(input_tokens=1, output_tokens=1, cached_input_tokens=None, measured=True), 'done'

        def events(self, since=0):
            return [event for event in self._events if event['seq'] > since]

        def cancel(self):
            self.status = 'cancelled'

    class Runner:
        def detect(self, timeout=5):
            return [dict(client='codex', available=True, path='/fake/bin/codex', version='codex-fake 1.0', reason=None),
                    dict(client='claude', available=True, path='/fake/bin/claude', version='claude-fake 2.0', reason=None)]

        def start(self, client, **options):
            run = Run(client, options['prompt'])
            self.last = dict(client=client, mode=options['mode'], cwd=options['cwd'])
            return run

    def now():
        return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    class Flow:
        def __init__(self, engine, root, runner):
            self.root, self.changes, self.lock = str(root), {}, threading.RLock()

        def _event(self, change, kind, text, data=None):
            change['_events'].append(dict(v=1, change=change['id'], seq=len(change['_events']) + 1, t=now(), kind=kind, text=text, data=data or {}))

        def _state(self, change, state):
            change['state'], change['updated'] = state, now()
            change['history'].append(dict(state=state, t=now(), by='engine', note=''))
            self._event(change, 'state', state)

        def _later(self, change, active, final, **fields):
            self._state(change, active)
            self._event(change, 'agent', '', dict(v=1, run='r1', seq=1, t=now(), kind='message', text=HOSTILE + ' lavoro su ' + active, data={}))
            def done():
                with self.lock:
                    if change['state'] != active:
                        return
                    change.update(fields)
                    self._state(change, final)
            threading.Timer(.6, done).start()
            return self.get(change['id'])

        def _find(self, change_id, *states):
            change = self.changes.get(change_id)
            if change is None:
                raise FlowError('not_found', 'Modifica sconosciuta.')
            if states and change['state'] not in states:
                raise FlowError('bad_state', 'Transizione non ammessa da ' + change['state'] + '.')
            return change

        def create(self, intent, author, reviewer, allow_dirty=False, budget_usd=None):
            with self.lock:
                change_id = 'c-20261106-090000-%04x' % len(self.changes)
                change = dict(format='cg-change/1', id=change_id, intent=intent, project=self.root, base=dict(branch='main', commit='0' * 40),
                              branch='cg/change-' + change_id, worktree=self.root + '/.code-graph/worktrees/' + change_id, author=author, reviewer=reviewer,
                              state='draft', round=0, created=now(), updated=now(), history=[], plan=None, approved=None, tests=dict(baseline=None, after=None),
                              edit=None, verify=None, review=None, integration=None, error=None, _events=[], budget_usd=budget_usd)
                self.changes[change_id] = change
                self._state(change, 'draft')
                return self.get(change_id)

        def list(self):
            return [dict(id=c['id'], intent=c['intent'], state=c['state'], updated=c['updated'], author=c['author'], reviewer=c['reviewer']) for c in self.changes.values()]

        def get(self, change_id):
            with self.lock:
                return {k: v for k, v in self._find(change_id).items() if k != '_events'}

        def events(self, change_id, since=0, limit=500):
            with self.lock:
                events = [e for e in self._find(change_id)['_events'] if e['seq'] > since][:limit]
                return dict(events=events, next=events[-1]['seq'] if events else since)

        def plan(self, change_id):
            with self.lock:
                change = self._find(change_id, 'draft', 'planned')
                touch = [dict(path='pkg/site.py', symbol='view', line=5, why='definizione')] if 'view' in change['intent'] else [dict(path='pkg/core.py', symbol='work', line=1, why='definizione')]
                if 'view' not in change['intent']:
                    # E1 / rinvio 17: the agent may propose paths the engine refuses (absolute, '..'); the engine keeps them in checked.unresolved.
                    touch += [dict(path=self.root + '/pkg/core.py', symbol='work', line=1, why='percorso assoluto'), dict(path='../fuori.py', symbol='x', line=1, why='fuori dal progetto')]
                plan = dict(agent=dict(summary='Rinominare work in compute e aggiornare i chiamanti.', touch=touch,
                                       affected=[dict(path='pkg/site.py', symbol='view', line=5, why='chiama work', confidence='high')],
                                       tests=[dict(path='tests/test_core.py', name='test_work', why='copre work')], out_of_scope=['README.md'], risks=['Import dinamici non visti dal grafo.'],
                                       unknowns=['Chiamate via getattr'], test_command=['python3', '-m', 'unittest', 'discover', '-s', 'tests']),
                            graph=dict(affected=[], tests=[], limits=['Registrazioni dinamiche escluse']), checked=dict(unresolved=['dynamic_handler']))
                return self._later(change, 'planning', 'planned', plan=plan)

        def approve(self, change_id, test_argv, touch=None, out_of_scope=None, no_tests_reason=None):
            with self.lock:
                change = self._find(change_id, 'planned')
                # Like ChangeFlow.approve: an explicit touch is validated strictly.
                for item in touch or []:
                    path = item if isinstance(item, str) else item.get('path', '')
                    if not path or path.startswith('/') or '..' in path.split('/'):
                        raise FlowError('invalid', 'Path is outside the editable project: ' + path)
                change['approved'] = dict(test_argv=test_argv, touch=touch, out_of_scope=out_of_scope, no_tests_reason=no_tests_reason, t=now())
                self._state(change, 'approved')
                return self.get(change_id)

        def edit(self, change_id, feedback=None):
            with self.lock:
                change = self._find(change_id, 'approved', 'verified', 'reviewed')
                baseline = dict(argv=change['approved']['test_argv'], cwd=change['worktree'], exit=0, status='passed', duration_s=0.8, summary=dict(total=3, failed=0, errors=0, skipped=0, framework='unittest'), failing=[], stdout_tail='', stderr_tail='')
                return self._later(change, 'editing', 'edited', edit=dict(run='r2', files=[dict(path='pkg/core.py', change='update'), dict(path='pkg/site.py', change='update')], result=dict(text='Rinominata work in compute.')), tests=dict(baseline=baseline, after=None))

        def verify(self, change_id):
            with self.lock:
                change = self._find(change_id, 'edited')
                after = dict(change['tests']['baseline'], duration_s=0.9)
                verify = dict(tests=dict(newly_failing=[], fixed=[], still_failing=[], before='passed', after='passed'), static={}, perimeter=dict(outside=['pkg/site.py'], planned_untouched=[]), blocking=['pkg/site.py fuori perimetro'], ok=False)
                return self._later(change, 'verifying', 'verified', tests=dict(baseline=change['tests']['baseline'], after=after), verify=verify)

        def review(self, change_id, same_client_reason=None):
            with self.lock:
                change = self._find(change_id, 'verified')
                review = dict(run='r3', result=dict(verdict='accept', summary='Rinomina corretta; view aggiornata.', findings=[dict(severity='minor', path='pkg/site.py', line=5, text='Aggiungi il file al perimetro.')]))
                return self._later(change, 'reviewing', 'reviewed', review=review)

        def integrate(self, change_id, mode='branch', force_reason=None):
            with self.lock:
                change = self._find(change_id, 'reviewed')
                if change['verify']['blocking'] and not force_reason:
                    raise FlowError('bad_state', 'Blocchi presenti: serve un motivo.')
                change['integration'] = dict(mode=mode, force_reason=force_reason, command='git merge --no-ff ' + change['branch'])
                self._state(change, 'integrated')
                return self.get(change_id)

        def discard(self, change_id):
            with self.lock:
                self._state(self._find(change_id), 'discarded')
                return self.get(change_id)

        def cancel(self, change_id):
            return self.get(change_id)

    server = workbench['create_server'](cg, workspace, runner=Runner(), flow_factory=lambda engine, root, runner: Flow(engine, root, runner))
    if bootstrap_delay:
        # Simulates a loaded machine: the dashboard bootstrap answers late (rinvio 7).
        handler, original = server.RequestHandlerClass, server.RequestHandlerClass.do_GET
        def slow(self):
            if self.path.startswith('/api/bootstrap'):
                time.sleep(bootstrap_delay)
            original(self)
        handler.do_GET = slow
    print('http://127.0.0.1:%d/#%s' % (server.server_port, server.session.token), flush=True)
    try:
        server.serve_forever(poll_interval=.1)
    finally:
        server.server_close()


def drive(args):
    from playwright.sync_api import expect, sync_playwright
    checks, errors, external = [], [], []
    def check(ok, label):
        if not ok:
            raise AssertionError(label)
        checks.append(label)
    with tempfile.TemporaryDirectory(prefix='cg-change-browser-') as directory:
        workspace = Path(directory)
        root = workspace / 'progetto-demo'
        (root / 'pkg').mkdir(parents=True)
        (root / 'tests').mkdir()
        (root / 'pkg/core.py').write_text('def work():\n    return 1\n')
        (root / 'pkg/site.py').write_text('from pkg.core import work\n\n\n\ndef view():\n    return work()\n')
        (root / 'tests/test_core.py').write_text('from pkg.core import work\ndef test_work():\n    assert work() == 1\n')
        process = subprocess.Popen([args.server_python, '-I', str(Path(__file__).resolve()), '--serve', str(workspace), '--bootstrap-delay', str(args.bootstrap_delay)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=REPO)
        try:
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(120):
                raise RuntimeError('server did not start')
            line = process.stdout.readline()
            match = re.search(r'http://127\.0\.0\.1:\d+/#\S+', line)
            if not match:
                raise RuntimeError(line + process.stdout.read())
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=args.browser)
                page = browser.new_context(viewport={'width': 1500, 'height': 1000}).new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' and not msg.text.startswith('Failed to load resource') else None)
                page.on('response', lambda response: errors.append('HTTP %d %s' % (response.status, urlsplit(response.url).path)) if response.status >= 400 and urlsplit(response.url).path != '/favicon.ico' else None)
                page.on('request', lambda request: external.append(request.url) if request.url.startswith(('http:', 'https:')) and urlsplit(request.url).hostname != '127.0.0.1' else None)
                approvals, creates, asks = [], [], []
                def body(request, path):
                    return json.loads(request.post_data or '{}') if request.method == 'POST' and urlsplit(request.url).path == path else None
                page.on('request', lambda request: creates.append(body(request, '/api/changes')) if body(request, '/api/changes') is not None else None)
                page.on('request', lambda request: asks.append(body(request, '/api/ask')) if body(request, '/api/ask') is not None else None)
                page.on('request', lambda request: approvals.append(json.loads(request.post_data)) if request.method == 'POST' and urlsplit(request.url).path == '/api/change/action' and json.loads(request.post_data or '{}').get('action') == 'approve' else None)
                # Wait for the real bootstrap answer, however late, not for a fixed 5 s (rinvio 7).
                with page.expect_response(lambda response: urlsplit(response.url).path == '/api/bootstrap', timeout=180000) as boot:
                    page.goto(match.group(0))
                check(boot.value.status == 200, 'dashboard bootstrap answered')
                expect(page.locator('#workspace-hint')).to_contain_text('Workspace', timeout=30000)
                page.fill('#project-input', str(root))
                page.click('#analyze')
                page.wait_for_selector('#workspace', state='visible', timeout=30000)
                page.fill('#search', 'work')
                page.locator('.symbol-result', has_text='pkg/core.py:1').first.click()
                page.locator('[data-tab="impact"]').click()
                expect(page.locator('#inspection-content')).to_contain_text('test_work', timeout=15000)
                impact = page.locator('#inspection-content').inner_text()
                check(not re.search(r'(distanza|distance) \d+[^\s·→]', impact) and '→ tests/test_core.py' in impact, 'impact chain is separated from the distance')
                page.click('#change-flow-button')
                section = page.locator('#change-flow')
                expect(section).to_be_visible()
                expect(page.locator('#overview')).to_be_hidden()
                expect(page.locator('.change-step')).to_have_count(6)
                check(page.locator('.change-step').nth(5).inner_text().endswith('✅'), 'stepper shows the six steps with the two user gates')
                expect(page.locator('.change-client')).to_have_count(2)
                page.fill('#change-intent', 'Rinomina work in compute')
                # F5: text the number field cannot parse (value '' with validity.badInput) must never become "no cap".
                page.locator('#change-budget').press_sequentially('1e')
                # F5b: a redraw (here the theme) must not turn the invalid text into an empty field.
                page.select_option('#theme', 'dark')
                page.select_option('#theme', 'light')
                check(page.locator('#change-budget').input_value() == '1e', 'invalid cap text survives a redraw of the change form')
                page.get_by_role('button', name='Crea la modifica').click()
                expect(page.locator('#message')).to_contain_text('Il tetto di spesa deve essere un numero maggiore di zero.')
                check(not creates and page.locator('dialog.change-consent').count() == 0, 'an unparsable spending cap is refused before consent and before any request')
                page.locator('#message').evaluate('(node)=>{node.hidden=true;node.textContent="";}')
                page.fill('#change-budget', '-1')
                page.get_by_role('button', name='Crea la modifica').click()
                expect(page.locator('#message')).to_contain_text('Il tetto di spesa deve essere un numero maggiore di zero.')
                check(not creates and page.locator('dialog.change-consent').count() == 0, 'an invalid spending cap is refused before consent and before any request')
                page.fill('#change-budget', '0,5')
                page.get_by_role('button', name='Crea la modifica').click()
                dialog = page.locator('dialog.change-consent')
                expect(dialog).to_be_visible()
                text = dialog.inner_text()
                check(all(term in text for term in ('codex-fake 1.0', 'read-only', 'workspace-write', 'senza shell', 'acceptEdits', 'senza Bash', '.code-graph/worktrees/<id>')), 'consent shows clients, modes, folders and sandbox')
                dialog.get_by_role('button', name='Do il consenso per questa sessione').click()
                expect(page.locator('#change-flow h1')).to_have_text('Rinomina work in compute')
                check('Consenso dato' in page.locator('.change-consent-state').inner_text(), 'consent is recorded for the session')
                check(creates[-1].get('budget_usd') == 0.5 and 'tetto Claude 0.5 USD' in page.locator('#change-flow .section-head p').inner_text(), 'the Claude spending cap is sent with the change and shown')
                page.get_by_role('button', name='Prepara il piano').click()
                expect(page.locator('#change-flow .section-head p')).to_contain_text('Piano pronto', timeout=10000)
                for kind in ('touch', 'affected', 'test', 'hidden'):
                    check(page.locator('.change-map .change-chip.' + kind).count() >= 2, 'map colours ' + kind)
                expect(page.locator('#change-argv')).to_have_value('python3\n-m\nunittest\ndiscover\n-s\ntests')
                check(page.locator('.change-perimeter input[type=checkbox]').count() == 1, 'scope lists only the editable planned files')
                rule = page.evaluate('(cases)=>cases.map(([path])=>window.createCgChange.editable(path))', EDITABLE_PATHS)
                check(rule == [expected for _, expected in EDITABLE_PATHS], 'the dashboard path rule matches the shared table')
                refused = page.locator('.change-step-panel .warning', has_text='Percorsi non validi, esclusi dal perimetro').inner_text()
                check('../fuori.py' in refused and '/pkg/core.py' in refused, 'paths the engine refuses are listed as excluded, not as checkboxes')
                # Touch the scope so that this change keeps a draft of its own (F2: drafts must not leak to change B).
                page.locator('.change-perimeter input[type=checkbox]').uncheck()
                page.locator('.change-perimeter input[type=checkbox]').check()
                events = page.locator('#change-events')
                expect(events).to_contain_text(HOSTILE)
                check(page.locator('#change-events img').count() == 0 and page.title() != 'pwned', 'agent events are rendered as text')
                page.get_by_role('button', name='✅ Approva il piano').click()
                expect(page.locator('#change-flow .section-head p')).to_contain_text('Piano approvato')
                check(approvals[-1]['touch'] == ['pkg/core.py'], 'change A approves its own displayed scope, without the excluded paths')
                page.locator('.change-step', has_text='Pianifica').click()
                expect(page.locator('.change-step-panel pre')).to_contain_text('"python3","-m","unittest","discover","-s","tests"')
                page.locator('.change-step', has_text='Modifica').click()
                page.get_by_role('button', name='Avvia la modifica').click()
                expect(page.locator('#change-flow .section-head p')).to_contain_text('Modificata', timeout=10000)
                page.get_by_role('button', name='Verifica', exact=True).click()
                expect(page.locator('#change-flow .section-head p')).to_contain_text('Verificata', timeout=10000)
                page.locator('.change-step', has_text='Verifica').click()
                table = page.locator('.change-step-panel .change-table')
                expect(table).to_contain_text('superati')
                check('pkg/site.py' in page.locator('.change-step-panel').inner_text(), 'verification shows the file outside the scope')
                page.locator('.change-step', has_text='Rileggi').click()
                page.get_by_role('button', name='Chiedi la rilettura').click()
                expect(page.locator('#change-flow .section-head p')).to_contain_text('Riletta', timeout=10000)
                expect(page.locator('.change-verdict')).to_contain_text('accetta')
                check('Riletta' in page.locator('#change-flow .section-head select option:checked').inner_text(), 'change picker follows the current state')
                check(page.locator('#change-force').count() == 1 and 'Non salta i controlli Git' in page.locator('.change-step-panel').inner_text(), 'force reason is offered only with blockers and never as a Git bypass')
                page.fill('#change-force', 'Il lettore accetta il file in più.')
                page.locator('.change-map .change-chip.touch', has_text='work').first.click()
                page.check('#change-client-claude')
                page.locator('#change-ask-budget').press_sequentially('1e')
                page.fill('#change-question', 'Chi chiama work?')
                # F5b, codex-d431's path: consent given, 1e typed, theme change redraws the panel, then Ask.
                page.select_option('#theme', 'dark')
                page.select_option('#theme', 'light')
                page.check('#change-client-claude')
                check(page.locator('#change-ask-budget').input_value() == '1e', 'invalid session cap text survives a redraw of the agent panel')
                asked = len(asks)
                page.get_by_role('button', name='Chiedi', exact=True).click()
                expect(page.locator('#message')).to_contain_text('Il tetto di spesa deve essere un numero maggiore di zero.')
                check(len(asks) == asked, 'an unparsable session cap blocks the question to Claude, also after a redraw')
                page.fill('#change-ask-budget', '1e-12')
                page.fill('#change-question', 'Chi chiama work?')
                page.get_by_role('button', name='Chiedi', exact=True).click()
                expect(page.locator('.change-ask pre')).to_contain_text('work è chiamata da view', timeout=10000)
                check(asks[-1].get('client') == 'claude' and asks[-1].get('budget_usd') == 1e-12, 'a question to Claude carries the session spending cap, also in exponent notation')
                if args.screenshot:
                    page.screenshot(path=str(args.screenshot), full_page=False)
                    checks.append('screenshot ' + str(args.screenshot))
                page.select_option('#language', 'en')
                expect(section).to_be_visible()
                expect(page.locator('#change-flow .eyebrow')).to_contain_text('Change with an agent')
                verify_step = page.locator('.change-step').nth(4).inner_text()
                check('Verify' in verify_step and 'Verifies' not in verify_step, 'the English verify step reads Verify')
                check(page.locator('#change-force').input_value() == 'Il lettore accetta il file in più.', 'drafts survive a language change')
                page.select_option('#theme', 'dark')
                expect(section).to_be_visible()
                page.get_by_role('button', name='✅ Integrate').click()
                expect(page.locator('#change-flow .section-head p')).to_contain_text('Integrated', timeout=10000)
                # Change B: a new plan with another file. The approved scope is exactly the one displayed (F2).
                page.locator('#change-flow .section-head select').select_option('')
                page.fill('#change-intent', 'Update view')
                page.get_by_role('button', name='Create the change').click()
                page.get_by_role('button', name='Prepare the plan').click()
                expect(page.locator('#change-flow .section-head p')).to_contain_text('Plan ready', timeout=10000)
                checked = page.locator('.change-perimeter input[type=checkbox]:checked')
                check(checked.count() == 1 and checked.first.get_attribute('value') == 'pkg/site.py', 'change B shows its own scope, all selected')
                page.get_by_role('button', name='✅ Approve the plan').click()
                expect(page.locator('#change-flow .section-head p')).to_contain_text('Plan approved')
                check(approvals[-1]['touch'] == ['pkg/site.py'] and approvals[-1]['id'] != approvals[0]['id'], 'change B approves the displayed scope, not the draft of change A')
                page.click('#overview-button')
                expect(section).to_be_hidden()
                expect(page.locator('#overview')).to_be_visible()
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=10)
    check(not errors, 'no page or console errors: ' + '; '.join(errors))
    check(not external, 'no external requests: ' + ', '.join(external))
    print(json.dumps(dict(ok=True, checks=checks), ensure_ascii=False, indent=1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', type=Path)
    parser.add_argument('--browser')
    parser.add_argument('--server-python', default=str(REPO / '.venv/bin/python'))
    parser.add_argument('--screenshot', type=Path)
    parser.add_argument('--bootstrap-delay', type=float, default=0.0, help='seconds the test server waits before answering the bootstrap')
    args = parser.parse_args()
    if args.serve:
        serve(args.serve, args.bootstrap_delay)
    elif not args.browser:
        parser.error('--browser is required')
    else:
        drive(args)


if __name__ == '__main__':
    sys.exit(main())
