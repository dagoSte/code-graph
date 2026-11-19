"""Synthetic execution and committed-receipt controls; no models or downloads."""
import hashlib
import importlib.util
import csv
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('locate_bench_test', REPO/'benchmarks/locate_bench.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class LocateBench(unittest.TestCase):
    def comparison_inputs(self, folder):
        data = folder/'inputs'
        shutil.copytree(bench.DATA, data,
                        ignore=shutil.ignore_patterns('comparison.json','README.md','RESULTS.md'))
        return data

    def test_compare_cli_regenerates_exact_bytes_without_the_comparison_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = self.comparison_inputs(root); output = root/'regenerated.json'
            bootstrap = '''import runpy, sys
from pathlib import Path
def audit(event, args):
    if event in ('subprocess.Popen','os.system','os.fork','os.posix_spawn',
                 'socket.connect','socket.getaddrinfo','sqlite3.connect'):
        raise AssertionError('No process, network or index: '+event)
    if event == 'open' and isinstance(args[0], str) and Path(args[0]).name == 'comparison.json':
        raise AssertionError('The comparison is not an input')
sys.addaudithook(audit)
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''
            run = subprocess.run([sys.executable,'-I','-S','-B','-c',bootstrap,str(REPO/'benchmarks/locate_bench.py'),
                'compare','--cases',str(data/'cases.json'),'--rule',str(data/'selection-rule.json'),
                '--output',str(output)], cwd=root, capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(output.read_bytes(),(bench.DATA/'comparison.json').read_bytes())
            self.assertFalse((data/'comparison.json').exists())
            for action in ('prepare','measure'):
                missing = root/(action+'.json')
                run = subprocess.run([sys.executable,'-I',str(REPO/'benchmarks/locate_bench.py'),
                    action,'--output',str(missing)], cwd=root, capture_output=True, text=True, timeout=30)
                self.assertEqual(run.returncode,2,run.stderr)
                self.assertIn('--skill-root and --cache-root are required',run.stderr)
                self.assertFalse(missing.exists())

    def test_compare_recounts_saved_receipts_without_processes_or_current_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = self.comparison_inputs(root); output = root/'regenerated.json'
            with patch('subprocess.Popen',side_effect=AssertionError('No process execution')), \
                 patch.object(bench.study,'engine_files',side_effect=AssertionError('No current engine')), \
                 patch.object(bench.study,'parser_versions',side_effect=AssertionError('No current parsers')):
                result = bench.compare(data/'cases.json',data/'selection-rule.json',output)
            self.assertEqual(output.read_bytes(),(bench.DATA/'comparison.json').read_bytes())
            self.assertEqual(result,json.loads(output.read_text()))

    def test_compare_refuses_tampered_inputs_before_writing(self):
        for changed in ('cases','issue','rule','dispositions','receipt','score'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); data = self.comparison_inputs(root); output = root/'regenerated.json'
                name = {'cases':'cases.json','issue':'cases.json','rule':'selection-rule.json',
                        'dispositions':'ground-truth-dispositions.json',
                        'receipt':'candidate-4000.json','score':'candidate-4000.json'}[changed]
                value = json.loads((data/name).read_text())
                if changed == 'cases': value['cases'][0]['issue_title'] += ' tampered'
                elif changed == 'issue':
                    task = value['cases'][0]
                    task['issue_body'] += ' tampered'
                    task['issue_body_sha256'] = hashlib.sha256(task['issue_body'].encode()).hexdigest()
                elif changed == 'rule': value['measurement'] += ' tampered'
                elif changed == 'dispositions': value['registered_before_measurement'] = False
                elif changed == 'receipt': value['ground_truth_dispositions_sha256'] = '0'*64
                else: value['cases'][0]['files_at']['5']['any_hit'] = not value['cases'][0]['files_at']['5']['any_hit']
                bench.save(data/name,value)
                with self.assertRaises(ValueError):
                    bench.compare(data/'cases.json',data/'selection-rule.json',output)
                self.assertFalse(output.exists())

    def test_compare_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = self.comparison_inputs(root); output = root/'existing.json'
            output.write_text('keep this file')
            with self.assertRaises(ValueError):
                bench.compare(data/'cases.json',data/'selection-rule.json',output)
            self.assertEqual(output.read_text(),'keep this file')

    def test_compare_refuses_consistently_forged_candidate_receipts(self):
        for filename in ('candidate-4000.json','candidate-5000.json'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); data = self.comparison_inputs(root); output = root/'regenerated.json'
                tasks = bench.load_cases(data/'cases.json',data/'selection-rule.json')['cases']
                task = next(t for t in tasks if t['id'] == 'django-22158')
                observed = json.loads((data/'candidate-5000.json').read_text())
                original = next(r for r in observed['cases'] if r['task'] == task['id'])
                target = next(r for r in original['candidates'] if r['file'] in task['production_files'])
                receipt = json.loads((data/filename).read_text())
                row = next(r for r in receipt['cases'] if r['task'] == task['id'])
                before = receipt['summary']['files_at']['1']['any']
                self.assertFalse(row['files_at']['1']['any_hit'])
                row['candidates'] = [target]+[r for r in row['candidates'] if r != target]
                row.update(bench.score(task,dict(items=row['candidates'],reading=row.get('reading'),
                                                 status=row.get('locate_status'))))
                receipt['summary'] = bench.summarize(tasks,receipt['cases'])
                self.assertEqual(receipt['summary']['files_at']['1']['any'],before+1)
                bench.save(data/filename,receipt)
                with self.assertRaisesRegex(ValueError,'Candidate receipt hash mismatch'):
                    bench.compare(data/'cases.json',data/'selection-rule.json',output)
                self.assertFalse(output.exists())

    def task(self, name='django-1'):
        return dict(id=name, repository=name.rsplit('-',1)[0], issue_title='leaf "quoted"', issue_body='line1\nline2 ☃',
                    production_files=['leaf.py'], fix_targets=[dict(file='leaf.py', declarations=[dict(line=1,end_line=2)])])

    def fixture(self, folder):
        rule = folder/'rule.json'; bench.save(rule,dict(per_repository=1))
        cases = folder/'cases.json'; tasks = [self.task(name+'-1') for name in bench.REPOSITORIES]
        for t in tasks:
            name = t['id']+'.patch'; (folder/name).write_text('historical diff')
            t.update(production_diff=name,production_diff_sha256=bench.digest(folder/name),
                     issue_body_sha256=hashlib.sha256(t['issue_body'].encode()).hexdigest())
        bench.save(cases,dict(format='codegraph-locate-cases/1',rule_sha256=bench.digest(rule),cases=tasks))
        return cases,rule,tasks

    def test_selection_keeps_native_rejects_and_uses_ledger_order_excluding_old_cases(self):
        rows=[dict(pr=9,issue=1,fix='a',production_files=['leaf.py']),
              dict(pr=8,issue=2,fix='b',production_files=['leaf.py'],reason='issue_not_classified_bug'),
              dict(pr=7,issue=3,fix='c',production_files=['leaf.py'],status='rejected_native'),
              dict(pr=6,issue=4,fix='d',production_files=['leaf.py'],other_production=['other.ts'])]
        ledger=dict(repositories={r:dict(metadata=rows) for r in bench.REPOSITORIES})
        selected,dispositions=bench.select(ledger,{r+'-9' for r in bench.REPOSITORIES},2)
        self.assertEqual([r['pr'] for r in selected],[7,6]*3)
        self.assertEqual(sum(d['disposition']=='original_study_case' for d in dispositions),3)
        self.assertEqual(selected[1]['other_production'],['other.ts'])
        with self.assertRaises(ValueError):bench.select(ledger,set(),0)
        with self.assertRaises(ValueError):bench.select(ledger,set(),4)

    def test_diff_truth_maps_parent_spans_and_declares_absent_and_partial_sources(self):
        engine=bench.study.shared.load(REPO/'scripts/cg.py','locate_bench_test_engine')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'leaf.py').write_text('def leaf():\n    return 1\n\ndef decoy():\n    return 2\n')
            target=bench.mapping.targets(engine,root,'python','leaf.py','@@ -2 +2 @@\n-    return 1\n+    return 3\n')
            self.assertEqual([(d['qname'],d['line'],d['end_line']) for d in target['declarations']],[('leaf',1,2)])
            self.assertEqual(bench.mapping.targets(engine,root,'python','absent.py','@@ -0,0 +1 @@')['status'],'absent_in_parent')
            (root/'leaf.py').write_text('def leaf(:')
            self.assertEqual(bench.mapping.targets(engine,root,'python','leaf.py','@@ -1 +1 @@')['status'],'partial_parent_ast')

    def test_same_name_wrong_span_is_not_a_callable_hit_and_reading_stays_native(self):
        task=self.task(); payload=dict(items=[dict(file='leaf.py',line=4,end_line=5)],reading=dict(returned_chars=99,file_chars=3000,exact=False))
        result=bench.mapping.score(task,payload)
        self.assertTrue(result['files_at']['1']['any_hit'])
        self.assertFalse(result['callable_at_5']['any_hit'])
        self.assertEqual(result['reading'],payload['reading'])

    def test_measure_uses_literal_issue_argv_and_validates_responses_without_leaking_output(self):
        seen=[]
        def execute(argv,cwd,**kwargs):
            seen.append(argv)
            return dict(status='passed',exit=0,stdout_tail=json.dumps(dict(format='codegraph-locate/1',status='ready',
                items=[dict(file='leaf.py',line=1,end_line=2,source='/private/tmp/DO_NOT_PUBLISH')],reading=dict(returned_chars=12,file_chars=50,exact=False))))
        row=bench.measure_case(SimpleNamespace(run_tests=execute),REPO,Path('/synthetic'),self.task())
        self.assertEqual(seen[0][-2],'leaf "quoted"\n\nline1\nline2 ☃')
        self.assertTrue(row['callable_at_5']['any_hit']); self.assertFalse(row['reading']['exact'])
        self.assertEqual(row['candidates'],[dict(file='leaf.py',line=1,end_line=2)])
        self.assertNotIn('DO_NOT_PUBLISH',json.dumps(row))
        for budget in (4000,5000):
            bench.measure_case(SimpleNamespace(run_tests=execute),REPO,Path('/synthetic'),self.task(),max_chars=budget)
            self.assertEqual(seen[-1][-2:],['--max-chars',str(budget)])
        for run in [dict(status='timeout',exit=1,stdout_tail='/private/tmp/DO_NOT_PUBLISH'),
                    dict(status='passed',exit=0,stdout_tail='not JSON'),
                    dict(status='passed',exit=0,stdout_tail='{"format":"codegraph-locate/1","items":[{"file":"/private/tmp/x"}]}')]:
            row=bench.measure_case(SimpleNamespace(run_tests=lambda *a,**k:run),REPO,Path('/synthetic'),self.task())
            self.assertIn('error_code',row); self.assertFalse(row['files_at']['5']['any_hit'])
            self.assertNotIn('/private/tmp',json.dumps(row))

    def test_missing_executor_receipt_fails_closed_on_cleanup(self):
        def execute(*a,**k):raise RuntimeError('private exception')
        row=bench.measure_case(SimpleNamespace(run_tests=execute),REPO,Path('/synthetic'),self.task())
        self.assertTrue(row['cleanup_error']); self.assertEqual(row['error_code'],'missing_executor_receipt')
        self.assertNotIn('private exception',json.dumps(row))

    def test_frozen_cases_reject_diff_issue_and_inventory_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); cases,rule,tasks=self.fixture(root)
            self.assertEqual(len(bench.load_cases(cases,rule)['cases']),3)
            (root/tasks[0]['production_diff']).write_text('changed')
            with self.assertRaises(ValueError):bench.load_cases(cases,rule)
            (root/tasks[0]['production_diff']).write_text('historical diff')
            data=json.loads(cases.read_text()); data['cases'][0]['issue_body']='changed'; bench.save(cases,data)
            with self.assertRaises(ValueError):bench.load_cases(cases,rule)
            data['cases']=[tasks[0],tasks[0],tasks[2]];bench.save(cases,data)
            with self.assertRaises(ValueError):bench.load_cases(cases,rule)

    def test_orchestration_retains_a_failed_case_and_stops_on_engine_drift(self):
        for drift in [None,'engine','parser']:
            with self.subTest(drift=drift),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);cases,rule,tasks=self.fixture(root); output=root/'results.json'; version=[0]; observed=[]
                def fake(executor,skill,source,task,max_chars=None):
                    observed.append(task['id'])
                    if drift:version[0]+=1
                    return dict(task=task['id'],error_code='synthetic_failed_query',**bench.mapping.score(task,dict(items=[])))
                with patch.object(bench,'parent_copy'),patch.object(bench,'measure_case',side_effect=fake),patch.object(bench.study,'engine_files',side_effect=lambda _:dict(version=str(version[0] if drift=='engine' else 0))),patch.object(bench.study,'parser_versions',side_effect=lambda:dict(version=str(version[0] if drift=='parser' else 0))):
                    result=bench.measure(cases,rule,root,REPO,output)
                    with self.assertRaises(ValueError):bench.measure(cases,rule,root,REPO,output)
                self.assertEqual(observed,[t['id'] for t in tasks[:1 if drift else 3]])
                self.assertEqual(result['summary']['assigned'],3)
                self.assertEqual(result['summary']['failed'],1 if drift else 3)
                self.assertEqual(result['summary']['missing'],2 if drift else 0)
                self.assertEqual(result['stop_reason'],'engine_drift' if drift else None)
                self.assertEqual(result['complete'],not drift)
                self.assertEqual(len(result['not_started']),2 if drift else 0)

    def test_summary_includes_unmapped_and_missing_denominators(self):
        tasks=[self.task('django-1'),dict(self.task('prometheus-1'),fix_targets=[])]
        row=dict(task='django-1',**bench.mapping.score(tasks[0],dict(items=[])))
        result=bench.summarize(tasks,[row])
        self.assertEqual(result['files_at']['5'],dict(cases=2,any=0,all=0))
        self.assertEqual(result['callable_at_5'],dict(mapped_cases=1,any=0,matched=0,targets=1))
        self.assertEqual(result['reading']['returned_chars'],dict(known=0,missing=2,median=None))

    def test_missing_file_truth_cannot_be_counted_as_a_vacuous_all_hit(self):
        task=dict(self.task(),production_files=[],fix_targets=[],ground_truth_status='unavailable')
        row=dict(task=task['id'],**bench.mapping.score(task,dict(items=[])))
        result=bench.summarize([task],[row])
        self.assertEqual(result['files_at']['5']['all'],0)
        self.assertEqual(result['files_at']['5']['cases'],0)
        self.assertEqual(result['file_ground_truth_missing'],1)
        self.assertIsNone(bench.score(task,dict(items=[]))['files_at']['5']['all_hit'])

    def test_unavailable_truth_needs_the_frozen_explicit_disposition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cases,rule,tasks=self.fixture(root)
            task=tasks[0];task.update(production_files=[],fix_targets=[],ground_truth_status='unavailable',
                                     ground_truth_reason='no_production_first_parent_diff')
            data=json.loads(cases.read_text());data['cases']=tasks;bench.save(cases,data)
            with self.assertRaises(ValueError):bench.load_cases(cases,rule)
            disposition=root/'ground-truth-dispositions.json'
            bench.save(disposition,dict(cases={task['id']:dict(reason=task['ground_truth_reason'])}))
            data['ground_truth_dispositions_sha256']=bench.digest(disposition);bench.save(cases,data)
            self.assertEqual(bench.load_cases(cases,rule)['cases'][0]['production_files'],[])
            bench.save(disposition,dict(cases={}))
            with self.assertRaises(ValueError):bench.load_cases(cases,rule)

    def test_prepare_and_real_cli_measure_three_synthetic_git_parents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); metadata=root/'metadata'; study_data=root/'study'; study_data.mkdir()
            repositories={}; heads={}
            sources={'django':('leaf.py','raise RuntimeError("must not execute")\ndef leaf():\n    return 1\n'),
                     'typescript-eslint':('leaf.ts','throw new Error("must not execute");\nexport function leaf(){return 1;}\n'),
                     'prometheus':('leaf.go','package p\nfunc init(){panic("must not execute")}\nfunc Leaf() int {return 1}\n')}
            git=bench.study.shared.git
            for repo,(filename,source) in sources.items():
                folder=metadata/repo;cache=folder/'cache';cache.mkdir(parents=True)
                git(cache,'init','-b','main');git(cache,'config','user.name','local fixture');git(cache,'config','user.email','fixture@example.invalid')
                (cache/filename).write_text(source)
                if repo=='prometheus':(cache/'extra.ts').write_text('export function extra(){return 1;}\n')
                git(cache,'add','.');git(cache,'commit','-m','parent')
                (cache/filename).write_text(source.replace('return 1','return 2'))
                if repo=='prometheus':(cache/'extra.ts').write_text('export function extra(){return 2;}\n')
                git(cache,'add','.');git(cache,'commit','-m','historical fix')
                fix=git(cache,'rev-parse','HEAD').stdout.strip();heads[repo]=fix
                row=dict(pr=1,issue=1,fix=fix,production_files=[filename])
                if repo=='prometheus':row['other_production']=['extra.ts']
                repositories[repo]=dict(metadata=[row])
                bench.save(folder/'pr-1.json',dict(merged=True,merge_commit_sha=fix,html_url='https://example.org/pr/1'))
                if repo=='django':
                    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=['summary','description','type']);writer.writeheader()
                    writer.writerow(dict(summary='leaf',description='return correct value',type='Bug'))
                    (folder/'ticket-1.csv').write_text(stream.getvalue())
                else:bench.save(folder/'issue-1.json',dict(title='leaf',body='return correct value',html_url='https://example.org/issue/1',labels=[dict(name='bug')]))
            bench.save(study_data/'selection-ledger.json',dict(repositories=repositories));bench.save(study_data/'cases.json',dict(cases=[]))
            rule=root/'rule.json';bench.save(rule,dict(per_repository=1,ledger_sha256=bench.digest(study_data/'selection-ledger.json'),excluded_cases_sha256=bench.digest(study_data/'cases.json')))
            bundle=root/'bundle'; prepared=bench.prepare(rule,study_data,metadata,REPO,bundle)
            self.assertEqual(len(prepared['cases']),3)
            self.assertEqual(prepared['cases'][-1]['production_files'],['extra.ts','leaf.go'])
            self.assertTrue(all(t['fix_targets'][0]['declarations'] for t in prepared['cases']))
            result=bench.measure(bundle/'cases.json',rule,metadata,REPO,root/'result.json')
            self.assertTrue(result['complete']); self.assertEqual(result['summary']['failed'],0)
            self.assertEqual((result['live_starts'],result['summary']['measured']),(0,3))
            self.assertNotIn(str(root),json.dumps(result))
            for repo,fix in heads.items():
                self.assertEqual(git(metadata/repo/'cache','rev-parse','HEAD').stdout.strip(),fix)
                self.assertFalse(git(metadata/repo/'cache','status','--porcelain').stdout)


if __name__=='__main__':unittest.main()
