#!/usr/bin/env python3
"""Synthetic CLI for subprocess tests. Never imports a model SDK or uses network."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def emit(value):
    print(json.dumps(value), flush=True)


if '--version' in sys.argv:
    print('fake-client 1.0')
    sys.exit(0)

options = json.load(sys.stdin)
if options.get('retain_pipes'):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], stdin=subprocess.DEVNULL)
    Path(options['retain_pipes']).write_text(str(child.pid))
client = 'codex' if '--json' in sys.argv else 'claude'
if options.get('observe'):
    observation = dict(argv=sys.argv[1:], cwd=os.getcwd(), prompt=options)
    for flag in ('--mcp-config', '--output-schema'):
        if flag in sys.argv:
            observation[flag] = json.loads(Path(sys.argv[sys.argv.index(flag) + 1]).read_text())
    Path(options['observe']).write_text(json.dumps(observation))

emit(dict(type='thread.started', thread_id='fake-codex-session') if client == 'codex' else
     dict(type='system', subtype='init', session_id='fake-claude-session'))

if options.get('hang'):
    if options.get('child'):
        heartbeat = options['child'] + '.heartbeat'
        child = subprocess.Popen([sys.executable, '-c',
                                  'import signal,time; from pathlib import Path; '
                                  'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                                  '\nwhile True: Path(' + repr(heartbeat) + ').write_text(str(time.monotonic())); time.sleep(.02)'])
        Path(options['child']).write_text(str(child.pid))
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)

if options.get('noise'):
    print('not JSON', flush=True)
    time.sleep(.1)
    sys.exit(0)
if options.get('oversize'):
    print('x' * (1024 * 1024 + 1), flush=True)
    sys.exit(0)
if options.get('stderr'):
    sys.stderr.write('e' * options['stderr'])
    sys.stderr.flush()

result = options.get('result', dict(answer='ok', count=2))
text = result if isinstance(result, str) else json.dumps(result)
if client == 'codex':
    emit(dict(type='item.completed', item=dict(id='r', type='reasoning', text='Consider the source.')))
    emit(dict(type='item.started', item=dict(id='t', type='mcp_tool_call', tool='cg_find', arguments=dict(pattern='work'))))
    emit(dict(type='item.completed', item=dict(id='t', type='mcp_tool_call', tool='cg_find', arguments=dict(pattern='work'), status='completed')))
    emit(dict(type='item.completed', item=dict(id='c', type='command_execution', command='fake command', exit_code=0, aggregated_output='fake output')))
    emit(dict(type='item.completed', item=dict(id='f', type='file_change', changes=[dict(path='src.py', kind='update')])) )
    for i in range(options.get('flood', 0)):
        emit(dict(type='item.completed', item=dict(id=str(i), type='reasoning', text='x' * 2000)))
    emit(dict(type='item.completed', item=dict(id='a', type='agent_message', text=text)))
    emit(dict(type='future.event', secret='must not enter normalized events'))
    if not options.get('missing_terminal'):
        if options.get('error'):
            emit(dict(type='turn.failed', error=dict(message='synthetic failure')))
        else:
            emit(dict(type='turn.completed', usage=options.get('usage', dict(input_tokens=100, output_tokens=20, cached_input_tokens=40, cache_write_input_tokens=12))))
else:
    emit(dict(type='assistant', message=dict(content=[dict(type='thinking', thinking='Consider the source.'),
               dict(type='text', text='Working.'), dict(type='tool_use', id='t', name='Read', input=dict(file_path='src.py'))])))
    emit(dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id='t', content='source')])) )
    emit(dict(type='future.event', secret='must not enter normalized events'))
    if not options.get('missing_terminal'):
        event = dict(type='result', subtype='error_during_execution' if options.get('error') else 'success',
                     is_error=bool(options.get('error')), result=text, session_id='fake-claude-session',
                     usage=options.get('usage', dict(input_tokens=10, output_tokens=20,
                                                    cache_read_input_tokens=40, cache_creation_input_tokens=50)))
        if '--json-schema' in sys.argv and not options.get('missing_structured'):
            event['structured_output'] = result
        emit(event)
sys.exit(options.get('exit', 0))
