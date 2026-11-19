"""Local documentation renders as inert HTML, with deliberate navigation only."""
from html.parser import HTMLParser
from pathlib import Path
import runpy
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
render = runpy.run_path(str(REPO / 'scripts/usage_guide.py'))['render']


class Tags(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class UsageGuide(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cg-usage-guide-test-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.guide = self.repo / 'docs/guide'
        self.guide.mkdir(parents=True)

    def page(self, text, name='README.md'):
        path = self.guide / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return render(path, self.guide)

    def test_raw_html_and_images_never_create_active_content_or_remote_assets(self):
        result = self.page('# Guide\n\n<script>alert(1)</script>\n'
                           '<img src=https://example.invalid/pixel onerror=alert(1)>\n\n'
                           '![Diagram](https://example.invalid/diagram.png)')
        tags = Tags(result).tags
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', result)
        self.assertFalse(any(tag in ('script', 'img', 'iframe', 'object', 'style') for tag, _ in tags))
        self.assertEqual([(tag, attrs.get('href')) for tag, attrs in tags if tag == 'link'],
                         [('link', '/usage-guide.css')])
        self.assertFalse(any('src' in attrs or any(key.startswith('on') for key in attrs) for _, attrs in tags))
        image = next(attrs for tag, attrs in tags if attrs.get('href') == 'https://example.invalid/diagram.png')
        self.assertEqual(image['rel'], 'noopener noreferrer')
        self.assertEqual(image['target'], '_blank')

    def test_unsafe_uri_schemes_authorities_and_encoded_controls_are_not_links(self):
        unsafe = ('javascript:alert', 'data:text/html,evil', 'file:///etc/passwd', '//example.invalid/x',
                  '%2f%2fexample.invalid/x', 'https://example.invalid/%0aevil', '..%5cprivate', '/etc/passwd')
        result = self.page('\n\n'.join(f'[unsafe-{i}]({uri})' for i, uri in enumerate(unsafe)))
        self.assertEqual([attrs['href'] for tag, attrs in Tags(result).tags if tag == 'a'], ['/guide/README.md'])
        for i in range(len(unsafe)):
            self.assertIn(f'unsafe-{i}', result)

    def test_links_escape_attributes_and_keep_explicit_https_navigation(self):
        result = self.page('[<svg/onload=evil>](HTTPS://example.invalid/\"onload=evil)')
        tags = Tags(result).tags
        self.assertIn('&lt;svg/onload=evil&gt;', result)
        link = next(attrs for tag, attrs in tags if attrs.get('target') == '_blank')
        self.assertEqual(link['href'], 'HTTPS://example.invalid/\"onload=evil')
        self.assertNotIn('onload', link)
        self.assertNotIn('svg', [tag for tag, _ in tags])

    def test_relative_links_stay_local_and_repository_links_go_to_static_github(self):
        (self.repo / 'benchmarks').mkdir()
        result = self.page('[Local](../reference/my%20page.md#cg_index)\n\n'
                           '[Top](#example) [README](../../../README.md#install) '
                           '[Bench](../../../benchmarks/) [Escape](../../../../secret.md)', 'guides/task.md')
        links = [attrs['href'] for tag, attrs in Tags(result).tags if tag == 'a']
        self.assertIn('/guide/reference/my%20page.md#cg_index', links)
        self.assertIn('#example', links)
        self.assertIn('https://github.com/dagoSte/code-graph/blob/main/README.md#install', links)
        self.assertIn('https://github.com/dagoSte/code-graph/tree/main/benchmarks', links)
        self.assertFalse(any('secret' in link for link in links))

    def test_headings_have_github_style_ids_including_duplicates_and_unicode(self):
        result = self.page('# **Hello**, `cg_index`!\n\n## Café\n\n## Café\n\n## [Linked](#x) title\n\n[Unicode](#caf%C3%A9)')
        ids = [attrs['id'] for tag, attrs in Tags(result).tags if re_heading(tag)]
        self.assertEqual(ids, ['hello-cg_index', 'café', 'café-1', 'linked-title'])
        self.assertIn('<strong>Hello</strong>', result)
        self.assertIn('<code>cg_index</code>', result)
        self.assertIn('href="#caf%C3%A9"', result)

    def test_fenced_code_is_escaped_and_never_parsed_as_markdown(self):
        result = self.page('```python\n# not a heading\n[x](https://example.invalid)\n<script>\n```\n\nAfter')
        self.assertIn('<pre><code># not a heading\n[x](https://example.invalid)\n&lt;script&gt;', result)
        self.assertFalse(any(re_heading(tag) for tag, _ in Tags(result).tags))
        self.assertEqual(sum(tag == 'a' for tag, _ in Tags(result).tags), 1)
        self.assertIn('<p>After</p>', result)

    def test_table_cells_keep_inline_code_bold_and_escaped_pipes(self):
        result = self.page('| Option | Meaning |\n|---|---|\n| `a\\|b` | **Choose** <x> |')
        tags = [tag for tag, _ in Tags(result).tags]
        self.assertEqual(tags.count('table'), 1)
        self.assertEqual(tags.count('th'), 2)
        self.assertEqual(tags.count('td'), 2)
        self.assertIn('<code>a|b</code>', result)
        self.assertIn('<strong>Choose</strong> &lt;x&gt;', result)

    def test_basic_lists_paragraphs_and_wrapped_items(self):
        result = self.page('Paragraph\ncontinued.\n\n- **First**\n  with `code`.\n- Second\n\n1. One\n2. Two')
        self.assertIn('<p>Paragraph continued.</p>', result)
        self.assertIn('<li><strong>First</strong> with <code>code</code>.</li>', result)
        tags = [tag for tag, _ in Tags(result).tags]
        self.assertEqual((tags.count('ul'), tags.count('ol'), tags.count('li')), (1, 1, 4))

    def test_source_and_configuration_bytes_are_only_escaped_text(self):
        for suffix in ('.py', '.json', '.toml', '.txt'):
            with self.subTest(suffix=suffix):
                path = self.guide / ('sample' + suffix)
                path.write_bytes(b'\xff<script>\n# **raw**\n' + 'café'.encode())
                result = render(path, self.guide)
                self.assertIn('<pre><code>�&lt;script&gt;\n# **raw**\ncafé</code></pre>', result)
                self.assertFalse(any(tag in ('script', 'strong', 'h1') for tag, _ in Tags(result).tags))

    def test_render_refuses_outside_files_and_unsupported_extensions(self):
        path = self.repo / 'outside.md'
        path.write_text('private', encoding='utf-8')
        with self.assertRaises(ValueError):
            render(path, self.guide)
        with self.assertRaises(ValueError):
            self.page('<script>', 'active.html')

    def test_all_shipped_guide_pages_render_without_remote_automatic_resources(self):
        pages = sorted((REPO / 'docs/guide').rglob('*.md'))
        self.assertGreater(len(pages), 10)
        for path in pages:
            with self.subTest(path=path.relative_to(REPO)):
                result = render(path, REPO / 'docs/guide')
                tags = Tags(result).tags
                self.assertFalse(any(tag in ('script', 'img', 'iframe') for tag, _ in tags))
                self.assertEqual([attrs['href'] for tag, attrs in tags if tag == 'link'], ['/usage-guide.css'])


def re_heading(tag):
    return tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6')


if __name__ == '__main__':
    unittest.main()
