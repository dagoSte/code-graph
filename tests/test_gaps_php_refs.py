"""PHP catalog origins/argument positions and namespace-aware references (R3/R5/R7/R8)."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cg_php_refs', REPO/'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)


class PhpReferences(unittest.TestCase):
    def project(self, files, catalog=None):
        temp = tempfile.TemporaryDirectory(prefix='cg-php-refs-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name, source in files.items():
            (root/name).parent.mkdir(parents=True, exist_ok=True)
            (root/name).write_text(source)
        if catalog is not None:
            (root/'.code-graph-catalog.json').write_text(json.dumps(catalog))
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=True))
        return root

    def refs(self, root):
        with contextlib.closing(cg.connect(str(root))) as db:
            return set(db.execute('''SELECT f.path,s.qname,g.path,t.qname FROM symbol_refs r
                JOIN files f ON f.id=r.file_id JOIN symbols s ON s.id=r.owner_id
                JOIN symbols t ON t.id=r.target_id JOIN files g ON g.id=t.file_id'''))

    def reindex(self, root):
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=False))

    def test_unknown_method_receiver_is_not_a_builtin(self):
        root = self.project({'app.php': "<?php function polish(){} function drive($obj){$obj->array_map('polish', []);}"})
        self.assertEqual(self.refs(root), set())

    def test_qualified_and_imported_builtins_keep_callbacks_and_dynamic_calls(self):
        for imports, mapper, caller in [('', r'\array_map', r'\call_user_func'),
                (r'use function array_map as mapping; use function call_user_func as invoke;', 'mapping', 'invoke')]:
            with self.subTest(mapper=mapper):
                root = self.project({'app.php': '<?php namespace App; '+imports+
                    f" function drive(){{{mapper}('polish', []); {caller}(str_rot13('cbby'));}}",
                    'lib.php': '<?php function polish(){} function pool(){}'})
                self.assertEqual(self.refs(root), {('app.php','drive','lib.php','polish')})
                with contextlib.closing(cg.connect(str(root))) as db:
                    self.assertEqual(db.execute('SELECT dynamic FROM calls WHERE recv_name=?', (caller,)).fetchone()[0], 1)
        root = self.project({'app.php': r"<?php function pool(){} \call_user_func(str_rot13('cbby'));"})
        with contextlib.closing(cg.connect(str(root))) as db:
            self.assertTrue(any(v == 'AMBIGUOUS' and 'pool' in cg.fmt(db,sid)
                                for v,sid,*_ in cg.dead_candidates(db,str(root))))

    def test_group_import_dynamic_receiver_keeps_only_the_imported_class(self):
        root = self.project({'app.php': r'<?php use A\{Widget}; function drive($m){$w=new Widget(); $w->$m();}',
                             'a.php': '<?php namespace A; class Widget {function first_live(){}}',
                             'b.php': '<?php namespace B; class Widget {function unrelated_unused(){}}'})
        with contextlib.closing(cg.connect(str(root))) as db:
            verdict = {cg.fmt(db,sid):v for v,sid,*_ in cg.dead_candidates(db,str(root))}
        self.assertTrue(any('a.php' in s and 'first_live' in s and v=='AMBIGUOUS' for s,v in verdict.items()))
        self.assertTrue(any('b.php' in s and 'unrelated_unused' in s and v=='LIKELY_DEAD' for s,v in verdict.items()))

    def test_dynamic_reach_remains_ambiguous_when_namespace_lines_are_ambiguous(self):
        cases = [
            {'app.php': r'<?php function drive($m){$w=new \A\Widget(); $w->$m();}',
             'types.php': r'<?php namespace A {class Widget {function run(){}}} namespace B {class Widget {function other(){}}}'},
            {'app.php': r'<?php namespace Caller {function drive($m){$w=new \A\Widget(); $w->$m();}} namespace {function other(){}}',
             'types.php': r'<?php namespace A; class Widget {function run(){}}'},
            {'app.php': r'<?php namespace Caller {use A\Widget; function drive($m){$w=new Widget(); $w->$m();}} namespace {function other(){}}',
             'types.php': r'<?php namespace A; class Widget {function run(){}}'}]
        for files in cases:
            with self.subTest(files=files):
                root=self.project(files)
                with contextlib.closing(cg.connect(str(root))) as db:
                    verdict={cg.fmt(db,sid):v for v,sid,*_ in cg.dead_candidates(db,str(root))}
                self.assertTrue(any('Widget.run' in s and v=='AMBIGUOUS' for s,v in verdict.items()))

    def test_callable_arrays_use_values_not_keys(self):
        root = self.project({'app.php': """<?php
class Service {public static function finish() {}}
function drive(){
 array_map(['Service'=>'not_a_class', 'finish'=>'not_a_method'], []);
}
"""})
        self.assertEqual(self.refs(root), set())

    def test_string_class_is_absolute_but_class_constant_uses_namespace(self):
        root = self.project({'app.php': """<?php
namespace App;
class Service {public static function finish() {}}
function literal(){array_map(['Service','finish'], []);}
function constant(){array_map([Service::class,'finish'], []);}
""", 'global.php': '<?php class Service {public static function finish() {}}'})
        self.assertEqual(self.refs(root), {('app.php','literal','global.php','Service.finish'),
                                         ('app.php','constant','app.php','Service.finish')})

    def test_callback_classes_follow_namespace_blocks(self):
        root = self.project({'app.php': """<?php
namespace A {class Service {public static function finish(){}} function drive(){array_map([Service::class,'finish'], []);}}
namespace B {class Service {public static function finish(){}} function other(){array_map([Service::class,'finish'], []);}}
"""})
        with contextlib.closing(cg.connect(str(root))) as db:
            rows = list(db.execute('SELECT s.line,t.line FROM symbol_refs r JOIN symbols s ON s.id=r.owner_id JOIN symbols t ON t.id=r.target_id'))
        self.assertEqual(sorted(rows), [(2,2), (3,3)])

    def test_interpolated_strings_never_name_a_callback(self):
        root = self.project({'app.php': r'''<?php function polish(){} function drive($name){
 array_map("$name", []); array_map("pol\x69sh", []); array_map('polish', []);
}'''})
        with contextlib.closing(cg.connect(str(root))) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM symbol_refs').fetchone()[0], 1)
        self.assertEqual(self.refs(root), {('app.php','drive','app.php','polish')})

    def test_cross_file_shadow_is_reconciled_incrementally(self):
        root = self.project({'app.php': "<?php namespace App; function drive(){array_map('polish', []); call_user_func('polish');}",
                             'lib.php': '<?php function polish(){}'})
        def dynamic():
            with contextlib.closing(cg.connect(str(root))) as db:
                return db.execute("SELECT dynamic FROM calls WHERE name='call_user_func'").fetchone()[0]
        expected = {('app.php','drive','lib.php','polish')}
        self.assertEqual(self.refs(root), expected)
        self.assertEqual(dynamic(), 1)
        (root/'shadow.php').write_text('<?php namespace App; function array_map($x,$y){} function call_user_func($x){}')
        self.reindex(root)
        self.assertEqual(self.refs(root), set())
        self.assertEqual(dynamic(), 0)
        (root/'shadow.php').unlink()
        self.reindex(root)
        self.assertEqual(self.refs(root), expected)
        self.assertEqual(dynamic(), 1)
        (root/'app.php').write_text('<?php function replacement(){}')
        self.reindex(root)
        with contextlib.closing(cg.connect(str(root))) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM observations WHERE kind='php.callback_api'").fetchone()[0], 0)

    def test_internal_provenance_is_absent_from_public_queries(self):
        root = self.project({'app.php': "<?php function polish(){} function drive(){array_map('polish', []);}",
                             'io.py': "def read():\n    return open('input.txt').read()\n"})
        with contextlib.closing(cg.connect(str(root))) as db:
            self.assertGreater(db.execute("SELECT count(*) FROM observations WHERE kind='php.callback_api'").fetchone()[0], 0)
            drive = db.execute("SELECT id FROM symbols WHERE name='drive'").fetchone()[0]
        ProjectData = runpy.run_path(str(REPO/'scripts/project_data.py'))['ProjectData']
        with ProjectData(cg, root) as project:
            resources = project.resources(limit=1)
            self.assertEqual(resources['total'], 1)
            self.assertEqual(resources['items'][0]['kind'], 'file.read')
            self.assertEqual(project.resources(sid=drive)['total'], 0)
            public = [project.architecture(), project.overview(), project.detail(drive), project.feature_catalog()]
            public.append(project.feature('symbol:'+str(drive)))
        with contextlib.closing(cg.connect(str(root))) as db:
            public.append(cg.export_data(db, argparse.Namespace(root=str(root), min_conf='low', symbol=None, max_nodes=100, depth=2)))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cg.cmd_stats(argparse.Namespace(root=str(root), json=True))
        public.append(out.getvalue())
        mcp = runpy.run_path(str(REPO/'scripts/mcp_server.py'))
        for tool, args in [('cg_show', {'symbol':'drive'}), ('cg_prepare', {'symbol':'drive'}), ('cg_impact', {'target':'drive'})]:
            result = mcp['_call'](cg, str(root), tool, args)
            self.assertFalse(result['isError'], result)
            public.append(result)
        self.assertNotIn('php.callback_api', json.dumps(public))

    def test_dynamic_receiver_protects_only_its_namespace_class(self):
        root=self.project({
            'app.php':r'<?php use A\Widget; function drive($m) {$w=new Widget(); return $w->$m();}',
            'a.php':r'<?php namespace A; class Widget {public function first_live(){}}',
            'b.php':r'<?php namespace B; class Widget {public function unrelated_unused(){}}'})
        with contextlib.closing(cg.connect(str(root))) as db:
            verdict={cg.fmt(db,sid):v for v,sid,*_ in cg.dead_candidates(db,str(root))}
        self.assertTrue(any('a.php' in s and 'first_live' in s and v=='AMBIGUOUS' for s,v in verdict.items()))
        self.assertTrue(any('b.php' in s and 'unrelated_unused' in s and v=='LIKELY_DEAD' for s,v in verdict.items()))

    @unittest.skipUnless(shutil.which('php'), 'PHP native oracle required')
    def test_receiver_alternatives_never_make_executed_methods_dead(self):
        cases = [
            (r'$w=new \A\Widget();', {'A'}),
            (r'if(true){$w=new \A\Widget();}else{$w=new \B\Widget();}', {'A','B'}),
            (r'if(false){$w=new \A\Widget();}else{$w=new \B\Widget();}', {'A','B'}),
            (r'$w=new \A\Widget();while(false){$w=new \B\Widget();}', {'A','B'}),
            (r'$w=new \A\Widget();$w->$m();$w=new \B\Widget();', {'A','B'}),
        ]
        for body, possible in cases:
            with self.subTest(body=body):
                root = self.project({
                    'a.php': "<?php namespace A; class Widget{function liveR3(){echo 'A';}}",
                    'b.php': "<?php namespace B; class Widget{function liveR3(){echo 'B';}}",
                    'app.php': '<?php require __DIR__."/a.php";require __DIR__."/b.php";'
                        +'function drive($m){'+body+'$w->$m();}drive(str_rot13("yvirE3"));'})
                result = subprocess.run(['php', str(root/'app.php')], capture_output=True, text=True, timeout=10)
                self.assertEqual((result.returncode,result.stderr), (0,''))
                self.assertTrue(result.stdout)
                self.assertTrue(set(result.stdout) <= possible)
                with contextlib.closing(cg.connect(str(root))) as db:
                    verdict = {Path(path).stem.upper(): v for v,sid,*_ in cg.dead_candidates(db,str(root))
                               for path,name in db.execute('SELECT f.path,s.name FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.id=?',(sid,))
                               if name=='liveR3'}
                    self.assertEqual({ns for ns,v in verdict.items() if v=='AMBIGUOUS'}, possible)
                    self.assertTrue(all(verdict[ns]=='AMBIGUOUS' for ns in result.stdout))

    @unittest.skipUnless(shutil.which('php'), 'PHP native oracle required')
    def test_property_receiver_keeps_all_possible_classes(self):
        cases = [
            ('public $w;', r'$this->w=new \A\Widget();while(false){$this->w=new \B\Widget();}', 'new Holder()', {'A','B'}),
            ('public $w;', r'if(true){$this->w=new \A\Widget();}else{$this->w=new \B\Widget();}', 'new Holder()', {'A','B'}),
            ('public $w;', r'$this->w=new \A\Widget();', 'new Holder()', {'A'}),
            (r'public \A\Widget|\B\Widget $w;function __construct(){$this->w=new \A\Widget();}',
             r'while(false){$this->w=new \B\Widget();}', 'new Holder()', {'A','B'}),
            (r'function __construct(public \A\Widget $w){}',
             r'while(false){$this->w=new \B\Widget();}', r'new Holder(new \A\Widget())', {'A','B'}),
            (r'public \A\Widget|\B\Widget $w;function __construct($w){$this->w=$w;}',
             r'while(false){$this->w=new \B\Widget();}', r'new Holder(new \A\Widget())', {'A','B'}),
        ]
        for declaration, body, construction, possible in cases:
            with self.subTest(declaration=declaration, body=body):
                root=self.project({
                    'a.php': "<?php namespace A; class Widget{function liveR3(){echo 'A';}}",
                    'b.php': "<?php namespace B; class Widget{function liveR3(){echo 'B';}}",
                    'app.php': '<?php require __DIR__."/a.php";require __DIR__."/b.php";class Holder{'
                        +declaration+'function drive($m){'+body+'$this->w->$m();}}('
                        +construction+')->drive(str_rot13("yvirE3"));'})
                result=subprocess.run(['php',str(root/'app.php')],capture_output=True,text=True,timeout=10)
                self.assertEqual((result.returncode,result.stdout,result.stderr),(0,'A',''))
                with contextlib.closing(cg.connect(str(root))) as db:
                    verdict={Path(path).stem.upper():v for v,sid,*_ in cg.dead_candidates(db,str(root))
                             for path,name in db.execute('SELECT f.path,s.name FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.id=?',(sid,))
                             if name=='liveR3'}
                self.assertEqual({ns for ns,v in verdict.items() if v=='AMBIGUOUS'},possible)

    @unittest.skipUnless(shutil.which('php'), 'PHP native oracle required')
    def test_instance_callback_requires_a_unique_constructed_binding(self):
        catalog = {'format':'codegraph-catalog/1','entries':[{
            'id':'project.registry.listen','language':'php','role':'callback',
            'origin':{'via':'instance','module':'Events','name':'Registry.listen'},'args':[1],
            'source':'synthetic native PHP contract','checked_with':'PHP CLI'}]}
        cases = [
            ('$r=new Registry();', True, 'H'),
            ('$r=new Registry();$id=$r->id;', True, 'H'),
            ('$r=new Registry();$id=$r?->id;', True, 'H'),
            ('$r=new Registry();$ok=$r instanceof Registry;', True, 'H'),
            ('if(true){$r=new Fake();}else{$r=new Registry();}', False, ''),
            ('if(false){$r=new Fake();}else{$r=new Registry();}', False, 'H'),
            ('$r=new Fake();while(false){$r=new Registry();}', False, ''),
            ('$r=new Registry();$r=new Fake();', False, ''),
            ('$r=new Registry();$alias=&$r;$alias=new Fake();', False, ''),
            ('$r=new Registry();foreach([new Fake()] as $r){}', False, ''),
            ('$r=new Registry();replace($r);', False, ''),
            ('$r=new Registry();function unrelated($r){$r=new Fake();}', True, 'H'),
        ]
        for body, proven, trace in cases:
            with self.subTest(body=body):
                root = self.project({
                    'events.php': '<?php namespace Events; class Registry{public int $id=1;function listen($topic,$cb){$cb();}}',
                    'app.php': '<?php use Events\\Registry;require __DIR__."/events.php";'
                        +'class Fake{function listen($topic,$cb){}}function handle(){echo "H";}'
                        +'function replace(&$r){$r=new Fake();}function drive(){'+body
                        +'$r->listen("topic","handle");}drive();'},catalog)
                result = subprocess.run(['php', str(root/'app.php')], capture_output=True, text=True, timeout=10)
                self.assertEqual((result.returncode,result.stdout,result.stderr), (0,trace,''))
                expected = {('app.php','drive','app.php','handle')} if proven else set()
                self.assertEqual(self.refs(root), expected)

    @unittest.skipUnless(shutil.which('php'), 'PHP native oracle required')
    def test_property_callback_requires_a_unique_unescaped_construction(self):
        catalog = {'format':'codegraph-catalog/1','entries':[{
            'id':'project.registry.listen','language':'php','role':'callback',
            'origin':{'via':'instance','module':'Events','name':'Registry.listen'},'args':[1],
            'source':'synthetic native PHP contract','checked_with':'PHP CLI'}]}
        cases = [
            ('$this->r=new Registry();', '', '', True, 'H'),
            ('$this->r=new Registry();', '$id=$this->r->id;', '', True, 'H'),
            ('$this->r=new Registry();', '$ok=$this->r instanceof Registry;', '', True, 'H'),
            ('$this->r=new Fake();if(false){$this->r=new Registry();}', '', '', False, ''),
            ('$this->r=new Registry();if(false){$this->r=new Fake();}', '', '', False, 'H'),
            ('$this->r=new Registry();', '$alias=&$this->r;$alias=new Fake();', '', False, ''),
            ('$this->r=new Registry();', 'replace($this->r);', '', False, ''),
            ('$this->r=new Registry();', '$owner=$this;', '', False, 'H'),
            ('$value=new Registry();$this->r=$value;', '', '', False, 'H'),
            ('$this->r=new Registry();', '', '$holder->r=new Fake();', False, ''),
            ('$this->r=new Registry();', '', '$field="r";$holder->$field=new Fake();', False, ''),
        ]
        for visibility in ('public', 'private'):
            for init, body, outside, proven, trace in cases:
                if visibility == 'private' and outside:
                    continue
                with self.subTest(visibility=visibility, init=init, body=body, outside=outside):
                    root=self.project({
                        'events.php':'<?php namespace Events; class Registry{public int $id=1;function listen($topic,$cb){$cb();}}',
                        'app.php':'<?php use Events\\Registry;require __DIR__."/events.php";'
                            +'class Fake{function listen($topic,$cb){}}function handle(){echo "H";}'
                            +'function replace(&$r){$r=new Fake();}class Holder{'+visibility+' $r;'
                            +'function __construct(){'+init+'}function drive(){'+body
                            +'$this->r->listen("topic","handle");}}$holder=new Holder();'+outside+'$holder->drive();'},catalog)
                    result=subprocess.run(['php',str(root/'app.php')],capture_output=True,text=True,timeout=10)
                    self.assertEqual((result.returncode,result.stdout,result.stderr),(0,trace,''))
                    expected={('app.php','Holder.drive','app.php','handle')} if proven else set()
                    self.assertEqual(self.refs(root),expected)
                    out=io.StringIO()
                    with contextlib.redirect_stdout(out):
                        cg.cmd_impact(argparse.Namespace(root=str(root),target='app.php:handle',
                            min_conf='medium',depth=1,file=False,json=True,limit=100))
                    self.assertEqual(len(json.loads(out.getvalue())['items']),int(proven))

    def test_data_arrays_are_not_callbacks(self):
        root=self.project({'app.php':'''<?php
class Service {
 private function unused() {}
 public function drive() { return json_encode([$this, 'unused']); }
}
'''})
        self.assertEqual(self.refs(root),set())

    def test_hook_name_and_non_callback_arguments_are_data(self):
        root=self.project({'app.php':'''<?php
function unused() {}
function live() {}
function drive() {
 add_action('unused', 'live');
 array_reduce([], 'live', 'unused');
 register_shutdown_function('live', 'unused');
}
'''})
        self.assertEqual(self.refs(root),{('app.php','drive','app.php','live')})

    def test_catalog_positions_and_named_callbacks_keep_true_references(self):
        root=self.project({'app.php':'''<?php
function polish() {}
class Service {
 public function finish() {}
 public function drive($items) {
  array_map('polish', $items);
  array_filter($items, 'polish');
  array_walk($items, [$this, 'finish']);
  array_reduce(array: $items, callback: 'polish', initial: 1);
 }
}
'''})
        self.assertEqual(self.refs(root),{('app.php','Service.drive','app.php','polish'),
                                         ('app.php','Service.drive','app.php','Service.finish')})

    def test_use_function_firstclass_respects_import_and_alias(self):
        for name, imported in [('polish',r'use function Other\polish;'),('p',r'use function Other\polish as p;')]:
            with self.subTest(name=name):
                root=self.project({'app.php':f'<?php namespace App; {imported} function drive() {{return {name}(...)();}}',
                                   'lib.php':r'<?php namespace Other; function polish(){return 1;}',
                                   'global.php':f'<?php function {name}(){{return 0;}}'})
                self.assertEqual(self.refs(root),{('app.php','drive','lib.php','polish')})

    def test_callback_global_namespace_is_per_block(self):
        for lib in [
            "<?php\nnamespace {function polish(){}}\nnamespace Other {function other(){}}",
            "<?php\nnamespace Other {function other(){}}\nnamespace {function polish(){}}"]:
            with self.subTest(lib=lib):
                root=self.project({'app.php':"<?php namespace App; function drive(){array_map('polish', []);}", 'lib.php':lib})
                self.assertEqual(self.refs(root),{('app.php','drive','lib.php','polish')})

    def test_callback_does_not_bind_namespaced_function_after_global_block(self):
        root=self.project({'app.php':"<?php namespace App; function drive(){array_map('polish', []);}",
                           'lib.php':"<?php\nnamespace Other {function polish(){}}\nnamespace {function other(){}}"})
        self.assertEqual(self.refs(root),set())

    def test_firstclass_current_namespace_and_global_controls(self):
        for ns in ('',r'namespace App;'):
            with self.subTest(namespace=ns):
                root=self.project({'app.php':f'<?php {ns} function polish(){{}} function drive(){{return polish(...);}}',
                                   'other.php':r'<?php namespace Other; function polish(){}'})
                self.assertEqual(self.refs(root),{('app.php','drive','app.php','polish')})

    def test_catalog_builtin_is_not_a_same_named_project_function(self):
        root=self.project({'app.php':'''<?php namespace App;
function array_map($arg, $data) { return $arg; }
function drive() { return array_map('polish', []); }
''','lib.php':'<?php function polish(){}'})
        self.assertEqual(self.refs(root),set())

    def test_project_catalog_import_origin_uses_selected_argument(self):
        extension={'format':'codegraph-catalog/1','entries':[{
            'id':'project.callbacks.listen','language':'php','role':'callback',
            'origin':{'via':'import','module':'Events','name':'listen'},
            'args':[1],'source':'fixture contract','checked_with':'synthetic'}]}
        root=self.project({'app.php':r'''<?php namespace App;
use function Events\listen as hook;
function drive(){hook('unused', 'polish');}
''','lib.php':'''<?php function polish(){} function unused(){}'''},extension)
        self.assertEqual(self.refs(root),{('app.php','drive','lib.php','polish')})

    def test_provenance_distinguishes_two_callbacks_on_the_same_line(self):
        extension={'format':'codegraph-catalog/1','entries':[{
            'id':'project.callbacks.listen','language':'php','role':'callback',
            'origin':{'via':'import','module':'Events','name':'listen'},
            'args':[0],'source':'fixture contract','checked_with':'synthetic'}]}
        root=self.project({'app.php':r"<?php namespace App; use function Events\listen; function drive(){array_map('polish', []); listen('polish');}",
                           'shadow.php':'<?php namespace App; function array_map($a,$b){}',
                           'lib.php':'<?php function polish(){}'},extension)
        with contextlib.closing(cg.connect(str(root))) as db:
            rows=list(db.execute('SELECT target_id FROM symbol_refs ORDER BY id'))
        self.assertEqual(len(rows),2)
        self.assertIsNone(rows[0][0])
        self.assertIsNotNone(rows[1][0])


if __name__=='__main__':unittest.main()
