"""`review` stays fast on large projects: the base snapshot reuses the current index, and the output
budget is met without serialising the answer once per dropped record."""
import json
import runpy
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
CG = REPO / 'scripts/cg'
ANALYSIS = REPO / 'scripts/change_analysis.py'


def facts(root):
    with sqlite3.connect(Path(root) / '.code-graph/graph.db') as db:
        symbols = sorted(db.execute('SELECT f.path,s.qname,s.kind,s.line,s.end_line,s.sig FROM symbols s JOIN files f ON f.id=s.file_id'))
        edges = sorted(db.execute('''SELECT fa.path,a.qname,fb.path,b.qname,e.conf FROM edges e
            JOIN symbols a ON a.id=e.caller_id JOIN files fa ON fa.id=a.file_id
            JOIN symbols b ON b.id=e.callee_id JOIN files fb ON fb.id=b.file_id'''))
        files = sorted(db.execute('SELECT path,sha FROM files'))
    return symbols, edges, files


class ReviewScale(unittest.TestCase):
    def project(self, folder):
        root = Path(folder) / 'app'
        root.mkdir()
        for i in range(12):
            (root / f'mod_{i}.py').write_text(f'from mod_{(i + 1) % 12} import f_{(i + 1) % 12}\n'
                                              f'def f_{i}(x):\n    return f_{(i + 1) % 12}(x) if x else {i}\n')
        subprocess.run([str(CG), '--root', str(root), 'index'], check=True, capture_output=True)
        return root

    def test_seeded_snapshot_index_has_the_facts_of_a_fresh_one(self):
        engine = runpy.run_path(str(REPO / 'scripts/cg.py'))
        from types import SimpleNamespace
        engine = SimpleNamespace(**engine)
        seeded_index = runpy.run_path(str(ANALYSIS))['seeded_index']
        with tempfile.TemporaryDirectory() as folder:
            root = self.project(folder)
            for name, edit in (('changed', True), ('identical', False)):
                with self.subTest(snapshot=name):
                    seeded, fresh = Path(folder) / f'seeded-{name}', Path(folder) / f'fresh-{name}'
                    for target in (seeded, fresh):
                        shutil.copytree(root, target, ignore=shutil.ignore_patterns('.code-graph'))
                        if edit:
                            (target / 'mod_3.py').write_text('def f_3(x, y=0):\n    return x + y\ndef g(x):\n    return f_3(x)\n')
                            (target / 'mod_5.py').unlink()
                    seeded_index(engine, root, seeded)
                    subprocess.run([str(CG), '--root', str(fresh), 'index'], check=True, capture_output=True)
                    self.assertEqual(facts(seeded), facts(fresh))

    def test_review_against_a_base_still_reports_the_change(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'shop%23one').mkdir()  # a literal percent sign must not break the index URI
            root = self.project(Path(folder) / 'shop%23one')
            git = ['git', '-C', str(root), '-c', 'user.name=t', '-c', 'user.email=t@example.invalid', '-c', 'commit.gpgsign=false']
            subprocess.run(git + ['init', '-q'], check=True)
            (root / '.gitignore').write_text('.code-graph/\n')
            subprocess.run(git + ['add', '-A'], check=True)
            subprocess.run(git + ['commit', '-qm', 'base'], check=True)
            (root / 'mod_3.py').write_text('from mod_4 import f_4\ndef f_3(x):\n    return f_4(x - 1) if x else 3\n')
            subprocess.run([str(CG), '--root', str(root), 'index'], check=True, capture_output=True)
            value = json.loads(subprocess.run([str(CG), '--root', str(root), 'review', '--base', 'HEAD', '--json'],
                                              check=True, capture_output=True, text=True).stdout)
            self.assertEqual(value['baseline']['status'], 'available')
            self.assertEqual([item['name'] for item in value['symbols']], ['f_3'])

    def test_budget_is_met_with_a_logarithmic_number_of_serialisations(self):
        bounded = runpy.run_path(str(ANALYSIS))['bounded']
        records = [dict(rule_id='r', file=f'f{i}.py', line=i, message='m' * 40) for i in range(4000)]
        originals = list(records)
        result = dict(comparison=dict(persistent=records, uncompared=[], resolved=[], new=[]), symbols=[], changes=[],
                      verification=dict(tests=[], snippets={}, support=[], unknown_calls=[]))
        import json as json_module
        real = json_module.dumps
        with mock.patch.object(json_module, 'dumps', side_effect=real) as dumps:
            value = bounded(result, 20000)
        self.assertLessEqual(len(json_module.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))), 20000)
        kept = len(value['comparison']['persistent'])
        self.assertGreater(kept, 0)
        self.assertEqual(value['output']['omitted_records'], 4000 - kept)
        # One more record would not fit: the fewest records were dropped.
        result_more = json_module.loads(json_module.dumps(value))
        result_more['comparison']['persistent'].append(originals[kept])
        self.assertGreater(len(json_module.dumps(result_more, ensure_ascii=False, sort_keys=True, separators=(',', ':'))) + 200, 20000)
        self.assertLess(dumps.call_count, 40)


    def test_every_kept_test_keeps_its_snippets(self):
        bounded = runpy.run_path(str(ANALYSIS))['bounded']
        for budget in (700, 1100, 1500, 2000, 4000):
            tests = [dict(symbol=dict(file='t.py', name=f't{i}'), chain=[dict(snippet_ref=f's{i}')]) for i in range(8)]
            snippets = {f's{i}': dict(text='x' * 140) for i in range(8)}
            result = dict(comparison=dict(persistent=[], uncompared=[], resolved=[], new=[]), symbols=[], changes=[],
                          verification=dict(tests=tests, snippets=snippets, support=[], unknown_calls=[]))
            value = bounded(result, budget)
            if value.get('status') == 'budget_insufficient':
                continue
            with self.subTest(budget=budget):
                kept = value['verification']['tests']
                self.assertEqual(sorted(value['verification']['snippets']),
                                 sorted(step['snippet_ref'] for test in kept for step in test['chain']))
                self.assertEqual(value['output']['omitted_records'], 8 - len(kept))


    def test_bisection_matches_the_record_by_record_reference(self):
        import copy
        import random
        bounded = runpy.run_path(str(ANALYSIS))['bounded']

        def reference(result, max_chars):
            # The previous algorithm: drop one record at a time, re-measuring the whole answer.
            def text():
                verification = result.get('verification', {})
                used = {step['snippet_ref'] for item in verification.get('tests', []) for step in item.get('chain', [])
                        if 'snippet_ref' in step}
                verification['snippets'] = {k: v for k, v in verification.get('snippets', {}).items() if k in used}
                return json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            original, omitted = len(text()), 0
            c, v = result['comparison'], result['verification']
            for collection in [c['persistent'], c['uncompared'], c['resolved'], v['support'], v['unknown_calls'],
                               result['symbols'], v['tests'], c['new'], result['changes']]:
                while collection and len(text()) + 200 > max_chars:
                    collection.pop()
                    omitted += 1
            return omitted, json.loads(text())

        rnd = random.Random(7)
        for trial in range(300):
            records = lambda n: [dict(m='y' * rnd.randint(5, 120), i=i) for i in range(n)]
            tests = [dict(symbol=dict(file='t', name=f't{i}'),
                          chain=[dict(snippet_ref=f's{rnd.randint(0, 9)}') for _ in range(rnd.randint(0, 3))])
                     for i in range(rnd.randint(0, 12))]
            base = dict(comparison=dict(persistent=records(rnd.randint(0, 30)), uncompared=records(rnd.randint(0, 10)),
                                        resolved=records(rnd.randint(0, 5)), new=records(rnd.randint(0, 5))),
                        symbols=records(rnd.randint(0, 5)), changes=records(rnd.randint(0, 4)),
                        verification=dict(tests=tests, snippets={f's{i}': dict(text='x' * rnd.randint(10, 200)) for i in range(10)},
                                          support=records(rnd.randint(0, 4)), unknown_calls=records(rnd.randint(0, 4))))
            budget = rnd.randint(128, 6000)
            value = bounded(copy.deepcopy(base), budget)
            omitted, expected = reference(copy.deepcopy(base), budget)
            with self.subTest(trial=trial, budget=budget):
                if value.get('status') == 'budget_insufficient':
                    continue
                self.assertEqual(value['output']['omitted_records'], omitted)
                value = {k: v for k, v in value.items() if k != 'output'}
                self.assertEqual(json.loads(json.dumps(value)), expected)


if __name__ == '__main__':
    unittest.main()
