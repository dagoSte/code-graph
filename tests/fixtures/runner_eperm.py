"""Inject process-group errors around real runner subprocesses, without models."""
import json
from pathlib import Path
import runpy
import signal
import sys
from unittest.mock import patch


runner_path, recovery, target, evidence, *arguments = sys.argv[1:]
runner = runpy.run_path(runner_path)
popen, killpg = runner['subprocess'].Popen, runner['os'].killpg
processes, attempts = {}, {}


def spawn(argv, **kwargs):
    proc = popen(argv, **kwargs)
    phase = 'worker' if '--_module' in argv else 'discovery'
    processes[proc.pid] = (proc, phase)
    # Enter launch's monitor only after the state required by the injection.
    # The live target keeps the real timeout; all other children finish first.
    if phase != target or recovery != 'live':
        proc.wait(timeout=10)
    return proc


def signal_group(pid, sig):
    proc, phase = processes[pid]
    if phase != target:
        return killpg(pid, sig)
    exited = proc.poll() is not None
    assert exited == (recovery != 'live'), 'wrong process state for cleanup injection'
    attempts[pid] = attempts.get(pid, 0) + 1
    if attempts[pid] == 1 or recovery in ('denied', 'live'):
        raise PermissionError(1, 'Synthetic runner cleanup EPERM')
    if recovery == 'gone':
        raise ProcessLookupError(3, 'Group gone')


try:
    with patch.object(runner['subprocess'], 'Popen', side_effect=spawn), \
            patch.object(runner['os'], 'killpg', side_effect=signal_group), \
            patch.object(sys, 'argv', [runner_path, *arguments]):
        code = runner['main']()
finally:
    Path(evidence).write_text(json.dumps(list(attempts.values())))
    for proc, _ in processes.values():
        if proc.poll() is None:
            killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=3)
raise SystemExit(code)
