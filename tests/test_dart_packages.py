"""Dart package config, URI identity and cache boundaries through the real engine."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
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
spec=importlib.util.spec_from_file_location('cg_dart_packages',REPO/'scripts/cg.py')
cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg)
ProjectData=runpy.run_path(str(REPO/'scripts/project_data.py'))['ProjectData']
ADAPTER=cg.LANGUAGE_ADAPTERS['dart']


class DartPackages(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='cg-dart-packages-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)/'project';self.root.mkdir()

    def put(self,name,source):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(source);return p

    def config(self,packages=None,name='.dart_tool/package_config.json'):
        return self.put(name,json.dumps(dict(configVersion=2,packages=packages if packages is not None else
            [dict(name='dep',rootUri='../deps/a',packageUri='lib/',languageVersion='3.6')]),ensure_ascii=False))

    def sources(self,imports="import 'package:dep/ops.dart' as p;",receiver='p.'):
        self.put('bin/app.dart',imports+'\nint drive()=>'+receiver+'leaf();\ndynamic selected()=>'+receiver+'leaf;\n')
        self.put('deps/a/lib/ops.dart','int leaf()=>11; int _secret()=>1; int other()=>2;\n')
        self.put('deps/b/lib/ops.dart','int leaf()=>22;\n')

    def index(self,full=False):
        with contextlib.redirect_stdout(io.StringIO()) as out:cg.cmd_index(argparse.Namespace(root=str(self.root),full=full))
        return out.getvalue()

    def rows(self,sql):
        with contextlib.closing(sqlite3.connect(self.root/'.code-graph/graph.db')) as db:return db.execute(sql).fetchall()

    def targets(self,owner):
        return set(self.rows("SELECT f.path,b.qname FROM edges e JOIN symbols a ON a.id=e.caller_id JOIN symbols b ON b.id=e.callee_id JOIN files f ON f.id=b.file_id WHERE a.qname='"+owner+"'"))

    def refs(self,owner):
        return set(self.rows("SELECT f.path,b.qname FROM symbol_refs r JOIN symbols a ON a.id=r.owner_id JOIN symbols b ON b.id=r.target_id JOIN files f ON f.id=b.file_id WHERE a.qname='"+owner+"'"))

    def graph(self):
        return self.rows('''SELECT f.path,a.qname,g.path,b.qname,c.line,c.name,e.conf FROM edges e
            JOIN symbols a ON a.id=e.caller_id JOIN symbols b ON b.id=e.callee_id JOIN calls c ON c.id=e.call_id
            JOIN files f ON f.id=a.file_id JOIN files g ON g.id=b.file_id ORDER BY 1,2,3,4,5,6,7''')

    def meta(self):return dict(self.rows('SELECT k,v FROM meta'))

    def assert_selected(self,path='deps/a/lib/ops.dart'):
        expected={(path,'leaf')} if path else set()
        self.assertEqual(self.targets('drive'),expected);self.assertEqual(self.refs('selected'),expected)

    def test_root_package_prefix_call_and_tearoff(self):
        self.sources();self.config();self.index();self.assert_selected()
        self.assertEqual(json.loads(self.meta()['dart_config'])['mode'],'packages')

    def test_package_filters_privacy_and_local_precedence(self):
        self.sources("import 'package:dep/ops.dart' show leaf;",'');self.config();self.index();self.assert_selected()
        self.sources("import 'package:dep/ops.dart' hide leaf;",'');self.index();self.assert_selected(None)
        self.put('bin/app.dart',"import 'package:dep/ops.dart';\nint leaf()=>3; int drive()=>leaf();\n")
        self.index();self.assertEqual(self.targets('drive'),{('bin/app.dart','leaf')})
        self.put('bin/app.dart',"import 'package:dep/ops.dart' as p; int drive()=>p._secret();")
        self.index();self.assertEqual(self.targets('drive'),set())

    def test_same_uri_duplicate_imports_deduplicate(self):
        self.sources("import 'package:dep/ops.dart'; import 'package:dep/ops.dart';",'')
        self.config();self.index();self.assert_selected()

    def test_mixed_package_and_file_uri_same_sid_remains_ambiguous(self):
        self.sources("import 'package:dep/ops.dart'; import '../deps/a/lib/ops.dart';",'')
        self.config();self.index();self.assert_selected(None)
        self.index(full=True);self.assert_selected(None)

    def test_separate_prefixes_preserve_source_targets(self):
        self.sources("import 'package:dep/ops.dart' as p; import '../deps/a/lib/ops.dart' as rel;")
        self.put('bin/app.dart',"import 'package:dep/ops.dart' as p; import '../deps/a/lib/ops.dart' as rel;\nint drive()=>p.leaf()+rel.leaf();\n")
        self.config();self.index();self.assertEqual(self.targets('drive'),{('deps/a/lib/ops.dart','leaf')})
        self.assertEqual(self.rows("SELECT count(*) FROM edges e JOIN symbols a ON a.id=e.caller_id WHERE a.qname='drive'"),[(2,)])

    def test_relative_import_different_file_and_package_contexts_abstains(self):
        self.config([dict(name='dep',rootUri='../dep',packageUri='lib/')])
        self.put('dep/lib/bridge.dart',"import '../lib/leaf.dart'; int bridge()=>leaf(); dynamic selected()=>leaf;")
        self.put('dep/lib/leaf.dart','int leaf()=>11;');self.put('dep/lib/lib/leaf.dart','int leaf()=>22;')
        self.put('bin/file.dart',"import '../dep/lib/bridge.dart'; int fromFile()=>bridge();")
        self.put('bin/package.dart',"import 'package:dep/bridge.dart'; int fromPackage()=>bridge();")
        self.index();self.assertEqual(self.targets('bridge'),set());self.assertEqual(self.refs('selected'),set())
        self.assertEqual(self.targets('fromFile'),{('dep/lib/bridge.dart','bridge')})
        self.assertEqual(self.targets('fromPackage'),{('dep/lib/bridge.dart','bridge')})

    def test_relative_import_safe_inside_package_root_keeps_binding(self):
        self.config([dict(name='dep',rootUri='../dep',packageUri='lib/')])
        self.put('dep/lib/sub/bridge.dart',"import '../leaf.dart'; int bridge()=>leaf();")
        self.put('dep/lib/leaf.dart','int leaf()=>11;');self.index()
        self.assertEqual(self.targets('bridge'),{('dep/lib/leaf.dart','leaf')})

    def test_no_config_keeps_relative_behavior_and_package_imports_unresolved(self):
        self.sources();self.index();self.assert_selected(None)
        self.sources("import '../deps/a/lib/ops.dart' as p;");self.index();self.assert_selected()

    def test_nested_config_blocks_imports_without_reparsing_sources(self):
        self.sources();self.config();self.put('nested/lib.dart','void unrelated(){}');self.index();self.assert_selected()
        self.config([],name='nested/.dart_tool/package_config.json')
        with patch.object(cg,'get_parser_for',side_effect=AssertionError('unexpected source parse')):self.index()
        self.assert_selected(None);self.assertEqual(json.loads(self.meta()['dart_config'])['mode'],'opaque')

    def test_ignored_root_and_nested_config_presence_is_fingerprinted(self):
        self.sources();self.config();self.put('nested/app.dart','void nested(){}');self.put('.gitignore','.dart_tool/\n')
        env={**os.environ,'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_COUNT':'0','GIT_TEMPLATE_DIR':''}
        for key in ('GIT_DIR','GIT_WORK_TREE','GIT_INDEX_FILE','GIT_COMMON_DIR'):env.pop(key,None)
        for args in (['init','-q'],['add','.']):subprocess.run(['git','-C',str(self.root),*args],env=env,check=True,capture_output=True)
        self.assertNotIn('.dart_tool/package_config.json',cg.checkout_files(self.root))
        self.index();self.assert_selected();before=self.meta()['config_hashes']
        self.config([],name='nested/.dart_tool/package_config.json')
        with self.assertRaisesRegex(ValueError,'Configurations'):ProjectData(cg,self.root)
        self.index();self.assert_selected(None);self.assertNotEqual(self.meta()['config_hashes'],before)

    def test_config_only_dirty_gate_with_independent_edge_and_reference(self):
        self.sources();path=self.config();self.put('anchor.py','def anchor():return 1\ndef keep():return anchor()\n')
        self.index();before=self.rows('SELECT path,sha FROM files ORDER BY path')
        for packages,target in [([dict(name='dep',rootUri='../deps/b',packageUri='lib/')],'deps/b/lib/ops.dart'),
                                ([dict(name='dep',rootUri='../missing',packageUri='lib/')],None),([],None)]:
            self.config(packages)
            with patch.object(cg,'get_parser_for',side_effect=AssertionError('unexpected source parse')):self.index()
            self.assert_selected(target);self.assertEqual(before,self.rows('SELECT path,sha FROM files ORDER BY path'))
            self.assertEqual(self.targets('keep'),{('anchor.py','anchor')})
            old=(self.graph(),self.refs('selected'));self.index(full=True)
            self.assertEqual(old,(self.graph(),self.refs('selected')))
        path.unlink();self.index();self.assert_selected(None)

    def test_parser_normalizes_omitted_dot_segments_and_utf8_roots(self):
        self.sources();self.config([dict(name='dep',rootUri='../deps/a/lib')]);self.index();self.assert_selected()
        self.config([dict(name='dep',rootUri='../deps/a',packageUri='lib/%2e%2e/lib')]);self.index();self.assert_selected()
        self.put('pkg é/lib/ops.dart','int leaf()=>33;')
        self.config([dict(name='dep',rootUri='../pkg%20%C3%A9',packageUri='lib')]);self.index();self.assert_selected('pkg é/lib/ops.dart')

    def test_raw_uri_spaces_cannot_select_the_trimmed_path(self):
        self.sources();self.config();self.index();self.assert_selected()
        self.put('deps/a/ lib/ops.dart','int leaf()=>22;')
        self.put('.dart_tool/ ../deps/a/lib/ops.dart','int leaf()=>33;')
        for root,package in (('../deps/a',' lib/'),(' ../deps/a','lib/')):
            with self.subTest(root=root,package=package):
                self.config([dict(name='dep',rootUri=root,packageUri=package)])
                for full in (False,True):
                    self.index(full=full);self.assert_selected(None)
                    self.assertEqual(json.loads(self.meta()['dart_config'])['mode'],'opaque')

    def test_percent_encoded_leading_spaces_and_utf8_keep_exact_targets(self):
        self.sources();self.put('deps/a/ lib/ops.dart','int leaf()=>22;')
        self.put(' pkg é/lib/ops.dart','int leaf()=>33;')
        for root,package,target in (('../deps/a','%20lib/','deps/a/ lib/ops.dart'),
                                    ('../%20pkg%20%C3%A9','lib/',' pkg é/lib/ops.dart')):
            with self.subTest(root=root,package=package):
                self.config([dict(name='dep',rootUri=root,packageUri=package)])
                for full in (False,True):self.index(full=full);self.assert_selected(target)

    def test_empty_root_query_and_fragment_invalidate_existing_call_and_reference(self):
        self.sources();self.config();self.index();self.assert_selected()
        for root in ('../deps/a?','../deps/a#'):
            with self.subTest(root=root):
                self.config([dict(name='dep',rootUri=root,packageUri='lib/')])
                for full in (False,True):
                    self.index(full=full);self.assert_selected(None)
                    self.assertEqual(json.loads(self.meta()['dart_config'])['mode'],'opaque')

    def test_unused_all_dot_names_invalidate_whole_mapping(self):
        self.sources()
        packages=[dict(name='dep',rootUri='../deps/a',packageUri='lib/'),
                  dict(name='...x',rootUri='file:///unopened/dart-package')]
        self.config(packages);self.index();self.assert_selected()
        for name in ('.','..','...','....'):
            with self.subTest(name=name):
                packages[1]['name']=name;self.config(packages)
                for full in (False,True):
                    self.index(full=full);self.assert_selected(None)
                    self.assertEqual(json.loads(self.meta()['dart_config'])['mode'],'opaque')

    def test_invalid_global_config_does_not_keep_internal_mapping(self):
        self.sources();self.config();self.index();self.assert_selected()
        variants=[dict(configVersion=True,packages=[]),dict(configVersion=2.0,packages=[]),dict(configVersion=3,packages=[]),
            dict(configVersion=2,packages=[dict(name='dep',rootUri='../deps/a',packageUri='../b/lib')]),
            dict(configVersion=2,packages=[dict(name='dep',rootUri='../deps/a',languageVersion='03.6')]),
            dict(configVersion=2,packages=[dict(name='dep',rootUri='../deps/a?',packageUri='lib/')]),
            dict(configVersion=2,packages=[dict(name='dep',rootUri='../deps%2fa')]),
            dict(configVersion=2,packages=[dict(name='dep',rootUri='../bad\ud800')]),
            dict(configVersion=2,packages=[dict(name='dep',rootUri='../deps/a',packageUri='lib/\ud800')]),
            dict(configVersion=2,packages=[dict(name='dep',rootUri='../deps/a'),dict(name='dep',rootUri='../deps/b')]),
            dict(configVersion=2,packages=[dict(name='dep',rootUri='../deps/a'),dict(name='other',rootUri='../deps/a')]),
            dict(configVersion=2,packages=[dict(name='dep',rootUri='../deps/a',packageUri='lib/'),dict(name='other',rootUri='../deps/a/lib/nested')])]
        for value in variants:
            with self.subTest(value=value):
                self.put('.dart_tool/package_config.json',json.dumps(value));self.index();self.assert_selected(None)
        self.put('.dart_tool/package_config.json','{"configVersion":2,"packages":[],"extra":NaN}')
        self.index();self.assert_selected(None)

    def test_duplicate_unused_external_roots_invalidate_whole_config(self):
        self.sources();outside=(self.root.parent/'nonexistent').as_uri()
        self.config([dict(name='dep',rootUri='../deps/a',packageUri='lib/'),dict(name='external1',rootUri=outside),dict(name='external2',rootUri=outside)])
        self.index();self.assert_selected(None)

    def test_large_config_is_opaque_instead_of_selecting_a_prefix(self):
        self.sources()
        packages=[dict(name='dep',rootUri='../deps/a',packageUri='lib/')]
        packages.extend(dict(name='external'+str(n),rootUri='file:///unopened/dependency'+str(n)) for n in range(500))
        self.config(packages);self.index();self.assert_selected(None)

    def test_external_missing_ignored_and_escaped_targets_never_fall_back(self):
        self.sources();self.config([dict(name='dep',rootUri=(self.root.parent/'external').as_uri())]);self.index();self.assert_selected(None)
        self.config();self.put('.cgignore','deps/a/*\n');self.index();self.assert_selected(None)
        (self.root/'.cgignore').unlink();self.sources("import 'package:dep/../lib/ops.dart' as p;");self.index();self.assert_selected(None)

    def test_unknown_import_does_not_disappear_from_ambiguity(self):
        self.sources("import 'package:dep/ops.dart'; import 'package:missing/ops.dart';",'');self.config();self.index();self.assert_selected(None)
        self.sources("import 'package:dep/ops.dart'; import 'package:missing/ops.dart' show other;",'');self.index();self.assert_selected()

    def test_symlink_config_file_and_directory_are_boundaries(self):
        self.sources();path=self.config();self.index();self.assert_selected()
        outside=self.root.parent/'outside';outside.mkdir();(outside/'package_config.json').write_text(path.read_text())
        path.unlink();path.symlink_to(outside/'package_config.json');self.index();self.assert_selected(None)
        self.assertTrue(cg.configuration_files(self.root)['.dart_tool/package_config.json'].startswith(b'\0codegraph-dart-config:symlink:'))
        path.unlink();path.parent.rmdir();path.parent.symlink_to(outside,target_is_directory=True)
        self.index();self.assert_selected(None)
        self.assertIn('.dart_tool/package_config.json',cg.dart_config_paths(self.root))
        self.assertTrue(cg.configuration_files(self.root)['.dart_tool/package_config.json'].startswith(b'\0codegraph-dart-config:directory-symlink:'))

    def test_nested_symlink_boundary_cannot_inherit_root_config(self):
        self.sources();self.config();self.put('nested/app.dart','void nested(){}');self.index();self.assert_selected()
        directory=self.root.parent/'outside';directory.mkdir()
        (self.root/'nested/.dart_tool').symlink_to(directory,target_is_directory=True)
        self.index();self.assert_selected(None)
        self.assertIn('nested/.dart_tool/package_config.json',cg.configuration_files(self.root))

    def test_oversize_and_nonregular_config_never_block_or_inherit(self):
        self.sources();path=self.config();self.index();self.assert_selected()
        path.write_bytes(b' '*200001);self.index();self.assert_selected(None)
        path.unlink();os.mkfifo(path);self.index();self.assert_selected(None)
        path.unlink();path.parent.rmdir();os.mkfifo(path.parent);self.index();self.assert_selected(None)

    def test_generated_dart_tool_sources_do_not_supply_targets(self):
        self.sources("import 'package:dep/ops.dart' as p;");self.config([dict(name='dep',rootUri='.',packageUri='generated/')])
        self.put('.dart_tool/generated/ops.dart','int leaf()=>1;');self.index();self.assert_selected(None)
        self.assertFalse(any(path.startswith('.dart_tool/') for path, in self.rows('SELECT path FROM files')))

    def test_config_race_rolls_back_consistent_graph_and_metadata(self):
        self.sources();path=self.config();self.index();before=self.meta();before_edges=self.targets('drive')
        original=ADAPTER['store_config']
        def changing(db,contents,root):
            result=original(db,contents,root);path.write_text('{"configVersion":2,"packages":[]}');return result
        with patch.dict(ADAPTER,store_config=changing):
            with self.assertRaisesRegex(SystemExit,'during index'):self.index()
        self.assertEqual(self.meta(),before);self.assertEqual(self.targets('drive'),before_edges)
        with contextlib.closing(sqlite3.connect(self.root/'.code-graph/graph.db',timeout=0)) as db:db.execute('BEGIN IMMEDIATE');db.rollback()

    def test_store_database_failure_releases_writer(self):
        self.sources();self.config();self.index();before=self.targets('drive')
        def fail(*args):raise sqlite3.OperationalError('synthetic config failure')
        with patch.dict(ADAPTER,store_config=fail):
            with self.assertRaisesRegex(sqlite3.Error,'synthetic'):self.index()
        self.assertEqual(self.targets('drive'),before)
        with contextlib.closing(sqlite3.connect(self.root/'.code-graph/graph.db',timeout=0)) as db:db.execute('BEGIN IMMEDIATE');db.rollback()

    def test_stale_config_rejected_by_projectdata_cli_and_mcp(self):
        self.sources();self.config();self.index();before=(self.root/'.code-graph/graph.db').read_bytes()
        self.config([])
        with self.assertRaisesRegex(ValueError,'Configurations'):ProjectData(cg,self.root)
        command=[sys.executable,'-I','-B',str(REPO/'scripts/cg.py'),'--root',str(self.root)]
        run=subprocess.run(command+['callees','drive'],capture_output=True,text=True,timeout=20)
        self.assertNotEqual(run.returncode,0);self.assertIn('Dart configuration or root changed',run.stderr)
        request=dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='cg_search',arguments=dict(query='drive')))
        run=subprocess.run(command+['mcp'],input=json.dumps(request)+'\n',capture_output=True,text=True,timeout=20)
        self.assertTrue(json.loads(run.stdout)['result']['isError'])
        self.assertEqual((self.root/'.code-graph/graph.db').read_bytes(),before)

    def test_moved_root_requires_reindex_even_with_identical_config_bytes(self):
        self.sources();self.config([dict(name='dep',rootUri=(self.root/'deps/a').resolve().as_uri(),packageUri='lib/')]);self.index()
        self.assert_selected()
        before=self.meta()['content_revision'];copy=self.root.parent/'moved';shutil.copytree(self.root,copy)
        with self.assertRaisesRegex(ValueError,'Dart'):ProjectData(cg,copy)
        self.root=copy;self.index();self.assert_selected(None);self.assertNotEqual(self.meta()['content_revision'],before)
        (self.root/'.dart_tool/package_config.json').unlink()
        self.sources("import '../deps/a/lib/ops.dart' as p;");self.index();self.assert_selected()
        again=self.root.parent/'moved-file-context';shutil.copytree(self.root,again)
        with ProjectData(cg,again) as project:self.assertIn('bin/app.dart',project.files)


if __name__=='__main__':unittest.main()
