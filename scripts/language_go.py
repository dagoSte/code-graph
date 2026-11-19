"""Go package facts and conservative local resolution; no compiler or project execution."""
from collections import defaultdict
import ast
import json
import posixpath
import re
import unicodedata

EXTENSIONS = {'.go': 'go'}
DEFINITIONS = {'function_declaration': 'function', 'method_declaration': 'method',
               'type_spec': 'class', 'type_alias': 'class'}
CALL_TYPES = ('call_expression', 'type_conversion_expression')
PLATFORMS = set('aix android darwin dragonfly freebsd hurd illumos ios js linux netbsd openbsd plan9 solaris wasip1 windows '
                '386 amd64 amd64p32 arm arm64 loong64 mips mipsle mips64 mips64le ppc64 ppc64le riscv64 s390x sparc64 wasm'.split())


def nodes(root):
    pending = [root] if root is not None else []
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.named_children))


def exported(name):
    return bool(name) and unicodedata.category(name[0]) == 'Lu'


def parameters(node):
    minimum, maximum = 0, 0
    for parameter in node.named_children if node is not None else ():
        if parameter.type not in ('parameter_declaration', 'variadic_parameter_declaration'):
            continue
        count = max(1, len(parameter.children_by_field_name('name')))
        maximum += count
        if parameter.type != 'variadic_parameter_declaration':
            minimum += count
        else:
            return minimum, None
    return minimum, maximum


def literal(node):
    text = txt(node)
    if node is not None and node.type == 'raw_string_literal':
        return text[1:-1].replace('\r', '')
    if node is not None and node.type == 'interpreted_string_literal':
        try:
            return ast.literal_eval(text)
        except (SyntaxError, ValueError):
            pass
    return None


def type_name(node):
    if node is None:
        return ''
    if node.type == 'type_identifier':
        return txt(node)
    if node.type == 'qualified_type':
        return txt(node.child_by_field_name('package'))+'.'+txt(node.child_by_field_name('name'))
    if node.type == 'generic_type':
        return type_name(node.child_by_field_name('type'))+'[]'
    if node.type in ('pointer_type', 'parenthesized_type'):
        child = next((child for child in node.named_children if child.type != 'comment'), None)
        return ('*' if node.type == 'pointer_type' else '')+type_name(child)
    return ''


def literal_type(node):
    pointer = ''
    while node is not None:
        if node.type == 'parenthesized_expression':
            node = next((child for child in node.named_children if child.type != 'comment'), None)
        elif node.type == 'unary_expression' and txt(node.child_by_field_name('operator')) == '&' and not pointer:
            pointer, node = '*', node.child_by_field_name('operand')
        else:
            break
    return pointer + type_name(node.child_by_field_name('type')) if node is not None and node.type == 'composite_literal' else ''


def direct_local(node, body):
    parent = node.parent
    while parent is not None and parent.type in ('var_spec_list', 'var_declaration', 'statement_list'):
        parent = parent.parent
    return parent == body


def bindings(function):
    """Whole-function shadowing; outer parameters/locals supply conservative receiver types."""
    found, seen = defaultdict(list), set()
    def declare(node, typed):
        names = node.children_by_field_name('name')
        declared_type = node.child_by_field_name('type')
        value = node.child_by_field_name('value')
        values = [child for child in value.named_children if child.type != 'comment'] if value is not None else []
        reads = {txt(child) for child in nodes(value) if child.type == 'identifier'} if typed and node.type == 'var_spec' else set()
        for index, name in enumerate(names):
            if txt(name) != '_':
                hint = type_name(declared_type) if typed else ''
                if typed and declared_type is None and node.type == 'var_spec' and len(names) == len(values):
                    hint = literal_type(values[index])
                if node.type == 'var_spec' and (txt(name) in seen or txt(name) in reads): hint = ''
                found[txt(name)].append(hint)
    for field in ('receiver', 'parameters', 'result'):
        group = function.child_by_field_name(field)
        for parameter in group.named_children if group is not None and group.type == 'parameter_list' else ():
            declare(parameter, True)
    for parameter in nodes(function.child_by_field_name('type_parameters')):
        if parameter.type == 'type_parameter_declaration':
            declare(parameter, False)
    for node in nodes(function.child_by_field_name('receiver')):
        if node.type == 'generic_type':
            arguments = node.child_by_field_name('type_arguments')
            for argument in arguments.named_children if arguments is not None else ():
                name = txt(argument)
                if name.isidentifier() and name != '_':
                    found[name].append('')
    body = function.child_by_field_name('body')
    for node in nodes(body):
        if node.type == 'identifier': seen.add(txt(node))
        if node.type in ('var_spec', 'const_spec', 'type_spec', 'type_alias'):
            declare(node, node.type == 'var_spec' and direct_local(node, body))
        elif node.type in ('short_var_declaration', 'range_clause', 'receive_statement') and ':=' in txt(node):
            left = node.child_by_field_name('left')
            names = [child for child in left.named_children if child.type != 'comment'] if left is not None else []
            right = node.child_by_field_name('right') if node.type == 'short_var_declaration' and direct_local(node, body) else None
            values = [child for child in right.named_children if child.type != 'comment'] if right is not None else []
            reads = {txt(child) for child in nodes(right) if child.type == 'identifier'}
            for index, name in enumerate(names):
                if name.type == 'identifier' and txt(name) != '_':
                    hint = literal_type(values[index]) if right is not None and len(names) == len(values) else ''
                    found[txt(name)].append(hint if txt(name) not in seen and txt(name) not in reads else '')
        elif node.type == 'type_switch_statement':
            alias = node.child_by_field_name('alias')
            for name in alias.named_children if alias is not None else ():
                found[txt(name)].append('')
        elif node.type == 'func_literal':
            for field in ('parameters', 'result'):
                group = node.child_by_field_name(field)
                for parameter in group.named_children if group is not None and group.type == 'parameter_list' else ():
                    declare(parameter, False)
    return dict(blocked=sorted(found), types={name: values[0] for name, values in found.items()
                                            if len(values) == 1 and re.fullmatch(r'\*?[\w]+(?:\.[\w]+)?', values[0])})


def call_head(node):
    head = node.child_by_field_name('function') or node.child_by_field_name('type')
    generic = False
    while head is not None and head.type in ('parenthesized_expression', 'parenthesized_type', 'index_expression', 'generic_type'):
        generic |= head.type in ('index_expression', 'generic_type')
        head = (head.child_by_field_name('operand') or head.child_by_field_name('type')
                or (head.named_children[0] if head.named_children else None))
    return head, generic


def handle_call(ex, node, function_id, class_id=None):
    head, generic = call_head(node)
    name, receiver, kind, dynamic = txt(head), '', 'go_dynamic', 1
    if head is not None and head.type in ('identifier', 'type_identifier'):
        kind, dynamic = 'go_function', 0
    elif head is not None and head.type in ('selector_expression', 'qualified_type'):
        name = txt(head.child_by_field_name('field') or head.child_by_field_name('name'))
        receiver = txt(head.child_by_field_name('operand') or head.child_by_field_name('package'))
        kind, dynamic = 'go_selector', 0
    arguments = node.child_by_field_name('arguments')
    children = [child for child in arguments.named_children if child.type != 'comment'] if arguments is not None else []
    if len(name) > 160 or len(receiver) > 160:
        kind, dynamic = 'go_dynamic', 1
    ex.calls.append(dict(caller=function_id, name=name[:160], recv=kind + ('_generic' if generic else ''),
                         recv_name=receiver[:160], line=node.start_point[0]+1, column_bytes=node.start_point[1],
                         nargs=len(children) if arguments is not None else 1,
                         splat=int(any(child.type == 'variadic_argument' for child in children)), dynamic=dynamic,
                         end=(node.end_point[0]+1, node.end_point[1])))


def collect(ex, body, owner):
    heads = set()
    for child in nodes(body):
        if child.type in CALL_TYPES:
            handle_call(ex, child, owner)
            head, _ = call_head(child)
            if head is not None and head.type in ('identifier', 'type_identifier', 'selector_expression', 'qualified_type'):
                heads.add((head.start_byte, head.end_byte, head.type))
    for child in nodes(body):
        if child.type not in ('identifier', 'selector_expression') or (child.start_byte, child.end_byte, child.type) in heads:
            continue
        if child.parent is not None and child.parent.type in ('selector_expression', 'parameter_declaration'):
            continue
        ancestor = child
        while ancestor.parent is not None and ancestor.parent.type == 'literal_element':
            ancestor = ancestor.parent
        if ancestor.parent is not None and ancestor.parent.type == 'keyed_element' and ancestor.parent.child_by_field_name('key') == ancestor:
            continue
        name, receiver = txt(child), ''
        if child.type == 'selector_expression':
            name, receiver = txt(child.child_by_field_name('field')), txt(child.child_by_field_name('operand'))
        ex.references.append(dict(owner=owner, name=name, receiver=receiver,
                                  kind='go.function_value', line=child.start_point[0]+1))


def extract(ex, root):
    module = ex.add_sym('<module>', 'module', None, root, sig='')
    def observe(owner, kind, node, value):
        ex.observations.append(dict(owner=owner, kind='go.'+kind, callee='', line=node.start_point[0]+1,
                                    value=json.dumps(value, sort_keys=True), value_kind='json'))
    package = next((txt(n.named_children[0]) for n in root.named_children if n.type == 'package_clause' and n.named_children), '')
    imports = []
    for node in nodes(root):
        if node.type == 'import_spec':
            path, alias = literal(node.child_by_field_name('path')), txt(node.child_by_field_name('name'))
            imports.append((path, alias))
            ex.imports.append((path or txt(node.child_by_field_name('path')), alias, node.start_point[0]+1))
    filename = posixpath.basename(ex.source_path or '')[:-3].removesuffix('_test')
    parts = filename.split('_')
    conditional = ((len(parts) > 1 and parts[-1] in PLATFORMS) or filename.startswith(('_', '.'))
                   or any(path == 'C' for path, _ in imports)
                   or any(n.type == 'comment' and re.match(r'//\s*(?:go:build|\+build|go:linkname)\b', txt(n))
                          for n in root.named_children))
    observe(module, 'file', root, dict(package=package, imports=imports,
                                     excluded=bool(conditional or root.has_error or not package)))
    module_blocked = set()
    for declaration in root.named_children:
        if declaration.type not in ('var_declaration', 'const_declaration'):
            continue
        pending = [declaration]
        while pending:
            spec = pending.pop()
            if spec.type in ('var_spec', 'const_spec'):
                value = spec.child_by_field_name('value')
                collect(ex, value, module)
                for node in nodes(value):
                    if node.type == 'func_literal':
                        module_blocked.update(bindings(node)['blocked'])
            else:
                pending.extend(spec.named_children)
    observe(module, 'locals', root, dict(blocked=sorted(module_blocked), types={}))
    types, declarations = {}, []
    for declaration in root.named_children:
        if declaration.type == 'type_declaration':
            for node in declaration.named_children:
                if node.type not in ('type_spec', 'type_alias'):
                    continue
                name = txt(node.child_by_field_name('name'))
                value = node.child_by_field_name('type')
                kind = 'interface' if value is not None and value.type == 'interface_type' else 'class'
                sid = ex.add_sym(name, kind, module, node, exported=int(exported(name)))
                types.setdefault(name, []).append(sid)
                observe(sid, 'symbol', node, dict(role='type', name=name, alias=node.type == 'type_alias',
                                                generic=node.child_by_field_name('type_parameters') is not None))
                if kind == 'interface':
                    for method in value.named_children:
                        if method.type != 'method_elem':
                            continue
                        method_name = txt(method.child_by_field_name('name'))
                        minimum, maximum = parameters(method.child_by_field_name('parameters'))
                        member = ex.add_sym(method_name, 'method', sid, method, pmin=minimum, pmax=maximum,
                                            exported=int(exported(method_name)))
                        observe(member, 'symbol', method, dict(role='method', name=method_name, receiver=name,
                                                               generic=False, body=False))
        elif declaration.type in ('function_declaration', 'method_declaration'):
            declarations.append(declaration)
    for node in declarations:
        name = txt(node.child_by_field_name('name'))
        receiver = node.child_by_field_name('receiver')
        parameter = next((child for child in receiver.named_children if child.type == 'parameter_declaration'), None) if receiver is not None else None
        receiver_type = type_name(parameter.child_by_field_name('type')) if parameter is not None else ''
        base = receiver_type.lstrip('*').split('[', 1)[0]
        parent = types[base][0] if len(types.get(base, [])) == 1 else module
        kind = 'method' if receiver is not None else 'function'
        minimum, maximum = parameters(node.child_by_field_name('parameters'))
        sid = ex.add_sym(name, kind, parent if kind == 'method' else module, node,
                        pmin=minimum, pmax=maximum, exported=int(exported(name)))
        if kind == 'method':
            ex.syms[sid]['qname'] = base + '.' + name
        observe(sid, 'symbol', node, dict(role=kind, name=name, receiver=base,
                                         generic=node.child_by_field_name('type_parameters') is not None or '[' in receiver_type,
                                         body=node.child_by_field_name('body') is not None))
        observe(sid, 'locals', node, bindings(node))
        collect(ex, node.child_by_field_name('body'), sid)
    return ex


def store_config(db, contents, paths):
    modules = {}
    for path in paths:
        if posixpath.basename(path) != 'go.mod':
            continue
        raw = contents.get(path, b'').decode('utf-8', 'replace')
        lines = re.findall(r'(?m)^\s*module\s+(?:"([^"\\\s]+)"|([^\s"`]+))\s*(?://[^\n]*)?$', raw)
        modules[posixpath.dirname(path)] = (lines[0][0] or lines[0][1]) if len(lines) == 1 and '/*' not in raw else None
    value = json.dumps(dict(modules=modules, workspace=any(posixpath.basename(p) == 'go.work' for p in paths)), sort_keys=True)
    previous = db.execute("SELECT v FROM meta WHERE k='go_config'").fetchone()
    db.execute("INSERT OR REPLACE INTO meta VALUES('go_config',?)", (value,))
    return previous is None or previous[0] != value


class GoPackages:
    def __init__(self, db):
        self.paths = dict(db.execute("SELECT id,path FROM files WHERE lang='go'"))
        row = db.execute("SELECT v FROM meta WHERE k='go_config'").fetchone()
        self.config = json.loads(row[0]) if row else dict(modules={}, workspace=False)
        self.files, self.locals, self.facts = {}, {}, {}
        for fid, owner, kind, value in db.execute("SELECT file_id,owner_id,kind,value FROM observations WHERE kind IN ('go.file','go.symbol','go.locals')"):
            if kind == 'go.file': self.files[fid] = json.loads(value)
            elif kind == 'go.locals': self.locals[owner] = json.loads(value)
            else: self.facts[owner] = json.loads(value)
        self.symbols = {sid: (fid, name, kind, public) for sid, fid, name, kind, public in db.execute(
            "SELECT s.id,s.file_id,s.name,s.kind,s.exported FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.lang='go'")}
        self.packages = defaultdict(set)
        self.package_files = defaultdict(list)
        self.definitions = defaultdict(list)
        for fid, info in self.files.items():
            key = (posixpath.dirname(self.paths[fid]), info['package'])
            self.packages[key[0]].add(key)
            self.package_files[key].append(fid)
        for sid, fact in self.facts.items():
            fid, name, kind, public = self.symbols[sid]
            key = (posixpath.dirname(self.paths[fid]), self.files[fid]['package'])
            self.definitions[key, fact['role'], fact.get('receiver', ''), name].append(sid)

    def bind_owners(self, db):
        modules = {fid: sid for sid, (fid, _, kind, _) in self.symbols.items() if kind == 'module'}
        owners = []
        for sid, fact in self.facts.items():
            if fact['role'] != 'method':
                continue
            fid = self.symbols[sid][0]
            candidates = [target for target in self.definitions[self.package(fid), 'type', '', fact['receiver']]
                          if self.visible(target, fid)]
            owners.append((candidates[0] if len(candidates) == 1 else modules[fid], sid))
        db.executemany('UPDATE symbols SET parent_id=? WHERE id=?', owners)

    def module(self, fid):
        directory = posixpath.dirname(self.paths[fid])
        while True:
            if directory in self.config['modules']:
                return directory, self.config['modules'][directory]
            if not directory: return None, None
            directory = posixpath.dirname(directory)

    def visible(self, sid, fid):
        target = self.symbols[sid][0]
        return (not self.files[target]['excluded'] and
                (self.paths[fid].endswith('_test.go') or not self.paths[target].endswith('_test.go')))

    def package(self, fid):
        return posixpath.dirname(self.paths[fid]), self.files[fid]['package']

    def imported(self, fid, alias, owner):
        if self.config['workspace'] or alias in self.locals.get(owner, {}).get('blocked', []):
            return None
        root, module = self.module(fid)
        if module is None: return None
        matches = set()
        for path, local in self.files[fid]['imports']:
            if path is None or local in ('_', '.') or not (path == module or path.startswith(module + '/')):
                continue
            directory = posixpath.normpath(posixpath.join(root, path[len(module):].lstrip('/'))).removeprefix('./')
            if directory == '.': directory = ''
            for key in self.packages.get(directory, ()):
                if (local or key[1]) != alias: continue
                if any(not self.files[other]['excluded'] and not self.paths[other].endswith('_test.go')
                       and self.module(other) == (root, module) for other in self.package_files[key]):
                    matches.add(key)
        return next(iter(matches)) if len(matches) == 1 else None

    def targets(self, fid, owner, name, receiver='', generic=False):
        if fid not in self.files or self.files[fid]['excluded']:
            return []
        scope = self.locals.get(owner, {})
        package = self.package(fid)
        if not receiver:
            if name in scope.get('blocked', []) or name == 'init' or any(local == '.' for _, local in self.files[fid]['imports']):
                return []
            candidates = self.definitions[package, 'function', '', name]
        elif receiver in scope.get('types', {}):
            raw = scope['types'][receiver].lstrip('*')
            alias, dot, type_name = raw.partition('.')
            if alias in scope.get('blocked', []): return []
            type_package = self.imported(fid, alias, owner) if dot else package
            type_name = type_name if dot else alias
            types = [sid for sid in self.definitions[type_package, 'type', '', type_name] if self.visible(sid, fid)
                     and (type_package == self.package(fid) or self.symbols[sid][3])]
            if len(types) != 1 or self.symbols[types[0]][2] == 'interface' or self.facts[types[0]]['alias'] or self.facts[types[0]]['generic']:
                return []
            package = type_package
            candidates = self.definitions[package, 'method', type_name, name]
        else:
            package = self.imported(fid, receiver, owner)
            candidates = self.definitions[package, 'function', '', name]
        candidates = [sid for sid in candidates if self.visible(sid, fid) and self.facts[sid].get('body')
                      and (not generic or self.facts[sid]['generic'])
                      and (package == self.package(fid) or self.symbols[sid][3])]
        return candidates if len(candidates) == 1 else []
