"""The public READMEs stay consistent: links resolve, both languages match, charts are fresh."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
READMES = {'en': REPO / 'README.md', 'it': REPO / 'README.it.md'}
LINK = re.compile(r'\]\(([^)\s]+)\)|(?:src|srcset|href)="([^"]+)"')


def targets(text):
    for match in LINK.finditer(text):
        target = match.group(1) or match.group(2)
        if not re.match(r'[a-z]+:', target):
            yield target


def anchors(text):
    return set(re.findall(r'<a id="([^"]+)"></a>', text))


def images(text):
    return sorted(t for t in targets(text) if t.startswith('docs/images/') or t.startswith('assets/'))


class Readme(unittest.TestCase):
    def test_relative_links_and_images_exist(self):
        for language, path in READMES.items():
            text = path.read_text()
            own = anchors(text)
            for target in targets(text):
                with self.subTest(language=language, target=target):
                    file, _, fragment = target.partition('#')
                    if file:
                        self.assertTrue((REPO / file).exists(), target)
                    else:
                        self.assertIn(fragment, own, target)

    def test_languages_share_anchors_and_images(self):
        en, it = (READMES[k].read_text() for k in ('en', 'it'))
        self.assertEqual(anchors(en), anchors(it))
        self.assertEqual(images(en), images(it))
        self.assertEqual(en.count('```mermaid'), it.count('```mermaid'))

    def test_every_published_image_is_used(self):
        used = set(images(READMES['en'].read_text()))
        for image in (REPO / 'docs/images').iterdir():
            with self.subTest(image=image.name):
                self.assertIn('docs/images/' + image.name, used)

    def test_no_development_leftovers(self):
        for language, path in READMES.items():
            text = path.read_text()
            for leftover in ('catalog-demo', 'code-graph.zip', '(in Italian)', '(in italiano)', 'references/'):
                with self.subTest(language=language, leftover=leftover):
                    self.assertNotIn(leftover, text)

    def test_chart_counts_hybrid_hits_not_the_union_of_both_modes(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('readme_charts', REPO / 'tools/readme_charts.py')
        charts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(charts)
        report = {'queries': [{'query': 'q', 'relevant': ['a.py:A', 'b.py:B'], 'modes': {
            'lexical': {'hits': ['a.py:A']}, 'hybrid': {'hits': ['b.py:B']}}}]}
        row, = charts.rows(report)
        self.assertEqual((row['lexical'], row['hybrid'], row['kept'], row['added']), (1, 1, 0, 1))
        svg = charts.render(report, 'light')
        self.assertIn('1/2  (BM25 alone 1/2)', svg)
        self.assertIn('hybrid 1/2', svg)

    def test_retrieval_charts_match_committed_results(self):
        result = subprocess.run([sys.executable, '-I', str(REPO / 'tools/readme_charts.py'), '--check'],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
