"""Declared Java source subset, real indexing and public readonly consumers."""
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

REPO=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cg_java_tests',REPO/'scripts/cg.py')
cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg)
ProjectData=runpy.run_path(str(REPO/'scripts/project_data.py'))['ProjectData']


class Java(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='cg-java-');self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)/'project';self.root.mkdir()

    def put(self,path,source):
        target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(source);return target

    def index(self,full=False):
        with contextlib.redirect_stdout(io.StringIO()):cg.cmd_index(argparse.Namespace(root=str(self.root),full=full))

    def rows(self,sql):
        with contextlib.closing(sqlite3.connect(self.root/'.code-graph/graph.db')) as db:return db.execute(sql).fetchall()

    def edges(self):
        return set(self.rows('''SELECT f.path,a.qname,g.path,b.qname,e.conf FROM edges e
             JOIN symbols a ON a.id=e.caller_id JOIN symbols b ON b.id=e.callee_id
             JOIN files f ON f.id=a.file_id JOIN files g ON g.id=b.file_id'''))

    def targets(self,caller):
        return {(path,name) for _,owner,path,name,_ in self.edges() if owner==caller}

    def test_same_class_static_private_final_and_virtual_distinction(self):
        self.put('App.java','class App {static int leaf(){return 1;} private int hidden(){return leaf();} '
                 'final int fixed(){return 1;} int virtual(){return 1;} '
                 'int drive(){return this.hidden()+fixed()+virtual();}}')
        self.index()
        self.assertEqual(self.targets('App.drive'),{('App.java','App.hidden'),('App.java','App.fixed')})
        self.assertEqual(self.targets('App.hidden'),{('App.java','App.leaf')})

    def test_final_class_methods_are_exact_but_arbitrary_receivers_abstain(self):
        self.put('App.java','final class App {int leaf(){return 1;} int drive(){return leaf();} '
                 'static int indirect(App value){return value.leaf();}}')
        self.index()
        self.assertEqual(self.targets('App.drive'),{('App.java','App.leaf')})
        self.assertEqual(self.targets('App.indirect'),set())

    def test_imports_fully_qualified_and_same_package_type_resolution(self):
        self.put('app/App.java','package app; import lib.Ops; class App {static int drive(){return Ops.leaf()+lib.Ops.leaf()+Local.leaf();}}')
        self.put('app/Local.java','package app; class Local {static int leaf(){return 1;}}')
        self.put('lib/Ops.java','package lib; public class Ops {public static int leaf(){return 2;}}')
        self.index()
        self.assertEqual(self.targets('App.drive'),{('lib/Ops.java','Ops.leaf'),('app/Local.java','Local.leaf')})
        self.assertEqual(self.rows('SELECT count(*) FROM edges'),[(3,)])

    def test_private_package_and_type_visibility(self):
        self.put('app/App.java','package app; import lib.Ops; import lib.Hidden; class App {static int drive(){return Ops.publicLeaf()+Ops.privateLeaf()+Ops.packageLeaf()+Hidden.leaf();}}')
        self.put('lib/Ops.java','package lib; public class Ops {public static int publicLeaf(){return 1;} private static int privateLeaf(){return 1;} static int packageLeaf(){return 1;}}')
        self.put('lib/Hidden.java','package lib; class Hidden {public static int leaf(){return 1;}}')
        self.index()
        self.assertEqual(self.targets('App.drive'),{('lib/Ops.java','Ops.publicLeaf')})

    def test_explicit_static_imports_and_unknown_or_wildcard_ambiguity(self):
        self.put('lib/Ops.java','package lib; public class Ops {public static int leaf(){return 1;}}')
        self.put('app/Good.java','package app; import static lib.Ops.leaf; class Good {static int drive(){return leaf();}}')
        self.put('app/Unknown.java','package app; import static lib.Ops.leaf; import static external.Other.leaf; class Unknown {static int drive(){return leaf();}}')
        self.put('app/Wild.java','package app; import static lib.Ops.*; class Wild {static int drive(){return leaf();}}')
        self.index()
        self.assertEqual(self.targets('Good.drive'),{('lib/Ops.java','Ops.leaf')})
        self.assertEqual(self.targets('Unknown.drive'),set());self.assertEqual(self.targets('Wild.drive'),set())

    def test_overloads_never_pick_by_arity_or_staticness(self):
        self.put('App.java','class App {static int leaf(int x){return 1;} static int leaf(String x){return 2;} '
                 'static int drive(){return leaf(1);} static int count(){return 1;} static int count(int x){return 2;} static int use(){return count();}}')
        self.index()
        self.assertEqual(self.targets('App.drive'),set());self.assertEqual(self.targets('App.use'),set())

    def test_heritage_object_methods_and_interface_dispatch_abstain(self):
        self.put('App.java','class Base {static int leaf(){return 1;}} class App extends Base {static int own(){return 1;} static int drive(){return own()+Base.leaf();}} '
                 'interface Contract {int leaf();} class Other {static int leaf(){return 1;} static int run(Contract x){return x.leaf();}}')
        self.index();self.assertEqual(self.edges(),set())

    def test_qualifier_shadowing_fields_parameters_locals_type_parameters_and_patterns(self):
        self.put('Ops.java','class Ops {static int leaf(){return 1;}}')
        self.put('App.java','class App {static int direct(){return Ops.leaf();} '
                 'static int param(Object Ops){return Ops.leaf();} '
                 'static int local(){Object Ops=null;return Ops.leaf();} '
                 'static <Ops> int generic(){return Ops.leaf();} '
                 'static int patterned(Object value){if(value instanceof String Ops){return Ops.leaf();}return 0;} '
                 'static int catching(){try{}catch(Exception Ops){return Ops.leaf();}return 0;}} '
                 'class Fields {Object Ops; int run(){return Ops.leaf();}}')
        self.index()
        self.assertEqual(self.targets('App.direct'),{('Ops.java','Ops.leaf')})
        for caller in ('App.param','App.local','App.generic','App.patterned','App.catching','Fields.run'):
            self.assertEqual(self.targets(caller),set(),caller)

    def test_variables_do_not_shadow_the_method_namespace(self):
        self.put('App.java','class App {static int leaf(){return 1;} static int drive(){int leaf=42;return leaf();}}')
        self.index();self.assertEqual(self.targets('App.drive'),{('App.java','App.leaf')})

    def test_nested_local_anonymous_and_lambda_ownership(self):
        self.put('Ops.java','class Ops {static int leaf(){return 1;}}')
        self.put('Mode.java','enum Mode {A {int run(){return Ops.leaf();}};}')
        self.put('App.java','class App {class Nested {int run(){return Ops.leaf();}} '
                 'static int local(){class Local {int run(){return Ops.leaf();}} return Ops.leaf();} '
                 'static void anonymous(){Object x=new Object(){int run(){return Ops.leaf();}};} '
                 'static void lambda(){java.util.function.Function<Object,Integer> f=(Ops)->Ops.leaf();}}')
        self.index();self.assertEqual(self.edges(),set())
        self.assertTrue(any('anonymous@' in q for q, in self.rows("SELECT qname FROM symbols WHERE kind='class'")))

    def test_method_references_are_not_calls_and_block_overloads(self):
        self.put('lib/Ops.java','package lib; public class Ops{public static int leaf(){return 1;}}')
        self.put('App.java','class App {static int leaf(){return 1;} private int hidden(){return 1;} '
                 'static int many(int x){return 1;} static int many(String x){return 2;} '
                 'void refs(){java.util.function.IntSupplier a=App::leaf; java.util.function.IntSupplier b=this::hidden; java.util.function.IntFunction<Integer> c=App::many; '
                 'java.util.function.IntSupplier d=new Other()::leaf; java.util.function.IntSupplier e=lib.Ops::leaf;}} class Other{int leaf(){return 1;}}')
        self.index()
        self.assertEqual(self.edges(),set())
        refs=self.rows('''SELECT a.qname,b.qname FROM symbol_refs r JOIN symbols a ON a.id=r.owner_id JOIN symbols b ON b.id=r.target_id''')
        self.assertEqual(set(refs),{('App.refs','App.leaf'),('App.refs','App.hidden'),('App.refs','Ops.leaf')})
        self.assertEqual(len(refs),3)

    def test_fields_static_and_instance_initializers_constructors_and_argument_calls(self):
        self.put('App.java','class App {static int leaf(){return 1;} static int seed=leaf(); static {leaf();} {leaf();} '
                 'App(){this(leaf());} App(int x){super();leaf();} static Object make(){return new App(leaf());}}')
        for full in (False,True):
            self.index(full=full)
            self.assertEqual(self.targets('App'),{('App.java','App.leaf')})
            self.assertEqual(self.targets('App.make'),{('App.java','App.leaf')})
            self.assertEqual(self.rows("SELECT count(*) FROM calls WHERE recv='java_constructor'"),[(3,)])
            self.assertEqual(self.rows("SELECT count(*) FROM calls WHERE name='leaf'"),[(6,)])

    def test_initializer_qualifiers_respect_local_shadowing(self):
        self.put('Ops.java','class Ops {static int leaf(){return 1;}}')
        self.put('App.java','class App {static {Object Ops=null; Ops.leaf();}}')
        self.index();self.assertEqual(self.edges(),set())

    def test_static_imported_values_obscure_even_same_file_type_qualifiers(self):
        self.put('app/App.java','package app; import static other.Holder.Ops; class Ops{static int leaf(){return 1001;}} '
                 'public class App{static int run(){return Ops.leaf();} public static void main(String[]args){System.out.println(run());}}')
        self.put('other/Holder.java','package other; public class Holder{public static final Node Ops=new Node(); '
                 'public static final class Node{public int leaf(){return 2;}}}')
        for full in (False,True):
            self.index(full=full);self.assertEqual(self.targets('App.run'),set())
            self.assertEqual(self.targets('App.main'),{('app/App.java','App.run')})

    def test_enum_constant_argument_lambdas_keep_their_parameter_bindings(self):
        self.put('App.java','class Ops{static int leaf(){return 1001;}} class Node{int leaf(){return 2;}} '
                 'enum E{A(Ops -> Ops.leaf()); final java.util.function.ToIntFunction<Node> fn; '
                 'E(java.util.function.ToIntFunction<Node> fn){this.fn=fn;} static int run(){return A.fn.applyAsInt(new Node());}} '
                 'public class App{public static void main(String[]args){System.out.println(E.run());}}')
        for full in (False,True):
            self.index(full=full);self.assertEqual(self.targets('E'),set())
            self.assertEqual(self.targets('App.main'),{('App.java','E.run')})

    def test_records_enums_interfaces_and_annotations_are_searchable(self):
        self.put('Shapes.java','record Row(int n){static int leaf(){return 1;}} '
                 'enum Mode{A; static int leaf(){return 1;}} '
                 'interface Api{static int leaf(){return 1;}} @interface Mark{int value();} '
                 'class Shapes{static int run(){return Row.leaf()+Mode.leaf()+Api.leaf();}}')
        self.index()
        self.assertEqual(self.targets('Shapes.run'),{('Shapes.java','Row.leaf'),('Shapes.java','Mode.leaf'),('Shapes.java','Api.leaf')})

    def test_implicit_record_accessors_and_constructor_references_never_pick_methods(self):
        self.put('R.java','record R(int value) {static int value(int x){return 99;} int run(){return value();}}')
        self.put('C.java','class C{C(){} static int C(){return 99;} void refs(){java.util.function.Supplier<C> make=C::new;}}')
        self.index();self.assertEqual(self.edges(),set())
        self.assertEqual(self.rows('SELECT count(*) FROM symbol_refs WHERE target_id IS NOT NULL'),[(0,)])

    def test_source_roots_duplicates_packages_and_other_languages_never_fallback(self):
        self.put('main/app/App.java','package app; import lib.Ops; class App{static int drive(){return Ops.leaf();}}')
        self.put('other/lib/Ops.java','package lib; public class Ops{public static int leaf(){return 1;}}')
        self.put('leaf.py','def leaf():return 1')
        self.index();self.assertEqual(self.edges(),set())
        self.put('main/lib/Ops.java','package lib; public class Ops{public static int leaf(){return 1;}}')
        self.index();self.assertEqual(self.targets('App.drive'),{('main/lib/Ops.java','Ops.leaf')})
        self.put('main/lib/Duplicate.java','package lib; class Ops{static int leaf(){return 2;}}')
        self.index();self.assertEqual(self.edges(),set())

    def test_type_names_and_static_type_imports_obscure_package_qualifiers(self):
        self.put('lib/Ops.java','package lib; public class Ops{public static int leaf(){return 1;}}')
        self.put('app/App.java','package app; class lib{static class Ops{static int leaf(){return 2;}}} class App{static int run(){return lib.Ops.leaf();}}')
        self.put('app/Local.java','package app; class Local{static int leaf(){return 1;}}')
        self.put('app/Imported.java','package app; import static external.Outer.Local; class Imported{static int run(){return Local.leaf();}}')
        self.index();self.assertEqual(self.edges(),set())

    def test_module_boundaries_invalidate_freshness_even_when_ignored_or_symlinked(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True,capture_output=True)
        self.put('.gitignore','src/module-info.java\n')
        self.put('src/app/App.java','package app; import lib.Ops; class App{static int drive(){return Ops.leaf();}}')
        self.put('src/lib/Ops.java','package lib; public class Ops{public static int leaf(){return 1;}}')
        self.index();self.assertEqual(self.targets('App.drive'),{('src/lib/Ops.java','Ops.leaf')})
        boundary=self.put('src/module-info.java','module app {}')
        with self.assertRaisesRegex(ValueError,'Configurations'):
            with ProjectData(cg,self.root):pass
        self.index();self.assertEqual(self.edges(),set())
        boundary.unlink();self.index();self.assertTrue(self.edges())
        outside=self.root.parent/'outside.java';outside.write_text('module app {}');boundary.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'Configurations'):
            with ProjectData(cg,self.root):pass
        self.index();self.assertEqual(self.edges(),set())
        boundary.unlink();boundary.write_text('/'*200001);self.index();self.assertEqual(self.edges(),set())

    def test_unicode_escapes_partial_files_and_symlinks_never_supply_targets(self):
        self.put('App.java','class App{static int run(){return Ops.leaf();}}')
        self.put('Ops.java','class Ops{static int leaf(){return 1;} String value="\\u0041";}')
        self.index();self.assertEqual(self.edges(),set())
        self.put('Ops.java','class Ops{static int leaf(){return 1;} class Broken{')
        self.index();self.assertEqual(self.edges(),set())
        (self.root/'Ops.java').unlink();(self.root/'Ops.java').symlink_to(self.root/'App.java');self.index();self.assertEqual(self.edges(),set())

    def test_incremental_target_delete_and_full_rebuild_agree(self):
        self.put('App.java','class App{static int drive(){return Ops.leaf();}}')
        path=self.put('Ops.java','class Ops{static int leaf(){return 1;}}');self.index();self.assertTrue(self.edges())
        path.write_text('class Ops{static int changed(){return 2;}}');self.index();self.assertEqual(self.edges(),set())
        path.unlink();self.index();before=self.edges();self.index(full=True);self.assertEqual(before,self.edges())

    def test_cli_mcp_search_complete_spans_and_readonly(self):
        self.put('App.java','class App {static int unrelated(){return 1;} static int matched(){\n return 2; // javaneedle\n}}')
        self.index()
        with ProjectData(cg,self.root) as project:
            result=cg.search_module()['search'](project,'javaneedle',mode='lexical')
            names=[item['symbol']['name'] for item in result['items']]
            self.assertIn('App.matched',names);self.assertNotIn('App.unrelated',names)
        path=self.root/'.code-graph/graph.db';before=path.read_bytes()
        command=[sys.executable,'-I',str(REPO/'scripts/cg.py'),'--root',str(self.root)]
        request=dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='cg_search',arguments=dict(query='javaneedle',mode='lexical')))
        run=subprocess.run(command+['mcp'],input=json.dumps(request)+'\n',text=True,capture_output=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stderr);result=json.loads(run.stdout)['result'];self.assertFalse(result['isError'])
        cli=subprocess.run(command+['search','javaneedle','--mode','lexical'],text=True,capture_output=True,timeout=20)
        self.assertEqual(cli.returncode,0,cli.stderr);self.assertEqual(json.loads(cli.stdout),json.loads(result['content'][0]['text']))
        self.assertEqual(before,path.read_bytes())

    def test_config_failure_rolls_back_graph_and_fts_and_releases_writer(self):
        self.put('App.java','class App{static int leaf(){return 1;} static int run(){return leaf();}}');self.index();before=self.edges()
        self.put('App.java','class App{static int replacement(){return 1;}}')
        def fail(db,*args):raise sqlite3.OperationalError('synthetic Java config failure')
        with patch.dict(cg.LANGUAGE_ADAPTERS['java'],store_config=fail):
            with self.assertRaisesRegex(sqlite3.Error,'synthetic'):self.index()
        self.assertEqual(before,self.edges())
        with contextlib.closing(sqlite3.connect(self.root/'.code-graph/graph.db',timeout=0)) as db:
            db.execute('BEGIN IMMEDIATE');self.assertEqual(db.execute("SELECT count(*) FROM search_fts WHERE search_fts MATCH 'replacement'").fetchone()[0],0);db.rollback()

    def test_engine_copy_isolation_and_java_fingerprint_invalidation(self):
        engine=self.root.parent/'engine';shutil.copytree(REPO/'scripts',engine,ignore=shutil.ignore_patterns('__pycache__'))
        def load(name):
            spec=importlib.util.spec_from_file_location(name,engine/'cg.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
        a=load('java_copy_a');self.put('App.java','class App{static int leaf(){return 1;} static int run(){return leaf();}}')
        with contextlib.redirect_stdout(io.StringIO()):a.cmd_index(argparse.Namespace(root=str(self.root),full=False))
        before=dict(self.rows('SELECT k,v FROM meta'))['analysis_fingerprint']
        path=engine/'language_java.py';path.write_text(path.read_text()+"\nEXTENSIONS['.javaprobe']='java'\n")
        b=load('java_copy_b');self.assertNotIn('.javaprobe',a.LANG_BY_EXT);self.assertIn('.javaprobe',b.LANG_BY_EXT);self.assertNotIn('.javaprobe',cg.LANG_BY_EXT)
        with contextlib.redirect_stdout(io.StringIO()):b.cmd_index(argparse.Namespace(root=str(self.root),full=False))
        self.assertNotEqual(before,dict(self.rows('SELECT k,v FROM meta'))['analysis_fingerprint'])
        self.assertEqual(self.targets('App.run'),{('App.java','App.leaf')})


if __name__=='__main__':unittest.main()
