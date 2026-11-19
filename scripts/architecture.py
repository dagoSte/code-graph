"""Resource candidates and explicit declarations, separate from call edges."""
import hashlib
import json
from pathlib import Path
import posixpath
import re


def normalize(value, root):
    if not isinstance(value,str) or not value or any(char in value for char in ('$', '{', '}', '%', '*', '?', '\x00')) or '://' in value:
        return None
    path=Path(posixpath.normpath(value))
    if path.is_absolute():
        try:return path.relative_to(root).as_posix()
        except ValueError:return None
    result=posixpath.normpath(value)
    return result if result!='..' and not result.startswith('../') else None


def manifest(project):
    raw=project.config_sources.get('.code-graph-manifest.json')
    if raw is None:return dict(format='codegraph-manifest/1',areas=[],resources=[]),[]
    try:
        value=json.loads(raw)
        if value.get('format')!='codegraph-manifest/1' or not isinstance(value.get('areas',[]),list) or not isinstance(value.get('resources',[]),list):raise ValueError('Unrecognized manifest schema.')
        def proofs(items):
            if not isinstance(items,list) or len(items)>100:return False
            return all(isinstance(item,dict) and isinstance(item.get('file'),str) and isinstance(item.get('line'),int)
                       and item['line']>0 and (item.get('sha256') is None or isinstance(item['sha256'],str) and re.fullmatch(r'[a-fA-F0-9]{64}',item['sha256'])) for item in items)
        for area in value.get('areas',[]):
            if not isinstance(area,dict) or not isinstance(area.get('id'),str) or not isinstance(area.get('files',[]),list) or not all(isinstance(path,str) for path in area.get('files',[])) or not proofs(area.get('evidence',[])):
                raise ValueError('Invalid manifest area: id, files and evidence must use the documented types.')
        for resource in value.get('resources',[]):
            if not isinstance(resource,dict) or not isinstance(resource.get('path'),str) or not proofs(resource.get('producers',[])) or not proofs(resource.get('consumers',[])):
                raise ValueError('Invalid manifest resource: path, producers and consumers must use the documented types.')
        return value,[]
    except (ValueError,AttributeError) as error:return dict(areas=[],resources=[]),[dict(status='invalid_manifest',message=str(error))]


def evidence(project, declaration):
    if not isinstance(declaration,dict):return dict(status='invalid_evidence')
    name=declaration.get('file');line=declaration.get('line')
    if name not in project.files or not isinstance(line,int) or line<1:return dict(file=name,line=line,status='unresolved_evidence')
    lines=project._read(name)
    if line>len(lines):return dict(file=name,line=line,status='unresolved_evidence')
    # The exact hash is collected with the same bounded, verified source read.
    actual=project.source_hashes[name]
    expected=declaration.get('sha256')
    state='verified_declaration' if expected==actual else 'stale_declaration' if expected else 'unversioned_declaration'
    return dict(file=name,line=line,status=state,sha256=actual,source=project.excerpt(name,line,line,1))


def analyze(project, limit=500):
    observations=[];groups={};declarations=[];issues=[];total_links=0
    for row in project.db.execute("SELECT o.*,f.path FROM observations o JOIN files f ON f.id=o.file_id WHERE o.kind IN ('file.read','file.write','database.connect','process.start') ORDER BY f.path,o.line,o.id"):
        path=normalize(row['value'],project.root) if row['value_kind']=='literal' else None
        item=dict(id='observation:'+str(row['id']),kind=row['kind'],file=row['path'],line=row['line'],value=row['value'],
                  resource=path,status='static_candidate' if path else 'dynamic',evidence_type='observation',
                  normalization='project_relative_cwd_assumption' if path and not Path(row['value']).is_absolute() else 'absolute_inside_project' if path else 'unknown')
        observations.append(item)
        if path and row['kind']!='process.start':groups.setdefault(path,[]).append(item)
    links=[]
    for path,items in sorted(groups.items()):
        producers=[item for item in items if item['kind']=='file.write']
        consumers=[item for item in items if item['kind'] in ('file.read','database.connect')]
        total_links += len(producers)*len(consumers)
        for producer in producers:
            if len(links)>=limit:break
            for consumer in consumers:
                if len(links)>=limit:break
                links.append(dict(kind='resource_candidate',resource=path,producer=producer,consumer=consumer,
                                  status='candidate',rule='same_normalized_path',assumptions=['Relative paths are resolved from the project root; confirm cwd and environment.']))
    manual,issues=manifest(project)
    for resource in manual.get('resources',[])[:500]:
        if not isinstance(resource,dict):continue
        path=normalize(resource.get('path'),project.root)
        producers=[evidence(project,item) for item in resource.get('producers',[])[:100] if isinstance(item,dict)]
        consumers=[evidence(project,item) for item in resource.get('consumers',[])[:100] if isinstance(item,dict)]
        declaration=dict(id=resource.get('id'),path=resource.get('path'),resource=path,evidence_type='manual_declaration',
                         status='declared' if path else 'dynamic_declaration',producers=producers,consumers=consumers)
        declarations.append(declaration)
        total_links += len(producers)*len(consumers)
        for producer in producers:
            if len(links)>=limit:break
            for consumer in consumers:
                if len(links)>=limit:break
                statuses={producer['status'],consumer['status']}
                status='stale_declaration' if 'stale_declaration' in statuses else 'declared_candidate' if path and statuses=={'verified_declaration'} else 'unverified_declaration'
                links.append(dict(kind='declared_resource',resource=path or resource.get('path'),producer=producer,consumer=consumer,status=status,
                                  rule='manual_manifest',assumptions=['Project declaration; not an execution trace.']))
    commands=project.config_sources.get('deploy/commands.json')
    if commands:
        try:
            value=json.loads(commands)
            def visit(value,trail):
                if isinstance(value,list) and value and all(isinstance(token,str) for token in value):
                    declarations.append(dict(kind='command',evidence_type='configuration',file='deploy/commands.json',key=trail,tokens=value,
                                             status='dynamic_declaration' if any(re.search(r'[{$%]',token) for token in value) else 'declared'))
                elif isinstance(value,list):
                    for index,item in enumerate(value):visit(item,trail+'/'+str(index))
                elif isinstance(value,dict):
                    for key,item in sorted(value.items()):visit(item,trail+'/'+key)
            visit(value,'')
        except (ValueError,TypeError,RecursionError) as error:issues.append(dict(status='invalid_commands',message=str(error)))
    for name,raw in sorted(project.config_sources.items()):
        if Path(name).suffix not in ('.service','.conf'):continue
        for number,line in enumerate(raw.decode('utf-8','replace').splitlines(),1):
            match=re.match(r'^\s*(ExecStart|root|alias|fastcgi_pass|proxy_pass)\s*(?:=|\s)\s*(.*?)\s*;?$',line)
            if match and not line.lstrip().startswith(('#',';')):
                declarations.append(dict(kind=match[1],value=match[2],file=name,line=number,evidence_type='configuration',
                                         status='dynamic_declaration' if re.search(r'[$%{}]',match[2]) else 'declared'))
    for row in project.db.execute('SELECT f.path,i.target,i.line,i.certainty FROM includes i JOIN files f ON f.id=i.file_id ORDER BY f.path,i.line'):
        declarations.append(dict(kind='php_include',file=row['path'],target=row['target'],line=row['line'],status=row['certainty'],evidence_type='syntax'))
    for link in links:
        for side in ('producer','consumer'):
            proof=link[side]
            if proof.get('file') in project.files and proof.get('line') and 'source' not in proof:
                proof['source']=project.excerpt(proof['file'],proof['line'],proof['line'],1)
    result=dict(format='codegraph-architecture/1',content_revision=project.meta['content_revision'],observations=observations[:limit],
                declarations=declarations[:limit],links=links[:limit],issues=issues,
                totals=dict(observations=len(observations),declarations=len(declarations),links=total_links),
                truncated=total_links>len(links) or any(len(items)>limit for items in (observations,declarations)),
                limitations=['Resource relationships are candidates or declarations, separate from call edges.',
                             'Dynamic paths, cwd and environment need confirmation; no resource is opened.'])
    if any(row['lang']=='sql' for row in project.files.values()):
        result['sql']=project.cg.LANGUAGE_ADAPTERS['sql']['architecture'](project,limit)
        result['truncated'] |= result['sql']['truncated']
    return result


def add_areas(project,groups):
    value,issues=manifest(project)
    for item in value.get('areas',[])[:100]:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str):continue
        proofs=[evidence(project,row) for row in item.get('evidence',[])[:100]]
        paths=[path for path in item.get('files',[]) if path in project.files]
        groups['declared:'+item['id']]=dict(id='declared:'+item['id'],title=str(item.get('title',item['id']))+' · declared',kind='evidence',
                                          files=list(dict.fromkeys(paths)),evidence=[dict(file=row.get('file'),line=row.get('line'),reason='Manual manifest: '+row['status']) for row in proofs],provenance='manual_declaration')
    commands=project.config_sources.get('deploy/commands.json')
    if commands:
        groups['declared:deployment']=dict(id='declared:deployment',title='Declared deployment commands',kind='evidence',files=[],
                                          evidence=[dict(file='deploy/commands.json',line=None,reason='Declarative configuration; unresolved placeholders')],provenance='configuration')
