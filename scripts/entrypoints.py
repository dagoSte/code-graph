"""Candidate entry points from indexed facts and static configuration; never imports projects."""
import ast
import json
from pathlib import Path
import re


def discover(project,test_role,limit=24,allow_fallback=True,query=''):
    found={}
    def add(sid,priority,reason,file,line=None):
        node=project._symbol(sid)
        if test_role(node)!='production':return
        hint=bool(re.search(r'(^|/)(compiled[^/]*|dist|build)/|\.min\.',node['file']))
        if hint:priority+=50
        item=found.setdefault(sid,dict(node=node,priority=priority,evidence=[],generated_path_hint=hint))
        item['priority']=min(priority,item['priority'])
        proof=dict(kind=reason,file=file,line=line)
        if proof not in item['evidence'] and len(item['evidence'])<3:item['evidence'].append(proof)
    for row in project.db.execute("SELECT owner_id,kind,line,f.path FROM observations o JOIN files f ON f.id=o.file_id WHERE kind IN ('registration.site','registration.mcp') ORDER BY f.path,line"):
        if row['owner_id'] is not None:add(row['owner_id'],0,row['kind'],row['path'],row['line'])
    for row in project.db.execute("SELECT target_id,kind,line,f.path FROM symbol_refs r JOIN files f ON f.id=r.file_id WHERE target_id IS NOT NULL AND kind='entrypoint' ORDER BY f.path,line"):
        add(row['target_id'],0,'registered_handler',row['path'],row['line'])
    for name,meta in sorted(project.files.items()):
        if meta['lang']!='python' or test_role(dict(file=name,name='',kind='module',language='python'))!='production':continue
        lines=project._read(name);text='\n'.join(lines)
        if '__main__' not in text:continue
        try:tree=ast.parse(text)
        except (SyntaxError,ValueError,RecursionError):continue
        for statement in tree.body:
            if not isinstance(statement,ast.If):continue
            condition=statement.test
            if not (isinstance(condition,ast.Compare) and len(condition.ops)==1 and isinstance(condition.ops[0],ast.Eq)
                    and len(condition.comparators)==1 and {ast.dump(condition.left),ast.dump(condition.comparators[0])}=={ast.dump(ast.Name(id='__name__',ctx=ast.Load())),ast.dump(ast.Constant(value='__main__'))}):continue
            pending=list(statement.body)
            while pending:
                node=pending.pop()
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):continue
                if isinstance(node,ast.Call):
                    target=node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else None
                    if target:
                        rows=list(project.db.execute("SELECT DISTINCT e.callee_id FROM edges e JOIN calls c ON c.id=e.call_id JOIN files f ON f.id=c.file_id WHERE f.path=? AND c.line=? AND c.name=? AND e.conf IN ('high','medium')",(name,node.lineno,target)))
                        if len(rows)==1:add(rows[0][0],5,'python_main_guard',name,node.lineno)
                pending.extend(ast.iter_child_nodes(node))
    raw=project.config_sources.get('pyproject.toml')
    if raw:
        try:
            try:import tomllib
            except ImportError:tomllib=None
            scripts=tomllib.loads(raw.decode()).get('project',{}).get('scripts',{}) if tomllib else {}
            for command,target in scripts.items():
                if not isinstance(target,str) or ':' not in target:continue
                module,symbol=target.split(':',1);module=module.replace('.','/')
                choices=[module+'.py',module+'/__init__.py','src/'+module+'.py','src/'+module+'/__init__.py']
                rows=list(project.db.execute('SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.path IN (?,?,?,?) AND s.qname=?',choices+[symbol]))
                if len(rows)==1:add(rows[0][0],1,'project_script:'+str(command),'pyproject.toml')
        except (ValueError,TypeError,AttributeError,UnicodeError):pass
    # Merge fallback facts even when registrations exist. Filter tests BEFORE applying the limit.
    rows=project.db.execute("""SELECT s.id,f.path,s.line,COUNT(DISTINCT e.callee_id) AS links FROM symbols s JOIN files f ON f.id=s.file_id
        JOIN edges e ON e.caller_id=s.id WHERE s.kind='module' AND e.conf IN ('high','medium')
        GROUP BY s.id ORDER BY links DESC,f.path,s.line""")
    for row in rows:add(row['id'],20,'module_calls',row['path'],row['line'])
    fallback_total=None
    if not found and allow_fallback:
        rows=project.db.execute("SELECT s.id,f.path,s.line FROM symbols s JOIN files f ON f.id=s.file_id WHERE kind IN ('function','method') ORDER BY f.path,s.line")
        eligible=[row for row in rows if test_role(dict(file=row['path'],name='',kind='module',language=''))=='production']
        eligible.sort(key=lambda row:(bool(re.search(r'(^|/)(compiled[^/]*|dist|build)/|\.min\.',row['path'])),row['path'],row['line']))
        tokens=query.casefold().split()
        eligible=[row for row in eligible if all(token in (row['path']+' '+project._symbol(row['id'])['name']).casefold() for token in tokens)]
        fallback_total=len(eligible)
        for row in eligible[:limit]:add(row['id'],40,'definition_fallback',row['path'],row['line'])
    # Evidence priority is independent of the number of test-module calls.
    tokens=query.casefold().split()
    matching=[item for item in found.values() if all(token in ' '.join(str(item['node'].get(key,'')) for key in ('file','name','kind','language')).casefold() for token in tokens)]
    ordered=sorted(matching,key=lambda item:(item['priority'],item['node']['file'],item['node']['line']))
    return ordered[:limit],fallback_total if fallback_total is not None else len(ordered)
