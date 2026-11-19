"""Frozen historical-bug localization benchmark. No models, project code or tests run."""
import argparse
from collections import Counter
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import statistics
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('locate_bench_study', Path(__file__).with_name('study_pilot.py'))
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)
mapping = study.shared.load(Path(__file__).with_name('study_localization.py'), 'locate_bench_mapping')
REPOSITORIES = ('django', 'typescript-eslint', 'prometheus')
DATA = Path(__file__).with_name('locate-bench')
# Published receipt hashes in locate-bench/RESULTS.md, fixed for this bank.
CANDIDATE_RECEIPT_SHA256 = {
    'candidate-4000.json': 'e48f2423b9e40e5abda2eb4e764b6404df237811bec4d65ee67ba5ecd133cdf5',
    'candidate-5000.json': 'd3d6aa520cad1e90794c08acce5da38002371e5ecb53a687aa69bb418aa159d0',
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n')


def select(ledger, excluded, count):
    """Use only the registered metadata criteria, never native test qualifications."""
    if type(count) is not int or count < 1:
        raise ValueError('Positive fixed count required')
    result, dispositions = [], []
    for repository in REPOSITORIES:
        taken, seen = 0, set()
        for row in ledger['repositories'][repository]['metadata']:
            key = f'{repository}-{row["pr"]}'
            if key in seen:
                raise ValueError('Duplicate ledger PR')
            seen.add(key)
            if key in excluded:
                reason = 'original_study_case'
            elif not row.get('issue') or row.get('reason') == 'issue_not_classified_bug':
                reason = 'no_verified_bug_issue'
            elif not row.get('fix') or not row.get('production_files'):
                reason = 'no_production_fix'
            elif taken >= count:
                reason = 'after_frozen_count'
            else:
                reason = 'selected'
                taken += 1
                result.append(dict(row, id=key, repository=repository))
            dispositions.append(dict(id=key, disposition=reason))
        if taken != count:
            raise ValueError('Insufficient candidates under the frozen rule')
    return result, dispositions


def issue(folder, number, repository):
    if repository == 'django':
        path = folder/f'ticket-{number}.csv'
        row, = csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig')))
        title, body, bug = row['summary'], row['description'], row['type'].lower() == 'bug'
        url = 'https://code.djangoproject.com/ticket/'+str(number)
    else:
        path = folder/f'issue-{number}.json'
        row = json.loads(path.read_text())
        labels = [v['name'].lower() for v in row.get('labels', [])]
        bug = ((row.get('type') or {}).get('name', '').lower() == 'bug' or
               any(v in ('bug', 'kind/bug', 'type:bug', 'type: bug', 'type / bug') or v.startswith('bug:') for v in labels))
        title, body, url = row['title'], row.get('body') or '', row['html_url']
    if not bug:
        raise ValueError('Cached issue is not a classified bug')
    return dict(issue_title=title, issue_body=body, issue_url=url, issue_snapshot_sha256=digest(path))


def safe_path(name):
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and '..' not in path.parts and path.parts[0] != '.git'


def parent_copy(cache, destination, task):
    study.pilot.seed(cache, destination, task,
                     exporter=lambda c, commit, target: study.pilot.archive(c, commit, target, allow_symlinks=True))


def truth_dispositions(rule_path):
    path = rule_path.with_name('ground-truth-dispositions.json')
    return (json.loads(path.read_text())['cases'], digest(path)) if path.exists() else ({}, None)


def prepare(rule_path, study_data, metadata_root, skill, output):
    """Build all ground truth before any historical-case locate response is read."""
    if output.exists():
        raise ValueError('Preparation output must be new')
    output.mkdir(parents=True)
    rule = json.loads(rule_path.read_text())
    ledger_path, study_path = study_data/'selection-ledger.json', study_data/'cases.json'
    if (digest(ledger_path) != rule['ledger_sha256']
            or study.recorded_case_input_digest(study_path) != rule['excluded_cases_sha256']):
        raise ValueError('Frozen selection inputs differ')
    excluded = {r['id'] for r in json.loads(study_path.read_text())['cases']}
    rows, dispositions = select(json.loads(ledger_path.read_text()), excluded, rule['per_repository'])
    missing_truth, disposition_hash = truth_dispositions(rule_path)
    engine = study.shared.load(skill/'scripts/cg.py', 'locate_bench_ground_truth')
    result = dict(format='codegraph-locate-cases/1', rule_sha256=digest(rule_path),
                  ground_truth_engine_files=study.engine_files(skill), parser_versions=study.parser_versions(),
                  ground_truth_dispositions_sha256=disposition_hash, cases=[])
    (output/'diffs').mkdir()
    save(output/'selection-ledger.json', dict(format='codegraph-locate-selection/1', dispositions=dispositions))
    for row in rows:
        folder = metadata_root/row['repository']
        cache = folder/'cache'
        details = json.loads((folder/f'pr-{row["pr"]}.json').read_text())
        if details['merge_commit_sha'] != row['fix'] or not details['merged']:
            raise ValueError('Cached PR does not match the frozen ledger')
        parent = study.shared.git(cache, '-c', 'protocol.allow=never', 'rev-parse', row['fix']+'^1').stdout.strip()
        files = sorted(set(row['production_files']) | set(row.get('other_production', [])))
        if any(not safe_path(n) for n in files):
            raise ValueError('Unsafe target path')
        task = dict(id=row['id'], repository=row['repository'], pr=row['pr'], historical_pr_url=details['html_url'],
                    fix=row['fix'], parent=parent, parent_tree=study.shared.git(cache, '-c', 'protocol.allow=never', 'rev-parse', parent+'^{tree}').stdout.strip(),
                    production_files=files, **issue(folder, row['issue'], row['repository']))
        task['issue_body_sha256'] = hashlib.sha256(task['issue_body'].encode()).hexdigest()
        task['fix_targets'] = []
        diff_parts = []
        with tempfile.TemporaryDirectory(prefix='cg-locate-ground-truth-') as temporary:
            root = Path(temporary)/'parent'
            parent_copy(cache, root, task)
            for name in files:
                diff = study.shared.git(cache, '-c', 'protocol.allow=never', 'diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--unified=0',
                                        parent, row['fix'], '--', name).stdout
                if not diff.strip():
                    if missing_truth.get(task['id'], {}).get('reason') == 'no_production_first_parent_diff':
                        continue
                    raise ValueError('Ledger production file has no historical diff')
                diff_parts.append(diff)
                language = engine.LANG_BY_EXT.get(Path(name).suffix)
                if (root/name).is_symlink() or language not in ('python', 'typescript', 'tsx', 'go'):
                    target = dict(file=name, status='unsupported_or_symlink_parent', declarations=[],
                                  old_hunks=[dict(start=a, count=b) for a, b in mapping.old_ranges(diff)])
                else:
                    target = mapping.targets(engine, root, language, name, diff)
                task['fix_targets'].append(target)
        if task['id'] in missing_truth:
            if diff_parts:
                raise ValueError('Unavailable-truth disposition differs from the historical diff')
            task.update(listed_production_files=files, production_files=[], ground_truth_status='unavailable',
                        ground_truth_reason=missing_truth[task['id']]['reason'])
        patch = output/'diffs'/(task['id']+'.patch')
        patch.write_text(''.join(diff_parts))
        task.update(production_diff='diffs/'+patch.name, production_diff_sha256=digest(patch))
        result['cases'].append(task)
        save(output/'cases.json', result)
    return result


def load_cases(path, rule_path):
    data = json.loads(path.read_text())
    if data['format'] != 'codegraph-locate-cases/1' or digest(rule_path) != data['rule_sha256']:
        raise ValueError('Case format or rule hash mismatch')
    ids = [r['id'] for r in data['cases']]
    count = json.loads(rule_path.read_text())['per_repository']
    if type(count) is not int or count < 1 or len(ids) != len(set(ids)) or len(ids) != count*len(REPOSITORIES):
        raise ValueError('Incomplete or duplicate frozen cohort')
    if any(sum(t['repository'] == name for t in data['cases']) != count for name in REPOSITORIES):
        raise ValueError('Frozen repository counts differ')
    missing_truth, disposition_hash = truth_dispositions(rule_path)
    if data.get('ground_truth_dispositions_sha256') != disposition_hash:
        raise ValueError('Ground-truth dispositions changed')
    for task in data['cases']:
        unavailable = (task['id'] in missing_truth and task.get('ground_truth_status') == 'unavailable'
                       and task.get('ground_truth_reason') == missing_truth[task['id']]['reason']
                       and not task['fix_targets'])
        if ((not task['production_files'] and not unavailable) or
                (task.get('ground_truth_status') == 'unavailable' and task['production_files']) or
                any(not safe_path(n) for n in task['production_files'])):
            raise ValueError('Unsafe or empty targets')
        patch = path.parent/task['production_diff']
        if (not safe_path(task['production_diff']) or not patch.resolve().is_relative_to(path.parent.resolve())
                or digest(patch) != task['production_diff_sha256']):
            raise ValueError('Production diff hash mismatch')
        if hashlib.sha256(task['issue_body'].encode()).hexdigest() != task['issue_body_sha256']:
            raise ValueError('Issue body hash mismatch')
    return data


def score(task, payload):
    result = mapping.score(task, payload)
    if not task['production_files']:
        for slot in result['files_at'].values():
            slot.update(any_hit=None, all_hit=None)
    return result


def summarize(tasks, rows):
    by_id = {r['task']: r for r in rows}
    if len(by_id) != len(rows) or not by_id.keys() <= {t['id'] for t in tasks}:
        raise ValueError('Duplicate or unassigned measurement')
    result = dict(assigned=len(tasks), measured=len(rows), failed=sum('error_code' in r for r in rows),
                  missing=len(tasks)-len(rows), files_at={}, callable_at_5={})
    known_files = {t['id'] for t in tasks if t['production_files']}
    result['file_ground_truth_missing'] = len(tasks)-len(known_files)
    for count in ('1', '3', '5'):
        result['files_at'][count] = dict(cases=len(known_files), **{
            key: sum(r['files_at'][count][key+'_hit'] is True for r in rows if r['task'] in known_files)
            for key in ('any', 'all')})
    result['callable_at_5'] = dict(mapped_cases=sum(bool(t['fix_targets']) and any(x['declarations'] for x in t['fix_targets']) for t in tasks),
        any=sum(r['callable_at_5']['any_hit'] is True for r in rows), matched=sum(r['callable_at_5']['matched'] for r in rows),
        targets=sum(len(x['declarations']) for t in tasks for x in t['fix_targets']))
    result['reading'] = {}
    for key in ('returned_chars', 'file_chars'):
        values = [r['reading'][key] for r in rows if isinstance(r.get('reading'), dict) and type(r['reading'].get(key)) is int]
        result['reading'][key] = dict(known=len(values), missing=len(tasks)-len(values), median=statistics.median(values) if values else None)
    result['reading']['upper_bound_answers'] = sum(isinstance(r.get('reading'), dict) and r['reading'].get('exact') is False for r in rows)
    return result


def compare(cases_path, rule_path, output):
    """Recount frozen receipts without the measured engine or comparison.json."""
    if output.exists() or output.is_symlink():
        raise ValueError('Comparison output must be new')
    data = load_cases(cases_path, rule_path)
    folder, tasks = cases_path.parent, data['cases']
    baseline = json.loads((folder/'baseline-engine.json').read_text())
    candidate = json.loads((folder/'candidate-engine.json').read_text())
    if (baseline['format'] != 'codegraph-locate-baseline-engine/1'
            or candidate['format'] != 'codegraph-locate-candidate-engine/1' or candidate['models'] != 0):
        raise ValueError('Engine pin format or model count mismatch')
    hashes = dict(cases_sha256=study.recorded_case_input_digest(cases_path), rule_sha256=digest(rule_path),
                  ground_truth_dispositions_sha256=data['ground_truth_dispositions_sha256'])
    if any(candidate.get(key) != value for key, value in hashes.items()):
        raise ValueError('Candidate pin input hashes mismatch')
    runs = (('baseline','baseline.json',None,baseline),
            ('candidate4000','candidate-4000.json',4000,candidate),
            ('candidate5000','candidate-5000.json',5000,candidate))
    if [entry['requested_max_chars'] for entry in candidate['evaluations']] != [4000,5000]:
        raise ValueError('Candidate pin budgets mismatch')
    receipts, audits = {}, {}
    for name, filename, budget, pin in runs:
        path = folder/filename
        if filename in CANDIDATE_RECEIPT_SHA256 and digest(path) != CANDIDATE_RECEIPT_SHA256[filename]:
            raise ValueError('Candidate receipt hash mismatch: '+name)
        receipt = json.loads(path.read_text())
        if (receipt['format'] != 'codegraph-locate-benchmark/1' or not receipt['complete']
                or receipt['stop_reason'] is not None or receipt['not_started']
                or receipt['live_starts'] != 0):
            raise ValueError('Incomplete or non-static receipt: '+name)
        if (any(receipt.get(key) != value for key, value in hashes.items())
                or receipt['tool_sha256'] != candidate['tool_sha256']
                or receipt['engine_files'] != pin['engine_files']
                or receipt['parser_versions'] != pin['parser_versions']):
            raise ValueError('Receipt input hashes or provenance mismatch: '+name)
        if (receipt['requested_max_chars'] != budget or receipt['defaults'] != (budget is None)
                or [row['task'] for row in receipt['cases']] != [task['id'] for task in tasks]):
            raise ValueError('Receipt budget or case order mismatch: '+name)
        if name == 'baseline' and digest(path) != candidate['baseline_sha256']:
            raise ValueError('Baseline receipt hash mismatch')
        for task, row in zip(tasks, receipt['cases']):
            expected = score(task, dict(items=row['candidates'], reading=row.get('reading'),
                                       status=row.get('locate_status')))
            if row.get('cleanup_error') or any(row.get(key) != value for key, value in expected.items()):
                raise ValueError('Receipt cleanup or saved score mismatch: '+name)
        summary = summarize(tasks, receipt['cases'])
        if receipt['summary'] != summary:
            raise ValueError('Receipt summary mismatch: '+name)
        receipts[name] = receipt
        audits[name] = dict(receipt_sha256=digest(path), scores_recomputed=len(tasks), summary=summary,
            query_statuses=dict(Counter(row.get('status','not_returned') for row in receipt['cases'])),
            errors=[{key: value for key, value in row.items()
                     if key in ('task','error_code','status','exit_code','cleanup_error')}
                    for row in receipt['cases'] if 'error_code' in row])

    def paired(before, after, endpoint, rank=None):
        pairs = []
        for left, right in zip(receipts[before]['cases'], receipts[after]['cases']):
            a = left[endpoint][rank]['any_hit'] if rank else left[endpoint]['any_hit']
            z = right[endpoint][rank]['any_hit'] if rank else right[endpoint]['any_hit']
            if a is not None and z is not None:
                pairs.append((a,z))
        return dict(evaluable=len(pairs), improved=sum(not a and z for a,z in pairs),
                    regressed=sum(a and not z for a,z in pairs), both_hit=sum(a and z for a,z in pairs),
                    neither_hit=sum(not a and not z for a,z in pairs))

    comparisons = {}
    for before, after in (('baseline','candidate4000'),('baseline','candidate5000'),
                          ('candidate4000','candidate5000')):
        comparisons[before+'_to_'+after] = dict(
            files_any_at={rank: paired(before,after,'files_at',rank) for rank in ('1','3','5')},
            callable_any_at_5=paired(before,after,'callable_at_5'))
    by_repository = {}
    for repository in REPOSITORIES:
        selected = [task for task in tasks if task['repository'] == repository]
        by_repository[repository] = {name: summarize(selected,
            [row for task, row in zip(tasks, receipt['cases']) if task['repository'] == repository])
            for name, receipt in receipts.items()}
    summary = audits['baseline']['summary']
    # The frozen protocol (locate-bench/README.md) specifies the old default as
    # 4000. The historical receipt records defaults=True, not a numeric override.
    result = dict(format='codegraph-locate-comparison/1', models=candidate['models'],
        assigned=len(tasks), file_ground_truth_known=len(tasks)-summary['file_ground_truth_missing'],
        file_ground_truth_missing=summary['file_ground_truth_missing'],
        mapped_callable_cases=summary['callable_at_5']['mapped_cases'],
        declaration_targets=summary['callable_at_5']['targets'], **hashes,
        tool_sha256=candidate['tool_sha256'], baseline_commit=baseline['main_commit'],
        candidate_commit=candidate['candidate_commit'], candidate_snapshot=candidate['candidate_snapshot'],
        baseline_budget=4000,
        candidate_budgets=[receipts[name]['requested_max_chars'] for name in receipts if name != 'baseline'],
        audits=audits, paired_comparisons=comparisons, by_repository=by_repository)
    save(output, result)
    return result


def measure_case(executor, skill, root, task, max_chars=None):
    text = task['issue_title']+'\n\n'+task['issue_body']
    command = [sys.executable, '-I', str(skill/'scripts/cg.py'), '--root', str(root), 'locate', text, '--json']
    if max_chars is not None:
        command += ['--max-chars', str(max_chars)]
    try:
        run = executor.run_tests(command, root, timeout=600, output_limit=512000)
    except Exception:
        # Without the executor's receipt no child cleanup can be assumed.
        return dict(task=task['id'], error_code='missing_executor_receipt', cleanup_error=True, candidates=[],
                    **score(task, dict(items=[])))
    result = dict(task=task['id'], status=run['status'], exit_code=run.get('exit'), cleanup_error=bool(run.get('cleanup_error')))
    try:
        if run['status'] != 'passed' or run.get('cleanup_error'):
            raise ValueError('Locate did not complete')
        payload = json.loads(run['stdout_tail'])
        if (not str(payload['format']).startswith('codegraph-locate/') or not isinstance(payload['items'], list)
                or any(not isinstance(r.get('file'), str) or not safe_path(r['file'])
                       or any(type(r.get(k)) is not int or r[k] < 1 for k in ('line','end_line'))
                       or r['end_line'] < r['line'] for r in payload['items'])):
            raise ValueError('Invalid locate shape')
        scored = score(task, payload)
        candidates = [{k: r[k] for k in ('file','line','end_line')} for r in payload['items']]
        result.update(scored, candidates=candidates)
        study.validate_public(result, [dict(tree_paths=task['production_files']+[r['file'] for r in candidates])])
    except (ValueError, KeyError, TypeError, AttributeError):
        result.update(error_code='locate_failed_or_invalid_receipt', candidates=[], **score(task, dict(items=[])))
    return result


def measure(cases_path, rule_path, cache_root, skill, output, max_chars=None):
    if output.exists():
        raise ValueError('Measurement output must be new; do not replace observed results')
    if max_chars is not None and (type(max_chars) is not int or max_chars <= 0):
        raise ValueError('Positive character budget required')
    data = load_cases(cases_path, rule_path)
    engine_files = study.engine_files(skill)
    parser_versions = study.parser_versions()
    executor = study.shared.load(skill/'scripts/change_tests.py', 'locate_bench_executor')
    result = dict(format='codegraph-locate-benchmark/1', live_starts=0, cases_sha256=digest(cases_path),
                  rule_sha256=digest(rule_path), tool_sha256=digest(Path(__file__)),
                  ground_truth_dispositions_sha256=data.get('ground_truth_dispositions_sha256'),
                  engine_files=engine_files, parser_versions=parser_versions, defaults=max_chars is None,
                  requested_max_chars=max_chars, cases=[], stop_reason=None)
    def write():
        result['summary'] = summarize(data['cases'], result['cases'])
        observed = {r['task'] for r in result['cases']}
        result['not_started'] = [t['id'] for t in data['cases'] if t['id'] not in observed]
        paths = [r['file'] for row in result['cases'] for r in row.get('candidates', [])]
        study.validate_public(result, [dict(tree_paths=t['production_files']+paths) for t in data['cases']])
        save(output, result)

    write()
    for task in data['cases']:
        if study.engine_files(skill) != engine_files or study.parser_versions() != parser_versions:
            result['stop_reason'] = 'engine_drift'
            break
        with tempfile.TemporaryDirectory(prefix='cg-locate-benchmark-') as temporary:
            root = Path(temporary)/'parent'
            try:
                parent_copy(cache_root/task['repository']/'cache', root, task)
                row = measure_case(executor, skill, root, task, max_chars=max_chars)
            except (OSError, ValueError, study.pilot.subprocess.SubprocessError):
                row = dict(task=task['id'], error_code='parent_copy_or_measurement_failed', candidates=[], **score(task, dict(items=[])))
            result['cases'].append(row)
        write()
        if row.get('cleanup_error'):
            result['stop_reason'] = 'cleanup_uncertain'
            break
    if study.engine_files(skill) != engine_files or study.parser_versions() != parser_versions:
        result['stop_reason'] = 'engine_drift'
    write()
    result['complete'] = not result['summary']['missing'] and result['stop_reason'] is None
    save(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare','measure','compare'))
    parser.add_argument('--skill-root', type=Path)
    parser.add_argument('--cache-root', type=Path, help='Frozen metadata/cache root, with <repository>/cache Git repositories')
    parser.add_argument('--rule', type=Path, default=DATA/'selection-rule.json')
    parser.add_argument('--study-data', type=Path, default=REPO/'benchmarks/study-cases')
    parser.add_argument('--cases', type=Path, default=DATA/'cases.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-chars', type=int, help='measure only: explicit answer budget; omit for engine defaults')
    args = parser.parse_args()
    if args.action == 'compare':
        if args.max_chars is not None:
            parser.error('--max-chars is only valid for measure')
        compare(args.cases, args.rule, args.output)
        return
    if args.skill_root is None or args.cache_root is None:
        parser.error('--skill-root and --cache-root are required for prepare and measure')
    if args.action == 'prepare':
        if args.max_chars is not None:
            parser.error('--max-chars is only valid for measure')
        prepare(args.rule, args.study_data, args.cache_root, args.skill_root, args.output)
    else:
        measure(args.cases, args.rule, args.cache_root, args.skill_root, args.output, args.max_chars)


if __name__ == '__main__':
    main()
