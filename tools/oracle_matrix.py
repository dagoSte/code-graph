#!/usr/bin/env python3
"""Score static call/reference edges against recorded native oracle observations.

Default runs only the trusted cg engine. --rerun explicitly executes the bundled
Node/PHP/Go/Dart/Java/Swift programs in disposable directories; it never rewrites their baseline.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('oracle_edge_diff', Path(__file__).with_name('edge_diff.py'))
edge_diff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edge_diff)
RANK = {'wrong': 0, 'abstention': 1, 'ok': 2}


def source_hash(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def evidence_hash(case):
    """Bind the entire reviewed case, including observations and baseline."""
    return source_hash({k:v for k,v in case.items() if k != 'evidence_sha256'})


def native_targets(case):
    """Interpret the native protocols used by this bounded matrix."""
    output = case['native']['stdout']
    protocol = case['protocol']
    if protocol == 'js-polish' and case['language'] == 'js':
        if output == 'A\n':
            return [['a.js','polish']]
        if output in ('SHADOW\n','NOT_CALLABLE\n'):
            return []
    elif protocol == 'php-firstclass' and case['language'] == 'php' and output == '1\n':
        return [['lib.php','polish']]
    elif protocol == 'php-global-callable' and case['language'] == 'php':
        if output == '{"global":true,"other":false}\n':
            return [['lib.php','polish']]
        if output == '{"global":false,"other":true}\n':
            return []
    elif (protocol,case['language']) in (('go-targets','go'),('dart-targets','dart'),('java-targets','java'),('swift-targets','swift')):
        targets = json.loads(output)
        if isinstance(targets, list) and all(isinstance(item, list) and len(item) == 2
                and all(isinstance(value, str) and value for value in item) for item in targets):
            return targets
    raise ValueError('Unrecognized native protocol/output: '+case['id'])


def load_cases(directory):
    cases, ids = [], set()
    for path in sorted(Path(directory).glob('*.json')):
        data = json.loads(path.read_text())
        if data.get('schema') != 'oracle-matrix/1':
            raise ValueError(f'Unknown oracle schema: {path.name}')
        for case in data['cases']:
            if not case.get('id') or case['id'] in ids:
                raise ValueError('Missing or duplicate oracle ID')
            ids.add(case['id'])
            if case['language'] not in ('js', 'php', 'go', 'dart', 'java', 'swift') or case['baseline']['status'] not in RANK:
                raise ValueError('Invalid language or baseline')
            if not case['files'] or not case['required_indexed']:
                raise ValueError('Oracle requires source files and indexed witnesses')
            if not set(case['required_indexed']) <= set(case['files']):
                raise ValueError('Required indexed files must exist in the program')
            observe = case['observe']
            if not observe['relations'] or not set(observe['relations']) <= {'call','reference'} or not observe.get('sites') or not observe.get('target_files'):
                raise ValueError('Oracle requires a nonempty call/reference observation domain')
            if any(len(t)!=2 or t[0] not in case['files'] or not t[1]
                   for t in case['targets']):
                raise ValueError('Expected target must be in the observation domain and program')
            for target in observe['target_files']:
                rel = PurePosixPath(target)
                if rel.is_absolute() or '..' in rel.parts or '\\' in target or not rel.parts:
                    raise ValueError('Invalid target domain path')
            for site in observe['sites']:
                if (len(site) != 5 or site[0] not in case['required_indexed'] or not site[1]
                        or not isinstance(site[2],int) or not 1 <= site[2] <= len(case['files'][site[0]].splitlines())
                        or not site[3] or site[4] not in observe['relations']):
                    raise ValueError('Invalid observed site: '+case['id'])
            if case.get('evidence_sha256') != evidence_hash(case):
                raise ValueError('Oracle evidence changed without review: '+case['id'])
            if case['targets'] != native_targets(case):
                raise ValueError('Expected targets disagree with native observation: '+case['id'])
            for name, source in case['files'].items():
                rel = PurePosixPath(name)
                if rel.is_absolute() or '..' in rel.parts or '\\' in name or not rel.parts or not isinstance(source, str):
                    raise ValueError('Invalid oracle source path: '+name)
            if source_hash(case['files']) != case['source_sha256']:
                raise ValueError('Oracle source changed without native evidence: '+case['id'])
            if case['baseline']['status']=='wrong' and not case['baseline'].get('decision'):
                raise ValueError('Known wrong requires a deferral decision')
            if case['baseline']['status']=='wrong' and not case['baseline'].get('wrong'):
                raise ValueError('Known wrong requires exact wrong edges')
            if case['native']['exit'] != 0:
                raise ValueError('A failed native program is not an oracle')
            cases.append(case)
    if not cases:
        raise ValueError('Empty oracle matrix')
    return cases


def classify(rows, case):
    """Observe both site locations and target domain, even with wrong edge ownership."""
    observation = case['observe']
    if any(row[6] not in ('low','medium','high') for row in rows):
        raise ValueError('Unknown edge confidence')
    sites = set(map(tuple,observation['sites']))
    locations = {(s[0],s[2],s[3],s[4]) for s in sites}
    found = sorted(tuple(row) for row in rows if row[6] != 'low'
                   and ((row[0],row[2],row[3],row[7]) in locations
                        or (row[7] in observation['relations'] and row[4] in observation['target_files'])))
    expected = {tuple(target) for target in case['targets']}
    wrong = [row for row in found if (row[4], row[5]) not in expected
             or ((row[0],row[2],row[3],row[7]) in locations and (*row[:4],row[7]) not in sites)]
    matched = {(row[4],row[5]) for row in found if (*row[:4],row[7]) in sites}
    missing = sorted(expected - matched)
    status = 'wrong' if wrong else 'abstention' if missing else 'ok'
    return {'status': status, 'found': found, 'wrong': wrong, 'missing': missing}


def baseline_errors(result, case):
    baseline = case['baseline']
    if baseline['status'] == 'wrong':
        if result['status'] != 'wrong':
            return ['stale known wrong: update the baseline after reviewing the fix']
        if (sorted(map(tuple,baseline['wrong'])) != result['wrong']
                or sorted(map(tuple,baseline['missing'])) != result['missing']):
            return ['known wrong changed: review and update its exact edges and missing targets']
    elif RANK[result['status']] < RANK[baseline['status']]:
        return [f"regression: {baseline['status']} -> {result['status']}"]
    return []


def native_result(case, root, rerun, go=None, dart=None, java_home=None, swiftc=None, swift_sdk=None):
    recorded = case['native']
    if not rerun:
        return {'mode': 'recorded', **recorded}, []
    language=case['language']
    if language=='swift':executable=swiftc if swift_sdk else None
    elif language=='java':executable=str(Path(java_home)/'bin/java') if java_home else None
    elif language=='dart':executable=dart or shutil.which('dart')
    elif language=='go':executable=go or shutil.which('go')
    else:executable=shutil.which('node' if language=='js' else 'php')
    if executable is None:
        print('Warning: native runtime unavailable; recorded evidence for '+case['id'], file=sys.stderr)
        return {'mode': 'recorded-runtime-unavailable', **recorded}, []
    # A caller's Node preload must not contribute code/output to a native observation.
    env = {key:value for key,value in edge_diff.os.environ.items() if key not in ('NODE_OPTIONS','NODE_PATH')}
    if language=='swift':
        for key in list(env):
            if key.startswith(('SWIFT_','DYLD_')) or key in ('SDKROOT','TOOLCHAINS','SWIFTFLAGS','CPATH','C_INCLUDE_PATH','CPLUS_INCLUDE_PATH','OBJC_INCLUDE_PATH','LIBRARY_PATH','LD_LIBRARY_PATH'):
                env.pop(key,None)
        cache=Path(root).parent/('swift-'+Path(root).name);cache.mkdir(exist_ok=True)
        output=cache/'program';modules=cache/'modules'
        sources=sorted(str(Path(root)/name) for name in case['files'] if name.endswith('.swift'))
        compiled=subprocess.run([executable,'-swift-version','5','-module-name','CodeGraphOracle',
            '-sdk',str(swift_sdk),'-module-cache-path',str(modules),'-o',str(output),*sources],
            cwd=root,capture_output=True,text=True,timeout=120,env=env)
        if compiled.returncode:
            return dict(mode='rerun-compilation-failed',exit=compiled.returncode,stdout=compiled.stdout,stderr=compiled.stderr),['native Swift compilation failed']
        run=subprocess.run([str(output),*recorded['args']],cwd=root,capture_output=True,text=True,timeout=20,env=env)
        result=dict(mode='rerun',args=recorded['args'],exit=run.returncode,stdout=run.stdout,stderr=run.stderr)
        equal=all(result[k]==recorded[k] for k in ('exit','stdout','stderr'))
        return result,[] if equal else ['native observation changed: review targets before updating the oracle']
    if case['language'] == 'java':
        for key in ('CLASSPATH','JAVA_TOOL_OPTIONS','_JAVA_OPTIONS','JDK_JAVA_OPTIONS','JDK_JAVAC_OPTIONS','JAVA_OPTIONS'):
            env.pop(key,None)
        cache=Path(root).parent/('java-'+Path(root).name)
        output,empty=cache/'classes',cache/'empty'
        output.mkdir(parents=True,exist_ok=True);empty.mkdir(exist_ok=True)
        sources=sorted(str(Path(root)/name) for name in case['files'] if name.endswith('.java'))
        compile_run=subprocess.run([str(Path(java_home)/'bin/javac'),'-J-XX:-UsePerfData',
            '--release','17','-proc:none','-implicit:none','-classpath',str(empty),'-sourcepath',str(empty),
            '-d',str(output),*sources],cwd=root,capture_output=True,text=True,timeout=120,env=env)
        if compile_run.returncode:
            return dict(mode='rerun-compilation-failed',exit=compile_run.returncode,stdout=compile_run.stdout,stderr=compile_run.stderr),['native Java compilation failed']
        run=subprocess.run([executable,'-XX:-UsePerfData','-Djava.io.tmpdir='+str(cache),'-cp',str(output),*recorded['args']],
                           cwd=root,capture_output=True,text=True,timeout=20,env=env)
        result=dict(mode='rerun',args=recorded['args'],exit=run.returncode,stdout=run.stdout,stderr=run.stderr)
        equal=all(result[k]==recorded[k] for k in ('exit','stdout','stderr'))
        return result,[] if equal else ['native observation changed: review targets before updating the oracle']
    if case['language'] == 'dart':
        env.pop('DART_VM_OPTIONS',None)
        env.pop('DART_FLAGS',None)
        env.update(DART_SUPPRESS_ANALYTICS='true',PUB_CACHE=str(Path(root).parent/'dart-pub-cache'))
    if case['language'] == 'go':
        for key in ('GOFLAGS','GOEXPERIMENT','GOROOT','GOPATH','GOOS','GOARCH','GODEBUG'):
            env.pop(key, None)
        cache = Path(root).parent/'go-cache'
        modules = Path(root).parent/'go-modcache'
        cache.mkdir(exist_ok=True)
        modules.mkdir(exist_ok=True)
        env.update(GOENV='off', GOTOOLCHAIN='local', GOWORK='off', GOPROXY='off', GOSUMDB='off',
                   CGO_ENABLED='0', GO111MODULE='on', GOCACHE=str(cache), GOMODCACHE=str(modules))
    run = subprocess.run([executable, *recorded['args']], cwd=root, capture_output=True,
                         text=True, timeout=120 if case['language']=='go' else 10, env=env)
    result = {'mode': 'rerun', 'args': recorded['args'], 'exit': run.returncode,
              'stdout': run.stdout, 'stderr': run.stderr}
    equal = all(result[k] == recorded[k] for k in ('exit','stdout','stderr'))
    return result, [] if equal else ['native observation changed: review targets before updating the oracle']


def run_matrix(engine, cases, repo=REPO, rerun=False, timeout=600, go=None, dart=None, java_home=None, swiftc=None, swift_sdk=None):
    with tempfile.TemporaryDirectory(prefix='cg-oracle-') as temp:
        projects, native = [], {}
        for i, case in enumerate(cases):
            root = Path(temp)/str(i)
            root.mkdir()
            for name, source in case['files'].items():
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source)
            projects.append({'id': case['id'], 'path': str(root)})
        # Index before executing any native probe so writes cannot alter the measured input.
        with edge_diff.engine_source(engine, repo) as (cg, identity):
            graphs = edge_diff.measure(cg, projects, timeout)
        for case, project in zip(cases,projects):
            native[case['id']] = native_result(case, project['path'], rerun, go, dart, java_home, swiftc, swift_sdk)
    results, counts, errors = {}, defaultdict(Counter), []
    for case in cases:
        graph = graphs[case['id']]
        missing_files = set(case['required_indexed']) - set(graph['indexed_files'])
        result = classify(graph['rows'], case)
        result['native'], result['errors'] = native[case['id']]
        result['errors'] += baseline_errors(result, case)
        missing_sites = set(map(tuple,case['observe']['sites'])) - set(map(tuple,graph['sites']))
        if missing_sites:
            result['errors'].append('oracle sites not extracted: '+repr(sorted(missing_sites)))
        if missing_files:
            result['errors'].append('oracle files not indexed: '+', '.join(sorted(missing_files)))
        if not graph['indexed_files']:
            result['errors'].append('empty index is not a measured oracle')
        result['known_wrong'] = case['baseline']['status']=='wrong'
        counts[case['language']][result['status']] += 1
        if result['errors']:
            errors.append(case['id'])
        results[case['id']] = result
    return {'schema': 'oracle-results/1', 'engine': identity, 'counts': dict(counts),
            'errors': errors, 'cases': results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', default=str(REPO))
    parser.add_argument('--repo', type=Path, default=REPO)
    parser.add_argument('--matrix', type=Path, default=REPO/'tests/oracle')
    parser.add_argument('--language', choices=('js','php','go','dart','java','swift'))
    parser.add_argument('--go', help='trusted Go executable for --rerun (otherwise use PATH)')
    parser.add_argument('--dart', help='trusted Dart executable for --rerun (otherwise use PATH)')
    parser.add_argument('--java-home', help='trusted JDK directory for Java --rerun (required for native Java)')
    parser.add_argument('--swiftc', help='trusted compiler for native Swift --rerun')
    parser.add_argument('--swift-sdk', help='explicit trusted SDK for native Swift --rerun')
    parser.add_argument('--rerun', action='store_true')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.matrix)
        if args.language:
            cases = [c for c in cases if c['language']==args.language]
        if not cases:
            raise ValueError('No programs selected')
        report = run_matrix(args.engine, cases, args.repo, args.rerun, args.timeout, args.go, args.dart, args.java_home, args.swiftc, args.swift_sdk)
        if args.output:
            args.output.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps({'counts': report['counts'], 'errors': report['errors']}, indent=2))
        return int(bool(report['errors']))
    except (KeyError, TypeError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(2, str(error)+'\n')


if __name__=='__main__':
    sys.exit(main())
