#!/usr/bin/env python3
"""code-graph engine: indice locale (tree-sitter + SQLite) di simboli, chiamate e import.

Uso:  python3 cg.py <comando> [args]   (dalla root del progetto, o con --root)
Comandi: index, map, find, show, callers, callees, impact, path, context, check, dead, stats, export, selftest
Output compatto, una riga per elemento: pensato per far leggere a un agente solo ciò che serve.
"""
import argparse, ast, base64, contextlib, hashlib, json, os, re, secrets, sqlite3, stat, subprocess, sys, tempfile, time
from pathlib import Path
from collections import defaultdict, deque
import runpy

VERSION = "1.9.0"
INDEX_VERSION = "1.4.0"

_LANGUAGE_REGISTRY = runpy.run_path(str(Path(__file__).with_name("language_adapters.py")))
LANGUAGE_ADAPTERS = _LANGUAGE_REGISTRY["ADAPTERS"]
LANG_BY_EXT = _LANGUAGE_REGISTRY["LANG_BY_EXT"]
DEF_TYPES = {family: adapter["DEFINITIONS"] for family, adapter in LANGUAGE_ADAPTERS.items()}
CLASSLIKE = _LANGUAGE_REGISTRY["CLASSLIKE"]
fam = _LANGUAGE_REGISTRY["fam"]
txt = _LANGUAGE_REGISTRY["txt"]
last_seg = _LANGUAGE_REGISTRY["last_seg"]
PythonFileLoads = LANGUAGE_ADAPTERS["python"]["PythonFileLoads"]
PhpNames = LANGUAGE_ADAPTERS["php"]["PhpNames"]
JsModuleFacts = LANGUAGE_ADAPTERS["js"]["JsModuleFacts"]
JsModules = LANGUAGE_ADAPTERS["js"]["JsModules"]
js_name = LANGUAGE_ADAPTERS["js"]["js_name"]
js_name_text = LANGUAGE_ADAPTERS["js"]["js_name_text"]
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".build", ".dart_tool", ".next", ".nuxt", "__pycache__", ".venv", "venv",
             "env", ".tox", ".mypy_cache", ".pytest_cache", "coverage", ".code-graph", "storage", "cache", ".idea", ".vscode"}
MAX_BYTES = 1_000_000
DB_DIR = ".code-graph"

# Python and PHP APIs and start files: scripts/catalog/*.json (docs/CATALOG.md).
DYNAMIC_FUNCS = {"js": {"eval", "Function"}}
MAGIC = re.compile(r"^(__\w+__|__construct|__destruct|__call|__callStatic|__get|__set|__isset|__unset|__toString|__invoke|"
                   r"__clone|__sleep|__wakeup|__serialize|__unserialize|__set_state|__debugInfo|constructor|main|setUp|tearDown|"
                   r"setUpBeforeClass|tearDownAfterClass)$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, path TEXT UNIQUE, lang TEXT, sha TEXT, mtime REAL, loc INT);
CREATE TABLE IF NOT EXISTS symbols(id INTEGER PRIMARY KEY, file_id INT, name TEXT, qname TEXT, kind TEXT, parent_id INT,
  line INT, end_line INT, pmin INT, pmax INT, sig TEXT, exported INT, visibility TEXT);
CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, file_id INT, caller_id INT, name TEXT, recv TEXT, recv_name TEXT,
  line INT, nargs INT, splat INT, dynamic INT);
CREATE TABLE IF NOT EXISTS imports(file_id INT, module TEXT, name TEXT, line INT);
CREATE TABLE IF NOT EXISTS import_bindings(file_id INT, module TEXT, original TEXT, local TEXT);
CREATE TABLE IF NOT EXISTS symbol_refs(id INTEGER PRIMARY KEY, file_id INT, owner_id INT, target_id INT,
  name TEXT, receiver TEXT, kind TEXT, line INT);
CREATE TABLE IF NOT EXISTS module_bindings(file_id INT, scope_id INT, local TEXT, module TEXT);
CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, file_id INT, owner_id INT, kind TEXT,
  callee TEXT, line INT, value TEXT, value_kind TEXT);
CREATE TABLE IF NOT EXISTS assignments(file_id INT,class_id INT,name TEXT,line INT,form TEXT,certainty TEXT);
CREATE TABLE IF NOT EXISTS includes(file_id INT,target TEXT,line INT,certainty TEXT);
CREATE TABLE IF NOT EXISTS guarded_definitions(file_id INT,symbol_id INT,line INT);
CREATE INDEX IF NOT EXISTS i_assignment_class ON assignments(class_id,name);
CREATE TABLE IF NOT EXISTS bases(symbol_id INT, base TEXT, base_id INT);
CREATE TABLE IF NOT EXISTS edges(caller_id INT, callee_id INT, call_id INT, conf TEXT);
CREATE INDEX IF NOT EXISTS i_sym_name ON symbols(name); CREATE INDEX IF NOT EXISTS i_sym_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS i_sym_parent ON symbols(parent_id); CREATE INDEX IF NOT EXISTS i_calls_file ON calls(file_id);
CREATE INDEX IF NOT EXISTS i_obs_owner ON observations(owner_id,line);
CREATE INDEX IF NOT EXISTS i_obs_file ON observations(file_id,line);
CREATE INDEX IF NOT EXISTS i_e_call ON edges(call_id);
CREATE INDEX IF NOT EXISTS i_e_caller ON edges(caller_id); CREATE INDEX IF NOT EXISTS i_e_callee ON edges(callee_id);
"""


def module_key(m):
    """'./api.js' -> 'api', 'app.util' -> 'util', 'App\\Repo\\User' -> 'user'."""
    m = m.replace("\\", "/").rstrip("/")
    if "/" in m or m.startswith("."):
        b = os.path.basename(m)
        return (os.path.splitext(b)[0] if os.path.splitext(b)[1] in LANG_BY_EXT else b).lower()
    return last_seg(m).lower()


# ----------------------------------------------------------------- estrazione
class Extractor:
    def __init__(self, lang, src, source_path=None, root=None, catalog=None):
        self.lang, self.f, self.src = lang, fam(lang), src
        self.adapter = LANGUAGE_ADAPTERS[self.f]
        self.source_path, self.root = source_path, root
        # cmd_index passes the catalog it loaded once; other callers load it lazily.
        self.catalog = catalog if catalog is not None else catalog_for(root)
        self.py = python_catalog(self.catalog)
        self.root_node, self.name_sites, self.rebinds_freely = None, {}, None  # Python decorators: python_sites()
        self.module_writes = {}
        self.scoped_names = {}
        self.scoped_call_depth = 0
        self.syms, self.calls, self.imports, self.bases = [], [], [], []
        self.bindings = []
        if self.f == 'php':
            self.php_callbacks = []
            self.php_variables, self.php_receiver_cache = defaultdict(list), {}
        self.modules, self.references, self.observations = {}, [], []
        self.module_bindings, self.shadowed = [], defaultdict(set)
        self.assignments, self.includes, self.guarded, self.self_receivers = [], [], [], {}
        self.vtypes, self.ctypes = defaultdict(dict), defaultdict(dict)  # inferenza tipi leggera: var/attributo -> classe
        self.js_modules = JsModuleFacts(self) if self.f == "js" else None

    def run(self, root):
        self.root_node = root
        if 'extract' in self.adapter:
            return self.adapter['extract'](self, root)
        mod = self.add_sym("<module>", "module", None, root, sig="")
        self.walk(root, mod, None, mod)
        if self.f == 'php':
            self.php_finish_callbacks()
        if self.js_modules is not None:
            self.js_modules.finish()
        if self.f == 'python' and self.source_path is not None:
            loaded = PythonFileLoads(self.src, self.source_path, self.root).calls()
            for call in self.calls:
                binding = loaded.get((call['line'], call['column_bytes']) + call['end'])
                if binding is not None:
                    name, path, confidence = binding[:3]
                    owner = binding[3] if len(binding) > 3 else None  # Class(...).method(): method of the loaded class
                    call.update(name=name, recv='loaded_' + confidence if path else 'dynamic_import',
                                recv_name=(path + '#' + owner if owner else path) if path else call['recv_name'],
                                dynamic=int(path is None))
        return self

    def add_sym(self, name, kind, parent, node, sig=None, pmin=None, pmax=None, exported=0, vis=None):
        idx = len(self.syms)
        q = name if parent is None or self.syms[parent]["kind"] == "module" else f'{self.syms[parent]["qname"]}.{name}'
        if sig is None:
            sig = self.src[node.start_byte:node.end_byte].split(b"\n", 1)[0].decode("utf-8", "replace").strip()[:160]
        self.syms.append(dict(name=name, qname=q, kind=kind, parent=parent, line=node.start_point[0] + 1,
                              end_line=node.end_point[0] + 1, start_byte=node.start_byte, end_byte=node.end_byte,
                              pmin=pmin, pmax=pmax, sig=sig, exported=exported, vis=vis))
        return idx

    def walk(self, node, scope, cls, func):
        stack = [(node, scope, cls, func)]
        while stack:
            n, scope, cls, func = stack.pop()
            t = n.type
            if self.f == 'php' and t == 'variable_name' and n.parent.type != 'property_element':
                self.php_variables[func, txt(n)].append(n)
            elif self.f == 'php' and t == 'member_access_expression' and txt(n.child_by_field_name('object')) == '$this':
                self.php_variables[cls, txt(n)].append(n)
            elif self.f == 'php' and t in ('property_element','property_promotion_parameter'):
                self.php_variables[cls, '$this->' + txt(n.child_by_field_name('name')).lstrip('$')].append(n)
            new_scope, new_cls, new_func = scope, cls, func
            self.record_facts(n, scope, cls, func)
            kind = DEF_TYPES[self.f].get(t)
            name = None
            if kind:
                name = txt(n.child_by_field_name("name"))
            elif self.f == "js" and t == "variable_declarator":
                v = n.child_by_field_name("value")
                if v is not None and v.type in ("arrow_function", "function_expression", "function", "class"):
                    kind = "class" if v.type == "class" else "function"
                    name = txt(n.child_by_field_name("name"))
            elif self.f == "js" and t in ("assignment_expression", "pair"):
                # CommonJS/prototype: app.get = function(){}, res.send = function send(){}, { key: () => {} }
                v = n.child_by_field_name("right" if t == "assignment_expression" else "value")
                if v is not None and v.type in ("arrow_function", "function_expression", "function"):
                    left = n.child_by_field_name("left" if t == "assignment_expression" else "key")
                    lt = txt(left)
                    if lt not in ("module.exports", "exports"):
                        name = last_seg(lt.replace("prototype.", "")).strip("'\"")
                        kind = "method" if t == "pair" or "." in lt else "function"
                    else:
                        name, kind = txt(v.child_by_field_name("name")) or "exports", "function"
                elif t == "assignment_expression" and v is not None and v.type == "class":
                    # module.exports = class Service {...}, exports.Service = class {...}: a class symbol, named as functions are
                    lt = txt(n.child_by_field_name("left"))
                    name = (txt(v.child_by_field_name("name")) or "exports") if lt in ("module.exports", "exports") else \
                        last_seg(lt.replace("prototype.", "")).strip("'\"")
                    kind = "class"
            elif self.f == "js" and t in ("public_field_definition", "field_definition"):
                v = n.child_by_field_name("value")
                if v is not None and v.type in ("arrow_function", "function_expression"):
                    kind, name = "method", txt(n.child_by_field_name("name") or n.child_by_field_name("property"))
            if kind and name:
                if kind == "function" and cls is not None and self.syms[scope]["kind"] in CLASSLIKE:
                    kind = "method"
                pmin, pmax = self.params(n, kind)
                exported = 1 if (n.parent is not None and n.parent.type == "export_statement") or \
                    (n.parent is not None and n.parent.parent is not None and n.parent.parent.type == "export_statement") else 0
                vis = None
                if self.f == "php":
                    for c in n.children:
                        if c.type == "visibility_modifier":
                            vis = txt(c)
                elif self.f == "python" and name.startswith("_") and not name.startswith("__"):
                    vis = "private"
                elif self.f == "js":
                    for c in n.children:
                        if c.type == "accessibility_modifier":
                            vis = txt(c)
                    if name.startswith("#"):
                        vis = "private"
                sidx = self.add_sym(name, kind, scope, n, pmin=pmin, pmax=pmax, exported=exported, vis=vis)
                if self.f == 'php':
                    ancestor = n.parent
                    while ancestor is not None:
                        if ancestor.type == 'if_statement':
                            condition, body = ancestor.child_by_field_name('condition'), ancestor.child_by_field_name('body')
                            if body is not None and body.start_byte <= n.start_byte < body.end_byte and re.fullmatch(r"\(\s*!\s*(?:function|class|interface|trait)_exists\(\s*['\"]" + re.escape(name) + r"['\"]\s*\)\s*\)", txt(condition)):
                                self.guarded.append((sidx, ancestor.start_point[0]+1))
                        ancestor = ancestor.parent
                if self.f == "python" and n.parent is not None and n.parent.type == "decorated_definition":
                    for decorator in n.parent.named_children:
                        if decorator.type == "decorator" and self.catalog_callback(txt(decorator).lstrip("@").strip(), scope):
                            self.references.append(dict(owner=scope, name=name, receiver="", kind="callback", line=decorator.start_point[0] + 1))
                        elif decorator.type == "decorator" and self.registers(decorator, scope):
                            # @cli.command(), @app.route(...), @command('x'): the decorator keeps a reference to the function
                            self.references.append(dict(owner=scope, name=name, receiver="", kind="decorator", line=decorator.start_point[0] + 1))
                if self.f == "python" and n.parent is not None and n.parent.type == "decorated_definition":
                    self.observe_registration(n, sidx, scope)
                new_scope = sidx
                if kind in CLASSLIKE:
                    new_cls = sidx
                    value = (n.child_by_field_name("value") or n.child_by_field_name("right")) if self.f == "js" else None
                    self.collect_bases(value if value is not None and value.type == "class" else n, sidx)  # X = class extends B
                else:
                    new_func = sidx
                    self.param_types(n, sidx, cls)
            elif t in self.call_types():
                self.handle_call(n, func, cls)
            if t in ("assignment_expression", "assignment", "variable_declarator", "property_declaration",
                     "public_field_definition", "augmented_assignment") and not (kind and name):
                self.track_assign(n, func, cls)
            elif t == "namespace_definition" and self.f == "php":
                self.imports.append(("__ns__", txt(n.child_by_field_name("name")), n.start_point[0] + 1))
            elif t in ("import_statement", "import_from_statement", "namespace_use_clause"):
                self.handle_import(n, scope)
            elif self.f == "python" and t in ("for_statement", "with_item", "except_clause", "except_group_clause",
                                              "named_expression", "case_clause"):
                self.shadow_targets(n, func)
            if self.js_modules is not None and (t in JsModuleFacts.NODES or new_scope != scope):
                self.js_modules.visit(n, scope, func, new_scope)
            for c in reversed(n.children):
                stack.append((c, new_scope, new_cls, new_func))

    # ---- inferenza dei tipi (solo casi certi: new X, parametri tipizzati, proprietà tipizzate)
    @staticmethod
    def clean_type(t):
        t = re.sub(r"^\s*:\s*", "", t or "").strip()
        m = re.match(r"^(Optional|Annotated)\[(.+)\]$", t)
        if m:
            t = m.group(2).split(",")[0]
        parts = [p.strip() for p in re.split(r"[|&]", t) if p.strip().lower() not in ("null", "none")]
        if len(parts) != 1:
            return None
        for part in parts:
            part = re.sub(r"[<\[].*", "", part.strip().lstrip("?")).strip("'\" ")
            if part and part.lower() not in ("null", "none", "false", "true", "mixed", "self", "static", "array", "string",
                                             "int", "float", "bool", "object", "callable", "iterable", "void", "any",
                                             "unknown", "number", "str", "dict", "list", "tuple", "set") \
                    and re.match(r"^[\\\w.$]+$", part):
                return part
        return None

    def param_types(self, n, sidx, cls):
        p = n.child_by_field_name("parameters")
        if p is None:
            v = n.child_by_field_name("value")
            p = v.child_by_field_name("parameters") if v is not None else None
        if p is None:
            return
        if self.f == 'python' and self.syms[sidx]['kind'] == 'method' and p.named_children:
            first = p.named_children[0]
            decorators = txt(n.parent)[:max(0, n.start_byte-n.parent.start_byte)] if n.parent is not None and n.parent.type == 'decorated_definition' else ''
            if '@staticmethod' not in decorators:
                raw = txt(first.child_by_field_name('name') or first).split(':',1)[0].split('=',1)[0].strip()
                if raw.isidentifier(): self.self_receivers[sidx] = raw
        for c in p.named_children:
            if self.f == "python":
                parameter = c if c.type == "identifier" else c.child_by_field_name("name") or c.child_by_field_name("pattern")
                if parameter is None and c.named_children:
                    parameter = c.named_children[0]
                if parameter is not None:
                    self.shadowed[sidx].add(txt(parameter).lstrip("*"))
            ty = c.child_by_field_name("type")
            if ty is None:
                continue
            nm = c.child_by_field_name("name") or c.child_by_field_name("pattern")
            if nm is None and c.named_children and c.named_children[0].type == "identifier":
                nm = c.named_children[0]
            t = self.python_type(txt(ty)) if self.f == "python" else self.clean_type(txt(ty))
            if nm is None or not t:
                continue
            var = txt(nm).lstrip("$")
            self.vtypes[sidx][var] = t
            if c.type == "property_promotion_parameter" and cls is not None:
                self.ctypes[cls][var] = t
            if self.f == "js" and cls is not None and any(x.type == "accessibility_modifier" for x in c.children):
                self.ctypes[cls][var] = t  # TS: constructor(private repo: Repo)

    def python_type(self, raw):
        base = re.sub(r"\[.*", "", raw.strip().strip("\"'"))
        builtins = {"dict", "list", "tuple", "set", "str", "bytes", "int", "float", "bool"}
        aliases = {"Dict": "dict", "Mapping": "dict", "MutableMapping": "dict", "List": "list", "Sequence": "list", "Set": "set", "Tuple": "tuple"}
        if any(sym["name"] == base and sym["kind"] in ("class", "function") for sym in self.syms):
            return base
        original = base.removeprefix("builtins.")
        if original in builtins:
            if not any(sym["name"] == original and sym["kind"] in ("class", "function") for sym in self.syms):
                return "__builtin__:" + original
        if base.startswith("typing.") and base[7:] in aliases:
            return "__builtin__:" + aliases[base[7:]]
        for module, imported, local in self.bindings:
            if local == base and module in ("typing", "collections.abc") and imported in aliases:
                return "__builtin__:" + aliases[imported]
        return self.clean_type(raw)

    def is_shadowed(self, name, owner):
        current = owner
        while current is not None:
            if name in self.shadowed.get(current, set()):
                return True
            current = self.syms[current]["parent"]
        return False

    def binding_shadowed_local(self, name, owner):
        """True when name is bound inside a function between owner and the module (parameter, assignment, loop target)."""
        current = owner
        while current is not None and self.syms[current]["kind"] != "module":
            if self.syms[current]["kind"] != "class" and name in self.shadowed.get(current, set()):
                return True
            current = self.syms[current]["parent"]
        return False

    def binding_shadowed(self, name, owner):
        current = owner
        while current is not None:
            if name in self.shadowed.get(current, set()) or any(
                    symbol['parent'] == current and symbol['name'] == name
                    and symbol['kind'] in ('function', 'class') for symbol in self.syms):
                return True
            current = self.syms[current]['parent']
        return False

    def imported_module(self, name, owner):
        current = owner
        while current is not None:
            if self.binding_shadowed(name, current):
                return None
            matches = [module for scope, local, module in self.module_bindings if scope == current and local == name]
            if matches:
                return matches[-1]
            current = self.syms[current]["parent"]
        return None

    def python_origins(self, text, table, owner=0):
        """The origins (via, module, name) of a catalog table that a dotted Python name is proven by import to have here:
        a from-import binding (alias followed) or an `import module` binding; a shadowed name proves nothing.
        The catalog and import checks come first: the shadowing walks run only for a name that could match."""
        first, dot, rest = text.partition(".")
        found = {origin for origin in (("import", module, original + (dot + rest if rest else "")) for module, original, local in self.bindings
                                       if local == first) if origin in table}
        if found and self.binding_shadowed(first, owner):
            found = set()
        if dot:
            parts = rest.split(".")  # import a  then a.b.c: the module may be a, a.b, ...
            under = lambda module: {("import", ".".join([module] + parts[:cut]), ".".join(parts[cut:])) for cut in range(len(parts))} & table.keys()
            if any(under(module) for _, local, module in self.module_bindings if local == first):
                module = self.imported_module(first, owner)
                found |= under(module) if module else set()
        return found

    def catalog_callback(self, name, owner=0):
        """The catalog callback entry of a call such as atexit.register(f), proven by import; None otherwise."""
        return next((self.py["callback"][o] for o in sorted(self.python_origins(name, self.py["callback"], owner))), None)

    def registers(self, decorator, scope):
        """Does this decorator keep the function referenced? Only a decorator proven pure here does not. Its name
        must have one binding in the whole file (python_sites: any scope or branch, no write of any kind), which is
        an import of a catalog pure decorator or a local identity def; a builtin (staticmethod) has none at all;
        @x.setter needs x to be a @property of this class. A call is pure only when the callee is proven pure and
        cannot hand back one of its arguments: a local identity factory, or a catalog decorator called with literal
        arguments only (functools.cache(register) wraps register). Any doubt follows the catalog default for
        unrecognised decorators (Python: it registers)."""
        expression = decorator.named_children[0] if decorator.named_children else None
        call = expression if expression is not None and expression.type == "call" else None
        head = txt(call.child_by_field_name("function") if call is not None else expression) if expression is not None else ""
        if not head:
            return False
        default, pure = self.py["default"] == "registrar", self.py["pure_decorator"]
        if not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", head) or self.root_node.has_error or self.python_rebinds_freely():
            return default
        arguments = [a for a in call.child_by_field_name("arguments").named_children if a.type != "comment"] if call is not None else []
        literal = all(self.python_literal(argument) for argument in arguments)
        first, dot, rest = head.partition(".")
        sites = self.python_sites(first)
        site = sites[0] if len(set(sites)) == 1 else ()
        if site[:1] == ("from",) and literal and ("import", site[1], site[2] + (dot + rest if rest else "")) in pure \
                and not self.python_module_written(site[1]):
            return False
        if site[:1] == ("import",) and dot and literal:
            parts = rest.split(".")  # import a.b binds a: the module may be a, a.b, ...
            if any(("import", ".".join([site[1]] + parts[:cut]), ".".join(parts[cut:])) in pure for cut in range(len(parts))):
                return False
        if not sites and not dot and literal and ("builtin", "", first) in pure:
            return False  # staticmethod, classmethod, property
        if call is None and rest in ("setter", "getter", "deleter") and ("instance", "builtins", "property." + rest) in pure \
                and self.python_property(first, decorator):
            return False  # @x.setter on a @property x of this class
        if site[:1] == ("def",) and not dot and self.python_identity(site) == ("factory" if call is not None else "plain"):
            return False  # def identity(f): return f, used as @identity; a factory of identities, used as @factory(...)
        return default

    def python_sites(self, name):
        """How name is bound in this Python file (cached), from its occurrences in the tree: ('from', module, name) and
        ('import', module) for imports, ('def', start, end) for a def, ('store',) for a write (assignment, for, with
        or walrus target, del, global, setattr's first argument), ('other',) for any use not known to be harmless.
        Harmless uses are left out: a decorator head, a call, an attribute read, a module path of an import."""
        if name not in self.name_sites:
            sites = []
            for match in re.finditer(rb"(?<![\w.])" + re.escape(name.encode()) + rb"(?!\w)", self.src):
                node = self.root_node.descendant_for_byte_range(match.start(), match.end())
                if node.type == "identifier" and (node.start_byte, node.end_byte) == match.span():  # not a string or comment
                    site = self.python_site(node)
                    if site is not None:
                        sites.append(site)
            self.name_sites[name] = sites
        return self.name_sites[name]

    def python_site(self, node):
        parent = node.parent
        if self.python_store(node):
            return ("store",)
        if parent.type in ("function_definition", "class_definition") and parent.child_by_field_name("name") == node:
            return ("def", node.start_byte, node.end_byte) if parent.type == "function_definition" else ("other",)
        statement = next((a for a in (parent, parent.parent, parent.parent and parent.parent.parent)
                          if a is not None and a.type in ("import_statement", "import_from_statement")), None)
        if statement is not None:
            module = statement.child_by_field_name("module_name")
            if module is not None and module.start_byte <= node.start_byte < module.end_byte:
                return None  # from <module> import ...: the module path binds nothing
            if parent.type == "aliased_import":
                target = txt(parent.child_by_field_name("name")) if parent.child_by_field_name("alias") == node else None
            elif parent.type == "dotted_name" and parent.parent == statement:
                target = txt(node) if parent.named_children[0] == node and (statement.type == "import_statement" or parent.named_child_count == 1) else None
            else:
                target = None  # inside the path of `import a.b as c` or `from m import a as c`: not bound here
            if target is None:
                return None
            return ("from", txt(module), target) if statement.type == "import_from_statement" else ("import", target)
        if parent.type == "decorator" or (parent.type == "call" and parent.child_by_field_name("function") == node) \
                or (parent.type == "attribute" and parent.child_by_field_name("object") == node):
            return None
        return ("other",)

    @staticmethod
    def python_store(node):
        """Is this node written: inside an assignment or for target, del, global/nonlocal, a with/except `as` target,
        a walrus name, or setattr/delattr's first argument?"""
        child, parent = node, node.parent
        while parent is not None and parent.type not in ("expression_statement", "block", "module", "decorator"):
            kind = parent.type
            if kind in ("assignment", "augmented_assignment", "for_statement", "for_in_clause") and parent.child_by_field_name("left") == child:
                return True
            if kind in ("delete_statement", "as_pattern_target", "global_statement", "nonlocal_statement") \
                    or (kind == "named_expression" and parent.child_by_field_name("name") == child):
                return True
            if kind == "argument_list" and parent.named_children and parent.named_children[0] == child and parent.parent.type == "call" \
                    and txt(parent.parent.child_by_field_name("function")) in ("setattr", "delattr"):
                return True
            child, parent = parent, parent.parent
        return False

    def python_rebinds_freely(self):
        """Can this file rebind names without spelling them (cached)? A star import, exec or eval, globals(), locals()
        or vars() used other than to read or copy them, or a write through builtins or sys.modules."""
        if self.rebinds_freely is None:
            self.rebinds_freely = self.python_namespace_written()
            for match in re.finditer(rb"(?<![\w.])(import|globals|locals|vars)(?!\w)|(?<!\w)(exec|eval)(?!\w)", self.src) if not self.rebinds_freely else ():
                node, word = self.root_node.descendant_for_byte_range(match.start(), match.end()), match.group(1) or match.group(2)
                if word == b"import":
                    found = node.parent is not None and node.parent.type == "import_from_statement" and any(c.type == "wildcard_import" for c in node.parent.children)
                elif node.type != "identifier" or (node.start_byte, node.end_byte) != match.span():
                    continue
                elif word in (b"exec", b"eval"):
                    found = self.python_execution_use(node)
                else:
                    found = not self.python_namespace_read(node)
                if found:
                    self.rebinds_freely = True
                    break
        return self.rebinds_freely

    def python_execution_use(self, node):
        """A callable use/escape of exec or eval, including an imported builtins module; a local def/lambda is not it."""
        statement = next((p for p in (node.parent, node.parent.parent, node.parent.parent and node.parent.parent.parent)
                          if p is not None and p.type in ("import_statement", "import_from_statement")), None)
        if statement is not None:
            module = statement.child_by_field_name("module_name")
            return module is None or not module.start_byte <= node.start_byte < module.end_byte
        parent, expression = node.parent, node
        if parent.type == "attribute" and parent.child_by_field_name("attribute") == node:
            obj = parent.child_by_field_name("object")
            if obj.type != "identifier":
                return False
            origins = [s for s, _ in self.python_scoped_sites(txt(obj), obj)]
            if not origins or set(origins) != {("import", "builtins")}:
                return False
            expression = parent
        else:
            if self.python_binds(node) or self.python_store(node) \
                    or parent.type == "keyword_argument" and parent.child_by_field_name("name") == node:
                return False
            sites = [(s, n) for s, n in self.python_scoped_sites(txt(node), node)
                     if self.python_binds(n) or self.python_store(n) or s == ("mutation",)]
            if len(sites) == 1:
                site, binding = sites[0]
                if self.python_parameter(binding):
                    return False
                if site[:1] == ("def",) and binding.parent.parent.type != "decorated_definition":
                    return False
                if binding.parent.type == "assignment":
                    value = binding.parent.child_by_field_name("right")
                    if value is not None and value.type == "lambda":
                        return False
        outer = expression.parent
        if outer.type == "attribute" and outer.child_by_field_name("object") == expression:
            return txt(outer.child_by_field_name("attribute")) in ("__call__", "__getattribute__", "__self__")
        return True

    def python_namespace_written(self):
        """A write into builtins or sys.modules: through the name, an import alias or a name assigned from it
        (b.staticmethod = ..., m = sys.modules[__name__]; m.memo = ...). A namespace handed to a call other than
        setattr/delattr is not followed (docs/CATALOG.md, limits)."""
        todo, seen = [("builtins", False), ("__builtins__", False), ("sys", True)], set()  # (name, only under .modules)
        while todo:
            name, under_modules = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            for match in re.finditer(rb"(?<![\w.])" + re.escape(name.encode()) + rb"(?!\w)", self.src):
                node = self.root_node.descendant_for_byte_range(match.start(), match.end())
                if node.type != "identifier" or (node.start_byte, node.end_byte) != match.span():
                    continue
                statement = next((a for a in (node.parent, node.parent.parent, node.parent.parent and node.parent.parent.parent)
                                  if a is not None and a.type in ("import_statement", "import_from_statement")), None)
                if statement is not None:  # import sys as s; from sys import modules as m
                    if node.parent.type == "dotted_name" and node.parent.parent.type == "aliased_import" and statement.type == "import_statement":
                        todo.append((txt(node.parent.parent.child_by_field_name("alias")), under_modules and txt(node.parent) == name))
                    elif statement.type == "import_from_statement" and name == "sys" and node.parent == statement.child_by_field_name("module_name"):
                        for item in statement.children_by_field_name("name"):
                            imported = txt(item.child_by_field_name("name")) if item.type == "aliased_import" else txt(item)
                            if imported == "modules":
                                todo.append((txt(item.child_by_field_name("alias")) if item.type == "aliased_import" else "modules", False))
                    continue
                top = node
                while top.parent is not None and any(top.parent.type == kind and top.parent.child_by_field_name(field) == top
                                                     for kind, field in (("attribute", "object"), ("subscript", "value"), ("call", "function"))):
                    top = top.parent
                if under_modules and not re.match(re.escape(name) + r"\s*\.\s*modules\b", txt(top)):
                    continue  # sys.path, sys.argv
                if self.python_store(node) and (top is not node or node.parent.type == "argument_list"):
                    return True  # a write under the name, or setattr(name, ...); rebinding the name itself is harmless
                holder = top.parent
                while holder is not None and holder.type in ("boolean_operator", "conditional_expression", "parenthesized_expression"):
                    holder = holder.parent
                if holder is not None and holder.type == "assignment" and holder.child_by_field_name("right") is not None \
                        and holder.child_by_field_name("right").start_byte <= top.start_byte:
                    left = holder.child_by_field_name("left")
                    for target in ([left] if left.type == "identifier" else left.named_children if left.type in ("pattern_list", "tuple_pattern") else [None]):
                        if target is None or target.type != "identifier":
                            return True  # self.x = sys.modules[...]: not followed
                        todo.append((txt(target), False))
        return False

    def python_namespace_read(self, node):
        """globals(), locals() or vars(...) only read or copied: subscripted, a read method, f(**...) or `in`."""
        call = node.parent
        if call.type != "call" or call.child_by_field_name("function") != node:
            return False
        outer = call.parent
        if outer.type == "subscript" and outer.child_by_field_name("value") == call:
            return not self.python_store(outer)
        if outer.type == "attribute" and outer.child_by_field_name("object") == call:
            return txt(outer.child_by_field_name("attribute")) in ("get", "keys", "values", "items", "copy")
        return outer.type in ("dictionary_splat", "comparison_operator")

    def python_module_written(self, module):
        """Is this module written through its name or an import alias of it or a parent module in this file?"""
        known = self.module_writes.get(module)
        if known is not None:
            return known
        top, names = module.split(".")[0], set()
        names.add(top)
        for match in re.finditer(rb"(?<![\w.])" + re.escape(top.encode()) + rb"(?!\w)", self.src):
            path = self.root_node.descendant_for_byte_range(match.start(), match.end()).parent
            if path is not None and path.type == "dotted_name" and path.parent is not None and path.parent.type == "aliased_import" \
                    and path.parent.parent.type == "import_statement" and (txt(path) == module or module.startswith(txt(path) + ".")):
                names.add(txt(path.parent.child_by_field_name("alias")))
        for name in names:
            for match in re.finditer(rb"(?<![\w.])" + re.escape(name.encode()) + rb"(?!\w)", self.src):
                node = self.root_node.descendant_for_byte_range(match.start(), match.end())
                if node.type != "identifier" or (node.start_byte, node.end_byte) != match.span():
                    continue
                child = node
                while child.parent is not None and child.parent.type == "parenthesized_expression":
                    child = child.parent
                parent = child.parent
                under = (parent.type == "attribute" and parent.child_by_field_name("object") == child) \
                    or (parent.type == "subscript" and parent.child_by_field_name("value") == child) \
                    or (parent.type == "argument_list" and parent.named_children[0] == child)
                if under and self.python_store(node):
                    self.module_writes[module] = True
                    return True
        self.module_writes[module] = False
        return False

    def python_property(self, name, decorator):
        """Is name a @property (proven builtin) of the class this decorator is in, bound only by decorated defs?"""
        if self.python_sites("property") or ("builtin", "", "property") not in self.py["pure_decorator"]:
            return False
        block, found = decorator.parent.parent, False
        for site in self.python_sites(name):
            definition = self.root_node.descendant_for_byte_range(site[1], site[2]).parent if site[0] == "def" else None
            outer = definition.parent if definition is not None else None
            if outer is None or outer.type != "decorated_definition":
                return False
            found = found or (outer.parent == block and any(d.type == "decorator" and txt(d).lstrip("@").strip() == "property" for d in outer.named_children))
        return found

    def python_identity(self, site):
        """'plain' or 'factory' for the undecorated def at this site (see identity_decorator), None otherwise."""
        definition = self.root_node.descendant_for_byte_range(site[1], site[2]).parent
        return None if definition.parent.type == "decorated_definition" else self.identity_decorator(definition)

    @staticmethod
    def python_literal(node):
        """A literal argument (string, number, True/False/None), positional or keyword."""
        node = node.child_by_field_name("value") if node.type == "keyword_argument" else node
        node = node.child_by_field_name("argument") if node is not None and node.type == "unary_operator" else node
        return node is not None and node.type in ("string", "concatenated_string", "integer", "float", "true", "false", "none")

    def identity_decorator(self, n, depth=0):
        """'plain' for def d(f): return f, 'factory' for a def whose body is only def inner(f): return f; return inner,
        None otherwise. An async def returns a coroutine, not the function."""
        parameters, body = n.child_by_field_name("parameters"), n.child_by_field_name("body")
        if parameters is None or body is None or not parameters.named_children or (n.children and n.children[0].type == "async"):
            return None
        first = parameters.named_children[0]
        first = first if first.type == "identifier" else first.child_by_field_name("name") or first
        static = lambda node: node.type == "string" and not self.has_descendant(node, ("interpolation", "format_expression"))
        statements = [c for c in body.named_children if c.type != "comment" and not static(c)  # a docstring, in either tree shape
                      and not (c.type == "expression_statement" and c.named_child_count == 1 and static(c.named_children[0]))]  # f"{...}" runs
        returned = statements[-1].named_children[0] if statements and statements[-1].type == "return_statement" and statements[-1].named_children else None
        if returned is None or returned.type != "identifier":
            return None
        if len(statements) == 1:
            return "plain" if txt(returned) == txt(first) else None
        inner = statements[0]
        return "factory" if (depth == 0 and len(statements) == 2 and inner.type == "function_definition"
                             and txt(inner.child_by_field_name("name")) == txt(returned) and self.identity_decorator(inner, 1) == "plain") else None

    @staticmethod
    def has_descendant(node, kinds):
        stack = list(node.children)
        while stack:
            child = stack.pop()
            if child.type in kinds:
                return True
            stack.extend(child.children)
        return False

    def callback_refs(self, n, func):
        """Functions passed as arguments are references: impact lists them and dead counts them."""
        args = n.child_by_field_name("arguments")
        if args is None:
            return
        line = n.start_point[0] + 1

        def add(name, receiver):
            if not re.fullmatch(r"[A-Za-z_]\w*", name or ""):
                return
            head = (receiver or name).split(".")[0]
            if self.f == "python" and head not in ("self", "cls", self.self_receivers.get(func)) and self.binding_shadowed_local(head, func):
                return  # a parameter or local variable (tools.polish with tools a parameter, R4), not the module or function
            self.references.append(dict(owner=func, name=name, receiver=receiver, kind="callback", line=line))

        if self.f == "python":
            function = n.child_by_field_name("function")
            if function is not None and self.catalog_callback(txt(function), func):
                return  # recorded at its catalog positions in handle_call
            for arg in args.named_children:
                value = arg.child_by_field_name("value") if arg.type == "keyword_argument" else arg
                if value is not None and value.type in ("identifier", "attribute"):
                    receiver, _, name = txt(value).rpartition(".")
                    add(name, receiver)
        elif self.f == "js":
            for arg in args.named_children:
                if arg.type == "identifier":
                    add(txt(arg), "")
                elif arg.type == "member_expression" and (arg.child_by_field_name("object") or arg).type == "this":
                    add(txt(arg.child_by_field_name("property")), "self")
        elif self.f == "php":
            if any(child.type == 'variadic_placeholder' for child in args.named_children):
                return
            receiver, via = None, 'import'
            if n.type in ('member_call_expression','nullsafe_member_call_expression'):
                receiver = n.child_by_field_name('object')
                via = 'instance'
            elif n.type == 'scoped_call_expression':
                receiver = txt(n.child_by_field_name('scope'))
            self.php_callbacks.append((n, func, receiver, via))

    def php_api(self, raw, line, role, names, receiver=None, via='import'):
        scope, namespace = names.scope(0, line)
        if namespace is None:
            return None
        if receiver is not None:
            imported = names.uses.get((0, scope, receiver.lower()))
            if not imported:
                return None
            module, _, cls = imported.rpartition('\\')
            return self.catalog.lookup('php', role, via, module, cls + '.' + raw)
        imported = names.function_uses.get((0, scope, raw.lower()))
        if imported is not None:
            if not imported:
                return None
            module, _, name = imported.rpartition('\\')
            if module:
                return self.catalog.lookup('php', role, 'import', module, name)
            raw = name  # an explicit import of a global function, possibly aliased
        elif raw.startswith('\\'):
            raw = raw[1:]
        elif raw.lower().startswith('namespace\\') and not namespace:
            raw = raw[10:]
        if not re.fullmatch(r'[A-Za-z_]\w*', raw):
            return None
        matches = [e for e in self.catalog.role('php', role)
                   if e['origin']['via'] in ('builtin','global') and e['origin']['name'].lower() == raw.lower()]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def php_string(node):
        if node.type not in ('string','encapsed_string'):
            return None
        value = txt(node)[1:-1]
        if txt(node).startswith('"'):
            # Interpolation and encoded escapes are not identifiers we can prove here.
            if '$' in value or re.search(r'(?<!\\)(?:\\\\)*\\[nrtvefxu0-7]', value):
                return None
        return value.replace('\\\\','\\').replace("\\'", "'")

    def php_finish_callbacks(self):
        names = PhpNames.from_extractor(self)
        for call in self.calls:
            if 'php_receiver' in call:
                types, _ = self.php_receiver_binding(call.pop('php_receiver'), call['caller'])
                # One dynamic callsite; its receiver is the union, including unexecuted writes.
                call['recv_name'] = '|'.join(sorted(types)) or call['recv_name']
            if call['recv'] == 'none' and call['name']:
                entry = self.php_api(call['recv_name'], call['line'], 'dynamic', names)
                call['dynamic'] = int(entry is not None)
                if entry and entry['origin']['via'] in ('builtin','global'):
                    call['php_api'] = call['recv_name']
        for node, owner, receiver, via in self.php_callbacks:
            function = node.child_by_field_name('function') or node.child_by_field_name('name')
            raw, line = txt(function), node.start_point[0] + 1
            if via == 'instance':
                types, construction = self.php_receiver_binding(receiver, owner)
                receiver = next(iter(types)) if construction is not None and construction.end_byte < node.start_byte else ''
            entry = self.php_api(raw, line, 'callback', names, receiver, via)
            if entry is None:
                continue
            position_known = True
            args = node.child_by_field_name('arguments')
            for position, arg in enumerate(c for c in args.named_children if c.type != 'comment'):
                keyword = txt(arg.child_by_field_name('name')) if arg.type == 'argument' else ''
                value = arg.named_children[-1] if arg.type == 'argument' and arg.named_children else arg
                if value.type == 'variadic_unpacking':
                    position_known = False
                    continue
                selected = keyword in entry.get('kwargs',[]) if keyword else position_known and (
                    position in entry.get('args',[]) or ('rest_from' in entry and position >= entry['rest_from']))
                if not selected:
                    continue
                target, target_receiver = self.php_string(value), ''
                if value.type == 'array_creation_expression':
                    elements = [e for e in value.named_children if e.type == 'array_element_initializer']
                    # Keyed/unpacked arrays need evaluation to prove slots 0 and 1.
                    if len(elements) != 2 or any(len(e.named_children) != 1 or
                            e.named_children[0].type == 'variadic_unpacking' for e in elements):
                        continue
                    items = [e.named_children[0] for e in elements]
                    target = self.php_string(items[1])
                    value_owner = txt(items[0])
                    if value_owner in ('$this','self::class','static::class'):
                        target_receiver = 'self'
                    elif value_owner.endswith('::class'):
                        target_receiver = value_owner[:-7]
                    else:
                        target_receiver = self.php_string(items[0])
                        if not target_receiver:
                            continue
                        target_receiver = '\\' + target_receiver.lstrip('\\')
                elif target and '::' in target:
                    target_receiver, target = target.split('::',1)
                    target_receiver = '\\' + target_receiver.lstrip('\\')
                if not target or not re.fullmatch(r'\\?[A-Za-z_]\w*(?:\\[A-Za-z_]\w*)*', target):
                    continue
                reference = dict(owner=owner, name=target, receiver=target_receiver, kind='callback', line=line)
                if entry['origin']['via'] in ('builtin','global'):
                    reference['php_api'] = raw
                self.references.append(reference)

    @staticmethod
    def php_receiver_read(site):
        parent = site.parent
        return (parent.type in ('member_call_expression','nullsafe_member_call_expression',
                                'member_access_expression','nullsafe_member_access_expression')
                and parent.child_by_field_name('object') == site) or (
                parent.type == 'binary_expression' and parent.child_by_field_name('left') == site
                and txt(parent.child_by_field_name('operator')) == 'instanceof')

    def php_receiver_binding(self, obj, owner):
        """Possible construction types for dead; a single unescaped assignment for catalog origins."""
        if obj is None:
            return set(), None
        prop = obj.type == 'member_access_expression' and txt(obj.child_by_field_name('object')) == '$this'
        if prop:
            while owner is not None and self.syms[owner]['kind'] not in CLASSLIKE:
                owner = self.syms[owner]['parent']
        elif obj.type != 'variable_name':
            return set(), None
        key = owner, txt(obj)
        if key not in self.php_receiver_cache:
            types, assignments, proven = set(), [], True
            for site in self.php_variables[key]:
                parent = site.parent
                if site.type in ('property_element','property_promotion_parameter'):
                    declaration = parent if site.type == 'property_element' else site
                    annotations = re.split('[|&]', txt(declaration.child_by_field_name('type')))
                    types.update(filter(None, map(self.clean_type, annotations)))
                    types.update(filter(None, [self.new_type(site.child_by_field_name('default_value'))]))
                    if site.type == 'property_promotion_parameter' or site.child_by_field_name('default_value') is not None:
                        proven = False
                elif parent.type == 'assignment_expression' and parent.child_by_field_name('left') == site:
                    ty = self.new_type(parent.child_by_field_name('right'))
                    assignments.append(parent)
                    if ty:
                        types.add(ty)
                    else:
                        proven = False
                elif parent.type in ('simple_parameter','variadic_parameter','property_promotion_parameter'):
                    annotations = re.split('[|&]', txt(parent.child_by_field_name('type')))
                    types.update(filter(None, map(self.clean_type, annotations)))
                    proven = False  # an annotation is not a unique construction
                elif not self.php_receiver_read(site):
                    proven = False  # aliases, arguments, foreach/global/unset and other writes
            if prop and proven:
                field = txt(obj.child_by_field_name('name'))
                for (scope, name), sites in self.php_variables.items():
                    if name == '$this':
                        while scope is not None and self.syms[scope]['kind'] not in CLASSLIKE:
                            scope = self.syms[scope]['parent']
                        if scope == owner and any(not self.php_receiver_read(site) for site in sites):
                            proven = False
                    for site in sites:
                        member = site.parent
                        if site.type != 'variable_name' or txt(site) == '$this' or member.type not in (
                                'member_access_expression','nullsafe_member_access_expression'):
                            continue
                        attribute = member.child_by_field_name('name')
                        if (attribute is None or attribute.type != 'name' or txt(attribute) == field) \
                                and not self.php_receiver_read(member):
                            proven = False  # an explicit write/escape through another instance
            construction = assignments[0] if proven and len(assignments) == len(types) == 1 else None
            self.php_receiver_cache[key] = types, construction
        return self.php_receiver_cache[key]


    @staticmethod
    def python_parameter(node):
        while node.parent is not None and node.parent.type in ("typed_parameter", "default_parameter", "typed_default_parameter",
                                                              "list_splat_pattern", "dictionary_splat_pattern"):
            if node.parent.named_children[0] != node:
                return False
            node = node.parent
        return node.parent is not None and node.parent.type in ("parameters", "lambda_parameters")

    def python_scope(self, node):
        parent = node.parent
        while parent is not None:
            if parent.type in ("function_definition", "class_definition", "lambda"):
                body = parent.child_by_field_name("body")
                if (body is not None and body.start_byte <= node.start_byte < body.end_byte) or self.python_parameter(node):
                    return parent
            elif parent.type in ("list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression"):
                first = next((c for c in parent.named_children if c.type == "for_in_clause"), None)
                iterable = first.child_by_field_name("right") if first is not None else None
                if iterable is None or not iterable.start_byte <= node.start_byte < iterable.end_byte:
                    return parent
            parent = parent.parent
        return self.root_node

    def python_binds(self, node):
        site = self.python_site(node)
        if site is not None and site[0] in ("from", "import", "def") or self.python_parameter(node):
            return True
        if node.parent.type == "class_definition" and node.parent.child_by_field_name("name") == node:
            return True
        child, parent = node, node.parent
        while parent is not None and parent.type not in ("expression_statement", "block", "module", "decorator"):
            if parent.type in ("attribute", "subscript", "call", "global_statement", "nonlocal_statement"):
                return False
            if parent.type in ("assignment", "augmented_assignment", "for_statement", "for_in_clause") and parent.child_by_field_name("left") == child:
                return True
            if parent.type in ("delete_statement", "as_pattern_target", "case_pattern") \
                    or parent.type == "named_expression" and parent.child_by_field_name("name") == child:
                return True
            child, parent = parent, parent.parent
        return False

    def python_scoped_sites(self, name, at):
        """Bindings and visible mutations of a name in its lexical namespace. Calls may read the object;
        an alias assignment remains uncertain. A global/nonlocal write also invalidates the enclosing binding."""
        if name not in self.scoped_names:
            records, globals_, nonlocals = [], set(), set()
            for match in re.finditer(rb"(?<![\w.])" + re.escape(name.encode()) + rb"(?!\w)", self.src):
                node = self.root_node.descendant_for_byte_range(match.start(), match.end())
                if node.type != "identifier" or (node.start_byte, node.end_byte) != match.span():
                    continue
                scope = self.python_scope(node)
                if node.parent.type in ("global_statement", "nonlocal_statement"):
                    (globals_ if node.parent.type == "global_statement" else nonlocals).add(scope)
                    continue
                binds = self.python_binds(node)
                if binds and node.parent.type == "named_expression":
                    while scope.type in ("list_comprehension", "set_comprehension", "dictionary_comprehension", "generator_expression"):
                        scope = self.python_scope(scope)
                alias = node.parent.type == "assignment" and node.parent.child_by_field_name("right") == node
                argument = node.parent.type == "argument_list" or (node.parent.type == "keyword_argument" and node.parent.child_by_field_name("value") == node)
                if binds or self.python_store(node) or alias or argument:
                    site = ("argument",) if argument and not binds and not self.python_store(node) else self.python_site(node) or ("other",)
                    records.append((node, scope, binds, site))
            self.scoped_names[name] = records, {s for _, s, binds, _ in records if binds} - globals_ - nonlocals, globals_, nonlocals, {}
        records, locals_, globals_, nonlocals, cache = self.scoped_names[name]

        def namespace(scope, node=None, binds=False):
            while scope != self.root_node:
                if scope in globals_:
                    return self.root_node
                if scope in locals_:
                    if scope.type == "class_definition" and node is not None and not binds:
                        # Class reads use the current dictionary, then globals; functions use lexical locals.
                        prior = [n for n, s, bound, _ in records if s == scope and bound and n.end_byte < node.start_byte
                                 and (n.parent.type not in ("assignment", "function_definition", "class_definition")
                                      or n.parent.end_byte < node.start_byte)
                                 and (n.parent.type != "assignment" or n.parent.child_by_field_name("right") is not None)]
                        if not prior or prior[-1].parent.type == "delete_statement":
                            return self.root_node
                    return scope
                scope = self.python_scope(scope)
                while scope.type == "class_definition":
                    scope = self.python_scope(scope)
            return self.root_node

        context = self.python_scope(at)
        owner = namespace(context, at)
        class_lookup = context.type == "class_definition" and owner != context
        key = (owner, at.start_byte) if class_lookup else owner
        if key not in cache:
            visible = [(site if scope == owner or not binds else ("other",), node)
                       for node, scope, binds, site in records
                       if (namespace(scope, node, binds) if class_lookup else namespace(scope)) == owner
                       and (not class_lookup or binds or scope.type != "class_definition" or node.start_byte < at.start_byte)]
            # Cache the direct facts first, including on recursive local call paths.
            cache[key] = [(site, node) for site, node in visible if site != ("argument",)]
            for site, node in visible:
                if site == ("argument",):
                    if self.scoped_call_depth >= 4:
                        mutates = True
                    else:
                        self.scoped_call_depth += 1
                        try:
                            mutates = self.python_mutating_argument(node, name)
                        finally:
                            self.scoped_call_depth -= 1
                    if mutates:
                        cache[key].append((("mutation",), node))
        return cache[key]

    def python_mutating_argument(self, node, name):
        """A visible local callee can mutate its parameter, even though merely passing a receiver is harmless."""
        argument, keyword = node, None
        if node.parent.type == "keyword_argument":
            argument, keyword = node.parent, txt(node.parent.child_by_field_name("name"))
        arguments = argument.parent
        if arguments.type != "argument_list" or arguments.parent.type != "call":
            return False
        callee = arguments.parent.child_by_field_name("function")
        if callee.type != "identifier" or txt(callee) == name:
            return False
        sites = self.python_scoped_sites(txt(callee), callee)
        if not sites or len(sites) == 1 and sites[0][0][:1] in (("from",), ("import",)):
            return False
        if len(sites) != 1:
            return True
        site, binding = sites[0]
        function = binding.parent if site[:1] == ("def",) else \
            binding.parent.child_by_field_name("right") if binding.parent.type == "assignment" else None
        if function is None or function.type not in ("function_definition", "lambda"):
            return True
        if function.parent.type == "decorated_definition":
            return True
        parameters = function.child_by_field_name("parameters")
        if parameters is None:
            return True
        params = []
        for param in parameters.named_children:
            if param.type in ("comment", "positional_separator", "keyword_separator"):
                continue
            while param.type in ("typed_parameter", "default_parameter", "typed_default_parameter", "list_splat_pattern", "dictionary_splat_pattern"):
                param = param.named_children[0]
            params.append(param)
        values = [a for a in arguments.named_children if a.type != "comment"]
        param = next((p for p in params if txt(p) == keyword), None) if keyword else \
            params[values.index(argument)] if values.index(argument) < len(params) else None
        if param is None:
            return True  # a local variadic call: its argument cannot be assigned to one parameter here
        call = arguments.parent
        returned_alias = self.python_identity(site) == "plain" if site[:1] == ("def",) else \
            function.child_by_field_name("body").type == "identifier" and txt(function.child_by_field_name("body")) == txt(param)
        if returned_alias and call.parent.type == "assignment" and call.parent.child_by_field_name("right") == call:
            target = call.parent.child_by_field_name("left")
            if target.type != "identifier":
                return True
            if any(not self.python_binds(n) and (s == ("other",) or self.python_store(n))
                   for s, n in self.python_scoped_sites(txt(target), target)):
                return True
        facts = self.python_scoped_sites(txt(param), function.child_by_field_name("body"))
        return any(not self.python_binds(n) and (s in (("other",), ("mutation",)) or self.python_store(n)) for s, n in facts)

    def python_registration(self, receiver, method, at):
        """An observation only for one construction in the receiver's namespace, with an imported constructor."""
        if not re.fullmatch(r"[A-Za-z_]\w*", receiver) or self.root_node.has_error or self.python_rebinds_freely() \
                or len(bindings := self.python_scoped_sites(receiver, at)) != 1:
            return None
        for site, name in bindings:
            assignment = name.parent
            if site != ("store",) \
                    or assignment.type != "assignment" or assignment.child_by_field_name("left") != name:
                continue
            value = assignment.child_by_field_name("right")
            if value is None or value.type != "call":
                return None
            if self.python_scope(name) == self.python_scope(at) and assignment.start_byte > at.start_byte:
                return None
            head = txt(value.child_by_field_name("function"))
            if not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", head):
                return None
            first, dot, rest = head.partition(".")
            origins_at = self.python_scoped_sites(first, name)
            if origins_at and all(self.python_scope(n) == self.python_scope(name) and n.start_byte > name.start_byte for _, n in origins_at):
                return None
            sites = [site for site, _ in origins_at]
            site = sites[0] if len(set(sites)) == 1 else ()
            origins = []
            if site[:1] == ("from",):
                origins = [(site[1], site[2] + (dot + rest if rest else ""))]
            elif site[:1] == ("import",) and dot:
                parts = rest.split(".")
                origins = [(".".join([site[1]] + parts[:cut]), ".".join(parts[cut:])) for cut in range(len(parts))]
            for module, original in origins:
                kind = self.py["registrar"].get(("instance", module, original + "." + method), {}).get("observation")
                if kind and not self.python_module_written(module):
                    return kind
            return None

    def observe_registration(self, node, target, scope):
        for decorator in node.parent.named_children:
            if decorator.type != "decorator":
                continue
            raw = txt(decorator).lstrip("@").strip().split("(", 1)[0]
            receiver, dot, method = raw.rpartition(".")
            if not dot:
                continue
            kind = self.python_registration(receiver, method, decorator)
            if kind:
                self.observations.append(dict(owner=target, kind=kind, callee=raw, line=decorator.start_point[0] + 1, value="", value_kind="registration"))
                self.references.append(dict(owner=scope, name=self.syms[target]["name"], receiver="", kind="entrypoint", line=decorator.start_point[0] + 1))

    def observe_python_call(self, node, function, owner, cls):
        raw = txt(function)
        head, dot, rest = raw.partition(".")
        imported = self.imported_module(head, owner) if dot else None
        canonical = imported + "." + rest if imported else None
        for module, original, local in self.bindings:
            if not dot and local == raw and not self.binding_shadowed(raw, owner):
                canonical = module + "." + original
        args = node.child_by_field_name("arguments")
        arguments = list(args.named_children) if args is not None else []
        first = arguments[0] if arguments else None
        kind = None
        if raw == "open" and not self.binding_shadowed("open", owner) and not any(local == "open" and module != "builtins" for module, original, local in self.bindings) and not any(sym["name"] == "open" and sym["kind"] in ("function", "class") for sym in self.syms):
            mode = txt(arguments[1]) if len(arguments) > 1 else ""
            mode += " ".join(txt(arg) for arg in arguments if arg.type == "keyword_argument" and txt(arg).startswith("mode"))
            kind = "file.write" if any(c in mode for c in ("w", "a", "x", "+")) else "file.read"
        elif canonical == "sqlite3.connect":
            kind = "database.connect"
        elif canonical in ("subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "os.system"):
            kind = "process.start"
        elif function.type == "attribute":
            receiver = function.child_by_field_name("object")
            method = txt(function.child_by_field_name("attribute"))
            typename = self.type_of(receiver, owner, cls)
            direct_path = receiver is not None and receiver.type == "call" and any(module == "pathlib" and original == "Path" and local == txt(receiver.child_by_field_name("function")) for module, original, local in self.bindings)
            typed_path = any(module == "pathlib" and original == "Path" and local == typename for module, original, local in self.bindings)
            if method in ("read_text", "read_bytes", "write_text", "write_bytes") and (direct_path or typed_path):
                kind = "file.read" if method.startswith("read") else "file.write"
                path_args = receiver.child_by_field_name("arguments") if direct_path else None
                first = path_args.named_children[0] if path_args is not None and path_args.named_children else None
        if not kind:
            return
        value, value_kind = "", "dynamic"
        literal_node = first
        if kind == "process.start" and first is not None:
            literal_node = first.named_children[0] if first.type in ("list", "tuple") and first.named_children else None
        if literal_node is not None and literal_node.type == "string":
            try:
                literal = ast.literal_eval(txt(literal_node))
                if isinstance(literal, str) and len(literal) <= 500:
                    if "://" in literal:
                        value_kind = "uri"
                    elif kind != "process.start" or re.fullmatch(r"[\w./+-]{1,100}", literal):
                        value, value_kind = literal, "literal"
            except (ValueError, SyntaxError):
                pass
        self.observations.append(dict(owner=owner, kind=kind, callee=(canonical or raw)[:160], line=node.start_point[0] + 1, value=value, value_kind=value_kind))

    def new_type(self, v):
        if v is None:
            return None
        if self.f == "python":
            literal = {"dictionary": "dict", "dictionary_comprehension": "dict", "list": "list", "list_comprehension": "list", "set": "set", "set_comprehension": "set", "tuple": "tuple", "string": "str", "integer": "int", "float": "float"}.get(v.type)
            if literal:
                return "__builtin__:" + literal
        if v.type in ("object_creation_expression",):
            tgt = next((c for c in v.named_children if c.type in ("name", "qualified_name", "relative_name")), None)
            return txt(tgt) if tgt is not None else None
        if v.type == "new_expression":
            c = v.child_by_field_name("constructor")
            return txt(c) if c is not None and c.type == "identifier" else None
        if v.type == "call" and self.f == "python":
            fn = v.child_by_field_name("function")
            if fn is not None and fn.type == "identifier":
                if txt(fn) in ("dict", "list", "tuple", "set", "str", "bytes"):
                    return self.python_type(txt(fn))
                if txt(fn)[:1].isupper():
                    return txt(fn)
        return None

    def track_assign(self, n, func, cls):
        t = n.type
        if t == "property_declaration":
            ty = self.clean_type(txt(n.child_by_field_name("type")))
            if ty and cls is not None:
                for el in n.named_children:
                    if el.type == "property_element":
                        self.ctypes[cls][txt(el.child_by_field_name("name") or el.named_children[0]).lstrip("$")] = ty
            return
        if t == "public_field_definition":
            ty = self.clean_type(txt(n.child_by_field_name("type")))
            nm = n.child_by_field_name("name")
            if cls is not None and nm is not None:
                ty = ty or self.new_type(n.child_by_field_name("value"))
                if ty:
                    self.ctypes[cls][txt(nm)] = ty
            return
        if t == "variable_declarator":
            left, right = n.child_by_field_name("name"), n.child_by_field_name("value")
            ty = self.clean_type(txt(n.child_by_field_name("type"))) or self.new_type(right)
        else:
            left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
            ty = self.new_type(right) or (self.python_type(txt(n.child_by_field_name("type"))) if t == "assignment" and self.f == "python" else self.clean_type(txt(n.child_by_field_name("type"))) if t == "assignment" else None)
            if not ty and self.f == "python" and right is not None and right.type == "identifier":
                ty = self.vtypes.get(func, {}).get(txt(right))  # self.repo = repo keeps the parameter's annotation
        if left is None:
            return
        lt = left.type
        if lt in ("variable_name", "identifier"):
            var = txt(left).lstrip("$")
            if self.f == "python":
                self.shadowed[func].add(var)
            if ty:
                self.vtypes[func][var] = ty
            else:
                self.vtypes[func].pop(var, None)
        elif lt in ("member_access_expression", "attribute", "member_expression") and cls is not None:
            obj = left.child_by_field_name("object")
            attr = left.child_by_field_name("name") or left.child_by_field_name("attribute") or left.child_by_field_name("property")
            if obj is not None and attr is not None and txt(obj) in ("$this", "self", "this"):
                if ty:
                    self.ctypes[cls][txt(attr)] = ty
                else:
                    self.ctypes[cls].pop(txt(attr), None)

    def type_of(self, obj, func, cls):
        if obj is None:
            return None
        literal = self.new_type(obj)
        if literal and literal.startswith("__builtin__:"):
            return literal
        if obj.type in ("variable_name", "identifier"):
            name = txt(obj).lstrip("$")
            if self.f != "python":
                return self.vtypes.get(func, {}).get(name)
            # Python reads an enclosing function's or the module's name unless it is bound locally; class bodies are skipped.
            current = func
            while current is not None:
                if self.syms[current]["kind"] != "class":
                    if name in self.vtypes.get(current, {}):
                        return self.vtypes[current][name]
                    if name in self.shadowed.get(current, set()):
                        return None
                current = self.syms[current]["parent"]
            return None
        if obj.type in ("member_access_expression", "attribute", "member_expression") and cls is not None:
            o = obj.child_by_field_name("object")
            a = obj.child_by_field_name("name") or obj.child_by_field_name("attribute") or obj.child_by_field_name("property")
            if o is not None and a is not None and txt(o) in ("$this", "self", "this"):
                return self.ctypes.get(cls, {}).get(txt(a))
        return None

    def record_facts(self, node, scope, cls, func):
        if cls is not None and node.type in ('assignment', 'assignment_expression', 'augmented_assignment'):
            left = node.child_by_field_name('left')
            pending = [left] if left is not None else []
            while pending:
                item = pending.pop()
                if item.type in ('attribute', 'member_expression'):
                    receiver = txt(item.child_by_field_name('object'))
                    name = txt(item.child_by_field_name('attribute') or item.child_by_field_name('property'))
                    if receiver in ('self', 'cls', 'this') or self.self_receivers.get(func) == receiver:
                        ancestor, certainty = node.parent, 'assigned'
                        while ancestor is not None and ancestor.type not in ('function_definition','method_declaration','program','module'):
                            if ancestor.type in ('if_statement','try_statement','for_statement','while_statement','conditional_expression'): certainty='conditional'
                            ancestor=ancestor.parent
                        self.assignments.append((cls, name, node.start_point[0]+1, left.type, certainty))
                elif item.type in ('pattern_list','tuple_pattern','list_pattern','tuple','list','array_pattern'):
                    pending.extend(item.named_children)
        if self.f == 'php' and node.type in ('require_expression', 'require_once_expression', 'include_expression', 'include_once_expression'):
            expression = txt(node.named_children[0]) if node.named_children else ''
            ancestor, certainty = node.parent, 'static'
            while ancestor is not None:
                if ancestor.type in ('if_statement','function_definition','method_declaration','anonymous_function','while_statement','foreach_statement'):
                    certainty='conditional'
                ancestor=ancestor.parent
            self.includes.append((expression,node.start_point[0]+1,certainty))

    def call_types(self):
        return self.adapter["CALL_TYPES"]

    def params(self, n, kind):
        if kind in CLASSLIKE:
            return None, None
        p = n.child_by_field_name("parameters")
        if p is None:
            v = n.child_by_field_name("value")
            p = v.child_by_field_name("parameters") if v is not None else None
            if p is None and v is not None:
                p = v.child_by_field_name("parameter")
                if p is not None:
                    return 1, 1
        if p is None:
            return 0, 0
        req, tot, var = 0, 0, False
        for c in p.named_children:
            ct = c.type
            if ct in ("comment", "keyword_separator", "positional_separator"):
                continue
            if ct in ("list_splat_pattern", "dictionary_splat_pattern", "variadic_parameter", "rest_pattern") or \
                    (ct == "typed_parameter" and c.named_children and
                     c.named_children[0].type in ("list_splat_pattern", "dictionary_splat_pattern")):
                var = True
                continue
            if ct == "identifier" and txt(c) in ("self", "cls") and tot == 0 and kind == "method" and self.f == "python":
                continue
            if ct in ("typed_parameter",) and tot == 0 and kind == "method" and self.f == "python":
                first = c.named_children[0] if c.named_children else None
                if first is not None and txt(first) in ("self", "cls"):
                    continue
            tot += 1
            optional = ct in ("default_parameter", "typed_default_parameter", "assignment_pattern", "optional_parameter") or \
                c.child_by_field_name("default_value") is not None or \
                (ct == "required_parameter" and c.child_by_field_name("value") is not None)
            if self.f == "js" and ct == "required_parameter" and c.named_children and c.named_children[0].type == "rest_pattern":
                var, tot = True, tot - 1
                continue
            if not optional:
                req += 1
        return req, (None if var else tot)

    def collect_bases(self, n, sidx):
        for c in n.children:
            if c.type in ("argument_list", "base_clause", "class_interface_clause", "class_heritage", "extends_clause",
                          "implements_clause", "superclass"):
                for b in c.named_children:
                    if b.type in ("comment", "keyword_argument"):
                        continue
                    raw = re.sub(r"[<\[].*", "", txt(b))  # via generics: Foo<T>, Mapping[str, X]
                    for part in re.split(r"[,\s]+", raw.replace("extends", " ").replace("implements", " ")):
                        if part and re.match(r"^[\w$\\.]+$", part):
                            self.bases.append((sidx, part if self.f == "php" else last_seg(part)))
        if self.f == "php":
            body = n.child_by_field_name("body")
            for c in (body.named_children if body is not None else []):
                if c.type == "method_declaration" and c.child_by_field_name("body") is None:
                    self.observations.append(dict(owner=sidx, kind="php.abstract_method",
                                                  callee=txt(c.child_by_field_name("name")).lower(),
                                                  line=c.start_point[0] + 1, value="", value_kind="definition"))
                if c.type == "use_declaration":
                    for b in c.named_children:
                        if b.type in ("name", "qualified_name"):
                            self.bases.append((sidx, txt(b)))

    def args(self, n):
        a = n.child_by_field_name("arguments")
        if a is None:
            for c in n.children:
                if c.type in ("arguments", "argument_list"):
                    a = c
        if a is None:
            return 0, 0
        if a.type == "generator_expression":
            return 1, 0
        kids = [c for c in a.named_children if c.type != "comment"]
        splat = any(c.type in ("list_splat", "dictionary_splat", "spread_element", "variadic_unpacking") or
                    (c.type == "argument" and any(g.type == "variadic_unpacking" for g in c.children)) for c in kids)
        return len(kids), int(splat)

    def add_call(self, n, func, name, recv, recv_name="", dynamic=0):
        nargs, splat = self.args(n)
        self.calls.append(dict(caller=func, name=name, recv=recv, recv_name=recv_name, line=n.start_point[0] + 1,column_bytes=n.start_point[1],
                               nargs=nargs, splat=splat, dynamic=dynamic, end=(n.end_point[0] + 1, n.end_point[1])))

    def handle_call(self, n, func, cls):
        self.callback_refs(n, func)
        self.adapter["handle_call"](self, n, func, cls)

    @staticmethod
    def try_arm(n):
        """"<try start>.<arm start>" for the try body or except clause holding n, inside the same function."""
        child, parent = n, n.parent
        while parent is not None and parent.type not in ("function_definition", "class_definition", "lambda"):
            if parent.type == "try_statement":
                body = parent.child_by_field_name("body")
                in_arm = child.type in ("except_clause", "except_group_clause") or (body is not None and child.start_byte == body.start_byte)
                return f"{parent.start_byte}.{child.start_byte}" if in_arm else None
            child, parent = parent, parent.parent
        return None

    def shadow_targets(self, n, func):
        """Names bound by for/with/except/:=/match hide an enclosing binding (and its type) in this function."""
        t = n.type
        if t == "for_statement":
            roots = [n.child_by_field_name("left")]
        elif t == "named_expression":
            roots = [n.child_by_field_name("name")]
        elif t in ("with_item", "except_clause", "except_group_clause"):
            roots = [x for x in _iter(n) if x.type == "as_pattern_target"]
        else:  # case_clause: captures are bare names in the pattern, plus *rest / **rest
            roots = [x for x in _iter(n) if (x.type == "dotted_name" and x.parent is not None
                     and x.parent.type in ("case_pattern", "keyword_pattern") and len(x.named_children) == 1)
                     or x.type == "splat_pattern"]
        for root in roots:
            if root is None:
                continue
            for x in [root] + list(_iter(root)):
                if x.type == "identifier":
                    self.shadowed[func].add(txt(x))
                    self.vtypes[func].pop(txt(x), None)

    def handle_import(self, n, scope=0):
        line = n.start_point[0] + 1
        if self.f == "python":
            if n.type == "import_from_statement":
                mod = txt(n.child_by_field_name("module_name"))
                arm = self.try_arm(n)
                for c in n.children_by_field_name("name"):
                    imported = txt(c.child_by_field_name("name")) if c.type == "aliased_import" else txt(c)
                    alias = txt(c.child_by_field_name("alias")) if c.type == "aliased_import" else imported
                    self.imports.append((mod, imported, line))
                    self.bindings.append((mod, imported, alias))
                    self.imports.append((mod, f"__bind__:{alias}:{imported}", line))  # every occurrence, in order
                    if scope == 0:  # module level: the package re-exports this name
                        self.imports.append((mod, f"__export__:{alias}:{imported}", line))
                    if arm is not None:  # an arm of a try statement: the arms are alternatives of each other
                        self.imports.append((mod, f"__alt__:{arm}:{alias}:{imported}", line))
                if any(c.type == "wildcard_import" for c in n.children):
                    self.imports.append((mod, "*", line))  # star import: the module is still known
            else:
                for c in n.named_children:
                    module = txt(c.child_by_field_name("name")) if c.type == "aliased_import" else txt(c)
                    alias = txt(c.child_by_field_name("alias")) if c.type == "aliased_import" else module.split(".")[0]
                    target = module if c.type == "aliased_import" else module.split(".")[0]
                    self.modules[alias] = target
                    self.module_bindings.append((scope, alias, target))
                    self.imports.append((target, "__module__:" + alias, line))
        elif self.f == "php":
            q = next((c for c in n.named_children if c.type in ("qualified_name", "name")), None)
            if q is not None:
                al = n.child_by_field_name("alias")
                module = txt(q).lstrip("\\")
                local = txt(al) if al is not None else last_seg(txt(q))
                declaration = n.parent.parent if n.parent.type == 'namespace_use_group' else n.parent
                first = declaration.named_children[0]
                if n.parent.type == 'namespace_use_group':
                    prefix = next((txt(c) for c in declaration.named_children if c.type == 'namespace_name'), '')
                    module = prefix.strip('\\') + '\\' + module
                if any(c.type == 'function' for node in (n, declaration, first) for c in node.children):
                    local = '__function__:' + local
                self.imports.append((module, local, line))
        else:
            s = n.child_by_field_name("source")
            mod = txt(s).strip("'\"`")
            names = []
            for x in _iter(n):
                if x.type == "import_specifier":
                    imported = txt(x.child_by_field_name("name"))
                    alias = txt(x.child_by_field_name("alias")) or imported
                    names.append(imported)
                    self.bindings.append((mod, imported, alias))
            for c in n.named_children:
                if c.type == "import_clause":
                    for d in c.named_children:
                        if d.type == "identifier":
                            names.append(txt(d))
            for nm in names or [""]:
                self.imports.append((mod, nm, line))


def _iter(n):
    st = [n]
    while st:
        x = st.pop()
        yield x
        st.extend(x.children)


# ----------------------------------------------------------------- moduli JavaScript/TypeScript


def js_json(raw):
    """JSON with comments and trailing commas (tsconfig style), as data."""
    text = raw.decode("utf-8", "replace")
    text = re.sub(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', lambda m: m.group(1) or "", text, flags=re.S)
    text = re.sub(r'("(?:\\.|[^"\\])*")|,(\s*[}\]])', lambda m: m.group(1) or m.group(2), text)
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def js_module_config(root):
    """tsconfig/jsconfig 'paths' and 'baseUrl' per folder, and workspace packages; files are only read."""
    import posixpath
    pats = load_ignore(root)
    listed = [p for p in checkout_files(root) if not any(x in SKIP_DIRS for x in p.split("/")[:-1]) and not ignored(p, pats)]
    wanted = ("tsconfig.json", "jsconfig.json", "package.json", "pnpm-workspace.yaml", "lerna.json")
    present = {p for p in listed if posixpath.basename(p) in wanted}
    jsons = {p for p in listed if p.endswith(".json")}  # tsconfig 'extends' may name any JSON file

    def read(rel):
        try:
            path = local_path(root, rel)
            if path.is_file() and path.stat().st_size <= 200000:
                return path.read_bytes()
        except (OSError, ValueError):
            pass
        return None

    def options(rel, depth=0):
        data = js_json(read(rel) or b"") if rel in jsons else None
        if data is None or depth > 5:
            return {}
        result = {}
        extends = data.get("extends")
        for parent in ([extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []):
            if isinstance(parent, str) and parent.startswith("."):
                parent = posixpath.normpath(posixpath.join(posixpath.dirname(rel), parent))
                result.update(options(parent if parent.endswith(".json") else parent + ".json", depth + 1))
        compiler = data.get("compilerOptions") if isinstance(data.get("compilerOptions"), dict) else {}
        folder = posixpath.dirname(rel)
        if isinstance(compiler.get("baseUrl"), str):
            result["base"] = posixpath.normpath(posixpath.join(folder, compiler["baseUrl"])).removeprefix("./")
            result["base"] = "" if result["base"] == "." else result["base"]
        if isinstance(compiler.get("paths"), dict):
            base = result.get("base", folder)
            result["paths"] = [[pattern, [t for t in targets if isinstance(t, str)][:10], base]
                               for pattern, targets in sorted(compiler["paths"].items())[:500] if isinstance(targets, list)]
        return result

    configs = {}
    for rel in sorted(present):
        name = posixpath.basename(rel)
        if name in ("tsconfig.json", "jsconfig.json"):
            folder = posixpath.dirname(rel)
            if folder not in configs or name == "tsconfig.json":
                found = options(rel)
                configs[folder] = dict(base=found.get("base"), paths=found.get("paths", []))
    globs = []  # (workspace root folder, glob): package.json workspaces, lerna.json, pnpm-workspace.yaml
    for rel in sorted(present)[:2000]:
        name, folder = posixpath.basename(rel), posixpath.dirname(rel)
        if name in ("package.json", "lerna.json"):
            data = js_json(read(rel) or b"") or {}
            found = data.get("workspaces") if name == "package.json" else data.get("packages")
            found = found.get("packages") if isinstance(found, dict) else found
            globs += [(folder, g) for g in found if isinstance(g, str)] if isinstance(found, list) else []
        elif name == "pnpm-workspace.yaml":
            block = False
            for line in (read(rel) or b"").decode("utf-8", "replace").splitlines():
                if re.match(r"^packages\s*:", line):
                    block = True
                elif block and re.match(r"^\s*-\s*", line):
                    globs.append((folder, re.sub(r"^\s*-\s*", "", line).split("#")[0].strip().strip("'\"")))
                elif block and line.strip() and not line.startswith((" ", "\t")):
                    block = False

    def pattern(folder, glob):
        glob = posixpath.normpath(posixpath.join(folder, glob.strip().lstrip("!")))
        parts = (re.escape(part).replace(r"\*\*", ".*").replace(r"\*", "[^/]*") for part in glob.split("/"))
        return re.compile("/".join(parts) + "$")

    include = [pattern(f, g) for f, g in globs if g.strip() and not g.startswith("!")][:500]
    exclude = [pattern(f, g) for f, g in globs if g.startswith("!")][:500]
    packages, seen = {}, set()
    for rel in sorted(p for p in present if posixpath.basename(p) == "package.json"):
        folder = posixpath.dirname(rel)
        if not any(p.match(folder) for p in include) or any(p.match(folder) for p in exclude):
            continue
        data = js_json(read(rel) or b"")
        name = data.get("name") if data else None
        if not isinstance(name, str) or not name:
            continue
        entries = []
        exports = data.get("exports")

        def chosen(value):  # a target string, through conditions ({"import": ..., "default": ...}) and arrays
            for _ in range(4):
                if isinstance(value, list):
                    value = next((v for v in value if isinstance(v, (str, dict))), None)
                elif isinstance(value, dict):
                    value = next((v for k, v in value.items() if k in ("source", "import", "require", "default", "types", "node")), None)
            return value if isinstance(value, str) else None

        subpaths = None
        if isinstance(exports, dict) and any(isinstance(k, str) and k.startswith(".") for k in exports):
            subpaths = {k: chosen(v) for k, v in exports.items() if isinstance(k, str) and k.startswith(".")}
        elif exports is not None:  # a string or conditions: only the package root is exported
            subpaths = {".": chosen(exports)}
        subpaths = {k: v for k, v in (subpaths or {}).items() if v} if subpaths is not None else None
        exports = exports.get(".", exports) if isinstance(exports, dict) else exports
        for value in [data.get("source"), exports] + [data.get(k) for k in ("module", "main", "types", "typings")]:
            if isinstance(value, dict):
                value = next((v for k, v in value.items() if isinstance(v, str) and k in ("source", "import", "require", "default", "types", "node")), None)
            if isinstance(value, str) and value not in entries:
                entries.append(value)
        if name in seen:
            packages.pop(name, None)  # two workspace packages with one name: ambiguous, left unresolved
        else:
            packages[name] = dict(dir=folder, entries=entries[:8])
            if subpaths is not None:
                packages[name]["exports"] = dict(sorted(subpaths.items())[:200])
        seen.add(name)
    return dict(configs=configs, packages=packages)


def js_store_config(db, root, files):
    """Store the JS/TS module configuration before resolve; True when it changed (edges must be recomputed)."""
    row = db.execute("SELECT v FROM meta WHERE k='js_modules'").fetchone()
    if not any(fam(LANG_BY_EXT[os.path.splitext(rel)[1]]) == "js" for rel in files):
        db.execute("DELETE FROM meta WHERE k='js_modules'")
        return row is not None
    value = json.dumps(js_module_config(root), sort_keys=True)
    db.execute("INSERT OR REPLACE INTO meta VALUES('js_modules',?)", (value,))
    return row is None or row[0] != value


def js_dynamic_hint(db, value, sids):
    """impact --json on a JS/TS symbol: dynamic calls in JS/TS files, and import()/require() of the target's file."""
    marks = ",".join("?" * len(sids))
    targets = {path for path, lang in db.execute(f"SELECT f.path,f.lang FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.id IN ({marks})", list(sids))
               if fam(lang) == "js"}
    if not targets:
        return
    rows = [tuple(r) for r in db.execute("SELECT c.file_id,f.path,c.line,s.qname,c.recv,c.recv_name FROM calls c JOIN files f ON f.id=c.file_id "
                                         "LEFT JOIN symbols s ON s.id=c.caller_id WHERE c.dynamic=1 AND f.lang IN ('javascript','typescript','tsx') "
                                         "ORDER BY f.path,c.line,c.id")]
    if not rows:
        return
    modules = JsModules(db)
    loads = [dict(file=path, line=line, caller=caller, module=spec) for fid, path, line, caller, recv, spec in rows
             if recv == "dynamic_import" and spec and any(modules.paths[f] in targets for f in modules.files(fid, spec))]
    value["dynamic"] = dict(calls=len(rows), loads=loads[:50],
                            note='Dynamic JavaScript/TypeScript calls (import(), require(expression), f()()) are not in the graph; '
                                 'loads are file loads for the symbol. Check with rg before changing signatures.')
    measurement = value.get("measurement")
    for _ in range(5 if isinstance(measurement, dict) and "baseline_chars" in measurement else 0):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        measurement.update(actual_chars=len(text), actual_bytes=len(text.encode()), estimated_tokens=(len(text) + 3) // 4,
                           saved_chars=measurement["baseline_chars"] - len(text))


# ----------------------------------------------------------------- indicizzazione
def local_path(root, rel):
    """Paths from the checkout or DB must stay inside root and never follow links."""
    base = Path(root).resolve()
    part = Path(rel)
    if part.is_absolute() or ".." in part.parts:
        raise ValueError(f"path outside root: {rel}")
    path = base
    for component in part.parts:
        path = path / component
        if path.is_symlink():
            raise ValueError(f"symlink not allowed: {rel}")
    if path.exists() and not (path.is_dir() or stat.S_ISREG(path.stat().st_mode)):
        raise ValueError(f"not a regular file: {rel}")
    return path


def regular_source(root, rel):
    try:
        return local_path(root, rel).is_file()
    except (OSError, ValueError):
        return False


def checkout_files(root):
    """NUL-delimited Git paths; an empty successful listing stays empty."""
    try:
        r = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", root, "ls-files", "-z", "-co", "--exclude-standard"],
                           capture_output=True, timeout=60)
        if r.returncode == 0:
            return list(dict.fromkeys(os.fsdecode(p) for p in r.stdout.split(b"\0") if p))
    except (OSError, subprocess.TimeoutExpired):
        pass
    files = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        files.extend(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/") for f in fns)
    return files


def load_ignore(root):
    """.cgignore nella root: un glob per riga (es. libs/*, tests/lib/*, *.min.js). Per codice vendorizzato nel repo."""
    pats = []
    try:
        for line in local_path(root, ".cgignore").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line.rstrip("/") + ("*" if line.endswith("/") else ""))
    except (OSError, ValueError):
        pass
    return pats


def ignored(rel, pats):
    import fnmatch
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, p + "/*") for p in pats)


def list_files(root):
    pats = load_ignore(root)
    return [f for f in _list_files(root) if not ignored(f, pats)]


def _list_files(root):
    return [p for p in checkout_files(root)
            if not any(x in SKIP_DIRS for x in p.split("/")[:-1])
            and os.path.splitext(p)[1] in LANG_BY_EXT and regular_source(root, p)]


def connect(root, create=False):
    try:
        d = local_path(root, DB_DIR)
        for leaf in ("graph.db", ".gitignore", "graph.db-journal", "graph.db-wal", "graph.db-shm"):
            local_path(root, f"{DB_DIR}/{leaf}")
    except (OSError, ValueError) as e:
        sys.exit(f"Index rejected: {e}")
    if create:
        os.makedirs(d, exist_ok=True)
        gi = os.path.join(d, ".gitignore")
        if not os.path.exists(gi):
            Path(gi).write_text("*\n", encoding="utf-8")
    p = os.path.join(d, "graph.db")
    if not create and not os.path.exists(p):
        sys.exit(f"No index in {root}. Run first: cg.py index")
    db = sqlite3.connect(p)
    try:
        v = db.execute("SELECT v FROM meta WHERE k='version'").fetchone()
    except sqlite3.OperationalError:
        v = None
    if v and v[0] != INDEX_VERSION:
        if not create:
            sys.exit(f"Index created with code-graph {v[0]}, engine {VERSION}: run cg.py index again")
        db.close()
        os.remove(p)
        db = sqlite3.connect(p)
    db.executescript(SCHEMA)
    if not create and db.execute("SELECT 1 FROM files WHERE lang='dart' LIMIT 1").fetchone():
        if not LANGUAGE_ADAPTERS['dart']['fresh_package_config'](db,root,configuration_files(root)):
            db.close()
            sys.exit('Dart configuration or root changed: run index before querying the graph.')
    return db


def static_php_include(source_path, expression):
    match = re.fullmatch(r"__DIR__\s*\.\s*(['\"])([^$\\]*?)\1", expression.strip())
    if not match:
        return None
    import posixpath
    target = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), match.group(2).lstrip('/')))
    if target.startswith('../') or target.startswith('/'):
        return None
    return target.removeprefix('./')


def js_config_paths(root):
    """The tsconfig.json and jsconfig.json files js_module_config reads, in any folder, with the relative files their
    'extends' names: editing one changes JS/TS resolution, so it is a configuration file of the index."""
    import posixpath
    pats = load_ignore(root)
    jsons = {p for p in checkout_files(root) if p.endswith(".json") and not any(x in SKIP_DIRS for x in p.split("/")[:-1])
             and not ignored(p, pats)}
    found, pending = [], sorted(p for p in jsons if posixpath.basename(p) in ("tsconfig.json", "jsconfig.json"))
    while pending and len(found) < 500:
        rel = pending.pop(0)
        if rel in found:
            continue
        found.append(rel)
        try:
            path = local_path(root, rel)
            data = js_json(path.read_bytes()) if path.is_file() and path.stat().st_size <= 200000 else None
        except (OSError, ValueError):
            data = None
        extends = data.get("extends") if data else None
        for parent in [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []:
            if isinstance(parent, str) and parent.startswith("."):
                parent = posixpath.normpath(posixpath.join(posixpath.dirname(rel), parent))
                parent = parent if parent.endswith(".json") else parent + ".json"
                if parent in jsons and parent not in found:
                    pending.append(parent)
    return found


def configuration_files(root):
    names = ["README.md", "package.json", "pyproject.toml", ".cgignore", ".gitignore", ".code-graph-manifest.json", ".code-graph-catalog.json",
             "deploy/commands.json"]
    names.extend(path for path in js_config_paths(root) if path not in names)  # tsconfig/jsconfig: JS/TS module resolution
    go_paths = set(go_config_paths(root))
    java_paths = set(java_module_paths(root))
    dart_paths = set(dart_config_paths(root))
    bounded_paths = go_paths | java_paths | dart_paths
    names.extend(path for path in sorted(bounded_paths) if path not in names)
    try:
        folder = local_path(root, ".github/workflows")
        if folder.is_dir():
            names.extend(str(path.relative_to(Path(root).resolve())) for path in sorted(folder.iterdir())[:100] if path.suffix in (".yaml", ".yml"))
    except (OSError, ValueError):
        pass
    try:
        deploy = local_path(root, 'deploy')
        if deploy.is_dir():
            names.extend(str(path.relative_to(Path(root).resolve())) for path in sorted(deploy.rglob('*'))[:1000] if path.suffix in ('.conf','.service') and not path.is_symlink())
    except (OSError,ValueError):
        pass
    result = {}
    for name in names:
        prefix = b'\0codegraph-dart-config:' if name in dart_paths else b'\0codegraph-java-config:' if name in java_paths else b'\0codegraph-go-config:'
        if name in bounded_paths:
            result[name] = prefix + b'unavailable'
        try:
            if name in dart_paths:
                directory=local_path(root,str(Path(name).parent.parent))/'.dart_tool'
                if directory.is_symlink():
                    result[name]=prefix+b'directory-symlink:'+hashlib.sha256(os.fsencode(os.readlink(directory))).hexdigest().encode()
                    continue
            if name in bounded_paths:
                leaf = local_path(root, str(Path(name).parent)) / Path(name).name
                if leaf.is_symlink():
                    result[name] = prefix + b'symlink:' + hashlib.sha256(os.fsencode(os.readlink(leaf))).hexdigest().encode()
                    continue
            path = local_path(root, name)
            if path.is_file():
                with path.open("rb") as handle:
                    raw = handle.read(200001)
                if len(raw) <= 200000:
                    result[name] = raw
                elif name in bounded_paths:
                    result[name] = prefix + b'oversize:' + hashlib.sha256(raw).hexdigest().encode()
        except (OSError, ValueError):
            continue
    return result


def go_config_paths(root):
    pats = load_ignore(root)
    selected = [path for path in checkout_files(root) if not any(part in SKIP_DIRS for part in path.split('/')[:-1])
                and not ignored(path, pats)]
    wanted = {'go.mod', 'go.work'} | {path for path in selected if os.path.basename(path) in ('go.mod', 'go.work')}
    for source in selected:
        if not source.endswith('.go') or not regular_source(root, source):
            continue
        parent = Path(source).parent
        while True:
            wanted.update(str(parent / name) for name in ('go.mod', 'go.work'))
            if parent == Path('.'): break
            parent = parent.parent
    result = []
    for path in sorted(wanted):
        try:
            parent = local_path(root, str(Path(path).parent))
            if os.path.lexists(parent / Path(path).name): result.append(path)
        except (OSError, ValueError):
            continue
    return result


def java_module_paths(root):
    pats = load_ignore(root)
    wanted = {'module-info.java'}
    for source in checkout_files(root):
        if not source.endswith('.java') or any(part in SKIP_DIRS for part in source.split('/')[:-1]) or ignored(source,pats) or not regular_source(root,source):
            continue
        for parent in Path(source).parents:
            wanted.add(str(parent/'module-info.java'))
    result = []
    for path in sorted(wanted):
        try:
            if os.path.lexists(local_path(root,str(Path(path).parent))/Path(path).name): result.append(path)
        except (OSError,ValueError):
            continue
    return result


def dart_config_paths(root):
    pats=load_ignore(root);wanted={'.dart_tool/package_config.json'}
    for source in checkout_files(root):
        if not source.endswith('.dart') or any(part in SKIP_DIRS for part in source.split('/')[:-1]) or ignored(source,pats) or not regular_source(root,source):continue
        wanted.update(str(parent/'.dart_tool/package_config.json') for parent in Path(source).parents)
    result=[]
    for path in sorted(wanted):
        try:
            directory=local_path(root,str(Path(path).parent.parent))/'.dart_tool'
            if directory.is_symlink() or (os.path.lexists(directory) and not directory.is_dir()) or os.path.lexists(directory/'package_config.json'):
                result.append(path)
        except (OSError,ValueError):continue
    return result


def get_parser_for(lang, cache={}):
    if lang not in cache:
        try:
            from tree_sitter_language_pack import get_parser
        except ImportError:
            sys.exit('tree-sitter is missing from this interpreter. Run scripts/setup.sh in the skill, then use scripts/cg.')
        cache[lang] = get_parser(lang)
    return cache[lang]


_CATALOG_MODULE = {}


def catalog_for(root=None):
    """The effective API catalog (scripts/catalog.py): base files parsed once per process, the project's
    .code-graph-catalog.json read again on each call (a rejected one is reported in warnings). Never per file."""
    if not _CATALOG_MODULE:
        import runpy
        _CATALOG_MODULE.update(runpy.run_path(str(Path(__file__).with_name('catalog.py'))))
        _CATALOG_MODULE['base'] = _CATALOG_MODULE['load'](None)
    return _CATALOG_MODULE['load'](root) if root is not None else _CATALOG_MODULE['base']


_PYTHON_VIEWS = {}


def python_catalog(catalog):
    """Lookup tables of the Python entries, keyed by (via, module, name), built once per catalog fingerprint."""
    view = _PYTHON_VIEWS.get(catalog.fingerprint)
    if view is None:
        entries = [entry for entry in catalog.entries if entry['language'] == 'python']
        origin = lambda entry: (entry['origin']['via'], entry['origin'].get('module', ''), entry['origin']['name'])
        view = _PYTHON_VIEWS[catalog.fingerprint] = {role: {origin(e): e for e in entries if e['role'] == role}
                                                     for role in ('pure_decorator', 'registrar', 'callback', 'dynamic')}
        view['dynamic_names'] = {e['origin']['name'].rpartition('.')[2] for e in entries if e['role'] == 'dynamic'}
        view['default'] = catalog.defaults.get('python', 'registrar')
    return view


def analysis_inputs(root=None, catalog=None):
    """Engine code, base catalog files, parser versions and the project's effective catalog: a change in any
    of them makes the next index re-parse every file."""
    import importlib.metadata
    here=Path(__file__).parent
    hashes={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(here.glob('*.py'))}
    hashes.update({'catalog/'+path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted((here/'catalog').glob('*.json'))})
    parsers={name:importlib.metadata.version(name) for name in ('tree-sitter','tree-sitter-language-pack')}
    catalog=(catalog or catalog_for(root)).fingerprint
    signature=hashlib.sha256(json.dumps([hashes,parsers,catalog],sort_keys=True).encode()).hexdigest()
    return hashes,parsers,signature


_SEARCH_MODULE = {}


def search_module():
    if not _SEARCH_MODULE:
        _SEARCH_MODULE.update(runpy.run_path(str(Path(__file__).with_name('search.py'))))
    return _SEARCH_MODULE


def cmd_start(a):
    from types import SimpleNamespace
    runpy.run_path(str(Path(__file__).with_name('start.py')))['cli'](sys.modules.get(__name__) or SimpleNamespace(**globals()), a)


def cmd_locate(a):
    from types import SimpleNamespace
    runpy.run_path(str(Path(__file__).with_name('locate.py')))['cli'](sys.modules.get(__name__) or SimpleNamespace(**globals()), a)


def cmd_search(a):
    from types import SimpleNamespace
    search_module()['cli'](sys.modules.get(__name__) or SimpleNamespace(**globals()), a)


def cmd_index(a):
    root = a.root
    db = connect(root, create=True)
    t0 = time.time()
    files = list_files(root)
    catalog=catalog_for(root)
    for warning in catalog.warnings:
        print('catalog warning: '+warning, file=sys.stderr)
    engine_hashes,parser_versions,analysis_signature=analysis_inputs(root, catalog)
    previous_analysis=(db.execute("SELECT v FROM meta WHERE k='analysis_fingerprint'").fetchone() or [None])[0]
    search = search_module()
    try:
        search_enabled, search_rebuild = search['setup'](db)
        rebuild=a.full or previous_analysis!=analysis_signature or search_rebuild
        if search_enabled and rebuild:
            db.execute('DELETE FROM search_fts')
    except sqlite3.Error:
        db.rollback()
        db.close()
        raise
    if not search_enabled:
        print('search warning: SQLite has no FTS5; static indexing is available.', file=sys.stderr)
    known = {r[1]: (r[0], r[2], r[3]) for r in db.execute("SELECT id,path,sha,mtime FROM files")}
    row = db.execute("SELECT v FROM meta WHERE k='partial_paths'").fetchone()
    partial_paths = set(json.loads(row[0])) if row else set()
    seen, changed, skipped, failed = set(), 0, 0, []
    for position, rel in enumerate(files):
        if getattr(a, "progress", None):
            a.progress(dict(phase="indexing", completed=position, total=len(files), file=rel))
        try:
            full = local_path(root, rel)
            st = full.stat()
            if st.st_size > MAX_BYTES:
                skipped += 1
                continue
            src = full.read_bytes()
        except (OSError, ValueError) as e:
            failed.append(f"{rel}: {e}")
            continue
        if rel.endswith(".min.js") or (src.count(b"\n") < 3 and len(src) > 20000):
            skipped += 1
            continue
        seen.add(rel)
        sha = hashlib.sha1(src).hexdigest()
        if rel in known and not rebuild and known[rel][1] == sha:
            db.execute("UPDATE files SET mtime=? WHERE id=?", (st.st_mtime, known[rel][0]))
            continue
        lang = LANG_BY_EXT[os.path.splitext(rel)[1]]
        if rel in known:
            drop_file(db, known[rel][0])
        try:
            tree = get_parser_for(lang).parse(src)
            ex = Extractor(lang, src, rel, root, catalog=catalog).run(tree.root_node)
            if tree.root_node.has_error:
                partial_paths.add(rel)
            else:
                partial_paths.discard(rel)
        except Exception as e:
            failed.append(f"{rel}: {e}")
            continue
        cur = db.execute("INSERT INTO files(path,lang,sha,mtime,loc) VALUES(?,?,?,?,?)",
                         (rel, lang, sha, st.st_mtime, src.count(b"\n") + 1))
        fid = cur.lastrowid
        ids = []
        for s in ex.syms:
            pid = ids[s["parent"]] if s["parent"] is not None else None
            c = db.execute("INSERT INTO symbols(file_id,name,qname,kind,parent_id,line,end_line,pmin,pmax,sig,exported,visibility)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (fid, s["name"] if s["kind"] != "module" else rel, s["qname"] if s["kind"] != "module" else rel,
                            s["kind"], pid, s["line"], s["end_line"], s["pmin"], s["pmax"], s["sig"], s["exported"], s["vis"]))
            ids.append(c.lastrowid)
        if search_enabled:
            try:
                search['store'](db, fid, rel, src, ex.syms, ids)
            except sqlite3.Error as error:
                failed.append(f'{rel}: ricerca FTS: {error}')
                continue
        db.executemany("INSERT INTO calls(file_id,caller_id,name,recv,recv_name,line,nargs,splat,dynamic) VALUES(?,?,?,?,?,?,?,?,?)",
                       [(fid, ids[c["caller"]], c["name"], c["recv"], c["recv_name"], c["line"], c["nargs"], c["splat"],
                         c["dynamic"]) for c in ex.calls])
        db.executemany("INSERT INTO imports VALUES(?,?,?,?)", [(fid, m, nm, ln) for m, nm, ln in ex.imports])
        db.executemany("INSERT INTO import_bindings VALUES(?,?,?,?)", [(fid, m, orig, local) for m, orig, local in ex.bindings])
        db.executemany("INSERT INTO module_bindings VALUES(?,?,?,?)", [(fid, ids[scope], local, module) for scope, local, module in ex.module_bindings])
        db.executemany("INSERT INTO symbol_refs(file_id,owner_id,target_id,name,receiver,kind,line) VALUES(?,?,NULL,?,?,?,?)",
                       [(fid, ids[r["owner"]], r["name"], r["receiver"], r["kind"], r["line"]) for r in ex.references])
        db.executemany("INSERT INTO observations(file_id,owner_id,kind,callee,line,value,value_kind) VALUES(?,?,?,?,?,?,?)",
                       [(fid, ids[o["owner"]], o["kind"], o["callee"], o["line"], o["value"], o["value_kind"]) for o in ex.observations])
        if lang == 'php':
            # Internal provenance is deleted with its file; IDs bind it to the exact fact, not a line guess.
            for table, value_kind, facts in (('symbol_refs','reference_id',ex.references),('calls','call_id',ex.calls)):
                fact_ids = [row[0] for row in db.execute(f'SELECT id FROM {table} WHERE file_id=? ORDER BY id',(fid,))]
                for fact_id, fact in zip(fact_ids, facts):
                    if fact.get('php_api'):
                        owner = fact['owner'] if table == 'symbol_refs' else fact['caller']
                        db.execute('INSERT INTO observations(file_id,owner_id,kind,callee,line,value,value_kind) VALUES(?,?,?,?,?,?,?)',
                                   (fid,ids[owner],'php.callback_api',fact['php_api'],fact['line'],str(fact_id),value_kind))
        db.executemany('INSERT INTO assignments VALUES(?,?,?,?,?,?)', [(fid,ids[cls],name,line,form,certainty) for cls,name,line,form,certainty in ex.assignments])
        db.executemany('INSERT INTO guarded_definitions VALUES(?,?,?)', [(fid,ids[symbol],line) for symbol,line in ex.guarded])
        for expression,line,certainty in ex.includes:
            target = static_php_include(rel, expression)
            if target is not None:
                db.execute('INSERT INTO includes VALUES(?,?,?,?)', (fid,target,line,certainty))
        db.executemany("INSERT INTO bases VALUES(?,?,NULL)", [(ids[s], b) for s, b in ex.bases])
        changed += 1
    if failed:
        db.rollback()
        db.close()
        sys.exit('Index not updated (rolled back); read/parser errors:\n' + "\n".join(failed))
    removed = 0
    for rel, (fid, _, _) in known.items():
        if rel not in seen:
            drop_file(db, fid)
            removed += 1
    if getattr(a, "progress", None):
        a.progress(dict(phase="resolving", completed=len(files), total=len(files), file=""))
    if js_store_config(db, root, files):  # tsconfig/workspace changes re-resolve JS/TS imports
        db.execute("DELETE FROM edges")
    has_dart=any(path.endswith('.dart') for path in files)
    config_snapshot=configuration_files(root) if has_dart else {}
    try:
        if any(path.endswith('.go') for path in files) and LANGUAGE_ADAPTERS['go']['store_config'](db, configuration_files(root), go_config_paths(root)):
            db.execute("DELETE FROM edges")
        if any(path.endswith('.java') for path in files) and LANGUAGE_ADAPTERS['java']['store_config'](db,java_module_paths(root)):
            db.execute("DELETE FROM edges")
        if has_dart and LANGUAGE_ADAPTERS['dart']['store_config'](db,config_snapshot,root):
            db.execute("DELETE FROM edges")
    except sqlite3.Error:
        db.rollback()
        db.close()
        raise
    nedges = resolve(db) if (changed or removed or rebuild or not db.execute("SELECT 1 FROM edges LIMIT 1").fetchone()) else None
    db.execute("INSERT OR REPLACE INTO meta VALUES('version',?)", (INDEX_VERSION,))
    final_configs=configuration_files(root)
    if has_dart and LANGUAGE_ADAPTERS['dart']['config_inputs'](final_configs)!=LANGUAGE_ADAPTERS['dart']['config_inputs'](config_snapshot):
        db.rollback();db.close()
        sys.exit('Dart configuration changed during index (rolled back): run index again.')
    config_hashes = {path: hashlib.sha256(raw).hexdigest() for path, raw in final_configs.items()}
    db.execute("INSERT OR REPLACE INTO meta VALUES('analysis_fingerprint',?)",(analysis_signature,))
    db.execute("INSERT OR REPLACE INTO meta VALUES('catalog_fingerprint',?)",(catalog.fingerprint,))
    db.execute("INSERT OR REPLACE INTO meta VALUES('catalog_warnings',?)",(json.dumps(list(catalog.warnings)),))
    fingerprint = dict(files=list(db.execute('SELECT path,lang,sha FROM files ORDER BY path')), configs=config_hashes,
                       engine=engine_hashes, parsers=parser_versions, skipped=skipped, catalog=catalog.fingerprint)
    if has_dart:
        dart_config=json.loads(db.execute("SELECT v FROM meta WHERE k='dart_config'").fetchone()[0])
        if dart_config['mode']!='files':fingerprint['dart_packages']=dart_config
    content_revision = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    # A full rebuild may reuse integer IDs differently even for identical content.
    layout = list(db.execute('SELECT f.path,s.id,s.qname,s.line FROM symbols s JOIN files f ON f.id=s.file_id ORDER BY f.path,s.line,s.id'))
    revision = hashlib.sha256(json.dumps([content_revision, layout], sort_keys=True).encode()).hexdigest()
    for key, value in [('revision', revision), ('content_revision', content_revision),
                       ('run_id', secrets.token_hex(16)), ('last_checked_at', time.strftime('%Y-%m-%d %H:%M:%S'))]:
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, value))
    db.execute("INSERT OR REPLACE INTO meta VALUES('config_hashes',?)", (json.dumps(config_hashes, sort_keys=True),))
    db.execute("INSERT OR REPLACE INTO meta VALUES('indexed_at',?)", (time.strftime("%Y-%m-%d %H:%M:%S"),))
    db.execute("INSERT OR REPLACE INTO meta VALUES('candidate_files',?)", (str(len(files)),))
    db.execute("INSERT OR REPLACE INTO meta VALUES('skipped_files',?)", (str(skipped),))
    partial_paths.intersection_update(seen)
    db.execute("INSERT OR REPLACE INTO meta VALUES('partial_files',?)", (str(len(partial_paths)),))
    db.execute("INSERT OR REPLACE INTO meta VALUES('partial_paths',?)", (json.dumps(sorted(partial_paths)),))
    db.commit()
    n = db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    print(f"index ok: {n} files ({changed} updated, {removed} removed, {skipped} skipped) "
          f"edges={'unchanged' if nedges is None else nedges} in {time.time() - t0:.1f}s")


def drop_file(db, fid):
    if (db.execute("SELECT v FROM meta WHERE k='search_status'").fetchone() or [None])[0] == 'ready':
        try:
            db.execute('DELETE FROM search_fts WHERE file_id=?', (fid,))
        except sqlite3.Error:
            db.rollback()
            db.close()
            raise
    sids = [r[0] for r in db.execute("SELECT id FROM symbols WHERE file_id=?", (fid,))]
    if sids:
        q = ",".join("?" * len(sids))
        db.execute(f"DELETE FROM bases WHERE symbol_id IN ({q})", sids)
    for t in ("symbols", "calls", "imports", "import_bindings", "symbol_refs", "observations", "module_bindings", "assignments", "includes", "guarded_definitions"):
        db.execute(f"DELETE FROM {t} WHERE file_id=?", (fid,))
    db.execute("DELETE FROM files WHERE id=?", (fid,))


def resolve(db):
    """Risolve le chiamate in archi con confidenza: high | medium | low. Ricalcolo globale (veloce: solo SQL+dict)."""
    try:
        dart = LANGUAGE_ADAPTERS['dart']['DartLibraries'](db) if db.execute("SELECT 1 FROM files WHERE lang='dart' LIMIT 1").fetchone() else None
        java = LANGUAGE_ADAPTERS['java']['JavaClasses'](db) if db.execute("SELECT 1 FROM files WHERE lang='java' LIMIT 1").fetchone() else None
        swift = LANGUAGE_ADAPTERS['swift']['SwiftFiles'](db) if db.execute("SELECT 1 FROM files WHERE lang='swift' LIMIT 1").fetchone() else None
    except sqlite3.Error:
        db.rollback()
        db.close()
        raise
    go = LANGUAGE_ADAPTERS['go']['GoPackages'](db) if db.execute("SELECT 1 FROM files WHERE lang='go' LIMIT 1").fetchone() else None
    if go is not None:
        try:
            go.bind_owners(db)
        except sqlite3.Error:
            db.rollback()
            db.close()
            raise
    db.execute("DELETE FROM edges")
    syms = {}
    by_name = defaultdict(list)
    methods_of = defaultdict(dict)
    methods_lc = defaultdict(dict)  # class_id -> name -> [ids]
    classes_by_name = defaultdict(list)
    file_of = {}
    for sid, fid, name, kind, pid in db.execute("SELECT id,file_id,name,kind,parent_id FROM symbols"):
        syms[sid] = (fid, name, kind, pid)
        file_of[sid] = fid
        if kind == "module":
            continue
        by_name[name].append(sid)
        if kind in CLASSLIKE:
            classes_by_name[name].append(sid)
        if kind == "method" and pid is not None:
            methods_of[pid].setdefault(name, []).append(sid)
            methods_lc[pid].setdefault(name.lower(), []).append(sid)  # PHP: nomi metodo case-insensitive
    imports = defaultdict(list)
    all_bindings = defaultdict(list)  # every binding of a local name, e.g. both arms of try/except ImportError
    for fid, module, original, local in db.execute("SELECT file_id,module,original,local FROM import_bindings ORDER BY rowid"):
        all_bindings[(fid, local)].append((original, module))
    bindings = {key: values[-1] for key, values in all_bindings.items()}

    def effective(fid, local, rows):
        """The last import of a name wins. When it is the last import of one arm of a try statement, the last
        imports of every arm of that try are alternatives (try/except ImportError)."""
        if not rows:
            return []
        last = max(rows, key=lambda row: row[:2])  # (line, order): source order only
        for arms in try_arms.get((fid, local), {}).values():
            ends = [max(entries, key=lambda row: row[:2]) for entries in arms.values()]
            if last[:2] in [end[:2] for end in ends] and len(ends) > 1:
                return list(dict.fromkeys((original, module) for _, _, original, module in sorted(ends, key=lambda row: row[:2])))
        return [last[2:]]
    php_ns, php_uses = {}, defaultdict(dict)
    python_modules = defaultdict(dict)
    occurrences = defaultdict(list)  # (fid, local) -> every from-import binding: (line, order, original, module)
    reexports = defaultdict(list)  # (fid, local) -> module-level from-imports: what a package re-exports
    try_arms = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))  # (fid, local) -> try -> arm -> bindings
    # Rows are read in insertion (source) order: the order breaks ties on one line, never the names.
    # An import writes its __bind__ row first; its __export__/__alt__ rows reuse that occurrence's order.
    current = {}
    for order, (fid, mod, nm, line) in enumerate(db.execute("SELECT file_id,module,name,line FROM imports ORDER BY rowid")):
        if nm.startswith("__bind__:"):
            _, local, original = nm.split(":", 2)
            current[(fid, local)] = occurrence = (line, order, original, mod)
            occurrences[(fid, local)].append(occurrence)
            continue
        if nm.startswith("__export__:"):
            _, local, original = nm.split(":", 2)
            reexports[(fid, local)].append(current.get((fid, local), (line, order, original, mod)))
            continue
        if nm.startswith("__alt__:"):
            _, arm, local, original = nm.split(":", 3)
            statement, _, branch = arm.partition(".")
            try_arms[(fid, local)][statement][branch].append(current.get((fid, local), (line, order, original, mod)))
            continue
        if nm.startswith("__module__:"):
            python_modules[fid][nm.split(":", 1)[1]] = mod
        elif mod == "__ns__":
            php_ns[fid] = nm
        else:
            imports[fid].append((mod, nm))
            php_uses[fid][nm.lower()] = mod
    paths = dict(db.execute("SELECT id,path FROM files"))
    langs = dict(db.execute("SELECT id,lang FROM files"))
    php_fq = defaultdict(list)
    for name, ids_ in classes_by_name.items():
        for k in ids_:
            if langs[file_of[k]] == "php":
                ns = php_ns.get(file_of[k], "")
                php_fq[((ns + "\\") if ns else "") .lower() + name.lower()].append(k)

    if 'php' in langs.values():
        php_names = PhpNames.from_db(db)
        blocked_php_refs = set()
        for fid, api, line, value, value_kind in db.execute("SELECT file_id,callee,line,value,value_kind FROM observations WHERE kind='php.callback_api'"):
            shadowed = bool(php_names.function_targets(fid, line, api))
            if value_kind == 'call_id':
                db.execute('UPDATE calls SET dynamic=? WHERE id=? AND file_id=?',(int(not shadowed),int(value),fid))
            elif shadowed:
                blocked_php_refs.add(int(value))
        php_functions, php_function_calls = php_names.functions, {}
        for cid, fid, name, line in db.execute("SELECT id,file_id,name,line FROM calls"):
            if langs[fid] == 'php':
                scope, _ = php_names.scope(fid, line)
                imported = php_names.function_uses.get((fid, scope, name.lower()))
                if imported is not None:
                    php_function_calls[cid] = imported

    def php_class(fid, raw):
        """Risoluzione PHP come il motore: FQ assoluto, alias 'use', poi namespace corrente."""
        if raw.lower() in ("self", "static", "parent"):
            return None
        if raw.startswith("\\"):
            return php_fq.get(raw.lstrip("\\").lower(), [])
        if raw.lower().startswith("namespace\\"):
            ns = php_ns.get(fid, "")
            return php_fq.get(((ns + "\\") if ns else "") .lower() + raw[10:].lower(), [])
        head, _, rest = raw.partition("\\")
        u = php_uses[fid].get(head.lower())
        if u:
            fq = u + ("\\" + rest if rest else "")
        elif "\\" in raw and not php_ns.get(fid):
            fq = raw
        else:
            ns = php_ns.get(fid, "")
            fq = (ns + "\\" + raw) if ns else raw
        hit = php_fq.get(fq.lower()) or ([] if "\\" in raw else php_fq.get(raw.lower(), []))
        return hit

    def class_targets(fid, raw):
        """-> (lista classi, confidenza). Lista vuota = esterna al progetto."""
        if langs[fid] == "php":
            hit = php_class(fid, raw)
            return (hit or [])[:1], "high"
        binding = bindings.get((fid, raw))
        raw = binding[0] if binding else raw
        ks = [k for k in classes_by_name.get(last_seg(raw), []) if fam(langs[file_of[k]]) == fam(langs[fid])]
        if binding:
            ks = import_match(fid, ks, last_seg(raw), binding[1])
        elif len(ks) > 1:
            ks = [k for k in ks if file_of[k] == fid] or import_match(fid, ks, last_seg(raw)) or ks
        return ks[:5], ("high" if len(ks) == 1 else "low")

    bases = defaultdict(list)
    php_abstract = set(db.execute("SELECT owner_id,callee FROM observations WHERE kind='php.abstract_method'"))

    def enclosing_class(sid):
        while sid is not None:
            fid, name, kind, pid = syms[sid]
            if kind in CLASSLIKE:
                return sid
            sid = pid
        return None

    def find_method(cid, name, depth=0, seen=None, reference=False, concrete_only=False):
        php = cid is not None and langs[file_of[cid]] == "php"
        if (php or reference) and depth == 0 and not concrete_only:
            concrete = find_method(cid, name, concrete_only=True)
            if concrete:
                return concrete
        seen = seen or set()
        if cid is None or cid in seen or depth > 8:
            return []
        seen.add(cid)
        if php and syms[cid][2] == "interface" and concrete_only:
            return []  # An interface declares a contract, not an executable method body.
        local = methods_of.get(cid, {}).get(name, [])
        if php:
            local = local or methods_lc.get(cid, {}).get(name.lower(), [])
        if local:
            if not concrete_only or not php or (cid, name.lower()) not in php_abstract:
                return local
            if syms[cid][2] != "trait":
                return []
        parents = bases.get(cid, [])
        if php and concrete_only:
            # Bodies follow trait precedence; unresolved contracts keep the original lookup order.
            parents = sorted(parents, key=lambda b: syms[b][2] != "trait")
        for b in parents:
            r = find_method(b, name, depth + 1, seen, reference, concrete_only)
            if r:
                return r
        return []

    subs = None

    def impls(cls, name):
        nonlocal subs
        if subs is None:
            subs = defaultdict(list)
            for sid_, lst in bases.items():
                for b in lst:
                    subs[b].append(sid_)
        out_, st, seen = [], [cls], set()
        while st and len(out_) < 10:
            c = st.pop()
            if c in seen:
                continue
            seen.add(c)
            for sub in subs.get(c, []):
                out_.extend(methods_of.get(sub, {}).get(name, []))
                st.append(sub)
        return out_

    def python_target(fid, module):
        dots = len(module) - len(module.lstrip("."))
        parent = os.path.dirname(paths[fid])
        for _ in range(max(0, dots - 1)):
            parent = os.path.dirname(parent)
        target = module[dots:].replace(".", "/")
        return (os.path.normpath(os.path.join(parent, target)) if dots else target), dots

    python_stems = {fid: os.path.splitext(path)[0] for fid, path in paths.items() if langs[fid] == "python"}

    module_files_of = {}  # (target, dots) -> module_files(): asked for again and again, one scan each

    def module_files(fid, module):
        """Project Python files a module name points to (exact path first); empty = outside the project."""
        target, dots = python_target(fid, module)
        found = module_files_of.get((target, dots))
        if found is None:
            variants = (target, target + "/__init__")
            found = tuple(f for f, stem in python_stems.items() if stem in variants)
            if not found and not dots:
                found = tuple(f for f, stem in python_stems.items() if any(stem.endswith("/" + v) for v in variants))
            module_files_of[(target, dots)] = found
        return found

    js = JsModules(db, syms, by_name, bindings, find_method)

    candidate_index = {}

    def indexed(cands):
        """For a long candidate list (all Python modules, a common name): stems and path-segment suffixes to
        candidates, so a module lookup is a dictionary access instead of a scan. Cached per list object."""
        entry = candidate_index.get(id(cands))
        if entry is None or entry[0] is not cands:
            position, exact, suffix = {}, defaultdict(list), defaultdict(list)
            for c in cands:
                if c in position:
                    continue
                position[c] = len(position)
                stem = os.path.splitext(paths[file_of[c]])[0]
                exact[stem].append(c)
                parts = stem.split("/")
                for i in range(1, len(parts)):
                    suffix["/".join(parts[i:])].append(c)
            entry = (cands, position, exact, suffix)
            candidate_index[id(cands)] = entry
        return entry

    def import_match(fid, cands, name, module=None, depth=0):
        if module is not None and fam(langs[fid]) == "js":
            return js.match(fid, cands, name, module)
        if module is not None:
            if langs[fid] == "python":
                target, dots = python_target(fid, module)
            elif module.startswith("."):
                target = os.path.normpath(os.path.join(os.path.dirname(paths[fid]), module))
                if os.path.splitext(target)[1] in LANG_BY_EXT:
                    target = os.path.splitext(target)[0]
            else:
                return []  # package aliases need project-specific module configuration
            variants = (target, target + "/__init__", target + "/index")
            if len(cands) >= 64:
                _, position, by_stem, by_suffix = indexed(cands)
                found = {c for v in variants for c in by_stem.get(v, ())}
                exact = sorted(found, key=position.__getitem__)
                keep = sorted({c for v in variants for c in by_suffix.get(v, ()) if c not in found},
                              key=position.__getitem__) if langs[fid] == "python" and not dots else []
            else:
                exact, keep = [], []
                for c in cands:
                    stem = os.path.splitext(paths[file_of[c]])[0]
                    if stem in variants:
                        exact.append(c)
                    elif langs[fid] == "python" and not dots and any(stem.endswith("/" + v) for v in variants):
                        keep.append(c)
            if exact or keep or langs[fid] != "python" or depth >= 4:
                return exact or keep
            # Follow re-exports: the module file itself imports the name (from .impl import f, or from .impl import *).
            for owner in module_files(fid, module):
                found = []
                for original, inner in effective(owner, name, reexports.get((owner, name), [])):
                    pool = cands if original == name else [c for c in by_name.get(original, [])
                                                           if syms[c][2] in ("function", "class") and langs[file_of[c]] == "python"]
                    found += [c for c in import_match(owner, pool, original, inner, depth + 1) if c not in found]
                if found:
                    return found
                for inner, star in imports.get(owner, []):
                    if star == "*":
                        found = import_match(owner, cands, name, inner, depth + 1)
                        if found:
                            return found
            return []
        mods = [m for m, nm in imports.get(fid, []) if nm == name or (not nm and m)]
        if not mods:
            return []
        keep = []
        for c in cands:
            p = paths[file_of[c]].lower()
            for m in mods:
                seg = module_key(m)
                stem = os.path.splitext(os.path.basename(p))[0]
                if seg and (seg == stem or "/" + seg + "/" in "/" + p or seg == name.lower()):
                    keep.append(c)
                    break
        return keep

    scoped_modules = {(fid, scope, local): module for fid, scope, local, module in db.execute("SELECT file_id,scope_id,local,module FROM module_bindings")}
    module_symbols = [sid for sid, info in syms.items() if info[2] == "module" and langs[info[0]] == "python"]

    def qualified_module(fid, receiver, owner=None):
        head, dot, rest = receiver.partition(".")
        current, module = owner, None
        while current is not None:
            module = scoped_modules.get((fid, current, head))
            if module:
                break
            current = syms[current][3]
        if module is None and (fid, head) in bindings:
            original, parent = bindings[(fid, head)]
            candidate = parent + ("" if parent.endswith(".") else ".") + original
            if import_match(fid, module_symbols, original, candidate):
                module = candidate
        return module + ("." + rest if dot else "") if module else None

    upd = []
    for rowid, sid, b in db.execute("SELECT rowid,symbol_id,base FROM bases").fetchall():
        ks, _ = class_targets(file_of[sid], b)
        ks = [k for k in ks if k != sid]
        bases[sid].extend(ks)
        upd.append((ks[0] if ks else None, rowid))
    db.executemany("UPDATE bases SET base_id=? WHERE rowid=?", upd)
    out = []
    rows = db.execute("SELECT id,file_id,caller_id,name,recv,recv_name FROM calls WHERE dynamic=0 AND name!=''").fetchall()
    for cid, fid, caller, name, recv, rname in rows:
        targets, conf = [], None
        if langs[fid] == 'sql':continue
        if langs[fid] == 'swift':
            out.extend((caller,target,cid,'high') for target in swift.targets(fid,caller,name,rname))
            continue
        if langs[fid] == 'java':
            out.extend((caller, target, cid, 'high') for target in java.targets(fid, caller, name, rname))
            continue
        if langs[fid] == 'dart':
            out.extend((caller, target, cid, 'high') for target in dart.targets(fid, caller, name, rname))
            continue
        if langs[fid] == 'go':
            targets = go.targets(fid, caller, name, rname, recv.endswith('_generic')) if recv.startswith(('go_function','go_selector')) else []
            out.extend((caller, target, cid, 'high') for target in targets)
            continue
        if recv in ('loaded_medium', 'loaded_low') and '#' in rname and rname.rpartition('#')[2].isidentifier():
            path, _, owner = rname.rpartition('#')
            classes = [c for c in by_name.get(owner, []) if paths[file_of[c]] == path and syms[c][2] == 'class'
                       and syms[syms[c][3]][2] == 'module']
            targets = [m for k in classes[:1] for m in find_method(k, name)]  # not a class: no edge, never a guess
            conf = recv.removeprefix('loaded_')
        elif recv in ('loaded_medium', 'loaded_low'):
            targets = [c for c in by_name.get(name, []) if paths[file_of[c]] == rname
                       and syms[c][2] in ('function', 'class') and syms[syms[c][3]][2] == 'module']
            # A loaded class call is a constructor call: keep the class and add its __init__, as for plain calls.
            targets += [init for t in targets if syms[t][2] == 'class' for init in find_method(t, '__init__')[:1]]
            conf = recv.removeprefix('loaded_')
        elif recv == "builtin":
            continue
        elif recv in ("module", "jsx", "scope"):  # JS/TS module object, default export, JSX element, hidden import
            targets, conf = js.call(fid, caller, name, recv, rname)
        elif recv in ("self", "parent"):
            k = enclosing_class(caller)
            if k is not None:
                if recv == "parent":
                    targets = next((r for b in bases.get(k, []) for r in [find_method(b, name)] if r), [])
                else:
                    targets = find_method(k, name)
                conf = "high"
        elif recv in ("class", "new") and not (langs[fid] == "python" and qualified_module(fid, rname, caller)):
            ks, conf = class_targets(fid, rname)
            for k in ks[:5]:
                m = find_method(k, name)
                targets.extend(m if m else ([k] if recv == "new" else []))
        elif recv == 'none' and langs[fid] == 'php' and '\\' not in rname and cid in php_function_calls:
            targets = php_functions.get(php_function_calls[cid], [])
            conf = 'high' if len(targets) == 1 else 'low'
        elif recv == "none":
            binding = bindings.get((fid, name))
            others = [b for b in effective(fid, name, occurrences.get((fid, name), [])) if b != binding] \
                if binding and langs[fid] == "python" else []
            name = binding[0] if binding else name
            cands = [c for c in by_name.get(name, []) if syms[c][2] in ("function", "class") and fam(langs[file_of[c]]) == fam(langs[fid])]
            local = [c for c in cands if file_of[c] == fid]
            if langs[fid] != "php":
                scope = caller
                local = []
                while scope is not None:
                    local = [c for c in cands if file_of[c] == fid and syms[c][3] == scope]
                    if local:
                        break
                    scope = syms[scope][3]
                cands = [c for c in cands if syms[syms[c][3]][2] == "module"]
            fallback, alternatives = [], []
            if binding:
                named = cands
                cands = import_match(fid, cands, name, binding[1])
                local = []
                for original, module in others:  # try/except ImportError: every arm is a possible target
                    pool = [c for c in by_name.get(original, []) if syms[c][2] in ("function", "class")
                            and langs[file_of[c]] == "python" and syms[syms[c][3]][2] == "module"]
                    alternatives += [c for c in import_match(fid, pool, original, module) if c not in cands + alternatives]
                if not cands and not alternatives and langs[fid] == "python" and any(
                        any(file_of[c] == owner and syms[syms[c][3]][2] == "module" for c in by_name.get("__getattr__", []))
                        for owner in module_files(fid, binding[1])):
                    # the project module serves names through a module-level __getattr__ (PEP 562): keep the name hypothesis, never above low
                    fallback = named
            if local:
                targets, conf = local[:1], "high"
            elif cands:
                im = import_match(fid, cands, name)
                if im:
                    targets, conf = im[:3], "high" if len(im) == 1 else "medium"
                elif len(cands) == 1:
                    targets, conf = cands, "medium"
                elif len(cands) <= 5:
                    star = [c for module, imported in imports.get(fid, []) if imported == "*"
                            for c in import_match(fid, cands, name, module)] if langs[fid] == "python" else []
                    targets, conf = (star[:3], "medium") if star else (cands, "low")
                if alternatives:
                    targets, conf = targets + alternatives[:3], "medium"
            elif alternatives:
                targets, conf = alternatives[:3], "medium"
            elif 0 < len(fallback) <= 5:
                targets, conf = fallback, "low"
            # python: chiamata a classe = costruttore
            targets = [(find_method(t, "__init__") or [t])[0] if syms[t][2] == "class" and paths[file_of[t]].endswith(".py")
                       else t for t in targets]
        elif recv == "typed":
            ks, conf = class_targets(fid, rname)
            for k in ks:
                m = find_method(k, name)
                if m:
                    targets.extend(m)
                    if syms[syms[m[0]][3]][2] in ("interface", "trait") or syms[k][2] == "interface":
                        targets.extend(impls(k, name))  # chiamata via interfaccia: anche le implementazioni
                else:  # interfaccia/astratta: le implementazioni nelle sottoclassi
                    targets.extend(impls(k, name))
                    conf = "medium" if targets else conf
            if not ks:  # tipo esterno al progetto (vendor/stdlib): nessun arco, è corretto
                targets = []
        elif langs[fid] == "python" and recv != "unknown_obj" and qualified_module(fid, rname, caller):
            module = qualified_module(fid, rname, caller)
            cands = [c for c in by_name.get(name, []) if syms[c][2] in ("function", "class") and langs[file_of[c]] == "python"]
            targets = import_match(fid, cands, name, module)
            conf = "high" if len(targets) == 1 else "low"
            if not targets and "." in module:  # module.Class.method()
                owner_module, _, class_name = module.rpartition(".")
                classes = import_match(fid, [c for c in classes_by_name.get(class_name, []) if langs[file_of[c]] == "python"],
                                       class_name, owner_module)
                targets = [m for k in classes[:1] for m in find_method(k, name)]
                conf = "high" if len(classes) == 1 else "low"
            targets = [(find_method(t, "__init__") or [t])[0] if syms[t][2] == "class" else t for t in targets]
        else:  # unknown obj.method(): name coincidence is only a hypothesis
            cands = [c for c in by_name.get(name, []) if syms[c][2] == "method" and fam(langs[file_of[c]]) == fam(langs[fid])]
            if len(cands) == 1:
                targets, conf = cands, "low"
            elif 1 < len(cands) <= 4:
                targets, conf = cands, "low"
        lf = fam(langs[fid])
        targets = [t for t in targets if fam(langs[file_of[t]]) == lf]  # mai archi tra linguaggi diversi
        for t in targets:
            out.append((caller, t, cid, conf))
    db.executemany("INSERT INTO edges VALUES(?,?,?,?)", out)
    db.execute("UPDATE symbol_refs SET target_id=NULL")
    for reference_id, fid, owner, name, receiver, ref_kind in db.execute("SELECT id,file_id,owner_id,name,receiver,kind FROM symbol_refs").fetchall():
        targets = []
        if langs[fid] == 'sql':continue
        elif langs[fid] == 'swift':
            targets = swift.targets(fid,owner,name,receiver)
        elif langs[fid] == 'java':
            targets = java.targets(fid, owner, name, receiver)
        elif langs[fid] == 'dart':
            targets = dart.targets(fid, owner, name, receiver)
        elif langs[fid] == 'go':
            targets = go.targets(fid, owner, name, receiver)
        elif langs[fid] == 'php' and reference_id in blocked_php_refs:
            continue
        elif langs[fid] == "php" and ref_kind in ("callback", "callable") and not receiver:
            line = db.execute("SELECT line FROM symbol_refs WHERE id=?", (reference_id,)).fetchone()[0]
            targets = php_names.function_targets(fid, line, name, literal=ref_kind == 'callback')
        elif receiver in ("self", "cls"):
            targets = find_method(enclosing_class(owner), name, reference=langs[fid] == 'php')
        elif fam(langs[fid]) == "js" and receiver.partition(":")[0] in ("ns", "default", "dyn", "required"):
            targets = js.property_reference(fid, name, receiver)
        elif receiver and langs[fid] == 'php':
            line = db.execute('SELECT line FROM symbol_refs WHERE id=?', (reference_id,)).fetchone()[0]
            for cls in php_names.class_targets(fid, line, receiver):
                targets.extend(find_method(cls, name, reference=True))
        elif receiver:
            module = qualified_module(fid, receiver, owner)
            if module:
                candidates = [sid for sid in by_name.get(name, []) if langs[file_of[sid]] == "python" and syms[sid][2] in ("function", "class")]
                targets = import_match(fid, candidates, name, module)
        else:
            binding = bindings.get((fid, name))
            if binding:
                original, module = binding
                candidates = [sid for sid in by_name.get(original, []) if fam(langs[file_of[sid]]) == fam(langs[fid])]
                targets = import_match(fid, candidates, original, module)
            else:
                scope = owner
                while scope is not None:
                    targets = [sid for sid in by_name.get(name, []) if file_of[sid] == fid and syms[sid][3] == scope]
                    if targets:
                        break
                    scope = syms[scope][3]
        if len(targets) == 1:
            db.execute("UPDATE symbol_refs SET target_id=? WHERE id=?", (targets[0], reference_id))
    return len(out)


# ----------------------------------------------------------------- query helpers
CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def fmt(db, sid, extra=""):
    r = db.execute("SELECT s.kind,s.qname,f.path,s.line,s.end_line,s.sig FROM symbols s JOIN files f ON f.id=s.file_id "
                   "WHERE s.id=?", (sid,)).fetchone()
    if not r:
        return f"?{sid}"
    kind, q, path, l1, l2, sig = r
    if kind == "module":
        return f"{path} [module]{extra}"
    return f"{path}:{l1}-{l2} {kind} {q}{extra}"


def lookup(db, spec, kinds=None):
    """spec: nome | Classe.metodo | path/file.php:nome | path:linea"""
    spec = spec.replace("::", ".").replace("->", ".")
    m = re.match(r"^(.+?):(\d+)$", spec)
    if m:
        r = db.execute("SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.path LIKE ? AND s.line<=? AND "
                       "s.end_line>=? AND s.kind!='module' ORDER BY (s.end_line-s.line) LIMIT 1",
                       ("%" + m.group(1), int(m.group(2)), int(m.group(2)))).fetchall()
        return [x[0] for x in r]
    path = None
    if ":" in spec and not spec.startswith(":"):
        path, spec = spec.rsplit(":", 1)
    name = spec.replace("::", ".").replace("->", ".")
    if "." in name:
        q = db.execute("SELECT s.id,f.path FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.qname=? OR s.qname LIKE ?",
                       (name, "%." + name)).fetchall()
    else:
        q = db.execute("SELECT s.id,f.path FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.name=? AND s.kind!='module'",
                       (name,)).fetchall()
    if path:
        q = [x for x in q if path in x[1]]
    return [x[0] for x in q]


def resolve_one(db, spec):
    ids = lookup(db, spec)
    if not ids:
        sys.exit(f"No symbol '{spec}'. Try: cg.py find {spec}")
    if len(ids) > 1:
        print(f"'{spec}' is ambiguous ({len(ids)}). Specify path:name or Class.method:")
        for i in ids[:20]:
            print("  " + fmt(db, i))
        sys.exit(2)
    return ids[0]


def neighbors(db, sid, direction, minconf, references=None):
    """Callers or callees by call edge. With a set as references (direction "in"), symbols that only
    reference sid (callback argument, registering decorator) are added at medium and collected in the set."""
    col, other = ("callee_id", "caller_id") if direction == "in" else ("caller_id", "callee_id")
    rows = db.execute(f"SELECT e.{other},e.conf,c.line,f.path FROM edges e JOIN calls c ON c.id=e.call_id "
                      f"JOIN files f ON f.id=c.file_id WHERE e.{col}=?", (sid,)).fetchall()
    best = {}
    for o, conf, line, path in rows:
        if CONF_RANK[conf] < CONF_RANK[minconf] or o == sid:
            continue
        if o not in best or CONF_RANK[conf] > CONF_RANK[best[o][0]]:
            best[o] = (conf, f"{path}:{line}")
    if references is not None and direction == "in" and CONF_RANK["medium"] >= CONF_RANK[minconf]:
        for o, line, path in db.execute("SELECT r.owner_id,r.line,f.path FROM symbol_refs r JOIN files f ON f.id=r.file_id "
                                        "WHERE r.target_id=? ORDER BY f.path,r.line", (sid,)):
            if o is not None and o != sid and o not in best:
                best[o] = ("medium", f"{path}:{line}")
                references.add((o, sid))
    return best


def dynamic_in(db, sid):
    """Chiamate dinamiche presenti nel corpo del simbolo."""
    return db.execute("SELECT line FROM calls WHERE caller_id=? AND dynamic=1", (sid,)).fetchall()


# ----------------------------------------------------------------- comandi
def cmd_find(a):
    db = connect(a.root)
    pat = a.pattern.replace("*", "%")
    if "%" not in pat:
        pat = f"%{pat}%"
    q = "SELECT id FROM symbols WHERE kind!='module' AND (name LIKE ? OR qname LIKE ?)"
    args = [pat, pat]
    if a.kind:
        q += " AND kind=?"
        args.append(a.kind)
    rows = db.execute(q + " ORDER BY length(name) LIMIT ?", args + [a.limit]).fetchall()
    for (i,) in rows:
        print(fmt(db, i))
    if len(rows) == a.limit:
        print(f"... (limit {a.limit}, use --limit)")


def cmd_show(a):
    db = connect(a.root)
    sid = resolve_one(db, a.symbol)
    print(fmt(db, sid))
    sig = db.execute("SELECT sig,pmin,pmax,visibility,exported FROM symbols WHERE id=?", (sid,)).fetchone()
    print(f"  signature: {sig[0]}")
    meta = [f"params {sig[1]}..{'∞' if sig[2] is None else sig[2]}" if sig[1] is not None else "",
            sig[3] or "", "exported" if sig[4] else ""]
    if any(meta):
        print("  " + " · ".join(m for m in meta if m))
    kids = db.execute("SELECT id FROM symbols WHERE parent_id=? ORDER BY line", (sid,)).fetchall()
    if kids:
        print(f"  members ({len(kids)}):")
        for (k,) in kids[:60]:
            print("    " + fmt(db, k))
    b = db.execute("SELECT base FROM bases WHERE symbol_id=?", (sid,)).fetchall()
    if b:
        print('  extends/implements: ' + ", ".join(x[0] for x in b))
    ins, outs = neighbors(db, sid, "in", "low"), neighbors(db, sid, "out", "low")
    print(f"  called by {len(ins)} · calls {len(outs)} · dynamic calls in body {len(dynamic_in(db, sid))}")


def cmd_edges(a, direction):
    db = connect(a.root)
    sid = resolve_one(db, a.symbol)
    print(fmt(db, sid))
    nb = neighbors(db, sid, direction, a.min_conf)
    for o, (conf, where) in sorted(nb.items(), key=lambda x: (-CONF_RANK[x[1][0]], x[1][1])):
        print(f"  {'←' if direction == 'in' else '→'} {fmt(db, o)}  [{conf}] @{where}")
    if not nb:
        print('  (none' + (' — possible entrypoint, callback or dynamic call)' if direction == "in" else ")"))
    if direction == "out":
        unres = db.execute("SELECT c.name,c.recv,c.recv_name,c.line,c.dynamic FROM calls c WHERE c.caller_id=? AND c.id NOT IN "
                           "(SELECT call_id FROM edges)", (sid,)).fetchall()
        dyn = [u for u in unres if u[4]]
        ext = sorted({(u[2] + "." if u[2] and u[1] in ('obj', 'class', 'new') else "") + u[0] for u in unres if not u[4]})
        if ext:
            print(f"  external/unresolved ({len(ext)}): " + ", ".join(ext[:40]))
        if dyn:
            print('  ⚠ dynamic calls at lines: ' + ", ".join(str(u[3]) for u in dyn))


def impact_traversal(db, roots, depth, min_conf, max_nodes=2000, references=None):
    """Impact follows call edges and references; references (optional set) receives the (referrer, symbol) pairs."""
    references = set() if references is None else references
    seen = {r: 0 for r in roots}
    via = {}
    dq = deque(roots)
    truncated = False
    while dq:
        symbol = dq.popleft()
        if seen[symbol] >= depth:
            continue
        for caller, (confidence, where) in neighbors(db, symbol, "in", min_conf, references).items():
            if caller not in seen:
                if len(seen) >= max_nodes:
                    truncated = True
                    continue
                seen[caller] = seen[symbol] + 1
                via[caller] = (symbol, confidence, where)
                dq.append(caller)
    return seen, via, truncated


def cmd_impact(a):
    if getattr(a, 'json', False):
        import runpy
        from types import SimpleNamespace
        engine=sys.modules.get(__name__) or SimpleNamespace(**globals())
        ProjectData=runpy.run_path(str(Path(__file__).with_name('project_data.py')))['ProjectData']
        with ProjectData(engine,a.root) as project:
            if a.file:
                roots=[row[0] for row in project.db.execute('SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.path=? AND s.kind!="module"',(a.target,))]
                value=dict(format='codegraph-impact-file/1',target=a.target,results=[project.impact(sid,a.min_conf,a.depth,compact=True,limit=a.limit) for sid in roots])
            else:value=project.impact(resolve_one(project.db,a.target),a.min_conf,a.depth,compact=True,limit=a.limit)
            js_dynamic_hint(project.db, value, roots if a.file else [resolve_one(project.db, a.target)])
            print(json.dumps(value,ensure_ascii=False,sort_keys=True))
        return
    db = connect(a.root)
    roots = []
    if a.file:
        f = db.execute("SELECT id FROM files WHERE path LIKE ?", ("%" + a.target,)).fetchall()
        if not f:
            sys.exit(f"file not indexed: {a.target}")
        roots = [r[0] for r in db.execute(f"SELECT id FROM symbols WHERE file_id IN ({','.join('?' * len(f))}) AND kind!='module'",
                                          [x[0] for x in f])]
    else:
        roots = [resolve_one(db, a.target)]
    references = set()
    seen, via, truncated = impact_traversal(db, roots, a.depth, a.min_conf, references=references)
    if truncated:
        print('Partial result: limit of 2000 symbols reached; reduce depth.')
    hit = [s for s in seen if s not in roots]
    low_extra = 0
    if a.min_conf != "low":
        low_extra = sum(1 for r in roots for o, (c, _) in neighbors(db, r, "in", "low").items() if c == "low" and o not in seen)
    files = defaultdict(list)
    for s in hit:
        p = db.execute("SELECT f.path FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.id=?", (s,)).fetchone()[0]
        files[p].append(s)
    tests = [p for p in files if re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|(_test|\.test|\.spec|Test)\.\w+$", p)]
    print(f"IMPACT of {a.target}: {len(hit)} symbols in {len(files)} files (depth ≤{a.depth}, conf ≥{a.min_conf})")
    for d in range(1, a.depth + 1):
        lvl = [s for s in hit if seen[s] == d]
        if not lvl:
            continue
        print(f"-- level {d} ({len(lvl)})")
        for s in sorted(lvl, key=lambda x: fmt(db, x))[:a.limit]:
            src, conf, where = via[s]
            print(f"  {fmt(db, s)}  [{conf}] {'references' if (s, src) in references else 'calls'} @{where}")
        if len(lvl) > a.limit:
            print(f"  ... more {len(lvl) - a.limit}")
    if low_extra:
        print(f"+ {low_extra} direct low-confidence callers excluded (ambiguous name): see --min-conf low")
    if tests:
        print('affected tests: ' + ", ".join(sorted(tests)[:30]))
    dyn = db.execute("SELECT COUNT(*) FROM calls WHERE dynamic=1").fetchone()[0]
    if dyn:
        print(f"⚠ {dyn} dynamic calls in the project are not in the graph: check with rg -n -w <name> before changing signatures.")


def cmd_path(a):
    db = connect(a.root)
    s, t = resolve_one(db, a.src), resolve_one(db, a.dst)
    prev = {s: None}
    dq = deque([s])
    while dq:
        x = dq.popleft()
        if x == t:
            break
        for o, (conf, where) in neighbors(db, x, "out", a.min_conf).items():
            if o not in prev:
                prev[o] = (x, conf, where)
                dq.append(o)
    if t not in prev:
        print('no call path found (try --min-conf low or check dynamic calls)')
        return
    chain, x = [], t
    while prev[x] is not None:
        chain.append((x, prev[x]))
        x = prev[x][0]
    print(fmt(db, s))
    for node, (_, conf, where) in reversed(chain):
        print(f"  → {fmt(db, node)}  [{conf}] @{where}")


def read_lines(root, path, l1, l2):
    try:
        with local_path(root, path).open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return "".join(f"{i}: {lines[i - 1]}" for i in range(l1, min(l2, len(lines)) + 1))
    except (OSError, ValueError):
        return ""


def cmd_flows(a):
    import runpy
    from types import SimpleNamespace
    engine=sys.modules.get(__name__) or SimpleNamespace(**globals())
    ProjectData=runpy.run_path(str(Path(__file__).with_name('project_data.py')))['ProjectData']
    module=runpy.run_path(str(Path(__file__).with_name('functional_map.py')))
    try:
        with ProjectData(engine,a.root) as project:
            value=module['build'](project,a.query,a.limit,a.min_conf,a.max_chars)
        print(module['dumps'](value))
    except (ValueError,OSError) as error:
        sys.exit(str(error))


def cmd_features(a):
    import runpy
    from types import SimpleNamespace
    engine=sys.modules.get(__name__) or SimpleNamespace(**globals())
    ProjectData=runpy.run_path(str(Path(__file__).with_name('project_data.py')))['ProjectData']
    features=runpy.run_path(str(Path(__file__).with_name('features.py')))
    try:
        with ProjectData(engine,a.root) as project:
            if a.cmd=='features':
                value=project.feature_catalog(a.query)
                if a.json:print(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')))
                else:
                    for item in value['items']:print(f"{item['id']} · {item['title']} [{item['provenance']}; {item['source_status']}]")
                    for issue in value['issues']:print(issue['message'])
                return
            if a.cmd=='source':
                window=runpy.run_path(str(Path(__file__).with_name('source_windows.py')))['read_window']
                print(json.dumps(window(project,a.path,a.line,a.column,a.max_chars),ensure_ascii=False,sort_keys=True,separators=(',',':')))
                return
            target='symbol:'+str(resolve_one(project.db,a.symbol)) if a.symbol else a.feature
            if not target:raise ValueError('Specify a feature or --symbol.')
            packet,_=features['prepare'](project,target,a.intent,a.max_chars,a.min_conf,a.depth,verification_symbols=a.verify_symbol)
            print(json.dumps(packet,ensure_ascii=False,sort_keys=True,separators=(',',':')))
    except (ValueError,OSError) as error:
        sys.exit(str(error))


def cmd_analysis(a):
    import runpy
    from types import SimpleNamespace
    engine=sys.modules.get(__name__) or SimpleNamespace(**globals())
    ProjectData=runpy.run_path(str(Path(__file__).with_name('project_data.py')))['ProjectData']
    change=runpy.run_path(str(Path(__file__).with_name('change_analysis.py')))
    a._summary_callers={} if a.cmd!='architecture' and not getattr(a,'json',False) and not getattr(a,'_mcp_json',False) else None
    if a.cmd=='review':
        result=change['review'](engine,a)
    else:
        with ProjectData(engine,a.root) as project:
            if a.cmd=='architecture':result=project.architecture()
            else:
                roots=[row[0] for row in project.db.execute('SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.path=? AND s.kind!="module"',(a.target,))] if a.file else [resolve_one(project.db,a.target)]
                result=change['plan'](project,roots,a.min_conf,a.depth)
                result['content_revision']=project.meta['content_revision']
                if a._summary_callers is not None:a._summary_callers.update(change['summary_callers'](project,result))
    if a.cmd=='review' and getattr(a,'summary',False) and result.get('format')=='codegraph-review-summary/1':
        print(json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':')))  # compact, as --max-chars measures it
    elif getattr(a,'json',False):print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    elif getattr(a,'_mcp_json',False) or a.cmd=='architecture':print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2))
    else:
        summaries=runpy.run_path(str(Path(__file__).with_name('summaries.py')))
        print(summaries['review'](result,caller_names=a._summary_callers) if a.cmd=='review' else summaries['verify'](result,a.target,caller_names=a._summary_callers),end='')


def agent_module():
    import runpy
    return runpy.run_path(str(Path(__file__).with_name('agent_queries.py')))


def cmd_context(a):
    from types import SimpleNamespace
    agent_module()['cmd_context'](sys.modules.get(__name__) or SimpleNamespace(**globals()), a)


def sccs(nodes, adj):
    index, low, onstack, st, res, counter = {}, {}, set(), [], [], [0]
    for v0 in nodes:
        if v0 in index:
            continue
        work = [(v0, iter(adj.get(v0, ())))]
        index[v0] = low[v0] = counter[0]; counter[0] += 1; st.append(v0); onstack.add(v0)
        while work:
            v, it = work[-1]
            nxt = next(it, None)
            if nxt is not None:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]; counter[0] += 1; st.append(nxt); onstack.add(nxt)
                    work.append((nxt, iter(adj.get(nxt, ()))))
                elif nxt in onstack:
                    low[v] = min(low[v], index[nxt])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = st.pop(); onstack.discard(w); comp.append(w)
                        if w == v:
                            break
                    if len(comp) > 1:
                        res.append(comp)
    return res


TEXT_EXT = {".twig", ".tpl", ".phtml", ".html", ".htm", ".vue", ".svelte", ".json", ".yml", ".yaml", ".xml", ".ini",
            ".neon", ".env", ".blade.php", ".md", ".sql", ".sh", ".conf", ".toml", ".cfg"}


def word_counts(root, cache={}):
    """Un solo passaggio su codice + template/config: quante volte compare ogni identificatore come parola."""
    if root in cache:
        return cache[root]
    from collections import Counter
    cnt = Counter()
    files = checkout_files(root)
    start_names, start_prefixes = catalog_for(root).start_files()
    word = re.compile(rb"[A-Za-z_$][\w$]*")
    for rel in files:
        parts = rel.split("/")
        if any(x in SKIP_DIRS for x in parts[:-1]):
            continue
        ext = os.path.splitext(rel)[1]
        base = parts[-1]
        if ext not in LANG_BY_EXT and ext not in TEXT_EXT and base not in start_names and not base.startswith(start_prefixes):
            continue
        try:
            full = local_path(root, rel)
            if full.stat().st_size > MAX_BYTES:
                continue
            cnt.update(m.decode() for m in word.findall(full.read_bytes()))
        except (OSError, ValueError):
            continue
    cache[root] = cnt
    return cnt


def rg_count(root, name):
    return word_counts(root).get(name, 0)


def defined_in_descendant(db, cls, name):
    st, seen = [cls], set()
    while st:
        c = st.pop()
        if c in seen or len(seen) > 300:
            continue
        seen.add(c)
        for (sub,) in db.execute("SELECT symbol_id FROM bases WHERE base_id=?", (c,)).fetchall():
            if db.execute("SELECT 1 FROM symbols WHERE parent_id=? AND lower(name)=lower(?)", (sub, name)).fetchone():
                return True
            st.append(sub)
    return False


def assigned_src(root, path, cache={}):
    key = (os.path.realpath(root), path)
    if key not in cache:
        try:
            cache[key] = local_path(root, path).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            cache[key] = ""
    return cache[key]


def assigned_attr(root, path, name):
    return re.search(r"(this|self)\.\s*" + re.escape(name) + r"\s*=(?!=)|^\s*(public|private|protected|readonly|static|\s)*"
                     + re.escape(name) + r"\s*[?!]?\s*[:=]", assigned_src(root, path), re.M) is not None


def ancestry(db, cls):
    """Classi antenate (incluse) e se la catena tocca una base esterna al progetto (base_id NULL)."""
    seen, external, st = set(), False, [cls]
    while st:
        c = st.pop()
        if c in seen or len(seen) > 50:
            continue
        seen.add(c)
        for (bid,) in db.execute("SELECT base_id FROM bases WHERE symbol_id=?", (c,)).fetchall():
            if bid is None:
                external = True
            else:
                st.append(bid)
    return seen, external


def is_override(db, cls, name):
    """True se un antenato definisce lo stesso metodo o se la catena esce dal progetto (framework/vendor)."""
    anc, external = ancestry(db, cls)
    anc.discard(cls)
    if external:
        return True
    return any(db.execute("SELECT 1 FROM symbols WHERE parent_id=? AND name=?", (k, name)).fetchone() for k in anc)


def dynamic_reach(db):
    """Files reached by getattr(module, ...) and classes reached by $obj->$m(), from any file."""
    files, classes = set(), set()
    stems = {os.path.splitext(path)[0]: fid for fid, path in db.execute("SELECT id,path FROM files WHERE lang='python'")}
    rows = db.execute("SELECT c.file_id,c.recv_name,f.lang,f.path FROM calls c JOIN files f ON f.id=c.file_id "
                      "WHERE c.dynamic=1 AND c.recv_name!=''").fetchall()
    if any(row[2] == 'php' for row in rows):
        php_names = PhpNames.from_db(db)
    for fid, receiver, lang, path in rows:
        if lang == "php":
            for (line,) in db.execute("SELECT line FROM calls WHERE file_id=? AND recv_name=? AND dynamic=1 AND recv IN ('obj','typed','class')", (fid, receiver)):
                for alternative in receiver.split('|'):
                    classes.update(php_names.class_targets(fid, line, alternative))
                    qualified = php_names.qualified(fid, line, alternative)
                    # Lost namespace precision must not turn dynamic reach into a dead-code claim.
                    # This fallback protects candidates only; it never creates reference/call edges.
                    for name in qualified or [alternative]:
                        classes.update(sid for sid in php_names.class_names[last_seg(name).lower()]
                                       if not qualified or sid in php_names.ambiguous_classes)
            continue
        if lang != "python":
            continue
        head = receiver.split(".", 1)[0]
        modules = [m for (m,) in db.execute("SELECT module FROM imports WHERE file_id=? AND name=?", (fid, "__module__:" + head))]
        modules += [m + ("" if m.endswith(".") else ".") + o for m, o in
                    db.execute("SELECT module,original FROM import_bindings WHERE file_id=? AND local=?", (fid, head))]
        for module in modules:
            dots = len(module) - len(module.lstrip("."))
            parent = os.path.dirname(path)
            for _ in range(max(0, dots - 1)):
                parent = os.path.dirname(parent)
            target = module[dots:].replace(".", "/")
            target = os.path.normpath(os.path.join(parent, target)) if dots else target
            files.update(f for stem, f in stems.items() if stem in (target, target + "/__init__")
                         or (not dots and (stem.endswith("/" + target) or stem.endswith("/" + target + "/__init__"))))
    return files, classes


def dead_candidates(db, root, verify=True):
    reached_files, reached_classes = dynamic_reach(db)
    rows = db.execute("""SELECT s.id,s.name,s.kind,s.visibility,s.exported,f.path,f.lang FROM symbols s JOIN files f ON f.id=s.file_id
        WHERE s.kind IN ('function','method') AND s.id NOT IN (SELECT callee_id FROM edges)
        AND s.id NOT IN (SELECT target_id FROM symbol_refs WHERE target_id IS NOT NULL)""").fetchall()
    out = []
    for sid, name, kind, vis, exp, path, language in rows:
        if language == 'go' and kind == 'function' and name == 'init':
            continue
        if MAGIC.match(name) or name.startswith("test") or re.search(r"(^|/)(tests?|__tests__|spec)/", path) or exp:
            continue
        if kind == "method":
            # override di un metodo di classe base/interfaccia: probabile callback del framework
            pid = db.execute("SELECT parent_id FROM symbols WHERE id=?", (sid,)).fetchone()[0]
            if vis != "private" and is_override(db, pid, name):
                continue
        n = rg_count(root, name) if verify else -1
        defs = db.execute("SELECT COUNT(*) FROM symbols WHERE name=?", (name,)).fetchone()[0]
        fid = db.execute("SELECT file_id FROM symbols WHERE id=?", (sid,)).fetchone()[0]
        dyn_here = db.execute("SELECT 1 FROM calls WHERE file_id=? AND dynamic=1 LIMIT 1", (fid,)).fetchone() or fid in reached_files
        if kind == "method" and not dyn_here and reached_classes:
            if db.execute("SELECT lang FROM files WHERE id=?", (fid,)).fetchone()[0] == 'php':
                dyn_here = pid in reached_classes
            else:
                owner = db.execute("SELECT p.name FROM symbols s JOIN symbols p ON p.id=s.parent_id WHERE s.id=?", (sid,)).fetchone()
                dyn_here = bool(owner and owner[0].lower() in reached_classes)  # $obj->$m() on this class elsewhere
        partial = set(json.loads((db.execute("SELECT v FROM meta WHERE k='partial_paths'").fetchone() or ['[]'])[0]))
        if path in partial:
            verdict = 'AMBIGUOUS'
        elif n == -1:
            verdict = "UNVERIFIED"
        elif dyn_here:
            verdict = "AMBIGUOUS"  # stesso file usa chiamate dinamiche ($this->$m(), getattr, call_user_func)
        elif n <= defs:
            verdict = "VERIFIED_DEAD" if vis in ("private", "protected") or kind == "function" else "LIKELY_DEAD"
        else:
            verdict = "AMBIGUOUS"
        out.append((verdict, sid, n, defs))
    return out


def cmd_dead(a):
    db = connect(a.root)
    res = dead_candidates(db, a.root)
    order = {"VERIFIED_DEAD": 0, "LIKELY_DEAD": 1, "AMBIGUOUS": 2, "UNVERIFIED": 3}
    res.sort(key=lambda x: (order[x[0]], fmt(db, x[1])))
    shown = [r for r in res if a.all or r[0] in ("VERIFIED_DEAD", "LIKELY_DEAD")]
    for verdict, sid, n, defs in shown[:a.limit]:
        print(f"{verdict:13} {fmt(db, sid)}  (text occurrences {n}, definitions {defs})")
    print(f"-- {len(shown)} shown out of {len(res)} without callers in the graph. AMBIGUOUS = name found as text elsewhere "
          f"(strings, routes, DI): read before removing. Always test before removing.")


def cmd_check(a):
    import runpy
    from types import SimpleNamespace
    engine = sys.modules.get(__name__) or SimpleNamespace(**globals())
    ProjectData = runpy.run_path(str(Path(__file__).with_name('project_data.py')))['ProjectData']
    check = runpy.run_path(str(Path(__file__).with_name('checks.py')))['findings']
    with ProjectData(engine, a.root) as project:
        all_items = check(project)
        diagnostics=[item for item in all_items if item['category'] in ('analysis_limit','diagnostic')]
        items=all_items if getattr(a,'include_diagnostics',False) else [item for item in all_items if item['category'] not in ('analysis_limit','diagnostic')]
        result = dict(format='codegraph-findings/1', engine_version=VERSION, content_revision=project.meta['content_revision'],
                      findings=items[:a.limit], total=len(items), omitted=max(0,len(items)-a.limit), diagnostics=dict(groups=len(diagnostics),occurrences=sum(item.get('occurrences',1) for item in diagnostics),included=getattr(a,'include_diagnostics',False),continuation='check --include-diagnostics'),
                      limitations=['Static candidates: confirm sources, bindings and runtime conditions.'])
        if getattr(a, 'json', False):
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            for item in items[:a.limit]:
                print(f"[{item['category']}] {item['rule_id']}: {item['file']}:{item['line']} {item['message']}")
            print(f"-- {len(items)} findings, {result['omitted']} omitted. These are static CANDIDATES, not bug probabilities. Diagnostics: {len(diagnostics)} groups; use --include-diagnostics.")


def cmd_map(a):
    db=connect(a.root)
    scope=getattr(a,'scope','all');minimum=getattr(a,'min_conf','medium')
    def accepted(path):
        test=bool(re.search(r'(^|/)(tests?|__tests__|spec)/|(_test|\.test|\.spec|Test)\.\w+$',path))
        return scope=='all' or (scope=='tests')==test
    files={row[0]:dict(path=row[1],loc=row[2]) for row in db.execute('SELECT id,path,loc FROM files') if accepted(row[1])}
    groups=defaultdict(lambda:dict(files=0,lines=0,definitions=0));gof={}
    for fid,item in files.items():
        parts=item['path'].split('/');group='/'.join(parts[:min(a.depth,len(parts)-1)]) or '.';gof[fid]=group
        groups[group]['files']+=1;groups[group]['lines']+=item['loc']
    for fid,count in db.execute('SELECT file_id,COUNT(*) FROM symbols WHERE kind!="module" GROUP BY file_id'):
        if fid in files:groups[gof[fid]]['definitions']+=count
    deps=defaultdict(set);hot=defaultdict(set);modules=defaultdict(set)
    symbols={row[0]:row for row in db.execute('SELECT id,file_id,qname,kind,line,end_line FROM symbols')}
    for caller,callee,conf in db.execute('SELECT caller_id,callee_id,conf FROM edges'):
        if CONF_RANK[conf]<CONF_RANK[minimum]:continue
        source,target=symbols[caller],symbols[callee]
        if source[1] not in files or target[1] not in files:continue
        hot[callee].add(caller);modules[callee].add(gof[source[1]])
        if gof[source[1]]!=gof[target[1]]:deps[(gof[source[1]],gof[target[1]])].add((caller,callee))
    hotspots=[dict(symbol=fmt(db,sid),distinct_callers=len(callers),caller_modules=len(modules[sid]),source_span_lines=symbols[sid][5]-symbols[sid][4]+1)
              for sid,callers in sorted(hot.items(),key=lambda item:(-len(item[1]),fmt(db,item[0])))[:a.limit]]
    result=dict(scope=scope,confidence=minimum,groups=dict(sorted(groups.items())),hotspots=hotspots,
                dependencies=[dict(source=pair[0],target=pair[1],distinct_links=len(links)) for pair,links in sorted(deps.items())],
                limitations=['source_span_lines measures lines, not cyclomatic complexity.'])
    if getattr(a,'json',False):print(json.dumps(result,ensure_ascii=False,sort_keys=True));return
    print(f'MAP · scope {scope} · conf ≥{minimum}')
    for name,item in sorted(groups.items(),key=lambda item:-item[1]['lines'])[:a.limit]:print(f"{name:40} {item['files']} files · {item['lines']} lines · {item['definitions']} definitions")
    print('MODULE DEPENDENCIES')
    for item in result['dependencies'][:a.limit]:print(f"{item['source']} → {item['target']} ({item['distinct_links']} distinct links)")
    print('HOTSPOTS · distinct callers, modules, symbol lines')
    for item in hotspots:print(f"{item['distinct_callers']} ← {item['symbol']} · {item['caller_modules']} modules · {item['source_span_lines']} lines")


def cmd_stats(a):
    db = connect(a.root)
    import runpy
    shared=runpy.run_path(str(Path(__file__).with_name('evidence.py')))['metrics'](db)
    if getattr(a,'json',False):
        print(json.dumps(dict(engine_version=VERSION,content_revision=dict(db.execute('SELECT k,v FROM meta')).get('content_revision'),metrics=shared),sort_keys=True));return
    q = lambda s: db.execute(s).fetchone()[0]
    ncalls = q("SELECT COUNT(*) FROM calls WHERE name!=''")
    res = q("SELECT COUNT(DISTINCT call_id) FROM edges")
    ver, at = q("SELECT v FROM meta WHERE k='version'"), q("SELECT v FROM meta WHERE k='indexed_at'")
    nsym = q("SELECT COUNT(*) FROM symbols WHERE kind!='module'")
    print(f"engine {VERSION} · index schema {ver} · indexed {at}")
    print(f"files {q('SELECT COUNT(*) FROM files')} · lines {q('SELECT SUM(loc) FROM files')} · "
          f"symbols {nsym} · calls {ncalls} · edges {q('SELECT COUNT(*) FROM edges')}")
    print(f"definitions {shared['definitions']} · nodes {shared['graph_nodes']} · resolved callsites {shared['resolved_callsites']} · distinct links {shared['distinct_links']}")
    for c, n in db.execute("SELECT conf,COUNT(*) FROM edges GROUP BY conf"):
        print(f"  edges {c}: {n}")
    print(f"resolved calls to project code: {res}/{ncalls} ({100 * res // max(ncalls, 1)}%) — the rest is "
          f"stdlib/vendor/external or ambiguous · dynamic: {q('SELECT COUNT(*) FROM calls WHERE dynamic=1')}")


def export_data(db, a, node_ids=None, focus_id=None):
    """Bounded call graph snapshot; the source database is opened read-only."""
    allowed = [conf for conf, rank in CONF_RANK.items() if rank >= CONF_RANK[a.min_conf]]
    conf_sql = ",".join("?" for _ in allowed)
    focus = focus_id if focus_id is not None else resolve_one(db, a.symbol) if a.symbol else None
    if focus is None:
        ids = set(node_ids) if node_ids is not None else {r[0] for r in db.execute("SELECT id FROM symbols ORDER BY id LIMIT ?", (a.max_nodes + 1,))}
    else:
        ids, frontier = {focus}, {focus}
        for _ in range(a.depth):
            found = set()
            batch_ids = sorted(frontier)
            for start in range(0, len(batch_ids), 250):
                batch = batch_ids[start:start + 250]
                marks = ",".join("?" for _ in batch)
                query = (f"SELECT DISTINCT caller_id,callee_id FROM edges WHERE conf IN ({conf_sql}) "
                         f"AND (caller_id IN ({marks}) OR callee_id IN ({marks}))")
                for src, dst in db.execute(query, allowed + batch + batch):
                    found.update((src, dst))
                    if len(ids | found) > a.max_nodes:
                        sys.exit(f"Graph exceeds --max-nodes {a.max_nodes}. Reduce --depth or raise the limit.")
            frontier = found - ids
            ids.update(found)
            if not frontier:
                break
    if len(ids) > a.max_nodes:
        sys.exit(f"Graph exceeds --max-nodes {a.max_nodes}: use --symbol NAME --depth 1 or raise the limit.")
    nodes, pairs, call_count = [], {}, 0
    ordered = sorted(ids)
    for start in range(0, len(ordered), 500):
        batch = ordered[start:start + 500]
        marks = ",".join("?" for _ in batch)
        rows = db.execute(f"""SELECT s.id,s.name,s.qname,s.kind,f.path,f.lang,s.line,s.end_line,s.sig
            FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.id IN ({marks}) ORDER BY s.id""", batch)
        for sid, name, qname, kind, path, language, line, end_line, signature in rows:
            nodes.append(dict(id=sid, name=name, label=qname, kind=kind, file=path, language=language,
                              line=line, end_line=end_line, signature=signature,
                              module=path.split("/")[0] if "/" in path else "."))
        rows = db.execute(f"""SELECT e.caller_id,e.callee_id,e.conf,c.id,c.line,f.path
            FROM edges e JOIN calls c ON c.id=e.call_id JOIN files f ON f.id=c.file_id
            WHERE e.caller_id IN ({marks}) AND e.conf IN ({conf_sql}) ORDER BY c.id,e.callee_id""", batch + allowed)
        for src, dst, confidence, call_id, line, path in rows:
            if dst not in ids:
                continue
            call_count += 1
            if call_count > a.max_edges:
                sys.exit(f"Graph exceeds --max-edges {a.max_edges}: narrow --symbol/--depth or raise the limit.")
            edge = pairs.setdefault((src, dst), dict(source=src, target=dst, confidence=confidence, calls={}))
            if CONF_RANK[confidence] > CONF_RANK[edge["confidence"]]:
                edge["confidence"] = confidence
            previous = edge["calls"].get(call_id)
            if previous is None or CONF_RANK[confidence] > CONF_RANK[previous["confidence"]]:
                edge["calls"][call_id] = dict(file=path, line=line, confidence=confidence)
    edges = []
    for edge in pairs.values():
        edge["calls"] = list(edge["calls"].values())
        edges.append(edge)
    meta = dict(db.execute("SELECT k,v FROM meta"))
    return dict(project=Path(a.root).resolve().name, engine=VERSION, indexed_at=meta.get("indexed_at", ""),
                focus=focus, min_conf=a.min_conf, nodes=nodes, edges=edges,
                total_nodes=db.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
                dynamic_calls=db.execute("SELECT COUNT(*) FROM calls WHERE dynamic=1").fetchone()[0])


def render_graph_html(data):
    template = (Path(__file__).resolve().parents[1] / "assets/explorer.html").read_text(encoding="utf-8")
    hashes = []
    for tag in ("style", "script"):
        code = re.search(rf"<{tag} data-cg-inline>(.*?)</{tag}>", template, re.S).group(1)
        hashes.append("'sha256-" + base64.b64encode(hashlib.sha256(code.encode()).digest()).decode() + "'")
    csp = (f"default-src 'none'; script-src {hashes[1]}; style-src {hashes[0]}; "
           "connect-src 'none'; base-uri 'none'; form-action 'none'")
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    for char, escaped in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e")):
        payload = payload.replace(char, escaped)
    page = template.replace("__CG_CSP__", csp).replace("__CG_DATA__", payload)
    return page


def cmd_export(a):
    root = Path(a.root).resolve()
    try:
        requested = Path(a.output)
        relative = requested.relative_to(root) if requested.is_absolute() else requested
        output = local_path(root, relative)
        db_path = local_path(root, f"{DB_DIR}/graph.db")
        for suffix in ("-journal", "-wal", "-shm"):
            local_path(root, f"{DB_DIR}/graph.db{suffix}")
        if output.suffix.lower() != ".html":
            sys.exit('Output must have the .html extension.')
        if output.exists() and not a.force:
            sys.exit(f"File already exists: {output}. Use --force to replace it.")
        if not db_path.is_file():
            sys.exit('No index: run cg --root REPO index first.')
        with contextlib.closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as db:
            version = db.execute("SELECT v FROM meta WHERE k='version'").fetchone()
            if version is None or version[0] != INDEX_VERSION:
                sys.exit('Incompatible index: run cg --root REPO index again.')
            db.execute("BEGIN")
            data = export_data(db, a)
        page = render_graph_html(data)
        output.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as handle:
                temp_path = Path(handle.name)
                handle.write(page)
            if a.force:
                os.replace(temp_path, output)
            else:
                os.link(temp_path, output)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    except (OSError, ValueError, sqlite3.Error) as e:
        sys.exit(f"Export failed: {e}")
    print(f"Browsable HTML: {output} ({len(data['nodes'])} nodes, {len(data['edges'])} links). Open it in the browser.")


def cmd_selftest(a):
    import tempfile, shutil
    d = tempfile.mkdtemp(prefix="cgtest")
    try:
        os.makedirs(os.path.join(d, "app"))
        open(os.path.join(d, "app/svc.php"), "w").write("""<?php
namespace App;
class Base { public function log($m){ return $m; } }
class Svc extends Base {
  public function run($x){ $this->helper($x); $this->log('a'); self::st(); fmt($x, 1); }
  private function helper($y){ return new Repo(); }
  private function orphan(){ return 1; }
  public static function st(){ return 2; }
  public function broken(){ $this->nonExist(); helper2(); }
}
class Repo { public function __construct(){} }
function fmt($a, $b, $c){ return $a; }
function helper2(){ return 1; }
""")
        open(os.path.join(d, "app/dyn.php"), "w").write("""<?php
class Dyn { public function go($p){ $m = '_render' . $p; return $this->$m(); } private function _renderX(){ return 1; } }
""")
        os.makedirs(os.path.join(d, "app/A")); os.makedirs(os.path.join(d, "app/B"))
        open(os.path.join(d, "app/A/Dim.php"), "w").write("<?php\nnamespace A;\nclass Dim { public function __construct($x, $y){} }\n")
        open(os.path.join(d, "app/B/Dim.php"), "w").write("<?php\nnamespace B;\nclass Dim { public function __construct(){} }\n")
        open(os.path.join(d, "app/B/Use.php"), "w").write("<?php\nnamespace B;\nuse A\\Dim as ADim;\n"
                                                         "class UseIt { function a(){ new Dim(); new ADim(1, 2); } }\n")
        open(os.path.join(d, "app/typed.php"), "w").write("""<?php
interface Store { public function save($x); }
class DbStore implements Store { public function save($x){ return 1; } }
class Mailer { public function send($to){ return 1; } public function ping(){ return 2; } }
class Ctl { public function __construct(private Mailer $mailer, private Store $store){}
  public function act(Mailer $m){ $m->send('a'); $this->mailer->ping(); $this->store->save(1); $o = new Mailer(); $o->send('b'); } }
""")
        open(os.path.join(d, "app/typed.py"), "w").write("class Repo:\n    def get(self, k): return k\n"
                                                        "def use(r: Repo):\n    return r.get(1)\n")
        open(os.path.join(d, "app/same.js"), "w").write("function fmt(a, b, c){ return a; }\n")
        open(os.path.join(d, "app/mod.py"), "w").write("""from app.util import util_fn
class K:
    def __init__(self, a): self.a = a
    def m(self): return self._p() + util_fn(1)
    def _p(self): return 1
    def _unused(self): return 0
def entry():
    k = K()
    return k.m()
""")
        open(os.path.join(d, "app/util.py"), "w").write("def util_fn(x):\n    return x\n")
        open(os.path.join(d, "app/ui.js"), "w").write("""import {api} from './api.js';
export class View { render(){ this.draw(); api('/x'); } draw(){ return 1; } }
const h = () => new View().render();
""")
        open(os.path.join(d, "app/api.js"), "w").write("export function api(u){ return fetch(u); }\n")
        ns = argparse.Namespace(root=d, full=False)
        import contextlib, io
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_index(ns)
        db = connect(d)

        def has_edge(src, dst, conf=None):
            s, t = lookup(db, src), lookup(db, dst)
            if not s or not t:
                return False
            r = db.execute("SELECT conf FROM edges WHERE caller_id=? AND callee_id=?", (s[0], t[0])).fetchone()
            return bool(r) and (conf is None or r[0] == conf)

        def edge_to_file(src, path):
            s_ = lookup(db, src)
            return {r[0] for r in db.execute("SELECT f.path FROM edges e JOIN symbols s ON s.id=e.callee_id JOIN files f "
                                             "ON f.id=s.file_id WHERE e.caller_id=?", (s_[0],))} if s_ else set()

        checks = [
            ("php namespace: new Dim() → B\\Dim, use-alias → A\\Dim", edge_to_file("UseIt.a", "") == {"app/A/Dim.php", "app/B/Dim.php"}),
            ('no PHP→JS edges (same fmt name in .js)', "app/same.js" not in edge_to_file("Svc.run", "")),
            ('php types: typed parameter $m->send()', has_edge("Ctl.act", "Mailer.send", "high")),
            ('php types: promoted property $this->mailer->ping()', has_edge("Ctl.act", "Mailer.ping", "high")),
            ('php types: interface → implementation', has_edge("Ctl.act", "DbStore.save")),
            ('python types: r: Repo → Repo.get', has_edge("use", "Repo.get", "high")),
            ('php $this->private method', has_edge("Svc.run", "Svc.helper", "high")),
            ('php inherited method', has_edge("Svc.run", "Base.log", "high")),
            ('php self::static', has_edge("Svc.run", "Svc.st", "high")),
            ('php new Class → __construct', has_edge("Svc.helper", "Repo.__construct")),
            ('php global function', has_edge("Svc.run", "app/svc.php:fmt", "high")),
            ("python self._p", has_edge("K.m", "K._p", "high")),
            ('python import from another file', has_edge("K.m", "util_fn", "high")),
            ("python K() → __init__", has_edge("entry", "K.__init__")),
            ("js this.draw", has_edge("View.render", "View.draw", "high")),
            ('js named import', has_edge("View.render", "api", "high")),
            ("js arrow + new", has_edge("h", "View.constructor") or bool(db.execute(
                "SELECT 1 FROM edges e JOIN symbols s ON s.id=e.callee_id WHERE s.name='View'").fetchone())),
            ('dynamic $this->$m() recorded', db.execute("SELECT COUNT(*) FROM calls WHERE dynamic=1").fetchone()[0] >= 1),
        ]
        import io as _io, contextlib as _cl
        buf = _io.StringIO()
        with _cl.redirect_stdout(buf):
            cmd_check(argparse.Namespace(root=d, limit=100))
        rep = buf.getvalue()
        checks += [('check: arity fmt(2 of 3)', 'passes 2 arguments' in rep and "fmt" in rep),
                   ('check: python K() without an argument', 'passes 0 arguments' in rep and "__init__" in rep),
                   ('check: missing method nonExist', "nonExist" in rep)]
        dead = {fmt(db, sid).split()[-1]: v for v, sid, n, d_ in dead_candidates(db, d)}
        checks += [("dead: Svc.orphan", dead.get("Svc.orphan") == "VERIFIED_DEAD"),
                   ("dead: K._unused", dead.get("K._unused") == "VERIFIED_DEAD"),
                   ('dead: does not flag a used helper', "Svc.helper" not in dead),
                   ('dead: method reachable via $this->$m() → AMBIGUOUS', dead.get("Dyn._renderX") == "AMBIGUOUS")]
        ok = sum(1 for _, c in checks if c)
        for n, c in checks:
            print(("PASS " if c else "FAIL ") + n)
        print(f"{ok}/{len(checks)} → {'TRUST' if ok == len(checks) else 'DISTRUST'}")
        sys.exit(0 if ok == len(checks) else 1)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def cmd_catalog(a):
    """cg catalog check|validate: the API catalog tool (scripts/catalog.py) on this project."""
    catalog_for()
    argv = [a.catalog_command, '--root', a.root]
    if a.catalog_command == 'check':
        argv += ['--limit', str(a.limit)] + (['--json'] if a.json else [])
    code = _CATALOG_MODULE['main'](argv)
    if code:
        sys.exit(code)


def cmd_explore(a):
    import runpy
    module = runpy.run_path(str(Path(__file__).with_name("workbench.py")))
    module["launch"](a, sys.modules[__name__])


def _module(name, entry):
    """Carica un modulo 1.6 accanto a cg.py; errore chiaro finché la corsia non lo consegna."""
    import runpy
    path = Path(__file__).with_name(name)
    if not path.is_file():
        raise SystemExit(name + ' is not yet available in this installation.')
    module = runpy.run_path(str(path))
    if entry not in module:
        raise SystemExit(name + ': missing ' + entry + "().")
    return module[entry]


def cmd_mcp(a):
    """Server MCP su stdio (contratto 1.6, § B)."""
    _module("mcp_server.py", "serve")(sys.modules[__name__], a.root)


def cmd_change(a):
    """Flusso di modifica con agenti (contratto 1.6, § C.8)."""
    _module("change_flow.py", "cli")(sys.modules[__name__], a.root, list(a.args))


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('the limit must be positive')
    return number


START_HERE_HELP = """start here:
  start                      what code-graph can answer on this project, with ready commands
  impact SYMBOL              callers up to a depth, each with its callsite
  verify SYMBOL --plan       tests that reach a function (proposed, never run)
  locate "request text"      where to change: candidate places, their callers and tests, in one answer
  search "words"             find the code for a request: text, word roots, synonyms and graph
  review --base HEAD         what a change touched: signatures, calls, findings

explore:   find, show, callers, callees, path, context, source, map, flows, architecture,
           features, prepare, explore (dashboard), export (offline HTML)
advanced:  index, stats, check, dead, catalog, change, mcp, selftest

Every command takes --root PROJECT before the command name; COMMAND --help for its options."""


CONF_HELP = "lowest edge confidence to follow: high, medium or low"
JSON_HELP = "print the machine-readable JSON answer"
# English help for every command and option, applied after the parser is built so the listing stays
# grouped as START_HERE_HELP shows it. tools/gen_reference.py builds docs/guide/reference/cli.md from it.
COMMAND_HELP = {
    "index": ("build or refresh the index (only files whose content changed are re-parsed)",
              {"full": "rebuild every file instead of only the changed ones"}),
    "start": ("start here: index the project and show what to ask it, with ready commands",
              {"json": JSON_HELP, "lang": "language of the guide (default: from the environment)"}),
    "locate": ("from the text of a request to the places to change, their callers and tests; no model calls",
               {"text": "the request, issue or stack trace, as text", "limit": "candidate places to return, 1-10",
                "max_chars": "budget for the whole answer in characters, 512-16000",
                "min_conf": CONF_HELP, "json": JSON_HELP}),
    "search": ("find the code for a request: full-text BM25 over names, paths, signatures and bodies, fused with the call graph",
               {"query": "words to search; word roots and synonyms widen it when exact words miss",
                "mode": "hybrid (text and graph, default) or lexical (text only)", "limit": "results to return",
                "depth": "graph steps from the best text matches, 1-3", "min_conf": CONF_HELP,
                "max_chars": "budget for the whole answer in characters", "json": JSON_HELP}),
    "catalog": ("API catalog: check (what the index uses that the catalog does not know) or validate",
                {"catalog_command": "check or validate", "json": JSON_HELP, "limit": "entries to show"}),
    "map": ("modules of the project: size, dependencies between them and the most-called functions",
            {"depth": "directory levels that make a module", "limit": "modules, dependencies and hotspots to show",
             "min_conf": CONF_HELP, "scope": "all files, production only or tests only", "json": JSON_HELP}),
    "find": ("find definitions by name; * matches any characters",
             {"pattern": "name or qualified name, e.g. cancel_* or Order.cancel", "kind": "only this kind: function, method, class, ...",
              "limit": "results to show"}),
    "show": ("one definition: file, lines, signature, callers and callees", {"symbol": "name or qualified name"}),
    "callers": ("direct callers of a function, each with its callsite", {"symbol": "name or qualified name", "min_conf": CONF_HELP}),
    "callees": ("functions a function calls directly", {"symbol": "name or qualified name", "min_conf": CONF_HELP}),
    "impact": ("what breaks if this changes: callers up to a depth, each with its callsite, and the tests reached",
               {"target": "symbol, or a file path with --file", "file": "TARGET is a file: impact of every definition in it",
                "depth": "caller levels to follow", "min_conf": CONF_HELP, "limit": "callers to show", "json": JSON_HELP}),
    "path": ("shortest call path from one function to another", {"src": "calling function", "dst": "called function",
                                                                 "min_conf": CONF_HELP}),
    "context": ("source of a function with the callers, callees and tests an agent needs, within a budget",
                {"symbol": "name or qualified name", "budget": "budget in tokens (about 4 characters each)",
                 "max_chars": "budget in characters; overrides --budget", "json": JSON_HELP,
                 "profile": "what the context is for: understand, change, debug or review",
                 "line": "pick the definition that contains this line when the name is ambiguous", "min_conf": CONF_HELP}),
    "check": ("static candidates for defects: wrong argument counts, missing methods, call cycles between files, PHP name clashes",
              {"limit": "findings to show", "json": JSON_HELP, "include_diagnostics": "also list parser and analysis diagnostics"}),
    "dead": ("definitions nothing calls in the graph, with a verdict on how sure that is",
             {"all": "also show ambiguous and unverified candidates", "limit": "candidates to show"}),
    "stats": ("size of the index: files, lines, definitions, calls and edges by confidence", {"json": JSON_HELP}),
    "review": ("what a change touched: signatures, calls and findings compared with a base, plus a test plan",
               {"base": "Git revision to compare with, e.g. HEAD or main", "staged": "review only staged changes",
                "baseline": "compare findings with a baseline file saved earlier", "save_baseline": "save the current findings to this file",
                "force": "overwrite the file given to --save-baseline", "max_chars": "budget for the whole answer in characters",
                "min_conf": CONF_HELP, "depth": "caller levels for the test plan", "json": JSON_HELP,
                "summary": "with --json: a compact answer (base, counts, new findings, tests with commands, limits); text output is already a summary"}),
    "verify": ("tests that reach a function or file, proposed and never run",
               {"target": "symbol, or a file path with --file", "file": "TARGET is a file",
                "plan": "accepted for compatibility; verify always proposes tests and never runs them",
                "min_conf": CONF_HELP, "depth": "caller levels to follow", "json": JSON_HELP}),
    "architecture": ("observations on the project structure and the resources it declares", {"json": JSON_HELP}),
    "features": ("feature cards: groups of related code with their entry points", {"query": "only features matching these words",
                                                                                   "json": JSON_HELP}),
    "flows": ("functional map: declared purposes, their code references and witnessed direct links; technical candidates are separate",
              {"query": "words in the declared purpose or technical candidate name", "limit": "declared nodes to return, 1-100",
               "max_chars": "budget for the whole JSON answer including its newline, 512-16000", "min_conf": CONF_HELP,
               "json": "accepted for compatibility; flows always returns structured JSON"}),
    "prepare": ("evidence package for changing a feature: places, impact and tests, within a budget",
                {"feature": "feature name from features", "symbol": "start from this symbol instead of a feature",
                 "verify_symbol": "also plan tests for this symbol (repeatable)", "intent": "what the change should do, in words",
                 "max_chars": "budget for the whole answer in characters", "min_conf": CONF_HELP,
                 "depth": "caller levels to follow", "json": JSON_HELP}),
    "source": ("a window of source around a line and Unicode column, checked against the index",
               {"path": "file path inside the project", "line": "line number", "column": "Unicode column on that line",
                "max_chars": "characters of source to return", "json": JSON_HELP}),
    "export": ("write an offline interactive HTML page of the call graph",
               {"output": ".html path inside the root (default: .code-graph/explore.html)",
                "symbol": "export only the neighbourhood of this symbol", "depth": "steps around --symbol",
                "min_conf": CONF_HELP, "max_nodes": "stop above this many nodes", "max_edges": "stop above this many edges",
                "force": "replace an existing HTML file"}),
    "explore": ("open the local dashboard in the browser",
                {"workspace": "folder the dashboard may choose projects from", "port": "local port; 0 picks a free one",
                 "no_open": "print the URL without opening the browser"}),
    "selftest": ("run the built-in parser and call-resolution checks", {}),
    "mcp": ("MCP server on stdio for Codex, Claude Code and other clients (read-only tools)", {}),
    "change": ("guided change with agents: plan, approval, isolated edit, tests and a review by the other model",
               {"args": "an action and its options; cg change --help lists them"}),
}


def apply_help(parser, sub):
    """Attach COMMAND_HELP to the built parser; a command or option without a text is a test failure."""
    parser._actions[[a.dest for a in parser._actions].index("root")].help = "project folder (default: the current folder)"
    listed = {action.dest: action for action in sub._choices_actions}
    for name, command in sub.choices.items():
        text, options = COMMAND_HELP[name]
        command.description = text[0].upper() + text[1:] + "."
        if name in listed:
            listed[name].help = text
        for action in command._actions:
            if action.dest in options:
                action.help = options[action.dest]


def build_parser():
    p = argparse.ArgumentParser(prog="cg", description="code-graph: local call graph and search for large codebases",
                                epilog=START_HERE_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=".")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    s = sub.add_parser("index"); s.add_argument("--full", action="store_true"); s.set_defaults(fn=cmd_index)
    s = sub.add_parser("start", help="start here: index the project and show what to ask it, with ready commands")
    s.add_argument("--json", action="store_true"); s.add_argument("--lang", choices=("en", "it")); s.set_defaults(fn=cmd_start)
    s = sub.add_parser("locate", help="from the text of a request to the places to change, their callers and tests; no model calls")
    s.add_argument("text"); s.add_argument("--limit", type=int, default=5); s.add_argument("--max-chars", type=int, default=5000)
    s.add_argument("--min-conf", default="medium", choices=CONF_RANK); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_locate)
    s = sub.add_parser('search', help='ricerca locale FTS5/BM25 e fusione con il grafo')
    s.add_argument('query'); s.add_argument('--mode', choices=('hybrid','lexical'), default='hybrid')
    s.add_argument('--limit', type=positive_int, default=10)
    s.add_argument('--depth', type=int, choices=range(1,4), default=2)
    s.add_argument('--min-conf', choices=CONF_RANK, default='medium')
    s.add_argument('--max-chars', type=positive_int, default=9000)
    s.add_argument('--json', action='store_true'); s.set_defaults(fn=cmd_search)
    s = sub.add_parser("catalog", help="API catalog: check (what the index uses that the catalog does not know) or validate")
    s.add_argument("catalog_command", choices=("check", "validate")); s.add_argument("--json", action="store_true")
    s.add_argument("--limit", type=int, default=40); s.set_defaults(fn=cmd_catalog)
    s = sub.add_parser("map"); s.add_argument("--depth", type=int, default=2); s.add_argument("--limit", type=int, default=25)
    s.add_argument("--min-conf", choices=CONF_RANK, default="medium"); s.add_argument("--scope", choices=("all","production","tests"), default="all"); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_map)
    s = sub.add_parser("find"); s.add_argument("pattern"); s.add_argument("--kind"); s.add_argument("--limit", type=int, default=40)
    s.set_defaults(fn=cmd_find)
    s = sub.add_parser("show"); s.add_argument("symbol"); s.set_defaults(fn=cmd_show)
    for name, d in (("callers", "in"), ("callees", "out")):
        s = sub.add_parser(name); s.add_argument("symbol"); s.add_argument("--min-conf", default="low", choices=CONF_RANK)
        s.set_defaults(fn=lambda a, d=d: cmd_edges(a, d))
    s = sub.add_parser("impact"); s.add_argument("target"); s.add_argument("--file", action="store_true")
    s.add_argument("--depth", type=int, default=3); s.add_argument("--min-conf", default="medium", choices=CONF_RANK)
    s.add_argument("--limit", type=int, default=40); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_impact)
    s = sub.add_parser("path"); s.add_argument("src"); s.add_argument("dst"); s.add_argument("--min-conf", default="low",
                                                                                          choices=CONF_RANK)
    s.set_defaults(fn=cmd_path)
    s = sub.add_parser("context"); s.add_argument("symbol"); s.add_argument("--budget", type=positive_int, default=2500)
    s.add_argument("--max-chars", type=positive_int); s.add_argument("--json", action="store_true")
    s.add_argument("--profile", choices=("understand", "change", "debug", "review"), default="change")
    s.add_argument("--line", type=positive_int)
    s.add_argument("--min-conf", default="medium", choices=CONF_RANK); s.set_defaults(fn=cmd_context)
    s = sub.add_parser("check"); s.add_argument("--limit", type=positive_int, default=60); s.add_argument("--json", action="store_true"); s.add_argument("--include-diagnostics", action="store_true"); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("dead"); s.add_argument("--all", action="store_true"); s.add_argument("--limit", type=int, default=60)
    s.set_defaults(fn=cmd_dead)
    s = sub.add_parser("stats"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_stats)
    s = sub.add_parser('review', help='analisi statica di diff e confronto baseline')
    s.add_argument('--base'); s.add_argument('--staged',action='store_true'); s.add_argument('--baseline'); s.add_argument('--save-baseline'); s.add_argument('--force',action='store_true')
    s.add_argument('--max-chars',type=positive_int,default=20000); s.add_argument('--min-conf',choices=CONF_RANK,default='medium'); s.add_argument('--depth',type=int,choices=range(1,6),default=3); s.add_argument('--json',action='store_true');s.add_argument('--summary',action='store_true');s.set_defaults(fn=cmd_analysis)
    s = sub.add_parser('verify', help='propone test pertinenti senza eseguirli');s.add_argument('target');s.add_argument('--file',action='store_true');s.add_argument('--plan',action='store_true')
    s.add_argument('--min-conf',choices=CONF_RANK,default='medium');s.add_argument('--depth',type=int,choices=range(1,6),default=3);s.add_argument('--json',action='store_true');s.set_defaults(fn=cmd_analysis)
    s = sub.add_parser('architecture',help='osservazioni e dichiarazioni di risorse');s.add_argument('--json',action='store_true');s.set_defaults(fn=cmd_analysis)
    s=sub.add_parser('features',help='schede funzionalità e punti di partenza');s.add_argument('--query',default='');s.add_argument('--json',action='store_true');s.set_defaults(fn=cmd_features)
    s=sub.add_parser('flows');s.add_argument('query',nargs='?',default='');s.add_argument('--limit',type=positive_int,default=25);s.add_argument('--max-chars',type=positive_int,default=12000);s.add_argument('--min-conf',choices=CONF_RANK,default='medium');s.add_argument('--json',action='store_true');s.set_defaults(fn=cmd_flows)
    s=sub.add_parser('prepare',help='pacchetto evidenze per una modifica di funzionalità');s.add_argument('feature',nargs='?');s.add_argument('--symbol');s.add_argument('--verify-symbol',action='append',default=[]);s.add_argument('--intent',default='');s.add_argument('--max-chars',type=positive_int,default=12000);s.add_argument('--min-conf',choices=CONF_RANK,default='medium');s.add_argument('--depth',type=int,choices=range(1,4),default=2);s.add_argument('--json',action='store_true');s.set_defaults(fn=cmd_features)
    s=sub.add_parser('source',help='finestra sorgente per riga e colonna Unicode');s.add_argument('path');s.add_argument('--line',type=positive_int,default=1);s.add_argument('--column',type=int,default=0);s.add_argument('--max-chars',type=positive_int,default=1200);s.add_argument('--json',action='store_true');s.set_defaults(fn=cmd_features)
    s = sub.add_parser("export", help="genera un HTML locale interattivo del grafo delle chiamate")
    s.add_argument("--output", default=".code-graph/explore.html", help="percorso .html dentro la root (default: .code-graph/explore.html)")
    s.add_argument("--symbol", help="esporta i vicini del simbolo specificato")
    s.add_argument("--depth", type=int, choices=range(1, 6), default=2)
    s.add_argument("--min-conf", choices=CONF_RANK, default="low")
    s.add_argument("--max-nodes", type=positive_int, default=5000)
    s.add_argument("--max-edges", type=positive_int, default=50000)
    s.add_argument("--force", action="store_true", help="sostituisci l'HTML già esistente")
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("explore", help="apri l’esploratore guidato locale del progetto")
    s.add_argument("--workspace", default=str(Path.home()), help="cartella entro cui scegliere i progetti")
    s.add_argument("--port", type=int, default=0, help="porta locale; 0 sceglie una porta libera")
    s.add_argument("--no-open", action="store_true", help="stampa l’URL senza aprire il browser")
    s.set_defaults(fn=cmd_explore)
    s = sub.add_parser("selftest"); s.set_defaults(fn=cmd_selftest)
    s = sub.add_parser("mcp", help="server MCP su stdio per Codex e Claude"); s.set_defaults(fn=cmd_mcp)
    s = sub.add_parser("change", help="flusso di modifica guidato con agenti", add_help=False)
    s.add_argument("args", nargs=argparse.REMAINDER); s.set_defaults(fn=cmd_change)
    apply_help(p, sub)
    return p


def main():
    p = build_parser()
    a, extra = p.parse_known_args()
    if a.cmd == "change":
        # REMAINDER non raccoglie un'opzione iniziale (es. --help): la inoltra al parser di change.
        a.args = extra + list(a.args)
    elif extra:
        p.error("unrecognized arguments: " + " ".join(extra))
    a.root = os.path.abspath(a.root)
    a.fn(a)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:  # output troncato da head/less: non è un errore
        sys.stderr.close()
