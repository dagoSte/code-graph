"""Bounded Java source bindings; no compiler, build scripts or classpath execution."""
from collections import defaultdict
import json
import posixpath

EXTENSIONS = {'.java': 'java'}
DEFINITIONS = {'class_declaration':'class', 'interface_declaration':'interface',
              'enum_declaration':'enum', 'record_declaration':'class', 'method_declaration':'method'}
CALL_TYPES = ('method_invocation','object_creation_expression','explicit_constructor_invocation')
TYPES = {'class_declaration','interface_declaration','enum_declaration','record_declaration','annotation_type_declaration'}
OBJECT_METHODS = set('clone equals finalize getClass hashCode notify notifyAll toString wait compareTo name ordinal valueOf values getDeclaringClass'.split())


def nodes(root):
    pending=[root] if root is not None else []
    while pending:
        node=pending.pop()
        yield node
        pending.extend(reversed(node.named_children))


def qualified(node):
    if node is None:return ''
    if node.type in ('identifier','type_identifier','this','super'):return txt(node)
    if node.type=='scoped_type_identifier':
        parts=[qualified(n) for n in node.named_children if n.type not in ('line_comment','block_comment')]
        return '.'.join(parts) if parts and all(parts) else ''
    if node.type in ('scoped_identifier','field_access'):
        left=node.child_by_field_name('scope') or node.child_by_field_name('object')
        right=node.child_by_field_name('name') or node.child_by_field_name('field')
        a,b=qualified(left),qualified(right)
        return a+'.'+b if a and b else ''
    return ''


def modifiers(node):
    group=next((n for n in node.named_children if n.type=='modifiers'),None)
    return {txt(n) for n in group.children} if group is not None else set()


def bindings(root):
    values,types=set(),set()
    opaque=False
    for node in nodes(root):
        if 'pattern' in node.type or node.type in TYPES or (node.type=='object_creation_expression' and any(n.type=='class_body' for n in node.named_children)):
            opaque=True
        if node.type in ('formal_parameter','spread_parameter','catch_formal_parameter','variable_declarator','enhanced_for_statement','instanceof_expression'):
            name=node.child_by_field_name('name')
            if name is None and node.type=='spread_parameter':
                name=next((n.child_by_field_name('name') for n in node.named_children if n.type=='variable_declarator'),None)
            if name is not None:values.add(txt(name))
        elif node.type=='lambda_expression':
            group=node.child_by_field_name('parameters')
            if group is not None and group.type=='identifier':values.add(txt(group))
            elif group is not None and group.type=='inferred_parameters':values.update(txt(n) for n in group.named_children if n.type=='identifier')
        elif node.type=='type_parameter':
            name=next((n for n in node.named_children if n.type=='type_identifier'),None)
            if name is not None:types.add(txt(name))
    return dict(values=sorted(values),types=sorted(types),opaque=opaque)


def handle_call(ex,node,function_id,class_id=None):
    constructor=node.type!='method_invocation'
    name='<init>' if constructor else txt(node.child_by_field_name('name'))
    receiver=qualified(node.child_by_field_name('object'))
    arguments=node.child_by_field_name('arguments')
    ex.calls.append(dict(caller=function_id,name=name,recv='java_constructor' if constructor else 'java_method',
        recv_name=receiver,line=node.start_point[0]+1,column_bytes=node.start_point[1],
        nargs=len([n for n in arguments.named_children if n.type not in ('line_comment','block_comment')]) if arguments is not None else 0,
        splat=0,dynamic=int(constructor or (node.child_by_field_name('object') is not None and not receiver)),
        end=(node.end_point[0]+1,node.end_point[1])))


def extract(ex,root):
    module=ex.add_sym('<module>','module',None,root,sig='')
    def observe(owner,kind,node,value):
        ex.observations.append(dict(owner=owner,kind='java.'+kind,callee='',line=node.start_point[0]+1,
                                    value=json.dumps(value,sort_keys=True),value_kind='json'))
    package=''
    imports=[]
    for node in root.named_children:
        if node.type=='package_declaration':
            package=next((qualified(n) for n in node.named_children if n.type in ('identifier','scoped_identifier')),'')
        elif node.type=='import_declaration':
            path=next((qualified(n) for n in node.named_children if n.type in ('identifier','scoped_identifier')),'')
            static=any(n.type=='static' for n in node.children)
            wildcard=any(n.type=='asterisk' for n in node.named_children)
            imports.append(dict(path=path,static=static,wildcard=wildcard))
            ex.imports.append((path+('.*' if wildcard else ''),'static' if static else '',node.start_point[0]+1))
    directory=posixpath.dirname(ex.source_path or '')
    suffix=package.replace('.','/')
    source_root=(directory[:-len(suffix)].rstrip('/') if suffix and (directory==suffix or directory.endswith('/'+suffix)) else directory if not suffix else None)
    observe(module,'file',root,dict(package=package,root=source_root,imports=imports,
            excluded=root.has_error or b'\\u' in ex.src or any(n.type=='module_declaration' for n in root.named_children)))

    def visit(node,owner,cls):
        if node.type in TYPES:
            name=txt(node.child_by_field_name('name'))
            kind=DEFINITIONS.get(node.type,'interface')
            sid=ex.add_sym(name,kind,owner,node,exported=int('public' in modifiers(node)))
            body=node.child_by_field_name('body')
            members=[]
            for member in body.named_children if body is not None else ():
                members.extend(member.named_children if member.type=='enum_body_declarations' else [member])
            fields=[];nested=[]
            for member in members:
                if member.type in ('field_declaration','constant_declaration'):
                    fields.extend(txt(n.child_by_field_name('name')) for n in member.named_children if n.type=='variable_declarator')
                elif member.type in TYPES:nested.append(txt(member.child_by_field_name('name')))
                elif member.type=='enum_constant':fields.append(txt(member.child_by_field_name('name')))
            parameters=node.child_by_field_name('parameters')
            components=bindings(parameters)['values'] if node.type=='record_declaration' else []
            fields.extend(components)
            type_parameters=node.child_by_field_name('type_parameters')
            nested.extend(bindings(type_parameters)['types'])
            flags=modifiers(node)
            heritage=any(n.type in ('superclass','super_interfaces','extends_interfaces') for n in node.named_children)
            observe(sid,'symbol',node,dict(role='type',name=name,public='public' in flags,
                final='final' in flags or node.type=='record_declaration',heritage=heritage,
                nested=node.parent.type!='program',fields=fields,types=nested,components=components,kind=node.type))
            for member in members:visit(member,sid,sid)
            return
        if node.type in ('method_declaration','constructor_declaration','compact_constructor_declaration','annotation_type_element_declaration'):
            name=txt(node.child_by_field_name('name'))
            flags=modifiers(node)
            parameters=node.child_by_field_name('parameters')
            parameters=[n for n in parameters.named_children if n.type in ('formal_parameter','spread_parameter')] if parameters is not None else []
            minimum=sum(n.type!='spread_parameter' for n in parameters)
            maximum=None if any(n.type=='spread_parameter' for n in parameters) else len(parameters)
            sid=ex.add_sym(name,'method',owner,node,pmin=minimum,pmax=maximum,exported=int('public' in flags))
            observe(sid,'symbol',node,dict(role='method' if node.type=='method_declaration' else 'constructor',name=name,
                static='static' in flags,final='final' in flags,visibility=next((v for v in ('public','private','protected') if v in flags),'package'),body=node.child_by_field_name('body') is not None))
            observe(sid,'scope',node,bindings(node))
            body=node.child_by_field_name('body')
            if body is not None:visit(body,sid,cls)
            return
        if node.type in ('field_declaration','constant_declaration'):
            for declaration in node.named_children:
                if declaration.type!='variable_declarator':continue
                ex.add_sym(txt(declaration.child_by_field_name('name')),'variable',owner,declaration)
                value=declaration.child_by_field_name('value')
                if value is not None:
                    observe(owner,'scope',value,bindings(value));visit(value,owner,cls)
            return
        if (node.type=='static_initializer' or (node.type=='block' and node.parent.type=='class_body')) and owner==cls:
            observe(owner,'scope',node,bindings(node))
        if node.type=='enum_constant':
            for arguments in node.named_children:
                if arguments.type=='argument_list':observe(owner,'scope',arguments,bindings(arguments))
        if node.type in CALL_TYPES:handle_call(ex,node,owner,cls)
        elif node.type=='method_reference':
            parts=[n for n in node.named_children if n.type not in ('line_comment','block_comment','type_arguments')]
            constructor=any(n.type=='new' for n in node.children)
            name=txt(parts[-1]) if not constructor and parts and parts[-1].type=='identifier' else '<init>'
            receiver=qualified(parts[0]) if parts and (constructor or len(parts)>1) else ''
            receiver=receiver or '<unknown>'
            ex.references.append(dict(owner=owner,name=name,receiver=receiver,kind='java.constructor_reference' if constructor else 'java.method_reference',line=node.start_point[0]+1))
        for child in node.named_children:
            if node.type in ('object_creation_expression','enum_constant') and child.type=='class_body':
                name='<anonymous@'+str(node.start_point[0]+1)+':'+str(node.start_point[1])+'>'
                sid=ex.add_sym(name,'class',owner,child)
                observe(sid,'symbol',child,dict(role='type',name=name,public=False,final=False,heritage=True,nested=True,fields=[],types=[],components=[],kind='anonymous'))
                for member in child.named_children:visit(member,sid,sid)
            else:visit(child,owner,cls)
    for node in root.named_children:visit(node,module,None)
    return ex


def store_config(db,paths):
    value=json.dumps(sorted(paths))
    previous=db.execute("SELECT v FROM meta WHERE k='java_modules'").fetchone()
    db.execute("INSERT OR REPLACE INTO meta VALUES('java_modules',?)",(value,))
    return previous is None or previous[0]!=value


class JavaClasses:
    def __init__(self,db):
        self.paths=dict(db.execute("SELECT id,path FROM files WHERE lang='java'"))
        self.files,self.facts,self.scopes={},{},{}
        for fid,owner,kind,value in db.execute("SELECT file_id,owner_id,kind,value FROM observations WHERE kind IN ('java.file','java.symbol','java.scope')"):
            fact=json.loads(value)
            if kind=='java.file':self.files[fid]=fact
            elif kind=='java.symbol':self.facts[owner]=fact
            else:
                previous=self.scopes.setdefault(owner,dict(values=[],types=[],opaque=False))
                previous['values']=sorted(set(previous['values'])|set(fact['values']))
                previous['types']=sorted(set(previous['types'])|set(fact['types']))
                previous['opaque']|=fact['opaque']
        self.symbols={sid:(fid,parent,kind) for sid,fid,parent,kind in db.execute("SELECT s.id,s.file_id,s.parent_id,s.kind FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.lang='java'")}
        self.types=defaultdict(list);self.methods=defaultdict(list)
        for sid,fact in self.facts.items():
            fid,parent,kind=self.symbols[sid]
            if fact['role']=='type' and not fact['nested']:
                self.types[self.files[fid]['root'],self.files[fid]['package']+'.'+fact['name'] if self.files[fid]['package'] else fact['name']].append(sid)
            elif fact['role']=='method':self.methods[parent,fact['name']].append(sid)
        row=db.execute("SELECT v FROM meta WHERE k='java_modules'").fetchone()
        self.modules=bool(json.loads(row[0])) if row else False

    def owner_class(self,owner):
        if self.facts.get(owner,{}).get('role')=='type':return owner
        return self.symbols[owner][1]

    def accessible_type(self,sid,fid):
        other=self.symbols[sid][0];fact=self.facts[sid]
        return (not self.files[other]['excluded'] and not fact['nested'] and
                (fid==other or (not self.modules and self.files[fid]['root'] is not None and self.files[fid]['root']==self.files[other]['root'])) and
                (self.files[fid]['package']==self.files[other]['package'] or fact['public']))

    def type_named(self,fid,name):
        info=self.files[fid];root=info['root']
        def lookup(fqn):
            found=self.types[root,fqn]
            return found[0] if len(found)==1 and self.accessible_type(found[0],fid) else None
        if '.' in name:
            first=name.split('.')[0]
            local_type=any(fact['role']=='type' and fact['name']==first and self.symbols[sid][0]==fid for sid,fact in self.facts.items())
            imported_type=any(entry['wildcard'] or entry['path'].rpartition('.')[2]==first for entry in info['imports'])
            package_type=self.types[root,info['package']+'.'+first if info['package'] else first]
            return None if local_type or imported_type or package_type else lookup(name)
        own=[sid for sid,fact in self.facts.items() if fact['role']=='type' and fact['name']==name and self.symbols[sid][0]==fid and not fact['nested']]
        if own:return own[0] if len(own)==1 else None
        if any(entry['static'] and not entry['wildcard'] and entry['path'].rpartition('.')[2]==name for entry in info['imports']):return None
        imports=[entry for entry in info['imports'] if not entry['static'] and not entry['wildcard'] and entry['path'].rpartition('.')[2]==name]
        if imports:
            paths={entry['path'] for entry in imports}
            return lookup(next(iter(paths))) if len(paths)==1 else None
        return lookup(info['package']+'.'+name if info['package'] else name)

    def method(self,cls,name,caller,static_only):
        if cls is None or name in OBJECT_METHODS:return []
        target_type=self.facts[cls]
        if target_type['heritage'] or target_type['nested'] or name in target_type['components']:return []
        candidates=self.methods[cls,name]
        if len(candidates)!=1:return []
        sid=candidates[0];fact=self.facts[sid]
        if not fact['body']:return []
        caller_class=self.owner_class(caller)
        a,b=self.symbols[cls][0],self.symbols[caller][0]
        same_package=self.files[a]['package']==self.files[b]['package']
        visibility=fact['visibility']
        if target_type['kind']=='interface_declaration' and visibility=='package':visibility='public'
        if visibility=='private' and cls!=caller_class:return []
        if visibility in ('package','protected') and not same_package:return []
        if fact['static']:return [sid]
        caller_fact=self.facts.get(caller,{})
        if static_only or cls!=caller_class or caller_fact.get('static',True):return []
        return [sid] if visibility=='private' or fact['final'] or target_type['final'] else []

    def targets(self,fid,owner,name,receiver=''):
        if self.files[fid]['excluded']:return []
        cls=self.owner_class(owner)
        if self.facts.get(cls,{}).get('role')!='type':return []
        current=self.facts[cls];scope=self.scopes.get(owner,{})
        if current['heritage'] or current['nested'] or scope.get('opaque'):return []
        if not receiver or receiver=='this':
            if self.methods[cls,name]:return self.method(cls,name,owner,False)
            if receiver:return []
            candidates=set()
            for entry in self.files[fid]['imports']:
                if not entry['static']:continue
                if entry['wildcard']:return []
                path,_,member=entry['path'].rpartition('.')
                if member!=name:continue
                other=self.type_named(fid,path)
                targets=self.method(other,name,owner,True)
                if not targets:return []
                candidates.update(targets)
            return sorted(candidates) if len(candidates)==1 else []
        first=receiver.split('.')[0]
        if first in set(current['fields']+current['types']+scope.get('values',[])+scope.get('types',[])):return []
        if any(entry['static'] and (entry['wildcard'] or entry['path'].rpartition('.')[2]==first) for entry in self.files[fid]['imports']):return []
        return self.method(self.type_named(fid,receiver),name,owner,True)
