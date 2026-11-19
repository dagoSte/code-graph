"""Read-only Git comparisons and verification proposals. No project commands run."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import runpy
import subprocess
import tempfile


def git(root, *args, input=None):
    result = subprocess.run(['git','-c','core.fsmonitor=false','-c','core.quotepath=false','-C',str(root),*args], input=input, capture_output=True, check=True)
    return result.stdout


def snapshot(root, ref=None):
    """Return regular blobs by name; symlinks/submodules never become source files."""
    if ref is not None:
        commit=git(root,'rev-parse','--verify',ref+'^{commit}').decode().strip()
        rows=git(root,'ls-tree','-rz',commit).split(b'\0')
        entries=[]
        for row in rows:
            if not row:continue
            metadata,name=row.split(b'\t',1);mode,kind,oid=metadata.split()
            if kind==b'blob' and mode in (b'100644',b'100755'): entries.append((name.decode('utf-8'),oid))
    else:
        commit='git-index'
        rows=git(root,'ls-files','-s','-z').split(b'\0');entries=[]
        for row in rows:
            if not row:continue
            metadata,name=row.split(b'\t',1);mode,oid,stage=metadata.split()
            if stage != b'0': raise ValueError('Git index has conflicts: staged comparison is unavailable.')
            if mode in (b'100644',b'100755'):entries.append((name.decode('utf-8'),oid))
    if len(entries)>100000:raise ValueError('Snapshot exceeds 100000 files.')
    descriptions=git(root,'cat-file','--batch-check',input=b''.join(oid+b'\n' for _,oid in entries)).splitlines()
    selected=[];total_size=0
    for entry,description in zip(entries,descriptions):
        size=int(description.split()[-1])
        if size>1000000:continue
        total_size+=size
        if total_size>100000000:raise ValueError('Snapshot exceeds 100 MB: narrow the repository.')
        selected.append(entry)
    entries=selected
    raw=git(root,'cat-file','--batch',input=b''.join(oid+b'\n' for _,oid in entries))
    offset,files,total=0,{},0
    for name,oid in entries:
        stop=raw.index(b'\n',offset);header=raw[offset:stop].split();size=int(header[-1]);offset=stop+1
        blob=raw[offset:offset+size];offset+=size+1
        if size>1000000:continue
        if total+size>100000000:raise ValueError('Snapshot exceeds 100 MB: narrow the repository.')
        path=Path(name)
        if path.is_absolute() or '..' in path.parts:raise ValueError('Non-local Git path.')
        files[name]=blob;total+=size
    return commit, files


def write_snapshot(folder, files):
    for name,raw in files.items():
        path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)


def seeded_index(engine, root, folder):
    """Index a snapshot folder starting from a copy of the project's index: the incremental index then
    re-parses only files whose content differs (the diff), instead of the whole project. A copy that
    does not fit (other engine, catalog or parsers) is rebuilt by the index itself, as before."""
    source=Path(root)/'.code-graph'/'graph.db'
    if source.is_file():
        import sqlite3
        (folder/'.code-graph').mkdir(exist_ok=True)
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(folder/'.code-graph'/'graph.db') as dst:
            src.backup(dst)
    with contextlib.redirect_stdout(io.StringIO()):
        engine.cmd_index(type('Args',(),dict(root=str(folder),full=False))())


def check_all(project):
    return [item for item in runpy.run_path(str(Path(__file__).with_name('checks.py')))['findings'](project) if item['category'] not in ('analysis_limit','diagnostic')]


def summary_callers(project, verification):
    # Resolve IDs while this revision is open; baseline IDs belong to another graph.
    ids={step['caller'] for item in verification.get('tests',[]) if item.get('basis')!='baseline'
         for step in item.get('chain',[])[1:3]}
    return {sid:project._symbol(sid)['name'] for sid in ids}


def plan(project, roots, confidence='medium', depth=3, with_targets=False, runner_root=None):
    commands = runpy.run_path(str(Path(__file__).with_name('test_commands.py')))
    detector = commands['Commands'](project, runner_root)
    tests, support, unknown = {}, {}, []
    test_targets={}
    truncated=False;frontier=0
    for sid in roots:
        target=project.selected_detail(sid,confidence,[])
        if target['role']=='test_case' or commands['python_test_case'](target) or commands['js_test_file'](target):
            tests[sid]=dict(symbol=project._symbol(sid),distance=0,chain=[],
                           role='test_file' if commands['js_test_file'](target) else 'test_case');test_targets.setdefault(sid,set()).add(sid)
        impact=project.impact(sid,confidence,depth)
        truncated=truncated or impact['truncated'];frontier+=sum(item['distance']==depth for item in impact['items'])
        for item in impact['items']:
            role=item['role']
            if role=='test_case' or commands['python_test_case'](item['symbol']) or commands['js_test_file'](item['symbol']):
                item=dict(item,role='test_file' if commands['js_test_file'](item['symbol']) else 'test_case')
                test_targets.setdefault(item['symbol']['id'],set()).add(sid)
                previous=tests.get(item['symbol']['id'])
                if previous is None or previous['distance']>item['distance']: tests[item['symbol']['id']]=item
            elif role=='test_support':support[item['symbol']['id']]=item['symbol']
        unknown.extend(item for item in project.call_diagnostics(sid,confidence) if item['status']!='resolved')
    ordered=[];js_files={}
    for item in sorted(tests.values(),key=lambda item:(item['distance'],item['symbol']['file'],item['symbol']['line'])):
        if item['role']=='test_file' and item['symbol']['file'] in js_files:
            test_targets[js_files[item['symbol']['file']]].update(test_targets[item['symbol']['id']])
            continue
        if item['role']=='test_file':js_files[item['symbol']['file']]=item['symbol']['id']
        ordered.append(item)
    for item in ordered:
        if with_targets:item['verified_for']=sorted(test_targets[item['symbol']['id']])
        item.update(priority='direct' if item['distance']==1 else 'transitive',
                    reason='Static calls to the symbol or its callers.')
        item.update(detector.test(item['symbol']))
    pool=runpy.run_path(str(Path(__file__).with_name('evidence.py')))['Snippets'](project)
    pool.chain(ordered)
    return dict(mode='proposal_only', execution_performed=False, tests=ordered, snippets=pool.items, support=list(support.values()),
                **commands['aggregate'](ordered),
                depth_limit=depth,node_limit_reached=truncated,frontier_count=frontier,
                unknown_calls=unknown, broader_verification_required=bool(unknown) or truncated or bool(frontier),
                limitations=['Commands are static proposals; use the project environment and confirm discovery, fixtures and integrations.',
                             'File commands may run other cases in that file; a combined Go selector may match in multiple packages.',
                             'The aggregate command covers all selected tests, including evidence omitted by the response budget.'])


def compare(current, baseline):
    if baseline is None:return dict(status='unavailable',reason='Baseline missing or incompatible.',uncompared=current,new=[],persistent=[],resolved=[])
    def group(items):
        result={}
        for item in items:result.setdefault(item['fingerprint'],[]).append(item)
        return result
    before,after=group(baseline),group(current)
    result=dict(status='comparable',new=[],persistent=[],resolved=[],uncompared=[])
    for key in sorted(set(before)|set(after)):
        left,right=before.get(key,[]),after.get(key,[])
        if len(left)>1 or len(right)>1:result['uncompared'].extend(right or left)
        elif left and right:result['persistent'].append(right[0])
        elif right:result['new'].append(right[0])
        else:result['resolved'].append(left[0])
    return result


def changed_ranges(diff):
    import ast
    def unquote(value):
        if value.startswith(chr(34)):
            try:return ast.literal_eval(value)
            except (SyntaxError,ValueError):return value
        return value
    changes=[];current=None;old=None
    for line in diff.decode('utf-8','replace').splitlines():
        if line.startswith('+++ '):
            name=unquote(line[4:]);current=old if name=='/dev/null' else name.removeprefix('b/')
        elif line.startswith('--- ') and line[4:]!='/dev/null':
            old=unquote(line[4:]).removeprefix('a/')
            if current is None: current=old
        elif line.startswith('@@'):
            match=re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',line)
            if match:changes.append(dict(file=current,start=int(match[3]),count=int(match[4] or 1),old_start=int(match[1]),old_count=int(match[2] or 1)))
    return changes


def bounded(result, max_chars):
    if max_chars<128:raise ValueError('max_chars must be at least 128.')
    # Snippets are filtered from the full set each time: bisection tries longer prefixes after shorter
    # ones, so a test that comes back must bring its snippets back too.
    all_snippets=dict(result.get('verification',{}).get('snippets',{}))
    def text():
        verification=result.get('verification',{})
        used={step['snippet_ref'] for item in verification.get('tests',[]) for step in item.get('chain',[]) if 'snippet_ref' in step}
        verification['snippets']={key:value for key,value in all_snippets.items() if key in used}
        return json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    original=len(text());omitted=0
    # Keep summaries and provenance; remove only whole evidence records.
    for collection in [result.get('comparison',{}).get('persistent',[]),result.get('comparison',{}).get('uncompared',[]),result.get('comparison',{}).get('resolved',[]),result.get('verification',{}).get('support',[]),result.get('verification',{}).get('unknown_calls',[]),result.get('symbols',[]),result.get('verification',{}).get('tests',[]),result.get('comparison',{}).get('new',[]),result.get('changes',[])]:
        if not collection or len(text())+200<=max_chars:continue
        # Fewest records to drop from the end: size is monotone in the kept prefix, so bisect it
        # (one serialisation per step instead of one per removed record).
        records=list(collection);low,high=0,len(records)
        while low<high:
            keep=(low+high+1)//2
            collection[:]=records[:keep]
            if len(text())+200<=max_chars:low=keep
            else:high=keep-1
        collection[:]=records[:low];omitted+=len(records)-low
    result['output']=dict(status='partial' if omitted else 'complete',omitted_records=omitted,max_chars=max_chars,original_chars=original,
                          continuation='Increase --max-chars to include all evidence.')
    if len(text())+1>max_chars:return dict(status='budget_insufficient',max_chars=max_chars)
    return result


def validate_review_index(project):
    """A diff's current coordinates must refer to the exact indexed source inventory."""
    engine=project.cg
    if engine.analysis_inputs(project.root)[2] != project.meta.get('analysis_fingerprint'):
        raise ValueError('Engine changed after indexing. Run index before review.')
    seen=set()
    for name in engine.list_files(project.root):
        with engine.local_path(project.root,name).open('rb') as source:
            raw=source.read(engine.MAX_BYTES+1)
        if len(raw)>engine.MAX_BYTES or name.endswith('.min.js') or (raw.count(b'\n')<3 and len(raw)>20000):
            continue
        seen.add(name)
        if name not in project.files or hashlib.sha1(raw).hexdigest()!=project.files[name]['sha']:
            raise ValueError('Source changed after indexing: '+name+'. Run index before review.')
    if seen!=set(project.files):
        raise ValueError('Source inventory changed after indexing. Run index before review.')


def review(engine,args):
    ProjectData=runpy.run_path(str(Path(__file__).with_name('project_data.py')))['ProjectData']
    root=Path(args.root).resolve()
    with ProjectData(engine,root) as project:
        if not args.staged:validate_review_index(project)
        current=check_all(project)
        baseline=None;baseline_info=dict(status='absent');baseline_plans=[]
        base_commit=None
        if args.base:
            try:
                base_commit,files=snapshot(root,args.base)
                with tempfile.TemporaryDirectory(prefix='cg-review-base-') as folder:
                    base=Path(folder);write_snapshot(base,files)
                    seeded_index(engine,root,base)
                    with ProjectData(engine,base) as old:
                        baseline=check_all(old)
                        base_diff=git(root,'diff','--no-ext-diff','--no-textconv','--no-renames','--unified=0',*(['--cached'] if args.staged else []),base_commit,'--')
                        for changed in changed_ranges(base_diff):
                            if changed['count']!=0:continue
                            roots=[row[0] for row in old.db.execute('SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.path=? AND s.kind!="module" AND s.line<=? AND s.end_line>=?',(changed['file'],changed['old_start']+max(1,changed['old_count'])-1,changed['old_start']))]
                            if roots:baseline_plans.append(dict(file=changed['file'],content_revision=old.meta.get('content_revision',old.meta['revision']),plan=plan(old,roots,args.min_conf,args.depth,runner_root=root)))
                baseline_info=dict(status='available',commit=base_commit,engine_version=engine.VERSION)
            except (subprocess.SubprocessError,OSError,ValueError) as error:
                baseline_info=dict(status='unavailable',reason=str(error))
        if args.baseline:
            path=engine.local_path(root,args.baseline)
            if path.stat().st_size>5000000:raise ValueError('Baseline exceeds 5 MB.')
            saved=json.loads(path.read_text())
            if saved.get('format')=='codegraph-baseline/1' and saved.get('engine_version')==engine.VERSION and saved.get('project')==hashlib.sha256(str(root).encode()).hexdigest():
                baseline=saved['findings'];baseline_info=dict(status='available',content_revision=saved['content_revision'])
            else:baseline_info=dict(status='incompatible');baseline=None
        ranges=[];scope='working_tree';workspace_revision=project.meta['content_revision']
        try:
            diff=git(root,'diff','--no-ext-diff','--no-textconv','--no-renames','--unified=0',*(['--cached'] if args.staged else []),base_commit or 'HEAD','--')
        except subprocess.CalledProcessError:
            diff=b''
        if diff or args.staged:

            ranges=changed_ranges(diff)
            if args.staged:
                scope='git_index'
                _,stage_files=snapshot(root)
                # Analyze the actual staged snapshot, not coordinates from the working tree.
                with tempfile.TemporaryDirectory(prefix='cg-review-staged-') as folder:
                    stage=Path(folder);write_snapshot(stage,stage_files)
                    seeded_index(engine,root,stage)
                    with ProjectData(engine,stage) as staged:
                        staged_findings=check_all(staged)
                        result=assemble(staged,args,staged_findings,baseline,baseline_info,ranges,scope,workspace_revision,baseline_plans)
                        if args.save_baseline:save_baseline(engine,root,args,staged_findings,staged.meta['content_revision'])
                        return result
            else:
                for name in git(root,'ls-files','--others','--exclude-standard','-z').decode().split('\0'):
                    if name in project.files:ranges.append(dict(file=name,start=1,count=project.files[name]['loc'],old_start=0,old_count=0))
        if not args.staged and not diff:
            try:
                for name in git(root,'ls-files','--others','--exclude-standard','-z').decode().split('\0'):
                    if name in project.files:ranges.append(dict(file=name,start=1,count=project.files[name]['loc'],old_start=0,old_count=0))
            except subprocess.CalledProcessError:pass
        result=assemble(project,args,current,baseline,baseline_info,ranges,scope,workspace_revision,baseline_plans)
        if args.save_baseline:save_baseline(engine,root,args,current,project.meta['content_revision'])
        return result


def save_baseline(engine,root,args,current,content_revision):
    destination=engine.local_path(root,args.save_baseline)
    if destination.suffix!='.json' or destination.parent!=engine.local_path(root,'.code-graph'):
        raise ValueError('Save the baseline as .code-graph/<name>.json.')
    if destination.exists() and not args.force:raise ValueError('Baseline exists: use --force to replace it.')
    content=dict(format='codegraph-baseline/1',engine_version=engine.VERSION,project=hashlib.sha256(str(root).encode()).hexdigest(),content_revision=content_revision,scope='git_index' if args.staged else 'working_tree',findings=current)
    with tempfile.NamedTemporaryFile(dir=destination.parent,delete=False) as handle:
        temp=Path(handle.name);handle.write(json.dumps(content,ensure_ascii=False,sort_keys=True).encode())
    try:temp.replace(destination)
    finally:temp.unlink(missing_ok=True)


def assemble(project,args,current,baseline,baseline_info,ranges,scope,workspace_revision,baseline_plans=()):
    roots=set();symbols=[]
    for change in ranges:
        if change['file'] not in project.files:continue
        start=change['start'];end=start+max(1,change['count'])-1
        for row in project.db.execute('SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.path=? AND s.kind!="module" AND s.line<=? AND s.end_line>=?',(change['file'],end,start)):
            if row[0] not in roots:
                node=project._symbol(row[0]);node['identity']=dict(file=node['file'],language=node['language'],scope_name=node['name'],kind=node['kind'])
                # No automatic rename matching or signature/behavior claims without an oracle.
                node['change_kind']='overlapping_diff_unknown_semantics';symbols.append(node);roots.add(row[0])
    # Commands run in the reviewed checkout, also when the analysed tree is a temporary snapshot.
    verification=plan(project,sorted(roots),args.min_conf,args.depth,runner_root=Path(args.root).resolve())
    verification['baseline_plans']=list(baseline_plans)
    for saved in baseline_plans:
        verification['snippets'].update(saved['plan']['snippets'])
        for test in saved['plan']['tests']:
            if not any(item['symbol']['file']==test['symbol']['file'] and item['symbol']['name']==test['symbol']['name'] for item in verification['tests']):
                verification['tests'].append(dict(test,basis='baseline',content_revision=saved['content_revision'],
                    command=None,command_reason='Test selected from the baseline; confirm it exists in the current tree.'))
    verification.update(runpy.run_path(str(Path(__file__).with_name('test_commands.py')))['aggregate'](verification['tests']))
    if getattr(args,'_summary_callers',None) is not None:
        args._summary_callers.update(summary_callers(project,verification))
    if any(change['count']==0 for change in ranges):verification['broader_verification_required']=True
    comparison=compare(current,baseline)
    summary={key:len(comparison.get(key,[])) for key in ('new','persistent','resolved','uncompared')}
    result=dict(format='codegraph-review/1',engine_version=project.cg.VERSION,content_revision=project.meta['content_revision'],
                workspace_revision=workspace_revision,scope=scope,baseline=baseline_info,changes=ranges,symbols=symbols,
                comparison=comparison,summary=summary,verification=verification,
                limitations=['Renames are not paired automatically; deleted files come from the diff, without inventing current symbols.',
                             'Signature/behavior classification undetermined: read the diff.'])
    if getattr(args,'summary',False) and (getattr(args,'json',False) or getattr(args,'_mcp_json',False)):
        return summarize(result,args)
    return bounded(result,args.max_chars)


def summarize(result,args):
    """review --summary --json: base, revision, counts, new findings, proposed tests with their commands and the
    limits, built from the complete result. Counts stay whole; only test and new-finding records leave the budget."""
    comparison,verification=result['comparison'],result['verification']
    limits=list(result['limitations'])+list(verification['limitations'])
    if comparison['status']!='comparable':
        reason=(result['baseline'].get('reason') or comparison.get('reason') or 'no base').rstrip('.')
        limits.insert(0,f'Comparison unavailable: {reason}; no finding is reported as new.')
    if result['summary']['uncompared']:
        limits.append(f"{result['summary']['uncompared']} findings could not be compared (duplicate identities or missing baseline).")
    if verification['broader_verification_required']:
        limits.append('Broader verification required: unresolved calls, the depth frontier, omitted nodes or deletions.')
    value=dict(format='codegraph-review-summary/1',engine_version=result['engine_version'],
               base=dict(result['baseline'],ref=getattr(args,'base',None),**({'file':args.baseline} if getattr(args,'baseline',None) else {})),
               revision=dict(content_revision=result['content_revision'],scope=result['scope']),counts=result['summary'],
               new=[dict(rule_id=item['rule_id'],message=item['message'],file=item['file'],line=item['line']) for item in comparison['new']],
               tests=[dict(name=item['symbol']['name'],file=item['symbol']['file'],line=item['symbol']['line'],distance=item['distance'],
                           basis=item.get('basis','current'),command=item.get('command'),command_reason=item.get('command_reason'))
                      for item in verification['tests']],
               command=verification.get('command'),command_reason=verification.get('command_reason'),cwd=verification.get('cwd','.'),
               limitations=limits)
    text=lambda:json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    omitted=dict(tests=0,new=0)
    value['output']=dict(status='complete',omitted_records=omitted,max_chars=args.max_chars,
                         continuation='Increase --max-chars for every record.')
    for key in ('tests','new'):  # tests leave first; fewest records dropped, found by bisection
        if not value[key] or len(text())+1<=args.max_chars:continue
        records=list(value[key]);low,high=0,len(records)
        while low<high:
            keep=(low+high+1)//2;value[key]=records[:keep]
            if len(text())+1<=args.max_chars:low=keep
            else:high=keep-1
        value[key]=records[:low];omitted[key]=len(records)-low
    value['output']['status']='partial' if any(omitted.values()) else 'complete'
    if len(text())+1>args.max_chars:return dict(status='budget_insufficient',max_chars=args.max_chars)
    return value
