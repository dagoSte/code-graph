"""PHP function imports must preserve the namespace and the local alias."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_gaps_php', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)


class FunctionImports(unittest.TestCase):
    def project(self, changes=None):
        temp = tempfile.TemporaryDirectory(prefix='cg-php-imports-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'project'
        shutil.copytree(REPO / 'tests/fixtures/gaps_php/H20b', root)
        for path, text in (changes or {}).items():
            (root / path).write_text(text)
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=False))
        return root

    def impact(self, root, target):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cg.cmd_impact(argparse.Namespace(root=str(root), target=target, min_conf='low',
                                            depth=1, file=False, json=True, limit=100))
        return {item['symbol']['name'] for item in json.loads(output.getvalue())['items']}

    def assert_only_n4(self, root):
        self.assertEqual(self.impact(root, 'src/N4/tools.php:polish'), {'drive'})
        for i in range(4):
            self.assertEqual(self.impact(root, f'src/N{i}/tools.php:polish'), set())

    def test_import_picks_namespace_beyond_first_three_homonyms(self):
        self.assert_only_n4(self.project())

    def test_function_alias_picks_exact_namespace(self):
        root = self.project({'src/app.php': r'''<?php
namespace Fx\App;
use function Fx\N4\polish as p;
function drive() { return p(); }
'''})
        self.assert_only_n4(root)

    def test_function_import_is_case_insensitive(self):
        root = self.project({'src/app.php': r'''<?php
namespace Fx\App;
use function FX\n4\POLISH as P;
function drive() { return p(); }
'''})
        self.assert_only_n4(root)

    def test_external_import_does_not_call_project_homonyms(self):
        root = self.project({'src/app.php': r'''<?php
namespace Fx\App;
use function External\Missing\polish;
function drive() { return polish(); }
'''})
        for i in range(5):
            self.assertEqual(self.impact(root, f'src/N{i}/tools.php:polish'), set())

    def test_single_namespace_positive_control(self):
        root = self.project({'src/app.php': r'''<?php
use function Fx\N4\polish;
function drive() { return polish(); }
'''})
        for i in range(4):
            shutil.rmtree(root / f'src/N{i}')
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=False))
        self.assertEqual(self.impact(root, 'src/N4/tools.php:polish'), {'drive'})

    def test_class_and_function_imports_have_separate_aliases(self):
        root = self.project({'src/app.php': r'''<?php
namespace Fx\App;
use function Fx\N4\polish as p;
use Fx\N0\Box as p;
function drive() { return p(); }
'''})
        self.assert_only_n4(root)

    def test_imports_do_not_leak_across_namespace_blocks(self):
        root = self.project({'src/app.php': r'''<?php
namespace One { use function Fx\N0\polish as p; function first() { p(); } }
namespace Two { use function Fx\N4\polish as p; function drive() { p(); } }
'''})
        self.assertEqual(self.impact(root, 'src/N0/tools.php:polish'), {'first'})
        self.assertEqual(self.impact(root, 'src/N4/tools.php:polish'), {'drive'})

    def test_definitions_in_multiple_namespaces_keep_their_namespace(self):
        root = self.project({'src/N4/tools.php': r'''<?php
namespace Fx\N4 { function polish() { return 4; } }
namespace Other { function unrelated() {} }
'''})
        self.assert_only_n4(root)

    def test_same_line_namespace_blocks_do_not_invent_exact_edges(self):
        root = self.project({'src/app.php': r'''<?php
namespace One { use function Fx\N0\polish; function first() { polish(); } } namespace Two { use function Fx\N4\polish; function drive() { polish(); } }
'''})
        for i in range(5):
            self.assertEqual(self.impact(root, f'src/N{i}/tools.php:polish'), set())
