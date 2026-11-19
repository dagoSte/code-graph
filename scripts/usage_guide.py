"""Small, inert renderer for the shipped docs/guide; not a CommonMark implementation."""
from html import escape
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit

SUFFIXES = {'.md', '.py', '.json', '.toml', '.txt'}
HEADING = re.compile(r'^(#{1,6})\s+(.+?)(?:\s+#+)?$')
FENCE = re.compile(r'^\s*(`{3,}|~{3,})(.*)$')
ITEM = re.compile(r'^\s*([-*+]|\d+[.)])\s+(.+)$')
DIVIDER = re.compile(r'\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*')
INLINE = re.compile(r'`([^`\n]+)`|\*\*([^*\n]+)\*\*|!?\[([^\]\n]*)\]\(([^)\s]+)\)')


def destination(target, path, root):
    """Only manual HTTP(S), fragments and bounded documentation/repository links."""
    decoded = unquote(target)
    if '\\' in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        return None
    try:
        parts = urlsplit(target)
    except ValueError:
        return None
    if parts.scheme:
        return target if parts.scheme.lower() in ('http', 'https') and parts.netloc else None
    if parts.netloc or unquote(parts.path).startswith('/'):
        return None
    fragment = '#' + quote(unquote(parts.fragment), safe='-_') if parts.fragment else ''
    if not parts.path:
        return fragment or '#'
    candidate = (path.parent / unquote(parts.path)).resolve()
    if candidate.is_relative_to(root):
        if candidate.is_dir():
            candidate /= 'README.md'
        return '/guide/' + quote(candidate.relative_to(root).as_posix()) + fragment
    repository = root.parent.parent  # The shipped guide lives at <distribution>/docs/guide.
    if candidate.is_relative_to(repository):
        kind = 'tree' if candidate.is_dir() else 'blob'
        return 'https://github.com/dagoSte/code-graph/' + kind + '/main/' + quote(candidate.relative_to(repository).as_posix()) + fragment
    return None


def inline(text, path, root):
    output, end = [], 0
    for match in INLINE.finditer(text):
        output.append(escape(text[end:match.start()]))
        code, bold, label, target = match.groups()
        if code is not None:
            output.append('<code>' + escape(code) + '</code>')
        elif bold is not None:
            output.append('<strong>' + inline(bold, path, root) + '</strong>')
        else:
            href, label = destination(target, path, root), inline(label, path, root)
            extra = ' target="_blank" rel="noopener noreferrer"' if href and href.lower().startswith(('http:', 'https:')) else ''
            output.append('<a href="' + escape(href, quote=True) + '"' + extra + '>' + label + '</a>' if href else label)
        end = match.end()
    return ''.join(output) + escape(text[end:])


def markdown(text, path, root):
    lines, output, used, i = text.splitlines(), [], {}, 0
    def block(line):
        return HEADING.match(line) or FENCE.match(line) or ITEM.match(line) or line.strip().startswith('<!--')
    def cells(line, tag):
        values = re.split(r'(?<!\\)\|', line.strip().strip('|'))
        return '<tr>' + ''.join('<' + tag + '>' + inline(value.strip().replace('\\|', '|'), path, root) + '</' + tag + '>' for value in values) + '</tr>'
    while i < len(lines):
        line = lines[i]
        if not line.strip() or re.fullmatch(r'\s*<!--.*-->\s*', line):
            i += 1; continue
        fence, heading, item = FENCE.match(line), HEADING.match(line), ITEM.match(line)
        if fence:
            body, marker = [], fence[1]; i += 1
            while i < len(lines) and not re.fullmatch(r'\s*' + re.escape(marker[0]) + '{' + str(len(marker)) + r',}\s*', lines[i]):
                body.append(lines[i]); i += 1
            output.append('<pre><code>' + escape('\n'.join(body)) + '</code></pre>'); i += 1
        elif heading:
            level, title = len(heading[1]), heading[2]
            plain = re.sub(r'!?\[([^\]]*)\]\([^)]+\)', r'\1', re.sub(r'<[^>]*>', '', title))
            slug = re.sub(r'[^\w -]', '', plain.strip().lower()).replace(' ', '-')
            number = used.get(slug, 0); used[slug] = number + 1
            anchor = slug + (f'-{number}' if number else '')
            output.append(f'<h{level} id="{escape(anchor, quote=True)}">' + inline(title, path, root) + f'</h{level}>'); i += 1
        elif i + 1 < len(lines) and '|' in line and DIVIDER.fullmatch(lines[i + 1]):
            output.append('<table><thead>' + cells(line, 'th') + '</thead><tbody>'); i += 2
            while i < len(lines) and lines[i].strip() and '|' in lines[i]:
                output.append(cells(lines[i], 'td')); i += 1
            output.append('</tbody></table>')
        elif item:
            tag = 'ol' if item[1][0].isdigit() else 'ul'; output.append('<' + tag + '>')
            while i < len(lines) and (item := ITEM.match(lines[i])) and ('ol' if item[1][0].isdigit() else 'ul') == tag:
                body = [item[2]]; i += 1
                while i < len(lines) and lines[i].strip() and not block(lines[i]):
                    body.append(lines[i].strip()); i += 1
                output.append('<li>' + inline(' '.join(body), path, root) + '</li>')
            output.append('</' + tag + '>')
        else:
            body = [line.strip()]; i += 1
            while i < len(lines) and lines[i].strip() and not block(lines[i]):
                if i + 1 < len(lines) and DIVIDER.fullmatch(lines[i + 1]):
                    break
                body.append(lines[i].strip()); i += 1
            output.append('<p>' + inline(' '.join(body), path, root) + '</p>')
    return '\n'.join(output)


def render(path, guide_root):
    """Render a validated shipped file; source/configuration files are text, never executed."""
    path, root = Path(path).resolve(), Path(guide_root).resolve()
    if not path.is_relative_to(root) or path.suffix not in SUFFIXES:
        raise ValueError('Unsupported guide file.')
    text = path.read_text(encoding='utf-8', errors='replace')
    body = markdown(text, path, root) if path.suffix == '.md' else '<pre><code>' + escape(text) + '</code></pre>'
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + escape(path.name) + ' · code-graph user guide</title>'
            '<link rel="stylesheet" href="/usage-guide.css"></head><body>'
            '<header><a href="/guide/README.md">code-graph · User guide</a></header>'
            '<main>' + body + '</main></body></html>')
