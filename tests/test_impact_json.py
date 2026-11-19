"""Impact JSON preserves the call/reference distinction exposed by text output."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_impact_json_test', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
ProjectData = runpy.run_path(str(REPO / 'scripts/project_data.py'))['ProjectData']
mcp_call = runpy.run_path(str(REPO / 'scripts/mcp_server.py'))['_call']


class ImpactJson(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='cg-impact-json-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def index(self, files):
        for name, source in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(self.root), full=False))

    def impact(self, target, **options):
        args = dict(root=str(self.root), target=target, depth=3, min_conf='medium',
                    file=False, json=True, limit=100)
        args.update(options)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cg.cmd_impact(argparse.Namespace(**args))
        return json.loads(output.getvalue()) if args['json'] else output.getvalue()

    def assertRelations(self, result, expected):
        items = {item['symbol']['name']: item for item in result['items']}
        self.assertEqual(set(items), set(expected))
        for name, relations in expected.items():
            self.assertEqual(items[name].get('relation'), relations[0], name)
            self.assertEqual([step.get('relation') for step in items[name]['chain']], relations, name)

    def test_calls_references_and_mixed_chains_in_three_languages(self):
        sources = {
            'py': 'def target(): return 1\ndef direct(): return target()\ndef callback(): return sorted([], key=target)\ndef outer(): return callback()\ndef both():\n    sorted([], key=target)\n    return target()\n',
            'js': 'function target() { return 1; }\nfunction direct() { return target(); }\nfunction callback() { return [].map(target); }\nfunction outer() { return callback(); }\nfunction both() { [].map(target); return target(); }\n',
            'php': "<?php\nfunction target() { return 1; }\nfunction direct() { return target(); }\nfunction callback() { return array_map('target', []); }\nfunction outer() { return callback(); }\nfunction both() { array_map('target', []); return target(); }\n",
        }
        for extension, source in sources.items():
            with self.subTest(language=extension):
                self.index({'app.' + extension: source})
                target = 'app.' + extension + ':target'
                expected = {'direct': ['call'], 'callback': ['reference'],
                            'outer': ['call', 'reference'], 'both': ['call']}
                self.assertRelations(self.impact(target), expected)
                text = self.impact(target, json=False)
                for name, relations in expected.items():
                    line = next(line for line in text.splitlines() if ' ' + name + ' ' in line)
                    self.assertIn('references' if relations[0] == 'reference' else 'calls', line)
                self.assertRelations(self.impact(target, min_conf='high'),
                                     {'direct': ['call'], 'both': ['call']})
                (self.root / ('app.' + extension)).unlink()

    def test_object_assign_export_is_reference_and_consumer_is_call(self):
        fixture = REPO / 'tests/fixtures/gaps_js/cjs_exports'
        shutil.copytree(fixture, self.root, dirs_exist_ok=True)
        self.index({})
        expected = {'assign.js': ['reference'], 'useAssign': ['call']}
        self.assertRelations(self.impact('assign.js:runScript'), expected)
        text = self.impact('assign.js:runScript', json=False)
        self.assertRegex(text, r'assign\.js .*references')
        self.assertRegex(text, r'useAssign .*calls')

    def test_file_and_mcp_outputs_keep_relations(self):
        self.index({'target.py': 'def target(): return 1\n',
                    'app.py': 'from target import target\ndef direct(): return target()\ndef callback(): return sorted([], key=target)\n'})
        expected = {'direct': ['call'], 'callback': ['reference']}
        result = self.impact('target.py', file=True)
        self.assertEqual(result['format'], 'codegraph-impact-file/1')
        self.assertEqual(len(result['results']), 1)
        self.assertRelations(result['results'][0], expected)
        response = mcp_call(cg, str(self.root), 'cg_impact', dict(target='target.py:target', json=True))
        self.assertFalse(response['isError'], response)
        self.assertRelations(json.loads(response['content'][0]['text']), expected)

    def test_relations_survive_pagination_and_separate_test_records(self):
        self.index({'target.py': 'def target(): return 1\n',
                    'app.py': 'from target import target\ndef direct(): return target()\n',
                    'tests/test_target.py': 'from target import target\ndef test_callback(): return sorted([], key=target)\n'})
        with ProjectData(cg, str(self.root)) as project:
            sid = cg.resolve_one(project.db, 'target.py:target')
            legacy = project.impact(sid)
            self.assertRelations(legacy, {'direct': ['call'], 'test_callback': ['reference']})
            page = project.impact(sid, compact=True, limit=1)
            self.assertRelations(page, {'direct': ['call']})
            records = list(page['test_records'].values())
            self.assertRelations(dict(items=records), {'test_callback': ['reference']})
            self.assertEqual(page['tests'], [dict(item_ref=records[0]['symbol']['id'])])
            second = project.impact(sid, compact=True, limit=1, cursor=page['pages']['items']['next_cursor'])
            self.assertRelations(second, {'test_callback': ['reference']})
            self.assertEqual(second['test_records'], {})


if __name__ == '__main__':
    unittest.main()
