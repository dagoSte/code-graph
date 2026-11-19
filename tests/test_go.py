"""Go's declared static subset: real parsing/indexing, no Go or project execution."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_go_tests', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
ProjectData = runpy.run_path(str(REPO / 'scripts/project_data.py'))['ProjectData']


class Go(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cg-go-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'project'
        self.root.mkdir()

    def put(self, path, source):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
        return target

    def index(self, full=False):
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(self.root), full=full))

    def rows(self, sql):
        with contextlib.closing(sqlite3.connect(self.root / '.code-graph/graph.db')) as db:
            return db.execute(sql).fetchall()

    def edges(self):
        return set(self.rows('''SELECT f.path,a.qname,g.path,b.qname,e.conf FROM edges e
            JOIN symbols a ON a.id=e.caller_id JOIN symbols b ON b.id=e.callee_id
            JOIN files f ON f.id=a.file_id JOIN files g ON g.id=b.file_id'''))

    def targets(self, caller):
        return {(path, target) for _, owner, path, target, _ in self.edges() if owner == caller}

    def basic(self):
        shutil.copytree(REPO / 'tests/fixtures/go/basic', self.root, dirs_exist_ok=True)
        self.index()

    def test_package_functions_import_alias_and_receiver_across_files(self):
        self.basic()
        self.assertEqual(self.targets('Run'), {('app.go','helper'),('app.go','after'),
                         ('types.go','Repo.Save'),('lib/ops.go','Export')})
        self.assertEqual(self.targets('Typed'), {('types.go','Repo.Save'),('types.go','Repo.Value')})
        self.assertEqual(self.targets('Indirect'), set())
        self.assertTrue(all(conf == 'high' for *_, conf in self.edges()))
        self.assertEqual(dict(self.rows("SELECT qname,kind FROM symbols WHERE qname IN ('Repo','Runner')")),
                         dict(Repo='class', Runner='interface'))
        with contextlib.closing(cg.connect(self.root)) as db:
            self.assertNotIn('init', [cg.fmt(db, sid).split()[-1] for _, sid, _, _ in cg.dead_candidates(db, self.root)])
        self.put('method.go','package app\nfunc(r Repo) init(){}\n')
        self.index()
        with contextlib.closing(cg.connect(self.root)) as db:
            names=[db.execute('SELECT qname FROM symbols WHERE id=?',(sid,)).fetchone()[0]
                   for _,sid,_,_ in cg.dead_candidates(db,self.root,verify=False)]
            self.assertIn('Repo.init',names)
            self.assertNotIn('init',names)

    def test_method_owner_cross_file_reordering_and_deleted_type(self):
        self.put('a.go','package app\nfunc(r *Repo) Save(){ helper() }\n')
        type_path=self.put('z.go','package app\ntype Repo struct{}\nfunc helper(){}\n')
        self.index()
        query='''SELECT s.qname,p.kind,f.path FROM symbols s JOIN symbols p ON p.id=s.parent_id
                 JOIN files f ON f.id=p.file_id WHERE s.kind='method' '''
        self.assertEqual(self.rows(query),[('Repo.Save','class','z.go')])
        self.assertEqual(self.targets('Repo.Save'),{('z.go','helper')})
        type_path.unlink()
        self.index()
        self.assertEqual(self.rows(query),[('Repo.Save','module','a.go')])
        self.assertEqual(self.targets('Repo.Save'),set())
        self.index(full=True)
        self.assertEqual(self.rows(query),[('Repo.Save','module','a.go')])

    def test_default_import_uses_declared_package_name_and_export_rules(self):
        self.put('go.mod', 'module example.com/app\ngo 1.23\n')
        self.put('lib/a.go', 'package actual\nfunc Export(){}; func private(){}\n')
        self.put('a.go', 'package app\nimport "example.com/app/lib"\n'
                 'func Entry(){ actual.Export(); lib.Export(); actual.private() }\n')
        self.index()
        self.assertEqual(self.targets('Entry'), {('lib/a.go','Export')})

    def test_import_lookup_does_not_rescan_unrelated_files_and_keeps_guards(self):
        self.put('go.mod','module example.com/app\ngo 1.23\n')
        self.put('a.go','package app\nimport "example.com/app/lib"\n'
                 'func Entry(){actual.Export()};var Ref=actual.Export\n')
        self.put('lib/only_test.go','package actual\nfunc Export(){}\n')
        self.put('lib/excluded_linux.go','package actual\nfunc Export(){}\n')
        for i in range(80):
            self.put(f'other{i}/a.go','package actual\nfunc Export(){}\n')
        self.put('other0/go.mod','module example.com/other\ngo 1.23\n')
        self.index()
        self.assertEqual(self.targets('Entry'),set())
        self.assertEqual(self.rows('SELECT target_id FROM symbol_refs WHERE name="Export"'),[(None,)])
        self.put('lib/a.go','package actual\nfunc Export(){}\n')
        self.index()
        self.assertEqual(self.targets('Entry'),{('lib/a.go','Export')})
        with contextlib.closing(sqlite3.connect(self.root/'.code-graph/graph.db')) as db:
            packages=cg.LANGUAGE_ADAPTERS['go']['GoPackages'](db)
            fid,owner=db.execute("SELECT file_id,id FROM symbols WHERE qname='Entry'").fetchone()
            with patch.object(packages,'package',wraps=packages.package) as reads:
                for _ in range(3):
                    self.assertEqual(len(packages.targets(fid,owner,'Export','actual')),1)
                self.assertLessEqual(reads.call_count,12,'Import lookup rescanned unrelated package files')
            packages.locals[owner]['blocked'].append('actual')
            self.assertEqual(packages.targets(fid,owner,'Export','actual'),[])
        marker=self.put('lib/go.mod','module example.com/foreign\ngo 1.23\n')
        self.index()
        self.assertEqual(self.targets('Entry'),set())
        self.assertEqual(self.rows('SELECT target_id FROM symbol_refs WHERE name="Export"'),[(None,)])
        marker.unlink()
        for full in (False,True):
            self.index(full=full)
            self.assertEqual(self.targets('Entry'),{('lib/a.go','Export')})
            self.assertTrue(all(target is not None for target, in self.rows('SELECT target_id FROM symbol_refs WHERE name="Export"')))

    def test_external_test_package_in_imported_directory_does_not_make_alias_ambiguous(self):
        self.put('go.mod','module example.com/app\ngo 1.23\n')
        self.put('a.go','package app\nimport x "example.com/app/lib"\nfunc Entry(){x.Export()}\n')
        self.put('lib/a.go','package actual\nfunc Export(){}\n')
        self.put('lib/a_test.go','package actual_test\nfunc Export(){}\n')
        self.index()
        self.assertEqual(self.targets('Entry'), {('lib/a.go','Export')})

    def test_receiver_leading_comment_preserves_cross_file_owner_and_target(self):
        self.put('a.go', 'package app\nfunc ( /*doc*/ r *Repo) Save() {}\n'
                 'func Entry(r Repo){ r.Save() }\n')
        self.put('z.go', 'package app\ntype Repo struct{}\n')
        query = '''SELECT s.qname,p.qname,p.kind FROM symbols s
                   JOIN symbols p ON p.id=s.parent_id WHERE s.kind='method' '''
        for full in (False, True):
            self.index(full=full)
            self.assertEqual(self.rows(query), [('Repo.Save','Repo','class')])
            self.assertEqual(self.targets('Entry'), {('a.go','Repo.Save')})

    def test_packages_languages_test_variants_and_missing_names_never_fallback(self):
        self.put('a.go', 'package app\nfunc Entry(){ leaf(); testOnly(); Missing() }; func leaf(){}\n')
        self.put('a_test.go', 'package app\nfunc testOnly(){}; func TestEntry(){ Entry(); testOnly() }\n')
        self.put('other/a.go', 'package app\nfunc Missing(){}; func leaf(){}\n')
        self.put('x_test.go', 'package app_test\nfunc External(){ leaf() }\n')
        self.put('a.py', 'def Missing(): pass\ndef PythonEntry(): Missing()\n')
        self.index()
        self.assertEqual(self.targets('Entry'), {('a.go','leaf')})
        self.assertEqual(self.targets('External'), set())
        self.assertEqual(self.targets('TestEntry'), {('a.go','Entry'),('a_test.go','testOnly')})
        self.assertEqual(self.targets('PythonEntry'), {('a.py','Missing')})

    def test_function_parameter_local_block_and_closure_shadowing(self):
        self.put('a.go', 'package app\nfunc leaf(){}; func Plain(){ leaf() }\n'
                 'func Param(leaf func()){ leaf() }\nfunc Local(){ leaf:=func(){}; leaf() }\n'
                 'func Block(){ { var leaf func(); leaf() }; leaf() }\n'
                 'func Closure(){ _=func(leaf func()){ leaf() }; leaf() }\n')
        self.index()
        self.assertEqual(self.targets('Plain'), {('a.go','leaf')})
        for caller in ('Param','Local','Block','Closure'):
            self.assertEqual(self.targets(caller), set(), caller)

    def test_alias_shadowing_dot_import_and_concrete_external_receiver(self):
        self.put('go.mod', 'module example.com/app\ngo 1.23\n')
        self.put('lib/a.go', 'package lib\ntype Repo struct{}; func(r *Repo) Save(){}; func Export(){}\n')
        self.put('a.go', 'package app\nimport alias "example.com/app/lib"\n'
                 'func Shadow(alias struct{Export func()}){ alias.Export() }\n'
                 'func Concrete(r *alias.Repo){ r.Save() }\n')
        self.put('dot.go', 'package app\nimport . "example.com/app/lib"\nfunc Dot(){ Export() }\n')
        self.index()
        self.assertEqual(self.targets('Shadow'), set())
        self.assertEqual(self.targets('Dot'), set())
        self.assertEqual(self.targets('Concrete'), {('lib/a.go','Repo.Save')})

    def test_promoted_interfaces_aliases_generic_receivers_and_shadowed_types_abstain(self):
        self.put('a.go', 'package app\ntype Base struct{}; func(b Base) Save(){}\n'
                 'type Embed struct{ Base }; type Contract interface{ Save() }; type Alias = Base\n'
                 'type Generic[T any] struct{}; func(g Generic[T]) Save(){}\n'
                 'func Promoted(e Embed){ e.Save() }; func ViaInterface(e Contract){ e.Save() }\n'
                 'func Aliased(e Alias){ e.Save() }; func GenericRecv(e Generic[int]){ e.Save() }\n'
                 'func TypeParam[Base any](e Base){ e.Save() }\n'
                 'func Expression(e Base){ Base.Save(e) }\n')
        self.index()
        for caller in ('Promoted','ViaInterface','Aliased','GenericRecv','TypeParam','Expression'):
            self.assertEqual(self.targets(caller), set(), caller)

    def test_generic_receiver_parameters_shadow_package_functions(self):
        self.put('a.go','package app\ntype Box[T ~int] struct{}\n'
                 'func T(n int) int { return 999 }\n'
                 'func(b Box[T]) Convert() T { return T(1) }\n'
                 'func Use() int { return T(1) }\n')
        self.index()
        self.assertEqual(self.targets('Box.Convert'),set())
        self.assertEqual(self.targets('Use'),{('a.go','T')})

    def test_generics_grammar_ambiguity_conversions_and_function_value_references(self):
        self.put('a.go', 'package app\ntype Number int\n'
                 'func Identity[T any](a T) T{return a}; func Pair[A,B any](a A) A{return a}\n'
                 'func leaf(){}; func Use(){ _=Identity[int](1); _=Pair[int,string](1); _=Number(1); f:=leaf; f() }\n')
        self.index()
        self.assertEqual(self.targets('Use'), {('a.go','Identity'),('a.go','Pair')})
        refs = self.rows('''SELECT a.qname,b.qname FROM symbol_refs r JOIN symbols a ON a.id=r.owner_id
                            JOIN symbols b ON b.id=r.target_id''')
        self.assertIn(('Use','leaf'), refs)
        self.assertNotIn(('Use','Number'), refs)

    def test_function_references_cross_files_and_imported_aliases(self):
        self.put('go.mod','module example.com/app\ngo 1.23\n')
        self.put('a.go','package app\nimport lib "example.com/app/lib"\nfunc Use(){ _=Export; _=lib.Send }\n')
        self.put('b.go','package app\nfunc Export(){}\n')
        self.put('lib/a.go','package remote\nfunc Send(){}\n')
        self.index()
        refs=self.rows('''SELECT a.qname,f.path,b.qname FROM symbol_refs r
            JOIN symbols a ON a.id=r.owner_id JOIN symbols b ON b.id=r.target_id
            JOIN files f ON f.id=b.file_id''')
        self.assertEqual(set(refs),{('Use','b.go','Export'),('Use','lib/a.go','Send')})
        self.assertEqual(self.targets('Use'),set())

    def test_package_initializers_collect_calls_and_function_references(self):
        self.put('a.go','package app\nvar Value=leaf()\nvar Ref=leaf\nfunc leaf()int{return 1}\n')
        self.put('shadow.go','package app\nfunc decoy(){}\nvar Callback=func(decoy func()){ decoy() }\n')
        self.index()
        self.assertEqual(self.targets('a.go'),{('a.go','leaf')})
        self.assertEqual(self.targets('shadow.go'),set())
        self.assertEqual(self.rows('''SELECT a.qname,b.qname FROM symbol_refs r
            JOIN symbols a ON a.id=r.owner_id JOIN symbols b ON b.id=r.target_id'''),[('a.go','leaf')])

    def test_nested_callees_keep_argument_and_function_literal_references(self):
        self.put('a.go','package app\nfunc leaf(){}\nfunc wrap(f func())func(){return f}\n'
                 'func Use(){ wrap(leaf)(); (func(){ _=leaf })() }\n')
        self.index()
        self.assertEqual(self.targets('Use'),{('a.go','wrap')})
        refs=self.rows('''SELECT a.qname,b.qname FROM symbol_refs r
            JOIN symbols a ON a.id=r.owner_id JOIN symbols b ON b.id=r.target_id''')
        self.assertEqual(refs,[('Use','leaf'),('Use','leaf')])

    def test_go_defer_argument_counts_variadic_and_grouped_parameters(self):
        self.put('a.go', 'package app\nfunc Leaf(a,b int, rest ...string){}\n'
                 'func Entry(xs []string){ go Leaf(1,2); defer Leaf(1,2,xs...) }\n')
        self.index()
        self.assertEqual(self.targets('Entry'), {('a.go','Leaf')})
        self.assertEqual(self.rows("SELECT pmin,pmax FROM symbols WHERE name='Leaf'"), [(2,None)])
        self.assertEqual(self.rows("SELECT nargs,splat FROM calls WHERE name='Leaf' ORDER BY id"), [(2,0),(3,1)])

    def test_conditional_and_partial_sources_remain_indexed_without_resolved_edges(self):
        self.put('a.go', 'package app\nfunc Entry(){ platform(); tagged(); broken() }\n')
        self.put('a_linux.go', 'package app\nfunc platform(){ Entry() }\n')
        self.put('tag.go', '//go:build custom\n\npackage app\nfunc tagged(){ Entry() }\n')
        self.put('broken.go', 'package app\nfunc broken(){ Entry(); type Incomplete\n')
        self.index()
        self.assertEqual(self.edges(), set())
        self.assertEqual(len(self.rows('SELECT * FROM files')), 4)
        self.assertEqual(dict(self.rows('SELECT k,v FROM meta'))['partial_files'], '1')

    def test_filename_rules_cgo_and_interface_declarations(self):
        self.put('a.go', 'package app\nfunc Entry(){ ignored(); hidden(); cgoOnly(); cgoEscape(); normal() }\n'
                 'type Contract interface { Save(int) }\n')
        self.put('_ignored.go', 'package app\nfunc ignored(){}\n')
        self.put('.hidden.go', 'package app\nfunc hidden(){}\n')
        self.put('cgo.go', 'package app\nimport "C"\nfunc cgoOnly(){}\n')
        self.put('escaped.go', 'package app\nimport "\\x43"\nfunc cgoEscape(){}\n')
        self.put('use_linux_impl.go', 'package app\nfunc normal(){}\n')
        self.index()
        self.assertEqual(self.targets('Entry'), {('use_linux_impl.go','normal')})
        self.assertEqual(self.rows("SELECT kind,pmin FROM symbols WHERE qname='Contract.Save'"), [('method',1)])

    def test_explicit_receiver_types_preserve_whitespace_and_comments(self):
        self.put('a.go','package app\ntype Repo struct{}\n'
                 'func(r * /* receiver */ Repo) Save(){}\n'
                 'func Direct(r * /* parameter */ Repo){ r.Save() }\n'
                 'func Local(){ var r /* local */ Repo; r.Save() }\n')
        self.index()
        self.assertEqual(self.targets('Direct'),{('a.go','Repo.Save')})
        self.assertEqual(self.targets('Local'),{('a.go','Repo.Save')})

    def test_literal_receivers_keep_calls_and_method_values(self):
        source='package app\ntype Repo struct{}; func(r *Repo) Save(){}\n'
        variants={'Value':'r:=Repo{}','Pointer':'r:=& /* pointer */ Repo{}',
                  'Variable':'var r=Repo{}','Wrapped':'r:=((&Repo{}))'}
        source+='\n'.join('func '+name+'(){'+declaration+';r.Save();_=r.Save}' for name,declaration in variants.items())
        self.put('a.go',source)
        for full in (False,True):
            self.index(full=full)
            for name in variants:self.assertEqual(self.targets(name),{('a.go','Repo.Save')})
            self.assertEqual(set(self.rows('''SELECT a.qname,b.qname FROM symbol_refs r
                JOIN symbols a ON a.id=r.owner_id JOIN symbols b ON b.id=r.target_id''')),
                {(name,'Repo.Save') for name in variants})

    def test_multiple_initializers_do_not_lend_types_to_other_values(self):
        self.put('a.go','package app\ntype Repo struct{};func(r Repo) Save(){}\n'
                 'type Tiny struct{};func(t *Tiny) Save(){}\nfunc build()Repo{return Repo{}}\n'
                 'func Multiple(){r,p,q,_:=Repo{},&Tiny{},build(),Repo{};r.Save();p.Save();q.Save()}\n')
        self.index()
        self.assertEqual(set(self.rows('''SELECT c.recv_name,b.qname FROM edges e JOIN calls c ON c.id=e.call_id
            JOIN symbols a ON a.id=e.caller_id JOIN symbols b ON b.id=e.callee_id WHERE a.qname='Multiple' ''')),
            {('','build'),('r','Repo.Save'),('p','Tiny.Save')})

    def test_explicit_interface_types_are_not_replaced_by_literal_types(self):
        self.put('a.go','package app\ntype Repo struct{};func(r *Repo) Save()int{return 1}\n'
                 'type Other struct{};func(r *Other) Save()int{return 2};type Contract interface{Save()int}\n'
                 'func Anonymous(){var r interface{Save()int}=&Repo{};r=&Other{};_=r.Save()}\n'
                 'func Named(){var r Contract=&Repo{};r=&Other{};_=r.Save()}\n'
                 'func Control(){r:=Repo{};_=r.Save()}\n')
        self.index()
        self.assertEqual(self.targets('Anonymous'),set());self.assertEqual(self.targets('Named'),set())
        self.assertEqual(self.targets('Control'),{('a.go','Repo.Save')})

    def test_control_headers_and_nested_bindings_do_not_leak_receiver_types(self):
        self.put('a.go','package app\ntype Repo struct{};func(r Repo) Save()int{return 1}\n'
                 'var r=struct{Save func()int}{func()int{return 2}}\n'
                 'func HeaderIf()int{if r:=(Repo{});true{_=r.Save()};return r.Save()}\n'
                 'func HeaderFor()int{for r:=(Repo{});false;{_=r.Save()};return r.Save()}\n'
                 'func HeaderSwitch()int{switch r:=(Repo{});{case false:_=r.Save()};return r.Save()}\n'
                 'func Inner(){{r:=Repo{};_=r.Save()}}\n'
                 'func Lambda(){_=func(){r:=Repo{};_=r.Save()}}\n'
                 'func Captured(){r:=Repo{};_=func(){_=r.Save()}}\n')
        self.index()
        for name in ('HeaderIf','HeaderFor','HeaderSwitch','Inner','Lambda'):
            self.assertEqual(self.targets(name),set(),name)
        self.assertEqual(self.targets('Captured'),{('a.go','Repo.Save')})

    def test_literal_aliases_generics_and_shadowed_type_names_still_abstain(self):
        self.put('a.go','package app\ntype Repo struct{};func(r Repo) Save(){}\n'
                 'type Alias=Repo;type Generic[T any]struct{};func(g Generic[T]) Save(){}\n'
                 'func Local(){type Repo struct{Save func()};r:=Repo{func(){}};r.Save()}\n'
                 'func Aliased(){r:=Alias{};r.Save()};func GenericLiteral(){r:=Generic[int]{};r.Save()}\n'
                 'func TypeParam[Repo interface{~struct{};Save()}](){r:=Repo{};r.Save()}\n'
                 'func Control(){r:=Repo{};r.Save()}\n')
        self.index()
        for name in ('Local','Aliased','GenericLiteral','TypeParam'):self.assertEqual(self.targets(name),set(),name)
        self.assertEqual(self.targets('Control'),{('a.go','Repo.Save')})

    def test_imported_literal_types_keep_module_boundary_and_reference_rules(self):
        self.put('go.mod','module example.com/app\ngo 1.23\n')
        self.put('lib/a.go','package lib\ntype Repo struct{};func(r *Repo) Save(){}\n')
        self.put('a.go','package app\nimport lib "example.com/app/lib"\n'
                 'func Imported(){r:=lib.Repo{};r.Save();_=r.Save}\n')
        self.index();self.assertEqual(self.targets('Imported'),{('lib/a.go','Repo.Save')})
        refs='''SELECT a.qname,b.qname FROM symbol_refs r JOIN symbols a ON a.id=r.owner_id
                JOIN symbols b ON b.id=r.target_id'''
        self.assertEqual(self.rows(refs),[('Imported','Repo.Save')])
        self.put('go.mod','module example.com/changed\ngo 1.23\n')
        for full in (False,True):
            self.index(full=full);self.assertEqual(self.targets('Imported'),set());self.assertEqual(self.rows(refs),[])

    def test_predeclaration_and_initializer_uses_do_not_inherit_local_receiver_types(self):
        self.put('a.go','package app\ntype Repo struct{p func()int};func(r Repo) Save()int{return 1}\n'
                 'type Other struct{};func(r Other) Save()int{return 2};var r=Other{}\n'
                 'func Before()int{a:=r.Save();r:=Repo{};return a+r.Save()}\n'
                 'func TypedBefore()int{a:=r.Save();var r Repo;return a+r.Save()}\n'
                 'func Initializer()int{r:=Repo{p:r.Save};return r.p()+r.Save()}\n'
                 'func VarInitializer()int{var r=Repo{p:r.Save};return r.p()+r.Save()}\n'
                 'func TypedInitializer()int{var r Repo=Repo{p:r.Save};return r.p()+r.Save()}\n'
                 'func MultipleInitializer()int{r,p:=Repo{},Repo{p:r.Save};return r.Save()+p.p()}\n'
                 'func CaptureBefore()int{f:=func()int{return r.Save()};r:=Repo{};return f()+r.Save()}\n'
                 'func Control(){r:=Repo{};_=r.Save();_=r.Save}\n')
        for full in (False,True):
            self.index(full=full)
            for name in ('Before','TypedBefore','Initializer','VarInitializer','TypedInitializer','MultipleInitializer','CaptureBefore'):
                self.assertEqual(self.targets(name),set(),name)
                self.assertEqual(self.rows('''SELECT b.qname FROM symbol_refs r JOIN symbols a ON a.id=r.owner_id
                    JOIN symbols b ON b.id=r.target_id WHERE a.qname='''+"'"+name+"'"),[],name)
            self.assertEqual(self.targets('Control'),{('a.go','Repo.Save')})

    def test_literal_type_changes_replace_calls_and_references_incrementally(self):
        self.put('types.go','package app\ntype Repo struct{};func(r Repo) Save(){}\n'
                 'type Tiny struct{};func(t Tiny) Save(){}\n')
        for name in ('Repo','Tiny'):
            self.put('a.go','package app\nfunc Entry(){r:='+name+'{};r.Save();_=r.Save}\n')
            self.index();self.assertEqual(self.targets('Entry'),{('types.go',name+'.Save')})
            refs='''SELECT a.qname,b.qname FROM symbol_refs r JOIN symbols a ON a.id=r.owner_id
                    JOIN symbols b ON b.id=r.target_id'''
            self.assertEqual(self.rows(refs),[('Entry',name+'.Save')])
            before=(self.edges(),self.rows(refs));self.index(full=True)
            self.assertEqual(before,(self.edges(),self.rows(refs)))

    def test_implicit_case_blocks_never_supply_outer_receiver_hints(self):
        source=('package app\ntype Repo struct{};func(Repo) Save()int{return 7}\n'
                'type Other struct{};func(Other) Save()int{return 11};var r=Other{}\n')
        functions=[]
        for prefix,opening in (('Switch','switch 1 {case 1:'),
                               ('TypeSwitch','switch any(1).(type) {case int:'),
                               ('Select','select {default:')):
            for suffix,declaration in (('Literal','var r=Repo{}'),('Explicit','var r Repo'),
                                       ('GroupedLiteral','var(r=Repo{};)'),('GroupedExplicit','var(r Repo;)')):
                name=prefix+suffix;functions.append(name)
                source+='func '+name+'()int{'+opening+declaration+';_=r};_=r.Save;return r.Save()}\n'
        controls={'Control':'r:=Repo{}','GroupedControl':'var(r=Repo{};)','TypedGroupedControl':'var(r Repo;)'}
        for name,declaration in controls.items():source+='func '+name+'()int{'+declaration+';_=r.Save;return r.Save()}\n'
        self.put('a.go',source)
        for full in (False,True):
            self.index(full=full)
            for name in functions:
                with self.subTest(owner=name,full=full):
                    self.assertEqual(self.targets(name),set())
                    self.assertEqual(self.rows('''SELECT b.qname FROM symbol_refs r JOIN symbols a ON a.id=r.owner_id
                        JOIN symbols b ON b.id=r.target_id WHERE a.qname='''+"'"+name+"'"),[])
            for name in controls:
                self.assertEqual(self.targets(name),{('a.go','Repo.Save')})
                self.assertEqual(self.rows('''SELECT b.qname FROM symbol_refs r JOIN symbols a ON a.id=r.owner_id
                    JOIN symbols b ON b.id=r.target_id WHERE a.qname='''+"'"+name+"'"),[('Repo.Save',)])

    def test_truncated_call_heads_never_resolve_another_identifier(self):
        short='r'+'x'*159;long=short+'z';function='F'+'x'*159;long_function=function+'z'
        method='M'+'x'*159;long_method=method+'z'
        for declaration in (short+':=A{};'+long+':=B{}',
                            'var '+short+' A;var '+long+' B'):
            source=('package app\ntype A struct{};func(A) Save()int{return 7}\n'
                    'type B struct{};func(B) Save()int{return 11}\n'
                    'func(A) '+method+'()int{return 7}\nfunc(A) '+long_method+'()int{return 11}\n'
                    'func '+function+'()int{return 7}\nfunc '+long_function+'()int{return 11}\n'
                    'func Entry()int{'+declaration+'\n_='+short+'.Save()\n_='+long+'.Save\n_='+long_function+'\nreturn '+long+'.Save()}\n'
                    'func FuncDrive()int{return '+long_function+'()}\n'
                    'func FuncControl()int{return '+function+'()}\n'
                    'func MethodDrive()int{r:=A{};_=r.'+long_method+';return r.'+long_method+'()}\n'
                    'func MethodControl()int{r:=A{};return r.'+method+'()}\n')
            self.put('a.go',source)
            for full in (False,True):
                with self.subTest(declaration=declaration,full=full):
                    self.index(full=full)
                    self.assertEqual(self.rows('''SELECT c.dynamic,b.qname FROM calls c
                        JOIN symbols a ON a.id=c.caller_id LEFT JOIN edges e ON e.call_id=c.id
                        LEFT JOIN symbols b ON b.id=e.callee_id WHERE a.qname='Entry' AND c.name='Save' ORDER BY c.line'''),
                        [(0,'A.Save'),(1,None)])
                    self.assertEqual(self.targets('FuncDrive'),set())
                    self.assertEqual(self.rows("SELECT dynamic,recv FROM calls c JOIN symbols a ON a.id=c.caller_id WHERE a.qname='FuncDrive'"),[(1,'go_dynamic')])
                    self.assertEqual(self.targets('FuncControl'),{('a.go',function)})
                    self.assertEqual(self.targets('MethodDrive'),set())
                    self.assertEqual(self.targets('MethodControl'),{('a.go','A.'+method)})
                    self.assertEqual(set(self.rows('''SELECT b.qname FROM symbol_refs r JOIN symbols a ON a.id=r.owner_id
                        JOIN symbols b ON b.id=r.target_id WHERE a.qname='Entry' ''')),{('B.Save',),(long_function,)})
                    self.assertEqual(self.rows('''SELECT b.qname FROM symbol_refs r JOIN symbols a ON a.id=r.owner_id
                        JOIN symbols b ON b.id=r.target_id WHERE a.qname='MethodDrive' '''),[('A.'+long_method,)])

    def test_module_change_re_resolves_and_invalidates_readers(self):
        self.put('go.mod', 'module example.com/app\ngo 1.23\n')
        self.put('a.go', 'package app\nimport "example.com/app/lib"\nfunc Entry(){ lib.Export() }\n')
        self.put('lib/a.go', 'package lib\nfunc Export(){}\n')
        self.index()
        self.assertEqual(self.targets('Entry'), {('lib/a.go','Export')})
        self.put('go.mod', 'module example.com/changed\ngo 1.23\n')
        with self.assertRaisesRegex(ValueError, 'Configurations'):
            with ProjectData(cg, self.root): pass
        self.index()
        self.assertEqual(self.targets('Entry'), set())
        self.index(full=True)
        self.assertEqual(self.targets('Entry'), set())

    def test_nested_invalid_symlinked_module_and_workspace_boundaries(self):
        for boundary in ('module example.com/child\n', 'not a module\n', 'symlink'):
            with self.subTest(boundary=boundary):
                self.put('go.mod', 'module example.com/app\ngo 1.23\n')
                self.put('a.go', 'package app\nimport "example.com/app/child"\nfunc Entry(){ child.Export() }\n')
                self.put('child/a.go', 'package child\nfunc Export(){}\n')
                path = self.root/'child/go.mod'
                path.unlink(missing_ok=True)
                if boundary == 'symlink':
                    outside = self.base/'outside.mod'
                    outside.write_text('module example.com/app/child\n')
                    path.symlink_to(outside)
                else:
                    path.write_text(boundary)
                self.index()
                self.assertEqual(self.targets('Entry'), set())
        (self.root/'child/go.mod').unlink()
        self.index()
        self.assertEqual(self.targets('Entry'), {('child/a.go','Export')})
        self.put('go.work', 'go 1.23\nuse .\n')
        self.index()
        self.assertEqual(self.targets('Entry'), set())

    def test_git_ignored_module_and_workspace_configs_still_form_boundaries(self):
        subprocess.run(['git','init','-q',str(self.root)], check=True, capture_output=True)
        self.put('.gitignore', 'child/go.mod\ngo.work\n')
        self.put('go.mod', 'module example.com/app\ngo 1.23\n')
        self.put('a.go', 'package app\nimport "example.com/app/child"\nfunc Entry(){ child.Export() }\n')
        self.put('child/a.go', 'package child\nfunc Export(){}\n')
        self.put('child/go.mod', 'module example.com/other\n')
        self.index()
        self.assertEqual(self.targets('Entry'), set())
        (self.root/'child/go.mod').unlink()
        self.index()
        self.assertEqual(self.targets('Entry'), {('child/a.go','Export')})
        self.put('go.work', 'go 1.23\nuse .\n')
        self.index()
        self.assertEqual(self.targets('Entry'), set())

    def test_new_symlinked_or_oversized_boundary_invalidates_existing_readers(self):
        self.put('go.mod','module example.com/app\ngo 1.23\n')
        self.put('a.go','package app\nimport "example.com/app/lib"\nfunc Entry(){ lib.Export() }\n')
        self.put('lib/a.go','package lib\nfunc Export(){}\n')
        self.index()
        self.assertEqual(self.targets('Entry'),{('lib/a.go','Export')})
        outside=self.base/'outside.mod'
        outside.write_text('module example.com/app/lib\n')
        (self.root/'lib/go.mod').symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'Configurations'):
            with ProjectData(cg,self.root):pass
        self.index()
        self.assertEqual(self.targets('Entry'),set())
        (self.root/'lib/go.mod').unlink()
        self.index()
        self.put('go.work','#' * 200001)
        with self.assertRaisesRegex(ValueError,'Configurations'):
            with ProjectData(cg,self.root):pass
        self.index()
        self.assertEqual(self.targets('Entry'),set())

    def test_search_impact_test_discovery_and_exported_unicode_names(self):
        self.basic()
        self.put('unicode.go', 'package app\nfunc Éclair(){}; func café(){}\n')
        self.index()
        self.assertEqual(dict(self.rows("SELECT name,exported FROM symbols WHERE name IN ('Éclair','café')")),
                         {'Éclair':1,'café':0})
        with ProjectData(cg, self.root) as project:
            result = cg.search_module()['search'](project, 'Identity')
            self.assertTrue(any(item['symbol']['language']=='go' for item in result['items']))
            sid = cg.resolve_one(project.db, 'Run')
            plan = runpy.run_path(str(REPO/'scripts/change_analysis.py'))['plan'](project,[sid])
            self.assertIn('TestRun', [item['symbol']['name'] for item in plan['tests']])
        role=runpy.run_path(str(REPO/'scripts/evidence.py'))['test_role']
        for name in ('TestMain','Testhelper','helper'):
            self.assertEqual(role(dict(file='app_test.go',name=name,kind='function',language='go')),'test_support')

    def test_cli_mcp_search_and_test_plan_keep_the_go_index_read_only(self):
        self.basic()
        path = self.root/'.code-graph/graph.db'
        before = path.read_bytes()
        requests = [dict(jsonrpc='2.0',id=1,method='initialize',params={}),
                    dict(jsonrpc='2.0',id=2,method='tools/call',params=dict(name='cg_search',arguments=dict(query='helper'))),
                    dict(jsonrpc='2.0',id=3,method='tools/call',params=dict(name='cg_verify_plan',arguments=dict(target='Run',json=True)))]
        command = [sys.executable,'-I',str(REPO/'scripts/cg.py'),'--root',str(self.root)]
        run = subprocess.run(command+['mcp'],input=''.join(json.dumps(row)+'\n' for row in requests),
                             capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stderr)
        responses = [json.loads(line)['result'] for line in run.stdout.splitlines()]
        self.assertFalse(responses[1]['isError'],responses[1])
        cli = subprocess.run(command+['search','helper'],capture_output=True,text=True,timeout=20)
        self.assertEqual(cli.returncode,0,cli.stderr)
        self.assertEqual(json.loads(cli.stdout),json.loads(responses[1]['content'][0]['text']))
        self.assertFalse(responses[2]['isError'],responses[2])
        plan = json.loads(responses[2]['content'][0]['text'])
        self.assertIn('TestRun',[item['symbol']['name'] for item in plan['tests']])
        self.assertEqual(before,path.read_bytes())

    def test_incremental_delete_and_full_rebuild_agree(self):
        self.basic()
        original = self.edges()
        self.index()
        self.assertEqual(self.edges(), original)
        self.put('lib/ops.go', 'package differentlyNamed\nfunc Changed(){}\n')
        self.index()
        updated = self.edges()
        self.assertNotIn(('lib/ops.go','Export'), self.targets('Run'))
        self.index(full=True)
        self.assertEqual(self.edges(), updated)

    def test_config_write_failure_rolls_back_fts_graph_and_releases_writer(self):
        self.basic()
        before=self.edges()
        metadata=dict(self.rows('SELECT k,v FROM meta'))
        self.put('app.go','package app\nfunc Replacement(){}\n')
        original=cg.LANGUAGE_ADAPTERS['go']['store_config']
        def fail(db,*args):
            original(db,*args)
            raise sqlite3.OperationalError('Synthetic Go config write failure')
        caught=None
        with patch.dict(cg.LANGUAGE_ADAPTERS['go'],store_config=fail):
            try:self.index()
            except sqlite3.OperationalError as error:caught=error
        self.assertIsNotNone(caught)
        with contextlib.closing(sqlite3.connect(self.root/'.code-graph/graph.db',timeout=0)) as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertEqual(dict(db.execute('SELECT k,v FROM meta'))['go_config'],metadata['go_config'])
            self.assertEqual(db.execute("SELECT count(*) FROM search_fts WHERE search_fts MATCH 'Replacement'").fetchone()[0],0)
            db.rollback()
        self.assertEqual(self.edges(),before)


if __name__ == '__main__':
    unittest.main()
