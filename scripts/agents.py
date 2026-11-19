"""Bounded, asynchronous CLI runs. No shell, model calls or detection at import."""
import copy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import secrets
import selectors
import shutil
import signal
import subprocess
import tempfile
import threading
import time


CLIENTS = ('codex', 'claude')
RAW_LIMIT = 5 * 1024 * 1024
STDERR_LIMIT = 256 * 1024
LINE_LIMIT = 1024 * 1024
TEXT_LIMIT = 4000
MAX_EVENTS = 10_000
TERMINAL = {'done', 'failed', 'cancelled', 'timeout'}
FORBIDDEN = {'--dangerously-bypass-approvals-and-sandbox', 'danger-full-access',
             '--dangerously-skip-permissions', 'bypassPermissions'}


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _positive(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError(name + ' must be a finite positive number')


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _load(text):
    def invalid(value):
        raise ValueError('Non-finite JSON number: ' + value)
    return json.loads(text, parse_constant=invalid)


def _bounded(value):
    encoded = _json(value)
    if len(encoded) <= TEXT_LIMIT:
        return value
    return dict(truncated=True, preview=encoded[:TEXT_LIMIT])


def _schema_check(schema, value=None, *, definition=False, path='$', depth=0):
    """Validate the explicit workflow schema subset; reject unsupported keywords."""
    if depth > 40 or not isinstance(schema, dict):
        raise ValueError(path + ': schema must be a bounded object')
    supported = {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum'}
    if schema.keys() - supported:
        raise ValueError(path + ': unsupported schema keywords: ' + ', '.join(sorted(schema.keys() - supported)))
    types = schema.get('type', [])
    types = types if isinstance(types, list) else [types]
    valid_types = {'object', 'array', 'string', 'integer', 'number', 'boolean', 'null'}
    if any(not isinstance(item, str) or item not in valid_types for item in types):
        raise ValueError(path + ': invalid schema type')
    properties = schema.get('properties', {})
    required = schema.get('required', [])
    if not isinstance(properties, dict) or not isinstance(required, list) or any(not isinstance(k, str) for k in required):
        raise ValueError(path + ': invalid properties/required')
    additional = schema.get('additionalProperties', True)
    if type(additional) is not bool:
        raise ValueError(path + ': additionalProperties must be boolean')
    if 'enum' in schema and (not isinstance(schema['enum'], list) or not schema['enum']):
        raise ValueError(path + ': enum must be a nonempty array')
    if definition:
        for key, child in properties.items():
            _schema_check(child, definition=True, path=path + '.' + key, depth=depth + 1)
        if 'items' in schema:
            _schema_check(schema['items'], definition=True, path=path + '[]', depth=depth + 1)
        return
    actual = ('null' if value is None else 'boolean' if type(value) is bool else
              'integer' if type(value) is int else 'number' if type(value) is float else
              'string' if isinstance(value, str) else 'array' if isinstance(value, list) else
              'object' if isinstance(value, dict) else 'unknown')
    if types and actual not in types and not (actual == 'integer' and 'number' in types):
        raise ValueError(path + ': unexpected ' + actual)
    if actual == 'number' and not math.isfinite(value):
        raise ValueError(path + ': invalid number')
    if 'enum' in schema and not any(_json(value) == _json(item) for item in schema['enum']):
        raise ValueError(path + ': value outside enum')
    if actual == 'object':
        if any(key not in value for key in required):
            raise ValueError(path + ': missing required field')
        if not additional and value.keys() - properties.keys():
            raise ValueError(path + ': unexpected fields')
        for key in value.keys() & properties.keys():
            _schema_check(properties[key], value[key], path=path + '.' + key, depth=depth + 1)
    if actual == 'array' and 'items' in schema:
        for item in value:
            _schema_check(schema['items'], item, path=path + '[]', depth=depth + 1)


EXITED_GROUP_ATTEMPTS = 8  # with pauses from 5 ms doubling to 50 ms: about 0.2 s in all


def _signal_group(process, sig):
    """Signal an owned group; return whether it is gone or SIGKILL was delivered."""
    pause = .005
    for attempt in range(EXITED_GROUP_ATTEMPTS):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            # macOS can retain a terminated group for a while, longer on a loaded machine, and answer
            # EPERM until it is reaped; poll alone does not prove that its descendants have exited.
            if process.poll() is None or attempt == EXITED_GROUP_ATTEMPTS - 1:
                raise
            time.sleep(pause)
            pause = min(pause * 2, .05)
        else:
            return sig == signal.SIGKILL


def detect(timeout=5):
    _positive(timeout, 'timeout')
    clients = []
    for client in CLIENTS:
        path = shutil.which(client)
        item = dict(client=client, available=False, path=path, version=None, reason=None)
        if path is None:
            item['reason'] = 'Executable not found on PATH'
        else:
            process = None
            try:
                process = subprocess.Popen([path, '--version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, encoding='utf-8', errors='replace',
                                           shell=False, start_new_session=True)
                stdout, _ = process.communicate(timeout=timeout)
                item['available'] = process.returncode == 0
                if item['available']:
                    item['version'] = stdout.strip()[:500] or None
                else:
                    item['reason'] = 'Version check failed (exit ' + str(process.returncode) + ')'
            except (OSError, subprocess.TimeoutExpired) as exc:
                item['reason'] = str(exc)[:500]
            finally:
                if process is not None:
                    try:
                        _signal_group(process, signal.SIGKILL)
                        process.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        detail = 'Cannot clean up version check process group: ' + str(exc)
                        item['reason'] = '; '.join(filter(None, (item['reason'], detail)))[:500]
                        if process.poll() is None:
                            item['available'], item['version'] = False, None
                    finally:
                        process.stdout.close()
                        process.stderr.close()
        clients.append(item)
    return clients


def _write_worktree(cwd, root):
    if cwd != root:
        raise ValueError('write requires root == cwd (the change worktree)')
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    try:
        info = subprocess.run(['git', '--no-optional-locks', '-C', cwd, 'rev-parse',
                               '--show-toplevel', '--absolute-git-dir', '--git-common-dir'],
                              capture_output=True, text=True, timeout=5, check=True, env=env)
        top, gitdir, common = info.stdout.strip().splitlines()
        common = (Path(cwd) / common).resolve()
        if Path(top).resolve() != Path(cwd) or Path(gitdir).resolve() == common:
            raise ValueError('write requires the root of a linked Git worktree')
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('Cannot verify the linked Git worktree') from exc


def start(client, *, mode, cwd, prompt, root, output_schema=None, timeout=900,
          model=None, budget_usd=None, log_dir=None, executable=None):
    if client not in CLIENTS or mode not in ('read', 'write'):
        raise ValueError('Unknown client or mode')
    _positive(timeout, 'timeout')
    if budget_usd is not None:
        _positive(budget_usd, 'budget_usd')
        if client != 'claude':
            raise ValueError('budget_usd is supported only by Claude')
    if not isinstance(prompt, str):
        raise ValueError('prompt must be a string')
    if model is not None and (not isinstance(model, str) or not model or model.startswith('-')
                              or model in FORBIDDEN or any(c in model for c in '\x00\r\n')):
        raise ValueError('Invalid model')
    cwd, root = str(Path(cwd).resolve(strict=True)), str(Path(root).resolve(strict=True))
    if not Path(cwd).is_dir() or not Path(root).is_dir():
        raise ValueError('cwd and root must be directories')
    if mode == 'write':
        _write_worktree(cwd, root)
    if output_schema is not None:
        output_schema = _load(_json(output_schema))
        _schema_check(output_schema, definition=True)
        if output_schema.get('type') != 'object':
            raise ValueError('output_schema must describe an object result')
    path = executable if executable is not None else shutil.which(client)
    if not path or not isinstance(path, (str, os.PathLike)):
        raise ValueError('Client executable not found')
    path = str(Path(path).resolve(strict=True))
    if not os.access(path, os.X_OK) or not Path(path).is_file():
        raise ValueError('Client executable is not executable')
    run = AgentRun(client, mode, cwd, root, prompt, output_schema, timeout,
                   model, budget_usd, log_dir, path)
    run._thread.start()
    return run


class AgentRun:
    def __init__(self, client, mode, cwd, root, prompt, schema, timeout, model, budget, log_dir, executable):
        self.id = secrets.token_hex(8)
        self.client, self.mode, self.cwd = client, mode, cwd
        self.status, self.session, self.result, self.usage = 'starting', None, None, None
        self._root, self._prompt, self._schema = root, prompt, schema
        self._timeout, self._model, self._budget = timeout, model, budget
        self._executable = executable
        self._log_dir = Path(log_dir).resolve() if log_dir is not None else None
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._events, self._tools, self._logs = [], {}, {}
        self._log_sizes = {}
        self._truncated_logs = set()
        self._log_error = None
        self._terminal_seen, self._client_error = False, False
        self._final_text, self._structured = '', None
        self._process = None
        self._group_killed = False
        self._thread = threading.Thread(target=self._work, name='cg-agent-' + self.id, daemon=True)

    def events(self, since=0):
        if type(since) is not int or since < 0:
            raise ValueError('since must be a nonnegative sequence')
        with self._lock:
            return copy.deepcopy(self._events[since:])

    def wait(self, timeout=None):
        self._thread.join(timeout)
        return self.status

    def cancel(self):
        with self._lock:
            if self.status not in TERMINAL:
                self._cancel.set()

    def resume_command(self):
        with self._lock:
            if not self.session:
                return None
            return [self.client, 'resume' if self.client == 'codex' else '--resume', self.session]

    def _emit(self, kind, text='', data=None, *, terminal=False):
        with self._lock:
            if len(self._events) >= MAX_EVENTS and not terminal:
                raise ValueError('Client exceeded the 10000 event limit')
            event = dict(v=1, run=self.id, seq=len(self._events) + 1, t=_now(), kind=kind,
                         text=str(text)[:TEXT_LIMIT], data=data or {})
            self._events.append(event)
            self._log('events.jsonl', (_json(event) + '\n').encode())

    def _log(self, name, value, limit=None):
        stream = self._logs.get(name)
        if stream is None:
            return
        used = self._log_sizes.get(name, 0)
        kept = value if limit is None else value[:max(0, limit - used)]
        if len(kept) < len(value):
            self._truncated_logs.add(name)
        try:
            stream.write(kept)
            stream.flush()
        except OSError as exc:
            # Preserve terminal state in memory if the log device fills up.
            self._logs.pop(name, None)
            self._log_error = str(exc)
            try:
                stream.close()
            except OSError:
                pass
        self._log_sizes[name] = used + len(kept)

    def _argv(self, folder):
        mcp_command = str(Path(__file__).resolve().with_name('cg'))
        mcp_args = ['--root', self._root, 'mcp']
        if self.client == 'codex':
            argv = [self._executable, 'exec', '--json', '--ignore-user-config', '--ignore-rules',
                    '-s', 'read-only' if self.mode == 'read' else 'workspace-write', '-C', self.cwd,
                    '-c', 'approval_policy="never"', '-c', 'features.hooks=false',
                    '-c', 'features.shell_tool=false', '-c', 'features.shell_snapshot=false',
                    '-c', 'features.multi_agent=false', '-c', 'features.multi_agent_v2=false',
                    '-c', 'features.apps=false',
                    '-c', 'mcp_servers={}', '-c', 'mcp_servers.code-graph.command=' + _json(mcp_command),
                    '-c', 'mcp_servers.code-graph.args=' + _json(mcp_args),
                    '-c', 'mcp_servers.code-graph.required=true']
            if self._schema is not None:
                schema_path = folder / 'schema.json'
                schema_path.write_text(_json(self._schema), encoding='utf-8')
                argv += ['--output-schema', str(schema_path)]
        else:
            config = folder / 'mcp.json'
            config.write_text(_json(dict(mcpServers={'code-graph': dict(command=mcp_command, args=mcp_args)})), encoding='utf-8')
            builtin = 'Read,Grep,Glob' + (',Edit,Write' if self.mode == 'write' else '')
            argv = [self._executable, '-p', '--output-format', 'stream-json', '--verbose',
                    '--permission-mode', 'plan' if self.mode == 'read' else 'acceptEdits',
                    '--restricted', '--setting-sources', '', '--tools', builtin,
                    '--allowedTools', builtin + ',mcp__code-graph__*',
                    '--mcp-config', str(config), '--strict-mcp-config']
            if self._schema is not None:
                argv += ['--json-schema', _json(self._schema)]
            if self._budget is not None:
                argv += ['--max-budget-usd', str(self._budget)]
        if self._model:
            argv += ['--model', self._model]
        if self.client == 'codex':
            argv.append('-')
        if any(argument in FORBIDDEN for argument in argv):
            raise ValueError('Unsafe client configuration')
        return argv

    def _usage(self, value):
        if not isinstance(value, dict):
            return
        incoming, outgoing = value.get('input_tokens'), value.get('output_tokens')
        if any(type(count) is not int or count < 0 for count in (incoming, outgoing)):
            return
        cache_key = 'cached_input_tokens' if self.client == 'codex' else 'cache_read_input_tokens'
        cached = value.get(cache_key)
        if cached is not None and (type(cached) is not int or cached < 0):
            return
        # Codex reports cache writes inside input_tokens: keep them as a separate count, never add them.
        written = value.get('cache_write_input_tokens') if self.client == 'codex' else None
        if written is not None and (type(written) is not int or written < 0):
            return
        if self.client == 'claude':
            creation = value.get('cache_creation_input_tokens')
            if creation is not None and (type(creation) is not int or creation < 0):
                return
            incoming += (cached or 0) + (creation or 0)
        self.usage = dict(input_tokens=incoming, output_tokens=outgoing,
                          cached_input_tokens=cached, cache_write_input_tokens=written, measured=True)
        self._emit('usage', data=dict(self.usage))

    def _native(self, event):
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            raise ValueError('Client event must be an object with a type')
        event_type = event['type']
        session = event.get('thread_id') if self.client == 'codex' else event.get('session_id')
        session_event = (self.client == 'codex' and event_type == 'thread.started') or (
            self.client == 'claude' and (event_type == 'result' or
                                         (event_type == 'system' and event.get('subtype') == 'init')))
        if (session_event and isinstance(session, str) and session and not session.startswith('-')
                and len(session) <= 256 and all(c.isalnum() or c in '-_' for c in session)):
            self.session = session
        if self.client == 'codex':
            if event_type == 'thread.started':
                return
            if event_type == 'turn.completed':
                self._terminal_seen = True
                self._usage(event.get('usage'))
                return
            if event_type in ('turn.failed', 'error'):
                self._client_error = True
                self._emit('error', event.get('message') or event.get('error', 'Client error'))
                return
            item = event.get('item', {})
            if event_type.startswith('item.') and isinstance(item, dict):
                kind = item.get('type')
                if kind == 'agent_message':
                    text = item.get('text', '')
                    if not isinstance(text, str):
                        raise ValueError('Invalid agent message text')
                    if event_type == 'item.completed':
                        self._final_text = text
                    self._emit('message', text)
                    return
                if kind == 'reasoning':
                    self._emit('reasoning', item.get('text', ''))
                    return
                if kind == 'command_execution':
                    exit_code = item.get('exit_code')
                    self._emit('command', item.get('aggregated_output', ''),
                               dict(command=str(item.get('command', ''))[:TEXT_LIMIT],
                                    exit=exit_code if type(exit_code) is int else None))
                    return
                if kind == 'file_change':
                    for change in item.get('changes', []):
                        action = {'add': 'add', 'update': 'update', 'delete': 'delete'}.get(change.get('kind'), 'update')
                        self._emit('file', data=dict(path=str(change.get('path', ''))[:TEXT_LIMIT], change=action))
                    return
                if kind == 'mcp_tool_call':
                    status = 'started' if event_type != 'item.completed' else 'error' if item.get('error') or item.get('status') == 'failed' else 'ok'
                    self._emit('tool', data=dict(name=str(item.get('tool', ''))[:TEXT_LIMIT],
                                                input=_bounded(item.get('arguments', {})), status=status))
                    return
        else:
            if event_type == 'system' and event.get('subtype') == 'init':
                return
            if event_type == 'result':
                self._terminal_seen = True
                self._client_error |= bool(event.get('is_error')) or event.get('subtype') != 'success'
                self._structured = event.get('structured_output')
                self._final_text = event.get('result', '')
                if not isinstance(self._final_text, str):
                    raise ValueError('Invalid result text')
                self._usage(event.get('usage'))
                if self._client_error:
                    self._emit('error', event.get('errors') or self._final_text or 'Client failed')
                else:
                    self._emit('message', self._final_text)
                return
            if event_type in ('assistant', 'user'):
                content = event.get('message', {}).get('content', [])
                if isinstance(content, list):
                    for block in content:
                        kind = block.get('type')
                        if kind in ('text', 'thinking'):
                            self._emit('message' if kind == 'text' else 'reasoning', block.get(kind, ''))
                        elif kind == 'tool_use':
                            name, arguments = str(block.get('name', '')), block.get('input', {})
                            self._tools[block.get('id')] = (name[:TEXT_LIMIT], _bounded(arguments))
                            self._emit('tool', data=dict(name=name[:TEXT_LIMIT], input=_bounded(arguments), status='started'))
                        elif kind == 'tool_result':
                            name, arguments = self._tools.pop(block.get('tool_use_id'), ('unknown', {}))
                            failed = bool(block.get('is_error'))
                            self._emit('tool', data=dict(name=name[:TEXT_LIMIT], input=_bounded(arguments), status='error' if failed else 'ok'))
                            if not failed and name in ('Edit', 'Write') and isinstance(arguments, dict):
                                self._emit('file', data=dict(path=str(arguments.get('file_path', ''))[:TEXT_LIMIT], change='update'))
                    return
        self._emit('other', data=dict(type=event_type[:TEXT_LIMIT]))

    def _signal(self, sig):
        if self._process is None or self._group_killed:
            return
        self._group_killed = _signal_group(self._process, sig)

    def _pump(self, errors):
        process = self._process

        def signal_group(sig):
            try:
                self._signal(sig)
            except PermissionError as exc:
                if process.poll() is None:
                    raise
                errors.append('Cannot clean up client process group: ' + str(exc))

        pending = memoryview(self._prompt.encode('utf-8'))
        self._prompt = None
        buffer = b''
        started = time.monotonic()
        stopping = None
        outcome = None
        with selectors.DefaultSelector() as selector:
            for pipe, mask, name in ((process.stdout, selectors.EVENT_READ, 'stdout'),
                                      (process.stderr, selectors.EVENT_READ, 'stderr'),
                                      (process.stdin, selectors.EVENT_WRITE, 'stdin')):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, mask, name)
            while selector.get_map() or process.poll() is None:
                now = time.monotonic()
                if outcome is None and (self._cancel.is_set() or now - started >= self._timeout):
                    outcome = 'cancelled' if self._cancel.is_set() else 'timeout'
                    signal_group(signal.SIGTERM)
                    stopping = now
                if self._log_error:
                    raise OSError('Cannot write run logs: ' + self._log_error)
                if stopping is not None and now - stopping >= .5:
                    signal_group(signal.SIGKILL)
                    break
                for key, _ in selector.select(.05):
                    pipe, name = key.fileobj, key.data
                    if name == 'stdin':
                        try:
                            count = os.write(pipe.fileno(), pending[:8192]) if pending else 0
                            pending = pending[count:]
                        except BrokenPipeError:
                            pending = pending[len(pending):]
                        if not pending:
                            selector.unregister(pipe)
                            pipe.close()
                        continue
                    chunk = os.read(pipe.fileno(), 65536)
                    if not chunk:
                        selector.unregister(pipe)
                        pipe.close()
                        continue
                    if name == 'stderr':
                        self._log('stderr.txt', chunk, STDERR_LIMIT)
                        continue
                    self._log('raw.jsonl', chunk, RAW_LIMIT)
                    buffer += chunk
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        if len(line) > LINE_LIMIT:
                            raise ValueError('Client JSONL line exceeds 1 MiB')
                        if line.strip():
                            self._native(_load(line))
                    if len(buffer) > LINE_LIMIT:
                        raise ValueError('Client JSONL line exceeds 1 MiB')
                if process.poll() is not None and stopping is None and selector.get_map():
                    # Bound draining when descendants retain pipes after the leader exits.
                    signal_group(signal.SIGTERM)
                    stopping = time.monotonic()
            if buffer.strip() and outcome is None:
                self._native(_load(buffer))
        process.wait(timeout=2)
        return outcome

    def _work(self):
        started = time.monotonic()
        created = _now()
        argv = []
        final_status = 'failed'
        errors = []
        try:
            if self._log_dir is not None:
                self._log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                for name in ('run.json', 'raw.jsonl', 'events.jsonl', 'stderr.txt'):
                    fd = os.open(self._log_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    self._logs[name] = os.fdopen(fd, 'wb')
            with tempfile.TemporaryDirectory(prefix='cg-agent-') as temp:
                argv = self._argv(Path(temp))
                self._emit('start', data=dict(client=self.client, mode=self.mode, cwd=self.cwd))
                self._metadata(argv, created, started)
                if self._cancel.is_set():
                    final_status = 'cancelled'
                else:
                    self._process = subprocess.Popen(argv, cwd=self.cwd, stdin=subprocess.PIPE,
                                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                                     shell=False, start_new_session=True)
                    self.status = 'running'
                    self._metadata(argv, created, started)
                    outcome = self._pump(errors)
                    if outcome:
                        final_status = outcome
                    elif self._process.returncode != 0:
                        raise ValueError('Client exited with code ' + str(self._process.returncode))
                    elif not self._terminal_seen or self._client_error:
                        raise ValueError('Client did not complete successfully')
                    else:
                        if self._schema is not None:
                            result = self._structured if self.client == 'claude' else _load(self._final_text)
                            _schema_check(self._schema, result)
                        else:
                            result = dict(text=self._final_text)
                        self.result = result
                        final_status = 'done'
        except Exception as exc:
            errors.append(str(exc))
        finally:
            if self._process is not None:
                try:
                    self._signal(signal.SIGKILL)
                    self._process.wait()
                except OSError as exc:
                    errors.append('Cannot clean up client process group: ' + str(exc))
                    if self._process.poll() is None:
                        final_status, self.result = 'failed', None
                finally:
                    for pipe in (self._process.stdin, self._process.stdout, self._process.stderr):
                        pipe.close()
            self._prompt = None
            with self._lock:
                if self._log_error and final_status == 'done':
                    final_status, self.result = 'failed', None
                if self._cancel.is_set() and final_status == 'done':
                    final_status, self.result = 'cancelled', None
                self.status = final_status
                try:
                    self._metadata(argv, created, started)
                except OSError as exc:
                    self.status, self.result = 'failed', None
                    errors.append('Cannot save run metadata: ' + str(exc))
                # Two reserved events: one bounded diagnostic for all final errors, then end.
                if errors:
                    limit = (TEXT_LIMIT - len(errors) + 1) // len(errors)
                    self._emit('error', '\n'.join(error[:limit] for error in errors), terminal=True)
                self._emit('end', data=dict(status=self.status, duration_s=round(time.monotonic() - started, 3)), terminal=True)
            for stream in self._logs.values():
                try:
                    stream.close()
                except OSError:
                    pass

    def _metadata(self, argv, created, started):
        stream = self._logs.get('run.json')
        if stream is not None:
            stream.seek(0)
            stream.truncate()
            stream.write(_json(dict(id=self.id, argv=argv, client=self.client, mode=self.mode,
                                    cwd=self.cwd, created=created, updated=_now(), status=self.status,
                                    truncated_logs=sorted(self._truncated_logs),
                                    duration_s=round(time.monotonic() - started, 3))).encode())
            stream.flush()
