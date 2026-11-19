"""Retrieval contracts on source that is parsed, never executed."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_search_test', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
search = cg.search_module()
ProjectData = runpy.run_path(str(REPO / 'scripts/project_data.py'))['ProjectData']


class Search(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cg-search-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'project'
        self.root.mkdir()

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def index(self, full=False):
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(self.root), full=full))

    def find(self, query, **kwargs):
        with ProjectData(cg, self.root) as project:
            return search['search'](project, query, **kwargs)

    def names(self, result):
        return [item['symbol']['name'] for item in result['items']]

    def test_roots_strip_one_suffix_and_never_go_below_four_characters(self):
        stem = search['stem']
        self.assertEqual([stem(word) for word in ('cancelling', 'cancellation', 'parsing', 'releasing', 'orders', 'stopped')],
                         ['cancel', 'cancel', 'pars', 'releas', 'order', 'stop'])
        self.assertEqual([stem(word) for word in ('uses', 'bed', 'class')], ['uses', 'bed', 'clas'])

    def test_widened_matches_follow_every_exact_match_and_are_marked(self):
        self.put('billing.py', 'def cancel_invoice():\n    return 1\n')
        self.put('orders.py', 'def abort_order():\n    return 2\n')
        self.put('other.py', 'def unrelated():\n    return 3\n')
        self.index()
        result = self.find('abort', mode='lexical')
        self.assertEqual(self.names(result), ['abort_order', 'cancel_invoice'])
        self.assertEqual([item['lexical']['match'] for item in result['items']], ['exact', 'expanded'])
        self.assertEqual(result['retrieval']['exact_candidates'], 1)
        self.assertIn(dict(kind='synonym', term='cancel', source='abort'), result['query']['expansions'])

    def test_widened_match_never_outranks_an_exact_match_in_hybrid_mode(self):
        # Reproduction from the S1 review: an expanded match that is also a graph neighbour
        # must not gain a lexical RRF contribution and overtake the exact symbol.
        self.put('z.py', 'def cancel_job():\n    return 1\n')
        self.put('a.py', 'from z import cancel_job as helper\ndef abort_alpha():\n    return helper()\n')
        self.put('b.py', 'def abort_beta():\n    return 1\n')
        self.put('c.py', 'def abort_gamma():\n    return 1\n')
        self.index()
        result = self.find('abort_alpha', limit=1, max_chars=16000)
        self.assertEqual(self.names(result), ['abort_alpha'])
        ranked = self.find('abort_alpha', limit=10, max_chars=16000)
        neighbour = next(item for item in ranked['items'] if item['symbol']['name'] == 'cancel_job')
        self.assertEqual(neighbour['lexical']['match'], 'expanded')
        self.assertAlmostEqual(neighbour['rrf_score'], 1 / (60 + neighbour['graph']['rank']))

    def test_synonym_and_inflection_find_code_without_the_query_words(self):
        self.put('locks.py', 'def release_lock():\n    """Free the mutex."""\n    return 1\n')
        self.put('other.py', 'def unrelated():\n    return 3\n')
        self.index()
        self.assertEqual(self.names(self.find('releasing locks', mode='lexical')), ['release_lock'])
        self.assertEqual(self.names(self.find('unlock', mode='lexical')), ['release_lock'])
        self.assertEqual(self.find('quasar nebula')['status'], 'empty')

    def test_compact_source_keeps_lines_hash_and_only_informative_window_fields(self):
        self.put('a.py', 'def leaf():\n    return 1\n')
        self.index()
        result = self.find('leaf')
        self.assertEqual(result['format'], 'codegraph-search/2')
        source = result['items'][0]['source']
        self.assertEqual((source['start_line'], source['text']), (1, 'def leaf():\n    return 1'))
        self.assertEqual(len(source['source_sha256']), 64)
        self.assertNotIn('file', source)
        self.assertNotIn('source_revision', source)
        self.assertEqual(result['retrieval']['source_window']['line_chars'], 200)

    def fixture(self):
        self.put('orders.py', 'from payments import reverse_charge\ndef cancel_order():\n    """Cancel order."""\n    return reverse_charge()\n')
        self.put('payments.py', 'def reverse_charge(): return 1\n')
        self.put('config.py', 'def parse_config(): return 1\n')
        self.index()

    def test_hybrid_retrieves_connected_symbol_without_query_words(self):
        self.fixture()
        lexical = self.find('cancel order', mode='lexical')
        hybrid = self.find('cancel order')
        self.assertEqual(self.names(lexical), ['cancel_order'])
        self.assertIn('reverse_charge', self.names(hybrid))
        self.assertNotIn('parse_config', self.names(hybrid))
        hit = next(item for item in hybrid['items'] if item['symbol']['name'] == 'reverse_charge')
        self.assertIsNone(hit['lexical'])
        step = hit['graph']['chain'][0]
        self.assertEqual((step['file'], step['line'], step['relation'], step['direction']),
                         ('orders.py', 4, 'call', 'out'))
        self.assertAlmostEqual(hit['rrf_score'], 1 / (60 + hit['graph']['rank']))

    def test_rrf_counts_each_channel_once(self):
        self.fixture()
        self.put('others.py', ''.join(f'def cancel_{i}(): return cancel_order()\n' for i in range(6)))
        self.index()
        result = self.find('cancel', limit=20, max_chars=16000)
        self.assertEqual(len(self.names(result)), len(set(self.names(result))))
        both = 0
        for item in result['items']:
            expected = 0
            if item['lexical']:
                expected += 1 / (60 + item['lexical']['rank'])
            if item['graph']:
                expected += 1 / (60 + item['graph']['rank'])
            both += bool(item['lexical'] and item['graph'])
            self.assertAlmostEqual(item['rrf_score'], expected)
        self.assertGreater(both, 0)

    def test_reference_witness_and_confidence_filter(self):
        self.put('hooks.py', 'import atexit\ndef stop_worker():\n    """Release lock."""\n    pass\ndef install_hook():\n    atexit.register(stop_worker)\n')
        self.index()
        result = self.find('release lock')
        hit = next(item for item in result['items'] if item['symbol']['name'] == 'install_hook')
        self.assertEqual(hit['graph']['chain'][0]['relation'], 'reference')
        self.assertEqual(hit['graph']['confidence'], 'medium')
        self.assertNotIn('install_hook', self.names(self.find('release lock', confidence='high')))
        self.put('hooks.py', (self.root / 'hooks.py').read_text().replace('def install_hook():',
                 'def install_hook():\n    """Register callback."""'))
        self.index()
        forward = self.find('register callback')
        worker = next(item for item in forward['items'] if item['symbol']['name'] == 'stop_worker')
        self.assertEqual(worker['graph']['chain'][0]['direction'], 'out')
        self.assertEqual(worker['graph']['chain'][0]['relation'], 'reference')

    def test_diamond_cycle_and_duplicate_calls_keep_distinct_seed_support(self):
        self.put('diamond.py', 'def alpha(): return common_leaf() + common_leaf()\n'
                 'def beta(): return common_leaf()\ndef gamma(): return common_leaf()\n'
                 'def common_leaf(): return alpha()\n')
        self.index()
        result = self.find('alpha beta gamma', depth=3)
        hit = next(item for item in result['items'] if item['symbol']['name'] == 'common_leaf')
        self.assertEqual(len(hit['graph']['supporting_seeds']), 3)
        self.assertEqual(hit['graph']['distance'], 1)
        self.assertEqual(len(hit['graph']['chain']), 1)
        self.assertEqual(len(self.names(result)), len(set(self.names(result))))

    def test_camel_case_unicode_and_literal_operators(self):
        self.put('api.ts', 'function cancelOrderHTTP() { return 1; }\n')
        self.put('café.py', 'def préparer_facture(): return 1\n')
        self.put('street.py', 'def straße(): return 1\n')
        self.index()
        self.assertIn('cancelOrderHTTP', self.names(self.find('cancelOrderHTTP')))
        self.assertIn('préparer_facture', self.names(self.find('préparer facture')))
        self.assertIn('straße', self.names(self.find('straße')))
        self.assertIn('cancelOrderHTTP', self.names(self.find('cancelOrderHTTP\" OR x; DROP TABLE symbols; --')))
        self.assertIn('préparer_facture', self.names(self.find('préparer facture')))

    def test_invalid_input_is_rejected(self):
        self.fixture()
        for query in ('', '---', None, True, 'x' * 513, ' '.join(f'term{i}' for i in range(17))):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.find(query)
        for kwargs in (dict(limit=True), dict(limit=51), dict(depth=0), dict(max_chars=63), dict(mode='semantic')):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.find('cancel', **kwargs)

    def test_budget_and_long_unicode_windows(self):
        self.put('long.py', 'def unicode_needle(): return "' + '🌱漢字' * 1000 + '"\n')
        self.index()
        for budget in (64, 1000, 2500, 4000, 12000):
            result = self.find('unicode needle', max_chars=budget)
            size = len(search['dumps'](result)) + 1
            self.assertLessEqual(size, budget)
            if result.get('items'):
                self.assertEqual(result['budget']['actual_chars'], size)
                source = result['items'][0]['source']
                self.assertTrue(source['truncated'])
                self.assertIn('continuation', source['window'])
                self.assertEqual(len(source['source_sha256']), 64)

    def test_body_prefix_limit_is_explicit(self):
        self.put('long.py', 'def long_body():\n' + '    x = 1\n' * 300 + '    return "tailwordunique"\n')
        self.index()
        self.assertEqual(self.find('tailwordunique')['status'], 'empty')
        self.assertEqual(self.find('long body')['retrieval']['body_prefix_chars'], 2000)

    def test_body_is_confined_to_declaration_byte_range(self):
        self.put('same.js', 'const label = "🌱"; function unrelated() { return 1; } '
                 'function actualMatch() { return "uniqueneedle"; }\n')
        self.index()
        self.assertEqual(self.names(self.find('uniqueneedle', mode='lexical')), ['actualMatch'])

    def test_graph_edge_budget_is_shared_across_seeds_and_depths(self):
        leaves = ''.join(f'def leaf_{i}(): return {i}\n' for i in range(70))
        seeds = ''.join(f'def rareanchor_{i}():\n' + ''.join(f'    leaf_{n}()\n' for n in range(70))
                        for i in range(3))
        self.put('wide.py', seeds + leaves)
        self.index()
        batches = []
        namespace = search['graph_candidates'].__globals__
        original = namespace['adjacent']
        def adjacent(*args, **kwargs):
            rows, cut = original(*args, **kwargs)
            batches.append(len(rows))
            return rows, cut
        with patch.dict(namespace, adjacent=adjacent):
            result = self.find('rareanchor', depth=3)
        self.assertLessEqual(sum(batches), 1000)
        self.assertTrue(result['retrieval']['graph']['truncated'])

    def test_incremental_update_delete_exclude_and_full_agree(self):
        path = self.put('a.py', 'def old_name(): return 1\n')
        self.index()
        path.write_text('def fresh_name(): return 1\n')
        self.index()
        self.assertEqual(self.find('old')['status'], 'empty')
        incremental = self.names(self.find('fresh name'))
        self.index(full=True)
        self.assertEqual(incremental, self.names(self.find('fresh name')))
        self.put('.cgignore', 'a.py\n')
        self.index()
        self.assertEqual(self.find('fresh name')['status'], 'empty')
        self.put('.cgignore', '')
        self.index()
        path.unlink()
        self.index()
        self.assertEqual(self.find('fresh name')['status'], 'empty')

    def test_failed_index_rolls_back_graph_and_search(self):
        path = self.put('a.py', 'def original_name(): return 1\n')
        self.index()
        path.write_text('def replacement_name(): return 1\n')
        self.put('broken.js', 'function broken() {}\n')
        original = cg.Extractor
        def extract(language, source, name, root, catalog):
            if name == 'broken.js':
                raise RuntimeError('synthetic parser failure')
            return original(language, source, name, root, catalog)
        with patch.object(cg, 'Extractor', extract), patch.object(cg, 'list_files', return_value=['a.py', 'broken.js']):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.index()
        path.write_text('def original_name(): return 1\n')
        self.assertIn('original_name', self.names(self.find('original name')))
        self.assertEqual(self.find('replacement')['status'], 'empty')

    def test_missing_fts_keeps_indexing_and_recovery_removes_orphans(self):
        self.put('a.py', 'def original_alpha(): return 1\n')
        self.put('b.py', 'def beta(): return 2\n')
        self.index()
        setup = search['setup']
        class WithoutFTS:
            def __init__(self, db): self.db = db
            def execute(self, sql, *args):
                if 'CREATE VIRTUAL TABLE' in sql:
                    raise sqlite3.OperationalError('no such module: fts5')
                return self.db.execute(sql, *args)
        self.put('a.py', 'def fresh_alpha(): return 1\n')
        with patch.dict(search, setup=lambda db: setup(WithoutFTS(db))), contextlib.redirect_stderr(io.StringIO()):
            self.index()
        with ProjectData(cg, self.root) as project:
            self.assertTrue(cg.lookup(project.db, 'fresh_alpha'))
        with self.assertRaisesRegex(ValueError, 'FTS5'):
            self.find('alpha')
        self.index()
        self.assertEqual(self.find('original')['status'], 'empty')
        self.assertIn('fresh_alpha', self.names(self.find('fresh alpha')))
        with ProjectData(cg, self.root) as project:
            self.assertEqual(project.db.execute('SELECT count(*) FROM search_fts').fetchone()[0], 2)

    def test_fts_write_error_rolls_back_and_releases_the_writer(self):
        path = self.put('a.py', 'def original_name(): return 1\n')
        self.index()
        path.write_text('def fresh_name(): return 1\n')
        def fail(*args):
            raise sqlite3.OperationalError('synthetic FTS failure')
        with patch.dict(search, store=fail):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.index()
        with contextlib.closing(sqlite3.connect(self.root / '.code-graph/graph.db', timeout=0)) as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertEqual(db.execute('SELECT qname FROM symbols WHERE kind="function"').fetchone()[0], 'original_name')
            db.rollback()
        path.write_text('def original_name(): return 1\n')
        self.assertIn('original_name', self.names(self.find('original')))

    def test_stale_lexical_and_graph_evidence_are_rejected(self):
        self.fixture()
        self.put('payments.py', 'def reverse_charge(): return 2\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.find('cancel order')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.find('cancel order', limit=1)
        self.index()
        self.put('orders.py', 'def cancel_order(): return 3\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.find('cancel order', mode='lexical')

    def test_changed_engine_requires_reindex(self):
        self.fixture()
        with patch.object(cg, 'analysis_inputs', return_value=({}, {}, 'new-engine')):
            with self.assertRaisesRegex(ValueError, 'Engine'):
                self.find('cancel order')

    def test_size_and_minified_skips_remove_cached_search_rows(self):
        for name, initial, replacement in (
            ('a.py', 'def indexed_name(): return 1\n', '#' * (cg.MAX_BYTES + 1)),
            ('b.js', 'function indexed_name() { return 1; }\n', 'var data="' + 'x' * 21000 + '";')):
            with self.subTest(name=name):
                path = self.put(name, initial)
                self.index()
                self.assertIn('indexed_name', self.names(self.find('indexed name')))
                path.write_text(replacement)
                self.index()
                self.assertEqual(self.find('indexed name')['status'], 'empty')

    def test_symlink_evidence_is_not_followed(self):
        self.fixture()
        path = self.root / 'orders.py'
        outside = self.base / 'outside.py'
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.find('cancel order')

    def test_lexical_cap_and_ties_are_reported_and_stable(self):
        self.put('many.py', ''.join(f'def needle_{i}(): return {i}\n' for i in range(55)))
        self.index()
        first = self.find('needle', mode='lexical', limit=50, max_chars=16000)
        self.assertTrue(first['retrieval']['lexical_truncated'])
        self.assertEqual(first['retrieval']['lexical_candidates'], 50)
        self.index(full=True)
        self.assertEqual(self.names(first), self.names(self.find('needle', mode='lexical', limit=50, max_chars=16000)))

    def test_graph_cap_is_explicit(self):
        self.put('wide.py', 'def anchor():\n    """Rare trigger."""\n' + ''.join(f'    leaf_{i}()\n' for i in range(550))
                 + ''.join(f'def leaf_{i}(): pass\n' for i in range(550)))
        self.index()
        result = self.find('rare trigger')
        self.assertTrue(result['retrieval']['graph']['truncated'])
        self.assertLessEqual(result['retrieval']['graph']['visited'], 500)


if __name__ == '__main__':
    unittest.main()
