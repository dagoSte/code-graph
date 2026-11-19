"""Python extraction: calls and bounded interpretation of file-module loaders."""
import ast
import os

EXTENSIONS = {".py": "python"}
DEFINITIONS = {"function_definition": "function", "class_definition": "class"}
CALL_TYPES = ("call",)


class PythonFileLoads:
    """Bounded, lexical interpretation of file loaders; no project code is executed.

    Only straight-line helpers and known path operations are evaluated, with
    depth/work limits and at most 4096 characters per path expression. Calls
    inside if/for/while/with/try are read too; names bound there stay inside.
    Other writes invalidate bindings; values never invoke code.
    """
    PARAMETER, COMPOSED = 1, 2  # provenance bits on text/path values
    TOTAL = 100000  # steps per file: 20000 for the module body, then 5000 per function
    def __init__(self, source, path, root):
        self.source, self.path, self.root = source, path, os.path.abspath(root)
        self.found, self.functions, self.steps, self.limit = {}, [], 0, 20000
        self.written = {}  # ast node -> writes(node)

    def calls(self):
        try:
            tree = ast.parse(self.source)
        except (SyntaxError, ValueError, RecursionError):
            return {}
        env = {'__file__': ('text', os.path.join(self.root, self.path), False),
               'str': ('api', 'str')}
        self.block(tree.body, env, 0, True)
        # Each function gets its own small budget, so one heavy function cannot hide the rest of a large file;
        # the whole file stays bounded (TOTAL).
        total, self.limit = self.steps, 5000
        for function in list(self.functions):
            if total > self.TOTAL:
                break
            self.steps = 0
            self.invoke(function, [], {}, 0, True)
            total += self.steps
        return self.found

    @staticmethod
    def text(value):
        return value is not None and value[0] in ('text', 'path')

    def target(self, value):
        if not self.text(value) or len(value[1]) > 4096 or '\x00' in value[1]:
            return None
        path = os.path.normpath(os.path.join(self.root, value[1]))
        relative = os.path.relpath(path, self.root)
        return relative if relative != '..' and not relative.startswith('../') else None

    def expression(self, node, env, depth, record):
        self.steps += 1
        if node is None or self.steps > self.limit or depth > 12:
            return None
        evaluate = lambda child: self.expression(child, env, depth + 1, record)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return ('text', node.value, 0) if len(node.value) <= 4096 else None
        if isinstance(node, ast.Name):
            return env.get(node.id)
        if isinstance(node, ast.Attribute):
            value = evaluate(node.value)
            if value:
                if value[0] == 'api':
                    return ('api', value[1] + '.' + node.attr)
                if value[0] == 'path' and node.attr == 'parent':
                    return ('path', os.path.dirname(value[1]), value[2])
                if value[0] == 'path' and node.attr == 'parents':
                    return ('parents', value)
                if value[0] == 'spec' and node.attr == 'loader':
                    return ('loader', value)
                if value[0] in ('module', 'namespace'):
                    return ('member', value, node.attr)
                if value[0] == 'opaque':  # a method of what a loaded function returned: receiver unknown
                    return ('opaque_member', node.attr)
                if value[0] == 'loaded_result':  # Member(...).method(): a method of the loaded class, if Member is one
                    return ('loaded_method', value, node.attr)
                if value[0] == 'path' and node.attr in ('resolve', 'with_name', 'joinpath'):
                    return ('path_method', value, node.attr)
                if value[0] == 'loader' and node.attr == 'exec_module':
                    return ('execute', value[1])
            return None
        if isinstance(node, ast.BinOp):
            left, right = evaluate(node.left), evaluate(node.right)
            if self.text(left) and self.text(right):
                if len(left[1]) + len(right[1]) > 4096:
                    return None
                if isinstance(node.op, ast.Add) and left[0] == right[0] == 'text':
                    origin = left[2] | right[2]
                    return ('text', left[1] + right[1], origin | (self.COMPOSED if origin & self.PARAMETER else 0))
                if isinstance(node.op, ast.Div) and left[0] == 'path':
                    return ('path', os.path.join(left[1], right[1]), left[2] | right[2])
            return None
        if isinstance(node, ast.Subscript):
            value = evaluate(node.value)
            if value and value[0] == 'parents':  # Path(...).parents[N]
                index = node.slice.value if isinstance(node.slice, ast.Constant) else None
                if type(index) is not int or not 0 <= index < 64:
                    return None
                path = value[1][1]
                for _ in range(index + 1):
                    path = os.path.dirname(path)
                return ('path', path, value[1][2])
            key = evaluate(node.slice)
            if value and value[0] == 'namespace' and self.text(key):
                return ('member', value, key[1])
            return None
        if isinstance(node, ast.Call):
            function = evaluate(node.func)
            args = [evaluate(arg) for arg in node.args]
            kwargs = {arg.arg: evaluate(arg.value) for arg in node.keywords}
            if function is None:
                return None
            if function[0] == 'member':
                module = function[1]
                value = module[1][1] if module[0] == 'module' else module[1]
                path = self.target(value) if module[2] else None
                confidence = 'low' if self.text(value) and value[2] & self.COMPOSED else 'medium'
                if record:
                    # start and end: f()(x) and f() begin at the same column
                    self.found[(node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)] = (function[2], path, confidence)
                return ('loaded_result', path, function[2], confidence) if path else ('opaque',)
            if function[0] == 'loaded_method':
                result, method = function[1:]
                if record:  # resolved against the loaded file's class only; never by method name elsewhere
                    self.found[(node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)] = \
                        (method, result[1], result[3], result[2])
                return ('opaque',)
            if function[0] == 'opaque_member':  # declared dynamic, never guessed by name
                if record:
                    self.found[(node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)] = (function[1], None, 'low')
                return ('opaque',)
            if function[0] == 'helper':
                return self.invoke(function, args, kwargs, depth, False)
            if function[0] == 'execute':
                module = args[0] if args else kwargs.get('module')
                if module and module[0] == 'module' and module[1] is function[1]:
                    module[2] = True
                return None
            if function[0] == 'path_method':
                value, method = function[1:]
                if method == 'resolve' and not args and not kwargs:
                    return ('path', os.path.normpath(os.path.join(self.root, value[1])), value[2])
                if method in ('with_name', 'joinpath') and len(args) == 1 and self.text(args[0]):
                    if method == 'with_name' and ('/' in args[0][1] or not args[0][1]):
                        return None
                    parent = os.path.dirname(value[1]) if method == 'with_name' else value[1]
                    return ('path', os.path.join(parent, args[0][1]), value[2] | args[0][2])
            if function[0] != 'api':
                return None
            name = function[1]
            first = args[0] if args else None
            if name in ('str', 'pathlib.Path') and len(args) == 1 and self.text(first):
                return ('text' if name == 'str' else 'path', first[1], first[2])
            if name == 'importlib.util.spec_from_file_location':
                return ('spec', args[1] if len(args) > 1 else kwargs.get('location'))
            if name == 'importlib.util.module_from_spec':
                value = first or kwargs.get('spec')
                if value and value[0] == 'spec':
                    return ['module', value, False]
            if name == 'runpy.run_path':
                return ['namespace', first or kwargs.get('path_name'), True]
            return None
        if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict, ast.UnaryOp, ast.Await,
                             ast.Starred, ast.JoinedStr, ast.FormattedValue)):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    evaluate(child)
        elif isinstance(node, ast.Compare):  # a < b < c stops after a false comparison: only a and b always run
            evaluate(node.left)
            evaluate(node.comparators[0])
        elif isinstance(node, ast.BoolOp):  # only the first operand always runs
            evaluate(node.values[0])
        elif isinstance(node, ast.IfExp):
            evaluate(node.test)
        return None

    def invoke(self, function, args, kwargs, depth, record):
        node, outer = function[1:]
        if depth > 4 or node.decorator_list or None in kwargs:
            return None
        env = self.fork(outer)
        parameters = node.args.posonlyargs + node.args.args
        names = [arg.arg for arg in parameters]
        if len(args) > len(names) or set(kwargs) - set(names + [a.arg for a in node.args.kwonlyargs]):
            return None
        # Python locals shadow outer bindings even before their first assignment.
        for name in self.writes(node)[0]:
            env[name] = None
        for arg in parameters + node.args.kwonlyargs:
            env[arg.arg] = None
        if node.args.vararg:
            env[node.args.vararg.arg] = None
        if node.args.kwarg:
            env[node.args.kwarg.arg] = None
        for name, default in zip(names[len(names) - len(node.args.defaults):], node.args.defaults):
            env[name] = self.expression(default, outer, depth + 1, False)
        for name, value in list(zip(names, args)) + list(kwargs.items()):
            env[name] = (value[0], value[1], value[2] | self.PARAMETER) if self.text(value) else \
                (None if value is not None and value[0] in ('opaque', 'loaded_result') else value)
        return self.block(node.body, env, depth + 1, record)

    @staticmethod
    def fork(env):
        """A copy of env for a call or a branch. Module states (lists) are copied too, once each, so names that
        share one share its copy; with no state in env a plain copy is the same."""
        if list not in map(type, env.values()):
            return dict(env)
        copies = {}
        return {name: copies.setdefault(id(value), list(value)) if isinstance(value, list) else value
                for name, value in env.items()}

    def writes(self, node):
        """(locals, rebound) under node: the names stored or deleted, and those plus def/class names and the
        base name of attribute writes. Cached per node; a statement reuses the cache of the statements it holds,
        so each node is read once per file. One explicit stack, no recursion: an elif chain nests statements
        without limit. Each name only ever becomes None: their order is irrelevant."""
        found = self.written.get(node)
        if found is not None:
            return found
        reading, stack = [], [node]  # reading: (statement, names, rebound) not yet closed, innermost last
        while stack:
            child = stack.pop()
            if child is None:  # everything below the innermost open statement is read: cache it, pass it up
                statement, names, rebound = reading.pop()
                found = self.written[statement] = (tuple(names), tuple(rebound))
                if reading and found[1]:
                    reading[-1][1].update(dict.fromkeys(found[0]))
                    reading[-1][2].update(dict.fromkeys(found[1]))
                continue
            if child is node or isinstance(child, ast.stmt):
                inner = self.written.get(child)
                if inner is not None:
                    if inner[1]:
                        reading[-1][1].update(dict.fromkeys(inner[0]))
                        reading[-1][2].update(dict.fromkeys(inner[1]))
                    continue
                reading.append((child, {}, {}))
                stack.append(None)
            names, rebound = reading[-1][1], reading[-1][2]
            if isinstance(child, ast.Name):
                if isinstance(child.ctx, (ast.Store, ast.Del)):
                    names[child.id] = rebound[child.id] = None
                continue  # nothing below a name
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                rebound[child.name] = None
            if isinstance(child, ast.Attribute) and isinstance(child.ctx, (ast.Store, ast.Del)):
                base = child.value
                while isinstance(base, ast.Attribute):
                    base = base.value
                if isinstance(base, ast.Name):
                    rebound[base.id] = None
            stack.extend(ast.iter_child_nodes(child))
        return found

    def invalidate(self, node, env):
        for name in self.writes(node)[1]:
            env[name] = None

    def block(self, statements, env, depth, record):
        for node in statements:
            if self.steps > self.limit:
                break
            if isinstance(node, ast.Import):
                for alias in node.names:
                    env[alias.asname or alias.name.split('.')[0]] = ('api', alias.name if alias.asname else alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    env[alias.asname or alias.name] = ('api', (node.module or '') + '.' + alias.name) if not node.level else None
            elif isinstance(node, ast.FunctionDef):
                function = ('helper', node, env)
                env[node.name] = function
                if record:
                    self.functions.append(function)
            elif isinstance(node, ast.ClassDef):
                env[node.name] = None
                if record:
                    self.functions.extend(('helper', child, env) for child in node.body
                                          if isinstance(child, ast.FunctionDef))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = self.expression(node.value, env, depth, record)
                if value is not None and value[0] in ('opaque', 'loaded_result'):
                    value = None  # only a direct chain f().g() is read here; a variable keeps the extractor's own typing
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    self.invalidate(target, env)
                    if isinstance(target, ast.Name):
                        env[target.id] = value
            elif isinstance(node, ast.Expr):
                self.expression(node.value, env, depth, record)
            elif isinstance(node, (ast.Assert, ast.Raise, ast.AugAssign)):  # read calls in assert/raise/+=
                # an assert message runs only when the assertion fails: never read it as executed
                for child in (getattr(node, 'test', None), getattr(node, 'exc', None),
                              getattr(node, 'cause', None), getattr(node, 'value', None)):
                    if child is not None:
                        self.expression(child, env, depth, record)
                self.invalidate(node, env)
                if isinstance(node, ast.Raise):
                    break  # nothing after a raise runs: no state from it
            elif isinstance(node, (ast.Break, ast.Continue)):
                break
            elif isinstance(node, ast.Return):
                return self.expression(node.value, env, depth, record)
            elif isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)) \
                    or type(node).__name__ in ('TryStar', 'Match'):
                self.compound(node, env, depth, record)
            else:
                self.invalidate(node, env)
        return None

    def compound(self, node, env, depth, record):
        """Read calls inside if/for/while/with/try/match.

        A with body always runs, so it continues in env. Every other branch runs on its own copy (module
        state included): a loader executed in one branch never counts in another or after the block, and
        names bound there never leave it. An elif (an else holding only an if) nests with no indentation, so
        the chain is read by this loop, not by recursion: each if runs on its else copy, and the invalidations
        come innermost first, as nested calls would make them."""
        chain = []  # (if, env) of the elif chain read so far
        while True:
            heads = [getattr(node, 'test', None), getattr(node, 'iter', None), getattr(node, 'subject', None)]
            heads += [item.context_expr for item in getattr(node, 'items', [])]
            for head in heads:
                if head is not None:
                    self.expression(head, env, depth, record)
            if isinstance(node, (ast.With, ast.AsyncWith)):  # never part of a chain: only an if follows an if
                for item in node.items:
                    if item.optional_vars is not None:
                        self.invalidate(item.optional_vars, env)
                        if isinstance(item.optional_vars, ast.Name):
                            env[item.optional_vars.id] = None
                self.block(node.body, env, depth, record)
                return
            bodies = [(getattr(node, name, None) or [], ()) for name in ('body', 'orelse', 'finalbody')]
            bodies += [(handler.body, (handler.name,) if handler.name else ()) for handler in getattr(node, 'handlers', [])]
            bodies += [(case.body, tuple(n.name for n in ast.walk(case.pattern) if isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name)
                        + tuple(n.rest for n in ast.walk(case.pattern) if isinstance(n, ast.MatchMapping) and n.rest))
                       for case in getattr(node, 'cases', [])]
            following = None
            for body, captured in bodies:
                if not body:  # an absent else/finally runs nothing: its copy would be thrown away unread
                    continue
                inner = self.fork(env)
                for name in captured:
                    inner[name] = None
                target = getattr(node, 'target', None)
                if target is not None:
                    self.invalidate(target, inner)
                    if isinstance(target, ast.Name):
                        inner[target.id] = None
                if isinstance(node, ast.If) and body is node.orelse and len(body) == 1 and isinstance(body[0], ast.If):
                    following = inner  # the elif, read by the next turn on this copy; an if's else is its last body
                    continue
                self.block(body, inner, depth, record)
            chain.append((node, env))
            if following is None or self.steps > self.limit:  # block() checks the limit before each statement
                break
            node, env = node.orelse[0], following
        for node, env in reversed(chain):
            self.invalidate(node, env)


def handle_call(extractor, n, func, cls):
    arguments = n.child_by_field_name("arguments")
    fn = n.child_by_field_name("function")
    if fn is None:
        return
    extractor.observe_python_call(n, fn, func, cls)
    callback = extractor.catalog_callback(txt(fn), func)
    if callback is not None:  # catalog callback API (atexit.register): the functions at its positions
        children = arguments.named_children if arguments is not None else []
        positional = [a for a in children if a.type != "keyword_argument"]
        keywords = {txt(a.child_by_field_name("name")): a.child_by_field_name("value") for a in children if a.type == "keyword_argument"}
        chosen = [positional[i] for i in callback.get("args", []) if i < len(positional)]
        chosen += positional[callback["rest_from"]:] if "rest_from" in callback else []
        chosen += [keywords[k] for k in callback.get("kwargs", []) if k in keywords]
        for value in chosen:
            if value is not None and value.type in ("identifier", "attribute"):
                receiver, _, reference = txt(value).rpartition(".")
                extractor.references.append(dict(owner=func, name=reference, receiver=receiver, kind="callback", line=n.start_point[0] + 1))
    if fn.type == "identifier":
        name = txt(fn)
        first = arguments.named_children[0] if name in ("getattr", "hasattr") and arguments is not None and arguments.named_children else None
        if first is not None and extractor.binding_shadowed_local(txt(first).split(".")[0], func):
            first = None  # getattr(handlers, ...) on a parameter or local: not the imported module (R2)
        # getattr(module, ...): keep the object, so dead knows which file is reached dynamically
        extractor.add_call(n, func, name, "none", txt(first) if first is not None and first.type in ("identifier", "attribute") else "",
                      dynamic=int(name in extractor.py["dynamic_names"]))
    elif fn.type == "attribute":
        obj, attr = fn.child_by_field_name("object"), txt(fn.child_by_field_name("attribute"))
        o = txt(obj)
        if o in ("self", "cls") or extractor.self_receivers.get(func) == o:
            extractor.add_call(n, func, attr, "self")
        elif obj.type == "call" and txt(obj.child_by_field_name("function")) == "super":
            extractor.add_call(n, func, attr, "parent")
        elif obj.type == "identifier" and o[:1].isupper() and not extractor.is_shadowed(o, func):
            extractor.add_call(n, func, attr, "class", o)
        else:
            ty = extractor.type_of(obj, func, cls)
            if ty and ty.startswith("__builtin__:"):
                extractor.add_call(n, func, attr, "builtin", ty.split(":", 1)[1])
            elif ty:
                extractor.add_call(n, func, attr, "typed", ty)
            else:
                extractor.add_call(n, func, attr, "unknown_obj" if extractor.is_shadowed(o.split(".")[0], func) else "obj", o, dynamic=int(attr in extractor.py["dynamic_names"]))
    else:
        extractor.add_call(n, func, "", "none", dynamic=1)
