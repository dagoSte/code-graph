"""Measure static indexing on generated multilingual and frozen real-source inputs."""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import platform
import resource
import runpy
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
SUFFIXES = ('.py', '.php', '.ts', '.go', '.dart', '.java', '.swift', '.sql')


def load(skill):
    spec = importlib.util.spec_from_file_location('cg_scale_engine', skill / 'scripts/cg.py')
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    return engine


def generated_file(suffix, unit, functions=40):
    text = '<?php\n' if suffix == '.php' else 'package bench\n\n' if suffix == '.go' else 'final class Bench' + str(unit) + ' {\n' if suffix == '.java' else ''
    for i in range(functions):
        name, previous = 'bench' + str(unit * functions + i), 'bench' + str(unit * functions + i - 1)
        call = 'next_value' if i == 0 else previous + '(next_value)'
        if suffix == '.py':
            text += 'def ' + name + '(value):\n    """Transform a benchmark value."""\n    next_value = value + 1\n    return ' + call + '\n\n'
        elif suffix == '.php':
            value = '$next_value' if i == 0 else previous + '($next_value)'
            text += 'function ' + name + '($value) {\n    $next_value = $value + 1;\n    return ' + value + ';\n}\n'
        elif suffix == '.ts':
            text += 'export function ' + name + '(value: number): number {\n    const next_value = value + 1;\n    return ' + call + ';\n}\n'
        elif suffix == '.go':
            text += 'func ' + name + '(value int) int {\n    next_value := value + 1\n    return ' + call + '\n}\n'
        elif suffix == '.dart':
            text += 'int ' + name + '(int value) {\n    final next_value = value + 1;\n    return ' + call + ';\n}\n'
        elif suffix == '.java':
            text += '    static int ' + name + '(int value) {\n        int next_value = value + 1;\n        return ' + call + ';\n    }\n'
        elif suffix == '.swift':
            text += 'private func ' + name + '(_ value: Int) -> Int {\n    let next_value = value + 1\n    return ' + call + '\n}\n'
        else:
            text += 'CREATE TABLE ' + name + ' (id INTEGER);\nINSERT INTO ' + name + ' VALUES (1);\nSELECT id FROM ' + name + ';\n'
    return text + ('}\n' if suffix == '.java' else '')


def generate(root, target_lines):
    lines, unit, functions = 0, 0, 40
    expected = dict(definitions=0, calls=0, resolved_calls=0)
    while lines < target_lines:
        suffix = SUFFIXES[unit % len(SUFFIXES)]
        path = root / suffix[1:] / ('unit' + str(unit) + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = generated_file(suffix, unit, functions)
        path.write_text(text)
        lines += text.count('\n')
        expected['definitions'] += functions * (3 if suffix == '.sql' else 2 if suffix == '.swift' else 1) + (suffix == '.java')
        expected['calls'] += 0 if suffix == '.sql' else functions - 1
        expected['resolved_calls'] += 0 if suffix == '.sql' else functions - 1
        unit += 1
    (root / 'go.mod').write_text('module example.invalid/scale\ngo 1.27\n')
    return dict(target_lines=target_lines, actual_source_lines=lines, expected=expected)


def freeze_source(engine, original, destination):
    copied, excluded, lines, size = {}, 0, 0, 0
    names = set(engine.list_files(str(original))) | set(engine.configuration_files(original))
    for name in sorted(names):
        path = engine.local_path(original, name)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > engine.MAX_BYTES:
            excluded += 1
            continue
        raw = path.read_bytes()
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        copied[name] = hashlib.sha256(raw).hexdigest()
        size += len(raw)
        if path.suffix in engine.LANG_BY_EXT:
            lines += raw.count(b'\n') + bool(raw and not raw.endswith(b'\n'))
    return dict(files=len(copied), excluded_from_copy=excluded, source_lines=lines, bytes=size,
                source_snapshot_sha256=hashlib.sha256(json.dumps(copied, sort_keys=True, separators=(',', ':')).encode()).hexdigest()), copied


def worker(skill, root, progress=None):
    engine = load(skill)
    Data = runpy.run_path(str(skill / 'scripts/project_data.py'))['ProjectData']
    search = engine.search_module()['search']
    parsed = [0]
    original = engine.Extractor.__init__

    def counted(instance, *args, **kwargs):
        parsed[0] += 1
        original(instance, *args, **kwargs)

    engine.Extractor.__init__ = counted
    result = dict(phases={}, source_execution=False, test_execution=False, completed=False)
    for name in ('full', 'noop', 'one_file_change'):
        if name == 'one_file_change':
            with Data(engine, root) as data:
                target = next(iter(sorted(data.files)))
            path = root / target
            comment = '\n# benchmark touch\n' if path.suffix == '.py' else '\n-- benchmark touch\n' if path.suffix == '.sql' else '\n// benchmark touch\n'
            path.write_bytes(path.read_bytes() + comment.encode())
        parsed[0] = 0
        started = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            engine.cmd_index(argparse.Namespace(root=str(root), full=name == 'full'))
        phase = dict(seconds=round(time.perf_counter()-started, 4), parsed_files=parsed[0])
        with Data(engine, root) as data:
            phase['content_revision'] = data.meta['content_revision']
            phase['counts'] = dict(files=len(data.files), definitions=data.db.execute("SELECT COUNT(*) FROM symbols WHERE kind!='module'").fetchone()[0],
                                  calls=data.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],
                                  resolved_calls=data.db.execute('SELECT COUNT(*) FROM edges').fetchone()[0],
                                  partial_files=int(data.meta.get('partial_files', '0')))
            phase['counts']['languages'] = dict(data.db.execute('SELECT lang,COUNT(*) FROM files GROUP BY lang'))
        result['phases'][name] = phase
        if progress is not None:
            progress(result)
    result['noop_content_revision_stable'] = result['phases']['full']['content_revision'] == result['phases']['noop']['content_revision']
    result['noop_parsed_nothing'] = result['phases']['noop']['parsed_files'] == 0
    result['incremental_parsed_one'] = result['phases']['one_file_change']['parsed_files'] == 1
    with Data(engine, root) as data:
        row = data.db.execute("SELECT name FROM symbols WHERE kind!='module' ORDER BY id LIMIT 1").fetchone()
        words = engine.search_module()['words'](row[0])[:3] if row else []
        result['query'] = ' '.join(words)
    result['queries'] = {}
    result['query_scope'] = 'fresh readonly reader open, source/config checks, search and close; warm OS cache; excludes HTTP'
    for mode in ('lexical', 'hybrid'):
        samples, statuses = [], []
        for _ in range(3):
            start = time.perf_counter()
            with Data(engine, root) as query_data:
                value = search(query_data, result['query'], mode=mode, limit=5, max_chars=12000)
            samples.append(round((time.perf_counter()-start)*1000, 3))
            statuses.append(value['status'])
        result['queries'][mode] = dict(milliseconds=samples, median_ms=statistics.median(samples), statuses=statuses)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result['peak_worker_rss_bytes'] = int(rss if sys.platform == 'darwin' else rss * 1024)
    result['peak_memory_scope'] = 'worker lifetime, includes engine loading, indexing and queries'
    result['engine_version'] = engine.VERSION
    result['sqlite_version'] = sqlite3.sqlite_version
    result['completed'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill-root', type=Path, default=REPO)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lines', nargs='+', type=int, default=[10000, 100000, 1000000])
    parser.add_argument('--real-project', nargs=2, action='append', metavar=('LABEL', 'PATH'), default=[])
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--worker-root', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_root is not None:
        def save(value):
            temporary = args.output.with_suffix('.partial')
            temporary.write_text(json.dumps(value, indent=2) + '\n')
            temporary.replace(args.output)
        save(worker(args.skill_root.resolve(), args.worker_root.resolve(), progress=save))
        return
    if any(n < 1000 or n > 2000000 for n in args.lines) or not 1 <= args.timeout <= 1800:
        parser.error('generated lines1000..2000000; timeout1..1800 seconds')
    skill = args.skill_root.resolve(strict=True)
    engine = load(skill)
    result = dict(format='codegraph-scale/1', source_execution=False, test_execution=False,
                  environment=dict(python=platform.python_version(), system=platform.system(), machine=platform.machine()),
                  engine_files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((skill / 'scripts').glob('*.py'))},
                  methodology='One full/noop/incremental index per frozen workload; three warm query samples. Constructor counting is instrumented. No cache flush or statistical significance claim.', workloads=[])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:
        if args.work_dir is None:
            folder = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='cg-scale-')))
        else:
            if args.work_dir.exists():
                parser.error('--work-dir must be new so earlier evidence is preserved')
            folder = args.work_dir
            folder.mkdir(parents=True)
        cases = [('generated-' + str(n), n) for n in args.lines] + [(label, Path(path).resolve(strict=True)) for label, path in args.real_project]
        for i, (label, source) in enumerate(cases):
            root = folder / str(i)
            root.mkdir()
            row = dict(label=label, kind='generated' if isinstance(source, int) else 'real_source')
            if isinstance(source, int):
                row.update(generate(root, source))
                snapshot, hashes = freeze_source(engine, root, folder / ('snapshot-' + str(i)))
                row['input'] = snapshot
            else:
                row['input'], hashes = freeze_source(engine, source, root)
            (folder / ('input-' + str(i) + '.json')).write_text(json.dumps(hashes, sort_keys=True, indent=2) + '\n')
            destination = folder / ('worker-' + str(i) + '.json')
            try:
                run = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--skill-root', str(skill), '--worker-root', str(root), '--output', str(destination)],
                                     capture_output=True, text=True, timeout=args.timeout)
                if run.returncode or not destination.is_file():
                    row['error'] = dict(exit_code=run.returncode, message=run.stderr[-2000:].replace(str(root), '[input]').replace(str(skill), '[skill]'))
                if destination.is_file():
                    row['measurement'] = json.loads(destination.read_text())
                    if 'expected' in row and row['measurement'].get('completed'):
                        actual = row['measurement']['phases']['full']['counts']
                        row['synthetic_counts_match'] = all(actual[k] == v for k, v in row['expected'].items())
            except subprocess.TimeoutExpired:
                row['error'] = dict(timeout_seconds=args.timeout)
                if destination.is_file():
                    row['measurement'] = json.loads(destination.read_text())
            result['workloads'].append(row)
            args.output.write_text(json.dumps(result, indent=2) + '\n')
            print(label, 'failed' if 'error' in row else 'measured', flush=True)


if __name__ == '__main__':
    main()
