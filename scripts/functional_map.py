"""Bounded functional overview from declarations and indexed witnesses; no inferred purpose."""
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import runpy

FEATURES = runpy.run_path(str(Path(__file__).with_name('features.py')))
ROLE = FEATURES['EVIDENCE']['test_role']
MAX_SQL_ROWS, MAX_EDGE_ROWS, MAX_CANDIDATE_ROWS = 10000, 3000, 500
MAX_LINKS, MAX_WITNESSES, MAX_CANDIDATES, MAX_SHARED = 500, 3, 24, 100
SYMBOL = '''SELECT s.id,f.path AS file,f.lang AS language,s.qname AS name,
    s.kind,s.line FROM symbols s JOIN files f ON f.id=s.file_id'''


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def build(project, query='', limit=25, min_conf='medium', max_chars=12000):
    """Overview only: use feature/prepare/verify for flow details and proposed tests."""
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError('Flow query must contain at most 200 characters.')
    if not isinstance(min_conf, str) or min_conf not in project.cg.CONF_RANK:
        raise ValueError('Invalid flow confidence.')
    if type(limit) is not int or not 1 <= limit <= 100 or type(max_chars) is not int or not 512 <= max_chars <= 16000:
        raise ValueError('Invalid flow limits.')
    if project.cg.analysis_inputs(project.root)[2] != project.meta.get('analysis_fingerprint'):
        raise ValueError('Engine changed: run index before flows.')
    scans = dict(sql_rows=0, sql_row_limit=MAX_SQL_ROWS, edge_rows=0, edge_row_limit=MAX_EDGE_ROWS,
                 candidate_rows=0, candidate_row_limit=MAX_CANDIDATE_ROWS)

    def read(sql, args=(), cap=2):
        # The lookahead consumes the same global budget as ordinary rows.
        remaining = MAX_SQL_ROWS - scans['sql_rows']
        if remaining <= 0:
            return [], True
        count = min(cap + 1, remaining)
        rows = project.db.execute(sql + ' LIMIT ?', [*args, count]).fetchall()
        scans['sql_rows'] += len(rows)
        cut = len(rows) > cap or (count <= cap and len(rows) == count)
        return rows[:cap], cut

    def technical(row):
        node = dict(row)
        project._read(node['file'])
        return dict(identity=FEATURES['identity'](node), symbol_id=node['id'], line=node['line'],
                    selector=node['file'] + ':' + node['name'], source_hash=project.source_hashes[node['file']])

    def proof(ref):
        item = FEATURES['ARCH']['evidence'](project, ref)
        item.pop('source', None)
        if ref.get('sha256'):
            item['declared_sha256'] = ref['sha256']
        return item

    declared, issues = FEATURES['declarations'](project)
    raw = project.config_sources.get('.code-graph-manifest.json', b'')
    manifest_hash = hashlib.sha256(raw).hexdigest()
    owners, symbols, nodes = defaultdict(set), {}, []
    resolution_cut = False
    for position, feature in enumerate(declared):
        fid, refs = 'declared:' + feature['id'], []
        for role, values in [('entrypoint', feature.get('entrypoints', [])), ('member', feature.get('members', []))]:
            for ref in values:
                sql, args = SYMBOL + ' WHERE f.path=?', [ref['file']]
                if ref.get('symbol') is None:
                    sql += " AND s.kind='module'"
                else:
                    sql += ' AND s.qname=?'; args.append(ref['symbol'])
                if ref.get('line') is not None:
                    sql += ' AND s.line=?'; args.append(ref['line'])
                rows, cut = read(sql + ' ORDER BY s.line,s.id', args)
                status = 'ambiguous' if len(rows) > 1 else 'unresolved_limit' if cut else 'resolved' if rows else 'missing'
                resolution_cut |= status == 'unresolved_limit'
                candidates = [technical(row) for row in rows]
                refs.append(dict(reference=ref, role=role, status=status, candidates=candidates, candidates_total=None if cut else len(rows)))
                if status == 'resolved':
                    sid = rows[0]['id']; owners[sid].add(fid); symbols[sid] = candidates[0]
        contracts = []
        for contract in feature.get('contracts', []):
            states = {proof(ref)['status'] for ref in contract.get('evidence', [])}
            state = 'stale_declaration' if 'stale_declaration' in states else 'referenced_declaration' if states == {'verified_declaration'} else 'unverified_declaration'
            contracts.append(dict(id=contract['id'], status=state))
        resolved = sum(ref['status'] == 'resolved' for ref in refs)
        nodes.append(dict(id=fid, title=feature['title'], summary=feature.get('summary', ''), stage=feature.get('stage', 'active'),
                          provenance='project_declaration', source=dict(file='.code-graph-manifest.json', sha256=manifest_hash, json_pointer='/features/' + str(position)),
                          source_status='resolved' if refs and resolved == len(refs) else 'partial' if resolved else 'not_resolved',
                          references=refs, files=sorted({row['identity']['file'] for ref in refs if ref['status'] == 'resolved' for row in ref['candidates']}),
                          evidence=[proof(ref) for ref in feature.get('evidence', [])], contracts=contracts, resources=feature.get('resources', []),
                          continuation=dict(feature=fid, prepare=fid, verify_selectors=sorted({row['selector'] for ref in refs if ref['status'] == 'resolved' for row in ref['candidates']}))))
    for node in nodes:
        for ref in node['references']:
            for item in ref['candidates']:
                item['feature_membership_count'] = None if resolution_cut else len(owners[item['symbol_id']])
    tokens = query.casefold().split()
    matched = sorted((node for node in nodes if all(word in (node['title'] + ' ' + node['summary']).casefold() for word in tokens)), key=lambda node: node['id'])
    selected = matched[:limit]
    selected_count = len(selected)
    selected_ids = {node['id'] for node in selected}
    sources = [] if resolution_cut else sorted((sid for sid in owners if owners[sid] & selected_ids), key=lambda sid: dumps(symbols[sid]['identity']))
    links, edge_cut = {}, resolution_cut
    allowed = [name for name, rank in project.cg.CONF_RANK.items() if rank >= project.cg.CONF_RANK[min_conf]]
    for start in range(0, len(sources), 200):
        remaining = MAX_EDGE_ROWS - scans['edge_rows']
        if remaining <= 1:
            edge_cut = True; break
        batch = sources[start:start + 200]
        marks, conf_marks = ','.join('?' for _ in batch), ','.join('?' for _ in allowed)
        before = scans['sql_rows']
        rows, cut = read('''SELECT e.caller_id,e.callee_id,e.conf,c.line,f.path AS file
            FROM edges e JOIN calls c ON c.id=e.call_id JOIN files f ON f.id=c.file_id
            JOIN symbols target ON target.id=e.callee_id JOIN files tf ON tf.id=target.file_id
            WHERE e.caller_id IN (''' + marks + ') AND e.conf IN (' + conf_marks + ''')
            ORDER BY f.path,c.line,tf.path,target.qname,e.conf,e.callee_id''', batch + allowed, remaining - 1)
        scans['edge_rows'] += scans['sql_rows'] - before
        edge_cut |= cut
        for row in rows:
            caller, callee = row['caller_id'], row['callee_id']
            if len(owners[caller]) != 1 or len(owners[callee]) != 1:
                continue
            source, target = next(iter(owners[caller])), next(iter(owners[callee]))
            if source == target or source not in selected_ids or target not in selected_ids:
                continue
            key = source, target
            item = links.setdefault(key, dict(source=source, target=target, kind='static_call', witnesses=[], observed_callsites=0))
            item['observed_callsites'] += 1
            if len(item['witnesses']) < MAX_WITNESSES:
                project._read(row['file'])
                item['witnesses'].append(dict(caller=symbols[caller]['identity'], callee=symbols[callee]['identity'],
                                               file=row['file'], line=row['line'], source_hash=project.source_hashes[row['file']], confidence=row['conf']))
        if cut:
            break
    for item in links.values():
        item['callsites_total'] = None if edge_cut else item['observed_callsites']
        item['witnesses_output'] = dict(total=item['callsites_total'], shown=len(item['witnesses']),
                                        omitted=None if edge_cut else item['observed_callsites'] - len(item['witnesses']),
                                        reasons=['scan_limit'] if edge_cut else ['limit'] if item['observed_callsites'] > len(item['witnesses']) else [])
    shared = [dict(symbol=symbols[sid], features=sorted(owners[sid] & selected_ids), other_features=len(owners[sid] - selected_ids))
              for sid in sorted(symbols, key=lambda sid: dumps(symbols[sid]['identity'])) if len(owners[sid]) > 1 and owners[sid] & selected_ids]

    candidate_sql = '''SELECT s.id,f.path AS file,f.lang AS language,s.qname AS name,s.kind AS kind,s.line AS line,
        o.kind AS reason,o.line AS evidence_line,f.path AS evidence_file
        FROM observations o JOIN symbols s ON s.id=o.owner_id JOIN files f ON f.id=s.file_id
        WHERE o.kind IN ('registration.mcp','registration.site')
        UNION ALL SELECT s.id,f.path,f.lang,s.qname,s.kind,s.line,'registered_handler',r.line,rf.path
        FROM symbol_refs r JOIN symbols s ON s.id=r.target_id JOIN files f ON f.id=s.file_id
        JOIN files rf ON rf.id=r.file_id WHERE r.kind='entrypoint'
        ORDER BY file,line,name,reason'''
    before = scans['sql_rows']
    candidate_rows, candidate_cut = read(candidate_sql, cap=MAX_CANDIDATE_ROWS - 1)
    scans['candidate_rows'] += scans['sql_rows'] - before
    eligible = [row for row in candidate_rows if ROLE(dict(row)) == 'production']
    if not eligible and not candidate_cut:
        before = scans['sql_rows']
        candidate_rows, candidate_cut = read(SYMBOL + " WHERE s.kind IN ('function','method') ORDER BY f.path,s.line,s.qname", cap=MAX_CANDIDATE_ROWS - scans['candidate_rows'] - 1)
        scans['candidate_rows'] += scans['sql_rows'] - before
        eligible = [row for row in candidate_rows if ROLE(dict(row)) == 'production']
    candidates, seen = [], set()
    for row in eligible:
        if row['id'] in seen or not all(word in (row['file'] + ' ' + row['name']).casefold() for word in tokens):
            continue
        seen.add(row['id'])
        candidate = technical(row)
        ident = candidate['identity']
        same, cut = read(SYMBOL + ' WHERE f.path=? AND f.lang=? AND s.qname=? AND s.kind=?', [ident[key] for key in ('file', 'language', 'name', 'kind')], 1)
        unique = len(same) == 1 and not cut
        cid = 'candidate:' + FEATURES['digest'](ident) if unique else 'candidate-revision:' + project.meta['revision'] + ':' + str(row['id'])
        reason = row['reason'] if 'reason' in row.keys() else 'definition_fallback'
        evidence_file = row['evidence_file'] if 'evidence_file' in row.keys() else row['file']
        project._read(evidence_file)
        evidence_line = row['evidence_line'] if 'evidence_line' in row.keys() else row['line']
        candidates.append(dict(id=cid, name=row['name'], summary='', provenance='code_candidate', identity_status='unique' if unique else 'ambiguous_or_limited',
                               technical=candidate, evidence=dict(kind=reason, file=evidence_file, line=evidence_line, source_hash=project.source_hashes[evidence_file])))
        candidate_cut |= cut and not same
    ordered_links = [links[key] for key in sorted(links)]
    result = dict(format='codegraph-functional-map/1', engine_version=project.cg.VERSION, revision=project.meta['revision'], content_revision=project.meta['content_revision'],
                  query=dict(text=query, limit=limit, confidence=min_conf), link_scope='selected_nodes', declarations_status='invalid' if issues else 'present' if declared else 'no_declared_features',
                  nodes=selected, links=ordered_links[:MAX_LINKS], entrypoint_candidates=candidates[:MAX_CANDIDATES], shared_references=shared[:MAX_SHARED], issues=issues,
                  scan=scans, freshness='consulted_sources', verification=dict(mode='deferred_to_feature_prepare_verify', execution_performed=False),
                  continuation=['features --json', 'prepare declared:<id> --json', 'verify <file:symbol> --plan --json', 'source <file> --line <N> --json'],
                  limitations=['Purpose and membership are declarations, not runtime proof.', 'Links cover direct calls between uniquely assigned resolved references only.',
                               'Candidates use bounded indexed registrations or definition fallback; names do not establish purpose.'])
    totals = dict(nodes=len(matched), links=None if edge_cut else len(ordered_links), entrypoint_candidates=None if candidate_cut else len(candidates), shared_references=None if resolution_cut else len(shared))
    observed = dict(nodes=len(matched), links=len(ordered_links), entrypoint_candidates=len(candidates), shared_references=len(shared))
    reasons = {key: ['scan_limit'] if value is None else [] for key, value in totals.items()}
    for key in totals:
        if len(result[key]) < observed[key]:
            reasons[key].append('limit')

    def size():
        visible = {node['id'] for node in result['nodes']}
        before_links, before_shared = len(result['links']), len(result['shared_references'])
        result['links'][:] = [link for link in result['links'] if link['source'] in visible and link['target'] in visible]
        for item in result['shared_references']:
            missing = set(item['features']) - visible
            item['features'] = sorted(set(item['features']) & visible)
            item['other_features'] += len(missing)
        result['shared_references'][:] = [item for item in result['shared_references'] if item['features']]
        for key, before in [('links', before_links), ('shared_references', before_shared)]:
            if len(result[key]) < before and 'max_chars' not in reasons[key]:
                reasons[key].append('max_chars')
        result['omissions'] = {key: dict(total=totals[key], observed=observed[key], shown=len(result[key]), omitted=None if totals[key] is None else totals[key] - len(result[key]), reasons=reasons[key]) for key in totals}
        incomplete = any(totals[key] is None or totals[key] != len(result[key]) for key in totals)
        incomplete |= any(link['witnesses_output']['omitted'] != 0 for link in result['links'])
        incomplete |= any(ref['candidates_total'] is None for node in result['nodes'] for ref in node['references'])
        result['status'] = 'partial' if incomplete else 'ready'
        result['budget'] = dict(max_chars=max_chars, actual_chars=0, unit='unicode_characters')
        for _ in range(5):
            actual = len(dumps(result)) + 1
            if actual == result['budget']['actual_chars']:
                break
            result['budget']['actual_chars'] = actual
        return len(dumps(result)) + 1

    for key in ('entrypoint_candidates', 'shared_references', 'links', 'nodes'):
        while size() > max_chars and result[key]:
            result[key].pop()
            if 'max_chars' not in reasons[key]:
                reasons[key].append('max_chars')
    if size() > max_chars or not result['nodes'] and selected_count or not result['entrypoint_candidates'] and candidates and not selected_count:
        return dict(format=result['format'], status='budget_insufficient', budget=dict(max_chars=max_chars), continuation='Increase max_chars or narrow query.')
    return result
