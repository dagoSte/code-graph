"""Authenticated feature preparation lifecycle through real loopback HTTP."""
import json
import unittest
import test_workbench as fixtures


class FeatureAPI(unittest.TestCase):
    def setUp(self):
        self.client=fixtures.WorkbenchTests('runTest');self.client.setUp();self.addCleanup(self.client.doCleanups)
        self.root=self.client.root
        manifest={'format':'codegraph-manifest/1','features':[{'id':'work','title':'Elaborazione','entrypoints':[{'file':'pkg/core.py','symbol':'work'}]}]}
        (self.root/'.code-graph-manifest.json').write_text(json.dumps(manifest))
        self.client.ready()

    def prepare(self,**kwargs):return self.client.request('/api/prepare',dict(generation=self.client.server.session.generation,feature='declared:work',**kwargs))

    def test_feature_apis_are_authenticated_and_graph_is_sandbox_ready(self):
        self.assertEqual(self.client.request('/api/features',auth=False)[0],403)
        self.assertEqual(self.client.request('/api/prepare',{'feature':'declared:work'},auth=False)[0],403)
        code,response=self.client.request(self.client.path('/api/feature',id='declared:work',view='1'))
        self.assertEqual(code,200,response);self.assertIn('"embedded":true',response['data']['graph_view']['html'])
        self.assertTrue(response['data']['graph_view']['node_ids'])

    def test_prepare_bare_id_suggestion_keeps_error_and_preparations_unchanged(self):
        before=len(self.client.server.session.preparations)
        code,response=self.client.request('/api/prepare',dict(generation=self.client.server.session.generation,feature='work'))
        self.assertEqual(code,400,response)
        self.assertIn('did you mean declared:work?',response['error'].lower())
        self.assertIn('did you mean declared:work?',response['error'].lower())
        self.assertEqual(len(self.client.server.session.preparations),before)
        self.assertEqual(self.prepare()[0],200)

    def test_packet_baseline_survives_refresh_and_detects_changed_signature(self):
        code,response=self.prepare();self.assertEqual(code,200,response);packet=response['data'];self.assertIn('preparation_id',packet)
        (self.root/'pkg/core.py').write_text('def work(required):return required\n')
        self.client.ready()
        code,result=self.client.request('/api/feature-review',dict(generation=self.client.server.session.generation,preparation_id=packet['preparation_id']))
        self.assertEqual(code,200,result);self.assertEqual(result['data']['status'],'review_required')
        self.assertTrue(any(item['kind']=='signature_changed' for item in result['data']['changes']))

    def test_foreign_project_cannot_reuse_preparation(self):
        _,response=self.prepare();pid=response['data']['preparation_id']
        self.client.root=self.client.workspace/'other';self.client.root.mkdir();(self.client.root/'app.py').write_text('def beta():return 1\n');self.client.ready()
        code,_=self.client.request('/api/feature-review',dict(generation=self.client.server.session.generation,preparation_id=pid));self.assertEqual(code,400)

    def test_memory_retention_is_bounded_and_old_handles_expire(self):
        _,response=self.prepare();first=response['data']['preparation_id']
        for _ in range(8):self.assertEqual(self.prepare()[0],200)
        self.assertEqual(len(self.client.server.session.preparations),8)
        self.assertEqual(self.client.request('/api/feature-review',dict(generation=self.client.server.session.generation,preparation_id=first))[0],400)

    def test_direct_symbol_selection_and_collaborator_plan_are_authenticated(self):
        code,response=self.client.request(self.client.path('/api/feature',symbol='pkg/core.py:work'))
        self.assertEqual(code,200,response);self.assertEqual(response['data']['provenance'],'code_candidate')
        for selectors in [{},[1],['pkg/core.py:work']*9,['missing.py:unknown']]:
            self.assertEqual(self.prepare(verification_symbols=selectors)[0],400,selectors)
        code,response=self.prepare(verification_symbols=['pkg/site.py:view'])
        self.assertEqual(code,200,response)
        self.assertEqual(len(response['data']['verification_scope']['subjects']),2)

    def test_bad_types_limits_and_stale_generation_are_explicit(self):
        for value in [dict(confidence={}),dict(depth=True),dict(max_chars=[]),dict(intent=[]),dict(feature=[]),dict(max_chars=100001)]:
            body=dict(generation=self.client.server.session.generation,feature='declared:work');body.update(value)
            self.assertEqual(self.client.request('/api/prepare',body)[0],400,value)
        self.assertEqual(self.client.request('/api/feature-review',dict(generation=self.client.server.session.generation,preparation_id={}))[0],400)
        self.assertEqual(self.client.request('/api/prepare',dict(generation='old',feature='declared:work'))[0],409)

    def test_insufficient_budget_does_not_register_an_unusable_preparation(self):
        before=len(self.client.server.session.preparations)
        code,response=self.prepare(max_chars=512);self.assertEqual(code,200,response)
        self.assertEqual(response['data']['status'],'budget_insufficient');self.assertEqual(len(self.client.server.session.preparations),before)


if __name__=='__main__':unittest.main()
