"""The declared Dart subset through the actual index and public query interfaces."""
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
spec = importlib.util.spec_from_file_location('cg_dart_tests', REPO/'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
ProjectData = runpy.run_path(str(REPO/'scripts/project_data.py'))['ProjectData']


class Dart(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cg-dart-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'project'
        self.root.mkdir()

    def put(self, path, source):
        target = self.root/path
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(source)
        return target

    def index(self, full=False):
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(self.root),full=full))

    def rows(self, sql):
        with contextlib.closing(sqlite3.connect(self.root/'.code-graph/graph.db')) as db:
            return db.execute(sql).fetchall()

    def edges(self):
        return set(self.rows('''SELECT f.path,a.qname,g.path,b.qname,e.conf FROM edges e
             JOIN symbols a ON a.id=e.caller_id JOIN symbols b ON b.id=e.callee_id
             JOIN files f ON f.id=a.file_id JOIN files g ON g.id=b.file_id'''))

    def targets(self, caller):
        return {(path,name) for _,owner,path,name,_ in self.edges() if owner==caller}

    def refs(self, owner):
        return self.rows(f'''SELECT f.path,b.qname FROM symbol_refs r JOIN symbols a ON a.id=r.owner_id
             JOIN symbols b ON b.id=r.target_id JOIN files f ON f.id=b.file_id WHERE a.qname='{owner}' ''')

    def test_standalone_library_functions_recursion_and_same_names(self):
        self.put('a.dart','int leaf(int n)=>n; int recur(int n)=>n>0?recur(n-1):leaf(n);')
        self.put('b.dart','int leaf(int n)=>n; int use()=>leaf(1);')
        self.index()
        self.assertEqual(self.targets('recur'),{('a.dart','recur'),('a.dart','leaf')})
        self.assertEqual(self.targets('use'),{('b.dart','leaf')})
        self.assertTrue(all(conf=='high' for *_,conf in self.edges()))

    def test_relative_prefix_unprefixed_import_and_privacy(self):
        self.put('lib/ops.dart','int leaf()=>1; int _secret()=>2;')
        self.put('app.dart',"import 'lib/ops.dart' as api; int use()=>api.leaf()+api._secret();")
        self.put('direct.dart',"import 'lib/ops.dart'; int useDirect()=>leaf()+_secret();")
        self.index()
        self.assertEqual(self.targets('use'),{('lib/ops.dart','leaf')})
        self.assertEqual(self.targets('useDirect'),{('lib/ops.dart','leaf')})

    def test_show_hide_combinators_and_duplicate_prefix_ambiguity(self):
        self.put('a.dart','int leaf()=>1; int hidden()=>2;')
        self.put('b.dart','int leaf()=>3;')
        self.put('c.dart',"import 'a.dart' show leaf; int use()=>leaf()+hidden();")
        self.put('d.dart',"import 'a.dart' as x hide hidden; int prefixed()=>x.leaf()+x.hidden();")
        self.put('e.dart',"import 'a.dart' as x; import 'b.dart' as x; int ambiguous()=>x.leaf();")
        self.index()
        self.assertEqual(self.targets('use'),{('a.dart','leaf')})
        self.assertEqual(self.targets('prefixed'),{('a.dart','leaf')})
        self.assertEqual(self.targets('ambiguous'),set())

    def test_local_declaration_wins_and_noncallable_import_blocks(self):
        self.put('a.dart','int leaf()=>1;')
        self.put('b.dart','final leaf=()=>2;')
        self.put('c.dart',"import 'a.dart'; import 'b.dart'; int ambiguous()=>leaf();")
        self.put('d.dart',"import 'a.dart'; int leaf()=>3; int own()=>leaf();")
        self.index()
        self.assertEqual(self.targets('ambiguous'),set())
        self.assertEqual(self.targets('own'),{('d.dart','leaf')})

    def test_top_level_getters_shadow_imported_functions(self):
        self.put('lib.dart','int leaf()=>1;')
        self.put('a.dart',"import 'lib.dart'; Function get leaf=>()=>2; int use()=>leaf();")
        self.index()
        self.assertEqual(self.targets('use'),set())
        self.assertEqual(self.rows("SELECT qname FROM symbols WHERE name='leaf'"),[('leaf',),('leaf',)])

    def test_parameters_locals_catches_loops_closures_and_patterns_shadow(self):
        self.put('a.dart','void leaf(){}\nvoid plain(){leaf();}\n'
                 'void param(void Function() leaf){leaf();}\n'
                 'void local(){var leaf=(){};leaf();}\n'
                 'void nested(){void leaf(){} leaf();}\n'
                 'void catching(){try {}catch(leaf){leaf();}}\n'
                 'void looping(){for(var leaf in <dynamic>[]){leaf();}}\n'
                 'void closure(){var f=(leaf)=>leaf();leaf();}\n'
                 'void patterned(){var (leaf,other)=(1,2);leaf();}\n')
        self.index()
        self.assertEqual(self.targets('plain'),{('a.dart','leaf')})
        for owner in ('param','local','nested','catching','looping','closure','patterned'):
            self.assertEqual(self.targets(owner),set(),owner)

    def test_prefix_shadowing_and_type_parameters(self):
        self.put('lib.dart','int leaf()=>1;')
        self.put('app.dart',"import 'lib.dart' as api; int leaf()=>2; "
                 'dynamic prefix(dynamic api)=>api.leaf(); '
                 'dynamic generic<leaf>()=>leaf(); '
                 'dynamic global()=>api.leaf();')
        self.index()
        self.assertEqual(self.targets('prefix'),set())
        self.assertEqual(self.targets('generic'),set())
        self.assertEqual(self.targets('global'),{('lib.dart','leaf')})

    def test_static_methods_and_uncertain_receivers_never_name_fallback(self):
        self.put('a.dart','class Repo { static int save()=>1; int run()=>1; }\n'
                 'int good()=>Repo.save(); int wrong()=>Repo.run();\n'
                 'dynamic variable(dynamic Repo)=>Repo.save();\n'
                 'dynamic instance(Repo repo)=>repo.run();\n'
                 'class Other { int save()=>1; int inside()=>save(); }\n'
                 'extension E on String { int save()=>1; }\n'
                 'dynamic extension(String value)=>value.save();\n')
        self.index()
        self.assertEqual(self.targets('good'),{('a.dart','Repo.save')})
        for owner in ('wrong','variable','instance','Other.inside','extension'):
            self.assertEqual(self.targets(owner),set(),owner)

    def test_part_conditional_deferred_missing_external_and_reexports_abstain(self):
        self.put('leaf.dart','int leaf()=>1;')
        sources={'parts':"part 'piece.dart'; int leaf()=>1; int useParts()=>leaf();",
            'piece':"part of 'parts.dart'; int usePiece()=>leaf();",
            'conditional':"import 'leaf.dart' if(dart.library.io) 'other.dart'; int useConditional()=>leaf();",
            'deferred':"import 'leaf.dart' deferred as api; int useDeferred()=>api.leaf();",
            'external':"import 'package:pkg/lib.dart'; int useExternal()=>leaf();",
            'missing':"import 'missing_file.dart'; import 'leaf.dart'; int useMissing()=>leaf();",
            'barrel':"export 'leaf.dart';", 'usebarrel':"import 'barrel.dart'; int useBarrel()=>leaf();"}
        for name,source in sources.items():self.put(name+'.dart',source)
        self.index()
        self.assertEqual(self.edges(),set())

    def test_initializers_arrow_block_spans_and_function_value_references(self):
        self.put('a.dart','int leaf()=>1;\nfinal seed=leaf();\nvar ref=leaf;\n'
                 'int use(){\n final f=leaf;\n return leaf();\n}\n')
        self.index()
        self.assertEqual(self.targets('a.dart'),{('a.dart','leaf')})
        self.assertEqual(self.targets('use'),{('a.dart','leaf')})
        self.assertEqual(self.refs('a.dart'),[('a.dart','leaf')])
        self.assertEqual(self.refs('use'),[('a.dart','leaf')])
        self.assertEqual(self.rows("SELECT line,end_line FROM symbols WHERE name='use'"),[(4,7)])

    def test_chained_and_immediately_invoked_callbacks_keep_argument_references(self):
        self.put('a.dart','void leaf(){}\nFunction wrap(Function f)=>f;\n'
                 'void use(){wrap(leaf)(); (()=>leaf)();}\n')
        self.index()
        self.assertEqual(self.targets('use'),{('a.dart','wrap')})
        self.assertEqual(self.refs('use'),[('a.dart','leaf'),('a.dart','leaf')])

    def test_imported_function_and_static_method_tearoffs_are_references(self):
        self.put('lib.dart','int leaf()=>1;')
        self.put('a.dart',"import 'lib.dart' as api; class Repo {static int save()=>1;} "
                 'void use(){final a=api.leaf; final b=Repo.save; a();b();}')
        self.index()
        self.assertEqual(self.targets('use'),set())
        self.assertEqual(set(self.refs('use')),{('lib.dart','leaf'),('a.dart','Repo.save')})

    def test_record_values_keep_function_references_without_treating_labels_as_values(self):
        self.put('a.dart','int leaf()=>1; int item()=>2;\n'
                 'dynamic positional()=>(leaf,0);\n'
                 'dynamic named()=>(item:leaf);\n')
        self.index()
        self.assertEqual(self.refs('positional'),[('a.dart','leaf')])
        self.assertEqual(self.refs('named'),[('a.dart','leaf')])

    def test_constructor_names_do_not_become_global_function_references(self):
        self.put('a.dart','class Repo{Repo.named(); const Repo.constant();} void named(){} '
                 'void make(){new Repo.named(); Repo.named();}')
        self.index()
        self.assertEqual(self.targets('make'),set())
        self.assertEqual(self.refs('make'),[])
        self.assertEqual(set(self.rows("SELECT qname FROM symbols WHERE kind='method'")),
                         {('Repo.named',),('Repo.constant',)})

    def test_constructor_initializer_calls_and_references_keep_member_owner(self):
        self.put('a.dart','int helper()=>1; void body(){}\n'
                 'class C { final int x; C():x=helper(){body();} }\n'
                 'class D { int x=helper(); }\n'
                 'class E { final Function ref; E():ref=helper; }\n')
        for full in (False,True):
            self.index(full=full)
            self.assertEqual(set(self.rows('''SELECT s.qname,c.name FROM calls c
                   JOIN symbols s ON s.id=c.caller_id''')),
                             {('C.C','helper'),('C.C','body'),('D','helper')})
            self.assertEqual(self.rows('''SELECT s.qname,r.name FROM symbol_refs r
                   JOIN symbols s ON s.id=r.owner_id WHERE r.name='helper' '''),[('E.E','helper')])
            self.assertEqual(self.edges(),set())

    def test_partial_import_target_and_duplicate_declarations_do_not_guess(self):
        self.put('bad.dart','int leaf()=>1; class Broken {')
        self.put('a.dart',"import 'bad.dart'; int use()=>leaf();")
        self.put('b.dart','int leaf()=>1; int leaf()=>2; int duplicate()=>leaf();')
        self.index()
        self.assertEqual(self.edges(),set())

    def test_parser_errors_report_partial_and_exclude_callers_targets_and_refs(self):
        # SDK 3.6 forms rejected by the pinned grammar. Test our error boundary,
        # not the parser bugs: a future grammar may accept any of these forms.
        forms={
            'library':'library;',
            'labels':'void probe(){outer:{break outer;} loop:while(true){break loop;}}',
            'abstract':'abstract class C { abstract void Function()? callback; }',
            'external':'class C { external int value; } external int value;',
            'named':'dynamic probe(dynamic f)=>f(get:1,set:2);',
            'record':'dynamic probe()=>();',
            'identifier':'void probe(){int set=1;}',
            'empty_for':'void probe(){for(int i=0;i<2;i++);}',
        }
        common='int leaf()=>1; int use()=>leaf(); dynamic ref()=>leaf;\n'
        self.put('control.dart',common)
        self.put('broken.dart',common+'class Broken {')
        for name in forms:
            self.put(f'import_{name}.dart',f"import '{name}.dart' as api; "
                     'int imported()=>api.leaf(); dynamic importedRef()=>api.leaf;')
        def facts():
            return [self.rows(sql+' ORDER BY 1,2,3,4') for sql in (
                '''SELECT f.path,s.qname,s.kind,s.sig,s.line,s.end_line FROM symbols s
                   JOIN files f ON f.id=s.file_id''',
                '''SELECT f.path,s.qname,c.name,c.line,c.recv,c.recv_name,c.nargs,c.dynamic
                   FROM calls c JOIN files f ON f.id=c.file_id JOIN symbols s ON s.id=c.caller_id''',
                '''SELECT f.path,s.qname,r.name,r.line,r.kind,r.receiver,g.path,t.qname
                   FROM symbol_refs r JOIN files f ON f.id=r.file_id JOIN symbols s ON s.id=r.owner_id
                   LEFT JOIN symbols t ON t.id=r.target_id LEFT JOIN files g ON g.id=t.file_id''',
                '''SELECT f.path,s.qname,o.kind,o.line,o.value FROM observations o
                   JOIN files f ON f.id=o.file_id JOIN symbols s ON s.id=o.owner_id''')]
        for include_forms in (True,False,True):
            with self.subTest(include_forms=include_forms):
                for name,form in forms.items():
                    source=(form+'\n'+common if name=='library' else common+form) if include_forms else common
                    self.put(name+'.dart',source)
                partial={p.name for p in self.root.glob('*.dart')
                         if cg.get_parser_for('dart').parse(p.read_bytes()).root_node.has_error}
                self.index()
                self.assertEqual(set(json.loads(dict(self.rows('SELECT k,v FROM meta'))['partial_paths'])),partial)
                eligible={'control.dart'}|{name+'.dart' for name in forms if name+'.dart' not in partial}
                expected={(path,'use',path,'leaf','high') for path in eligible}
                expected|={(f'import_{name}.dart','imported',name+'.dart','leaf','high')
                           for name in forms if name+'.dart' not in partial}
                self.assertEqual(self.edges(),expected)
                refs=set(self.rows('''SELECT f.path,s.qname,g.path,t.qname FROM symbol_refs r
                    JOIN symbols s ON s.id=r.owner_id JOIN symbols t ON t.id=r.target_id
                    JOIN files f ON f.id=s.file_id JOIN files g ON g.id=t.file_id'''))
                expected_refs={(path,'ref',path,'leaf') for path in eligible}
                expected_refs|={(f'import_{name}.dart','importedRef',name+'.dart','leaf')
                                for name in forms if name+'.dart' not in partial}
                self.assertEqual(refs,expected_refs)
                before=facts()
                self.index(full=True)
                self.assertEqual(facts(),before)
                self.assertEqual(self.edges(),expected)
                self.assertEqual(set(json.loads(dict(self.rows('SELECT k,v FROM meta'))['partial_paths'])),partial)

    def test_escape_symlink_and_uri_variants_do_not_read_external_targets(self):
        self.put('lib.dart','int leaf()=>1;')
        (self.root/'linked.dart').symlink_to(self.root/'lib.dart')
        for i,uri in enumerate(['../outside.dart','/absolute.dart','lib.dart?x','lib%2edart','linked.dart']):
            self.put(f'a{i}.dart',f"import '{uri}'; int use{i}()=>leaf();")
        self.index()
        self.assertEqual(self.edges(),set())
        self.assertNotIn(('linked.dart',),self.rows('SELECT path FROM files'))

    def test_incremental_import_changes_deletion_and_full_rebuild_agree(self):
        path=self.put('lib.dart','int leaf()=>1;')
        self.put('a.dart',"import 'lib.dart'; int use()=>leaf();")
        self.index();self.assertEqual(self.targets('use'),{('lib.dart','leaf')})
        self.put('a.dart',"import 'lib.dart' hide leaf; int use()=>leaf();")
        self.index();self.assertEqual(self.targets('use'),set())
        self.put('a.dart',"import 'lib.dart'; int use()=>leaf();")
        path.unlink();self.index();self.assertEqual(self.targets('use'),set())
        before=self.edges();self.index(full=True);self.assertEqual(self.edges(),before)

    def test_cli_mcp_search_source_spans_and_readonly(self):
        self.put('a.dart','int leaf()=>1;\nint use(){\n return leaf(); // dartneedle\n}\n')
        self.put('same.dart','int unrelated()=>1; String matched() /* doc */ =>"dartneedle";')
        self.index()
        with ProjectData(cg,self.root) as project:
            result=cg.search_module()['search'](project,'dartneedle',mode='lexical')
            self.assertIn('use',[item['symbol']['name'] for item in result['items']])
            self.assertNotIn('leaf',[item['symbol']['name'] for item in result['items']])
            self.assertIn('matched',[item['symbol']['name'] for item in result['items']])
            self.assertNotIn('unrelated',[item['symbol']['name'] for item in result['items']])
        path=self.root/'.code-graph/graph.db';before=path.read_bytes()
        request=dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='cg_search',arguments=dict(query='dartneedle',mode='lexical')))
        command=[sys.executable,'-I',str(REPO/'scripts/cg.py'),'--root',str(self.root)]
        run=subprocess.run(command+['mcp'],input=json.dumps(request)+'\n',capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stderr)
        result=json.loads(run.stdout)['result'];self.assertFalse(result['isError'])
        cli=subprocess.run(command+['search','dartneedle','--mode','lexical'],capture_output=True,text=True,timeout=20)
        self.assertEqual(cli.returncode,0,cli.stderr)
        self.assertEqual(json.loads(cli.stdout),json.loads(result['content'][0]['text']))
        self.assertEqual(before,path.read_bytes())

    def test_dart_resolution_error_rolls_back_graph_and_fts(self):
        self.put('a.dart','int leaf()=>1; int use()=>leaf();');self.index();before=self.edges()
        self.put('a.dart','int replacement()=>2;')
        caught=None
        with patch.dict(cg.LANGUAGE_ADAPTERS['dart'],DartLibraries=lambda db:(_ for _ in ()).throw(sqlite3.OperationalError('synthetic Dart error'))):
            try:self.index()
            except sqlite3.OperationalError as error:caught=error
        self.assertIsNotNone(caught)
        self.assertEqual(self.edges(),before)
        with contextlib.closing(sqlite3.connect(self.root/'.code-graph/graph.db',timeout=0)) as db:
            db.execute('BEGIN IMMEDIATE')
            self.assertEqual(db.execute("SELECT count(*) FROM search_fts WHERE search_fts MATCH 'replacement'").fetchone()[0],0)
            db.rollback()

    def test_engine_copy_isolation_and_dart_fingerprint_invalidation(self):
        engine=self.root.parent/'engine'
        shutil.copytree(REPO/'scripts',engine,ignore=shutil.ignore_patterns('__pycache__'))
        def load(path,name):
            spec=importlib.util.spec_from_file_location(name,path)
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            return module
        a=load(engine/'cg.py','dart_copy_a')
        self.put('a.dart','int leaf()=>1; int use()=>leaf();')
        with contextlib.redirect_stdout(io.StringIO()):a.cmd_index(argparse.Namespace(root=str(self.root),full=False))
        before=dict(self.rows('SELECT k,v FROM meta'))['analysis_fingerprint']
        adapter=engine/'language_dart.py'
        adapter.write_text(adapter.read_text()+"\nEXTENSIONS['.dartprobe']='dart'\n")
        b=load(engine/'cg.py','dart_copy_b')
        self.assertNotIn('.dartprobe',a.LANG_BY_EXT)
        self.assertIn('.dartprobe',b.LANG_BY_EXT)
        self.assertNotIn('.dartprobe',cg.LANG_BY_EXT)
        with contextlib.redirect_stdout(io.StringIO()):b.cmd_index(argparse.Namespace(root=str(self.root),full=False))
        self.assertNotEqual(before,dict(self.rows('SELECT k,v FROM meta'))['analysis_fingerprint'])
        self.assertEqual(self.targets('use'),{('a.dart','leaf')})


if __name__=='__main__':unittest.main()
