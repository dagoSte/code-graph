"""Indexing work stays linear and never depends on the recursion limit: counted in nodes read, never in seconds."""
import argparse
import ast
from collections import Counter
import contextlib
import importlib.util
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_perf_index_test', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)


class LoaderInterpreterWork(unittest.TestCase):
    def test_nested_blocks_read_each_node_once(self):
        """Every block invalidates what it writes; nested blocks must not re-read their statements (depth x size)."""
        kinds = ['if flag:', 'for item in items:', 'while flag:', 'with open(item) as handle:', 'try:']
        lines = ['import importlib.util', 'def deep(flag, items):']
        for level in range(60):
            lines.append('    ' * (level + 1) + kinds[level % len(kinds)])
            lines.append('    ' * (level + 2) + f'value{level} = importlib.util.find_spec(item)')
        for level in reversed(range(60)):
            if kinds[level % len(kinds)] == 'try:':
                lines += ['    ' * (level + 1) + 'except ImportError:', '    ' * (level + 2) + 'pass']
        lines.append('deep(True, [1])')
        source = '\n'.join(lines) + '\n'
        nodes = sum(1 for _ in ast.walk(ast.parse(source)))
        reads = []
        original = ast.iter_child_nodes
        with mock.patch.object(ast, 'iter_child_nodes', lambda node: reads.append(node) or original(node)):
            cg.PythonFileLoads(source, 'deep.py', str(REPO)).calls()
        self.assertLess(len(reads), 2 * nodes)


class DeepNesting(unittest.TestCase):
    """An elif chain nests statements with no indentation; a long expression nests nodes. Neither may fail a file."""

    LOAD = ["spec = importlib.util.spec_from_file_location('plug', 'plug.py')",
            'mod = importlib.util.module_from_spec(spec)', 'spec.loader.exec_module(mod)', 'mod.run()']

    def edges(self, lines):
        with tempfile.TemporaryDirectory(prefix='cg-deep-') as root:
            Path(root, 'app.py').write_text('\n'.join(lines))
            Path(root, 'plug.py').write_text('def run():\n    return 1\n')
            errors = io.StringIO()
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                    cg.cmd_index(argparse.Namespace(root=root, full=True))
            except SystemExit as exit:
                self.fail(f'index failed: {exit.code}')
            con = sqlite3.connect(Path(root, '.code-graph', 'graph.db'))
            try:
                return Counter(con.execute("""SELECT sc.qname, c.line, st.qname, e.conf FROM edges e
                    JOIN calls c ON c.id=e.call_id JOIN symbols sc ON sc.id=e.caller_id
                    JOIN symbols st ON st.id=e.callee_id""").fetchall())
            finally:
                con.close()

    def test_long_elif_chains_and_expressions_index_like_the_base(self):
        for elifs in (1000, 3000):
            with self.subTest(elifs=elifs):
                lines = ['import importlib.util', '', 'def g():', '    return 1', '', 'def f(x):']
                lines += ['    ' + line for line in self.LOAD] + ['    return g()', '    if x == 0: g()']
                lines += [f'    elif x == {i}: g()' for i in range(1, elifs)]
                lines += ['', 'def h(a):', '    return g() + ' + ' + '.join(['a'] * 3000), '', 'k = ' + 'g(' * 150 + ')' * 150, '']
                expected = Counter({('f', 10, 'run', 'medium'): 1, ('h', elifs + 14, 'g', 'high'): 1,
                                    ('app.py', elifs + 16, 'g', 'high'): 150})
                expected.update(('f', line, 'g', 'high') for line in range(11, elifs + 12))
                self.assertEqual(self.edges(lines), expected)

    def test_live_elif_chains_read_the_loader_in_their_branch(self):
        """1.6.3 indexed these files; the branch loader is the only edge 1.6.4 adds. Loaders sit inside the step
        budget: 20000 steps for the module body, 5000 for a function, about 6 per elif here."""
        for elifs in (1000, 3000):
            for indent, owner, loader_at in (('', 'app.py', elifs - 10), ('    ', 'f', 500)):
                with self.subTest(elifs=elifs, owner=owner):
                    lines = ['import importlib.util', '', 'def g():', '    return 1', '', 'x = 1']
                    lines += ['def f(x):'] if owner == 'f' else []
                    expected = Counter()
                    for i in range(elifs):
                        if i == loader_at:
                            lines.append(indent + f'elif x == {i}:')
                            lines += [indent + '    ' + line for line in self.LOAD]
                            expected[(owner, len(lines), 'run', 'medium')] += 1
                        else:
                            lines.append(indent + ('if' if i == 0 else 'elif') + f' x == {i}: g()')
                            expected[(owner, len(lines), 'g', 'high')] += 1
                    lines += [indent + 'mod.run()', 'f(1)' if owner == 'f' else '']
                    if owner == 'f':
                        expected[('app.py', len(lines), 'f', 'high')] += 1
                    self.assertEqual(self.edges(lines), expected)


if __name__ == '__main__':
    unittest.main()
