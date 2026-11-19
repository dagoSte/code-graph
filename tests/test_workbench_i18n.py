"""English dashboard: dynamic overview and symbol labels go through the translation templates."""
import json
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
I18N = (REPO / 'assets/workbench-i18n.js').read_text()
SCRIPT = (REPO / 'assets/workbench.js').read_text()


def templates():
    line = next(l for l in I18N.splitlines() if l.startswith('const templates='))
    return json.loads(line[len('const templates='):].rstrip(';'))


def translate(value):
    """Python mirror of the template loop in workbench-i18n.js (first matching template wins)."""
    for italian, english in templates():
        keys, parts = [], []
        for part in re.split(r'(\{\w+\})', italian):
            if re.fullmatch(r'\{\w+\}', part):
                keys.append(part[1:-1]); parts.append('(.*?)')
            else:
                parts.append(re.escape(part))
        match = re.fullmatch(''.join(parts), value)
        if match:
            args = dict(zip(keys, match.groups()))
            return re.sub(r'\{(\w+)\}', lambda m: args[m.group(1)], english)
    return value


class WorkbenchI18n(unittest.TestCase):
    def test_language_counts_and_registered_references_have_english_templates(self):
        self.assertEqual(translate('dart: 4 file'), 'dart: 4 files')
        self.assertEqual(translate('sql: 1 file'), 'sql: 1 file')
        self.assertEqual(translate('Registrata come callback · scripts/cg.py:3481'), 'Registered callback · scripts/cg.py:3481')
        self.assertEqual(translate('Entrypoint registrato · app.py:2'), 'Registered entry point · app.py:2')

    def test_dynamic_labels_are_passed_through_translation(self):
        for literal in ("Registrata come callback", "${l.files} file`"):
            with self.subTest(literal=literal):
                index = SCRIPT.index(literal)
                start = SCRIPT.rfind('`', 0, index)
                if literal.endswith('`'):
                    start = SCRIPT.rfind('`', 0, index - 1)
                self.assertEqual(SCRIPT[start - 3:start], 'tr(', SCRIPT[start - 40:index + 40])

    def test_start_here_overview_strings_have_english_translations(self):
        start = SCRIPT.index("tr('Inizia da qui')")
        block = SCRIPT[start:SCRIPT.index('box.append(recipes)', start)]
        strings = set(re.findall(r"tr\('((?:[^'\\]|\\.)*)'\)", block))
        self.assertGreaterEqual(len(strings), 10)
        missing = sorted(text for text in strings if json.dumps(text, ensure_ascii=False) + ':' not in I18N)
        self.assertEqual(missing, [])


def engine_italian():
    line = next(l for l in I18N.splitlines() if l.startswith('const engineItalian='))
    return json.loads(line[len('const engineItalian='):].rstrip(';'))


def to_italian(value):
    """Python mirror of engineToItalian in workbench-i18n.js."""
    data = engine_italian()
    if value in data['exact']:
        return data['exact'][value]
    if value in data.get('manual', {}):
        return data['manual'][value]
    for english, italian in data['prefixes'].items():
        if value.startswith(english):
            return italian + value[len(english):]
    for english, italian in data['templates'].items():
        match = re.fullmatch('(.*?)'.join(re.escape(part) for part in english.split('{}')), value, re.S)
        if match:
            pieces = italian.split('{}')
            return ''.join(piece + (match.group(i + 1) if i < len(match.groups()) else '') for i, piece in enumerate(pieces))
    return None


class EngineTextsInItalian(unittest.TestCase):
    """The engine speaks English; the Italian dashboard shows the original Italian wording."""

    def test_engine_labels_and_messages_come_back_in_italian(self):
        for english, italian in (('Code registration', 'Registrazione nel codice'), ('Whole project', 'Tutto il progetto'),
                                 ('Run step in the workflow', 'Step run nel workflow'),
                                 ('Publishing step in the workflow', 'Step di pubblicazione nel workflow'),
                                 ('Declared script: build', 'Script dichiarato: build'),
                                 ('Declared entrypoint: app', 'Entrypoint dichiarato: app'),
                                 ('Manual manifest: declared', 'Manifest manuale: declared'),
                                 ('Already 3 questions running: wait for one to finish.', 'Già 3 domande in corso: attendi che una termini.')):
            with self.subTest(english=english):
                self.assertEqual(to_italian(english), italian)
        self.assertIsNone(to_italian('A text the engine never printed'))

    def test_every_entry_is_a_text_the_engine_still_prints(self):
        import runpy
        constants = runpy.run_path(str(REPO / 'tools/engine_italian.py'))['constants']
        modules = runpy.run_path(str(REPO / 'tools/engine_italian.py'))['MODULES']
        current = {text for module in modules for text in constants((REPO / 'scripts' / module).read_text())}
        data = engine_italian()
        for kind in ('exact', 'prefixes', 'templates', 'manual'):
            for english in data.get(kind, {}):
                with self.subTest(kind=kind, english=english[:60]):
                    self.assertTrue(english in current, 'stale entry: the engine no longer prints this text')

    def test_generated_part_is_what_the_tool_prints(self):
        # The tool reads two historical commits; a copy without history (the public single-commit
        # repository) checks against the committed output of the tool instead.
        import subprocess
        import sys
        fixture = REPO / 'tests/fixtures/engine-italian-706afc1-266d1ad.json'
        generated = json.loads(fixture.read_text())
        history = all(subprocess.run(['git', 'cat-file', '-e', revision + '^{commit}'], cwd=REPO,
                                     capture_output=True).returncode == 0 for revision in ('706afc1', '266d1ad'))
        if history:
            printed = json.loads(subprocess.run([sys.executable, str(REPO / 'tools/engine_italian.py'), '706afc1', '266d1ad'],
                                                capture_output=True, text=True, check=True, cwd=REPO).stdout)
            self.assertEqual(printed, generated, 'the committed fixture is not what the tool prints')
        data = engine_italian()
        for kind in ('exact', 'prefixes', 'templates'):
            removed = set(generated[kind]) - set(data[kind])
            with self.subTest(kind=kind):
                self.assertEqual({k: v for k, v in generated[kind].items() if k not in removed}, data[kind])
                self.assertTrue(all(key not in data.get('manual', {}) for key in removed))


if __name__ == '__main__':
    unittest.main()
