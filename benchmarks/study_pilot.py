"""Paired multi-language study; default mode qualifies historical cases, starting no models."""
import argparse
import gzip
import hashlib
import importlib.util
import importlib.metadata
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('study_integrity', Path(__file__).with_name('study_integrity.py'))
integrity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integrity)
pilot = integrity.pilot
shared = pilot.shared
NATIVE = Path(__file__).with_name('study_native.py')
PROFILE = '(version 1)(allow default)(deny network-outbound (remote ip "*:*"))'
TIMEOUT = 600
MAX_STARTS = 144
MAX_REVIEWS = 48
REVIEW_USD = 0.60
TOTAL_USD = 30
EXECUTOR = None

# Exact publication copies of the locate and study case manifests. The keys
# identify the redacted files, and the values identify the inputs measured.
RECORDED_CASE_INPUTS = {
    '73e61740a1949b0c01c31ee6bf0d2159f68ab3e8390c1e0cc5958760a39b9da0':
        '882570a3870e211eae012aaa4a588529c1c8b0b7feaeeeb83974536252486be0',
    'feeda9165d717ee5107059810f6a4db8cc843005e80d707bc9ded84b3b110c9f':
        '2683551d2a818f6d0765858ca3f74c293c1bc23648bbbbbb2966f91da48e2b99',
}


def recorded_case_input_digest(path):
    digest = pilot.digest(path)
    return RECORDED_CASE_INPUTS.get(digest, digest)


class CallBudget(list):
    def __init__(self, pairs):
        super().__init__([0, 0])
        self.limit = min(MAX_STARTS, 6 * pairs)


class StudyRunner(pilot.RealRunner):
    def __init__(self, agents, flow, skill, task, arm, counter, **kwargs):
        super().__init__(agents, flow, skill, dict(task, inventory_at_plan_only=True), arm, counter, **kwargs)

    def start(self, client, **kwargs):
        with self._lock:
            if self.closed or self.counter[0] >= self.counter.limit:
                raise RuntimeError('Study start gate closed')
            if client == 'claude' and self.counter[1] >= MAX_REVIEWS:
                raise RuntimeError('Study review cap reached')
            self.counter[0] += 1
            self.counter[1] += client == 'claude'
            return self._start(client, kwargs)


def engine_files(skill):
    return {str(p.relative_to(skill)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder, pattern in (('scripts', '*.py'), ('scripts/catalog', '*.json'))
            for p in sorted((skill / folder).glob(pattern))}


def parser_versions():
    return {name: importlib.metadata.version(name) for name in ('tree-sitter', 'tree-sitter-language-pack')}


def validate_public(value, tasks):
    # A tracked relative path may legitimately contain "/home/"; accept only exact
    # names in the frozen inventories, keeping the existing private-field checks.
    allowed = {name for task in tasks for name in task['tree_paths']}
    def checked(item):
        if isinstance(item, str) and item in allowed:
            return '<public tracked path>'
        if isinstance(item, dict):
            return {key: checked(part) for key, part in item.items()}
        if isinstance(item, list):
            return [checked(part) for part in item]
        return item
    shared.load(Path(__file__).with_name('real_rescore.py'), 'study_public_projection').public_json(checked(value))


def load_cases(path):
    manifest = json.loads(path.read_text())
    if manifest.get('selection_sha256') and hashlib.sha256((path.parent/'selection-rule.json').read_bytes()).hexdigest() != manifest['selection_sha256']:
        raise ValueError('Selection rule hash mismatch')
    tasks = []
    for item in manifest['cases']:
        task = dict(item)
        if 'inventory' in task:
            inventory = (path.parent / task['inventory']).resolve(strict=True)
            if (not inventory.is_relative_to(path.parent.resolve()) or
                    hashlib.sha256(inventory.read_bytes()).hexdigest() != task['inventory_sha256']):
                raise ValueError('Inventory hash or confinement mismatch')
            data = json.loads(gzip.decompress(inventory.read_bytes()) if inventory.suffix == '.gz' else inventory.read_bytes())
            if set(data) != {'source_paths', 'tree_paths'}:
                raise ValueError('Unexpected inventory fields')
            task.update(data)
        patch = (path.parent / task['hidden_patch']).resolve(strict=True)
        if not patch.is_relative_to(path.parent.resolve()):
            raise ValueError('Historical patch must be inside the case bundle')
        if hashlib.sha256(patch.read_bytes()).hexdigest() != task['hidden_patch_sha256']:
            raise ValueError('Historical patch hash mismatch')
        if hashlib.sha256(task['issue_body'].encode()).hexdigest() != task['issue_body_sha256']:
            raise ValueError('Issue body hash mismatch')
        for key in ('source_paths', 'tree_paths', 'test_files', 'test_modules', 'production_files'):
            paths = task[key]
            if not paths or any(not name or Path(name).is_absolute() or '..' in Path(name).parts
                                or Path(name).parts[0] in ('.git', '.code-graph') for name in paths):
                raise ValueError('Unsafe or empty case inventory')
        if not task['hidden_ids'] or not task['smoke_ids']:
            raise ValueError('Native test selectors cannot be empty')
        task['hidden_patch'] = str(patch)
        tasks.append(task)
    if len({task['id'] for task in tasks}) != len(tasks):
        raise ValueError('Duplicate study case')
    return manifest, tasks


def ordered_cases(tasks):
    groups = [[task for task in tasks if task['repository'] == name]
              for name in ('django', 'typescript-eslint', 'prometheus')]
    if sum(map(len, groups)) != len(tasks):
        raise ValueError('Unknown study repository')
    return [group[i] for i in range(max(map(len, groups), default=0)) for group in groups if i < len(group)]


def apply_historical_fixture(root, patch, patch_sha256, expected_files):
    """Host-only fake-agent control; live agents never receive the historical fix."""
    if not expected_files or hashlib.sha256(Path(patch).read_bytes()).hexdigest() != patch_sha256:
        raise ValueError('Missing fixture hashes or historical patch hash mismatch')
    shared.git(root, 'apply', '--unidiff-zero', '--check', str(patch))
    shared.git(root, 'apply', '--unidiff-zero', str(patch))
    actual = {name: hashlib.sha256((Path(root)/name).read_bytes()).hexdigest() for name in expected_files}
    if actual != expected_files:
        raise ValueError('Mechanical fixture differs from the historical fix')
    return dict(historical_fix_exact=True, production_sha256=actual)


def native_argv(environment, task, ids):
    config = json.loads(Path(environment).read_text())
    python = config['python'] if task['language'] == 'python' else sys.executable
    return ['/usr/bin/sandbox-exec', '-p', PROFILE, python, '-I', str(NATIVE),
            '--environment', str(environment), '--language', task['language'], '--ids', json.dumps(ids),
            '--test-files', json.dumps(task.get('test_modules', task['test_files']))]


def test_run(root, environment, task, ids):
    try:
        if EXECUTOR is None:
            raise ValueError('A skill-root test executor is required')
        run = EXECUTOR.run_tests(native_argv(environment, task, ids), root, timeout=TIMEOUT, output_limit=512000)
        folder = Path(json.loads(Path(environment).read_text())['native_evidence'])
        folder.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(run, sort_keys=True).encode()
        binding = hashlib.sha256(payload).hexdigest()
        (folder / (binding + '.json')).write_bytes(payload)
        for trace in (root / '.code-graph/study-native').glob('native*'):
            data = trace.read_bytes()
            (folder / (hashlib.sha256(data).hexdigest() + trace.suffix)).write_bytes(data)
        if run['status'] in ('timeout', 'error') or run.get('cleanup_error'):
            return dict(exit_code=1, errors=1, error_code='native_timeout_start_or_cleanup', evidence_sha256=binding)
        lines = [s for s in run['stdout_tail'].splitlines() if s.startswith('CG_STUDY_TEST=')]
        if not lines:
            return dict(exit_code=run['exit'], errors=1, error_code='missing_native_receipt', evidence_sha256=binding)
        result = json.loads(lines[-1].split('=', 1)[1])
        result.update(exit_code=run['exit'], evidence_sha256=binding)
        return result
    except subprocess.TimeoutExpired:
        return dict(exit_code=1, errors=1, timed_out=True, error_code='native_timeout')
    except (OSError, ValueError):
        return dict(exit_code=1, errors=1, error_code='native_start_or_receipt')


def source_copy(source, destination, paths):
    """Copy tracked inputs only, retaining tracked dist/fixtures but no dependency/build caches."""
    allowed = set(paths)
    parents = {str(p) for name in paths for p in Path(name).parents if str(p) != '.'}
    visible = allowed | parents
    if any(Path(n).is_absolute() or '..' in Path(n).parts or Path(n).parts[0] == '.git' for n in paths):
        raise ValueError('Unsafe tracked source path')

    def ignore(directory, names):
        return [name for name in names if str((Path(directory) / name).relative_to(source)) not in visible]

    shutil.copytree(source, destination, symlinks=True, ignore=ignore)
    for name in allowed:
        path = destination / name
        if path.is_symlink() and not path.resolve().is_relative_to(destination.resolve()):
            raise ValueError('Source symlink escapes the frozen tree')


def frozen_tests(candidate, baseline, task, environment, hidden=False):
    with tempfile.TemporaryDirectory(prefix='cg-study-score-') as folder:
        root = Path(folder) / 'repo'
        source_copy(candidate, root, task['tree_paths'])
        for name in task['test_files']:
            old, actual = baseline / name, root / name
            if old.is_file():
                actual.parent.mkdir(parents=True, exist_ok=True)
                actual.write_bytes(old.read_bytes())
            elif actual.is_file():
                actual.unlink()
        if hidden:
            patch = Path(task['hidden_patch'])
            if hashlib.sha256(patch.read_bytes()).hexdigest() != task['hidden_patch_sha256']:
                raise ValueError('Historical patch hash mismatch')
            shared.git(root, 'apply', '--check', str(patch))
            shared.git(root, 'apply', str(patch))
        return test_run(root, environment, task, task['hidden_ids'] if hidden else task['smoke_ids'])


def qualify(prepared, task, environment):
    base, fix = prepared / 'base', prepared / 'fix'
    result = dict(parent_public=test_run(base, environment, task, task['smoke_ids']),
                  parent_hidden=frozen_tests(base, base, task, environment, hidden=True),
                  fix_public=frozen_tests(fix, base, task, environment),
                  fix_hidden=test_run(fix, environment, task, task['hidden_ids']))
    red = result['parent_hidden']
    result['qualified'] = (all(pilot.green(result[k]) for k in ('parent_public', 'fix_public', 'fix_hidden'))
                           and red.get('failed', 0) > 0 and red.get('errors') == red.get('skipped') == 0
                           and red.get('executed', 0) == result['fix_hidden'].get('executed') and not red.get('timed_out'))
    return result


def is_test(name):
    return 'tests' in Path(name).parts or 'testdata' in Path(name).parts or name.endswith(('_test.go', '.test.ts', '.test.tsx'))


def hooks(task):
    def configure_flow(flow, engine):
        original = flow._TESTS['run_tests']
        def run(argv, *args, **kwargs):
            if len(argv) >= 3 and argv[:3] == [sys.executable, '-I', str(Path(engine.__file__).resolve())]:
                kwargs['timeout'] = TIMEOUT
            return original(argv, *args, **kwargs)
        flow._TESTS['run_tests'] = run

    def retained(candidate, baseline, changed):
        result = integrity.retained(candidate, baseline, [n for n in changed if is_test(n)], task['language'])
        if task['language'] == 'python':
            sources = {n: (baseline / n).read_text() for n in changed
                       if n.endswith('.py') and not is_test(n) and (baseline / n).is_file()}
            result['top_level_api'] = shared.api_retention(candidate, dict(sources=sources))
        else:
            result['api_scope'] = 'review_and_native_tests; no complete static API comparison'
        return result

    def seed(cache, destination, item):
        pilot.seed(cache, destination, item,
                   exporter=lambda c, rev, dest: pilot.archive(c, rev, dest, allow_symlinks=True))

    return dict(seed=seed, runner=StudyRunner, configure_flow=configure_flow, step_seconds=3*TIMEOUT+60,
                public_argv=lambda env, item: native_argv(env, item, item['smoke_ids']),
                frozen_tests=frozen_tests, retained=retained, check_api=task['language'] == 'python')


def live_paths(tasks, skill, prepared, environment, work_dir, result, save):
    if not 20 <= len(tasks) <= 24 or {t['repository'] for t in tasks} != {'django', 'typescript-eslint', 'prometheus'}:
        raise ValueError('The frozen study requires 20 to24 cases across all three repositories')
    if work_dir.exists():
        raise ValueError('Live work directory must be new')
    work_dir.mkdir(parents=True)
    result['clients'] = shared.client_metadata()
    counter, accounted, reported, unknown, stop = CallBudget(len(tasks)), 0.0, 0.0, 0, None
    for index, task in enumerate(ordered_cases(tasks)):
        for arm in (('source_only', 'graph_assisted') if index % 2 == 0 else ('graph_assisted', 'source_only')):
            if not stop and result.get('engine_files') is not None and engine_files(skill) != result['engine_files']:
                stop = 'engine_drift'
            if not stop and accounted + REVIEW_USD > TOTAL_USD + 1e-9:
                stop = 'claude_total_budget'
            if not stop and counter[0] + 3 > counter.limit:
                stop = 'start_ceiling'
            row = dict(task=task['id'], arm=arm, outcome='not_started_after_stop_rule', calls=[])
            if not stop:
                reviews_before = counter[1]
                try:
                    row = pilot.run_case(skill, prepared / task['repository'] / 'cache',
                        work_dir / (task['id'] + '-' + arm), task, arm, counter, environment, hooks=hooks(task))
                except Exception as error:
                    row.update(outcome='harness_failed', error_code=type(error).__name__,
                               cleanup_drained=False, calls_receipt_complete=False)
                    unknown += counter[1] - reviews_before
                    accounted += REVIEW_USD * (counter[1] - reviews_before)
                for call in row['calls']:
                    if call['client'] == 'claude':
                        cost = call.get('cli_reported_cost_usd')
                        if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
                            accounted += cost
                            reported += cost
                        else:
                            accounted += REVIEW_USD
                            unknown += 1
                if row.get('access_failure') or row.get('cleanup_drained') is False or row.get('agent_command_violation'):
                    stop = 'access_cleanup_or_command'
                if result.get('engine_files') is not None and engine_files(skill) != result['engine_files']:
                    row['outcome_before_engine_drift'] = row['outcome']
                    row['outcome'], stop = 'engine_changed_unscored', 'engine_drift'
            result['cases'].append(row)
            result.update(live_starts=counter[0], review_starts=counter[1], claude_reported_cost_usd=round(reported, 6),
                          claude_accounted_with_unknown_caps_usd=round(accounted, 6), unknown_cost_reviews=unknown,
                          stop_reason=stop)
            save()
    analysis = shared.load(Path(__file__).with_name('study_analysis.py'), 'study_analysis')
    result['analysis'] = analysis.analyze(tasks, result['cases'], stopped=bool(stop))
    save()


def main():
    global EXECUTOR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--prepared', type=Path, required=True)
    parser.add_argument('--environment', type=Path, required=True)
    parser.add_argument('--skill-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--engine-manifest', type=Path, help='Accepted engine_files/parser_versions freeze; mandatory for --live and --localize')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true', help='Only in a separately assigned live phase, after protocol/case/engine acceptance')
    mode.add_argument('--localize', action='store_true', help='Run frozen locate once per parent, without models or tuning')
    parser.add_argument('--work-dir', type=Path)
    args = parser.parse_args()
    EXECUTOR = shared.load(args.skill_root / 'scripts/change_tests.py', 'study_test_executor')
    manifest, tasks = load_cases(args.cases)
    frozen = dict(engine_files=engine_files(args.skill_root), parser_versions=parser_versions())
    if args.live or args.localize:
        if args.engine_manifest is None:
            parser.error('The separate study phase requires --engine-manifest')
        expected = json.loads(args.engine_manifest.read_text())
        if any(expected.get(key) != value for key, value in frozen.items()):
            parser.error('Engine or parser differs from the accepted freeze')
    result = dict(format='codegraph-study/1', live=args.live, live_starts=0, cases=[],
                  cases_sha256=hashlib.sha256(args.cases.read_bytes()).hexdigest(),
                  environment_sha256=hashlib.sha256(args.environment.read_bytes()).hexdigest(),
                  **frozen, call_seconds=TIMEOUT, max_starts=MAX_STARTS, max_reviews=MAX_REVIEWS,
                  cohort_max_starts=6*len(tasks), cohort_max_reviews=2*len(tasks),
                  harness_files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},
                  review_usd=REVIEW_USD, total_usd=TOTAL_USD)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        validate_public(result, tasks)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    if args.live:
        if manifest.get('stop') or args.work_dir is None:
            parser.error('Live requires a qualified cohort and a new --work-dir')
        live_paths(tasks, args.skill_root, args.prepared, args.environment, args.work_dir, result, save)
        return 0
    if args.localize:
        localization = shared.load(Path(__file__).with_name('study_localization.py'), 'study_localization')
        for task in ordered_cases(tasks):
            if engine_files(args.skill_root) != result['engine_files']:
                result['stop_reason'] = 'engine_drift'
                save()
                return 1
            result['cases'].append(localization.run(EXECUTOR, args.skill_root, args.prepared, task))
            if engine_files(args.skill_root) != result['engine_files']:
                result['stop_reason'] = 'engine_drift'
                save()
                return 1
            save()
        return 0 if tasks and all(not row.get('error_code') for row in result['cases']) else 1
    for task in tasks:
        try:
            row = qualify(args.prepared / task['repository'] / 'cases' / str(task['pr']), task, args.environment)
        except Exception as error:
            row = dict(qualified=False, error_code=type(error).__name__)
        result['cases'].append(dict(task=task['id'], **row))
        save()
    return 0 if tasks and all(r['qualified'] for r in result['cases']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
