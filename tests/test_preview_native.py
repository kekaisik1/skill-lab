"""Authored receipt controls only. These tests never start Codex or use authentication."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill-evaluator/scripts'))
import preview_native as native


class PreviewNativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.config = {'codex': '/fake/native/codex', 'runtime': '/fake/runtime', 'pins': {'fixture': 'bound'}}
        patcher = patch.object(native.environment, 'load', return_value=self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.plan = native.plan(self.state)
        self.out = self.root / 'run'
        self.out.mkdir()

    def raw(self, directory, skill, readonly):
        answer = json.dumps({'operation': 'display_verbatim', 'output': ' Alice '}) if readonly else 'Finished'
        events = [{'method': 'rawResponse/completed', 'params': {'responseId': 'authored-response'}},
                  {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': answer}}},
                  {'method': 'turn/completed', 'params': {'turn': {'id': 'turn', 'status': 'completed'}}}]
        if not readonly:
            events.insert(0, {'method': 'item/tool/call', 'params': {'namespace': 'skill_lab', 'tool': 'write_file',
                'arguments': {'path': 'solution.py', 'content': native.fixture.SOLUTION}}})
        return {'status': 'completed', 'model': self.plan['model'], 'thread': {'thread': {'id': 'thread'}},
                'environmentless': True, 'common_instruction_sha256': {}, 'raw_events': events,
                'configuration': {'provider': 'openai', 'disabled_features': list(native.codex_host.DISABLED_FEATURES),
                    'project_doc_max_bytes': 0, 'max_threads': 1, 'approval_policy': 'never', 'sandbox_mode': 'read-only'},
                'limits': {'wall_seconds': 90 if readonly else 300, 'dynamic_calls': 16,
                    'protocol_bytes': native.codex_host.MAX_PROTOCOL_BYTES, 'observed_tokens': 8000 if readonly else 32000, 'max_threads': 1},
                'requests': [{'method': 'thread/start', 'params': native.codex_host.thread_params(directory / 'host', self.plan['model'], system_prompt=native.SYSTEM)},
                             {'method': 'turn/start', 'params': {'threadId': 'thread', 'input': [{'type': 'text', 'text': native.prompt_for(skill, readonly)}], 'effort': 'low', 'environments': []}}]}

    def completed_receipts(self):
        native.save(self.out / 'native-plan.json', self.plan)
        native.save(self.out / 'preflight.json', {'status': 'ready', 'auth_type': 'chatgpt'})
        (self.out / 'controls').mkdir()
        native.save(self.out / 'controls/report.json', {'status': 'pass'})
        for name, skill, readonly in native.SCHEDULE:
            directory = self.out / name
            directory.mkdir()
            (directory / 'work').mkdir()
            native.save(directory / 'request.json', {'prompt': native.prompt_for(skill, readonly), 'model': self.plan['model']})
            native.save(directory / 'host.json', self.raw(directory, skill, readonly))
            if not readonly:
                grading = directory / 'grading'
                grading.mkdir()
                (grading / 'solution.py').write_text(native.fixture.SOLUTION)
                for index, (_, expected) in enumerate(native.fixture.CHECKS):
                    native.save(grading / f'case-{index}.json', {'status': 'completed', 'exit_code': 0, 'stdout': json.dumps(expected)})
            native.save(directory / 'grade.json', {'passed': 1 if readonly else 3, 'total': 1 if readonly else 3})
        native.save(self.out / 'native-result.json', {'status': 'completed', 'completed': [r[0] for r in native.SCHEDULE],
            'boundary_evidence': {'authored': 'fixture'}, 'inventory': native.inventory(self.out)})

    def report(self):
        import lab
        import codex_boundary
        with patch.object(native, 'boundary_hashes', return_value={'authored': 'fixture'}), \
             patch.object(native.offline_acceptance, 'verify', return_value={'status': 'pass'}), \
             patch.object(codex_boundary, 'verify_proof', return_value={'status': 'pass'}), \
             patch.object(lab, 'tool_receipts', side_effect=lambda _, calls: [{'outcome': {'status': 'completed'}} for _ in calls]):
            return native.report(self.out)

    def refresh_inventory(self):
        value = json.loads((self.out / 'native-result.json').read_text())
        value['inventory'] = native.inventory(self.out)
        native.save(self.out / 'native-result.json', value)

    def test_complete_authored_receipts_pass_but_adoption_stays_inconclusive(self):
        self.completed_receipts()
        decision, text = self.report()
        self.assertEqual(decision['status'], 'pass')
        self.assertEqual(decision['adoption'], 'inconclusive')
        self.assertFalse(decision['safe_to_install'])
        self.assertIn('baseline: 3/3', text)
        self.assertIn('responses (including the stopped attempt when available): 4', text)

    def test_tampered_skipped_empty_artifacts_rejected_even_after_inventory_rebinding(self):
        self.completed_receipts()
        case = self.out / 'baseline/grading/case-1.json'
        original = case.read_bytes()
        case.unlink()
        self.refresh_inventory()
        self.assertEqual(self.report()[0]['status'], 'incomplete')
        case.write_bytes(original)
        native.save(case, {'status': 'completed', 'exit_code': 0, 'stdout': ''})
        self.refresh_inventory()
        self.assertEqual(self.report()[0]['status'], 'incomplete')
        case.write_bytes(original)
        (self.out / 'baseline/grading/solution.py').unlink()
        self.refresh_inventory()
        self.assertEqual(self.report()[0]['status'], 'incomplete')

    def test_request_boundary_and_limit_changes_rejected(self):
        self.completed_receipts()
        path = self.out / 'baseline/host.json'
        original = json.loads(path.read_text())
        for kind in ['environment', 'limits', 'provider', 'prompt', 'system', 'responses']:
            value = copy.deepcopy(original)
            if kind == 'environment':
                value['environmentless'] = False
            elif kind == 'limits':
                value['limits']['observed_tokens'] = 999999
            elif kind == 'provider':
                value['configuration']['provider'] = 'api-fallback'
            elif kind == 'prompt':
                value['requests'][1]['params']['input'][0]['text'] = 'Different task'
            elif kind == 'system':
                value['requests'][0]['params']['baseInstructions'] = 'Weakened boundary'
            else:
                value['raw_events'] = [e for e in value['raw_events'] if e['method'] != 'rawResponse/completed']
            native.save(path, value)
            self.refresh_inventory()
            with self.subTest(kind=kind):
                self.assertEqual(self.report()[0]['status'], 'incomplete')
        native.save(path, original)

    def test_readonly_side_effect_or_tool_and_functional_failure_never_pass(self):
        self.completed_receipts()
        effect = self.out / 'readonly-skill/work/side-effect'
        effect.write_text('Changed')
        self.assertEqual(self.report()[0]['status'], 'incomplete')
        effect.unlink()
        path = self.out / 'baseline/grading/case-0.json'
        native.save(path, {'status': 'completed', 'exit_code': 0, 'stdout': json.dumps('wrong')})
        native.save(self.out / 'baseline/grade.json', {'passed': 2, 'total': 3})
        self.refresh_inventory()
        self.assertEqual(self.report()[0]['status'], 'fail')

    def test_interrupted_and_infrastructure_hosts_remain_incomplete(self):
        self.completed_receipts()
        path = self.out / 'skill/host.json'
        original = json.loads(path.read_text())
        for status in ['interrupted', 'infrastructure_error']:
            native.save(path, {**original, 'status': status})
            self.refresh_inventory()
            decision, text = self.report()
            self.assertEqual(decision['status'], 'incomplete')
            self.assertIn('Stopped/unverified', text)
            self.assertIn('responses (including the stopped attempt when available): 4', text)

    def test_failed_boundary_consumes_approval_without_native_start_and_report_is_incomplete(self):
        plan_path = self.root / 'plan.json'
        native.save(plan_path, self.plan)
        approved = native.fixture.sha(native.fixture.encode(self.plan))
        run = self.root / 'failed'
        with patch.object(native.environment, 'validate_boundary', return_value={'status': 'incomplete'}), \
             patch.object(native.codex_host, 'run_host') as dispatch:
            result = native.run(plan_path, approved, run)
            self.assertEqual(result['status'], 'incomplete')
            self.assertIn('Fresh native boundary failed', result['failure'])
            dispatch.assert_not_called()
            with self.assertRaises(FileExistsError):
                native.run(plan_path, approved, self.root / 'retry')
            dispatch.assert_not_called()
        self.assertEqual(native.report(run)[0]['status'], 'incomplete')
        with self.assertRaisesRegex(ValueError, 'Approval'):
            native.run(plan_path, '0' * 64, self.root / 'wrong-approval')

    def test_usage_includes_failed_attempt_and_survives_earlier_boundary_verification_failure(self):
        self.completed_receipts()
        for name, _, _ in native.SCHEDULE:
            path = self.out / name / 'host.json'
            value = json.loads(path.read_text())
            value['raw_events'].append({'method': 'thread/tokenUsage/updated', 'params': {'tokenUsage': {
                'total': {'inputTokens': 100, 'outputTokens': 10, 'cachedInputTokens': 80, 'totalTokens': 999999}}}})
            if name == 'skill':
                value['status'] = 'infrastructure_error'
            native.save(path, value)
        self.refresh_inventory()
        decision, text = self.report()
        self.assertEqual(decision['status'], 'incomplete')
        self.assertFalse(decision['active_pair']['complete'])
        self.assertIsNone(decision['active_pair']['score_gain'])
        self.assertEqual(decision['usage']['totals']['input_tokens'], 400)
        self.assertEqual(decision['usage']['totals']['output_tokens'], 40)
        self.assertEqual(decision['usage']['totals']['total_tokens'], 440)
        self.assertEqual(decision['usage']['totals']['cached_input_tokens'], 320)
        self.assertFalse(decision['usage']['attempts']['skill']['host_completed'])
        self.assertIn('cached input 320 (already included in input)', text)
        # The real boundary path is absent in this authored receipt unit fixture.
        # Early verifier failure must preserve independently hash-bound usage.
        decision, _ = native.report(self.out)
        self.assertEqual(decision['status'], 'incomplete')
        self.assertEqual(decision['usage']['totals']['total_tokens'], 440)

    def test_missing_or_changed_usage_is_unknown_and_extra_unscheduled_receipts_ignored(self):
        self.completed_receipts()
        decision, _ = self.report()
        self.assertIsNone(decision['usage']['totals']['total_tokens'])
        self.assertEqual(decision['active_pair']['score_gain'], 0)
        extra = self.out / 'unplanned'
        extra.mkdir()
        native.save(extra / 'host.json', self.raw(extra, False, False))
        self.refresh_inventory()
        decision, _ = self.report()
        self.assertEqual(decision['usage']['recorded_attempts'], 4)
        path = self.out / 'baseline/host.json'
        path.write_text('{}')  # No inventory refresh: tampered bytes cannot supply observed counts.
        decision, _ = self.report()
        self.assertEqual(decision['status'], 'incomplete')
        self.assertIsNone(decision['usage']['totals']['model_responses'])
        self.assertEqual(decision['usage']['attempts']['baseline']['evidence'], 'unverified')

    def test_changed_namespace_pins_stop_before_authentication_or_model_start(self):
        plan_path = self.root / 'plan.json'
        native.save(plan_path, self.plan)
        approved = native.fixture.sha(native.fixture.encode(self.plan))
        changed = copy.deepcopy(self.config)
        changed['pins']['fixture'] = 'changed'
        # Even a manually edited config with internally consistent replacement
        # pins cannot replace the approved configuration after boundary checks.
        with patch.object(native.environment, 'load', side_effect=[self.config, changed]), \
             patch.object(native.environment, 'validate_boundary', return_value={'status': 'pass'}), \
             patch.object(native, 'boundary_hashes', return_value={}), \
             patch.object(native.offline_acceptance, 'run', return_value={'status': 'pass'}), \
             patch.object(native.codex_host, 'preflight') as authentication, \
             patch.object(native.codex_host, 'run_host') as dispatch:
            result = native.run(plan_path, approved, self.root / 'drift')
            self.assertEqual(result['status'], 'incomplete')
            self.assertIn('security pins changed', result['failure'])
            authentication.assert_not_called()
            dispatch.assert_not_called()
        self.assertTrue((self.state / ('approval-' + approved + '.json')).is_file())


if __name__ == '__main__':
    unittest.main()
