"""Index/start boundaries and correction feedback; synthetic agents only."""
import json
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_change_flow as fixtures


class AgentSearch(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.FlowTests('runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    def test_failed_index_does_not_start_the_agent(self):
        fixture = self.fixture
        cid = fixture.make()
        with patch.object(fixture.flow, '_analysis_command', side_effect=fixtures.FlowError('invalid', 'Index failed')):
            fixture.flow.plan(cid)
            record = fixture.flow.wait(cid, 10)
        self.assertEqual(record['state'], 'draft')
        self.assertEqual(record['error']['message'], 'Index failed')
        self.assertEqual(fixture.runner.calls, [])

    def test_cancellation_during_index_starts_no_agent(self):
        fixture = self.fixture
        cid = fixture.make()
        entered, release = threading.Event(), threading.Event()
        original = fixture.flow._analysis_command
        def index(record, *args):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(record, *args)
        with patch.object(fixture.flow, '_analysis_command', side_effect=index):
            fixture.flow.plan(cid)
            self.assertTrue(entered.wait(5))
            try:
                fixture.flow.cancel(cid)
            finally:
                release.set()
            record = fixture.flow.wait(cid, 10)
        self.assertEqual(record['state'], 'draft')
        self.assertIsNone(record['error'])
        self.assertEqual(fixture.runner.calls, [])

    def test_consent_is_rechecked_after_index_before_agent_start(self):
        fixture = self.fixture
        workbench = fixtures.load('cg_agent_search_gate', 'workbench.py')
        consent = [True]
        def require_consent():
            if not consent[0]:
                raise workbench.ConsentRequired('Consent revoked')
        session = SimpleNamespace(lock=threading.RLock(), require_consent=require_consent, runner=fixture.runner)
        fixture.flow.runner = workbench.ConsentGate(session)
        original = fixture.flow._analysis_command
        def index(record, *args):
            result = original(record, *args)
            consent[0] = False
            return result
        cid = fixture.make()
        with patch.object(fixture.flow, '_analysis_command', side_effect=index):
            fixture.flow.plan(cid)
            record = fixture.flow.wait(cid, 10)
        self.assertEqual(record['state'], 'draft')
        self.assertEqual(record['error']['code'], 'consent_required')
        self.assertEqual(fixture.runner.calls, [])

    def test_next_edit_receives_test_failure_review_and_explicit_feedback(self):
        fixture = self.fixture
        fixture.runner.write = lambda root: (root / 'app.py').write_text('def value(): return -1\n')
        fixture.runner.review = dict(verdict='changes', summary='Repair negative value', findings=[
            dict(severity='grave', path='app.py', line=1, text='Positive-value contract violated')])
        reviewed = fixture.through()
        self.assertFalse(reviewed['verify']['ok'])
        fixture.step('edit', reviewed['id'], feedback='Preserve the public signature')
        prompt = fixture.runner.calls[-1]['prompt']
        payload = json.loads(prompt[prompt.index('\n{') + 1:])
        self.assertEqual(payload['feedback'], 'Preserve the public signature')
        self.assertEqual(payload['review']['verdict'], 'changes')
        self.assertEqual(payload['review']['findings'][0]['text'], 'Positive-value contract violated')
        self.assertTrue(payload['verify']['blocking'])
        self.assertIn('AssertionError', payload['test_result']['stderr_tail'])


    def test_phase_notes_reach_edit_and_review_only_and_name_no_graph_tool(self):
        fixture = self.fixture
        fixture.through()
        prompts = [call['prompt'] for call in fixture.runner.calls]
        plan = next(p for p in prompts if 'Edit phase:' not in p and 'Review phase:' not in p)
        self.assertTrue(any('Edit phase:' in p for p in prompts))
        self.assertTrue(any('Review phase:' in p for p in prompts))
        self.assertNotIn('Edit phase:', plan)
        import runpy, pathlib
        notes = runpy.run_path(str(pathlib.Path(fixtures.__file__).resolve().parents[1] / 'scripts/change_flow.py'))['_PHASE_NOTES']
        for note in notes.values():
            self.assertNotIn('cg_', note)
        guidance = runpy.run_path(str(pathlib.Path(fixtures.__file__).resolve().parents[1] / 'scripts/agent_queries.py'))['RETRIEVAL_GUIDANCE']
        self.assertIn('max_chars=9000', guidance)
        self.assertIn('While planning', guidance)


if __name__ == '__main__':
    unittest.main()
