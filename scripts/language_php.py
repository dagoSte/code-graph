"""PHP extraction: calls and namespace-aware function/class lookup."""
from collections import defaultdict

EXTENSIONS = {".php": "php"}
DEFINITIONS = {"function_definition": "function", "method_declaration": "method", "class_declaration": "class",
               "interface_declaration": "interface", "trait_declaration": "trait", "enum_declaration": "enum"}
CALL_TYPES = ("function_call_expression", "member_call_expression", "scoped_call_expression",
              "object_creation_expression", "nullsafe_member_call_expression")


class PhpNames:
    """PHP namespace blocks and separate class/function import spaces, from indexed facts."""
    def __init__(self, imports, symbols):
        self.namespaces, self.uses, self.function_uses = defaultdict(list), {}, {}
        self.functions, self.classes = defaultdict(list), defaultdict(list)
        self.class_names, self.ambiguous_classes = defaultdict(list), set()
        for fid, module, name, line in imports:
            if module == '__ns__':
                self.namespaces[fid].append((line, name))
        for fid, module, name, line in imports:
            if module == '__ns__':
                continue
            scope, ns = self.scope(fid, line)
            table = self.function_uses if name.startswith('__function__:') else self.uses
            local = name[13:] if name.startswith('__function__:') else name
            table[(fid, scope, local.lower())] = module.lower() if ns is not None else ''
        for sid, fid, name, kind, parent_kind, line in symbols:
            _, ns = self.scope(fid, line)
            if kind in CLASSLIKE:
                self.class_names[name.lower()].append(sid)
                if ns is None:
                    self.ambiguous_classes.add(sid)
            if ns is None:
                continue
            qualified = ((ns + '\\') if ns else '') + name
            if kind == 'function' and parent_kind == 'module':
                self.functions[qualified.lower()].append(sid)
            elif kind in CLASSLIKE:
                self.classes[qualified.lower()].append(sid)

    @classmethod
    def from_db(cls, db):
        imports = list(db.execute("SELECT i.file_id,i.module,i.name,i.line FROM imports i JOIN files f ON f.id=i.file_id WHERE f.lang='php' ORDER BY i.rowid"))
        symbols = db.execute("""SELECT s.id,s.file_id,s.name,s.kind,p.kind,s.line FROM symbols s JOIN files f ON f.id=s.file_id
            LEFT JOIN symbols p ON p.id=s.parent_id WHERE f.lang='php'""")
        return cls(imports, symbols)

    @classmethod
    def from_extractor(cls, ex):
        return cls([(0,m,n,line) for m,n,line in ex.imports],
                   [(i,0,s['name'],s['kind'],ex.syms[s['parent']]['kind'] if s['parent'] is not None else None,s['line'])
                    for i,s in enumerate(ex.syms)])

    def scope(self, fid, line):
        entries = self.namespaces[fid]
        scope = next((entry for entry in reversed(entries) if entry[0] <= line), (0, ''))
        # The database has line precision: multiple namespace starts on one line are ambiguous.
        return (scope[0], None) if sum(entry[0] == scope[0] for entry in entries) > 1 else scope

    def qualified(self, fid, line, raw, function=False):
        if raw and raw.startswith('\\'):
            return [raw.lstrip('\\').lower()]
        scope, ns = self.scope(fid, line)
        if ns is None or not raw:
            return []
        raw = raw.lower()
        prefix = ns.lower() + '\\' if ns else ''
        if raw.startswith('namespace\\'):
            return [prefix + raw[10:]]
        head, sep, tail = raw.partition('\\')
        imported = (self.function_uses if function and not sep else self.uses).get((fid, scope, head))
        if imported:
            return [imported + ('\\' + tail if sep else '')]
        return [prefix + raw, raw] if function and not sep and prefix else [prefix + raw]

    def function_targets(self, fid, line, raw, literal=False):
        names = [raw.lstrip('\\').lower()] if literal else self.qualified(fid, line, raw, function=True)
        return next((self.functions[name] for name in names if self.functions.get(name)), [])

    def class_targets(self, fid, line, raw):
        return [sid for name in self.qualified(fid, line, raw) for sid in self.classes.get(name, [])]


def handle_call(extractor, n, func, cls):
    t = n.type
    arguments = n.child_by_field_name("arguments")
    if arguments is not None and any(child.type == 'variadic_placeholder' for child in arguments.named_children):
        function = n.child_by_field_name('function') or n.child_by_field_name('name')
        receiver = txt(n.child_by_field_name('scope') or n.child_by_field_name('object'))
        if receiver in ('$this', 'self', 'static'): receiver = 'self'
        extractor.references.append(dict(owner=func, name=txt(function), receiver=receiver, kind='callable', line=n.start_point[0]+1))
        return
    if t == "function_call_expression":
        fn = n.child_by_field_name("function")
        if fn is None:
            return
        if fn.type in ("name", "qualified_name", "relative_name"):
            name = last_seg(txt(fn))
            extractor.add_call(n, func, name, "none", txt(fn), dynamic=int(name in {e['origin']['name'] for e in extractor.catalog.role('php', 'dynamic')}))
        else:
            extractor.add_call(n, func, "", "none", dynamic=1)
    elif t in ("member_call_expression", "nullsafe_member_call_expression"):
        nm, obj = n.child_by_field_name("name"), n.child_by_field_name("object")
        if nm is None or nm.type != "name":
            # $w->$m(): the receiver's class (when known) is reached dynamically
            extractor.add_call(n, func, "", "obj", (None if txt(obj) == "$this" else extractor.type_of(obj, func, cls)) or "", dynamic=1)
            extractor.calls[-1]['php_receiver'] = obj
            return
        o = txt(obj)
        ty = None if o == "$this" else extractor.type_of(obj, func, cls)
        extractor.add_call(n, func, txt(nm), "self" if o == "$this" else ("typed" if ty else "obj"), ty or o.lstrip("$"))
    elif t == "scoped_call_expression":
        nm, sc = n.child_by_field_name("name"), txt(n.child_by_field_name("scope"))
        if nm is None or nm.type != "name":
            extractor.add_call(n, func, "", "class", dynamic=1)
            return
        if sc in ("self", "static"):
            extractor.add_call(n, func, txt(nm), "self")
        elif sc == "parent":
            extractor.add_call(n, func, txt(nm), "parent")
        elif sc.startswith("$"):
            extractor.add_call(n, func, txt(nm), "obj", sc.lstrip("$"))
        else:
            extractor.add_call(n, func, txt(nm), "class", sc)
    elif t == "object_creation_expression":
        tgt = next((c for c in n.named_children if c.type in ("name", "qualified_name", "relative_name")), None)
        if tgt is None:
            extractor.add_call(n, func, "", "new", dynamic=1)
        else:
            extractor.add_call(n, func, "__construct", "new", txt(tgt))
