"""Guided UI API tests: real local HTTP, synthetic projects, no project execution."""
import contextlib
import hashlib
import importlib.util
import json
from pathlib import Path
import runpy
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_workbench_test', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
workbench = runpy.run_path(str(REPO / 'scripts/workbench.py'))

SOURCES = {
 'pkg/core.py': 'def work(): return 1\n',
 'pkg/site.py': "from fastapi import FastAPI\nfrom pkg.core import work\napp = FastAPI()\n@app.get('/value')\ndef view(): return work()\n",
 'pkg/mcp_server.py': "from mcp.server.fastmcp import FastMCP\nfrom pkg.core import work\nmcp = FastMCP('fixture')\n@mcp.tool()\ndef get_value(): return work()\n",
 'pipeline.py': 'from pkg.core import work\ndef run(): return work()\n',
 'tests/test_core.py': 'from pkg.core import work\ndef test_work(): return work()\n',
 'resources.py': "import sqlite3\nimport subprocess\ndef producer():\n    with open('data/export.json', 'w') as handle: handle.write('{}')\ndef consumer():\n    return open('data/export.json').read()\ndef database(): return sqlite3.connect('data/site.db')\ndef process(): return subprocess.run(['python3', 'worker.py'])\n",
 'pyproject.toml': '[project]\nname="fixture"\n[project.scripts]\nfixture-pipeline="pipeline:run"\n',
 '.github/workflows/publish.yml': 'name: publish\njobs:\n  publish:\n    steps:\n      - uses: actions/deploy-pages@v4\n',
}


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cg-workbench-test-')
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.root = self.workspace / 'project space'
        self.root.mkdir()
        for name, source in SOURCES.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)
        self.server = workbench['create_server'](cg, self.workspace)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
        self.token = self.server.session.token

    def request(self, path, body=None, auth=True, extra=None, raw=None):
        headers = {'X-CG-Token': self.token} if auth else {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        headers.update(extra or {})
        payload = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        req = Request(self.base + path, data=payload, headers=headers, method='POST' if payload is not None else 'GET')
        try:
            with urlopen(req, timeout=10) as response:
                content = response.read()
                return response.status, json.loads(content) if response.headers.get_content_type() == 'application/json' else content
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def ready(self):
        code, start = self.request('/api/open', {'path': str(self.root)})
        self.assertEqual(code, 202, start)
        for _ in range(150):
            _, status = self.request('/api/status')
            if status['status']['phase'] == 'ready':
                return status
            if status['status']['phase'] == 'error':
                self.fail(status['status'])
            time.sleep(.02)
        self.fail('index did not finish')

    def path(self, route, **params):
        from urllib.parse import urlencode
        return route + '?' + urlencode(dict(generation=self.server.session.generation, **params))

    def search_fixture(self):
        (self.root / 'orders.py').write_text('from payments import reverse_charge\ndef cancel_order():\n    """Cancel order."""\n    return reverse_charge()\n')
        (self.root / 'payments.py').write_text('def reverse_charge(): return 1\n')
        self.ready()

    def test_functional_map_matches_engine_and_checks_freshness_readonly(self):
        manifest=dict(format='codegraph-manifest/1',features=[
            dict(id='payment',title='Payment',entrypoints=[dict(file='payments.py',symbol='reverse_charge')]),
            dict(id='orders',title='Orders',entrypoints=[dict(file='orders.py',symbol='cancel_order')])])
        (self.root/'.code-graph-manifest.json').write_text(json.dumps(manifest));self.search_fixture()
        database=self.root/'.code-graph/graph.db';before=database.read_bytes()
        for confidence in ('high','medium','low'):
            code,result=self.request(self.path('/api/flows',confidence=confidence))
            self.assertEqual(code,200,result)
            with workbench['ProjectData'](cg,self.root) as project:
                expected=runpy.run_path(str(REPO/'scripts/functional_map.py'))['build'](project,min_conf=confidence)
            self.assertEqual(result['data'],expected)
        self.assertEqual(len(expected['links']),1)
        self.assertEqual(self.request(self.path('/api/flows'),auth=False)[0],403)
        self.assertEqual(self.request('/api/flows?generation=obsolete')[0],409)
        for params in (dict(q='x'*201),dict(limit=0),dict(limit=101),dict(confidence='certain'),dict(max_chars=511),dict(max_chars=16001)):
            self.assertEqual(self.request(self.path('/api/flows',**params))[0],400)
        (self.root/'payments.py').write_text('def reverse_charge(): return 2\n')
        code,result=self.request(self.path('/api/flows'))
        self.assertEqual((code,result['code']),(409,'stale_index'))
        self.assertNotIn('data',result);self.assertEqual(database.read_bytes(),before)

    def test_functional_map_empty_and_engine_changed(self):
        self.ready();code,result=self.request(self.path('/api/flows'))
        self.assertEqual(code,200,result)
        self.assertEqual(result['data']['declarations_status'],'no_declared_features')
        self.assertEqual(result['data']['nodes'],[])
        self.assertTrue(result['data']['entrypoint_candidates'])
        inputs=cg.analysis_inputs(self.root)
        with patch.object(cg,'analysis_inputs',return_value=(*inputs[:2],'changed')):
            code,result=self.request(self.path('/api/flows'))
        self.assertEqual((code,result['code']),(409,'stale_index'))

    def test_usage_guide_is_local_without_project_access_or_remote_assets(self):
        code,page=self.request('/guide/README.md',auth=False)
        self.assertEqual(code,200)
        text=page.decode()
        self.assertIn('/usage-guide.css',text)
        self.assertIn('/guide/guides/two-maps.md',text)
        self.assertNotIn(self.token,text)
        self.assertNotIn(str(self.root),text)
        self.assertNotRegex(text,r'<(?:img|script)[^>]+(?:src|href)=["\']https?://')
        self.assertEqual(self.request('/usage-guide.css',auth=False)[0],200)
        self.assertEqual(self.request('/guide/guides/two-maps.md',auth=False)[0],200)
        self.assertEqual(self.request('/guide/example/shop/shop/orders.py',auth=False)[0],200)
        for path in ('/guide/../../SKILL.md','/guide/%2e%2e/%2e%2e/README.md','/guide/.env','/guide/missing.md'):
            self.assertEqual(self.request(path,auth=False)[0],404,path)

    def test_usage_guide_refuses_symlink_on_guide_root_and_docs_ancestor(self):
        bundle=self.workspace/'manual-bundle';guide=bundle/'docs/guide'
        (bundle/'assets').mkdir(parents=True);guide.mkdir(parents=True)
        (guide/'README.md').write_text('# Inside manual')
        outside=self.workspace/'outside-manual';(outside/'guide').mkdir(parents=True)
        (outside/'README.md').write_text('OUTSIDE_GUIDE_SENTINEL')
        (outside/'guide/README.md').write_text('OUTSIDE_DOCS_SENTINEL')
        with patch.dict(workbench['create_server'].__globals__,ASSETS=bundle/'assets',GUIDE_ROOT=guide,GUIDE_FILES=frozenset({'README.md'})):
            self.assertEqual(self.request('/guide/README.md',auth=False)[0],200)
            guide.rename(bundle/'docs/guide-original');guide.symlink_to(outside,target_is_directory=True)
            code,result=self.request('/guide/README.md',auth=False)
            self.assertEqual(code,404,result)
            self.assertNotIn('OUTSIDE',json.dumps(result))
            guide.unlink();(bundle/'docs/guide-original').rename(guide)
            (bundle/'docs').rename(bundle/'docs-original');(bundle/'docs').symlink_to(outside,target_is_directory=True)
            code,result=self.request('/guide/README.md',auth=False)
            self.assertEqual(code,404,result)
            self.assertNotIn('OUTSIDE',json.dumps(result))

    def test_search_http_matches_engine_and_preserves_readonly_index(self):
        self.search_fixture()
        dbpath = self.root / '.code-graph/graph.db'
        before = hashlib.sha256(dbpath.read_bytes()).hexdigest()
        for mode in ('lexical', 'hybrid'):
            with self.subTest(mode=mode):
                code, result = self.request(self.path('/api/search', q='cancel order', mode=mode))
                self.assertEqual(code, 200, result)
                with workbench['ProjectData'](cg, self.root) as project:
                    expected = cg.search_module()['search'](project, 'cancel order', mode=mode)
                self.assertEqual(result['data'], expected)
                self.assertEqual(result['generation'], self.server.session.generation)
                names = {item['symbol']['name'] for item in result['data']['items']}
                self.assertIn('cancel_order', names)
                self.assertEqual('reverse_charge' in names, mode == 'hybrid')
                for item in result['data']['items']:
                    self.assertEqual(item['source']['source_sha256'], hashlib.sha256((self.root / item['symbol']['file']).read_bytes()).hexdigest())
        self.assertEqual(hashlib.sha256(dbpath.read_bytes()).hexdigest(), before)

    def test_search_http_validates_bounds_auth_generation_and_budget(self):
        self.search_fixture()
        self.assertEqual(self.request(self.path('/api/search', q='cancel'), auth=False)[0], 403)
        self.assertEqual(self.request('/api/search?generation=obsolete&q=cancel')[0], 409)
        for params in ({'q': ''}, {'q': '.'}, {'q': 'x' * 513}, {'q': 'cancel', 'mode': 'semantic'},
                       {'q': 'cancel', 'limit': 51}, {'q': 'cancel', 'depth': 4},
                       {'q': 'cancel', 'max_chars': 63}, {'q': 'cancel', 'limit': 'bad'},
                       {'q': 'cancel', 'confidence': 'certain'}):
            with self.subTest(params=params):
                self.assertEqual(self.request(self.path('/api/search', **params))[0], 400)
        code, value = self.request(self.path('/api/search', q='cancel', max_chars=64))
        self.assertEqual(code, 200)
        self.assertEqual(value['data'], {'status': 'budget_insufficient', 'max_chars': 64})
        self.assertFalse(self.server.session.consent)
        self.assertIsNone(self.server.session._runner)

    def test_search_http_rejects_stale_graph_evidence(self):
        self.search_fixture()
        (self.root / 'payments.py').write_text('def reverse_charge(): return 99\n')
        code, value = self.request(self.path('/api/search', q='cancel order', mode='hybrid'))
        self.assertEqual(code, 409, value)
        self.assertEqual(value['code'], 'stale_index')
        self.assertNotIn('data', value)

    def test_search_http_requires_current_engine(self):
        self.search_fixture()
        inputs = cg.analysis_inputs(self.root)
        with patch.object(cg, 'analysis_inputs', return_value=(*inputs[:2], 'changed')):
            code, value = self.request(self.path('/api/search', q='cancel'))
        self.assertEqual(code, 409, value)
        self.assertEqual(value['code'], 'stale_index')

    def test_search_http_reports_unavailable_without_writes(self):
        self.search_fixture()
        dbpath = self.root / '.code-graph/graph.db'
        with sqlite3.connect(dbpath) as db:
            db.execute("UPDATE meta SET v='unavailable' WHERE k='search_status'")
        before = hashlib.sha256(dbpath.read_bytes()).hexdigest()
        code, value = self.request(self.path('/api/search', q='cancel'))
        self.assertEqual(code, 503, value)
        self.assertEqual(value['code'], 'search_unavailable')
        self.assertEqual(hashlib.sha256(dbpath.read_bytes()).hexdigest(), before)
        self.assertEqual(self.request(self.path('/api/symbols', q='cancel'))[0], 200)

    def test_open_overview_verified_areas_and_no_project_execution(self):
        marker = self.workspace / 'project-imported'
        (self.root / 'never_import.py').write_text("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('bad')\n")
        before = {name: (self.root / name).read_bytes() for name in SOURCES}
        status = self.ready()
        self.assertFalse(marker.exists())
        profile = status['project']
        self.assertEqual({a['id'] for a in profile['areas'] if a['kind'] == 'evidence'}, {'area:mcp', 'area:site', 'area:pipeline', 'area:publication'})
        self.assertTrue(all(a['evidence'] for a in profile['areas'] if a['kind'] == 'evidence'))
        self.assertTrue(all((self.root / name).read_bytes() == contents for name, contents in before.items()))
        self.assertEqual(profile['partial_files'], 0)
        self.assertGreater(profile['callbacks'], 1)

    def test_auth_host_origin_and_assets_are_bounded(self):
        for path in ('/api/bootstrap', '/api/status', '/api/directories'):
            self.assertEqual(self.request(path, auth=False)[0], 403)
        for headers in ({'Origin': 'https://invalid.example'}, {'Origin': 'null'}, {'Host': 'evil.example'}):
            self.assertEqual(self.request('/api/bootstrap', extra=headers)[0], 403)
        self.assertEqual(self.request('/api/open', {'path': str(self.root)}, auth=False)[0], 403)
        self.assertFalse((self.root / '.code-graph').exists())
        self.assertEqual(self.request('/pkg/core.py')[0], 404)
        self.assertEqual(self.request('/')[0], 200)
        self.assertEqual(self.request('/workbench.js')[0], 200)

    def test_workspace_escape_and_input_limits(self):
        self.assertEqual(self.request('/api/open', {'path': str(self.workspace.parent)})[0], 400)
        (self.workspace / 'escape').symlink_to(self.workspace.parent, target_is_directory=True)
        self.assertEqual(self.request('/api/directories?path=' + str(self.workspace / 'escape'))[0], 400)
        for value in (None, {}, [], ''):
            self.assertEqual(self.request('/api/open', {'path': value})[0], 400)
        self.assertEqual(self.request('/api/open', raw=b'x' * 9000, extra={'Content-Type': 'application/json'})[0], 413)
        self.assertEqual(self.request('/api/open', raw=b'not json', extra={'Content-Type': 'application/json'})[0], 400)

    def test_source_calls_impact_tests_and_readonly_queries(self):
        self.ready()
        dbpath = self.root / '.code-graph/graph.db'
        before = hashlib.sha256(dbpath.read_bytes()).hexdigest()
        _, result = self.request(self.path('/api/symbols', q='work'))
        sid = next(n['id'] for n in result['data']['items'] if n['file'] == 'pkg/core.py')
        _, details = self.request(self.path('/api/symbol', id=sid))
        self.assertIn('def work()', details['data']['source']['lines'][0]['text'])
        self.assertGreaterEqual(len(details['data']['callers']), 3)
        _, impact = self.request(self.path('/api/impact', id=sid))
        self.assertEqual({item['symbol']['file'] for item in impact['data']['tests']}, {'tests/test_core.py'})
        self.assertTrue(all(item['chain'] for item in impact['data']['tests']))
        self.assertEqual(hashlib.sha256(dbpath.read_bytes()).hexdigest(), before)

    def test_stale_source_is_not_combined_with_old_coordinates(self):
        self.ready()
        _, listing = self.request(self.path('/api/symbols', q='work'))
        sid = next(n['id'] for n in listing['data']['items'] if n['file'] == 'pkg/core.py')
        (self.root / 'pkg/core.py').write_text('def shifted(): return 2\n')
        code, error = self.request(self.path('/api/symbol', id=sid))
        self.assertEqual(code, 409)
        self.assertEqual(error['code'], 'stale_index')

    def test_resources_are_observations_without_opening_them(self):
        self.ready()
        _, resources = self.request(self.path('/api/resources'))
        kinds = {item['kind'] for item in resources['data']['items']}
        self.assertTrue({'file.read', 'file.write', 'database.connect', 'process.start'} <= kinds)
        self.assertFalse((self.root / 'data').exists())
        self.assertTrue(all(item['source']['lines'] for item in resources['data']['items']))

    def test_sql_architecture_http_preserves_sources_hashes_and_readonly_cache(self):
        (self.root/'schema.sql').write_text('CREATE TABLE users(id INTEGER); CREATE VIEW active AS SELECT * FROM users;')
        self.ready();database=self.root/'.code-graph/graph.db';before=database.read_bytes()
        code,result=self.request(self.path('/api/architecture'));self.assertEqual(code,200,result)
        with workbench['ProjectData'](cg,self.root) as project:expected=project.architecture()['sql']
        self.assertEqual(result['data']['sql'],expected)
        self.assertTrue(expected['links']);self.assertTrue(all(item['sha256'] and item['source']['lines'] for item in expected['items']))
        self.assertEqual(database.read_bytes(),before)

    def test_area_graph_and_standalone_snapshot_share_data(self):
        self.ready()
        _, graph = self.request(self.path('/api/graph', area='folder:pkg'))
        from html.parser import HTMLParser
        self.assertIn('"embedded":true', graph['data']['html'])
        ids = graph['data']['node_ids']
        with sqlite3.connect(self.root / '.code-graph/graph.db') as db:
            expected = {r[0] for r in db.execute("SELECT s.id FROM symbols s JOIN files f ON f.id=s.file_id WHERE f.path LIKE 'pkg/%'")}
        self.assertEqual(set(ids), expected)
        _, standalone = self.request(self.path('/api/graph', area='folder:pkg', standalone='1'))
        self.assertEqual(standalone['data']['node_ids'], ids)
        self.assertIn('"embedded":false', standalone['data']['html'])

    def test_generation_and_unknown_symbols_are_rejected(self):
        self.ready()
        self.assertEqual(self.request('/api/symbols?generation=wrong')[0], 409)
        self.assertEqual(self.request(self.path('/api/symbol', id=999999))[0], 400)
        self.assertEqual(self.request(self.path('/api/symbols', area='../outside'))[0], 400)
        self.assertEqual(self.request(self.path('/api/impact', id=1, depth=999))[0], 400)

    def test_names_alone_do_not_create_semantic_areas(self):
        isolated = self.workspace / 'names-only'
        isolated.mkdir()
        (isolated / 'mcp_site_pipeline.py').write_text('def ordinary(): return 1\n')
        self.root = isolated
        profile = self.ready()['project']
        self.assertFalse(any(area['kind'] == 'evidence' for area in profile['areas']))


    def test_external_reindex_invalidates_the_active_session_snapshot(self):
        self.ready()
        (self.root / 'pkg/core.py').write_text('def extra(): return 0\ndef work(): return 2\n')
        import argparse
        cg.cmd_index(argparse.Namespace(root=str(self.root), full=False))
        code, error = self.request(self.path('/api/symbols'))
        self.assertEqual(code, 409)
        self.assertEqual(error['code'], 'stale_index')

    def test_configuration_change_requires_refresh_before_new_queries(self):
        self.ready()
        path = self.root / 'pyproject.toml'
        path.write_text('\n\n# changed since analysis\n' + path.read_text())
        code, error = self.request(self.path('/api/symbols'))
        self.assertEqual(code, 409)
        self.assertEqual(error['code'], 'stale_index')

    def test_manifest_evidence_points_to_actual_script_and_supports_js(self):
        (self.root / 'build.js').write_text('function build() { return 1; }\n')
        (self.root / 'package.json').write_text('{"build":"decoy",\n"scripts": {"build": "node build.js"}}')
        (self.root / 'pyproject.toml').write_text('[project]\nname="pipeline"\n[project.scripts]\npipeline="pipeline:run"\n')
        (self.root / '.github/workflows/comment.yml').write_text('# run: python pipeline.py\n')
        profile = self.ready()['project']
        area = next(a for a in profile['areas'] if a['id'] == 'area:pipeline')
        _, listing = self.request(self.path('/api/symbols', area='area:pipeline'))
        self.assertIn('build.js', {item['file'] for item in listing['data']['items']})
        proofs = {e['file']: e['line'] for e in area['evidence']}
        self.assertEqual(proofs['package.json'], 2)
        self.assertEqual(proofs['pyproject.toml'], 4)
        self.assertNotIn('.github/workflows/comment.yml', proofs)

    def test_shadowed_library_names_are_not_resources(self):
        (self.root / 'shadow.py').write_text('import subprocess\nimport sqlite3\ndef fake(subprocess, sqlite3, open):\n    subprocess.run(["false-positive"])\n    sqlite3.connect("false-positive")\n    open("false-positive")\n')
        (self.root / 'class_shadow.py').write_text('import subprocess\nclass subprocess:\n    @staticmethod\n    def run(value): return value\ndef fake(): return subprocess.run(["false-positive"])\n')
        self.ready()
        _, resources = self.request(self.path('/api/resources'))
        self.assertFalse(any(item['file'] in ('shadow.py', 'class_shadow.py') for item in resources['data']['items']))


if __name__ == '__main__':
    unittest.main()
