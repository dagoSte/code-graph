"""Standalone Dart libraries and explicit static calls; never run project code."""
from collections import defaultdict
import json
import hashlib
from pathlib import Path
import posixpath
import re
from urllib.parse import unquote, urljoin, urlsplit

EXTENSIONS = {'.dart': 'dart'}
DEFINITIONS = {'function_signature': 'function', 'class_definition': 'class'}
CALL_TYPES = ('argument_part',)


def within(path,directory):
    return (directory=='.' and not path.startswith(('../','/'))) or path==directory or path.startswith(directory.rstrip('/')+'/')


def file_uri_path(uri):
    if not isinstance(uri,str) or any(ord(c)<32 for c in uri) or '?' in uri or '#' in uri:raise ValueError('unsupported URI')
    uri.encode('utf-8',errors='strict')
    parsed=urlsplit(uri)
    if parsed.scheme!='file' or parsed.netloc or not parsed.path.startswith('/') or parsed.path.startswith('//'):raise ValueError('file URI required')
    if re.search(r'%(?![0-9a-fA-F]{2})|%(?:2f|5c|00)',parsed.path,re.I):raise ValueError('unsupported escape')
    path=unquote(parsed.path,encoding='utf-8',errors='strict')
    if '\\' in path or any(ord(c)<32 for c in path):raise ValueError('unsupported path')
    return posixpath.normpath(path)


def invalid_constant(value):
    raise ValueError('invalid JSON constant')


def package_config(raw,root):
    """None means opaque; mappings are source-relative directories or external None."""
    try:
        data=json.loads(raw.decode('utf-8'),parse_constant=invalid_constant)
        if not isinstance(data,dict) or type(data.get('configVersion')) is not int or data['configVersion']!=2:return None
        packages=data.get('packages')
        if not isinstance(packages,list) or len(packages)>500:return None
        project=Path(root).resolve();anchor=project.as_posix();base=(project/'.dart_tool/package_config.json').as_uri()
        entries=[];names=set();roots=set()
        for package in packages:
            if not isinstance(package,dict):return None
            name=package.get('name');rawroot=package.get('rootUri');rawpackage=package.get('packageUri','')
            if not isinstance(name,str) or not re.fullmatch(r"[A-Za-z0-9._~!$&'()*+,;=@-]+",name) or not name.strip('.') or name in names:return None
            if not isinstance(rawroot,str) or not isinstance(rawpackage,str):return None
            # Validate originals: urlsplit/urljoin can discard leading spaces or empty delimiters.
            if any(ord(c)<=32 or c in '\\?#' for c in rawroot+rawpackage):return None
            if 'languageVersion' in package and (not isinstance(package['languageVersion'],str) or not re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)',package['languageVersion'])):return None
            part=urlsplit(rawpackage)
            if part.scheme or part.netloc or rawpackage.startswith('/'):return None
            directory=file_uri_path(urljoin(base,rawroot))
            exported=file_uri_path(urljoin(Path(directory).as_uri().rstrip('/')+'/',rawpackage))
            if not within(exported,directory) or directory in roots:return None
            # External packages may be inspected lexically, never read or followed.
            if not within(directory,anchor) and (within(anchor,exported) or within(exported,anchor)):return None
            entries.append((name,directory,exported));names.add(name);roots.add(directory)
        for i,(_,directory,exported) in enumerate(entries):
            for j,(_,other,_) in enumerate(entries):
                if i!=j and within(other,directory) and (within(other,exported) or within(exported,other)):return None
        return {name:posixpath.relpath(exported,anchor) if within(directory,anchor) else None
                for name,directory,exported in entries}
    except (ValueError,TypeError,UnicodeError):
        return None


def nodes(root):
    pending = [root] if root is not None else []
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.named_children))


def children(node):
    return [child for child in node.named_children if child.type not in ('comment', 'documentation_comment')]


def names(node):
    return [txt(child) for child in node.named_children if child.type == 'identifier']


def scope(*roots):
    blocked, opaque = set(), False
    for root in roots:
        for node in nodes(root):
            if 'pattern' in node.type:
                opaque = True
            if node.type in ('formal_parameter', 'initialized_variable_definition') or (node.type == 'function_signature' and node != root):
                name = node.child_by_field_name('name')
                if name is not None: blocked.add(txt(name))
                elif node.type == 'formal_parameter': blocked.update(names(node))
            elif node.type in ('initialized_identifier','static_final_declaration'):
                blocked.update(names(node)[:1])
            elif node.type in ('catch_parameters', 'for_loop_parts', 'inferred_parameters'):
                blocked.update(names(node))
            elif node.type == 'type_parameter':
                blocked.update(txt(child) for child in node.named_children if child.type == 'type_identifier')
    return dict(blocked=sorted(blocked), opaque=opaque)


def parameters(signature):
    group = next((n for n in signature.named_children if n.type == 'formal_parameter_list'), None)
    minimum, maximum = 0, 0
    for node in nodes(group):
        if node.type != 'formal_parameter': continue
        # Function-typed parameter signatures contain their own parameter list.
        parent = node.parent
        while parent is not None and parent != group and parent.type != 'formal_parameter':
            parent = parent.parent
        if parent != group: continue
        maximum += 1
        if node.parent.type != 'optional_formal_parameters': minimum += 1
    return minimum, maximum


def selector_name(node):
    if node.type != 'selector': return ''
    parts = children(node)
    if len(parts) == 1 and parts[0].type == 'unconditional_assignable_selector':
        ids = names(parts[0])
        if len(ids) == 1: return ids[0]
    return ''


def handle_call(ex, node, function_id, class_id=None):
    node = node.parent  # argument_part belongs to the invocation selector.
    siblings = [child for child in node.parent.children if child.type not in ('comment','documentation_comment')]
    position = siblings.index(node)
    before = siblings[:position]
    name, receiver, head = '', '', []
    if before and before[-1].type == 'identifier':
        head, name = before[-1:], txt(before[-1])
    elif len(before) >= 2 and before[-2].type == 'identifier' and selector_name(before[-1]):
        head, receiver, name = before[-2:], txt(before[-2]), selector_name(before[-1])
    if head:
        start = position-len(head)
        if start and siblings[start-1].type == 'selector': name = ''
    arguments = next((n for n in nodes(node) if n.type == 'arguments'), None)
    start = head[0] if name else node
    ex.calls.append(dict(caller=function_id, name=name or '<indirect>', recv='dart_selector' if receiver else 'dart_function',
                         recv_name=receiver, line=start.start_point[0]+1, column_bytes=start.start_point[1],
                         nargs=len(children(arguments)) if arguments is not None else 0,
                         splat=0, dynamic=int(not name), end=(node.end_point[0]+1,node.end_point[1])))
    return head if name else []


def collect(ex, root, owner):
    heads = set()
    for node in nodes(root):
        if node.type == 'selector' and any(child.type == 'argument_part' for child in node.named_children):
            argument = next(child for child in node.named_children if child.type == 'argument_part')
            for head in handle_call(ex, argument, owner): heads.add((head.start_byte,head.end_byte))
        elif node.type in ('new_expression','const_object_expression','cascade_section'):
            if any(child.type in ('arguments','argument_part') for child in node.named_children):
                ex.calls.append(dict(caller=owner,name='<indirect>',recv='dart_dynamic',recv_name='',
                    line=node.start_point[0]+1,column_bytes=node.start_point[1],nargs=None,splat=0,dynamic=1,
                    end=(node.end_point[0]+1,node.end_point[1])))
    expression_types = {'return_statement','expression_statement','argument','named_argument',
                        'function_body','function_expression_body','initialized_variable_definition',
                        'initialized_identifier','static_final_declaration','field_initializer','pair','record_field','record_literal','list_literal','set_or_map_literal',
                        'parenthesized_expression','conditional_expression'}
    for node in nodes(root):
        if node.type in ('new_expression','const_object_expression'): continue
        if node.type not in expression_types and not node.type.endswith('_expression'): continue
        items = children(node)
        if node.type in ('initialized_identifier','static_final_declaration','initialized_variable_definition','field_initializer'):
            name = next((i for i,n in enumerate(items) if n.type == 'identifier'), len(items))
            items = items[name+1:]
        if node.type == 'named_argument': items = items[1:]
        for i, child in enumerate(items):
            if child.type != 'identifier' or (child.start_byte,child.end_byte) in heads: continue
            receiver, name = '', txt(child)
            if i+1 < len(items) and items[i+1].type == 'selector':
                if (items[i+1].start_byte,items[i+1].end_byte) in heads: continue
                name = selector_name(items[i+1])
                if not name: continue
                receiver = txt(child)
            ex.references.append(dict(owner=owner, name=name, receiver=receiver, kind='dart.function_value',
                                      line=child.start_point[0]+1))


def extract(ex, root):
    module = ex.add_sym('<module>', 'module', None, root, sig='')
    def observe(owner, kind, node, value):
        ex.observations.append(dict(owner=owner,kind='dart.'+kind,callee='',line=node.start_point[0]+1,
                                    value=json.dumps(value,sort_keys=True),value_kind='json'))
    directives, exported = [], False
    for node in nodes(root):
        if node.type == 'library_export': exported = True
        if node.type != 'import_specification': continue
        uri = next((txt(n) for n in nodes(node) if n.type == 'uri'), '')
        plain = re.fullmatch(r"(['\"])([^'\"\\$%?#\s]+)\1",uri)
        path = plain[2] if plain else None
        prefix = names(node)
        filters = [(txt(n).split()[0],names(n)) for n in node.named_children if n.type == 'combinator']
        directives.append(dict(path=path,prefix=prefix[0] if prefix else '',filters=filters,
                               opaque=any(n.type=='configuration_uri' for n in nodes(node)) or any(txt(n)=='deferred' for n in node.children)))
        ex.imports.append((path or uri,prefix[0] if prefix else '',node.start_point[0]+1))
    observe(module,'file',root,dict(imports=directives,exports=exported,
            excluded=root.has_error or any(n.type in ('part_directive','part_of_directive') for n in root.named_children)))

    def declarations(container, parent, class_context=False):
        items = children(container)
        for i, node in enumerate(items):
            if node.type in ('class_definition','mixin_declaration','extension_declaration','enum_declaration'):
                name = txt(node.child_by_field_name('name')) or (names(node) or ['<anonymous@'+str(node.start_point[0]+1)+'>'])[0]
                sid = ex.add_sym(name,'class',parent,node,exported=int(not name.startswith('_')))
                observe(sid,'symbol',node,dict(role='class' if node.type=='class_definition' else 'other',name=name,static=False))
                body = node.child_by_field_name('body') or next((n for n in node.named_children if n.type in ('class_body','extension_body','enum_body')),None)
                if body is not None: declarations(body,sid,True)
                continue
            signature = node if node.type in ('function_signature','getter_signature','setter_signature') else next((n for n in node.named_children if n.type.endswith('_signature')),None) if node.type in ('method_signature','declaration') else None
            body = items[i+1] if i+1<len(items) and items[i+1].type=='function_body' else None
            if signature is not None:
                name = txt(signature.child_by_field_name('name')) or (names(signature) or ['<unnamed>'])[-1]
                if signature.type in ('constructor_signature','factory_constructor_signature','constant_constructor_signature'):
                    name = names(signature)[-1]
                sid = ex.add_sym(name,'method' if class_context else 'function',parent,node,
                        pmin=parameters(signature)[0],pmax=parameters(signature)[1],exported=int(not name.startswith('_')))
                if body is not None:
                    ex.syms[sid].update(end_line=body.end_point[0]+1,end_byte=body.end_byte)
                callable_ = signature.type=='function_signature' and body is not None
                static = any(child.type=='static' or txt(child)=='static' for child in node.children)
                observe(sid,'symbol',node,dict(role='method' if class_context else 'function' if callable_ else 'other',name=name,static=static and callable_))
                initializers=next((n for n in node.named_children if n.type=='initializers'),None)
                info=scope(signature,initializers,body);info['class_context']=class_context
                observe(sid,'scope',node,info)
                collect(ex,initializers,sid)
                if body is not None: collect(ex,body,sid)
            elif node.type in ('initialized_identifier_list','static_final_declaration_list','type_alias','declaration'):
                if node.type=='type_alias':
                    bindings=[next((txt(n) for n in node.named_children if n.type=='type_identifier'),'')]
                else:
                    bindings=[names(n)[0] for n in nodes(node) if n.type in ('initialized_identifier','static_final_declaration') and names(n)]
                for name in bindings:
                    sid=ex.add_sym(name,'variable',parent,node,exported=int(not name.startswith('_')))
                    observe(sid,'symbol',node,dict(role='other',name=name,static=False))
                observe(parent,'scope',node,dict(scope(node),class_context=class_context))
                collect(ex,node,parent)
    declarations(root,module)
    return ex


def config_inputs(contents):
    return {path:raw for path,raw in contents.items()
            if path=='.dart_tool/package_config.json' or path.endswith('/.dart_tool/package_config.json')}


def store_config(db,contents,root):
    inputs=config_inputs(contents)
    mapping=package_config(inputs['.dart_tool/package_config.json'],root) if list(inputs)==['.dart_tool/package_config.json'] else None
    mode='files' if not inputs else 'packages' if mapping is not None else 'opaque'
    value=json.dumps(dict(mode=mode,packages=mapping or {},root=Path(root).resolve().as_posix() if inputs else None),sort_keys=True)
    previous=db.execute("SELECT v FROM meta WHERE k='dart_config'").fetchone()
    db.execute("INSERT OR REPLACE INTO meta VALUES('dart_config',?)",(value,))
    return previous is None or previous[0]!=value


def fresh_package_config(db,root,contents):
    if not db.execute("SELECT 1 FROM files WHERE lang='dart' LIMIT 1").fetchone():return True
    inputs=config_inputs(contents)
    row=db.execute("SELECT v FROM meta WHERE k='config_hashes'").fetchone()
    expected=config_inputs(json.loads(row[0]) if row else {})
    if {path:hashlib.sha256(raw).hexdigest() for path,raw in inputs.items()}!=expected:return False
    row=db.execute("SELECT v FROM meta WHERE k='dart_config'").fetchone()
    if row is None:return not inputs
    previous=json.loads(row[0])
    return previous['root'] is None or previous['root']==Path(root).resolve().as_posix()


class DartLibraries:
    def __init__(self,db):
        row=db.execute("SELECT v FROM meta WHERE k='dart_config'").fetchone()
        self.config=json.loads(row[0]) if row else dict(mode='files',packages={})
        self.paths=dict(db.execute("SELECT id,path FROM files WHERE lang='dart'"))
        self.by_path={path:fid for fid,path in self.paths.items()}
        self.files,self.scopes,self.facts={},{},{}
        for fid,owner,kind,value in db.execute("SELECT file_id,owner_id,kind,value FROM observations WHERE kind IN ('dart.file','dart.scope','dart.symbol')"):
            fact=json.loads(value)
            if kind=='dart.file': self.files[fid]=fact
            elif kind=='dart.symbol': self.facts[owner]=fact
            else:
                previous=self.scopes.setdefault(owner,dict(blocked=[],opaque=False,class_context=False))
                previous['blocked']=sorted(set(previous['blocked'])|set(fact['blocked']))
                previous['opaque']|=fact['opaque'];previous['class_context']|=fact['class_context']
        self.symbols={sid:(fid,parent,kind) for sid,fid,parent,kind in db.execute("SELECT s.id,s.file_id,s.parent_id,s.kind FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.lang='dart'")}
        self.definitions=defaultdict(list)
        for sid,fact in self.facts.items():
            fid,parent,kind=self.symbols[sid]
            self.definitions[parent,fact['name']].append(sid)
        self.modules={fid:sid for sid,(fid,_,kind) in self.symbols.items() if kind=='module'}

    def namespace(self,fid,name):
        if self.files[fid]['excluded']: return None
        own=self.definitions[self.modules[fid],name]
        return own if own or not self.files[fid]['exports'] else None

    def imported(self,fid,name,prefix):
        if self.config['mode']=='opaque':return []
        candidates=set()
        for entry in self.files[fid]['imports']:
            if entry['prefix']!=prefix:continue
            if any((mode=='show' and name not in allowed) or (mode=='hide' and name in allowed) for mode,allowed in entry['filters']):continue
            path=entry['path']
            if entry['opaque'] or path is None or path.startswith('/') or '\\' in path:return []
            if path.startswith('package:'):
                target=self.package_target(path);identity=('package',path)
            else:
                if ':' in path:return []
                target=posixpath.normpath(posixpath.join(posixpath.dirname(self.paths[fid]),path))
                identity=('file',target)
                # The caller can be a file URI or a package URI. Both must bind
                # this relative import to the same source, not merely the same spelling.
                for package,directory in self.config['packages'].items():
                    if directory is None or not within(self.paths[fid],directory):continue
                    payload=self.paths[fid] if directory=='.' else self.paths[fid][len(directory)+1:]
                    virtual=posixpath.normpath(posixpath.join('/'+posixpath.dirname(payload),path)).lstrip('/')
                    if posixpath.normpath(posixpath.join(directory,virtual))!=target:return []
            if target is None:return []
            other=self.by_path.get(target)
            if other is None or target.startswith('../'):return []
            found=self.namespace(other,name)
            if found is None:return []
            candidates.update((identity,sid) for sid in found)
        # A package URI and a file URI can denote distinct libraries at one path.
        return [sid for _,sid in sorted(candidates)] if not name.startswith('_') else []

    def package_target(self,path):
        package,separator,payload=path.removeprefix('package:').partition('/')
        if not separator or not payload or any(part in ('','.','..') for part in payload.split('/')):return None
        if any(c in payload for c in '\\%?#:$\x00'):return None
        directory=self.config['packages'].get(package)
        return posixpath.normpath(posixpath.join(directory,payload)) if directory is not None else None

    def targets(self,fid,owner,name,receiver=''):
        if self.files[fid]['excluded']:return []
        info=self.scopes.get(owner,{})
        if info.get('opaque') or info.get('class_context') or (receiver or name) in info.get('blocked',[]):return []
        if not receiver:
            candidates=self.namespace(fid,name)
            if candidates is None:return []
            if not candidates:candidates=self.imported(fid,name,'')
            role='function'
        else:
            local=self.namespace(fid,receiver)
            if local:
                if len(local)!=1 or self.facts[local[0]]['role']!='class':return []
                candidates=self.definitions[local[0],name]
                role='method'
            else:
                if local is None:return []
                candidates=self.imported(fid,name,receiver);role='function'
        return [sid for sid in candidates if self.facts[sid]['role']==role and (role!='method' or self.facts[sid]['static'])] if len(candidates)==1 else []
