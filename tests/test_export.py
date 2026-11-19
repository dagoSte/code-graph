"""HTML export contracts: bounds, callsites, local paths and inert source text."""
import argparse
import contextlib
import hashlib
from html.parser import HTMLParser
import importlib.util
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cg_export_test", REPO / "scripts/cg.py")
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.scripts = []
        self.payload = ""
        self.in_data = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attributes = dict(attrs)
            self.scripts.append(attributes)
            self.in_data = attributes.get("id") == "graph-data"

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_data = False

    def handle_data(self, text):
        if self.in_data:
            self.payload += text


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cg-html-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "progetto con spazi"
        self.root.mkdir()
        (self.root / "main.py").write_text(
            "def leaf(): return 1\n"
            "def caller():\n    leaf()\n    return leaf()\n"
            "def parent(): return caller()\n"
            "def entry(): return parent()\n"
            "def isolated(): return 0\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(self.root), full=False))
        self.dbpath = self.root / ".code-graph/graph.db"

    def args(self, **changes):
        values = dict(root=str(self.root), output=".code-graph/explore.html", symbol=None, depth=2,
                      min_conf="low", max_nodes=5000, max_edges=50000, force=False)
        values.update(changes)
        return argparse.Namespace(**values)

    def export(self, **changes):
        args = self.args(**changes)
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_export(args)
        output = Path(args.output)
        if not output.is_absolute():
            output = self.root / output
        html = output.read_text(encoding="utf-8")
        return html, json.loads(Page(html).payload)

    def test_complete_export_keeps_isolated_nodes_and_all_callsites(self):
        html, data = self.export()
        labels = {node["id"]: node["label"] for node in data["nodes"]}
        self.assertIn("isolated", labels.values())
        edge = next(edge for edge in data["edges"] if labels[edge["source"]] == "caller" and labels[edge["target"]] == "leaf")
        self.assertEqual([call["line"] for call in edge["calls"]], [3, 4])
        self.assertEqual(data["total_nodes"], len(data["nodes"]))
        self.assertNotIn(str(self.root), html)
        self.assertEqual(len(Page(html).scripts), 2)
        self.assertTrue(all("src" not in attrs for attrs in Page(html).scripts))

    def test_focused_export_traverses_both_directions_with_depth(self):
        _, data = self.export(symbol="caller", depth=1)
        self.assertEqual({node["label"] for node in data["nodes"]}, {"caller", "leaf", "parent"})
        self.assertIsNotNone(data["focus"])
        _, data = self.export(symbol="caller", depth=2, force=True)
        self.assertEqual({node["label"] for node in data["nodes"]}, {"caller", "leaf", "parent", "entry"})

    def test_confidence_filters_data_not_only_display(self):
        with sqlite3.connect(self.dbpath) as db:
            leaf = db.execute("SELECT id FROM symbols WHERE name='leaf'").fetchone()[0]
            db.execute("UPDATE edges SET conf='low' WHERE callee_id=?", (leaf,))
        _, data = self.export(symbol="caller", depth=1, min_conf="high")
        self.assertEqual({node["label"] for node in data["nodes"]}, {"caller", "parent"})
        self.assertTrue(all(edge["confidence"] == "high" for edge in data["edges"]))

    def test_existing_index_is_read_only_and_schema_remains_compatible(self):
        before = hashlib.sha256(self.dbpath.read_bytes()).hexdigest()
        self.export()
        self.assertEqual(hashlib.sha256(self.dbpath.read_bytes()).hexdigest(), before)
        with sqlite3.connect(self.dbpath) as db:
            self.assertEqual(db.execute("SELECT v FROM meta WHERE k='version'").fetchone()[0], cg.INDEX_VERSION)

    def test_export_refuses_clobber_without_force(self):
        self.export()
        output = self.root / ".code-graph/explore.html"
        output.write_text("keep me")
        with self.assertRaises(SystemExit):
            self.export()
        self.assertEqual(output.read_text(), "keep me")
        html, _ = self.export(force=True)
        self.assertIn("<!doctype html>", html)

    def test_paths_outside_root_and_symlinks_are_rejected(self):
        outside = Path(self.tmp.name) / "outside.html"
        outside.write_text("private sentinel")
        (self.root / "link.html").symlink_to(outside)
        (self.root / "parent-link").symlink_to(outside.parent, target_is_directory=True)
        for name in ("../outside.html", str(outside), "link.html", "parent-link/outside.html", "bad.py"):
            with self.subTest(path=name), self.assertRaises(SystemExit):
                self.export(output=name, force=True)
        self.assertEqual(outside.read_text(), "private sentinel")

    def test_symlink_database_sidecar_is_rejected(self):
        target = Path(self.tmp.name) / "external"
        target.write_text("sentinel")
        (self.root / ".code-graph/graph.db-wal").symlink_to(target)
        with self.assertRaises(SystemExit):
            self.export()
        self.assertEqual(target.read_text(), "sentinel")

    def test_bounds_fail_explicitly_without_partial_output(self):
        for options in (dict(max_nodes=2), dict(max_edges=1), dict(symbol="caller", max_nodes=2)):
            with self.subTest(options=options), self.assertRaises(SystemExit):
                self.export(**options)
            self.assertFalse((self.root / ".code-graph/explore.html").exists())

    def test_html_source_values_are_inert_and_round_trip_exactly(self):
        hostile = '</script><script>window.PWNED=1</script><img src="https://invalid.example/x" onerror="alert(1)">\u2028&'
        with sqlite3.connect(self.dbpath) as db:
            db.execute("UPDATE symbols SET name=?,qname=?,sig=? WHERE name='isolated'", (hostile, hostile, hostile))
        html, data = self.export()
        page = Page(html)
        self.assertEqual(len(page.scripts), 2)
        self.assertTrue(any(node["label"] == hostile and node["signature"] == hostile for node in data["nodes"]))
        self.assertNotIn(hostile, html)
        self.assertIn('Content-Security-Policy', html)
        self.assertNotIn("'unsafe-inline'", html)
        for tag in ("script", "style"):
            code = re.search(rf"<{tag} data-cg-inline>(.*?)</{tag}>", html, re.S).group(1)
            import base64
            digest = base64.b64encode(hashlib.sha256(code.encode()).digest()).decode()
            self.assertIn("sha256-" + digest, html)

    def test_failed_atomic_replace_preserves_previous_export(self):
        self.export()
        before = (self.root / ".code-graph/explore.html").read_bytes()
        with patch.object(cg.os, "replace", side_effect=OSError("synthetic write failure")):
            with self.assertRaises(SystemExit):
                self.export(force=True)
        self.assertEqual((self.root / ".code-graph/explore.html").read_bytes(), before)
        self.assertEqual({p.name for p in (self.root / ".code-graph").iterdir()}, {"graph.db", ".gitignore", "explore.html"})

    def test_empty_index_and_incompatible_index(self):
        with sqlite3.connect(self.dbpath) as db:
            db.execute("DELETE FROM symbols")
            db.execute("DELETE FROM edges")
        _, data = self.export()
        self.assertEqual(data["nodes"], [])
        with sqlite3.connect(self.dbpath) as db:
            db.execute("UPDATE meta SET v='1.1.0' WHERE k='version'")
        with self.assertRaises(SystemExit):
            self.export(force=True)


if __name__ == "__main__":
    unittest.main()
