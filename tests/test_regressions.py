"""Behavioral regressions from the installation councils; synthetic projects only."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ENGINE = Path(os.environ.get("CG_ENGINE", Path(__file__).resolve().parents[1] / "scripts/cg.py"))
spec = importlib.util.spec_from_file_location("cg", ENGINE)
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)


class Regressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cg-regression-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo"
        self.root.mkdir()

    def put(self, name, text):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def index(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(self.root), full=False))

    def db(self):
        db = cg.connect(str(self.root))
        self.addCleanup(db.close)
        return db

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)

    def test_builtin_selftest(self):
        self.put("tree_sitter_language_pack.py", "from pathlib import Path\nPath('unexpected-import').write_text('synthetic marker')\n")
        env = dict(os.environ, PYTHONPATH=str(self.root))
        launcher = ENGINE.parent / "cg"
        command = [str(launcher)] if launcher.exists() else [sys.executable, "-I", str(ENGINE)]
        r = subprocess.run(command + ["selftest"], capture_output=True, text=True, cwd=self.root, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("TRUST", r.stdout)
        self.assertFalse((self.root / "unexpected-import").exists())

    def test_git_paths_preserve_unicode_quotes_and_newlines(self):
        self.git("init", "-q")
        names = ["caffè.py", 'a"b.py', "line\nbreak.py"]
        for n in names:
            self.put(n, "def present(): return 1\n")
        self.git("add", ".")
        self.assertEqual(set(cg.list_files(str(self.root))), set(names))
        self.assertEqual(cg.word_counts(str(self.root))["present"], 3)

    def test_empty_git_listing_does_not_scan_ignored_files(self):
        self.git("init", "-q")
        self.put(".gitignore", "*.py\n")
        self.put("ignored.py", "def hidden(): return 1\n")
        self.assertEqual(cg.list_files(str(self.root)), [])
        self.assertEqual(cg.word_counts(str(self.root)).get("hidden", 0), 0)

    def test_output_directory_symlink_is_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        dbpath = outside / "graph.db"
        with sqlite3.connect(dbpath) as db:
            db.executescript("CREATE TABLE meta(k TEXT,v TEXT); INSERT INTO meta VALUES('version','old'); CREATE TABLE sentinel(x);")
        before = dbpath.read_bytes()
        (self.root / ".code-graph").symlink_to(outside, target_is_directory=True)
        with self.assertRaises((SystemExit, OSError, ValueError)):
            self.index()
        self.assertEqual(dbpath.read_bytes(), before)
        self.assertFalse((outside / ".gitignore").exists())

    def test_database_symlink_is_rejected(self):
        state = self.root / ".code-graph"
        state.mkdir()
        target = self.base / "external.sqlite"
        with sqlite3.connect(target) as db:
            db.execute("CREATE TABLE sentinel(x)")
        before = target.read_bytes()
        (state / "graph.db").symlink_to(target)
        with self.assertRaises((SystemExit, OSError, ValueError)):
            self.index()
        self.assertEqual(target.read_bytes(), before)

    def test_ignore_symlink_is_rejected(self):
        state = self.root / ".code-graph"
        state.mkdir()
        target = self.base / "external-ignore"
        (state / ".gitignore").symlink_to(target)
        with self.assertRaises((SystemExit, OSError, ValueError)):
            self.index()
        self.assertFalse(target.exists())

    def test_source_symlink_and_external_context_are_not_read(self):
        private = self.base / "secret.py"
        private.write_text("def private_marker(): return 'PRIVATE'\n")
        (self.root / "visible.py").symlink_to(private)
        self.assertEqual(cg.list_files(str(self.root)), [])
        self.assertEqual(cg.read_lines(str(self.root), "visible.py", 1, 2), "")
        self.assertEqual(cg.read_lines(str(self.root), "../secret.py", 1, 2), "")
        self.assertEqual(cg.word_counts(str(self.root)).get("private_marker", 0), 0)

    def test_cpp_style_method_selector(self):
        self.put("a.py", "class C:\n    def m(self): return 1\n")
        self.index()
        db = self.db()
        self.assertEqual(cg.lookup(db, "C::m"), cg.lookup(db, "C.m"))
        self.assertEqual(cg.lookup(db, "a.py:C::m"), cg.lookup(db, "C.m"))

    def test_context_truncates_body_visibly_within_budget(self):
        self.put("a.py", "def large():\n" + "    x = 'payload'\n" * 200)
        self.index()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cg.cmd_context(argparse.Namespace(root=str(self.root), symbol="large", budget=250, min_conf="medium"))
        out = buf.getvalue()
        self.assertIn("def large():", out)
        self.assertIn("partial", out)
        self.assertLessEqual(len(out.split("[~")[0].rstrip("\n")), 1000)

    def test_same_mtime_content_change_is_detected(self):
        p = self.put("a.py", "def before(): return 1\n")
        self.index()
        stamp = p.stat().st_mtime_ns
        p.write_text("def after_(): return 1\n")
        os.utime(p, ns=(stamp, stamp))
        self.index()
        db = self.db()
        self.assertTrue(cg.lookup(db, "after_"))
        self.assertFalse(cg.lookup(db, "before"))

    def test_file_becoming_minified_drops_old_symbols(self):
        p = self.put("a.js", "function oldSymbol(){ return 1; }\n")
        self.index()
        p.write_text("var s='" + "x" * 21000 + "';")
        self.index()
        self.assertFalse(cg.lookup(self.db(), "oldSymbol"))

    def test_parser_failure_is_not_reported_as_success(self):
        p = self.put("a.py", "def before(): return 1\n")
        self.index()
        dbpath = self.root / ".code-graph/graph.db"
        before = hashlib.sha256(dbpath.read_bytes()).hexdigest()
        p.write_text("def after_(): return 2\n")
        with patch.object(cg, "get_parser_for", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(SystemExit):
                self.index()
        self.assertEqual(hashlib.sha256(dbpath.read_bytes()).hexdigest(), before)

    def test_php_absolute_class_keeps_global_namespace(self):
        self.put("global.php", "<?php class Target { static function hit() {} }\n")
        self.put("app.php", "<?php namespace App; class Target { static function hit($x) {} } class UseIt { function go(){ \\Target::hit(); } }\n")
        self.index()
        db = self.db()
        sid = cg.lookup(db, "UseIt.go")[0]
        targets = list(db.execute("SELECT f.path,e.conf FROM edges e JOIN symbols s ON s.id=e.callee_id JOIN files f ON f.id=s.file_id WHERE caller_id=?", (sid,)))
        self.assertEqual(targets, [("global.php", "high")])

    def test_python_nested_homonyms_respect_lexical_scope(self):
        self.put("a.py", "def one():\n    def helper(): return 1\n    return helper()\ndef two():\n    def helper(): return 2\n    return helper()\n")
        self.index()
        db = self.db()
        for name in ("one", "two"):
            sid = cg.lookup(db, name)[0]
            self.assertEqual([r[0] for r in db.execute("SELECT s.qname FROM edges e JOIN symbols s ON s.id=e.callee_id WHERE caller_id=?", (sid,))], [name + ".helper"])

    def test_union_receiver_does_not_claim_one_certain_target(self):
        self.put("a.py", "class A:\n    def ping(self): return 1\nclass B:\n    def ping(self): return 2\ndef use(x: A | B):\n    return x.ping()\n")
        self.index()
        db = self.db()
        sid = cg.lookup(db, "use")[0]
        self.assertFalse(db.execute("SELECT 1 FROM edges WHERE caller_id=? AND conf='high'", (sid,)).fetchone())

    def test_unknown_reassignment_clears_inferred_receiver(self):
        self.put("a.py", "class A:\n    def ping(self): return 1\ndef use():\n    x = A()\n    x = unknown()\n    return x.ping()\n")
        self.index()
        db = self.db()
        self.assertFalse(db.execute("SELECT 1 FROM edges e JOIN calls c ON c.id=e.call_id WHERE c.name='ping' AND e.conf='high'").fetchone())

    def test_named_import_aliases_python_and_javascript(self):
        self.put("target.py", "def work(): return 1\n")
        self.put("use.py", "from target import work as alias\ndef work(required): return 2\ndef entry_py(): return alias()\n")
        self.put("target.js", "export function work(){ return 1; }\n")
        self.put("use.js", "import {work as alias} from './target.js';\nfunction work(required){ return 2; }\nfunction entry_js(){ return alias(); }\n")
        self.index()
        db = self.db()
        for name, target in (("entry_py", "target.py"), ("entry_js", "target.js")):
            sid = cg.lookup(db, name)[0]
            self.assertEqual([r[0] for r in db.execute("SELECT f.path FROM edges e JOIN symbols s ON s.id=e.callee_id JOIN files f ON f.id=s.file_id WHERE caller_id=?", (sid,))], [target])

    def test_class_import_alias_takes_precedence_over_original_local_name(self):
        self.put("target.py", "class Real:\n    def ping(self): return 1\n")
        self.put("use.py", "from target import Real as Alias\nclass Real:\n    def ping(self, required): return 2\ndef entry(x: Alias):\n    return x.ping()\ndef construct():\n    return Alias()\n")
        self.index()
        db = self.db()
        for name in ("entry", "construct"):
            sid = cg.lookup(db, name)[0]
            self.assertEqual([r[0] for r in db.execute("SELECT f.path FROM edges e JOIN symbols s ON s.id=e.callee_id JOIN files f ON f.id=s.file_id WHERE caller_id=?", (sid,))], ["target.py"])


if __name__ == "__main__":
    unittest.main()
