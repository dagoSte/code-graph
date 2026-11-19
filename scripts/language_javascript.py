"""JavaScript/TypeScript extraction and module resolution, including JSX and CommonJS."""
from collections import defaultdict
import json
import os
import re

EXTENSIONS = {".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
              ".ts": "typescript", ".mts": "typescript", ".tsx": "tsx"}
DEFINITIONS = {"function_declaration": "function", "generator_function_declaration": "function", "class_declaration": "class",
               "abstract_class_declaration": "class", "method_definition": "method", "interface_declaration": "interface",
               "method_signature": "method", "abstract_method_signature": "method"}
CALL_TYPES = ("call_expression", "new_expression")


JS_ESCAPE = re.compile(r"\\(u\{[0-9a-fA-F]{1,6}\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[0-3][0-7]{0,2}|[4-7][0-7]?|\r\n|[\s\S])")
JS_SIMPLE = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\n": "", "\r": "", "\r\n": "",
             chr(0x2028): "", chr(0x2029): ""}  # a backslash before a line terminator continues the line


def _js_unescape(match):
    sequence = match.group(1)
    if sequence[0] == "u":
        point = int(sequence[2:-1] if sequence[1] == "{" else sequence[1:], 16)
        return chr(point) if point <= 0x10FFFF else ""
    if sequence[0] == "x":
        return chr(int(sequence[1:], 16))
    if sequence[0] in "01234567":  # legacy octal (sloppy mode): \151 is i, \0 is NUL
        return chr(int(sequence, 8))
    return JS_SIMPLE.get(sequence, sequence)


def _utf16(text):
    """Join UTF-16 pairs. Encode keys with lone surrogates as NUL + hex UTF-16 units, without path separators.
    Keys containing NUL use the same encoding, so ordinary strings cannot impersonate an opaque key."""
    out, i = [], 0
    while i < len(text):
        code = ord(text[i])
        if 0xD800 <= code <= 0xDBFF and i + 1 < len(text) and 0xDC00 <= ord(text[i + 1]) <= 0xDFFF:
            out.append(chr(0x10000 + ((code - 0xD800) << 10) + (ord(text[i + 1]) - 0xDC00)))
            i += 2
            continue
        if code == 0 or 0xD800 <= code <= 0xDFFF:
            return "\0" + text.encode("utf-16-be", "surrogatepass").hex()
        out.append(text[i])
        i += 1
    return "".join(out)


def _js_decode(text):
    text = JS_ESCAPE.sub(_js_unescape, text)
    return _utf16(text) if "\0" in text or any(0xD800 <= ord(c) <= 0xDFFF for c in text) else text


def js_name_text(text):
    """js_name for the text of an identifier."""
    return _js_decode(text) if "\\" in text or "\0" in text else text


def js_name(node):
    """The name an identifier, property name or string literal spells, with escapes decoded as JavaScript reads them
    (an identifier written m + backslash-u0061 is ma; 'pol\\x69sh' is polish). A template literal with substitutions
    spells no fixed name: None."""
    if node is None:
        return ""
    text = txt(node)
    if node.type in ("string", "template_string"):
        if any(c.type == "template_substitution" for c in node.named_children):
            return None
        text = text[1:-1]
    return _js_decode(text) if "\\" in text or "\0" in text else text


class JsModuleFacts:
    """Module facts of one JavaScript/TypeScript file, read lexically; nothing is evaluated.

    Local names that hold a module (import * as ns, import d, const m = require('./m'),
    const m = await import('./m'), import('./m').then(m => ...)) are looked up lexically: let/const in
    their block, var and parameters in their function, imports in the file. Any other declaration, a
    parameter or a local function with that name hides the module; any write to the name (m = ...,
    m += ..., m++, for (m of ...)) makes it unknown; require only loads when it is not a local name.
    A write to a member drops that member (m.x = ..., (m as T).x = ..., (a || m).x = ...); a module object
    handed on (an argument, another name, a property, a returned value, see SINKS) has unknown members, and
    a write through one binding of a specifier is seen through all of them. Names are compared as Node reads
    them, with escapes decoded (js_name). Rebinding the name module (module = {...}) leaves require Node's.
    Calls on such names become recv 'module' with recv_name '<kind>:<specifier>': ns (module object),
    default (default import), dyn (literal import()), call (the binding itself is called), member:<key>
    (const { key } = require(...)). A call of an imported name that a local hides here is recv 'scope'
    (recv_name 'symbol' when the local is a function). JSX elements are recv 'jsx'. Exports mark symbols (exported=2 is the default export); re-exports become imports
    rows named '__export__:<original>:<alias>' (original '*' for export *, alias '=' for
    module.exports = require(...)).
    """
    CALLBACKS = ("arrow_function", "function_expression", "function")
    NODE_MODULE = ("module", "node:module")
    FUNCTIONS = CALLBACKS + ("function_declaration", "generator_function_declaration", "generator_function",
                             "method_definition")
    BLOCKS = ("statement_block", "for_statement", "for_in_statement", "switch_body", "class_static_block")
    # Nodes that hand a value on (to a callee, a variable, a property, a caller, a component): a module object that
    # reaches one of them may be written through the other name, so its members are unknown (see leak()).
    SINKS = ("new_expression", "return_statement", "yield_expression", "throw_statement", "jsx_expression",
             "with_statement", "class_heritage", "assignment_pattern", "object_assignment_pattern", "required_parameter",
             "optional_parameter", "public_field_definition", "field_definition")
    NODES = frozenset(CALLBACKS + SINKS + (
        "import_statement", "variable_declarator", "call_expression", "export_statement", "assignment_expression",
        "augmented_assignment_expression", "update_expression", "unary_expression", "for_in_statement", "catch_clause",
        "jsx_opening_element", "jsx_self_closing_element"))
    WRAPPERS = ("parenthesized_expression", "as_expression", "satisfies_expression", "non_null_expression",
                "type_assertion")  # (m), m as T, m satisfies T, m!, <T>m: the value is m
    OPAQUE = frozenset(CALLBACKS + ("string", "number", "template_string", "true", "false", "null", "undefined", "regex",
                                    "call_expression", "new_expression", "class", "this", "unary_expression",
                                    "update_expression", "comment"))  # values that are never the object of a name
    KEEP = {"assignment_pattern": "left", "object_assignment_pattern": "left", "required_parameter": "pattern",
            "optional_parameter": "pattern", "pair_pattern": "value"}
    _DYNAMIC = {}  # catalog fingerprint -> bare names of the catalog's JS builtin dynamic entries (eval, Function)

    @classmethod
    def dynamic_names(cls, catalog):
        """Calls of these bare names are flagged dynamic: scripts/catalog/js.json, read once per catalog."""
        names = cls._DYNAMIC.get(catalog.fingerprint)
        if names is None:
            names = cls._DYNAMIC[catalog.fingerprint] = frozenset(
                entry['origin']['name'] for entry in catalog.role('js', 'dynamic') if entry['origin']['via'] == 'builtin')
        return names

    def __init__(self, extractor):
        self.ex = extractor
        self.dynamic = self.dynamic_names(extractor.catalog)
        # a binding is (kind, specifier, declaration start); None is a local name that holds no module
        self.names = defaultdict(dict)  # symbol -> name -> binding: imports (file) and parameters (function)
        self.ranges = defaultdict(list)  # (function symbol, name) -> [(start, end, binding)]: declarations, callbacks
        self.defined, self.bound = set(), set()  # callbacks that became symbols / whose parameter is a module
        # A module binding holds while its declaration is the only write to its name in its scope.
        self.assigned, self.invalid = [], set()  # (name, function, position) of every other write; dropped bindings
        self.via_require, self.declared = {}, {}  # binding -> (function, position) of require(); binding -> (function, name)
        self.creators, self.proven = [], set()  # const require = createRequire(...); the ones proven to be node:module's
        self.member_writes = []  # (name, property or None for every member, function, position)
        self.root_writes = []  # (root name, first member or None, function, position) of any member write, at any depth
        self.cjs, self.esm = [], False  # CommonJS export events; the file has ESM exports
        self.named, self.defaults, self.reexports = [], [], []  # (local, exported name), local names, rows
        self.originals = {}  # local name of a named import -> imported name
        self.callback_args = []  # (name, function, position, line) of a name passed as an argument (callback_refs)
        self.member_args = []  # (object, member, function, position, line) of m.f passed as an argument
        self.properties, self.default_members, self.accessors = [], set(), set()  # export slots; ESM default object; getters/setters
        self.property_assignments = []  # direct assignments at file level, for properties of an exported function
        # module.exports = m; module.exports.polish = ...: a write through the exports object reaches what it holds
        self.export_values, self.export_writes = defaultdict(list), []  # path -> [(name, function, position, site)]; writes
        self.exempt = []  # (root, function, position, value, held): module.exports = m, kept from leak() while module is free

    @staticmethod
    def key(node):
        return node.start_byte, node.end_byte, node.type

    @staticmethod
    def literal(call):
        """'./x' for require('./x') / import('./x'); None when the argument is an expression."""
        args = call.child_by_field_name("arguments")
        first = next((c for c in args.named_children if c.type != "comment"), None) if args is not None else None
        if first is None or first.type not in ("string", "template_string") or \
                any(c.type == "template_substitution" for c in first.named_children):
            return None
        value = txt(first)[1:-1]
        return value if 0 < len(value) <= 500 and "\n" not in value else None

    @staticmethod
    def required(node):
        if node is not None and node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None and fn.type == "identifier" and txt(fn) == "require":
                return JsModuleFacts.literal(node)
        return None

    @staticmethod
    def imported(node):
        if node is not None and node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None and fn.type == "import":
                return JsModuleFacts.literal(node)
        return None

    @staticmethod
    def comma_callee(fn):
        """X for (0, X)(...) with X a name or a member (TypeScript's CommonJS output calls imports so); tree-sitter
        reads await (0, X)(...) inside an async function as await(0, X)(...). None for any other callee."""
        if fn.type == "parenthesized_expression":
            inner = [c for c in fn.named_children if c.type != "comment"]
            items = inner[0].named_children if len(inner) == 1 and inner[0].type == "sequence_expression" else []
        elif fn.type == "call_expression" and txt(fn.child_by_field_name("function")) == "await":
            args = fn.child_by_field_name("arguments")
            items = args.named_children if args is not None and JsModuleFacts.in_async(fn) else []
        else:
            return None
        items = [c for c in items if c.type != "comment"]
        if len(items) >= 2 and all(c.type == "number" for c in items[:-1]) and items[-1].type in ("identifier", "member_expression"):
            return items[-1]
        return None

    @staticmethod
    def in_async(node):
        """node is inside an async function, where await is an operator: await(x) is not a call of a function await."""
        scope = node.parent
        while scope is not None and scope.type not in JsModuleFacts.FUNCTIONS:
            scope = scope.parent
        return scope is not None and any(c.type == "async" for c in scope.children)

    @staticmethod
    def computed_callee(fn):
        """f()() and (a || b)() call a value computed at run time; IIFEs and (0, f)() are not recorded."""
        if fn.type == "call_expression":  # tree-sitter reads `await (0, f)(x)` as await(0, f)(x): not a computed callee
            inner = fn.child_by_field_name("function")
            return not (inner is not None and inner.type == "identifier" and txt(inner) == "await")
        if fn.type == "parenthesized_expression":
            inner = next((c for c in fn.named_children if c.type != "comment"), None)
            return inner is not None and inner.type not in JsModuleFacts.CALLBACKS + (
                "sequence_expression", "identifier", "member_expression", "class")
        return False

    def identifiers(self, node):
        out, stack = [], [node]
        while stack and len(out) < 200:
            item = stack.pop()
            if item.type in ("identifier", "shorthand_property_identifier_pattern"):
                out.append(js_name(item))
            elif item.type in self.KEEP:
                child = item.child_by_field_name(self.KEEP[item.type])
                if child is not None:
                    stack.append(child)
            elif item.type != "type_annotation":
                stack.extend(item.named_children)
        return out

    def parameters(self, node):
        single = node.child_by_field_name("parameter")
        if single is not None:
            return [js_name(single)] if single.type == "identifier" else []
        params = node.child_by_field_name("parameters")
        return self.identifiers(params) if params is not None else []

    @classmethod
    def unwrapped(cls, node):
        """The expression inside parentheses and TypeScript wrappers: (m), m as T, m satisfies T, m!, <T>m."""
        while node is not None and node.type in cls.WRAPPERS:
            inner = [c for c in node.named_children if c.type != "comment"]
            node = (inner[-1] if node.type == "type_assertion" else inner[0]) if inner else None
        return node

    def values(self, node, held=False):
        """The identifiers whose object the value of node may be, as Node evaluates it: through parentheses and TS
        wrappers, await, ||, && and ??, both arms of ?:, the last comma operand and assignments; the elements of array
        and object literals (which hold the object), and a member of such a literal ([m][0], ({k: m}).k).
        held: only the objects held by literals (what a destructuring pattern can bind: const {v: x} = {v: m})."""
        kind = node.type if node is not None else None
        if kind == "identifier":  # the common cases first: a name, or a value that is no object of a name
            return [] if held else [node]
        if kind is None or kind in self.OPAQUE:
            return []
        out, stack = [], [(node, not held)]
        while stack and len(out) < 200:
            item, whole = stack.pop()
            item = self.unwrapped(item)
            if item is None:
                continue
            t = item.type
            if t in ("identifier", "shorthand_property_identifier"):
                if whole:
                    out.append(item)
            elif t == "await_expression":
                stack.extend((c, whole) for c in item.named_children if c.type != "comment")
            elif t == "binary_expression":
                if txt(item.child_by_field_name("operator")) in ("||", "&&", "??"):
                    stack += [(item.child_by_field_name("left"), whole), (item.child_by_field_name("right"), whole)]
            elif t == "ternary_expression":
                stack += [(item.child_by_field_name("consequence"), whole), (item.child_by_field_name("alternative"), whole)]
            elif t == "sequence_expression":
                stack.extend((c, whole) for c in [c for c in item.named_children if c.type != "comment"][-1:])
            elif t in ("assignment_expression", "augmented_assignment_expression"):
                stack.append((item.child_by_field_name("right"), whole))
            elif t == "array":
                stack.extend((c, True) for c in item.named_children if c.type not in ("comment", "spread_element"))
            elif t == "object":
                for c in item.named_children:
                    stack.append((c.child_by_field_name("value") if c.type == "pair" else c if
                                  c.type == "shorthand_property_identifier" else None, True))
            elif t in ("member_expression", "subscript_expression"):
                owner = self.unwrapped(item.child_by_field_name("object"))
                if owner is not None and owner.type in ("array", "object"):
                    stack.append((owner, whole))
        return out

    def leak(self, node, func, held=False):
        """node's value is handed on: a module object (or require, module, node:module) among its values may be
        written through the other name, so every member of it is unknown, as for Object.assign(m, ...)."""
        if node is None:
            return
        path = self.export_path(node) if not held else None
        if path is not None:  # Object.assign(module.exports, ...): whatever the exports object holds
            self.export_writes.append(path + (None, func, node.start_byte))
        for item in self.values(node, held):
            name, at = js_name(item), item.start_byte
            self.member_writes.append((name, None, func, at))
            self.root_writes.append((name, None, func, at))

    def handed(self, target, value, func, at, held):
        """other = m, o.k = m, ({v: x} = {v: m}): m is handed on. module.exports = m hands m to other files, which the
        per-file rule leaves out, but only when module (or exports) is the file's own: finish decides (exempt)."""
        path = self.export_target(target)
        if path is None:
            self.leak(value, func, held)
        else:
            self.exempt.append((path.split(".")[0], func, at, value, held))

    def sink(self, n, func):
        """The values a node hands on: arguments, initializers, assigned values, defaults, returned/thrown/yielded
        values, arrow results, JSX expressions, with objects, extends clauses and class fields (see SINKS)."""
        t = n.type
        if t in ("call_expression", "new_expression"):
            args = n.child_by_field_name("arguments")
            items = [] if args is None else [s for s in args.named_children if s.type == "template_substitution"] \
                if args.type == "template_string" else list(args.named_children)
            for item in items:  # f(...[m]) passes m; f(...m) passes m's elements
                spread = item.type == "spread_element"
                inner = next((c for c in item.named_children if c.type != "comment"), None) if spread or \
                    item.type == "template_substitution" else item
                if not spread or (inner is not None and inner.type == "array"):
                    self.leak(inner, func)
        elif t == "variable_declarator":  # const other = m; const {f} = m reads members, const {v: x} = {v: m} binds m
            name = n.child_by_field_name("name")
            if name is not None:
                self.leak(n.child_by_field_name("value"), func, held=name.type != "identifier")
        elif t in ("assignment_pattern", "object_assignment_pattern"):  # x = m as a default value
            left = n.child_by_field_name("left")
            if left is not None and left.type in ("identifier", "shorthand_property_identifier_pattern"):
                self.leak(n.child_by_field_name("right"), func)
        elif t in ("required_parameter", "optional_parameter"):  # TS: (x = m)
            pattern = n.child_by_field_name("pattern")
            if pattern is not None and pattern.type == "identifier":
                self.leak(n.child_by_field_name("value"), func)
        elif t in ("public_field_definition", "field_definition"):
            self.leak(n.child_by_field_name("value"), func)
        elif t == "arrow_function":
            body = n.child_by_field_name("body")
            if body is not None and body.type != "statement_block":
                self.leak(body, func)
        elif t == "with_statement":
            self.leak(n.child_by_field_name("object"), func)
        elif t in self.SINKS:  # return, yield, throw, {jsx}, extends (TS: extends_clause)
            for item in n.named_children:
                if item.type != "comment":
                    self.leak(item.child_by_field_name("value") if item.type == "extends_clause" else item, func)

    def passed(self, n, func):
        """Record names and direct module members passed as arguments; finish checks their bindings."""
        args = n.child_by_field_name("arguments")
        fn = n.child_by_field_name("function")
        awaiting = fn is not None and fn.type == "identifier" and txt(fn) == "await" and self.in_async(n)
        for arg in args.named_children if args is not None and args.type == "arguments" else ():
            if arg.type == "identifier":
                self.callback_args.append((txt(arg), func, arg.start_byte, n.start_point[0] + 1))
            elif arg.type == "member_expression" and not awaiting:  # await (0, m.f)(x): m.f is the callee
                owner, member = arg.child_by_field_name("object"), arg.child_by_field_name("property")
                if owner is not None and owner.type == "identifier" and member is not None and member.type == "property_identifier":
                    self.member_args.append((js_name(owner), js_name(member), func, owner.start_byte, n.start_point[0] + 1))

    def dotted(self, node):
        """'a.b.c' for a name or a chain of members with fixed names (parentheses, TS wrappers, m['x'] and escapes
        read as Node reads them); None for any other expression."""
        if node is None:
            return None
        parts = []
        node = self.unwrapped(node)
        while node is not None and node.type in ("member_expression", "subscript_expression"):
            if node.type == "member_expression":
                part = js_name(node.child_by_field_name("property"))
            else:
                index = node.child_by_field_name("index")
                part = js_name(index) if index is not None and index.type in ("string", "template_string") else None
            if part is None:
                return None
            parts.append(part)
            node = self.unwrapped(node.child_by_field_name("object"))
        return ".".join([js_name(node)] + parts[::-1]) if node is not None and node.type == "identifier" else None

    def maybe_exports(self, node):
        """A cheap first test: only a name, a member or a wrapper written from module, exports, ( or < (or with an
        escape) can be the exports object."""
        if node is None or (node.type not in ("identifier", "member_expression", "subscript_expression") and node.type not in self.WRAPPERS):
            return False
        head = self.ex.src[node.start_byte:min(node.end_byte, node.start_byte + 9)]
        return head.startswith((b"module", b"exports", b"(", b"<")) or b"\\" in head

    def export_target(self, node):
        """'module.exports', 'module.exports.x' or 'exports.x' for an export target, written in any form Node reads as
        such ((module).exports, module['exports'], escaped names); None for any other target."""
        path = self.dotted(node) if self.maybe_exports(node) else None
        return path if path is not None and (path == "module.exports" or path.startswith(("module.exports.", "exports."))) else None

    def export_path(self, node):
        """(path, root) for the exports object or a path in it: path is 'module.exports[.x...]' (exports.x is
        module.exports.x), root the name it is written from ('module' or 'exports'); else None."""
        path = self.dotted(node) if self.maybe_exports(node) else None
        if path is None or not (path == "exports" or path.startswith("exports.") or path == "module.exports"
                                or path.startswith("module.exports.")):
            return None
        return (path if path.startswith("module.") else "module." + path), path.split(".")[0]

    def visit(self, n, scope, func, new_scope):
        t = n.type
        if new_scope != scope:  # n became a symbol: its parameters are locals of that symbol
            value = n.child_by_field_name("value") or n.child_by_field_name("right")
            callback = value is not None and value.type in self.CALLBACKS
            if callback:
                self.defined.add(self.key(value))
            for name in self.parameters(value if callback else n):
                self.names[new_scope][name] = None
        if t in self.CALLBACKS:
            if self.key(n) not in self.defined and self.key(n) not in self.bound:
                for name in self.parameters(n):
                    self.ranges[(func, name)].append((n.start_byte, n.end_byte, None))
            self.sink(n, func)  # () => m
        elif t == "import_statement":
            self.import_names(n, scope)
        elif t == "variable_declarator":
            self.declare(n, func)
            self.sink(n, func)  # const other = m
        elif t == "for_in_statement":
            left, kind = n.child_by_field_name("left"), n.child_by_field_name("kind")
            if left is not None and kind is not None:  # for (const m of ...): a new local in the loop (var: the function)
                block = n if txt(kind) != "var" else self.block(n, True)
                for local in self.identifiers(left):
                    self.local(func, local, block, None, n.start_byte)
            elif left is not None:  # for (m of ...), for (m.x in ...): a write
                self.written(left, func, n.start_byte)
            right = self.unwrapped(n.child_by_field_name("right"))
            if right is not None and right.type == "array" and txt(n.child_by_field_name("operator")) == "of":
                self.leak(right, func)  # for (const x of [m]): x is m
        elif t == "catch_clause":
            parameter = n.child_by_field_name("parameter")
            for local in self.identifiers(parameter) if parameter is not None else []:
                self.ranges[(func, local)].append((n.start_byte, n.end_byte, None))
        elif t in ("augmented_assignment_expression", "update_expression"):  # m += ..., m ||= ..., m.x &&= ..., m++
            target = n.child_by_field_name("left") or n.child_by_field_name("argument")
            if target is not None:
                self.written(target, func, n.start_byte)
            if txt(n.child_by_field_name("operator")) in ("||=", "&&=", "??="):
                self.handed(target, n.child_by_field_name("right"), func, n.start_byte, False)  # other ||= m
        elif t == "unary_expression":  # delete m.x / delete m[k]: every member is unknown
            target = self.unwrapped(n.child_by_field_name("argument"))
            if txt(n.child_by_field_name("operator")) == "delete" and target is not None and \
                    target.type in ("member_expression", "subscript_expression"):
                self.member_write(target, func, n.start_byte, every=True)
        elif t == "call_expression":
            self.then_callback(n, func)
            self.sink(n, func)  # f(m), Object.assign(m, ...), Reflect.set(m, ...), helper(m)
            self.passed(n, func)
        elif t == "export_statement":
            self.export(n)
        elif t == "assignment_expression":
            if n.parent is not None and n.parent.type == "expression_statement" and n.parent.parent.type == "program":
                self.property_assignments.append(n)
            left = n.child_by_field_name("left")
            if left is not None:
                self.written(left, func, n.start_byte)
                self.handed(left, n.child_by_field_name("right"), func, n.start_byte, left.type in ("object_pattern", "array_pattern"))
            self.commonjs_export(n, scope, new_scope, func)
        elif t in self.SINKS:
            self.sink(n, func)
            if t == "new_expression":
                self.passed(n, func)
        elif t in ("jsx_opening_element", "jsx_self_closing_element"):
            tag = n.child_by_field_name("name")
            if tag is not None and tag.type == "identifier" and txt(tag)[:1].isupper():
                self.ex.add_call(n, func, txt(tag), "jsx", "")

    def written(self, node, func, at):
        """An assignment target: names it writes, and members it writes (None: a computed key, every member)."""
        stack = [node]
        while stack:
            item = self.unwrapped(stack.pop())  # (m) = ..., (m as T) = ..., m! = ...
            if item is None:
                continue
            if item.type in ("identifier", "shorthand_property_identifier_pattern"):
                self.assigned.append((js_name(item), func, at))
            elif item.type in ("member_expression", "subscript_expression"):
                self.member_write(item, func, at)
            elif item.type in self.KEEP:
                child = item.child_by_field_name(self.KEEP[item.type])
                if child is not None:
                    stack.append(child)
            elif item.type in ("array_pattern", "object_pattern", "rest_pattern"):
                stack.extend(item.named_children)

    @staticmethod
    def member_name(target):
        """The member m.x or m['x'] names, escapes decoded (m['pol\\x69sh'] is polish); None for a computed key."""
        if target.type == "member_expression":
            return js_name(target.child_by_field_name("property"))
        index = target.child_by_field_name("index")
        if index is not None and index.type == "number":
            return txt(index)
        return js_name(index) if index is not None and index.type in ("string", "template_string") else None

    def member_write(self, target, func, at, every=False):
        """A write to a member: of every object its owner may be ((m).x, (m as T).x, m!.x, (a || m).x, [m][0].x), and
        of that member (None: a computed key, or every member)."""
        self.rooted(target, func, at)
        member = None if every else self.member_name(target)
        for owner in self.values(target.child_by_field_name("object")):
            self.member_writes.append((js_name(owner), member, func, at))
        path = self.export_path(target.child_by_field_name("object"))
        if path is not None:
            self.export_writes.append(path + (member, func, at))

    def rooted(self, target, func, at):
        """Record the root name of a member write (require.cache[k], module.require, M['createRequire'], (M).x)."""
        member = None
        target = self.unwrapped(target)
        while target is not None and target.type in ("member_expression", "subscript_expression"):
            member = self.member_name(target)
            target = self.unwrapped(target.child_by_field_name("object"))
        for root in self.values(target) if target is not None else ():
            self.root_writes.append((js_name(root), member, func, at))

    def local(self, func, name, block, binding, at):
        """Declare name in block; a second declaration in the same scope, or one over a parameter or an import, is a
        write, so the name's module binding (if any) is unknown."""
        span = (block.start_byte, block.end_byte)
        scope_level = block.parent is None or block.type in self.FUNCTIONS or (
            block.type == "statement_block" and block.parent is not None and block.parent.type in self.FUNCTIONS)
        if any(r[:2] == span for r in self.ranges.get((func, name), ())) or (scope_level and name in self.names.get(func, {})):
            self.assigned.append((name, func, at))
            binding = None
        self.ranges[(func, name)].append(span + (binding,))
        if binding:
            self.declared[binding] = (func, name)

    def block(self, n, function_scoped):
        """The node a declaration belongs to: its block for let/const, its function (or the file) for var."""
        node = n.parent
        kinds = self.FUNCTIONS if function_scoped else self.FUNCTIONS + self.BLOCKS
        while node is not None and node.parent is not None and node.type not in kinds:
            node = node.parent
        return node if node is not None else n

    def declare(self, n, func):
        name, value = n.child_by_field_name("name"), n.child_by_field_name("value")
        if name is None:
            return
        block = self.block(n, n.parent is not None and n.parent.type == "variable_declaration")
        if name.type != "identifier":
            spec, done = self.required(value), set()
            for item in name.named_children if name.type == "object_pattern" and spec is not None else []:
                key = item if item.type == "shorthand_property_identifier_pattern" else item.child_by_field_name("key")
                alias = item if item.type == "shorthand_property_identifier_pattern" else item.child_by_field_name("value")
                if key is None or key.type not in ("property_identifier", "shorthand_property_identifier_pattern", "string") \
                        or alias is None or alias.type not in ("identifier", "shorthand_property_identifier_pattern"):
                    continue  # a default value, a nested pattern, a computed key or ...rest: a local that holds no member
                if spec in self.NODE_MODULE and js_name(key) == "createRequire":  # const { createRequire } = require('module')
                    binding = ("named", spec, n.start_byte)
                    self.originals[js_name(alias)] = "createRequire"
                else:  # const { polish, shine: gloss } = require('./tools'): each local is that member of the module (J02)
                    binding = ("member", spec, n.start_byte, js_name(key))
                self.via_require[binding] = (func, n.start_byte)
                self.local(func, js_name(alias), block, binding, n.start_byte)
                done.add(js_name(alias))
            for local in self.identifiers(name):
                if local not in done:
                    self.local(func, local, block, None, n.start_byte)
            return
        local, binding = js_name(name), None
        if value is not None and value.type in self.CALLBACKS + ("class",):
            span = (block.start_byte, block.end_byte)  # var run = () => ...: a local function symbol, found as such
            if any(r[:2] == span for r in self.ranges.get((func, local), ())):
                self.assigned.append((local, func, n.start_byte))  # ...that also overwrites an earlier var run
            return
        callee = value.child_by_field_name("function") if value is not None and value.type == "call_expression" else None
        if local == "require" and callee is not None and (last_seg(txt(callee)) == "createRequire" or (
                callee.type == "identifier" and self.originals.get(txt(callee)) == "createRequire")):
            binding = ("loader", txt(callee)[:200], n.start_byte)  # checked in finish: createRequire must be node:module's
            self.creators.append((binding, callee, func, n.start_byte))
            self.local(func, local, block, binding, n.start_byte)
            return
        spec = self.required(value)
        if spec is not None:
            binding = ("ns", spec, n.start_byte)
            self.via_require[binding] = (func, n.start_byte)
        elif value is not None and value.type == "await_expression" and value.named_children:
            spec = self.imported(value.named_children[0])
            binding = ("dyn", spec, n.start_byte) if spec is not None else None
        self.local(func, local, block, binding, n.start_byte)

    def import_names(self, n, scope):
        source = n.child_by_field_name("source")
        spec = txt(source).strip("'\"`") if source is not None else ""
        for clause in n.named_children:
            if clause.type == "import_require_clause":  # TS: import x = require('./x')
                local = next((c for c in clause.named_children if c.type == "identifier"), None)
                target = next((c for c in clause.named_children if c.type == "string"), None)
                if local is not None and target is not None:
                    self.names[scope][js_name(local)] = ("ns", txt(target).strip("'\"`"), n.start_byte)
                    self.declared[self.names[scope][js_name(local)]] = (scope, js_name(local))
            if clause.type != "import_clause" or not spec:
                continue
            for c in clause.named_children:
                if c.type == "identifier":
                    self.names[scope][js_name(c)] = ("default", spec, n.start_byte)
                    self.declared[self.names[scope][js_name(c)]] = (scope, js_name(c))
                elif c.type == "namespace_import":
                    local = next((x for x in c.named_children if x.type == "identifier"), None)
                    if local is not None:
                        self.names[scope][js_name(local)] = ("ns", spec, n.start_byte)
                        self.declared[self.names[scope][js_name(local)]] = (scope, js_name(local))
                elif c.type == "named_imports":
                    for item in c.named_children:
                        local = item.child_by_field_name("alias") or item.child_by_field_name("name")
                        if item.type == "import_specifier" and local is not None:
                            self.names[scope][js_name(local)] = ("named", spec, n.start_byte)
                            self.originals[js_name(local)] = js_name(item.child_by_field_name("name"))

    def then_callback(self, n, func):
        """import('./m').then(m => m.f()): inside the callback m is the loaded module."""
        fn = n.child_by_field_name("function")
        if fn is None or fn.type != "member_expression" or txt(fn.child_by_field_name("property")) != "then":
            return
        spec = self.imported(fn.child_by_field_name("object"))
        args = n.child_by_field_name("arguments")
        callback = next((c for c in args.named_children if c.type != "comment"), None) if spec and args is not None else None
        if callback is None or callback.type not in self.CALLBACKS:
            return
        single = callback.child_by_field_name("parameter")
        params = callback.child_by_field_name("parameters")
        items = [c for c in params.named_children if c.type != "comment"] if params is not None else []
        if single is None and len(items) == 1:
            single = items[0].child_by_field_name("pattern") if items[0].type == "required_parameter" else items[0]
        if single is not None and single.type == "identifier":
            self.bound.add(self.key(callback))
            binding = ("dyn", spec, n.start_byte)
            self.ranges[(func, js_name(single))].append((callback.start_byte, callback.end_byte, binding))
            self.declared[binding] = (func, js_name(single))

    def export(self, n):
        self.esm = True
        source = n.child_by_field_name("source")
        line = n.start_point[0] + 1
        clause = next((c for c in n.named_children if c.type == "export_clause"), None)
        specifiers = []
        for item in (clause.named_children if clause is not None else []):
            name, alias = item.child_by_field_name("name"), item.child_by_field_name("alias")
            if item.type == "export_specifier" and name is not None:
                specifiers.append((txt(name), txt(alias) if alias is not None else txt(name)))
        if source is not None:
            spec = txt(source).strip("'\"`")
            if clause is not None:
                self.reexports.extend((spec, name, alias, line, None) for name, alias in specifiers)
            elif not any(c.type == "namespace_export" for c in n.named_children):
                self.reexports.append((spec, "*", "*", line, None))
            return
        if any(c.type == "=" for c in n.children) and n.named_children and n.named_children[0].type == "identifier":
            self.defaults.append((txt(n.named_children[0]), n.start_byte))  # TS: export = polish
        declaration = n.child_by_field_name("declaration")
        if declaration is not None and not any(c.type == "default" for c in n.children):
            declarations = declaration.named_children if declaration.type in ("lexical_declaration", "variable_declaration") else [declaration]
            for item in declarations:
                name = item.child_by_field_name("name")
                if name is not None and name.type == "identifier":
                    self.properties.append(("named", "", js_name(name), js_name(name), line))
        for name, alias in specifiers:
            if alias == "default":
                self.defaults.append((name, n.start_byte))
            else:
                self.named.append((name, alias, line, n.start_byte))
        if any(c.type == "default" for c in n.children):
            declaration, value = n.child_by_field_name("declaration"), n.child_by_field_name("value")
            name = declaration.child_by_field_name("name") if declaration is not None else None
            if name is not None:
                self.defaults.append((txt(name), n.start_byte))
            elif value is not None and value.type == "identifier":
                self.defaults.append((txt(value), n.start_byte))
            elif value is not None and value.type == "object":
                start = len(self.named)
                self.members(value, line)
                self.default_members.update(self.named[start:])

    def commonjs_export(self, n, scope, new_scope, func):
        """Record module.exports = ... / exports.x = ...; finish keeps the ones that are still live."""
        left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
        target = self.export_target(left)  # also (module).exports, module["exports"], escaped names: as Node reads them
        if right is None or target is None:
            return
        spec = self.required(right)
        if right.type == "call_expression" and txt(right.child_by_field_name("function")) == "Object.assign":
            args = [c for c in right.child_by_field_name("arguments").named_children if c.type != "comment"]
            right = args[0] if args and args[0].type == "identifier" else right  # Object.assign(fn, {...}) is fn
        value = ("symbol", new_scope) if new_scope != scope else ("name", js_name(right)) if right.type == "identifier" else \
            ("object", right) if right.type == "object" else ("require", spec) if spec is not None else ("other", None)
        slot, site = target if target.startswith("module.") else "module." + target, (target.split(".")[0], func, n.start_byte)
        held = set(map(self.key, self.values(right, held=True)))  # module.exports = { m } holds m one level down
        for item in self.values(right):  # what the slot holds now: module.exports = m, exports.k = m
            if self.key(item) not in held:
                self.export_values[slot].append((js_name(item), func, item.start_byte, site))
        for item in right.named_children if right.type == "object" else ():  # module.exports = { k: m }
            if item.type in ("pair", "shorthand_property_identifier"):
                key = js_name(item.child_by_field_name("key")) if item.type == "pair" else js_name(item)
                for held in self.values(item.child_by_field_name("value") if item.type == "pair" else item):
                    self.export_values[f"{slot}.{key}"].append((js_name(held), func, held.start_byte, site))
        ancestor = n.parent  # unconditional only as a statement of the file: exports = module.exports = x is fine
        while ancestor is not None and ancestor.type in ("assignment_expression", "expression_statement", "parenthesized_expression"):
            ancestor = ancestor.parent
        later = ancestor  # inside a function (a callback too): it may run at any time, also after the file has loaded
        while later is not None and later.type not in self.FUNCTIONS:
            later = later.parent
        event = dict(at=n.start_byte, line=n.start_point[0] + 1, func=func, value=value, root=target.split(".")[0],
                     certain=ancestor is not None and ancestor.type == "program", later=later is not None)
        if target == "module.exports":
            realias = n.parent is not None and n.parent.type == "assignment_expression" and \
                txt(n.parent.child_by_field_name("left")) == "exports"
            self.cjs.append(dict(event, kind="replace", realias=realias))
        elif target.count(".") == target.startswith("module.") + 1:  # exports.polish = ...
            self.cjs.append(dict(event, kind="member", via="exports" if target.startswith("exports.") else "module.exports",
                                 name=last_seg(target)))

    @staticmethod
    def survivors(events):
        """The writes of one export slot whose value Node may keep: the last unconditional one, and every write that
        may run after it (a later conditional one, or one inside a function, which may run at any time)."""
        certain = [e for e in events if e["certain"]]
        last = certain[-1] if certain else None
        return [e for e in events if e is last or (not e["certain"] and (last is None or e["later"] or e["at"] > last["at"]))]

    def commonjs_surface(self):
        """Node keeps the last module.exports = ...; exports.x before it, or through the old exports object after
        it, is gone. A replacement that may run after the last certain one (conditional, or in a callback) is an
        alternative: every possible default is kept, and calling the module reaches them at medium (alternatives).
        An export slot (exports.polish) keeps the same survivors: exports.polish = A; exports.polish = B is B."""
        replaces = [e for e in self.cjs if e["kind"] == "replace"]
        alternatives = self.survivors(replaces)
        certain = [e for e in replaces if e["certain"]]
        last = certain[-1] if certain else None
        if replaces:  # known: an object (or required module) lists the exports; open: a function or computed value,
            # whose properties are members too; alternatives: a replacement may not run, or may run after another
            value = alternatives[0]["value"] if len(alternatives) == 1 and alternatives[0] is last else None
            known = value is not None and (value[0] == "require" or (
                value[0] == "object" and not any(c.type == "spread_element" for c in value[1].named_children)))
            self.reexports.append(("", "#cjs", "alternatives" if value is None else "known" if known else "open", 0, None))
        live, slots = list(alternatives), defaultdict(list)
        for event in self.cjs:
            if event["kind"] != "member":
                continue  # exports.x before the last replacement, or through the old exports object after it, is gone;
            if last is not None and ((event["at"] < last["at"] and not (event["later"] and event["via"] == "module.exports"))
                                     or (event["via"] == "exports" and not last["realias"])):
                continue  # module.exports.x = ... in a function may run after it
            slots[event["name"]].append(event)
        for events in slots.values():
            live += self.survivors(events)
        # module.exports = { f: a }; module.exports.f = b: the later certain write replaces the literal's f
        replaced = {name for name, events in slots.items() if last is not None and
                    any(e["certain"] and e["at"] > last["at"] for e in events)}
        for event in sorted(live, key=lambda e: e["at"]):
            kind, value = event["value"]
            name, line, at = event.get("name"), event["line"], event["at"]
            if kind == "symbol":
                symbol = self.ex.syms[value]
                symbol["exported"] = 2 if name is None else symbol["exported"] or 1
                self.properties.append(("value" if name is None else "named", "", symbol["name"], name or "default", line))
            elif kind == "name":
                (self.defaults.append((value, at)) if name is None else self.named.append((value, name, line, at)))
            elif kind == "other" and name is not None:
                self.properties.append(("named", "", "", name, line))
            elif kind == "object" and name is None:
                self.members(value, line, event["func"], replaced if event is last else ())
            elif kind == "require":  # module.exports = require('./x') / exports.find = require('./find.js')
                self.reexports.append((value, "*" if name is None else "default", "=" if name is None else name,
                                       line, (event["func"], at)))

    def function_properties(self, top, local_defs):
        """Keep a single direct property write before exporting a stable local function as the CommonJS value."""
        exports = self.survivors([e for e in self.cjs if e["kind"] == "replace"])
        if self.esm or len(exports) != 1 or not exports[0]["certain"] or exports[0]["func"] != 0:
            return
        export = exports[0]
        kind, owner = export["value"]
        functions = {name for name, ids in top.items() if len(ids) == 1 and self.ex.syms[ids[0]]["kind"] == "function"}
        changed = {name for name, _, _ in self.assigned}
        if kind != "name" or owner not in functions or changed.intersection((owner, "module", "exports")):
            return
        if self.lookup(owner, 0, export["at"], local_defs) != "symbol":
            return
        writes = {(prop, func, at) for name, prop, func, at in self.root_writes + self.member_writes if name == owner}
        for node in self.property_assignments:
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if node.start_byte >= export["at"] or left is None or right is None or \
                    left.type not in ("member_expression", "subscript_expression"):
                continue
            obj = left.child_by_field_name("object")
            if obj is None or obj.type != "identifier" or js_name(obj) != owner or right.type != "identifier":
                continue
            member, value = self.member_name(left), js_name(right)
            if member is None or value not in functions or value in changed:
                continue
            if self.lookup(owner, 0, node.start_byte, local_defs) != "symbol" or \
                    self.lookup(value, 0, node.start_byte, local_defs) != "symbol":
                continue
            if {w for w in writes if w[0] in (None, member)} == {(member, 0, node.start_byte)}:
                self.properties.append(("function", "", value, member, node.start_point[0] + 1))

    def members(self, obj, line, func=0, skip=()):
        for item in obj.named_children:
            if item.type == "shorthand_property_identifier":
                if js_name(item) not in skip:
                    self.named.append((js_name(item), js_name(item), line, obj.start_byte))
            elif item.type == "method_definition":
                if js_name(item.child_by_field_name("name")) not in skip:
                    self.named.append((js_name(item.child_by_field_name("name")), js_name(item.child_by_field_name("name")), line, obj.start_byte))
                    if any(child.type in ("get", "set") for child in item.children):
                        self.accessors.add(self.named[-1])
            elif item.type == "pair":
                key, value = js_name(item.child_by_field_name("key")), item.child_by_field_name("value")
                if key is None or key in skip:
                    continue
                if value is not None and value.type == "identifier":
                    self.named.append((txt(value), key, line, obj.start_byte))
                elif value is not None and value.type in self.CALLBACKS:
                    self.named.append((key, key, line, obj.start_byte))
                elif self.required(value) is not None:  # { find: require('./find.js') }
                    self.reexports.append((self.required(value), "default", key, line, (func, item.start_byte)))

    def loader(self, func, position, local_defs):
        """require here is Node's loader: the global one, or one made by a proven node:module createRequire."""
        binding = self.lookup("require", func, position, local_defs)
        return binding is None or (isinstance(binding, tuple) and binding[0] == "loader" and binding in self.proven)

    def real_create_require(self, callee, func, position, local_defs):
        """Every name in the chain is node:module's: an import of it, or a require() of it made by the loader."""
        def from_node_module(binding, kinds):
            return isinstance(binding, tuple) and binding[0] in kinds and binding[1] in self.NODE_MODULE and \
                (binding not in self.via_require or self.loader(*self.via_require[binding], local_defs))
        if callee.type == "identifier":  # import { createRequire as make } from 'node:module'; const { createRequire } = require('module')
            return self.originals.get(txt(callee)) == "createRequire" and \
                from_node_module(self.lookup(txt(callee), func, position, local_defs), ("named",))
        if callee.type == "member_expression" and txt(callee.child_by_field_name("property")) == "createRequire":
            target = callee.child_by_field_name("object")
            if self.required(target) in self.NODE_MODULE:  # require('module').createRequire, with require the loader
                return self.loader(func, target.start_byte, local_defs)
            return target.type == "identifier" and \
                from_node_module(self.lookup(txt(target), func, position, local_defs), ("ns", "default"))
        return False

    @staticmethod
    def spec_key(spec):
        """One key per module a specifier may load in this file: './a', './a.js' and './a/index.js' share it."""
        if spec.startswith((".", "/")):
            spec = os.path.normpath(spec).replace(os.sep, "/")
            stem, ext = os.path.splitext(spec)
            spec = stem if ext in LANG_BY_EXT or ext == ".json" else spec
            spec = spec[:-len("/index")] if spec.endswith("/index") else "." if spec == "index" else spec
        return spec.removeprefix("node:")

    def lookup(self, name, scope, position, local_defs, raw=False):
        """The binding of name at position: (kind, specifier, key), 'symbol' (a local function or class),
        'local' (a declaration that holds no module, or a module binding that is written), or None (global).
        raw: a module binding is returned even when it was dropped."""
        current = scope
        while current is not None:
            inner = [r for r in self.ranges.get((current, name), ()) if r[0] <= position < r[1]]
            binding = min(inner, key=lambda r: r[1] - r[0])[2] if inner else False
            if binding is False and (current, name) in local_defs:
                return "symbol"
            if binding is False and name in self.names.get(current, {}):
                binding = self.names[current][name]
            if binding is not False:
                return "local" if binding is None or (binding in self.invalid and not raw) else binding
            current = self.ex.syms[current]["parent"]
        return None

    def finish(self):
        ex = self.ex
        top = defaultdict(list)
        for index, symbol in enumerate(ex.syms):
            if symbol["parent"] == 0 and symbol["kind"] in ("function", "class", "method"):
                top[symbol["name"]].append(index)
        local_defs = {(s["parent"], s["name"]) for s in ex.syms if s["kind"] in ("function", "class")}
        starts = []
        # Only the file's own module and exports export: a parameter or local of that name is an ordinary object
        # (function fake(module) { module.exports = f }), and what it receives is handed on like any other value.
        free = lambda root, func, at: self.lookup(root, func, at, local_defs) is None
        for root, func, at, value, held in self.exempt:
            if not free(root, func, at):
                self.leak(value, func, held)
        self.cjs = [event for event in self.cjs if free(event["root"], event["func"], event["at"])]
        self.export_writes = [write for write in self.export_writes if free(write[1], write[3], write[4])]
        self.export_values = {path: [v for v in values if free(*v[3])] for path, values in self.export_values.items()}

        def position(call):
            if not starts:
                starts.extend([0] + [m.end() for m in re.finditer(rb"\n", ex.src)])
            return starts[call["line"] - 1] + call["column_bytes"]

        modules = {name for names in self.names.values() for name, b in names.items() if b and b[0] != "named"}
        modules |= {name for (_, name), items in self.ranges.items() if any(item[2] for item in items)}
        bound = modules | {name for names in self.names.values() for name, b in names.items() if b}
        for name, func, at in self.assigned:  # any write besides the declaration: the value is unknown
            binding = self.lookup(name, func, at, local_defs) if name in bound else None
            if isinstance(binding, tuple):
                self.invalid.add(binding)
        for binding, (func, name) in self.declared.items():  # a function or class with the name in the same scope
            if (func, name) in local_defs:
                self.invalid.add(binding)
        changed = True  # created loaders: proven from the global require and node:module only, never assumed
        while changed:
            changed = False
            for binding, callee, func, at in self.creators:
                if binding not in self.proven and binding not in self.invalid and \
                        self.real_create_require(callee, func, at, local_defs):
                    self.proven.add(binding)
                    changed = True
        self.invalid.update(binding for binding, *_ in self.creators if binding not in self.proven)
        for binding, (func, at) in self.via_require.items():  # function f(require) { const m = require('./m') }
            if not self.loader(func, at, local_defs):
                self.invalid.add(binding)
        # mod.polish = ... drops polish; mod[key] = ..., delete, a module object handed on: all. Node loads a module
        # once, so a write through one binding of it is seen through every other binding of that specifier.
        replaced, wiped = set(), set()
        for path, _, prop, func, at in self.export_writes:  # through the exports object: what the export site put there
            self.member_writes.extend((name, prop, where, position) for name, where, position, _ in self.export_values.get(path, ()))
        for name, prop, func, at in self.member_writes:
            binding = self.lookup(name, func, at, local_defs) if name in bound else None
            if isinstance(binding, tuple) and binding[0] in ("ns", "default", "dyn"):  # a module object, not a member of it
                (wiped.add(self.spec_key(binding[1])) if prop is None else replaced.add((self.spec_key(binding[1]), prop)))
        def node_module(name, func, at):
            binding = self.lookup(name, func, at, local_defs)
            return isinstance(binding, tuple) and binding[1] in self.NODE_MODULE
        # A file that writes to the loader (require, the CommonJS module object, node:module's createRequire) sends no
        # call through the loader or the factory: none of its calls becomes a module edge.
        tampered = any(name in ("require", "createRequire") or (name in bound and node_module(name, func, at))
                       for name, func, at in self.assigned)
        for root, member, func, at in self.root_writes:
            if root in ("require", "createRequire"):
                tampered = True
            elif root in bound and node_module(root, func, at):  # only a bound name can hold node:module
                tampered |= member in (None, "createRequire")
            elif root == "module" and self.lookup("module", func, at, local_defs) is None:
                tampered |= member != "exports"  # module.exports = ... is an export, module.require = ... is not
        if tampered:
            self.invalid.update(self.via_require)
            self.invalid.update(binding for binding, *_ in self.creators)
            self.proven.clear()
        calls = []
        for call in ex.calls:  # require('./m').f(), require('./m')(), require(x): only where require is the loader
            marker = call.pop("js_require", None)
            if marker is not None and tampered:
                marker = None if marker == "drop" else {}
            if marker is not None:
                loader = self.loader(call["caller"], position(call), local_defs)
                if marker == "drop" and not loader:
                    continue
                if marker != "drop" and loader:
                    call.update(marker)
            calls.append(call)
        ex.calls[:] = calls
        self.commonjs_surface()
        self.function_properties(top, local_defs)
        if self.esm:
            self.reexports.append(("", "#esm", "#esm", 0, None))
        forwarded = {"ns": ("*", "="), "default": ("default", "default")}
        for name, at in self.defaults:
            self.properties.append(("value", "", name, "default", 0))
            for index in top.get(name, ()):
                ex.syms[index]["exported"] = 2
            binding = self.lookup(name, 0, at, local_defs)  # module.exports = required / export default imported
            if isinstance(binding, tuple):
                original, alias = forwarded.get(binding[0], (binding[3] if binding[0] == "member" else
                                                             self.originals.get(name, name), "default"))
                self.reexports.append((binding[1], original, alias, 0, None))
        for local, alias, line, at in self.named:
            for index in top.get(local, ()):
                ex.syms[index]["exported"] = ex.syms[index]["exported"] or 1
            binding = self.lookup(local, 0, at, local_defs)  # const find = require('./find.js'); module.exports = { find }
            if isinstance(binding, tuple):
                original = "default" if binding[0] in ("ns", "default") else binding[3] if binding[0] == "member" \
                    else self.originals.get(local, local)
                self.reexports.append((binding[1], original, alias, line, None))
            elif alias != local:
                self.reexports.append(("", local, alias, line, None))
            mode = "object" if (local, alias, line, at) in self.default_members else "named"
            if (local, alias, line, at) in self.accessors:
                self.properties.append((mode, "", "", alias, line))
            elif isinstance(binding, tuple):
                self.properties.append((mode, binding[1], original, alias, line))
            else:
                self.properties.append((mode, "", local, alias, line))
        mutated = {name for name, func, at in self.assigned}
        for mode, spec, original, alias, line in self.properties:
            if spec or original not in mutated:
                ex.imports.append((spec, f"__property__:{mode}:{original}:{alias}", line))
        for spec, original, alias, line, check in self.reexports:
            if check is None or (not tampered and self.loader(check[0], check[1], local_defs)):
                ex.imports.append((spec, f"__export__:{original}:{alias}", line))
        hidden = defaultdict(int)  # f(polish) with polish a parameter or local: no reference to a function polish (R4)
        for name, func, at, line in self.callback_args:
            if self.lookup(js_name_text(name), func, at, local_defs) == "local":
                hidden[(func, name, line)] += 1
        if hidden:
            kept = []
            for ref in ex.references:
                where = (ref["owner"], ref["name"], ref["line"])
                if ref["kind"] == "callback" and not ref["receiver"] and hidden.get(where):
                    hidden[where] -= 1
                    continue
                kept.append(ref)
            ex.references[:] = kept
        for owner, member, func, at, line in self.member_args:
            binding = self.lookup(owner, func, at, local_defs) if owner in bound and not tampered else None
            if isinstance(binding, tuple) and binding[0] in ("ns", "default", "dyn") and \
                    self.spec_key(binding[1]) not in wiped and (self.spec_key(binding[1]), member) not in replaced:
                kind = "required" if binding in self.via_require else binding[0]
                ex.references.append(dict(owner=func, name=member, receiver=f"{kind}:{binding[1]}", kind="callback", line=line))
        named = {name: b for name, b in self.names.get(0, {}).items() if b and b[0] == "named"}  # import { f } from
        if not modules and not named and not any(call["recv"] == "jsx" for call in ex.calls):
            return
        for call in ex.calls:
            receiver = call.get("js_receiver")
            if receiver is None and call["recv"] == "none" and not call["dynamic"] and call["name"] in named:
                here = self.lookup(call["name"], call["caller"], position(call), local_defs)
                if not isinstance(here, tuple):  # import { f }, but f here is a parameter, a local or a local function
                    call.update(recv="scope", recv_name="symbol" if here == "symbol" else "")
                    continue
            if call["dynamic"] or (receiver is None and call["recv"] not in ("none", "jsx")) or (tampered and receiver is None):
                continue
            name = receiver if receiver is not None else call["name"]
            if call["recv"] != "jsx" and name not in modules:
                continue
            held = self.lookup(name, call["caller"], position(call), local_defs, raw=True)  # dropped bindings included
            binding = "local" if tampered and isinstance(held, tuple) else self.lookup(name, call["caller"], position(call), local_defs)
            kind, spec = binding[:2] if isinstance(binding, tuple) else (None, None)
            if receiver is not None:
                if kind in ("ns", "default", "dyn") and self.spec_key(spec) not in wiped and (self.spec_key(spec), call["name"]) not in replaced:
                    call.update(recv="module", recv_name=f"{kind}:{spec}")
                elif isinstance(held, tuple) and call["recv"] == "class" and not (held[0] == "member" and binding == held):
                    call.update(recv="obj", recv_name=receiver)  # Service = require(...): a value, not class Service
            elif call["recv"] == "none":
                if kind in ("ns", "default"):
                    call.update(recv="module", recv_name="call:" + spec)
                elif kind == "member" and self.spec_key(spec) not in wiped and (self.spec_key(spec), binding[3]) not in replaced:
                    call.update(recv="module", recv_name=f"member:{binding[3]}:{spec}")
            elif kind in ("ns", "default"):
                call["recv_name"] = "call:" + spec
            elif kind == "member" and self.spec_key(spec) not in wiped and (self.spec_key(spec), binding[3]) not in replaced:
                call["recv_name"] = f"member:{binding[3]}:{spec}"
            elif binding == "local":
                call["recv_name"] = "local"

class JsModules:
    """JavaScript/TypeScript specifiers -> indexed files, and module/JSX calls -> symbols (used by resolve).

    Relative paths, tsconfig/jsconfig 'paths' and 'baseUrl', and workspace packages (read at index time
    into meta 'js_modules') are resolved; any other bare specifier is a package outside the project and
    stays unresolved. Re-exports are followed up to four levels.
    """
    TOP = ("function", "class", "method")

    def __init__(self, db, syms=None, by_name=None, bindings=None, find_method=None):
        self.paths = {fid: path for fid, path, lang in db.execute("SELECT id,path,lang FROM files") if fam(lang) == "js"}
        self.syms, self.by_name, self.bindings, self.find_method = syms or {}, by_name or {}, bindings or {}, find_method
        self.stems, self.cache = defaultdict(list), {}
        self.reexports, self.defaults, self.exported = defaultdict(list), defaultdict(list), set()
        self.cjs, self.known = set(), set()  # module.exports that may not run or is no function / exports listed
        self.alternatives = set()  # files whose module.exports may be one of several values
        self.properties, self.esm = defaultdict(list), set()
        self.configs, self.packages = {}, {}
        if not self.paths:
            return
        for fid, path in self.paths.items():
            self.stems[os.path.splitext(path)[0]].append(fid)
        row = db.execute("SELECT v FROM meta WHERE k='js_modules'").fetchone()
        try:
            config = json.loads(row[0]) if row else {}
        except (TypeError, ValueError):
            config = {}
        self.configs, self.packages = config.get("configs", {}), config.get("packages", {})
        if syms is None:
            return
        for fid, module, name in db.execute("SELECT file_id,module,name FROM imports WHERE substr(name,1,11)='__export__:'"):
            if fid in self.paths and name.count(":") >= 2:
                _, original, alias = name.split(":", 2)
                if original == "#esm":
                    self.esm.add(fid)
                if original in ("#esm", "#cjs"):
                    if alias in ("known", "alternatives"):  # calling the module never guesses a same-named function
                        self.cjs.add(fid)
                    if alias == "alternatives":  # several values may be the default: none is certain
                        self.alternatives.add(fid)
                    if alias in ("#esm", "known"):
                        self.known.add(fid)
                else:
                    self.reexports[fid].append((module, original, alias))
        for fid, module, name in db.execute("SELECT file_id,module,name FROM imports WHERE substr(name,1,13)='__property__:'"):
            if fid in self.paths:
                _, mode, original, alias = name.split(":", 3)
                self.properties[fid].append((mode, module, original, alias))
        for sid, fid, exported in db.execute("SELECT id,file_id,exported FROM symbols WHERE exported>0"):
            if fid in self.paths:
                self.exported.add(sid)
                if exported == 2:
                    self.defaults[fid].append(sid)

    # -- specifier -> files
    def stem(self, target):
        target = os.path.normpath(target).replace(os.sep, "/")
        if target.startswith("../") or target == ".." or target.startswith("/"):
            return ()
        if os.path.splitext(target)[1] in LANG_BY_EXT:
            target = os.path.splitext(target)[0]
        if target == ".":
            return tuple(self.stems.get("index", ()))
        return tuple(self.stems.get(target) or self.stems.get(target + "/index") or ())

    def files(self, fid, spec):
        if fid not in self.paths or not spec:
            return ()
        key = (os.path.dirname(self.paths[fid]), spec)
        if key not in self.cache:
            self.cache[key] = self._files(*key)
        return self.cache[key]

    def _files(self, folder, spec):
        if spec in (".", "..") or spec.startswith(("./", "../")):
            return self.stem(os.path.join(folder, spec))
        if spec.startswith("/"):
            return ()
        current = folder
        while current not in self.configs and current:
            current = os.path.dirname(current)
        config = self.configs.get(current)
        if config:
            best, size, star = None, -1, ""
            for pattern, targets, base in config.get("paths", []):
                prefix, wildcard, suffix = pattern.partition("*")
                if not wildcard and pattern == spec:
                    best, size, star = (targets, base), 1 << 30, ""
                elif wildcard and spec.startswith(prefix) and spec.endswith(suffix) and \
                        len(spec) >= len(prefix) + len(suffix) and len(prefix) > size:
                    best, size, star = (targets, base), len(prefix), spec[len(prefix):len(spec) - len(suffix)]
            for target in (best[0][:10] if best else []):
                hit = self.stem(os.path.join(best[1], target.replace("*", star, 1)))
                if hit:
                    return hit
            if config.get("base") is not None:
                hit = self.stem(os.path.join(config["base"], spec))
                if hit:
                    return hit
        parts = spec.split("/")
        name, rest = ("/".join(parts[:2]), parts[2:]) if spec.startswith("@") else (parts[0], parts[1:])
        package = self.packages.get(name)
        if not package:
            return ()  # a package outside the project: never guessed by name
        if rest:
            sub = "./" + "/".join(rest)
            if "exports" not in package:
                return self.entry(package["dir"], sub)
            subpaths = package["exports"]  # package.json "exports": the only subpaths Node loads
            target = subpaths.get(sub)
            if target is None:
                patterns = [(key.partition("*"), value) for key, value in subpaths.items() if key.count("*") == 1]
                best = max(((len(prefix), value.replace("*", sub[len(prefix):len(sub) - len(suffix)], 1))
                            for (prefix, _, suffix), value in patterns
                            if sub.startswith(prefix) and sub.endswith(suffix) and len(sub) >= len(prefix) + len(suffix)),
                           default=None)
                target = best[1] if best else None
            return self.entry(package["dir"], target) if target else ()
        for entry in package.get("entries", []) + ["index"]:
            hit = self.entry(package["dir"], entry)
            if hit:
                return hit
        return ()

    def entry(self, folder, path):
        """A package file; built output that is not indexed (dist/, build/, out/) is read from src/."""
        hit = self.stem(os.path.join(folder, path))
        parts = os.path.normpath(path).split("/")
        if not hit and len(parts) > 1 and parts[0] in ("dist", "build", "out"):
            hit = self.stem(os.path.join(folder, "src", *parts[1:]))
        return hit

    # -- files -> symbols
    def module_level(self, sid):
        parent = self.syms[sid][3]
        return parent is not None and self.syms[parent][2] == "module"

    def pool(self, name, kinds):
        return [s for s in self.by_name.get(name, []) if self.syms[s][0] in self.paths and self.syms[s][2] in kinds
                and self.module_level(s)]

    def match(self, fid, cands, name, module, depth=0, seen=None):
        """import_match for JS/TS: cands defined in the module, or re-exported by it."""
        files = self.files(fid, module)
        return self.within(files, cands, name, depth, set() if seen is None else seen) if files else []

    def within(self, files, cands, name, depth, seen):
        exact = [c for c in cands if self.syms[c][0] in files]
        if depth:  # reached through a re-export: Node only sees names the module exports
            exact = [c for c in exact if c in self.exported]
        if exact or depth >= 4:
            return exact
        kinds = {self.syms[c][2] for c in cands} or {"function", "class"}
        for owner in files:
            if (owner, name) in seen:
                continue
            seen.add((owner, name))
            rows = self.reexports.get(owner, ())
            explicit = [row for row in rows if row[2] == name and row[1] != "*"]
            chosen = []  # export {polish} from './b.js' wins over export *; CommonJS alternatives (exports.polish = a;
            for module, original, alias in explicit:  # if (x) exports.polish = b) give one row each: all of them count
                if original == "default" and module:
                    found = self.default_of(self.files(owner, module), depth + 1)
                elif module:
                    found = self.match(owner, cands if original == name else self.pool(original, kinds), original, module, depth + 1, seen)
                else:  # exports.polish = first: the file's own first, whatever the kind of the other polish candidates
                    found = [c for c in self.pool(original, self.TOP) if self.syms[c][0] == owner]
                chosen += [c for c in found if c not in chosen]
            if chosen:
                return chosen
            if explicit:
                continue
            found = []
            for module, original, alias in rows:
                if original == "*" and (alias == "=" or name != "default"):
                    hit = self.match(owner, cands, name, module, depth + 1, seen)
                    if hit and found and set(hit) != set(found):
                        return []  # two export * give the name: Node rejects it as ambiguous
                    found = found or hit
            if found:
                return found
        return []

    def default_of(self, files, depth=0):
        found = [s for f in files for s in self.defaults.get(f, ())]
        if found or depth >= 4:
            return found
        for owner in files:
            for module, original, alias in self.reexports.get(owner, ()):
                if module and (alias == "=" or (original, alias) == ("default", "default")):
                    found = self.default_of(self.files(owner, module), depth + 1)
                    if found:
                        return found
        return []

    def top(self, files, name, kinds=TOP):
        """Module-level symbols called name in files, exported first; a file whose exports are known (ESM, or a
        certain module.exports = ...) shows only those."""
        found = [s for s in self.by_name.get(name, []) if self.syms[s][0] in files and self.syms[s][2] in kinds
                 and self.module_level(s) and (s in self.exported or self.syms[s][0] not in self.known)]
        exported = [s for s in found if s in self.exported]
        return exported or found, bool(exported)

    def call(self, fid, caller, name, recv, rname):
        if recv == "jsx":
            return self.jsx(fid, caller, name, rname)
        if recv == "scope":  # import { f }, hidden here by a local function f (symbol) or a parameter or variable
            local = self.local_function(fid, caller, name) if rname == "symbol" else []
            return local, "high" if local else None
        return self.module_call(fid, name, rname)

    def local_function(self, fid, caller, name):
        """The nearest function or class called name in the scopes of caller, in its own file."""
        scope = caller
        while scope is not None:
            local = [s for s in self.by_name.get(name, []) if self.syms[s][0] == fid and self.syms[s][3] == scope
                     and self.syms[s][2] in ("function", "class")]
            if local:
                return local[:1]
            scope = self.syms[scope][3]
        return []

    def property_reference(self, fid, name, rname, seen=frozenset()):
        """Resolve an exported slot, never a same-named symbol or an instance method."""
        kind, _, spec = rname.partition(":")
        files = self.files(fid, spec)
        if len(files) != 1:
            return []
        owner = files[0]
        mode = "object" if owner in self.esm and kind == "default" else "named"
        if name == "default" and (owner in self.esm and kind != "default" or owner not in self.esm and kind in ("ns", "dyn")):
            mode = "value"
        key = (owner, mode, name)
        if key in seen or len(seen) >= 4:
            return []
        seen = seen | {key}
        rows = [(module, original) for scope, module, original, alias in self.properties[owner] if scope == mode and alias == name]
        if not rows and owner not in self.esm and kind in ("required", "default"):
            rows = [(module, original) for scope, module, original, alias in self.properties[owner]
                    if scope == "function" and alias == name]
        if mode == "named":
            rows += [(module, original) for module, original, alias in self.reexports[owner]
                     if module and alias == name and original != "*"]
        found = []
        if any(not original for _, original in rows):
            return []
        for module, original in rows:
            if module:
                found += self.property_reference(owner, original, "ns:" + module, seen)
            else:
                found += [s for s in self.by_name.get(original, ()) if self.syms[s][0] == owner and self.module_level(s)]
        if not rows and mode == "named":
            forwards = [(module, alias) for module, original, alias in self.reexports[owner]
                        if module and original == "*" and (alias == "=" or alias == "*" and name != "default")]
            if len(forwards) == 1:
                module, alias = forwards[0]
                found += self.property_reference(owner, name, ("required:" if alias == "=" else "ns:") + module, seen)
        return list(dict.fromkeys(found))

    def module_call(self, fid, name, rname):
        kind, _, spec = rname.partition(":")
        if kind == "member":  # const { polish: p } = require('./m'); p(): the export polish of m, as m.polish()
            name, _, spec = spec.partition(":")
            kind = "ns"
        files = self.files(fid, spec)
        if not files:
            return [], None
        if kind == "call":  # the module (or its default export) is called: p(), whatever()
            found = self.default_of(files)
            if found:
                return found[:3], "high" if len(found) == 1 and not self.alternatives & set(files) else "medium"
            if any(f in self.cjs for f in files):  # module.exports may not run, or is not a function
                return [], None
            return self.top(files, name, ("function", "class"))[0][:3], "medium"
        classes = [d for d in self.default_of(files) if self.syms[d][2] in CLASSLIKE] if kind == "default" else []
        found = [m for k in classes[:1] for m in self.find_method(k, name)]
        conf = "high" if len(classes) == 1 else "medium"  # as Class.method(): one class, its same-named methods
        if not found:
            found, exported = self.top(files, name)
            conf = "high" if exported and len(found) == 1 and kind != "default" else "medium"
        if not found:  # re-exports only: the module's own names were weighed by top()
            found = self.within(files, [c for c in self.pool(name, self.TOP) if self.syms[c][0] not in files], name, 0, set())
            conf = "high" if len(found) == 1 and kind != "default" else "medium"
        if not found and kind != "default":
            classes = [d for d in self.default_of(files) if self.syms[d][2] in CLASSLIKE]
            found = [m for k in classes[:1] for m in self.find_method(k, name)]
            conf = "high" if len(classes) == 1 else "medium"
        if not found:  # module.exports = new Service(): a method of that file, never above low
            methods = [s for s in self.by_name.get(name, []) if self.syms[s][0] in files and self.syms[s][0] not in self.known
                       and self.syms[s][2] == "method"]
            found, conf = (methods, "low") if len(methods) <= 4 else ([], None)
        if kind == "dyn" and conf == "high":
            conf = "medium"
        return found[:4], conf

    def jsx(self, fid, caller, name, rname):
        """<Badge />: React calls the component; never above medium."""
        if rname == "local":
            return [], None
        if rname.startswith(("call:", "member:")):
            found, conf = self.module_call(fid, name, rname)
            return found, "medium" if conf == "high" else conf
        defs = self.pool(name, ("function", "class"))
        local = self.local_function(fid, caller, name)
        if local:
            return local, "medium"
        binding = self.bindings.get((fid, name))
        if binding:
            original, module = binding
            return self.match(fid, self.pool(original, ("function", "class")), original, module)[:3], "medium"
        return (defs, "low") if len(defs) == 1 else ([], None)


def handle_call(extractor, n, func, cls):
    t = n.type
    if t == "new_expression":
        c = n.child_by_field_name("constructor")
        if c is not None and c.type in ("identifier", "member_expression"):
            extractor.add_call(n, func, "constructor", "new", last_seg(txt(c)))
        else:
            extractor.add_call(n, func, "", "new", dynamic=1)
        return
    fn = n.child_by_field_name("function")
    if fn is None:
        return
    fn = JsModuleFacts.comma_callee(fn) or fn  # (0, mod_1.fn)(): compiled TypeScript calls mod_1.fn
    if fn.type == "import":  # import(...): the module is loaded at run time
        extractor.add_call(n, func, "import", "dynamic_import", JsModuleFacts.literal(n) or "", dynamic=1)
        return
    if fn.type == "identifier":
        name = txt(fn)
        if name == "await" and JsModuleFacts.in_async(n):  # tree-sitter: await (0, f)(x) as await(0, f)(x)
            return
        if name == "require":  # require('./x') is an import; require(expression) is dynamic
            if JsModuleFacts.literal(n) is None:
                extractor.add_call(n, func, "require", "dynamic_import", dynamic=1)
                extractor.calls[-1]["js_require"] = "drop"  # unless require is a local name
            return
        extractor.add_call(n, func, name, "none", dynamic=int(name in extractor.js_modules.dynamic))
    elif fn.type == "member_expression":
        obj, prop = fn.child_by_field_name("object"), txt(fn.child_by_field_name("property"))
        o = txt(obj)
        required = JsModuleFacts.required(obj)
        if obj.type == "this":
            extractor.add_call(n, func, prop, "self")
        elif obj.type == "super":
            extractor.add_call(n, func, prop, "parent")
        elif required is not None:  # require('./tools').polish(), when require is the loader
            extractor.add_call(n, func, prop, "obj", last_seg(o))
            extractor.calls[-1]["js_require"] = dict(recv="module", recv_name="ns:" + required)
        elif obj.type == "identifier" and o[:1].isupper():
            extractor.add_call(n, func, prop, "class", o)
            extractor.calls[-1]["js_receiver"] = js_name(obj)
        else:
            ty = extractor.type_of(obj, func, cls)
            extractor.add_call(n, func, prop, "typed" if ty else "obj", ty or last_seg(o))
            if obj.type == "identifier" and not ty:
                extractor.calls[-1]["js_receiver"] = js_name(obj)
    elif fn.type == "super":
        extractor.add_call(n, func, "constructor", "parent")
    elif fn.type == "subscript_expression":
        extractor.add_call(n, func, "", "obj", dynamic=1)
    elif JsModuleFacts.required(fn) is not None:  # require('./cli.js')(process): the module itself is called
        extractor.add_call(n, func, "", "none", dynamic=1)
        extractor.calls[-1]["js_require"] = dict(name="require", recv="module", recv_name="call:" + JsModuleFacts.required(fn), dynamic=0)
    elif JsModuleFacts.computed_callee(fn):  # getHandler()(), (a || b)()
        extractor.add_call(n, func, "", "none", dynamic=1)
