"""Host-only offline native test runners; no models, engine copies or downloads."""
import argparse
import functools
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
import traceback

PROFILE = '(version 1)(allow default)(deny network-outbound (remote ip "*:*"))'
MARKER = 'CG_STUDY_TEST='


def summary():
    return dict(executed=0, passed=0, failed=0, errors=0, skipped=0)


def identifier(root, filename, name):
    root = Path(root).resolve()
    relative = str(Path(filename).resolve().relative_to(root))
    normalized = name.replace(str(root), '<ROOT>')
    return hashlib.sha256(json.dumps([relative, normalized], ensure_ascii=False).encode()).hexdigest()


def vitest_result(data, root, ids):
    expected, rows, runtime_errors = set(ids), {}, 0
    for module in data.get('testResults', []):
        for test in module.get('assertionResults', []):
            name = ' > '.join([*test['ancestorTitles'], test['title']])
            key = identifier(root, module['name'], name)
            messages = test.get('failureMessages', [])
            if test['status'] == 'failed' and (not messages or not all(message.startswith('AssertionError') for message in messages)):
                runtime_errors += 1
            if not expected or key in expected:
                rows.setdefault(key, []).append(test['status'])
    result = summary()
    for statuses in rows.values():
        result['executed'] += sum(s in ('passed', 'failed') for s in statuses)
        result['passed'] += statuses.count('passed')
        result['failed'] += statuses.count('failed')
        result['skipped'] += sum(s in ('pending', 'skipped', 'todo', 'disabled') for s in statuses)
        result['errors'] += sum(s not in ('passed', 'failed', 'pending', 'skipped', 'todo', 'disabled') for s in statuses)
    result['errors'] += len(expected - rows.keys())
    result['errors'] += runtime_errors
    if not rows and data.get('numFailedTestSuites', 0):
        result['errors'] += data['numFailedTestSuites']
    return dict(result, ids=sorted(rows), native_total_tests=data.get('numTotalTests'),
                native_executed_total=data.get('numPassedTests', 0) + data.get('numFailedTests', 0),
                native_failed_total=data.get('numFailedTests'), native_skipped_total=data.get('numPendingTests'))


def go_result(lines, selected):
    statuses, skipped, build_errors, panic = {}, 0, 0, False
    for line in lines.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        test, action = event.get('Test'), event.get('Action')
        if action == 'output' and event.get('Output', '').startswith(('panic:', 'fatal error:')):
            panic = True
        if action == 'build-fail':
            build_errors += 1
        if not test or test.split('/')[0] not in selected:
            continue
        if action == 'skip':
            skipped += 1
        if '/' not in test and action in ('pass', 'fail', 'skip'):
            statuses[test] = action
    result = summary()
    result.update(executed=len(statuses), passed=sum(s == 'pass' for s in statuses.values()),
                  failed=sum(s == 'fail' for s in statuses.values()), skipped=skipped,
                  errors=build_errors + len(set(selected) - statuses.keys()) + int(panic and 'fail' in statuses.values()))
    return result


def go_test_definitions(root, files):
    from tree_sitter_language_pack import get_parser
    definitions = {}
    for filename in files:
        path = Path(root) / filename
        if not path.is_file():
            continue
        tree = get_parser('go').parse(path.read_bytes())
        if tree.root_node.has_error:
            raise ValueError('Partial Go test module')
        for node in tree.root_node.named_children:
            if node.type != 'function_declaration':
                continue
            name = node.child_by_field_name('name').text.decode()
            if name.startswith('Test') and name != 'TestMain' and (len(name) == 4 or not name[4].islower()):
                definitions[('./' + str(Path(filename).parent), name)] = node.text.decode()
    return definitions


def django(ids):
    root = Path.cwd()
    sys.path[:0] = [str(root), str(root / 'tests')]
    seen = []
    original = unittest.TextTestRunner

    class Capture(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.successes = 0
            seen.append(self)

        def addSuccess(self, test):
            self.successes += 1
            super().addSuccess(test)

    class Runner(original):
        resultclass = Capture

    unittest.TextTestRunner = Runner
    sys.argv = ['runtests.py', *ids, '--settings=test_sqlite', '--parallel=1', '--noinput', '--verbosity=0']
    code = 0
    try:
        runpy.run_path(str(root / 'tests/runtests.py'), run_name='__main__')
    except SystemExit as error:
        code = int(error.code or 0)
    finally:
        unittest.TextTestRunner = original
    return dict(executed=sum(r.testsRun for r in seen), passed=sum(r.successes for r in seen),
                failed=sum(len(r.failures) for r in seen), errors=sum(len(r.errors) for r in seen),
                skipped=sum(len(r.skipped) for r in seen), exit_code=code)


def django_id(test):
    method = inspect.unwrap(getattr(test, test._testMethodName))
    while isinstance(method, functools.partial):
        method = inspect.unwrap(method.func)
    return dict(id=test.id(), implementation=method.__module__ + '.' + method.__qualname__)


def django_list(files):
    root = Path.cwd()
    sys.path[:0] = [str(root), str(root / 'tests')]
    os.environ['DJANGO_SETTINGS_MODULE'] = 'test_sqlite'
    import runtests
    from django.test.runner import DiscoverRunner
    labels = [name.removeprefix('tests/').removesuffix('.py').replace('/', '.') for name in files]
    labels, state = runtests.setup_run_tests(0, None, None, labels)
    try:
        suite = DiscoverRunner(verbosity=0, interactive=False, parallel=1).build_suite(labels)
        result, pending = [], [suite]
        while pending:
            test = pending.pop()
            if isinstance(test, unittest.TestSuite):
                pending.extend(test)
            else:
                result.append(django_id(test))
        return dict(tests=sorted(result, key=lambda r: r['id']), exit_code=0)
    finally:
        runtests.teardown_run_tests(state)


def node_environment(config, root):
    temporary = root / '.code-graph/study-native'
    temporary.mkdir(parents=True, exist_ok=True)
    # macOS Unix socket paths have a small fixed limit; keep IPC out of long worktree paths.
    ipc = Path(config['node_tmp']) / hashlib.sha256(str(root).encode()).hexdigest()[:10]
    ipc.mkdir(parents=True, exist_ok=True)
    path = os.pathsep.join([str(Path(config['pnpm']).parent), str(Path(config['node']).parent), os.environ.get('PATH', '')])
    return dict(os.environ, PATH=path, CI='true', NX_DAEMON='false', NX_NO_CLOUD='true',
                NX_ISOLATE_PLUGINS='false', NX_SKIP_NX_CACHE='true',
                NX_SOCKET_DIR=str(ipc / 'sockets'), NX_CACHE_DIRECTORY=str(temporary / 'nx-cache'),
                NX_WORKSPACE_DATA_DIRECTORY=str(temporary / 'nx-data'), TMPDIR=str(ipc),
                SKIP_POSTINSTALL='1', COREPACK_ENABLE_NETWORK='0')


def typescript(config, files, ids, listing=False):
    root = Path.cwd().resolve()
    env = node_environment(config, root)
    pnpm = [config['pnpm'], '--config.manage-package-manager-versions=false', '--config.package-manager-strict-version=false']
    prepare = [pnpm + ['install', '--offline', '--frozen-lockfile', '--ignore-scripts', '--store-dir', config['pnpm_store']],
               pnpm + ['exec', 'nx', 'run-many', '-t', 'build', '--exclude', 'website', 'website-eslint',
                       '--parallel=2', '--skip-nx-cache', '--output-style=static']]
    for index, command in enumerate(prepare):
        run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=600)
        (root / '.code-graph/study-native' / ('prepare-' + str(index) + '.log')).write_text(run.stdout + run.stderr)
        if run.returncode:
            return dict(summary(), errors=1, exit_code=run.returncode, error_code='offline_prepare_failed')
    runner = [config['node'], str(root / 'node_modules/vitest/vitest.mjs')]
    if listing:
        major = int(json.loads((root / 'node_modules/vitest/package.json').read_text())['version'].split('.')[0])
        # Vitest 5 static listing cannot discover RuleTester-generated cases.
        dynamic = ['--no-static-parse'] if major >= 5 else []
        listed = subprocess.run(runner + ['list', *files, '--json', '--maxWorkers=1', *dynamic], env=env, capture_output=True, text=True, timeout=600)
        (root / '.code-graph/study-native/listing.log').write_text(listed.stdout + listed.stderr)
        if listed.returncode:
            return dict(summary(), errors=1, exit_code=listed.returncode, error_code='test_listing_failed')
        names = json.loads(listed.stdout)
        return dict(ids=sorted(identifier(root, r['file'], r['name']) for r in names), exit_code=0)
    with tempfile.TemporaryDirectory(prefix='study-vitest-') as folder:
        output = Path(folder) / 'result.json'
        run = subprocess.run(runner + ['run', *files, '--no-file-parallelism', '--maxWorkers=1',
                                       '--allowOnly=false', '--retry=0', '--reporter=json', '--outputFile', str(output)],
                             env=env, capture_output=True, text=True, timeout=600)
        if not output.is_file():
            return dict(summary(), errors=1, exit_code=run.returncode, error_code='missing_native_report')
        (root / '.code-graph/study-native/native-vitest.json').write_bytes(output.read_bytes())
        return dict(vitest_result(json.loads(output.read_text()), root, ids), exit_code=run.returncode)


def go(config, ids):
    env = dict(os.environ, GOENV='off', GOTOOLCHAIN='local', GOPROXY='off', GOSUMDB='off',
               GOMODCACHE=config['go_modcache'], GOCACHE=config['go_cache'], GOMAXPROCS='2', CGO_ENABLED='0')
    result = summary()
    packages = sorted({row['package'] for row in ids})
    exit_code = 0
    for package in packages:
        names = sorted({row['name'] for row in ids if row['package'] == package})
        pattern = '^(?:' + '|'.join(re.escape(n) for n in names) + ')$'
        run = subprocess.run([config['go'], 'test', '-mod=readonly', '-p=2', '-parallel=1', '-count=1', '-json',
                              '-run', pattern, package], env=env, capture_output=True, text=True, timeout=600)
        trace = Path('.code-graph/study-native')
        trace.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(package.encode()).hexdigest()[:10]
        (trace / ('native-go-' + key + '.jsonl')).write_text(run.stdout)
        (trace / ('native-go-' + key + '.stderr')).write_text(run.stderr)
        row = go_result(run.stdout, names)
        for key in result:
            result[key] += row[key]
        exit_code = max(exit_code, run.returncode)
    return dict(result, exit_code=exit_code)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', type=Path, required=True)
    parser.add_argument('--language', choices=('python', 'typescript', 'go'), required=True)
    parser.add_argument('--ids', required=True)
    parser.add_argument('--test-files', default='[]')
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args()
    config, ids = json.loads(args.environment.read_text()), json.loads(args.ids)
    if not isinstance(ids, list) or (not ids and not args.list) or (args.list and args.language == 'go'):
        parser.error('Nonempty test IDs required; --list supports Python and TypeScript')
    started = time.monotonic()
    try:
        if args.language == 'python':
            result = django_list(json.loads(args.test_files)) if args.list else django(ids)
        elif args.language == 'go':
            result = go(config, ids)
        else:
            result = typescript(config, json.loads(args.test_files), ids, args.list)
    except subprocess.TimeoutExpired:
        result = dict(summary(), errors=1, exit_code=1, error_code='native_timeout')
    except Exception as error:
        traceback.print_exc()
        result = dict(summary(), errors=1, exit_code=1, error_code=type(error).__name__)
    result['seconds'] = round(time.monotonic() - started, 3)
    if not args.list and not (result.get('executed', 0) > 0 and result.get('passed') == result['executed']
                             and result.get('failed') == result.get('errors') == result.get('skipped') == 0):
        result['exit_code'] = result['exit_code'] or 1
    print(MARKER + json.dumps(result))
    return result['exit_code']


if __name__ == '__main__':
    raise SystemExit(main())
