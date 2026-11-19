"""`cg locate`: from the text of a request to the places to change, their callers and tests, in one bounded answer.

Deterministic and local: hybrid search over the index, then the static impact of each candidate.
No model is called and no project code is executed.
"""
from collections import Counter
import json
from pathlib import Path
import re
import runpy

MAX_TERMS = 16
MAX_INPUT_CHARS = 200000
ANALYSIS_CHARS = 20000
QUERY_CHARS = 512
TRIM_NOTICE = 'Text search used the first 20000 characters and a 512-character query budget; stack frames were scanned throughout.'
STOPWORDS = set('''
a an and are as at be been but by can could did do does doing done each for from had has have how i if in into is it
its just like may me might more most must my no not of on once only or other our out over same should so some such than
that the their them then there these they this those to too under until up very was we were what when where which while
who why will with would you your also any all both few further here itself own about above after again against before
being below between during off ourselves yourselves through instead get gets got give gives given using use used
issue bug bugs problem wrong expected actual behaviour behavior result results error errors return returns returned
case cases example examples code test tests call calls called currently still now please thanks
'''.split())
# Consume a whole reference once, then classify it. A path-shaped prefix must not cause
# backtracking at every character of punctuation-heavy input.
CODE_TOKEN = re.compile(r'`([^`\n]{1,200})`|(?<![\w])(@?[A-Za-z_][A-Za-z0-9_./\\-]*)')
URL = re.compile(r'https?://[^\s`<>"\'\])]+', re.I)
SOURCE_SUFFIXES = {'.py', '.php', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts',
                   '.go', '.dart', '.java', '.swift', '.sql'}
FRAMES = (
    re.compile(r'File "([^"]+)", line (\d+), in ([\w<>.]+)'),                 # Python
    re.compile(r'at (?:async )?([\w$.<>]+) \(([^()\s]+?):(\d+)(?::\d+)?\)'),    # JavaScript / TypeScript
    re.compile(r'^\s*([\w./-]+\.go):(\d+)', re.M),                             # Go
)
TEST_PATH = re.compile(r'(^|/)(tests?|__tests__|spec|testing)/|(^|/)test_[^/]*$|(_test|\.test|\.spec|Test)\.\w+$')


def without_frames(text):
    """The text with each stack-frame line reduced to its function name: paths, line numbers and
    `Traceback` headers are not words of the request."""
    kept = []
    for line in text.splitlines():
        if line.strip().startswith('Traceback (most recent call last)'):
            continue
        found = [found for found in frames(line)]
        kept.append(' '.join(name for _, _, name in found if name) if found else line)
    return '\n'.join(kept)


def code_tokens(text):
    """Code references with their source spans; ordinary words remain available for frequency ranking."""
    for match in CODE_TOKEN.finditer(text):
        quoted = match.group(1) is not None
        token = (match.group(1) or match.group(2)).replace('\\', '/').rstrip('.')
        if (quoted or '/' in token or '_' in token or Path(token).suffix.casefold() in SOURCE_SUFFIXES or
                '.' in token and re.fullmatch(r'[A-Za-z_]\w*(?:\.\w+)+', token) or
                '-' in token and re.fullmatch(r'[A-Za-z_]\w*(?:-\w+)+', token) or
                re.search(r'[a-z0-9][A-Z]|[A-Z][a-z0-9]+[A-Z]', token)):
            yield match, token


def request_text(text, paths=()):
    """Discard URL scaffolding; keep an indexed source-path suffix, otherwise only its filename."""
    paths = sorted(paths, key=len, reverse=True)
    def replace(match):
        parts = re.split(r'[?#]', match.group(), maxsplit=1)[0].split('/', 3)
        if len(parts) != 4 or Path(parts[3]).suffix.casefold() not in SOURCE_SUFFIXES:
            return ' '
        path = parts[3]
        return next((candidate for candidate in paths if path == candidate or path.endswith('/' + candidate)), Path(path).name)
    return URL.sub(replace, without_frames(text))


def query_terms(text, words, stem=None):
    """Code-like tokens first (backticks, dotted, snake_case, camelCase), then frequent plain words;
    with `stem`, a word whose root is already there is not repeated."""
    text = request_text(text)
    ordered = []
    plain_parts, end = [], 0
    for match, reference in code_tokens(text):
        plain_parts.append(text[end:match.start()])
        end = match.end()
        token = reference.rsplit('/', 1)[-1]
        if '/' in reference or Path(token).suffix.casefold() in SOURCE_SUFFIXES:
            token = Path(token).stem
        for part in words(token):
            ordered.append(part.casefold())
    plain_parts.append(text[end:])
    plain = [word.casefold() for word in words(' '.join(plain_parts))]
    counts = Counter(word for word in plain if len(word) >= 3 and word not in STOPWORDS and not word.isdigit())
    first = {}
    for position, word in enumerate(plain):
        first.setdefault(word, position)
    ordered += [word for word, _ in sorted(counts.items(), key=lambda item: (-item[1], first[item[0]]))]
    terms, roots = [], set()
    for term in ordered:
        root = stem(term) if stem else term
        if root not in roots and term not in STOPWORDS and len(term) >= 2 and not term.isdigit():
            terms.append(term)
            roots.add(root)
        if len(terms) == MAX_TERMS:
            break
    return terms


def frames(text):
    """Stack frames in the text, outermost first: (path, line, function or None)."""
    found = []
    for pattern in FRAMES:
        for match in pattern.finditer(text):
            groups = match.groups()
            if pattern is FRAMES[0]:
                found.append((match.start(), groups[0], int(groups[1]), groups[2]))
            elif pattern is FRAMES[1]:
                found.append((match.start(), groups[1], int(groups[2]), groups[0].split('.')[-1]))
            else:
                found.append((match.start(), groups[0], int(groups[1]), None))
    return [(path, line, name) for _, path, line, name in sorted(found)]


def frame_symbols(project, text):
    """Indexed symbols for stack frames, deepest first: the indexed file whose path ends the frame path."""
    paths = sorted(project.files)
    by_name, path_cache = {}, {}
    for path in paths:
        by_name.setdefault(path.rsplit('/', 1)[-1], []).append(path)
    # Long logs are scanned line by line: Go's leading whitespace must not backtrack across
    # thousands of blank lines. Short requests retain their original frame parsing exactly.
    entries = ((entry for line in text.splitlines() if line.strip() for entry in frames(line))
               if len(text) > ANALYSIS_CHARS else frames(text))
    resolved = []
    for depth, (path, line, name) in enumerate(entries):
        normal = path.replace('\\', '/')
        if normal not in path_cache:
            matches = [candidate for candidate in by_name.get(normal.rsplit('/', 1)[-1], [])
                       if normal == candidate or normal.endswith('/' + candidate)]
            path_cache[normal] = max(matches, key=len) if matches else None
        if path_cache[normal] is not None:
            resolved.append((depth, path_cache[normal], line, name))
    # The raising frame is often a shared helper; the file the trace passes through most is a better
    # first guess. Files by frame count, then by depth; within a file, its deepest frame.
    counts = Counter(file for _, file, _, _ in resolved)
    deepest = {}
    for depth, file, line, name in resolved:
        deepest[file] = (depth, line, name)
    order = sorted(deepest, key=lambda file: (-counts[file], -deepest[file][0]))
    result, seen = [], set()
    for file in order:
        _, line, name = deepest[file]
        # Reported line numbers often come from another version: match the frame's function name first.
        rows = []
        if name and name.isidentifier():
            rows = project.db.execute('''SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id
                WHERE f.path=? AND s.kind!='module' AND (s.qname=? OR substr(s.qname, -?)=?)
                ORDER BY ABS(s.line-?) LIMIT 1''', (file, name, len(name) + 1, '.' + name, line)).fetchall()
        if not rows:
            rows = project.db.execute('''SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id
                WHERE f.path=? AND s.kind!='module' AND s.line<=? AND s.end_line>=? ORDER BY s.line DESC LIMIT 1''',
                (file, line, line)).fetchall()
        if rows and rows[0][0] not in seen:
            seen.add(rows[0][0])
            result.append(dict(symbol=project._symbol(rows[0][0]), frame=f'{file}:{line}' + (f' in {name}' if name else '')
                               + (f', {counts[file]} frames' if counts[file] > 1 else '')))
    return result


def hint_symbols(project, text, terms, search):
    """Resolve explicit code names against the index, before the global lexical cap.

    File paths are evidence, never filesystem inputs. Within a named file, use the same
    BM25 weights as search; keep up to three declarations rather than assuming a name like `create`.
    """
    tokens = list(dict.fromkeys(token.strip('()') for _, token in code_tokens(request_text(text, project.files))))[:32]
    result, seen = [], set()
    paths = [path for path in sorted(project.files) if not TEST_PATH.search(path)]
    rooted = [(term, search['stem'](term)) for term in terms]
    match_query = search['fts_query']([dict(kind='prefix' if len(root) >= 4 else 'exact',
                                           term=root if len(root) >= 4 else term) for term, root in rooted])
    for token in tokens:
        if not re.fullmatch(r'@?[\w$./-]+', token):
            continue
        exact = [path for path in paths if token == path or token.endswith('/' + path)]
        leaf = token.rsplit('/', 1)[-1]
        # An unqualified word (`response`, `main`) or a directory (`src/cache`) is not a
        # named source file. Compound rule names and filenames are; scoped rule names use the leaf.
        file_name = (Path(leaf).suffix.casefold() in SOURCE_SUFFIXES or
                     ('/' not in token or token.startswith('@')) and bool(re.search(r'[-_]', leaf)))
        matched = exact or ([path for path in paths if Path(path).name == leaf or Path(path).stem == leaf]
                            if file_name else [])
        if not exact and len(matched) > 1:
            continue  # a basename shared by several production files is not a strong path hint
        rows = []
        for path in matched[:3]:
            ranked = project.db.execute('''SELECT s.id FROM search_fts
                JOIN symbols s ON s.id=search_fts.rowid JOIN files f ON f.id=s.file_id
                WHERE f.path=? AND search_fts MATCH ?
                ORDER BY bm25(search_fts,0,8,2,3,1),s.line,s.qname LIMIT 3''',
                (path, match_query)).fetchall() if match_query else []
            rows.extend((row[0], 'named file') for row in ranked)
        if not matched and '.' in token and '/' not in token and Path(token).suffix.casefold() not in SOURCE_SUFFIXES:
            rows = [(row[0], 'named symbol') for row in project.db.execute('''SELECT s.id,f.path
                FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.kind!='module' AND
                (s.qname=? OR substr(s.qname,-?)=?) ORDER BY f.path,s.line LIMIT 3''',
                (token, len(token) + 1, '.' + token)) if not TEST_PATH.search(row[1])]
        for sid, why in rows:
            if sid not in seen:
                seen.add(sid)
                result.append(dict(symbol=project._symbol(sid), hint=why))
    return result


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


SOURCE_LINES, LINE_WIDTH = 2, 100
MEANING = 'file characters for shown candidates/callers/tests versus this answer; not agent tokens'


def file_chars(project, path):
    """Exact characters of an indexed file as decoded text (final newline counted only when present)."""
    project._read(path)  # checks the file still matches the index
    raw = project.cg.local_path(project.root, path).read_bytes()
    return len(raw.decode('utf-8', 'replace'))


def measure(result, render, sizes):
    """Fill `reading` for exactly what `render(result)` returns; the size sits inside the answer, so iterate."""
    files = sorted({path for item in result['items'] for path in item['files']})
    total = sum(sizes[path] for path in files)
    result['reading'] = dict(files=len(files), file_chars=total, returned_chars=0, saved_ratio=None, meaning=MEANING)
    def settle(size):
        result['reading'].update(returned_chars=size, saved_ratio=round(1 - size / total, 3) if total else None)
        return len(render(result))
    size = len(render(result))
    for _ in range(6):
        if settle(size) == size:
            return size
        size = len(render(result))
    # The ratio's digits can make the size oscillate. Look for a nearby size that describes itself;
    # when none exists, report the smallest size that is not below the real one, marked exact=false.
    window = sorted(range(max(1, size - 8), size + 24), key=lambda n: (abs(n - size), n))
    for candidate in window:
        if settle(candidate) == candidate:
            return candidate
    result['reading']['exact'] = False
    for candidate in sorted(range(max(1, size - 8), size + 40)):
        if settle(candidate) <= candidate:
            return candidate
    raise RuntimeError('locate: the answer size does not settle')


def as_json(result):
    return dumps(result) + '\n'


def locate(project, text, limit=5, max_chars=5000, confidence='medium', search_module=None, render=as_json):
    """Candidates for `text`, kept within `max_chars` of `render(result)`; `reading` measures that rendering."""
    if not isinstance(text, str) or not 1 <= len(text) <= MAX_INPUT_CHARS:
        raise ValueError('Request must contain 1..200000 characters.')
    if type(limit) is not int or not 1 <= limit <= 10 or type(max_chars) is not int or not 512 <= max_chars <= 16000:
        raise ValueError('Invalid locate limits.')
    search = search_module or runpy.run_path(str(Path(__file__).with_name('search.py')))
    trimmed = len(text) > ANALYSIS_CHARS
    lexical_text = text[:ANALYSIS_CHARS] if trimmed else text
    terms = query_terms(lexical_text, search['words'], search['stem'])
    # Search's query limit applies even when a short request contains oversized identifiers.
    bounded, size = [], 0
    for term in terms:
        needed = len(term) + bool(bounded)
        if size + needed <= QUERY_CHARS:
            bounded.append(term)
            size += needed
    trim_info = dict(input_chars=len(text), analysed_chars=len(lexical_text), trimmed=True) if trimmed else {}
    if len(bounded) != len(terms):
        trim_info['omitted_terms'] = len(terms) - len(bounded)
    terms = bounded
    result = dict(format='codegraph-locate/1', engine_version=project.cg.VERSION, revision=project.meta['revision'],
                  query=dict(terms=terms, limit=limit, confidence=confidence), items=[],
                  limitations=['Static candidates: verify in source and tests.', 'Listed tests were not executed.'])
    result['query'].update(trim_info)
    # Stack frames do not need words: a trace of bare frames still names the place.
    traced = [item for item in frame_symbols(project, text) if not TEST_PATH.search(item['symbol']['file'])][:3]
    result['query']['frames'] = len(traced)
    hinted = hint_symbols(project, lexical_text, terms, search)
    best_text, eligible = [], []
    if terms:
        found = search['search'](project, ' '.join(terms), mode='hybrid', limit=50, depth=2, confidence=confidence,
                                 roots=True, ranking_only=True)
        result['query']['searched'] = found['terms']
        eligible = [item for item in found['ranked'] if not TEST_PATH.search(item['symbol']['file'])
                    and item['symbol']['kind'] != 'module']
        # Fusion favours neighbours of the seeds over the seeds themselves (seeds get no graph credit),
        # so the two best text matches lead and the fused order follows. Among text matches, a name that
        # covers more of the request leads: with word roots, BM25 alone prefers short bodies that repeat
        # forms of the words (notify_cancelled over cancel_order).
        stems = [term.rstrip('*') for term in found['terms']]
        def name_hits(item):
            parts = [part.casefold() for part in search['words'](item['symbol']['name'])]
            return sum(any(part.startswith(stem) for part in parts) for stem in stems)
        best_text = sorted((item for item in eligible if item.get('lexical')),
                           key=lambda item: (-name_hits(item), item['lexical']['rank']))[:2]
    unique = {}
    for item in traced + hinted + best_text + eligible:
        unique.setdefault(item['symbol']['id'], item)
    first_files, repeated, file_counts = [], [], Counter()
    for item in unique.values():
        file = item['symbol']['file']
        (repeated if file_counts[file] >= 2 else first_files).append(item)
        file_counts[file] += 1
    candidates = (first_files + repeated)[:limit]
    sizes = {}
    for item in candidates:
        symbol = item['symbol']
        impact = project.impact(symbol['id'], confidence=confidence, depth=2)
        callers = [entry for entry in impact['items'] if entry['distance'] == 1 and entry['role'] != 'test_case']
        tests = impact['tests']
        files = sorted({symbol['file']} | {entry['symbol']['file'] for entry in callers[:1] + tests[:1]})
        for path in files:
            if path not in sizes:
                sizes[path] = file_chars(project, path)
        excerpt = project.excerpt(symbol['file'], symbol['line'], symbol['end_line'], SOURCE_LINES)
        lines = [line['text'] for line in excerpt['lines']]
        cut = sum(len(line) > LINE_WIDTH for line in lines)
        if item.get('frame'):
            why = 'stack frame ' + item['frame']
        elif item.get('hint'):
            why = item['hint']
        elif item.get('lexical'):
            why = 'text #' + str(item['lexical']['rank']) + (' (expanded)' if item['lexical'].get('match') == 'expanded' else '')
        else:
            why = 'graph neighbour of a text match, ' + item['graph']['confidence']
        result['items'].append(dict(
            file=symbol['file'], line=symbol['line'], end_line=symbol['end_line'], name=symbol['name'],
            kind=symbol['kind'], signature=symbol['signature'], why=why, files=files,
            # truncated: fewer lines than the symbol has, or lines_cut lines shortened to LINE_WIDTH characters;
            # read the rest from file at line..end_line.
            source=dict(start_line=symbol['line'], text='\n'.join(line[:LINE_WIDTH] for line in lines),
                        truncated=symbol['end_line'] - symbol['line'] + 1 > len(lines) or cut > 0, lines_cut=cut,
                        source_sha256=project.source_hashes[symbol['file']]),
            callers=dict(count=len(callers), shown=[f"{c['symbol']['file']}:{c['chain'][0]['line']} {c['symbol']['name']}" for c in callers[:1]]),
            tests=dict(count=len(tests), shown=[f"{t['symbol']['file']}:{t['symbol']['line']} {t['symbol']['name']}" for t in tests[:1]],
                       truncated=bool(impact.get('truncated')))))
    while True:
        result['status'] = 'ready' if result['items'] else ('budget_insufficient' if result.get('omitted') else 'empty')
        if measure(result, render, sizes) <= max_chars or not result['items']:
            break
        result['items'].pop()
        result['omitted'] = result.get('omitted', 0) + 1
    if result['reading']['returned_chars'] > max_chars:
        # Even the bare answer does not fit: keep only what says so and how to ask again.
        result = dict(format=result['format'], status='budget_insufficient', omitted=result.get('omitted', 0),
                      revision=result['revision'], items=[], hint='raise max_chars or lower limit')
        if trim_info:
            # Even the smallest response declares the shortened analysis. The status already
            # explains that the answer budget is insufficient; use the hint space for metadata.
            del result['hint']
            result['query'] = trim_info
        measure(result, render, sizes)
    return result


def cli(engine, args):
    import argparse, contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):  # incremental: re-parses only changed files
        engine.cmd_index(argparse.Namespace(root=args.root, full=False))
    project_class = runpy.run_path(str(Path(__file__).with_name('project_data.py')))['ProjectData']
    try:
        with project_class(engine, args.root) as project:
            value = locate(project, args.text, args.limit, args.max_chars, args.min_conf, engine.search_module(),
                           render=as_json if args.json else as_text)
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
    print(as_json(value) if args.json else as_text(value), end='')


def as_text(value):
    reading, out = value['reading'], []
    query = value.get('query', {})
    out.append(f"{len(value['items'])} candidate places · {value['status']} · searched: "
               f"{' '.join(query.get('searched', query.get('terms', [])))}")
    if reading.get('saved_ratio') is not None:
        out.append(f"reading: {'at most ' if reading.get('exact') is False else ''}{reading['returned_chars']:,} characters here instead of {reading['file_chars']:,} in "
                   f"{reading['files']} files " + (f"({reading['saved_ratio']:.0%} less to read)" if reading['saved_ratio'] > 0
                                                 else "(these files are short: reading them is as cheap)"))
    if query.get('trimmed'):
        out.append(TRIM_NOTICE)
    if value.get('omitted'):
        out.append(f"{value['omitted']} more omitted to stay within the budget")
    for number, item in enumerate(value['items'], 1):
        out.append(f"\n{number}. {item['file']}:{item['line']} {item['name']}  [{item['why']}]")
        out.append(f"   {item['signature']}")
        out.append(f"   callers {item['callers']['count']}: " + ('; '.join(item['callers']['shown']) or '—'))
        out.append(f"   tests {item['tests']['count']}: " + ('; '.join(item['tests']['shown']) or '—'))
    return '\n'.join(out) + '\n'
