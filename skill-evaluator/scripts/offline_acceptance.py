#!/usr/bin/env python3
"""Fresh, fail-closed acceptance journey for bundled authored fixtures; no model calls."""
import argparse
import json
from pathlib import Path
import sys

import fixture_runner as runner

# Fixed scope: passing, behavioral failures, missing artifact and isolation control.
EXPECTED = {
    'positive': ('valid', 'pass'),
    'negative': ('valid', 'fail'),
    'activation-good': ('valid', 'pass'),
    'activation-always': ('valid', 'fail'),
    'activation-never': ('valid', 'fail'),
    'forged': ('invalid_artifact', 'invalid'),
    'adversarial': ('valid', 'pass'),
}


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_hash(path):
    require(path.is_file() and not path.is_symlink(), f'Missing or unsafe artifact: {path}')
    return runner.sha(path.read_bytes())


def plan():
    return {
        'schema': 1,
        'evidence_class': 'authored_offline_fixtures',
        'model_calls': 0,
        'spend_usd': 0,
        'skill_sha256': file_hash(runner.SKILL),
        'runner_sha256': file_hash(Path(runner.__file__)),
        'acceptance_sha256': file_hash(Path(__file__)),
        'fixtures': [
            {'fixture': name, 'expected_status': status, 'expected_grade': grade,
             'task_sha256': runner.sha(runner.encode(runner.task_for(name))),
             'candidate_sha256': runner.sha(runner.CANDIDATES[name].encode())}
            for name, (status, grade) in EXPECTED.items()
        ],
    }


def verify(out):
    """Recompute grades from recorded actual stdout, never from reported scores alone."""
    out = Path(out)
    frozen = read(out / 'plan.json')
    require(frozen == plan(), 'Plan, task, skill or evaluator version changed')
    rows = []
    for item in frozen['fixtures']:
        name = item['fixture']
        directory = out / name
        receipt_path = directory / 'receipt.json'
        receipt_hash = file_hash(receipt_path)
        receipt = read(receipt_path)
        require(receipt.get('synthetic') is True, f'{name}: missing fixture classification')
        require(receipt.get('fixture') == name, f'{name}: wrong fixture identity')
        for key in ['skill_sha256', 'runner_sha256']:
            require(receipt.get(key) == frozen[key], f'{name}: wrong {key}')
        for key in ['task_sha256', 'candidate_sha256']:
            require(receipt.get(key) == item[key], f'{name}: wrong {key}')
        require(file_hash(directory / 'SKILL.md') == frozen['skill_sha256'], f'{name}: changed skill')
        require(file_hash(directory / 'task.json') == item['task_sha256'], f'{name}: changed task')
        require(file_hash(directory / 'candidate.py') == receipt.get('executed_candidate_sha256'),
                f'{name}: changed executed source')
        task = read(directory / 'task.json')
        require(receipt.get('task_id') == task['id'], f'{name}: wrong task id')
        checks = task['checks']
        require(checks and len({c['id'] for c in checks}) == len(checks), f'{name}: empty/duplicate checks')
        require(receipt.get('oracle_sha256') == runner.sha(runner.encode(checks)), f'{name}: wrong oracle')
        require((receipt.get('status'), receipt.get('grade')) == EXPECTED[name],
                f'{name}: unexpected status/grade: {receipt.get("status")}/{receipt.get("grade")}')
        processes = receipt.get('processes', [])
        expected_phases = ['isolation_preflight', 'candidate']
        if name != 'forged':
            expected_phases += ['grading_candidate_execution'] * len(checks)
        require([p.get('phase') for p in processes] == expected_phases, f'{name}: missing/extra executions')
        require(all(p.get('status') == 'completed' and type(p.get('exit_code')) is int
                    and p['exit_code'] == 0 for p in processes), f'{name}: execution did not complete')
        require(processes[0]['stdout'].strip() == 'READY', f'{name}: preflight did not run')
        if name == 'forged':
            require(not (directory / 'solution.py').exists() and not receipt.get('cases'),
                    'forged: unexpected artifact or grades')
            rows.append({'fixture': name, 'receipt_sha256': receipt_hash, 'status': 'expected_rejection'})
            continue
        require(file_hash(directory / 'solution.py') == receipt.get('artifact_sha256'), f'{name}: changed output')
        cases = receipt.get('cases', [])
        require([c.get('id') for c in cases] == [c['id'] for c in checks], f'{name}: skipped/duplicate cases')
        passes = []
        for check, case, process in zip(checks, cases, processes[2:]):
            require(process.get('case_id') == check['id'], f'{name}: wrong execution case')
            actual = json.loads(process['stdout'])
            passed = runner.same_answer(actual, check['expected'])
            require(runner.same_answer(case.get('actual'), actual), f'{name}: changed actual answer')
            require(case.get('stdout_sha256') == runner.sha(process['stdout'].encode()), f'{name}: changed stdout')
            require(case.get('passed') is passed, f'{name}: forged case verdict')
            passes.append(passed)
        require(type(receipt.get('total')) is int and receipt['total'] == len(checks), f'{name}: wrong total')
        require(type(receipt.get('passed')) is int and receipt['passed'] == sum(passes), f'{name}: wrong pass count')
        require(receipt['grade'] == ('pass' if all(passes) else 'fail'), f'{name}: forged grade')
        if name == 'adversarial':
            probes = receipt.get('isolation_probes', {})
            require(set(probes) == {'holdouts', 'home', 'project', 'source_write', 'network'}
                    and set(probes.values()) == {'blocked'}, 'Isolation control failed')
        rows.append({'fixture': name, 'receipt_sha256': receipt_hash,
                     'status': receipt['status'], 'grade': receipt['grade'],
                     'task_id': task['id'], 'artifact_sha256': receipt['artifact_sha256'],
                     'passed': sum(passes), 'total': len(checks)})
    require(len(rows) == len(EXPECTED) > 0, 'Incomplete acceptance journey')
    return {'status': 'pass', 'evidence_class': 'authored_offline_fixtures',
            'model_calls': 0, 'spend_usd': 0,
            'plan_sha256': file_hash(out / 'plan.json'), 'results': rows,
            'limitations': 'Public regression cases; no real model or unseen-case performance evidence. '
                           'Hashes detect drift, not malicious controller-side forgery.'}


def run(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    runner.dump(out / 'plan.json', plan())
    # A crash leaves a nonpassing report, never a stale success from an older run.
    runner.dump(out / 'report.json', {'status': 'incomplete'})
    try:
        for name in EXPECTED:
            result = runner.run(name, out / name)
            require((result.get('status'), result.get('grade')) == EXPECTED[name],
                    f'{name}: unexpected outcome; inspect {out / name / "receipt.json"}')
        report = verify(out)
    except (OSError, ValueError, KeyError, TypeError) as error:
        report = {'status': 'fail', 'evidence_class': 'authored_offline_fixtures',
                  'model_calls': 0, 'error': str(error)}
    runner.dump(out / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'verify'])
    parser.add_argument('--out', required=True, help='New output directory for run; existing for verify')
    args = parser.parse_args()
    try:
        report = run(args.out) if args.command == 'run' else verify(args.out)
        if args.command == 'verify':
            require(read(Path(args.out) / 'report.json') == report, 'Saved report differs from evidence')
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0 if report['status'] == 'pass' else 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'Acceptance failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
