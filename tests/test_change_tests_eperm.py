"""Inject signal failures without touching any real process group."""
import errno
import importlib.util
import io
from pathlib import Path
import signal
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('change_tests_eperm', REPO / 'scripts/change_tests.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class CleanupPermission(unittest.TestCase):
    def process(self, code=0):
        output = b'test_payload\n'
        error = (b'Ran 1 test in 0.001s\nOK\n' if code == 0 else
                 b'FAIL: test_payload (Case)\nRan 1 test in 0.001s\nFAILED (failures=1)\n')
        return Mock(pid=424242, returncode=code, stdout=io.BytesIO(output),
                    stderr=io.BytesIO(error), poll=Mock(return_value=code), wait=Mock(return_value=code))

    def run_exited(self, process, outcomes, **kwargs):
        with patch.object(runner.subprocess, 'Popen', return_value=process), \
                patch.object(runner.os, 'killpg', side_effect=outcomes) as kill:
            try:
                result = runner.run_tests(['fake-tests'], REPO, **kwargs)
            except PermissionError as exc:
                self.fail(f'Cleanup discarded the completed test result: {exc}')
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertEqual(kill.call_count, len(outcomes))
        for call in kill.call_args_list:
            self.assertEqual(call.args, (process.pid, signal.SIGKILL))
        return result

    def test_exited_eperm_recovery_preserves_test_result(self):
        for code in (0, 1):
            for recovery in (None, ProcessLookupError(errno.ESRCH, 'gone')):
                with self.subTest(code=code, recovery=recovery):
                    result = self.run_exited(self.process(code),
                        [PermissionError(errno.EPERM, 'denied'), recovery])
                    self.assertEqual(result['exit'], code)
                    self.assertEqual(result['status'], 'passed' if code == 0 else 'failed')
                    self.assertEqual(result['summary']['total'], 1)
                    self.assertEqual(result['summary']['failed'], code)
                    self.assertEqual(result['stdout_tail'], 'test_payload\n')
                    self.assertEqual(result['failing'], [] if code == 0 else ['test_payload (Case)'])

    def test_persistent_exited_eperm_is_bounded_and_reported(self):
        for code in (0, 1):
            with self.subTest(code=code):
                result = self.run_exited(self.process(code), [PermissionError(errno.EPERM, 'denied')] * 3)
                self.assertEqual(result['exit'], code)
                self.assertEqual(result['status'], 'passed' if code == 0 else 'failed')
                self.assertEqual(result['summary']['total'], 1)
                self.assertEqual(result['summary']['failed'], code)
                self.assertIn('denied', result['cleanup_error'])
                self.assertEqual(result['stdout_tail'], 'test_payload\n')
                self.assertIn('Ran 1 test', result['stderr_tail'])

    def test_timeout_then_exited_eperm_keeps_timeout_status(self):
        process = self.process(-9)
        process.poll.side_effect = [None, -9, -9, -9]
        result = self.run_exited(process, [PermissionError(errno.EPERM, 'denied')] * 3, timeout=0)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['exit'], -9)

    def test_live_eperm_positive_control_still_raises(self):
        process = self.process()
        process.poll.return_value = None
        with patch.object(runner.subprocess, 'Popen', return_value=process), \
                patch.object(runner.os, 'killpg', side_effect=PermissionError(errno.EPERM, 'denied')) as kill:
            with self.assertRaises(PermissionError):
                runner.run_tests(['fake-tests'], REPO, timeout=0)
        self.assertEqual(kill.call_count, 1)

    def test_cancelled_then_exited_eperm_keeps_cancellation(self):
        process = self.process(-9)
        process.poll.side_effect = [None, -9, -9, -9]
        with self.assertRaises(runner.TestCancelled):
            self.run_exited(process, [PermissionError(errno.EPERM, 'denied')] * 3, cancelled=lambda: True)
