"""CLI integration tested with real subprocesses and synthetic JSONL only."""
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


REPO = Path(__file__).resolve().parents[1]
agents = runpy.run_path(str(REPO / 'scripts/agents.py'))
SCHEMA = dict(type='object', properties=dict(answer=dict(type='string', enum=['ok']),
                                            count=dict(type='integer')),
              required=['answer', 'count'], additionalProperties=False)


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cg-agent-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project space'
        self.project.mkdir()
        self.fake = self.root / 'fake client'
        source = (REPO / 'tests/fixtures/fake_clients/client.py').read_text()
        self.fake.write_text('#!' + sys.executable + '\n' + source.split('\n', 1)[1])
        self.fake.chmod(0o700)

    def start(self, client='codex', options=None, **kwargs):
        defaults = dict(mode='read', cwd=str(self.project), root=str(self.project),
                        prompt=json.dumps(options or {}), executable=str(self.fake), timeout=5)
        defaults.update(kwargs)
        run = agents['start'](client, **defaults)
        def cleanup():
            run.cancel()
            run.wait(3)
        self.addCleanup(cleanup)
        return run

    def complete(self, client='codex', options=None, **kwargs):
        run = self.start(client, options, **kwargs)
        self.assertEqual(run.wait(10), 'done', run.events())
        return run

    def worktree(self):
        def git(*argv):
            return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(self.project), *argv],
                                  capture_output=True, check=True)
        git('init', '-q')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-q', '--allow-empty', '-m', 'base')
        tree = self.root / 'linked tree'
        git('worktree', 'add', '-q', '-b', 'fixture-lane', str(tree))
        return tree

    def test_detect_only_uses_version_and_reports_missing(self):
        with patch('shutil.which', side_effect=[str(self.fake), None]):
            clients = agents['detect']()
        self.assertTrue(clients[0]['available'])
        self.assertEqual(clients[0]['version'], 'fake-client 1.0')
        self.assertFalse(clients[1]['available'])
        self.assertIsNone(clients[1]['path'])

    def test_detect_exited_group_eperm_preserves_version_results(self):
        popen = subprocess.Popen
        for exit_code in (0, 7):
            self.fake.write_text('#!' + sys.executable + '\nimport sys\nprint("fixture version")\nsys.exit(' + str(exit_code) + ')\n')
            for recovery in ('gone', 'delivered', 'denied'):
                with self.subTest(exit_code=exit_code, recovery=recovery):
                    processes, attempts = {}, {}

                    def spawn(*args, **kwargs):
                        proc = popen(*args, **kwargs)
                        processes[proc.pid] = proc
                        self.addCleanup(proc.stdout.close)
                        self.addCleanup(proc.stderr.close)
                        return proc

                    def signal_group(pid, sig):
                        self.assertEqual(sig, signal.SIGKILL)
                        self.assertIsNotNone(processes[pid].poll())
                        attempts[pid] = attempts.get(pid, 0) + 1
                        if attempts[pid] == 1 or recovery == 'denied':
                            raise PermissionError(1, 'Synthetic detect cleanup EPERM')
                        if recovery == 'gone':
                            raise ProcessLookupError(3, 'Group gone')

                    with patch('shutil.which', return_value=str(self.fake)), \
                            patch('subprocess.Popen', side_effect=spawn), patch('os.killpg', side_effect=signal_group):
                        try:
                            clients = agents['detect']()
                        except PermissionError as exc:
                            self.fail('detect escaped instead of returning both clients: ' + str(exc))
                    self.assertEqual([c['client'] for c in clients], ['codex', 'claude'])
                    self.assertEqual(list(attempts.values()), [agents['EXITED_GROUP_ATTEMPTS'] if recovery == 'denied' else 2] * 2)
                    for item in clients:
                        self.assertEqual(item['available'], exit_code == 0)
                        self.assertEqual(item['version'], 'fixture version' if exit_code == 0 else None)
                        self.assertEqual('cleanup EPERM' in (item['reason'] or ''), recovery == 'denied')
                        if exit_code:
                            self.assertIn('exit 7', item['reason'])
                    self.assertTrue(all(p.stdout.closed and p.stderr.closed for p in processes.values()))

    def test_detect_timeout_cleanup_denial_is_bounded_and_keeps_failure(self):
        for leader_status in (None, 0):
            with self.subTest(leader_status=leader_status):
                proc = Mock(pid=12345)
                proc.communicate.side_effect = subprocess.TimeoutExpired('fixture --version', .01)
                proc.poll.return_value = leader_status
                with patch('shutil.which', side_effect=[str(self.fake), None]), \
                        patch('subprocess.Popen', return_value=proc), \
                        patch('os.killpg', side_effect=PermissionError(1, 'Denied')) as killpg:
                    try:
                        clients = agents['detect'](.01)
                    except PermissionError as exc:
                        self.fail('detect timeout escaped cleanup: ' + str(exc))
                self.assertEqual(killpg.call_count, 1 if leader_status is None else agents['EXITED_GROUP_ATTEMPTS'])
                self.assertFalse(clients[0]['available'])
                self.assertIsNone(clients[0]['version'])
                self.assertIn('timed out', clients[0]['reason'])
                self.assertIn('Denied', clients[0]['reason'])
                for call in proc.wait.call_args_list:
                    self.assertGreater(call.kwargs['timeout'], 0)
                    self.assertLessEqual(call.kwargs['timeout'], 2)
                proc.stdout.close.assert_called_once()
                proc.stderr.close.assert_called_once()
                self.assertEqual(clients[1]['reason'], 'Executable not found on PATH')

    def test_agents_api_returns_clients_after_exited_group_eperm(self):
        import test_workbench as fixtures
        client = fixtures.WorkbenchTests('runTest')
        self.addCleanup(client.doCleanups)
        client.setUp()
        client.server.session._runner = SimpleNamespace(detect=agents['detect'])
        with patch('shutil.which', side_effect=[str(self.fake), None]), \
                patch('os.killpg', side_effect=PermissionError(1, 'Synthetic API cleanup EPERM')) as killpg:
            code, reply = client.request('/api/agents')
        self.assertEqual(code, 200, reply)
        self.assertEqual(killpg.call_count, agents['EXITED_GROUP_ATTEMPTS'])
        self.assertEqual(len(reply['clients']), 2)
        self.assertTrue(reply['clients'][0]['available'])
        self.assertEqual(reply['clients'][0]['version'], 'fake-client 1.0')
        self.assertIn('cleanup EPERM', reply['clients'][0]['reason'])

    def test_both_streams_schema_usage_sequence_and_resume(self):
        for client in ('codex', 'claude'):
            with self.subTest(client=client):
                run = self.complete(client, output_schema=SCHEMA)
                self.assertEqual(run.result, dict(answer='ok', count=2))
                self.assertEqual(run.usage, dict(input_tokens=100, output_tokens=20, cached_input_tokens=40,
                                                 cache_write_input_tokens=12 if client == 'codex' else None,
                                                 measured=True))
                self.assertEqual(run.resume_command(), [client, 'resume' if client == 'codex' else '--resume', 'fake-' + client + '-session'])
                events = run.events()
                self.assertEqual([e['seq'] for e in events], list(range(1, len(events) + 1)))
                self.assertEqual(events[0]['kind'], 'start')
                self.assertEqual(events[-1]['kind'], 'end')
                self.assertEqual(events[-1]['data']['status'], 'done')
                self.assertEqual(run.events(events[-2]['seq']), events[-1:])
                self.assertTrue(all(e['v'] == 1 and e['run'] == run.id and e['t'].endswith('Z') for e in events))
                self.assertTrue({'reasoning', 'message', 'tool', 'usage'} <= {e['kind'] for e in events})
                self.assertEqual([e['data'] for e in events if e['kind'] == 'other'], [dict(type='future.event')])
                events[0]['data']['client'] = 'mutated'
                self.assertEqual(run.events()[0]['data']['client'], client)
                run.cancel()
                self.assertEqual(run.status, 'done')

    def test_read_flags_stdin_logs_and_temporary_config_cleanup(self):
        for client in ('codex', 'claude'):
            observation = self.root / (client + '.json')
            logs = self.root / (client + '-logs')
            prompt = dict(observe=str(observation), result='prompt sentinel')
            run = self.complete(client, prompt, log_dir=logs, model='test-model')
            seen = json.loads(observation.read_text())
            self.assertEqual(seen['prompt'], prompt)
            self.assertEqual(Path(seen['cwd']).resolve(), self.project)
            argv = seen['argv']
            self.assertNotIn(json.dumps(prompt), argv)
            self.assertTrue(agents['FORBIDDEN'].isdisjoint(argv))
            self.assertEqual(argv[argv.index('--model') + 1], 'test-model')
            if client == 'codex':
                self.assertEqual(argv[argv.index('-s') + 1], 'read-only')
                self.assertIn('approval_policy="never"', argv)
                self.assertIn('features.hooks=false', argv)
                self.assertIn('features.shell_tool=false', argv)
                self.assertIn('--ignore-rules', argv)
                self.assertIn('mcp_servers.code-graph.args=' + json.dumps(['--root', str(self.project), 'mcp'], separators=(',', ':')), argv)
            else:
                self.assertEqual(argv[argv.index('--permission-mode') + 1], 'plan')
                self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Grep,Glob')
                self.assertIn('--strict-mcp-config', argv)
                config = seen['--mcp-config']['mcpServers']['code-graph']
                self.assertEqual(config['args'], ['--root', str(self.project), 'mcp'])
                self.assertEqual(config['command'], str(REPO / 'scripts/cg'))
                self.assertFalse(Path(argv[argv.index('--mcp-config') + 1]).exists())
            metadata = json.loads((logs / 'run.json').read_text())
            self.assertEqual(metadata['status'], 'done')
            self.assertNotIn('prompt sentinel', (logs / 'run.json').read_text())
            self.assertNotIn('env', metadata)
            self.assertEqual([json.loads(line) for line in (logs / 'events.jsonl').read_text().splitlines()], run.events())
            self.assertTrue((logs / 'raw.jsonl').stat().st_size)
            self.assertEqual((logs / 'run.json').stat().st_mode & 0o777, 0o600)

    def test_write_requires_linked_worktree_and_uses_its_root(self):
        with self.assertRaises(ValueError):
            self.start(mode='write')
        tree = self.worktree()
        with self.assertRaises(ValueError):
            self.start(mode='write')
        with self.assertRaises(ValueError):
            self.start(mode='write', cwd=str(tree), root=str(self.project))
        subdir = tree / 'sub'
        subdir.mkdir()
        with self.assertRaises(ValueError):
            self.start(mode='write', cwd=str(subdir), root=str(subdir))
        for client in ('codex', 'claude'):
            observation = self.root / (client + '-write.json')
            self.complete(client, dict(observe=str(observation)), mode='write', cwd=str(tree), root=str(tree))
            argv = json.loads(observation.read_text())['argv']
            if client == 'codex':
                self.assertEqual(argv[argv.index('-s') + 1], 'workspace-write')
                self.assertEqual(argv[argv.index('-C') + 1], str(tree))
            else:
                self.assertEqual(argv[argv.index('--permission-mode') + 1], 'acceptEdits')
                self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Grep,Glob,Edit,Write')
                self.assertNotIn('Bash', argv[argv.index('--allowedTools') + 1])

    def test_budget_and_schema_flags(self):
        for client in ('codex', 'claude'):
            observation = self.root / (client + '-schema.json')
            self.complete(client, dict(observe=str(observation)), output_schema=SCHEMA,
                          **(dict(budget_usd=1.25) if client == 'claude' else {}))
            seen = json.loads(observation.read_text())
            if client == 'codex':
                self.assertEqual(seen['--output-schema'], SCHEMA)
                self.assertFalse(Path(seen['argv'][seen['argv'].index('--output-schema') + 1]).exists())
            else:
                self.assertEqual(seen['argv'][seen['argv'].index('--max-budget-usd') + 1], '1.25')
                self.assertEqual(json.loads(seen['argv'][seen['argv'].index('--json-schema') + 1]), SCHEMA)

    def test_invalid_schema_result_never_becomes_success(self):
        for client in ('codex', 'claude'):
            for value in ({'answer': 'ok'}, {'answer': 'wrong', 'count': 2},
                          {'answer': 'ok', 'count': True}, {'answer': 'ok', 'count': 1.5},
                          {'answer': 'ok', 'count': 2, 'extra': 1}, 'not json'):
                with self.subTest(client=client, value=value):
                    run = self.start(client, dict(result=value), output_schema=SCHEMA)
                    self.assertEqual(run.wait(5), 'failed')
                    self.assertIsNone(run.result)
        run = self.start('claude', dict(missing_structured=True), output_schema=SCHEMA)
        self.assertEqual(run.wait(5), 'failed')

    def test_no_false_success_for_malformed_missing_or_failed_streams(self):
        for client in ('codex', 'claude'):
            for options in (dict(noise=True), dict(oversize=True), dict(error=True),
                            dict(exit=7), dict(missing_terminal=True)):
                with self.subTest(client=client, options=options):
                    run = self.start(client, options)
                    self.assertEqual(run.wait(5), 'failed', run.events())
                    self.assertIsNone(run.result)
                    self.assertEqual(len([e for e in run.events() if e['kind'] == 'end']), 1)

    def test_usage_missing_is_not_estimated(self):
        for client in ('codex', 'claude'):
            for usage in ({}, dict(input_tokens=True, output_tokens=3),
                          dict(input_tokens=5, output_tokens=3, cache_write_input_tokens=-1) if client == 'codex' else {}):
                run = self.complete(client, dict(usage=usage))
                self.assertIsNone(run.usage)
                self.assertNotIn('usage', [e['kind'] for e in run.events()])

    def test_timeout_returns_immediately_and_records_end(self):
        started = time.monotonic()
        run = self.start(options=dict(hang=True), timeout=.15)
        self.assertLess(time.monotonic() - started, .1)
        self.assertIn(run.wait(.01), ('starting', 'running'))
        self.assertEqual(run.wait(3), 'timeout')
        self.assertIsNone(run.result)

    def test_cancel_kills_owned_group_and_preserves_unrelated_process(self):
        child_pid = self.root / 'child.pid'
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        self.addCleanup(lambda: (unrelated.terminate(), unrelated.wait()))
        run = self.start(options=dict(hang=True, child=str(child_pid)))
        heartbeat = Path(str(child_pid) + '.heartbeat')
        deadline = time.monotonic() + 3
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(heartbeat.exists())
        run.cancel()
        self.assertEqual(run.wait(3), 'cancelled')
        self.assertIsNone(unrelated.poll())
        stopped_at = heartbeat.read_text()
        time.sleep(.15)
        self.assertEqual(heartbeat.read_text(), stopped_at)
        self.assertEqual(run.events()[-1]['data']['status'], 'cancelled')

    def test_cancel_cleanup_does_not_resignal_a_killed_group(self):
        run = agents['AgentRun']('codex', 'read', str(self.project), str(self.project),
                                  '{}', None, 5, None, None, None, str(self.fake))
        killpg = os.killpg
        killed = []

        def signal_group(pid, sig):
            if killed and run._process.poll() is not None:
                raise PermissionError(1, 'Synthetic macOS terminated-group EPERM')
            killpg(pid, sig)
            if sig == signal.SIGKILL:
                killed.append(pid)

        def stopped(errors):
            run._signal(signal.SIGKILL)
            run._process.wait(timeout=3)
            run._cancel.set()
            return 'cancelled'

        with patch('os.killpg', side_effect=signal_group), patch.object(run, '_pump', side_effect=stopped):
            try:
                run._work()
            except PermissionError as exc:
                self.fail('Cleanup signalled an already killed group: ' + str(exc))
        self.assertEqual(killed, [run._process.pid])
        self.assertEqual(run.status, 'cancelled')
        self.assertEqual(run.events()[-1]['data']['status'], 'cancelled')
        self.assertFalse(any(event['kind'] == 'error' for event in run.events()))

    def test_signal_permission_error_without_confirmed_kill_is_not_hidden(self):
        for leader_status, attempts in ((None, 1), (0, agents['EXITED_GROUP_ATTEMPTS'])):
            with self.subTest(leader_status=leader_status):
                run = agents['AgentRun']('codex', 'read', str(self.project), str(self.project),
                                          '{}', None, 5, None, None, None, str(self.fake))
                run._process = Mock(pid=12345)
                run._process.poll.return_value = leader_status
                with patch('os.killpg', side_effect=PermissionError(1, 'Denied')) as killpg:
                    with self.assertRaises(PermissionError):
                        run._signal(signal.SIGKILL)
                self.assertEqual(killpg.call_count, attempts)

    def test_cleanup_eperm_after_exit_preserves_terminal_outcome(self):
        for expected, options, timeout in (('done', {}, 5), ('failed', {'noise': True}, 5),
                                           ('timeout', {'hang': True}, .05)):
            for cleanup in ('gone', 'delivered', 'denied'):
                with self.subTest(expected=expected, cleanup=cleanup):
                    run = agents['AgentRun']('codex', 'read', str(self.project), str(self.project),
                                              json.dumps(options), None, timeout, None, None, None, str(self.fake))
                    pump, killpg = run._pump, os.killpg
                    attempts = []

                    def after_exit(errors):
                        try:
                            return pump(errors)
                        finally:
                            run._process.wait(timeout=3)

                    def signal_group(pid, sig):
                        # Keep the EOF probe from confirming ESRCH before the
                        # first cleanup SIGKILL whose EPERM this fixture injects.
                        if sig == signal.SIGTERM and expected != 'timeout':
                            return None
                        if sig == signal.SIGKILL and run._process.poll() is not None:
                            attempts.append(pid)
                            if len(attempts) == 1 or cleanup == 'denied':
                                raise PermissionError(1, 'Synthetic macOS exited-group EPERM')
                            if cleanup == 'gone':
                                raise ProcessLookupError(3, 'Group gone')
                            return None
                        return killpg(pid, sig)

                    with patch('os.killpg', side_effect=signal_group), patch.object(run, '_pump', side_effect=after_exit):
                        try:
                            run._work()
                        except PermissionError as exc:
                            self.fail('Exited client escaped terminal cleanup: ' + str(exc))
                    self.assertEqual(len(attempts), agents['EXITED_GROUP_ATTEMPTS'] if cleanup == 'denied' else 2)
                    self.assertEqual(run.status, expected)
                    self.assertEqual(run.events()[-1]['data']['status'], expected)
                    self.assertEqual(run.result is not None, expected == 'done')
                    errors = [event['text'] for event in run.events() if event['kind'] == 'error']
                    self.assertEqual(any('clean up' in text for text in errors), cleanup == 'denied')
                    self.assertTrue(all(pipe.closed for pipe in
                                        (run._process.stdin, run._process.stdout, run._process.stderr)))

    def test_cleanup_eperm_for_live_process_records_failure(self):
        run = agents['AgentRun']('codex', 'read', str(self.project), str(self.project),
                                  '{"hang": true}', None, 5, None, None, None, str(self.fake))
        killpg = os.killpg

        def denied_live_group(pid, sig):
            self.assertIsNone(run._process.poll())
            raise PermissionError(1, 'Live group denied')

        try:
            with patch('os.killpg', side_effect=denied_live_group) as denied, \
                    patch.object(run, '_pump', return_value='timeout'):
                try:
                    run._work()
                except PermissionError as exc:
                    self.fail('Live-group error escaped instead of recording failed/end: ' + str(exc))
            self.assertEqual(denied.call_count, 1)
            self.assertEqual(run.status, 'failed')
            self.assertEqual(run.events()[-1]['data']['status'], 'failed')
            self.assertTrue(any(event['kind'] == 'error' and 'Live group denied' in event['text']
                                for event in run.events()))
            self.assertIsNone(run.result)
        finally:
            if run._process is not None:
                try:
                    killpg(run._process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                run._process.wait(timeout=3)
                for pipe in (run._process.stdin, run._process.stdout, run._process.stderr):
                    pipe.close()

    def test_eof_probe_eperm_preserves_result_and_terminal_event(self):
        for expected, options in (('done', {}), ('failed', {'error': True})):
            for recovery in ('gone', 'delivered', 'denied', 'denied-all'):
                with self.subTest(expected=expected, recovery=recovery):
                    options = dict(options, retain_pipes=str(self.root / 'retainer.pid'))
                    run = agents['AgentRun']('codex', 'read', str(self.project), str(self.project),
                                             json.dumps(options), None, 5, None, None, None, str(self.fake))
                    killpg, attempts = os.killpg, []

                    def signal_group(pid, sig):
                        if (sig == signal.SIGTERM or recovery == 'denied-all') and run._process.poll() is not None:
                            attempts.append(sig)
                            if len(attempts) == 1 or recovery.startswith('denied'):
                                raise PermissionError(1, 'Synthetic EOF probe EPERM')
                            killpg(pid, sig)
                            if recovery == 'gone':
                                raise ProcessLookupError(3, 'Group gone')
                            return
                        return killpg(pid, sig)

                    try:
                        with patch('os.killpg', side_effect=signal_group):
                            run._work()
                        self.assertEqual(attempts.count(signal.SIGTERM), agents['EXITED_GROUP_ATTEMPTS'] if recovery.startswith('denied') else 2)
                        if recovery == 'denied-all':
                            self.assertEqual(attempts.count(signal.SIGKILL), 2 * agents['EXITED_GROUP_ATTEMPTS'])
                        self.assertEqual(run.status, expected, run.events())
                        self.assertEqual(run.events()[-1]['data']['status'], expected)
                        self.assertEqual(run.result is not None, expected == 'done')
                        self.assertEqual(any('EOF probe EPERM' in e['text'] for e in run.events()),
                                         recovery.startswith('denied'))
                        self.assertTrue(all(p.closed for p in (run._process.stdin, run._process.stdout,
                                                               run._process.stderr)))
                    finally:
                        if run._process is not None and not run._group_killed:
                            try:
                                killpg(run._process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            run._process.wait(timeout=3)

    def test_pump_permission_error_with_live_leader_remains_failure(self):
        run = agents['AgentRun']('codex', 'read', str(self.project), str(self.project),
                                 '{"hang": true}', None, .05, None, None, None, str(self.fake))
        killpg = os.killpg

        def signal_group(pid, sig):
            if sig == signal.SIGTERM:
                self.assertIsNone(run._process.poll())
                raise PermissionError(1, 'Live probe denied')
            return killpg(pid, sig)

        with patch('os.killpg', side_effect=signal_group):
            run._work()
        self.assertEqual(run.status, 'failed')
        self.assertIsNone(run.result)
        self.assertEqual(run.events()[-1]['data']['status'], 'failed')
        self.assertTrue(any('Live probe denied' in e['text'] for e in run.events()))

    def test_log_and_text_limits_do_not_block_pipes(self):
        logs = self.root / 'large-logs'
        run = self.complete(options=dict(flood=2700, stderr=300000, result='z' * 10000), log_dir=logs)
        self.assertEqual((logs / 'raw.jsonl').stat().st_size, agents['RAW_LIMIT'])
        self.assertEqual((logs / 'stderr.txt').stat().st_size, agents['STDERR_LIMIT'])
        self.assertTrue(all(len(event['text']) <= 4000 for event in run.events()))
        self.assertEqual(run.result, dict(text='z' * 10000))
        self.assertEqual(json.loads((logs / 'run.json').read_text())['truncated_logs'], ['raw.jsonl', 'stderr.txt'])

    def test_large_stdin_is_sent_without_deadlock(self):
        run = self.complete(options=dict(unused='x' * 300000))
        self.assertEqual(json.loads(run.result['text']), dict(answer='ok', count=2))

    def test_reject_invalid_inputs_before_any_process(self):
        for values in (dict(mode='unsafe'), dict(timeout=0), dict(timeout=float('nan')),
                       dict(model='--dangerously-skip-permissions'), dict(model='bypassPermissions'),
                       dict(budget_usd=1), dict(output_schema={'type': 'object', 'pattern': '.*'})):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.start(**values)
        for timeout in (False, 0, float('inf')):
            with self.assertRaises(ValueError):
                agents['detect'](timeout)

    def test_excessive_events_fail_instead_of_consuming_unbounded_memory(self):
        run = self.start(options=dict(flood=10020))
        self.assertEqual(run.wait(10), 'failed')
        self.assertLessEqual(len(run.events()), agents['MAX_EVENTS'] + 2)
        self.assertEqual(run.events()[-1]['kind'], 'end')
        self.assertIsNone(run.result)

    def test_event_limit_preserves_cleanup_and_metadata_errors_within_the_bound(self):
        limit = 8
        for cleanup_error, metadata_error in ((True, False), (False, True), (True, True)):
            with self.subTest(cleanup=cleanup_error, metadata=metadata_error):
                logs = self.root / ('bounded-%s-%s' % (cleanup_error, metadata_error))
                run = agents['AgentRun']('codex', 'read', str(self.project), str(self.project),
                                         json.dumps(dict(flood=20)), None, 5, None, None, logs, str(self.fake))
                original_signal, original_metadata = run._signal, run._metadata
                injected = []

                def signal_group(sig):
                    try:
                        original_signal(sig)
                    except PermissionError:
                        # The real group can answer EPERM on its own while macOS reaps it; the injected
                        # cleanup error below must still be reported, whatever the timing.
                        if not (cleanup_error and sig == signal.SIGKILL):
                            raise
                    if cleanup_error and sig == signal.SIGKILL:
                        run._process.wait(timeout=3)
                        injected.append('cleanup denied')
                        raise PermissionError('cleanup denied' + ('x' * agents['TEXT_LIMIT'] if metadata_error else ''))

                def metadata(*args):
                    if metadata_error and run.status in agents['TERMINAL']:
                        injected.append('metadata denied')
                        raise OSError('metadata denied')
                    return original_metadata(*args)

                with patch.dict(agents['AgentRun']._emit.__globals__, MAX_EVENTS=limit), \
                        patch.object(run, '_signal', side_effect=signal_group), \
                        patch.object(run, '_metadata', side_effect=metadata):
                    run._work()
                self.assertEqual(injected, (['cleanup denied'] if cleanup_error else []) +
                                 (['metadata denied'] if metadata_error else []))
                events = run.events()
                self.assertLessEqual(len(events), limit + 2)
                self.assertEqual(run.status, 'failed')
                self.assertIsNone(run.result)
                self.assertEqual(events[-1]['kind'], 'end')
                self.assertEqual(events[-1]['data']['status'], 'failed')
                errors = '\n'.join(e['text'] for e in events if e['kind'] == 'error')
                self.assertIn('event limit', errors)
                for message in injected:
                    self.assertIn(message, errors)
                self.assertTrue(all(len(e['text']) <= agents['TEXT_LIMIT'] for e in events))
                self.assertEqual([e['seq'] for e in events], list(range(1, len(events) + 1)))
                self.assertEqual([json.loads(line) for line in (logs / 'events.jsonl').read_text().splitlines()], events)

    def test_native_error_events_cannot_bypass_the_event_limit(self):
        limit = 8
        self.fake.write_text('#!' + sys.executable + '\nimport json\n'
                             'for _ in range(24): print(json.dumps(dict(type="error", message="client error")))\n')
        with patch.dict(agents['AgentRun']._emit.__globals__, MAX_EVENTS=limit):
            run = self.start()
            self.assertEqual(run.wait(5), 'failed')
        events = run.events()
        self.assertLessEqual(len(events), limit + 2)
        self.assertTrue(any('event limit' in e['text'] for e in events))
        self.assertEqual(events[-1]['kind'], 'end')
        self.assertIsNone(run.result)

    def test_pump_cleanup_at_event_limit_preserves_terminal_outcome(self):
        for expected, options, timeout, exact in (
                ('done', {'retain_pipes': str(self.root / 'retainer.pid')}, 5, 9),
                ('cancelled', {'hang': True}, 5, 1),
                ('timeout', {'hang': True}, .05, 1)):
            for limit in (exact, exact + 1):
                with self.subTest(expected=expected, limit=limit):
                    run = agents['AgentRun']('codex', 'read', str(self.project), str(self.project),
                                             json.dumps(options), None, timeout, None, None, None, str(self.fake))
                    pump, killpg = run._pump, os.killpg
                    attempts = []

                    def begin(*args):
                        if expected == 'cancelled':
                            run.cancel()
                        return pump(*args)

                    def signal_group(pid, sig):
                        if sig == signal.SIGTERM:
                            if expected != 'done':
                                try:
                                    killpg(pid, signal.SIGKILL)
                                except ProcessLookupError:
                                    pass
                                run._process.wait(timeout=3)
                            if run._process.poll() is not None:
                                attempts.append(pid)
                                raise PermissionError(1, 'Synthetic pump cleanup EPERM')
                        return killpg(pid, sig)

                    with patch.dict(agents['AgentRun']._emit.__globals__, MAX_EVENTS=limit), \
                            patch('os.killpg', side_effect=signal_group), patch.object(run, '_pump', side_effect=begin):
                        run._work()
                    self.assertEqual(len(attempts), agents['EXITED_GROUP_ATTEMPTS'])
                    self.assertEqual(run.status, expected)
                    self.assertEqual(run.result is not None, expected == 'done')
                    events = run.events()
                    self.assertLessEqual(len(events), limit + 2)
                    self.assertEqual(events[-1]['data']['status'], expected)
                    errors = '\n'.join(event['text'] for event in events if event['kind'] == 'error')
                    self.assertIn('Synthetic pump cleanup EPERM', errors)
                    self.assertNotIn('event limit', errors)
                    self.assertTrue(all(pipe.closed for pipe in
                                        (run._process.stdin, run._process.stdout, run._process.stderr)))

    def test_existing_logs_are_not_overwritten_and_no_client_is_started(self):
        logs = self.root / 'old-logs'
        logs.mkdir()
        (logs / 'run.json').write_text('previous run')
        observation = self.root / 'should-not-start'
        run = self.start(options=dict(observe=str(observation)), log_dir=logs)
        self.assertEqual(run.wait(5), 'failed')
        self.assertFalse(observation.exists())
        self.assertEqual((logs / 'run.json').read_text(), 'previous run')

    def test_nested_plan_schema_validation(self):
        schema = dict(type='object', properties=dict(touch=dict(type='array',
                      items=dict(type='object', properties=dict(path=dict(type='string'),
                                                               line=dict(type='integer')),
                                 required=['path', 'line'], additionalProperties=False))),
                      required=['touch'], additionalProperties=False)
        result = dict(touch=[dict(path='src.py', line=1)])
        self.assertEqual(self.complete(options=dict(result=result), output_schema=schema).result, result)
        for result in (dict(touch={}), dict(touch=[dict(line=1)]), dict(touch=[dict(path='x', line='1')])):
            run = self.start(options=dict(result=result), output_schema=schema)
            self.assertEqual(run.wait(5), 'failed')

    def test_unused_schema_keywords_rejected_before_start(self):
        unused = dict(const={}, title='Title', description='Description', default={},
                      minItems=0, maxItems=2, minLength=0, maxLength=20, minimum=0, maximum=9,
                      anyOf=[{}], oneOf=[{}], allOf=[{}], unknown=True)
        for keyword, value in unused.items():
            for nested in ('root', 'properties', 'items'):
                schema = dict(type='object', **{keyword: value})
                if nested == 'properties':
                    schema = dict(type='object', properties=dict(child=schema))
                elif nested == 'items':
                    schema = dict(type='object', properties=dict(children=dict(type='array', items=schema)))
                spawn = Mock()
                with self.subTest(keyword=keyword, nested=nested), patch.dict(agents['start'].__globals__, AgentRun=spawn):
                    with self.assertRaisesRegex(ValueError, 'unsupported schema keywords'):
                        self.start(output_schema=schema)
                    spawn.assert_not_called()

    def test_logs_do_not_dump_inherited_environment(self):
        logs = self.root / 'private-logs'
        with patch.dict(os.environ, CG_TEST_AUTH_SENTINEL='not-a-real-secret-5791'):
            self.complete(log_dir=logs)
        self.assertTrue(all('not-a-real-secret-5791' not in path.read_text() for path in logs.iterdir()))


if __name__ == '__main__':
    unittest.main()
