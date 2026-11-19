"""Read-only project queries for the guided UI; never import project modules."""
import contextlib
import hashlib
import json
from pathlib import Path
import re
import shlex
import sqlite3
import runpy
EVIDENCE = runpy.run_path(str(Path(__file__).with_name("evidence.py")))
Snippets = EVIDENCE["Snippets"]
test_role = EVIDENCE["test_role"]
page_info = EVIDENCE["page_info"]
page_offset = EVIDENCE["page_offset"]
try:
    import tomllib
except ImportError:
    tomllib = None


class StaleIndex(ValueError):
    pass


class ProjectData:
    def __init__(self, engine, root, expected_revision=None):
        self.cg = engine
        self.root = Path(root).resolve()
        path = engine.local_path(self.root, '.code-graph/graph.db')
        for suffix in ('-wal', '-shm', '-journal'):
            engine.local_path(self.root, '.code-graph/graph.db' + suffix)
        self.db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute('BEGIN')
        self.meta = dict(self.db.execute('SELECT k,v FROM meta'))
        if self.meta.get('version') != engine.INDEX_VERSION:
            self.db.close()
            raise StaleIndex('Refresh the index before navigating the project.')
        if expected_revision is not None and self.meta.get('revision') != expected_revision:
            self.db.close()
            raise StaleIndex('Another process updated the index. Refresh the analysis before navigating.')
        self.config_sources = engine.configuration_files(self.root)
        current_configs = {path: hashlib.sha256(raw).hexdigest() for path, raw in self.config_sources.items()}
        if current_configs != json.loads(self.meta.get('config_hashes', '{}')):
            self.db.close()
            raise StaleIndex('Configurations changed after analysis. Refresh the index.')
        dart_fresh=getattr(engine,'LANGUAGE_ADAPTERS',{}).get('dart',{}).get('fresh_package_config')
        if dart_fresh is not None and not dart_fresh(self.db,self.root,self.config_sources):
            self.db.close()
            raise StaleIndex('Dart context changed after analysis. Refresh the index.')
        self.files = {r['path']: dict(r) for r in self.db.execute('SELECT * FROM files')}
        self.cache = {}
        self.source_hashes = {}
        self.areas = self._areas()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _read(self, path):
        if path not in self.cache:
            expected = self.files.get(path)
            if expected is None:
                raise ValueError('File missing from the selected index.')
            file = self.cg.local_path(self.root, path)
            with file.open('rb') as handle:
                raw = handle.read(self.cg.MAX_BYTES + 1)
            if len(raw) > self.cg.MAX_BYTES:
                raise StaleIndex('Source exceeds the limit after indexing: ' + path)
            if hashlib.sha1(raw).hexdigest() != expected['sha']:
                raise StaleIndex('Source changed after indexing: ' + path)
            self.source_hashes[path] = hashlib.sha256(raw).hexdigest()
            self.cache[path] = raw.decode('utf-8', 'replace').splitlines()
        return self.cache[path]

    def excerpt(self, path, start, end, limit=200):
        lines = self._read(path)
        stop = min(end, start + limit - 1, len(lines))
        result, size = [], 0
        for number in range(max(1, start), stop + 1):
            text = lines[number - 1]
            remaining = 24000 - size
            if remaining <= 0:
                break
            result.append(dict(number=number, text=text[:remaining]))
            size += len(text)
        return dict(file=path, lines=result, truncated=(len(result) < max(0, end - start + 1) or size > 24000))

    def _symbol(self, sid):
        row = self.db.execute('''SELECT s.id,s.qname AS name,s.kind,s.line,s.end_line,s.sig AS signature,
            f.path AS file,f.lang AS language FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.id=?''', (sid,)).fetchone()
        if row is None:
            raise ValueError('Symbol missing from the active project.')
        return dict(row)

    def _areas(self):
        groups = {'.': dict(id='.', title='Whole project', kind='folder', files=list(self.files), evidence=[])}
        for path in self.files:
            parts = path.split('/')[:-1]
            for depth in range(1, min(len(parts), 4) + 1):
                folder = '/'.join(parts[:depth])
                item = groups.setdefault('folder:' + folder, dict(id='folder:' + folder, title=folder, kind='folder', files=[], evidence=[]))
                item['files'].append(path)
        registrations = self.db.execute('''SELECT o.kind,o.line,f.path FROM observations o JOIN files f ON f.id=o.file_id
            WHERE o.kind IN ('registration.mcp','registration.site') ORDER BY f.path,o.line''').fetchall()
        for row in registrations:
            key = 'mcp' if row['kind'].endswith('mcp') else 'site'
            item = groups.setdefault('area:' + key, dict(id='area:' + key, title='MCP' if key == 'mcp' else 'Site / API', kind='evidence', files=[], evidence=[]))
            if row['path'] not in item['files']:
                item['files'].append(row['path'])
            item['evidence'].append(dict(file=row['path'], line=row['line'], reason='Code registration'))
        self._configured_areas(groups)
        architecture = runpy.run_path(str(Path(__file__).with_name('architecture.py')))
        architecture['add_areas'](self, groups)
        counts = dict(self.db.execute('SELECT f.path,COUNT(CASE WHEN s.kind!="module" THEN 1 END) FROM files f LEFT JOIN symbols s ON s.file_id=f.id GROUP BY f.id'))
        for item in groups.values():
            item['file_count'] = len(item['files'])
            item['symbol_count'] = sum(counts.get(path, 0) for path in item['files'])
            item['evidence'] = item['evidence'][:100]
        return groups

    def _configured_areas(self, groups):
        def read(path):
            raw = self.config_sources.get(path)
            return raw.decode('utf-8', 'replace') if raw is not None else None

        def declared(kind, label, path, line, reason, command=''):
            item = groups.setdefault('area:' + kind, dict(id='area:' + kind, title=label, kind='evidence', files=[], evidence=[]))
            item['evidence'].append(dict(file=path, line=line, reason=reason))
            tokens = []
            try:
                for command_line in command.splitlines():
                    tokens.extend(shlex.split(command_line, comments=True))
            except ValueError:
                tokens = []
            candidates = [token for token in tokens if Path(token).suffix in self.cg.LANG_BY_EXT]
            for position, token in enumerate(tokens[:-2]):
                if re.fullmatch(r'python[\d.]*', Path(token).name) and tokens[position + 1] == '-m':
                    module = tokens[position + 2].replace('.', '/')
                    candidates.extend([module + '.py', 'src/' + module + '.py', module + '/__init__.py', 'src/' + module + '/__init__.py'])
            for candidate in candidates:
                candidate = candidate.removeprefix('./')
                if candidate in self.files and candidate not in item['files']:
                    item['files'].append(candidate)

        def json_script_lines(source):
            decoder = json.JSONDecoder()
            positions = {}
            def members(position):
                position = source.index('{', position) + 1
                while True:
                    while source[position].isspace() or source[position] == ',':
                        position += 1
                    if source[position] == '}':
                        return
                    start = position
                    key, position = decoder.raw_decode(source, position)
                    position = source.index(':', position) + 1
                    while source[position].isspace():
                        position += 1
                    value_start = position
                    _, position = decoder.raw_decode(source, position)
                    yield key, start, value_start
            for key, _, value_start in members(0):
                if key == 'scripts' and source[value_start] == '{':
                    for name, start, _ in members(value_start):
                        positions[name] = source.count('\n', 0, start) + 1
            return positions

        package = read('package.json')
        if package:
            try:
                scripts = json.loads(package).get('scripts', {})
                positions = json_script_lines(package)
                for name, command in scripts.items():
                    if not isinstance(command, str):
                        continue
                    line = positions.get(name)
                    publication = name in ('publish', 'deploy', 'release')
                    declared('publication' if publication else 'pipeline', 'Declared publishing' if publication else 'Commands and pipelines', 'package.json', line, 'Declared script: ' + str(name), command)
            except (ValueError, AttributeError):
                pass
        project = read('pyproject.toml')
        if project and tomllib:
            try:
                positions = {}
                in_scripts = False
                for number, text in enumerate(project.splitlines(), 1):
                    if text.lstrip().startswith('['):
                        in_scripts = bool(re.match(r'^\s*\[project\.scripts\]\s*(?:#.*)?$', text))
                    elif in_scripts and '=' in text and not text.lstrip().startswith('#'):
                        key = text.split('=', 1)[0].strip()
                        try:
                            decoded = tomllib.loads(key + ' = ""')
                            positions.update({name: number for name in decoded})
                        except ValueError:
                            pass
                for name, target in tomllib.loads(project).get('project', {}).get('scripts', {}).items():
                    if not isinstance(target, str):
                        continue
                    line = positions.get(name)
                    declared('pipeline', 'Commands and pipelines', 'pyproject.toml', line, 'Declared entrypoint: ' + str(name), 'python -m ' + target.split(':')[0])
            except (ValueError, AttributeError, TypeError):
                pass
        for relative in sorted(self.config_sources):
            if not relative.startswith('.github/workflows/'):
                continue
            source = read(relative)
            if not source:
                continue
            lines = source.splitlines()
            position = 0
            while position < len(lines):
                text = lines[position]
                run = re.match(r'^(\s*)(-\s+)?run\s*:\s*(.*)$', text)
                uses = re.match(r'^\s*(?:-\s+)?uses:\s*[\w-]+/(?:deploy-pages|upload-pages-artifact|action-gh-release)@', text)
                if run:
                    command = run.group(3)
                    line = position + 1
                    if command.startswith(('|', '>')):
                        indentation = len(run.group(1)) + len(run.group(2) or '')
                        block = []
                        position += 1
                        while position < len(lines):
                            value = lines[position]
                            if value.strip() and len(value) - len(value.lstrip()) <= indentation:
                                break
                            block.append(value.strip())
                            position += 1
                        command = '\n'.join(block)
                        declared('pipeline', 'Commands and pipelines', relative, line, 'Run step in the workflow', command)
                        continue
                    declared('pipeline', 'Commands and pipelines', relative, line, 'Run step in the workflow', command)
                if uses:
                    declared('publication', 'Declared publishing', relative, position + 1, 'Publishing step in the workflow')
                position += 1

    def _paths(self, area):
        if area not in self.areas:
            raise ValueError('Area missing from the active project.')
        return set(self.areas[area]['files'])

    def overview(self):
        scalar = lambda q: self.db.execute(q).fetchone()[0]
        areas = [{k: v for k, v in item.items() if k != 'files'} for item in self.areas.values()]
        shared = EVIDENCE['metrics'](self.db)
        return dict(metrics=shared, content_revision=self.meta.get('content_revision'), name=self.root.name, path=str(self.root), revision=self.meta.get('revision',''), indexed_at=self.meta.get('indexed_at', ''),
                    files=len(self.files), symbols=shared['definitions'], graph_nodes=shared['graph_nodes'],
                    calls=scalar('SELECT COUNT(*) FROM calls'), resolved_calls=scalar('SELECT COUNT(DISTINCT call_id) FROM edges'),
                    hypothetical_edges=scalar("SELECT COUNT(*) FROM edges WHERE conf='low'"),
                    dynamic_calls=scalar('SELECT COUNT(*) FROM calls WHERE dynamic=1'),
                    callbacks=scalar('SELECT COUNT(*) FROM symbol_refs WHERE target_id IS NOT NULL'),
                    skipped_files=int(self.meta.get('skipped_files', 0)), partial_files=int(self.meta.get('partial_files', 0)),
                    languages=[dict(language=r[0], files=r[1]) for r in self.db.execute('SELECT lang,COUNT(*) FROM files GROUP BY lang')],
                    areas=areas[:500], areas_truncated=len(areas) > 500)

    def symbols(self, area='.', query='', offset=0):
        paths = self._paths(area)
        conditions, args = [], []
        if area.startswith('folder:'):
            prefix = self.areas[area]['title'] + '/'
            conditions.append('substr(f.path,1,?)=?')
            args.extend((len(prefix), prefix))
        elif area != '.':
            if not paths:
                return dict(items=[], total=0, offset=offset, more=False)
            if len(paths) > 5000:
                raise ValueError('Area too broad: choose a more specific real folder.')
            conditions.append('f.path IN (' + ','.join('?' for _ in paths) + ')')
            args.extend(sorted(paths))
        if query:
            self.db.create_function('casefold', 1, lambda value: value.casefold(), deterministic=True)
            conditions.append("instr(casefold(s.qname || ' ' || f.path),?)>0")
            args.append(query.casefold())
        base = ' FROM symbols s JOIN files f ON f.id=s.file_id'
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        total = self.db.execute('SELECT COUNT(*)' + base + where, args).fetchone()[0]
        rows = self.db.execute('SELECT s.id,s.qname AS name,s.kind,f.path AS file,s.line' + base + where + ' ORDER BY f.path,s.line,s.id LIMIT 80 OFFSET ?', args + [offset])
        return dict(items=[dict(row) for row in rows], total=total, offset=offset, more=offset + 80 < total)

    def detail(self, sid, confidence='medium', fields=None, limit=80, cursor=None):
        if fields is not None:
            return self.selected_detail(sid, confidence, fields, limit, cursor)
        node = self._symbol(sid)
        node['source'] = self.excerpt(node['file'], node['line'], node['end_line'])
        allowed = [name for name, rank in self.cg.CONF_RANK.items() if rank >= self.cg.CONF_RANK[confidence]]
        marks = ','.join('?' for _ in allowed)
        relations = {'callers': {}, 'callees': {}}
        rows = self.db.execute(f'''SELECT e.caller_id,e.callee_id,e.conf,c.line,f.path FROM edges e
            JOIN calls c ON c.id=e.call_id JOIN files f ON f.id=c.file_id
            WHERE (e.caller_id=? OR e.callee_id=?) AND e.conf IN ({marks}) ORDER BY f.path,c.line LIMIT 501''', [sid, sid] + allowed).fetchall()
        for row in rows[:500]:
            for direction, other in [('callers', row['caller_id']), ('callees', row['callee_id'])]:
                if (direction == 'callers' and row['callee_id'] != sid) or (direction == 'callees' and row['caller_id'] != sid):
                    continue
                item = relations[direction].setdefault(other, dict(symbol=self._symbol(other), callsites=[]))
                item['callsites'].append(dict(file=row['path'], line=row['line'], confidence=row['conf'], source=self.excerpt(row['path'], row['line'], row['line'], 1)))
        node.update({key: list(value.values()) for key, value in relations.items()})
        node['relations_truncated'] = len(rows) > 500
        node['references'] = []
        for row in self.db.execute('''SELECT r.kind,r.line,f.path FROM symbol_refs r JOIN files f ON f.id=r.file_id WHERE r.target_id=? LIMIT 101''', (sid,)):
            node['references'].append(dict(kind=row['kind'], file=row['path'], line=row['line'], source=self.excerpt(row['path'], row['line'], row['line'], 1)))
        node['observations'] = self.resources(sid=sid)
        return node

    def impact(self, sid, confidence='medium', depth=3, compact=False, limit=200, cursor=None, test_cursor=None, known_snippets=()):
        self._symbol(sid)
        references = set()
        seen, via, truncated = self.cg.impact_traversal(self.db, [sid], depth, confidence, references=references)
        items, tests = [], []
        for caller, distance in sorted(seen.items(), key=lambda item: item[1]):
            if caller == sid:
                continue
            node = self._symbol(caller)
            chain, current = [], caller
            while current in via:
                target, conf, where = via[current]
                path, line = where.rsplit(':', 1)
                chain.append(dict(caller=current, callee=target, relation='reference' if (current, target) in references else 'call', confidence=conf, file=path, line=int(line), source=self.excerpt(path, int(line), int(line), 1)))
                current = target
            item = dict(symbol=node, distance=distance, relation=chain[0]['relation'], chain=chain, role=test_role(node))
            items.append(item)
            if test_role(node) == 'test_case':
                tests.append(item)
        if not compact:
            return dict(items=items, tests=tests, truncated=truncated, depth=depth, confidence=confidence)
        if not 1 <= limit <= 500:
            raise ValueError('Limit outside the range 1..500.')
        query = dict(kind='impact', sid=sid, confidence=confidence, depth=depth, limit=limit)
        revision = self.meta['revision']
        offset = page_offset(cursor, revision, query)
        test_offset = page_offset(test_cursor, revision, dict(query, kind='tests'))
        legacy_page = dict(items=items[offset:offset + limit], tests=tests[test_offset:test_offset + limit], truncated=truncated, depth=depth, confidence=confidence)
        legacy_text = json.dumps(legacy_page, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        pool = Snippets(self, known_snippets)
        selected = pool.chain(items[offset:offset + limit])
        selected_tests = pool.chain(tests[test_offset:test_offset + limit])
        item_ids={item['symbol']['id'] for item in selected}
        test_records={str(item['symbol']['id']):item for item in selected_tests if item['symbol']['id'] not in item_ids}
        test_refs=[dict(item_ref=item['symbol']['id']) for item in selected_tests]
        result = dict(format='codegraph-impact/1', revision=revision, content_revision=self.meta.get('content_revision'),
                    items=selected, tests=test_refs, test_records=test_records, snippets=pool.items, known_snippets=list(known_snippets),
                    pages=dict(items=page_info(len(items), offset, limit, revision, query),
                               tests=page_info(len(tests), test_offset, limit, revision, dict(query, kind='tests'))),
                    truncated=truncated, depth=depth, confidence=confidence,
                    limitations=['Tests recognized statically; support and fixtures are not test cases.',
                                 'The graph does not prove there are no other tests.'])
        measurement = dict(scope='impact_page_same_chains', baseline_chars=len(legacy_text),
                           baseline_bytes=len(legacy_text.encode()), estimator='unicode_characters/4', measured_tokens=None,
                           unique_snippets=len(pool.items), chain_occurrences=sum(len(item['chain']) for item in selected + selected_tests))
        result['measurement'] = measurement
        for _ in range(5):
            text = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            measurement.update(actual_chars=len(text), actual_bytes=len(text.encode()),
                               estimated_tokens=(len(text)+3)//4, baseline_estimated_tokens=(len(legacy_text)+3)//4,
                               saved_chars=len(legacy_text)-len(text))
        return result


    def resources(self, area='.', sid=None, limit=200, cursor=None):
        if not 1 <= limit <= 500:
            raise ValueError('Limit outside the range 1..500.')
        paths = sorted(self._paths(area))
        conditions, args = ["o.kind!='php.callback_api'"], []
        if sid is not None:
            self._symbol(sid)
            conditions.append('o.owner_id=?')
            args.append(sid)
        if area != '.':
            if not paths:
                return dict(items=[], total=0, truncated=False, page=page_info(0, 0, limit, self.meta['revision'], {}))
            conditions.append('f.path IN (' + ','.join('?' for _ in paths) + ')')
            args.extend(paths)
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        base = ' FROM observations o JOIN files f ON f.id=o.file_id' + where
        query = dict(kind='resources', area=area, sid=sid, limit=limit)
        offset = page_offset(cursor, self.meta['revision'], query)
        total = self.db.execute('SELECT COUNT(*)' + base, args).fetchone()[0]
        rows = self.db.execute('SELECT o.*,f.path' + base + ' ORDER BY f.path,o.line,o.id LIMIT ? OFFSET ?', args + [limit, offset])
        items = [dict(id=row['id'], owner=row['owner_id'], kind=row['kind'], callee=row['callee'], file=row['path'], line=row['line'],
                      value=row['value'], value_kind=row['value_kind'], source=self.excerpt(row['path'], row['line'], row['line'], 1)) for row in rows]
        page = page_info(total, offset, limit, self.meta['revision'], query)
        return dict(items=items, total=total, truncated=page['status'] == 'partial', page=page)

    def selected_detail(self, sid, confidence, fields, limit=80, cursor=None):
        allowed_fields = {'source', 'callers', 'callees', 'references', 'observations', 'diagnostics'}
        fields = set(fields)
        if not fields <= allowed_fields or not 1 <= limit <= 500:
            raise ValueError('Invalid fields or limit.')
        node = self._symbol(sid)
        node.update(revision=self.meta['revision'], content_revision=self.meta.get('content_revision'), role=test_role(node), pages={})
        if 'source' in fields:
            node['source'] = self.excerpt(node['file'], node['line'], node['end_line'])
        allowed = [name for name, rank in self.cg.CONF_RANK.items() if rank >= self.cg.CONF_RANK[confidence]]
        marks = ','.join('?' for _ in allowed)
        for direction, own, other in [('callers', 'callee_id', 'caller_id'), ('callees', 'caller_id', 'callee_id')]:
            if direction not in fields:
                continue
            query = dict(kind=direction, sid=sid, confidence=confidence, limit=limit)
            offset = page_offset(cursor, self.meta['revision'], query)
            base = f' FROM edges e JOIN calls c ON c.id=e.call_id JOIN files f ON f.id=c.file_id WHERE e.{own}=? AND e.conf IN ({marks})'
            total = self.db.execute('SELECT COUNT(*)' + base, [sid] + allowed).fetchone()[0]
            rows = self.db.execute(f'SELECT e.{other} AS other,e.conf,c.line,c.recv,c.id AS call_id,f.path' + base + ' ORDER BY f.path,c.line,c.id,e.callee_id LIMIT ? OFFSET ?', [sid] + allowed + [limit, offset])
            relations = {}
            for row in rows:
                relation = relations.setdefault(row['other'], dict(symbol=self._symbol(row['other']), callsites=[]))
                relation['callsites'].append(dict(call_id=row['call_id'], file=row['path'], line=row['line'], confidence=row['conf'],
                    reason=EVIDENCE['edge_reason'](row['recv'], row['conf']), source=self.excerpt(row['path'], row['line'], row['line'], 1)))
            node[direction] = list(relations.values())
            node['pages'][direction] = page_info(total, offset, limit, self.meta['revision'], query)
            node['pages'][direction]['unit']='callsite'
            node['pages'][direction]['distinct_symbols']=self.db.execute(f'SELECT COUNT(DISTINCT e.{other})'+base,[sid]+allowed).fetchone()[0]
        if 'references' in fields:
            node['references'] = [dict(kind=row['kind'], file=row['path'], line=row['line'], source=self.excerpt(row['path'], row['line'], row['line'], 1))
                for row in self.db.execute('SELECT r.kind,r.line,f.path FROM symbol_refs r JOIN files f ON f.id=r.file_id WHERE r.target_id=? ORDER BY f.path,r.line LIMIT 101', (sid,))]
            node['references_truncated'] = len(node['references']) > 100
            node['references'] = node['references'][:100]
        if 'observations' in fields:
            node['observations'] = self.resources(sid=sid, limit=limit, cursor=cursor)
        if 'diagnostics' in fields:
            node['diagnostics'] = self.call_diagnostics(sid, confidence)
        return node

    def call_diagnostics(self, sid, confidence='medium'):
        self._symbol(sid)
        partial = set(json.loads(self.meta.get('partial_paths', '[]')))
        rows = self.db.execute('SELECT c.*,f.path FROM calls c JOIN files f ON f.id=c.file_id WHERE c.caller_id=? ORDER BY c.line,c.id', (sid,))
        result = []
        for row in rows:
            edges = list(self.db.execute('SELECT callee_id,conf FROM edges WHERE call_id=? ORDER BY callee_id', (row['id'],)))
            accepted = [(target, conf) for target, conf in edges if self.cg.CONF_RANK[conf] >= self.cg.CONF_RANK[confidence]]
            status = ('partial_syntax' if row['path'] in partial else 'dynamic' if row['dynamic'] else
                      'resolved' if accepted else 'excluded_by_confidence' if edges else 'known_builtin' if row['recv'] == 'builtin' else 'unresolved_or_external')
            result.append(dict(file=row['path'], line=row['line'], name=row['name'], status=status,
                               reason=EVIDENCE['edge_reason'](row['recv'], accepted[0][1]) if accepted else 'unknown',
                               candidates=len(edges)))
        return result

    def architecture(self, limit=500):
        return runpy.run_path(str(Path(__file__).with_name('architecture.py')))['analyze'](self, limit)

    def feature_catalog(self, query=''):
        return runpy.run_path(str(Path(__file__).with_name('features.py')))['catalog'](self, query)

    def feature(self, feature_id, confidence='medium', depth=2):
        return runpy.run_path(str(Path(__file__).with_name('features.py')))['detail'](self, feature_id, confidence, depth)

    def graph(self, area='.', focus=None, confidence='medium'):
        import argparse
        paths = self._paths(area)
        args = argparse.Namespace(root=str(self.root), symbol=None, depth=2, min_conf=confidence, max_nodes=5000, max_edges=50000)
        if focus is not None:
            self._symbol(focus)
        ids = [r[0] for r in self.db.execute('SELECT s.id,f.path FROM symbols s JOIN files f ON f.id=s.file_id') if r[1] in paths] if focus is None else None
        data = self.cg.export_data(self.db, args, node_ids=ids, focus_id=focus)
        data['embedded'] = True
        return data
