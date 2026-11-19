"""Build the dashboard's map from the engine's English texts back to their Italian wording.

The engine's messages were translated from Italian to English (I18N, 706afc1 -> 266d1ad); the dashboard
still offers Italian. This compares each module's string constants before and after that change and keeps
the pairs, so the Italian dashboard shows the original wording. New engine texts written in English need an
Italian entry added by hand under `manual` in the same line of assets/workbench-i18n.js; generated entries the
engine no longer prints are removed (tests/test_workbench_i18n.py finds them).

    python tools/engine_italian.py BEFORE AFTER   # prints the JSON map (exact texts and prefixes)
"""
import ast
import difflib
import json
import re
import subprocess
import sys

MODULES = ('features.py', 'project_data.py', 'architecture.py', 'feature_packets.py', 'evidence.py',
           'entrypoints.py', 'workbench.py', 'search.py', 'source_windows.py', 'catalog.py', 'change_analysis.py',
           'checks.py', 'agent_queries.py', 'agents.py', 'change_flow.py', 'locate.py')


def constants(source):
    """String constants in source order; an f-string becomes one template with {} for each value."""
    found = []

    def visit(node):
        if isinstance(node, ast.JoinedStr):
            found.append(''.join(part.value if isinstance(part, ast.Constant) else '{}' for part in node.values))
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(node.value)
        for child in ast.iter_child_nodes(node):
            visit(child)
    visit(ast.parse(source))
    return found


def show(revision, path):
    return subprocess.run(['git', 'show', f'{revision}:scripts/{path}'], capture_output=True, text=True, check=True).stdout


def pairs(before, after):
    exact, prefixes, templates = {}, {}, {}
    for module in MODULES:
        old, new = constants(show(before, module)), constants(show(after, module))
        matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != 'replace' or i2 - i1 != j2 - j1:
                continue
            for italian, english in zip(old[i1:i2], new[j1:j2]):
                # The translation changed only texts, so every changed constant is a translated text.
                if italian == english or len(english) < 3 or not re.search(r'[A-Za-z]{3}', english):
                    continue
                if '{}' in english:
                    if english.count('{}') == italian.count('{}'):
                        templates[english] = italian
                elif english.endswith(': ') and italian.endswith(': ') and not english[0].isspace():
                    prefixes[english] = italian
                else:
                    exact.setdefault(english, italian)
    return dict(exact=dict(sorted(exact.items())), prefixes=dict(sorted(prefixes.items())),
                templates=dict(sorted(templates.items())))


if __name__ == '__main__':
    print(json.dumps(pairs(*sys.argv[1:3]), ensure_ascii=False, sort_keys=True))
