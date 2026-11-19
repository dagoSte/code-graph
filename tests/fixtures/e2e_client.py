#!/usr/bin/env python3
"""Fake model boundary: real argv, stdin, graph MCP and worktree file operations.

Only this executable is substituted in integration tests. It never invokes Codex,
Claude, a provider SDK or an external service. Recognized test intents deliberately
produce either a valid edit, a failing regression or an out-of-scope file.
"""
import json
import hashlib
from pathlib import Path
import subprocess
import sys


def emit(value):
    print(json.dumps(value), flush=True)


def flag(name):
    return sys.argv[sys.argv.index(name) + 1]


def graph_read(client):
    if client == 'codex':
        overrides = [sys.argv[i + 1] for i, arg in enumerate(sys.argv[:-1]) if arg == '-c']
        config = dict(item.split('=', 1) for item in overrides)
        command = json.loads(config['mcp_servers.code-graph.command'])
        arguments = json.loads(config['mcp_servers.code-graph.args'])
    else:
        config = json.loads(Path(flag('--mcp-config')).read_text())['mcpServers']['code-graph']
        command, arguments = config['command'], config['args']
    assert arguments == ['--root', str(Path.cwd()), 'mcp'], arguments
    requests = [
        dict(jsonrpc='2.0', id=1, method='initialize',
             params=dict(protocolVersion='2025-06-18', capabilities={}, clientInfo=dict(name='e2e-fixture', version='1'))),
        dict(jsonrpc='2.0', method='notifications/initialized'),
        dict(jsonrpc='2.0', id=2, method='tools/list'),
        dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name='cg_search', arguments=dict(query='value', limit=5))),
        dict(jsonrpc='2.0', id=4, method='tools/call', params=dict(name='cg_read', arguments=dict(path='app.py'))),
        dict(jsonrpc='2.0', id=5, method='ping'),
    ]
    result = subprocess.run([command, *arguments],
                            input=''.join(json.dumps(row) + '\n' for row in requests),
                            capture_output=True, text=True, timeout=10, check=True)
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [row['id'] for row in replies] == [1, 2, 3, 4, 5], replies
    assert 'tools' in replies[0]['result']['capabilities'], replies
    assert 'cg_read' in [tool['name'] for tool in replies[1]['result']['tools']], replies
    assert not replies[2]['result']['isError'], replies[2]
    hits = json.loads(replies[2]['result']['content'][0]['text'])['items']
    target = next(item for item in hits if item['symbol']['name'] == 'value')
    assert target['source']['source_sha256'] == hashlib.sha256(Path('app.py').read_bytes()).hexdigest()
    assert not replies[3]['result']['isError'], replies[3]
    source = replies[3]['result']['content'][0]['text']
    assert 'def value():' in source, source
    if client == 'codex':
        emit(dict(type='item.completed', item=dict(id='search', type='mcp_tool_call', tool='cg_search',
                                                  arguments=dict(query='value', limit=5), status='completed')))
        emit(dict(type='item.completed', item=dict(id='read', type='mcp_tool_call', tool='cg_read',
                                                  arguments=dict(path='app.py'), status='completed')))
    else:
        emit(dict(type='assistant', message=dict(content=[dict(type='tool_use', id='search',
                  name='mcp__code-graph__cg_search', input=dict(query='value', limit=5))])))
        emit(dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id='search',
                  content=replies[2]['result']['content'][0]['text'])])))
        emit(dict(type='assistant', message=dict(content=[dict(type='tool_use', id='read',
                  name='mcp__code-graph__cg_read', input=dict(path='app.py'))])))
        emit(dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id='read', content=source)])))
    return source


def main():
    if '--version' in sys.argv:
        print('code-graph e2e fixture 1')
        return
    client = 'codex' if '--json' in sys.argv else 'claude'
    schema = None
    if '--output-schema' in sys.argv:
        schema = json.loads(Path(flag('--output-schema')).read_text())
    elif '--json-schema' in sys.argv:
        schema = json.loads(flag('--json-schema'))
    properties = schema['properties'] if schema else {}
    mode = flag('-s') if client == 'codex' else flag('--permission-mode')
    step = ('plan' if 'touch' in properties else 'review' if 'verdict' in properties else
            'ask' if mode in ('read-only', 'plan') else 'edit')
    assert mode == ({'plan': 'read-only', 'review': 'read-only', 'ask': 'read-only', 'edit': 'workspace-write'}[step]
                    if client == 'codex' else {'plan': 'plan', 'review': 'plan', 'ask': 'plan', 'edit': 'acceptEdits'}[step])
    prompt = sys.stdin.read()
    assert prompt and prompt not in sys.argv, 'Prompt must arrive on stdin'
    assert 'cg_search' in prompt and 'cg_source/cg_read' in prompt, 'Missing retrieval guidance'
    assert {'plan': 'Prepare a change plan only.', 'edit': 'Implement the approved plan only',
            'review': 'Review without edits', 'ask': 'Read only:'}[step] in prompt, prompt
    emit(dict(type='thread.started', thread_id='e2e-codex-' + step) if client == 'codex' else
         dict(type='system', subtype='init', session_id='e2e-claude-' + step))
    source = graph_read(client)
    if step == 'ask':
        assert schema is None
        assert 'Question: What does value return?' in prompt
        assert 'Selected symbol:' in prompt
        assert 'return 1' in source
        result = 'app.py:1: ' + source.strip()
    elif step == 'plan':
        assert 'return 1' in source
        result = dict(summary='Increase value while preserving positive results',
                      touch=[dict(path='app.py', symbol='value', line=1, why='Change the returned value')],
                      affected=[], tests=[dict(path='tests/test_app.py', name='TestApp.test_positive', why='Positive-value contract')],
                      out_of_scope=['extra.txt'], risks=[], unknowns=[],
                      test_command=[sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests'])
    elif step == 'edit':
        assert (Path.cwd() / '.git').is_file(), 'The fixture may edit only a linked worktree'
        assert 'return 1' in source
        value = -1 if 'E2E_BREAK_TEST' in prompt else 2
        (Path.cwd() / 'app.py').write_text(f'def value():\n    return {value}\n')
        if 'E2E_OUTSIDE_SCOPE' in prompt:
            (Path.cwd() / 'extra.txt').write_text('Intentional perimeter violation\n')
        result = 'Edited app.py in the isolated worktree'
        if client == 'codex':
            emit(dict(type='item.completed', item=dict(id='edit', type='file_change',
                      changes=[dict(path='app.py', kind='update')])) )
    else:
        assert 'return 2' in source or 'return -1' in source
        # Even an accepting reviewer must not override a blocking verification.
        result = dict(verdict='accept', summary='Synthetic reviewer accepts the supplied diff', findings=[])
    text = result if isinstance(result, str) else json.dumps(result)
    if client == 'codex':
        emit(dict(type='item.completed', item=dict(id='answer', type='agent_message', text=text)))
        emit(dict(type='turn.completed', usage=dict(input_tokens=120, cached_input_tokens=30, output_tokens=24)))
    else:
        terminal = dict(type='result', subtype='success', is_error=False, result=text,
                        session_id='e2e-claude-' + step,
                        usage=dict(input_tokens=90, cache_read_input_tokens=30,
                                   cache_creation_input_tokens=0, output_tokens=24))
        if schema:
            terminal['structured_output'] = result
        emit(terminal)


if __name__ == '__main__':
    main()
