"""Rescore saved SymPy candidates offline; never start or retry an agent."""
import argparse
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('offline_real_pilot', REPO / 'benchmarks/real_pilot.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)
SCORES = ('outcome', 'retention', 'final_state', 'candidate_public', 'candidate_hidden',
          'candidate_scope_ok', 'verification_ok', 'review_verdict', 'integrated_hidden')
REASON = ('The recorded check compared whole compound statements and rejected added nested assertions. '
          'The corrected check preserves the original statement multiset and headers at every depth; additions are allowed.')


def git(root, *args):
    return subprocess.check_output(
        ['git', '--no-optional-locks', '-c', 'core.hooksPath=/dev/null', '-c', 'core.excludesFile=/dev/null',
         '-c', 'commit.gpgsign=false', '-C', str(root), *args], text=True, stderr=subprocess.PIPE,
        env=dict(os.environ, GIT_OPTIONAL_LOCKS='0')).strip()


def public_json(value):
    """Refuse private material rather than silently redacting scientific inputs."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('session_id', 'thread_id', 'cwd', 'argv', 'prompt', 'stdout', 'stderr', 'raw'):
                raise ValueError('Private field in projected receipt')
            public_json(key)
            public_json(item)
    elif isinstance(value, list):
        for item in value:
            public_json(item)
    elif isinstance(value, str):
        if (value.startswith('/') or any(part in value for part in ('/Users/', '/private/', '/home/', '/tmp/', '/var/', 'file://'))
                or re.search(r'(?:^|\s)[A-Za-z]:[\\/]', value)
                or re.search(r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b', value)):
            raise ValueError('Private path or session in projected receipt')
    return json.dumps(value, indent=2, allow_nan=False) + '\n'


def same_test_counts(before, after):
    return all(before.get(key) == after.get(key)
               for key in ('exit_code', 'executed', 'passed', 'failed', 'errors', 'skipped'))


def eligible(score):
    return (score['verification_ok'] is True and score['review_verdict'] == 'accept'
            and score['candidate_scope_ok'] is True and pilot.green(score['candidate_public'])
            and pilot.green(score['candidate_hidden']) and score['retention']['tests_preserved']
            and score['retention']['top_level_api']['passed'])


def integrate_copy(host, record, destination, candidate_tree):
    """Same guarded no-ff merge, in a separate local clone of the disposable host."""
    if record['state'] != 'reviewed' or record['review']['verdict'] != 'accept' or record['verify']['blocking']:
        raise ValueError('Recorded review or verification does not permit integration')
    if git(host, 'rev-parse', 'HEAD') != record['base']['commit']:
        raise ValueError('Original unintegrated host moved from its base')
    git(host, 'clone', '--no-hardlinks', '--no-checkout', str(host), str(destination))
    git(destination, 'config', 'user.name', 'Code Graph offline rescore')
    git(destination, 'config', 'user.email', 'offline@code-graph.invalid')
    git(destination, 'checkout', '-B', record['base']['branch'], record['base']['commit'])
    if git(destination, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Integration copy is dirty')
    git(destination, 'merge-base', '--is-ancestor', record['base']['commit'], record['edit']['commit'])
    git(destination, 'merge', '--no-ff', '--no-edit', record['edit']['commit'])
    tree = git(destination, 'rev-parse', 'HEAD^{tree}')
    if tree != candidate_tree or git(destination, 'status', '--porcelain', '--untracked-files=all'):
        raise ValueError('Integrated tree differs from the scored candidate')
    return dict(mode='merge', performed_offline=True, candidate_tree=candidate_tree, integrated_tree=tree)


def rescore(receipt, work_dir, test_python):
    original_bytes = receipt.read_bytes()
    recorded = json.loads(original_bytes)
    public_json(recorded)
    tasks = {task['id']: task for task in pilot.cases()}
    expected = {(name, arm) for name in tasks for arm in ('source_only', 'graph_assisted')}
    if (len(recorded['cases']) != len(expected)
            or {(r['task'], r['arm']) for r in recorded['cases']} != expected
            or recorded['cases_sha256'] != pilot.digest(pilot.DATA / 'cases.json')
            or recorded['selection_sha256'] != pilot.digest(pilot.DATA / 'selection-rule.json')):
        raise ValueError('Receipt differs from the frozen task selection')
    if pilot.test_environment(test_python) != recorded['test_environment']:
        raise ValueError('Test environment differs from the recorded run')
    work_dir.mkdir(parents=True)
    result = dict(format='codegraph-real-pilot-rescore/1',
                  recorded_run={key: deepcopy(value) for key, value in recorded.items() if key != 'cases'}, cases=[],
                  rescore=dict(reason=REASON, additional_live_starts=0, recorded_receipt_sha256=pilot.digest(receipt),
                               scorer_files={p.name: pilot.digest(p) for p in (Path(__file__), REPO / 'benchmarks/real_pilot.py')},
                               timing_scope='Calls, stages and workflow seconds are as recorded; rescored test/integration timings are offline.'))
    for row in recorded['cases']:
        started = time.monotonic()
        name = row['task'] + '-' + row['arm']
        host = receipt.parent / 'work' / name
        records = list((host / '.code-graph/changes').glob('*/change.json'))
        if len(records) != 1:
            raise ValueError('Expected one saved change per task/arm')
        saved = records[0].read_bytes()
        record = json.loads(saved)
        task = tasks[row['task']]
        if (record['state'] != row['final_state'] or record['state'] not in ('reviewed', 'integrated')
                or record['verify']['ok'] != row['verification_ok'] or record['review']['verdict'] != row['review_verdict']):
            raise ValueError('Saved workflow disagrees with the receipt')
        initial_head = git(host, 'rev-parse', 'HEAD')
        if git(host, 'status', '--porcelain', '--untracked-files=all'):
            raise ValueError('Saved host is dirty')
        base, edit = record['base']['commit'], record['edit']['commit']
        if git(host, 'rev-parse', base + '^{tree}') != task['parent_tree']:
            raise ValueError('Baseline tree differs from the historical parent')
        if record['state'] == 'reviewed':
            candidate = Path(record['worktree'])
            if git(candidate, 'rev-parse', 'HEAD') != edit or git(candidate, 'status', '--porcelain', '--untracked-files=all'):
                raise ValueError('Saved candidate moved or is dirty')
        folder = work_dir / name
        baseline, candidate = folder / 'baseline', folder / 'candidate'
        pilot.archive(host, base, baseline)
        pilot.archive(host, edit, candidate)
        changed = git(host, 'diff', '--name-only', '--no-ext-diff', '--no-textconv', base, edit, '--').splitlines()
        scope_ok = set(changed) <= set(task['source_paths'])
        if changed != sorted(row['changed_files']) or scope_ok != row['candidate_scope_ok']:
            raise ValueError('Candidate files differ from the recorded perimeter')
        score = {key: deepcopy(row[key]) for key in SCORES if key in row}
        score['retention'] = pilot.retained(candidate, baseline, changed)
        for key, hidden in (('candidate_public', False), ('candidate_hidden', True)):
            score[key] = pilot.frozen_tests(candidate, baseline, task, test_python, hidden=hidden)
            if not same_test_counts(row[key], score[key]):
                raise ValueError('Frozen test counts differ from the recorded observation')
        integration = None
        if eligible(score):
            if row['outcome'] == 'success':
                if git(host, 'rev-parse', 'HEAD^{tree}') != git(host, 'rev-parse', edit + '^{tree}'):
                    raise ValueError('Original integrated tree differs from the candidate')
                integrated = folder / 'integrated'
                pilot.archive(host, initial_head, integrated)
                integration = dict(mode='merge', performed_offline=False, recorded_integration_rechecked=True)
            else:
                integrated = folder / 'integrated'
                integration = integrate_copy(host, record, integrated, git(host, 'rev-parse', edit + '^{tree}'))
            score['integrated_hidden'] = pilot.frozen_tests(integrated, baseline, task, test_python, hidden=True)
            score['outcome'] = 'success' if pilot.green(score['integrated_hidden']) else 'integration_failed'
            score['final_state'] = 'integrated'
        else:
            score['outcome'] = 'verification_review_or_hidden_failed'
        score['integration'] = integration
        score['offline_seconds'] = round(time.monotonic() - started, 3)
        result['cases'].append(dict({key: deepcopy(value) for key, value in row.items() if key not in SCORES},
                                    as_recorded={key: deepcopy(row[key]) for key in SCORES if key in row}, rescored=score))
        if (records[0].read_bytes() != saved or git(host, 'rev-parse', 'HEAD') != initial_head
                or git(host, 'status', '--porcelain', '--untracked-files=all')):
            raise ValueError('Original evidence changed during offline rescoring')
    if receipt.read_bytes() != original_bytes:
        raise ValueError('Original receipt changed during offline rescoring')
    public_json(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True, help='new directory for isolated scoring/integration copies')
    parser.add_argument('--test-python', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = rescore(args.receipt.resolve(), args.work_dir.resolve(), args.test_python.absolute())
    args.output.write_text(public_json(result))


if __name__ == '__main__':
    main()
