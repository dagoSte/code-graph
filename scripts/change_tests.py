"""Run only an explicitly approved argv; retain bounded output and test identities."""
import os
import re
import signal
import subprocess
import threading
import time


class TestCancelled(Exception):
    pass


def parse_output(text):
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    summary = dict(total=None, failed=None, errors=None, skipped=None, framework='unknown')
    failing = []
    if re.search(r'^Ran \d+ tests? in ', text, re.M):
        summary.update(framework='unittest', total=int(re.search(r'^Ran (\d+) tests?', text, re.M)[1]),
                       failed=0, errors=0, skipped=0)
        for key, field in [('failures', 'failed'), ('errors', 'errors'), ('skipped', 'skipped')]:
            match = re.search(r'\b' + key + r'=(\d+)', text)
            if match:
                summary[field] = int(match[1])
        failing = re.findall(r'^(?:FAIL|ERROR): (.+)$', text, re.M)
    elif re.search(r'\b(?:\d+ passed|\d+ failed|\d+ errors?)\b.*\bin \d', text) or 'test session starts' in text:
        summary.update(framework='pytest', failed=0, errors=0, skipped=0)
        counts = {}
        # The final summary wins over intermediate progress lines.
        for line in text.splitlines():
            if re.search(r'\bin [\d.]+s', line):
                for count, label in re.findall(r'(\d+) (passed|failed|errors?|skipped|xfailed|xpassed)', line):
                    counts[label.rstrip('s') if label.startswith('error') else label] = int(count)
        if counts:
            summary.update(total=sum(counts.values()), failed=counts.get('failed', 0),
                           errors=counts.get('error', 0), skipped=counts.get('skipped', 0) + counts.get('xfailed', 0))
        failing = re.findall(r'^(?:FAILED|ERROR) (\S+)', text, re.M)
    elif 'PHPUnit ' in text or re.search(r'OK \(\d+ tests?, \d+ assertions?\)', text):
        summary.update(framework='phpunit', failed=0, errors=0, skipped=0)
        match = re.search(r'OK \((\d+) tests?', text)
        if match:
            summary['total'] = int(match[1])
        for label, key in [('Tests', 'total'), ('Failures', 'failed'), ('Errors', 'errors'), ('Skipped', 'skipped')]:
            match = re.search(r'\b' + label + r':\s*(\d+)', text)
            if match:
                summary[key] = int(match[1])
        failing = re.findall(r'^\d+\) (.+)$', text, re.M)
    elif re.search(r'^Tests:\s', text, re.M):
        summary.update(framework='jest', failed=0, errors=0, skipped=0)
        line = re.findall(r'^Tests:\s*(.+)$', text, re.M)[-1]
        for count, key in re.findall(r'(\d+) (failed|skipped|total)', line):
            summary[key] = int(count)
        suite = ''
        for entry in text.splitlines():
            match = re.match(r'^\s*FAIL\s+(\S+)', entry)
            if match:
                suite = match[1]
            match = re.match(r'^\s*● (.+)$', entry)
            if match:
                failing.append((suite + '::' if suite else '') + match[1])
    return summary, sorted(set(failing))


def run_tests(argv, cwd, *, timeout=600, cancelled=lambda: False, output_limit=8000):
    """No shell, no command inference, no environment dump. Kill only our process group."""
    start = time.monotonic()
    result = dict(argv=list(argv), cwd=str(cwd), exit=None, status='error', duration_s=0.0,
                  summary=dict(total=None, failed=None, errors=None, skipped=None, framework='unknown'),
                  failing=[], stdout_tail='', stderr_tail='')
    if not argv:
        return dict(result, exit=0, status='passed', skipped=True)
    buffers = [bytearray(), bytearray()]

    def drain(stream, buffer):
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                buffer.extend(chunk)
                del buffer[:-512000]
        finally:
            stream.close()

    try:
        process = subprocess.Popen(argv, cwd=cwd, shell=False, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    except (OSError, ValueError) as error:
        result.update(stderr_tail=str(error)[-8000:], duration_s=time.monotonic() - start)
        return result
    readers = [threading.Thread(target=drain, args=(stream, buffer), daemon=True)
               for stream, buffer in zip((process.stdout, process.stderr), buffers)]
    for thread in readers:
        thread.start()
    stopped = None
    try:
        while process.poll() is None:
            if cancelled():
                stopped = 'cancelled'
                break
            if time.monotonic() - start >= timeout:
                stopped = 'timeout'
                break
            time.sleep(.02)
    finally:
        # Also reap children retaining stdout after the parent has exited.
        for attempt in range(3):
            try:
                os.killpg(process.pid, signal.SIGKILL)
                break
            except ProcessLookupError:
                break
            except PermissionError as error:
                if process.poll() is None:
                    raise
                if attempt == 2:
                    result['cleanup_error'] = str(error)
                    break
                # An exited leader does not prove that its descendants are gone.
                time.sleep(.01)
        process.wait()
        for thread in readers:
            thread.join(timeout=2)
    if stopped == 'cancelled':
        raise TestCancelled()
    stdout, stderr = (bytes(buffer).decode('utf-8', 'replace') for buffer in buffers)
    summary, failing = parse_output(stdout + '\n' + stderr)
    result.update(exit=process.returncode, status=stopped or ('passed' if process.returncode == 0 else 'failed'),
                  duration_s=round(time.monotonic() - start, 3), summary=summary, failing=failing,
                  stdout_tail=stdout[-output_limit:], stderr_tail=stderr[-output_limit:])
    return result


def compare_tests(before, after):
    old = set(before['failing']) if before['status'] == 'failed' else set()
    new = set(after['failing']) if after['status'] == 'failed' else set()
    newly = sorted(new - old)
    # An unparsed nonzero exit must not be mistaken for a known passing/known-red run.
    if after['status'] == 'failed' and not new:
        newly.append('Unidentified test failure (nonzero exit)')
    new_count = sum(after['summary'].get(key) or 0 for key in ('failed', 'errors'))
    if after['status'] == 'failed' and new_count > len(new) and not newly:
        newly.append('Failures without a test identity cannot be classified as known')
    return dict(newly_failing=newly, fixed=sorted(old - new), still_failing=sorted(old & new),
                before=before['status'], after=after['status'])
