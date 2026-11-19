"""Real-pilot boundaries: local fixtures/fakes only, never a provider or network."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('real_pilot_test_subject', REPO / 'benchmarks/real_pilot.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
text_mcp = pilot.shared.load(REPO / 'benchmarks/real_pilot_mcp.py', 'real_text_test_subject')
rescorer = pilot.shared.load(REPO / 'benchmarks/real_rescore.py', 'real_rescore_test_subject')


class RealPilot(unittest.TestCase):
    def test_frozen_cases_and_selection_are_consistent(self):
        tasks = pilot.cases()
        self.assertEqual([t['pr'] for t in tasks], [30885, 30882, 30872, 30866])
        ledger = json.loads((pilot.DATA / 'selection-ledger.json').read_text())
        self.assertEqual(len(ledger['pool']), 100)
        self.assertEqual([i['number'] for i in ledger['pool'] if i['status'] == 'selected'], [t['pr'] for t in tasks])
        for task in tasks:
            self.assertEqual(len(task['smoke_ids']), 5)
            self.assertEqual(len(task['hidden_ids']), 1)
            self.assertGreater(len([p for p in task['source_paths'] if '/tests/' not in p]), 300)
            self.assertEqual(task['source_paths'], sorted(set(task['source_paths'])))

    def test_both_mcp_arms_offer_text_search_with_identical_results_and_valid_wire_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'later.py').write_text('#' * 40000 + '\nraise RuntimeError("uniqueneedle must never be imported")\n')
            (root / 'quotes.py').write_text(('needle ' + '\\"漢' * 100 + '\n') * 80)
            pilot.shared.git(root, 'init', '-b', 'main')
            pilot.shared.git(root, 'add', '.')
            outputs = []
            for arm in ('source_only', 'graph_assisted'):
                requests = [dict(jsonrpc='2.0', id=1, method='tools/list'),
                            dict(jsonrpc='2.0', id=2, method='tools/call', params=dict(name='text_search', arguments=dict(pattern='uniqueneedle'))),
                            dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name='text_search', arguments=dict(pattern='needle', path_glob='quotes.py')))]
                run = subprocess.run([sys.executable, '-I', str(REPO / 'benchmarks/real_pilot_mcp.py'), '--skill-root', str(REPO),
                                      '--root', str(root), '--arm', arm], input='\n'.join(json.dumps(r) for r in requests) + '\n',
                                     capture_output=True, text=True, timeout=20)
                self.assertEqual(run.returncode, 0, run.stderr)
                lines = run.stdout.splitlines()
                replies = [json.loads(line) for line in lines]
                tools = {tool['name'] for tool in replies[0]['result']['tools']}
                self.assertTrue({'text_search', 'cg_read'} <= tools)
                self.assertEqual('cg_search' in tools, arm == 'graph_assisted')
                result = json.loads(replies[1]['result']['content'][0]['text'])
                self.assertEqual(result['items'][0]['path'], 'later.py')
                self.assertEqual(result['items'][0]['line'], 2)
                outputs.append(result)
                bounded = json.loads(replies[2]['result']['content'][0]['text'])
                self.assertTrue(bounded['truncated'])
                self.assertLess(len(lines[2]), 40000)
            self.assertEqual(outputs[0], outputs[1])
            self.assertFalse((root / '.code-graph').exists())

    def test_text_search_filters_and_omits_untracked_binary_large_and_symlink_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'repo'
            root.mkdir()
            (root / 'a.py').write_text('needle[\nneedle[\n')
            (root / 'binary').write_bytes(b'needle[\0')
            (root / 'large').write_bytes(b'x' * (text_mcp.MAX_BYTES + 1))
            secret = Path(folder) / 'secret'
            secret.write_text('needle[ SECRET')
            (root / 'link').symlink_to(secret)
            pilot.shared.git(root, 'init', '-b', 'main')
            pilot.shared.git(root, 'add', '.')
            (root / 'untracked.py').write_text('needle[ SECRET')
            result = text_mcp.text_search(root, 'needle[')
            self.assertEqual([i['path'] for i in result['items']], ['a.py', 'a.py'])
            self.assertEqual(result['omitted_files'], 3)
            limited = text_mcp.text_search(root, 'needle[', '*.py', 1)
            self.assertEqual(len(limited['items']), 1)
            self.assertTrue(limited['truncated'])
            self.assertNotIn('SECRET', json.dumps(result))
            (root / 'nested').mkdir()
            (root / 'nested/a.py').write_text('needle[\n')
            pilot.shared.git(root, 'add', '.')
            (root / 'nested/a.py').unlink()
            (root / 'nested').rmdir()
            (root / 'nested').symlink_to(Path(folder), target_is_directory=True)
            self.assertEqual(text_mcp.text_search(root, 'needle[', 'nested/*')['items'], [])

    def test_client_redirect_uses_the_same_text_capable_wrapper_in_both_arms(self):
        class Run:
            def _argv(self, folder):
                (folder / 'mcp.json').write_text(json.dumps(dict(mcpServers={'code-graph': {}})))
                return ['codex', 'mcp_servers.code-graph.command="old"', 'mcp_servers.code-graph.args=[]']
        with tempfile.TemporaryDirectory() as folder:
            for arm in ('source_only', 'graph_assisted'):
                runner = pilot.RealRunner(SimpleNamespace(AgentRun=Run), SimpleNamespace(), REPO, {}, arm, [0], test_argv=[])
                try:
                    for client in ('codex', 'claude'):
                        argv = Run._argv(SimpleNamespace(client=client, _root='/fixture'), Path(folder))
                        args = (json.loads(argv[2].split('=', 1)[1]) if client == 'codex' else
                                json.loads((Path(folder) / 'mcp.json').read_text())['mcpServers']['code-graph']['args'])
                        self.assertIn(str(REPO / 'benchmarks/real_pilot_mcp.py'), args)
                        self.assertEqual(args[-2:], ['--arm', arm])
                finally:
                    runner.close()

    def test_test_timeout_and_missing_receipt_are_sanitized(self):
        with patch.object(pilot.subprocess, 'run', side_effect=subprocess.TimeoutExpired('/private/secret', 120)):
            result = pilot.test_run('/unused', '/private/python', ['test_a'])
        self.assertEqual(result['error_code'], 'test_timeout')
        self.assertFalse(pilot.green(result))
        with patch.object(pilot.subprocess, 'run', return_value=SimpleNamespace(stdout='/private/secret', returncode=2)):
            result = pilot.test_run('/unused', '/private/python', ['test_a'])
        self.assertNotIn('/private', json.dumps(result))
        self.assertEqual(result['error_code'], 'missing_test_receipt')

    def test_green_requires_executed_tests_and_no_skip_or_errors(self):
        good = dict(exit_code=0, executed=1, passed=1, failed=0, errors=0, skipped=0)
        self.assertTrue(pilot.green(good))
        for key, value in [('executed', 0), ('passed', 0), ('skipped', 1), ('errors', 1), ('failed', 1), ('exit_code', 1)]:
            self.assertFalse(pilot.green(dict(good, **{key: value})))

    def test_archive_rejects_escape_links_and_disables_fetch(self):
        for name, link in [('../escape', False), ('/absolute', False), ('safe', True)]:
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode='w') as bundle:
                member = tarfile.TarInfo(name)
                if link:
                    member.type, member.linkname = tarfile.SYMTYPE, '/private'
                bundle.addfile(member)
            with tempfile.TemporaryDirectory() as folder:
                with patch.object(pilot.subprocess, 'run', return_value=SimpleNamespace(stdout=data.getvalue())) as call:
                    with self.assertRaises(ValueError):
                        pilot.archive(Path('/cache'), 'sha', Path(folder) / 'copy')
                self.assertIn('protocol.allow=never', call.call_args.args[0])
                self.assertEqual(call.call_args.kwargs['env']['GIT_NO_LAZY_FETCH'], '1')

    def test_retention_rejects_weakened_tests_and_changed_api(self):
        with tempfile.TemporaryDirectory() as folder:
            baseline, candidate = Path(folder) / 'before', Path(folder) / 'after'
            for root in (baseline, candidate):
                (root / 'pkg/tests').mkdir(parents=True)
                (root / 'pkg/leaf.py').write_text('def leaf(value): return value\n')
                (root / 'pkg/tests/test_leaf.py').write_text('def test_leaf():\n    assert 1 == 1\n')
            changed = ['pkg/leaf.py', 'pkg/tests/test_leaf.py']
            self.assertTrue(pilot.retained(candidate, baseline, changed)['tests_preserved'])
            (candidate / changed[1]).write_text('@skip\ndef test_leaf():\n    assert 1 == 1\n')
            self.assertFalse(pilot.retained(candidate, baseline, changed)['tests_preserved'])
            (candidate / changed[1]).write_text('def test_leaf(): pass\n')
            self.assertFalse(pilot.retained(candidate, baseline, changed)['tests_preserved'])
            (candidate / changed[0]).write_text('def leaf(): return 1\n')
            self.assertFalse(pilot.retained(candidate, baseline, changed)['top_level_api']['passed'])

    def test_retention_allows_added_assertions_at_every_statement_depth(self):
        source = ('def test_original():\n'
                  '    with manager():\n'
                  '        for value in values:\n'
                  '            if value:\n'
                  '                try:\n'
                  '                    assert check(value)\n'
                  '                except ValueError:\n'
                  '                    assert recover(value)\n'
                  '                else:\n'
                  '                    assert done(value)\n'
                  '                finally:\n'
                  '                    assert cleanup(value)\n')
        with tempfile.TemporaryDirectory() as folder:
            baseline, candidate = Path(folder) / 'before', Path(folder) / 'after'
            name = 'pkg/tests/test_original.py'
            for root in (baseline, candidate):
                (root / name).parent.mkdir(parents=True)
                (root / name).write_text(source)
            for statement in ('check(value)', 'recover(value)', 'done(value)', 'cleanup(value)'):
                with self.subTest(statement=statement):
                    added = source.replace('assert ' + statement, 'assert ' + statement + '\n                    assert extra(value)')
                    (candidate / name).write_text(added)
                    self.assertTrue(pilot.retained(candidate, baseline, [name])['tests_preserved'])

    def test_retention_rejects_removed_nested_assertions_changed_headers_and_duplicate_loss(self):
        source = ('def test_original():\n'
                  '    for value in values:\n'
                  '        if value:\n'
                  '            assert check(value)\n'
                  '            assert check(value)\n')
        variants = [source.replace('assert check(value)', 'assert other(value)', 1),
                    source.replace('            assert check(value)\n', '', 1),
                    source.replace('in values:', 'in other_values:'),
                    source.replace('if value:', 'if other:'),
                    source.replace('test_original():', 'test_original(extra=None):')]
        with tempfile.TemporaryDirectory() as folder:
            baseline, candidate = Path(folder) / 'before', Path(folder) / 'after'
            name = 'pkg/tests/test_original.py'
            for root in (baseline, candidate):
                (root / name).parent.mkdir(parents=True)
                (root / name).write_text(source)
            for changed in variants:
                with self.subTest(changed=changed):
                    (candidate / name).write_text(changed)
                    self.assertFalse(pilot.retained(candidate, baseline, [name])['tests_preserved'])

    def test_offline_receipt_rejects_private_material(self):
        for value in (dict(session_id='private'), dict(path='/Users/owner/private'),
                      dict(path='C:\\Users\\owner\\private'), dict(text='session 12345678-1234-1234-1234-123456789abc')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rescorer.public_json(value)
        self.assertIn('candidate_tree', rescorer.public_json(dict(candidate_tree='a' * 40, path='pkg/test.py')))

    def test_retention_preserves_context_manager_and_exception_headers(self):
        source = ('def test_saved():\n    with manager():\n        try:\n            assert value\n'
                  '        except ValueError as error:\n            assert valid(error)\n'
                  '        else:\n            assert done()\n        finally:\n            assert cleanup()\n')
        with tempfile.TemporaryDirectory() as folder:
            baseline, candidate = Path(folder) / 'before', Path(folder) / 'after'
            name = 'pkg/tests/test_saved.py'
            for root in (baseline, candidate):
                (root / name).parent.mkdir(parents=True)
                (root / name).write_text(source)
            for original, replacement in (('manager()', 'other_manager()'), ('ValueError', 'TypeError'), ('as error:', 'as other:')):
                with self.subTest(header=original):
                    (candidate / name).write_text(source.replace(original, replacement))
                    self.assertFalse(pilot.retained(candidate, baseline, [name])['tests_preserved'])
            for clause, assertion in (('else', 'done()'), ('finally', 'cleanup()')):
                with self.subTest(removed_clause=clause):
                    moved = source.replace('        ' + clause + ':\n            assert ' + assertion + '\n', '')
                    moved = moved.replace('            assert value\n', '            assert value\n            assert ' + assertion + '\n')
                    (candidate / name).write_text(moved)
                    self.assertFalse(pilot.retained(candidate, baseline, [name])['tests_preserved'])

    def test_offline_integration_merges_a_copy_and_preserves_the_original_host(self):
        with tempfile.TemporaryDirectory() as folder:
            host = Path(folder) / 'original'
            host.mkdir()
            pilot.shared.git(host, 'init', '-b', 'main')
            pilot.shared.git(host, 'config', 'user.name', 'fixture')
            pilot.shared.git(host, 'config', 'user.email', 'fixture@example.invalid')
            (host / 'leaf.py').write_text('def leaf(): return 1\n')
            pilot.shared.git(host, 'add', '.')
            pilot.shared.git(host, 'commit', '-m', 'base')
            base = rescorer.git(host, 'rev-parse', 'HEAD')
            pilot.shared.git(host, 'checkout', '-b', 'candidate')
            (host / 'leaf.py').write_text('def leaf(): return 2\n')
            pilot.shared.git(host, 'commit', '-am', 'candidate')
            edit = rescorer.git(host, 'rev-parse', 'HEAD')
            tree = rescorer.git(host, 'rev-parse', 'HEAD^{tree}')
            pilot.shared.git(host, 'checkout', 'main')
            record = dict(state='reviewed', review=dict(verdict='accept'), verify=dict(blocking=[]),
                          base=dict(commit=base, branch='main'), edit=dict(commit=edit))
            destination = Path(folder) / 'integration'
            result = rescorer.integrate_copy(host, record, destination, tree)
            self.assertTrue(result['performed_offline'])
            self.assertEqual((destination / 'leaf.py').read_text(), 'def leaf(): return 2\n')
            self.assertEqual(rescorer.git(host, 'rev-parse', 'HEAD'), base)
            self.assertEqual((host / 'leaf.py').read_text(), 'def leaf(): return 1\n')
            self.assertEqual(result['integrated_tree'], tree)

    def test_offline_rescore_preserves_recorded_measurements_and_both_classifications(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tasks = [dict(id='fixture' + str(i), parent_tree='parent-tree', source_paths=['pkg/tests/test_saved.py']) for i in range(4)]
            old_retention = dict(tests_preserved=False, weakened_test_ids=['pkg/tests/test_saved.py::test_saved'],
                                 top_level_api=dict(passed=True, changed_signatures=[]))
            rows = []
            for task in tasks:
                for arm in ('source_only', 'graph_assisted'):
                    host = root / 'work' / (task['id'] + '-' + arm)
                    state = host / '.code-graph/changes/saved/change.json'
                    state.parent.mkdir(parents=True)
                    state.write_text(json.dumps(dict(state='reviewed', worktree=str(host / '_candidate'),
                        base=dict(commit='base', branch='main'), edit=dict(commit='edit'),
                        review=dict(verdict='accept'), verify=dict(ok=True, blocking=[]))))
                    good = dict(exit_code=0, executed=1, passed=1, failed=0, errors=0, skipped=0)
                    hidden = good if arm == 'graph_assisted' else dict(good, exit_code=1, passed=0, failed=1)
                    rows.append(dict(task=task['id'], arm=arm, final_state='reviewed', verification_ok=True, review_verdict='accept',
                                     outcome='verification_review_or_hidden_failed', changed_files=['pkg/tests/test_saved.py'],
                                     candidate_scope_ok=True, retention=old_retention, candidate_public=good, candidate_hidden=hidden,
                                     seconds=123, calls=[dict(client='codex', native_usage=[dict(input_tokens=42)])]))
            receipt = root / 'receipt.json'
            receipt.write_text(json.dumps(dict(format='fixture', cases=rows, live_starts=24, test_environment={},
                cases_sha256=pilot.digest(pilot.DATA / 'cases.json'), selection_sha256=pilot.digest(pilot.DATA / 'selection-rule.json'))))
            before = receipt.read_bytes()

            def git(host, *args):
                if args == ('rev-parse', 'HEAD'):
                    return 'edit' if host.name == '_candidate' else 'base'
                if args[0] == 'status':
                    return ''
                if args == ('rev-parse', 'base^{tree}'):
                    return 'parent-tree'
                if args[0] == 'diff':
                    return 'pkg/tests/test_saved.py'
                return 'edited-tree'

            def archive(host, commit, destination):
                path = destination / 'pkg/tests/test_saved.py'
                path.parent.mkdir(parents=True)
                path.write_text('def test_saved():\n    for value in values:\n        assert value\n' +
                                ('        assert extra(value)\n' if commit == 'edit' else ''))

            def checks(candidate, baseline, task, python, hidden=False):
                row = next(r for r in rows if r['task'] == task['id'] and r['arm'] in str(candidate))
                return row['candidate_hidden' if hidden else 'candidate_public']

            with patch.object(rescorer.pilot, 'cases', return_value=tasks), patch.object(rescorer.pilot, 'test_environment', return_value={}), \
                    patch.object(rescorer, 'git', side_effect=git), patch.object(rescorer.pilot, 'archive', side_effect=archive), \
                    patch.object(rescorer.pilot, 'frozen_tests', side_effect=checks), \
                    patch.object(rescorer, 'integrate_copy', return_value=dict(performed_offline=True)) as merge, \
                    patch.object(rescorer.pilot.shared, 'run_case', side_effect=AssertionError('no agents')):
                result = rescorer.rescore(receipt, root / 'replay', Path(sys.executable))
            self.assertEqual(merge.call_count, 4)
            self.assertEqual(result['rescore']['additional_live_starts'], 0)
            self.assertEqual(receipt.read_bytes(), before)
            for original, rescored in zip(rows, result['cases']):
                self.assertEqual(rescored['calls'], original['calls'])
                self.assertEqual(rescored['seconds'], 123)
                self.assertEqual(rescored['as_recorded']['retention'], old_retention)
                self.assertTrue(rescored['rescored']['retention']['tests_preserved'])
                self.assertEqual(rescored['rescored']['outcome'] == 'success', original['arm'] == 'graph_assisted')
            self.assertNotIn(folder, rescorer.public_json(result))

    def test_call_receipt_keeps_phase_native_usage_and_confined_successful_reads(self):
        events = [dict(kind='tool', data=dict(name='cg_read', input=dict(path='./pkg/a.py'), status='ok')),
                  dict(kind='tool', data=dict(name='Read', input=dict(file_path='/fixture/pkg/b.py'), status='ok')),
                  dict(kind='tool', data=dict(name='cg_read', input=dict(path='pkg/fail.py'), status='error')),
                  dict(kind='tool', data=dict(name='Read', input=dict(file_path='/private/secret'), status='ok')),
                  dict(kind='end', data=dict(duration_s=1.5))]
        run = SimpleNamespace(client='codex', mode='write', status='done', usage=None, cwd='/fixture',
                              _log_dir=None, events=lambda: events)
        result = pilot.call_metadata(run, 'edit', {'pkg/a.py', 'pkg/b.py', 'pkg/fail.py'})
        self.assertEqual(result['explicit_source_files_read'], ['pkg/a.py', 'pkg/b.py'])
        self.assertEqual(result['phase'], 'edit')
        self.assertEqual(result['seconds'], 1.5)
        self.assertIsNone(result['native_usage'])
        self.assertNotIn('/private', json.dumps(result))

    def test_prompt_includes_identical_inventory_without_historical_solution_or_host_graph(self):
        class Run:
            def _argv(self, folder):
                return []
        agents = SimpleNamespace(AgentRun=Run, start=lambda client, **kwargs: SimpleNamespace(status='done', captured=kwargs))
        task = dict(source_paths=['pkg/a.py'], production=['HIDDEN_IMPL.py'], hidden_ids=['HIDDEN_TEST'])
        flow = SimpleNamespace(_RETRIEVAL_GUIDANCE='RETRIEVAL')
        original = Run._argv
        for arm in ('source_only', 'graph_assisted'):
            runner = pilot.RealRunner(agents, flow, REPO, task, arm, [0], test_argv=['python', 'public-test'])
            try:
                runner.phase = 'review'
                run = runner.start('codex', prompt='RETRIEVAL\n\nPlan\n' + json.dumps(dict(intent='bug', checked='HOST_GRAPH', graph='HOST_GRAPH')), timeout=900)
                prompt = run.captured['prompt']
                self.assertIn('pkg/a.py', prompt)
                self.assertNotIn('HIDDEN_', prompt)
                self.assertNotIn('HOST_GRAPH', prompt)
                self.assertEqual(run.captured['timeout'], 600)
                self.assertEqual(runner.phases, ['review'])
            finally:
                runner.close()
            self.assertIs(Run._argv, original)

    def test_native_receipt_retains_cache_units_and_structured_file_mentions_without_raw_content(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = dict(format='codegraph-search/1', items=[dict(path='pkg/a.py', graph=dict(chain=[dict(path='pkg/b.py')]))],
                           private='/private/SECRET', source='SECRET source text')
            raw = [dict(type='thread.started', thread_id='SECRET-session'),
                   dict(type='item.completed', item=dict(type='mcp_tool_call', id='SECRET-tool', tool='cg_search', status='completed',
                                                        result=dict(content=[dict(type='text', text=json.dumps(payload))]))),
                   dict(type='turn.completed', usage=dict(input_tokens=10, output_tokens=5, cached_input_tokens=8, cache_write_input_tokens=2))]
            (Path(folder) / 'raw.jsonl').write_text('\n'.join(json.dumps(i) for i in raw))
            run = SimpleNamespace(client='codex', mode='read', status='done', usage=None, cwd='/fixture',
                                  _log_dir=Path(folder), events=lambda: [])
            result = pilot.call_metadata(run, 'plan', {'pkg/a.py', 'pkg/b.py'})
            self.assertEqual(result['tool_returned_file_mentions'], ['pkg/a.py', 'pkg/b.py'])
            self.assertEqual(result['native_usage'][0]['cache_write_input_tokens'], 2)
            self.assertEqual(result['native_usage'][0]['input_tokens'], 10)
            self.assertTrue(result['native_tools'][0]['graph_evidence_observed'])
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertNotIn('/private', json.dumps(result))

    def test_dry_run_and_startup_failure_keep_receipts_without_models(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'receipt.json'
            argv = ['real_pilot', '--repo-cache', '/private/cache', '--test-python', '/private/python', '--output', str(output)]
            with patch.object(sys, 'argv', argv), patch.object(pilot, 'qualify', return_value=dict(qualified=True)), \
                    patch.object(pilot, 'test_environment', return_value=dict(python='fixture')), \
                    patch.object(pilot, 'run_case', side_effect=AssertionError('must not start')):
                self.assertEqual(pilot.main(), 0)
            self.assertEqual(json.loads(output.read_text())['live_starts'], 0)
            with patch.object(sys, 'argv', argv + ['--live', '--work-dir', str(Path(folder) / 'work')]), \
                    patch.object(pilot, 'test_environment', return_value=dict(python='fixture')), \
                    patch.object(pilot.shared, 'client_metadata', return_value={}), \
                    patch.object(pilot, 'run_case', side_effect=OSError('/private/secret')) as call:
                self.assertEqual(pilot.main(), 0)
            result = json.loads(output.read_text())
            self.assertEqual(call.call_count, 1)
            self.assertEqual(len(result['cases']), 8)
            self.assertFalse(result['cases'][0]['cleanup_drained'])
            self.assertTrue(all(row['outcome'] == 'not_started_after_stop_rule' for row in result['cases'][1:]))
            self.assertNotIn('/private', json.dumps(result))

    def test_tree_export_contains_no_future_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            cache, destination = Path(folder) / 'cache', Path(folder) / 'agent'
            cache.mkdir()
            pilot.shared.git(cache, 'init', '-b', 'main')
            pilot.shared.git(cache, 'config', 'user.name', 'fixture')
            pilot.shared.git(cache, 'config', 'user.email', 'fixture@example.invalid')
            (cache / 'a.py').write_text('def leaf(): return 1\n')
            pilot.shared.git(cache, 'add', '.')
            pilot.shared.git(cache, 'commit', '-m', 'parent')
            parent = pilot.shared.git(cache, 'rev-parse', 'HEAD').stdout.strip()
            tree = pilot.shared.git(cache, 'rev-parse', 'HEAD^{tree}').stdout.strip()
            (cache / 'a.py').write_text('def leaf(): return 2\n')
            pilot.shared.git(cache, 'commit', '-am', 'future fix')
            future = pilot.shared.git(cache, 'rev-parse', 'HEAD').stdout.strip()
            pilot.seed(cache, destination, dict(parent=parent, parent_tree=tree))
            self.assertIn('return 1', (destination / 'a.py').read_text())
            run = subprocess.run(['git', '-C', str(destination), 'cat-file', '-e', future], capture_output=True)
            self.assertNotEqual(run.returncode, 0)

    def test_live_driver_accounts_unknown_review_cost_and_preserves_all_eight_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'receipt.json'
            argv = ['real_pilot', '--live', '--repo-cache', '/cache', '--test-python', '/python',
                    '--work-dir', str(Path(folder) / 'work'), '--output', str(output)]

            def case(skill, cache, directory, task, arm, counter, python):
                counter[0] += 3
                return dict(task=task['id'], arm=arm, outcome='fake', cleanup_drained=True,
                            calls=[dict(client='claude', cli_reported_cost_usd=None)])

            with patch.object(sys, 'argv', argv), patch.object(pilot, 'test_environment', return_value={}), \
                    patch.object(pilot.shared, 'client_metadata', return_value={}), patch.object(pilot, 'run_case', side_effect=case):
                self.assertEqual(pilot.main(), 0)
            result = json.loads(output.read_text())
            self.assertEqual(result['live_starts'], 24)
            self.assertEqual(result['claude_reported_cost_usd'], 4.8)
            self.assertEqual([r['arm'] for r in result['cases']], ['source_only', 'graph_assisted', 'graph_assisted', 'source_only',
                                                                  'source_only', 'graph_assisted', 'graph_assisted', 'source_only'])

    def test_real_change_flow_with_memory_agents_and_host_tests(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / 'cache'
            (cache / 'pkg/tests').mkdir(parents=True)
            (cache / 'pkg/core.py').write_text('def leaf(): return 1\n')
            (cache / 'pkg/tests/test_core.py').write_text('import unittest\nfrom pkg.core import leaf\nclass Smoke(unittest.TestCase):\n    def test_unchanged(self): self.assertIn(leaf(), (1, 2))\n')
            pilot.shared.git(cache, 'init', '-b', 'main')
            pilot.shared.git(cache, 'config', 'user.name', 'fixture')
            pilot.shared.git(cache, 'config', 'user.email', 'fixture@example.invalid')
            pilot.shared.git(cache, 'add', '.')
            pilot.shared.git(cache, 'commit', '-m', 'parent')
            task = dict(id='memory', parent=pilot.shared.git(cache, 'rev-parse', 'HEAD').stdout.strip(),
                        parent_tree=pilot.shared.git(cache, 'rev-parse', 'HEAD^{tree}').stdout.strip(),
                        issue_title='leaf should return two', issue_body='Preserve API.',
                        source_paths=['pkg/core.py', 'pkg/tests/test_core.py'], test_files=['pkg/tests/test_core.py'])
            started = []

            class Run:
                def _argv(self, folder):
                    return []

                def events(self, since=0):
                    return [dict(seq=1, kind='end', data=dict(duration_s=.01))] if since < 1 else []

                def wait(self, timeout=None):
                    return self.status

                def cancel(self):
                    pass

            def start(client, **kwargs):
                run = Run()
                run.id, run.client, run.mode, run.status = 'fake-' + str(len(started)), client, kwargs['mode'], 'done'
                run.cwd, run.usage, run._log_dir = kwargs['cwd'], None, Path(kwargs['log_dir'])
                if not started:
                    run.result = dict(summary='Correct leaf', touch=[dict(path='pkg/core.py', symbol='leaf', line=1, why='bug')],
                                      affected=[], tests=[dict(path='pkg/tests/test_core.py', name='Smoke.test_unchanged', why='smoke')],
                                      out_of_scope=[], risks=[], unknowns=[], test_command=[])
                elif kwargs['mode'] == 'write':
                    (Path(kwargs['cwd']) / 'pkg/core.py').write_text('def leaf(): return 2\n')
                    run.result = 'Corrected leaf.'
                else:
                    run.result = dict(verdict='accept', summary='Checked', findings=[])
                started.append(run)
                return run

            agents = SimpleNamespace(AgentRun=Run, start=start)
            original_load = pilot.shared.load

            def load(path, name):
                return agents if path.name == 'agents.py' else original_load(path, name)

            argv = [sys.executable, '-I', '-c', 'import sys,unittest;sys.path.insert(0,".");unittest.main(module="pkg.tests.test_core")']
            good = dict(exit_code=0, executed=1, passed=1, failed=0, errors=0, skipped=0)
            with patch.object(pilot.shared, 'load', side_effect=load), patch.object(pilot, 'public_argv', return_value=argv), \
                    patch.object(pilot, 'frozen_tests', return_value=good):
                result = pilot.run_case(REPO, cache, Path(folder) / 'host', task, 'graph_assisted', [0], Path(sys.executable))
            self.assertEqual(result['outcome'], 'success', result)
            self.assertEqual([call['phase'] for call in result['calls']], ['plan', 'edit', 'review'])
            self.assertEqual(len(started), 3)
            self.assertTrue(result['cleanup_drained'])
            self.assertNotIn(folder, json.dumps(result))


if __name__ == '__main__':
    unittest.main()
