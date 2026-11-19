#!/usr/bin/env python3
"""Run unittest discovery in isolated module processes; reconcile every test ID.

Usage: python -I tools/parallel_tests.py [--jobs N] [--json]
Each module keeps unittest's fixture lifecycle. Workers have private temporary
folders; tests must still allocate their own ephemeral ports and Git fixtures.
Skips, discovery errors, crashes and incomplete execution fail the proof.
"""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest


def test_ids(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from test_ids(test)
        else:
            yield test.id()


class ModuleLoader(unittest.TestLoader):
    def __init__(self):
        super().__init__()
        self.modules = {}
        self.depth = 0

    def loadTestsFromModule(self, module, *args, **kwargs):
        self.depth += 1
        try:
            suite = super().loadTestsFromModule(module, *args, **kwargs)
        finally:
            self.depth -= 1
        if self.depth == 0:
            self.modules[module.__name__] = suite
        return suite


def child(args):
    folder = Path(args._folder)
    loader = ModuleLoader()
    suite = loader.discover(args.start_directory, pattern=args.pattern)
    ids = list(test_ids(suite))
    groups = {name: list(test_ids(tests)) for name, tests in loader.modules.items()}
    errors = list(loader.errors)
    if Counter(ids) != Counter(item for group in groups.values() for item in group):
        errors.append('Module partition differs from discovery')
    receipt = dict(ids=ids, modules=groups, problems=errors)
    if args._module:
        if errors:
            raise RuntimeError('\n'.join(errors))
        suite = loader.modules[args._module]
        receipt = dict(ids=list(test_ids(suite)))
        with (folder / 'started.jsonl').open('w', encoding='utf-8') as journal:
            class Result(unittest.TextTestResult):
                successes = 0

                def startTest(self, test):
                    super().startTest(test)
                    journal.write(json.dumps(test.id()) + '\n')
                    journal.flush()

                def addSuccess(self, test):
                    super().addSuccess(test)
                    self.successes += 1

            result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
        receipt.update(executed=result.testsRun, passed=result.successes,
                       failed=len(result.failures) + len(result.unexpectedSuccesses),
                       errors=len(result.errors), skipped=len(result.skipped),
                       expected_failures=len(result.expectedFailures),
                       successful=result.wasSuccessful() and not result.skipped)
    (folder / 'result.json').write_text(json.dumps(receipt), encoding='utf-8')
    return 0 if not errors and receipt.get('successful', True) else 1


def launch(args, folder, cancel, module=None):
    folder.mkdir()
    temporary = folder / 'tmp'
    temporary.mkdir()
    env = dict(os.environ, TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary),
               PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
               '-s', args.start_directory, '-p', args.pattern, '--_folder', str(folder)]
    if module:
        command += ['--_module', module]
    problems = []
    started = time.monotonic()
    code = None
    with (folder / 'stdout.log').open('w') as out, (folder / 'stderr.log').open('w') as err:
        if cancel.is_set():
            return dict(problems=['Cancelled before start'], native_rc=None)
        proc = subprocess.Popen(command, stdout=out, stderr=err, env=env, start_new_session=True)
        try:
            while proc.poll() is None:
                if cancel.is_set():
                    problems.append('Cancelled')
                    break
                if time.monotonic() - started >= args.timeout:
                    problems.append(f'Timed out after {args.timeout}s')
                    break
                cancel.wait(0.05)
            code = proc.poll()
        finally:
            # Reap the worker and any descendants still in its owned process group.
            for attempt in range(3):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                    break
                except ProcessLookupError:
                    break
                except PermissionError as exc:
                    if proc.poll() is None or attempt == 2:
                        problems.append(f'Cannot clean up worker process group: {exc}')
                        break
                    time.sleep(.01)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                problems.append('Worker still running after cleanup')
    if code != 0:
        problems.append(f'Worker exited with status {code}')
    return dict(problems=problems, native_rc=code, seconds=round(time.monotonic() - started, 3))


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def reconcile(folder, expected, process):
    report = dict(process, collected=len(expected), executed=0, passed=0,
                  failed=0, errors=0, skipped=0, expected_failures=0)
    problems = report['problems']
    started = []
    try:
        started = [json.loads(line) for line in (folder / 'started.jsonl').read_text().splitlines()]
        report['executed'] = len(started)
        receipt = read_json(folder / 'result.json')
        if receipt['ids'] != expected or receipt['executed'] != len(started):
            problems.append('Collection/execution receipt mismatch')
        for name in ('passed', 'failed', 'errors', 'skipped', 'expected_failures'):
            value = receipt[name]
            if type(value) is not int or value < 0:
                raise ValueError(f'Invalid {name} count')
            report[name] = value
        if receipt['successful'] is not True or any(report[k] for k in ('failed', 'errors', 'skipped')):
            problems.append('Tests did not all pass (skips are incomplete coverage)')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        problems.append(f'Missing or invalid worker receipt: {exc}')
    if started != expected:
        problems.append(f'ID/count mismatch: collected={len(expected)} executed={len(started)}')
    return report, started


def run(args):
    started_at = time.monotonic()
    args.start_directory = str(Path(args.start_directory).resolve())
    cancel = threading.Event()
    previous = {}

    def interrupt(signum, frame):
        cancel.set()
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous[sig] = signal.signal(sig, interrupt)
    try:
        with tempfile.TemporaryDirectory(prefix='cg-parallel-') as temporary:
            folder = Path(temporary)
            discovery = folder / 'discovery'
            process = launch(args, discovery, cancel)
            problems = list(process['problems'])
            collected, executed, reports = [], [], {}
            try:
                manifest = read_json(discovery / 'result.json')
                collected = manifest['ids']
                problems.extend(manifest['problems'])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                problems.append(f'Discovery failed: {exc}')
            if not problems:
                modules = [(name, ids) for name, ids in manifest['modules'].items() if ids]
                with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                    pending = {}
                    try:
                        for index, (name, ids) in enumerate(sorted(modules, key=lambda item: -len(item[1]))):
                            worker = folder / str(index)
                            future = pool.submit(launch, args, worker, cancel, name)
                            pending[future] = (name, ids, worker)
                        for future in as_completed(pending):
                            name, ids, worker = pending[future]
                            report, found = reconcile(worker, ids, future.result())
                            executed.extend(found)
                            reports[name] = report
                            if report['problems']:
                                problems.extend(f'{name}: {problem}' for problem in report['problems'])
                                report['stdout'] = (worker / 'stdout.log').read_text(errors='replace')
                                report['stderr'] = (worker / 'stderr.log').read_text(errors='replace')
                    finally:
                        cancel.set()
            if Counter(collected) != Counter(executed):
                problems.append('Global collected/executed ID mismatch')
            if not collected:
                problems.append('No tests collected')
            summary = dict(collected=len(collected), executed=len(executed),
                           **{key: sum(r[key] for r in reports.values()) for key in
                              ('passed', 'failed', 'errors', 'skipped', 'expected_failures')},
                           successful=not problems, problems=problems, modules=reports,
                           jobs=args.jobs, seconds=round(time.monotonic() - started_at, 3))
            if process['problems']:
                summary['discovery_stderr'] = (discovery / 'stderr.log').read_text(errors='replace')
            if args.json:
                print(json.dumps(summary, indent=2))
            else:
                for name, report in sorted(reports.items()):
                    print(f"{name}: {report['executed']}/{report['collected']} tests, {report['seconds']}s")
                    if report['problems']:
                        print(report.get('stdout', '') + report.get('stderr', ''))
                print(' '.join(f'{key}={value}' for key, value in summary.items() if key not in ('modules', 'problems')))
                for problem in problems:
                    print(problem)
            return 0 if summary['successful'] else 1
    finally:
        cancel.set()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-j', '--jobs', type=positive, default=os.cpu_count() or 1)
    parser.add_argument('-s', '--start-directory', default='tests')
    parser.add_argument('-p', '--pattern', default='test*.py')
    parser.add_argument('--timeout', type=positive, default=300, help='seconds per worker (default: 300)')
    parser.add_argument('--json', action='store_true', help='print a JSON report, including failure logs')
    parser.add_argument('--_folder', help=argparse.SUPPRESS)
    parser.add_argument('--_module', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        return child(args) if args._folder else run(args)
    except KeyboardInterrupt:
        print('Interrupted: no passing proof', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
