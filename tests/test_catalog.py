"""API catalog (format codegraph-catalog/1): validation, the 1.6.4 dump, the project extension, the index hook, catalog check."""
import argparse
import contextlib
import importlib.util
import inspect
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


catalog = module('cg_catalog_under_test', REPO / 'scripts/catalog.py')
cg = module('cg_engine_for_catalog_test', REPO / 'scripts/cg.py')


def entry(**changes):
    value = dict(id='project.example.page', language='python', role='registrar',
                 origin=dict(via='instance', module='example.web', name='Router.page'), source='team wiki', checked_with='example 2.1')
    value.update(changes)
    return value


ODD_JSON = ('null', 'true', 'false', '0', '-1', '1.5', '""', '"x"', '[]', '[[]]', '{}', '{"a": {"b": [null]}}', 'NaN', '-Infinity',
            '1e999', '9' * 5000, '9' * 5000 + '.5', '[' * 900 + ']' * 900, '[' * 5000 + ']' * 5000)


def mutants(document, depth=9):
    """(path, odd value, raw JSON) with one value of the document (or the document itself) replaced by each ODD_JSON value."""
    marker = '\u0000odd'
    def paths(value, path, level):
        yield path
        if level < depth and type(value) in (dict, list):
            for k, child in (value.items() if type(value) is dict else enumerate(value)):
                yield from paths(child, path + (k,), level + 1)
    def put(value, path):
        if not path:
            return marker
        value = json.loads(json.dumps(value))
        holder = value
        for k in path[:-1]:
            holder = holder[k]
        holder[path[-1]] = marker
        return value
    for path in paths(document, (), 0):
        text = json.dumps(put(document, path))
        for odd in ODD_JSON:
            yield path, odd, text.replace(json.dumps(marker), odd)


class Temp(unittest.TestCase):
    def folder(self):
        temp = tempfile.TemporaryDirectory(prefix='cg-catalog-')
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    def project(self, data=None, raw=None):
        root = self.folder()
        if data is not None or raw is not None:
            (root / catalog.PROJECT_FILE).write_bytes(raw if raw is not None else json.dumps(data).encode())
        return root, catalog.load(root)

    def rejected(self, data=None, fragment='', raw=None):
        _, loaded = self.project(data, raw)
        self.assertEqual(len(loaded.warnings), 1, loaded.warnings)
        self.assertIn(fragment, loaded.warnings[0])
        self.assertEqual(loaded.entries, catalog.load().entries)  # never partly applied
        return loaded.warnings[0]


class BaseCatalog(Temp):
    def test_valid_sorted_and_stable(self):
        first, second = catalog.load(), catalog.load()
        self.assertEqual(first.warnings, ())
        self.assertEqual([e['id'] for e in first.entries], sorted(e['id'] for e in first.entries))
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_invalid_base_raises_naming_file_entry_and_field(self):
        folder = self.folder() / 'catalog'
        shutil.copytree(REPO / 'scripts/catalog', folder)
        data = json.loads((folder / 'python.json').read_text())
        data['entries'][3]['argument'] = 1
        (folder / 'python.json').write_text(json.dumps(data))
        with self.assertRaisesRegex(catalog.CatalogError, r"catalog/python\.json entry 3 \('.+'\): unknown field 'argument'"):
            catalog.load(folder=folder)

    def test_each_base_file_has_the_schema_of_its_name(self):
        language_doc = json.loads((REPO / 'scripts/catalog/python.json').read_text())
        for name, wrong in (('python', dict(format=catalog.FORMAT, files=[])), ('js', dict(format=catalog.FORMAT, files=[])),
                            ('php', dict(format=catalog.FORMAT, files=[])), ('files', language_doc), ('js', language_doc)):
            with self.subTest(file=name, wrong=sorted(wrong)):
                folder = self.folder() / 'catalog'
                shutil.copytree(REPO / 'scripts/catalog', folder)
                (folder / f'{name}.json').write_text(json.dumps(wrong))
                with self.assertRaisesRegex(catalog.CatalogError, rf'catalog/{name}\.json'):
                    catalog.load(folder=folder)

    def test_odd_json_types_in_a_base_file_raise_a_catalog_error(self):
        folder = self.folder() / 'catalog'
        shutil.copytree(REPO / 'scripts/catalog', folder)
        for name in ('python', 'php', 'files'):
            valid = json.loads((REPO / f'scripts/catalog/{name}.json').read_text())
            if 'entries' in valid:  # one entry per role and via: entry checks are shared with the project fuzz below
                valid['entries'] = list({(e['role'], e['origin']['via']): e for e in reversed(valid['entries'])}.values())
            for path, odd, raw in mutants(valid, depth=3):
                with self.subTest(file=name, path=path, value=odd[:12]):
                    (folder / f'{name}.json').write_text(raw)
                    catalog._BASE.pop(str(folder), None)
                    try:
                        catalog.load(folder=folder)
                    except catalog.CatalogError:
                        pass
            (folder / f'{name}.json').write_text(json.dumps(valid))

    def test_lookup_needs_the_proven_origin(self):
        loaded = catalog.load()
        self.assertIsNotNone(loaded.lookup('python', 'pure_decorator', 'import', 'functools', 'lru_cache'))
        for wrong in (('import', 'mylib', 'lru_cache'), ('builtin', '', 'lru_cache'), ('import', 'functools', 'LRU_CACHE')):
            with self.subTest(origin=wrong):
                self.assertIsNone(loaded.lookup('python', 'pure_decorator', *wrong))

    def test_php_names_are_case_insensitive(self):
        loaded = catalog.load()
        self.assertIsNotNone(loaded.lookup('php', 'callback', 'builtin', '', 'ARRAY_MAP'))
        self.assertIsNotNone(loaded.lookup('php', 'callback', 'global', 'WordPress', 'Add_Action'))


class Validation(Temp):
    def doc(self, *entries, **extra):
        return dict(format=catalog.FORMAT, entries=list(entries), **extra)

    def test_extension_is_applied_with_its_own_fingerprint(self):
        _, loaded = self.project(self.doc(entry()))
        self.assertEqual(loaded.warnings, ())
        self.assertEqual(loaded.lookup('python', 'registrar', 'instance', 'example.web', 'Router.page')['id'], 'project.example.page')
        self.assertNotEqual(loaded.fingerprint, catalog.load().fingerprint)

    def test_entry_without_origin_or_source(self):
        for field in ('origin', 'source', 'checked_with', 'id', 'role', 'language'):
            with self.subTest(field=field):
                value = entry()
                del value[field]
                self.rejected(self.doc(value), f'missing field {field!r}')

    def test_duplicates(self):
        self.rejected(self.doc(entry(), entry()), "duplicate id 'project.example.page'")
        self.rejected(raw=b'{"format": "codegraph-catalog/1", "format": "codegraph-catalog/1"}', fragment="duplicate JSON key 'format'")
        same = entry(id='project.lru', role='pure_decorator', origin=dict(via='import', module='functools', name='lru_cache'))
        self.rejected(self.doc(same), "same origin and role as 'python.functools.lru_cache.pure'")
        twice = [entry(id='project.a'), entry(id='project.b')]
        self.rejected(self.doc(*twice), "same origin and role as 'project.a'")

    def test_disable_then_replace(self):
        same = entry(id='project.lru', role='pure_decorator', origin=dict(via='import', module='functools', name='lru_cache'))
        _, loaded = self.project(self.doc(same, disable=['python.functools.lru_cache.pure']))
        self.assertEqual(loaded.warnings, ())
        self.assertEqual(loaded.lookup('python', 'pure_decorator', 'import', 'functools', 'lru_cache')['id'], 'project.lru')
        self.rejected(self.doc(disable=['python.nothing.here.pure']), "names no base entry")
        for dynamic in ('python.builtin.getattr.dynamic', 'php.builtin.eval.dynamic', 'js.builtin.eval.dynamic'):
            with self.subTest(dynamic=dynamic):
                self.rejected(self.doc(disable=[dynamic]), 'a dynamic entry cannot be disabled')

    def test_closed_keys_and_reserved_names(self):
        self.rejected(self.doc(entry(argument=1)), "unknown field 'argument'")
        self.rejected(self.doc(entry(returns='example.Page')), "reserved for a later format field 'returns'")
        self.rejected(self.doc(entry(role='return_type')), "role 'return_type' is reserved")
        self.rejected(self.doc(entry(role='dynamic_loader')), "role 'dynamic_loader' is reserved")
        self.rejected(self.doc(entry(role='registrer')), "unknown role 'registrer'")
        self.rejected(self.doc(entry(origin=dict(via='import', module='x', name='y', extra=1))), "unknown field 'extra'")
        self.rejected(dict(format=catalog.FORMAT, plugins=[]), "unknown field 'plugins'")

    def test_format_is_exact(self):
        for value in ('codegraph-catalog/2', 'codegraph-catalog/1.1', 1):
            with self.subTest(format=value):
                self.rejected(dict(format=value), 'is not supported')

    def test_project_never_matches_by_bare_name(self):
        for origin in (dict(via='builtin', name='map'), dict(via='global', provider='wordpress', name='add_action'),
                       dict(via='builtin', name='lru_cache')):
            with self.subTest(origin=origin):
                self.rejected(self.doc(entry(origin=origin)), 'cannot match by bare name')

    def test_origin_names_are_exact_dotted_names(self):
        for name in ('lru_*', 'functools.*', 're.compile(x)', 'a..b', ' page', 'page ', 'Router.', '', 'a.b.c.d.e.f.g.h.i'):
            with self.subTest(name=name):
                self.rejected(self.doc(entry(origin=dict(via='instance', module='example.web', name=name))), 'origin')
        for module_name in ('../x', 'example.', '.example', 'ex ample', 'ex*'):
            with self.subTest(module=module_name):
                self.rejected(self.doc(entry(origin=dict(via='instance', module=module_name, name='Router.page'))), 'origin module')
        self.rejected(self.doc(entry(origin=dict(via='instance', module='example', name='page'))), 'Class.member')
        self.rejected(self.doc(entry(origin=dict(via='import', name='page'))), "needs 'module'")
        self.rejected(self.doc(entry(origin=dict(via='attr', module='x', name='y'))), "unknown via 'attr'")

    def test_role_fields(self):
        callback = dict(role='callback', origin=dict(via='import', module='example.tasks', name='schedule'))
        _, loaded = self.project(self.doc(entry(args=[0, 2], kwargs=['handler'], rest_from=3, label='example', **callback)))
        self.assertEqual(loaded.warnings, ())
        self.rejected(self.doc(entry(**callback)), "needs one of args, kwargs, rest_from")
        self.rejected(self.doc(entry(args=[0])), "field 'args' does not belong to role 'registrar'")
        for args, why in (([True], 'integer'), ([1.0], 'integer'), ([64], 'integer'), ([-1], 'integer'), ([], 'non-empty'), ('0', 'non-empty')):
            with self.subTest(args=args):
                self.rejected(self.doc(entry(args=args, **callback)), why)
        self.rejected(self.doc(entry(kwargs=['not-a-name'], **callback)), 'kwargs')
        self.rejected(self.doc(entry(observation='registration.cron')), "unknown observation 'registration.cron'")

    def test_ids_and_files(self):
        for entry_id in ('example.page', 'project.Page', 'project.', 'project.a b'):
            with self.subTest(id=entry_id):
                self.rejected(self.doc(entry(id=entry_id)), 'id')
        _, loaded = self.project(dict(format=catalog.FORMAT, files=[dict(id='project.taskfile', name='Taskfile')]))
        self.assertIn('Taskfile', loaded.start_files()[0])
        for item, why in ((dict(id='project.x', name='../Procfile'), 'name'), (dict(id='project.x', name='a/b'), 'name'),
                          (dict(id='project.x', name='A', prefix='B'), 'exactly one'), (dict(id='file.x', name='A'), 'id')):
            with self.subTest(item=item):
                self.rejected(dict(format=catalog.FORMAT, files=[item]), why)

    def test_unsafe_project_files(self):
        self.rejected(raw=b'{"format": NaN}', fragment='NaN is not valid JSON')
        self.rejected(raw=b'\xff\xfe', fragment='not UTF-8')
        self.rejected(raw=b'[' * 100000, fragment='')
        self.rejected(raw=b' ' * (catalog.LIMITS['project_bytes'] + 1), fragment='more than')
        root = self.folder()
        real = root / 'real.json'
        real.write_text(json.dumps(dict(format=catalog.FORMAT)))
        os.symlink(real, root / catalog.PROJECT_FILE)
        loaded = catalog.load(root)
        self.assertIn('symbolic link', loaded.warnings[0])
        (root / catalog.PROJECT_FILE).unlink()
        (root / catalog.PROJECT_FILE).mkdir()
        self.assertTrue(catalog.load(root).warnings)


class DumpOf164Constants(unittest.TestCase):
    """The base catalog holds all and only the knowledge hardcoded in cg.py 1.6.4, frozen here as the dump's specification.
    One section per language: lanes B-py, B-js and B-php each change only their own."""
    loaded = catalog.load()

    def names(self, language, role):
        return {e['origin']['name'].rpartition('.')[2] for e in self.loaded.role(language, role)}

    def test_one_decorator_default_per_language(self):
        self.assertEqual(set(self.loaded.defaults), {'python', 'js', 'php'})

    # Python and start files (B-py: cg.py reads them from the catalog)
    def test_python_pure_decorators(self):
        self.assertEqual(self.names('python', 'pure_decorator'),
                         {'staticmethod', 'classmethod', 'property', 'cached_property', 'abstractmethod', 'lru_cache', 'cache', 'wraps',
                          'dataclass', 'contextmanager', 'asynccontextmanager', 'overload', 'override', 'final', 'total_ordering',
                          'singledispatch', 'setter', 'getter', 'deleter', 'deprecated'})

    def test_python_dynamic_functions(self):
        self.assertEqual(self.names('python', 'dynamic'), {'getattr', 'eval', 'exec', '__import__', 'import_module', 'setattr'})
        self.assertNotIn('python', cg.DYNAMIC_FUNCS)

    def test_python_registrars(self):
        registrars = self.loaded.role('python', 'registrar')
        site = {(e['origin']['module'], e['origin']['name']) for e in registrars if e.get('observation') == 'registration.site'}
        dump = {(module, f'{cls}.{method}') for module, cls in (('fastapi', 'FastAPI'), ('fastapi', 'APIRouter'), ('flask', 'Flask'))
                for method in ('route', 'get', 'post', 'put', 'delete', 'patch', 'websocket')}
        self.assertLessEqual(dump, site)  # dec-165-cpy-dump: all 21 of 1.6.4; the entries C-py adds are listed in test_catalog_python
        mcp = {(e['origin']['module'], e['origin']['name']) for e in registrars if e.get('observation') == 'registration.mcp'}
        self.assertEqual(mcp, {(module, 'FastMCP.' + method) for module in ('fastmcp', 'mcp.server.fastmcp') for method in ('tool', 'resource', 'prompt')})

    def test_python_atexit_and_the_decorator_default(self):
        self.assertIsNotNone(self.loaded.lookup('python', 'registrar', 'import', 'atexit', 'register'))
        self.assertEqual(self.loaded.lookup('python', 'callback', 'import', 'atexit', 'register')['args'], [0])
        self.assertEqual(self.loaded.defaults['python'], 'registrar')

    def test_start_files(self):
        self.assertEqual(self.loaded.start_files(), ({'Procfile', 'Dockerfile', 'Containerfile', 'Makefile', 'Justfile'}, ('Dockerfile.',)))

    def test_python_engine_reads_the_catalog(self):
        for name in ('PURE_DECORATORS', 'START_FILES'):
            self.assertFalse(hasattr(cg, name), name)
        self.assertNotIn('"fastapi", "flask"', inspect.getsource(cg.Extractor.observe_registration))

    # PHP (B-php)
    def test_php_callables(self):
        names = {'array_map', 'array_filter', 'array_walk', 'array_walk_recursive', 'array_reduce', 'usort', 'uasort', 'uksort',
                 'call_user_func', 'call_user_func_array', 'register_shutdown_function', 'spl_autoload_register', 'add_action',
                 'add_filter', 'add_shortcode', 'set_error_handler', 'set_exception_handler', 'iterator_apply'}
        self.assertEqual(self.names('php', 'callback'), names)
        first = {'array_map', 'call_user_func', 'call_user_func_array', 'register_shutdown_function',
                 'spl_autoload_register', 'set_error_handler', 'set_exception_handler'}
        for name in names:
            with self.subTest(name=name):
                position = 0 if name in first else 1
                args = ['\'data\'', '\'data\'', '\'data\'']
                args[position] = "'polish'"
                source = ('<?php function polish(){} function drive(){' + name + '(' + ','.join(args) + ');}').encode()
                ex = cg.Extractor('php', source)
                ex.run(cg.get_parser_for('php').parse(source).root_node)
                self.assertEqual([(r['name'], r['kind']) for r in ex.references], [('polish','callback')])

    def test_php_dynamic_functions_and_the_decorator_default(self):
        names = {'call_user_func', 'call_user_func_array', 'forward_static_call', 'eval'}
        self.assertEqual(self.names('php', 'dynamic'), names)
        self.assertNotIn('php', cg.DYNAMIC_FUNCS)
        for name in names - {'eval'}:  # eval is a language construct, not a function
            source = ('<?php function drive(){' + name + "('polish');}").encode()
            ex = cg.Extractor('php', source)
            ex.run(cg.get_parser_for('php').parse(source).root_node)
            self.assertTrue(any(c['name'] == name and c['dynamic'] for c in ex.calls), name)
        self.assertEqual(self.loaded.defaults['php'], 'none')

    # JavaScript and TypeScript (B-js)
    def test_js_dynamic_functions_and_the_decorator_default(self):
        names = {'eval', 'Function'}
        self.assertEqual(self.names('js', 'dynamic'), names)
        self.assertEqual(cg.DYNAMIC_FUNCS['js'], names)
        self.assertEqual(self.loaded.defaults['js'], 'none')


class IndexHook(Temp):
    def index(self, engine, root):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            engine.cmd_index(argparse.Namespace(root=str(root), full=False))
        return out.getvalue(), err.getvalue()

    def make(self):
        root = self.folder() / 'project'
        root.mkdir()
        (root / 'app.py').write_text('def helper():\n    return 1\n\ndef run():\n    return helper()\n')
        (root / 'tools.py').write_text('from app import run\n\ndef go():\n    return run()\n')
        return root

    def edges(self, engine, root):
        db = engine.connect(str(root))
        try:
            return sorted(db.execute('SELECT a.qname, b.qname, e.conf FROM edges e JOIN symbols a ON a.id=e.caller_id JOIN symbols b ON b.id=e.callee_id'))
        finally:
            db.close()

    def test_project_catalog_change_reparses_every_file(self):
        root = self.make()
        self.index(cg, root)
        self.assertIn('0 updated', self.index(cg, root)[0])
        before = self.edges(cg, root)
        (root / catalog.PROJECT_FILE).write_text(json.dumps(dict(format=catalog.FORMAT, entries=[entry()])))
        self.assertIn(catalog.PROJECT_FILE, cg.configuration_files(str(root)))  # queries see the index as stale until re-indexed
        self.assertIn('2 updated', self.index(cg, root)[0])
        self.assertEqual(self.edges(cg, root), before)  # the hook changes no result: roles are wired by lanes B
        db = cg.connect(str(root))
        meta = dict(db.execute('SELECT k,v FROM meta'))
        db.close()
        self.assertEqual(meta['catalog_fingerprint'], catalog.load(root).fingerprint)
        self.assertEqual(json.loads(meta['catalog_warnings']), [])

    def test_base_catalog_change_reparses_every_file(self):
        scripts = self.folder() / 'scripts'
        shutil.copytree(REPO / 'scripts', scripts, ignore=shutil.ignore_patterns('__pycache__'))
        engine = module('cg_engine_copy_for_catalog_test', scripts / 'cg.py')
        root = self.make()
        self.index(engine, root)
        data = json.loads((scripts / 'catalog/files.json').read_text())
        data['files'][0]['note'] = 'changed note: a byte change in a base file'
        (scripts / 'catalog/files.json').write_text(json.dumps(data))
        self.assertIn('2 updated', self.index(engine, root)[0])

    def test_rejected_project_catalog_is_reported_and_recorded(self):
        root = self.make()
        (root / catalog.PROJECT_FILE).write_text('{"format": "codegraph-catalog/1", "entries": [{"id": "x"}]}')
        out, err = self.index(cg, root)
        self.assertIn('index ok', out)
        self.assertIn('catalog warning', err)
        db = cg.connect(str(root))
        self.assertTrue(json.loads(dict(db.execute('SELECT k,v FROM meta'))['catalog_warnings']))
        db.close()


    def test_malformed_project_files_leave_the_base_and_index_runs(self):
        cases = [dict(format=catalog.FORMAT, entries=[entry(role=role)]) for role in ([], {}, ['registrar'])]
        cases += [dict(format=catalog.FORMAT, entries=[entry(origin=dict(via=via, module='example.web', name='Router.page'))])
                  for via in ([], {}, ['import'])]
        cases += [dict(format=catalog.FORMAT, files=[dict(id='project.start', name=name)]) for name in ('', None, False, 0)]
        raws = [json.dumps(case) for case in cases]
        raws += [json.dumps(dict(format=catalog.FORMAT, entries=[entry(role='callback', args=[0])])).replace('[0]', '[' + '9' * 5000 + ']'),
                 'null', '[]', '"x"', '9' * 5000, '[' * 900 + ']' * 900]
        base = catalog.load()
        for raw in raws:
            with self.subTest(project=raw[:90]):
                root = self.make()
                (root / catalog.PROJECT_FILE).write_text(raw)
                loaded = catalog.load(root)
                self.assertEqual(len(loaded.warnings), 1)
                self.assertEqual((loaded.entries, loaded.files, loaded.defaults), (base.entries, base.files, base.defaults))
                out, err = self.index(cg, root)
                self.assertIn('index ok', out)
                self.assertIn('catalog warning', err)
                db = cg.connect(str(root))
                meta = dict(db.execute('SELECT k,v FROM meta'))
                db.close()
                self.assertEqual(json.loads(meta['catalog_warnings']), list(loaded.warnings))
                self.assertEqual(meta['catalog_fingerprint'], loaded.fingerprint)

    def test_odd_json_types_anywhere_in_a_project_file_are_rejected_whole(self):
        valid = dict(format=catalog.FORMAT,
                     entries=[entry(), entry(id='project.example.defer', role='callback', origin=dict(via='import', module='example.jobs', name='defer'),
                                             args=[0], kwargs=['fn'], rest_from=1, label='job', note='n')],
                     files=[dict(id='project.taskfile', name='Taskfile'), dict(id='project.envs', prefix='Envfile.', note='n')],
                     disable=['python.functools.lru_cache.pure'])
        root, applied = self.project(valid)
        self.assertEqual(applied.warnings, ())
        base = catalog.load()
        for path, odd, raw in mutants(valid):
            with self.subTest(path=path, value=odd[:12]):
                (root / catalog.PROJECT_FILE).write_text(raw)
                loaded = catalog.load(root)
                if loaded.warnings:
                    self.assertEqual((loaded.entries, loaded.files), (base.entries, base.files))
                else:  # an odd value the format allows (note "x", files []): the document is applied whole
                    document = json.loads(raw)
                    self.assertEqual([e for e in loaded.entries if e['id'].startswith('project.')], sorted(document.get('entries', []), key=lambda e: e['id']))
                    self.assertEqual([f for f in loaded.files if f['id'].startswith('project.')], sorted(document.get('files', []), key=lambda f: f['id']))

    def test_counts_what_the_catalog_does_not_know(self):
        root = self.folder() / 'project'
        root.mkdir()
        (root / 'web.py').write_text(
            'import threading\nfrom functools import lru_cache as memo\nfrom flask import Flask\nfrom celery import Celery\n'
            'app = Flask(__name__)\ntasks = Celery("x")\n\n'
            'def deco(f):\n    return f\n\n'
            '@app.route("/")\ndef index():\n    return 1\n\n'
            '@tasks.task\ndef send():\n    return 2\n\n'
            '@memo\ndef cached():\n    return 3\n\n'
            '@deco\ndef plain():\n    return 4\n\n'
            'def start():\n    threading.Thread(target=index).start()\n\n'
            'def chained(db):\n    return db.query(index).filter(\n        index\n    ).first()\n')
        self.index(cg, root)
        result = catalog.check(str(root))
        decorators = {row['origin']: row['count'] for row in result['decorators']}
        self.assertEqual(decorators, {'celery.Celery.task': 1, 'project:deco': 1})
        callees = {row['origin'] for row in result['callback_callees']}
        self.assertIn('python:threading.Thread', callees)
        self.assertIn('python:<expr>.filter', callees)  # db.query(index).filter: a call chain is not a receiver name
        self.assertFalse([origin for origin in callees if not re.fullmatch(r'[a-z]+:[\w$<>.]+', origin)])
        self.assertEqual(result['catalog'], catalog.load(root).fingerprint)


class JsEngineReadsTheCatalog(IndexHook):
    """B-js: the JavaScript/TypeScript dynamic calls come from scripts/catalog/js.json, not from a constant of cg.py."""

    def dynamic_calls(self, engine, root):
        db = engine.connect(str(root))
        try:
            return {(name, flag) for name, flag in db.execute('SELECT name,dynamic FROM calls WHERE name!=""')}
        finally:
            db.close()

    def test_js_dynamic_names_follow_the_base_catalog(self):
        scripts = self.folder() / 'scripts'
        shutil.copytree(REPO / 'scripts', scripts, ignore=shutil.ignore_patterns('__pycache__'))
        root = self.folder() / 'project'
        root.mkdir()
        (root / 'app.js').write_text('function run(code) { eval(code); Function(code)(); setImmediate(run); }\n')
        engine = module('cg_engine_js_catalog_base', scripts / 'cg.py')
        self.index(engine, root)
        self.assertEqual(self.dynamic_calls(engine, root), {('eval', 1), ('Function', 1), ('setImmediate', 0)})
        data = json.loads((scripts / 'catalog/js.json').read_text())
        data['entries'] = [e for e in data['entries'] if e['origin']['name'] != 'Function'] + [dict(
            id='js.builtin.setimmediate.dynamic', language='js', role='dynamic', origin=dict(via='builtin', name='setImmediate'),
            source='https://nodejs.org/api/timers.html', checked_with='test only')]
        (scripts / 'catalog/js.json').write_text(json.dumps(data))
        engine = module('cg_engine_js_catalog_changed', scripts / 'cg.py')
        self.index(engine, root)
        self.assertEqual(self.dynamic_calls(engine, root), {('eval', 1), ('Function', 0), ('setImmediate', 1)})

    def test_js_dynamic_names_are_the_builtin_entries(self):
        self.assertEqual(cg.JsModuleFacts.dynamic_names(catalog.load()), {'eval', 'Function'})
        self.assertNotIn('DYNAMIC_FUNCS["js"]', inspect.getsource(cg.Extractor.handle_call))


if __name__ == '__main__':
    unittest.main()
