"""Deterministic historical-diff targets and frozen locate scoring; no models."""
import json
import re
import sys
import time


def old_ranges(diff):
    return [(int(start), int(count) if count else 1)
            for start, count in re.findall(r'^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@', diff, re.M)]


def targets(engine, root, language, filename, diff):
    path = root / filename
    ranges = old_ranges(diff)
    result = dict(file=filename, old_hunks=[dict(start=start, count=count) for start, count in ranges], declarations=[])
    if not path.is_file():
        result['status'] = 'absent_in_parent'
        return result
    source = path.read_bytes()
    language = 'tsx' if filename.endswith('.tsx') else language
    tree = engine.get_parser_for(language).parse(source)
    if tree.root_node.has_error:
        result['status'] = 'partial_parent_ast'
        return result
    extraction = engine.Extractor(language, source, source_path=filename, root=root).run(tree.root_node)
    for symbol in extraction.syms:
        if symbol['kind'] not in ('function', 'method'):
            continue
        if any((symbol['line'] <= start < symbol['end_line']) if count == 0 else
               (symbol['line'] <= start + count - 1 and start <= symbol['end_line']) for start, count in ranges):
            result['declarations'].append({key: symbol[key] for key in ('qname', 'kind', 'line', 'end_line')})
    result['status'] = 'mapped' if result['declarations'] else 'no_parent_callable_hunk'
    return result


def score(task, payload):
    candidates = payload.get('items', [])
    files = set(task['production_files'])
    file_scores = {}
    for count in (1, 3, 5):
        matched = {item['file'] for item in candidates[:count]} & files
        file_scores[str(count)] = dict(any_hit=bool(matched), all_hit=files <= matched,
                                      matched=len(matched), targets=len(files))
    declarations = [(target['file'], declaration) for target in task['fix_targets'] for declaration in target['declarations']]
    matched = sum(any(item['file'] == filename and item['line'] == declaration['line']
                      and item['end_line'] == declaration['end_line'] for item in candidates[:5])
                  for filename, declaration in declarations)
    return dict(files_at=file_scores, callable_at_5=dict(any_hit=bool(matched) if declarations else None,
                  matched=matched, targets=len(declarations)), reading=payload.get('reading'),
                locate_status=payload.get('status'), candidates_returned=len(candidates))


def run(executor, skill, prepared, task):
    root = prepared / task['repository'] / 'cases' / str(task['pr']) / 'base'
    text = task['issue_title'] + '\n\n' + task['issue_body']
    started = time.monotonic()
    command = [sys.executable, '-I', str(skill / 'scripts/cg.py'), '--root', str(root), 'locate', text, '--json']
    run = executor.run_tests(command, root, timeout=600, output_limit=512000)
    result = dict(task=task['id'], seconds=round(time.monotonic() - started, 3), exit_code=run.get('exit'),
                  status=run['status'], cleanup_error=bool(run.get('cleanup_error')))
    try:
        if run['status'] != 'passed' or run.get('cleanup_error'):
            raise ValueError('Locate did not complete')
        payload = json.loads(run['stdout_tail'])
        if payload['format'] != 'codegraph-locate/1':
            raise ValueError('Unexpected locate format')
        result.update(score(task, payload))
    except (ValueError, KeyError):
        result.update(error_code='locate_failed_or_invalid_receipt', **score(task, dict(items=[])))
    return result
