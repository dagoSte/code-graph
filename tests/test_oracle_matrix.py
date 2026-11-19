"""The recorded native oracle runs in the suite without Node, PHP or Go."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('oracle_matrix_test',REPO/'tools/oracle_matrix.py')
oracle=importlib.util.module_from_spec(spec);spec.loader.exec_module(oracle)


class OracleMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases=oracle.load_cases(REPO/'tests/oracle')
        cls.first_js=next(case for case in cls.cases if case['id']=='js/require_equal')

    def test_recorded_programs_do_not_regress_without_native_runtimes(self):
        with patch.object(oracle.shutil,'which',side_effect=AssertionError('native runtime lookup in recorded mode')):
            result=oracle.run_matrix(str(REPO),self.cases)
        self.assertEqual(result['errors'],[],json.dumps(result['cases'],indent=2))
        self.assertEqual(sum(result['counts']['js'].values()),102)
        self.assertEqual(sum(result['counts']['php'].values()),3)
        self.assertEqual(sum(result['counts']['go'].values()),14)
        self.assertEqual(sum(result['counts']['dart'].values()),12)
        self.assertEqual(sum(result['counts']['java'].values()),10)
        self.assertEqual(sum(result['counts']['swift'].values()),8)
        self.assertTrue(all(r['native']['mode']=='recorded' for r in result['cases'].values()))

    def test_wrong_target_beats_correct_target_and_low_does_not_count(self):
        case=next(c for c in self.cases if c['id']=='js/receiver_Service_control')
        good=('app.js','drive',2,'polish','a.js','polish','high','call')
        bad=('app.js','drive',2,'polish','decoy.js','Service.polish','high','call')
        self.assertEqual(oracle.classify([good],case)['status'],'ok')
        wrong=oracle.classify([good,bad],case)
        self.assertEqual(wrong['status'],'wrong')
        self.assertTrue(oracle.baseline_errors(wrong,case))
        self.assertEqual(oracle.classify([(*good[:6],'low','call')],case)['status'],'abstention')
        self.assertTrue(oracle.baseline_errors(oracle.classify([],case),case))

    def test_known_wrong_fix_and_different_wrong_are_both_reported(self):
        # B-js (1.6.5) fixed every JS known wrong: the 1.6.4 baseline of factory_alias stands in for any known wrong.
        case=copy.deepcopy(next(c for c in self.cases if c['id']=='js/factory_alias'))
        case['baseline']={'status':'wrong','decision':'dec-rinvio-1.6.4','missing':[],
                          'wrong':[['app.js','drive',3,'polish','a.js','polish','high','call',3,1,'module','ns:./a.js',0,'']]}
        unchanged=oracle.classify(case['baseline']['wrong'],case)
        self.assertEqual(oracle.baseline_errors(unchanged,case),[])
        self.assertIn('stale',oracle.baseline_errors(oracle.classify([],case),case)[0])
        different=list(case['baseline']['wrong'][0]);different[4]='b.js'
        self.assertIn('changed',oracle.baseline_errors(oracle.classify([different],case),case)[0])

    def test_loader_rejects_unbound_sources_duplicate_ids_and_escaping_paths(self):
        for mutation in ('source','duplicate','escape','empty','empty_sources','empty_relations','empty_witnesses'):
            data={'schema':'oracle-matrix/1','cases':[copy.deepcopy(self.first_js)]}
            if mutation=='source':data['cases'][0]['files']['a.js']='changed'
            if mutation=='duplicate':data['cases']*=2
            if mutation=='escape':data['cases'][0]['files']['../escape']='changed'
            if mutation=='empty':data['cases']=[]
            if mutation=='empty_sources':
                data['cases'][0]['files']={};data['cases'][0]['source_sha256']=oracle.source_hash({})
            if mutation=='empty_relations':data['cases'][0]['observe']['relations']=[]
            if mutation=='empty_witnesses':data['cases'][0]['required_indexed']=[]
            expected='.'
            if mutation=='source':
                data['cases'][0]['evidence_sha256']=oracle.evidence_hash(data['cases'][0])
                expected='source changed'
            if mutation=='escape':
                data['cases'][0]['source_sha256']=oracle.source_hash(data['cases'][0]['files'])
                data['cases'][0]['evidence_sha256']=oracle.evidence_hash(data['cases'][0])
                expected='Invalid oracle source path'
            with tempfile.TemporaryDirectory() as temp:
                (Path(temp)/'bad.json').write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError,expected):oracle.load_cases(temp)

    def test_every_target_of_the_observed_site_is_checked(self):
        case=next(c for c in self.cases if c['id']=='js/factory_control_plain')
        good=('app.js','drive',3,'polish','a.js','polish','high','call')
        bad=(*good[:4],'app.js','fakeFactory.polish','high','call')
        self.assertEqual(oracle.classify([good,bad],case)['status'],'wrong')
        unrelated=('app.js','other',1,'polish','app.js','helper','high','call')
        self.assertEqual(oracle.classify([good,unrelated],case)['status'],'ok')

    def test_evidence_metadata_is_bound_and_targets_match_native_output(self):
        for field in ('targets','observe','native','required_indexed'):
            case=copy.deepcopy(self.first_js)
            if field=='targets':case[field]=[['a.js','polish']]
            elif field=='observe':case[field]['sites']=[]
            elif field=='native':case[field]['stdout']='A\n'
            else:case[field]=['a.js']
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                (Path(temp)/'bad.json').write_text(json.dumps({'schema':'oracle-matrix/1','cases':[case]}))
                with self.assertRaises(ValueError):oracle.load_cases(temp)

    def test_rehashed_targets_still_must_match_recorded_native_result(self):
        case=copy.deepcopy(self.first_js);case['targets']=[['a.js','polish']]
        case['evidence_sha256']=oracle.evidence_hash(case)
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp)/'bad.json').write_text(json.dumps({'schema':'oracle-matrix/1','cases':[case]}))
            with self.assertRaisesRegex(ValueError,'disagree'):oracle.load_cases(temp)

    def test_unknown_confidence_is_an_error(self):
        case=next(c for c in self.cases if c['id']=='js/factory_control_plain')
        for confidence in ('HIGH','certain',''):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                oracle.classify([('app.js','drive',3,'polish','a.js','polish',confidence,'call')],case)

    def test_known_wrong_improvement_requires_baseline_update(self):
        case=copy.deepcopy(next(c for c in self.cases if c['id']=='php/firstclass_import_global_decoy'))
        # Preserve a synthetic known-wrong baseline after the real PHP case is fixed.
        case['baseline']={'status':'wrong','decision':'synthetic-known-wrong',
                          'wrong':[['app.php','drive',1,'polish','global.php','polish','medium','reference']],
                          'missing':[['lib.php','polish']]}
        rows=case['baseline']['wrong']+[('app.php','drive',1,'polish','lib.php','polish','medium','reference')]
        self.assertTrue(oracle.baseline_errors(oracle.classify(rows,case),case))

    def test_reattributed_wrong_edges_cannot_escape_the_oracle(self):
        case=copy.deepcopy(next(c for c in self.cases if c['id']=='js/require_equal'))
        case['observe']['target_files']=['a.js','b.js','decoy.js']
        site=case['observe']['sites'][0]
        for target in ('a.js','app.js'):
            bad=(site[0],'app.js',site[2],site[3],target,'polish','high','call')
            with self.subTest(target=target):
                self.assertEqual(oracle.classify([bad],case)['status'],'wrong')
        # The target domain also detects a changed line/name at an unrelated site.
        bad=('app.js','other',1,'renamed','a.js','polish','high','call')
        self.assertEqual(oracle.classify([bad],case)['status'],'wrong')

    def test_only_edges_from_the_exact_site_satisfy_expected_targets(self):
        case=next(c for c in self.cases if c['id']=='js/receiver_Service_control')
        good=('app.js','drive',2,'polish','a.js','polish','high','call')
        self.assertEqual(oracle.classify([good],case)['status'],'ok')
        for caller in ('app.js','other'):
            row=(good[0],caller,*good[2:])
            with self.subTest(caller=caller):
                self.assertEqual(oracle.classify([row],case)['status'],'wrong')
        unrelated=('app.js','app.js',3,'log','a.js','polish','high','call')
        self.assertEqual(oracle.classify([unrelated],case)['status'],'abstention')

    def test_missing_extracted_site_is_an_error_even_for_empty_targets(self):
        case=next(c for c in self.cases if c['id']=='js/require_equal')
        graph={'rows':[],'indexed_files':case['required_indexed'],'sites':[]}
        with patch.object(oracle.edge_diff,'measure',return_value={case['id']:graph}):
            result=oracle.run_matrix(str(REPO),[case])
        self.assertIn(case['id'],result['errors'])
        self.assertIn('site',str(result['cases'][case['id']]['errors']))

    def test_rerun_missing_runtime_falls_back_and_mismatch_fails(self):
        case=self.first_js
        with patch.object(oracle.shutil,'which',return_value=None):
            result,errors=oracle.native_result(case,'.',True)
        self.assertEqual(result['mode'],'recorded-runtime-unavailable');self.assertEqual(errors,[])
        with patch.object(oracle.shutil,'which',return_value='/node'), patch.object(oracle.subprocess,'run') as run:
            run.return_value.returncode=0;run.return_value.stdout='different\n';run.return_value.stderr=''
            result,errors=oracle.native_result(case,'.',True)
            self.assertNotIn('NODE_OPTIONS',run.call_args.kwargs['env'])
        self.assertTrue(errors)

    def test_go_rerun_disables_downloads_and_inherited_build_flags(self):
        case=next(case for case in self.cases if case['id']=='go/explicit_alias')
        with tempfile.TemporaryDirectory() as temp, patch.dict(oracle.edge_diff.os.environ,
                GOFLAGS='-toolexec=untrusted',GOROOT='/untrusted',GOOS='foreign'), \
                patch.object(oracle.shutil,'which',return_value='/go'), patch.object(oracle.subprocess,'run') as run:
            root=Path(temp)/'case';root.mkdir()
            run.return_value.returncode=0
            run.return_value.stdout=case['native']['stdout']
            run.return_value.stderr=''
            result,errors=oracle.native_result(case,str(root),True)
            env=run.call_args.kwargs['env']
            self.assertEqual((env['GOPROXY'],env['GOSUMDB'],env['GOTOOLCHAIN'],env['GOWORK']),
                             ('off','off','local','off'))
            self.assertTrue(Path(env['GOCACHE']).is_relative_to(Path(temp)))
            for key in ('GOFLAGS','GOROOT','GOOS'):self.assertNotIn(key,env)
        self.assertEqual(errors,[])
        self.assertEqual(result['mode'],'rerun')

    def test_dart_rerun_uses_vm_without_pub_or_inherited_flags(self):
        case=next(case for case in self.cases if case['id']=='dart/prefix')
        with tempfile.TemporaryDirectory() as temp, patch.dict(oracle.edge_diff.os.environ,
                DART_VM_OPTIONS='untrusted',DART_FLAGS='untrusted'), patch.object(oracle.subprocess,'run') as run:
            run.return_value.returncode=0
            run.return_value.stdout=case['native']['stdout'];run.return_value.stderr=''
            result,errors=oracle.native_result(case,temp,True,dart='/trusted/dart')
            args=run.call_args.args[0];env=run.call_args.kwargs['env']
            self.assertEqual(args,['/trusted/dart','--disable-dart-dev','app.dart'])
            self.assertNotIn('DART_VM_OPTIONS',env);self.assertNotIn('DART_FLAGS',env)
            self.assertEqual(env['DART_SUPPRESS_ANALYTICS'],'true')
        self.assertEqual(errors,[]);self.assertEqual(result['mode'],'rerun')

    def test_java_rerun_disables_processors_and_inherited_options(self):
        case=next(case for case in self.cases if case['id']=='java/type_import')
        with tempfile.TemporaryDirectory() as temp, patch.dict(oracle.edge_diff.os.environ,
                CLASSPATH='untrusted',JAVA_TOOL_OPTIONS='untrusted',JDK_JAVAC_OPTIONS='-processor untrusted'), patch.object(oracle.subprocess,'run') as run:
            run.return_value.returncode=0;run.return_value.stdout=case['native']['stdout'];run.return_value.stderr=''
            result,errors=oracle.native_result(case,temp,True,java_home='/trusted/jdk')
            compile_args=run.call_args_list[0].args[0];runtime_args=run.call_args_list[1].args[0]
            self.assertEqual(compile_args[0],'/trusted/jdk/bin/javac');self.assertIn('-proc:none',compile_args)
            self.assertEqual(compile_args[compile_args.index('--release')+1],'17')
            self.assertIn('-sourcepath',compile_args);self.assertIn('-classpath',compile_args)
            self.assertEqual(runtime_args[0],'/trusted/jdk/bin/java')
            for key in ('CLASSPATH','JAVA_TOOL_OPTIONS','JDK_JAVAC_OPTIONS'):self.assertNotIn(key,run.call_args.kwargs['env'])
        self.assertEqual(errors,[]);self.assertEqual(result['mode'],'rerun')

    def test_java_compilation_failure_never_runs_the_program(self):
        case=next(case for case in self.cases if case['id']=='java/type_import')
        with tempfile.TemporaryDirectory() as temp, patch.object(oracle.subprocess,'run') as run:
            run.return_value.returncode=1;run.return_value.stdout='';run.return_value.stderr='compile failure'
            result,errors=oracle.native_result(case,temp,True,java_home='/trusted/jdk')
            self.assertEqual(run.call_count,1)
        self.assertEqual(result['mode'],'rerun-compilation-failed');self.assertTrue(errors)

    def test_swift_rerun_uses_explicit_sdk_and_isolated_cache_without_driver_hooks(self):
        case=next(case for case in self.cases if case['id']=='swift/private_global')
        with tempfile.TemporaryDirectory() as temp, patch.dict(oracle.edge_diff.os.environ,
                SWIFT_DRIVER_SWIFT_FRONTEND_EXEC='/untrusted',SDKROOT='/untrusted',CPATH='/untrusted'), patch.object(oracle.subprocess,'run') as run:
            run.return_value.returncode=0;run.return_value.stdout=case['native']['stdout'];run.return_value.stderr=''
            result,errors=oracle.native_result(case,temp,True,swiftc='/trusted/swiftc',swift_sdk='/trusted/sdk')
            args=run.call_args_list[0].args[0];env=run.call_args_list[0].kwargs['env']
            self.assertEqual(args[0],'/trusted/swiftc');self.assertEqual(args[args.index('-swift-version')+1],'5')
            self.assertEqual(args[args.index('-sdk')+1],'/trusted/sdk');self.assertIn('-module-cache-path',args)
            for key in ('SWIFT_DRIVER_SWIFT_FRONTEND_EXEC','SDKROOT','CPATH'):self.assertNotIn(key,env)
        self.assertEqual(errors,[]);self.assertEqual(result['mode'],'rerun')

    def test_swift_compilation_failure_never_executes_the_program(self):
        case=next(case for case in self.cases if case['id']=='swift/private_global')
        with tempfile.TemporaryDirectory() as temp, patch.object(oracle.subprocess,'run') as run:
            run.return_value.returncode=1;run.return_value.stdout='';run.return_value.stderr='compile failure'
            result,errors=oracle.native_result(case,temp,True,swiftc='/trusted/swiftc',swift_sdk='/trusted/sdk')
            self.assertEqual(run.call_count,1)
        self.assertTrue(errors);self.assertEqual(result['mode'],'rerun-compilation-failed')


if __name__=='__main__':unittest.main()
