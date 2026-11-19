"""Deterministic evidence records, pagination and metrics; no project execution."""
import base64
import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def test_role(node):
    """Recognized cases are candidates for discovery, not a claim of execution."""
    path, name = node['file'], node['name'].split('.')[-1]
    test_file = bool(re.search(r'(^|/)(tests?|__tests__|spec)(/|$)|(_test|\.test|\.spec|Test)\.\w+$', path))
    if not test_file:
        return 'production'
    if node.get('language') == 'python' and node.get('kind') in ('function', 'method') and name.startswith('test_'):
        return 'test_case'
    if node.get('language') == 'php' and node.get('kind') == 'method' and name.startswith('test'):
        return 'test_case'
    if node.get('language') == 'go' and node.get('kind') == 'function' and path.endswith('_test.go'):
        if name != 'TestMain' and any(name.startswith(prefix) and (name == prefix or not name[len(prefix)].islower())
               for prefix in ('Test', 'Benchmark', 'Fuzz', 'Example')):
            return 'test_case'
    return 'test_support'


def edge_reason(recv, confidence):
    if recv in ('loaded_medium', 'loaded_low'):
        return 'dynamic_module_file'
    if recv == 'module':  # JS/TS: an import or require binding; low: a method of a file exporting an instance
        return 'module_method_candidate' if confidence == 'low' else 'module_binding'
    if recv == 'jsx':  # <Badge />: React calls the component
        return 'jsx_element'
    reasons = {'self': 'self_or_inherited_method', 'parent': 'ancestor_method', 'typed': 'inferred_receiver_type',
               'class': 'class_or_qualified_import', 'new': 'constructor', 'builtin': 'known_builtin'}
    if confidence == 'low':
        return 'name_candidate_unknown_receiver'
    return reasons.get(recv, 'static_name_resolution' if confidence == 'high' else 'unique_name_candidate')


def metrics(db):
    q = lambda sql: db.execute(sql).fetchone()[0]
    return dict(indexed_files=q('SELECT COUNT(*) FROM files'), definitions=q("SELECT COUNT(*) FROM symbols WHERE kind!='module'"),
                graph_nodes=q('SELECT COUNT(*) FROM symbols'), callsites=q('SELECT COUNT(*) FROM calls'),
                resolved_callsites=q('SELECT COUNT(DISTINCT call_id) FROM edges'),
                distinct_links=q('SELECT COUNT(*) FROM (SELECT DISTINCT caller_id,callee_id FROM edges)'),
                callsite_edges=q('SELECT COUNT(*) FROM edges'), registered_references=q('SELECT COUNT(*) FROM symbol_refs WHERE target_id IS NOT NULL'),
                low_edges=q("SELECT COUNT(*) FROM edges WHERE conf='low'"))


def page_offset(cursor, revision, query, offset=0):
    if cursor:
        try:
            payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if payload['revision'] != revision or payload['query'] != digest(query):
                raise ValueError('Cursor is stale or belongs to another query: refresh the view.')
            offset = payload['offset']
        except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError('Invalid or stale cursor: refresh the view.') from error
    if not isinstance(offset, int) or not 0 <= offset <= 1000000:
        raise ValueError('Offset outside the limit.')
    return offset


def page_info(total, offset, limit, revision, query):
    shown = max(0, min(limit, total - offset))
    next_offset = offset + shown
    cursor = None
    if next_offset < total:
        cursor = base64.urlsafe_b64encode(json.dumps(dict(revision=revision, query=digest(query), offset=next_offset), sort_keys=True).encode()).decode()
    return dict(total=total, shown=shown, offset=offset, omitted=max(0, total-next_offset), next_cursor=cursor,
                status='partial' if offset or next_offset < total else 'complete')


class Snippets:
    def __init__(self, project, known=()):
        self.project = project
        self.items = {}
        self.known = set(known)

    def add(self, source):
        lines = source['lines']
        record = dict(file=source['file'], source_revision=self.project.files[source['file']]['sha'],
                      start_line=lines[0]['number'] if lines else 0, end_line=lines[-1]['number'] if lines else 0,
                      text_hash=digest(lines), lines=lines, truncated=source['truncated'])
        if source.get('window'):record['window']=source['window']
        sid = digest(record)[:24]
        if sid in self.items and self.items[sid] != record:
            raise ValueError('Extract identifier collision: request a new packet.')
        if sid not in self.known:
            self.items[sid] = record
        return sid

    def chain(self, items):
        for item in items:
            for step in item['chain']:
                if 'source' in step:
                    step['snippet_ref'] = self.add(step.pop('source'))
        return items
