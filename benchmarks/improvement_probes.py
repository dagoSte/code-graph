import argparse
import collections
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import tempfile

parser = argparse.ArgumentParser(description='Probes di contesto e duplicazione su fixture sintetiche, senza eseguire i sorgenti indicizzati.')
parser.add_argument('--skill-root', type=Path, required=True)
parser.add_argument('--output', type=Path)
options = parser.parse_args()
skill = options.skill_root.resolve()
spec = importlib.util.spec_from_file_location('cg_proposals_probe', skill / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
ProjectData = runpy.run_path(str(skill / 'scripts/project_data.py'))['ProjectData']
with tempfile.TemporaryDirectory(prefix='cg-proposal-evidence-') as temp:
    root = Path(temp)
    (root / 'tests').mkdir()
    body = ['def leaf(value):', '    return value + 1', '', 'def work(value):']
    body += [f'    step_{i} = value + {i}' for i in range(240)]
    body += ['    return leaf(step_239)', '', 'def entry(value):', '    return work(value)', '']
    (root / 'app.py').write_text('\n'.join(body))
    tests = ['from app import entry', '']
    for i in range(12):
        tests += [f'def test_work_{i}():', f'    return entry({i})', '']
    (root / 'tests/test_work.py').write_text('\n'.join(tests))
    with contextlib.redirect_stdout(io.StringIO()):
        cg.cmd_index(argparse.Namespace(root=str(root), full=False))
    db = cg.connect(str(root))
    revision_before = db.execute("select v from meta where k='revision'").fetchone()[0]
    sid = db.execute("select id from symbols where qname='work'").fetchone()[0]
    db.close()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        cg.cmd_context(argparse.Namespace(root=str(root), symbol='work', budget=250, min_conf='medium'))
    content = output.getvalue()
    with ProjectData(cg, root) as project:
        impact = project.impact(sid, confidence='high', depth=3)
        modern = 'compact' in __import__('inspect').signature(project.impact).parameters
        detail = project.detail(sid, confidence='high', fields=['source','references']) if modern else project.detail(sid, confidence='high')
        packed = project.impact(sid, confidence='high', compact=True) if modern else None
    snippets = [(s['file'], s['line'], json.dumps(s['source'], sort_keys=True)) for item in impact['items'] for s in item['chain']]
    duplicates = sum(n-1 for n in collections.Counter(snippets).values())
    chain_refs=len(snippets)
    if packed is not None:
        snippets=list(packed['snippets']);duplicates=0
    with contextlib.redirect_stdout(io.StringIO()):
        cg.cmd_index(argparse.Namespace(root=str(root), full=False))
    db = cg.connect(str(root))
    revision_after = db.execute("select v from meta where k='revision'").fetchone()[0]
    db.close()
    result = {
        'engine_version': cg.VERSION,
        'analysis_code_sha256': {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((skill/'scripts').glob('*.py'))},
        'engine_sha256': hashlib.sha256((skill / 'scripts/cg.py').read_bytes()).hexdigest(),
        'fixture': {'indexed_files': 2, 'test_functions': 12, 'work_assignment_lines': 240, 'source_executed': False},
        'context': {'requested_estimated_tokens': 250, 'output_characters_including_footer': len(content), 'output_utf8_bytes': len(content.encode()), 'body_truncated': 'troncato' in content or 'source: partial' in content, 'callers_section_present': '## CHIAMANTI' in content, 'callees_section_present': '## CHIAMATI' in content},
        'impact': {'items': len(impact['items']), 'test_items': len(impact['tests']), 'chain_snippet_references': chain_refs, 'source_snippet_occurrences': len(snippets), 'unique_source_snippets': len(set(snippets)), 'repeated_source_snippet_occurrences': duplicates},
        'detail': {'fields': list(detail), 'json_utf8_bytes': len(json.dumps(detail, ensure_ascii=False).encode()), 'source_lines': len(detail['source']['lines']), 'source_truncated': detail['source']['truncated']},
        'index': {'no_source_changes': True, 'revision_changed_on_noop': revision_before != revision_after},
    }
    if options.output:
        options.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
