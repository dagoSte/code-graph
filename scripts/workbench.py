"""Loopback-only guided explorer. Assets are fixed; projects are never served as files."""
import argparse
from collections import OrderedDict
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import re
from pathlib import Path
import runpy
import secrets
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit, unquote
import webbrowser

QUERY = runpy.run_path(str(Path(__file__).with_name('project_data.py')))
ProjectData = QUERY['ProjectData']
StaleIndex = QUERY['StaleIndex']
RETRIEVAL_GUIDANCE = runpy.run_path(str(Path(__file__).with_name('agent_queries.py')))['RETRIEVAL_GUIDANCE']
ASSETS = Path(__file__).resolve().parents[1] / 'assets'
GUIDE_ROOT = ASSETS.parent / 'docs/guide'
GUIDE_FILES = frozenset(path.relative_to(GUIDE_ROOT).as_posix() for path in GUIDE_ROOT.rglob('*')
                        if path.is_file() and not path.is_symlink() and path.suffix in ('.md', '.py', '.toml', '.json', '.txt'))
CLIENTS = ('codex', 'claude')
CHANGE_ACTIONS = ('plan', 'approve', 'edit', 'verify', 'review', 'integrate', 'discard', 'cancel')
AGENT_ACTIONS = ('plan', 'edit', 'review')
FLOW_STATUS = {'not_found': 404, 'bad_state': 409, 'busy': 409, 'conflict': 409, 'dirty': 409, 'invalid': 400, 'not_git': 400}
ASK_LIMIT = 3
ASK_ACTIVE = ('starting', 'running')
ASK_TIMEOUT = 600
LARGE_BODY = 65536
SMALL_BODY = 8192


class Busy(ValueError):
    pass


class ConsentRequired(ValueError):
    code = 'consent_required'


class NotFound(ValueError):
    code = 'not_found'


class ConsentGate:
    """The only runner ChangeFlow and /api/ask see: every agent start re-checks the session consent
    under the same lock as a revocation, immediately before the real start."""

    def __init__(self, session):
        self.session = session

    def detect(self, *args, **kwargs):
        return self.session.runner.detect(*args, **kwargs)

    def start(self, client, **options):
        with self.session.lock:
            self.session.require_consent()
            return self.session.runner.start(client, **options)


def flow_error_status(error):
    """FlowError (§ C.8) is matched by its code, so the real class and test doubles map alike."""
    code = getattr(error, 'code', None)
    return (FLOW_STATUS[code], code) if isinstance(code, str) and code in FLOW_STATUS else (None, None)


def default_runner():
    module = runpy.run_path(str(Path(__file__).with_name('agents.py')))
    return argparse.Namespace(detect=module['detect'], start=module['start'])


def default_flow_factory(engine, root, runner):
    return runpy.run_path(str(Path(__file__).with_name('change_flow.py')))['ChangeFlow'](engine, str(root), runner=runner)


def bounded_text(value, name, limit, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise ValueError(f'{name}: text required, at most {limit} characters.')
    return value


def budget(value):
    """Optional Claude spending cap in USD (dec-cucitura-budget): a finite number > 0, or absent."""
    if value is None:
        return None
    # Range before isfinite: math.isfinite(10**400) raises OverflowError (aligned with ChangeFlow.create).
    if type(value) not in (int, float) or value <= 0 or value > sys.float_info.max or not math.isfinite(value):
        raise ValueError('budget_usd: a number greater than zero, in dollars.')
    return value


def text_list(value, name, limit=200, item=4000):
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > limit or not all(isinstance(entry, str) and len(entry) <= item for entry in value):
        raise ValueError(f'{name}: a list of texts is required.')
    return value


class Session:
    def __init__(self, engine, workspace, initial=None, runner=None, flow_factory=None):
        self.engine = engine
        self._runner = runner
        self.flow_factory = flow_factory or default_flow_factory
        self.gate = ConsentGate(self)
        self.flows = {}
        self.consent = False
        self.asks = OrderedDict()
        self.closed = False
        self.workspace = Path(workspace).expanduser().resolve(strict=True)
        if not self.workspace.is_dir():
            raise ValueError('Workspace must be a folder.')
        self.initial = str(initial or self.workspace)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.picker = threading.Lock()
        self.root = None
        self.generation = ''
        self.running = False
        self.status = dict(phase='idle', completed=0, total=0, file='')
        self.profile = None
        self.inventory = {}
        self.preparations = OrderedDict()

    def directory(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Enter a valid folder.')
        path = Path(value).expanduser()
        path = (self.workspace / path).resolve(strict=True) if not path.is_absolute() else path.resolve(strict=True)
        if not path.is_relative_to(self.workspace) or not path.is_dir():
            raise ValueError('Choose a folder inside the workspace: ' + str(self.workspace))
        return path

    def folders(self, value):
        path = self.directory(value or str(self.workspace))
        entries = []
        for item in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
            if item.name.startswith('.'):
                continue
            try:
                actual = item.resolve(strict=True)
                if item.is_dir() and actual.is_relative_to(self.workspace):
                    entries.append(dict(name=item.name, path=str(actual)))
            except (OSError, ValueError):
                continue
        return dict(path=str(path), parent=str(path.parent) if path != self.workspace else None, items=entries[:250], truncated=len(entries) > 250)

    def choose(self):
        if sys.platform != 'darwin':
            return dict(native=False)
        if not self.picker.acquire(blocking=False):
            raise Busy('The folder chooser is already open.')
        try:
            result = subprocess.run(['/usr/bin/osascript', '-e', 'POSIX path of (choose folder with prompt "Choose the project to analyze")'], capture_output=True, text=True, timeout=180)
            if result.returncode:
                return dict(cancelled=True)
            return dict(path=str(self.directory(result.stdout.strip())), native=True)
        finally:
            self.picker.release()

    def start(self, value):
        root = self.directory(value)
        if root in (Path('/'), Path.home(), Path('/Users'), Path('/Volumes')):
            raise ValueError('Choose a specific project folder, not the entire home folder or disk.')
        with self.lock:
            if self.running:
                raise Busy('An analysis is already running. Wait for it to finish.')
            self.running = True
            self.root = root
            self.generation = secrets.token_hex(12)
            self.profile = None
            self.status = dict(phase='scanning', completed=0, total=0, file='')
            generation = self.generation
        threading.Thread(target=self._index, args=(root,), daemon=True).start()
        return dict(generation=generation)

    def _index(self, root):
        def progress(value):
            with self.lock:
                self.status = value
        try:
            candidates = self.engine.checkout_files(str(root))
            if len(candidates) > 100000:
                raise ValueError('Folder too broad: select a project or smaller area.')
            ignore = self.engine.load_ignore(str(root))
            unsupported = excluded = 0
            for path in candidates:
                if any(part in self.engine.SKIP_DIRS for part in Path(path).parts[:-1]) or self.engine.ignored(path, ignore):
                    excluded += 1
                elif Path(path).suffix not in self.engine.LANG_BY_EXT:
                    unsupported += 1
            inventory = dict(discovered_files=len(candidates), unsupported_files=unsupported, excluded_files=excluded)
            self.engine.cmd_index(argparse.Namespace(root=str(root), full=False, progress=progress))
            with ProjectData(self.engine, root) as data:
                profile = data.overview()
            profile.update(inventory)
            with self.lock:
                self.profile, self.inventory = profile, inventory
                self.status = dict(phase='ready', completed=profile['files'], total=profile['files'], file='')
        except BaseException as error:
            with self.lock:
                self.status = dict(phase='error', message=str(error) or type(error).__name__, completed=0, total=0, file='')
        finally:
            with self.lock:
                self.running = False

    def remember(self, generation, preparation_id, baseline):
        with self.lock:
            root,_=self.project(generation)
            if baseline['project'] != QUERY['EVIDENCE']['digest'](str(root)):
                raise Busy('Project changed during preparation.')
            if len(json.dumps(baseline))>1000000:
                raise ValueError('Preparation exceeds 1 MB: narrow the references.')
            self.preparations[preparation_id]=baseline
            while len(self.preparations)>8:self.preparations.popitem(last=False)

    def prepared(self, preparation_id):
        with self.lock:
            if not isinstance(preparation_id,str) or preparation_id not in self.preparations:
                raise ValueError('Preparation unavailable: generate a new packet in this session.')
            return self.preparations[preparation_id]

    def snapshot(self):
        with self.lock:
            return dict(generation=self.generation, status=dict(self.status), project=self.profile)

    def project(self, generation):
        with self.lock:
            if generation != self.generation or self.running or self.status['phase'] != 'ready':
                raise Busy('Project changed or analysis is running: refresh the view.')
            return self.root, self.profile['revision']

    @property
    def runner(self):
        with self.lock:
            if self._runner is None:
                self._runner = default_runner()
            return self._runner

    def agents(self):
        return dict(clients=self.runner.detect(), consent=self.consent)

    def set_consent(self, value):
        if not isinstance(value, bool):
            raise ValueError('consent: true or false required.')
        with self.lock:
            self.consent = value
            return dict(consent=self.consent)

    def require_consent(self):
        with self.lock:
            if not self.consent:
                raise ConsentRequired('Session consent is required before starting an agent.')

    def flow(self, generation):
        root, _ = self.project(generation)
        with self.lock:
            if root not in self.flows:
                self.flows[root] = self.flow_factory(self.engine, root, self.gate)
            return self.flows[root]

    def change_action(self, generation, body):
        action, change_id = body.get('action'), body.get('id')
        if action not in CHANGE_ACTIONS:
            raise ValueError('Invalid action.')
        bounded_text(change_id, 'id', 200)
        flow = self.flow(generation)
        if action in AGENT_ACTIONS:
            # Early answer for the UI; the agent itself starts later through self.gate, which re-checks.
            self.require_consent()
        if action == 'approve':
            argv = body.get('test_argv')
            if not isinstance(argv, list) or not all(isinstance(item, str) and item and len(item) <= 4000 for item in argv) or len(argv) > 200:
                raise ValueError('test_argv: argument list required (may be empty with a reason).')
            reason = bounded_text(body.get('no_tests_reason'), 'no_tests_reason', 2000, required=not argv)
            touch = body.get('touch')
            if touch is not None and (not isinstance(touch, list) or len(touch) > 500 or len(json.dumps(touch)) > 60000):
                raise ValueError('touch: a list is required.')
            return flow.approve(change_id, argv, touch=touch, out_of_scope=text_list(body.get('out_of_scope'), 'out_of_scope'), no_tests_reason=reason)
        if action == 'edit':
            return flow.edit(change_id, feedback=bounded_text(body.get('feedback'), 'feedback', 20000, required=False))
        if action == 'review':
            return flow.review(change_id, same_client_reason=bounded_text(body.get('same_client_reason'), 'same_client_reason', 2000, required=False))
        if action == 'integrate':
            mode = body.get('mode', 'branch')
            if mode not in ('branch', 'merge'):
                raise ValueError('mode: branch or merge.')
            return flow.integrate(change_id, mode=mode, force_reason=bounded_text(body.get('force_reason'), 'force_reason', 2000, required=False))
        return getattr(flow, action)(change_id)

    def ask(self, generation, body, data=None):
        client, question, symbol = body.get('client'), body.get('question'), body.get('symbol')
        if client not in CLIENTS:
            raise ValueError('client: codex or claude.')
        bounded_text(question, 'question', 8000)
        if symbol is not None and not isinstance(symbol, (str, dict)):
            raise ValueError('symbol: text or a reference is required.')
        cap = budget(body.get('budget_usd'))
        root, _ = self.project(generation)
        prompt = ['Answer the project question in ' + str(root) + '. Use code-graph tools (mcp__code-graph__*) to find symbols, calls and impact, and cite file:line. Read only: do not edit files or execute project code.']
        prompt.append(RETRIEVAL_GUIDANCE)
        if symbol:
            prompt.append('Selected symbol: ' + (json.dumps(symbol, ensure_ascii=False) if isinstance(symbol, dict) else symbol)[:2000])
        prompt.append('Question: ' + question)
        with self.lock:
            if self.closed:
                raise Busy('The service is shutting down.')
            active = [run for run in self.asks.values() if getattr(run, 'status', None) in ASK_ACTIVE]
            if len(active) >= ASK_LIMIT:
                raise Busy(f'Already {ASK_LIMIT} questions running: wait for one to finish.')
            # Codex has no spending cap: agents.start refuses budget_usd for it, so only Claude receives it.
            extra = dict(budget_usd=cap) if cap is not None and client == 'claude' else {}
            run = self.gate.start(client, mode='read', cwd=str(root), prompt='\n\n'.join(prompt), root=str(root), timeout=ASK_TIMEOUT, **extra)
            self.asks[run.id] = run
            for key in [key for key, old in self.asks.items() if getattr(old, 'status', None) not in ASK_ACTIVE][:max(0, len(self.asks) - 20)]:
                del self.asks[key]
        return dict(run=run.id, client=run.client, mode=run.mode, cwd=run.cwd, status=run.status)

    def ask_events(self, run_id, since):
        with self.lock:
            run = self.asks.get(run_id) if isinstance(run_id, str) else None
        if run is None:
            raise NotFound('Run missing from this session.')
        status = run.status
        return dict(run=run.id, client=run.client, status=status, events=run.events(since),
                    result=run.result if status not in ASK_ACTIVE else None, usage=run.usage if status not in ASK_ACTIVE else None)

    def close(self):
        with self.lock:
            self.closed = True
            runs = [run for run in self.asks.values() if getattr(run, 'status', None) in ASK_ACTIVE]
        for run in runs:
            with contextlib.suppress(Exception):
                run.cancel()


def create_server(engine, workspace, port=0, initial=None, runner=None, flow_factory=None):
    """runner: object with detect()/start() as in agents.py; flow_factory(engine, root, runner) -> ChangeFlow.

    The factory receives the session ConsentGate, never the raw runner: it must pass it on to ChangeFlow."""
    session = Session(engine, workspace, initial, runner=runner, flow_factory=flow_factory)
    # Blob snapshots inherit the creator policy: grant only the exact trusted graph script/style.
    graph_policy = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', engine.render_graph_html({})).group(1)
    graph_script = re.search(r'script-src ([^;]+)', graph_policy).group(1)
    graph_style = re.search(r'style-src ([^;]+)', graph_policy).group(1)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'code-graph-local'

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass

        def send(self, code, content, kind='application/json; charset=utf-8', extra=None):
            raw = content if isinstance(content, bytes) else content.encode('utf-8')
            self.send_response(code)
            for key, value in {'Content-Type': kind, 'Content-Length': str(len(raw)), 'Cache-Control': 'no-store',
                               'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer',
                               'Content-Security-Policy': f"default-src 'none'; script-src 'self' {graph_script}; style-src 'self' {graph_style}; connect-src 'self'; frame-src blob:; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'", **(extra or {})}.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)

        def result(self, value, code=200):
            self.send(code, json.dumps(value, ensure_ascii=True))

        def allowed(self, api=False):
            origin = 'http://127.0.0.1:' + str(self.server.server_port)
            if self.headers.get('Host') != origin[7:]:
                self.result(dict(error='Host not allowed.'), 403)
                return False
            if self.headers.get('Origin') not in (None, origin):
                self.result(dict(error='Origin not allowed.'), 403)
                return False
            if api and not secrets.compare_digest(self.headers.get('X-CG-Token', '').encode('utf-8'), session.token.encode('ascii')):
                self.result(dict(error='Unauthorized local session: open the URL printed in the terminal.'), 403)
                return False
            return True

        def do_GET(self):
            parsed = urlsplit(self.path)
            if not self.allowed(parsed.path.startswith('/api/')):
                return
            assets = {'/': ('workbench.html', 'text/html; charset=utf-8'), '/workbench.js': ('workbench.js', 'text/javascript; charset=utf-8'), '/workbench-i18n.js': ('workbench-i18n.js', 'text/javascript; charset=utf-8'), '/workbench-panels.js': ('workbench-panels.js', 'text/javascript; charset=utf-8'), '/workbench-features.js': ('workbench-features.js', 'text/javascript; charset=utf-8'), '/workbench-flows.js': ('workbench-flows.js', 'text/javascript; charset=utf-8'), '/workbench-icon.svg': ('workbench-icon.svg', 'image/svg+xml'), '/usage-guide.css': ('usage-guide.css', 'text/css; charset=utf-8'), '/workbench-guide.js': ('workbench-guide.js', 'text/javascript; charset=utf-8'), '/workbench-change.js': ('workbench-change.js', 'text/javascript; charset=utf-8'), '/workbench.css': ('workbench.css', 'text/css; charset=utf-8')}
            if parsed.path in assets:
                name, kind = assets[parsed.path]
                self.send(200, (ASSETS / name).read_bytes(), kind)
                return
            if parsed.path.startswith('/guide/'):
                relative=unquote(parsed.path[len('/guide/'):]) or 'README.md'
                try:
                    if relative not in GUIDE_FILES:
                        raise ValueError('Unknown guide page.')
                    page=engine.local_path(ASSETS.parent,'docs/guide/'+relative)
                    renderer=runpy.run_path(str(Path(__file__).with_name('usage_guide.py')))['render']
                    self.send(200,renderer(page,GUIDE_ROOT),'text/html; charset=utf-8')
                except (OSError,ValueError):
                    self.result(dict(error='Guide page unavailable.'),404)
                return
            query = parse_qs(parsed.query)
            get = lambda name, default='': query.get(name, [default])[0]
            if parsed.path in ('/api/agents', '/api/changes', '/api/change', '/api/change/events', '/api/ask/events'):
                self.change_get(parsed.path, get)
                return
            try:
                if parsed.path == '/api/bootstrap':
                    snapshot=session.snapshot()
                    self.result(dict(workspace=str(session.workspace), initial=session.initial, native_picker=sys.platform == 'darwin', engine=engine.VERSION, generation=snapshot['generation'], phase=snapshot['status']['phase']))
                elif parsed.path == '/api/directories':
                    self.result(session.folders(get('path', str(session.workspace))))
                elif parsed.path == '/api/status':
                    self.result(session.snapshot())
                elif parsed.path in ('/api/symbols', '/api/search', '/api/symbol', '/api/impact', '/api/resources', '/api/graph', '/api/efficiency', '/api/architecture', '/api/context', '/api/features', '/api/feature', '/api/flows'):
                    generation = get('generation')
                    root, revision = session.project(generation)
                    confidence = get('confidence', 'medium')
                    if confidence not in engine.CONF_RANK:
                        raise ValueError('Invalid confidence.')
                    area = get('area', '.')
                    with ProjectData(engine, root, expected_revision=revision) as data:
                        if parsed.path == '/api/search':
                            if engine.analysis_inputs(data.root)[2] != data.meta.get('analysis_fingerprint'):
                                raise StaleIndex('Engine changed: refresh the index before searching.')
                            if data.meta.get('search_status') != 'ready':
                                self.result(dict(error='FTS5 search unavailable: use symbol search or refresh the index with SQLite FTS5.', code='search_unavailable'), 503)
                                return
                            value = engine.search_module()['search'](data, get('q'), mode=get('mode', 'hybrid'),
                                limit=int(get('limit', '10')), depth=int(get('depth', '2')),
                                confidence=confidence, max_chars=int(get('max_chars', '9000')))
                        elif parsed.path == '/api/symbols':
                            q, offset = get('q'), int(get('offset', '0'))
                            if len(q) > 200 or not 0 <= offset <= 100000:
                                raise ValueError('Search outside the limit.')
                            value = data.symbols(area, q, offset)
                        elif parsed.path == '/api/symbol':
                            fields = get('fields').split(',') if get('fields') else None
                            value = data.detail(int(get('id')), confidence, fields, int(get('limit', '80')), get('cursor') or None)
                        elif parsed.path == '/api/impact':
                            depth = int(get('depth', '3'))
                            if depth not in range(1, 6):
                                raise ValueError('Depth outside the limit.')
                            value = data.impact(int(get('id')), confidence, depth, compact=get('compact') == '1', limit=int(get('limit', '200')), cursor=get('cursor') or None, test_cursor=get('test_cursor') or None)
                        elif parsed.path == '/api/flows':
                            if engine.analysis_inputs(data.root)[2] != data.meta.get('analysis_fingerprint'):
                                raise StaleIndex('Engine changed: refresh the index before reading the functional map.')
                            value=runpy.run_path(str(Path(__file__).with_name('functional_map.py')))['build'](data,get('q'),int(get('limit','25')),confidence,int(get('max_chars','12000')))
                        elif parsed.path == '/api/features':
                            value=data.feature_catalog(get('q'))
                        elif parsed.path == '/api/feature':
                            target=get('id')
                            if get('symbol'):
                                try:target='symbol:'+str(engine.resolve_one(data.db,get('symbol')))
                                except SystemExit as error:raise ValueError(str(error)) from error
                            value=data.feature(target,confidence,int(get('depth','2')))
                            feature_graph=value.pop('graph')
                            feature_graph.update(language=get('language','it') if get('language','it') in ('it','en') else 'it',theme=get('theme','light') if get('theme','light') in ('light','dark') else 'light',bridge_generation=generation,bridge_view=get('view'))
                            value['graph_view']=dict(html=engine.render_graph_html(feature_graph),node_ids=[node['id'] for node in feature_graph['nodes']],truncated=feature_graph['selection_truncated'])
                        elif parsed.path == '/api/context':
                            maximum=int(get('max_chars','4000'))
                            if not 64 <= maximum <= 100000:raise ValueError('Budget outside the range 64..100000.')
                            profile=get('profile','change')
                            if profile not in ('understand','change','debug','review'):raise ValueError('Invalid profile.')
                            text=engine.agent_module()['context'](data,int(get('id')),maximum,profile,confidence)
                            value=dict(text=text,max_chars=maximum,actual_chars=len(text),estimated_tokens=(len(text)+3)//4,measured_tokens=None)
                        elif parsed.path == '/api/architecture':
                            value = data.architecture()
                        elif parsed.path == '/api/efficiency':
                            sid = int(get('id'))
                            full = data.detail(sid, confidence)
                            selected = data.detail(sid, confidence, ['source', 'references'])
                            serialize = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                            before, after = serialize(full), serialize(selected)
                            value = dict(scope='source_tab_vs_legacy_all_fields', baseline_chars=len(before), actual_chars=len(after),
                                         baseline_bytes=len(before.encode()), actual_bytes=len(after.encode()), saved_chars=len(before)-len(after),
                                         estimated_tokens=(len(after)+3)//4, baseline_estimated_tokens=(len(before)+3)//4,
                                         estimator='unicode_characters/4', measured_tokens=None,
                                         revision=revision, evidence_preserved=full['source'] == selected['source'] and full['references'] == selected['references'])
                        elif parsed.path == '/api/resources':
                            value = data.resources(area, limit=int(get('limit', '200')), cursor=get('cursor') or None)
                        else:
                            focus = int(get('id')) if get('id') else None
                            value = data.feature(get('feature'),confidence,int(get('depth','2')))['graph'] if get('feature') else data.graph(area, focus, confidence)
                            value['language'] = get('language','it') if get('language','it') in ('it','en') else 'it'
                            value['theme'] = get('theme','light') if get('theme','light') in ('light','dark') else 'light'
                            value['bridge_generation'] = generation
                            value['bridge_view'] = get('view')
                            value['embedded'] = get('standalone') != '1'
                            self.result(dict(generation=generation, data=dict(html=engine.render_graph_html(value), node_ids=[node['id'] for node in value['nodes']])))
                            return
                    self.result(dict(generation=generation, data=value))
                else:
                    self.result(dict(error='Path unavailable.'), 404)
            except StaleIndex as error:
                self.result(dict(error=str(error), code='stale_index'), 409)
            except Busy as error:
                self.result(dict(error=str(error), code='busy'), 409)
            except (ValueError, OSError, KeyError, json.JSONDecodeError, engine.sqlite3.Error, SystemExit) as error:
                self.result(dict(error=str(error) or 'Invalid request.'), 400)

        def flow_result(self, call, code=200):
            """Change, agent and ask endpoints: FlowError codes map to HTTP as in contract § D."""
            try:
                self.result(call(), code)
            except ConsentRequired as error:
                self.result(dict(error=str(error), code=error.code), 403)
            except StaleIndex as error:
                self.result(dict(error=str(error), code='stale_index'), 409)
            except Busy as error:
                self.result(dict(error=str(error), code='busy'), 409)
            except (ValueError, OSError) as error:
                status, code = flow_error_status(error)
                self.result(dict(error=str(error) or 'Invalid request.', code=code or 'invalid'), status or 400)
            except Exception as error:
                self.result(dict(error='Internal error: ' + type(error).__name__, code='internal'), 500)

        def change_get(self, path, get):
            generation = get('generation')
            def since():
                value = int(get('since', '0') or '0')
                if not 0 <= value <= 10**9:
                    raise ValueError('since outside the limit.')
                return value
            def call():
                if path == '/api/agents':
                    return session.agents()
                if path == '/api/ask/events':
                    return session.ask_events(get('run'), since())
                flow = session.flow(generation)
                if path == '/api/changes':
                    return dict(generation=generation, data=flow.list())
                change_id = bounded_text(get('id'), 'id', 200)
                if path == '/api/change':
                    return dict(generation=generation, data=flow.get(change_id))
                return dict(generation=generation, data=flow.events(change_id, since=since()))
            self.flow_result(call)

        def change_post(self, path, body):
            generation = body.get('generation', '')
            def call():
                if path == '/api/agents/consent':
                    return session.set_consent(body.get('consent'))
                if path == '/api/ask':
                    return session.ask(generation, body)
                if path == '/api/changes':
                    intent = bounded_text(body.get('intent'), 'intent', 20000)
                    author, reviewer = body.get('author'), body.get('reviewer')
                    if author not in CLIENTS or reviewer not in CLIENTS:
                        raise ValueError('author and reviewer: codex or claude.')
                    cap = budget(body.get('budget_usd'))
                    session.require_consent()
                    extra = dict(budget_usd=cap) if cap is not None else {}
                    return dict(generation=generation, data=session.flow(generation).create(intent, author, reviewer, allow_dirty=body.get('allow_dirty') is True, **extra))
                return dict(generation=generation, data=session.change_action(generation, body))
            self.flow_result(call, 202 if path in ('/api/ask', '/api/changes') else 200)

        def do_POST(self):
            if not self.allowed(True):
                return
            large = self.path in ('/api/agents/consent', '/api/changes', '/api/change/action', '/api/ask')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= (LARGE_BODY if large else SMALL_BODY):
                    self.result(dict(error='Request size outside the limit.'), 413)
                    return
                if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    self.result(dict(error='JSON format required.'), 415)
                    return
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('JSON object required.')
                if large:
                    self.change_post(self.path, body)
                elif self.path == '/api/open':
                    self.result(session.start(body.get('path', '')), 202)
                elif self.path in ('/api/prepare','/api/feature-review'):
                    generation=body.get('generation','')
                    root,revision=session.project(generation)
                    features=runpy.run_path(str(Path(__file__).with_name('features.py')))
                    with ProjectData(engine,root,expected_revision=revision) as data:
                        if self.path=='/api/prepare':
                            preparation_id=secrets.token_hex(16)
                            value,baseline=features['prepare'](data,body.get('feature'),body.get('intent',''),body.get('max_chars',12000),body.get('confidence','medium'),body.get('depth',2),preparation_id,body.get('verification_symbols',[]))
                            if value.get('status')!='budget_insufficient':session.remember(generation,preparation_id,baseline)
                        else:
                            baseline=session.prepared(body.get('preparation_id'))
                            value=features['compare'](data,baseline)
                    session.project(generation)
                    self.result(dict(generation=generation,data=value))
                elif self.path == '/api/choose':
                    self.result(session.choose())
                else:
                    self.result(dict(error='Path unavailable.'), 404)
            except StaleIndex as error:
                self.result(dict(error=str(error),code='stale_index'),409)
            except Busy as error:
                self.result(dict(error=str(error)), 409)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                self.result(dict(error=str(error)), 400)

        def do_OPTIONS(self):
            self.result(dict(error='Method unavailable.'), 405)

    class Server(ThreadingHTTPServer):
        # The page loads eight assets at once; the default listen backlog of 5 resets connections under load.
        request_queue_size = 128

        def server_close(self):
            try:
                session.close()
            finally:
                super().server_close()

    server = Server(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.session = session
    return server


def launch(args, engine):
    if not 0 <= args.port <= 65535:
        raise SystemExit('Invalid port.')
    server = create_server(engine, args.workspace, args.port, args.root)
    url = 'http://127.0.0.1:' + str(server.server_port) + '/#' + server.session.token
    print('Local explorer: ' + url, flush=True)
    print('Choose a project folder. Ctrl+C closes the service.', flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
