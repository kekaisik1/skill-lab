#!/usr/bin/env python3
"""Decision design and analysis. No candidate execution, credentials or network.

The caller must obtain evidence from its trusted adapter verifier. This module
does not authenticate user-authored JSON or certify reviewer judgments.
"""
import hashlib
import json
import math
import statistics


ADAPTERS = ('offline-controls', 'codex-demo')
PRESETS = {
    'quick': {'repetitions': 1, 'meaning': 'Feasibility and gross failures; no repeatability estimate.'},
    'balanced': {'repetitions': 2, 'meaning': 'Descriptive paired comparison; modest coverage, not statistical proof.'},
    'deeper': {'repetitions': 3, 'meaning': 'More repetition of the same work; does not fix unrepresentative tasks.'},
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def proposal_hash(proposal):
    return digest({k: v for k, v in proposal.items() if k != 'review'})


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def pin(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def assess(proposal, adapters=ADAPTERS):
    """Review explicit reasoning and limitations, never equate schema with adequacy."""
    blockers, warnings = [], []

    def issue(code, message, correction, warning=False):
        (warnings if warning else blockers).append(dict(code=code, message=message, correction=correction))

    decision, skill = proposal.get('decision', {}), proposal.get('skill', {})
    software = decision.get('scope') == 'software_control'
    if proposal.get('schema') != 'skill-lab-adoption-proposal/1':
        issue('schema', 'Unsupported proposal format.', 'Use the current proposal format.')
    if not decision.get('intended_work') or not decision.get('improvement') or not decision.get('user_confirmed'):
        issue('decision', 'The adoption question has not been confirmed by the user.',
              'Ask what work and improvement matter; reuse their supplied answers.')
    if decision.get('objective') not in ('quality', 'cost', 'workflow'):
        issue('objective', 'No measurable primary objective selected.', 'Choose quality, cost or an explicit workflow requirement.')
    if decision.get('scope') not in ('tested_work', 'software_control'):
        issue('scope', 'No valid intended scope.', 'Choose tested_work or software_control.')
    for key in ('min_gain', 'max_regression', 'max_token_overhead', 'max_time_overhead'):
        if not number(decision.get(key)) or (key in ('min_gain', 'max_regression') and decision[key] > 1):
            issue('threshold', 'Missing or invalid user threshold: ' + key,
                  'Set finite nonnegative thresholds; score changes use fractions from 0 to 1.')
    if decision.get('objective') == 'workflow' and decision.get('workflow_is_outcome') is not True:
        issue('workflow', 'Following instructions is not an outcome by itself.',
              'Confirm that this workflow is an explicit user requirement, or measure resulting defects.')
    if not skill.get('name') or not skill.get('version') or not pin(skill.get('sha256')):
        issue('skill_pin', 'Selected skill version and exact bytes are not pinned.', 'Reuse inert intake inventory and declared revision.')
    claims = proposal.get('claims', [])
    claim_ids = {c.get('id') for c in claims}
    if not claims or len(claim_ids) != len(claims) or None in claim_ids or not all(c.get('text') for c in claims):
        issue('claims', 'Specific distinct skill claims are missing.', 'Quote the selected skill and identify testable claims.')
    tasks = proposal.get('tasks', [])
    ids = [t.get('id') for t in tasks]
    if not tasks or len(set(ids)) != len(ids) or not all(isinstance(i, str) and i and ':' not in i for i in ids):
        issue('inventory', 'Task IDs are empty, duplicated or unsafe.', 'Use a unique nonempty ID without colons for each case.')
    evaluation = [t for t in tasks if t.get('split') == 'evaluation']
    active = [t for t in evaluation if t.get('activate') is True]
    if not active or not any(t.get('activate') is False for t in evaluation):
        issue('activation', 'Evaluation lacks active or nonactivation cases.', 'Include representative work and a real out-of-scope request.')
    if len(active) < 2:
        issue('independent_tasks', 'Fewer than two independent active tasks cannot cover a work category.',
              'Add an independently selected representative task; repetitions are not new tasks.', software)
    covered = set()
    task_pins = set()
    for task in tasks:
        label = str(task.get('id', '?'))
        if task.get('split') not in ('calibration', 'evaluation') or type(task.get('activate')) is not bool:
            issue('split', label + ': split or activation is unspecified.', 'Declare calibration/evaluation and activation before execution.')
        if not pin(task.get('sha256')) or task.get('sha256') in task_pins:
            issue('case_pin', label + ': missing or duplicated case bytes.', 'Pin disjoint cases; do not relabel the same case as independent.')
        task_pins.add(task.get('sha256'))
        if task.get('adapter') not in adapters:
            issue('adapter', label + ': no implemented executable adapter.',
                  'Author and independently validate a trusted adapter before live execution; a task description is not an adapter.')
        references = task.get('claim_ids', [])
        if not references or any(c not in claim_ids for c in references):
            issue('mapping', label + ': claim mapping is missing or unknown.', 'Connect exact claims to this task and its measurable outcome.')
        if task in evaluation:
            covered.update(references)
        if not task.get('representative_reason') or not task.get('outcome') or not task.get('difficulty'):
            issue('relevance', label + ': relevance, difficulty or observable outcome is unexplained.',
                  'Explain how the task resembles selected work, including ordinary easy work when relevant.')
        grader = task.get('grader', {})
        if not (grader.get('independent') is True and grader.get('control_verified') is True
                and grader.get('coverage') and isinstance(grader.get('limitations'), list)):
            issue('grader', label + ': independent executable grading coverage is unproven.',
                  'Run known-good and known-defect controls and document unmeasured outcomes.')
        if grader.get('measures') == 'workflow' and decision.get('objective') != 'workflow':
            issue('proxy', label + ': grader measures workflow rather than outcome.', 'Measure independent correctness or another user-valued outcome.')
        if task.get('expected_answers_exposed') is not False or task.get('evaluation_seen') is not False:
            issue('contamination', label + ': expected answers exposed or prior evaluation exposure unresolved.',
                  'Separate private grading and freeze fresh evaluation cases before candidate results.')
        provenance = task.get('provenance')
        if not provenance:
            issue('provenance', label + ': source and selection process missing.', 'Document authoring/selection; disjoint does not mean representative.')
        elif provenance == 'public_fixture':
            issue('public_fixture', label + ': public examples are regression fixtures, not unseen work.',
                  'Use these for software controls; select relevant evaluation tasks separately.', software)
        rate = task.get('baseline_rate')
        if rate is not None and (not number(rate) or rate > 1):
            issue('baseline_rate', label + ': invalid prior baseline rate.', 'Use a documented rate from separate development evidence or null.')
        elif rate == 1 and task in active and decision.get('objective') == 'quality':
            issue('ceiling', label + ': prior baseline is already perfect on the measured outcome.',
                  'Retain ordinary work but add relevant outcome coverage with room to improve, or predeclare a cost comparison.', software)
        elif rate == 0 and task in active:
            issue('floor', label + ': prior baseline never succeeds; feasibility is uncertain.',
                  'Use separately budgeted calibration to validate feasibility and grader sensitivity.', software)
        elif rate is None and task in active:
            issue('sensitivity_unknown', label + ': baseline headroom is unknown.',
                  'Review realistic difficulty and optionally approve separate calibration; do not tune evaluation after results.', True)
    uncovered = sorted(claim_ids - covered, key=str)
    if uncovered:
        issue('untested_claims', 'Claims without evaluation coverage: ' + ', '.join(map(str, uncovered)),
              'Remove these from the adoption claim or add relevant independently graded cases.')
    review = proposal.get('review', {})
    if not (review.get('independent') is True and review.get('reviewer')
            and review.get('reviewer') != proposal.get('author')
            and review.get('proposal_sha256') == proposal_hash(proposal)
            and review.get('representativeness') and review.get('discrimination')
            and isinstance(review.get('limitations'), list)):
        issue('independent_review', 'Independent semantic review of this exact proposal is missing or stale.',
              'Have a separate reviewer explain representativeness, discrimination and limitations against the proposal hash.')
    return {'schema': 'skill-lab-adequacy/1', 'proposal_sha256': proposal_hash(proposal),
            'adequate': not blockers, 'adequate_for': 'software_validation' if software else 'scoped_comparison',
            'blockers': blockers, 'warnings': warnings, 'independent_active_tasks': len(active),
            'review_limit': 'Recorded independent reasoning is inspectable, not automatically authenticated or scientifically certified.'}


def budgets(proposal):
    result = {}
    for name, preset in PRESETS.items():
        option = {'name': name, **preset, 'token_limit': 32000, 'wall_seconds': 300, 'tool_limit': 16}
        calibration = 2 * sum(t.get('split') == 'calibration' for t in proposal.get('tasks', []))
        evaluation = 2 * sum(preset['repetitions'] if t.get('activate') else 1
                             for t in proposal.get('tasks', []) if t.get('split') == 'evaluation')
        option.update(calibration_sessions=calibration, evaluation_sessions=evaluation,
                      max_sessions=calibration + evaluation,
                      total_token_threshold=(calibration + evaluation) * option['token_limit'],
                      total_wall_seconds=(calibration + evaluation) * option['wall_seconds'])
        result[name] = option
    return result


def plan(proposal, budget, model_config, *, adapters=ADAPTERS):
    review = assess(proposal, adapters)
    require(review['adequate'], 'Benchmark inadequate: ' + '; '.join(i['message'] for i in review['blockers']))
    if isinstance(budget, str):
        require(budget in PRESETS, 'Unknown budget choice')
        budget = budgets(proposal)[budget]
    require(isinstance(budget, dict), 'Choose a preset or explicit bounded budget')
    offline = (isinstance(model_config, dict) and model_config.get('model') == 'none'
               and all(t['adapter'] == 'offline-controls' for t in proposal['tasks']))
    for field in ('repetitions', 'token_limit', 'wall_seconds', 'tool_limit', 'max_sessions'):
        minimum = 0 if offline and field in ('token_limit', 'tool_limit') else 1
        require(type(budget.get(field)) is int and budget[field] >= minimum,
                'Budget must specify a bounded integer ' + field + ' >= ' + str(minimum))
    nonactivation_limits = budget.get('nonactivation_limits', {key: budget[key] for key in ('token_limit', 'wall_seconds', 'tool_limit')})
    require(isinstance(nonactivation_limits, dict), 'Nonactivation limits must be an explicit object')
    for key in ('token_limit', 'wall_seconds', 'tool_limit'):
        minimum = 0 if offline and key in ('token_limit', 'tool_limit') else 1
        require(type(nonactivation_limits.get(key)) is int and nonactivation_limits[key] >= minimum,
                'Nonactivation limits must specify a bounded integer ' + key + ' >= ' + str(minimum))
    require(budget['repetitions'] <= 100 and budget['max_sessions'] <= 1000, 'Plan exceeds supported finite inventory')
    require(isinstance(model_config, dict) and model_config.get('model') and model_config.get('effort'),
            'Exact model and effort configuration required')
    schedule = []
    for split in ('calibration', 'evaluation'):
        for task_index, task in enumerate(t for t in proposal['tasks'] if t['split'] == split):
            for repetition in range(1 if split == 'calibration' or not task['activate'] else budget['repetitions']):
                order = ('baseline', 'skill') if (repetition + task_index) % 2 == 0 else ('skill', 'baseline')
                for condition in order:
                    schedule.append({'session_id': f'{split}:{task["id"]}:{repetition + 1}:{condition}',
                                     'task_id': task['id'], 'split': split, 'repetition': repetition + 1,
                                     'condition': condition, 'activate': task['activate'],
                                     'limits': ({key: budget[key] for key in ('token_limit', 'wall_seconds', 'tool_limit')}
                                                if task['activate'] else dict(nonactivation_limits))})
    require(len(schedule) <= budget['max_sessions'], 'Selected budget cannot cover the frozen inventory; reduce scope before approval')
    return {'schema': 'skill-lab-adoption-plan/1', 'proposal': proposal, 'proposal_sha256': proposal_hash(proposal),
            'review_sha256': digest(proposal['review']), 'adequacy': review, 'validated_adapters': list(adapters),
            'budget': budget, 'model_config': model_config, 'schedule': schedule,
            'design': 'fixed; all cases and analysis frozen before any calibration or evaluation',
            'calibration_sessions': sum(s['split'] == 'calibration' for s in schedule),
            'evaluation_sessions': sum(s['split'] == 'evaluation' for s in schedule),
            'total_token_threshold': sum(s['limits']['token_limit'] for s in schedule),
            'total_wall_seconds': sum(s['limits']['wall_seconds'] for s in schedule),
            'total_tool_limit': sum(s['limits']['tool_limit'] for s in schedule),
            'separately_billed_api_allowance_usd': 0, 'automatic_retries': 0, 'api_fallback': False,
            'subscription_use': 'All calibration, failed and stopped attempts consume the approved budget; provider quota delta unknown.',
            'limit_caveats': ['Observed token thresholds may overshoot by one response.',
                              'Native transport retries and subscription quota units are not independently measurable.'],
            'stop_on': ['security or pin failure', 'interruption', 'infrastructure failure', 'missing or invalid evidence',
                        'per-session or total limit', 'quota exhaustion'],
            'analysis': {'unit': 'paired task mean across predeclared repetitions; equal task weighting',
                         'minimum_active_tasks_for_adoption': 2, 'minimum_repetitions_for_adoption': 2,
                         'inference': 'descriptive scoped decision; no population or statistical significance claim',
                         'calibration': 'usage only; excluded from effectiveness',
                         'missing': 'never impute, retry, exclude failures or extend collection',
                         'workflow': 'separate unless explicitly selected as primary requirement'}}


def usage(rows):
    result = {'attempts': len(rows), 'input_tokens': 0, 'cached_input_tokens': 0,
              'output_tokens': 0, 'total_tokens': 0, 'wall_seconds': 0, 'tool_calls': 0,
              'missing': {}, 'subscription_units': None, 'api_usd_allowance': 0}
    for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'wall_seconds', 'tool_calls'):
        available = [r[key] for r in rows if number(r.get(key))]
        result[key] = sum(available) if available else None
        result['missing'][key] = len(rows) - len(available)
    tokens = [r['input_tokens'] + r['output_tokens'] for r in rows
              if number(r.get('input_tokens')) and number(r.get('output_tokens'))]
    result['total_tokens'] = sum(tokens) if tokens else None
    result['missing']['total_tokens'] = len(rows) - len(tokens)
    result['accounting'] = 'Cached input is a subset of input; total = input + output. Partial totals are lower bounds.'
    return result


def verdict(prepared, evidence):
    """Analyze adapter-verified normalized receipts, including failed attempts."""
    proposal, schedule = prepared['proposal'], prepared['schedule']
    decision = proposal['decision']
    rows = evidence.get('rows', [])
    rows = rows if isinstance(rows, list) else []
    result = {'schema': 'skill-lab-adoption-verdict/1', 'recommendation': 'insufficient_evidence',
              'experiment_status': 'incomplete', 'reason': '', 'scope': decision['intended_work'],
              'skill': proposal['skill'], 'model_config': prepared['model_config'],
              'classification': evidence.get('classification', 'unknown'),
              'strength': 'No population inference; limited to frozen work, version and configuration.',
              'usage': usage(rows), 'usage_by_split': {}, 'pairs': [], 'correctness': {},
              'activation': {}, 'workflow': {}, 'resources': {}, 'security': evidence.get('security', 'unverified'),
              'limitations': list(proposal.get('review', {}).get('limitations', [])), 'next_experiment': ''}

    def finish(status, reason, next_step, recommendation='insufficient_evidence'):
        result.update(experiment_status=status, recommendation=recommendation, reason=reason, next_experiment=next_step)
        if (status in ('valid', 'valid_limited') and
                (evidence.get('classification') != 'real_model' or decision.get('scope') == 'software_control')):
            result['control_rule_result'] = {'recommendation': recommendation, 'reason': reason, 'status': status}
            result.update(experiment_status='valid_software_control', recommendation='insufficient_evidence',
                          reason='Authored controls exercise the decision rules; they do not establish skill adoption value.',
                          next_experiment='Select representative work and independently review a separately approved real-model comparison.')
        return result

    if evidence.get('security') == 'fail':
        return finish('security_failure', 'Containment failed; this is not a negative effectiveness result.',
                      'Repair and independently revalidate the boundary with fake-secret controls before any new approval.')
    if not evidence.get('verified') or evidence.get('plan_sha256') != digest(prepared):
        return finish('invalid', 'Evidence is unverified or plan binding differs.',
                      'Reverify the preserved receipts and exact plan; never reconstruct missing success.')
    if prepared.get('proposal_sha256') != proposal_hash(proposal) or prepared.get('review_sha256') != digest(proposal['review']):
        return finish('invalid', 'The frozen proposal or independent review changed.', 'Preserve this invalid run; prepare a separately reviewed plan.')
    actual_adequacy = assess(proposal, prepared.get('validated_adapters', ADAPTERS))
    if not actual_adequacy['adequate'] or prepared.get('adequacy') != actual_adequacy:
        return finish('invalid', 'Recomputed benchmark adequacy failed or differs from the frozen assessment.',
                      'Correct the specific design blockers and obtain a fresh independent review before new spending.')
    try:
        rebuilt = plan(proposal, prepared['budget'], prepared['model_config'],
                       adapters=prepared.get('validated_adapters', ADAPTERS))
    except (KeyError, ValueError, TypeError) as error:
        return finish('invalid', 'Frozen plan cannot be reconstructed: ' + str(error), 'Review the preserved plan without changing consumed evidence.')
    if any(prepared.get(key) != rebuilt[key] for key in ('schedule', 'analysis', 'calibration_sessions', 'evaluation_sessions',
                                                       'total_token_threshold', 'total_wall_seconds', 'total_tool_limit')):
        return finish('invalid', 'Frozen schedule, analysis or total limits differ from the predeclared design.',
                      'Do not select receipts after results; audit the frozen inventory.')
    identifiers = [r.get('session_id') for r in rows]
    planned = {s['session_id']: s for s in schedule}
    if len(set(identifiers)) != len(identifiers) or any(i not in planned for i in identifiers):
        return finish('invalid', 'Duplicate or unscheduled receipt; attempts cannot be selected after results.', 'Audit receipts against the frozen inventory.')
    for row in rows:
        for key in ('input_tokens', 'output_tokens', 'cached_input_tokens', 'wall_seconds', 'tool_calls'):
            if row.get(key) is not None and not number(row[key]):
                return finish('invalid', 'Invalid usage counter: ' + key, 'Reparse original native counters.')
        if (number(row.get('cached_input_tokens')) and number(row.get('input_tokens'))
                and row['cached_input_tokens'] > row['input_tokens']):
            return finish('invalid', 'Cached input exceeds total input.', 'Reparse original native counters without double counting.')
        if row.get('status') == 'skipped' or row.get('tampered') or row.get('skipped_checks', 0):
            return finish('invalid', 'Skipped or tampered evidence cannot support adoption.', 'Preserve and investigate the missing or altered grading evidence.')
    for split in ('calibration', 'evaluation'):
        result['usage_by_split'][split] = usage([r for r in rows if planned[r['session_id']]['split'] == split])
    for row in rows:
        limits = planned[row['session_id']]['limits']
        counters = usage([row])
        for metric, limit in (('total_tokens', 'token_limit'), ('wall_seconds', 'wall_seconds'), ('tool_calls', 'tool_limit')):
            if number(counters[metric]) and counters[metric] > limits[limit]:
                return finish('limit_exceeded', 'An observed session limit was exceeded; retain usage including any one-response overshoot.',
                              'Do not continue or raise limits automatically. Review the preserved attempt before a separately approved plan.')
    bad = [r for r in rows if r.get('status') != 'completed']
    if bad or len(rows) != len(schedule) or evidence.get('status') != 'completed':
        statuses = sorted({str(r.get('status')) for r in bad})
        if evidence.get('status') != 'completed':
            statuses.append('experiment:' + str(evidence.get('status', 'missing')))
        status = 'infrastructure_failure' if 'infrastructure_error' in statuses or evidence.get('status') == 'infrastructure_error' else 'incomplete'
        detail = (' Recorded stop: ' + str(evidence['failure'])) if evidence.get('failure') else ''
        return finish(status, f'{len(rows)}/{len(schedule)} session receipts; stopped/failed statuses: {statuses or "missing receipts"}. Usage includes every available attempt.' + detail,
                      'Resolve the stated stop and request a new fixed plan only if still useful; consumed attempts are never retried.')
    if evidence.get('security') != 'pass':
        return finish('invalid', 'Completed execution lacks verified containment evidence.',
                      'Reverify the preserved boundary receipts; missing controls cannot support adoption.')
    indexed = {r['session_id']: r for r in rows}
    evaluation = [s for s in schedule if s['split'] == 'evaluation']
    active = [s for s in evaluation if s['activate']]
    for s in evaluation:
        row = indexed[s['session_id']]
        if not number(row.get('score')) or row['score'] > 1 or type(row.get('activation_pass')) is not bool:
            return finish('invalid', 'Independent score or activation evidence is missing.', 'Reverify executable grading; do not substitute model claims.')
        if decision['objective'] == 'workflow' and type(row.get('workflow_pass')) is not bool:
            return finish('invalid', 'Explicit workflow outcome is missing.', 'Obtain independent workflow receipts.')
        if 'meaningful_regression' in row and type(row['meaningful_regression']) is not bool:
            return finish('invalid', 'Malformed independent regression evidence.', 'Reverify artifact preservation against the frozen task contract.')
    paired_gains = []
    for task_id in sorted({s['task_id'] for s in active}):
        arm = {}
        for condition in ('baseline', 'skill'):
            selected = [indexed[s['session_id']] for s in active if s['task_id'] == task_id and s['condition'] == condition]
            arm[condition] = statistics.mean(r['score'] for r in selected)
        result['pairs'].append({'task_id': task_id, **arm, 'gain': arm['skill'] - arm['baseline']})
        for repetition in sorted({s['repetition'] for s in active if s['task_id'] == task_id}):
            pair = {s['condition']: indexed[s['session_id']]['score'] for s in active
                    if s['task_id'] == task_id and s['repetition'] == repetition}
            paired_gains.append(pair['skill'] - pair['baseline'])
    require(result['pairs'], 'Plan has no active evaluation pair')
    gains = [p['gain'] for p in result['pairs']]
    result['correctness'] = {'mean_task_gain': statistics.mean(gains), 'worst_task_gain': min(gains),
                             'worst_pair_gain': min(paired_gains),
                             'all_correct': all(p['baseline'] == p['skill'] == 1 for p in result['pairs'])}
    result['correctness']['meaningful_regressions'] = {
        condition: sum(indexed[s['session_id']].get('meaningful_regression') is True for s in active
                       if s['condition'] == condition) for condition in ('baseline', 'skill')}
    for kind, predicate in (('activation', lambda s: s['activate']), ('nonactivation', lambda s: not s['activate'])):
        selected = [indexed[s['session_id']] for s in evaluation if predicate(s)]
        result['activation'][kind] = {'passed': sum(r['activation_pass'] for r in selected), 'total': len(selected)}
        for condition in ('baseline', 'skill'):
            selected = [indexed[s['session_id']] for s in evaluation if predicate(s) and s['condition'] == condition]
            result['activation'][kind][condition] = {'passed': sum(r['activation_pass'] for r in selected), 'total': len(selected)}
    workflow = {}
    for condition in ('baseline', 'skill'):
        selected = [indexed[s['session_id']] for s in active if s['condition'] == condition]
        workflow[condition] = {'passed': sum(r.get('workflow_pass') is True for r in selected),
                               'observed': sum(type(r.get('workflow_pass')) is bool for r in selected), 'total': len(selected)}
    result['workflow'] = workflow
    result['workflow']['interpretation'] = 'Additional workflow does not establish defect prevention or better outcomes.'
    resource_missing = False
    for metric in ('total_tokens', 'wall_seconds'):
        arms = {condition: usage([indexed[s['session_id']] for s in active if s['condition'] == condition]) for condition in ('baseline', 'skill')}
        baseline, skill = arms['baseline'][metric], arms['skill'][metric]
        missing = any(a['missing'][metric] for a in arms.values())
        overhead = (skill - baseline) / baseline if not missing and baseline and skill is not None else None
        resource_missing |= overhead is None
        result['resources'][metric] = {'baseline': baseline, 'skill': skill, 'overhead': overhead, 'missing': missing}
    if result['correctness']['all_correct']:
        result['limitations'].append('Correctness ceiling: all measured cases passed in both arms; no room to observe quality improvement. Cost comparison remains descriptive.')
    if not prepared['adequacy']['adequate']:
        return finish('invalid', 'Benchmark adequacy failed.', 'Correct the specific design blockers before new spending.')
    if any(v['skill']['passed'] != v['skill']['total'] for v in result['activation'].values()):
        return finish('valid', 'Activation or nonactivation requirements failed on tested cases.',
                      'Inspect the failed cases before considering a revised skill.', 'not_worth_using')
    if min(paired_gains) < -decision['max_regression'] or result['correctness']['meaningful_regressions']['skill']:
        return finish('valid', 'Measured correctness regression in at least one matched pair exceeds the accepted tolerance.',
                      'Inspect the regressed task; do not adopt this version for this work.', 'not_worth_using')
    if len(result['pairs']) < 2 or prepared['budget']['repetitions'] < 2:
        return finish('valid_limited', 'Too few independent tasks or repetitions for the predeclared adoption rule.',
                      'A new fixed comparison needs at least two representative tasks and two repetitions; this threshold is not a power guarantee.')
    if resource_missing:
        return finish('valid_limited', 'Resource coverage is incomplete, so accepted overhead cannot be assessed.',
                      'Repair resource collection before a separately approved comparison; missing counters are not zero.')
    overhead_ok = (result['resources']['total_tokens']['overhead'] <= decision['max_token_overhead']
                   and result['resources']['wall_seconds']['overhead'] <= decision['max_time_overhead'])
    if not overhead_ok:
        return finish('valid', 'Measured resource overhead exceeds the user-selected allowance.',
                      'Use baseline for these conditions, or explicitly reconsider the tradeoff in a new decision.', 'not_worth_measured_overhead')
    objective = decision['objective']
    improvement = statistics.mean(gains)
    if objective == 'cost':
        improvement = -result['resources']['total_tokens']['overhead']
    elif objective == 'workflow':
        improvement = workflow['skill']['passed'] / workflow['skill']['total'] - workflow['baseline']['passed'] / workflow['baseline']['total']
    if improvement >= decision['min_gain'] and improvement > 0:
        return finish('valid', 'The predeclared improvement threshold was met within regression and resource allowances.',
                      'Use only for the tested task conditions; expand coverage separately if your work changes.', 'worth_using')
    return finish('valid_limited', 'No measured improvement reached the adoption threshold.' +
                  (' Correctness was at ceiling.' if result['correctness']['all_correct'] else ''),
                  'Review unmeasured user-valued outcomes; if relevant, predeclare a new independent task sample or cost objective. More tools alone will not answer this question.')


def render(report):
    labels = {'worth_using': 'Worth using for the tested work', 'not_worth_using': 'Not worth using for the tested work',
              'not_worth_measured_overhead': 'Not worth the measured overhead for the tested work',
              'insufficient_evidence': 'Insufficient evidence for adoption'}
    lines = [labels[report['recommendation']] + '.', '', report['reason'], '',
             'Scope: ' + report['scope'], 'Evidence: ' + report['classification'] + '; ' + report['experiment_status'],
             'Skill/version: ' + report['skill']['name'] + ' / ' + report['skill']['version'],
             'Strength: ' + report['strength'], '', 'Correctness: ' + json.dumps(report['correctness'], sort_keys=True),
             'Activation/nonactivation: ' + json.dumps(report['activation'], sort_keys=True),
             'Workflow: ' + json.dumps(report['workflow'], sort_keys=True),
             'Resources: ' + json.dumps(report['resources'], sort_keys=True),
             'All-attempt usage: ' + json.dumps(report['usage'], sort_keys=True),
             'Containment: ' + report['security'] + ' (finite controls, not universal safety)', '',
             *['Limitation: ' + str(item) for item in report['limitations']], '',
             'Smallest useful next step: ' + report['next_experiment']]
    return '\n'.join(lines) + '\n'
