"""CLI entry points added in 1.6: `change` forwards every argument, other commands stay strict."""
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
CG = REPO / 'scripts' / 'cg'


def run(*args, cwd):
    return subprocess.run([str(CG), '--root', str(cwd), *args], capture_output=True, text=True, timeout=60)


class ChangeEntryPointTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='cg-cli-')
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def test_change_help_reaches_the_change_parser(self):
        result = run('change', '--help', cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('cg change', result.stdout)
        for action in ('new', 'plan', 'approve', 'integrate'):
            self.assertIn(action, result.stdout)

    def test_change_action_help_is_forwarded(self):
        result = run('change', 'approve', '--help', cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--test-argv', result.stdout)

    def test_other_commands_still_reject_unknown_options(self):
        result = run('stats', '--no-such-option', cwd=self.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn('unrecognized arguments', result.stderr)


if __name__ == '__main__':
    unittest.main()
