"""SQLite resource syntax through indexing, source consumers and native authorizer."""
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
spec=importlib.util.spec_from_file_location('cg_sql_tests',REPO/'scripts/cg.py')
cg=importlib.util.module_from_spec(spec);spec.loader.exec_module(cg)
ProjectData=runpy.run_path(str(REPO/'scripts/project_data.py'))['ProjectData']


class SQL(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='cg-sql-');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/'project';self.root.mkdir()

    def put(self,path,source):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(source);return p

    def index(self,full=False):
        with contextlib.redirect_stdout(io.StringIO()):cg.cmd_index(argparse.Namespace(root=str(self.root),full=full))

    def rows(self,sql):
        with contextlib.closing(sqlite3.connect(self.root/'.code-graph/graph.db')) as db:return db.execute(sql).fetchall()

    def facts(self,kind):
        return [json.loads(value) for value, in self.rows("SELECT value FROM observations WHERE kind='sql."+kind+"' ORDER BY id")]

    def architecture(self,limit=500):
        with ProjectData(cg,self.root) as p:return p.architecture(limit)['sql']

    def test_table_view_and_query_owners_have_complete_spans(self):
        self.put('schema.sql','CREATE TABLE users(id INTEGER);\nCREATE VIEW active AS\nSELECT id FROM users;\nSELECT * FROM active;')
        self.index();self.assertEqual(self.rows("SELECT name,kind,line,end_line FROM symbols WHERE kind!='module' ORDER BY id"),
            [('users','table',1,1),('active','view',2,3),('<select@4:0>','query',4,4)])
        self.assertEqual([x['name'] for x in self.facts('read')],['users','active'])
        self.assertEqual(len(self.architecture()['links']),2)

    def test_alias_columns_and_functions_are_not_table_reads(self):
        self.put('query.sql','SELECT length(u.name) AS alias FROM users u JOIN other o ON u.id=o.id;')
        self.index();self.assertEqual([x['name'] for x in self.facts('read')],['users','other'])
        self.assertEqual(self.rows('SELECT count(*) FROM calls'),[(0,)])

    def test_insert_update_delete_keep_writes_and_subquery_reads(self):
        self.put('query.sql','INSERT INTO dest SELECT * FROM src;\nUPDATE dest SET id=1 WHERE id IN (SELECT id FROM src);\nDELETE FROM dest WHERE id IN (SELECT id FROM src);')
        self.index();self.assertEqual([x['name'] for x in self.facts('write')],['dest']*3)
        self.assertEqual([x['name'] for x in self.facts('read')],['src']*3)

    def test_ctes_shadow_reads_but_explicit_schema_keeps_resource(self):
        self.put('query.sql','WITH /*cte*/ users AS (SELECT * FROM source) SELECT * FROM users JOIN main.users u ON 1;')
        self.index();self.assertEqual(self.facts('read'),[dict(name='source',schema=None),dict(name='users',schema='main')])

    def test_recursive_and_nested_cte_names_abstain_conservatively(self):
        self.put('query.sql','WITH RECURSIVE nums(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM nums WHERE n<3) SELECT * FROM nums;\n'
                 'SELECT * FROM real WHERE id IN (WITH real AS (SELECT 1) SELECT * FROM real);')
        self.index();self.assertTrue(self.facts('file')[0]['supported']);self.assertEqual(self.facts('read'),[])

    def test_ascii_case_quotes_and_unicode_do_not_use_casefold(self):
        self.put('schema.sql','CREATE TABLE main."Odd Name"(id INTEGER);\nSELECT * FROM MAIN."ODD NAME";\n'
                 'CREATE TABLE "straße"(id INTEGER);\nSELECT * FROM "STRASSE";')
        self.index();self.assertEqual([x['name'] for x in self.facts('declare')],['odd name','straße'])
        self.assertEqual(len(self.architecture()['links']),1)

    def test_temporary_and_other_schema_declarations_block_name_candidates(self):
        self.put('schema.sql','CREATE TABLE main.t(id INTEGER);\nCREATE TEMP TABLE t(id INTEGER);\nSELECT * FROM t;\nSELECT * FROM main.t;')
        self.index();self.assertEqual(self.architecture()['links'],[])

    def test_duplicate_declarations_do_not_pick_a_schema_owner(self):
        self.put('schema.sql','CREATE TABLE t(id INTEGER);\nCREATE TABLE IF NOT EXISTS t(id INTEGER);\nSELECT * FROM t;')
        self.index();self.assertEqual(self.architecture()['links'],[])

    def test_cross_file_names_never_imply_database_or_migration_order(self):
        self.put('schema.sql','CREATE TABLE t(id INTEGER);');self.put('query.sql','SELECT * FROM t;')
        self.index();self.assertEqual(self.architecture()['links'],[])

    def test_unsupported_and_partial_sources_do_not_claim_access_inventory(self):
        for name,sql in [('pragma.sql','PRAGMA foreign_keys=ON; SELECT * FROM t;'),
                         ('trigger.sql','CREATE TRIGGER tr AFTER INSERT ON t BEGIN SELECT 1; END;'),
                         ('partial.sql','CREATE TABLE broken('),
                         ('foreign.sql','CREATE TABLE t(id INTEGER REFERENCES u(id)); SELECT * FROM t;')]:self.put(name,sql)
        self.index();self.assertTrue(all(not x['supported'] for x in self.facts('file')))
        self.assertEqual(self.facts('read'),[]);self.assertEqual(self.facts('write'),[])

    def test_table_valued_functions_are_not_named_table_or_call_targets(self):
        self.put('query.sql',"SELECT * FROM json_each('[]');")
        self.index();self.assertEqual(self.facts('read'),[]);self.assertEqual(self.rows('SELECT count(*) FROM calls'),[(0,)])

    def test_create_table_as_select_and_transaction_wrappers(self):
        self.put('schema.sql','BEGIN TRANSACTION; CREATE TABLE copied AS SELECT * FROM original; SELECT * FROM copied; COMMIT;')
        self.index();self.assertTrue(self.facts('file')[0]['supported'])
        self.assertEqual([x['name'] for x in self.facts('read')],['original','copied'])
        self.assertEqual(len(self.architecture()['links']),1)

    def test_sql_never_resolves_calls_references_or_inheritance(self):
        self.put('query.sql','SELECT leaf(id) FROM leaf;');self.put('app.py','def leaf(x): return x\ndef entry(): return leaf(1)\n')
        for n in range(5):self.put(f'schema{n}.sql','CREATE TABLE leaf(id INTEGER);')
        self.index();self.assertEqual(self.rows("SELECT count(*) FROM calls c JOIN files f ON f.id=c.file_id WHERE f.lang='sql'"),[(0,)])
        self.assertEqual(self.rows("SELECT count(*) FROM edges"),[(1,)])
        self.assertEqual(self.rows("SELECT count(*) FROM symbol_refs"),[(0,)])

    def test_no_analyzed_sql_is_prepared_or_executed(self):
        self.put('query.sql',"SELECT load_extension('/never-open-this'); DROP TABLE symbols;")
        self.index();self.assertEqual(self.rows("SELECT count(*) FROM symbols WHERE kind='module'"),[(1,)])
        self.assertEqual(self.facts('read'),[])

    def test_incremental_delete_ignore_symlink_and_full_rebuild(self):
        path=self.put('schema.sql','CREATE TABLE t(id INTEGER); SELECT * FROM t;');self.index();self.assertTrue(self.architecture()['links'])
        path.write_text('SELECT * FROM t;');self.index();self.assertEqual(self.architecture()['links'],[])
        self.index(full=True);self.assertEqual(self.architecture()['links'],[])
        self.put('.cgignore','schema.sql\n');self.index();self.assertEqual(self.rows('SELECT count(*) FROM files'),[(0,)])
        (self.root/'.cgignore').unlink();path.unlink();path.symlink_to('/does/not/exist');self.index();self.assertEqual(self.rows('SELECT count(*) FROM files'),[(0,)])

    def test_source_changes_invalidate_resource_evidence(self):
        path=self.put('schema.sql','CREATE TABLE t(id INTEGER); SELECT * FROM t;');self.index()
        path.write_text('CREATE TABLE different(id INTEGER);')
        with self.assertRaisesRegex(ValueError,'changed'):self.architecture()

    def test_architecture_row_budget_and_truncation(self):
        self.put('query.sql','SELECT * FROM t;\n'*600);self.index();data=self.architecture(2)
        self.assertEqual(len(data['items']),2);self.assertEqual(data['total'],601);self.assertTrue(data['truncated'])
        # Count actual records delivered across the complete architecture query.
        delivered=[]
        class Cursor:
            def __init__(self,cursor):self.cursor=cursor
            def count(self,row):
                if row is not None and 'kind' in row.keys() and str(row['kind']).startswith('sql.'):delivered.append(row)
                return row
            def __iter__(self):return (self.count(row) for row in self.cursor)
            def fetchall(self):return [self.count(row) for row in self.cursor.fetchall()]
            def fetchone(self):return self.count(self.cursor.fetchone())
        class Connection:
            def __init__(self,db):self.db=db
            def execute(self,*args):return Cursor(self.db.execute(*args))
            def close(self):self.db.close()
        with ProjectData(cg,self.root) as project:
            project.db=Connection(project.db);self.assertTrue(project.architecture(2)['truncated'])
        self.assertEqual(len(delivered),2)

    def test_isolated_engine_copy_and_fingerprint_invalidation(self):
        engine=self.root.parent/'engine';shutil.copytree(REPO/'scripts',engine,ignore=shutil.ignore_patterns('__pycache__'))
        def load(name):
            spec=importlib.util.spec_from_file_location(name,engine/'cg.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
        a=load('sql_a');self.put('query.sql','SELECT * FROM t;')
        with contextlib.redirect_stdout(io.StringIO()):a.cmd_index(argparse.Namespace(root=str(self.root),full=False))
        p=engine/'language_sql.py';p.write_text(p.read_text()+"\nEXTENSIONS['.sqlprobe']='sql'\n");b=load('sql_b')
        self.assertNotIn('.sqlprobe',a.LANG_BY_EXT);self.assertIn('.sqlprobe',b.LANG_BY_EXT);self.assertNotIn('.sqlprobe',cg.LANG_BY_EXT)
        with ProjectData(b,self.root) as project:
            with self.assertRaisesRegex(ValueError,'Engine changed'):project.architecture()
        with contextlib.redirect_stdout(io.StringIO()):b.cmd_index(argparse.Namespace(root=str(self.root),full=False))
        with ProjectData(b,self.root) as project:self.assertEqual(project.architecture()['sql']['total'],2)

    def test_extract_failure_rolls_back_graph_and_fts(self):
        self.put('query.sql','CREATE TABLE original(id INTEGER);');self.index();before=self.rows('SELECT name FROM symbols ORDER BY id')
        self.put('query.sql','CREATE TABLE replacement(id INTEGER);')
        with patch.dict(cg.LANGUAGE_ADAPTERS['sql'],extract=lambda *a:(_ for _ in ()).throw(RuntimeError('synthetic SQL failure'))):
            with self.assertRaisesRegex(SystemExit,'synthetic'):self.index()
        self.assertEqual(before,self.rows('SELECT name FROM symbols ORDER BY id'))
        with contextlib.closing(sqlite3.connect(self.root/'.code-graph/graph.db',timeout=0)) as db:
            db.execute('BEGIN IMMEDIATE');self.assertEqual(db.execute("SELECT count(*) FROM search_fts WHERE search_fts MATCH 'replacement'").fetchone()[0],0);db.rollback()

    def test_search_spans_cli_mcp_readonly_and_architecture(self):
        self.put('schema.sql','CREATE TABLE unrelated(id INTEGER); CREATE VIEW matched AS\nSELECT id AS sqlneedle FROM source;')
        self.index();path=self.root/'.code-graph/graph.db';before=path.read_bytes()
        command=[sys.executable,'-I',str(REPO/'scripts/cg.py'),'--root',str(self.root)]
        request=dict(jsonrpc='2.0',id=1,method='tools/call',params=dict(name='cg_search',arguments=dict(query='sqlneedle',mode='lexical')))
        run=subprocess.run(command+['mcp'],input=json.dumps(request)+'\n',capture_output=True,text=True,timeout=20)
        result=json.loads(run.stdout)['result'];self.assertFalse(result['isError'])
        names=[x['symbol']['name'] for x in json.loads(result['content'][0]['text'])['items']];self.assertIn('matched',names);self.assertNotIn('unrelated',names)
        cli=subprocess.run(command+['architecture','--json'],capture_output=True,text=True,timeout=20)
        self.assertEqual(cli.returncode,0,cli.stderr);self.assertEqual(json.loads(cli.stdout)['sql'],self.architecture());self.assertEqual(path.read_bytes(),before)

    def test_native_sqlite_authorizer_confirms_cte_alias_and_resource_reads(self):
        setup='CREATE TABLE users(id INTEGER, name TEXT); CREATE TABLE other(id INTEGER);'
        cases=[('SELECT length(u.name) FROM users u JOIN other o ON u.id=o.id;',{'users','other'}),
               ('WITH users AS (SELECT id FROM other) SELECT * FROM users;',{'other'}),
               ('WITH users AS (SELECT 1) SELECT * FROM main.users;',{'users'})]
        for query,expected in cases:
            self.put('query.sql',query);self.index();self.assertEqual({x['name'] for x in self.facts('read')},expected)
            native=set()
            with contextlib.closing(sqlite3.connect(':memory:')) as db:
                db.executescript(setup)
                def authorize(action,name,column,schema,context):
                    if action==sqlite3.SQLITE_READ:native.add(name)
                    return sqlite3.SQLITE_OK
                db.set_authorizer(authorize);db.execute(query).fetchall()
            self.assertEqual(native,expected)

    def test_native_valid_forms_can_be_unsupported_by_bundled_grammar(self):
        source='CREATE TABLE [odd name](id); SELECT * FROM [odd name];'
        with contextlib.closing(sqlite3.connect(':memory:')) as db:db.executescript(source)
        self.put('schema.sql',source);self.index()
        self.assertFalse(self.facts('file')[0]['supported']);self.assertEqual(self.facts('read'),[])

    def test_native_sqlite_authorizer_confirms_dml_targets(self):
        cases=['INSERT INTO dest SELECT * FROM src;',
               'UPDATE dest SET id=1 WHERE id IN (SELECT id FROM src);',
               'DELETE FROM dest WHERE id IN (SELECT id FROM src);']
        for query in cases:
            self.put('query.sql',query);self.index();native=set()
            with contextlib.closing(sqlite3.connect(':memory:')) as db:
                db.executescript('CREATE TABLE dest(id INTEGER); CREATE TABLE src(id INTEGER); INSERT INTO src VALUES(1);')
                def authorize(action,name,column,schema,context):
                    if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):native.add(name)
                    return sqlite3.SQLITE_OK
                db.set_authorizer(authorize);db.execute(query).fetchall()
            self.assertEqual(native,{'dest'});self.assertEqual({x['name'] for x in self.facts('write')},native)

    def test_native_views_and_tables_match_direct_resource_evidence(self):
        source='CREATE TABLE users(id INTEGER); CREATE VIEW active AS SELECT * FROM users; SELECT * FROM active;'
        self.put('schema.sql',source);self.index();native=set()
        with contextlib.closing(sqlite3.connect(':memory:')) as db:
            db.executescript(source)
            self.assertEqual(set(db.execute("SELECT name,type FROM sqlite_master WHERE type IN ('table','view')")),{('users','table'),('active','view')})
            def authorize(action,name,column,schema,context):
                if action==sqlite3.SQLITE_READ:native.add(name)
                return sqlite3.SQLITE_OK
            db.set_authorizer(authorize);db.execute('SELECT * FROM active').fetchall()
        self.assertEqual({x['name'] for x in self.facts('read')},native)


if __name__=='__main__':unittest.main()
