"""Observable contracts from the evolution report, with positive/negative oracles."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cg_evolution',REPO/'scripts/cg.py')
cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg)
ProjectData=runpy.run_path(str(REPO/'scripts/project_data.py'))['ProjectData']
agent=runpy.run_path(str(REPO/'scripts/agent_queries.py'))
change=runpy.run_path(str(REPO/'scripts/change_analysis.py'))
check=runpy.run_path(str(REPO/'scripts/checks.py'))['findings']

# PHP 8.1 enums: every enum has cases(); only a backed enum (": string" / ": int") has from() and tryFrom().
ENUM_TYPES=r'''<?php
namespace App;
#[\Attribute]
final class Marker {}
#[Marker]
enum Backed: string
{
    case A = 'a';
    public static function parse(string $v): self { return self::tryFrom($v) ?? self::from('a'); }
}
enum Numbered
    : int
{
    case One = 1;
}
enum Pure
{
    case A;
    public static function all(): array { return self::cases(); }
    public static function bad(): ?self { return static::tryFrom('a'); }
}
final class Plain {}
'''
ENUM_CALLS=r'''<?php
require __DIR__ . '/types.php';
use App\Backed;
use App\Numbered;
use App\Pure;
use App\Plain;
$calls = [
    __LINE__ => fn() => Backed::from('a'),
    __LINE__ => fn() => Backed::tryFrom('a'),
    __LINE__ => fn() => Backed::cases(),
    __LINE__ => fn() => Backed::TRYFROM('a'),
    __LINE__ => fn() => Backed::parse('a'),
    __LINE__ => fn() => Numbered::from(1),
    __LINE__ => fn() => Numbered::cases(),
    __LINE__ => fn() => Pure::cases(),
    __LINE__ => fn() => Pure::all(),
    __LINE__ => fn() => Pure::from('a'),
    __LINE__ => fn() => Pure::tryFrom('a'),
    __LINE__ => fn() => Pure::bad(),
    __LINE__ => fn() => Backed::form('a'),
    __LINE__ => fn() => Plain::cases(),
];
foreach ($calls as $line => $call) {
    try { $call(); } catch (\Error $error) { echo $line, ' ', $error->getMessage(), "\n"; }
}
'''
def line_of(text,needle):return next(number for number,line in enumerate(text.splitlines(),1) if needle in line)
# Calls PHP rejects; Pure::bad() fails inside types.php, where static::tryFrom() is written.
ENUM_MISSING={('missing_static_method','calls.php',line_of(ENUM_CALLS,needle)) for needle in ("Pure::from('a')","Pure::tryFrom('a')","Backed::form('a')","Plain::cases()")}|{('missing_method','types.php',line_of(ENUM_TYPES,'static::tryFrom'))}


class Evolution(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cg-evolution-');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def put(self,name,text):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);return path

    def index(self,full=False):
        with contextlib.redirect_stdout(io.StringIO()):cg.cmd_index(argparse.Namespace(root=str(self.root),full=full))

    def data(self):return ProjectData(cg,self.root)
    def sid(self,p,name):return p.db.execute('SELECT id FROM symbols WHERE qname=?',(name,)).fetchone()[0]

    def fixture(self):
        self.put('app.py','def leaf(x): return x+1\ndef work(x):\n'+''.join(f'    step_{i}=x+{i}\n' for i in range(240))+'    return leaf(step_239)\ndef entry(x): return work(x)\n')
        self.put('tests/test_app.py','from app import entry\n'+''.join(f'def test_work_{i}(): return entry({i})\n' for i in range(12)))
        self.index()

    def test_context_long_preserves_relations_within_total_budget(self):
        self.fixture()
        with self.data() as p:
            result=agent['context'](p,self.sid(p,'work'),1000)
            for text in ('def work(x)','entry','leaf','revision','OMISSIONS','partial'):self.assertIn(text,result)
            self.assertLessEqual(len(result)+1,1000)
            self.assertIn('return leaf',result)

    def test_context_short_is_complete(self):
        self.put('app.py','def small(x):\n    return x+1\n');self.index()
        with self.data() as p:
            result=json.loads(agent['context'](p,self.sid(p,'small'),8000,as_json=True))
            self.assertEqual(result['omissions']['source']['status'],'complete')
            self.assertEqual(len(result['snippets'][0]['lines']),2)
            self.assertIsNone(result['budget']['measured_tokens'])

    def test_unicode_long_line_does_not_claim_exact_tokens(self):
        self.put('unicode.py','def funzione():\n    value="'+'🌱漢字'*2000+'"\n    return value\n');self.index()
        with self.data() as p:
            text=agent['context'](p,self.sid(p,'funzione'),1000)
            self.assertLessEqual(len(text)+1,1000);self.assertIn('unavailable',text);self.assertIn('partial',text)
            self.assertEqual(text.encode().decode(),text)

    def test_insufficient_budget_is_explicit(self):
        self.put('a.py','def f(): return 1\n');self.index()
        with self.data() as p:
            text=agent['context'](p,self.sid(p,'f'),64,as_json=True)
            self.assertEqual(json.loads(text)['status'],'budget_insufficient');self.assertLessEqual(len(text)+1,64)

    def test_compact_impact_preserves_every_chain_and_deduplicates(self):
        self.fixture()
        with self.data() as p:
            sid=self.sid(p,'work');legacy=p.impact(sid,'high');compact=p.impact(sid,'high',compact=True)
            self.assertEqual(len(compact['snippets']),13)
            self.assertEqual(sum(len(item['chain']) for item in compact['items']),25)
            for before,after in zip(legacy['items'],compact['items']):
                self.assertEqual(before['symbol'],after['symbol'])
                for left,right in zip(before['chain'],after['chain']):
                    self.assertEqual(left['source']['lines'],compact['snippets'][right['snippet_ref']]['lines'])
            self.assertTrue(all('source' not in step for item in compact['items'] for step in item['chain']))

    def test_known_snippets_optional_and_selfcontained_default(self):
        self.fixture()
        with self.data() as p:
            sid=self.sid(p,'work');first=p.impact(sid,compact=True)
            next=p.impact(sid,compact=True,known_snippets=first['snippets'])
            self.assertEqual(next['snippets'],{});self.assertEqual(set(next['known_snippets']),set(first['snippets']))
            self.assertEqual(set(p.impact(sid,compact=True)['snippets']),set(first['snippets']))

    def test_selective_source_does_not_read_relation_files(self):
        self.fixture()
        with self.data() as p, patch.object(p,'_read',wraps=p._read) as read:
            result=p.detail(self.sid(p,'work'),fields=['source'])
            self.assertNotIn('callers',result);self.assertNotIn('observations',result)
            self.assertEqual({call.args[0] for call in read.call_args_list},{'app.py'})

    def test_callers_and_callees_have_independent_pages(self):
        self.put('app.py','def work():\n'+''.join(f'    leaf_{i}()\n' for i in range(9))+''.join(f'def leaf_{i}(): pass\n' for i in range(9))+''.join(f'def caller_{i}(): work()\n' for i in range(11)));self.index()
        with self.data() as p:
            sid=self.sid(p,'work')
            for field,total in [('callers',11),('callees',9)]:
                cursor=None;seen=[]
                while True:
                    data=p.detail(sid,fields=[field],limit=3,cursor=cursor)
                    seen.extend(call['call_id'] for item in data[field] for call in item['callsites'])
                    self.assertEqual(data['pages'][field]['total'],total)
                    cursor=data['pages'][field]['next_cursor']
                    if not cursor:break
                self.assertEqual(len(seen),total);self.assertEqual(len(set(seen)),total)

    def test_cursor_rejects_other_query_and_revision(self):
        self.fixture()
        with self.data() as p:
            sid=self.sid(p,'entry');page=p.detail(sid,fields=['callers'],limit=2);cursor=page['pages']['callers']['next_cursor']
            with self.assertRaises(ValueError):p.detail(sid,'low',fields=['callers'],limit=2,cursor=cursor)
        self.put('new.py','def another(): pass\n');self.index()
        with self.data() as p:
            with self.assertRaises(ValueError):p.detail(self.sid(p,'entry'),fields=['callers'],limit=2,cursor=cursor)

    def test_noop_keeps_content_and_view_revision_but_changes_run(self):
        self.fixture()
        with self.data() as p:before=dict(p.meta)
        self.index()
        with self.data() as p:
            self.assertEqual(p.meta['content_revision'],before['content_revision']);self.assertEqual(p.meta['revision'],before['revision']);self.assertNotEqual(p.meta['run_id'],before['run_id'])

    def test_engine_change_reparses_unchanged_sources_before_new_revision(self):
        self.put('app.py','def same():return 1\n');self.index()
        original=cg.Extractor
        class NewPolicy(original):
            def add_sym(self,*args,**kwargs):
                index=super().add_sym(*args,**kwargs)
                if self.syms[index]['kind']=='function':self.syms[index]['exported']=1
                return index
        hashes,parsers,_=cg.analysis_inputs();updated=dict(hashes,policy='exported-one')
        with patch.object(cg,'Extractor',NewPolicy),patch.object(cg,'analysis_inputs',return_value=(updated,parsers,'new-policy')):
            self.index()
            with self.data() as p:
                self.assertEqual(p.db.execute('SELECT exported FROM symbols WHERE name="same"').fetchone()[0],1);revision=p.meta['content_revision']
            self.index(full=True)
            with self.data() as p:self.assertEqual(p.meta['content_revision'],revision)


    def test_config_and_exclusions_change_revision(self):
        self.fixture()
        with self.data() as p:before=p.meta['content_revision']
        self.put('package.json','{"scripts":{"build":"python app.py"}}');self.index()
        with self.data() as p:self.assertNotEqual(p.meta['content_revision'],before);before=p.meta['content_revision']
        self.put('.cgignore','tests/*\n');self.index()
        with self.data() as p:self.assertNotEqual(p.meta['content_revision'],before);self.assertNotIn('tests/test_app.py',p.files)

    def test_same_mtime_changes_content_revision(self):
        path=self.put('app.py','def before(): pass\n');self.index();stamp=path.stat().st_mtime_ns
        with self.data() as p:before=p.meta['content_revision']
        path.write_text('def after_(): pass\n');os.utime(path,ns=(stamp,stamp));self.index()
        with self.data() as p:self.assertNotEqual(before,p.meta['content_revision'])

    def test_shared_metrics_definitions_and_nodes(self):
        self.fixture()
        with self.data() as p:
            overview=p.overview();metrics=overview['metrics'];self.assertEqual(metrics['definitions'],overview['symbols']);self.assertEqual(metrics['graph_nodes'],metrics['definitions']+2)
            self.assertEqual(metrics['indexed_files'],2);self.assertEqual(metrics['resolved_callsites'],14)
            self.assertEqual(p.areas['.']['symbol_count'],metrics['definitions'])

    def test_python_binding_valid_and_invalid_modes(self):
        cases=[('C.m(C(),1)',False,''),('C.m(C())',True,''),('C.m(1,2)',False,'    @staticmethod\n'),('C.m(1)',True,'    @staticmethod\n')]
        for expression,invalid,decorator in cases:
            with self.subTest(expression=expression,decorator=decorator):
                self.put('app.py','class C:\n'+decorator+'    def m(self,x):return x\n'+expression+'\n');self.index()
                with self.data() as p:self.assertEqual(any(f['rule_id']=='arity' for f in check(p)),invalid)

    def test_python_keywords_and_generator(self):
        for expression,invalid in [('f(a=1,b=2)',False),('f(b=1)',True),('f(1,a=2)',True),('f(a=1,unknown=2)',True)]:
            self.put('app.py','def f(a,b=0): return a+b\n'+expression+'\n');self.index()
            with self.data() as p:self.assertEqual(any(f['rule_id']=='arity' for f in check(p)),invalid)
        self.put('app.py','def f(items):return items\nf(x for x in range(3))\n');self.index()
        with self.data() as p:self.assertFalse(any(f['rule_id']=='arity' for f in check(p)));self.assertEqual(p.db.execute('SELECT nargs FROM calls WHERE name="f"').fetchone()[0],1)

    def test_keyword_only_and_positional_only(self):
        for parameters,expression,invalid in [('a, /','f(a=1)',True),('a, *, b','f(1,2)',True),('a, *, b','f(1,b=2)',False)]:
            self.put('app.py','def f('+parameters+'):pass\n'+expression+'\n');self.index()
            with self.data() as p:self.assertEqual(any(f['rule_id']=='arity' for f in check(p)),invalid)

    def test_php_callable_reference_and_true_arity(self):
        self.put('app.php','<?php class C { static function request($a,$b) {} static function create(){return self::request(...);} static function bad(){self::request(1);} }');self.index()
        with self.data() as p:
            self.assertEqual(p.db.execute('SELECT COUNT(*) FROM calls WHERE name="request"').fetchone()[0],1)
            self.assertEqual(p.db.execute('SELECT COUNT(*) FROM symbol_refs WHERE kind="callable" AND target_id IS NOT NULL').fetchone()[0],1)
            self.assertEqual(len([f for f in check(p) if f['rule_id']=='arity']),1)

    def test_mixin_provider_and_true_typo(self):
        self.put('app.py','class Provider:\n    def verify(self):pass\nclass Mixin:\n    def run(self):self.verify();self.veriffy()\nclass Concrete(Mixin,Provider):pass\n');self.index()
        with self.data() as p:
            findings=[f for f in check(p) if f['rule_id']=='missing_method'];self.assertEqual(len(findings),1);self.assertIn('veriffy',findings[0]['message'])

    def test_tuple_assignment_is_class_scoped_and_conditional_unknown(self):
        self.put('app.py','class A:\n    def __init__(self):self.a,self.callback=(1,lambda:1)\n    def run(self):self.callback()\nclass B:\n    def run(self):self.callback()\nclass C:\n    def __init__(self,flag):\n        if flag:self.callback=lambda:1\n    def run(self):self.callback()\n');self.index()
        with self.data() as p:
            findings=check(p);missing=[f for f in findings if f['rule_id']=='missing_method'];self.assertEqual(len(missing),1);self.assertIn('B',missing[0]['message']);self.assertTrue(any(f['category']=='analysis_limit' for f in findings))

    def test_php_duplicates_depend_on_coload_order(self):
        self.put('a.php','<?php if(!function_exists("helper")){function helper(){}}');self.put('b.php','<?php function helper(){}')
        self.put('runner.php','<?php require __DIR__ . "/a.php";require __DIR__ . "/b.php";');self.index()
        with self.data() as p:self.assertTrue(any(f['rule_id']=='php_duplicate' and f['category']=='possible_defect' for f in check(p)))
        self.put('runner.php','<?php require __DIR__ . "/b.php";require __DIR__ . "/a.php";');self.index()
        with self.data() as p:self.assertFalse(any(f['rule_id']=='php_duplicate' for f in check(p)))

    def test_php_namespace_does_not_match_unrelated_shortname(self):
        self.put('app.php','<?php namespace App; class Target{} \\Vendor\\Target::missing();');self.index()
        with self.data() as p:self.assertFalse(any(f['rule_id']=='missing_static_method' for f in check(p)))

    def test_php_enum_builtins_follow_the_backing_type(self):
        self.put('types.php',ENUM_TYPES);self.put('calls.php',ENUM_CALLS);self.index()
        with self.data() as p:
            found=sorted((f['rule_id'],f['file'],f['line']) for f in check(p) if f['rule_id'] in ('missing_static_method','missing_method'))
        self.assertEqual(found,sorted(ENUM_MISSING))

    @unittest.skipUnless(shutil.which('php'),'native PHP required')
    def test_php_enum_fixture_matches_native_runtime(self):
        self.put('types.php',ENUM_TYPES);self.put('calls.php',ENUM_CALLS)
        result=subprocess.run(['php',str(self.root/'calls.php')],capture_output=True,text=True,timeout=10)
        self.assertEqual((result.returncode,result.stderr),(0,''))
        failed={int(line.split()[0]):line for line in result.stdout.splitlines()}
        self.assertTrue(all('Call to undefined method' in line for line in failed.values()),failed)
        expected={line for rule,path,line in ENUM_MISSING if path=='calls.php'}|{line_of(ENUM_CALLS,'Pure::bad()')}
        self.assertEqual(set(failed),expected)

    def test_test_helpers_are_not_counted_as_cases(self):
        self.put('app.py','def work():return 1\n');self.put('tests/test_app.py','from app import work\ndef helper():return work()\ndef test_work():return helper()\n');self.index()
        with self.data() as p:
            sid=self.sid(p,'work');impact=p.impact(sid);self.assertEqual([item['symbol']['name'] for item in impact['tests']],['test_work'])
            plan=change['plan'](p,[sid]);self.assertEqual(plan['tests'][0]['symbol']['name'],'test_work');self.assertEqual(plan['support'][0]['name'],'helper');self.assertFalse(plan['execution_performed'])

    def test_resource_candidates_keep_distinct_directories_and_dynamic_paths(self):
        self.put('app.py','def producer():open("a/catalog.json","w")\ndef consumer():open("a/catalog.json")\ndef other():open("b/catalog.json")\ndef dynamic():open("{export}/catalog.json")\n');self.index()
        with self.data() as p:
            data=p.architecture();self.assertEqual(len(data['links']),1);self.assertEqual(data['links'][0]['resource'],'a/catalog.json');self.assertTrue(any(item['status']=='dynamic' for item in data['observations']))

    def test_manifest_is_declared_and_evidence_goes_stale(self):
        path=self.put('app.py','def producer():return 1\ndef consumer():return 2\n');sha=hashlib.sha256(path.read_bytes()).hexdigest()
        manifest=dict(format='codegraph-manifest/1',areas=[dict(id='mcp',title='MCP',files=['app.py'],evidence=[dict(file='app.py',line=1,sha256=sha)])],resources=[dict(id='export',path='export.json',producers=[dict(file='app.py',line=1,sha256=sha)],consumers=[dict(file='app.py',line=2,sha256=sha)])])
        self.put('.code-graph-manifest.json',json.dumps(manifest));self.index()
        with self.data() as p:
            self.assertEqual(p.areas['declared:mcp']['provenance'],'manual_declaration');self.assertEqual(p.architecture()['links'][0]['status'],'declared_candidate')
        path.write_text(path.read_text()+'# moved\n');self.index()
        with self.data() as p:self.assertEqual(p.architecture()['links'][0]['status'],'stale_declaration')

    def test_partial_syntax_never_verified_dead(self):
        self.put('app.py','def orphan():return 1\ndef broken(\n');self.index()
        with self.data() as p:self.assertFalse(any(verdict=='VERIFIED_DEAD' for verdict,*_ in cg.dead_candidates(p.db,str(self.root))))

    def test_incremental_equals_full_after_new_name_and_base_change(self):
        self.put('app.py','class A:\n    def f(self):pass\nclass B(A):\n    def run(self):self.f()\n');self.index()
        self.put('new.py','class Other:\n    def f(self):pass\n');self.put('app.py','class A:\n    def f(self):pass\nclass B:\n    def run(self):self.f()\n');self.index()
        def facts():
            with self.data() as p:return [(a,b,c,line,conf) for a,b,c,line,conf in p.db.execute('SELECT fa.path,sa.qname,sb.qname,c.line,e.conf FROM edges e JOIN symbols sa ON sa.id=e.caller_id JOIN files fa ON fa.id=sa.file_id JOIN symbols sb ON sb.id=e.callee_id JOIN calls c ON c.id=e.call_id ORDER BY fa.path,sa.qname,sb.qname,c.line,e.conf')]
        incremental=facts();self.index(full=True);self.assertEqual(incremental,facts())

    def test_measurement_counts_actual_serialization_and_same_chains(self):
        self.fixture()
        with self.data() as p:
            value=p.impact(self.sid(p,'work'),compact=True);text=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
            self.assertEqual(value['measurement']['actual_chars'],len(text));self.assertEqual(value['measurement']['actual_bytes'],len(text.encode()));self.assertGreater(value['measurement']['baseline_chars'],0)

    def test_invalid_manifest_is_diagnostic_and_absolute_dotdot_is_external(self):
        self.put('app.py','def f():pass\n')
        self.put('.code-graph-manifest.json',json.dumps({'format':'codegraph-manifest/1','areas':[{'id':'bad','files':None}]}));self.index()
        with self.data() as p:
            self.assertEqual(p.architecture()['issues'][0]['status'],'invalid_manifest')
            architecture=runpy.run_path(str(REPO/'scripts/architecture.py'))
            self.assertIsNone(architecture['normalize'](str(self.root/'..'/'external.json'),self.root))

    def test_plan_reports_frontier_and_includes_target_test_itself(self):
        self.put('app.py','def f():pass\ndef caller():f()\n');self.put('tests/test_app.py','from app import caller\ndef test_deep():caller()\n');self.index()
        with self.data() as p:
            result=change['plan'](p,[self.sid(p,'f')],depth=1)
            self.assertTrue(result['broader_verification_required']);self.assertEqual(result['frontier_count'],1)
            target=change['plan'](p,[self.sid(p,'test_deep')]);self.assertEqual(target['tests'][0]['distance'],0)


    PHPUNIT_FILTER='/^(?:[^\\\\:\\s]+\\\\)*(?:{})(?: with data set .*)?\\z/s'
    PHPUNIT_ADD=['php','vendor/bin/phpunit','--filter',PHPUNIT_FILTER.format('CalcTest::testAdd'),'tests/CalcTest.php']
    def phpunit_project(self,add='return $a + $b;'):
        self.put('src/Calc.php','<?php\nnamespace App;\nfinal class Calc\n{\n    public function add(int $a, int $b): int\n    {\n        '+add+'\n    }\n}\n')
        self.put('tests/CalcTest.php','<?php\nnamespace Tests;\nuse App\\Calc;\nuse PHPUnit\\Framework\\TestCase;\nfinal class CalcTest extends TestCase\n{\n'
                 '    public function testAdd(): void { $calc = new Calc(); $this->assertSame(3, $calc->add(1, 2)); }\n}\n')
        self.put('app.py','def work():return 1\n');self.put('tests/test_app.py','from app import work\ndef test_work():return work()\n')

    def test_plan_proposes_phpunit_command_only_when_the_project_declares_it(self):
        self.phpunit_project();self.index()
        def plan():
            with self.data() as p:
                result=change['plan'](p,[self.sid(p,'Calc.add')])
            item=result['tests'][0]
            return item['command'],item['command_evidence'],result['command']
        self.assertEqual(plan()[0],None)
        self.put('phpunit.xml','<phpunit/>\n')
        self.assertIsNone(plan()[0],'configuration without vendor/bin/phpunit')
        self.put('vendor/bin/phpunit','#!/usr/bin/env php\n<?php\n')
        command,evidence,aggregate=plan()
        self.assertEqual((command,evidence),(self.PHPUNIT_ADD,['phpunit.xml','vendor/bin/phpunit']))
        self.assertEqual(aggregate,self.PHPUNIT_ADD,'one test in one file: the aggregate is the same command')
        (self.root/'phpunit.xml').unlink();self.put('phpunit.xml.dist','<phpunit/>\n')
        self.assertEqual(plan()[0],self.PHPUNIT_ADD)
        (self.root/'phpunit.xml.dist').unlink()
        self.assertIsNone(plan()[0],'binary without configuration')

    def test_phpunit_aggregate_stays_in_one_file(self):
        # Without a path PHPUnit runs only the suites in phpunit.xml: a selected file outside them would be skipped silently.
        self.phpunit_project();self.put('phpunit.xml','<phpunit/>\n');self.put('vendor/bin/phpunit','<?php\n')
        self.put('tests/CalcTest.php','<?php\nnamespace Tests;\nuse App\\Calc;\nuse PHPUnit\\Framework\\TestCase;\nfinal class CalcTest extends TestCase\n{\n'
                 '    public function testAdd(): void { $calc = new Calc(); $this->assertSame(3, $calc->add(1, 2)); }\n'
                 '    public function testZero(): void { $calc = new Calc(); $this->assertSame(0, $calc->add(0, 0)); }\n}\n')
        self.index()
        with self.data() as p:result=change['plan'](p,[self.sid(p,'Calc.add')])
        self.assertEqual(result['command'],['php','vendor/bin/phpunit','--filter',self.PHPUNIT_FILTER.format('CalcTest::testAdd|CalcTest::testZero'),'tests/CalcTest.php'])
        self.put('tests/IntegrationCalcTest.php','<?php\nnamespace Tests;\nuse App\\Calc;\nuse PHPUnit\\Framework\\TestCase;\nfinal class IntegrationCalcTest extends TestCase\n{\n'
                 '    public function testAdd(): void { $calc = new Calc(); $this->assertSame(4, $calc->add(2, 2)); }\n}\n')
        self.index()
        with self.data() as p:result=change['plan'](p,[self.sid(p,'Calc.add')])
        commands={item['symbol']['file']:item['command'] for item in result['tests']}
        self.assertEqual(commands['tests/IntegrationCalcTest.php'][-1],'tests/IntegrationCalcTest.php')
        self.assertIsNone(result['command']);self.assertIn('one test path per run',result['command_reason'])

    def test_phpunit_filter_matches_only_the_selected_full_name(self):
        pattern=re.compile(self.PHPUNIT_FILTER.format('CalcTest::testAdd')[1:-2].replace('\\z','\\Z'),re.S)
        for name in ('CalcTest::testAdd','Tests\\Unit\\CalcTest::testAdd','Tests\\CalcTest::testAdd with data set "one"','Tests\\CalcTest::testAdd with data set #0','Équipe\\Unit\\CalcTest::testAdd','Tests\\CalcTest::testAdd with data set "line\nbreak"'):
            self.assertRegex(name,pattern)
        for name in ('Tests\\CalcTest::testAddress','Tests\\OtherCalcTest::testAdd','Tests\\CalcTest::testUnselected with data set "CalcTest::testAdd"','Tests\\CalcTest::testAdd\n'):
            self.assertNotRegex(name,pattern)

    def test_phpunit_binary_must_resolve_inside_the_project(self):
        self.phpunit_project();self.put('phpunit.xml','<phpunit/>\n');self.index()
        with tempfile.TemporaryDirectory() as outside:
            Path(outside,'phpunit').write_text('<?php\n');(self.root/'vendor/bin').mkdir(parents=True)
            (self.root/'vendor/bin/phpunit').symlink_to(Path(outside,'phpunit'))
            with self.data() as p:self.assertIsNone(change['plan'](p,[self.sid(p,'Calc.add')])['tests'][0]['command'])
        (self.root/'vendor/bin/phpunit').unlink();self.put('vendor/phpunit/phpunit/phpunit','<?php\n')
        (self.root/'vendor/bin/phpunit').symlink_to('../phpunit/phpunit/phpunit')
        with self.data() as p:self.assertEqual(change['plan'](p,[self.sid(p,'Calc.add')])['tests'][0]['command'],self.PHPUNIT_ADD)

    def test_review_commands_come_from_the_reviewed_checkout(self):
        self.git('init','-q');self.git('config','user.name','Synthetic');self.git('config','user.email','synthetic@example.invalid')
        self.put('.gitignore','.code-graph/\nvendor/\n');self.phpunit_project();self.put('phpunit.xml','<phpunit/>\n');self.put('vendor/bin/phpunit','<?php\n')
        self.git('add','.');self.git('commit','-qm','fixture')
        self.phpunit_project(add='return $b + $a;');self.index()
        tests=lambda result:{item['symbol']['name']:item['command'] for item in result['verification']['tests']}
        self.assertEqual(tests(change['review'](cg,self.review_args(base='HEAD'))),{'CalcTest.testAdd':self.PHPUNIT_ADD})
        # The staged snapshot is a temporary copy without the ignored vendor/: the command still belongs to this checkout.
        self.git('add','src/Calc.php')
        self.assertEqual(tests(change['review'](cg,self.review_args(base='HEAD',staged=True))),{'CalcTest.testAdd':self.PHPUNIT_ADD})

    def cli(self,*args):
        result=subprocess.run([sys.executable,'-I',str(REPO/'scripts/cg.py'),'--root',str(self.root),*args],capture_output=True,text=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stderr);return result.stdout

    def test_review_summary_keeps_counts_new_candidates_tests_and_limits(self):
        self.git('init','-q');self.git('config','user.name','Synthetic');self.git('config','user.email','synthetic@example.invalid')
        self.put('.gitignore','.code-graph/\nvendor/\n');self.phpunit_project();self.put('phpunit.xml','<phpunit/>\n');self.put('vendor/bin/phpunit','<?php\n')
        self.put('defects.py','def scale(x):return x\ndef old_call():return scale()\ndef kept_call():return scale()\n')
        self.git('add','.');self.git('commit','-qm','fixture');head=self.git('rev-parse','HEAD').decode().strip()
        self.phpunit_project(add='return $b + $a;');self.put('app.py','def work():return 2\n')
        self.put('defects.py','def scale(x):return x\ndef kept_call():return scale()\ndef new_call(a):return scale(a, a)\n');self.index()
        full=self.cli('review','--base','HEAD','--json')
        compact=self.cli('review','--base','HEAD','--summary','--json');value=json.loads(compact)
        self.assertLess(len(compact),len(full))
        self.assertEqual(set(value),{'format','engine_version','base','revision','counts','new','tests','command','command_reason','cwd','limitations','output'})
        self.assertEqual(value['format'],'codegraph-review-summary/1')
        self.assertEqual((value['base']['ref'],value['base']['commit'],value['base']['status']),('HEAD',head,'available'))
        self.assertEqual(value['revision'],dict(content_revision=json.loads(full)['content_revision'],scope='working_tree'))
        self.assertEqual(value['counts'],dict(new=1,persistent=1,resolved=1,uncompared=0))
        self.assertEqual([(item['rule_id'],item['file'],item['line']) for item in value['new']],[('arity','defects.py',3)])
        self.assertIn('scale',value['new'][0]['message'])
        self.assertEqual({item['name']:item['command'] for item in value['tests']}.get('CalcTest.testAdd'),self.PHPUNIT_ADD)
        self.assertEqual(next(item for item in value['tests'] if item['name']=='CalcTest.testAdd')['file'],'tests/CalcTest.php')
        self.assertEqual(value['output']['status'],'complete')
        budget=len(compact)-50;partial=self.cli('review','--base','HEAD','--summary','--json','--max-chars',str(budget))
        self.assertLessEqual(len(partial.rstrip('\n')),budget);partial=json.loads(partial)
        self.assertEqual((partial['output']['status'],partial['counts'],len(partial['new'])),('partial',value['counts'],1))
        self.assertEqual(partial['output']['omitted_records']['tests'],len(value['tests'])-len(partial['tests']))
        self.assertGreater(partial['output']['omitted_records']['tests'],0)
        text=self.cli('review','--base','HEAD','--summary')
        self.assertFalse(text.lstrip().startswith('{'))
        self.assertIn('Findings: new 1',text)
        unavailable=json.loads(self.cli('review','--summary','--json'))
        self.assertEqual((unavailable['base']['status'],unavailable['counts']['new'],unavailable['new']),('absent',0,[]))
        self.assertIn('Comparison unavailable',unavailable['limitations'][0])

    def git(self,*args):return subprocess.check_output(['git','-c','core.fsmonitor=false','-C',str(self.root),*args],stderr=subprocess.DEVNULL)
    def git_fixture(self):
        self.git('init','-q');self.git('config','user.name','Synthetic');self.git('config','user.email','synthetic@example.invalid')
        self.put('.gitignore','.code-graph/\n');self.put('app.py','def work(x):return x\ndef caller():return work()\n');self.git('add','.');self.git('commit','-qm','fixture');self.index()
    def review_args(self,**kwargs):
        return argparse.Namespace(root=str(self.root),base=kwargs.get('base'),staged=kwargs.get('staged',False),baseline=kwargs.get('baseline'),save_baseline=kwargs.get('save_baseline'),force=False,max_chars=100000,min_conf='medium',depth=3)

    def review_source_fixture(self):
        self.git_fixture()
        self.put('app.py','def alpha():\n    return 1\n\ndef beta():\n    return 2\n')
        self.git('add','app.py');self.git('commit','-qm','source fixture');self.index()

    def test_review_rejects_stale_source_before_mapping_changed_and_new_functions(self):
        self.review_source_fixture()
        self.put('app.py','def added():\n    return 3\n\ndef alpha():\n    return 10\n\ndef beta():\n    return 2\n')
        database=self.root/'.code-graph/graph.db';before=database.read_bytes()
        with self.assertRaisesRegex(ValueError,'index'):
            change['review'](cg,self.review_args(base='HEAD'))
        staged=change['review'](cg,self.review_args(base='HEAD',staged=True))
        self.assertEqual((staged['scope'],staged['symbols']),('git_index',[]))
        self.assertEqual(database.read_bytes(),before)
        self.index()
        result=change['review'](cg,self.review_args(base='HEAD'))
        self.assertEqual({item['name'] for item in result['symbols']},{'added','alpha'})

    def test_review_rejects_unindexed_new_source_files(self):
        self.review_source_fixture();self.put('new.py','def newly_added():return 3\n')
        with self.assertRaisesRegex(ValueError,'index'):
            change['review'](cg,self.review_args(base='HEAD'))
        self.index()
        result=change['review'](cg,self.review_args(base='HEAD'))
        self.assertEqual({item['name'] for item in result['symbols']},{'newly_added'})

    def test_review_rejects_deleted_indexed_sources(self):
        self.review_source_fixture();(self.root/'app.py').unlink()
        with self.assertRaisesRegex(ValueError,'index'):
            change['review'](cg,self.review_args(base='HEAD'))
        self.index()
        result=change['review'](cg,self.review_args(base='HEAD'))
        self.assertEqual(result['symbols'],[])
        self.assertTrue(any(item['file']=='app.py' and item['count']==0 for item in result['changes']))

    def test_review_freshness_uses_the_index_source_exclusions(self):
        self.review_source_fixture()
        self.put('generated.min.js','function generated(){return 1;}\n')
        self.put('large.py','x'*(cg.MAX_BYTES+1))
        self.put('dense.py','x'*20001)
        self.put('notes.txt','This is not an indexed source.\n')
        result=change['review'](cg,self.review_args(base='HEAD'))
        self.assertEqual(result['symbols'],[])

    def test_review_rejects_changed_engine_fingerprint(self):
        self.review_source_fixture();inputs=cg.analysis_inputs(self.root)
        with patch.object(cg,'analysis_inputs',return_value=(*inputs[:2],'changed')):
            with self.assertRaisesRegex(ValueError,'index'):
                change['review'](cg,self.review_args(base='HEAD'))

    def test_review_without_baseline_does_not_claim_everything_new(self):
        self.put('app.py','def work(x):return x\ndef caller():return work()\n');self.index()
        result=change['review'](cg,self.review_args());self.assertEqual(result['comparison']['status'],'unavailable');self.assertEqual(result['summary']['new'],0)

    def test_review_persists_finding_when_only_lines_move(self):
        self.git_fixture();self.put('app.py','\n\ndef work(x):return x\ndef caller():return work()\n');self.index()
        result=change['review'](cg,self.review_args(base='HEAD'));self.assertEqual(result['summary']['persistent'],1);self.assertEqual(result['summary']['new'],0)

    def test_review_staged_analyzes_index_not_worktree(self):
        self.git_fixture();self.put('app.py','def work(x):return x\ndef caller():return work(1)\n');self.git('add','app.py')
        self.put('app.py','def work(x):return x\ndef caller():return work()\n');self.index()
        result=change['review'](cg,self.review_args(base='HEAD',staged=True));self.assertEqual(result['scope'],'git_index');self.assertEqual(result['summary']['resolved'],1);self.assertEqual(result['summary']['persistent'],0)

    def test_saved_baseline_requires_compatible_engine_and_project(self):
        self.git_fixture();change['review'](cg,self.review_args(save_baseline='.code-graph/baseline.json'))
        path=self.root/'.code-graph/baseline.json';saved=json.loads(path.read_text());saved['engine_version']='incompatible';path.write_text(json.dumps(saved))
        result=change['review'](cg,self.review_args(baseline='.code-graph/baseline.json'));self.assertEqual(result['comparison']['status'],'unavailable');self.assertEqual(result['summary']['new'],0)

    def test_deleted_files_are_reported_without_invented_current_symbols(self):
        self.git_fixture();(self.root/'app.py').unlink();self.index()
        result=change['review'](cg,self.review_args(base='HEAD'));self.assertTrue(any(item['file']=='app.py' and item['count']==0 for item in result['changes']));self.assertEqual(result['symbols'],[])

    def test_default_review_maps_worktree_and_unicode_paths(self):
        self.git_fixture()
        self.put('città.py','def work():return 1\n');self.put('tests/test_citta.py','from città import work\ndef test_entry():return work()\n');self.git('add','.');self.git('commit','-qm','unicode fixture')
        self.put('città.py','def work():return 2\n');self.index()
        result=change['review'](cg,self.review_args())
        self.assertTrue(any(item['file']=='città.py' for item in result['symbols']))
        self.assertTrue(any(item['symbol']['name']=='test_entry' for item in result['verification']['tests']))
        self.assertEqual(result['comparison']['status'],'unavailable')

    def test_deleted_symbol_retains_baseline_test_plan(self):
        self.git_fixture();self.put('tests/test_app.py','from app import work\ndef test_work():return work(1)\n');self.git('add','.');self.git('commit','-qm','test fixture');self.index()
        (self.root/'app.py').unlink();self.index()
        result=change['review'](cg,self.review_args(base='HEAD'))
        self.assertTrue(result['verification']['broader_verification_required'])
        self.assertTrue(any(item['symbol']['name']=='test_work' and item.get('basis')=='baseline' for item in result['verification']['tests']))

    def test_staged_review_saves_staged_baseline(self):
        self.git_fixture();self.put('app.py','def work(x):return x\ndef caller():return work(1)\n');self.git('add','app.py')
        change['review'](cg,self.review_args(base='HEAD',staged=True,save_baseline='.code-graph/staged.json'))
        saved=json.loads((self.root/'.code-graph/staged.json').read_text());self.assertEqual(saved['scope'],'git_index');self.assertFalse(any(item['rule_id']=='arity' for item in saved['findings']))

    def test_only_untracked_files_are_in_default_review(self):
        self.git_fixture();self.put('new.py','def added():return 1\n');self.index()
        result=change['review'](cg,self.review_args())
        self.assertTrue(any(item['file']=='new.py' for item in result['symbols']))


if __name__=='__main__':unittest.main()
