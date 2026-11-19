"""Small stdio MCP adapter for the existing, non-executing graph commands."""
import argparse
import codecs
import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import selectors
import stat
import sys
import threading


MAX_RESPONSE = 40_000
MAX_REQUEST = 1_000_000
TRUNCATED = "\n[truncated: response limit reached; narrow the request]"
VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
_OUTPUT_LOCK = threading.Lock()
_LIBC = ctypes.CDLL(None)
_LIBC.fflush.argtypes = [ctypes.c_void_p]
_LIBC.fflush.restype = ctypes.c_int


def _property(kind, default=None, **limits):
    value = dict(type=kind, **limits)
    if default is not None:
        value['default'] = default
    return value


TEXT = _property('string')
SYMBOL = dict(symbol=TEXT)
CONF = _property('string', 'medium', enum=['high', 'medium', 'low'])
DEPTH = _property('integer', 3, minimum=1, maximum=5)
LIMIT = _property('integer', 40, minimum=1)
JSON = _property('boolean', True)

# Defaults mirror cg.main(). Root and dispatch are never client-controlled.
SPECS = {
    'cg_index': ('index', 'Index source files without executing them; writes only the graph cache.',
                 dict(full=_property('boolean', False)), []),
    'cg_find': ('find', 'Find indexed symbols by name or wildcard.',
                dict(pattern=TEXT, kind=TEXT, limit=LIMIT), ['pattern']),
    'cg_search': ('search', 'Retrieve bounded code evidence using local FTS5/BM25 and optional graph RRF; no model calls.',
                  dict(query=_property('string', minLength=1, maxLength=512),
                       mode=_property('string', 'hybrid', enum=['hybrid', 'lexical']),
                       limit=_property('integer', 10, minimum=1, maximum=50),
                       depth=_property('integer', 2, minimum=1, maximum=3), min_conf=CONF,
                       max_chars=_property('integer', 9000, minimum=64, maximum=16000), json=JSON), ['query']),
    'cg_locate': ('locate', 'Start here: from the text of a request or issue to the places to change, with their source, callers and tests, in one bounded answer; no model calls.',
                  dict(text=_property('string', minLength=1, maxLength=200000),
                       limit=_property('integer', 5, minimum=1, maximum=10),
                       max_chars=_property('integer', 5000, minimum=512, maximum=16000),
                       min_conf=CONF, json=_property('boolean', True)), ['text']),
    'cg_show': ('show', 'Show an indexed symbol, signature and neighbours.', SYMBOL, ['symbol']),
    'cg_callers': ('callers', 'Show callers and confidence; static evidence, not runtime proof.',
                   dict(**SYMBOL, min_conf=dict(CONF, default='low')), ['symbol']),
    'cg_callees': ('callees', 'Show callees and unresolved calls.',
                   dict(**SYMBOL, min_conf=dict(CONF, default='low')), ['symbol']),
    'cg_impact': ('impact', 'Find affected callers and candidate tests.',
                  dict(target=TEXT, file=_property('boolean', False), depth=DEPTH,
                       min_conf=CONF, limit=LIMIT, json=JSON), ['target']),
    'cg_verify_plan': ('verify', 'Propose relevant tests and argv from static runner evidence; never run them.',
                       dict(target=TEXT, file=_property('boolean', False),
                            plan=_property('boolean', True), min_conf=CONF, depth=DEPTH,
                            json=JSON), ['target']),
    'cg_prepare': ('prepare', 'Prepare bounded evidence for a feature or symbol.',
                   dict(feature=TEXT, symbol=TEXT, verify_symbol=_property('array', [], items=TEXT, maxItems=8),
                        intent=_property('string', ''), max_chars=_property('integer', 12000, minimum=1),
                        min_conf=CONF, depth=dict(DEPTH, default=2, maximum=3), json=JSON), []),
    'cg_flows': ('flows', 'Read the functional map: declared purposes linked to code with source witnesses; technical candidates are separate. Use cg_prepare on a declared ID for impact and tests.',
                 dict(query=_property('string', '', maxLength=200),
                      limit=_property('integer', 25, minimum=1, maximum=100), min_conf=CONF,
                      max_chars=_property('integer', 12000, minimum=512, maximum=16000), json=JSON), []),
    'cg_source': ('source', 'Read a bounded source window inside the indexed project.',
                  dict(path=TEXT, line=_property('integer', 1, minimum=1),
                       column=_property('integer', 0, minimum=0),
                       max_chars=_property('integer', 1200, minimum=1), json=JSON), ['path']),
    'cg_review': ('review', 'Inspect a diff and baseline; does not save baselines or execute tests.',
                  dict(base=TEXT, staged=_property('boolean', False), baseline=TEXT,
                       max_chars=_property('integer', 20000, minimum=1), min_conf=CONF,
                       depth=DEPTH, summary=_property('boolean', False), json=JSON), []),
    'cg_check': ('check', 'Report static candidates; not a compiler or test run.',
                 dict(limit=dict(LIMIT, default=60), json=JSON,
                      include_diagnostics=_property('boolean', False)), []),
    'cg_read': ('read', 'Read numbered file lines inside the project without executing code.',
                dict(path=TEXT, start_line=_property('integer', 1, minimum=1),
                     end_line=_property(['integer', 'null'], minimum=1)), ['path']),
}


class _Capture(io.TextIOBase):
    """Keep a bounded prefix even when a CLI prints a very large result."""
    def __init__(self):
        self.parts = []
        self.size = 0
        self.truncated = False
        self.incomplete = False

    def write(self, text):
        available = max(0, MAX_RESPONSE - self.size)
        if available:
            self.parts.append(text[:available])
        self.size += min(len(text), available)
        self.truncated |= len(text) > available
        return len(text)

    def value(self):
        return (''.join(self.parts) + (TRUNCATED if self.truncated else '') +
                ('\n[truncated: capture incomplete; flush failed or descendant pipe still open]'
                 if self.incomplete else ''))


@contextlib.contextmanager
def _capture_output(capture):
    """Capture fd 1/2; allow 1 s for EOF and .2 s to stop the nonblocking drain."""
    with _OUTPUT_LOCK, contextlib.ExitStack() as stack:
        sys.stdout.flush()
        sys.stderr.flush()
        _LIBC.fflush(None)
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, 'rb', buffering=0)
        os.set_blocking(read_fd, False)
        writer = stack.enter_context(os.fdopen(write_fd, 'w', encoding='utf-8', buffering=1, newline=''))
        saved = {}
        for fd in (1, 2):
            saved[fd] = os.dup(fd)
            stack.callback(os.close, saved[fd])

        stop = threading.Event()

        def drain():
            # Decode across read boundaries without universal-newline translation.
            decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
            with reader, selectors.DefaultSelector() as selector:
                selector.register(reader, selectors.EVENT_READ)
                while not stop.is_set():
                    if not selector.select(.05):
                        continue
                    chunk = reader.read(8192)
                    if chunk == b'':
                        break
                    if chunk:
                        capture.write(decoder.decode(chunk))
                capture.write(decoder.decode(b'', final=True))

        thread = threading.Thread(target=drain, daemon=True)
        thread.start()
        try:
            for fd in saved:
                os.dup2(writer.fileno(), fd)
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                try:
                    yield
                finally:
                    # Flush every wrapper independently while fd 1/2 still target the pipe.
                    for stream in (writer, sys.__stdout__, sys.__stderr__):
                        try:
                            if stream is not None:
                                stream.flush()
                        except (OSError, ValueError):
                            capture.incomplete = True
                    try:
                        if _LIBC.fflush(None) != 0:
                            capture.incomplete = True
                    except OSError:
                        capture.incomplete = True
        finally:
            for fd, original in saved.items():
                os.dup2(original, fd)
            writer.close()
            thread.join(1)
            if thread.is_alive():
                capture.incomplete = True
                stop.set()
                thread.join(.2)


def _validate(value, schema, name):
    expected = schema['type']
    types = expected if isinstance(expected, list) else [expected]
    matches = {'string': type(value) is str, 'integer': type(value) is int,
               'boolean': type(value) is bool, 'array': type(value) is list, 'null': value is None}
    if not any(matches.get(kind, False) for kind in types):
        raise ValueError(f'{name}: expected {expected}')
    if value is None:
        return
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{name}: invalid choice')
    for key, valid in [('minimum', lambda bound: value >= bound),
                       ('maximum', lambda bound: value <= bound)]:
        if key in schema and not valid(schema[key]):
            raise ValueError(f'{name}: outside allowed range')
    if expected == 'array':
        if len(value) > schema.get('maxItems', MAX_REQUEST):
            raise ValueError(f'{name}: too many items')
        for item in value:
            _validate(item, schema['items'], name)


def _read(root, args):
    """Open each path component relative to a directory fd, never following links."""
    part = Path(args.path)
    if part.is_absolute() or '..' in part.parts or not part.parts:
        raise ValueError('path must be a relative file inside root')
    if args.end_line is not None and args.end_line < args.start_line:
        raise ValueError('end_line precedes start_line')
    with contextlib.ExitStack() as stack:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        stack.callback(os.close, parent)
        for component in part.parts[:-1]:
            parent = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            stack.callback(os.close, parent)
        fd = os.open(part.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        stack.callback(os.close, fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('path must name a regular file')
        source = stack.enter_context(os.fdopen(fd, 'r', encoding='utf-8', errors='replace', closefd=False))
        text = ''
        number = 0
        while args.end_line is None or number < args.end_line:
            line = source.readline(MAX_RESPONSE + 1)
            if not line:
                break
            number += 1
            if number < args.start_line:
                while line and not line.endswith('\n'):
                    line = source.readline(MAX_RESPONSE + 1)
                continue
            numbered = str(number) + ': ' + line.rstrip('\n') + '\n'
            if len(json.dumps(text + numbered, ensure_ascii=False)) > 32000:
                if text:
                    return text + f'[truncated: next_line={number}]'
                lo, hi = 0, len(numbered)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if len(json.dumps(numbered[:mid], ensure_ascii=False)) <= 32000:
                        lo = mid
                    else:
                        hi = mid - 1
                return numbered[:lo] + f'\n[truncated within line {number}; remainder omitted; next_line={number + 1}]'
            text += numbered
        return text or '[no lines in requested range]'


def _call(engine, root, name, arguments):
    capture = _Capture()
    error = False
    try:
        if name not in SPECS:
            raise ValueError('Unknown tool: ' + str(name))
        cmd, _, properties, required = SPECS[name]
        if not isinstance(arguments, dict):
            raise ValueError('arguments must be an object')
        if set(arguments) - properties.keys():
            raise ValueError('Unknown arguments: ' + ', '.join(sorted(set(arguments) - properties.keys())))
        if any(key not in arguments for key in required):
            raise ValueError('Missing required arguments: ' + ', '.join(required))
        for key, value in arguments.items():
            _validate(value, properties[key], key)
        values = {key: prop.get('default') for key, prop in properties.items()}
        values.update(arguments)
        values.update(root=root, cmd=cmd)
        if cmd == 'prepare' and not (values['feature'] or values['symbol']):
            raise ValueError('Specify feature or symbol')
        if cmd == 'review':
            values.update(save_baseline=None, force=False)
        if cmd in ('review', 'verify'):
            values['_mcp_json'] = True  # preserve JSON even when the caller requests json:false
        args = argparse.Namespace(**values)
        with _capture_output(capture):
            if cmd == 'read':
                print(_read(root, args))
            elif cmd in ('callers', 'callees'):
                engine.cmd_edges(args, 'in' if cmd == 'callers' else 'out')
            elif cmd in ('prepare', 'source'):
                engine.cmd_features(args)
            elif cmd in ('review', 'verify'):
                engine.cmd_analysis(args)
            else:
                getattr(engine, 'cmd_' + cmd)(args)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            error = True
            capture.write('\n' + str(exc.code))
    except Exception as exc:
        error = True
        capture.write('\n' + str(exc))
    text = capture.value()
    # Keep the CLI JSON contract, including evidence and omission metadata. Only
    # remove serialization whitespace; source strings and textual tools are inert.
    # Search owns its serialization and exact character-budget accounting.
    if not error and name not in ('cg_search', 'cg_flows') and not capture.truncated and not capture.incomplete:
        if properties.get('json') and values.get('json'):
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n'
            except (ValueError, RecursionError):
                pass  # Mixed output or incomplete JSON must keep its diagnostics.
    return dict(content=[dict(type='text', text=text)], isError=error)


def _error(request_id, code, message):
    return dict(jsonrpc='2.0', id=request_id, error=dict(code=code, message=message))


def _dispatch(engine, root, request):
    if not isinstance(request, dict):
        return _error(None, -32600, 'Expected a JSON-RPC request object')
    request_id = request.get('id')
    if (request.get('jsonrpc') != '2.0' or not isinstance(request.get('method'), str)
            or ('id' in request and request_id is not None and type(request_id) not in (str, int))
            or (isinstance(request_id, str) and len(request_id) > 1000)):
        return _error(None, -32600, 'Invalid JSON-RPC request')
    if 'id' not in request:
        return None
    params = request.get('params', {})
    if not isinstance(params, dict):
        return _error(request_id, -32602, 'params must be an object')
    method = request['method']
    if method == 'initialize':
        version = params.get('protocolVersion')
        result = dict(protocolVersion=version if version in VERSIONS else VERSIONS[-1],
                      capabilities=dict(tools=dict(listChanged=False)),
                      serverInfo=dict(name='code-graph', version=engine.VERSION))
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = dict(tools=[dict(name=name, description=description,
                                 inputSchema=dict(type='object', properties=properties,
                                                  required=required, additionalProperties=False),
                                 annotations=dict(readOnlyHint=name != 'cg_index',
                                                  destructiveHint=False, openWorldHint=False))
                            for name, (_, description, properties, required) in SPECS.items()])
    elif method == 'tools/call':
        name = params.get('name')
        if not isinstance(name, str):
            return _error(request_id, -32602, 'Tool name must be a string')
        result = _call(engine, root, name, params.get('arguments', {}))
    else:
        return _error(request_id, -32601, 'Method not found')
    return dict(jsonrpc='2.0', id=request_id, result=result)


def _encode(response):
    encoded = json.dumps(response, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(encoded) <= MAX_RESPONSE:
        return encoded
    result = response.get('result', {})
    if isinstance(result, dict) and 'content' in result:
        text = result['content'][0]['text']
        # Escaped characters also count towards the wire budget.
        lo, hi = 0, min(len(text), MAX_RESPONSE)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            result['content'][0]['text'] = text[:mid] + TRUNCATED
            if len(json.dumps(response, ensure_ascii=False, separators=(',', ':'))) <= MAX_RESPONSE:
                lo = mid
            else:
                hi = mid - 1
        result['content'][0]['text'] = text[:lo] + TRUNCATED
        return json.dumps(response, ensure_ascii=False, separators=(',', ':'))
    return json.dumps(_error(None, -32600, 'Response exceeds limit'))


def serve(engine, root):
    """Serve newline-delimited JSON-RPC until stdin closes; no extra stdout."""
    root = str(Path(root).resolve(strict=True))
    protocol_out = sys.stdout
    while True:
        line = sys.stdin.readline(MAX_REQUEST + 1)
        if not line:
            return
        if len(line) > MAX_REQUEST:
            while line and not line.endswith('\n'):
                line = sys.stdin.readline(MAX_REQUEST + 1)
            response = _error(None, -32600, 'Request exceeds limit')
        else:
            try:
                def invalid_constant(value):
                    raise ValueError('Invalid JSON constant: ' + value)
                request = json.loads(line, parse_constant=invalid_constant)
                response = _dispatch(engine, root, request)
            except (ValueError, RecursionError):
                response = _error(None, -32700, 'Invalid JSON')
        if response is not None:
            protocol_out.write(_encode(response) + '\n')
            protocol_out.flush()
