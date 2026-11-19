"""A synthetic command that writes below Python's stdout/stderr wrappers."""
import ctypes
import os
import runpy
import subprocess
import sys
import threading
from types import SimpleNamespace

children = []


def noisy(args):
    if args.pattern == 'newlines':
        os.write(1, b'raw\r\nreturn\rend\n')
        sys.stdout.write('python\r\nreturn\rend\n')
        return
    if args.pattern in ('original-streams', 'flush-error'):
        if args.pattern == 'original-streams':
            sys.__stdout__.write('original stdout pending')
        else:
            class BrokenFlush:
                def flush(self):
                    raise OSError('synthetic flush failure')
            sys.__stdout__ = BrokenFlush()
        sys.__stderr__.write('original stderr pending')
        ctypes.CDLL(None).puts(b'buffered libc')
        return
    if args.pattern == 'retained-pipe':
        os.write(1, 'before descendant \U0001f600\r\n'.encode())
        children.append(subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                         stdin=subprocess.DEVNULL))
        return
    os.write(1, b'fd stdout\n')
    os.write(2, b'fd stderr\n')
    subprocess.run(['/bin/echo', 'child stdout'], check=True)
    print('python stdout')
    print('python stderr', file=sys.stderr)
    ctypes.CDLL(None).puts(b'buffered libc')
    if args.pattern == 'fail':
        raise RuntimeError('command failed')
    if args.pattern == 'large':
        os.write(1, ('😀"\\\n' * 50000).encode())


if __name__ == '__main__':
    server = runpy.run_path(sys.argv[1])
    original_stdout = sys.__stdout__
    try:
        server['serve'](SimpleNamespace(VERSION='fixture', cmd_find=noisy), sys.argv[2])
    finally:
        sys.__stdout__ = original_stdout
        for child in children:
            child.terminate()
            child.wait(timeout=3)
    assert threading.active_count() == 1, 'capture drain thread survived the request'
    os.write(2, b'fd2 restored\n')
