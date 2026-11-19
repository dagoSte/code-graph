"""PHP method precedence, with synthetic witnesses checked separately by the native runtime."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_php_traits', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)
FIXTURE = REPO / 'tests/fixtures/gaps_php/traits_precedence'
CASES = json.loads((FIXTURE / 'cases.json').read_text())
BASE = {p.name: p.read_text() for p in FIXTURE.glob('*.php')}


class TraitPrecedence(unittest.TestCase):
    def test_method_resolution_and_static_contracts(self):
        for case in CASES:
            with self.subTest(case=case['name']), tempfile.TemporaryDirectory(prefix='cg-php-traits-') as temp:
                root = Path(temp)
                for path, source in {**BASE, **case['files']}.items():
                    (root / path).write_text(source)
                with contextlib.redirect_stdout(io.StringIO()):
                    cg.cmd_index(argparse.Namespace(root=str(root), full=False))
                with contextlib.closing(cg.connect(str(root))) as db:
                    rows = set(db.execute(
                        'SELECT target.qname,e.conf FROM edges e JOIN calls c ON c.id=e.call_id '
                        'JOIN symbols caller ON caller.id=c.caller_id '
                        'JOIN symbols target ON target.id=e.callee_id '
                        'WHERE caller.qname=? AND c.name=?', ('Service.run', case['call'])))
                    expected = {(case['expected'], 'high')} if case['expected'] else set()
                    self.assertEqual(rows, expected)
                    if 'reference' in case:
                        refs = set(db.execute(
                            'SELECT target.qname FROM symbol_refs r JOIN symbols owner ON owner.id=r.owner_id '
                            'JOIN symbols target ON target.id=r.target_id WHERE owner.qname=? AND r.name=?',
                            ('Service.run', 'getContainer')))
                        self.assertEqual(refs, {(case['reference'],)})

    def test_abstract_contracts_keep_impact_callers(self):
        with tempfile.TemporaryDirectory(prefix='cg-php-contracts-') as temp:
            root = Path(temp)
            (root / 'a.php').write_text((REPO / 'tests/fixtures/gaps_php/traits_static_contracts/a.php').read_text())
            with contextlib.redirect_stdout(io.StringIO()):
                cg.cmd_index(argparse.Namespace(root=str(root), full=False))
            for target, caller in [('I.m', 'useit'), ('Base.n', 'Base.run')]:
                with self.subTest(target=target):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        cg.cmd_impact(argparse.Namespace(root=str(root), target='a.php:' + target,
                            min_conf='high', depth=1, file=False, json=True, limit=100))
                    items = json.loads(output.getvalue())['items']
                    self.assertEqual({item['symbol']['name'] for item in items}, {caller})
                    self.assertTrue(all(item['chain'][0]['confidence'] == 'high' for item in items))


if __name__ == '__main__':
    unittest.main()
