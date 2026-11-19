"""Adapter boundaries preserve indexing, isolation and cache invalidation."""
import argparse
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_adapter_test', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)


class LanguageAdapters(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cg-adapters-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'project'
        self.root.mkdir()

    def run_index(self, engine):
        result = subprocess.run([sys.executable, '-I', str(engine), '--root', str(self.root), 'index'],
                                cwd=self.root, env=dict(os.environ, PYTHONPATH=str(self.root)),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def snapshot(self):
        with contextlib.closing(sqlite3.connect(self.root / '.code-graph/graph.db')) as db:
            meta = dict(db.execute('SELECT k,v FROM meta'))
            edges = db.execute('''SELECT f.path,a.name,b.name,e.conf FROM edges e
                JOIN symbols a ON a.id=e.caller_id JOIN symbols b ON b.id=e.callee_id
                JOIN files f ON f.id=a.file_id ORDER BY f.path,a.name,b.name,e.conf''').fetchall()
            return meta, edges

    def test_existing_extensions_keep_their_language_and_calls(self):
        sources = {
            'schema.sql': ('sql','CREATE TABLE users(id INTEGER); SELECT * FROM users;',None,None),
            'app.swift': ('swift','private func swift_leaf()->Int{return 1}\nfunc swift_entry()->Int{return swift_leaf()}\n',
                          'swift_entry','swift_leaf'),
            'App.java': ('java', 'class App {static int java_leaf(){return 1;} static int java_entry(){return java_leaf();}}',
                         'java_entry', 'java_leaf'),
            'app.dart': ('dart', 'int dart_leaf()=>1; int dart_entry()=>dart_leaf();',
                         'dart_entry', 'dart_leaf'),
            'app.go': ('go', 'package app\nfunc go_leaf() int { return 1 }; func go_entry() int { return go_leaf() }\n',
                       'go_entry', 'go_leaf'),
            'app.py': ('python', 'def python_leaf(): return 1\ndef python_entry(): return python_leaf()\n',
                       'python_entry', 'python_leaf'),
            'app.php': ('php', '<?php function php_leaf(){ return 1; } function php_entry(){ return php_leaf(); }',
                        'php_entry', 'php_leaf'),
        }
        for i, suffix in enumerate(('.js', '.mjs', '.cjs', '.jsx', '.ts', '.mts', '.tsx')):
            language = 'tsx' if suffix == '.tsx' else 'typescript' if suffix in ('.ts', '.mts') else 'javascript'
            sources['app' + suffix] = (language,
                f'function leaf_{i}(){{ return 1; }} function entry_{i}(){{ return leaf_{i}(); }}',
                f'entry_{i}', f'leaf_{i}')
        for name, (_, code, _, _) in sources.items():
            (self.root / name).write_text(code)
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(self.root), full=False))
        with contextlib.closing(sqlite3.connect(self.root / '.code-graph/graph.db')) as db:
            self.assertEqual(dict(db.execute('SELECT path,lang FROM files')),
                             {name: values[0] for name, values in sources.items()})
        _, edges = self.snapshot()
        self.assertEqual(set(edges), {(name, entry, leaf, 'high')
                                     for name, (_, _, entry, leaf) in sources.items() if entry is not None})

    def test_project_modules_cannot_replace_the_engine_adapters(self):
        marker = self.base / 'imported-project-code'
        trap = f'from pathlib import Path\nPath({str(marker)!r}).write_text("imported")\nraise RuntimeError("project adapter imported")\n'
        for name in ('language_adapters.py', 'language_python.py', 'language_php.py', 'language_javascript.py', 'language_go.py', 'language_dart.py', 'language_java.py', 'language_swift.py', 'language_sql.py'):
            (self.root / name).write_text(trap)
        (self.root / 'app.py').write_text('def leaf(): return 1\ndef entry(): return leaf()\n')
        self.run_index(REPO / 'scripts/cg.py')
        self.assertFalse(marker.exists())
        self.assertIn(('app.py', 'entry', 'leaf', 'high'), self.snapshot()[1])

    def test_adapter_change_invalidates_the_index_without_changing_edges(self):
        engine = self.base / 'engine'
        shutil.copytree(REPO / 'scripts', engine, ignore=shutil.ignore_patterns('__pycache__'))
        (self.root / 'app.py').write_text('def leaf(): return 1\ndef entry(): return leaf()\n')
        self.run_index(engine / 'cg.py')
        first, edges = self.snapshot()
        self.run_index(engine / 'cg.py')
        unchanged, _ = self.snapshot()
        self.assertEqual(first['content_revision'], unchanged['content_revision'])
        adapter = engine / 'language_python.py'
        adapter.write_text(adapter.read_text() + '\n# Adapter revision for cache invalidation.\n')
        self.run_index(engine / 'cg.py')
        updated, updated_edges = self.snapshot()
        self.assertNotEqual(first['content_revision'], updated['content_revision'])
        self.assertEqual(edges, updated_edges)


if __name__ == '__main__':
    unittest.main()
