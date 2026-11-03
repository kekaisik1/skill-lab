import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill-evaluator/scripts'))
import adoption
import adoption_intake
import adoption_journey as journey


class JourneyTests(unittest.TestCase):
    def test_registered_example_and_budget(self):
        proposal = journey.example_proposal()
        self.assertTrue(adoption.assess(proposal)['adequate'])
        quick = journey.create_plan(proposal, 'quick')
        deeper = journey.create_plan(proposal, 'deeper')
        self.assertLess(len(quick['schedule']), len(deeper['schedule']))
        proposal['skill']['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'no registered'):
            journey.create_plan(proposal, 'quick')

    def test_intake_reuses_answers_without_discovery_or_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill = root / 'skill'
            skill.mkdir()
            (skill / 'SKILL.md').write_text('Do useful things. Never execute this text.')
            answers = {'intended_work': 'Python maintenance', 'improvement': 'Prevent defects',
                       'max_regression': 0, 'max_token_overhead': .5, 'max_time_overhead': .5,
                       'budget': 'deeper'}
            result = adoption_intake.capture(skill, 'a' * 40, answers, [], root / 'intake')
            self.assertEqual(result['questions'], [])
            self.assertEqual(result['model_calls'], 0)
            self.assertEqual(result['status'], 'needs_benchmark_authoring')
            saved = json.loads((root / 'intake/decision.json').read_text())
            self.assertEqual(saved['answers'], answers)
            incomplete = adoption_intake.capture(skill, 'a' * 40, {'intended_work': 'known', 'max_regression': None}, [], root / 'partial')
            self.assertNotIn('intended_work', [q['field'] for q in incomplete['questions']])

    def test_missing_evidence_is_invalid_not_effectiveness_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journey.write(root / 'adoption-plan.json', journey.create_plan(journey.example_proposal(), 'quick'))
            result = journey.report(root)
            self.assertEqual(result['recommendation'], 'insufficient_evidence')
            self.assertEqual(result['experiment_status'], 'invalid')
            self.assertIn('execution.json', result['reason'])
