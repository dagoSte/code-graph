"""The guide in docs/guide stays true to the code: generated reference fresh, every link and anchor resolves."""
import argparse
import re
import runpy
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUIDE = REPO / 'docs/guide'
LINK = re.compile(r'(?<!!)\[[^\]]*\]\(([^)\s]+)\)|!\[[^\]]*\]\(([^)\s]+)\)|(?:src|href)="([^"]+)"')
FENCE = re.compile(r'^```.*?^```', re.M | re.S)


def slug(heading):
    """GitHub's anchor for a Markdown heading."""
    text = re.sub(r'<[^>]+>', '', heading).strip().lower()
    text = re.sub(r'[`*_~]', lambda m: '_' if m.group() == '_' else '', text)
    return re.sub(r'[^\w\- ]', '', text).replace(' ', '-')


def anchors(text):
    found, counts = set(), {}
    for heading in re.findall(r'^#{1,6} (.+)$', FENCE.sub('', text), re.M):
        base = slug(heading)
        number = counts.get(base, 0)
        counts[base] = number + 1
        found.add(base if number == 0 else f'{base}-{number}')
    return found | set(re.findall(r'<a id="([^"]+)"></a>', text))


def links(text):
    for match in LINK.finditer(FENCE.sub('', text)):
        target = next(group for group in match.groups() if group)
        if not re.match(r'[a-z]+:', target):
            yield target


class Guide(unittest.TestCase):
    def test_reference_is_generated_from_the_current_code(self):
        result = subprocess.run([sys.executable, str(REPO / 'tools/gen_reference.py'), '--check'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_local_paths_of_the_machine_that_built_the_guide(self):
        home = str(Path.home())
        for page in sorted(GUIDE.rglob('*.md')):
            with self.subTest(page=str(page.relative_to(REPO))):
                self.assertNotIn(home, page.read_text())

    def test_examples_show_what_the_commands_print_today(self):
        result = subprocess.run([sys.executable, str(REPO / 'tools/docs_examples.py'), '--check'],
                                capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_command_and_option_has_english_help(self):
        cg = runpy.run_path(str(REPO / 'scripts/cg.py'))
        change = runpy.run_path(str(REPO / 'scripts/change_flow.py'))['build_parser']()
        parser = cg['build_parser']()
        parsers = [('cg', parser)]
        for top in (parser, change):
            sub = next(a for a in top._actions if isinstance(a, argparse._SubParsersAction))
            parsers += [(f'{top.prog} {name}', command) for name, command in sub.choices.items()]
        for name, command in parsers:
            for action in command._actions:
                if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
                    continue
                with self.subTest(command=name, option=action.dest):
                    self.assertTrue(action.help, 'no help text')
                    self.assertNotRegex(action.help, r'[àèéìòù]| della | per | il ', 'not English')
            if name != 'cg':
                with self.subTest(command=name):
                    self.assertTrue(command.description or name.startswith('cg change '), 'no description')

    def test_links_and_anchors_resolve(self):
        pages = sorted(GUIDE.rglob('*.md'))
        self.assertGreater(len(pages), 10)
        own = {page: anchors(page.read_text()) for page in pages}
        for page in pages:
            for target in links(page.read_text()):
                with self.subTest(page=str(page.relative_to(REPO)), target=target):
                    file, _, fragment = target.partition('#')
                    destination = (page.parent / file).resolve() if file else page
                    self.assertTrue(destination.exists(), target)
                    if fragment and destination.suffix == '.md':
                        known = own.get(destination) or anchors(destination.read_text())
                        self.assertIn(fragment, known, target)

    def test_every_page_is_reachable_from_the_index(self):
        seen, queue = set(), [GUIDE / 'README.md']
        while queue:
            page = queue.pop()
            if page in seen or not page.exists():
                continue
            seen.add(page)
            for target in links(page.read_text()):
                file = target.partition('#')[0]
                target_page = (page.parent / file).resolve()
                if file.endswith('.md') and GUIDE.resolve() in target_page.parents:
                    queue.append(target_page)
        for page in sorted(GUIDE.rglob('*.md')):
            with self.subTest(page=str(page.relative_to(REPO))):
                self.assertIn(page.resolve(), seen)


if __name__ == '__main__':
    unittest.main()
