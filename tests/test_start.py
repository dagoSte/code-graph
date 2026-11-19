"""`cg start`: one command that indexes a project and shows what to ask it, with commands that run."""
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CG = REPO / 'scripts/cg'


def cg(root, *arguments, env=None):
    run = subprocess.run([str(CG), '--root', str(root), *arguments], capture_output=True, text=True, timeout=120,
                         env=dict(os.environ, **(env or {})))
    if run.returncode:
        raise AssertionError(run.stderr)
    return run.stdout


class Start(unittest.TestCase):
    def project(self, folder, helpers=0, git=True):
        root = Path(folder) / 'shop'
        (root / 'tests').mkdir(parents=True)
        (root / 'pricing.py').write_text('def tax(amount):\n    return amount * 0.2\n')
        callers = ''.join(f'def total_{i}(amount):\n    return amount + tax(amount)\n' for i in range(4))
        (root / 'checkout.py').write_text('from pricing import tax\n' + callers)
        (root / 'tests/test_checkout.py').write_text(
            'from checkout import total_0\ndef test_total():\n    assert total_0(10) == 12\n')
        for i in range(helpers):
            (root / f'helper_{i}.py').write_text(f'def helper_{i}(value):\n    return value\n')
        if git:
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
        return root

    def test_an_ambiguous_example_name_is_skipped_not_fatal(self):
        # Two nested functions share one qualified name: verify exits on the ambiguity, start must not.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'twins'
            root.mkdir()
            (root / 'twins.py').write_text('def outer(a):\n    if a:\n        def wrap():\n            return 1\n'
                                           '    else:\n        def wrap():\n            return 2\n    return wrap()\n')
            info = json.loads(cg(root, 'start', '--json'))
            self.assertEqual(info['format'], 'codegraph-start/1')
            self.assertIn('Small project', cg(root, 'start', '--lang', 'en'))

    def test_json_names_a_real_example_and_runnable_commands(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder)
            info = json.loads(cg(root, 'start', '--json'))
            self.assertEqual(info['format'], 'codegraph-start/1')
            self.assertTrue(info['small_project'])
            self.assertEqual(info['example']['symbol'], 'pricing.py:tax')
            self.assertEqual(info['example']['callers'], 4)
            self.assertGreaterEqual(info['example']['tests'], 1)
            impact = shlex.split(info['commands']['impact'])
            self.assertEqual(Path(impact[0]), CG)
            self.assertIn('tax', subprocess.run(impact, capture_output=True, text=True, timeout=60).stdout)
            self.assertIn('review', info['commands'])
            self.assertIn('tax', subprocess.run(shlex.split(info['commands']['search']), capture_output=True,
                                                text=True, timeout=60).stdout)

    def test_text_says_when_it_pays_off_in_both_languages(self):
        with tempfile.TemporaryDirectory() as folder:
            small = cg(self.project(folder), 'start', '--lang', 'en')
            self.assertIn('Small project', small)
            self.assertIn('called from 4 places', small)
            self.assertIn('1 test reach', small)
        with tempfile.TemporaryDirectory() as folder:
            large = cg(self.project(folder, helpers=30, git=False), 'start', env=dict(LANG='it_IT.UTF-8', LC_ALL=''))
            self.assertIn('troppi da leggere', large)
            self.assertIn('non è un checkout Git', large)

    def test_project_without_tested_callees_still_gets_guidance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'tiny'
            root.mkdir()
            (root / 'a.py').write_text('def alone():\n    return 1\n')
            info = json.loads(cg(root, 'start', '--json'))
            self.assertIsNone(info['example'])
            self.assertNotIn('impact', info['commands'])
            self.assertIn('search', info['commands'])
            self.assertIn('cg map', cg(root, 'start', '--lang', 'en'))


if __name__ == '__main__':
    unittest.main()


class Help(unittest.TestCase):
    def test_help_groups_every_command_once_and_leads_with_start_here(self):
        run = subprocess.run([str(CG), '--help'], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        text = run.stdout
        epilog = text[text.index('start here:'):]
        self.assertLess(epilog.index('start here:'), epilog.index('explore:'))
        self.assertLess(epilog.index('explore:'), epilog.index('advanced:'))
        import re
        error = subprocess.run([str(CG), '--root', '.', 'nope'], capture_output=True, text=True, timeout=60).stderr
        commands = set(re.findall(r"'([a-z]+)'", error[error.index('choose from'):]))
        self.assertGreater(len(commands), 20)
        named = re.findall(r'\b([a-z]+)\b', epilog)
        for command in sorted(commands):
            with self.subTest(command=command):
                self.assertIn(command, named)
