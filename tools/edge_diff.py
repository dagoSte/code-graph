#!/usr/bin/env python3
"""Compare two trusted cg engines on identical, disposable project snapshots.

Python dependencies are those of code-graph. Project sources are never executed.
See tests/oracle/README.md for CLI examples and the normalized row contract.
"""
import argparse
from collections import Counter
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
FIELDS = ['caller_file', 'caller_symbol', 'line', 'called_name', 'target_file',
          'target_symbol', 'confidence', 'relation', 'caller_line', 'target_line',
          'receiver', 'receiver_name', 'nargs', 'reference_kind']
EXCLUDE = {'.git', '.code-graph', '__pycache__', '.venv', 'venv', 'node_modules', 'vendor'}


def git(repo, *args):
    return subprocess.run(['git', '-c', 'core.fsmonitor=false', '-C', str(repo), *args],
                          check=True, capture_output=True).stdout


def files_under(root):
    """Use Git's selected files when available; never follow a symlink."""
    root = Path(root).resolve()
    try:
        names = git(root, 'ls-files', '-z', '-co', '--exclude-standard').split(b'\0')
        candidates = [root / os.fsdecode(n) for n in names if n]
    except subprocess.CalledProcessError:
        candidates = []
        for directory, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDE and not (Path(directory)/d).is_symlink())
            candidates.extend(Path(directory)/f for f in sorted(files))
    result = []
    for path in sorted(set(candidates)):
        rel = path.relative_to(root)
        if any(part in EXCLUDE for part in rel.parts) or not path.is_file():
            continue
        if any((root / Path(*rel.parts[:i])).is_symlink() for i in range(1, len(rel.parts)+1)):
            continue
        result.append(rel)
    return result


def snapshot(source, destination):
    source, destination = Path(source).resolve(), Path(destination)
    if not source.is_dir() and not source.is_file():
        raise ValueError(f'Project directory does not exist: {source}')
    destination.mkdir(parents=True)
    manifest = {}
    if source.is_file():
        names, source = [Path(source.name)], source.parent
    else:
        names = files_under(source)
        # Project configuration participates even when ignored by Git.
        catalog = source/'.code-graph-catalog.json'
        if catalog.is_file() and not catalog.is_symlink() and not catalog.parent.is_symlink():
            names.append(Path('.code-graph-catalog.json'))
    for rel in names:
        raw = (source/rel).read_bytes()
        target = destination/rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        manifest[rel.as_posix()] = hashlib.sha256(raw).hexdigest()
    return manifest


@contextlib.contextmanager
def engine_source(value, repo=REPO):
    """Freeze scripts and data from a checkout or Git commit, without checkout/hooks."""
    with tempfile.TemporaryDirectory(prefix='cg-engine-') as temp:
        root = Path(temp)
        path = Path(value)
        if path.exists():
            scripts = path if path.is_dir() and (path/'cg.py').is_file() else path/'scripts'
            if path.is_file():
                scripts = path.parent
            if not (scripts/'cg.py').is_file():
                raise ValueError(f'No cg.py in engine: {value}')
            manifest = snapshot(scripts, root/'scripts')
            identity = {'source': str(path.resolve()), 'files': manifest}
        else:
            commit = git(repo, 'rev-parse', '--verify', str(value)+'^{commit}').decode().strip()
            manifest = {}
            for row in git(repo, 'ls-tree', '-r', '-z', commit, '--', 'scripts').split(b'\0'):
                if not row:
                    continue
                meta, name = row.split(b'\t', 1)
                mode, kind, oid = meta.split()
                if kind != b'blob' or mode not in (b'100644', b'100755'):
                    raise ValueError('Engine must contain only regular files: '+os.fsdecode(name))
                rel = os.fsdecode(name)
                raw = git(repo, 'cat-file', 'blob', oid.decode())
                target = root/rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                manifest[rel.removeprefix('scripts/')] = hashlib.sha256(raw).hexdigest()
            identity = {'commit': commit, 'files': manifest}
        if not (root/'scripts/cg.py').is_file():
            raise ValueError(f'No scripts/cg.py at {value}')
        identity['sha256'] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        yield root/'scripts/cg.py', identity


def read_rows(database):
    """References have medium confidence, matching cg.neighbors; old DBs may lack them."""
    with contextlib.closing(sqlite3.connect(f'{Path(database).as_uri()}?mode=ro', uri=True)) as db:
        rows = db.execute('''SELECT f.path,s.qname,c.line,c.name,g.path,t.qname,e.conf,'call',
            s.line,t.line,COALESCE(c.recv,''),COALESCE(c.recv_name,''),c.nargs,''
            FROM edges e JOIN calls c ON c.id=e.call_id JOIN files f ON f.id=c.file_id
            JOIN symbols s ON s.id=e.caller_id JOIN symbols t ON t.id=e.callee_id
            JOIN files g ON g.id=t.file_id''').fetchall()
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_refs'").fetchone():
            rows += db.execute('''SELECT f.path,COALESCE(s.qname,f.path),r.line,r.name,g.path,t.qname,'medium','reference',
                COALESCE(s.line,0),t.line,COALESCE(r.receiver,''),'',-1,r.kind
                FROM symbol_refs r JOIN files f ON f.id=r.file_id LEFT JOIN symbols s ON s.id=r.owner_id
                JOIN symbols t ON t.id=r.target_id JOIN files g ON g.id=t.file_id''').fetchall()
        indexed = [r[0] for r in db.execute('SELECT path FROM files ORDER BY path')]
    return sorted(rows), indexed


def read_sites(database):
    """Read extraction witnesses independently of resolution and edge ownership."""
    with contextlib.closing(sqlite3.connect(f'{Path(database).as_uri()}?mode=ro', uri=True)) as db:
        sites = db.execute('''SELECT f.path,COALESCE(s.qname,f.path),c.line,c.name,'call'
            FROM calls c JOIN files f ON f.id=c.file_id
            LEFT JOIN symbols s ON s.id=c.caller_id''').fetchall()
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='symbol_refs'").fetchone():
            sites += db.execute('''SELECT f.path,COALESCE(s.qname,f.path),r.line,r.name,'reference'
                FROM symbol_refs r JOIN files f ON f.id=r.file_id
                LEFT JOIN symbols s ON s.id=r.owner_id''').fetchall()
        meta = dict(db.execute('SELECT k,v FROM meta'))
        partial = json.loads(meta.get('partial_paths','[]'))
        if partial or int(meta.get('partial_files','0')):
            raise ValueError('Partial analysis: '+(', '.join(partial) or meta['partial_files']+' files'))
    return sorted(sites)


def worker(engine, jobs, output):
    # Only the explicitly selected engine is imported. Each version gets a new process.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(engine).parent))
    spec = importlib.util.spec_from_file_location('cg_measure_engine', engine)
    cg = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = cg
    spec.loader.exec_module(cg)
    result = []
    for project in json.loads(Path(jobs).read_text()):
        with tempfile.TemporaryDirectory(prefix='cg-project-', dir=Path(output).parent) as temp:
            root = Path(temp)/'project'
            shutil.copytree(project['path'], root)
            # Track exactly the snapshot, including dot-directories. No project or global
            # Git configuration/hooks/filters are loaded into this new repository.
            env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                   'GIT_CONFIG_COUNT': '0', 'GIT_TEMPLATE_DIR': ''}
            for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'):
                env.pop(key, None)
            for args in (['init', '-q'], ['add', '-f', '--all']):
                subprocess.run(['git', '-C', str(root), *args], env=env, check=True, capture_output=True)
            with contextlib.redirect_stdout(io.StringIO()):
                cg.cmd_index(argparse.Namespace(root=str(root), full=True))
            rows, indexed = read_rows(root/'.code-graph/graph.db')
            sites = read_sites(root/'.code-graph/graph.db')
            if not indexed:
                raise ValueError('Empty index for project: '+project['id'])
            result.append({'id': project['id'], 'rows': rows, 'indexed_files': indexed, 'sites': sites})
    Path(output).write_text(json.dumps(result))


def measure(engine, projects, timeout=600):
    with tempfile.TemporaryDirectory(prefix='cg-measure-') as temp:
        job, out = Path(temp)/'jobs.json', Path(temp)/'result.json'
        job.write_text(json.dumps(projects))
        run = subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve()),
                              '_index', str(engine), str(job), str(out)],
                             capture_output=True, text=True, timeout=timeout)
        if run.returncode:
            raise RuntimeError(f'Index worker failed ({run.returncode}): {run.stderr[-4000:]}')
        return {p['id']: p for p in json.loads(out.read_text())}


def compare(before, after):
    a, b = Counter(map(tuple, before)), Counter(map(tuple, after))
    return {'before': sum(a.values()), 'after': sum(b.values()),
            'added': sorted((b-a).elements()), 'removed': sorted((a-b).elements())}


def fixture_projects(root):
    """One group per fixture family, one project per immediate child directory."""
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f'Fixture directory does not exist: {root}')
    projects = []
    for family in sorted(root.iterdir()):
        if family.is_dir() and not family.is_symlink():
            children = [p for p in sorted(family.iterdir()) if p.is_dir() and p.name not in EXCLUDE and not p.is_symlink()]
            # A family with top-level sources is itself a complete project.
            if any(p.is_file() and p.suffix in ('.py','.php','.js','.ts','.tsx','.jsx','.mjs','.cjs','.mts') for p in family.iterdir()):
                children = [family]
            for project in children:
                projects.append({'group': family.name, 'id': project.relative_to(root).as_posix(), 'path': str(project)})
        elif family.is_file() and not family.is_symlink():
            projects.append({'group': 'standalone', 'id': family.name, 'path': str(family)})
    return projects


def run_comparison(before, after, projects, repo=REPO, timeout=600):
    if not projects or len({p['id'] for p in projects}) != len(projects):
        raise ValueError('Provide nonempty projects with unique IDs')
    with tempfile.TemporaryDirectory(prefix='cg-inputs-') as temp:
        frozen, manifests = [], {}
        for i, project in enumerate(projects):
            path = Path(temp)/str(i)
            manifests[project['id']] = snapshot(project['path'], path)
            if not manifests[project['id']]:
                raise ValueError('Empty selected input for project: '+project['id'])
            frozen.append({**project, 'path': str(path)})
        with engine_source(before, repo) as (engine, a):
            old = measure(engine, frozen, timeout)
        with engine_source(after, repo) as (engine, b):
            new = measure(engine, frozen, timeout)
    if a['sha256'] == b['sha256']:
        print('Warning: both engines have identical content', file=sys.stderr)
    details, groups = {}, {}
    for project in projects:
        key, group = project['id'], project['group']
        delta = compare(old[key]['rows'], new[key]['rows'])
        delta['indexed_before'] = old[key]['indexed_files']
        delta['indexed_after'] = new[key]['indexed_files']
        details[key] = {'group': group, **delta}
        total = groups.setdefault(group, Counter())
        total.update(projects=1, before=delta['before'], after=delta['after'],
                     added=len(delta['added']), removed=len(delta['removed']))
        for relation in ('call', 'reference'):
            total[relation+'_before'] += sum(r[7]==relation for r in old[key]['rows'])
            total[relation+'_after'] += sum(r[7]==relation for r in new[key]['rows'])
            total[relation+'_added'] += sum(r[7]==relation for r in delta['added'])
            total[relation+'_removed'] += sum(r[7]==relation for r in delta['removed'])
    return {'schema': 'edge-diff/2', 'fields': FIELDS, 'engines': {'before': a, 'after': b},
            'inputs': manifests, 'groups': groups, 'projects': details}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', required=True)
    parser.add_argument('--after', required=True)
    parser.add_argument('--repo', type=Path, default=REPO, help='Git repository for commit inputs')
    parser.add_argument('--fixtures', type=Path, default=REPO/'tests/fixtures')
    parser.add_argument('--no-fixtures', action='store_true')
    parser.add_argument('--project', action='append', default=[], metavar='GROUP=PATH')
    parser.add_argument('--project-list', type=Path, help='JSON array of {id,group,path}')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--output', type=Path, help='Full JSON; stdout always prints group counts')
    args = parser.parse_args(argv)
    try:
        projects = [] if args.no_fixtures else fixture_projects(args.fixtures)
        for item in args.project:
            group, path = item.split('=', 1)
            projects.append({'id': group+'/'+Path(path).name, 'group': group, 'path': path})
        if args.project_list:
            projects.extend(json.loads(args.project_list.read_text()))
        report = run_comparison(args.before, args.after, projects, args.repo, args.timeout)
        if args.output:
            args.output.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report['groups'] if args.output else report, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(2, str(error)+'\n')


if __name__ == '__main__':
    if sys.argv[1:2] == ['_index']:
        worker(*sys.argv[2:])
    else:
        sys.exit(main())
