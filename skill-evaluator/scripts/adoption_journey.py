#!/usr/bin/env python3
"""Installed adoption journey. Authoring is inert; only registered adapters execute."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time

import adoption
import evaluate
import preview_intake


def read(path):
    try:
        return json.loads(preview_intake.selected_file(path))
    except (OSError, ValueError) as error:
        raise ValueError(Path(path).name + ': ' + str(error)) from error


def write(path, value):
    path = preview_intake.no_links(path)
    with path.open('xb') as stream:
        stream.write(evaluate.encode(value))


def example_proposal():
    import fixture_runner as fixture
    task = fixture.task_for('positive')
    value = {
        'schema': 'skill-lab-adoption-proposal/1', 'author': 'bundled control author',
        'decision': {'intended_work': 'Validate adoption decision software using public regression controls',
            'improvement': 'Detect known behavioral improvement independently of workflow compliance',
            'objective': 'quality', 'scope': 'software_control', 'user_confirmed': True,
            'min_gain': .1, 'max_regression': 0, 'max_token_overhead': .5,
            'max_time_overhead': .5, 'workflow_is_outcome': False},
        'skill': {'name': 'original normalization fixture', 'version': 'local-content-pin',
                  'sha256': fixture.sha(fixture.SKILL.read_bytes())},
        'claims': [{'id': 'normalize', 'text': 'Trim surrounding whitespace and lowercase labels.'}],
        'tasks': [],
    }
    for name, active in [('normalization', True), ('scope-routing', True), ('nonactivation', False)]:
        value['tasks'].append({'id': name, 'split': 'evaluation', 'activate': active,
            'adapter': 'offline-controls', 'claim_ids': ['normalize'],
            'sha256': adoption.digest({'task': task if name == 'normalization' else fixture.task_for('activation-good'), 'selected_check': None if name == 'normalization' else 0 if active else 1}),
            'representative_reason': 'Authored software control only, not a sample of user work',
            'outcome': 'Exact independent JSON behavioral checks',
            'grader': {'independent': True, 'coverage': 'Whitespace, case and empty input; router scope',
                       'limitations': ['Public small fixture, no real-work representativeness'], 'control_verified': True},
            'difficulty': 'easy', 'provenance': 'public_fixture', 'evaluation_seen': False,
            'expected_answers_exposed': False, 'baseline_rate': None})
    value['review'] = {'reviewer': 'bundled control review', 'independent': True,
        'representativeness': 'Software control only; no efficacy inference',
        'discrimination': 'Known missing-trim defect changes independent behavioral score',
        'limitations': ['Not a real model study or unseen holdout'],
        'proposal_sha256': adoption.proposal_hash(value)}
    return value


def example(out):
    out = preview_intake.no_links(out)
    out.mkdir(parents=True, exist_ok=False)
    value = example_proposal()
    write(out / 'proposal.json', value)
    return {'proposal': str(out / 'proposal.json'), 'review': adoption.assess(value), 'model_calls': 0}


def create_plan(proposal, budget, scenario='improvement'):
    import fixture_runner as fixture
    import offline_acceptance
    if adoption.proposal_hash(proposal) != adoption.proposal_hash(example_proposal()):
        raise ValueError('Authoring only: no registered execution for these exact task and skill pins')
    if scenario not in ('improvement', 'regression', 'equal', 'overhead', 'workflow'):
        raise ValueError('Unsupported authored control scenario')
    selected = copy.deepcopy(adoption.budgets(proposal)[budget] if isinstance(budget, str) else budget)
    process_count = sum(2 + (0 if name == 'forged' else len(fixture.task_for(name)['checks']))
                        for name in offline_acceptance.EXPECTED)
    selected.update(token_limit=0, tool_limit=0, wall_seconds=process_count * fixture.LIMITS['wall_seconds'])
    value = adoption.plan(proposal, selected, {'model': 'none', 'effort': 'none',
                          'configuration': 'fresh isolated authored fixture suite for every scheduled session'})
    value.update(offline_allowance={'model_tokens': 0, 'model_tools': 0,
        'subscription_use': 'None: authored programs only; no authentication or model requests.',
        'wall_scope': 'Sum of bounded fixture-process thresholds; trusted setup/report overhead excluded.'},
        fixture_limits=fixture.LIMITS, fixture_processes_per_session=process_count,
        fixture_suite=offline_acceptance.plan(), control_scenario=scenario,
        journey_sha256=evaluate.digest(Path(__file__).read_bytes()),
        decision_engine_sha256=evaluate.digest(Path(adoption.__file__).read_bytes()),
        registered_proposal_sha256=adoption.proposal_hash(example_proposal()))
    return value


def session_directory(out, index):
    return out / ('session-%04d' % index)


def receipt_inventory(directory):
    return {str(p.relative_to(directory)): evaluate.digest(p.read_bytes())
            for p in sorted(directory.rglob('receipt.json')) if p.is_file() and not p.is_symlink()}


def run(plan_path, out):
    import offline_acceptance
    frozen = read(plan_path)
    expected = create_plan(example_proposal(), frozen['budget'], frozen['control_scenario'])
    if frozen != expected:
        raise ValueError('Changed plan or adapter pins; prepare a new reviewed plan')
    out = preview_intake.no_links(out)
    out.mkdir(parents=True, exist_ok=False)
    write(out / 'adoption-plan.json', frozen)
    execution = {'status': 'started', 'model_calls': 0, 'attempts': []}
    write(out / 'execution.json', execution)
    for index, session in enumerate(frozen['schedule']):
        directory = session_directory(out, index)
        attempt = {'session_id': session['session_id'], 'status': 'started'}
        execution['attempts'].append(attempt)
        (out / 'execution.json').write_bytes(evaluate.encode(execution))
        try:
            result = offline_acceptance.run(directory)
            attempt['status'] = 'completed' if result['status'] == 'pass' else 'infrastructure_error'
            if attempt['status'] == 'completed':
                receipts = [read(path) for path in directory.rglob('receipt.json')]
                observed_wall = sum(p['wall_seconds'] for r in receipts for p in r.get('processes', []))
                if observed_wall > session['limits']['wall_seconds']:
                    attempt['status'] = 'limit_exceeded'
        except KeyboardInterrupt:
            attempt['status'] = 'interrupted'
        except (OSError, ValueError) as error:
            attempt.update(status='infrastructure_error', error=str(error))
        finally:
            attempt['receipt_hashes'] = receipt_inventory(directory)
            (out / 'execution.json').write_bytes(evaluate.encode(execution))
        if attempt['status'] != 'completed':
            break
    execution['status'] = 'completed' if (len(execution['attempts']) == len(frozen['schedule'])
        and all(a['status'] == 'completed' for a in execution['attempts'])) else execution['attempts'][-1]['status']
    (out / 'execution.json').write_bytes(evaluate.encode(execution))
    result = report(out)
    write(out / 'decision.json', result)
    (out / 'REPORT.md').write_text(adoption.render(result) + '\n' + result['control_notice'] + '\n')
    return result


def evidence(out, frozen):
    import offline_acceptance
    result = {'verified': False, 'security': 'unverified', 'status': 'incomplete',
        'classification': 'authored_control', 'plan_sha256': adoption.digest(frozen), 'rows': []}
    try:
        if frozen != create_plan(example_proposal(), frozen['budget'], frozen['control_scenario']):
            raise ValueError('Changed adapter plan')
        execution = read(out / 'execution.json')
        if len(execution['attempts']) > len(frozen['schedule']):
            raise ValueError('Extra execution attempts')
        for index, attempt in enumerate(execution['attempts']):
            session = frozen['schedule'][index]
            if attempt['session_id'] != session['session_id']:
                raise ValueError('Changed execution ordering')
            directory = session_directory(out, index)
            if attempt.get('receipt_hashes') != receipt_inventory(directory):
                raise ValueError('Missing or changed attempt receipts')
            receipts = [read(directory / name) for name in attempt['receipt_hashes']]
            processes = [p for r in receipts for p in r.get('processes', [])]
            if not all(adoption.number(p.get('wall_seconds')) for p in processes):
                raise ValueError('Missing actual process duration')
            row = {'session_id': session['session_id'], 'status': attempt['status'],
                   'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0,
                   'wall_seconds': sum(p['wall_seconds'] for p in processes), 'tool_calls': 0,
                   'fixture_processes': len(processes), 'receipt_hashes': attempt['receipt_hashes']}
            result['rows'].append(row)
            if attempt['status'] != 'completed':
                continue
            verified = offline_acceptance.verify(directory)
            if verified != read(directory / 'report.json'):
                raise ValueError('Changed saved suite report')
            scenario, condition = frozen['control_scenario'], session['condition']
            defective = ((scenario == 'improvement' and condition == 'baseline') or
                         (scenario == 'regression' and condition == 'skill'))
            task_id = session['task_id']
            name = ('negative' if defective else 'positive') if task_id == 'normalization' else (
                   'activation-never' if defective and session['activate'] else 'activation-good')
            scored = read(directory / name / 'receipt.json')
            score = scored['passed'] / scored['total'] if task_id == 'normalization' else float(scored['cases'][0 if session['activate'] else 1]['passed'])
            row.update(score=score, activation_pass=True, workflow_pass=None,
                       selected_receipt_sha256=evaluate.digest((directory / name / 'receipt.json').read_bytes()))
        result.update(verified=True, security='pass' if any(r['status'] == 'completed' for r in result['rows']) else 'unverified',
                      status=execution['status'])
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['reason'] = str(error)
    return result


def report(out):
    out = preview_intake.no_links(out)
    frozen = read(out / 'adoption-plan.json')
    observed = evidence(out, frozen)
    result = adoption.verdict(frozen, observed)
    if observed.get('reason'):
        result['reason'] += ' Verification detail: ' + observed['reason']
    result['control_notice'] = ('Software evidence only: behavioral scores use actual isolated fixture receipts. '
        'Every scheduled row executes a fresh fixture suite; tokens/tools are zero and time sums observed fixture processes. ')
    return result


def exercise(out):
    import shutil
    out = preview_intake.no_links(out)
    out.mkdir(parents=True, exist_ok=False)
    rows = []
    expected = {'improvement': 'worth_using', 'regression': 'not_worth_using',
                'equal': 'insufficient_evidence', 'overhead': 'not_worth_measured_overhead',
                'workflow': 'insufficient_evidence'}
    for scenario in expected:
        plan_path = out / (scenario + '-plan.json')
        write(plan_path, create_plan(example_proposal(), 'balanced', scenario))
        value = run(plan_path, out / scenario)
        frozen = read(plan_path)
        observations = evidence(out / scenario, frozen)
        if not observations['verified'] or len(observations['rows']) != len(frozen['schedule']):
            raise ValueError('Actual scenario execution failed: ' + scenario)
        # Separate deterministic telemetry exercises thresholds without pretending
        # offline process durations are subscription usage. Outcome scores remain
        # independently verified actual executions, never fabricated success.
        hypothetical = copy.deepcopy(observations)
        analysis_budget = dict(frozen['budget'], token_limit=1000, wall_seconds=60, tool_limit=10)
        analysis_plan = adoption.plan(frozen['proposal'], analysis_budget, frozen['model_config'])
        for row, session in zip(hypothetical['rows'], frozen['schedule']):
            skill = session['condition'] == 'skill'
            row.update(input_tokens=160 if skill and scenario == 'overhead' else 110 if skill else 100,
                       output_tokens=10, cached_input_tokens=50, wall_seconds=1.1 if skill else 1,
                       tool_calls=3 if skill else 1, workflow_pass=skill)
        hypothetical['plan_sha256'] = adoption.digest(analysis_plan)
        analysis = adoption.verdict(analysis_plan, hypothetical)
        rule = analysis.get('control_rule_result', {}).get('recommendation')
        if rule != expected[scenario]:
            raise ValueError('Incorrect control rule for ' + scenario + ': ' + str(rule))
        write(out / scenario / 'authored-resource-analysis.json', {'classification': 'authored_telemetry_control',
              'notice': 'Hypothetical counters only; not observed model use. Actual process usage is in decision.json.',
              'plan': analysis_plan, 'evidence': hypothetical, 'result': analysis})
        rows.append({'scenario': scenario, 'decision': value, 'authored_resource_rule': rule})
    for fault in ['missing', 'skipped', 'tampered', 'interrupted', 'infrastructure']:
        destination = out / fault
        shutil.copytree(out / 'equal', destination)
        receipt = destination / 'session-0000/positive/receipt.json'
        value = read(receipt)
        if fault == 'missing':
            (destination / 'session-0000/positive/solution.py').unlink()
        elif fault == 'skipped':
            value['cases'] = value['cases'][:-1]
        elif fault == 'tampered':
            value['passed'] = 999
        elif fault == 'interrupted':
            shutil.rmtree(destination / 'session-0000/activation-good')
        else:
            value['processes'][0]['status'] = 'infrastructure_error'
        receipt.write_bytes(evaluate.encode(value))
        decision = report(destination)
        rows.append({'scenario': fault, 'decision': decision, 'fault_classification': 'injected evidence corruption'})
        if evidence(destination, read(destination / 'adoption-plan.json'))['verified']:
            raise ValueError('Fault accepted: ' + fault)
    bad = example_proposal()
    bad['decision']['scope'] = 'tested_work'
    bad['tasks'][0]['baseline_rate'] = 1.0
    bad['review']['proposal_sha256'] = adoption.proposal_hash(bad)
    inadequate = adoption.assess(bad)
    if inadequate['adequate']:
        raise ValueError('Ceiling/public smoke incorrectly adequate for adoption')
    result = {'schema': 'skill-lab-adoption-exercise/1', 'status': 'pass',
              'classification': 'authored_control', 'model_calls': 0,
              'adequate_control': adoption.assess(example_proposal()), 'inadequate_control': inadequate,
              'scenarios': rows}
    write(out / 'exercise.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ['example', 'exercise']:
        sub.add_parser(name).add_argument('--out', required=True)
    for name in ['review', 'budgets', 'plan']:
        p = sub.add_parser(name)
        p.add_argument('--proposal', required=True)
        if name == 'plan':
            p.add_argument('--budget', choices=['quick', 'balanced', 'deeper'], required=True)
            p.add_argument('--out', required=True)
    p = sub.add_parser('run')
    p.add_argument('--plan', required=True)
    p.add_argument('--out', required=True)
    sub.add_parser('report').add_argument('directory')
    args = parser.parse_args(argv)
    try:
        if args.command in {'example', 'exercise'}:
            result = (example if args.command == 'example' else exercise)(args.out)
        elif args.command == 'run':
            result = run(args.plan, args.out)
        elif args.command == 'report':
            result = report(args.directory)
            print(adoption.render(result))
            print(result.get('control_notice', ''))
            return 0 if evidence(Path(args.directory), read(Path(args.directory) / 'adoption-plan.json'))['verified'] else 2
        else:
            proposal = read(args.proposal)
            result = (adoption.assess(proposal) if args.command == 'review' else
                      adoption.budgets(proposal) if args.command == 'budgets' else create_plan(proposal, args.budget))
            if args.command == 'plan':
                write(args.out, result)
        print(json.dumps(result, indent=2))
        if args.command == 'run':
            return 0 if result['experiment_status'] == 'valid_software_control' else 2
        if args.command == 'review':
            return 0 if result['adequate'] else 2
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Adoption journey unavailable: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
