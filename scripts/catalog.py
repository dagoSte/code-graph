"""code-graph API catalog (format codegraph-catalog/1): what languages and frameworks say about callbacks,
registering decorators, pure decorators, dynamic calls and start files, kept as data.

Base: scripts/catalog/{python,js,php}.json and files.json. Project extension: <root>/.code-graph-catalog.json.
Standard library only, no network: the same files always give the same catalog and fingerprint.
Contract and rules: docs/CATALOG.md.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys

HERE = Path(__file__).with_name('catalog')
PROJECT_FILE = '.code-graph-catalog.json'
SCHEMA = json.loads((HERE / 'schema.json').read_text(encoding='utf-8'))
FORMAT = SCHEMA['format']
LIMITS = SCHEMA['limits']
IDENT = {'python': r'[A-Za-z_][A-Za-z0-9_]*', 'js': r'[A-Za-z_$][A-Za-z0-9_$]*', 'php': r'[A-Za-z_][A-Za-z0-9_]*'}
MODULE = {'python': r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*',
          'js': r'(@[a-z0-9._-]+/)?[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*|node:[a-z0-9_/]+',
          'php': r'[A-Za-z_][A-Za-z0-9_]*(\\[A-Za-z_][A-Za-z0-9_]*)*'}
ENTRY_ID = r'[a-z0-9]([a-z0-9._-]{0,158}[a-z0-9])?'
FILE_NAME = r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}'


class CatalogError(ValueError):
    """An invalid catalog file: the message names the file, the entry and the field."""


def _parse(raw, label):
    def pairs(items):
        keys = [key for key, _ in items]
        if len(keys) != len(set(keys)):
            raise CatalogError(f'{label}: duplicate JSON key {sorted(k for k in keys if keys.count(k) > 1)[0]!r}')
        return dict(items)

    def constant(value):
        raise CatalogError(f'{label}: {value} is not valid JSON here')

    def integer(text):
        if len(text) > 20:  # the format's integers are argument positions; Python refuses ints over 4300 digits
            raise CatalogError(f'{label}: an integer of {len(text)} digits is not valid here')
        return int(text)

    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant, parse_int=integer)
    except UnicodeDecodeError as error:
        raise CatalogError(f'{label}: not UTF-8 ({error.reason})') from None
    except RecursionError:
        raise CatalogError(f'{label}: nested too deeply') from None
    except json.JSONDecodeError as error:
        raise CatalogError(f'{label}: invalid JSON at line {error.lineno}: {error.msg}') from None


def _keys(value, label, required, optional=()):
    if type(value) is not dict:
        raise CatalogError(f'{label}: expected an object')
    missing = [key for key in required if key not in value]
    unknown = sorted(set(value) - set(required) - set(optional))
    if missing:
        raise CatalogError(f'{label}: missing field {missing[0]!r}')
    if unknown:
        reserved = 'reserved for a later format' if unknown[0] in SCHEMA['reserved']['fields'] else 'unknown'
        raise CatalogError(f'{label}: {reserved} field {unknown[0]!r}; allowed: {", ".join(sorted(set(required) | set(optional)))}')


def _text(value, label, pattern=None):
    if type(value) is not str or not value.strip() or len(value) > LIMITS['text_chars']:
        raise CatalogError(f'{label}: expected non-empty text of at most {LIMITS["text_chars"]} characters')
    if pattern is not None and not re.fullmatch(pattern, value):
        raise CatalogError(f'{label}: {value!r} is not allowed here')
    return value


def _index(value, label):
    if type(value) is not int or not 0 <= value <= LIMITS['arg_index']:
        raise CatalogError(f'{label}: expected an integer from 0 to {LIMITS["arg_index"]}')
    return value


def _entry(entry, label, scope, language=None):
    if type(entry) is dict and type(entry.get('id')) is str:
        label = f'{label} ({entry["id"]!r})'
    _keys(entry, label, SCHEMA['entry_fields']['required'],
          SCHEMA['entry_fields']['optional'] + [field for spec in SCHEMA['roles'].values() for field in spec['fields']])
    _text(entry['id'], label + ' id', ENTRY_ID)
    if scope == 'project' and not entry['id'].startswith('project.'):
        raise CatalogError(f'{label}: a project entry id starts with "project."')
    lang = entry['language']
    if lang not in SCHEMA['languages'] or (language is not None and lang != language):
        raise CatalogError(f'{label}: language must be {language or " or ".join(SCHEMA["languages"])}')
    role = _text(entry['role'], label + ' role')
    if role in SCHEMA['reserved']['roles']:
        raise CatalogError(f'{label}: role {role!r} is reserved for a later format')
    if role not in SCHEMA['roles']:
        raise CatalogError(f'{label}: unknown role {role!r}; allowed: {", ".join(SCHEMA["roles"])}')
    spec = SCHEMA['roles'][role]
    extra = [field for spec_ in SCHEMA['roles'].values() for field in spec_['fields'] if field in entry and field not in spec['fields']]
    if extra:
        raise CatalogError(f'{label}: field {extra[0]!r} does not belong to role {role!r}')
    if spec['one_of'] and not any(field in entry for field in spec['one_of']):
        raise CatalogError(f'{label}: role {role!r} needs one of {", ".join(spec["one_of"])}')
    origin = entry['origin']
    _keys(origin, label + ' origin', ['via', 'name'], ['module', 'provider'])
    via = SCHEMA['via'].get(_text(origin['via'], label + ' origin via'))
    if via is None:
        raise CatalogError(f'{label} origin: unknown via {origin["via"]!r}; allowed: {", ".join(SCHEMA["via"])}')
    if scope == 'project' and not via['project']:
        raise CatalogError(f'{label} origin: a project entry cannot match by bare name (via {origin["via"]!r})')
    for field in ('module', 'provider'):
        if via[field] != (field in origin):
            raise CatalogError(f'{label} origin: via {origin["via"]!r} {"needs" if via[field] else "takes no"} {field!r}')
    segments = _text(origin['name'], label + ' origin name').split('.')
    if len(segments) > LIMITS['name_segments'] or not all(re.fullmatch(IDENT[lang], s) and len(s) <= LIMITS['segment_chars'] for s in segments):
        raise CatalogError(f'{label} origin name: {origin["name"]!r} is not a dotted {lang} name')
    if origin['via'] == 'instance' and len(segments) < 2:
        raise CatalogError(f'{label} origin name: via "instance" names Class.member')
    if 'module' in origin:
        _text(origin['module'], label + ' origin module', MODULE[lang])
    if 'provider' in origin:
        _text(origin['provider'], label + ' origin provider', r'[a-z0-9][a-z0-9_-]{0,63}')
    if 'observation' in entry and _text(entry['observation'], label + ' observation') not in SCHEMA['observations']:
        raise CatalogError(f'{label}: unknown observation {entry["observation"]!r}')
    if 'args' in entry and (type(entry['args']) is not list or not entry['args']):
        raise CatalogError(f'{label}: args is a non-empty list of argument positions')
    for position in entry.get('args', []):
        _index(position, label + ' args')
    if 'kwargs' in entry and (type(entry['kwargs']) is not list or not entry['kwargs']):
        raise CatalogError(f'{label}: kwargs is a non-empty list of parameter names')
    for name in entry.get('kwargs', []):
        _text(name, label + ' kwargs', IDENT[lang])
    if 'rest_from' in entry:
        _index(entry['rest_from'], label + ' rest_from')
    for field in ('label', 'note'):
        if field in entry:
            _text(entry[field], f'{label} {field}')
    _text(entry['source'], label + ' source', r'https://\S+' if scope == 'base' else None)
    _text(entry['checked_with'], label + ' checked_with')
    return entry


def key(entry):
    """Lookup key (language, role, via, module, name); PHP names are case-insensitive."""
    origin = entry['origin']
    parts = (origin.get('module') or origin.get('provider') or '', origin['name'])
    if entry['language'] == 'php':
        parts = tuple(part.lower() for part in parts)
    return (entry['language'], entry['role'], origin['via']) + parts


def _file_entry(item, label, scope):
    _keys(item, label, ['id'], ['name', 'prefix', 'note'])
    _text(item['id'], label + ' id', (r'project\.' if scope == 'project' else r'file\.') + ENTRY_ID)
    if ('name' in item) == ('prefix' in item):
        raise CatalogError(f'{label}: give exactly one of name or prefix')
    field = 'name' if 'name' in item else 'prefix'
    _text(item[field], f'{label} {field}', FILE_NAME)
    if 'note' in item:
        _text(item['note'], label + ' note')
    return item


def _document(data, label, scope, language=None):
    """A project file, a base language file (language given) or the base files.json (language None)."""
    if scope == 'project':
        _keys(data, label, ['format'], ['entries', 'files', 'disable'])
    elif language is None:
        _keys(data, label, ['format', 'files'])
    else:
        _keys(data, label, ['format', 'language', 'unrecognized_decorator', 'entries'])
        if data['language'] != language:
            raise CatalogError(f'{label}: language must be {language!r}')
    if data['format'] != FORMAT:
        shown = repr(data['format'][:60]) if type(data['format']) is str else 'a JSON ' + type(data['format']).__name__
        raise CatalogError(f'{label}: format {shown} is not supported; this engine reads {FORMAT!r}')
    if 'unrecognized_decorator' in data and data['unrecognized_decorator'] not in SCHEMA['unrecognized_decorator']:
        raise CatalogError(f'{label}: unrecognized_decorator must be one of {", ".join(SCHEMA["unrecognized_decorator"])}')
    for field in ('entries', 'files', 'disable'):
        if field in data and (type(data[field]) is not list or len(data[field]) > LIMITS['entries']):
            raise CatalogError(f'{label}: {field} is a list of at most {LIMITS["entries"]} items')
    entries = [_entry(item, f'{label} entry {number}', scope, language) for number, item in enumerate(data.get('entries', []))]
    files = [_file_entry(item, f'{label} file {number}', scope) for number, item in enumerate(data.get('files', []))]
    disable = [_text(item, f'{label} disable', ENTRY_ID) for item in data.get('disable', [])]
    return entries, files, disable


def _read_project(root):
    """The project file through one no-follow open: a regular file inside root, size checked before reading."""
    path = Path(root) / PROJECT_FILE
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CatalogError(f'{PROJECT_FILE}: cannot be opened ({error.strerror}); a symbolic link is not accepted') from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise CatalogError(f'{PROJECT_FILE}: not a regular file')
    with os.fdopen(fd, 'rb') as handle:
        if info.st_size > LIMITS['project_bytes']:
            raise CatalogError(f'{PROJECT_FILE}: {info.st_size} bytes, more than {LIMITS["project_bytes"]}')
        return handle.read(LIMITS['project_bytes'] + 1)


class Catalog:
    """The effective catalog: base entries plus the project's, minus the ones the project disables."""

    def __init__(self, entries, files, defaults, warnings, project_status):
        self.entries = tuple(sorted(entries, key=lambda e: e['id']))
        self.files = tuple(sorted(files, key=lambda f: f['id']))
        self.defaults = defaults
        self.warnings = tuple(warnings)
        canonical = json.dumps([self.entries, self.files, defaults, project_status], sort_keys=True, separators=(',', ':'))
        self.fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        self._by_key = {key(entry): entry for entry in self.entries}

    def lookup(self, language, role, via, module_or_provider, name):
        """The entry for an origin the engine has proven, or None: there is no fallback by bare name."""
        probe = dict(language=language, role=role, origin=dict(via=via, name=name, module=module_or_provider))
        return self._by_key.get(key(probe))

    def role(self, language, role):
        return [entry for entry in self.entries if entry['language'] == language and entry['role'] == role]

    def start_files(self):
        return {f['name'] for f in self.files if 'name' in f}, tuple(f['prefix'] for f in self.files if 'prefix' in f)


def _merge(base_entries, base_files, project):
    project_entries, project_files, disable = project
    by_id = {entry['id']: entry for entry in base_entries}
    for entry_id in disable:
        if entry_id not in by_id:
            raise CatalogError(f'{PROJECT_FILE}: disable {entry_id!r} names no base entry')
        if by_id[entry_id]['role'] not in SCHEMA['project_may_disable']:
            raise CatalogError(f'{PROJECT_FILE}: disable {entry_id!r}: a {by_id[entry_id]["role"]} entry cannot be disabled')
    kept = [entry for entry in base_entries if entry['id'] not in set(disable)]
    taken = {key(entry): entry['id'] for entry in kept}
    for entry in project_entries:
        if key(entry) in taken:
            raise CatalogError(f'{PROJECT_FILE} entry {entry["id"]!r}: same origin and role as {taken[key(entry)]!r}; disable that entry first')
        taken[key(entry)] = entry['id']
    return kept + project_entries, base_files + project_files


def _unique(items, label):
    seen = set()
    for item in items:
        if item['id'] in seen:
            raise CatalogError(f'{label}: duplicate id {item["id"]!r}')
        seen.add(item['id'])


_BASE = {}


def _load_base(folder):
    """Parsed and validated once per process and folder; callers get fresh lists."""
    if str(folder) not in _BASE:
        _BASE[str(folder)] = _read_base(folder)
    entries, files, defaults = _BASE[str(folder)]
    return list(entries), list(files), dict(defaults)


def _read_base(folder):
    entries, files, defaults = [], [], {}
    for name in ('python', 'js', 'php', 'files'):
        label = f'catalog/{name}.json'
        language = None if name == 'files' else name
        data = _parse((folder / f'{name}.json').read_bytes(), label)
        found, found_files, _ = _document(data, label, 'base', language)
        entries += found
        files += found_files
        if language:
            defaults[name] = data['unrecognized_decorator']
    _unique(entries, 'catalog')
    _unique(files, 'catalog files')
    keys = {}
    for entry in entries:
        if key(entry) in keys:
            raise CatalogError(f'catalog: {entry["id"]!r} and {keys[key(entry)]!r} have the same origin and role')
        keys[key(entry)] = entry['id']
    return entries, files, defaults


def load(root=None, folder=HERE):
    """Base catalog (errors raise: the engine never runs on an invalid base) plus the project extension
    (an invalid project file is rejected whole, reported in warnings, and never partly applied)."""
    entries, files, defaults = _load_base(Path(folder))
    warnings, status = [], 'absent'
    if root is not None:
        try:
            raw = _read_project(root)
            if raw is not None:
                status = 'rejected:' + hashlib.sha256(raw).hexdigest()
                project = _document(_parse(raw, PROJECT_FILE), PROJECT_FILE, 'project')
                _unique(project[0] + entries, PROJECT_FILE)
                _unique(project[1] + files, PROJECT_FILE + ' files')
                entries, files = _merge(entries, files, project)
                status = 'applied:' + hashlib.sha256(raw).hexdigest()
        except CatalogError as error:
            warnings.append(f'{error}; the project catalog is ignored and the base catalog is used')
    return Catalog(entries, files, defaults, warnings, status)


# ---- catalog check: what the index uses that the catalog does not know (to choose new entries on data)
def check(root, catalog=None, limit=40):
    """Counts decorators and callees with function arguments that no entry describes. Origins are not stored
    in graph.db: the decorator side reads Python sources with ast, the callee side matches by name only."""
    import ast
    catalog = catalog or load(root)
    db_path = Path(root) / '.code-graph' / 'graph.db'
    if not db_path.is_file():
        raise CatalogError('no index: run `cg --root ROOT index` first')
    db = sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)
    known = {(e['origin'].get('module') + '.' if e['origin'].get('module') else '') + e['origin']['name'] for e in catalog.entries
             if e['language'] == 'python'}
    known_names = {(e['language'], e['origin']['name'].rpartition('.')[2].lower()) for e in catalog.entries}
    decorators, callees = {}, {}
    for (path,) in db.execute("SELECT path FROM files WHERE lang='python' ORDER BY path"):
        full = Path(root) / path
        if full.is_symlink() or not full.is_file():
            continue
        try:
            tree = ast.parse(full.read_bytes())
        except (SyntaxError, ValueError, RecursionError):
            continue
        imported = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                imported.update({alias.asname or alias.name: f'{node.module}.{alias.name}' for alias in node.names})
            elif isinstance(node, ast.Import):
                imported.update({(alias.asname or alias.name).split('.')[0]: alias.name.split('.')[0] if not alias.asname else alias.name
                                 for alias in node.names})
        local = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        for node in tree.body:  # module-level `app = Flask(...)`: the variable is an instance of the imported class
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and len(node.targets) == 1 \
                    and isinstance(node.targets[0], ast.Name):
                callee = ast.unparse(node.value.func)
                head, _, rest = callee.partition('.')
                if head in imported:
                    imported[node.targets[0].id] = imported[head] + ('.' + rest if rest else '')
        for node in ast.walk(tree):
            for decorator in getattr(node, 'decorator_list', []):
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                try:
                    text = ast.unparse(target)
                except (ValueError, RecursionError):
                    continue
                head, _, rest = text.partition('.')
                if head in imported:
                    origin = imported[head] + ('.' + rest if rest else '')
                elif head in local and rest in ('setter', 'getter', 'deleter'):
                    origin = 'builtins.property.' + rest
                elif head in local:
                    origin = 'project:' + text
                else:
                    origin = text if '.' not in text and text not in local else 'unresolved:' + text
                if origin not in known:
                    decorators[origin] = decorators.get(origin, 0) + 1
    rows = db.execute('''SELECT f.lang, c.name, c.recv_name FROM symbol_refs r JOIN files f ON f.id=r.file_id
                         JOIN calls c ON c.file_id=r.file_id AND c.line=r.line WHERE r.kind='callback' AND c.name!=''
                         AND r.target_id IS NOT NULL''').fetchall()
    db.close()
    for lang, name, receiver in rows:
        language = {'javascript': 'js', 'typescript': 'js', 'tsx': 'js'}.get(lang, lang)
        if (language, name.lower()) not in known_names:
            receiver = receiver if not receiver or re.fullmatch(r'[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*', receiver) else '<expr>'
            label = f'{language}:{receiver + "." if receiver else ""}{name}'  # db.query(X).filter counts as <expr>.filter
            callees[label] = callees.get(label, 0) + 1
    top = lambda counts: [dict(origin=origin, count=count) for origin, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]
    return dict(format='codegraph-catalog-check/1', catalog=catalog.fingerprint, warnings=list(catalog.warnings),
                decorators=top(decorators), callback_callees=top(callees),
                limits=['decorators: Python only, origin from the module imports (aliases followed, no re-exports)',
                        'callees: calls on the same line as a resolved callback reference, matched by name only'])


def main(argv=None):
    parser = argparse.ArgumentParser(description='code-graph API catalog: validate the catalogs and count what they do not know.')
    commands = parser.add_subparsers(dest='command', required=True)
    validate = commands.add_parser('validate', help='validate the base catalog and the project extension')
    validate.add_argument('--root')
    checker = commands.add_parser('check', help='decorators and callback callees the catalog does not recognise')
    checker.add_argument('--root', required=True)
    checker.add_argument('--limit', type=int, default=40)
    checker.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        catalog = load(args.root)
        if args.command == 'validate':
            print(json.dumps(dict(format=FORMAT, entries=len(catalog.entries), files=len(catalog.files),
                                  fingerprint=catalog.fingerprint, warnings=list(catalog.warnings)), indent=1))
            return 1 if catalog.warnings else 0
        result = check(args.root, catalog, args.limit)
    except CatalogError as error:
        print(f'catalog: {error}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        for warning in result['warnings']:
            print('warning: ' + warning)
        for title, rows in (('Decorators not in the catalog', result['decorators']), ('Callees with callback arguments not in the catalog', result['callback_callees'])):
            print(title + ':')
            print('\n'.join(f'  {row["count"]:5d}  {row["origin"]}' for row in rows) or '  (none)')
        print('Limits: ' + '; '.join(result['limits']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
