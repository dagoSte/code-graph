"""Python catalog wiring (B-py): decorators bound to their origin (R1), getattr receivers (R2), shadowed callback receivers (R4)."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_catalog_python_test', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
GAPS = REPO / 'tests/fixtures/gaps'
catalog_spec = importlib.util.spec_from_file_location('cg_catalog_for_python_test', REPO / 'scripts/catalog.py')
catalog = importlib.util.module_from_spec(catalog_spec)
catalog_spec.loader.exec_module(catalog)
DEAD = ('VERIFIED_DEAD', 'LIKELY_DEAD')


class Project(unittest.TestCase):
    def files(self, files):
        temp = tempfile.TemporaryDirectory(prefix='cg-catalog-py-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'project'
        for path, text in files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text)
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=False))
        return root

    def fixture(self, name):
        return self.files({str(path.relative_to(GAPS / name)): path.read_text() for path in (GAPS / name).rglob('*') if path.is_file()})

    def dead(self, root):
        db = cg.connect(str(root))
        try:
            return {db.execute('SELECT name FROM symbols WHERE id=?', (sid,)).fetchone()[0]: verdict
                    for verdict, sid, _, _ in cg.dead_candidates(db, str(root))}
        finally:
            db.close()

    def impact(self, root, target):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cg.cmd_impact(argparse.Namespace(root=str(root), target=target, min_conf='low', depth=1, file=False, json=True, limit=100))
        return {item['symbol']['name']: item['chain'][0]['confidence'] for item in json.loads(output.getvalue())['items']}


class R1DecoratorsBoundToOrigin(Project):
    def test_pure_decorators_through_aliases_and_modules_keep_dead_code_visible(self):
        for source in ('from functools import lru_cache as memoize\n@memoize()\ndef unused(): return 1\n',
                       'import functools as ft\n@ft.lru_cache\ndef unused(): return 1\n',
                       'import functools\n@functools.cache\ndef unused(): return 1\n',
                       'from functools import lru_cache\n@lru_cache()\ndef unused(): return 1\n',
                       'from contextlib import contextmanager as cm\n@cm\ndef unused(): yield 1\n'):
            with self.subTest(source=source.splitlines()[0]):
                self.assertEqual(self.dead(self.files({'lib.py': source})).get('unused'), 'VERIFIED_DEAD')

    def test_local_identity_decorators_do_not_register(self):
        for source in ('def identity(fn): return fn\n@identity\ndef unused(): return 1\n',
                       'def identity(fn):\n    """Docs."""\n    return fn\n@identity\ndef unused(): return 1\n',
                       'def factory(x):\n    def inner(fn): return fn\n    return inner\n@factory(1)\ndef unused(): return 1\n'):
            with self.subTest(source=source.splitlines()[0]):
                self.assertEqual(self.dead(self.files({'lib.py': source})).get('unused'), 'VERIFIED_DEAD')

    def test_registering_decorators_keep_their_functions(self):
        for name, function in (('P32', 'sync_all'), ('P36', 'sync_all'), ('P35', 'index_page'), ('P35c', 'index_page'), ('P12b', 'start_cmd')):
            with self.subTest(fixture=name):
                self.assertNotIn(self.dead(self.fixture(name)).get(function), DEAD)
        for source in ('REG = []\ndef reg(fn):\n    REG.append(fn)\n    return fn\n@reg\ndef used(): return 1\n',
                       'HOOKS = []\ndef lru_cache(fn):\n    HOOKS.append(fn)\n    return fn\n@lru_cache\ndef used(): return 1\n',
                       'def deco(fn):\n    print(fn)\n    return fn\n@deco\ndef used(): return 1\n',
                       'from mylib import cache\n@cache\ndef used(): return 1\n'):
            with self.subTest(source=source.splitlines()[1]):
                self.assertNotIn(self.dead(self.files({'lib.py': source})).get('used'), DEAD)

    def test_property_accessors_are_not_references(self):
        root = self.files({'shape.py': 'class Shape:\n    @property\n    def area(self): return 1\n    @area.setter\n'
                                       '    def area(self, value): pass\n    @staticmethod\n    def helper(): return 2\n'})
        db = cg.connect(str(root))
        kinds = [kind for (kind,) in db.execute("SELECT kind FROM symbol_refs WHERE kind='decorator'")]
        db.close()
        self.assertEqual(kinds, [])


REGISTRY_LIB = 'REG = []\ndef register(fn):\n    REG.append(fn)\n    return fn\ncache = register\nmemo = register\n'
REGISTER = 'REG = []\ndef register(fn):\n    REG.append(fn)\n    return fn\n'


class R1BindingAtTheDecorator(Project):
    """A decorator is pure only when the name's binding at the decorator is proven; any doubt keeps the function.
    Each source is also run by Python: when it registers `used`, the engine must keep `used` alive."""
    KEPT = {  # the read of codex-d431 (bpy-identity-d431, bpy-imports-d431), then more of the same class
        'reassigned_identity': REGISTER + 'def identity(fn): return fn\nidentity = register\n@identity\ndef used(): return 1\n',
        'decorated_identity': REGISTER + 'def make(old): return register\n@make\ndef identity(fn): return fn\n@identity\ndef used(): return 1\n',
        'parameter_shadows_identity': REGISTER + 'def identity(fn): return fn\ndef outer(identity):\n    @identity\n    def used(): return 1\nouter(register)\n',
        'from_import_rebound': 'from functools import cache as memo\nfrom registry_lib import register as memo\n@memo\ndef used(): return 1\n',
        'import_in_unrelated_function': 'from registry_lib import register as memo\ndef unrelated():\n    from functools import cache as memo\n@memo\ndef used(): return 1\n',
        'conditional_from_import': 'from functools import cache as memo\nflag = True\nif flag:\n    from registry_lib import register as memo\n@memo\ndef used(): return 1\n',
        'qualified_module_rebound': 'import functools as ft\nimport registry_lib as ft\n@ft.cache\ndef used(): return 1\n',
        'assigned_after_import': REGISTER + 'from functools import lru_cache as memo\nmemo = register\n@memo\ndef used(): return 1\n',
        'loop_rebinds': REGISTER + 'from functools import cache as memo\nfor memo in [register]:\n    pass\n@memo\ndef used(): return 1\n',
        'walrus_rebinds': REGISTER + 'from functools import cache as memo\n(memo := register)\n@memo\ndef used(): return 1\n',
        'star_import_rebinds': 'from functools import cache as memo\nfrom registry_lib import *\n@memo\ndef used(): return 1\n',
        'globals_write': REGISTER + 'from functools import cache as memo\nglobals()["memo"] = register\n@memo\ndef used(): return 1\n',
        'module_setattr': REGISTER + 'import sys\nfrom functools import cache as memo\nsetattr(sys.modules[__name__], "memo", register)\n@memo\ndef used(): return 1\n',
        'class_attribute_shadows': REGISTER + 'def identity(fn): return fn\nclass Box:\n    identity = staticmethod(register)\n    @identity\n    def used(): return 1\n',
        'builtin_rebound': REGISTER + 'staticmethod = register\nclass Box:\n    @staticmethod\n    def used(): return 1\n',
        'setter_of_a_local_class': REGISTER + 'class x:\n    setter = staticmethod(register)\n@x.setter\ndef used(): return 1\n',
        'async_identity': 'async def identity(fn): return fn\n@identity\ndef used(): return 1\n',  # `await used` gives the function back
        'globals_handed_over': REGISTER + 'from functools import cache as memo\ndef patch(ns): ns["memo"] = register\npatch(ns=globals())\n@memo\ndef used(): return 1\n',
        'vars_write': REGISTER + 'from functools import cache as memo\nvars()["memo"] = register\n@memo\ndef used(): return 1\n',
        'builtins_patched': REGISTER + 'import builtins\nbuiltins.staticmethod = register\nclass Box:\n    @staticmethod\n    def used(): return 1\n',
        # a call hands back what the callee returns (bpy-extra-done-d431): identity(register) is register
        'identity_called_with_a_registrar': REGISTER + 'def identity(fn): return fn\n@identity(register)\ndef used(): return 1\n',
        'pure_module_called_with_a_registrar': REGISTER + 'import functools\n@functools.cache(register)\ndef used(): return 1\n',
        'pure_import_called_with_a_registrar': REGISTER + 'from functools import lru_cache\n@lru_cache(register)\ndef used(): return 1\n',
        'builtin_called_with_a_registrar': REGISTER + 'class Box:\n    @staticmethod(register)\n    def used(): return 1\n',
        'fake_setter': REGISTER + 'def label(): return 1\nlabel.setter = register\n@label.setter\ndef used(): return 1\n',
        'origin_module_written': REGISTER + 'import functools\nfunctools.cache = register\nfrom functools import cache\n@cache\ndef used(): return 1\n',
        'origin_module_alias_written': REGISTER + 'import functools as ft\nft.cache = register\nfrom functools import cache\n@cache\ndef used(): return 1\n',
        'builtins_alias_written': REGISTER + 'import builtins as b\nb.staticmethod = register\nclass Box:\n    @staticmethod\n    def used(): return 1\n',
        'sys_modules_assigned_then_written': REGISTER + 'import sys\nfrom functools import cache as memo\nme = sys.modules[__name__]\nme.memo = register\n@memo\ndef used(): return 1\n',
        'sys_modules_imported_then_written': REGISTER + 'from sys import modules\nfrom functools import cache as memo\nmodules[__name__].memo = register\n@memo\ndef used(): return 1\n',
        # an f-string statement runs its expressions; only a static string is a no-op (bpy-fix-fstring-d431)
        'fstring_in_identity': REGISTER + 'def identity(fn):\n    f"{register(fn)}"\n    return fn\n@identity\ndef used(): return 1\n',
        'fstring_in_factory_inner': REGISTER + 'def factory(x):\n    def identity(fn):\n        f"{register(fn)}"\n        return fn\n    return identity\n@factory(1)\ndef used(): return 1\n',
        'concatenated_fstring_in_identity': REGISTER + 'def identity(fn):\n    "prefix" f"{register(fn)}"\n    return fn\n@identity\ndef used(): return 1\n',
        'fstring_format_spec_in_identity': REGISTER + 'def identity(fn):\n    f"{0:{len(str(register(fn)))}}"\n    return fn\n@identity\ndef used(): return 1\n',
        'tuple_statement_in_identity': REGISTER + 'def identity(fn):\n    "doc", register(fn)\n    return fn\n@identity\ndef used(): return 1\n',
        'module_form_written': REGISTER + 'import functools\nfunctools.lru_cache = register\n@functools.lru_cache\ndef used(): return 1\n',
    }
    DEAD = {
        'identity': 'def identity(fn): return fn\n@identity\ndef used(): return 1\n',
        'pure_from_import': 'from functools import cache as memo\n@memo\ndef used(): return 1\n',
        'pure_import_repeated': 'from functools import cache as memo\ndef other():\n    from functools import cache as memo\n@memo\ndef used(): return 1\n',
        'pure_module': 'import functools as ft\n@ft.cache\ndef used(): return 1\n',
        'builtin': 'class Box:\n    @staticmethod\n    def used(): return 1\n',
        'sys_modules_only_read': 'import sys\nfrom functools import cache as memo\nME = sys.modules.get(__name__)\nARGV = sys.argv\nNAMES = [ME]\n@memo\ndef used(): return 1\n',
        'true_docstring': 'def identity(fn):\n    "register(fn) is only text here"\n    return fn\n@identity\ndef used(): return 1\n',
        'local_named_builtins': 'def kinds():\n    builtins = {"dict", "list"}\n    return builtins\nclass Box:\n    @staticmethod\n    def used(): return 1\n',
        'pure_called_with_literals': 'from functools import lru_cache\n@lru_cache(maxsize=128)\ndef used(): return 1\n',
        'globals_only_read': 'import types\nfrom functools import cache as memo\nSEEN = "memo" in globals()\nCOPY = types.SimpleNamespace(**globals())\n'
                             'KEYS = list(globals().keys())\n@memo\ndef used(): return 1\n',
    }

    def registered_by_python(self, source):
        """The functions Python registers when it imports the source as module app, in a separate interpreter."""
        import subprocess, sys
        with tempfile.TemporaryDirectory(prefix='cg-binding-oracle-') as folder:
            (Path(folder) / 'app.py').write_text(source)
            (Path(folder) / 'registry_lib.py').write_text(REGISTRY_LIB)
            code = ('import json, sys; sys.path.insert(0, sys.argv[1]); import registry_lib, app\n'
                    'lists = {id(found): found for found in (getattr(app, "REG", []), registry_lib.REG)}\n'
                    'print(json.dumps([f.__name__ for found in lists.values() for f in found]))')
            done = subprocess.run([sys.executable, '-I', '-B', '-c', code, folder], capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout)

    def verdict(self, source):
        root = self.files({'app.py': source, 'registry_lib.py': REGISTRY_LIB})
        return {name: verdict for name, verdict in self.dead(root).items() if name == 'used'}.get('used')

    def test_a_doubtful_binding_keeps_the_function(self):
        for case, source in self.KEPT.items():
            with self.subTest(case=case):
                if case != 'async_identity':
                    self.assertEqual(self.registered_by_python(source), ['used'], 'the oracle: Python registers used')
                self.assertNotIn(self.verdict(source), DEAD)

    def test_a_proven_pure_binding_leaves_dead_code_visible(self):
        for case, source in self.DEAD.items():
            with self.subTest(case=case):
                self.assertEqual(self.registered_by_python(source), [])
                self.assertIn(self.verdict(source), DEAD)


class RouteObservations(Project):
    """C-py: the route decorators 1.6.4 did not know, chosen on catalog check of local corpora (head) and Flask Blueprint."""
    NEW = {('fastapi', 'FastAPI', 'app = FastAPI()'): ('head', 'options', 'trace', 'api_route', 'websocket_route'),
           ('fastapi', 'APIRouter', 'app = APIRouter()'): ('head', 'options', 'trace', 'api_route', 'websocket_route'),
           ('flask', 'Blueprint', 'app = Blueprint("shop", __name__)'): ('route', 'get', 'post', 'put', 'delete', 'patch')}

    def sites(self, source):
        root = self.files({'web.py': source})
        db = cg.connect(str(root))
        try:
            observed = sorted(db.execute("SELECT s.name, o.kind FROM observations o JOIN symbols s ON s.id=o.owner_id WHERE o.value_kind='registration'"))
            entry = sorted(db.execute("SELECT s.name FROM symbol_refs r JOIN symbols s ON s.id=r.target_id WHERE r.kind='entrypoint'"))
            return observed, entry
        finally:
            db.close()

    def routes(self, head, make, methods):
        return head + make + '\n' + ''.join(f'@app.{m}("/{m}")\ndef endpoint_{m}(): return 1\n' for m in methods)

    def test_the_new_route_methods_are_entry_points(self):
        for (module, cls, make), methods in self.NEW.items():
            with self.subTest(origin=f'{module}.{cls}'):
                expected = sorted(f'endpoint_{m}' for m in methods)
                observed, entry = self.sites(self.routes(f'from {module} import {cls}\n', make, methods))
                self.assertEqual(observed, [(name, 'registration.site') for name in expected])
                self.assertEqual(entry, [(name,) for name in expected])

    def test_the_site_entries_are_the_1_6_4_dump_plus_these(self):
        dump = {('fastapi', f'{cls}.{m}') for cls in ('FastAPI', 'APIRouter') for m in ('route', 'get', 'post', 'put', 'delete', 'patch', 'websocket')}
        dump |= {('flask', f'Flask.{m}') for m in ('route', 'get', 'post', 'put', 'delete', 'patch', 'websocket')}
        site = {(e['origin']['module'], e['origin']['name']): e for e in catalog.load().role('python', 'registrar')
                if e.get('observation') == 'registration.site'}
        added = {(module, f'{cls}.{m}') for (module, cls, _), methods in self.NEW.items() for m in methods}
        self.assertEqual(set(site), dump | added)
        for key in added:
            self.assertRegex(site[key]['checked_with'], r'^(fastapi 0\.104\.1|flask 2\.3\.3) .*hasattr')
            self.assertTrue(site[key]['source'].startswith('https://'))

    def test_homonymous_decoys_are_not_entry_points(self):
        for (module, cls, make), methods in self.NEW.items():
            for decoy in (f'from mylib import {cls}\n', f'class {cls}:\n    def __init__(self, *a): pass\n'
                          + ''.join(f'    def {m}(self, path): return lambda f: f\n' for m in methods)):
                with self.subTest(decoy=decoy.splitlines()[0], cls=cls):
                    self.assertEqual(self.sites(self.routes(decoy, make, methods))[0], [])


SYNTHETIC_FRAMEWORK = (  # registers every decorated function: the oracle for the receivers below
    'REG = []\n'
    'def _method(self, *args, **kwargs):\n'
    '    def register(fn):\n'
    '        REG.append(fn.__name__)\n'
    '        return fn\n'
    '    return register\n')


class ReceiverBinding(Project):
    """A route or tool decorator registers only on a receiver proven to be an instance of the catalog class at the decorator
    (dec-165-cpy-registrar, read of codex-d431, seq 3111). Python runs each source against synthetic frameworks."""
    CLASSES = (('fastapi', 'FastAPI', 'FastAPI()', ('get', 'post', 'head', 'api_route', 'websocket')),
               ('fastapi', 'APIRouter', 'APIRouter()', ('get', 'delete', 'options', 'trace', 'websocket_route')),
               ('flask', 'Flask', 'Flask(__name__)', ('route', 'get', 'patch')),
               ('flask', 'Blueprint', 'Blueprint("shop", __name__)', ('route', 'post', 'put')),
               ('fastmcp', 'FastMCP', 'FastMCP("x")', ('tool', 'resource', 'prompt')),
               ('mcp.server.fastmcp', 'FastMCP', 'FastMCP("x")', ('tool', 'prompt')))

    def variants(self, module, cls, make, methods):
        fake = 'class Fake:\n    def __init__(self, *a): pass\n' + ''.join(f'    def {m}(self, *a, **k): return lambda fn: fn\n' for m in methods)
        routes = lambda indent: ''.join(f'{indent}@app.{m}("/{m}")\n{indent}def endpoint_{m}(): return 1\n' for m in methods)
        top = module.split('.')[0]
        yield 'positive', f'from {module} import {cls}\napp = {make}\n' + routes('')
        yield 'module_form', f'import {module}\napp = {module}.{make}\n' + routes('')
        yield 'constructor_alias', f'from {module} import {cls} as Factory\napp = {make.replace(cls, "Factory")}\n' + routes('')
        yield 'module_alias', f'import {module} as fw\napp = fw.{make}\n' + routes('')
        yield 'annotated', f'from {module} import {cls}\napp: object = {make}\n' + routes('')
        yield 'module_named_receiver', f'from {module} import {cls}\n{top} = {make}\n' + routes('').replace('@app.', f'@{top}.')
        yield 'module_rebound', f'import {module}\nfrom {module} import {cls}\n{top} = object()\napp = {make}\n' + routes('')
        yield 'module_alias_rebound', f'import {module} as fw\nfw = object()\nfrom {module} import {cls}\napp = {make}\n' + routes('')
        yield 'exec_homonym', f'from {module} import {cls}\nfrom types import SimpleNamespace\nfor exec in [SimpleNamespace(phase=1)]:\n    state = exec.phase\napp = {make}\n' + routes('')
        yield 'eval_homonym', f'from {module} import {cls}\nfor eval in [object()]:\n    pass\napp = {make}\n' + routes('')
        yield 'exec_function_homonym', f'from {module} import {cls}\ndef exec(source): return None\nexec("text")\napp = {make}\n' + routes('')
        yield 'eval_lambda_homonym', f'from {module} import {cls}\neval = lambda source: 1\neval("text")\napp = {make}\n' + routes('')
        yield 'unrelated_parameter', f'from {module} import {cls}\napp = {make}\ndef other(app: {cls}): return app\n' + routes('')
        yield 'unrelated_local', f'from {module} import {cls}\n{fake}app = {make}\ndef other():\n    app = Fake()\n    return app\nother()\n' + routes('')
        yield 'passed_receiver', f'from {module} import {cls}\napp = {make}\ndef read(value): return value\nread(app)\n' + routes('')
        writes = ''.join(f'    value.{m} = Fake().{m}\n' for m in methods)
        yield 'mutating_argument', f'from {module} import {cls}\n{fake}app = {make}\ndef change(value):\n' + writes + 'change(app)\n' + routes('')
        yield 'mutating_keyword', f'from {module} import {cls}\n{fake}app = {make}\ndef change(value):\n' + writes + 'change(value=app)\n' + routes('')
        yield 'mutating_alias_argument', f'from {module} import {cls}\n{fake}app = {make}\ndef change(value):\n    other = value\n' + ''.join(f'    other.{m} = Fake().{m}\n' for m in methods) + 'change(app)\n' + routes('')
        lambda_writes = ', '.join(f'setattr(value, "{m}", Fake().{m})' for m in methods)
        yield 'mutating_lambda', f'from {module} import {cls}\n{fake}app = {make}\nchange = lambda value: ({lambda_writes})\nchange(app)\n' + routes('')
        yield 'passed_lambda', f'from {module} import {cls}\napp = {make}\nread = lambda value: value\nread(app)\n' + routes('')
        yield 'returned_alias', f'from {module} import {cls}\n{fake}app = {make}\ndef read(value): return value\nother = read(app)\n' + ''.join(f'other.{m} = Fake().{m}\n' for m in methods) + routes('')
        yield 'global_read', f'from {module} import {cls}\napp = {make}\ndef other():\n    global app\n    return app\nother()\n' + routes('')
        yield 'local_receiver', f'from {module} import {cls}\ndef build():\n    app = {make}\n' + routes('    ') + '    return 1\nbuild()\n'
        yield 'enclosing_receiver', f'from {module} import {cls}\ndef outer():\n    app = {make}\n    def build():\n' + routes('        ') + '        return 1\n    build()\nouter()\n'
        yield 'parameter_receiver', f'from {module} import {cls}\napp = {make}\n{fake}def build(app):\n' + routes('    ') + '    return 1\nbuild(Fake())\n'
        yield 'constructor_parameter', f'from {module} import {cls}\n{fake}def build({cls}):\n    app = {make}\n' + routes('    ') + '    return 1\nbuild(Fake)\n'
        yield 'global_write', f'from {module} import {cls}\n{fake}app = {make}\ndef change():\n    global app\n    app = Fake()\nchange()\n' + routes('')
        yield 'nonlocal_write', f'from {module} import {cls}\n{fake}def outer():\n    app = {make}\n    def change():\n        nonlocal app\n        app = Fake()\n    change()\n' + routes('    ') + '    return 1\nouter()\n'
        yield 'construction_after_decorator', f'from {module} import {cls}\ntry:\n' + routes('    ') + f'except NameError: pass\napp = {make}\n'
        yield 'local_construction_after_decorator', f'from {module} import {cls}\ndef build():\n    try:\n' + routes('        ') + f'    except NameError: pass\n    app = {make}\n    return 1\nbuild()\n'
        yield 'constructor_import_after_use', f'try:\n    app = {make}\nexcept NameError: pass\nfrom {module} import {cls}\ntry:\n' + routes('    ') + 'except NameError: pass\n'
        yield 'local_class_redefines', f'from {module} import {cls}\n' + fake.replace('class Fake', f'class {cls}') + f'app = {make}\n' + routes('')
        yield 'reassigned', f'from {module} import {cls}\n{fake}app = {make}\napp = Fake()\n' + routes('')
        yield 'module_written', f'import {top}\nimport {module}\n{fake}{module}.{cls} = Fake\nfrom {module} import {cls}\napp = {make}\n' + routes('')
        yield 'constructor_reassigned', f'from {module} import {cls}\n{fake}{cls} = Fake\napp = {make}\n' + routes('')
        yield 'receiver_written', f'from {module} import {cls}\n{fake}app = {make}\n' + ''.join(f'app.{m} = Fake().{m}\n' for m in methods) + routes('')
        yield 'receiver_escaped', f'from {module} import {cls}\n{fake}app = {make}\nother = app\n' + ''.join(f'other.{m} = Fake().{m}\n' for m in methods) + routes('')
        path = module[len(top):] + '.' + cls
        yield 'parent_module_written', f'import {module}\nimport {top} as parent\n{fake}parent{path} = Fake\nfrom {module} import {cls}\napp = {make}\n' + routes('')
        yield 'exec_receiver', f'from {module} import {cls}\n{fake}app = {make}\nexec("app = Fake()")\n' + routes('')
        yield 'exec_alias', f'from {module} import {cls}\n{fake}app = {make}\nexecute = exec\nexecute("app = Fake()")\n' + routes('')
        yield 'exec_call', f'from {module} import {cls}\n{fake}app = {make}\nexec.__call__("app = Fake()")\n' + routes('')
        yield 'exec_getattribute', f'from {module} import {cls}\n{fake}app = {make}\nexec.__getattribute__("__call__")("app = Fake()")\n' + routes('')
        yield 'exec_builtins', f'from {module} import {cls}\n{fake}app = {make}\nexecute = exec.__self__.exec\nexecute("app = Fake()")\n' + routes('')
        yield 'eval_receiver', f'from {module} import {cls}\n{fake}app = {make}\neval("(app := Fake())")\n' + routes('')
        yield 'qualified_exec', f'from {module} import {cls}\nimport builtins as b\n{fake}app = {make}\nb.exec("app = Fake()")\n' + routes('')
        yield 'qualified_eval', f'from {module} import {cls}\nimport builtins as b\n{fake}app = {make}\nb.eval("(app := Fake())")\n' + routes('')
        yield 'namespace_receiver', f'from {module} import {cls}\n{fake}app = {make}\nglobals()["app"] = Fake()\n' + routes('')
        yield 'setattr_constructor', f'import {module} as fw\n{fake}setattr(fw, "{cls}", Fake)\nfrom {module} import {cls}\napp = {make}\n' + routes('')

    def registered_by_python(self, source):
        import subprocess, sys
        with tempfile.TemporaryDirectory(prefix='cg-receiver-oracle-') as folder:
            for module, cls, _, methods in self.CLASSES:
                package = Path(folder, *module.split('.'))
                package.mkdir(parents=True, exist_ok=True)
                for parent in package.relative_to(folder).parents:
                    (Path(folder) / parent / '__init__.py').touch()
                init = package / '__init__.py'
                body = init.read_text() if init.exists() and init.read_text() else SYNTHETIC_FRAMEWORK
                methods_all = ('route', 'get', 'post', 'put', 'delete', 'patch', 'head', 'options', 'trace', 'api_route',
                               'websocket', 'websocket_route', 'tool', 'resource', 'prompt')
                body += f'class {cls}:\n    def __init__(self, *a, **k): pass\n' + ''.join(f'    {m} = _method\n' for m in methods_all)
                init.write_text(body)
            (Path(folder) / 'web.py').write_text(source)
            code = ('import json, sys; sys.path.insert(0, sys.argv[1]); import importlib\n'
                    'import web\n'
                    'mods = [importlib.import_module(m) for m in ("fastapi", "flask", "fastmcp", "mcp.server.fastmcp")]\n'
                    'print(json.dumps(sorted(name for m in mods for name in m.REG)))')
            done = subprocess.run([sys.executable, '-I', '-B', '-c', code, folder], capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout)

    def observed(self, source):
        root = self.files({'web.py': source})
        db = cg.connect(str(root))
        try:
            names = sorted(name for (name,) in db.execute("SELECT s.name FROM observations o JOIN symbols s ON s.id=o.owner_id WHERE o.value_kind='registration'"))
            entry = sorted(name for (name,) in db.execute("SELECT s.name FROM symbol_refs r JOIN symbols s ON s.id=r.target_id WHERE r.kind='entrypoint'"))
            return names, entry
        finally:
            db.close()

    def test_an_entry_point_only_where_python_registers(self):
        for module, cls, make, methods in self.CLASSES:
            for variant, source in self.variants(module, cls, make, methods):
                with self.subTest(origin=f'{module}.{cls}', variant=variant):
                    registered = self.registered_by_python(source)
                    expected = sorted(f'endpoint_{m}' for m in methods) if variant in (
                        'positive', 'module_form', 'constructor_alias', 'module_alias', 'annotated', 'module_named_receiver',
                        'module_rebound', 'module_alias_rebound', 'exec_homonym', 'eval_homonym', 'exec_function_homonym',
                        'eval_lambda_homonym', 'unrelated_parameter',
                        'unrelated_local', 'passed_receiver', 'passed_lambda', 'global_read', 'local_receiver', 'enclosing_receiver') else []
                    self.assertEqual(registered, expected, 'the oracle')
                    self.assertEqual(self.observed(source), (registered, registered))

    def test_repeated_receiver_bindings_leave_the_origin_unproven(self):
        for module, cls, make, methods in self.CLASSES:
            for scope in ('module', 'class'):
                with self.subTest(origin=f'{module}.{cls}', scope=scope):
                    source = f'from {module} import {cls}\napp = {make}\napp = {make}\n' + ''.join(
                        f'@app.{m}("/{m}")\ndef endpoint_{m}(): return 1\n' for m in methods)
                    if scope == 'class':
                        source = f'from {module} import {cls}\nclass Handlers:\n    app = {make}\n' + ''.join(
                            f'    @app.{m}("/{m}")\n    def endpoint_{m}(): return 1\n' for m in methods) + '    app = object()\n'
                    self.assertEqual(self.registered_by_python(source), sorted(f'endpoint_{m}' for m in methods))
                    self.assertEqual(self.observed(source), ([], []))

    def test_class_body_names_follow_execution_order(self):
        for module, cls, make, methods in self.CLASSES:
            header = f'from {module} import {cls}\n'
            fake = 'class Fake:\n' + ''.join(f'    def {m}(self, *a, **k): return lambda fn: fn\n' for m in methods)
            routes = lambda indent: ''.join(f'{indent}@app.{m}("/{m}")\n{indent}def endpoint_{m}(): return 1\n' for m in methods)
            cases = [
                ('global_before_local', header+f'app = {make}\nclass Handlers:\n'+routes('    ')+'    app = object()\n', True),
                ('constructor_before_local', header+f'class Handlers:\n    app = {make}\n'+routes('    ')+f'    {cls} = object()\n', True),
                ('nested_class', header+f'app = {make}\nclass Outer:\n    app = object()\n    class Handlers:\n'+routes('        ')+'        app = object()\n', True),
                ('enclosing_function', header+f'def build():\n    app = {make}\n    class Handlers:\n'+routes('        ')+'build()\n', True),
                ('class_fallback_skips_enclosing_local', header+f'app = {make}\ndef build():\n    app = object()\n    class Handlers:\n'+routes('        ')+'        app = object()\nbuild()\n', True),
                ('annotation_only', header+f'app = {make}\nclass Handlers:\n    app: object\n'+routes('    ')+'    app = object()\n', True),
                ('deleted_class_name', header+f'app = {make}\nclass Handlers:\n    app = object()\n    del app\n'+routes('    ')+'    app = object()\n', True),
                ('global_route_before_class_mutation', header+fake+f'app = {make}\n'+routes('')+'class Handlers:\n'
                    +''.join(f'    app.{m} = Fake().{m}\n' for m in methods)+'    app = object()\n', True),
                ('conditional_class_binding', header+fake+f'app = Fake()\nclass Handlers:\n    if False:\n        app = {make}\n'+routes('    ')+'    app = object()\n', False),
                ('class_binding_shadows_global', header+fake+f'app = {make}\nclass Handlers:\n    app = Fake()\n'+routes('    ')+'    app = object()\n', False),
                ('class_mutates_global_before_local', header+fake+f'app = {make}\nclass Handlers:\n'
                    +''.join(f'    app.{m} = Fake().{m}\n' for m in methods)+routes('    ')+'    app = object()\n', False),
                ('class_global_write', header+fake+f'app = {make}\nclass Handlers:\n    global app\n    app = Fake()\n'+routes('    '), False),
            ]
            for variant, source, registers in cases:
                with self.subTest(origin=f'{module}.{cls}', variant=variant):
                    expected = sorted(f'endpoint_{m}' for m in methods) if registers else []
                    self.assertEqual(self.registered_by_python(source), expected, 'the oracle')
                    self.assertEqual(self.observed(source), (expected, expected))

    def test_local_exec_and_eval_parameters_do_not_hide_registrations(self):
        for module, cls, make, methods in self.CLASSES:
            header = f'from {module} import {cls}\napp = {make}\n'
            fake = 'class Fake:\n' + ''.join(f'    def {m}(self, *a, **k): return lambda fn: fn\n' for m in methods)
            routes = ''.join(f'@app.{m}("/{m}")\ndef endpoint_{m}(): return 1\n' for m in methods)
            cases = [
                ('exec_parameter', 'def unrelated(exec): exec("text")\nunrelated(lambda source: None)\n', True),
                ('eval_parameter', 'def unrelated(eval): eval("text")\nunrelated(lambda source: 1)\n', True),
                ('typed_parameter', 'def unrelated(exec: object): exec("text")\nunrelated(lambda source: None)\n', True),
                ('default_lambda', 'def unrelated(eval=lambda source: 1): eval("text")\nunrelated()\n', True),
                ('keyword_parameter', 'def unrelated(*, exec): exec("text")\nunrelated(exec=lambda source: None)\n', True),
                ('nested_parameter', 'def outer():\n    def unrelated(exec): exec("text")\n    unrelated(lambda source: None)\nouter()\n', True),
                ('builtin_argument', 'def unrelated(exec): exec("global app; app = Fake()")\nunrelated(exec)\n', False),
                ('builtin_keyword', 'def unrelated(*, exec): exec("global app; app = Fake()")\nunrelated(exec=exec)\n', False),
                ('builtin_default', 'def unrelated(exec=exec): exec("global app; app = Fake()")\nunrelated()\n', False),
                ('qualified_builtin_argument', 'import builtins as b\ndef unrelated(exec): exec("global app; app = Fake()")\nunrelated(b.exec)\n', False),
            ]
            for variant, body, registers in cases:
                with self.subTest(origin=f'{module}.{cls}', variant=variant):
                    source = header+fake+body+routes
                    expected = sorted(f'endpoint_{m}' for m in methods) if registers else []
                    self.assertEqual(self.registered_by_python(source), expected, 'the oracle')
                    self.assertEqual(self.observed(source), (expected, expected))


class R2GetattrReceivers(Project):
    def test_a_local_name_does_not_reach_the_imported_module(self):
        for app in ('import handlers\ndef drive(handlers, event): return getattr(handlers, event)()\n',
                    'import handlers\ndef drive(event):\n    handlers = object()\n    return getattr(handlers, event)()\n',
                    'import handlers\ndef drive(items, event):\n    for handlers in items:\n        getattr(handlers, event)()\n',
                    'import handlers\ndef outer():\n    def inner(handlers, event): return getattr(handlers, event)()\n    return inner\n'):
            with self.subTest(app=app.splitlines()[1]):
                self.assertEqual(self.dead(self.files({'app.py': app, 'handlers.py': 'def unused(): return 1\n'})).get('unused'), 'VERIFIED_DEAD')

    def test_the_imported_module_is_still_reached(self):
        verdicts = self.dead(self.files({'app.py': 'import handlers\ndef drive(event): return getattr(handlers, event)()\n',
                                         'handlers.py': 'def handler(): return 1\n', 'other.py': 'def unrelated_unused(): return 2\n'}))
        self.assertNotIn(verdicts.get('handler'), DEAD)
        self.assertEqual(verdicts.get('unrelated_unused'), 'VERIFIED_DEAD')


class R4ShadowedCallbackReceivers(Project):
    def test_a_parameter_named_like_the_module_is_not_the_module(self):
        for app in ('import tools\ndef drive(tools): return consume(tools.polish)\n',
                    'import tools\ndef drive():\n    tools = make()\n    return consume(tools.polish)\n',
                    'import tools\ndef drive(items):\n    for tools in items:\n        consume(tools.polish)\n'):
            with self.subTest(app=app.splitlines()[1]):
                self.assertEqual(self.impact(self.files({'app.py': app, 'tools.py': 'def polish(): return 1\n'}), 'tools.py:polish'), {})

    def test_the_module_and_self_still_produce_references(self):
        root = self.files({'app.py': 'import tools\ndef drive(): return consume(tools.polish)\n', 'tools.py': 'def polish(): return 1\n'})
        self.assertEqual(self.impact(root, 'tools.py:polish'), {'drive': 'medium'})
        root = self.files({'svc.py': 'class Svc:\n    def fmt(self, x): return x\n    def all(self, xs): return list(map(self.fmt, xs))\n'})
        self.assertEqual(self.impact(root, 'svc.py:Svc.fmt'), {'Svc.all': 'medium'})


class CatalogCommand(Project):
    def test_cg_catalog_check_runs_the_catalog_tool(self):
        root = self.files({'web.py': 'from celery import Celery\ntasks = Celery("x")\n@tasks.task\ndef send(): return 1\n'})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cg.cmd_catalog(argparse.Namespace(root=str(root), catalog_command='check', json=True, limit=10))
        self.assertIn({'origin': 'celery.Celery.task', 'count': 1}, json.loads(output.getvalue())['decorators'])


if __name__ == '__main__':
    unittest.main()
