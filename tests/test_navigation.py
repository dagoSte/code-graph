"""Regression cases observed while navigating a real Python project."""
import argparse
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

ENGINE = Path(__file__).resolve().parents[1] / "scripts/cg.py"
spec = importlib.util.spec_from_file_location("cg_navigation_test", ENGINE)
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)


class NavigationRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cg-navigation-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def index(self, files):
        for name, source in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(self.root), full=False))
        db = cg.connect(str(self.root))
        self.addCleanup(db.close)
        return db

    def targets(self, db, caller):
        return list(db.execute("SELECT b.qname,f.path,e.conf FROM edges e JOIN symbols a ON a.id=e.caller_id JOIN symbols b ON b.id=e.callee_id JOIN files f ON f.id=b.file_id WHERE a.qname=?", (caller,)))

    def test_dictionary_get_is_not_client_get(self):
        db = self.index({"client.py": "class Client:\n    def get(self, url): return url\n",
                         "app.py": "from client import Client\ndef read_payload(payload: dict): return payload.get('value')\ndef read_client(client: Client): return client.get('url')\n"})
        self.assertEqual(self.targets(db, "read_payload"), [])
        self.assertEqual(self.targets(db, "read_client"), [("Client.get", "client.py", "high")])

    def test_module_qualified_function_call_is_resolved(self):
        db = self.index({"server.py": "def serve_export(): return 1\n",
                         "other/server.py": "def serve_export(): return 2\n",
                         "app.py": "import server as exports\ndef start(): return exports.serve_export()\n"})
        self.assertEqual(self.targets(db, "start"), [("serve_export", "server.py", "high")])

    def test_atexit_registered_function_is_not_reported_dead(self):
        db = self.index({"app.py": "import atexit\n@atexit.register\ndef cleanup(): pass\n"})
        results = {db.execute("SELECT name FROM symbols WHERE id=?", (sid,)).fetchone()[0]: verdict
                   for verdict, sid, _, _ in cg.dead_candidates(db, str(self.root))}
        self.assertNotIn(results.get("cleanup"), ("VERIFIED_DEAD", "LIKELY_DEAD"))

    def test_known_dictionary_forms_do_not_link_client(self):
        db = self.index({'app.py': "from typing import Mapping\nclass Client:\n    def get(self, url): return url\ndef one():\n    value = {}\n    return value.get('x')\ndef two(value: Mapping[str, int]): return value.get('x')\ndef three():\n    value = dict()\n    return value.get('x')\ndef unknown(value): return value.get('x')\n"})
        for name in ('one', 'two', 'three'):
            self.assertEqual(self.targets(db, name), [])
        self.assertEqual(self.targets(db, 'unknown'), [('Client.get', 'app.py', 'low')])

    def test_callback_aliases_and_bound_methods(self):
        db = self.index({'app.py': "import atexit as lifecycle\nfrom atexit import register as on_exit\n@on_exit\ndef cleanup_alias(): pass\ndef cleanup_explicit(): pass\nlifecycle.register(cleanup_explicit)\nclass Owner:\n    def __init__(self): lifecycle.register(self.cleanup)\n    def cleanup(self): pass\n"})
        found = {r[0] for r in db.execute('SELECT s.qname FROM symbol_refs r JOIN symbols s ON s.id=r.target_id')}
        self.assertEqual(found, {'cleanup_alias', 'cleanup_explicit', 'Owner.cleanup'})
        self.assertEqual(db.execute("SELECT COUNT(*) FROM edges e JOIN symbols s ON s.id=e.callee_id WHERE s.name LIKE 'cleanup%'").fetchone()[0], 0)

    def test_qualified_module_variants(self):
        for statement, call in [('import package.server', 'package.server.serve_export()'), ('from package import server as exports', 'exports.serve_export()'), ('import package.server as Exports', 'Exports.serve_export()')]:
            with self.subTest(statement=statement):
                db = self.index({'package/server.py': 'def serve_export(): return 1\n', 'app.py': statement + '\ndef start(): return ' + call + '\n'})
                self.assertEqual(self.targets(db, 'start'), [('serve_export', 'package/server.py', 'high')])

    def test_inventory_does_not_run_git_fsmonitor(self):
        import shlex
        import subprocess
        subprocess.run(['git', '-C', str(self.root), 'init', '-q'], check=True, capture_output=True)
        (self.root / 'app.py').write_text('def example(): return 1\n')
        subprocess.run(['git', '-C', str(self.root), 'add', 'app.py'], check=True, capture_output=True)
        marker = self.root / 'hook-executed'
        hook = self.root / 'watch.sh'
        hook.write_text('#!/bin/sh\nprintf synthetic > ' + shlex.quote(str(marker)) + '\nprintf "fixture-token\\0"\n')
        hook.chmod(0o755)
        subprocess.run(['git', '-C', str(self.root), 'config', 'core.fsmonitor', str(hook)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'config', 'core.fsmonitorHookVersion', '2'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.root), 'ls-files', '-co', '--exclude-standard'], check=True, capture_output=True)
        self.assertTrue(marker.exists(), 'the control must demonstrate the hook can run')
        marker.unlink()
        self.assertEqual(cg.list_files(str(self.root)), ['app.py'])
        self.assertFalse(marker.exists())


    def test_module_alias_shadowed_by_a_parameter_is_not_resolved_as_module(self):
        db = self.index({'server.py': 'def serve_export(): return 1\n', 'app.py': 'import server as exports\ndef start(exports): return exports.serve_export()\n'})
        self.assertEqual(self.targets(db, 'start'), [])


if __name__ == "__main__":
    unittest.main()
