"""Evidence-backed feature cards and change briefings. Source and declarations are data."""
import argparse
from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import re
import runpy

HERE = Path(__file__).parent
ARCH = runpy.run_path(str(HERE / 'architecture.py'))
EVIDENCE = runpy.run_path(str(HERE / 'evidence.py'))
CHANGE = runpy.run_path(str(HERE / 'change_analysis.py'))
WINDOW = runpy.run_path(str(HERE / 'source_windows.py'))
ENTRY = runpy.run_path(str(HERE / 'entrypoints.py'))
MAX_FEATURES, MAX_ROOTS, MAX_NODES, MAX_EDGES = 100, 16, 120, 500


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def identity(node):
    return dict(file=node['file'], language=node['language'], name=node['name'], kind=node['kind'])


def key(node):
    return digest(identity(node))[:24]


def declarations(project):
    raw = project.config_sources.get('.code-graph-manifest.json')
    if not raw:
        return [], []
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get('format') != 'codegraph-manifest/1':
            raise ValueError('Unrecognized manifest format.')
        items = value.get('features', [])
        if not isinstance(items, list) or len(items) > MAX_FEATURES:
            raise ValueError('features must contain at most 100 cards.')
        ids = set()
        def references(refs, limit=MAX_ROOTS):
            return isinstance(refs, list) and len(refs) <= limit and all(
                isinstance(ref, dict) and isinstance(ref.get('file'), str) and len(ref['file']) <= 1000
                and (ref.get('symbol') is None or isinstance(ref['symbol'], str) and len(ref['symbol']) <= 500)
                and (ref.get('line') is None or type(ref['line']) is int and ref['line'] > 0) for ref in refs)
        def proofs(refs):
            return isinstance(refs, list) and len(refs) <= 20 and all(isinstance(ref, dict)
                and isinstance(ref.get('file'), str) and type(ref.get('line')) is int and ref['line'] > 0
                and (ref.get('sha256') is None or isinstance(ref['sha256'], str) and re.fullmatch(r'[a-fA-F0-9]{64}', ref['sha256'])) for ref in refs)
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not re.fullmatch(r'[\w.-]{1,80}', item['id']) or item['id'] in ids:
                raise ValueError('Each feature needs a unique id (letters, numbers, . - _).')
            ids.add(item['id'])
            if not isinstance(item.get('title'), str) or not 1 <= len(item['title']) <= 200 or not isinstance(item.get('summary', ''), str) or len(item.get('summary', '')) > 2000:
                raise ValueError('Invalid feature title or description.')
            if item.get('stage', 'active') not in ('planned', 'active') or not references(item.get('entrypoints', [])) or not references(item.get('members', [])):
                raise ValueError('Invalid stage, entrypoints or members.')
            if len(item.get('entrypoints', [])) + len(item.get('members', [])) > MAX_ROOTS:
                raise ValueError('At most 16 references per feature.')
            resources, contracts = item.get('resources', []), item.get('contracts', [])
            if not isinstance(resources, list) or len(resources) > 20 or not all(isinstance(name, str) and len(name) <= 100 for name in resources):
                raise ValueError('resources must be a list of identifiers.')
            if not isinstance(contracts, list) or len(contracts) > 20 or not proofs(item.get('evidence', [])):
                raise ValueError('Invalid contracts or evidence.')
            seen = set()
            for contract in contracts:
                if not isinstance(contract, dict) or not isinstance(contract.get('id'), str) or not 1 <= len(contract['id']) <= 80 or contract['id'] in seen or not isinstance(contract.get('description'), str) or not 1 <= len(contract['description']) <= 2000 or not proofs(contract.get('evidence', [])):
                    raise ValueError('Each contract needs valid id, description and evidence.')
                seen.add(contract['id'])
        return items, []
    except (ValueError, AttributeError, TypeError) as error:
        return [], [dict(status='invalid_features', message=str(error))]


def resolve(project, ref):
    if ref['file'] not in project.files:
        return dict(reference=ref, status='missing', candidates=[])
    sql = 'SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.path=?'
    args = [ref['file']]
    if ref.get('symbol') is None:
        sql += " AND s.kind='module'"
    else:
        sql += ' AND s.qname=?'
        args.append(ref['symbol'])
    if ref.get('line') is not None:
        sql += ' AND s.line=?'
        args.append(ref['line'])
    rows = [project._symbol(row[0]) for row in project.db.execute(sql + ' ORDER BY s.line,s.id LIMIT 3', args)]
    return dict(reference=ref, status='resolved' if len(rows) == 1 else 'ambiguous' if rows else 'missing', candidates=rows)


def catalog(project, query=''):
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError('Feature search outside the limit.')
    declared, issues = declarations(project)
    tokens=query.casefold().split()
    items = []
    for item in declared:
        refs = [resolve(project, ref) for ref in item.get('entrypoints', []) + item.get('members', [])]
        count = sum(ref['status'] == 'resolved' for ref in refs)
        items.append(dict(id='declared:' + item['id'], title=item['title'], summary=item.get('summary', ''),
                          stage=item.get('stage', 'active'), provenance='project_declaration', resolved=count, references=len(refs),
                          source_status='resolved' if refs and count == len(refs) else 'partial' if count else 'not_resolved'))
    items=[item for item in items if all(token in (item['title']+' '+item['summary']).casefold() for token in tokens)]
    candidates,candidate_total=ENTRY['discover'](project,EVIDENCE['test_role'],allow_fallback=not declared,query=query)
    for candidate in candidates:
        node=candidate['node']
        items.append(dict(id='symbol:' + str(node['id']),title=node['name'],summary='',stage='active',
                          provenance='code_candidate',resolved=1,references=1,source_status='indexed',location=identity(node),
                          entrypoint_evidence=candidate['evidence'],generated_path_hint=candidate['generated_path_hint']))
    tokens = query.casefold().split()
    matched=items
    readme = project.config_sources.get('README.md')
    introduction = None
    if readme:
        introduction = dict(file='README.md', sha256=hashlib.sha256(readme).hexdigest(), provenance='project_documentation',
                            text='\n'.join(readme.decode('utf-8', 'replace').splitlines()[:20])[:3000])
    return dict(format='codegraph-features/1', revision=project.meta['revision'], content_revision=project.meta['content_revision'],
                items=matched, total=len(items), query=query, candidate_total=candidate_total, candidates_truncated=candidate_total>len(candidates), issues=issues, introduction=introduction,
                limitations=['Candidate cards show where to start; their functional purpose is not inferred from the name.'])


def choose(project, feature_id):
    if not isinstance(feature_id, str) or len(feature_id) > 200:
        raise ValueError('Invalid feature identifier.')
    if feature_id.startswith('declared:'):
        items, issues = declarations(project)
        matches = [item for item in items if 'declared:' + item['id'] == feature_id]
        if not matches:
            raise ValueError('Feature missing from the valid manifest.')
        value = dict(matches[0], provenance='project_declaration')
        return value
    if feature_id.startswith('symbol:'):
        node = project._symbol(int(feature_id[7:]))
        return dict(id=feature_id, title=node['name'], summary='', stage='active', provenance='code_candidate',
                    entrypoints=[dict(file=node['file'], symbol=None if node['kind'] == 'module' else node['name'], line=node['line'])], members=[], resources=[], contracts=[])
    if any(item['id'] == feature_id for item in declarations(project)[0]):
        suggested = 'declared:' + feature_id
        raise ValueError(f'Unknown feature ID; did you mean {suggested}?')
    raise ValueError('Use declared:<id> or symbol:<id> from the current index.')


def graph(project, roots, confidence, depth):
    if not isinstance(confidence,str) or confidence not in project.cg.CONF_RANK or type(depth) is not int or not 1 <= depth <= 3:
        raise ValueError('Invalid confidence or depth.')
    allowed = [name for name, rank in project.cg.CONF_RANK.items() if rank >= project.cg.CONF_RANK[confidence]]
    ids, frontier, truncated = set(roots), set(roots), False
    for _ in range(depth):
        found = set()
        for sid in sorted(frontier):
            rows = project.db.execute('SELECT DISTINCT e.callee_id FROM edges e JOIN symbols s ON s.id=e.callee_id JOIN files f ON f.id=s.file_id WHERE e.caller_id=? AND e.conf IN (' + ','.join('?' for _ in allowed) + ') ORDER BY f.path,s.line,e.callee_id LIMIT 121', [sid] + allowed)
            for (other,) in rows:
                if other not in ids and other not in found:
                    if len(ids) + len(found) >= MAX_NODES:
                        truncated = True
                        continue
                    found.add(other)
        frontier = found - ids
        ids.update(found)
        if not frontier:
            break
    # Direct consumers are useful even before the separate verification traversal.
    for sid in roots:
        for (other,) in project.db.execute('SELECT DISTINCT caller_id FROM edges WHERE callee_id=? AND conf IN (' + ','.join('?' for _ in allowed) + ') ORDER BY caller_id LIMIT 121', [sid] + allowed):
            if len(ids) < MAX_NODES or other in ids:
                ids.add(other)
            else:
                truncated = True
    args = argparse.Namespace(root=str(project.root), symbol=None, depth=depth, min_conf=confidence, max_nodes=MAX_NODES, max_edges=MAX_EDGES)
    try:
        data = project.cg.export_data(project.db, args, node_ids=ids)
    except SystemExit:
        # Never silently return a dense partial graph as if complete.
        raise ValueError('Flow exceeds 500 edges: narrow depth or declared references.')
    for node in data['nodes']:
        project._read(node['file'])
    data['embedded'] = True
    data['selection_truncated'] = truncated
    return data


def detail(project, feature_id, confidence='medium', depth=2, verification_symbols=()):
    feature = choose(project, feature_id)
    refs = [dict(resolve(project, ref), role=role) for role, values in [('entrypoint', feature.get('entrypoints', [])), ('member', feature.get('members', []))] for ref in values]
    roots = list(dict.fromkeys(ref['candidates'][0]['id'] for ref in refs if ref['status'] == 'resolved'))
    data = graph(project, roots, confidence, depth)
    proofs = [ARCH['evidence'](project, item) for item in feature.get('evidence', [])]
    contracts = []
    for item in feature.get('contracts', []):
        evidence = [ARCH['evidence'](project, ref) for ref in item.get('evidence', [])]
        statuses = {ref['status'] for ref in evidence}
        status = 'stale_declaration' if 'stale_declaration' in statuses else 'referenced_declaration' if evidence and statuses == {'verified_declaration'} else 'unverified_declaration'
        contracts.append(dict(id=item['id'], description=item['description'], provenance='project_declaration', status=status, evidence=evidence))
    architecture = project.architecture() if feature.get('resources') else dict(declarations=[], links=[], truncated=False)
    resources = [item for item in architecture['declarations'] if item.get('id') in feature.get('resources', [])]
    missing_resources = [name for name in feature.get('resources', []) if not any(item.get('id') == name for item in resources)]
    if not isinstance(verification_symbols,(list,tuple)) or len(verification_symbols)>8 or any(not isinstance(ref,str) or not 1<=len(ref)<=1000 for ref in verification_symbols):
        raise ValueError('At most 8 qualified verification selectors.')
    selected=[]
    for selector in verification_symbols:
        try:sid=project.cg.resolve_one(project.db,selector)
        except SystemExit as error:raise ValueError(str(error)) from error
        if sid not in roots and sid not in selected:selected.append(sid)
    verification = CHANGE['plan'](project, roots+selected, confidence, min(5, depth + 2),with_targets=True)
    verification['subjects']=[dict(symbol=project._symbol(sid),role='root' if sid in roots else 'selected_collaborator') for sid in roots+selected]
    collaborators=[dict(id=node['id'],**identity(dict(node,name=node['label'])),line=node['line']) for node in data['nodes'] if node['id'] not in roots and EVIDENCE['test_role'](dict(node,name=node['label']))=='production']
    for ref in refs:
        if ref['status'] != 'resolved':
            verification['broader_verification_required'] = True
    if not roots or data['selection_truncated'] or missing_resources or any(item['status']!='referenced_declaration' for item in contracts) or any(item['status']!='verified_declaration' for item in proofs):
        verification['broader_verification_required'] = True
    return dict(format='codegraph-feature/1', id=feature_id, title=feature['title'], summary=feature.get('summary', ''),
                stage=feature.get('stage', 'active'), provenance=feature['provenance'], revision=project.meta['revision'],
                content_revision=project.meta['content_revision'], references=refs, roots=roots, evidence=proofs,
                graph=data, collaborators=collaborators, contracts=contracts, resources=resources, missing_resources=missing_resources, verification=verification,
                confidence=confidence, depth=depth, limitations=[
                    'Declared purpose and contracts do not prove behavior.',
                    'The graph shows static calls; resources are separate declarations.',
                    'The flow is bounded by the chosen depth; check external boundaries too.'])


def snapshot(project, card):
    symbols = []
    for node in card['graph']['nodes']:
        lines = project._read(node['file'])
        body = '\n'.join(lines[node['line'] - 1:node['end_line']])
        record = dict(identity=identity(dict(node, name=node['label'])), signature=node['signature'], body_hash=digest(body),
                      source_hash=project.files[node['file']]['sha'])
        symbols.append(record)
    key_of = {node['id']: key(dict(node, name=node['label'])) for node in card['graph']['nodes']}
    links = []
    for edge in card['graph']['edges']:
        for call in edge['calls']:
            text = project._read(call['file'])[call['line'] - 1].strip()
            links.append(dict(source=key_of[edge['source']], target=key_of[edge['target']], confidence=call['confidence'], file=call['file'], text_hash=digest(text)))
    contracts = [dict(id=item['id'], description=item['description'], status=item['status'], evidence_hash=digest(item['evidence'])) for item in card['contracts']]
    return dict(format='codegraph-feature-snapshot/1', project=digest(str(project.root)), feature=card['id'], engine=project.cg.VERSION,
                revision=card['revision'], content_revision=card['content_revision'], symbols=symbols, links=links, contracts=contracts, verification_references=[dict(file=item['symbol']['file'],symbol=item['symbol']['name']) for item in card['verification']['subjects'] if item['role']=='selected_collaborator'], feature_evidence_hash=digest(card['evidence']),
                provenance=card['provenance'], candidate_reference=({k:v for k,v in card['references'][0]['reference'].items() if k!='line'} if card['provenance']=='code_candidate' else None),
                prior_tests=[dict(identity=identity(item['symbol']),distance=item['distance']) for item in card['verification']['tests'][:100]],
                prior_tests_total=len(card['verification']['tests']), prior_tests_truncated=len(card['verification']['tests'])>100,
                confidence=card['confidence'], depth=card['depth'], selection_truncated=card['graph']['selection_truncated'])


def prepare(project, feature_id, intent='', max_chars=12000, confidence='medium', depth=2, preparation_id=None, verification_symbols=()):
    if not isinstance(intent,str) or len(intent)>1000 or type(max_chars) is not int or not 512<=max_chars<=100000:
        raise ValueError('Invalid intent or budget (512..100000 characters).')
    card=detail(project,feature_id,confidence,depth,verification_symbols)
    baseline=snapshot(project,card)
    builder=runpy.run_path(str(HERE/'feature_packets.py'))
    return builder['build'](project,card,intent,max_chars,preparation_id),baseline


def compare(project, baseline):
    if baseline.get('format') != 'codegraph-feature-snapshot/1' or baseline.get('project') != digest(str(project.root)) or baseline.get('engine') != project.cg.VERSION:
        raise ValueError('Preparation is incompatible with the project or engine.')
    previous_tests=dict(prior_tests=baseline.get('prior_tests',[]),prior_tests_total=baseline.get('prior_tests_total',len(baseline.get('prior_tests',[]))),prior_tests_truncated=baseline.get('prior_tests_truncated',False),before_revision=baseline['content_revision'])
    feature_id=baseline['feature']
    if baseline.get('provenance')=='code_candidate':
        resolved=resolve(project,baseline['candidate_reference'])
        if resolved['status']!='resolved':
            return dict(format='codegraph-feature-review/1',feature=feature_id,status='reference_not_resolved',reference=resolved,
                        **previous_tests,verification=dict(tests=[],broader_verification_required=True),
                        limitations=['Missing or ambiguous identity: do not reuse SQLite IDs from the old revision.'])
        feature_id='symbol:'+str(resolved['candidates'][0]['id'])
    verification_refs=[resolve(project,ref) for ref in baseline.get('verification_references',[])]
    selectors=[ref['reference']['file']+':'+ref['reference']['symbol'] for ref in verification_refs if ref['status']=='resolved']
    try:
        current = detail(project, feature_id, baseline['confidence'], baseline['depth'],selectors)
        if any(ref['status']!='resolved' for ref in verification_refs):current['verification']['broader_verification_required']=True
    except ValueError as error:
        if feature_id.startswith('declared:') and not any('declared:'+item['id']==feature_id for item in declarations(project)[0]):
            return dict(format='codegraph-feature-review/1',feature=feature_id,status='declaration_not_available',
                        **previous_tests,verification=dict(tests=[],broader_verification_required=True),limitations=[str(error)])
        raise
    after = snapshot(project, current)
    def group(items):
        result = {}
        for item in items:
            result.setdefault(digest(item['identity'])[:24], []).append(item)
        return result
    before_nodes, after_nodes = group(baseline['symbols']), group(after['symbols'])
    changes = []
    for symbol in sorted(set(before_nodes) | set(after_nodes)):
        old, new = before_nodes.get(symbol, []), after_nodes.get(symbol, [])
        if len(old) > 1 or len(new) > 1:
            changes.append(dict(kind='ambiguous_identity', identity=(new or old)[0]['identity']))
        elif not old:
            changes.append(dict(kind='added_to_selected_graph', identity=new[0]['identity']))
        elif not new:
            changes.append(dict(kind='absent_from_selected_graph', identity=old[0]['identity']))
        else:
            for field, kind in [('signature', 'signature_changed'), ('body_hash', 'source_window_changed'), ('source_hash', 'source_file_changed')]:
                if old[0][field] != new[0][field]:
                    changes.append(dict(kind=kind, identity=new[0]['identity']))
    old_edges = Counter(digest(item) for item in baseline['links'])
    new_edges = Counter(digest(item) for item in after['links'])
    old_contracts, new_contracts = {item['id']: item for item in baseline['contracts']}, {item['id']: item for item in after['contracts']}
    contract_changes = [dict(id=name, before=old_contracts.get(name), after=new_contracts.get(name)) for name in sorted(set(old_contracts) | set(new_contracts)) if old_contracts.get(name) != new_contracts.get(name)]
    evidence_changed=baseline.get('feature_evidence_hash')!=after['feature_evidence_hash']
    return dict(format='codegraph-feature-review/1', feature=baseline['feature'], **previous_tests, content_revision=after['content_revision'],
                changes=changes, callsites_added=sum((new_edges - old_edges).values()), callsites_removed=sum((old_edges - new_edges).values()),
                verification_references=verification_refs, contracts=contract_changes, feature_evidence_changed=evidence_changed, evidence=current['evidence'], verification=current['verification'],
                selection_truncated=baseline['selection_truncated'] or after['selection_truncated'],
                status='review_required' if changes or contract_changes or evidence_changed or old_edges != new_edges else 'no_selected_graph_change',
                limitations=['Comparison of the selected subgraph, not project certification.',
                             'Signature, text and link differences require reading and tests; no test was run.',
                             'Renames and moves are not paired automatically.'])
