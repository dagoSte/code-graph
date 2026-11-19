"""Pilot-only MCP: equal bounded literal text search in both evidence arms."""
import argparse
import contextlib
import fnmatch
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import time

MAX_BYTES = 1_000_000


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def text_search(root, pattern, path_glob=None, max_results=50):
    if not isinstance(pattern, str) or not 1 <= len(pattern) <= 256:
        raise ValueError('pattern must contain 1..256 literal characters')
    if path_glob is not None and (not isinstance(path_glob, str) or len(path_glob) > 256):
        raise ValueError('path_glob must be null or a glob of at most 256 characters')
    if type(max_results) is not int or not 1 <= max_results <= 50:
        raise ValueError('max_results must be 1..50')
    listed = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(root), 'ls-files', '-z'],
                            capture_output=True, check=True, timeout=2).stdout.decode().split('\0')
    result = dict(format='codegraph-pilot-text/1', items=[], files_scanned=0, omitted_files=0, truncated=False)
    deadline = time.monotonic() + 2
    for name in sorted(set(filter(None, listed))):
        if path_glob is not None and not fnmatch.fnmatchcase(name, path_glob):
            continue
        if time.monotonic() >= deadline:
            result.update(truncated=True, reason='time_limit')
            break
        parts = Path(name).parts
        if not parts or Path(name).is_absolute() or '..' in parts or parts[0] in ('.git', '.code-graph'):
            result['omitted_files'] += 1
            continue
        try:
            # Same fd confinement as the product reader, without changing its API.
            with contextlib.ExitStack() as stack:
                parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                stack.callback(os.close, parent)
                for component in parts[:-1]:
                    parent = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    stack.callback(os.close, parent)
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                stack.callback(os.close, fd)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                    result['omitted_files'] += 1
                    continue
                source = stack.enter_context(os.fdopen(fd, 'rb', closefd=False))
                data = source.read(MAX_BYTES + 1)
        except OSError:
            result['omitted_files'] += 1
            continue
        if len(data) > MAX_BYTES or b'\0' in data:
            result['omitted_files'] += 1
            continue
        result['files_scanned'] += 1
        for line, text in enumerate(data.decode('utf-8', errors='replace').splitlines(), 1):
            if time.monotonic() >= deadline:
                result.update(truncated=True, reason='time_limit')
                return result
            position = text.find(pattern)
            if position < 0:
                continue
            start = max(0, position - 60)
            item = dict(path=name, line=line, text=text[start:start + 160], snippet_start_column=start + 1)
            result['items'].append(item)
            # Count escaped content too, leaving room for the MCP envelope.
            if len(result['items']) > max_results or len(json.dumps(json.dumps(result))) > 24000:
                result['items'].pop()
                result.update(truncated=True, reason='result_or_response_limit')
                return result
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill-root', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--arm', choices=('source_only', 'graph_assisted'), required=True)
    args = parser.parse_args()
    skill, root = args.skill_root.resolve(strict=True), args.root.resolve(strict=True)
    engine = load(skill / 'scripts/cg.py', 'real_text_engine')
    server = load(skill / 'scripts/mcp_server.py', 'real_text_server')
    if args.arm == 'source_only':
        server.SPECS = {'cg_read': server.SPECS['cg_read']}
    server.SPECS['text_search'] = (
        'pilot_text_search', 'Search literal case-sensitive text in tracked files without an index. No regex; omissions and limits are explicit.',
        dict(pattern=dict(type='string', minLength=1, maxLength=256),
             path_glob=dict(type=['string', 'null']),
             max_results=dict(type='integer', default=50, minimum=1, maximum=50)), ['pattern'])
    engine.cmd_pilot_text_search = lambda query: print(json.dumps(text_search(root, query.pattern, query.path_glob, query.max_results)))
    server.serve(engine, str(root))


if __name__ == '__main__':
    main()
