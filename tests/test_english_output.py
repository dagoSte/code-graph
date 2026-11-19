"""Exercise public CLI text and descriptive JSON on an English-only project."""
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
ITALIAN = re.compile(r'\b(?:indice|chiamanti|chiamati|simboli|righe|grafo|progetto|'
                     r'nessun[oa]?|esegui|omissioni|revisione|sorgente|arità|'
                     r'argomenti|metodo-mancante|duplicato|caratteri|intendevi|'
                     r'risolte|dichiarat[oi]|funzionalità|aggiorna|maiuscole)\b', re.I)


class EnglishOutput(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cg-english-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'tests').mkdir()
        (self.root / 'service.py').write_text(
            'def save(value):\n    return value\n'
            'def publish(value):\n    return save(value)\n'
            'def run():\n    return publish()\n')
        (self.root / 'tests/test_service.py').write_text(
            'from service import publish\ndef test_publish():\n    assert publish(1) == 1\n')
        (self.root / '.gitignore').write_text('.code-graph/\n')
        (self.root / '.code-graph-manifest.json').write_text(json.dumps({
            'format': 'codegraph-manifest/1',
            'features': [{'id': 'publish', 'title': 'Publishing',
                          'entrypoints': [{'file': 'service.py', 'symbol': 'publish'}]}]}))
        for args in [('init', '-q'), ('add', '.'),
                     ('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                      'commit', '-qm', 'English fixture')]:
            subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)
        self.cli('index')

    def cli(self, *args, error=False):
        result = subprocess.run([sys.executable, '-I', str(REPO / 'scripts/cg.py'),
                                 '--root', str(self.root), *args],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode == 0, not error, result.stdout + result.stderr)
        text = result.stdout + result.stderr
        self.assertIsNone(ITALIAN.search(text), text)
        self.assertNotIn('Traceback', text)
        return result.stderr if error else result.stdout

    def test_text_commands_and_findings_are_english(self):
        for command in [('index',), ('stats',), ('find', 'publish'), ('show', 'publish'),
                        ('callers', 'publish'), ('callees', 'publish'), ('impact', 'publish'),
                        ('path', 'run', 'save'), ('context', 'publish'), ('map',),
                        ('dead',), ('catalog', 'check'), ('catalog', 'validate'), ('change', 'list'),
                        ('export', '--output', 'graph.html')]:
            with self.subTest(command=command):
                self.assertTrue(self.cli(*command).strip())
        text = self.cli('check')
        self.assertIn('arity: passes 0 arguments to publish', text)
        self.assertIn('revision ', self.cli('context', 'publish'))

    def test_descriptive_json_keeps_public_formats(self):
        for command, expected_format in [
            (('context', 'publish', '--json'), 'codegraph-context/1'),
            (('impact', 'publish', '--json'), 'codegraph-impact/1'),
            (('architecture', '--json'), 'codegraph-architecture/1'),
            (('features', '--json'), 'codegraph-features/1'),
            (('prepare', 'declared:publish', '--json'), 'codegraph-feature-context/1'),
            (('review', '--base', 'HEAD', '--json'), 'codegraph-review/1'),
            (('search', 'publish', '--json'), 'codegraph-search/2'),
            (('locate', 'publish event', '--json'), 'codegraph-locate/1')]:
            with self.subTest(command=command):
                value = json.loads(self.cli(*command))
                self.assertEqual(value['format'], expected_format)
        plan = json.loads(self.cli('verify', 'publish', '--json'))
        self.assertTrue(plan['tests'])
        self.assertFalse(plan['broader_verification_required'])
        findings = json.loads(self.cli('check', '--json'))
        self.assertTrue(any(item['rule_id'] == 'arity' for item in findings['findings']))

    def test_user_errors_are_english_without_tracebacks(self):
        for command, message in [
            (('show', 'missing_symbol'), "No symbol 'missing_symbol'"),
            (('prepare', 'publish'), 'did you mean declared:publish?'),
            (('source', 'service.py', '--line', '900'), 'beyond the end'),
            (('search', 'publish', '--limit', '0'), 'positive'),
            (('locate', 'publish', '--max-chars', '100'), 'Invalid locate limits'),
            (('prepare', 'declared:publish', '--max-chars', '100'), 'Invalid intent or budget')]:
            with self.subTest(command=command):
                self.assertIn(message, self.cli(*command, error=True))

    def test_baseline_saved_before_translation_keeps_the_same_finding(self):
        # This identity was recorded by the pre-translation engine on this fixture.
        old_fingerprint = '42f18e995725d4e22625b164b51a5f409e9665974ce34049791c2fe8b2ef7bb1'
        self.cli('review', '--save-baseline', '.code-graph/baseline.json')
        path = self.root / '.code-graph/baseline.json'
        baseline = json.loads(path.read_text())
        self.assertEqual(len(baseline['findings']), 1)
        self.assertEqual(baseline['findings'][0]['fingerprint'], old_fingerprint)
        baseline['findings'][0]['message'] = "arità: passa 0 argomenti a publish: missing a required argument: 'value'"
        path.write_text(json.dumps(baseline))
        result = json.loads(self.cli('review', '--baseline', '.code-graph/baseline.json', '--json'))
        self.assertEqual(result['summary'], dict(new=0, persistent=1, resolved=0, uncompared=0))


if __name__ == '__main__':
    unittest.main()
