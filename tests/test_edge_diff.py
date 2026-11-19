"""Behavioral checks for normalized evidence and isolated engine comparisons."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('edge_diff_test', REPO/'tools/edge_diff.py')
edge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge)


class EdgeDiff(unittest.TestCase):
    def test_ids_do_not_matter_but_site_target_confidence_and_relation_do(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'graph.db'
            with sqlite3.connect(path) as db:
                db.executescript('''CREATE TABLE files(id,path); CREATE TABLE symbols(id,file_id,qname,line);
                    CREATE TABLE calls(id,file_id,line,name,recv,recv_name,nargs); CREATE TABLE edges(caller_id,callee_id,call_id,conf);
                    CREATE TABLE symbol_refs(file_id,owner_id,target_id,line,name,receiver,kind);
                    INSERT INTO files VALUES(8,'app.py'),(2,'lib.py');
                    INSERT INTO symbols VALUES(91,8,'drive',2),(12,2,'polish',1);
                    INSERT INTO calls VALUES(5,8,9,'alias',NULL,'',0); INSERT INTO edges VALUES(91,12,5,'high');
                    INSERT INTO symbol_refs VALUES(8,91,12,10,'polish',NULL,'callback');''')
            rows, indexed = edge.read_rows(path)
            self.assertEqual(rows, [('app.py','drive',9,'alias','lib.py','polish','high','call',2,1,'','',0,''),
                                    ('app.py','drive',10,'polish','lib.py','polish','medium','reference',2,1,'','',-1,'callback')])
            self.assertEqual(indexed, ['app.py','lib.py'])
            with sqlite3.connect(path) as db:
                db.execute('UPDATE symbols SET id=100 WHERE id=91')
                db.execute('UPDATE edges SET caller_id=100')
                db.execute('UPDATE symbol_refs SET owner_id=100')
            same, _ = edge.read_rows(path)
            self.assertEqual(edge.compare(rows,same)['added'], [])
            for position, value in ((2,99),(4,'decoy.py'),(6,'medium'),(7,'reference'),(8,50),(9,50),(10,'obj'),(11,'other'),(12,2)):
                changed = list(rows[0]);changed[position]=value
                delta = edge.compare(rows,[changed,rows[1]])
                self.assertEqual(len(delta['added']),1)
                self.assertEqual(len(delta['removed']),1)

    def test_old_database_without_references_is_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'graph.db'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE files(id,path); CREATE TABLE symbols(id,file_id,qname,line);'
                                 'CREATE TABLE calls(id,file_id,line,name,recv,recv_name,nargs); CREATE TABLE edges(caller_id,callee_id,call_id,conf);')
            self.assertEqual(edge.read_rows(path), ([],[]))

    def test_snapshot_excludes_state_and_symlinks_but_keeps_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'source';root.mkdir()
            (root/'x.py').write_text('pass\n')
            (root/'.code-graph').mkdir();(root/'.code-graph/graph.db').write_bytes(b'old')
            (root/'.code-graph-catalog.json').write_text('{}')
            (root/'external.py').symlink_to(root/'x.py')
            target=Path(temp)/'target'
            manifest=edge.snapshot(root,target)
            self.assertEqual(set(manifest),{'x.py','.code-graph-catalog.json'})
            self.assertFalse((target/'.code-graph/graph.db').exists())
            self.assertFalse((target/'external.py').exists())

    def test_commit_and_directory_engines_match_without_executing_project(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'input';root.mkdir()
            sentinel=Path(temp)/'executed'
            (root/'a.py').write_text(f'from pathlib import Path\nPath({str(sentinel)!r}).touch()\ndef polish(): pass\ndef drive(): polish()\n')
            head=edge.git(REPO,'rev-parse','HEAD').decode().strip()
            result=edge.run_comparison(head,str(REPO),[{'id':'fixture','group':'python','path':str(root)}])
            self.assertEqual(result['groups']['python']['added'],0)
            self.assertEqual(result['groups']['python']['removed'],0)
            self.assertGreater(result['groups']['python']['call_after'],0)
            self.assertFalse(sentinel.exists())
            self.assertFalse((root/'.code-graph').exists())
            self.assertFalse((root/'.git').exists())

    def test_empty_or_duplicate_projects_are_not_success(self):
        for projects in ([],[{'id':'x'},{'id':'x'}]):
            with self.assertRaises(ValueError):edge.run_comparison(str(REPO),str(REPO),projects)

    def test_empty_selected_input_and_empty_index_are_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'root'; root.mkdir()
            edge.git(root,'init','-q')
            (root/'.gitignore').write_text('ignored/\n')
            ignored=root/'ignored'; ignored.mkdir(); (ignored/'x.py').write_text('pass')
            readme=root/'readme'; readme.mkdir(); (readme/'README.md').write_text('no sources')
            build=root/'generated'; (build/'build').mkdir(parents=True)
            (build/'build/x.py').write_text('pass')
            for project in (ignored,readme,build):
                with self.subTest(project=project.name), self.assertRaises((ValueError,RuntimeError)):
                    edge.run_comparison(str(REPO),str(REPO),[{'id':'x','group':'x','path':str(project)}])

    def test_duplicate_edges_are_counted(self):
        row=('app.py','drive',2,'f','lib.py','f','high','call')
        delta=edge.compare([row,row],[row])
        self.assertEqual(delta['before'],2)
        self.assertEqual(delta['removed'],[row])

    def test_same_name_target_and_same_line_calls_remain_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'graph.db'
            with sqlite3.connect(path) as db:
                db.executescript('''CREATE TABLE files(id,path);
                    CREATE TABLE symbols(id,file_id,qname,line);
                    CREATE TABLE calls(id,file_id,line,name,recv,recv_name,nargs);
                    CREATE TABLE edges(caller_id,callee_id,call_id,conf);
                    INSERT INTO files VALUES(1,'app.js');
                    INSERT INTO symbols VALUES(1,1,'drive',1),(2,1,'polish',4),(3,1,'polish',8);
                    INSERT INTO calls VALUES(1,1,2,'polish','obj','left',0),(2,1,2,'polish','obj','right',1);
                    INSERT INTO edges VALUES(1,2,1,'high'),(1,3,2,'high');''')
            before,_=edge.read_rows(path)
            with sqlite3.connect(path) as db:
                db.execute('UPDATE edges SET callee_id=CASE callee_id WHEN 2 THEN 3 ELSE 2 END')
            after,_=edge.read_rows(path)
            self.assertEqual(len(edge.compare(before,after)['added']),2)

    def test_partial_parse_is_not_an_equal_complete_measurement(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'input';root.mkdir()
            (root/'broken.py').write_text('def broken(:\n')
            with self.assertRaisesRegex(RuntimeError,'[Pp]artial'):
                edge.run_comparison(str(REPO),str(REPO),[{'id':'broken','group':'python','path':str(root)}])

    def test_root_catalog_is_copied_even_if_git_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'input';root.mkdir();edge.git(root,'init','-q')
            (root/'.gitignore').write_text('.code-graph-catalog.json\n')
            catalog=root/'.code-graph-catalog.json';catalog.write_text('{"format":"codegraph-catalog/1","entries":[]}')
            target=Path(temp)/'copy';manifest=edge.snapshot(root,target)
            self.assertIn(catalog.name,manifest)
            self.assertEqual((target/catalog.name).read_bytes(),catalog.read_bytes())

    def test_fixture_discovery_keeps_standalone_files_and_families(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'single.py').write_text('pass')
            (root/'js/a').mkdir(parents=True);(root/'js/a/app.js').write_text('f()')
            (root/'mts').mkdir();(root/'mts/app.mts').write_text('f()')
            self.assertEqual({p['id'] for p in edge.fixture_projects(root)}, {'single.py','js/a','mts'})


if __name__=='__main__':unittest.main()
