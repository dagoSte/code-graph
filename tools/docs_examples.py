"""Run the commands written in the guide on the example project and put their real output in the pages.

A page marks an example like this; everything between the markers is replaced:

    <!-- cg: locate "Cancelling an order does not refund the customer" | lines=14 -->
    ```text
    (output)
    ```
    <!-- /cg -->

Options after `|`: lines=N keeps the first N lines, pretty pretty-prints JSON, keep=a.b.0 keeps one
part of a JSON answer, edit=NAME applies a change from EDITS first (for review examples).
Each example runs on a fresh copy of docs/guide/example/shop, committed in a new Git repository and
indexed, so examples do not depend on each other or on this checkout.

    python tools/docs_examples.py          # refresh every page
    python tools/docs_examples.py --check  # exit 1 if a page shows output the code no longer prints
"""
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUIDE = REPO / 'docs/guide'
EXAMPLE = GUIDE / 'example/shop'
CG = REPO / 'scripts/cg'
BLOCK = re.compile(r'(<!-- cg: (.+?) -->\n)(.*?)(<!-- /cg -->)', re.S)
# Changes applied before an example; each is (file, old text, new text).
EDITS = {
    'partial-refund': ('shop/orders.py',
                       "    refunded = payments.refund(order, order.paid) if order.paid else 0\n",
                       "    refunded = payments.refund(order, order.total()) if order.paid else 0\n"),
}
# Values that change from run to run; --check compares pages with these masked.
VOLATILE = (
    (re.compile(r'\b[0-9a-f]{32,64}\b'), '<hash>'),
    (re.compile(r'\b\d+(?:\.\d+)?\s?(?:ms|s)\b'), '<time>'),
    (re.compile(r'\d{4}-\d\d-\d\dT?[ \d:.+Z-]*'), '<date>'),
    (re.compile(r'/[^\s"\']*?/shop\b'), '<root>'),
    (re.compile(r'127\.0\.0\.1:\d+\S*'), '127.0.0.1:<port>'),
)


def masked(text):
    for pattern, replacement in VOLATILE:
        text = pattern.sub(replacement, text)
    return text


def git(root, *args):
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=guide', '-c', 'user.email=guide@example.invalid',
                    '-c', 'commit.gpgsign=false', *args], check=True, capture_output=True)


def project(folder, edit=None):
    root = Path(folder) / 'shop'
    shutil.copytree(EXAMPLE, root, ignore=shutil.ignore_patterns('.code-graph', '__pycache__'))
    git(root, 'init', '-q', '-b', 'main')
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', 'shop')
    subprocess.run([str(CG), '--root', str(root), 'index'], check=True, capture_output=True)
    if edit:
        path, old, new = EDITS[edit]
        file = root / path
        text = file.read_text()
        if old not in text:
            raise SystemExit(f'docs_examples: edit {edit} no longer applies to {path}')
        file.write_text(text.replace(old, new))
        subprocess.run([str(CG), '--root', str(root), 'index'], check=True, capture_output=True)
    return root


def pick(value, path):
    for part in path.split('.'):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def run(spec):
    command, _, options = spec.partition(' | ')
    settings = dict(item.split('=', 1) if '=' in item else (item, True) for item in options.split())
    args = [arg.replace('\\n', '\n') for arg in shlex.split(command)]  # \n in a marker is a new line
    with tempfile.TemporaryDirectory() as folder:
        root = project(folder, settings.get('edit'))
        result = subprocess.run([str(CG), '--root', str(root), *args], capture_output=True, text=True, timeout=300,
                                cwd=root)
    output = (result.stdout + result.stderr).replace(str(CG), 'cg')
    for form in sorted({str(root.resolve()), str(root)}, key=len, reverse=True):  # /var is /private/var on macOS
        output = output.replace(form, '~/code/shop')
    if result.returncode:
        raise SystemExit(f'docs_examples: `cg {command}` failed ({result.returncode}):\n{output}')
    language = 'text'
    if 'keep' in settings or 'pretty' in settings:
        value = json.loads(output)
        if 'keep' in settings:
            value = pick(value, settings['keep'])
        output = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
        language = 'json'
    lines = output.rstrip('\n').split('\n')
    if 'lines' in settings and len(lines) > int(settings['lines']):
        lines = lines[:int(settings['lines'])] + ['…']
    # A multi-line argument (a pasted trace) is shown as read from a file, with the file before the block.
    shown = 'cg ' + ' '.join('"$(cat request.txt)"' if '\n' in arg else shlex.quote(arg) for arg in args) \
        if language == 'text' else None
    body = '\n'.join(([f'$ {shown}'] if shown else []) + lines)
    return f'```{language}\n{body}\n```\n'


def refresh(text):
    return BLOCK.sub(lambda m: m.group(1) + run(m.group(2)) + m.group(4), text)


def main():
    check = '--check' in sys.argv[1:]
    stale = []
    for page in sorted(GUIDE.rglob('*.md')):
        if EXAMPLE in page.parents:
            continue
        text = page.read_text()
        if '<!-- cg: ' not in text:
            continue
        fresh = refresh(text)
        if check:
            if masked(fresh) != masked(text):
                stale.append(str(page.relative_to(REPO)))
        elif fresh != text:
            page.write_text(fresh)
            print('updated', page.relative_to(REPO))
    if stale:
        raise SystemExit('Examples out of date, run python tools/docs_examples.py: ' + ', '.join(stale))


if __name__ == '__main__':
    main()
