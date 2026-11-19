"""Local lexical retrieval and bounded graph expansion; never execute project code."""
from collections import defaultdict
import json
from pathlib import Path
import re
import runpy
import sqlite3

BODY_CHARS = 2000
LEXICAL_LIMIT = 50
GRAPH_LIMIT = 500
GRAPH_RANK_LIMIT = 100
RRF_K = 60
# Common programming synonyms, applied only after exact matches and always reported.
SYNONYMS = [set(group.split()) for group in (
    'remove delete drop erase discard destroy',
    'create add new make insert register',
    'get fetch load read retrieve lookup',
    'save store write persist',
    'update modify change edit',
    'cancel abort stop terminate kill',
    'start begin launch run init open',
    'error exception fail failure fault',
    'config configuration settings options preferences',
    'check validate verify ensure',
    'parse decode deserialize',
    'render print display format',
    'send emit post dispatch publish',
    'receive handle process consume',
    'release free unlock',
    'lock mutex',
    'copy clone duplicate',
    'count total size length',
)]
SUFFIXES = ('ations', 'ation', 'ings', 'ing', 'ions', 'ion', 'ers', 'er', 'ies', 'ied', 'es', 'ed', 's')


def words(text):
    text = re.sub(r'(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])', ' ', text)
    return re.findall(r'[^\W_]+', text, re.UNICODE)


def stem(term):
    """Strip one common English suffix, keeping at least four characters (cancelling -> cancel)."""
    for suffix in SUFFIXES:
        if term.endswith(suffix) and len(term) - len(suffix) >= 4:
            base = term[:-len(suffix)] + ('y' if suffix in ('ies', 'ied') else '')
            # A shorter prefix only widens the match: cancell -> cancel, stopp -> stop.
            if len(base) >= 5 and base[-1] == base[-2]:
                base = base[:-1]
            return base
    return term


def expansions(terms):
    """Prefix forms and synonyms of the query terms, excluding the exact terms themselves."""
    exact, found = set(terms), {}
    for term in terms:
        root = stem(term)
        if len(root) >= 4:
            found.setdefault(('prefix', root), term)
        for form in dict.fromkeys((term, root, root + 'e')):
            for group in SYNONYMS:
                if form in group:
                    for other in sorted(group - exact):
                        found.setdefault(('synonym', other), term)
    return [dict(kind=kind, term=value, source=source) for (kind, value), source in found.items()]


def fts_query(items):
    quoted = lambda text: '"' + text.replace('"', '""') + '"'
    return ' OR '.join(quoted(item['term']) + ('*' if item['kind'] == 'prefix' else '') for item in items)


def searchable(text):
    return text + ' ' + ' '.join(word.casefold() for word in words(text))


def setup(db):
    previous = dict(db.execute("SELECT k,v FROM meta WHERE k IN ('search_status','search_schema')"))
    existed = db.execute("SELECT 1 FROM sqlite_master WHERE name='search_fts'").fetchone() is not None
    try:
        db.execute('''CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
            file_id UNINDEXED, name, path, signature, body, tokenize='unicode61')''')
        db.execute('SELECT rowid FROM search_fts LIMIT 0')
    except sqlite3.OperationalError as error:
        if 'no such module: fts5' not in str(error).lower():
            raise
        db.execute("INSERT OR REPLACE INTO meta VALUES('search_status','unavailable')")
        return False, False
    db.execute("INSERT OR REPLACE INTO meta VALUES('search_status','ready')")
    db.execute("INSERT OR REPLACE INTO meta VALUES('search_schema','1')")
    return True, not existed or previous != {'search_status': 'ready', 'search_schema': '1'}


def store(db, fid, path, source, symbols, ids):
    rows = []
    for symbol, sid in zip(symbols, ids):
        if symbol['kind'] == 'module':
            continue
        body = source[symbol['start_byte']:symbol['end_byte']].decode('utf-8', 'replace')[:BODY_CHARS]
        rows.append((sid, fid, searchable(symbol['qname']), searchable(path),
                     searchable(symbol['sig']), searchable(body)))
    db.executemany('INSERT INTO search_fts(rowid,file_id,name,path,signature,body) VALUES(?,?,?,?,?,?)', rows)


def adjacent(project, frontier, confidence, limit):
    marks = ','.join('?' for _ in frontier)
    allowed = [name for name, rank in project.cg.CONF_RANK.items()
               if rank >= project.cg.CONF_RANK[confidence]]
    conf_marks = ','.join('?' for _ in allowed)
    sql = f'''WITH adjacent AS (
        SELECT e.caller_id AS source,e.callee_id AS target,e.conf AS confidence,
            f.path AS file,c.line AS line,'call' AS relation
        FROM edges e JOIN calls c ON c.id=e.call_id JOIN files f ON f.id=c.file_id
        WHERE e.caller_id IN ({marks}) OR e.callee_id IN ({marks})
        UNION ALL
        SELECT r.owner_id,r.target_id,'medium',f.path,r.line,'reference'
        FROM symbol_refs r JOIN files f ON f.id=r.file_id
        WHERE r.owner_id IS NOT NULL AND r.target_id IS NOT NULL
            AND (r.owner_id IN ({marks}) OR r.target_id IN ({marks}))
    ) SELECT * FROM adjacent WHERE confidence IN ({conf_marks})
      ORDER BY file,line,source,target,relation LIMIT ?'''
    rows = project.db.execute(sql, list(frontier) * 4 + allowed + [limit + 1]).fetchall()
    return rows[:limit], len(rows) > limit


def graph_candidates(project, seeds, depth, confidence, symbol):
    pool, evidence = set(seeds), defaultdict(dict)
    capped = False
    remaining = 1000
    strength = project.cg.CONF_RANK
    for seed in seeds:
        visited = {seed}
        frontier = {seed: ('high', [])}
        for distance in range(1, depth + 1):
            if remaining == 0:
                capped = True
                break
            ordered = sorted(frontier, key=lambda sid: identity(symbol(sid)))
            capped |= len(ordered) > 100
            active = set(ordered[:100])
            if not active:
                break
            rows, cut = adjacent(project, sorted(active), confidence, remaining)
            remaining -= len(rows)
            capped |= cut
            following = {}
            for edge in rows:
                project._read(edge['file'])
                for current, other in ((edge['source'], edge['target']), (edge['target'], edge['source'])):
                    if current not in active or other in visited:
                        continue
                    if other not in pool and len(pool) >= GRAPH_LIMIT:
                        capped = True
                        continue
                    pool.add(other)
                    old_conf, chain = frontier[current]
                    weakest = min((old_conf, edge['confidence']), key=strength.get)
                    step = dict(edge)
                    step.update(direction='out' if current == edge['source'] else 'in')
                    if other not in following or strength[weakest] > strength[following[other][0]]:
                        following[other] = (weakest, chain + [step])
            for sid, (weakest, chain) in following.items():
                evidence[sid][seed] = dict(distance=distance, confidence=weakest, chain=chain)
            visited.update(following)
            frontier = following
    result = {}
    for sid, witnesses in evidence.items():
        if sid in seeds or symbol(sid)['kind'] == 'module':
            continue
        best = min(witnesses.values(), key=lambda item: (item['distance'], -strength[item['confidence']]))
        result[sid] = dict(best, supporting_seeds=sorted(witnesses))
    ranked = sorted(result, key=lambda sid: (result[sid]['distance'], -len(result[sid]['supporting_seeds']),
                                             -strength[result[sid]['confidence']], identity(symbol(sid))))
    capped |= len(ranked) > GRAPH_RANK_LIMIT
    ranked = ranked[:GRAPH_RANK_LIMIT]
    return ranked, result, dict(seeds=seeds, visited=len(pool), depth=depth,
                               edges_scanned=1000-remaining, edge_limit=1000, truncated=capped)


def identity(symbol):
    return symbol['file'], symbol['line'], symbol['name']


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def search(project, query, mode='hybrid', limit=10, depth=2, confidence='medium', max_chars=9000,
           roots=False, ranking_only=False):
    """roots: query words absent from the index are searched by their root (locate's natural-language input).
    ranking_only: return the fused ranking without source windows or budget (internal callers)."""
    if not isinstance(query, str) or not 1 <= len(query) <= 512:
        raise ValueError('Query must contain 1..512 characters.')
    terms = list(dict.fromkeys(word.casefold() for word in words(query)))
    if not 1 <= len(terms) <= 16:
        raise ValueError('Query must contain 1..16 distinct terms.')
    if mode not in ('lexical', 'hybrid') or confidence not in project.cg.CONF_RANK:
        raise ValueError('Invalid search mode or confidence.')
    for value, low, high in ((limit, 1, 50), (depth, 1, 3), (max_chars, 64, 16000)):
        if type(value) is not int or not low <= value <= high:
            raise ValueError('Invalid search limits.')
    if project.meta.get('search_status') != 'ready':
        raise ValueError('FTS5 search unavailable: run index with SQLite built with FTS5.')
    if project.cg.analysis_inputs(project.root)[2] != project.meta.get('analysis_fingerprint'):
        raise ValueError('Engine changed: refresh the index before searching.')
    def lexical_rows(match, limit):
        return project.db.execute('''SELECT search_fts.rowid AS id,bm25(search_fts,0,8,2,3,1) AS score
            FROM search_fts JOIN symbols s ON s.id=search_fts.rowid JOIN files f ON f.id=s.file_id
            WHERE search_fts MATCH ? ORDER BY score,f.path,s.line,s.qname LIMIT ?''', (match, limit)).fetchall()
    primary_terms = [dict(kind='exact', term=term) for term in terms]
    if roots:
        # Natural-language input: the word form is accidental, so every word is searched by its root.
        primary_terms = [dict(kind='prefix', term=stem(item['term'])) if len(stem(item['term'])) >= 4 else item
                         for item in primary_terms]
    exact_rows = lexical_rows(fts_query(primary_terms), LEXICAL_LIMIT + 1)
    expanded = expansions(terms)
    extra = []
    if expanded and len(exact_rows) < LEXICAL_LIMIT:
        seen = {row['id'] for row in exact_rows}
        extra = [row for row in lexical_rows(fts_query(expanded), LEXICAL_LIMIT + 1) if row['id'] not in seen]
    rows = [(row, 'exact') for row in exact_rows] + [(row, 'expanded') for row in extra]
    lexical_cut = len(rows) > LEXICAL_LIMIT
    rows = rows[:LEXICAL_LIMIT]
    symbols = {}
    def symbol(sid):
        if sid not in symbols:
            symbols[sid] = project._symbol(sid)
            project._read(symbols[sid]['file'])
        return symbols[sid]
    lexical = {}
    for rank, (row, match) in enumerate(rows, 1):
        symbol(row['id'])
        lexical[row['id']] = dict(rank=rank, bm25=float(f"{row['score']:.4g}"), match=match)
    graph, evidence, graph_info = [], {}, dict(seeds=[], visited=0, depth=depth,
                                             edges_scanned=0, edge_limit=1000, truncated=False)
    # Widened matches never compete with exact ones: when exact matches exist, seeds and fusion use
    # only them (as before widening) and widened matches follow; otherwise they take their place.
    primary = {sid: item for sid, item in lexical.items() if item['match'] == 'exact'} or lexical
    if mode == 'hybrid' and rows:
        graph, evidence, graph_info = graph_candidates(project, list(primary)[:3], depth, confidence, symbol)
    graph_ranks = {sid: rank for rank, sid in enumerate(graph, 1)}
    if mode == 'hybrid' and lexical:
        rankings = [(sid, item['rank']) for sid, item in primary.items()] + list(graph_ranks.items())
        values = ','.join('(?,?)' for _ in rankings)
        scores = dict(project.db.execute(f'''WITH rankings(id,position) AS (VALUES {values})
            SELECT id,SUM(1.0 / (? + position)) FROM rankings GROUP BY id''',
            [value for pair in rankings for value in pair] + [RRF_K]))
        ranked = sorted(scores, key=lambda sid: (-scores[sid], identity(symbol(sid))))
        ranked += [sid for sid in lexical if sid not in scores]
    else:
        scores, ranked = {}, list(lexical)
    result = dict(format='codegraph-search/2', engine_version=project.cg.VERSION,
                  revision=project.meta['revision'], content_revision=project.meta['content_revision'],
                  query=dict(text=query, terms=terms, expansions=expanded, mode=mode, confidence=confidence, depth=depth, limit=limit),
                  retrieval=dict(lexical_candidates=len(lexical), lexical_truncated=lexical_cut,
                                 exact_candidates=len(exact_rows[:LEXICAL_LIMIT]), body_prefix_chars=BODY_CHARS, partial_files=int(project.meta.get('partial_files', '0')),
                                 graph=graph_info, rrf_k=RRF_K if mode == 'hybrid' else None,
                                 source_window=dict(max_chars=640, line_chars=200, context_lines=8,
                                                    columns='unicode_codepoints_zero_based_half_open')),
                  items=[], limitations=['Lexical search over symbols and body prefixes; it does not translate the question. After exact matches it adds common prefixes and synonyms, marked expanded.',
                      'Ranking scores are separate from static confidence; the graph is bounded and may be incomplete.'])
    window = runpy.run_path(str(Path(__file__).with_name('source_windows.py')))['window']
    def size():
        shown = len(result['items'])
        result['omissions'] = dict(shown=shown, ranked=len(ranked), omitted=len(ranked)-shown,
                                  reason='max_chars' if shown < min(limit, len(ranked)) else 'limit' if shown < len(ranked) else None)
        partial = (shown < len(ranked) or lexical_cut or graph_info['truncated']
                   or int(project.meta.get('partial_files', '0')) > 0
                   or any(item['source']['truncated'] for item in result['items']))
        result['status'] = 'partial' if partial else 'ready' if ranked else 'empty'
        result['budget'] = dict(max_chars=max_chars, actual_chars=0, unit='unicode_characters', measured_tokens=None)
        for _ in range(5):
            result['budget']['actual_chars'] = len(dumps(result)) + 1
        return result['budget']['actual_chars']
    if size() > max_chars:
        return dict(status='budget_insufficient', max_chars=max_chars)
    if ranking_only:
        return dict(ranked=[dict(symbol=symbol(sid), lexical=lexical.get(sid), rrf_score=scores.get(sid),
                                 graph=dict(evidence[sid], rank=graph_ranks[sid]) if sid in graph_ranks else None)
                            for sid in ranked], terms=[item['term'] + ('*' if item['kind'] == 'prefix' else '') for item in primary_terms])
    for sid in ranked[:limit]:
        target = symbol(sid)
        source = window(project, project.excerpt(target['file'], target['line'], target['end_line'], 8),
                        max_chars=640, line_chars=200)
        # File, window limits and column units are in symbol / retrieval.source_window: keep only what varies.
        window_info = {key: value for key, value in source.pop('window', {}).items()
                       if key in ('continuation', 'cuts', 'reason', 'focus') and value not in (None, [], {})}
        if not (window_info.get('continuation') or {}).get('available', True):
            window_info.pop('continuation')
        source.pop('file', None)
        lines = source.pop('lines')
        source.update(start_line=lines[0]['number'] if lines else target['line'],
                      text='\n'.join(line['text'] for line in lines))
        source.update(source_sha256=project.source_hashes[target['file']], **({'window': window_info} if window_info else {}))
        item = dict(symbol=target, lexical=lexical.get(sid), graph=None, rrf_score=scores.get(sid), source=source)
        if sid in graph_ranks:
            item['graph'] = dict(evidence[sid], rank=graph_ranks[sid])
        result['items'].append(item)
        if size() > max_chars:
            result['items'].pop()
            break
    size()
    return result


def cli(engine, args):
    project_class = runpy.run_path(str(Path(__file__).with_name('project_data.py')))['ProjectData']
    try:
        with project_class(engine, args.root) as project:
            value = search(project, args.query, args.mode, args.limit, args.depth, args.min_conf, args.max_chars)
        print(dumps(value))
    except (ValueError, OSError, sqlite3.Error) as error:
        raise SystemExit(str(error))
