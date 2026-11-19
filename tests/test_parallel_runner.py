"""Black-box checks of discovery, execution reconciliation and failure propagation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


RUNNER = Path(__file__).resolve().parents[1] / 'tools/parallel_tests.py'


class ParallelRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cg-runner-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def module(self, name, source):
        (self.root / f'test_{name}.py').write_text(textwrap.dedent(source))

    def run_suite(self, *extra):
        result = subprocess.run([sys.executable, '-I', str(RUNNER), '-s', str(self.root),
                                 '--jobs', '2', '--json', *extra],
                                capture_output=True, text=True, timeout=15)
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode == 0, report['successful'], result.stderr)
        return result.returncode, report

    def test_modules_run_concurrently_with_isolated_temporary_directories(self):
        source = '''
            import os, tempfile, time, unittest
            from pathlib import Path
            def setUpModule():
                marker = Path(__file__).with_suffix('.ready')
                marker.write_text(str(os.getpid()) + '\\n' + tempfile.gettempdir())
                deadline = time.monotonic() + 4
                while len(list(marker.parent.glob('*.ready'))) < 2:
                    if time.monotonic() > deadline:
                        raise AssertionError('modules did not overlap')
                    time.sleep(.01)
            class Tests(unittest.TestCase):
                @classmethod
                def setUpClass(cls):
                    cls.values = []
                def test_a(self):
                    self.values.append('a')
                def test_b(self):
                    self.assertEqual(self.values, ['a'])
        '''
        self.module('one', source)
        self.module('two', source)
        code, report = self.run_suite()
        self.assertEqual(code, 0, report)
        self.assertEqual((report['collected'], report['executed'], report['passed']), (4, 4, 4))
        markers = [path.read_text().splitlines() for path in self.root.glob('*.ready')]
        self.assertEqual(len({value[0] for value in markers}), 2)
        self.assertEqual(len({value[1] for value in markers}), 2)

    def test_failing_module_propagates_failure_and_preserves_logs(self):
        self.module('bad', '''
            import unittest
            class Tests(unittest.TestCase):
                def test_bad(self):
                    self.fail('fixture assertion sentinel')
        ''')
        self.module('good', '''
            import unittest
            class Tests(unittest.TestCase):
                def test_good(self): pass
        ''')
        code, report = self.run_suite()
        self.assertNotEqual(code, 0)
        self.assertEqual((report['collected'], report['executed'], report['passed'], report['failed']), (2, 2, 1, 1))
        self.assertIn('fixture assertion sentinel', report['modules']['test_bad']['stderr'])

    def test_missing_execution_is_not_a_success(self):
        self.module('missing', '''
            import unittest
            class Tests(unittest.TestCase):
                def test_one(self): pass
            class IncompleteSuite(unittest.TestSuite):
                def run(self, result, debug=False): return result
            def load_tests(loader, tests, pattern):
                return IncompleteSuite([Tests('test_one')])
        ''')
        code, report = self.run_suite()
        self.assertNotEqual(code, 0)
        self.assertEqual((report['collected'], report['executed']), (1, 0))
        self.assertIn('ID/count mismatch', ' '.join(report['problems']))

    def test_same_count_with_different_ids_is_rejected(self):
        self.module('different', '''
            import unittest
            class Tests(unittest.TestCase):
                def test_one(self): pass
                def test_other(self): pass
            class DifferentSuite(unittest.TestSuite):
                def run(self, result, debug=False):
                    Tests('test_other').run(result)
                    return result
            def load_tests(loader, tests, pattern):
                return DifferentSuite([Tests('test_one')])
        ''')
        code, report = self.run_suite()
        self.assertNotEqual(code, 0)
        self.assertEqual((report['collected'], report['executed']), (1, 1))
        self.assertIn('ID/count mismatch', ' '.join(report['problems']))

    def test_load_tests_duplicates_preserve_unittest_multiplicity(self):
        self.module('duplicate', '''
            import unittest
            class Tests(unittest.TestCase):
                def test_one(self): pass
            def load_tests(loader, tests, pattern):
                return unittest.TestSuite([Tests('test_one'), Tests('test_one')])
        ''')
        code, report = self.run_suite('--jobs', '1')
        self.assertEqual(code, 0, report)
        self.assertEqual((report['collected'], report['executed'], report['passed']), (2, 2, 2))

    def test_crash_cannot_leave_a_passing_receipt(self):
        self.module('crash', '''
            import os, unittest
            class Tests(unittest.TestCase):
                def test_crash(self): os._exit(0)
        ''')
        code, report = self.run_suite()
        self.assertNotEqual(code, 0)
        self.assertEqual(report['executed'], 1)
        self.assertIn('Missing or invalid worker receipt', ' '.join(report['problems']))

    def test_discovery_error_and_empty_suite_fail(self):
        for source in ('raise RuntimeError("discovery sentinel")', ''):
            with self.subTest(source=source):
                self.module('broken', source)
                code, report = self.run_suite()
                self.assertNotEqual(code, 0)
                self.assertEqual(report['executed'], 0)
                self.assertTrue(report['problems'])

    def test_errors_skips_and_subtest_failures_are_counted(self):
        self.module('outcomes', '''
            import unittest
            class Tests(unittest.TestCase):
                def test_error(self): raise RuntimeError('fixture error')
                @unittest.skip('fixture skip')
                def test_skip(self): pass
                def test_subtest(self):
                    with self.subTest(value=1): self.fail('subtest sentinel')
        ''')
        code, report = self.run_suite()
        self.assertNotEqual(code, 0)
        self.assertEqual((report['collected'], report['executed'], report['errors'],
                          report['skipped'], report['failed']), (3, 3, 1, 1, 1))

    def test_timeout_fails_and_stops_worker(self):
        self.module('timeout', '''
            import os, time, unittest
            from pathlib import Path
            class Tests(unittest.TestCase):
                def test_slow(self):
                    Path(__file__).with_suffix('.pid').write_text(str(os.getpid()))
                    time.sleep(30)
        ''')
        code, report = self.run_suite('--timeout', '1')
        self.assertNotEqual(code, 0)
        self.assertIn('Timed out', ' '.join(report['problems']))
        pid = int((self.root / 'test_timeout.pid').read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_jobs_must_be_positive(self):
        result = subprocess.run([sys.executable, '-I', str(RUNNER), '--jobs', '0'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn('must be positive', result.stderr)

    def injected_cleanup(self, recovery, target='worker'):
        evidence = self.root / 'signals.json'
        result = subprocess.run([sys.executable, '-I', str(RUNNER.parents[1] / 'tests/fixtures/runner_eperm.py'),
                                 str(RUNNER), recovery, target, str(evidence), '-s', str(self.root),
                                 '--jobs', '1', '--timeout', '1', '--json'],
                                capture_output=True, text=True, timeout=30)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        try:
            report = json.loads(result.stdout)
        except ValueError:
            self.fail('Runner did not finish with a report: ' + result.stderr)
        self.assertEqual(result.returncode == 0, report['successful'])
        return report, json.loads(evidence.read_text())

    def test_exited_worker_cleanup_eperm_retries_and_always_reports(self):
        self.module('good', '''
            import unittest
            class Tests(unittest.TestCase):
                def test_good(self): pass
        ''')
        for target in ('discovery', 'worker'):
            for recovery in ('gone', 'delivered', 'denied'):
                with self.subTest(target=target, recovery=recovery):
                    report, attempts = self.injected_cleanup(recovery, target)
                    self.assertEqual(attempts, [3 if recovery == 'denied' else 2])
                    self.assertEqual(report['successful'], recovery != 'denied', report)
                    self.assertEqual(report['collected'], 1)
                    if target == 'worker' or recovery != 'denied':
                        self.assertEqual((report['executed'], report['passed']), (1, 1))
                    if recovery == 'denied':
                        self.assertIn('Synthetic runner cleanup EPERM', ' '.join(report['problems']))

    def test_exited_cleanup_waits_for_slow_discovery(self):
        self.module('slow_import', '''
            import time, unittest
            time.sleep(1.2)
            class Tests(unittest.TestCase):
                def test_good(self): pass
        ''')
        for target in ('discovery', 'worker'):
            with self.subTest(target=target):
                report, attempts = self.injected_cleanup('delivered', target)
                self.assertEqual(attempts, [2])
                self.assertTrue(report['successful'], report)
                self.assertEqual((report['collected'], report['executed'], report['passed']), (1, 1, 1))

    def test_live_worker_cleanup_permission_error_fails_with_a_report(self):
        self.module('slow', '''
            import time, unittest
            class Tests(unittest.TestCase):
                def test_slow(self): time.sleep(30)
        ''')
        report, attempts = self.injected_cleanup('live')
        self.assertEqual(attempts, [1])
        self.assertFalse(report['successful'])
        self.assertIn('Timed out', ' '.join(report['problems']))
        self.assertIn('Synthetic runner cleanup EPERM', ' '.join(report['problems']))
