"""`cg locate`: request text to places, callers and tests in one bounded, model-free answer."""
import json
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
CG = REPO / 'scripts/cg'


def runtime():
    engine = SimpleNamespace(**runpy.run_path(str(REPO / 'scripts/cg.py')))
    project = runpy.run_path(str(REPO / 'scripts/project_data.py'))['ProjectData']
    locate = runpy.run_path(str(REPO / 'scripts/locate.py'))
    return engine, project, locate


def run(root, *args):
    result = subprocess.run([str(CG), '--root', str(root), *args], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout


def large_cli(root, text):
    # Run the unmodified CLI in a child, passing argv via stdin so OS argument-size limits
    # do not constrain the application's character-limit test.
    bootstrap = ('import json,runpy,sys; command=sys.argv[1]; '
                 'sys.argv=[command,*json.load(sys.stdin)]; runpy.run_path(command,run_name="__main__")')
    return subprocess.run([sys.executable, '-I', '-c', bootstrap, str(REPO / 'scripts/cg.py')],
                          input=json.dumps(['--root', str(root), 'locate', text, '--json']),
                          capture_output=True, text=True, timeout=20)


class Locate(unittest.TestCase):
    def project(self, folder):
        root = Path(folder) / 'shop'
        (root / 'tests').mkdir(parents=True)
        (root / 'orders.py').write_text('from ledger import record\ndef cancel_order(order):\n    """Cancel an order and refund it."""\n    return record(order)\n')
        (root / 'ledger.py').write_text('def record(entry):\n    return entry\n')
        (root / 'api.py').write_text('from orders import cancel_order\ndef handle_cancel(request):\n    return cancel_order(request)\n')
        (root / 'tests/test_orders.py').write_text('from orders import cancel_order\ndef test_cancel():\n    assert cancel_order(1) == 1\n')
        for i in range(5):
            (root / f'other_{i}.py').write_text(f'def unrelated_{i}():\n    return {i}\n')
        return root

    def test_request_limit_and_clear_rejections(self):
        engine, Project, module = runtime()
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder)
            run(root, 'index')
            with Project(engine, root) as project:
                for size in (20001, 200000):
                    text = 'cancel order\n'.ljust(size)
                    value = module['locate'](project, text)
                    self.assertEqual(value['status'], 'ready')
                    self.assertEqual(value['query']['input_chars'], size)
                    self.assertEqual(value['query']['analysed_chars'], 20000)
                    self.assertTrue(value['query']['trimmed'])
                for invalid in ('', 'x' * 200001):
                    with self.assertRaisesRegex(ValueError, '200000'):
                        module['locate'](project, invalid)

    def test_long_requests_follow_the_cli_handler_and_real_stdio_mcp(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder)
            text = 'cancel order\n'.ljust(200000)
            output = large_cli(root, text)
            self.assertEqual(output.returncode, 0, output.stderr)
            cli = json.loads(output.stdout)
            self.assertEqual(cli['query']['input_chars'], 200000)
            for invalid in ('', 'x' * 200001):
                rejected = large_cli(root, invalid)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn('200000', rejected.stderr)
            executable = json.loads(run(root, 'locate', text[:20001], '--json'))
            self.assertTrue(executable['query']['trimmed'])
            calls = [dict(jsonrpc='2.0', id=i, method='tools/call',
                          params=dict(name='cg_locate', arguments=dict(text=value)))
                     for i, value in enumerate((text, 'x' * 200001, ''), 1)]
            calls.append(dict(jsonrpc='2.0', id=4, method='ping'))
            response = subprocess.run([str(CG), '--root', str(root), 'mcp'],
                                      input=''.join(json.dumps(call) + '\n' for call in calls),
                                      capture_output=True, text=True, timeout=20)
            self.assertEqual(response.returncode, 0, response.stderr)
            results = [json.loads(line)['result'] for line in response.stdout.splitlines()]
            self.assertFalse(results[0]['isError'], results[0])
            self.assertEqual(json.loads(results[0]['content'][0]['text']), cli)
            for error in results[1:3]:
                self.assertTrue(error['isError'], error)
                self.assertRegex(error['content'][0]['text'].lower(), r'text|request.*200000')
            self.assertEqual(results[-1], {})

    def test_long_request_frames_are_found_at_start_middle_and_end(self):
        engine, Project, module = runtime()
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder)
            (root / 'trace.js').write_text('function dispatch(value) {\n  return value;\n}\n')
            (root / 'trace.go').write_text('package trace\n\nfunc Route() int { return 1 }\n')
            run(root, 'index')
            traces = [('  File "/srv/orders.py", line 300, in cancel_order', 'orders.py'),
                      ('    at dispatch (/srv/trace.js:2:4)', 'trace.js'),
                      ('/srv/trace.go:3 +0x1d', 'trace.go')]
            with Project(engine, root) as project:
                for frame, expected in traces:
                    for offset in (0, 100000, 200000 - len(frame) - 2):
                        with self.subTest(frame=expected, offset=offset):
                            text = (' ' * offset + '\n' + frame + '\n').ljust(200000)
                            value = module['locate'](project, text)
                            self.assertEqual(value['items'][0]['file'], expected)
                            self.assertIn('stack frame', value['items'][0]['why'])

    def test_long_request_trimming_is_declared_even_at_the_smallest_budget(self):
        engine, Project, module = runtime()
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder)
            run(root, 'index')
            with Project(engine, root) as project:
                for kind, request in [('matches', 'cancel order\n'.ljust(200000)), ('oversized term', 'x' * 200000)]:
                    for render in (module['as_json'], module['as_text']):
                        for budget in (512, 1200, 5000):
                            with self.subTest(kind=kind, render=render.__name__, budget=budget):
                                value = module['locate'](project, request, max_chars=budget, render=render)
                                output = render(value)
                                self.assertLessEqual(len(output), budget)
                                self.assertTrue(value['query']['trimmed'])
                                self.assertEqual(value['query']['analysed_chars'], 20000)
                                if kind == 'oversized term':
                                    self.assertEqual(value['query']['omitted_terms'], 1)
                                self.assertGreaterEqual(value['reading']['returned_chars'], len(output))
                                if value['reading'].get('exact', True):
                                    self.assertEqual(value['reading']['returned_chars'], len(output))
                                if render is module['as_text']:
                                    self.assertIn('stack frames', output)

    def test_200000_character_adversarial_inputs_finish_under_two_seconds(self):
        engine, Project, module = runtime()
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder)
            (root / 'trace.go').write_text('package trace\n\nfunc Route() int { return 1 }\n')
            run(root, 'index')
            inputs = [('a-.' * 66667)[:200000], '\n' * 200000,
                      ('/srv/trace.go:3 +0x1d\n' * 10000)[:200000], 'x' * 200000]
            with Project(engine, root) as project:
                for number, text in enumerate(inputs):
                    with self.subTest(input=number):
                        self.assertEqual(len(text), 200000)
                        started = time.perf_counter()
                        value = module['locate'](project, text)
                        elapsed = time.perf_counter() - started
                        self.assertLess(elapsed, 2.0)
                        self.assertTrue(value['query']['trimmed'])
                        self.assertLessEqual(len(module['as_json'](value)), 5000)

    def test_query_budget_applies_to_short_and_long_requests(self):
        engine, Project, module = runtime()
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder)
            run(root, 'index')
            requests = ['x' * size for size in (513, 20000, 20001)]
            requests.append('x' * 300 + ' ' + 'y' * 300 + ' cancel order')
            with Project(engine, root) as project:
                for request in requests:
                    for budget in (512, 1200, 5000):
                        for render in (module['as_json'], module['as_text']):
                            with self.subTest(size=len(request), budget=budget, render=render.__name__):
                                value = module['locate'](project, request, max_chars=budget, render=render)
                                self.assertEqual(value['query']['omitted_terms'], 1)
                                self.assertLessEqual(len(' '.join(value['query'].get('terms', []))), 512)
                                self.assertLessEqual(len(render(value)), budget)
                                if len(request) <= 20000:
                                    self.assertNotIn('trimmed', value['query'])
                                else:
                                    self.assertTrue(value['query']['trimmed'])
                                if 'cancel order' in request and budget == 5000:
                                    self.assertEqual(value['items'][0]['name'], 'cancel_order')
                                    self.assertIn('cancel', value['query']['terms'])

    def test_issue_text_finds_place_callers_tests_and_reports_reading_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder)
            value = json.loads(run(root, 'locate', 'Cancelling an order does not refund the customer', '--json'))
            self.assertEqual(value['format'], 'codegraph-locate/1')
            first = value['items'][0]
            self.assertEqual((first['file'], first['name']), ('orders.py', 'cancel_order'))
            self.assertEqual(first['callers']['count'], 1)
            self.assertIn('api.py', first['callers']['shown'][0])
            self.assertEqual(first['tests']['count'], 1)
            self.assertIn('test_cancel', first['tests']['shown'][0])
            self.assertIn('def cancel_order', first['source']['text'])
            self.assertEqual(len(first['source']['source_sha256']), 64)
            self.assertTrue(all(not item['file'].startswith('tests/') for item in value['items']))
            reading = value['reading']
            self.assertLessEqual(reading['returned_chars'], 5000)
            self.assertEqual(reading['returned_chars'], len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))) + 1)
            self.assertGreater(reading['file_chars'], 0)

    def test_terms_prefer_code_tokens_and_drop_stopwords(self):
        import runpy
        locate = runpy.run_path(str(REPO / 'scripts/locate.py'))
        words = runpy.run_path(str(REPO / 'scripts/search.py'))['words']
        terms = locate['query_terms']('The `refund_order` call in OrderService.cancel is wrong when the order is empty', words)
        self.assertEqual(terms[:2], ['refund', 'order'])
        self.assertIn('service', terms)
        self.assertIn('cancel', terms)
        self.assertNotIn('the', terms)
        self.assertNotIn('wrong', terms)
        self.assertLessEqual(len(terms), 16)

    def test_default_budget_keeps_five_candidates_with_declared_source_cuts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'handlers'
            handlers = root / 'project_components/order_processing/customer_fulfillment_context/request_handlers'
            handlers.mkdir(parents=True)
            for number in range(5):
                (handlers / f'handler_{number}.py').write_text(
                    f'def process_order_{number}(value):\n'
                    '    """' + 'Process an order and preserve its existing value. ' * 8 + '"""\n'
                    '    first = value + 1\n    second = first + 1\n'
                    '    third = second + 1\n    fourth = third + 1\n    return fourth\n')
            text, value = self.answer(root, 'process order', '--json')
            self.assertEqual(len(value['items']), 5)
            self.assertEqual(len({item['file'] for item in value['items']}), 5)
            self.assertLessEqual(len(text), 5000)
            self.assertTrue(all(item['source']['truncated'] for item in value['items']))
            _, smaller = self.answer(root, 'process order', '--json', '--max-chars', '4000')
            self.assertLess(len(smaller['items']), 5)
            request = dict(jsonrpc='2.0', id=1, method='tools/call',
                           params=dict(name='cg_locate', arguments=dict(text='process order')))
            response = subprocess.run([str(CG), '--root', str(root), 'mcp'], input=json.dumps(request) + '\n',
                                      capture_output=True, text=True, timeout=20)
            self.assertEqual(response.returncode, 0, response.stderr)
            result = json.loads(response.stdout)['result']
            self.assertFalse(result['isError'], result)
            self.assertEqual(json.loads(result['content'][0]['text']), value)

    def test_code_names_precede_url_payloads_and_scoped_names_keep_the_leaf(self):
        import runpy
        locate = runpy.run_path(str(REPO / 'scripts/locate.py'))
        search = runpy.run_path(str(REPO / 'scripts/search.py'))
        request = ('Bug: [guard-missing-value] gives a false positive\n'
                   'https://example.invalid/play?config=N4KABGBEBOCuA2BTAzpAXGYBfEWg#code=encoded\n'
                   'Read https://example.invalid/help and check the configuration.')
        terms = locate['query_terms'](request, search['words'])
        self.assertEqual(terms[:3], ['guard', 'missing', 'value'])
        for noise in ('example', 'invalid', 'config', 'n4', 'encoded'):
            self.assertNotIn(noise, terms)
        terms = locate['query_terms']('@lint-rules/no-floating-requests is incorrect', search['words'])
        self.assertEqual(terms[:2], ['floating', 'requests'])
        terms = locate['query_terms']('Processing fails in guard-missing-value.ts', search['words'])
        self.assertEqual(terms[:3], ['guard', 'missing', 'value'])

    def test_source_paths_in_links_keep_the_file_but_not_encoded_query_words(self):
        import runpy
        locate = runpy.run_path(str(REPO / 'scripts/locate.py'))
        search = runpy.run_path(str(REPO / 'scripts/search.py'))
        text = 'See https://example.invalid/blob/revision/src/cache-store.ts?code=EncodedPayload#L42'
        terms = locate['query_terms'](text, search['words'])
        self.assertEqual(terms[:2], ['cache', 'store'])
        for noise in ('example', 'invalid', 'encoded', 'payload', 'ts'):
            self.assertNotIn(noise, terms)

    def test_repository_url_prefixes_and_path_directories_are_not_query_words(self):
        import runpy
        locate = runpy.run_path(str(REPO / 'scripts/locate.py'))
        search = runpy.run_path(str(REPO / 'scripts/search.py'))
        text = ('The `CacheStore.lookup` method crashes, see '
                'https://host.invalid/organization/repository/blob/topic/branch/src/cache_store.py#L10 '
                'and docs/guide/reference/formats.md')
        terms = locate['query_terms'](text, search['words'])
        self.assertEqual(terms[:3], ['cache', 'store', 'lookup'])
        for noise in ('organization', 'repository', 'blob', 'topic', 'branch', 'src', 'docs', 'guide', 'reference', 'md', 'py'):
            self.assertNotIn(noise, terms)
        cleaned = locate['request_text'](text, ['src/cache_store.py'])
        self.assertIn('src/cache_store.py', cleaned)
        self.assertNotIn('organization/repository', cleaned)

    def test_bare_kebab_uses_the_same_rule_as_quoted_references(self):
        import runpy
        locate = runpy.run_path(str(REPO / 'scripts/locate.py'))
        words = runpy.run_path(str(REPO / 'scripts/search.py'))['words']
        for compound in ('read-only', 'built-in', 'well-known', 'non-null', 'out-of-date', 'word-' * 7 + 'ending'):
            with self.subTest(compound=compound):
                bare = locate['query_terms'](compound + ' fails in `CacheStore.lookup`', words)
                quoted = locate['query_terms']('`' + compound + '` fails in `CacheStore.lookup`', words)
                self.assertEqual(bare, quoted)

    def test_punctuation_flood_is_not_interpreted_as_a_code_reference(self):
        import runpy
        locate = runpy.run_path(str(REPO / 'scripts/locate.py'))
        words = runpy.run_path(str(REPO / 'scripts/search.py'))['words']
        self.assertEqual(locate['query_terms']('a-.' * 6666, words), [])

    def test_named_files_precede_text_matches_in_the_evaluated_order(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'p'
            (root / 'pkg').mkdir(parents=True)
            (root / 'pkg/config_loader.go').write_text(
                'package pkg\nfunc Alpha() {}\nfunc Beta() {}\nfunc Gamma() {}\n')
            (root / 'pkg/http_server.go').write_text(
                'package pkg\nfunc Start() {}\nfunc Stop() {}\nfunc Restart() {}\n')
            (root / 'pkg/compaction.go').write_text(
                'package pkg\nfunc CompactBlocks() { mergeOverlapping() }\n'
                'func mergeOverlapping() { /* merging chunks of overlapping blocks */ }\n')
            _, value = self.answer(root, 'Compaction of overlapping blocks panics when merging chunks. '
                                   'My setup loads config via config_loader.go and serves through http_server.go.', '--json')
            # Restore the evaluated order: even files mentioned only as context precede text matches.
            # This can move the actual compaction bug behind setup code, an explicit trade-off.
            self.assertEqual([item['file'] for item in value['items']],
                             ['pkg/config_loader.go', 'pkg/config_loader.go',
                              'pkg/http_server.go', 'pkg/http_server.go', 'pkg/compaction.go'])
            self.assertTrue(all(item['why'] == 'named file' for item in value['items'][:4]))
            self.assertIn(value['items'][-1]['name'], {'CompactBlocks', 'mergeOverlapping'})

    def test_a_named_file_without_usable_words_does_not_invent_declarations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'p'
            root.mkdir()
            (root / 'all.py').write_text('def alpha(): return 1\ndef beta(): return 2\ndef gamma(): return 3\n')
            _, value = self.answer(root, '`all.py`', '--json')
            self.assertEqual(value['query']['terms'], [])
            self.assertEqual(value['items'], [])
            self.assertEqual(value['status'], 'empty')

    def test_named_rule_file_is_found_even_when_its_callable_has_a_generic_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'rules'
            root.mkdir()
            (root / 'guard-missing-value.ts').write_text(
                'export function create(context: unknown) { return context; }\n')
            for number in range(55):
                (root / f'noise_{number}.ts').write_text(
                    f'export function guardMissingValue{number}() {{ return "guard missing value"; }}\n')
            _, value = self.answer(root, '[guard-missing-value] reports a false positive', '--json')
            hinted = [item for item in value['items'][:3] if item['file'] == 'guard-missing-value.ts']
            self.assertTrue(hinted)
            self.assertIn('named file', hinted[0]['why'])

    def test_source_path_and_qualified_name_disambiguate_without_opening_request_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'p'
            (root / 'src').mkdir(parents=True)
            (root / 'legacy').mkdir()
            (root / 'src/cache-store.py').write_text('def create(value):\n    return value\n')
            (root / 'legacy/cache-store.py').write_text('def create(value):\n    return value\n')
            (root / 'service.py').write_text(
                'class OrderService:\n    def cancel(self, order):\n        return order\n')
            _, value = self.answer(root, 'See https://example.invalid/blob/rev/src/cache-store.py?payload=Noise', '--json')
            self.assertIn('src/cache-store.py', [item['file'] for item in value['items'][:3]])
            _, value = self.answer(root, '`OrderService.cancel` fails', '--json')
            self.assertEqual(value['items'][0]['name'], 'OrderService.cancel')
            self.assertIn('named symbol', value['items'][0]['why'])

    def test_candidates_keep_at_most_two_per_file_when_other_files_match(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'p'
            root.mkdir()
            (root / 'dominant_rule.py').write_text(''.join(
                f'def process_order_{number}():\n    return "process order"\n' for number in range(5)))
            for number in range(4):
                (root / f'other_{number}.py').write_text('def process_order():\n    return 1\n')
            _, value = self.answer(root, '`dominant_rule.py` process order', '--json', '--max-chars', '8000')
            self.assertEqual(len(value['items']), 5)
            self.assertGreaterEqual(len({item['file'] for item in value['items']}), 3)
            self.assertLessEqual(sum(item['file'] == 'dominant_rule.py' for item in value['items']), 2)
            for path in root.glob('other_*.py'):
                path.unlink()
            _, value = self.answer(root, 'process order', '--json', '--max-chars', '8000')
            self.assertEqual(len(value['items']), 5)  # a one-file project can still return five declarations

    def test_ambiguous_basename_does_not_override_text_ranking(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'p'
            for directory in ('src', 'legacy'):
                (root / directory).mkdir(parents=True)
                (root / directory / 'cache.py').write_text('def create(value):\n    return value\n')
            _, value = self.answer(root, '`cache.py` fails to create a value', '--json')
            self.assertTrue(value['items'])
            self.assertTrue(all(item['why'] != 'named file' for item in value['items']))
            _, value = self.answer(root, '`src/cache.py` fails to create a value', '--json')
            self.assertIn('src/cache.py', [item['file'] for item in value['items'][:3]])

    def test_stack_trace_puts_the_most_traversed_file_first_by_function_name(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder)
            (root / 'helpers.py').write_text('def compare(a, b):\n    return a < b\n')
            (root / 'ledger.py').write_text('from helpers import compare\ndef record(entry):\n    return entry\ndef check(entry):\n    return compare(entry, 0)\n')
            trace = ('TypeError: Invalid NaN comparison\nTraceback (most recent call last):\n'
                     '  File "/site-packages/shop/api.py", line 90, in handle_cancel\n'
                     '  File "/site-packages/shop/ledger.py", line 70, in record\n'
                     '  File "/site-packages/shop/ledger.py", line 75, in check\n'
                     '  File "/site-packages/shop/helpers.py", line 2, in compare\n')
            value = json.loads(run(root, 'locate', trace, '--json'))
            first = value['items'][0]
            self.assertEqual((first['file'], first['name']), ('ledger.py', 'check'))
            self.assertIn('stack frame', first['why'])
            self.assertEqual(value['query']['frames'], 3)

    def test_no_terms_and_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder)
            self.assertEqual(json.loads(run(root, 'locate', 'the and of', '--json'))['status'], 'empty')
            result = subprocess.run([str(CG), '--root', str(root), 'locate', 'x', '--max-chars', '10'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def answer(self, root, *args):
        text = run(root, 'locate', *args)
        return text, (json.loads(text) if '--json' in args else None)

    def test_every_branch_measures_the_answer_it_returns(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder)
            request = 'Cancelling an order does not refund the customer'
            for args in (('the and of', '--json'), (request, '--json'), (request, '--json', '--max-chars', '512'),
                         (request, '--json', '--max-chars', '1200', '--limit', '5')):
                text, value = self.answer(root, *args)
                self.assertEqual(value['reading']['returned_chars'], len(text), args)
                budget = int(args[args.index('--max-chars') + 1]) if '--max-chars' in args else 5000
                self.assertLessEqual(len(text), budget, args)
                expected = 'ready' if value['items'] else ('budget_insufficient' if value.get('omitted') else 'empty')
                self.assertEqual(value['status'], expected, args)
                files = sorted({path for item in value['items'] for path in item['files']})
                self.assertEqual(value['reading']['files'], len(files), args)
                self.assertEqual(value['reading']['file_chars'],
                                 sum(len((root / path).read_text()) for path in files), args)
            text, _ = self.answer(root, request)
            self.assertIn(f"reading: {len(text):,} characters here", text)
            self.assertNotIn('-', text.splitlines()[1].split('(')[1])  # never a negative saving

    def test_long_lines_and_missing_final_newline_are_declared_and_counted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'p'
            root.mkdir()
            body = 'def compute_invoice_total(items): return ' + ' + '.join(f'items[{i}]' for i in range(60))
            (root / 'invoice.py').write_text(body)  # one long line, no final newline
            _, value = self.answer(root, 'compute_invoice_total is wrong', '--json')
            item = value['items'][0]
            self.assertEqual(item['name'], 'compute_invoice_total')
            self.assertTrue(item['source']['truncated'])
            self.assertEqual(item['source']['lines_cut'], 1)
            self.assertEqual(value['reading']['file_chars'], len(body))

    def test_a_name_that_covers_the_request_leads_over_a_short_body(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'p'
            root.mkdir()
            (root / 'orders.py').write_text(
                'from notes import notify_cancelled\nfrom pay import refund\n'
                'def cancel_order(order):\n    """Cancel an order: give the stock back and refund what the customer paid."""\n'
                '    if order.status == "cancelled":\n        return 0\n    order.release()\n'
                '    refunded = refund(order, order.paid)\n    order.status = "cancelled"\n'
                '    notify_cancelled(order, refunded)\n    return refunded\n')
            (root / 'notes.py').write_text('def notify_cancelled(order, refunded):\n'
                                           '    send(order.customer_id, f"Order {order.id} cancelled, refunded {refunded}")\n')
            (root / 'pay.py').write_text('def refund(order, amount):\n    order.paid -= amount\n    return amount\n')
            _, value = self.answer(root, 'Cancelling an order does not refund the customer', '--json')
            self.assertEqual(value['items'][0]['name'], 'cancel_order')

    def test_reading_counter_describes_the_rendering_or_says_it_is_an_upper_bound(self):
        import runpy
        locate = runpy.run_path(str(REPO / 'scripts/locate.py'))
        inexact = 0
        for total in list(range(1, 3000, 7)) + [10 ** 6 + 3]:
            for pad in range(0, 400, 13):
                result = dict(items=[dict(files=['a'])], x='y' * pad)
                size = locate['measure'](result, locate['as_json'], {'a': total})
                real = len(locate['as_json'](result))
                if result['reading'].get('exact', True):
                    self.assertEqual(size, real, (total, pad))
                else:
                    inexact += 1
                    self.assertTrue(0 <= size - real <= 3, (total, pad, size, real))
        self.assertLess(inexact, 13299 // 10)

    def test_trace_lines_do_not_become_search_words_and_roots_are_not_repeated(self):
        import runpy
        locate = runpy.run_path(str(REPO / 'scripts/locate.py'))
        search = runpy.run_path(str(REPO / 'scripts/search.py'))
        text = ('Refunds fail for cancelled orders\nTraceback (most recent call last):\n'
                '  File "/srv/app/shop/orders.py", line 22, in cancel_order\n'
                '    at handle (/srv/web/src/api.ts:10:3)\n/srv/go/shop/pay.go:14 +0x1d\n'
                'ValueError: refund larger than the captured amount')
        terms = locate['query_terms'](text, search['words'], search['stem'])
        for noise in ('srv', 'app', 'py', 'line', 'file', 'traceback', 'ts', 'go', 'web', 'src'):
            self.assertNotIn(noise, terms)
        self.assertIn('cancel', [search['stem'](term) for term in terms])
        self.assertEqual(len({search['stem'](term) for term in terms}), len(terms))

    def test_a_trace_of_bare_frames_still_names_the_place(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'p'
            root.mkdir()
            (root / 'f.py').write_text('def get(x):\n    return x\n')
            (root / 'cache.go').write_text('package cache\n\nfunc Get(k string) string {\n\treturn k\n}\n')
            for trace, name in (('Traceback (most recent call last):\n  File "/srv/f.py", line 2, in get\n', 'get'),
                                ('/srv/go/cache.go:4 +0x1d\n', 'Get')):
                with self.subTest(trace=trace):
                    _, value = self.answer(root, trace, '--json')
                    self.assertEqual(value['status'], 'ready')
                    self.assertEqual(value['items'][0]['name'], name)
                    self.assertIn('stack frame', value['items'][0]['why'])

    def test_text_output_says_when_the_size_is_an_upper_bound(self):
        import runpy
        locate = runpy.run_path(str(REPO / 'scripts/locate.py'))
        value = dict(items=[], status='ready', query=dict(terms=['x']),
                     reading=dict(files=1, file_chars=221, returned_chars=201, saved_ratio=0.09, exact=False))
        self.assertIn('reading: at most 201 characters', locate['as_text'](value))

    def test_frame_name_is_matched_literally(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'p'
            root.mkdir()
            (root / 'service.py').write_text('class C:\n    def asolve(self): return 1\n    def _solve(self): return 2\n')
            trace = 'Traceback (most recent call last):\n  File "/external/service.py", line 2, in _solve\n'
            _, value = self.answer(root, trace, '--json')
            self.assertEqual(value['items'][0]['name'], 'C._solve')


if __name__ == '__main__':
    unittest.main()
