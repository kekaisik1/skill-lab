"""Authored analysis controls only; no inference, candidate or real model evidence."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill-evaluator/scripts'))
import adoption


def proposal(software=False):
    value = {'schema': 'skill-lab-adoption-proposal/1', 'author': 'authoring-control',
             'skill': {'name': 'control', 'version': '1', 'sha256': 'a' * 64},
             'decision': {'intended_work': 'Two independently selected Python maintenance tasks',
                          'improvement': 'Prevent independently observed defects', 'user_confirmed': True,
                          'objective': 'quality', 'scope': 'software_control' if software else 'tested_work',
                          'min_gain': .1, 'max_regression': 0, 'max_token_overhead': .5,
                          'max_time_overhead': .5, 'workflow_is_outcome': False},
             'claims': [{'id': 'prevent', 'text': 'prevent defects'}], 'tasks': []}
    for index in range(3):
        value['tasks'].append({'id': 'case-' + str(index), 'split': 'evaluation', 'activate': index < 2,
                               'adapter': 'offline-controls', 'sha256': adoption.digest({'case': index}),
                               'claim_ids': ['prevent'], 'representative_reason': 'Selected maintenance operation; a control explanation is out of scope.',
                               'outcome': 'Observed externally checked program outputs', 'difficulty': 'ordinary',
                               'provenance': 'authored_control' if software else 'selected_work_example',
                               'expected_answers_exposed': False, 'evaluation_seen': False, 'baseline_rate': .5,
                               'grader': {'independent': True, 'control_verified': True, 'coverage': 'Output and mutation controls',
                                          'limitations': ['No maintainability judgment'], 'measures': 'correctness'}})
    return review(value)


def review(value):
    value['review'] = {'reviewer': 'independent-control', 'independent': True,
                       'representativeness': 'Authored reasoning control, not an empirical relevance claim.',
                       'discrimination': 'Independent outputs distinguish known defects; no tools-as-quality proxy.',
                       'limitations': ['Descriptive small sample'], 'proposal_sha256': adoption.proposal_hash(value)}
    return value


def prepared(value=None, budget='balanced'):
    return adoption.plan(value or proposal(), budget, {'model': 'authored-no-model', 'effort': 'none'})


def evidence(plan, baseline=.5, skill=.8):
    rows = []
    for session in plan['schedule']:
        rows.append({'session_id': session['session_id'], 'status': 'completed',
                     'score': (baseline if session['condition'] == 'baseline' else skill) if session['activate'] else 1,
                     'activation_pass': True, 'workflow_pass': session['condition'] == 'skill',
                     'input_tokens': 100, 'cached_input_tokens': 70, 'output_tokens': 20,
                     'wall_seconds': 2, 'tool_calls': 3})
    # The marker tests the real-model analysis branch; the module/test docstring
    # explicitly labels all these inputs as authored software evidence.
    return {'verified': True, 'security': 'pass', 'classification': 'real_model', 'status': 'completed',
            'plan_sha256': adoption.digest(plan), 'rows': rows}


class AdoptionTests(unittest.TestCase):
    def test_semantic_review_is_required_and_bound(self):
        value = proposal()
        self.assertTrue(adoption.assess(value)['adequate'])
        value['tasks'][0]['outcome'] = 'changed'
        self.assertIn('independent_review', [i['code'] for i in adoption.assess(value)['blockers']])
        review(value)
        value['review']['reviewer'] = value['author']
        self.assertFalse(adoption.assess(value)['adequate'])

    def test_specific_adequacy_blockers_have_corrections(self):
        mutations = [('decision', lambda p: p['decision'].update(user_confirmed=False)),
                     ('adapter', lambda p: p['tasks'][0].update(adapter='arbitrary-project')),
                     ('grader', lambda p: p['tasks'][0]['grader'].update(control_verified=False)),
                     ('contamination', lambda p: p['tasks'][0].update(expected_answers_exposed=True)),
                     ('contamination', lambda p: p['tasks'][0].update(evaluation_seen=True)),
                     ('public_fixture', lambda p: p['tasks'][0].update(provenance='public_fixture')),
                     ('ceiling', lambda p: p['tasks'][0].update(baseline_rate=1)),
                     ('floor', lambda p: p['tasks'][0].update(baseline_rate=0)),
                     ('proxy', lambda p: p['tasks'][0]['grader'].update(measures='workflow')),
                     ('untested_claims', lambda p: p['claims'].append({'id': 'other', 'text': 'unmeasured'}))]
        for expected, mutate in mutations:
            with self.subTest(expected=expected):
                value = proposal()
                mutate(value)
                result = adoption.assess(review(value))
                self.assertIn(expected, [i['code'] for i in result['blockers']])
                self.assertTrue(all(i['correction'] for i in result['blockers']))

    def test_same_case_cannot_be_calibration_and_evaluation(self):
        value = proposal()
        duplicate = copy.deepcopy(value['tasks'][0])
        duplicate.update(id='calibration', split='calibration')
        value['tasks'].append(duplicate)
        self.assertIn('case_pin', [i['code'] for i in adoption.assess(review(value))['blockers']])

    def test_software_fixture_cannot_be_mistaken_for_adoption_design(self):
        value = proposal(True)
        value['tasks'][0].update(provenance='public_fixture', baseline_rate=1)
        assessed = adoption.assess(review(value))
        self.assertTrue(assessed['adequate'])
        self.assertEqual(assessed['adequate_for'], 'software_validation')
        self.assertIn('ceiling', [i['code'] for i in assessed['warnings']])

    def test_unsupported_adapter_does_not_enable_live_execution(self):
        value = proposal()
        value['tasks'][0]['adapter'] = 'reviewed-private'
        review(value)
        with self.assertRaisesRegex(ValueError, 'inadequate'):
            prepared(value)
        self.assertTrue(adoption.assess(value, (*adoption.ADAPTERS, 'reviewed-private'))['adequate'])

    def test_budget_sums_separate_calibration_and_fixed_counterbalance(self):
        value = proposal()
        calibration = copy.deepcopy(value['tasks'][0])
        calibration.update(id='development', sha256='b' * 64, split='calibration')
        value['tasks'].append(calibration)
        result = prepared(review(value))
        self.assertEqual(result['calibration_sessions'], 2)
        self.assertEqual(result['evaluation_sessions'], 10)
        self.assertEqual(result['total_token_threshold'], 12 * 32000)
        self.assertEqual(result['separately_billed_api_allowance_usd'], 0)
        active = [s for s in result['schedule'] if s['task_id'] == 'case-0']
        self.assertEqual([s['condition'] for s in active], ['baseline', 'skill', 'skill', 'baseline'])
        self.assertEqual(len({s['session_id'] for s in result['schedule']}), 12)

    def test_deeper_six_active_tasks_means_38_sessions(self):
        value = proposal()
        for index in range(3, 7):
            task = copy.deepcopy(value['tasks'][0])
            task.update(id=f'case-{index}', sha256=adoption.digest({'case': index}))
            value['tasks'].append(task)
        result = prepared(review(value), 'deeper')
        self.assertEqual(result['evaluation_sessions'], 38)

    def test_budget_cannot_expand_user_cap(self):
        budget = {'name': 'custom', 'repetitions': 2, 'token_limit': 100, 'wall_seconds': 10,
                  'tool_limit': 3, 'max_sessions': 3}
        with self.assertRaisesRegex(ValueError, 'cannot cover'):
            prepared(budget=budget)
        budget['max_sessions'] = 10
        self.assertEqual(prepared(budget=budget)['total_token_threshold'], 1000)

    def test_nonactivation_limits_are_separate_and_summed(self):
        budget = {'name': 'custom', 'repetitions': 2, 'token_limit': 1000, 'wall_seconds': 100,
                  'tool_limit': 30, 'max_sessions': 10,
                  'nonactivation_limits': {'token_limit': 100, 'wall_seconds': 10, 'tool_limit': 3}}
        plan = prepared(budget=budget)
        self.assertEqual(plan['total_token_threshold'], 8200)
        self.assertEqual(plan['total_wall_seconds'], 820)
        self.assertEqual(plan['total_tool_limit'], 246)
        budget['nonactivation_limits']['tool_limit'] = -1
        with self.assertRaisesRegex(ValueError, 'Nonactivation'):
            prepared(budget=budget)

    def test_zero_model_budget_is_limited_to_offline_controls(self):
        budget = {'name': 'offline', 'repetitions': 2, 'token_limit': 0, 'wall_seconds': 60,
                  'tool_limit': 0, 'max_sessions': 10}
        plan = adoption.plan(proposal(True), budget, {'model': 'none', 'effort': 'none'})
        self.assertEqual(plan['total_token_threshold'], 0)
        with self.assertRaises(ValueError):
            adoption.plan(proposal(True), budget, {'model': 'live', 'effort': 'low'})

    def test_known_improvement_and_regression(self):
        plan = prepared()
        self.assertEqual(adoption.verdict(plan, evidence(plan))['recommendation'], 'worth_using')
        result = adoption.verdict(plan, evidence(plan, baseline=.8, skill=.6))
        self.assertEqual(result['recommendation'], 'not_worth_using')
        self.assertLess(result['correctness']['worst_pair_gain'], 0)

    def test_regression_cannot_cancel_across_repetitions(self):
        plan = prepared()
        data = evidence(plan, baseline=.5, skill=.9)
        data['rows'][1]['score'] = .4
        result = adoption.verdict(plan, data)
        self.assertGreater(result['correctness']['mean_task_gain'], 0)
        self.assertEqual(result['recommendation'], 'not_worth_using')

    def test_preservation_regression_is_not_workflow_or_correctness_proxy(self):
        plan = prepared()
        data = evidence(plan)
        data['rows'][1]['meaningful_regression'] = True
        result = adoption.verdict(plan, data)
        self.assertEqual(result['recommendation'], 'not_worth_using')
        self.assertEqual(result['correctness']['meaningful_regressions']['skill'], 1)

    def test_task_weighting_not_check_or_repetition_pool(self):
        plan = prepared()
        result = adoption.verdict(plan, evidence(plan))
        self.assertAlmostEqual(result['correctness']['mean_task_gain'], .3)
        self.assertEqual(len(result['pairs']), 2)

    def test_equal_correctness_ceiling_and_cost_decision(self):
        plan = prepared()
        data = evidence(plan, 1, 1)
        result = adoption.verdict(plan, data)
        self.assertEqual(result['recommendation'], 'insufficient_evidence')
        self.assertIn('ceiling', result['reason'])
        value = proposal()
        value['decision']['objective'] = 'cost'
        plan = prepared(review(value))
        data = evidence(plan, 1, 1)
        for row, session in zip(data['rows'], plan['schedule']):
            if session['condition'] == 'skill':
                row.update(input_tokens=60, cached_input_tokens=30)
        result = adoption.verdict(plan, data)
        self.assertEqual(result['recommendation'], 'worth_using')

    def test_workflow_without_outcome_improvement_is_not_benefit(self):
        plan = prepared()
        result = adoption.verdict(plan, evidence(plan, .8, .8))
        self.assertEqual(result['recommendation'], 'insufficient_evidence')
        self.assertGreater(result['workflow']['skill']['passed'], result['workflow']['baseline']['passed'])

    def test_explicit_workflow_requirement_can_be_outcome(self):
        value = proposal()
        value['decision'].update(objective='workflow', workflow_is_outcome=True)
        plan = prepared(review(value))
        self.assertEqual(adoption.verdict(plan, evidence(plan, 1, 1))['recommendation'], 'worth_using')

    def test_overhead_and_cached_usage(self):
        plan = prepared()
        data = evidence(plan)
        for row, session in zip(data['rows'], plan['schedule']):
            if session['condition'] == 'skill':
                row.update(input_tokens=300)
        result = adoption.verdict(plan, data)
        self.assertEqual(result['recommendation'], 'not_worth_measured_overhead')
        self.assertEqual(result['usage']['total_tokens'], 5 * 120 + 5 * 320)
        self.assertEqual(result['usage']['cached_input_tokens'], 10 * 70)

    def test_unknown_usage_is_not_zero(self):
        plan = prepared()
        data = evidence(plan)
        data['rows'][0]['input_tokens'] = None
        result = adoption.verdict(plan, data)
        self.assertEqual(result['recommendation'], 'insufficient_evidence')
        self.assertEqual(result['usage']['missing']['total_tokens'], 1)

    def test_calibration_usage_never_contributes_outcomes(self):
        value = proposal()
        task = copy.deepcopy(value['tasks'][0])
        task.update(id='calibration', split='calibration', sha256='e' * 64)
        value['tasks'].append(task)
        plan = prepared(review(value))
        data = evidence(plan)
        data['rows'][0]['score'] = 1
        data['rows'][1]['score'] = 0
        result = adoption.verdict(plan, data)
        self.assertEqual(result['recommendation'], 'worth_using')
        self.assertEqual(result['usage_by_split']['calibration']['attempts'], 2)
        self.assertEqual(len(result['pairs']), 2)

    def test_missing_duplicate_skipped_tampered_and_wrong_pin(self):
        plan = prepared()
        for mutation, status in ((lambda d: d['rows'].pop(), 'incomplete'),
                                 (lambda d: d['rows'].append(d['rows'][0]), 'invalid'),
                                 (lambda d: d['rows'][0].update(status='skipped'), 'invalid'),
                                 (lambda d: d['rows'][0].update(tampered=True), 'invalid'),
                                 (lambda d: d.update(plan_sha256='bad'), 'invalid')):
            data = evidence(plan)
            mutation(data)
            self.assertEqual(adoption.verdict(plan, data)['experiment_status'], status)

    def test_interruption_and_infrastructure_count_consumed_attempts(self):
        plan = prepared()
        for status, expected in [('interrupted', 'incomplete'), ('infrastructure_error', 'infrastructure_failure')]:
            data = evidence(plan)
            data['rows'] = data['rows'][:1]
            data['rows'][0]['status'] = status
            result = adoption.verdict(plan, data)
            self.assertEqual(result['experiment_status'], expected)
            self.assertEqual(result['usage']['total_tokens'], 120)

    def test_security_failure_is_not_negative_effectiveness(self):
        plan = prepared()
        data = evidence(plan)
        data['security'] = 'fail'
        result = adoption.verdict(plan, data)
        self.assertEqual(result['experiment_status'], 'security_failure')
        self.assertEqual(result['recommendation'], 'insufficient_evidence')

    def test_failure_before_boundary_is_infrastructure_not_invalid_effectiveness(self):
        plan = prepared()
        data = evidence(plan)
        data.update(status='infrastructure_error', security='unverified', rows=[], failure='namespace permission denied')
        result = adoption.verdict(plan, data)
        self.assertEqual(result['experiment_status'], 'infrastructure_failure')
        self.assertIn('namespace permission denied', result['reason'])
        data['verified'] = False
        self.assertEqual(adoption.verdict(plan, data)['experiment_status'], 'invalid')

    def test_authored_rules_are_reported_without_adoption_claim(self):
        plan = prepared(proposal(True))
        data = evidence(plan)
        data['classification'] = 'authored_control'
        result = adoption.verdict(plan, data)
        self.assertEqual(result['experiment_status'], 'valid_software_control')
        self.assertEqual(result['recommendation'], 'insufficient_evidence')
        self.assertEqual(result['control_rule_result']['recommendation'], 'worth_using')
        self.assertIn('Insufficient evidence', adoption.render(result))

    def test_baseline_activation_failure_does_not_reject_skill(self):
        plan = prepared()
        data = evidence(plan)
        data['rows'][0]['activation_pass'] = False
        self.assertEqual(adoption.verdict(plan, data)['recommendation'], 'worth_using')
        data['rows'][1]['activation_pass'] = False
        self.assertEqual(adoption.verdict(plan, data)['recommendation'], 'not_worth_using')

    def test_small_budget_cannot_promise_decisive_answer(self):
        plan = prepared(budget='quick')
        result = adoption.verdict(plan, evidence(plan))
        self.assertEqual(result['recommendation'], 'insufficient_evidence')
        self.assertIn('repetitions', result['reason'])

    def test_invalid_or_over_limit_counters(self):
        plan = prepared()
        for key, value, expected in [('input_tokens', -1, 'invalid'), ('input_tokens', float('nan'), 'invalid'),
                                     ('cached_input_tokens', 1000, 'invalid'), ('tool_calls', 999, 'limit_exceeded'),
                                     ('wall_seconds', 301, 'limit_exceeded'), ('input_tokens', 40000, 'limit_exceeded')]:
            data = evidence(plan)
            data['rows'][0][key] = value
            self.assertEqual(adoption.verdict(plan, data)['experiment_status'], expected)

    def test_forged_adequacy_and_changed_analysis_rejected(self):
        for mutate in (lambda p: p['adequacy'].update(adequate=False),
                       lambda p: p['analysis'].update(minimum_repetitions_for_adoption=0),
                       lambda p: p['schedule'].pop()):
            plan = prepared()
            mutate(plan)
            data = evidence(plan)
            self.assertEqual(adoption.verdict(plan, data)['experiment_status'], 'invalid')

    def test_complete_rows_do_not_override_stopped_experiment(self):
        plan = prepared()
        data = evidence(plan)
        data['status'] = 'interrupted'
        self.assertEqual(adoption.verdict(plan, data)['experiment_status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
