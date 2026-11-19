"""SQLite source resource evidence; never prepare or execute analyzed SQL."""
import json

EXTENSIONS={'.sql':'sql'}
DEFINITIONS={}
CALL_TYPES=()
SUPPORTED={'create_table','create_view','insert','update','delete','select','set_operation'}


def nodes(root):
    pending=[root]
    while pending:
        node=pending.pop();yield node;pending.extend(reversed(node.named_children))


def identifier(node):
    value=txt(node)
    if value.startswith('"') and value.endswith('"'):value=value[1:-1].replace('""','"')
    return value.translate(str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'))


def resource(node):
    name=node.child_by_field_name('name');schema=node.child_by_field_name('schema')
    if name is None:return None
    return dict(name=identifier(name),schema=identifier(schema) if schema is not None else None)


def extract(ex,root):
    module=ex.add_sym('<module>','module',None,root,sig='')
    def observe(owner,kind,node,value):
        ex.observations.append(dict(owner=owner,kind='sql.'+kind,callee=value.get('name',''),line=node.start_point[0]+1,
            value=json.dumps(value,sort_keys=True),value_kind='json'))
    def statements(node):
        for child in node.named_children:
            if child.type=='statement':yield child
            elif child.type=='transaction':yield from statements(child)
    roots=list(statements(root))
    def operation(statement):
        return next((n for n in statement.named_children if n.type not in ('comment','marginalia','keyword_with','keyword_recursive','cte')),None)
    # Unsupported syntax cannot establish a resource inventory for this file.
    supported=not root.has_error and all(operation(n) is not None and operation(n).type in SUPPORTED for n in roots)
    supported=supported and not any(n.type in ('keyword_references','keyword_virtual') for n in nodes(root))
    observe(module,'file',root,dict(dialect='sqlite',supported=bool(supported)))
    for statement in roots:
        op=operation(statement)
        declaration=op is not None and op.type in ('create_table','create_view')
        target=next((n for n in op.named_children if n.type=='object_reference'),None) if declaration else None
        value=resource(target) if target is not None else None
        kind=op.type.removeprefix('create_') if value else 'query'
        name=(value['schema']+'.' if value['schema'] else '')+value['name'] if value else '<'+(op.type if op is not None else 'statement')+'@'+str(statement.start_point[0]+1)+':'+str(statement.start_point[1])+ '>'
        owner=ex.add_sym(name,kind,module,statement)
        if not supported:continue
        if value:
            temporary=any(n.type in ('keyword_temp','keyword_temporary') for n in op.named_children)
            observe(owner,'declare',target,dict(value,object_kind=kind,temporary=temporary))
        # CTEs are statement-local names. A conservative union also blocks nested scopes.
        ctes={identifier(n.named_children[0]) for n in nodes(statement) if n.type=='cte' and n.named_children}
        seen=set()
        def access(node,mode):
            value=resource(node)
            if value is None:return
            if mode=='read' and value['schema'] is None and value['name'] in ctes:return
            key=(node.start_byte,node.end_byte,mode)
            if key in seen:return
            seen.add(key);observe(owner,mode,node,value)
        for node in nodes(statement):
            if node.type=='relation':
                ref=next((n for n in node.named_children if n.type=='object_reference'),None)
                if ref is not None:access(ref,'write' if node.parent.type=='update' else 'read')
            elif node.type=='insert':
                ref=next((n for n in node.named_children if n.type=='object_reference'),None)
                if ref is not None:access(ref,'write')
            elif node.type=='from':
                deleting=any(n.type=='delete' for n in node.parent.named_children)
                for ref in node.named_children:
                    if ref.type=='object_reference':access(ref,'write' if deleting else 'read')
    return ex


def architecture(project,limit):
    """Bounded candidates within one script; no database/execution-order proof."""
    if type(limit) is not int or not 1<=limit<=500:raise ValueError('Limit outside the range 1..500.')
    if project.cg.analysis_inputs(project.root)[2] != project.meta.get('analysis_fingerprint'):
        raise ValueError('Engine changed after indexing; refresh the index.')
    where=" FROM observations o JOIN files f ON f.id=o.file_id WHERE o.kind IN ('sql.file','sql.declare','sql.read','sql.write')"
    total=project.db.execute("SELECT COUNT(*)"+where).fetchone()[0]
    rows=project.db.execute("SELECT o.*,f.path"+where+" ORDER BY f.path,o.line,o.id LIMIT ?",(limit,)).fetchall()
    items=[];links=[]
    def proof(row):
        source=project.excerpt(row['path'],row['line'],row['line'],1)
        return dict(id='observation:'+str(row['id']),kind=row['kind'],value=json.loads(row['value']),
            file=row['path'],line=row['line'],owner=row['owner_id'],status='syntax_candidate',evidence_type='syntax',
            source=source,sha256=project.source_hashes[row['path']])
    for row in rows:
        item=proof(row);items.append(item)
        if row['kind'] not in ('sql.read','sql.write') or item['value']['schema'] not in (None,'main'):continue
        # All schemas/temp declarations count before eligibility: they can shadow main.
        group=project.db.execute("SELECT o.*,? AS path FROM observations o WHERE file_id=? AND kind='sql.declare' AND callee=? ORDER BY id LIMIT 2",
            (row['path'],row['file_id'],row['callee'])).fetchall()
        if len(group)!=1:continue
        target=json.loads(group[0]['value'])
        if target['temporary'] or target['schema'] not in (None,'main'):continue
        links.append(dict(kind='sql_resource_candidate',resource=item['value'],producer=proof(group[0]),consumer=item,
            status='candidate',rule='same_script_unique_name',assumptions=['Unique name in the same script; no connection, migration order or runtime binding proof.']))
    return dict(dialect='sqlite',items=items,total=total,links=links,links_scope='returned_accesses',truncated=total>len(items))
