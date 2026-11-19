"""Local, explicit change workflow. Project code runs only through approved test argv."""
import argparse
import contextlib
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import secrets
import subprocess
import sys
import tempfile
import threading
import time


ACTIVE = {'planning', 'baseline', 'editing', 'verifying', 'reviewing'}
TERMINAL = {'integrated', 'discarded'}
STABLE = {'draft', 'planned', 'approved', 'edited', 'verified', 'reviewed'} | TERMINAL
CLIENTS = ('codex', 'claude')
TEST_TIMEOUT = 600
AGENT_TIMEOUT = 900
_PROCESS_NONCE = secrets.token_hex(16)
_TESTS = runpy.run_path(str(Path(__file__).with_name('change_tests.py')))
_ANALYSIS = runpy.run_path(str(Path(__file__).with_name('change_analysis.py')))
_ProjectData = runpy.run_path(str(Path(__file__).with_name('project_data.py')))['ProjectData']
_RETRIEVAL_GUIDANCE = runpy.run_path(str(Path(__file__).with_name('agent_queries.py')))['RETRIEVAL_GUIDANCE']


def _record_schema(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


_TEXT = {'type': 'string'}
_TEXTS = {'type': 'array', 'items': _TEXT}
_LOCATION = dict(path=_TEXT, symbol=_TEXT, line={'type': 'integer'}, why=_TEXT)
PLAN_SCHEMA = _record_schema(dict(
    summary=_TEXT, touch=dict(type='array', items=_record_schema(_LOCATION)),
    affected=dict(type='array', items=_record_schema(dict(_LOCATION, confidence={'type': 'string', 'enum': ['high', 'medium', 'low']}))),
    tests=dict(type='array', items=_record_schema(dict(path=_TEXT, name=_TEXT, why=_TEXT))),
    out_of_scope=_TEXTS, risks=_TEXTS, unknowns=_TEXTS, test_command=_TEXTS))
REVIEW_SCHEMA = _record_schema(dict(
    verdict={'type': 'string', 'enum': ['accept', 'changes', 'reject']}, summary=_TEXT,
    findings=dict(type='array', items=_record_schema(dict(
        severity={'type': 'string', 'enum': ['grave', 'minor']}, path=_TEXT,
        line={'type': 'integer'}, text=_TEXT)))))


# Tool-neutral phase notes: they apply whatever evidence tools the agent has.
_PHASE_NOTES = {
    'edit': ('Edit phase: the approved plan already names the files, affected symbols and tests. Do not repeat '
             'discovery; read only the code you change, plus direct callers if a signature changes.\n\n'),
    'review': ('Review phase: check the diff against the intent and the approved plan; read the changed code '
               'and only what it touches.\n\n'),
}


class FlowError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class _Cancelled(Exception):
    pass


def _validate(value, schema):
    """Validate the small, closed schemas at the injected runner boundary as well."""
    kind = schema['type']
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'integer': type(value) is int}[kind]
    if not valid or ('enum' in schema and value not in schema['enum']):
        raise FlowError('invalid', 'Agent result does not match the requested schema.')
    if kind == 'object':
        if set(value) != set(schema['properties']):
            raise FlowError('invalid', 'Agent result has missing or unexpected fields.')
        for key, field in schema['properties'].items():
            _validate(value[key], field)
    elif kind == 'array':
        for item in value:
            _validate(item, schema['items'])


class ChangeFlow:
    def __init__(self, engine, project_root, runner=None, clock=None):
        self.engine = engine
        self.root = Path(project_root).resolve()
        self.runner = runner
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self):
        value = self.clock()
        if isinstance(value, (int, float)):
            value = datetime.fromtimestamp(value, timezone.utc)
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def _path(self, relative):
        try:
            return self.engine.local_path(self.root, relative)
        except (ValueError, OSError) as error:
            raise FlowError('invalid', str(error)) from error

    def _id(self, value):
        if not isinstance(value, str) or not re.fullmatch(r'c-\d{8}-\d{6}-[a-f0-9]{4}', value):
            raise FlowError('invalid', 'Invalid change id.')
        return value

    def _archive(self, cid, name=''):
        return self._path(f'.code-graph/changes/{self._id(cid)}/' + name)

    def _worktree(self, cid):
        return self._path(f'.code-graph/worktrees/{self._id(cid)}')

    @contextlib.contextmanager
    def _locked(self):
        folder = self._path('.code-graph/changes')
        folder.mkdir(parents=True, exist_ok=True)
        ignore = self._path('.code-graph/.gitignore')
        if not ignore.exists():
            ignore.write_text('*\n', encoding='utf-8')
        with self._path('.code-graph/changes/.lock').open('a+b') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _git(self, root, *args, check=True):
        # Do not inherit a caller's Git redirections or execute checkout hooks.
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        argv = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                '-c', 'commit.gpgSign=false', '-c', 'core.quotepath=false', '-C', str(root), *args]
        try:
            result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=env, shell=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise FlowError('invalid', str(error)) from error
        if check and result.returncode:
            raise FlowError('invalid', result.stderr.decode('utf-8', 'replace')[-2000:])
        return result

    def _git_text(self, root, *args):
        return self._git(root, *args).stdout.decode('utf-8', 'replace').strip()

    def _load(self, cid):
        path = self._archive(cid, 'change.json')
        if not path.is_file():
            raise FlowError('not_found', 'Change not found: ' + cid)
        try:
            if path.stat().st_size > 5_000_000:
                raise ValueError('Change record exceeds 5 MB.')
            record = json.loads(path.read_text(encoding='utf-8'))
            valid = (record['format'] == 'cg-change/1' and record['id'] == cid
                     and record['project'] == str(self.root) and record['branch'] == 'cg/change-' + cid
                     and record['worktree'] == str(self._worktree(cid))
                     and record['state'] in STABLE | ACTIVE
                     and re.fullmatch(r'[a-f0-9]{40,64}', record['base']['commit']))
            if not valid:
                raise ValueError('Invalid change record or ownership.')
            if record['state'] in ACTIVE and not self._worker_alive(record):
                previous = record.get('step', {}).get('previous')
                if previous not in STABLE - TERMINAL:
                    previous = next(entry['state'] for entry in reversed(record['history'])
                                    if entry['state'] in STABLE - TERMINAL)
                note = 'step interrupted: the process ended'
                record['error'] = dict(code='interrupted', message=note)
                self._archive(cid, 'cancel').unlink(missing_ok=True)
                self._state(record, previous, note)
            return record
        except (KeyError, TypeError, ValueError) as error:
            raise FlowError('invalid', str(error)) from error

    def _worker_alive(self, record):
        # The worker holds this lease until its final state is saved. Unlike a PID
        # probe, it also detects a dead thread, process restart and reused PID.
        with self._archive(record['id'], 'worker.json').open('a+') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lease.seek(0)
                return json.load(lease) == record.get('step', {}).get('owner')
            return False

    def _save(self, record):
        record['updated'] = self._now()
        path = self._archive(record['id'], 'change.json')
        raw = json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8')
        if len(raw) > 5_000_000:
            raise FlowError('invalid', 'Change record exceeds 5 MB.')
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _event(self, record, kind, text, data=None):
        # Called while holding the project lock; seq survives process restarts.
        path = self._archive(record['id'], 'events.jsonl')
        seq = 0
        if path.exists():
            with path.open('rb') as handle:
                end = path.stat().st_size
                start = end
                tail = b''
                while start > 0:
                    size = min(8192, start)
                    start -= size
                    handle.seek(start)
                    tail = handle.read(size) + tail
                    lines = tail.rstrip(b'\n').split(b'\n')
                    if len(lines) > 1 or start == 0:
                        seq = json.loads(lines[-1])['seq']
                        break
        event = dict(v=1, change=record['id'], seq=seq + 1, t=self._now(), kind=kind,
                     text=str(text)[:4000], data=data or {})
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + '\n')

    def _state(self, record, state, note='', by='engine'):
        record['state'] = state
        if state in STABLE:
            record.pop('step', None)
        record['history'].append(dict(state=state, t=self._now(), by=by, note=note))
        self._save(record)
        self._event(record, 'state', note or state, dict(state=state))

    def _require(self, record, allowed):
        if record['state'] in ACTIVE:
            raise FlowError('busy', 'A step is already running for this change.')
        if record['state'] not in allowed:
            raise FlowError('bad_state', 'Action is not allowed in state ' + record['state'])

    def _owned_checkout(self, record):
        worktree = self._worktree(record['id'])
        if self._git_text(worktree, 'rev-parse', '--show-toplevel') != str(worktree):
            raise FlowError('invalid', 'Worktree ownership changed.')
        if self._git_text(worktree, 'symbolic-ref', '--short', 'HEAD') != record['branch']:
            raise FlowError('invalid', 'Worktree branch changed.')
        return worktree

    def _relative(self, path):
        if not isinstance(path, str) or not path or '\x00' in path or '\\' in path:
            raise FlowError('invalid', 'Expected a project-relative file path.')
        parts = PurePosixPath(path).parts
        if path.startswith('/') or '..' in parts or not parts or parts[0] in ('.git', '.code-graph'):
            raise FlowError('invalid', 'Path is outside the editable project: ' + path)
        return str(PurePosixPath(path))

    def create(self, intent, author, reviewer, allow_dirty=False, budget_usd=None):
        if not isinstance(intent, str) or not intent.strip() or len(intent) > 32000:
            raise FlowError('invalid', 'A nonempty intent of at most 32000 characters is required.')
        if author not in CLIENTS or reviewer not in CLIENTS:
            raise FlowError('invalid', 'Choose codex or claude.')
        if budget_usd is not None and (type(budget_usd) not in (int, float) or budget_usd <= 0
                                      or budget_usd > sys.float_info.max or not math.isfinite(budget_usd)):
            raise FlowError('invalid', 'budget_usd must be a finite number greater than zero, or null.')
        repo = self._git(self.root, 'rev-parse', '--show-toplevel', check=False)
        if repo.returncode or repo.stdout.decode().strip() != str(self.root):
            raise FlowError('not_git', 'Choose the root of a Git working tree.')
        with self._locked():
            dirty = bool(self._git_text(self.root, 'status', '--porcelain', '--untracked-files=all'))
            if dirty and not allow_dirty:
                raise FlowError('dirty', 'Commit or save the checkout changes before creating a change.')
            base = dict(branch=self._git_text(self.root, 'symbolic-ref', '--short', 'HEAD'),
                        commit=self._git_text(self.root, 'rev-parse', 'HEAD'))
            stamp = datetime.fromisoformat(self._now().replace('Z', '+00:00')).strftime('%Y%m%d-%H%M%S')
            while True:
                cid = 'c-' + stamp + '-' + secrets.token_hex(2)
                if not self._archive(cid).exists() and not self._worktree(cid).exists():
                    break
            branch = 'cg/change-' + cid
            worktree = self._worktree(cid)
            worktree.parent.mkdir(parents=True, exist_ok=True)
            # Refuse tracked state: generated state must never become a source commit.
            if self._git_text(self.root, 'ls-files', '--', '.code-graph'):
                raise FlowError('invalid', 'The .code-graph state directory must be untracked.')
            self._git(self.root, 'worktree', 'add', '-b', branch, str(worktree), base['commit'])
            self._archive(cid).mkdir()
            record = dict(format='cg-change/1', id=cid, intent=intent, project=str(self.root), base=base,
                          branch=branch, worktree=str(worktree), author=author, reviewer=reviewer,
                          budget_usd=budget_usd, state='draft', round=0, created=self._now(), updated=self._now(), history=[],
                          plan=None, approved=None, tests=dict(baseline=None, after=None), edit=None,
                          verify=None, review=None, integration=None, error=None)
            self._state(record, 'draft', by='user')
            if dirty:
                self._event(record, 'warning', 'Dirty checkout allowed; the worktree starts from committed HEAD only.')
            return record

    def list(self):
        with self._locked():
            records = [self._load(path.name) for path in self._path('.code-graph/changes').iterdir()
                       if re.fullmatch(r'c-\d{8}-\d{6}-[a-f0-9]{4}', path.name)]
            fields = ('id', 'intent', 'state', 'updated', 'author', 'reviewer')
            return [{key: record[key] for key in fields} for record in sorted(records, key=lambda r: r['updated'], reverse=True)]

    def get(self, change_id):
        with self._locked():
            return self._load(change_id)

    def events(self, change_id, since=0, limit=500):
        if type(since) is not int or since < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise FlowError('invalid', 'Invalid event cursor or limit (1–500).')
        with self._locked():
            self._load(change_id)
            found = []
            with self._archive(change_id, 'events.jsonl').open(encoding='utf-8') as handle:
                for line in handle:
                    event = json.loads(line)
                    if event['seq'] > since:
                        found.append(event)
                    if len(found) == limit:
                        break
            return dict(events=found, next=found[-1]['seq'] if found else since)

    def _cancelled(self, cid):
        return self._archive(cid, 'cancel').exists()

    def _check_cancel(self, cid):
        if self._cancelled(cid):
            raise _Cancelled()

    def _start(self, cid, allowed, active, operation, prepare=None):
        with self._locked():
            record = self._load(cid)
            self._require(record, allowed)
            self._owned_checkout(record)
            if active in ('editing', 'baseline'):
                for path in self._path('.code-graph/changes').glob('c-*'):
                    other = self._load(path.name)
                    if other['state'] in ('editing', 'baseline'):
                        raise FlowError('busy', 'Another change is editing this project.')
            if prepare:
                prepare(record)
            previous = record['state']
            self._archive(cid, 'cancel').unlink(missing_ok=True)
            record['error'] = None
            lease = self._archive(cid, 'worker.json').open('a+')
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                owner = dict(pid=os.getpid(), nonce=_PROCESS_NONCE)
                lease.seek(0)
                lease.truncate()
                json.dump(owner, lease)
                lease.flush()
                os.fsync(lease.fileno())
                record['step'] = dict(previous=previous, owner=owner)
                self._state(record, active)
                initial = json.loads(json.dumps(record))
            except BaseException:
                lease.close()
                raise

            def work():
                try:
                    updates, final = operation(record)
                    with self._locked():
                        self._check_cancel(cid)
                        current = self._load(cid)
                        current.update(updates)
                        self._state(current, final)
                        lease.close()
                except (_Cancelled, _TESTS['TestCancelled']):
                    with self._locked():
                        current = self._load(cid)
                        current['error'] = None
                        self._state(current, previous, 'Step cancelled; inspect any worktree changes before retrying.')
                        lease.close()
                except (Exception, SystemExit) as error:
                    with self._locked():
                        current = self._load(cid)
                        current['error'] = dict(code=getattr(error, 'code', 'invalid'), message=str(error)[:4000])
                        if not isinstance(current['error']['code'], str):
                            current['error']['code'] = 'invalid'
                        self._state(current, previous, 'Step failed.')
                        self._event(current, 'error', str(error), current['error'])
                        lease.close()
                finally:
                    lease.close()
            try:
                threading.Thread(target=work, name='cg-change-' + cid, daemon=True).start()
            except BaseException:
                lease.close()
                raise
            return initial

    def _agent(self, record, client, mode, prompt, schema=None, phase='plan'):
        cid = record['id']
        self._check_cancel(cid)
        self._analysis_command(record, 'index')
        self._check_cancel(cid)
        prompt = _RETRIEVAL_GUIDANCE + '\n\n' + _PHASE_NOTES.get(phase, '') + prompt
        runner = self.runner
        if runner is None:
            module = runpy.run_path(str(Path(__file__).with_name('agents.py')))
            start = module['start']
        else:
            start = runner.start
        log_dir = self._archive(cid, 'runs/' + secrets.token_hex(8))
        log_dir.mkdir(parents=True)
        run = start(client, mode=mode, cwd=record['worktree'], root=record['worktree'], prompt=prompt,
                    output_schema=schema, timeout=AGENT_TIMEOUT, log_dir=str(log_dir),
                    **({'budget_usd': record.get('budget_usd')} if client == 'claude' else {}))
        seen = 0
        deadline = time.monotonic() + AGENT_TIMEOUT
        def forward():
            nonlocal seen
            for event in run.events(since=seen):
                if event.get('seq', 0) <= seen:
                    continue
                seen = event['seq']
                with self._locked():
                    self._event(record, 'agent', event.get('text', ''), event)
        def retain_logs():
            if not isinstance(run.id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run.id):
                raise FlowError('invalid', 'Invalid agent run id.')
            destination = self._archive(cid, 'runs/' + run.id)
            if destination != log_dir:
                log_dir.rename(destination)
        try:
            while True:
                forward()
                if self._cancelled(cid):
                    raise _Cancelled()
                if run.status not in ('starting', 'running'):
                    break
                if time.monotonic() >= deadline:
                    raise FlowError('invalid', 'Agent timed out.')
                time.sleep(.02)
            run.wait(timeout=5)
            forward()
            if run.status != 'done':
                raise FlowError('invalid', 'Agent did not complete: ' + run.status)
            result = run.result
            if schema is not None:
                _validate(result, schema)
            if len(json.dumps(result)) > 256000:
                raise FlowError('invalid', 'Agent result exceeds 256000 characters.')
            retain_logs()
            return run.id, result
        except BaseException:
            run.cancel()
            run.wait(timeout=5)
            forward()
            if log_dir.exists():
                retain_logs()
            raise

    def _analysis_command(self, record, *args):
        # The existing CLI redirects stdout internally. Isolate it from other HTTP
        # threads instead of swapping process-global sys.stdout around their work.
        result = _TESTS['run_tests'](
            [sys.executable, '-I', str(Path(self.engine.__file__).resolve()), '--root', record['worktree'], *args],
            record['worktree'], timeout=120, cancelled=lambda: self._cancelled(record['id']), output_limit=40000)
        if result['status'] != 'passed':
            raise FlowError('invalid', 'Static analysis ' + result['status'] + ': ' + result['stderr_tail'][-2000:])
        return result['stdout_tail']

    def _enrich(self, record, agent):
        root = record['worktree']
        self._analysis_command(record, 'index')
        unresolved, affected, tests, limits, roots = [], {}, {}, [], []
        with _ProjectData(self.engine, root) as project:
            for role in ('touch', 'affected', 'tests'):
                for item in agent[role]:
                    try:
                        path = self._relative(item['path'])
                    except FlowError:
                        unresolved.append(dict(role=role, **item))
                        continue
                    symbol = item.get('symbol', item.get('name', ''))
                    candidates = self.engine.lookup(project.db, path + ':' + symbol) if symbol else []
                    candidates = [sid for sid in candidates if project._symbol(sid)['file'] == path]
                    if len(candidates) != 1:
                        unresolved.append(dict(role=role, **item))
                        continue
                    if role != 'touch':
                        continue
                    sid = candidates[0]
                    roots.append(sid)
                    impact = project.impact(sid, 'medium', 3)
                    for entry in impact['items']:
                        node = entry['symbol']
                        confidence = 'medium' if any(step['confidence'] == 'medium' for step in entry['chain']) else 'high'
                        affected[node['id']] = dict(path=node['file'], symbol=node['name'], line=node['line'],
                                                   why='Static caller', confidence=confidence)
                    if impact['truncated']:
                        limits.append('Impact was truncated; inspect the remaining callers.')
            proposal = _ANALYSIS['plan'](project, roots)
            for entry in proposal['tests']:
                node = entry['symbol']
                tests[node['id']] = dict(path=node['file'], name=node['name'], why=entry['reason'])
            limits.extend(proposal['limitations'])
            if proposal['broader_verification_required']:
                limits.append('Dynamic calls or the depth frontier require broader verification.')
            for key, label in [('skipped_files', 'files skipped'), ('partial_files', 'files parsed partially')]:
                if int(project.meta.get(key, 0)):
                    limits.append(project.meta[key] + ' ' + label + ' by the index.')
        return dict(agent=agent, graph=dict(affected=list(affected.values()), tests=list(tests.values()), limits=limits),
                    checked=dict(unresolved=unresolved))

    def plan(self, change_id):
        def operation(record):
            prompt = ('Prepare a change plan only. Do not modify source files or run project commands. '
                      'Use code-graph MCP for evidence. Return the requested JSON schema. Intent:\n' + record['intent'])
            _, agent = self._agent(record, record['author'], 'read', prompt, PLAN_SCHEMA)
            return dict(plan=self._enrich(record, agent), approved=None), 'planned'
        return self._start(change_id, {'draft', 'planned'}, 'planning', operation)

    def _plan_items(self, plan, role, items):
        valid = []
        for item in items:
            try:
                valid.append(dict(item, path=self._relative(item['path'])))
            except FlowError:
                unresolved = dict(role=role, **item)
                if unresolved not in plan['checked']['unresolved']:
                    plan['checked']['unresolved'].append(unresolved)
        return valid

    def approve(self, change_id, test_argv, touch=None, out_of_scope=None, no_tests_reason=None):
        if not isinstance(test_argv, list) or any(not isinstance(x, str) or not x or '\x00' in x for x in test_argv):
            raise FlowError('invalid', 'test_argv must be a list of nonempty strings, never a shell command.')
        if not test_argv and (not isinstance(no_tests_reason, str) or not no_tests_reason.strip()):
            raise FlowError('invalid', 'Empty test argv requires no_tests_reason.')
        with self._locked():
            record = self._load(change_id)
            self._require(record, {'planned'})
            agent = record['plan']['agent']
            touch = self._plan_items(record['plan'], 'touch', agent['touch']) if touch is None else touch
            if not isinstance(touch, list):
                raise FlowError('invalid', 'touch must be a list of paths or plan records.')
            normalized = []
            for item in touch:
                if isinstance(item, str):
                    item = dict(path=item, symbol='', line=0, why='Approved by user')
                if not isinstance(item, dict) or 'path' not in item:
                    raise FlowError('invalid', 'Invalid touch record.')
                normalized.append(dict(item, path=self._relative(item['path'])))
            excluded = agent['out_of_scope'] if out_of_scope is None else out_of_scope
            if not isinstance(excluded, list) or any(not isinstance(x, str) for x in excluded):
                raise FlowError('invalid', 'out_of_scope must be a list of strings.')
            record['approved'] = dict(test_argv=list(test_argv), touch=normalized, out_of_scope=excluded,
                                      no_tests_reason=no_tests_reason, t=self._now())
            self._state(record, 'approved', by='user')
            return record

    def _test(self, record):
        self._check_cancel(record['id'])
        root = self._owned_checkout(record)
        before = self._untracked(root)
        artifacts = []
        try:
            result = _TESTS['run_tests'](record['approved']['test_argv'], root,
                                       timeout=TEST_TIMEOUT, cancelled=lambda: self._cancelled(record['id']))
        finally:
            # Preserve new test output outside the worktree, including on cancellation.
            # Existing files and tracked changes remain visible to the scope/dirty checks.
            folder = 'test-artifacts/' + secrets.token_hex(8)
            for path in sorted(self._untracked(root) - before):
                relative = Path(self._relative(path))
                source = self.engine.local_path(root, str(relative.parent)) / relative.name
                destination = self._archive(record['id'], folder + '/' + path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                artifact = dict(path=path, archive=folder + '/' + path)
                if source.is_symlink():
                    # Preserve link metadata without putting a live symlink in the archive.
                    destination.write_text(json.dumps(dict(symlink=os.readlink(source))), encoding='utf-8')
                    source.unlink()
                    artifact['kind'] = 'symlink'
                else:
                    self.engine.local_path(root, str(relative)).replace(destination)
                artifacts.append(artifact)
            if artifacts:
                with self._locked():
                    self._event(record, 'test', 'Generated test files archived.', dict(artifacts=artifacts))
        result['artifacts'] = artifacts
        with self._locked():
            self._event(record, 'test', 'Tests ' + result['status'], result)
        return result

    def _untracked(self, root):
        return set(filter(None, self._git(root, 'ls-files', '--others', '--exclude-standard', '-z')
                          .stdout.decode('utf-8', 'replace').split('\0')))

    def _files(self, record):
        root = record['worktree']
        output = self._git(root, 'diff', '--no-renames', '--name-status', '-z', record['base']['commit'], '--').stdout
        parts = output.decode('utf-8', 'replace').split('\0')
        files = {parts[i + 1]: dict(path=parts[i + 1], change={'A': 'add', 'D': 'delete'}.get(parts[i], 'update'))
                 for i in range(0, len(parts) - 1, 2)}
        for path in self._untracked(root):
            files[path] = dict(path=path, change='add')
        return [files[path] for path in sorted(files)]

    def edit(self, change_id, feedback=None):
        if feedback is not None and not isinstance(feedback, str):
            raise FlowError('invalid', 'Feedback must be text.')
        def prepare(record):
            if record['round'] >= 3:
                raise FlowError('bad_state', 'Maximum of three edit rounds reached.')
            if record['tests']['baseline'] is None:
                root = record['worktree']
                if (self._git_text(root, 'rev-parse', 'HEAD') != record['base']['commit']
                        or self._git_text(root, 'status', '--porcelain', '--untracked-files=all')):
                    raise FlowError('dirty', 'Baseline requires the clean base commit in the worktree.')
        def operation(record):
            if record['tests']['baseline'] is None:
                baseline = self._test(record)
                with self._locked():
                    self._check_cancel(change_id)
                    current = self._load(change_id)
                    current['tests']['baseline'] = baseline
                    self._state(current, 'editing')
                    record = current
            prompt = ('Implement the approved plan only inside this worktree. Do not run tests, shell commands, '
                      'Git commands or modify .git/.code-graph. The engine runs the approved tests and commits. '
                      'Read files using cg_read/cg_source and edit only with the client file-writing tools. '
                      'Stay within approved touch/tests files. Explain any additional files you need in the result.\n'
                      + json.dumps(dict(intent=record['intent'], plan=record['plan'], approved=record['approved'],
                                        feedback=feedback, verify=record['verify'], review=record['review'],
                                        test_result=record['tests']['after']), ensure_ascii=False))
            run_id, result = self._agent(record, record['author'], 'write', prompt, phase='edit')
            self._check_cancel(change_id)
            root = self._owned_checkout(record)
            files = self._files(record)
            # Stage all visible changes so new files are present in the diff and scope check.
            self._git(root, 'add', '-A', '--', '.')
            patch = self._git(root, 'diff', '--cached', '--binary', '--no-ext-diff', '--no-textconv',
                              record['base']['commit'], '--').stdout
            self._archive(change_id, 'diff.patch').write_bytes(patch)
            if self._git(root, 'diff', '--cached', '--quiet', check=False).returncode:
                self._git(root, '-c', 'user.name=Code Graph', '-c', 'user.email=code-graph@localhost',
                          'commit', '-m', 'Change ' + change_id)
            edit = dict(run=run_id, files=files, result=result, commit=self._git_text(root, 'rev-parse', 'HEAD'))
            return dict(edit=edit, round=record['round'] + 1, tests=dict(baseline=record['tests']['baseline'], after=None),
                        verify=None, review=None), 'edited'
        record = self.get(change_id)
        active = 'baseline' if record['tests']['baseline'] is None else 'editing'
        return self._start(change_id, {'approved', 'verified', 'reviewed'}, active, operation, prepare)

    def verify(self, change_id):
        def operation(record):
            root = self._owned_checkout(record)
            self._analysis_command(record, 'index')
            after = self._test(record)
            comparison = _TESTS['compare_tests'](record['tests']['baseline'], after)
            # Tests may modify tracked sources; review must describe that final worktree.
            self._analysis_command(record, 'index')
            static = json.loads(self._analysis_command(record, 'review', '--base', record['base']['commit'],
                                                       '--max-chars', '20000', '--json'))
            plan = record['plan']
            planned = {item['path'] for item in self._plan_items(plan, 'touch', record['approved']['touch'])}
            planned.update(item['path'] for item in self._plan_items(plan, 'tests', plan['agent']['tests']))
            files = {item['path'] for item in self._files(record)}
            perimeter = dict(outside=sorted(files - planned), planned_untouched=sorted(planned - files))
            blocking = []
            if comparison['newly_failing']:
                blocking.append('New failing tests')
            if after['status'] in ('error', 'timeout'):
                blocking.append('Test execution ' + after['status'])
            if perimeter['outside']:
                blocking.append('Files outside the approved perimeter')
            # A test must not silently modify the candidate that the author committed.
            if self._git_text(root, 'status', '--porcelain', '--untracked-files=all'):
                blocking.append('Worktree changed after the edit commit')
            if self._git_text(root, 'rev-parse', 'HEAD') != record['edit']['commit']:
                blocking.append('Edit commit changed')
            verification = dict(tests=comparison, static=static, perimeter=perimeter, blocking=blocking, ok=not blocking)
            return dict(plan=plan, verify=verification, tests=dict(baseline=record['tests']['baseline'], after=after)), 'verified'
        return self._start(change_id, {'edited'}, 'verifying', operation)

    def review(self, change_id, same_client_reason=None):
        def prepare(record):
            if record['author'] == record['reviewer'] and not (isinstance(same_client_reason, str) and same_client_reason.strip()):
                raise FlowError('invalid', 'The reviewer must use the other client, or give same_client_reason.')
        def operation(record):
            with self._archive(change_id, 'diff.patch').open('rb') as handle:
                raw = handle.read(240004)
            patch = raw.decode('utf-8', 'replace')
            if len(patch) > 60000:
                patch = patch[:59960] + '\n[DIFF TRUNCATED: inspect worktree]\n'
            prompt = ('Review without edits or running project commands. Use MCP and inspect the worktree. '
                      'Return the requested JSON verdict and findings.\n'
                      + json.dumps(dict(intent=record['intent'], plan=record['plan'], diff=patch,
                                        verify=record['verify']), ensure_ascii=False))
            run_id, result = self._agent(record, record['reviewer'], 'read', prompt, REVIEW_SCHEMA, phase='review')
            return dict(review=dict(result, run=run_id, same_client_reason=same_client_reason)), 'reviewed'
        return self._start(change_id, {'verified'}, 'reviewing', operation, prepare)

    def integrate(self, change_id, mode='branch', force_reason=None):
        if mode not in ('branch', 'merge'):
            raise FlowError('invalid', 'Integration mode must be branch or merge.')
        if force_reason is not None and (not isinstance(force_reason, str) or not force_reason.strip()):
            raise FlowError('invalid', 'force_reason must explain the override.')
        with self._locked():
            record = self._load(change_id)
            self._require(record, {'reviewed'})
            if (record['review']['verdict'] == 'reject' or record['verify']['blocking']) and not force_reason:
                raise FlowError('bad_state', 'Review or verification blocks integration; a force_reason is required.')
            root = self._owned_checkout(record)
            if self._git_text(root, 'status', '--porcelain', '--untracked-files=all'):
                raise FlowError('dirty', 'The reviewed worktree is no longer clean.')
            if self._git_text(root, 'rev-parse', 'HEAD') != record['edit']['commit']:
                raise FlowError('invalid', 'The reviewed commit changed.')
            if mode == 'merge':
                if self._git_text(self.root, 'status', '--porcelain', '--untracked-files=all'):
                    raise FlowError('dirty', 'The destination checkout is dirty.')
                if self._git_text(self.root, 'symbolic-ref', '--short', 'HEAD') != record['base']['branch']:
                    raise FlowError('invalid', 'The destination branch changed.')
                if self._git(self.root, 'merge-base', '--is-ancestor', record['base']['commit'], 'HEAD', check=False).returncode:
                    raise FlowError('invalid', 'The destination no longer contains the base commit.')
                merge = self._git(self.root, 'merge', '--no-ff', '--no-edit', record['branch'], check=False)
                if merge.returncode:
                    if self._git(self.root, 'rev-parse', '--verify', 'MERGE_HEAD', check=False).returncode == 0:
                        self._git(self.root, 'merge', '--abort')
                    record['error'] = dict(code='conflict', message=merge.stderr.decode('utf-8', 'replace')[-2000:] or 'Merge conflict.')
                    self._state(record, 'reviewed', 'Merge failed and was aborted.')
                    raise FlowError('conflict', record['error']['message'])
            self._git(self.root, 'worktree', 'remove', str(root))
            record['integration'] = dict(mode=mode, branch=record['branch'], commit=record['edit']['commit'],
                                          merge_command=['git', 'merge', '--no-ff', '--no-edit', record['branch']],
                                          force_reason=force_reason)
            record['error'] = None
            self._state(record, 'integrated', force_reason or 'Integration approved.', by='user')
            return record

    def cancel(self, change_id):
        with self._locked():
            record = self._load(change_id)
            if record['state'] not in ACTIVE:
                if (record.get('error') or {}).get('code') == 'interrupted':
                    return record
                raise FlowError('bad_state', 'No active step to cancel.')
            self._archive(change_id, 'cancel').touch()
            self._event(record, 'step', 'Cancellation requested.')
            return record

    def wait(self, change_id, timeout=None):
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        while True:
            record = self.get(change_id)
            if record['state'] not in ACTIVE or (deadline is not None and time.monotonic() >= deadline):
                return record
            time.sleep(.02)

    def discard(self, change_id):
        record = self.get(change_id)
        if record['state'] in ACTIVE:
            self.cancel(change_id)
            record = self.wait(change_id, timeout=10)
        with self._locked():
            record = self._load(change_id)
            self._require(record, STABLE - TERMINAL)
            root = self._owned_checkout(record)
            self._git(self.root, 'worktree', 'remove', '--force', str(root))
            self._git(self.root, 'branch', '-D', record['branch'])
            self._state(record, 'discarded', 'Own worktree and branch removed; archive retained.', by='user')
            return record


def build_parser():
    parser = argparse.ArgumentParser(prog='cg change', description='Guided change with agents: one model plans and edits '
                                     'in its own worktree, the approved tests run before and after, the other model reviews.')
    commands = parser.add_subparsers(dest='action', required=True, metavar='ACTION')
    new = commands.add_parser('new', help='open a change from an intent; nothing runs yet')
    new.add_argument('--intent', required=True, help='what the change should do, in words')
    new.add_argument('--author', choices=CLIENTS, required=True, help='model that plans and edits')
    new.add_argument('--reviewer', choices=CLIENTS, required=True, help='model that reviews; normally the other one')
    new.add_argument('--allow-dirty', action='store_true', help='start although the checkout has uncommitted changes')
    new.add_argument('--budget-usd', type=float, help='spending cap passed to Claude when it is the author or reviewer')
    commands.add_parser('list', help='changes in this project and their state')
    helps = dict(show='state, plan, tests and review of a change', plan='ask the author for a plan grounded in the graph',
                 approve='approve the plan and the exact test command; the tests run once now as the baseline',
                 edit='let the author edit in its isolated worktree',
                 verify='re-index, run the approved tests on the edit, compare with the baseline and review the diff statically', review='ask the reviewer to read the diff',
                 integrate='bring the reviewed change into its branch, or merge it with --merge',
                 discard='drop a change that is not integrated: its worktree and branch are removed, the archive is kept',
                 cancel='stop the step that is running')
    for action in ('show', 'plan', 'approve', 'edit', 'verify', 'review', 'integrate', 'discard', 'cancel'):
        command = commands.add_parser(action, help=helps[action])
        command.add_argument('id', help='change id, as printed by new and list')
        if action in ('plan', 'edit', 'review'):
            command.add_argument('--yes', action='store_true', help='Confirm starting the agent and sending context to its provider')
        if action == 'approve':
            command.add_argument('--test-argv', required=True, help='test command as a JSON list of arguments, never a shell string')
            command.add_argument('--no-tests-reason', help='why an empty test command is acceptable')
        elif action == 'edit':
            command.add_argument('--feedback', help='what to change in a new edit round')
        elif action == 'review':
            command.add_argument('--same-client-reason', help='why the reviewer may be the same model as the author')
        elif action == 'integrate':
            command.add_argument('--merge', action='store_true', help='merge into the base branch instead of leaving a branch')
            command.add_argument('--force-reason', help='why to integrate although the review or the tests block it')
    return parser


def cli(engine, root, argv):
    parser = build_parser()
    args = parser.parse_args(argv)
    flow = ChangeFlow(engine, root)
    try:
        if args.action in ('plan', 'edit', 'review') and not args.yes:
            if not sys.stdin.isatty():
                parser.error('Starting an agent requires confirmation. Use --yes when you authorize this step.')
            record = flow.get(args.id)
            client = record['reviewer'] if args.action == 'review' else record['author']
            print(f'Start {client} for {args.action} in {record["worktree"]}? '
                  'Project context will be sent to its provider and may consume quota. [y/N] ',
                  end='', file=sys.stderr, flush=True)
            try:
                confirmed = sys.stdin.readline().strip().lower() in ('y', 'yes')
            except (EOFError, KeyboardInterrupt):
                confirmed = False
            if not confirmed:
                parser.error('Agent start not confirmed; no agent was started.')
        if args.action == 'new':
            result = flow.create(args.intent, args.author, args.reviewer, args.allow_dirty, args.budget_usd)
        elif args.action == 'list':
            result = flow.list()
        elif args.action == 'show':
            result = flow.get(args.id)
        elif args.action == 'approve':
            try:
                test_argv = json.loads(args.test_argv)
            except ValueError as error:
                raise FlowError('invalid', 'test-argv must be JSON.') from error
            result = flow.approve(args.id, test_argv, no_tests_reason=args.no_tests_reason)
        elif args.action == 'edit':
            result = flow.edit(args.id, args.feedback)
        elif args.action == 'review':
            result = flow.review(args.id, args.same_client_reason)
        elif args.action == 'integrate':
            result = flow.integrate(args.id, 'merge' if args.merge else 'branch', args.force_reason)
        else:
            result = getattr(flow, args.action)(args.id)
        if isinstance(result, dict) and result.get('state') in ACTIVE:
            result = flow.wait(args.id)
        print(json.dumps(result, ensure_ascii=False))
        if isinstance(result, dict) and result.get('error'):
            raise SystemExit(1)
        return result
    except FlowError as error:
        print(json.dumps(dict(error=str(error), code=error.code), ensure_ascii=False))
        raise SystemExit(1) from error
