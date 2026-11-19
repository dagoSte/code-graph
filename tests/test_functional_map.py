"""Functional names require declarations; connections require bounded source witnesses."""
import argparse
import contextlib
import hashlib
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


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cg = load('cg_functional_tests', REPO / 'scripts/cg.py')
flows = load('functional_map_tests', REPO / 'scripts/functional_map.py')
ProjectData = runpy.run_path(str(REPO / 'scripts/project_data.py'))['ProjectData']


class FunctionalMapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cg-flows-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.put('shop.py', 'def charge(value):\n    return value\n\ndef checkout():\n    return charge(1)\n')
        self.features = [dict(id='checkout', title='Acquisto', summary='Conclude un ordine', entrypoints=[dict(file='shop.py', symbol='checkout')]),
                         dict(id='payment', title='Pagamento', summary='Incassa il pagamento', entrypoints=[dict(file='shop.py', symbol='charge')])]
        self.manifest()
        self.index()

    def put(self, name, text):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def manifest(self):
        self.put('.code-graph-manifest.json', json.dumps(dict(format='codegraph-manifest/1', features=self.features)))

    def index(self, full=False):
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(self.root), full=full))

    def build(self, **kwargs):
        with ProjectData(cg, self.root) as project:
            return flows.build(project, **kwargs)

    def test_declared_purpose_links_and_source_provenance(self):
        result = self.build()
        self.assertEqual({item['title'] for item in result['nodes']}, {'Acquisto', 'Pagamento'})
        self.assertTrue(all(item['provenance'] == 'project_declaration' for item in result['nodes']))
        manifest_hash = hashlib.sha256((self.root / '.code-graph-manifest.json').read_bytes()).hexdigest()
        self.assertEqual({item['source']['sha256'] for item in result['nodes']}, {manifest_hash})
        self.assertEqual([(link['source'], link['target']) for link in result['links']], [('declared:checkout', 'declared:payment')])
        witness = result['links'][0]['witnesses'][0]
        self.assertEqual((witness['caller']['name'], witness['callee']['name'], witness['file'], witness['line']), ('checkout', 'charge', 'shop.py', 5))
        self.assertEqual(witness['source_hash'], hashlib.sha256((self.root / 'shop.py').read_bytes()).hexdigest())
        self.assertEqual(witness['confidence'], 'high')
        self.assertFalse(result['verification']['execution_performed'])
        self.assertEqual(result['nodes'][0]['continuation']['prepare'], 'declared:checkout')

    def test_query_and_duplicate_titles_do_not_merge_features(self):
        self.features[0]['title'] = self.features[1]['title'] = 'Pagamento'
        self.manifest(); self.index()
        result = self.build(query='pagamento')
        self.assertEqual([item['id'] for item in result['nodes']], ['declared:checkout', 'declared:payment'])
        self.assertEqual(self.build(query='invented business')['nodes'], [])

    def test_candidates_have_technical_names_without_invented_purpose(self):
        self.features = []; self.manifest(); self.index()
        result = self.build()
        self.assertEqual(result['declarations_status'], 'no_declared_features')
        self.assertEqual(result['nodes'], [])
        self.assertEqual({item['name'] for item in result['entrypoint_candidates']}, {'checkout', 'charge'})
        self.assertTrue(all(item['summary'] == '' and 'title' not in item for item in result['entrypoint_candidates']))
        self.assertTrue(all(item['provenance'] == 'code_candidate' for item in result['entrypoint_candidates']))

    def test_registered_candidate_keeps_its_actual_source_witness(self):
        self.put('api.py', 'from fastapi import FastAPI\napp=FastAPI()\n@app.get("/value")\ndef view():return 1\nraise RuntimeError("Never execute analyzed sources")\n')
        self.index()
        candidates = self.build()['entrypoint_candidates']
        route = next(item for item in candidates if item['name'] == 'view')
        self.assertIn(route['evidence']['kind'], ('registration.site', 'registered_handler'))
        self.assertEqual(route['evidence']['file'], 'api.py')
        self.assertEqual(route['evidence']['source_hash'], hashlib.sha256((self.root / 'api.py').read_bytes()).hexdigest())

    def test_shared_reference_never_invents_cross_feature_calls(self):
        self.features[0]['summary'] = 'Incassa il pagamento durante acquisto'
        self.features.append(dict(id='finance', title='Finanza', members=[dict(file='shop.py', symbol='charge')]))
        self.manifest(); self.index()
        result = self.build(query='pagamento')
        self.assertEqual(len(result['nodes']), 2)  # A third, filtered-out owner still prevents exclusive attribution.
        self.assertEqual(result['links'], [])
        result = self.build()
        self.assertEqual(result['links'], [])
        shared = result['shared_references'][0]
        self.assertEqual(shared['features'], ['declared:finance', 'declared:payment'])
        self.assertEqual(shared['symbol']['identity']['name'], 'charge')
        self.assertEqual(shared['symbol']['feature_membership_count'], 2)

    def test_planned_missing_ambiguous_and_contract_states_remain_visible(self):
        self.put('duplicate.py', 'def run(): return 1\ndef run(): return 2\n')
        self.features.append(dict(id='future', title='Rimborso', stage='planned', entrypoints=[dict(file='future.py', symbol='refund'), dict(file='duplicate.py', symbol='run')],
                                  contracts=[dict(id='refund-rule', description='Keep the original receipt', evidence=[dict(file='shop.py', line=1, sha256='0' * 64)])]))
        self.manifest(); self.index()
        node = next(item for item in self.build()['nodes'] if item['id'] == 'declared:future')
        self.assertEqual(node['stage'], 'planned')
        self.assertEqual([ref['status'] for ref in node['references']], ['missing', 'ambiguous'])
        self.assertEqual(len(node['references'][1]['candidates']), 2)
        self.assertEqual(node['contracts'], [dict(id='refund-rule', status='stale_declaration')])
        self.assertEqual(node['source_status'], 'not_resolved')

    def test_candidate_identity_survives_line_moves_and_full_rebuild(self):
        self.features = []; self.manifest(); self.index()
        before = {item['name']: item['id'] for item in self.build()['entrypoint_candidates']}
        self.put('shop.py', '\n\n' + (self.root / 'shop.py').read_text())
        self.index(full=True)
        after = {item['name']: item['id'] for item in self.build()['entrypoint_candidates']}
        self.assertEqual(before, after)

    def test_duplicate_candidate_identities_are_not_merged(self):
        self.features = []; self.manifest()
        self.put('duplicate.py', 'def run(): return 1\ndef run(): return 2\n')
        self.index()
        candidates = [item for item in self.build()['entrypoint_candidates'] if item['name'] == 'run']
        self.assertEqual(len(candidates), 2)
        self.assertEqual(len({item['id'] for item in candidates}), 2)
        self.assertTrue(all(item['identity_status'] == 'ambiguous_or_limited' for item in candidates))

    def test_confidence_filter_controls_direct_links(self):
        with sqlite3.connect(self.root / '.code-graph/graph.db') as db:
            db.execute("UPDATE edges SET conf='low'")
        self.assertEqual(self.build(min_conf='medium')['links'], [])
        self.assertEqual(self.build(min_conf='low')['links'][0]['witnesses'][0]['confidence'], 'low')

    def test_global_row_and_edge_bounds_report_unknown_totals(self):
        self.put('shop.py', 'def charge(value):return value\n' + ''.join(f'def buy_{n}():return charge({n})\n' for n in range(20)))
        self.features[0]['entrypoints'] = [dict(file='shop.py', symbol=f'buy_{n}') for n in range(16)]
        self.manifest(); self.index()
        with patch.object(flows, 'MAX_EDGE_ROWS', 5):
            result = self.build()
        self.assertLessEqual(result['scan']['edge_rows'], 5)
        self.assertIsNone(result['omissions']['links']['total'])
        self.assertIsNone(result['links'][0]['callsites_total'])
        with patch.object(flows, 'MAX_SQL_ROWS', 5):
            limited = self.build()
        self.assertLessEqual(limited['scan']['sql_rows'], 5)
        self.assertIsNone(limited['omissions']['links']['total'])
        self.assertIsNone(limited['omissions']['entrypoint_candidates']['total'])

    def test_witness_and_candidate_cutoffs_expose_omissions(self):
        self.put('shop.py', 'def charge(value):return value\n' + ''.join(f'def buy_{n}():return charge({n})\n' for n in range(6)))
        self.features[0]['entrypoints'] = [dict(file='shop.py', symbol=f'buy_{n}') for n in range(6)]
        self.manifest(); self.index()
        result = self.build()
        link = result['links'][0]
        self.assertEqual(link['callsites_total'], 6)
        self.assertEqual(link['witnesses_output'], dict(total=6, shown=3, omitted=3, reasons=['limit']))
        self.assertEqual(result['status'], 'partial')
        with patch.object(flows, 'MAX_CANDIDATE_ROWS', 3):
            limited = self.build()
        self.assertLessEqual(limited['scan']['candidate_rows'], 3)
        self.assertIsNone(limited['omissions']['entrypoint_candidates']['total'])

    def test_sql_budget_covers_actual_rows_across_all_queries(self):
        returned = []

        class Cursor:
            def __init__(self, cursor):
                self.cursor = cursor

            def fetchall(self):
                values = self.cursor.fetchall()
                returned.extend(values)
                return values

        class Database:
            def __init__(self, db):
                self.db = db

            def execute(self, sql, args=()):
                self_test.assertIn('LIMIT', sql)
                return Cursor(self.db.execute(sql, args))

        self_test = self
        with ProjectData(cg, self.root) as project, patch.object(flows, 'MAX_SQL_ROWS', 5):
            connection = project.db
            project.db = Database(connection)
            try:
                result = flows.build(project)
            finally:
                project.db = connection
        self.assertLessEqual(len(returned), 5)
        self.assertEqual(len(returned), result['scan']['sql_rows'])

    def test_canonical_unicode_budget_and_no_dangling_links(self):
        self.features.extend(dict(id=f'f{n}', title='Funzionalità più lunga ' + str(n), summary='à' * 100,
                                  entrypoints=[dict(file='shop.py', symbol='checkout')]) for n in range(30))
        self.manifest(); self.index()
        for maximum in (512, 1800, 3000, 8000, 16000):
            with self.subTest(maximum=maximum):
                value = self.build(limit=100, max_chars=maximum)
                actual = len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))) + 1
                self.assertLessEqual(actual, maximum)
                if value['status'] == 'budget_insufficient':
                    continue
                self.assertEqual(value['budget']['actual_chars'], actual)
                self.assertTrue(value['nodes'])
                ids = {item['id'] for item in value['nodes']}
                self.assertTrue(all(link['source'] in ids and link['target'] in ids for link in value['links']))
                self.assertTrue(all(set(item['features']) <= ids for item in value['shared_references']))

    def test_stale_sources_and_engine_refused_without_database_writes(self):
        database = self.root / '.code-graph/graph.db'
        before = database.read_bytes()
        self.build()
        self.assertEqual(database.read_bytes(), before)
        self.put('shop.py', '\n' + (self.root / 'shop.py').read_text())
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.build()
        self.assertEqual(database.read_bytes(), before)
        self.index()
        inputs = cg.analysis_inputs(self.root)
        with patch.object(cg, 'analysis_inputs', return_value=(*inputs[:2], 'changed')):
            with self.assertRaisesRegex(ValueError, 'Engine changed'):
                self.build()

    def test_overview_does_not_request_feature_detail_or_test_plan(self):
        with patch.dict(flows.FEATURES, detail=lambda *a, **kw: self.fail('Unexpected detail traversal')):
            with patch.dict(flows.FEATURES['CHANGE'], plan=lambda *a, **kw: self.fail('Unexpected verification traversal')):
                self.assertEqual(len(self.build()['nodes']), 2)

    def test_invalid_inputs_fail_at_boundary(self):
        for kwargs in (dict(query=[]), dict(query='x' * 201), dict(limit=True), dict(limit=101),
                       dict(min_conf=[]), dict(min_conf='certain'), dict(max_chars=511), dict(max_chars=16001)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.build(**kwargs)


if __name__ == '__main__':
    unittest.main()
