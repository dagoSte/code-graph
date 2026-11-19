"""Offline study contracts; no model clients, external repositories or network."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('study_integrity_test', REPO / 'benchmarks/study_integrity.py')
integrity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integrity)
native = integrity.pilot.shared.load(REPO / 'benchmarks/study_native.py', 'study_native_test')
study = integrity.pilot.shared.load(REPO / 'benchmarks/study_pilot.py', 'study_pilot_test')
analysis = integrity.pilot.shared.load(REPO / 'benchmarks/study_analysis.py', 'study_analysis_test')
localization = integrity.pilot.shared.load(REPO / 'benchmarks/study_localization.py', 'study_localization_test')
rescore = integrity.pilot.shared.load(REPO / 'benchmarks/study_rescore.py', 'study_rescore_test')


class Study(unittest.TestCase):
    def test_offline_literal_insertions_preserve_old_cases_in_three_languages(self):
        examples=[
            ('python','def test_x():\n cases=[(1,2), {"x":[3]}]\n for case in cases:\n  assert case\n',
                      'def test_x():\n cases=[(1,2), (4,5), {"x":[3,4],"y":6}]\n for case in cases:\n  assert case\n'),
            ('typescript',"ruleTester.run('r',rule,{valid:['a',{code:'b',options:[1]}],invalid:[]});",
                          "ruleTester.run('r',rule,{valid:['a','c',{code:'b',options:[1,2]}],invalid:[]});"),
            ('go','package p\nfunc TestX(t *T){cases:=[]struct{x int}{{1},{2}};for _,c:=range cases{check(c.x)}}',
                  'package p\nfunc TestX(t *T){cases:=[]struct{x int}{{1},{3},{2}};for _,c:=range cases{check(c.x)}}')]
        for language,before,after in examples:
            with self.subTest(language=language):
                self.assertTrue(rescore.preserved(rescore.headers(before,language),rescore.headers(after,language)))
                self.assertFalse(rescore.preserved(rescore.headers(after,language),rescore.headers(before,language)))
        before,after=examples[1][1:]
        self.assertTrue(integrity.counts(before,'typescript')-integrity.counts(after,'typescript'))

    def test_offline_guard_rejects_changed_cases_duplicates_assertions_and_headers(self):
        examples=[
            ('python','def test_x():\n cases=[1,1,2]\n for x in cases:\n  assert x\n',
             ['cases=[1,2]','cases=[1,1,3]','cases=[2,1,1]']),
            ('typescript',"ruleTester.run('r',rule,{valid:['a','a','b'],invalid:[]});",
             ["valid:['a','b']","valid:['a','a','c']","valid:['b','a','a']"]),
            ('go','package p\nfunc TestX(t *T){cases:=[]int{1,1,2};for _,x:=range cases{check(x)}}',
             ['cases:=[]int{1,2}','cases:=[]int{1,1,3}','cases:=[]int{2,1,1}'])]
        for language,before,replacements in examples:
            old=('cases=[1,1,2]' if language=='python' else "valid:['a','a','b']" if language=='typescript' else 'cases:=[]int{1,1,2}')
            for value in replacements:
                with self.subTest(language=language,value=value):
                    self.assertFalse(rescore.preserved(rescore.headers(before,language),rescore.headers(before.replace(old,value),language)))
        for before,after in [('def test_x():\n assert 1\n','def test_x():\n pass\n'),
                             ('def test_x():\n if ok:\n  assert 1\n','def test_x():\n if other:\n  assert 1\n'),
                             ('def test_x():\n if ok:\n  assert 1\n else:\n  assert 2\n','def test_x():\n if ok:\n  assert 1\n')]:
            self.assertFalse(rescore.preserved(rescore.headers(before,'python'),rescore.headers(after,'python')))

    def test_offline_guard_matches_supersets_once_and_leaves_parse_errors_undecided(self):
        from collections import Counter
        def header(values):return ('statement',((rescore.COLLECTION,'array',(),tuple(('int',str(v)) for v in values)),))
        self.assertTrue(rescore.preserved(Counter([header([1]),header([2])]),Counter([header([1,2]),header([1,3])])))
        self.assertFalse(rescore.preserved(Counter([header([1]),header([1])]),Counter([header([1,2])])))
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)/'base';candidate=Path(folder)/'candidate';(base/'tests').mkdir(parents=True);(candidate/'tests').mkdir(parents=True)
            (base/'tests/test_x.py').write_text('def test_x():\n assert 1\n');(candidate/'tests/test_x.py').write_text('def test_x(:')
            self.assertIsNone(rescore.retained(candidate,base,['tests/test_x.py'],'python')['tests_preserved'])
            self.assertEqual(rescore.retained(candidate,base,['tests/test_x.py'],'python')['undecided_test_files'],['tests/test_x.py'])

    def test_zero_context_mechanical_patch_must_match_expected_historical_bytes(self):
        import difflib
        import hashlib
        before='a = 1\nb = 2\nc = 3\n\ndef work(value):\n    return value\n\nclass Other:\n    pass\n'
        after=before.replace('b = 2\n','b = 2\nimport math\n').replace('def work(value):','@decorate\ndef work(value):')
        patch_text=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/leaf.py',tofile='b/leaf.py',n=0))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);patch_file=root/'fixture.patch';patch_file.write_text(patch_text)
            patch_hash=hashlib.sha256(patch_file.read_bytes()).hexdigest()
            expected={'leaf.py':hashlib.sha256(after.encode()).hexdigest()}
            (root/'leaf.py').write_text(before)
            result=study.apply_historical_fixture(root,patch_file,patch_hash,expected)
            self.assertTrue(result['historical_fix_exact'])
            self.assertEqual((root/'leaf.py').read_text(),after)
            (root/'leaf.py').write_text(before)
            original=study.shared.git
            def plain(root,*args):return original(root,*[arg for arg in args if arg!='--unidiff-zero'])
            with patch.object(study.shared,'git',side_effect=plain),self.assertRaises(ValueError):
                study.apply_historical_fixture(root,patch_file,patch_hash,expected)
            (root/'leaf.py').write_text(before)
            with self.assertRaises(ValueError):study.apply_historical_fixture(root,patch_file,'0'*64,expected)
            self.assertEqual((root/'leaf.py').read_text(),before)

    def test_live_order_caps_unknown_cost_and_complete_path_inventory_without_clients(self):
        tasks=[dict(id=str(i),repository=('django','typescript-eslint','prometheus')[i%3],
                    language=('python','typescript','go')[i%3]) for i in range(20)]
        def fake(skill,cache,directory,task,arm,counter,environment,**kwargs):
            counter[0]+=3;counter[1]+=1
            return dict(task=task['id'],arm=arm,outcome='success',cleanup_drained=True,
                        calls=[dict(client='claude',phase='review',cli_reported_cost_usd=None)])
        with tempfile.TemporaryDirectory() as folder,patch.object(study.shared,'client_metadata',return_value={}),patch.object(study.pilot,'run_case',side_effect=fake) as start:
            result=dict(cases=[])
            study.live_paths(tasks,REPO,Path('/cache'),Path('/env'),Path(folder)/'work',result,lambda:None)
        self.assertEqual((start.call_count,result['live_starts'],result['review_starts']),(40,120,40))
        self.assertEqual(result['claude_accounted_with_unknown_caps_usd'],24)
        self.assertEqual(result['claude_reported_cost_usd'],0)
        self.assertEqual(result['unknown_cost_reviews'],40)
        self.assertEqual([r['arm'] for r in result['cases'][:4]],['source_only','graph_assisted','graph_assisted','source_only'])
        self.assertTrue(result['analysis']['complete'])

    def test_budget_or_uncertain_cleanup_stops_without_losing_unstarted_paths(self):
        tasks=[dict(id=str(i),repository=('django','typescript-eslint','prometheus')[i%3],
                    language=('python','typescript','go')[i%3]) for i in range(20)]
        for cost,drained,reason in [(30,True,'claude_total_budget'),(.1,False,'access_cleanup_or_command')]:
            def fake(skill,cache,directory,task,arm,counter,environment,**kwargs):
                counter[0]+=3;counter[1]+=1
                return dict(task=task['id'],arm=arm,outcome='failed',cleanup_drained=drained,
                            calls=[dict(client='claude',phase='review',cli_reported_cost_usd=cost)])
            with self.subTest(reason=reason),tempfile.TemporaryDirectory() as folder,patch.object(study.shared,'client_metadata',return_value={}),patch.object(study.pilot,'run_case',side_effect=fake) as start:
                result=dict(cases=[])
                study.live_paths(tasks,REPO,Path('/cache'),Path('/env'),Path(folder)/'work',result,lambda:None)
            self.assertEqual(start.call_count,1)
            self.assertEqual(len(result['cases']),40)
            self.assertEqual(result['stop_reason'],reason)
            self.assertTrue(all(r['outcome']=='not_started_after_stop_rule' for r in result['cases'][1:]))
            self.assertIsNone(result['analysis']['confirmatory'])

    def test_exception_accounts_only_reviews_started_in_failed_arm_as_unknown(self):
        tasks=[dict(id=str(i),repository=('django','typescript-eslint','prometheus')[i%3],
                    language=('python','typescript','go')[i%3]) for i in range(20)]
        for reviews in (0,1,2):
            attempted=[]
            def fake(skill,cache,directory,task,arm,counter,environment,**kwargs):
                attempted.append(arm)
                if len(attempted)==1:
                    counter[0]+=3;counter[1]+=1
                    return dict(task=task['id'],arm=arm,outcome='success',cleanup_drained=True,
                                calls=[dict(client='claude',phase='review',cli_reported_cost_usd=.2)])
                counter[0]+=2+reviews;counter[1]+=reviews
                raise RuntimeError('Receipt unavailable after starts')
            with self.subTest(reviews=reviews),tempfile.TemporaryDirectory() as folder,patch.object(study.shared,'client_metadata',return_value={}),patch.object(study.pilot,'run_case',side_effect=fake) as start:
                result=dict(cases=[])
                study.live_paths(tasks,REPO,Path('/cache'),Path('/env'),Path(folder)/'work',result,lambda:None)
            self.assertEqual(start.call_count,2)
            self.assertEqual(result['review_starts'],1+reviews)
            self.assertEqual(result['live_starts'],5+reviews)
            self.assertEqual(result['unknown_cost_reviews'],reviews)
            self.assertEqual(result['claude_accounted_with_unknown_caps_usd'],round(.2+reviews*.6,6))
            self.assertEqual(result['claude_reported_cost_usd'],.2)
            self.assertEqual(result['stop_reason'],'access_cleanup_or_command')
            self.assertFalse(result['cases'][1]['calls_receipt_complete'])
            self.assertEqual(result['cases'][1]['calls'],[])
            self.assertTrue(all(row['outcome']=='not_started_after_stop_rule' for row in result['cases'][2:]))
            self.assertIsNone(result['analysis']['confirmatory'])

    def test_runner_start_gate_counts_attempts_and_blocks_late_or_excess_starts(self):
        class Run:
            def _argv(self,folder):return []
        calls=[]
        def start(client,**kwargs):
            calls.append(client)
            return SimpleNamespace(status='done',captured=kwargs)
        budget=study.CallBudget(1);budget[0]=5
        runner=study.StudyRunner(SimpleNamespace(AgentRun=Run,start=start),SimpleNamespace(_RETRIEVAL_GUIDANCE='GUIDANCE'),
                                 REPO,dict(language='go',source_paths=['leaf.go']),'source_only',budget,test_argv=[])
        try:
            runner.phase='plan'
            run=runner.start('codex',prompt='GUIDANCE\n\nEdit phase: read the approved code.\n\nIntent',timeout=900)
            self.assertEqual(run.captured['timeout'],600)
            self.assertIn('Edit phase:',run.captured['prompt'])
            self.assertNotIn('GUIDANCE',run.captured['prompt'])
            self.assertIn('Go file inventory',run.captured['prompt'])
            with self.assertRaises(RuntimeError):runner.start('codex',prompt='intent',timeout=900)
            budget[0]=0;budget[1]=48
            with self.assertRaises(RuntimeError):runner.start('claude',prompt='intent',timeout=900)
            runner.stop_starts()
            with self.assertRaises(RuntimeError):runner.start('codex',prompt='intent',timeout=900)
            self.assertEqual(calls,['codex'])
        finally:runner.close()

    def test_host_timeout_adapter_changes_only_engine_analysis_and_keeps_cancellation(self):
        calls=[]
        def run(argv,*args,**kwargs):calls.append((argv,kwargs));return 'receipt'
        flow=SimpleNamespace(_TESTS=dict(run_tests=run));engine=SimpleNamespace(__file__=str(REPO/'scripts/cg.py'))
        hooks=study.hooks(dict(language='go'));hooks['configure_flow'](flow,engine)
        cancelled=lambda:True
        flow._TESTS['run_tests']([study.sys.executable,'-I',engine.__file__,'--root','/fixture','check'],
                                  '/fixture',timeout=120,cancelled=cancelled)
        self.assertEqual(calls[-1][1]['timeout'],600)
        self.assertIs(calls[-1][1]['cancelled'],cancelled)
        flow._TESTS['run_tests'](['native-test'],'/fixture',timeout=120)
        self.assertEqual(calls[-1][1]['timeout'],120)
        self.assertEqual(hooks['step_seconds'],1860)

    def test_both_arms_receive_full_inventory_only_in_plan_and_keep_phase_notes(self):
        class Run:
            def _argv(self,folder):return []
        for language,paths,label in [('typescript',['pkg/existing.ts'],'TypeScript'),
                                     ('go',['leaf.go','parser.y'],'Go and tracked .y grammar')]:
            for arm in ('source_only','graph_assisted'):
                agents=SimpleNamespace(AgentRun=Run,start=lambda client,**kw:SimpleNamespace(status='done',captured=kw))
                runner=study.StudyRunner(agents,SimpleNamespace(_RETRIEVAL_GUIDANCE='GUIDANCE'),REPO,
                    dict(language=language,source_paths=paths),arm,
                    study.CallBudget(2),test_argv=[])
                try:
                    for phase in ('plan','edit','review'):
                        runner.phase=phase
                        prompt=runner.start('codex',prompt='GUIDANCE\n\n'+phase+' phase: frozen plan',timeout=600).captured['prompt']
                        for name in paths:self.assertEqual(name in prompt,phase=='plan')
                        self.assertIn('Only existing '+label+' files from this inventory may change.',prompt)
                        self.assertIn(phase+' phase:',prompt)
                        self.assertIn('text_search',prompt)
                finally:runner.close()

    def test_go_grammar_inventory_keeps_localization_targets_and_fixture_hashes_separate(self):
        data=REPO/'benchmarks/study-cases'
        _,tasks=study.load_cases(data/'cases.json')
        fixtures=json.loads((data/'mechanical-fixture-hashes.json').read_text())
        self.assertEqual(fixtures['cases_sha256'],study.recorded_case_input_digest(data/'cases.json'))
        go=[task for task in tasks if task['language']=='go']
        self.assertEqual(len(go),7)
        for task in go:
            with self.subTest(task=task['id']):
                grammars={name for name in task['source_paths'] if name.endswith('.y')}
                self.assertEqual(grammars,{'promql/parser/generated_parser.y'})
                self.assertTrue(set(task['source_paths'])<=set(task['tree_paths']))
                self.assertTrue(all(name.endswith(('.go','.y')) for name in task['source_paths']))
                self.assertTrue(all(name.endswith('.go') for name in task['production_files']))
                self.assertTrue(all(target['file'].endswith('.go') for target in task['fix_targets']))
                auxiliary=set(task.get('auxiliary_fix_files',[]))
                self.assertTrue(auxiliary<=grammars)
                self.assertEqual(set(fixtures['production_files_sha256'][task['id']]),set(task['production_files'])|auxiliary)
                production_patch=data/task['production_diff']
                self.assertEqual(study.pilot.digest(production_patch),task['production_diff_sha256'])
                for name in auxiliary:self.assertIn('+++ b/'+name+'\n',production_patch.read_text())
        self.assertEqual(next(task for task in go if task['pr']==19403)['auxiliary_fix_files'],
                         ['promql/parser/generated_parser.y'])

    def test_original_issue_with_json_is_not_parsed_as_host_workflow_payload(self):
        class Run:
            def _argv(self,folder):return []
        issue='Intent:\nA JSON API rejects this value:\n{\n  "graph": "original public issue"\n}\nThen it raises an error.'
        for arm in ('source_only','graph_assisted'):
            agents=SimpleNamespace(AgentRun=Run,start=lambda client,**kw:SimpleNamespace(status='done',captured=kw))
            runner=study.StudyRunner(agents,SimpleNamespace(_RETRIEVAL_GUIDANCE='GUIDANCE'),REPO,
                dict(language='typescript',source_paths=['x.ts']),arm,study.CallBudget(1),test_argv=[])
            try:
                runner.phase='plan'
                prompt=runner.start('codex',prompt='GUIDANCE\n\n'+issue,timeout=600).captured['prompt']
                self.assertIn(issue,prompt)
            finally:runner.close()

    def test_paired_analysis_exact_test_interval_and_missingness(self):
        tasks=[dict(id=str(i),repository=('django','typescript-eslint','prometheus')[i%3]) for i in range(8)]
        rows=[dict(task=t['id'],arm=arm,outcome='success' if arm=='graph_assisted' else 'verify_failed',cleanup_drained=True,calls=[])
              for t in tasks for arm in ('source_only','graph_assisted')]
        result=analysis.analyze(tasks,rows)
        self.assertEqual(result['paired_categories'],dict(both=0,source_only=0,graph_only=8,neither=0,missing=0))
        self.assertEqual(result['confirmatory']['exact_mcnemar_p'],.0078125)
        self.assertEqual(result['confirmatory']['success_difference_95_interval'],[1,1])
        missing=analysis.analyze(tasks,rows[:-1])
        self.assertFalse(missing['complete']);self.assertIsNone(missing['confirmatory'])
        self.assertIsNone(missing['primary_success_difference'])
        self.assertEqual(missing['paired_categories']['missing'],1)
        with self.assertRaises(ValueError):analysis.analyze(tasks,rows+[rows[0]])

    def test_stratified_interval_resamples_bug_pairs_in_each_repository(self):
        tasks=[dict(id=f'{repo}-{n}',repository=repo) for repo in analysis.REPOSITORIES for n in (0,1)]
        rows=[dict(task=t['id'],arm=arm,outcome='success' if arm=='graph_assisted' and t['id'].endswith('1') else 'failed',
                   cleanup_drained=True,calls=[]) for t in tasks for arm in analysis.ARMS]
        result=analysis.analyze(tasks,rows)
        self.assertEqual(result['primary_success_difference'],.5)
        self.assertEqual(result['confirmatory']['exact_mcnemar_p'],.25)
        self.assertEqual(result['confirmatory']['success_difference_95_interval'],[1/6,5/6])

    def test_native_input_secondary_keeps_total_separate_from_pair_ratio_and_cache(self):
        tasks=[dict(id='a',repository='django'),dict(id='b',repository='prometheus')]
        rows=[]
        for task,source,graph in [('a',10,20),('b',100,50)]:
            for arm,count in [('source_only',source),('graph_assisted',graph)]:
                rows.append(dict(task=task,arm=arm,outcome='failed',cleanup_drained=True,calls=[dict(client='codex',phase='edit',
                    native_usage=[dict(input_tokens=count,cached_input_tokens=count,cache_write_input_tokens=count)])]))
        result=analysis.analyze(tasks,rows)['secondary'][1]
        self.assertEqual(result['totals_of_observed'],dict(source_only=110,graph_assisted=70))
        self.assertEqual(result['paired_median_ratio'],1.25)
        self.assertEqual(result['aggregate_ratio_of_observed'],70/110)

    def test_localization_counts_files_and_exact_callable_spans_without_token_claims(self):
        task=dict(production_files=['a.py','b.py'],fix_targets=[dict(file='a.py',declarations=[dict(line=10,end_line=20)])])
        payload=dict(items=[dict(file='wrong.py',line=1,end_line=2),dict(file='a.py',line=10,end_line=20),
                            dict(file='a.py',line=40,end_line=50),dict(file='b.py',line=1,end_line=4)],
                     reading=dict(returned_chars=500,file_chars=2000),status='ready')
        result=localization.score(task,payload)
        self.assertFalse(result['files_at']['1']['any_hit'])
        self.assertEqual(result['files_at']['3'],dict(any_hit=True,all_hit=False,matched=1,targets=2))
        self.assertTrue(result['files_at']['5']['all_hit'])
        self.assertEqual(result['callable_at_5'],dict(any_hit=True,matched=1,targets=1))
        self.assertEqual(result['reading'],payload['reading'])
        self.assertEqual(localization.old_ranges('@@ -10,0 +11,2 @@\n@@ -20 +22 @@'),[(10,0),(20,1)])

    def test_historical_hunks_map_parent_callables_in_three_languages(self):
        engine=study.shared.load(REPO/'scripts/cg.py','study_target_engine')
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for language,name,source,line in [('python','x.py','def leaf():\n    return 1\n',2),
                    ('typescript','x.ts','function leaf() {\n  return 1;\n}\n',2),
                    ('go','x.go','package p\nfunc leaf() int {\n return 1\n}\n',3)]:
                with self.subTest(language=language):
                    (root/name).write_text(source)
                    result=localization.targets(engine,root,language,name,f'@@ -{line} +{line} @@\n-return 1\n+return 2\n')
                    self.assertEqual([d['qname'] for d in result['declarations']],['leaf'])

    def test_localization_cli_failure_is_retained_and_counts_as_no_hit(self):
        task=dict(id='case',repository='django',pr=1,issue_title='long issue',issue_body='body',production_files=['a.py'],
                  fix_targets=[dict(file='a.py',declarations=[])])
        executor=SimpleNamespace(run_tests=lambda *args,**kwargs:dict(status='timeout',exit=None,stdout_tail='',cleanup_error=False))
        result=localization.run(executor,REPO,Path('/prepared'),task)
        self.assertEqual(result['error_code'],'locate_failed_or_invalid_receipt')
        self.assertFalse(result['files_at']['5']['any_hit'])
        self.assertIsNone(result['callable_at_5']['any_hit'])

    def test_case_bundle_hashes_paths_and_nonempty_native_ids(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'hidden.patch').write_text('historical test patch')
            task=dict(id='test',hidden_patch='hidden.patch',hidden_patch_sha256=study.pilot.digest(root/'hidden.patch'),
                      issue_body='original intent',issue_body_sha256=hashlib.sha256(b'original intent').hexdigest(),
                      source_paths=['a.py'],tree_paths=['a.py'],test_files=['tests/test_a.py'],test_modules=['tests/test_a.py'],
                      production_files=['a.py'],hidden_ids=['hidden'],smoke_ids=['smoke'])
            path=root/'cases.json';path.write_text(json.dumps(dict(cases=[task])))
            self.assertEqual(study.load_cases(path)[1][0]['issue_body'],'original intent')
            for field,value in [('source_paths',['../outside.py']),('hidden_ids',[]),('hidden_patch_sha256','0'*64)]:
                with self.subTest(field=field):
                    path.write_text(json.dumps(dict(cases=[dict(task,**{field:value})])))
                    with self.assertRaises(ValueError):study.load_cases(path)

    def test_public_guard_accepts_exact_tracked_relative_home_path_but_no_private_material(self):
        path='packages/website/src/components/home/index.tsx'
        tasks=[dict(tree_paths=[path])]
        study.validate_public(dict(explicit_source_files_read=[path]),tasks)
        for value in (dict(path='/Users/owner/private'),dict(path='unknown/home/file.tsx'),dict(argv=[path]),dict(session_id='secret')):
            with self.subTest(value=value),self.assertRaises(ValueError):study.validate_public(value,tasks)

    def test_nested_assertion_additions_in_three_languages(self):
        pairs = {
            'python': ('def test_loop():\n for x in [1,2]:\n  assert x > 0\n',
                       'def test_loop():\n for x in [1,2]:\n  assert x > 0\n  assert x < 3\n'),
            'typescript': ('test("loop", () => {for (const x of [1,2]) {expect(x).toBeGreaterThan(0);}});',
                           'test("loop", () => {for (const x of [1,2]) {expect(x).toBeGreaterThan(0);expect(x).toBeLessThan(3);}});'),
            'go': ('package p\nfunc TestLoop(t *T) {for _, x := range []int{1,2} {check(x > 0)}}',
                   'package p\nfunc TestLoop(t *T) {for _, x := range []int{1,2} {check(x > 0);check(x < 3)}}'),
        }
        for language, (old, new) in pairs.items():
            with self.subTest(language=language):
                self.assertFalse(integrity.counts(old, language) - integrity.counts(new, language))

    def test_removal_change_header_and_duplicate_loss_are_rejected(self):
        for language, source, replacements in (
            ('python', 'def test_x():\n if flag:\n  assert x == 2\n  assert x == 2\n',
             [('  assert x == 2\n', '', 1), ('x == 2', 'x == 1', 2), ('if flag', 'if False', 1), ('test_x', 'test_y', 1)]),
            ('typescript', 'test("x", () => {if(flag) {expect(x).toBe(2);expect(x).toBe(2);}});',
             [('expect(x).toBe(2);', '', 1), ('toBe(2)', 'toBe(1)', 2), ('if(flag)', 'if(false)', 1), ('test("x"', 'test("y"', 1)]),
            ('go', 'package p\nfunc TestX(t *T) {if flag {check(x==2);check(x==2)}}',
             [('check(x==2);', '', 1), ('x==2', 'x==1', 2), ('if flag', 'if false', 1), ('TestX', 'TestY', 1)]),
        ):
            for old, new, n in replacements:
                with self.subTest(language=language, replacement=(old,new)):
                    self.assertTrue(integrity.counts(source, language) - integrity.counts(source.replace(old,new,n), language))

    def test_nested_switch_and_exception_additions_preserve_headers(self):
        sources = [
            ('typescript', 'test("x",()=>{try {switch(x){case 1:check(1);break;default:check(2);}}catch(e){check(e);}finally{check(3);}});'),
            ('go', 'package p\nfunc TestX(t *T){switch x {case 1:check(1);default:check(2)}}'),
        ]
        for language, old in sources:
            with self.subTest(language=language):
                new = old.replace('check(1);', 'check(1);check(4);')
                self.assertFalse(integrity.counts(old,language)-integrity.counts(new,language))
                self.assertTrue(integrity.counts(old,language)-integrity.counts(old.replace('case 1','case 2'),language))

    def test_partial_changed_tests_fail_closed_and_unchanged_partial_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            base, candidate = Path(folder)/'base', Path(folder)/'candidate'
            base.mkdir();candidate.mkdir()
            (base/'x.test.ts').write_text('test("x",()=>{check(1);});')
            (candidate/'x.test.ts').write_text('test("x",()=>{')
            self.assertFalse(integrity.retained(candidate,base,['x.test.ts'],'typescript')['tests_preserved'])
            (base/'x.test.ts').write_text('test("x",()=>{')
            self.assertTrue(integrity.retained(candidate,base,['x.test.ts'],'typescript')['tests_preserved'])

    def test_native_go_assertion_failure_is_distinct_from_build_panic_and_skip(self):
        def events(rows):
            return '\n'.join(json.dumps(r) for r in rows)
        failed = [dict(Action='fail', Test='TestX')]
        self.assertEqual(native.go_result(events(failed), ['TestX']), dict(executed=1,passed=0,failed=1,errors=0,skipped=0))
        for rows in ([dict(Action='build-fail')],
                     [dict(Action='output',Output='panic: runtime error\n'),*failed],
                     [dict(Action='skip',Test='TestX')]):
            with self.subTest(rows=rows):
                result = native.go_result(events(rows),['TestX'])
                self.assertTrue(result['errors'] or result['skipped'])

    def test_go_selectors_exclude_suite_entrypoint_and_non_test_helpers(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'p').mkdir()
            (root/'p/x_test.go').write_text('package p\nfunc TestMain(m *M){}\nfunc Testhelper(t *T){}\nfunc TestValue(t *T){check(1)}\n')
            self.assertEqual(set(native.go_test_definitions(root,['p/x_test.go'])),{('./p','TestValue')})

    def test_vitest_counts_selected_cases_and_preserves_native_totals(self):
        root = Path('/private/tmp/native-fixture')
        filename = str(root/'x.test.ts')
        title = 'expect(x > 0).toBe(true)'
        selected = native.identifier(root,filename,'suite > '+title)
        data = dict(numTotalTests=2,numPassedTests=1,numFailedTests=0,numPendingTests=1,
                    testResults=[dict(name=filename,assertionResults=[
                        dict(ancestorTitles=['suite'],title=title,status='passed'),
                        dict(ancestorTitles=['suite'],title='other',status='skipped')])])
        result = native.vitest_result(data,root,[selected])
        self.assertEqual((result['executed'],result['passed'],result['skipped']),(1,1,0))
        self.assertEqual((result['native_total_tests'],result['native_executed_total'],result['native_skipped_total']),(2,1,1))
        self.assertEqual(native.vitest_result(data,root,['missing'])['errors'],1)

    def test_vitest_runtime_errors_do_not_qualify_as_assertions(self):
        root=Path('/private/tmp/native-fixture');filename=str(root/'x.test.ts')
        key=native.identifier(root,filename,'test')
        for message,errors in [('AssertionError: expected1toBe2',0),('TypeError: setup',1),('',1)]:
            test=dict(ancestorTitles=[],title='test',status='failed',failureMessages=[message] if message else [])
            result=native.vitest_result(dict(testResults=[dict(name=filename,assertionResults=[test])]),root,[key])
            self.assertEqual((result['failed'],result['errors']),(1,errors))

    def test_django_selector_uses_concrete_case_and_original_mixin_implementation(self):
        import functools
        class Mixin:
            def test_value(self):pass
        class Concrete(Mixin,unittest.TestCase):pass
        result=native.django_id(Concrete('test_value'))
        self.assertTrue(result['id'].endswith('Concrete.test_value'))
        self.assertTrue(result['implementation'].endswith('Mixin.test_value'))
        class Generated(unittest.TestCase):
            test_generated = staticmethod(functools.partial(Mixin.test_value, None))
        self.assertTrue(native.django_id(Generated('test_generated'))['implementation'].endswith('Mixin.test_value'))

    def test_unselected_vitest_runtime_error_still_blocks_qualification(self):
        root=Path('/private/tmp/native-fixture');filename=str(root/'x.test.ts')
        key=native.identifier(root,filename,'assertion')
        data=dict(testResults=[dict(name=filename,assertionResults=[
            dict(ancestorTitles=[],title='assertion',status='failed',failureMessages=['AssertionError: original']),
            dict(ancestorTitles=[],title='runtime',status='failed',failureMessages=['TypeError: setup']),
        ])])
        result=native.vitest_result(data,root,[key])
        self.assertEqual((result['executed'],result['failed'],result['errors']),(1,1,1))

    def test_native_cli_zero_or_missing_cases_cannot_exit_successfully(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder)/'env.json';config.write_text('{}')
            argv = ['study_native.py','--environment',str(config),'--language','go','--ids','[{"package":"./p","name":"TestX"}]']
            for response in (dict(native.summary(),exit_code=0),
                             dict(native.summary(),exit_code=0,errors=1)):
                with self.subTest(response=response),patch.object(native.sys,'argv',argv),patch.object(native,'go',return_value=response),patch('sys.stdout',new_callable=io.StringIO):
                    self.assertEqual(native.main(),1)

    def test_source_copy_keeps_tracked_outputs_and_safe_links_excluding_caches(self):
        with tempfile.TemporaryDirectory() as folder:
            base,target = Path(folder)/'base',Path(folder)/'copy'
            for name in ['keep.py','dist/a.ts','docs/base.txt','node_modules/thirdparty.js','.code-graph/private.json']:
                p=base/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(name)
            (base/'docs/link.txt').symlink_to('base.txt')
            study.source_copy(base,target,['keep.py','dist/a.ts','docs/base.txt','docs/link.txt'])
            self.assertEqual((target/'dist/a.ts').read_text(),'dist/a.ts')
            self.assertTrue((target/'docs/link.txt').is_symlink())
            self.assertFalse((target/'node_modules').exists())
            self.assertFalse((target/'.code-graph').exists())
            (base/'escape.py').symlink_to(Path(folder)/'outside.py')
            with self.assertRaises(ValueError):study.source_copy(base,Path(folder)/'bad',['escape.py'])

    def test_archive_symlinks_are_opt_in_and_confined(self):
        import tarfile
        from types import SimpleNamespace
        def packet(link):
            output=io.BytesIO()
            with tarfile.open(fileobj=output,mode='w') as archive:
                member=tarfile.TarInfo('link');member.type=tarfile.SYMTYPE;member.linkname=link;archive.addfile(member)
            return SimpleNamespace(stdout=output.getvalue())
        with tempfile.TemporaryDirectory() as folder:
            for index,(link,allowed,works) in enumerate([('target',False,False),('target',True,True),('../escape',True,False),('/tmp/escape',True,False)]):
                with self.subTest(link=link,allowed=allowed),patch.object(integrity.pilot.subprocess,'run',return_value=packet(link)):
                    destination=Path(folder)/str(index)
                    if works:
                        integrity.pilot.archive(Path(folder),'commit',destination,allow_symlinks=allowed)
                        self.assertTrue((destination/'link').is_symlink())
                    else:
                        with self.assertRaises(ValueError):integrity.pilot.archive(Path(folder),'commit',destination,allow_symlinks=allowed)


if __name__ == '__main__':
    unittest.main()
