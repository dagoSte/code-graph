"""WordPress shortcode registration: native contract probes, independent of project execution."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_php_catalog', REPO/'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)


class PhpShortcodes(unittest.TestCase):
    def project(self, files):
        temp = tempfile.TemporaryDirectory(prefix='cg-php-shortcode-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)/'project'
        root.mkdir()
        for name, source in files.items():
            (root/name).write_text(source)
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=True))
        return root

    def references(self, root):
        with contextlib.closing(cg.connect(str(root))) as db:
            return {(owner, target) for owner,target in db.execute('''
                SELECT o.qname,t.qname FROM symbol_refs r JOIN symbols o ON o.id=r.owner_id
                JOIN symbols t ON t.id=r.target_id''')}

    def native(self, root, provider=True):
        # A synthetic WordPress 5.7.2 contract: store the callback, then render it later.
        runtime = root.parent/'runtime.php'
        runtime.write_text('<?php $shortcode_tags=[];'+(
            'function add_shortcode($tag,$callback){$GLOBALS["shortcode_tags"][$tag]=$callback;}'
            if provider else '')+'require __DIR__."/project/plugin.php";'
            +'$results=[];foreach($shortcode_tags as $tag=>$callback){$results[]=$callback([],null,$tag);}'
            +'echo json_encode($results);')
        result = subprocess.run(['php',str(runtime)], capture_output=True, text=True, timeout=10)
        self.assertEqual((result.returncode,result.stderr), (0,''))
        return json.loads(result.stdout)

    @unittest.skipUnless(shutil.which('php'), 'native PHP required')
    def test_documented_function_and_instance_callbacks_are_references(self):
        fixture = REPO/'tests/fixtures/gaps_php/C_shortcode'
        root = self.project({p.name:p.read_text() for p in fixture.glob('*.php')})
        self.assertEqual(self.native(root), ['function','method'])
        self.assertEqual(self.references(root), {('register_shortcodes','render_shortcode'),
                                                ('Shortcode.register','Shortcode.render')})

    @unittest.skipUnless(shutil.which('php'), 'native PHP required')
    def test_qualified_alias_and_named_arguments_keep_only_the_callback(self):
        cases = [('', "add_shortcode('tag_only','render_shortcode');"),
                 ('namespace App;', "\\add_shortcode('tag_only','render_shortcode');"),
                 ('namespace App;use function add_shortcode as shortcode;', "shortcode('tag_only','render_shortcode');"),
                 ('', "add_shortcode(callback:'render_shortcode',tag:'tag_only');")]
        for imports, call in cases:
            with self.subTest(call=call):
                root=self.project({'callbacks.php':"<?php function render_shortcode(){return 'function';}function tag_only(){return 'data';}",
                    'plugin.php':'<?php '+imports+'require __DIR__."/callbacks.php";function register_shortcodes(){'+call+'}register_shortcodes();'})
                self.assertEqual(self.native(root), ['function'])
                self.assertEqual(self.references(root), {('register_shortcodes','render_shortcode')})

    @unittest.skipUnless(shutil.which('php'), 'native PHP required')
    def test_static_callable_arrays_and_strings_select_the_same_method(self):
        calls = ["add_shortcode('tag',[StaticShortcode::class,'render']);",
                 "add_shortcode('tag',['StaticShortcode','render']);",
                 "add_shortcode('tag','StaticShortcode::render');"]
        for call in calls:
            with self.subTest(call=call):
                root=self.project({'plugin.php': '<?php '
                    +'class StaticShortcode{static function render(){return "static";}}'
                    +'class UnrelatedShortcode{static function render(){return "unrelated";}}'
                    +'function register_shortcodes(){'+call+'}register_shortcodes();'})
                self.assertEqual(self.native(root), ['static'])
                self.assertEqual(self.references(root), {('register_shortcodes','StaticShortcode.render')})

    @unittest.skipUnless(shutil.which('php'), 'native PHP required')
    def test_project_function_method_and_foreign_homonyms_are_data(self):
        cases = [
            ('', 'function add_shortcode($tag,$callback){}', "add_shortcode('tag','render_shortcode');", False),
            ('namespace App;', 'function add_shortcode($tag,$callback){}', "add_shortcode('tag','render_shortcode');", True),
            ('', 'class Data{static function add_shortcode($tag,$callback){}}', "Data::add_shortcode('tag','render_shortcode');", True),
            ('', 'class Data{function add_shortcode($tag,$callback){}}', "$data=new Data();$data->add_shortcode('tag','render_shortcode');", True),
            ('namespace App;use function Other\\add_shortcode;', '', "add_shortcode('tag','render_shortcode');", True),
        ]
        for imports, definitions, call, provider in cases:
            with self.subTest(call=call, imports=imports):
                root=self.project({'callbacks.php':"<?php function render_shortcode(){return 'function';}",
                    'foreign.php':'<?php namespace Other;function add_shortcode($tag,$callback){}',
                    'plugin.php':'<?php '+imports+'require __DIR__."/callbacks.php";require __DIR__."/foreign.php";'
                        +definitions+'function register_shortcodes(){'+call+'}register_shortcodes();'})
                self.assertEqual(self.native(root,provider), [])
                self.assertEqual(self.references(root), set())


if __name__ == '__main__':
    unittest.main()
