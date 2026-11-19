"""Swift file-local subset through the actual parser, index and readonly consumers."""
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
spec=importlib.util.spec_from_file_location('cg_swift_tests',REPO/'scripts/cg.py')
cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg)
ProjectData=runpy.run_path(str(REPO/'scripts/project_data.py'))['ProjectData']


class Swift(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='cg-swift-');self.addCleanup(temporary.cleanup)
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

    def refs(self,caller):
        return set(self.rows(f'''SELECT f.path,b.qname FROM symbol_refs r JOIN symbols a ON a.id=r.owner_id
            JOIN symbols b ON b.id=r.target_id JOIN files f ON f.id=b.file_id WHERE a.qname='{caller}' '''))

    def test_private_fileprivate_functions_and_recursive_calls(self):
        self.put('main.swift','private func leaf()->Int {return 1}\nfileprivate func other()->Int{return leaf()}\n'
                 'private func recur(_ n:Int)->Int {return n>0 ? recur(n-1):leaf()}\nfunc drive()->Int{return other()+recur(1)}\n')
        self.index()
        self.assertEqual(self.targets('other'),{('main.swift','leaf')})
        self.assertEqual(self.targets('recur'),{('main.swift','recur'),('main.swift','leaf')})
        self.assertEqual(self.targets('drive'),{('main.swift','other'),('main.swift','recur')})

    def test_internal_public_and_cross_file_bindings_abstain(self):
        self.put('a.swift','private func leaf()->Int{return 1}\nfunc local()->Int{return leaf()}\nfunc ordinary()->Int{return 2}\n')
        self.put('b.swift','func remote()->Int{return leaf()+ordinary()}\n')
        self.index();self.assertEqual(self.targets('local'),{('a.swift','leaf')});self.assertEqual(self.targets('remote'),set())

    def test_same_name_groups_include_labels_access_and_other_files(self):
        self.put('a.swift','private func leaf(_ n:Int)->Int{return 1}\nfunc leaf(text:String)->Int{return 2}\nfunc drive()->Int{return leaf(1)}\n')
        self.put('b.swift','private func leaf()->Int{return 3}\n')
        self.index();self.assertEqual(self.edges(),set())

    def test_stdlib_type_function_and_value_names_do_not_form_false_singletons(self):
        self.put('main.swift','private func abs(_ value:Int)->Int{return 1001}\n'
                 'func drive()->Double{return abs(-2.0)}\n')
        self.index();self.assertEqual(self.edges(),set())
        guards=cg.LANGUAGE_ADAPTERS['swift']['STDLIB_NAMES']
        self.assertTrue({'abs','Int','String','print','min','max','Task','withUnsafeCurrentTask','Regex'}<=guards)
        self.put('main.swift','private struct Task {static func leaf()->Int{return 1}}\nfunc drive()->Int{return Task.leaf()}\n')
        self.index();self.assertEqual(self.edges(),set())

    def test_static_methods_on_private_nominal_types(self):
        self.put('main.swift','private struct Item {static func leaf()->Int{return 1}}\n'
                 'fileprivate final class Repo {static func save()->Int{return 2}}\n'
                 'func drive()->Int{return Item.leaf()+Repo.save()}\n')
        self.index();self.assertEqual(self.targets('drive'),{('main.swift','Item.leaf'),('main.swift','Repo.save')})

    def test_enum_cases_block_same_name_static_methods(self):
        self.put('main.swift','private enum ZXChoice {\ncase leaf(Int)\nstatic func leaf(_ value:String)->Int{return 1001}\n}\n'
                 'private func drive()->ZXChoice{return ZXChoice.leaf(2)}\nprint(drive())\n')
        self.index();self.assertEqual(self.targets('drive'),set())
        self.index(full=True);self.assertEqual(self.targets('drive'),set())

    def test_typealias_extension_does_not_form_false_static_singleton(self):
        self.put('main.swift','private struct ZXBox {static func leaf(_ value:Int)->Int{return 1001}}\n'
                 'private typealias Alias=ZXBox\nextension Alias {static func leaf(_ value:Double)->Double{return value.magnitude}}\n'
                 'private func drive()->Double{return ZXBox.leaf(-2.0)}\nprint(drive())\n')
        self.index();self.assertEqual(self.targets('drive'),set())
        self.index(full=True);self.assertEqual(self.targets('drive'),set())

    def test_instance_class_methods_and_non_private_types_abstain(self):
        self.put('main.swift','struct Item {static func leaf()->Int{return 1}}\n'
                 'private class Repo {class func save()->Int{return 2}\nfunc get()->Int{return 3}}\n'
                 'func drive(_ value:Repo)->Int{return Item.leaf()+Repo.save()+value.get()}\n')
        self.index();self.assertEqual(self.edges(),set())

    def test_member_contexts_and_extensions_preserve_distinct_owners(self):
        self.put('main.swift','private func leaf()->Int{return 1}\nprivate struct Item {static func save()->Int{return leaf()}}\n'
                 'extension Item {func get()->Int{return leaf()}}\nfunc drive()->Int{return Item.save()}\n')
        self.index();self.assertEqual(self.edges(),set())
        owners=self.rows("SELECT qname FROM symbols WHERE kind='class'")
        self.assertTrue(any('extension Item@' in name for name, in owners))

    def test_inherited_generic_and_protocol_types_abstain(self):
        self.put('main.swift','private protocol P {}\nprivate struct Item:P {static func leaf()->Int{return 1}}\n'
                 'private struct Box<T> {static func save()->Int{return 2}}\n'
                 'func drive()->Int{return Item.leaf()+Box<Int>.save()}\n')
        self.index();self.assertEqual(self.edges(),set())

    def test_local_variables_parameters_and_type_parameters_shadow_candidates(self):
        self.put('main.swift','private func leaf()->Int{return 1}\nprivate struct Item {static func save()->Int{return 2}}\n'
                 'func parameter(_ leaf:()->Int)->Int{return leaf()}\n'
                 'func local()->Int{let leaf=other;return leaf()}\nfunc other()->Int{return 3}\n'
                 'func typeParam<Item>()->Int{return Item.save()}\n'
                 'func valueParam(_ Item:Int)->Int{return Item.save()}\n')
        self.index();self.assertEqual(self.edges(),set())

    def test_closures_nested_functions_and_complex_patterns_abstain(self):
        self.put('main.swift','private func leaf()->Int{return 1}\n'
                 'func closure()->Int{let f={leaf()};return f()}\n'
                 'func nested()->Int{func inner()->Int{return leaf()};return leaf()}\n'
                 'func tuple()->Int{let (leaf,x)=(other,1);return leaf()}\nfunc other()->Int{return 2}\n')
        self.index();self.assertEqual(self.edges(),set())

    def test_unknown_receivers_do_not_fall_through_to_global_names(self):
        self.put('main.swift','private func leaf()->Int{return 1001}\nprivate struct Item {func leaf()->Int{return 2}}\n'
                 'func drive()->Int{return Item().leaf()}\nfunc refs(){let f=Item().leaf;_=f()}\n')
        self.index();self.assertEqual(self.edges(),set());self.assertEqual(self.refs('refs'),set())

    def test_tearoffs_are_references_and_values_remain_unresolved_calls(self):
        self.put('main.swift','private func leaf()->Int{return 1}\nprivate struct Item {static func save()->Int{return 2}}\n'
                 'func drive()->Int{let f=leaf;let g=Item.save;return f()+g()}\n')
        self.index();self.assertEqual(self.edges(),set())
        self.assertEqual(self.refs('drive'),{('main.swift','leaf'),('main.swift','Item.save')})

    def test_labels_only_selection_is_a_reference_not_an_invocation(self):
        self.put('main.swift','private func leaf(_ value:Int)->Int{return value}\n'
                 'private struct Item {static func save(value:Int)->Int{return value}}\n'
                 'func refs(){let f=leaf(_:);let g=Item.save(value:);_=f(1);_=g(2)}\n')
        self.index();self.assertEqual(self.edges(),set())
        self.assertEqual(self.refs('refs'),{('main.swift','leaf'),('main.swift','Item.save')})
        self.assertEqual(self.rows("SELECT count(*) FROM calls WHERE name IN ('leaf','save')"),[(0,)])

    def test_top_level_closures_do_not_rebind_parameters_as_private_globals(self):
        self.put('main.swift','private func leaf()->Int{return 1001}\n'
                 'private func apply(_ f:(()->Int)->Int)->Int{return f(leaf)}\n'
                 'print(apply { leaf in leaf() })\n')
        self.index();self.assertEqual(self.targets('main.swift'),set())

    def test_cross_file_blockers_recompute_calls_and_references_incrementally(self):
        self.put('main.swift','private func zxqLeaf(_ n:Int)->Int{return n}\n'
                 'func drive()->Int{let f=zxqLeaf;return zxqLeaf(1)}\n')
        self.index();self.assertEqual(self.targets('drive'),{('main.swift','zxqLeaf')});self.assertTrue(self.refs('drive'))
        other=self.put('Other.swift','import Foundation\nfunc zxqLeaf(_ n:Double)->Double{return n.magnitude}\n')
        self.index();self.assertEqual(self.targets('drive'),set());self.assertEqual(self.refs('drive'),set())
        self.index(full=True);self.assertEqual(self.targets('drive'),set())
        other.unlink();self.index();self.assertEqual(self.targets('drive'),{('main.swift','zxqLeaf')});self.assertTrue(self.refs('drive'))
        other.write_text('func unknown( {')
        self.index();self.assertEqual(self.targets('drive'),set());self.assertEqual(self.refs('drive'),set())

    def test_named_tuple_array_and_dictionary_values_keep_references_without_labels(self):
        self.put('main.swift','private func leaf()->Int{return 1}\nprivate func item()->Int{return 2}\n'
                 'func refs(){let r=(item:leaf);let p=(leaf,0);let a=[leaf];let d=["x":leaf]}\n')
        self.index();self.assertEqual(self.refs('refs'),{('main.swift','leaf')})
        self.assertEqual(self.rows("SELECT count(*) FROM symbol_refs WHERE name='leaf' AND target_id IS NOT NULL"),[(4,)])

    def test_chained_call_heads_preserve_argument_references(self):
        self.put('main.swift','private func leaf()->Int{return 1}\nprivate func wrap(_ f:()->Int)->()->Int{return f}\n'
                 'func drive()->Int{return wrap(leaf)()}\n')
        self.index();self.assertEqual(self.targets('drive'),{('main.swift','wrap')})
        self.assertEqual(self.refs('drive'),{('main.swift','leaf')})

    def test_initializers_defaults_and_computed_bodies_keep_callsites(self):
        self.put('main.swift','private func leaf()->Int{return 1}\nlet boot=leaf()\n'
                 'private struct Item {init(value:Int=leaf()){}\nvar computed:Int{return leaf()}}\n'
                 'func drive(value:Int=leaf())->Int{return value}\n')
        self.index()
        self.assertEqual(self.targets('main.swift'),{('main.swift','leaf')})
        self.assertEqual(self.targets('drive'),{('main.swift','leaf')})
        self.assertEqual(self.rows("SELECT count(*) FROM calls WHERE name='leaf'"),[(4,)])

    def test_imports_directives_attributes_and_partial_sources_exclude_resolution(self):
        sources={'import.swift':'import Foundation\nprivate func leaf()->Int{return 1}\nfunc run()->Int{return leaf()}\n',
            'directive.swift':'#if os(macOS)\nprivate func leaf()->Int{return 1}\n#endif\nfunc run()->Int{return leaf()}\n',
            'attribute.swift':'@inline(__always) private func unique()->Int{return 1}\nfunc run()->Int{return unique()}\n',
            'partial.swift':'private func broken()->Int{return 1}\nclass Broken{'}
        for path,source in sources.items():self.put(path,source)
        self.index();self.assertEqual(self.edges(),set())

    def test_backticks_and_unicode_identifiers_are_canonical(self):
        self.put('main.swift','private func `repeat`()->Int{return 1}\nprivate func café()->Int{return 2}\n'
                 'func drive()->Int{return `repeat`()+café()}\n')
        self.index();self.assertEqual(self.targets('drive'),{('main.swift','repeat'),('main.swift','café')})

    def test_incremental_target_change_deletion_and_full_rebuild_agree(self):
        source='private func leaf()->Int{return 1}\nfunc drive()->Int{return leaf()}\n'
        path=self.put('main.swift',source);self.index();self.assertTrue(self.edges())
        path.write_text(source.replace('private func leaf','func leaf'));self.index();self.assertEqual(self.edges(),set())
        self.index(full=True);self.assertEqual(self.edges(),set());path.unlink();self.index();self.assertEqual(self.rows('SELECT count(*) FROM files'),[(0,)])

    def test_swiftpm_build_cache_does_not_supply_overload_blockers(self):
        self.put('main.swift','private func leaf()->Int{return 1}\nfunc drive()->Int{return leaf()}\n')
        self.put('.build/generated.swift','func leaf(_ n:Int)->Int{return n}\n')
        self.index();self.assertEqual(self.targets('drive'),{('main.swift','leaf')})
        self.assertEqual(self.rows('SELECT path FROM files'),[('main.swift',)])

    def test_engine_copy_isolation_and_guard_fingerprint_invalidation(self):
        engine=self.root.parent/'engine';shutil.copytree(REPO/'scripts',engine,ignore=shutil.ignore_patterns('__pycache__'))
        def load(name):
            spec=importlib.util.spec_from_file_location(name,engine/'cg.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
        a=load('swift_copy_a');self.put('main.swift','private func leaf()->Int{return 1}\nfunc drive()->Int{return leaf()}\n')
        with contextlib.redirect_stdout(io.StringIO()):a.cmd_index(argparse.Namespace(root=str(self.root),full=False))
        before=dict(self.rows('SELECT k,v FROM meta'))['analysis_fingerprint']
        path=engine/'language_swift.py';path.write_text(path.read_text()+"\nEXTENSIONS['.swiftprobe']='swift'\n")
        b=load('swift_copy_b');self.assertNotIn('.swiftprobe',a.LANG_BY_EXT);self.assertIn('.swiftprobe',b.LANG_BY_EXT);self.assertNotIn('.swiftprobe',cg.LANG_BY_EXT)
        with contextlib.redirect_stdout(io.StringIO()):b.cmd_index(argparse.Namespace(root=str(self.root),full=False))
        self.assertNotEqual(before,dict(self.rows('SELECT k,v FROM meta'))['analysis_fingerprint']);self.assertTrue(self.edges())

    def test_cli_mcp_search_full_spans_and_readonly(self):
        self.put('main.swift','private func unrelated()->Int{return 1}; private func matched()->Int {\nreturn 2 // swiftneedle\n}\n')
        self.index()
        with ProjectData(cg,self.root) as project:
            result=cg.search_module()['search'](project,'swiftneedle',mode='lexical');names=[item['symbol']['name'] for item in result['items']]
            self.assertIn('matched',names);self.assertNotIn('unrelated',names)
        path=self.root/'.code-graph/graph.db';before=path.read_bytes()
        command=[sys.executable,'-I',str(REPO/'scripts/cg.py'),'--root',str(self.root)]
        request=dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='cg_search',arguments=dict(query='swiftneedle',mode='lexical')))
        run=subprocess.run(command+['mcp'],input=json.dumps(request)+'\n',capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stderr);result=json.loads(run.stdout)['result'];self.assertFalse(result['isError'])
        cli=subprocess.run(command+['search','swiftneedle','--mode','lexical'],capture_output=True,text=True,timeout=20)
        self.assertEqual(cli.returncode,0,cli.stderr);self.assertEqual(json.loads(cli.stdout),json.loads(result['content'][0]['text']))
        self.assertEqual(before,path.read_bytes())

    def test_resolver_failure_rolls_back_and_releases_writer(self):
        self.put('main.swift','private func leaf()->Int{return 1}\nfunc drive()->Int{return leaf()}\n');self.index();before=self.edges()
        self.put('main.swift','private func replacement()->Int{return 2}\n')
        def fail(db):raise sqlite3.OperationalError('synthetic Swift failure')
        with patch.dict(cg.LANGUAGE_ADAPTERS['swift'],SwiftFiles=fail):
            with self.assertRaisesRegex(sqlite3.Error,'synthetic'):self.index()
        self.assertEqual(before,self.edges())
        with contextlib.closing(sqlite3.connect(self.root/'.code-graph/graph.db',timeout=0)) as db:
            db.execute('BEGIN IMMEDIATE');self.assertEqual(db.execute("SELECT count(*) FROM search_fts WHERE search_fts MATCH 'replacement'").fetchone()[0],0);db.rollback()


if __name__=='__main__':unittest.main()
