"""Exercise the real stdio protocol and graph on unexecuted synthetic source."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest


REPO = Path(__file__).resolve().parents[1]
mcp = runpy.run_path(str(REPO / 'scripts/mcp_server.py'))


def request(method, params=None, request_id=1):
    return dict(jsonrpc='2.0', id=request_id, method=method, params=params or {})


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cg-mcp-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.marker = self.root / 'executed'
        (self.root / 'core.py').write_text('def work(value): return value\ndef caller(): return work(1)\n')
        (self.root / 'tests').mkdir()
        (self.root / 'tests/test_core.py').write_text('from core import work\ndef test_work(): assert work(2) == 2\n')
        (self.root / 'never_import.py').write_text('from pathlib import Path\nPath(' + repr(str(self.marker)) + ').touch()\n')

    def protocol(self, requests):
        completed = subprocess.run([sys.executable, '-I', str(REPO / 'scripts/cg.py'), '--root', str(self.root), 'mcp'],
                                   input=''.join((json.dumps(item) if not isinstance(item, str) else item) + '\n'
                                                 for item in requests),
                                   capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(self.marker.exists())
        return [json.loads(line) for line in completed.stdout.splitlines()]

    def call(self, name, args=None, request_id=1):
        return request('tools/call', dict(name=name, arguments=args or {}), request_id)

    def test_initialization_list_ping_notifications_and_clean_stdout(self):
        results = self.protocol([request('initialize', dict(protocolVersion='2025-06-18')),
                                 dict(jsonrpc='2.0', method='notifications/initialized'),
                                 request('tools/list', request_id=2), request('ping', request_id=3)])
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]['result']['protocolVersion'], '2025-06-18')
        self.assertEqual(results[-1]['result'], {})
        tools = results[1]['result']['tools']
        self.assertEqual(len(tools), 15)
        self.assertEqual({tool['name'] for tool in tools}, set(mcp['SPECS']))
        self.assertEqual([t['name'] for t in tools if not t['annotations']['readOnlyHint']], ['cg_index'])
        review = next(t for t in tools if t['name'] == 'cg_review')
        self.assertNotIn('save_baseline', review['inputSchema']['properties'])
        self.assertTrue(all(t['inputSchema']['additionalProperties'] is False for t in tools))

    def test_all_eleven_tools_use_existing_commands(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*.py')}
        calls = [('cg_index', {}), ('cg_find', dict(pattern='work')), ('cg_show', dict(symbol='work')),
                 ('cg_callers', dict(symbol='work')), ('cg_callees', dict(symbol='caller')),
                 ('cg_impact', dict(target='work')), ('cg_verify_plan', dict(target='work')),
                 ('cg_prepare', dict(symbol='work', intent='change return value')),
                 ('cg_source', dict(path='core.py', line=1)), ('cg_review', {}), ('cg_check', {})]
        results = self.protocol([self.call(name, args, i + 1) for i, (name, args) in enumerate(calls)])
        self.assertEqual(len(results), 11)
        for (name, _), value in zip(calls, results):
            self.assertFalse(value['result']['isError'], (name, value))
            self.assertTrue(value['result']['content'][0]['text'])
            self.assertLessEqual(len(json.dumps(value, ensure_ascii=False, separators=(',', ':'))), 40000)
        self.assertIn('core.py', results[1]['result']['content'][0]['text'])
        for result in results[5:]:
            self.assertIsInstance(json.loads(result['result']['content'][0]['text']), dict)
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*.py')})

    def test_search_cli_mcp_parity_and_read_only_index(self):
        self.protocol([self.call('cg_index')])
        path = self.root / '.code-graph/graph.db'
        before = path.read_bytes()
        cli = subprocess.run([sys.executable, '-I', str(REPO / 'scripts/cg.py'), '--root', str(self.root),
                              'search', 'work', '--json'], capture_output=True, text=True, timeout=20)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        response = self.protocol([self.call('cg_search', dict(query='work'))])[0]['result']
        self.assertFalse(response['isError'], response)
        self.assertEqual(json.loads(cli.stdout), json.loads(response['content'][0]['text']))
        self.assertEqual(before, path.read_bytes())

    def test_compact_json_keeps_complete_cli_evidence(self):
        self.protocol([self.call('cg_index')])
        cases = [
            ('cg_impact', dict(target='work'), ['impact', 'work', '--json']),
            ('cg_verify_plan', dict(target='work'), ['verify', 'work', '--plan', '--json']),
            ('cg_prepare', dict(symbol='work'), ['prepare', '--symbol', 'work', '--json']),
            ('cg_source', dict(path='core.py'), ['source', 'core.py', '--json']),
            ('cg_review', {}, ['review', '--json']),
            ('cg_check', {}, ['check', '--json']),
        ]
        for name, arguments, argv in cases:
            with self.subTest(name=name):
                cli = subprocess.run([sys.executable, '-I', str(REPO / 'scripts/cg.py'),
                                      '--root', str(self.root), *argv],
                                     capture_output=True, text=True, timeout=20)
                self.assertEqual(cli.returncode, 0, cli.stderr)
                result = self.protocol([self.call(name, arguments)])[0]['result']
                self.assertFalse(result['isError'], result)
                text = result['content'][0]['text']
                # All fields, nested snippets, hashes, confidence and omissions
                # remain equal; this deliberately forbids field pruning.
                self.assertEqual(json.loads(text), json.loads(cli.stdout))
                if name in ('cg_impact', 'cg_verify_plan', 'cg_review', 'cg_check'):
                    self.assertLess(len(text), len(cli.stdout))
                else:
                    self.assertEqual(text, cli.stdout)

    def test_compaction_preserves_source_strings_and_error_diagnostics(self):
        evidence = dict(file='quoted.py', line=7, confidence='low', hash='abc',
                        source='  spaced  \t \r\n 🌱 "quoted"',
                        omitted=0, incomplete=False, next_cursor=None)
        def emit(args):
            print(json.dumps(evidence, ensure_ascii=False, indent=2))
        result = mcp['_call'](SimpleNamespace(cmd_check=emit), str(self.root), 'cg_check', {})
        self.assertEqual(json.loads(result['content'][0]['text']), evidence)

        def fail(args):
            emit(args)
            raise RuntimeError('keep diagnostics')
        result = mcp['_call'](SimpleNamespace(cmd_check=fail), str(self.root), 'cg_check', {})
        self.assertTrue(result['isError'])
        self.assertIn(json.dumps(evidence, ensure_ascii=False, indent=2), result['content'][0]['text'])
        self.assertIn('keep diagnostics', result['content'][0]['text'])

    def test_validation_errors_keep_server_alive_and_reject_writes(self):
        invalid = [('missing', {}), ('cg_show', {}), ('cg_show', dict(symbol='missing')),
                   ('cg_source', dict(path='../outside')), ('cg_source', dict(path='core.py', line=True)),
                   ('cg_impact', dict(target='work', min_conf='certain')),
                   ('cg_review', dict(save_baseline='.code-graph/forbidden.json')),
                   ('cg_review', dict(force=True)), ('cg_index', dict(root='/')),
                   ('cg_find', dict(pattern='*', limit=-1)),
                   ('cg_search', dict(query='---')), ('cg_search', dict(query='work', depth=True)),
                   ('cg_search', dict(query='work', mode='semantic'))]
        responses = self.protocol([self.call('cg_index')] + [self.call(name, args, i + 2) for i, (name, args) in enumerate(invalid)]
                                  + [request('ping', request_id=99)])
        self.assertTrue(all(value['result']['isError'] for value in responses[1:-1]))
        self.assertEqual(responses[-1]['result'], {})
        self.assertFalse((self.root / '.code-graph/forbidden.json').exists())

    def test_search_json_survives_max_budget_and_escaped_rpc_id(self):
        text = '"\\🌱' * 500
        (self.root / 'quotes.py').write_text(''.join(f'def needle_{i}(): return {text!r}\n' for i in range(40)))
        responses = self.protocol([self.call('cg_index'),
            self.call('cg_search', dict(query='needle', limit=50, max_chars=16000), request_id='\x00' * 1000)])
        response = responses[-1]
        self.assertFalse(response['result']['isError'], response)
        content = response['result']['content'][0]['text']
        value = json.loads(content)
        self.assertEqual(value['format'], 'codegraph-search/2')
        self.assertEqual(len(content), value['budget']['actual_chars'])
        self.assertLessEqual(len(content), 16000)
        self.assertLessEqual(len(json.dumps(response, ensure_ascii=False, separators=(',', ':'))), 40000)

    def test_flows_matches_cli_and_keeps_bounded_unicode_json(self):
        features=[dict(id='pay',title='Payment "\\🌱',summary='Charge an order',entrypoints=[dict(file='core.py',symbol='work')]),
                  dict(id='buy',title='Checkout',entrypoints=[dict(file='core.py',symbol='caller')])]
        (self.root / '.code-graph-manifest.json').write_text(json.dumps(dict(format='codegraph-manifest/1',features=features)))
        responses=self.protocol([self.call('cg_index'),self.call('cg_flows',dict(max_chars=16000),request_id='\x00'*1000),
                                 self.call('cg_flows',dict(max_chars=512,json=False))])
        reply=responses[1]
        self.assertFalse(reply['result']['isError'],reply)
        text=reply['result']['content'][0]['text'];value=json.loads(text)
        self.assertEqual(value['format'],'codegraph-functional-map/1')
        self.assertEqual(len(text),value['budget']['actual_chars'])
        self.assertLessEqual(len(json.dumps(reply,ensure_ascii=False,separators=(',',':'))),40000)
        self.assertEqual(len(value['links']),1)
        result=subprocess.run([sys.executable,'-I',str(REPO/'scripts/cg.py'),'--root',str(self.root),'flows','--max-chars','16000','--json'],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,text)
        self.assertEqual(json.loads(responses[2]['result']['content'][0]['text'])['status'],'budget_insufficient')

    def test_flows_invalid_arguments_preserve_the_server(self):
        invalid=[dict(query='x'*201),dict(limit=True),dict(limit=101),dict(min_conf='certain'),dict(max_chars=511),dict(max_chars=16001),dict(root='/')]
        replies=self.protocol([self.call('cg_index')]+[self.call('cg_flows',args,i+2) for i,args in enumerate(invalid)]+[request('ping',request_id=99)])
        self.assertTrue(all(row['result']['isError'] for row in replies[1:-1]))
        self.assertEqual(replies[-1]['result'],{})

    def test_bad_json_and_rpc_requests_are_distinct_from_tool_errors(self):
        responses = self.protocol(['not-json', '[]', request('missing'),
                                  dict(jsonrpc='2.0', id=4, method='tools/call', params=[]),
                                  dict(jsonrpc='2.0', method='unknown-notification'), request('ping', request_id=5)])
        self.assertEqual([value['error']['code'] for value in responses[:-1]], [-32700, -32600, -32601, -32602])
        self.assertEqual(responses[-1]['id'], 5)

    def test_large_response_is_bounded_even_with_escaped_characters(self):
        def noisy(args):
            print('"\\\n' * 50000)
            print('captured stderr', file=sys.stderr)
        engine = SimpleNamespace(VERSION='test', cmd_find=noisy)
        incoming = io.StringIO(json.dumps(self.call('cg_find', dict(pattern='*'))) + '\n')
        outgoing, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(outgoing), contextlib.redirect_stderr(errors):
            saved = sys.stdin
            try:
                sys.stdin = incoming
                mcp['serve'](engine, self.root)
            finally:
                sys.stdin = saved
        self.assertLessEqual(len(outgoing.getvalue().strip()), 40000)
        value = json.loads(outgoing.getvalue())
        self.assertIn('[truncated:', value['result']['content'][0]['text'])
        self.assertEqual(errors.getvalue(), '')

    def test_native_fd_output_is_captured_and_protocol_survives(self):
        for pattern in ('normal', 'fail', 'large'):
            with self.subTest(pattern=pattern):
                requests = [self.call('cg_find', dict(pattern=pattern)), request('ping', request_id=2)]
                result = subprocess.run([sys.executable, '-I', str(REPO / 'tests/fixtures/mcp_noisy_command.py'),
                                         str(REPO / 'scripts/mcp_server.py'), str(self.root)],
                                        input=''.join(json.dumps(row) + '\n' for row in requests),
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, 'fd2 restored\n')
                lines = result.stdout.splitlines()
                self.assertEqual(len(lines), 2, result.stdout[:1000])
                try:
                    reply, ping = [json.loads(line) for line in lines]
                except ValueError:
                    self.fail('Native output corrupted JSON-RPC: ' + result.stdout[:1000])
                self.assertEqual(ping['id'], 2)
                self.assertEqual(ping['result'], {})
                self.assertEqual(reply['result']['isError'], pattern == 'fail')
                text = reply['result']['content'][0]['text']
                self.assertNotIn('fd2 restored', text)
                for message in ('fd stdout', 'fd stderr', 'child stdout', 'python stdout', 'python stderr'):
                    self.assertIn(message, text)
                if pattern == 'large':
                    self.assertIn('[truncated:', text)
                    self.assertNotIn('\ufffd', text)
                else:
                    self.assertIn('buffered libc', text)
                if pattern == 'fail':
                    self.assertIn('command failed', text)
                self.assertLessEqual(len(lines[0]), mcp['MAX_RESPONSE'])

    def noisy_capture(self, pattern):
        requests = [self.call('cg_find', dict(pattern=pattern)), request('ping', request_id=2)]
        proc = subprocess.Popen([sys.executable, '-I', str(REPO / 'tests/fixtures/mcp_noisy_command.py'),
                                 str(REPO / 'scripts/mcp_server.py'), str(self.root)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        try:
            stdout, stderr = proc.communicate((''.join(json.dumps(row) + '\n' for row in requests)).encode(),
                                              timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate(timeout=3)
            self.fail('MCP capture did not return while a descendant retained its pipe')
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertEqual(stderr, b'fd2 restored\n')
        try:
            reply, ping = [json.loads(line) for line in stdout.splitlines()]
        except (ValueError, TypeError):
            self.fail('Capture leaked into JSON-RPC: ' + repr(stdout[:1000]))
        self.assertEqual(ping['result'], {})
        return reply['result']

    def test_capture_preserves_crlf_and_carriage_returns(self):
        result = self.noisy_capture('newlines')
        self.assertFalse(result['isError'])
        self.assertEqual(result['content'][0]['text'], 'raw\r\nreturn\rend\npython\r\nreturn\rend\n')

    def test_original_python_streams_flush_before_fd_restoration(self):
        result = self.noisy_capture('original-streams')
        self.assertFalse(result['isError'])
        text = result['content'][0]['text']
        for message in ('original stdout pending', 'original stderr pending', 'buffered libc'):
            self.assertIn(message, text)

    def test_failed_original_stream_flush_does_not_skip_other_flushes(self):
        result = self.noisy_capture('flush-error')
        text = result['content'][0]['text']
        self.assertIn('original stderr pending', text)
        self.assertIn('buffered libc', text)
        self.assertIn('[truncated:', text)

    def test_descendant_retaining_pipe_cannot_block_next_rpc(self):
        result = self.noisy_capture('retained-pipe')
        self.assertIn('before descendant \U0001f600\r\n', result['content'][0]['text'])
        self.assertIn('[truncated:', result['content'][0]['text'])

    def test_system_exit_and_exception_become_tool_errors(self):
        for error in (SystemExit('not indexed'), RuntimeError('failure')):
            def fail(args):
                print('partial output')
                raise error
            result = mcp['_call'](SimpleNamespace(cmd_find=fail), str(self.root), 'cg_find', dict(pattern='*'))
            self.assertTrue(result['isError'])
            self.assertIn('partial output', result['content'][0]['text'])

    def test_oversized_request_is_drained_then_ping_works(self):
        responses = self.protocol(['x' * (mcp['MAX_REQUEST'] + 10), request('ping', request_id=7)])
        self.assertEqual(responses[0]['error']['code'], -32600)
        self.assertEqual(responses[1]['id'], 7)

    def test_read_unindexed_file_lines_and_refuse_symlinks(self):
        (self.root / 'notes.txt').write_text('one\ntwo\nthree\nfour\n')
        (self.root / 'link').symlink_to(self.root / 'notes.txt')
        (self.root / 'directory-link').symlink_to(self.root / 'tests', target_is_directory=True)
        results = self.protocol([self.call('cg_read', dict(path='notes.txt', start_line=2, end_line=3)),
                                 self.call('cg_read', dict(path='notes.txt', end_line=None)),
                                 self.call('cg_read', dict(path='link')),
                                 self.call('cg_read', dict(path='directory-link/test_core.py')),
                                 self.call('cg_read', dict(path='../outside')),
                                 self.call('cg_read', dict(path=str(self.root / 'notes.txt'))),
                                 self.call('cg_read', dict(path='notes.txt', start_line=3, end_line=1))])
        self.assertEqual(results[0]['result']['content'][0]['text'].strip(), '2: two\n3: three')
        self.assertIn('4: four', results[1]['result']['content'][0]['text'])
        self.assertTrue(all(value['result']['isError'] for value in results[2:]))
        self.assertFalse((self.root / '.code-graph').exists())

    def test_read_truncation_keeps_continuation_even_with_escaping(self):
        (self.root / 'many.txt').write_text(('"\\\t' * 20 + '\n') * 1000)
        (self.root / 'long.txt').write_text('x' * 100000 + '\nend\n')
        results = self.protocol([self.call('cg_read', dict(path='many.txt')),
                                 self.call('cg_read', dict(path='long.txt')),
                                 self.call('cg_read', dict(path='long.txt', start_line=2))])
        for value in results[:2]:
            self.assertFalse(value['result']['isError'])
            self.assertIn('next_line=', value['result']['content'][0]['text'])
            self.assertLessEqual(len(json.dumps(value, ensure_ascii=False)), 40000)
        self.assertIn('remainder omitted', results[1]['result']['content'][0]['text'])
        self.assertEqual(results[2]['result']['content'][0]['text'].strip(), '2: end')

    def test_large_rpc_id_cannot_bypass_response_limit(self):
        result = self.protocol([request('ping', request_id='x' * 50000)])[0]
        self.assertEqual(result['error']['code'], -32600)


if __name__ == '__main__':
    unittest.main()
