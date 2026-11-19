"""Pilot harness boundaries; no client, network, provider or live model is started."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('live_benchmark_test_subject', REPO / 'benchmarks/live_agent.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class LivePilot(unittest.TestCase):
    def test_holdout_timeout_returns_failed_outcome_and_test_receipt_omits_private_output(self):
        with patch.object(pilot.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 15)):
            result = pilot.holdout(Path('/fixture'), pilot.TASKS[0])
        self.assertFalse(result['passed'])
        self.assertTrue(result['timed_out'])
        tests = dict(baseline=dict(status='passed', summary=dict(total=2, failed=0), seconds=.1,
                                  stdout='/private/source/path', stderr='sensitive-output', argv=['/private/python']))
        encoded = json.dumps(pilot.test_summary(tests))
        self.assertNotIn('/private', encoded)
        self.assertNotIn('sensitive-output', encoded)
        self.assertEqual(pilot.test_summary(tests)['baseline']['summary']['total'], 2)

    def test_native_metadata_follows_the_workflow_log_rename(self):
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / 'runs' / 'original'
            renamed = original.parent / 'runid'
            renamed.mkdir(parents=True)
            (renamed / 'raw.jsonl').write_text(json.dumps(dict(type='system', model='fixture-model')) + '\n'
                + json.dumps(dict(type='result', total_cost_usd=0.25)) + '\n')
            result = pilot.native_metadata(SimpleNamespace(_log_dir=original, id='runid'))
            self.assertEqual(result, dict(resolved_models=['fixture-model'], cli_reported_cost_usd=0.25,
                                          native_usage=None, native_tools=None))

    def test_seed_tasks_pass_public_tests_but_fail_the_held_out_bug(self):
        with tempfile.TemporaryDirectory() as folder:
            for task in pilot.TASKS:
                root = Path(folder) / task['id']
                root.mkdir()
                pilot.seed(root, task)
                run = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], cwd=root, capture_output=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertFalse(pilot.holdout(root, task)['passed'])

    def test_source_only_stdio_exposes_reads_and_rejects_graph_without_indexing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'sample.py').write_text('raise RuntimeError("never import this source")\n')
            requests = [dict(jsonrpc='2.0', id=1, method='tools/list'),
                        dict(jsonrpc='2.0', id=2, method='tools/call', params=dict(name='cg_read', arguments=dict(path='sample.py'))),
                        dict(jsonrpc='2.0', id=3, method='tools/call', params=dict(name='cg_search', arguments=dict(query='sample')))]
            run = subprocess.run([sys.executable, '-I', str(REPO / 'benchmarks/source_only_mcp.py'), '--skill-root', str(REPO), '--root', str(root)],
                                 input='\n'.join(json.dumps(r) for r in requests) + '\n', text=True, capture_output=True, timeout=20)
            self.assertEqual(run.returncode, 0, run.stderr)
            responses = [json.loads(line) for line in run.stdout.splitlines()]
            self.assertEqual([t['name'] for t in responses[0]['result']['tools']], ['cg_read'])
            self.assertIn('never import this source', responses[1]['result']['content'][0]['text'])
            self.assertTrue(responses[2]['result']['isError'])
            self.assertFalse((root / '.code-graph').exists())

    def test_client_config_redirect_is_bound_correctly_and_leaves_assisted_unchanged(self):
        class Run:
            client = 'codex'
            _root = '/fixture'

            def _argv(self, folder):
                return ['codex', '-c', 'mcp_servers.code-graph.command="original"', '-c', 'mcp_servers.code-graph.args=[]']

        original = Run._argv
        for arm in ('source_only', 'graph_assisted'):
            runner = pilot.PilotRunner(SimpleNamespace(AgentRun=Run), SimpleNamespace(), REPO, pilot.TASKS[0], arm, [0])
            try:
                argv = Run()._argv(Path('/unused'))
                if arm == 'source_only':
                    self.assertEqual(json.loads(argv[2].split('=', 1)[1]), sys.executable)
                    args = json.loads(argv[4].split('=', 1)[1])
                    self.assertEqual(args[-2:], ['--root', '/fixture'])
                    self.assertIn(str(REPO / 'benchmarks/source_only_mcp.py'), args)
                else:
                    self.assertEqual(argv, original(Run(), Path('/unused')))
            finally:
                runner.close()
            self.assertIs(Run._argv, original)

    def test_reference_fixes_pass_the_held_out_cases_and_intents_do_not_name_the_bug(self):
        fixes = {
            'refund': ('domain/refunds.py', 'age_days < 30', 'age_days <= 30', 'refund_allowed'),
            'falsy': ('domain/settings.py', 'values.get(key) or default', 'values[key] if key in values else default', 'lookup_config'),
            'discount': ('shop/discounts.py', 'amount > 100', 'amount >= 100', 'member_discount'),
            'reserve': ('warehouse/reservations.py',
                        '    for line in lines:\n        if available(stock, line["sku"]) < line["qty"]:',
                        '    wanted = {}\n    for line in lines:\n        wanted[line["sku"]] = wanted.get(line["sku"], 0) + line["qty"]\n'
                        '    for sku, qty in wanted.items():\n        if available(stock, sku) < qty:', 'reserve_lines'),
        }
        self.assertEqual({task['id'] for task in pilot.TASKS}, set(fixes))
        with tempfile.TemporaryDirectory() as folder:
            for task in pilot.TASKS:
                name, old, new, buggy = fixes[task['id']]
                with self.subTest(task=task['id']):
                    root = Path(folder) / task['id']
                    root.mkdir()
                    pilot.seed(root, task)
                    path = root / name
                    self.assertIn(old, path.read_text())
                    path.write_text(path.read_text().replace(old, new))
                    self.assertTrue(pilot.holdout(root, task)['passed'], pilot.holdout(root, task))
                    if task['id'] in ('discount', 'reserve'):
                        self.assertNotIn(buggy, task['intent'])
                        self.assertNotIn(name, task['intent'])

    def test_total_review_budget_counts_unreported_reviews_at_their_cap(self):
        row = dict(calls=[dict(client='codex', cli_reported_cost_usd=None), dict(client='claude', cli_reported_cost_usd=0.21),
                          dict(client='claude', cli_reported_cost_usd=None), dict(client='claude', cli_reported_cost_usd=float('nan'))])
        self.assertAlmostEqual(pilot.reported_review_cost(row), 0.21 + 2 * pilot.REVIEW_BUDGET_USD)
        self.assertLessEqual(pilot.REVIEW_BUDGET_USD * len(pilot.TASKS) * 2, pilot.TOTAL_REVIEW_BUDGET_USD)
        self.assertEqual(pilot.MAX_CALLS, 3 * 2 * len(pilot.TASKS))

    def test_prompt_removes_host_graph_leakage_and_enforces_live_start_cap(self):
        class Run:
            status = 'done'

            def _argv(self, folder):
                return []

            def events(self):
                return []

        prompts = []
        agents = SimpleNamespace(AgentRun=Run, start=lambda client, **kwargs: prompts.append(kwargs['prompt']) or Run())
        runner = pilot.PilotRunner(agents, SimpleNamespace(_RETRIEVAL_GUIDANCE='GUIDANCE'), REPO, pilot.TASKS[0], 'source_only', [pilot.MAX_CALLS - 1])
        try:
            prompt = 'GUIDANCE\n\nUse code-graph MCP for evidence.\n' + json.dumps(dict(plan=dict(checked=dict(edges='hidden_graph'), graph=dict(affected='hidden_graph'), agent=dict(summary='keep_plan')), verify=dict(static='hidden_graph', tests='keep_tests')))
            runner.start('codex', prompt=prompt, timeout=900)
            self.assertNotIn('hidden_graph', prompts[0])
            self.assertNotIn('GUIDANCE', prompts[0])
            self.assertIn('keep_plan', prompts[0])
            self.assertIn('keep_tests', prompts[0])
            self.assertIn('cg_read', prompts[0])
            with self.assertRaisesRegex(RuntimeError, 'cap'):
                runner.start('codex', prompt=prompt, timeout=900)
            self.assertEqual(len(prompts), 1)
        finally:
            runner.close()

    def test_original_public_suite_and_test_api_retention_reject_masked_regression(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = pilot.TASKS[0]
            pilot.seed(root, task)
            self.assertTrue(pilot.public_tests(root, task)['passed'])
            path = root / 'domain/refunds.py'
            path.write_text(path.read_text().replace('0 <= age_days < 30', 'age_days == 30'))
            test = root / 'tests/test_refunds.py'
            test.write_text(test.read_text().replace('    def test_paid(self): self.assertEqual(cancel_order({"status":"paid"}, 2), "refund")\n', ''))
            self.assertTrue(pilot.holdout(root, task)['passed'])
            self.assertFalse(pilot.public_tests(root, task)['passed'])
            self.assertFalse(pilot.test_retention(root, task)['passed'])
            self.assertTrue(pilot.api_retention(root, task)['passed'])
            path.write_text(path.read_text().replace('age_days', 'days'))
            self.assertFalse(pilot.api_retention(root, task)['passed'])
            pilot.seed(root, task)
            test.write_text(test.read_text() + '    def test_new(self): pass\n')
            self.assertFalse(pilot.regression_probe(root, task)['passed'])
            test.write_text(test.read_text().replace('def test_new(self): pass', 'def test_new(self): self.assertEqual(cancel_order({"status":"paid"}, 30), "refund")'))
            path.write_text(path.read_text().replace('age_days < 30', 'age_days <= 30'))
            self.assertTrue(pilot.regression_probe(root, task)['passed'])

    def test_native_cache_counts_tool_outcomes_and_stderr_access_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            events = [dict(type='assistant', message=dict(content=[dict(type='tool_use', id='a', name='mcp__code_graph__cg_search')])),
                      dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id='a', is_error=True, content='failed')])),
                      dict(type='result', usage=dict(input_tokens=10, output_tokens=5, cache_read_input_tokens=30, cache_creation_input_tokens=20))]
            (logs / 'raw.jsonl').write_text('\n'.join(json.dumps(event) for event in events))
            run = SimpleNamespace(_log_dir=logs, id='run', events=lambda: [], status='failed')
            metadata = pilot.native_metadata(run)
            self.assertEqual(metadata['native_usage'][0]['input_tokens'], 10)
            self.assertEqual(metadata['native_usage'][0]['cache_creation_input_tokens'], 20)
            self.assertEqual(metadata['native_tools'][0]['status'], 'error')
            self.assertFalse(metadata['native_tools'][0]['graph_evidence_observed'])
            for text, expected in [('Usage limit reached; quota exhausted.', 'quota_or_rate_limit'),
                                   ("You've hit your weekly limit", 'quota_or_rate_limit'),
                                   ('Authentication failed: expired token', 'authentication')]:
                (logs / 'stderr.txt').write_text(text)
                self.assertEqual(pilot.access_failure(SimpleNamespace(runs=[run], start_failures=[])), expected)

    def test_native_cache_write_preserves_zero_positive_and_absence(self):
        with tempfile.TemporaryDirectory() as folder:
            logs = Path(folder)
            for value in (0, 17, None):
                with self.subTest(cache_write_input_tokens=value):
                    usage = dict(input_tokens=10, output_tokens=5, cache_creation_input_tokens=20)
                    if value is not None:
                        usage['cache_write_input_tokens'] = value
                    (logs / 'raw.jsonl').write_text(json.dumps(dict(type='turn.completed', usage=usage)) + '\n')
                    run = SimpleNamespace(_log_dir=logs, id='run')
                    actual = pilot.native_metadata(run)['native_usage'][0]
                    self.assertEqual(actual.pop('cache_write_input_tokens'), value)
                    self.assertEqual(actual, dict(input_tokens=10, output_tokens=5, cached_input_tokens=None,
                        cache_read_input_tokens=None, cache_creation_input_tokens=20, reasoning_output_tokens=None))

    def test_flow_timeout_drains_worker_and_blocks_late_start_before_restoring_argv(self):
        task, load = pilot.TASKS[0], pilot.load
        release, starts, timers, planned_tests = threading.Event(), [], [], []

        class Run:
            status, usage, _log_dir = 'done', None, None

            def __init__(self, client, kwargs):
                self.client, self.mode = client, kwargs['mode']
                self.id = str(len(starts))
                self.result = dict(summary='Boundary', touch=[dict(path='domain/refunds.py', symbol='refund_allowed', line=1, why='Fix')],
                                   affected=[], tests=planned_tests, out_of_scope=[], risks=[], unknowns=[], test_command=[])

            def _argv(self, folder): return []
            def events(self, since=0): return []
            def wait(self, timeout=None): return self.status
            def cancel(self): self.status = 'cancelled'

        original = Run._argv

        def start(client, **kwargs):
            starts.append(kwargs['mode'])
            return Run(client, kwargs)

        def fake_load(path, name):
            if name == 'pilot_agents':
                return SimpleNamespace(AgentRun=Run, start=start)
            module = load(path, name)
            if name == 'pilot_flow':
                base = module.ChangeFlow
                class Flow(base):
                    def _test(self, record):
                        release.wait(5)
                        return super()._test(record)
                    def wait(self, cid, timeout=None):
                        if self.get(cid)['state'] == 'baseline':
                            timer = threading.Timer(.2, release.set)
                            timers.append(timer)
                            timer.start()
                            timeout = .02
                        return super().wait(cid, timeout=timeout)
                module.ChangeFlow = Flow
            return module

        try:
            with tempfile.TemporaryDirectory() as folder, patch.object(pilot, 'load', fake_load):
                row = pilot.run_case(REPO, Path(folder) / 'case', task, 'source_only', [0])
                self.assertTrue(row['cleanup_drained'])
                self.assertEqual(starts, ['read'])
                self.assertEqual(len(row['calls']), 1)
                self.assertEqual(row['final_state'], 'approved')
                self.assertIs(Run._argv, original)
                planned_tests.append(dict(path='unrelated_0.py', name='helper_0', why='Outside task'))
                starts.clear()
                outside = pilot.run_case(REPO, Path(folder) / 'outside', task, 'source_only', [0])
                self.assertEqual(outside['outcome'], 'plan_outside_fixture_scope')
                self.assertEqual(starts, ['read'])
                self.assertIs(Run._argv, original)
        finally:
            release.set()
            for timer in timers:
                timer.join()
