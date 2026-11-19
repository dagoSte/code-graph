"""`cg start`: index a project and show what code-graph can answer on it, with ready commands."""
import argparse
import contextlib
import io
import json
import os
import re
import shlex
from collections import defaultdict

TEST_PATH = re.compile(r'(^|/)(tests?|__tests__|spec|fixtures?)/|(^|/)test_[^/]*$|(_test|\.test|\.spec|Test)\.\w+$')
SMALL_PROJECT_FILES = 30

TEXT = {
    'en': dict(
        title='code-graph · {project}',
        summary='{files} source files · {definitions} definitions · {resolved} resolved calls · languages: {languages}',
        partial='{n} files could not be fully parsed: they are listed by `cg stats` and code-graph abstains on them.',
        small=('Small project ({files} source files). Reading the files directly is usually just as fast here: '
               'in our live pilot on projects of about ten files, agents fixed every task with and without code-graph, and the graph tools '
               'only added time. code-graph is meant for larger or unfamiliar code.'),
        large=('{files} source files: too many to read one by one. This is what code-graph is meant for: '
               'finding the code for a request, the impact of a change and the tests to run.'),
        heading='Three things to try now',
        impact_title='1. What breaks if I change it, and which tests should I run?',
        impact_body='{symbol} is called from {callers}; {tests} reach it through those calls.',
        places=('1 place', '{n} places'), tests=('1 test', '{n} tests'),
        impact_none='No function here has both callers and tests reaching it; try `cg map` to see the busiest ones.',
        search_title='2. Where is the code for a request?',
        search_body='Describe it with words that may appear in the code. Hybrid search ranks matching declarations and adds the functions they call or are called by.',
        review_title='3. Did my change touch anything I did not intend?',
        review_body='After editing, compare signatures, calls and findings with the last commit.',
        review_nogit='This folder is not a Git checkout: `cg review` needs a base to compare with.',
        agent_title='With Codex or Claude Code',
        agent_body='Paste this into your agent; it runs the commands above and reads the source it points to:',
        agent_prompt='Use code-graph to prepare this change: [describe it]. Show me where to intervene, the possible impact and the tests to run.',
        dashboard='To explore visually:',
    ),
    'it': dict(
        title='code-graph · {project}',
        summary='{files} file sorgente · {definitions} definizioni · {resolved} chiamate risolte · linguaggi: {languages}',
        partial='{n} file non si analizzano del tutto: li elenca `cg stats` e su di loro code-graph si astiene.',
        small=('Progetto piccolo ({files} file sorgente). Qui leggere direttamente i file è di solito altrettanto rapido: '
               'nel nostro pilota con agenti reali su progetti di una decina di file, gli agenti hanno corretto tutto con e senza code-graph, '
               'e gli strumenti del grafo hanno solo aggiunto tempo. code-graph è pensato per codice più grande o sconosciuto.'),
        large=('{files} file sorgente: troppi da leggere uno a uno. È il caso per cui code-graph è pensato: '
               'trovare il codice di una richiesta, l’impatto di una modifica e i test da lanciare.'),
        heading='Tre cose da provare subito',
        impact_title='1. Cosa si rompe se lo cambio, e quali test devo lanciare?',
        impact_body='{symbol} è chiamata da {callers}; attraverso quelle chiamate la raggiungono {tests}.',
        places=('1 punto', '{n} punti'), tests=('1 test', '{n} test'),
        impact_none='Qui nessuna funzione ha insieme chiamanti e test che la raggiungono; prova `cg map` per vedere le più usate.',
        search_title='2. Dov’è il codice di una richiesta?',
        search_body='Descrivila con parole che possono comparire nel codice. La ricerca ibrida ordina le dichiarazioni pertinenti e aggiunge le funzioni che chiamano o da cui sono chiamate.',
        review_title='3. La mia modifica ha toccato qualcosa che non volevo?',
        review_body='Dopo la modifica, confronta firme, chiamate e segnalazioni con l’ultimo commit.',
        review_nogit='Questa cartella non è un checkout Git: `cg review` ha bisogno di una base con cui confrontare.',
        agent_title='Con Codex o Claude Code',
        agent_body='Incolla questo nel tuo agente; esegue i comandi qui sopra e legge il sorgente che indicano:',
        agent_prompt='Usa code-graph per preparare questa modifica: [descrivila]. Mostrami dove intervenire, il possibile impatto e i test da lanciare.',
        dashboard='Per esplorare in modo visuale:',
    ),
}


def language(requested):
    if requested in TEXT:
        return requested
    for name in ('LC_ALL', 'LC_MESSAGES', 'LANG'):
        value = os.environ.get(name, '')
        if value:
            return 'it' if value.lower().startswith('it') else 'en'
    return 'en'


def words(name):
    """Split an identifier into lower-case search words: ChangeFlow.edit -> change flow edit."""
    parts = re.findall(r'[A-Z]+(?=[A-Z][a-z]|\b|_|\d)|[A-Z]?[a-z]+|\d+', name.replace('.', ' ').replace(':', ' '))
    return ' '.join(part.lower() for part in parts if len(part) > 1) or name


def verified_tests(engine, root, symbol):
    """Number of tests `cg verify --plan` proposes for the symbol, using the real command."""
    args = argparse.Namespace(root=root, cmd='verify', target=symbol, file=False, plan=True,
                              min_conf='medium', depth=3, json=True)
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            engine.cmd_analysis(args)
        return len(json.loads(buffer.getvalue()).get('tests') or [])
    except (Exception, SystemExit):  # an unresolvable, ambiguous or stale example is skipped, never fatal
        return 0


def count(pair, n):
    return pair[0] if n == 1 else pair[1].format(n=n)


def example(engine, db, root, limit=40):
    """A production function with a readable number of callers (3-15) and tests (1-25) as the example;
    otherwise the most-called one that tests reach."""
    paths = {fid: path for fid, path in db.execute('SELECT id, path FROM files')}
    rows = db.execute('SELECT s.id, s.qname, s.file_id FROM symbols s WHERE s.kind IN ("function", "method")').fetchall()
    production = {sid: (qname, paths[fid]) for sid, qname, fid in rows if not TEST_PATH.search(paths[fid])}
    callers = defaultdict(set)
    for caller, callee in db.execute('SELECT caller_id, callee_id FROM edges WHERE conf IN ("high", "medium")'):
        if callee in production:
            callers[callee].add(caller)
    ranked = sorted(callers, key=lambda sid: (-len(callers[sid]), production[sid][1], production[sid][0]))
    fallback = None
    readable = [sid for sid in ranked if 3 <= len(callers[sid]) <= 15][:limit]
    for sid in readable + ranked[:limit]:
        qname, path = production[sid]
        symbol = path + ':' + qname
        tests = verified_tests(engine, root, symbol)
        found = dict(symbol=symbol, name=qname, callers=len(callers[sid]), tests=tests)
        if tests and 3 <= found['callers'] <= 15 and tests <= 25:
            return found
        if tests and fallback is None:
            fallback = found
    return fallback


def collect(engine, root):
    with contextlib.redirect_stdout(io.StringIO()):
        engine.cmd_index(argparse.Namespace(root=root, full=False))
    db = engine.connect(root)
    q = lambda sql: db.execute(sql).fetchone()[0]
    languages = [row for row in db.execute('SELECT lang, COUNT(*) FROM files GROUP BY lang ORDER BY COUNT(*) DESC, lang')]
    meta = dict(db.execute('SELECT k, v FROM meta'))
    partial = json.loads(meta.get('partial_paths') or '[]')
    sources = sum(count for _, count in languages)
    return dict(
        project=os.path.basename(os.path.abspath(root)) or root,
        source_files=sources,
        definitions=q('SELECT COUNT(*) FROM symbols WHERE kind != "module"'),
        resolved_calls=q('SELECT COUNT(DISTINCT call_id) FROM edges'),
        languages={lang: count for lang, count in languages},
        partial_files=len(partial),
        small_project=sources < SMALL_PROJECT_FILES,
        git=os.path.isdir(os.path.join(root, '.git')) or os.path.isfile(os.path.join(root, '.git')),
        example=example(engine, db, root),
        root=os.path.abspath(root),
        launcher=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cg'),
    )


def commands(info):
    found = info['example']
    base = shlex.quote(info['launcher']) + ' --root ' + shlex.quote(info['root'])
    query = words(found['name']) if found else 'describe the behaviour'
    result = dict(search=base + ' search ' + shlex.quote(query))
    if found:
        result.update(impact=base + ' impact ' + shlex.quote(found['symbol']) + ' --depth 3',
                      tests=base + ' verify ' + shlex.quote(found['symbol']) + ' --plan')
    if info['git']:
        result['review'] = base + ' review --base HEAD'
    result['explore'] = shlex.quote(info['launcher']) + ' explore'
    return result


def render(info, lang):
    t = TEXT[lang]
    run = commands(info)
    lines = [t['title'].format(project=info['project']), '',
             t['summary'].format(files=info['source_files'], definitions=info['definitions'], resolved=info['resolved_calls'],
                                 languages=', '.join(f'{k} {v}' for k, v in info['languages'].items()) or '—')]
    if info['partial_files']:
        lines.append(t['partial'].format(n=info['partial_files']))
    lines += ['', t['small' if info['small_project'] else 'large'].format(files=info['source_files']), '', t['heading'], '',
              t['impact_title']]
    found = info['example']
    if found:
        lines += ['   ' + t['impact_body'].format(symbol=found['name'], callers=count(t['places'], found['callers']),
                                                tests=count(t['tests'], found['tests'])),
                  '   $ ' + run['impact'], '   $ ' + run['tests']]
    else:
        lines.append('   ' + t['impact_none'])
    lines += ['', t['search_title'], '   ' + t['search_body'], '   $ ' + run['search'], '', t['review_title']]
    lines += ['   ' + t['review_body'], '   $ ' + run['review']] if 'review' in run else ['   ' + t['review_nogit']]
    lines += ['', t['agent_title'], '   ' + t['agent_body'], '   «' + t['agent_prompt'] + '»', '', t['dashboard'], '   $ ' + run['explore']]
    return '\n'.join(lines)


def cli(engine, args):
    info = collect(engine, args.root)
    if args.json:
        print(json.dumps(dict(info, commands=commands(info), format='codegraph-start/1'), ensure_ascii=False, sort_keys=True))
    else:
        print(render(info, language(args.lang)))
