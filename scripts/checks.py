"""Explainable static candidates. Never execute or import analyzed source."""
import ast
import hashlib
import inspect
import json
from pathlib import Path
import re


def findings(project):
    cg, db = project.cg, project.db
    results, trees, parents = [], {}, {}
    function_nodes, call_nodes = {}, {}
    partial = set(json.loads(project.meta.get('partial_paths', '[]')))

    def python_tree(path):
        if path not in trees:
            try:
                tree = ast.parse('\n'.join(project._read(path)))
            except (SyntaxError, ValueError):
                tree = None
            trees[path] = tree
            if tree:
                parents[path] = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
                for node in ast.walk(tree):
                    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):function_nodes.setdefault((path,node.lineno),[]).append(node)
                    elif isinstance(node,ast.Call):call_nodes.setdefault((path,node.lineno,getattr(node.func,'attr',None) or getattr(node.func,'id',None)),[]).append(node)
        return trees[path]

    def add(rule, category, path, line, message, evidence=(), assumptions=(), uncertainties=(), reason='unknown', severity='warning', identity_message=None):
        text = project._read(path)[line-1].strip() if path in project.files and line > 0 else ''
        # Line movement does not change this identity; duplicate occurrences remain ambiguous.
        # Keep saved-baseline identities stable when the displayed wording is translated.
        fingerprint = hashlib.sha256(json.dumps([rule, path, text, message if identity_message is None else identity_message], sort_keys=True).encode()).hexdigest()
        results.append(dict(rule_id=rule, category=category, severity=severity, file=path, line=line, message=message,
                            evidence=list(evidence) or [dict(file=path, line=line, text=text)], assumptions=list(assumptions),
                            uncertainties=list(uncertainties) + (['partial_syntax'] if path in partial else []),
                            resolution_reason=reason, fingerprint=fingerprint))

    def symbol(sid): return project._symbol(sid)

    def signature(fn, bound):
        args, default = fn.args, object()
        pos = args.posonlyargs + args.args
        optional_start = len(pos)-len(args.defaults)
        params = [inspect.Parameter(arg.arg, inspect.Parameter.POSITIONAL_ONLY if i<len(args.posonlyargs) else inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                    default=default if i>=optional_start else inspect.Parameter.empty) for i,arg in enumerate(pos)]
        if bound and params: params=params[1:]
        if args.vararg: params.append(inspect.Parameter(args.vararg.arg, inspect.Parameter.VAR_POSITIONAL))
        params += [inspect.Parameter(arg.arg, inspect.Parameter.KEYWORD_ONLY, default=default if value is not None else inspect.Parameter.empty)
                   for arg,value in zip(args.kwonlyargs,args.kw_defaults)]
        if args.kwarg: params.append(inspect.Parameter(args.kwarg.arg, inspect.Parameter.VAR_KEYWORD))
        return inspect.Signature(params)

    rows = db.execute('''SELECT c.*,f.path,f.lang,e.callee_id,s.line AS target_line,s.file_id AS target_file,s.kind,s.pmin,s.pmax
        FROM edges e JOIN calls c ON c.id=e.call_id JOIN files f ON f.id=c.file_id JOIN symbols s ON s.id=e.callee_id
        WHERE e.conf='high' AND f.lang IN ('python','php') ORDER BY f.path,c.line,c.id,e.callee_id''').fetchall()
    for row in rows:
        if row['splat']:
            continue
        if db.execute('SELECT COUNT(*) FROM edges WHERE call_id=?', (row['id'],)).fetchone()[0] != 1:
            continue
        target = symbol(row['callee_id'])
        if row['lang'] == 'python':
            tree, target_tree = python_tree(row['path']), python_tree(target['file'])
            if not tree or not target_tree: continue
            functions = function_nodes.get((target['file'],target['line']),[])
            calls = call_nodes.get((row['path'],row['line'],row['name']),[])
            if len(functions)!=1 or len(calls)!=1: continue
            fn, call = functions[0], calls[0]
            if any(isinstance(arg,ast.Starred) for arg in call.args) or any(kw.arg is None for kw in call.keywords): continue
            decorators = [ast.unparse(node) for node in fn.decorator_list]
            unknown_decorators = [name for name in decorators if name not in ('staticmethod','classmethod','overload','typing.overload')]
            if unknown_decorators or any(name.endswith('overload') for name in decorators): continue
            method = isinstance(parents[target['file']].get(fn),ast.ClassDef)
            constructor = method and fn.name in ('__init__','__new__') and row['name'] != fn.name
            bound = method and ('classmethod' in decorators or ('staticmethod' not in decorators and (row['recv']!='class' or constructor)))
            try:
                sig = signature(fn,bound)
                sig.bind(*[None]*len(call.args), **{kw.arg:None for kw in call.keywords})
            except TypeError as error:
                add('arity','supported_violation',row['path'],row['line'],f"arity: passes {row['nargs']} arguments to {target['name']}: {error}",
                    evidence=[dict(file=target['file'],line=target['line'],signature=target['signature']),dict(file=row['path'],line=row['line'],text=ast.unparse(call))],
                    assumptions=['The static definition and binding are the ones executed.'], reason='python_signature_binding',
                    identity_message=f"arità: passa {row['nargs']} argomenti a {target['name']}: {error}")
            except ValueError:
                pass
        elif row['pmin'] is not None and row['nargs'] < row['pmin']:
            add('arity','supported_violation',row['path'],row['line'],f"arity: passes {row['nargs']} arguments to {target['name']} (minimum {row['pmin']})",
                evidence=[dict(file=target['file'],line=target['line'],signature=target['signature'])], assumptions=['Normal PHP invocation without dynamic binding.'], reason='php_required_parameters',
                identity_message=f"arità: passa {row['nargs']} argomenti a {target['name']} (minimo {row['pmin']})")

    classes = [row[0] for row in db.execute("SELECT id FROM symbols WHERE kind IN ('class','trait','interface','enum')")]
    ancestors = {sid: cg.ancestry(db,sid) for sid in classes}
    symbol_rows={row['id']:dict(row) for row in db.execute('SELECT id,parent_id,kind,name,file_id FROM symbols')}
    class_for={}
    for sid in symbol_rows:
        owner=sid
        while owner is not None and symbol_rows[owner]['kind'] not in cg.CLASSLIKE:owner=symbol_rows[owner]['parent_id']
        class_for[sid]=owner
    methods={sid:set() for sid in classes}
    for item in symbol_rows.values():
        if item['parent_id'] in methods:methods[item['parent_id']].add(item['name'])
    inherited_methods={sid:set().union(*(methods.get(base,set()) for base in ancestors[sid][0])) for sid in classes}
    consumers_of={sid:[consumer for consumer in classes if consumer!=sid and sid in ancestors[consumer][0]] for sid in classes}
    enum_methods={}
    def php_enum_builtins(owner):
        """PHP 8.1: every enum has cases(); only a backed enum (enum X: string|int) also has from() and tryFrom()."""
        if owner not in enum_methods:
            node=symbol(owner);builtins=set()
            if symbol_rows[owner]['kind']=='enum' and node['language']=='php':
                builtins={'cases'}
                tree=cg.get_parser_for('php').parse('\n'.join(project._read(node['file'])).encode())
                found,pending=[],[tree.root_node]
                while pending:
                    item=pending.pop();pending.extend(item.children)
                    name=item.child_by_field_name('name') if item.type=='enum_declaration' else None
                    if name is not None and name.text.decode('utf-8','replace')==symbol_rows[owner]['name']:found.append(item)
                if len(found)>1:found=[item for item in found if item.start_point[0]+1==node['line']]
                # The backing type is a direct child of the declaration; an undetermined declaration keeps from()/tryFrom() flagged.
                if len(found)==1 and any(child.type=='primitive_type' for child in found[0].children):builtins|={'from','tryfrom'}
            enum_methods[owner]=builtins
        return enum_methods[owner]
    assigned_of={}
    for row in db.execute('SELECT class_id,name,line,certainty FROM assignments'):assigned_of.setdefault((row['class_id'],row['name']),[]).append(dict(row))
    uncertain_by_owner={}
    for row in db.execute('''SELECT c.*,f.path FROM calls c JOIN files f ON f.id=c.file_id
        WHERE c.recv='self' AND c.dynamic=0 AND c.id NOT IN (SELECT call_id FROM edges) ORDER BY f.path,c.line,c.id''').fetchall():
        owner=class_for.get(row['caller_id'])
        if owner is None or symbol_rows[owner]['kind'] in ('trait','interface'):continue
        if row['name'].lower() in php_enum_builtins(owner):continue
        anc,external=ancestors[owner];consumers=consumers_of[owner]
        assigned_rows=[assignment for base in anc for assignment in assigned_of.get((base,row['name']),[])]
        assigned=any(assignment['certainty']=='assigned' for assignment in assigned_rows)
        magic=bool(inherited_methods[owner] & {'__call','__callStatic','__getattr__','__getattribute__'})
        if assigned or magic or (consumers and all(row['name'] in inherited_methods[sid] for sid in consumers)):continue
        uncertain=external or any(ancestors[sid][1] for sid in consumers) or bool(assigned_rows)
        if uncertain:
            item=uncertain_by_owner.setdefault(owner,dict(file=row['path'],line=row['line'],count=0,evidence=[],conditional=False))
            item['count']+=1;item['conditional']=item['conditional'] or bool(assigned_rows)
            if len(item['evidence'])<10:item['evidence'].append(dict(file=row['path'],line=row['line'],name=row['name']))
        else:
            add('missing_method','possible_defect',row['path'],row['line'],f"missing-method: {symbol(owner)['name']} and known consumers do not define {row['name']}()",
                evidence=[dict(file=symbol(sid)['file'],line=symbol(sid)['line'],name=symbol(sid)['name']) for sid in consumers],
                assumptions=['No monkey patches, metaclasses or external consumers intervene.'],reason='known_class_and_consumers',
                identity_message=f"metodo-mancante: {symbol(owner)['name']} e consumatori noti non definiscono {row['name']}()")
    for owner,item in sorted(uncertain_by_owner.items()):
        add('receiver_contract_unknown','analysis_limit',item['file'],item['line'],
            f"{item['count']} undetermined contracts on {symbol(owner)['name']}",evidence=item['evidence'],
            uncertainties=['Conditional assignment or external bases; no proof of a missing method.'],
            reason='conditional_assignment_or_external_consumers',severity='info',
            identity_message=f"{item['count']} contratti non determinabili su {symbol(owner)['name']}")
        results[-1]['occurrences']=item['count'];results[-1]['evidence_omitted']=max(0,item['count']-len(item['evidence']))

    # PHP class::missing retains the namespace-aware existing resolver logic as candidates.
    php_files={row['id'] for row in db.execute('SELECT id FROM files WHERE lang="php"')}
    php_namespaces=dict(db.execute("SELECT file_id,name FROM imports WHERE module='__ns__'"))
    php_aliases={}
    for row in db.execute("SELECT file_id,name,module FROM imports WHERE module!='__ns__'"):
        if row['file_id'] in php_files:php_aliases.setdefault(row['file_id'],{})[row['name'].lower()]=row['module']
    php_classes={}
    for sid in classes:
        info=symbol_rows[sid]
        if info['file_id'] not in php_files:continue
        ns=php_namespaces.get(info['file_id'],'')
        php_classes.setdefault(((ns+'\\' if ns else '')+info['name']).lower(),[]).append(sid)
    for row in db.execute('''SELECT c.*,f.path FROM calls c JOIN files f ON f.id=c.file_id WHERE f.lang='php'
                            AND c.recv='class' AND c.dynamic=0 AND c.id NOT IN (SELECT call_id FROM edges)''').fetchall():
        raw=row['recv_name']
        namespace = php_namespaces.get(row['file_id'],'')
        aliases = php_aliases.get(row['file_id'],{})
        head, separator, tail = raw.partition('\\')
        if raw.startswith('\\'): full=raw.lstrip('\\')
        elif head.lower() in aliases: full=aliases[head.lower()]+('\\'+tail if separator else '')
        else: full=(namespace+'\\' if namespace else '')+raw
        targets=php_classes.get(full.lower(),[])
        if len(targets)!=1: continue
        owner=targets[0];anc,external=ancestors[owner]
        if external or row['name'].lower()=='class' or row['name'].lower() in php_enum_builtins(owner): continue
        if any(db.execute("SELECT 1 FROM symbols WHERE parent_id=? AND (lower(name)=? OR name IN ('__call','__callStatic'))",(sid,row['name'].lower())).fetchone() for sid in anc): continue
        add('missing_static_method','possible_defect',row['path'],row['line'],f"missing-method: {row['recv_name']}::{row['name']}()",assumptions=['Unique static class resolution.'],reason='php_class_name',
            identity_message=f"metodo-mancante: {row['recv_name']}::{row['name']}()")

    ns = dict(db.execute("SELECT file_id,name FROM imports WHERE module='__ns__'"))
    groups = {}
    for row in db.execute("SELECT s.id,s.file_id,s.name,s.kind FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.lang='php' AND s.kind IN ('function','class','interface','trait') AND s.parent_id IN (SELECT id FROM symbols WHERE kind='module')"):
        groups.setdefault((ns.get(row['file_id'],'').lower(),row['name'].lower(),row['kind']=='function'),[]).append(row['id'])
    include_graph = {}
    for row in db.execute('SELECT f.path,i.target FROM includes i JOIN files f ON f.id=i.file_id WHERE i.certainty="static"'):
        include_graph.setdefault(row['path'],set()).add(row['target'])
    def loaded(entry):
        seen, pending=set(),[entry]
        while pending:
            path=pending.pop()
            if path in seen: continue
            seen.add(path);pending.extend(include_graph.get(path,()))
        return seen
    closures={path:loaded(path) for path in sorted(include_graph)}
    for key, ids in groups.items():
        if len(ids)<2: continue
        nodes=[symbol(sid) for sid in ids]
        # A syntactic guard belongs to this declaration, not an arbitrary function_exists elsewhere.
        guarded={node['id'] for node in nodes if db.execute('SELECT 1 FROM guarded_definitions WHERE symbol_id=?',(node['id'],)).fetchone()}
        paths={node['file'] for node in nodes}
        common=[entry for entry,closure in closures.items() if len(paths & closure)>=2]
        same_file=len(paths)<len(nodes)
        def collision(entry):
            declared,active=set(),set()
            def load(path):
                if path in active:return False
                active.add(path)
                local=[node for node in nodes if node['file']==path]
                for node in local:
                    if node['id'] not in guarded:
                        if declared:return True
                        declared.add(node['id'])
                events=[(node['line'],'guard',node) for node in local if node['id'] in guarded]
                events += [(row['line'],'include',row['target']) for row in db.execute('SELECT line,target FROM includes i JOIN files f ON f.id=i.file_id WHERE f.path=? AND i.certainty="static" ORDER BY line',(path,))]
                for _,kind,value in sorted(events,key=lambda event:event[0]):
                    if kind=='include':
                        if load(value):return True
                    elif not declared:declared.add(value['id'])
                active.remove(path)
                return False
            return load(entry)
        collisions=[entry for entry in common if collision(entry)]
        if same_file:collisions += [path for path in paths if collision(path)]
        if (common or same_file) and not collisions:continue
        unguarded=nodes
        common=collisions
        node=unguarded[0]
        add('php_duplicate','possible_defect' if common or same_file else 'diagnostic',node['file'],node['line'],
            f"duplicate: {key[1]} defined {len(nodes)} times in the same namespace",
            evidence=[dict(file=n['file'],line=n['line'],name=n['name']) for n in nodes]+[dict(entrypoint=entry) for entry in common],
            assumptions=['The identified static loads may coexist.'] if common else [],
            uncertainties=[] if common or same_file else ['No proof of loading together in the same process.'],
            reason='static_common_loader' if common else 'duplicate_name_only',severity='warning' if common or same_file else 'info',
            identity_message=f"duplicato: {key[1]} definito {len(nodes)} volte nello stesso namespace")

    adjacency={}
    for a,b in db.execute('SELECT DISTINCT sa.file_id,sb.file_id FROM edges e JOIN symbols sa ON sa.id=e.caller_id JOIN symbols sb ON sb.id=e.callee_id WHERE e.conf="high" AND sa.file_id!=sb.file_id'):
        adjacency.setdefault(a,set()).add(b)
    paths=dict(db.execute('SELECT id,path FROM files'))
    for component in cg.sccs(sorted(adjacency),adjacency)[:20]:
        path=sorted(paths[sid] for sid in component)[0]
        add('file_cycle','maintenance',path,1,'file-cycle: '+', '.join(sorted(paths[sid] for sid in component)),reason='high_call_cycle',severity='info',
            identity_message='ciclo-file: '+', '.join(sorted(paths[sid] for sid in component)))
    for row in db.execute('SELECT c.line,f.path,c.name,s.name AS target FROM edges e JOIN calls c ON c.id=e.call_id JOIN files f ON f.id=c.file_id JOIN symbols s ON s.id=e.callee_id WHERE f.lang="php" AND c.name!=s.name AND lower(c.name)=lower(s.name)'):
        add('php_case','maintenance',row['path'],row['line'],f"letter case: {row['name']} / {row['target']}",reason='php_case_insensitive_name',severity='info',
            identity_message=f"maiuscole: {row['name']} / {row['target']}")
    counts={}
    for item in results: counts[item['fingerprint']]=counts.get(item['fingerprint'],0)+1
    for item in results:
        if counts[item['fingerprint']]>1: item['uncertainties'].append('Duplicate identity: comparison across revisions is ambiguous.')
    return sorted(results,key=lambda item:(item['file'],item['line'],item['rule_id'],item['fingerprint']))
