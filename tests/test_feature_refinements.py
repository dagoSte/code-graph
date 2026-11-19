"""Real failure shapes from 1.5: long lines, crowded discovery, sparse packets and collaborator tests."""
import hashlib
import json
import unittest
import runpy
import test_features as base

W=runpy.run_path(str(base.REPO/'scripts/source_windows.py'))


class Refinements(unittest.TestCase):
    def setUp(self):
        self.fixture=base.FeatureTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
    def test_required_call_on_17kb_line_fits_default_budget_with_truthful_columns(self):
        t=self.fixture
        line='function leaf(value){return value;} function unused(){return leaf(0);} const pad="'+'x'*17000+'"; function caller(){return leaf(1);} caller();'
        t.put('compiled.js',line+'\n');t.index()
        with t.data() as p:
            sid=p.db.execute('SELECT id FROM symbols WHERE name="caller"').fetchone()[0]
            packet,_=base.features['prepare'](p,'symbol:'+str(sid),max_chars=12000)
            self.assertNotEqual(packet['status'],'budget_insufficient')
            self.assertTrue(packet['calls']);self.assertLessEqual(packet['budget']['actual_chars'],12000)
            snippets=[snippet for snippet in packet['snippets'].values() if snippet.get('window')]
            self.assertTrue(snippets)
            call_windows=[snippet for snippet in snippets if any('leaf(1)' in row['text'] for row in snippet['lines'])]
            self.assertTrue(call_windows)
            outgoing=next(edge for edge in packet['calls'] if edge['source']==sid)
            call_snippet=packet['snippets'][outgoing['calls'][0]['snippet_ref']]
            self.assertTrue(any('leaf(1)' in row['text'] for row in call_snippet['lines']))
            for snippet in snippets:
                self.assertTrue(snippet['truncated'])
                for row in snippet['lines']:
                    self.assertLessEqual(len(row['text']),320)
                    self.assertEqual(row['text'],line[row['start_column']:row['end_column']])
                    self.assertEqual(row['original_text_sha256'],hashlib.sha256(line.encode()).hexdigest())

    def test_empty_indexed_module_can_be_prepared_without_inventing_source(self):
        t=self.fixture;t.put('future.py','')
        t.manifest['features']=[dict(id='future',title='Future',stage='planned',entrypoints=[dict(file='future.py')])];t.write_manifest();t.index()
        with t.data() as p:
            packet,_=base.features['prepare'](p,'declared:future')
            self.assertNotEqual(packet['status'],'budget_insufficient')
            self.assertTrue(all(not snippet['lines'] for snippet in packet['snippets'].values()))

    def test_unicode_columns_and_real_expansion_keep_the_same_source_revision(self):
        t=self.fixture;line='😀漢字'*100+' target() '+'z'*900;t.put('unicode.py',repr(line)+'\n');t.index()
        with t.data() as p:
            a=W['read_window'](p,'unicode.py',1,200,128)
            self.assertEqual(a['lines'][0]['text'],p._read('unicode.py')[0][200:328])
            continuation=a['window']['continuation']
            b=W['read_window'](p,'unicode.py',continuation['line'],continuation['column'],128)
            self.assertEqual(a['source_revision'],b['source_revision']);self.assertEqual(b['lines'][0]['start_column'],328)
            with self.assertRaises(ValueError):W['read_window'](p,'../outside.py')

    def test_tests_cannot_fill_the_catalog_before_backend_entrypoints_are_considered(self):
        t=self.fixture
        for i in range(30):
            t.put('tests/test_busy'+str(i)+'.py','\n'.join('def busy_%d_%d():return 1'%(i,j) for j in range(30))+'\n'+'\n'.join('busy_%d_%d()'%(i,j) for j in range(30)))
        t.put('backend.py','def serve():return 1\nif __name__ == "__main__":\n    serve()\n')
        t.put('compiled-js/bundle.js','function front(){return 1;} front();\n');t.index()
        with t.data() as p:
            candidates=[item for item in p.feature_catalog()['items'] if item['provenance']=='code_candidate']
            self.assertTrue(any(item['title']=='serve' and item['entrypoint_evidence'][0]['kind']=='python_main_guard' for item in candidates))
            self.assertFalse(any(item['location']['file'].startswith('tests/') for item in candidates))
            self.assertEqual(candidates[0]['title'],'serve')

    def test_definition_fallback_declares_candidates_beyond_the_display_limit(self):
        t=self.fixture;t.manifest['features']=[];t.write_manifest()
        t.put('app.py','\n'.join('def candidate_%02d():return %d'%(i,i) for i in range(40))+'\n');t.index()
        with t.data() as p:
            catalog=p.feature_catalog()
            self.assertEqual(len(catalog['items']),24)
            self.assertEqual(catalog['candidate_total'],40)
            self.assertTrue(catalog['candidates_truncated'])

    def test_search_reaches_a_candidate_beyond_the_unfiltered_catalog_limit(self):
        t=self.fixture;t.manifest['features']=[];t.write_manifest()
        t.put('app.py','\n'.join('def candidate_%02d():return %d'%(i,i) for i in range(40))+'\n');t.index()
        with t.data() as p:
            result=p.feature_catalog('candidate_39')
            self.assertEqual([item['title'] for item in result['items']],['candidate_39'])
            self.assertEqual(result['candidate_total'],1)
            self.assertFalse(result['candidates_truncated'])

    def test_registered_route_and_configured_cli_keep_distinct_evidence(self):
        t=self.fixture
        t.put('api.py','from fastapi import FastAPI\napp=FastAPI()\n@app.get("/value")\ndef view():return 1\n')
        t.put('cli.py','def start():return 1\n');t.put('pyproject.toml','[project.scripts]\ndemo="cli:start"\n');t.index()
        with t.data() as p:
            items=p.feature_catalog()['items'];route=next(item for item in items if item['title']=='view');cli=next(item for item in items if item['title']=='start')
            self.assertTrue(any(ref['kind']=='registration.site' for ref in route['entrypoint_evidence']))
            self.assertTrue(any(ref['kind']=='project_script:demo' for ref in cli['entrypoint_evidence']))

    def test_dense_packet_retains_a_connected_flow_instead_of_isolated_nodes(self):
        t=self.fixture
        t.put('app.py','\n'.join('def helper_%02d():return %d'%(i,i) for i in range(55))+'\ndef publish(value):return '+'+'.join('helper_%02d()'%i for i in range(55))+'\n')
        t.index()
        with t.data() as p:
            packet,_=base.features['prepare'](p,'declared:publish',max_chars=12000)
            self.assertNotEqual(packet['status'],'budget_insufficient')
            self.assertGreaterEqual(len(packet['calls']),10)
            nodes={node['id'] for node in packet['participants']};seen={node['id'] for node in packet['participants'] if node['root']}
            for _ in nodes:
                for edge in packet['calls']:
                    self.assertTrue({edge['source'],edge['target']}<=nodes)
                    if seen.intersection((edge['source'],edge['target'])):seen.update((edge['source'],edge['target']))
            self.assertEqual(seen,nodes);self.assertTrue(packet['tests']);self.assertLessEqual(packet['budget']['actual_chars'],12000)

    def test_selected_collaborator_tests_are_unioned_deduplicated_and_versioned(self):
        t=self.fixture;t.put('tests/test_save.py','from app import save\ndef test_save():return save(1)\n');t.index()
        with t.data() as p:
            plain,_=base.features['prepare'](p,'declared:publish',max_chars=20000)
            self.assertNotIn('test_save',[test['symbol']['name'] for test in plain['tests']])
            packet,before=base.features['prepare'](p,'declared:publish',max_chars=30000,verification_symbols=['app.py:save'])
            names=[test['symbol']['name'] for test in packet['tests']]
            self.assertEqual(sorted(names),['test_publish','test_save']);self.assertEqual(len(names),len(set(names)))
            self.assertTrue(all(test['verified_for'] for test in packet['tests']))
            self.assertEqual(before['verification_references'],[dict(file='app.py',symbol='save')])
        t.put('app.py','\ndef save(value):return value+1\ndef publish(value):return save(value)\ndef run():return publish(1)\n');t.index()
        with t.data() as p:
            review=base.features['compare'](p,before)
            self.assertIn('test_save',[test['symbol']['name'] for test in review['verification']['tests']])
