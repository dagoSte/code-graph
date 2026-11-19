"""JavaScript/TypeScript resolution gaps from the 1.6.2 survey: real indexing and impact on small fixtures.

The fixtures are only parsed, never executed.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / 'tests/fixtures/gaps_js'
spec = importlib.util.spec_from_file_location('cg_gaps_js_test', REPO / 'scripts/cg.py')
cg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cg)


class GapFixture(unittest.TestCase):
    def project(self, name):
        temp = tempfile.TemporaryDirectory(prefix='cg-gaps-js-')
        self.addCleanup(temp.cleanup)
        root = Path(temp.name) / 'project'
        shutil.copytree(FIXTURES / name, root)
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=False))
        return root

    def impact_json(self, root, target, confidence='low'):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cg.cmd_impact(argparse.Namespace(root=str(root), target=target, min_conf=confidence,
                                            depth=1, file=False, json=True, limit=100))
        return json.loads(output.getvalue())

    def impact(self, root, target, confidence='low'):
        return {item['symbol']['name']: item['chain'][0]['confidence']
                for item in self.impact_json(root, target, confidence)['items']}

    def calls(self, root, where=''):
        with contextlib.closing(cg.connect(str(root))) as db:
            return db.execute('SELECT f.path,c.line,c.name,c.recv,c.dynamic FROM calls c JOIN files f ON f.id=c.file_id '
                              'WHERE f.path LIKE ? ORDER BY f.path,c.line,c.id', ('%' + where,)).fetchall()

    def edges(self, root, caller_file):
        """{(line, target file, target): confidence} for the calls written in caller_file."""
        with contextlib.closing(cg.connect(str(root))) as db:
            return {(line, path, qname): conf for line, path, qname, conf in db.execute(
                'SELECT c.line,fb.path,sb.qname,e.conf FROM edges e JOIN calls c ON c.id=e.call_id '
                'JOIN files fa ON fa.id=c.file_id JOIN symbols sb ON sb.id=e.callee_id JOIN files fb ON fb.id=sb.file_id '
                'WHERE fa.path=?', (caller_file,))}

    def certain(self, root, caller_file):
        """Edges above low: low ones are name hypotheses, as on 1.6.3."""
        return {key: conf for key, conf in self.edges(root, caller_file).items() if conf != 'low'}

    def dead_verdict(self, root, target):
        with contextlib.closing(cg.connect(str(root))) as db:
            sid = cg.resolve_one(db, target)
            return {sid_: verdict for verdict, sid_, *_ in cg.dead_candidates(db, str(root))}.get(sid)


class BarrelReExports(GapFixture):
    def test_star_reexport_through_directory_index(self):
        root = self.project('J07c')
        self.assertEqual(self.impact(root, 'lib/tools.ts:polish'), {'drive': 'high'})

    def test_star_reexport_leaves_the_unexported_homonym_alone(self):
        root = self.project('J07')
        # './lib/index' does not name tools.js, so the shared name check keeps the unique target at medium
        self.assertEqual(self.impact(root, 'lib/tools.js:polish'), {'drive': 'medium'})
        self.assertEqual(self.impact(root, 'lib/legacy.js:polish'), {})


class ModuleObjects(GapFixture):
    def test_required_module_as_receiver(self):
        root = self.project('J01b')
        self.assertEqual(self.impact(root, 'tools.js:polish'), {'drive': 'high'})

    def test_required_module_ignores_the_homonym_in_another_file(self):
        root = self.project('J01')
        self.assertEqual(self.impact(root, 'tools.js:polish'), {'drive': 'high'})
        self.assertEqual(self.impact(root, 'legacy.js:polish'), {})

    def test_namespace_import_as_receiver(self):
        root = self.project('J13b')
        self.assertEqual(self.impact(root, 'tools.ts:polish'), {'drive': 'high'})

    def test_namespace_import_ignores_the_homonym_in_another_file(self):
        root = self.project('J13')
        self.assertEqual(self.impact(root, 'tools.js:polish'), {'drive': 'high'})
        self.assertEqual(self.impact(root, 'legacy.js:polish'), {})

    def test_parameter_shadowing_the_module_name_is_not_the_module(self):
        root = self.project('shadowed_module')
        self.assertEqual(self.impact(root, 'tools.js:polish'), {'direct': 'high'})

    def test_hoisted_caller_callback_parameter_and_python_homonym(self):
        root = self.project('module_forms')
        self.assertEqual(self.impact(root, 'tools.js:polish'), {'early': 'high'})  # not shadow(): tools => tools.polish()
        self.assertEqual(self.impact(root, 'tools.py:polish'), {})

    def test_commonjs_index_forwarding_required_modules(self):
        root = self.project('module_forms')
        self.assertEqual(self.impact(root, 'git/find.js:findRoot'), {'work': 'high'})
        self.assertEqual(self.impact(root, 'git/clone.js:cloneRepo'), {'work': 'high'})


class ModuleBindingsFollowTheProgram(GapFixture):
    """Reader's probes for 1.6.4 (B1-B3); Node v25 results in comments. Fixtures are valid CommonJS."""

    def test_reassigned_module_is_not_the_first_module(self):
        root = self.project('reassigned')
        self.assertEqual(self.certain(root, 'module.js'), {})  # Node: B
        self.assertEqual(self.certain(root, 'object.js'), {})  # Node: 0 (the local object)
        self.assertEqual(self.certain(root, 'later.js'), {})   # written in another function

    def test_var_redeclaration_is_a_write(self):
        root = self.project('reassigned')
        self.assertEqual(self.certain(root, 'redeclared.js'), {})  # Node: B; also var redeclared in a nested block

    def test_replaced_module_member_is_not_the_module_function(self):
        root = self.project('reassigned')
        self.assertEqual(self.certain(root, 'patched.js'), {})  # Node: SHADOW (mod.polish = ...)
        self.assertEqual(self.certain(root, 'configured.js'), {(3, 'b.js', 'polish'): 'high'})  # another member written

    def test_module_binding_that_is_never_written_is_kept(self):
        root = self.project('reassigned')
        self.assertEqual(self.certain(root, 'kept.js'), {(2, 'a.js', 'polish'): 'high'})

    def test_block_binding_stays_in_its_block(self):
        root = self.project('block_scope')
        self.assertEqual(self.certain(root, 'app.js'),  # Node: A A
                         {(3, 'b.js', 'polish'): 'high', (4, 'a.js', 'polish'): 'high'})

    def test_loop_catch_and_callback_locals_hide_the_module(self):
        root = self.project('block_scope')
        self.assertEqual(self.certain(root, 'locals.js'), {(6, 'a.js', 'polish'): 'high'})

    def test_require_that_is_a_local_name_is_not_the_loader(self):
        root = self.project('shadowed_require')
        self.assertEqual(self.certain(root, 'app.js'), {})  # Node: SHADOW
        self.assertNotIn('viaForward', self.impact(root, 'c.js:again'))

    def test_create_require_must_be_node_modules(self):
        root = self.project('shadowed_require')  # Node: SHADOW (parameter, fakecreateRequire, local createRequire)
        self.assertEqual({key: conf for key, conf in self.certain(root, 'creators.js').items() if key[1] == 'a.js'}, {})

    def test_create_require_from_node_module_loads(self):
        root = self.project('shadowed_require')
        self.assertEqual(self.certain(root, 'loader_esm.mjs'),
                         {(3, 'a.js', 'polish'): 'high', (4, 'b.js', 'polish'): 'high'})

    def test_global_and_created_require_still_load(self):
        root = self.project('shadowed_require')
        self.assertEqual(self.certain(root, 'loader.js'),
                         {(2, 'b.js', 'polish'): 'high', (5, 'a.js', 'polish'): 'high'})
        self.assertEqual(self.impact(root, 'c.js:again'), {'viaControl': 'high'})


class OneWritePerModuleBinding(GapFixture):
    """Reader's rc2 probes (Node v25): a module binding holds only while its declaration is the only write."""

    def module_edges(self, root, caller_file):
        return {key: conf for key, conf in self.certain(root, caller_file).items() if key[1] in ('a.js', 'run.js')}

    def test_any_other_write_to_the_name_drops_the_module(self):
        root = self.project('one_write')
        for name in ('var_arrow.js', 'var_class.js', 'function_name.js', 'logical.js', 'patterns.js', 'parameter.js'):
            with self.subTest(name=name):  # var_arrow.js is the probe: Node gives SHADOW
                self.assertEqual(self.module_edges(root, name), {})

    def test_single_declaration_is_kept(self):
        root = self.project('one_write')
        self.assertEqual(self.module_edges(root, 'kept.js'), {(2, 'a.js', 'polish'): 'high'})

    def test_member_writes_drop_that_member(self):
        root = self.project('member_writes')  # bracket.js and logical.js are the probes: Node gives SHADOW
        self.assertEqual(self.module_edges(root, 'bracket.js'), {(3, 'a.js', 'other'): 'high'})
        self.assertEqual(self.module_edges(root, 'logical.js'), {})
        self.assertEqual(self.module_edges(root, 'pattern.js'), {(3, 'a.js', 'other'): 'high'})
        self.assertEqual(self.module_edges(root, 'other_member.js'), {(3, 'a.js', 'polish'): 'high'})

    def test_computed_delete_and_object_assign_drop_every_member(self):
        root = self.project('member_writes')
        for name in ('computed.js', 'deleted.js', 'assigned.js'):
            with self.subTest(name=name):
                self.assertEqual(self.module_edges(root, name), {})

    def test_create_require_chain_must_start_from_the_loader(self):
        root = self.project('loader_chain')  # shadowed_factory.js is the probe: Node gives SHADOW
        for name in ('shadowed_factory.js', 'cycle.js', 'creator_reassigned.js', 'fake_module.js'):
            with self.subTest(name=name):
                self.assertEqual(self.module_edges(root, name), {})
        self.assertEqual(self.module_edges(root, 'real.js'), {(4, 'a.js', 'polish'): 'high'})


class LoaderTamperedInTheFile(GapFixture):
    """Reader's rc3 probes (Node v25): a file that writes to the loader sends no call through it."""

    def module_edges(self, root, caller_file):
        return {key: conf for key, conf in self.certain(root, caller_file).items() if key[1] in ('a.js', 'b.js')}

    def test_writes_to_the_loader_drop_every_module_edge_in_the_file(self):
        root = self.project('tampered')  # factory_member.js and global_require.js are the probes: Node gives SHADOW
        for name in ('factory_member.js', 'global_require.js', 'require_member.js', 'require_logical.js',
                     'module_object.js', 'factory_bracket.js', 'factory_assign.js', 'create_require_write.js'):
            with self.subTest(name=name):
                self.assertEqual(self.module_edges(root, name), {})

    def test_module_bound_receiver_is_never_a_same_named_class(self):
        root = self.project('tampered')  # Node: A; decoy.js has class Service { static polish() }
        self.assertEqual(self.certain(root, 'receiver_class.js'), {})
        self.assertEqual(self.certain(root, 'receiver_class_written.js'), {})
        self.assertEqual(self.certain(root, 'receiver_class_control.js'), {(2, 'a.js', 'polish'): 'high'})

    def test_unrelated_writes_keep_the_loader(self):
        root = self.project('tampered')  # Node: A
        self.assertEqual(self.module_edges(root, 'unrelated_member.js'), {(3, 'a.js', 'polish'): 'high'})
        self.assertEqual(self.module_edges(root, 'exports_control.js'), {(2, 'b.js', 'polish'): 'high'})


class ImportedNamesFollowTheirScope(GapFixture):
    """J02 (1.6.5): const { f } = require('./m') binds f to m's export f; an imported name (ESM or CommonJS) is that
    import only where no parameter, local or local function hides it. Node v25.2.1 results in comments."""

    def test_destructured_require_is_the_module_member(self):
        root = self.project('destructured_require')  # Node: T S (decoy.js has polish and gloss too)
        self.assertEqual({k: v for k, v in self.certain(root, 'app.js').items() if k[0] == 2},
                         {(2, 'tools.js', 'polish'): 'high', (2, 'tools.js', 'shine'): 'high'})
        self.assertEqual(self.impact(root, 'decoy.js:polish', 'medium'), {})  # low rows stay name hypotheses
        self.assertEqual(self.impact(root, 'decoy.js:gloss', 'medium'), {})

    def test_destructured_require_follows_scope_writes_and_the_loader(self):
        root = self.project('destructured_require')
        edges = self.certain(root, 'app.js')
        self.assertNotIn(3, {line for line, *_ in edges})  # shadowed(polish): Node P, the argument
        self.assertEqual({k: v for k, v in edges.items() if k[0] == 4}, {(4, 'app.js', 'local.polish'): 'high'})  # Node: L
        self.assertEqual({k: v for k, v in edges.items() if k[0] == 6}, {(6, 'app.js', 'later'): 'high'})  # Node: X
        self.assertEqual({k for k in self.certain(root, 'fake_loader.js') if k[1] != 'fake_loader.js'}, set())  # SHADOW

    def test_destructured_member_can_be_exported_again(self):
        root = self.project('destructured_require')  # module.exports = { polish } forwards tools.js polish: Node T
        self.assertEqual(self.certain(root, 'forward.js'), {(2, 'tools.js', 'polish'): 'high'})

    def test_esm_named_import_hidden_by_a_local(self):
        root = self.project('esm_named_shadow')  # Node: T P L B Q
        self.assertEqual(self.certain(root, 'app.mjs'), {(2, 'tools.mjs', 'polish'): 'high', (4, 'app.mjs', 'local.polish'): 'high',
                                                         (5, 'app.mjs', 'block.polish'): 'high'})


class ModuleMembersPassedAsArguments(GapFixture):
    """A direct module member argument is a reference; locals and altered bindings are not.
    Native witnesses are checked separately with Node; the index never executes the fixtures."""

    def refs(self, root):
        with contextlib.closing(cg.connect(str(root))) as db:
            return {(path, line, name, target) for path, line, name, target in db.execute(
                'SELECT f.path,r.line,r.name,fb.path||":"||st.qname FROM symbol_refs r JOIN files f ON f.id=r.file_id '
                'JOIN symbols st ON st.id=r.target_id JOIN files fb ON fb.id=st.file_id WHERE r.receiver!=""')}

    def test_binding_matrix(self):
        # The same cases are checked with Node in the author's native probe; indexing never executes them.
        cases = json.loads((FIXTURES / 'module_member_args/matrix.json').read_text())
        for case in cases:
            with self.subTest(case=case['name']), tempfile.TemporaryDirectory(prefix='cg-member-arg-') as temp:
                root = Path(temp)
                (root / 'target.js').write_text(case.get('target', 'function handle() { return "real"; } exports.handle = handle;\n'))
                (root / 'decoy.js').write_text('function handle() { return "decoy"; } exports.handle = handle;\n')
                (root / ('app.' + case['ext'])).write_text(
                    'function other() { return "decoy"; }\nfunction sink(fn) { console.log(fn ? fn() : "missing"); }\n' + case['code'])
                with contextlib.redirect_stdout(io.StringIO()):
                    cg.cmd_index(argparse.Namespace(root=str(root), full=False))
                targets = {target for _, _, name, target in self.refs(root) if name == 'handle'}
                self.assertEqual(targets, {'target.js:handle'} if case['reference'] else set())

    def test_export_slots_match_native_values(self):
        cases = json.loads((FIXTURES / 'module_member_args/exports.json').read_text())
        for case in cases:
            with self.subTest(case=case['name']), tempfile.TemporaryDirectory(prefix='cg-export-arg-') as temp:
                root = Path(temp)
                (root / case['target_file']).write_text(case['target'])
                (root / ('app.' + case['ext'])).write_text('function sink(fn) {}\n' + case['code'])
                with contextlib.redirect_stdout(io.StringIO()):
                    cg.cmd_index(argparse.Namespace(root=str(root), full=False))
                targets = {target for _, _, _, target in self.refs(root)}
                native = {path + ':' + name for path, name in case['targets']}
                self.assertLessEqual(targets, native)
                if case.get('required'):
                    self.assertEqual(targets, native)
                    for target in native:
                        self.assertEqual(self.impact(root, target, confidence='medium').get(case['consumer']), 'medium')

    def test_module_member_argument_is_a_reference(self):
        root = self.project('module_member_args')  # Node: /users=users.list id=users.load, ordered ab, /esm=users.show
        refs = self.refs(root)                     # /decoy=decoy.show (a default import of decoy.js), /c=users.list
        for ref in [('app.js', 5, 'list', 'users.js:list'), ('app.js', 6, 'load', 'users.js:load'),
                    ('app.js', 7, 'byName', 'sort.js:byName'), ('capital.js', 3, 'list', 'users.js:list'),
                    ('esm.mjs', 3, 'show', 'users.js:show'), ('esm.mjs', 3, 'show', 'decoy.js:show'),
                    ('local_homonym.js', 3, 'byName', 'sort.js:byName')]:
            with self.subTest(ref=ref):
                self.assertIn(ref, refs)
        self.assertEqual(self.impact(root, 'sort.js:byName'), {'ordered': 'medium'})

    def test_homonyms_locals_parameters_and_written_modules_are_not(self):
        root = self.project('module_member_args')
        refs = self.refs(root)
        self.assertEqual(self.impact(root, 'decoy.js:list'), {})  # the same name in another module
        self.assertEqual(self.impact(root, 'local_homonym.js:byName'), {})  # the same name in the calling file
        self.assertFalse([r for r in refs if r[2] == 'show' and r[0] == 'app.js'])  # mount(users) and local.show: Node
        self.assertFalse([r for r in refs if r[0] == 'patched.js'])  # param.show, local.show; patched.js: users.show and
        self.assertEqual(self.impact(root, 'users.js:show'), {'register': 'medium'})  # sort written: Node patched

    def test_await_callee_keeps_the_existing_parameter_filter(self):
        root = self.project('module_member_args')  # Node runs other, not the file's handle.
        with contextlib.closing(cg.connect(str(root))) as db:
            targets = {row[0] for row in db.execute(
                'SELECT s.name FROM symbol_refs r JOIN symbols s ON s.id=r.target_id '
                'JOIN files f ON f.id=r.file_id WHERE f.path="await_shadow.js"')}
        self.assertEqual(targets, {'other'})


class CallbackNamesFollowTheirScope(GapFixture):
    """R4 (rinvio 1.6.4): a name passed as an argument is a reference to the function of that name only where no
    parameter or local hides it. Node v25.2.1: run/caught/viaArrow/looped/wrap call their own argument."""

    def test_parameter_named_like_a_function_is_not_a_reference(self):
        root = self.project('callback_params')
        self.assertEqual(self.impact(root, 'app.js:1'), {'use': 'medium', 'later': 'medium'})  # function polish, line 1
        self.assertEqual(self.impact(root, 'app.js:inner.polish'), {'inner': 'medium'})
        self.assertEqual(self.impact(root, 'lib.mjs:shine'), {'direct': 'medium'})


class EvidenceReasons(GapFixture):
    def reasons(self, root, caller, confidence='medium'):
        import runpy
        ProjectData = runpy.run_path(str(REPO / 'scripts/project_data.py'))['ProjectData']
        with ProjectData(cg, str(root)) as project:
            sid = cg.resolve_one(project.db, caller)
            return {(row['name'], row['reason']) for row in project.call_diagnostics(sid, confidence) if row['status'] == 'resolved'}

    def test_module_and_jsx_edges_name_their_evidence(self):
        self.assertEqual(self.reasons(self.project('J01b'), 'app.js:drive'), {('polish', 'module_binding')})
        self.assertEqual(self.reasons(self.project('J24'), 'App.jsx:App'), {('Badge', 'jsx_element')})
        self.assertIn(('run', 'module_method_candidate'), self.reasons(self.project('cjs_exports'), 'app.js:useInstance', 'low'))


class CompiledTypeScript(GapFixture):
    def test_comma_callee_is_a_call_of_the_imported_function(self):
        root = self.project('compiled_ts')  # tsc's CommonJS output: (0, mod_1.fn)(); Node: 3 3, later 1
        self.assertEqual(self.certain(root, 'app.js'), {
            (4, 'mod.js', 'fn'): 'high', (4, 'mod.js', 'helper'): 'high', (5, 'mod.js', 'fn'): 'high',
            (6, 'app.js', 'local'): 'high', (9, 'app.js', 'await'): 'high'})  # line 9: a sync function calls await(...)
        self.assertEqual(self.impact(root, 'decoy.js:fn'), {})

    def test_await_in_an_async_function_is_not_a_call(self):
        root = self.project('compiled_ts')  # tree-sitter reads await (0, f)(x) as await(0, f)(x)
        self.assertNotIn(('app.js', 5, 'await', 'none', 0), self.calls(root, 'app.js'))


class HandedOnModuleObjects(GapFixture):
    """1.6.5 (dec-164-varianti-rinvio): the 17 known wrongs of the oracle matrix and their class, checked with Node v25.2.1.
    A module object (or node:module) handed on, written through a wrapper or named with escapes, has unknown members;
    every file of handed_on calls a.js:polish on line 3 unless Node says otherwise (ts_angle.ts: TypeScript semantics,
    Node's type stripping refuses <T>expr)."""

    SHADOW = ('factory_escaped.js', 'factory_unicode.js', 'factory_parenthesized.js', 'factory_reflect_set.js',
              'factory_reflect_define.js', 'factory_reflect_delete.js', 'factory_alias.js', 'factory_destructure_alias.js',
              'factory_local_helper.js', 'factory_assign_alias.js', 'factory_object_computed.js', 'factory_call_assign.js',
              'ordinary_escape.js', 'ordinary_reflect.js', 'ordinary_alias.js', 'ts_cast.ts', 'ts_nonnull.ts')
    VARIANTS = ('ts_satisfies.ts', 'ts_angle.ts', 'ts_alias_cast.ts', 'octal_key.js', 'continued_key.js', 'escaped_owner.js',
                'logical_owner.js', 'sequence_owner.js', 'literal_owner.js', 'returned.js', 'arrow_result.js',
                'default_value.js', 'class_field.js', 'logical_assign.js', 'tagged.js', 'loop_alias.js', 'with_object.js',
                'delete_parenthesized.js', 'second_binding.js', 'escaped_require.js', 'escaped_shadow.js', 'exports_alias.js',
                'exports_member_alias.js', 'destructured_after_write.js', 'destructured_after_leak.js')

    def module_edges(self, root, caller_file):
        return {key: conf for key, conf in self.certain(root, caller_file).items() if key[1] == 'a.js'}

    def test_known_wrongs_of_the_matrix_have_no_strong_edge(self):
        root = self.project('handed_on')  # Node: SHADOW, or NOT_CALLABLE for factory_reflect_delete
        for name in self.SHADOW:
            with self.subTest(name=name):
                self.assertEqual(self.module_edges(root, name), {})

    def test_the_same_class_in_other_forms(self):
        root = self.project('handed_on')  # Node: SHADOW (delete_parenthesized: NOT_CALLABLE)
        for name in self.VARIANTS:
            with self.subTest(name=name):
                self.assertEqual(self.module_edges(root, name), {})

    def test_reads_copies_and_exports_keep_the_module(self):
        root = self.project('handed_on')  # Node: A
        for name in ('read_control.js', 'copy_control.js', 'export_control.js', 'factory_read_control.js', 'exports_object_control.js'):
            with self.subTest(name=name):
                self.assertEqual(self.module_edges(root, name), {(3, 'a.js', 'polish'): 'high'})

    def test_rebinding_the_module_name_leaves_the_loader(self):
        root = self.project('handed_on')  # module = {...} rebinds a local name; require stays Node's (Node: A A)
        self.assertEqual(self.module_edges(root, 'module_rebound.js'),
                         {(2, 'a.js', 'polish'): 'high', (3, 'a.js', 'polish'): 'high'})


class ExportsAsNodeSeesThem(GapFixture):
    """Reader's probes for 1.6.4 (N1-N3): what Node exposes, not every same-named function."""

    def test_export_star_follows_only_exported_unambiguous_names(self):
        root = self.project('reexport_rules')
        self.assertEqual(self.certain(root, 'private/app.js'), {})    # Node: no export named 'polish'
        self.assertEqual(self.certain(root, 'ambiguous/app.js'), {})  # Node: conflicting star exports
        self.assertEqual(self.certain(root, 'explicit/app.js'), {(2, 'explicit/b.js', 'polish'): 'medium'})  # Node: B
        self.assertEqual(self.certain(root, 'simple/app.js'), {(2, 'simple/a.js', 'polish'): 'medium'})

    def test_namespace_shows_only_exports(self):
        root = self.project('reexport_rules')
        self.assertEqual(self.certain(root, 'namespace/app.js'), {(2, 'namespace/a.js', 'shown'): 'high'})

    def test_last_commonjs_export_wins(self):
        root = self.project('cjs_exports')
        self.assertEqual(self.impact(root, 'replaced.js:second'), {'useReplaced': 'high'})  # Node: 2
        self.assertEqual(self.impact(root, 'replaced.js:first'), {})
        self.assertEqual(self.impact(root, 'stale.js:kept'), {'useStale': 'high'})
        self.assertEqual(self.impact(root, 'stale.js:lost'), {})  # exports.lost after module.exports = {...}
        self.assertEqual(self.impact(root, 'realias.js:main'), {'useRealias': 'high'})
        self.assertEqual(self.impact(root, 'realias.js:extra'), {'useRealias': 'high'})

    def test_export_target_is_read_as_node_reads_it(self):
        root = self.project('cjs_exports')  # (module)["exports"] = {...} replaces the exports: Node: 2
        self.assertEqual(self.impact(root, 'escaped_target.js:second'), {'useEscaped': 'high'})
        self.assertEqual(self.impact(root, 'escaped_target.js:first'), {})

    def test_export_that_may_not_run_gives_both_alternatives(self):
        root = self.project('cjs_exports')  # Node: plain, or debug with DEBUG set (1.6.4: no default at all)
        self.assertEqual(self.impact(root, 'conditional.js:plain'), {'useConditional': 'medium'})
        self.assertEqual(self.impact(root, 'conditional.js:debug'), {'useConditional': 'medium'})

    def test_export_replaced_in_a_callback_gives_both_alternatives(self):
        root = self.project('cjs_exports')  # npm arborist lib/tree-check.js: debug(() => module.exports = checkTree)
        self.assertEqual(self.impact(root, 'tree_check.js:checkTree'), {'useTreeCheck': 'medium'})  # Node: with CHECK_TREE
        self.assertEqual(self.impact(root, 'tree_check.js:exports'), {'useTreeCheck': 'medium'})    # Node: without it

    def test_class_export_is_a_class_symbol(self):
        root = self.project('cjs_exports')  # module.exports = class Service extends Base {...}: Node 1base
        self.assertEqual(self.impact(root, 'class_export.js:Service.create', 'medium'), {'useClass': 'high'})
        self.assertNotIn(self.dead_verdict(root, 'class_export.js:Service'), ('VERIFIED_DEAD', 'LIKELY_DEAD'))
        with contextlib.closing(cg.connect(str(root))) as db:
            self.assertEqual(db.execute('SELECT b.base, t.qname FROM bases b JOIN symbols s ON s.id=b.symbol_id '
                                        'JOIN symbols t ON t.id=b.base_id WHERE s.qname="Service" AND s.kind="class"').fetchall(),
                             [('Base', 'Base')])

    def target_lines(self, root, caller, name):
        with contextlib.closing(cg.connect(str(root))) as db:
            return {(path, line, conf) for path, line, conf in db.execute(
                'SELECT fb.path,sb.line,e.conf FROM edges e JOIN calls c ON c.id=e.call_id JOIN symbols sa ON sa.id=e.caller_id '
                'JOIN symbols sb ON sb.id=e.callee_id JOIN files fb ON fb.id=sb.file_id WHERE sa.name=? AND c.name=? AND e.conf!="low"',
                (caller, name))}

    def test_last_write_of_an_export_slot_wins(self):
        root = self.project('cjs_exports')  # Node: B (exports.polish = A; exports.polish = B) and second
        self.assertEqual(self.target_lines(root, 'useMember', 'polish'), {('member_replaced.js', 2, 'high')})
        self.assertEqual(self.impact(root, 'member_names.js:second'), {'useMember': 'high'})
        self.assertEqual(self.impact(root, 'member_names.js:first'), {})

    def test_slot_writes_that_may_run_later_are_alternatives(self):
        root = self.project('cjs_exports')  # Node: plain or traced (TRACE); early at load, later after setImmediate
        for name in ('plain', 'traced', 'early', 'later'):
            with self.subTest(name=name):
                self.assertEqual(self.impact(root, 'member_alternatives.js:' + name), {'useAlternatives': 'medium'})

    def test_function_export_and_its_properties(self):
        root = self.project('cjs_exports')
        # Object.assign(runScript, ...) exports runScript; passing it as an argument is also a reference (H2).
        self.assertEqual(self.impact(root, 'assign.js:runScript'), {'useAssign': 'high', 'assign.js': 'medium'})
        self.assertEqual(self.impact(root, 'props.js:mapWorkspaces'), {'useProps': 'high'})
        self.assertEqual(self.impact(root, 'props.js:virtual'), {'useProps': 'medium'})  # mapWorkspaces.virtual = ...

    def test_computed_export_keeps_the_low_method_hypothesis(self):
        root = self.project('cjs_exports')  # module.exports = new Service(): run() is a method of that file
        self.assertEqual(self.impact(root, 'instance.js:Service.run'), {'useInstance': 'low'})

    def test_workspace_package_exports_map(self):
        root = self.project('workspace_exports')
        self.assertEqual(self.impact(root, 'packages/lib/src/index.js:visible'), {'drive': 'high'})
        self.assertEqual(self.impact(root, 'packages/lib/src/tools.js:shine'), {'drive': 'high'})
        self.assertEqual(self.impact(root, 'packages/lib/src/private.js:polish'), {})  # ERR_PACKAGE_PATH_NOT_EXPORTED
        self.assertEqual(self.impact(root, 'packages/open/src/index.js:opened'), {'drive': 'high'})
        self.assertEqual(self.impact(root, 'packages/open/src/deep.js:deep'), {'drive': 'high'})  # no "exports"


class ExportsOfTheFileScope(GapFixture):
    """Reader's probes on B-js (Node v25.2.1): only the file's own module and exports export; escaped keys always index."""

    def strong(self, root, caller):
        with contextlib.closing(cg.connect(str(root))) as db:
            return {(path, qname, conf) for path, qname, conf in db.execute(
                'SELECT fb.path,sb.qname,e.conf FROM edges e JOIN symbols sa ON sa.id=e.caller_id JOIN symbols sb ON sb.id=e.callee_id '
                'JOIN files fb ON fb.id=sb.file_id WHERE sa.name=? AND e.conf!="low"', (caller,))}

    def test_a_local_named_module_or_exports_exports_nothing(self):
        root = self.project('exports_scope')  # Node: B for every file
        for name in ('parameter', 'block', 'variable', 'arrow', 'caught', 'pattern', 'exports_param'):
            with self.subTest(name=name):
                self.assertEqual(self.strong(root, 'use_' + name), {(name + '.js', 'second', 'high')})

    def test_what_a_local_named_module_receives_is_handed_on(self):
        root = self.project('exports_scope')  # stash(module) { module.exports = mod; module.exports.polish = ... }: SHADOW
        self.assertEqual(self.strong(root, 'drive'), set())

    def test_export_writes_in_a_function_and_later_certain_writes(self):
        root = self.project('exports_scope')  # delayed: A, or B when patch() runs; overridden: B
        self.assertEqual(self.strong(root, 'use_delayed'), {('delayed.js', 'first', 'medium'), ('delayed.js', 'second', 'medium')})
        self.assertEqual(self.strong(root, 'use_overridden'), {('overridden.js', 'second', 'high')})

    def test_surrogate_keys_index_and_name_nothing(self):
        root = self.project('exports_surrogate')  # exports['\\uD800'], ['\\uD83D\\uDE00'], ['\\u{1F600}']: Node B BA
        self.assertEqual(self.strong(root, 'useLone'), {('lone.js', 'second', 'high')})
        self.assertEqual(self.strong(root, 'usePair'), {('pair.js', 'second', 'high')})

    def test_lone_surrogates_do_not_replace_ascii_exports(self):
        for code in ('D800', 'DFFF'):
            for reverse in (False, True):
                for receiver in ('exports', 'module.exports'):
                    with self.subTest(code=code, reverse=reverse, receiver=receiver), tempfile.TemporaryDirectory() as tmp:
                        root = Path(tmp)
                        writes = [f'{receiver}.u{code} = second;', f'{receiver}["\\u{code}"] = first;']
                        if reverse:
                            writes.reverse()
                        (root / 'lib.js').write_text("function first(){return 'A'}\nfunction second(){return 'B'}\n" + '\n'.join(writes))
                        (root / 'app.js').write_text(f"const m = require('./lib');\nfunction drive(){{return m.u{code}();}}\n")
                        with contextlib.redirect_stdout(io.StringIO()):
                            cg.cmd_index(argparse.Namespace(root=str(root), full=True))
                        self.assertEqual(self.strong(root, 'drive'), {('lib.js', 'second', 'high')})

    def test_unicode_keys_remain_distinct_after_normalization(self):
        # Each spelling below denotes a different JavaScript string, including literal escape text and NUL.
        spellings = [r'\uD800', r'\uDFFF', r'\\uD800', r'\\uDFFF', 'uD800', 'uDFFF',
                     r'\0d800', r'\0dfff', r'\uD800.uD800', r'\uD800:uD800', r'\uD800\\uD800',
                     r'\0' + '00300064003800300030', r'\0\uD800', r'\uD800\0']
        normalized = [cg.js_name_text(s) for s in spellings]
        self.assertEqual(len(set(normalized)), len(spellings))
        for value in normalized:
            value.encode('utf-8')  # every key can be stored in SQLite
        self.assertEqual(cg.js_name_text('\0d800'), cg.js_name_text(r'\0d800'))
        self.assertEqual(cg.js_name_text(r'\uD835\uDC9C'), cg.js_name_text(r'\u{1D49C}'))
        self.assertEqual(cg.js_name_text(r'\uD835\uDC9C'), '𝒜')


class PathAliasesAndWorkspaces(GapFixture):
    def test_tsconfig_paths_alias(self):
        root = self.project('J23b')
        self.assertEqual(self.impact(root, 'src/lib/tools.ts:polish'), {'drive': 'high'})

    def test_tsconfig_paths_alias_ignores_the_homonym(self):
        root = self.project('J23')
        self.assertEqual(self.impact(root, 'src/lib/tools.ts:polish'), {'drive': 'high'})
        self.assertEqual(self.impact(root, 'src/lib/legacy.ts:polish'), {})

    def test_workspace_package_import(self):
        root = self.project('J23c')
        self.assertEqual(self.impact(root, 'packages/utils/src/index.ts:polish'), {'drive': 'high'})

    def test_unknown_bare_packages_stay_unresolved(self):
        root = self.project('bare_packages')
        for name in ('polish', 'useState', 'shine'):
            with self.subTest(name=name):
                self.assertEqual(self.impact(root, 'src/local.ts:' + name), {})

    def test_unknown_workspace_name_is_not_guessed(self):
        root = self.project('workspace_unknown')
        self.assertEqual(self.impact(root, 'packages/utils/src/index.ts:polish'), {'viaPackage': 'high'})

    def test_extended_tsconfig_paths_and_base_url(self):
        root = self.project('module_forms')
        self.assertEqual(self.impact(root, 'cfg/src/deep/mod.ts:deepFn'), {'useCfg': 'high'})
        self.assertEqual(self.impact(root, 'cfg/src/deep/mod.ts:baseFn'), {'useCfg': 'high'})

    def test_config_change_alone_re_resolves(self):
        root = self.project('module_forms')
        (root / 'cfg/tsconfig.base.json').write_text('{"compilerOptions": {"baseUrl": "src", "paths": {"~/*": ["gone/*"]}}}\n')
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=False))
        self.assertEqual(self.impact(root, 'cfg/src/deep/mod.ts:deepFn'), {})
        self.assertEqual(self.impact(root, 'cfg/src/deep/mod.ts:baseFn'), {'useCfg': 'high'})

    def meta(self, root):
        with contextlib.closing(cg.connect(str(root))) as db:
            return dict(db.execute('SELECT k,v FROM meta'))

    def test_config_only_edit_changes_the_revision(self):
        root = self.project('module_forms')  # cfg/tsconfig.json extends ./tsconfig.base.json
        (root / 'web').mkdir()
        (root / 'web/jsconfig.json').write_text('{"compilerOptions": {"baseUrl": "."}}\n')
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=False))
        self.assertTrue({'cfg/tsconfig.json', 'cfg/tsconfig.base.json', 'web/jsconfig.json'} <= set(cg.configuration_files(str(root))))
        before = self.meta(root)
        (root / 'cfg/tsconfig.base.json').write_text('{"compilerOptions": {"baseUrl": "src", "paths": {"~/*": ["gone/*"]}}}\n')
        current = {path: hashlib.sha256(raw).hexdigest() for path, raw in cg.configuration_files(str(root)).items()}
        self.assertNotEqual(current, json.loads(before['config_hashes']))  # queries see the index as stale until indexed
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=False))
        after = self.meta(root)
        self.assertNotEqual(after['content_revision'], before['content_revision'])
        self.assertNotEqual(after['revision'], before['revision'])


class DynamicLoads(GapFixture):
    def test_dynamic_import_is_recorded_as_dynamic(self):
        root = self.project('J04')
        self.assertIn(('app.js', 2, 'import', 'dynamic_import', 1), self.calls(root, 'app.js'))
        # the literal module of import('./tools.js').then(m => ...) is followed, one step below static imports
        self.assertEqual(self.impact(root, 'tools.js:polish'), {'drive': 'medium'})

    def test_impact_json_reports_the_dynamic_load_of_the_target_file(self):
        root = self.project('J04')
        hint = self.impact_json(root, 'tools.js:polish').get('dynamic')
        self.assertIsNotNone(hint)
        self.assertEqual(hint['calls'], 1)
        self.assertEqual([(load['file'], load['line'], load['caller']) for load in hint['loads']], [('app.js', 2, 'drive')])

    def test_computed_require_import_and_call_results_are_dynamic(self):
        root = self.project('dynamic_calls')
        dynamic = {(line, name) for path, line, name, recv, flag in self.calls(root, 'app.js') if flag}
        self.assertEqual(dynamic, {(9, ''), (12, 'require'), (15, 'import')})
        self.assertIn('dynamic', self.impact_json(root, 'app.js:polish'))

    def test_python_target_has_no_javascript_hint(self):
        root = self.project('J04')
        (root / 'tool.py').write_text('def helper():\n    return 1\n')
        with contextlib.redirect_stdout(io.StringIO()):
            cg.cmd_index(argparse.Namespace(root=str(root), full=False))
        self.assertNotIn('dynamic', self.impact_json(root, 'tool.py:helper'))


class ExportsAndJsx(GapFixture):
    def test_module_exports_function_is_not_dead_and_has_its_caller(self):
        root = self.project('J19')
        self.assertNotIn(self.dead_verdict(root, 'tools.js:polish'), ('VERIFIED_DEAD', 'LIKELY_DEAD'))
        self.assertEqual(self.impact(root, 'tools.js:polish'), {'drive': 'high'})

    def test_jsx_local_components_and_local_values(self):
        root = self.project('jsx_local')
        self.assertEqual(self.certain(root, 'App.jsx'), {(5, 'App.jsx', 'Card'): 'medium', (5, 'App.jsx', 'Frame'): 'medium'})

    def test_jsx_element_is_a_use_of_the_component(self):
        root = self.project('J24')
        self.assertEqual(self.impact(root, 'Badge.jsx:Badge'), {'App': 'medium'})
        self.assertNotIn('App', self.impact(root, 'Badge.jsx:Badge', 'high'))


if __name__ == '__main__':
    unittest.main()
