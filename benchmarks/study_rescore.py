"""Offline sensitivity analysis for decision studio-ricalcolo-retention; no agents."""
import argparse
import ast
from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import time

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('study_rescore_pilot', Path(__file__).with_name('study_pilot.py'))
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)
saved = study.shared.load(Path(__file__).with_name('real_rescore.py'), 'study_rescore_saved')
RULE = 'studio-ricalcolo-retention'
RULE_EVENT_SHA256 = '7171d4bd1d8f8b8f69364bb1f9252364cef399aac794ba5ac9d88dbde38faa12'
COLLECTION = '<literal collection>'


def py_value(value):
    if isinstance(value, ast.List):
        return COLLECTION, 'list', py_value(value.ctx), tuple(map(py_value, value.elts))
    if isinstance(value, ast.Dict):
        return COLLECTION, 'dict', (), tuple((py_value(k), py_value(v)) for k, v in zip(value.keys, value.values))
    if isinstance(value, ast.AST):
        return type(value).__name__, tuple((k, py_value(v)) for k, v in ast.iter_fields(value))
    if isinstance(value, list):
        return tuple(map(py_value, value))
    return type(value).__name__, repr(value)


def headers(source, language, filename=''):
    """Same original statement/header units; only literal elements get a sequence tag."""
    if language == 'python':
        tree = ast.parse(source)
        result = Counter()
        bodies = {'body', 'orelse', 'finalbody', 'handlers', 'cases'}
        for node in ast.walk(tree):
            if isinstance(node, (ast.stmt, ast.ExceptHandler, ast.match_case)):
                header = type(node).__name__, tuple((k, py_value([] if k in bodies and isinstance(v, list) else v))
                                                    for k, v in ast.iter_fields(node))
                result[header] += 1
                for clause in ('orelse', 'finalbody'):
                    if getattr(node, clause, None):
                        result[(clause, header)] += 1
        return result
    if language not in ('typescript', 'go'):
        raise ValueError('Unsupported language')
    from tree_sitter_language_pack import get_parser
    data = source.encode('utf-8')
    tree = get_parser('tsx' if filename.endswith('.tsx') else language).parse(data)
    if tree.root_node.has_error:
        raise ValueError('Partial test syntax')
    literals = {'array', 'object'} if language == 'typescript' else {'literal_value'}
    relevant, containers = study.integrity.relevant, study.integrity.CONTAINERS

    def header(node):
        if node.type in literals:
            return COLLECTION, node.type, (), tuple(header(c) for c in node.named_children if not relevant(c))
        if not node.children:
            return node.type, data[node.start_byte:node.end_byte]
        return node.type, tuple((c.type,) if c.type in containers else header(c) for c in node.children
                               if not (c.is_named and relevant(c)))

    result, stack = Counter(), [tree.root_node]
    while stack:
        node = stack.pop()
        if relevant(node):
            result[header(node)] += 1
        stack.extend(node.named_children)
    return result


def extends(before, after):
    """Literal elements may be inserted; old elements keep order and multiplicity."""
    if before == after:
        return True
    if not isinstance(before, tuple) or not isinstance(after, tuple):
        return False
    if before and after and before[0] == after[0] == COLLECTION:
        if before[1:3] != after[1:3]:
            return False
        cursor = iter(after[3])
        return all(any(extends(old, new) for new in cursor) for old in before[3])
    return len(before) == len(after) and all(extends(old, new) for old, new in zip(before, after))


def preserved(before, after):
    # Exact matches consume their multiplicity first. Augmenting paths avoid
    # greedily assigning a superset to an old header that has another match.
    old, new = list((before-after).elements()), list((after-before).elements())
    edges = [[j for j, candidate in enumerate(new) if header[0] == candidate[0] and extends(header, candidate)]
             for header in old]
    assigned = {}

    def take(i, seen):
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in assigned or take(assigned[j], seen):
                assigned[j] = i
                return True
        return False

    return all(take(i, set()) for i in range(len(old)))


def retained(candidate, baseline, changed, language):
    missing, undecided = [], []
    for name in changed:
        if not study.is_test(name) or not (baseline/name).is_file():
            continue
        try:
            before, after = (baseline/name).read_text(), (candidate/name).read_text()
            if before != after and not preserved(headers(before, language, name), headers(after, language, name)):
                missing.append(name)
        except (OSError, UnicodeError, SyntaxError, ValueError):
            undecided.append(name)
    return dict(tests_preserved=False if missing else None if undecided else True,
                weakened_test_ids=missing, undecided_test_files=undecided, rule=RULE,
                guard='original_headers_with_literal_insertions', semantic_equivalence_proven=False)


def eligible(row):
    api = row.get('retention', {}).get('top_level_api')
    return (row.get('verification_ok') is True and row.get('review_verdict') == 'accept'
            and row.get('candidate_scope_ok') is True and study.pilot.green(row.get('candidate_public', {}))
            and study.pilot.green(row.get('candidate_hidden', {}))
            and row['retention']['tests_preserved'] is True
            and (api.get('passed') is True if isinstance(api, dict) else row.get('language') != 'python'))


def rescore(receipt, cases, live_dir, work_dir, environment, skill):
    original = receipt.read_bytes()
    recorded = json.loads(original)
    _, tasks = study.load_cases(cases)
    study.validate_public(recorded, tasks)
    if (recorded['cases_sha256'] != study.recorded_case_input_digest(cases)
            or recorded['environment_sha256'] != study.pilot.digest(environment)):
        raise ValueError('Frozen cases/environment differ from the live run')
    if study.engine_files(skill) != recorded['engine_files'] or study.parser_versions() != recorded['parser_versions']:
        raise ValueError('Engine differs from the recorded freeze')
    expected = {(t['id'], arm) for t in tasks for arm in ('source_only', 'graph_assisted')}
    if len(recorded['cases']) != len(expected) or {(r['task'], r['arm']) for r in recorded['cases']} != expected:
        raise ValueError('Incomplete path inventory')
    if work_dir.exists():
        raise ValueError('Offline work directory must be new')
    work_dir.mkdir(parents=True)
    study.EXECUTOR = study.shared.load(skill/'scripts/change_tests.py', 'study_rescore_executor')
    by_id = {t['id']: t for t in tasks}
    result = dict(format='codegraph-study-rescore/1', rule=RULE, rule_event_sha256=RULE_EVENT_SHA256,
                  recorded_receipt_sha256=study.pilot.digest(receipt), additional_model_starts=0,
                  scorer_files={p.name: study.pilot.digest(p) for p in (Path(__file__), Path(study.__file__), Path(saved.__file__))},
                  timing_scope='Original calls/stages/workflow times remain observed live times; offline times are separate.', cases=[])
    scored_rows = []
    for row in recorded['cases']:
        start = time.monotonic()
        task = by_id[row['task']]
        score = deepcopy(row)
        name = row['task']+'-'+row['arm']
        host = live_dir/name
        records = list((host/'.code-graph/changes').glob('*/change.json'))
        if len(records) != 1:
            raise ValueError('Expected one saved workflow per path')
        record_bytes = records[0].read_bytes()
        record = json.loads(record_bytes)
        head = saved.git(host, 'rev-parse', 'HEAD')
        if saved.git(host, 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('Original host is dirty')
        base, edit = record['base']['commit'], record.get('edit', {}).get('commit')
        if saved.git(host, 'rev-parse', base+'^{tree}') != task['parent_tree']:
            raise ValueError('Historical parent tree mismatch')
        integration = None
        original_candidate = None
        if edit is not None:
            if record['state'] != row['final_state'] or record['verify']['ok'] != row['verification_ok'] or record['review']['verdict'] != row['review_verdict']:
                raise ValueError('Saved state differs from the recorded run')
            if record['state'] != 'integrated':
                original_candidate = Path(record['worktree'])
                if (saved.git(original_candidate, 'rev-parse', 'HEAD') != edit
                        or saved.git(original_candidate, 'status', '--porcelain', '--untracked-files=all')):
                    raise ValueError('Original candidate moved or is dirty')
            folder = work_dir/name
            baseline, candidate = folder/'baseline', folder/'candidate'
            study.pilot.archive(host, base, baseline, allow_symlinks=True)
            study.pilot.archive(host, edit, candidate, allow_symlinks=True)
            changed = saved.git(host, 'diff', '--name-only', '--no-ext-diff', '--no-textconv', base, edit, '--').splitlines()
            if changed != sorted(row['changed_files']) or (set(changed) <= set(task['source_paths'])) != row['candidate_scope_ok']:
                raise ValueError('Saved candidate perimeter mismatch')
            guard = retained(candidate, baseline, changed, task['language'])
            score['retention'] = dict(row['retention'], **guard)
            score['language'] = task['language']
            if guard['tests_preserved'] is None:
                score['outcome'] = 'undecided'
            elif eligible(score):
                if row['outcome'] == 'success':
                    if saved.git(host, 'rev-parse', 'HEAD^{tree}') != saved.git(host, 'rev-parse', edit+'^{tree}'):
                        raise ValueError('Original integrated tree moved')
                    integration = dict(performed_offline=False, original_integration_reused=True)
                else:
                    integrated = folder/'integrated'
                    integration = saved.integrate_copy(host, record, integrated, saved.git(host, 'rev-parse', edit+'^{tree}'))
                    score['integrated_hidden'] = study.frozen_tests(integrated, baseline, task, environment, hidden=True)
                score['outcome'] = 'success' if study.pilot.green(score['integrated_hidden']) else 'integration_failed'
            else:
                score['outcome'] = 'verification_review_or_hidden_failed'
        if records[0].read_bytes() != record_bytes or saved.git(host, 'rev-parse', 'HEAD') != head:
            raise ValueError('Original workflow evidence changed')
        if original_candidate is not None and (saved.git(original_candidate, 'rev-parse', 'HEAD') != edit
                or saved.git(original_candidate, 'status', '--porcelain', '--untracked-files=all')):
            raise ValueError('Original candidate evidence changed')
        result['cases'].append(dict(task=row['task'], arm=row['arm'], as_recorded=deepcopy(row), corrected=score,
                                    integration=integration, offline_seconds=round(time.monotonic()-start, 3)))
        scored_rows.append(score)
    if receipt.read_bytes() != original:
        raise ValueError('Original receipt changed')
    analysis = study.shared.load(Path(__file__).with_name('study_analysis.py'), 'study_rescore_analysis')
    decided = [row for row in scored_rows if row['outcome'] != 'undecided']
    result['analysis'] = analysis.analyze(tasks, decided, stopped=bool(recorded.get('stop_reason')) or len(decided) != len(scored_rows))
    result['analysis']['secondary'] = deepcopy(recorded['analysis']['secondary'])
    study.validate_public(result, tasks)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('receipt', 'cases', 'live-dir', 'work-dir', 'environment', 'skill-root', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    result = rescore(args.receipt, args.cases, args.live_dir, args.work_dir, args.environment, args.skill_root)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
