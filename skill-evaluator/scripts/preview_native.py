#!/usr/bin/env python3
"""Small original native Codex adapter; approval-bound, no API fallback or retries."""
import json
from pathlib import Path
import shutil

import codex_host
import fixture_runner as fixture
import offline_acceptance
import preview_environment as environment

ROOT = Path(__file__).resolve().parents[2]
SCHEDULE = [('baseline', False, False), ('skill', True, False),
            ('readonly-skill', True, True), ('readonly-baseline', False, True)]
TASK = ('Write /work/solution.py using only skill_lab tools. Read one JSON string from stdin, '
        'trim surrounding whitespace and lowercase it, then print the resulting JSON string. '
        'No packages, network, git or quality companions are needed. A separate controller grades your artifact.')
READONLY = ('Explanation only. Do not use tools or write files. Return only this JSON object: '
            '{"operation":"display_verbatim","output":" Alice "}. Preserve whitespace and case.')
SYSTEM = ('Use only the supplied skill_lab tools inside /work. Native tools, network and package installs '
          'are unavailable. Do not access credentials or private references. No quality companion tools '
          'are installed. For explanation-only tasks do not use any tools. Do not claim grades.')


def save(path, value):
    Path(path).write_bytes(fixture.encode(value))


def required(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    path = Path(path)
    required(path.is_file() and not path.is_symlink(), 'Missing or unsafe regular evidence file')
    return fixture.sha(path.read_bytes())


def plan(state, instructions=(), disabled_mcp=()):
    config = environment.load(state)
    required(config.get('codex'), 'Codex unavailable; setup with --codex native-binary')
    selected = {}
    for name in instructions:
        path = Path(name).absolute()
        required(not path.is_symlink() and all(not p.is_symlink() for p in path.parents), 'Instruction symlink refused')
        required(path.stat().st_size <= 32768, 'Instruction source too large')
        selected[str(path.resolve())] = digest(path)
    return {'schema': 'skill-lab-native-demo/1', 'target': 'codex-demo', 'state': str(Path(state).resolve()),
            'configuration': config, 'model': codex_host.DEFAULT_MODEL, 'effort': 'low',
            'conditions': [r[0] for r in SCHEDULE], 'sessions': 4,
            'active_wall_seconds': 300, 'readonly_wall_seconds': 90, 'max_tool_calls': 16,
            'active_observed_token_threshold': 32000, 'readonly_observed_token_threshold': 8000,
            'sum_observed_token_thresholds': 80000, 'sum_wall_seconds': 780,
            'max_protocol_bytes': codex_host.MAX_PROTOCOL_BYTES,
            'instructions': selected, 'disabled_mcp': list(disabled_mcp),
            'skill_sha256': digest(fixture.SKILL), 'task': TASK, 'readonly_task': READONLY,
            'system': SYSTEM, 'controller_sha256': digest(__file__),
            'grader_sha256': digest(fixture.__file__), 'offline_acceptance_sha256': digest(offline_acceptance.__file__),
            'authentication': 'existing native ChatGPT subscription; auth stays outside candidate mounts',
            'separately_billed_api_allowance_usd': 0, 'api_fallback': False,
            'subscription_quota_use': 'unknown; four bounded sessions including failed attempts',
            'automatic_retries': 0, 'stop_on': ['boundary or pin drift', 'failed controls', 'host error',
                'missing or invalid artifact', 'rejected tool request', 'time/token/tool limit', 'interruption'],
            'limits': ['Observed token thresholds can overshoot by one response.',
                       'No provider-side hard response/token cap; native transport retries not separately capped.',
                       'Public authored smoke only; not unseen holdouts or general usefulness.',
                       'Explicit invocation, not automatic skill discovery; no aggregate cgroup quota.']}


def prompt_for(skill, readonly):
    prompt = READONLY if readonly else TASK
    return prompt + ('\nSkill available only for in-scope tasks:\n' + fixture.SKILL.read_text() if skill else '')


def validate_host(raw, frozen, directory, skill, readonly):
    """Bind independently parsed results to the exact scheduled native request."""
    required(raw.get('environmentless') is True, 'Native environment boundary missing')
    required(raw.get('limits') == {
        'wall_seconds': frozen['readonly_wall_seconds'] if readonly else frozen['active_wall_seconds'],
        'dynamic_calls': frozen['max_tool_calls'], 'protocol_bytes': frozen['max_protocol_bytes'],
        'observed_tokens': frozen['readonly_observed_token_threshold'] if readonly else frozen['active_observed_token_threshold'],
        'max_threads': 1}, 'Native limits differ from approved plan')
    required(raw.get('common_instruction_sha256') == frozen['instructions'], 'Native instruction pins differ')
    configuration = raw.get('configuration', {})
    required(configuration.get('provider') == 'openai'
             and configuration.get('disabled_features') == list(codex_host.DISABLED_FEATURES)
             and configuration.get('project_doc_max_bytes') == 0
             and configuration.get('max_threads') == 1
             and configuration.get('approval_policy') == 'never'
             and configuration.get('sandbox_mode') == 'read-only', 'Native configuration differs from safe policy')
    threads = [row['params'] for row in raw.get('requests', []) if row.get('method') == 'thread/start']
    turns = [row['params'] for row in raw.get('requests', []) if row.get('method') == 'turn/start']
    required(threads == [codex_host.thread_params(directory / 'host', frozen['model'], system_prompt=SYSTEM)],
             'Native thread differs from approved model/system/tool boundary')
    thread_id = raw.get('thread', {}).get('thread', {}).get('id')
    required(thread_id and turns == [{'threadId': thread_id, 'input': [{'type': 'text', 'text': prompt_for(skill, readonly)}],
                                     'effort': 'low', 'environments': []}], 'Native turn differs from scheduled prompt')
    expected_request = {'prompt': prompt_for(skill, readonly), 'model': frozen['model']}
    required(json.loads((directory / 'request.json').read_text()) == expected_request, 'Changed request snapshot')
    host = codex_host.parse_host(raw, frozen['model'])
    required(host['model_responses'] > 0, 'Missing actual model responses')
    return host


def boundary_hashes(out):
    return {name: digest(Path(out) / name) for name in (
        'boundary/report.json', 'boundary/native/report.json', 'boundary/security/report.json',
        'boundary/security/hostile-process.json', 'boundary/security/known-unsafe-process.json')}


def grade(source, directory):
    """Execute only a frozen artifact in credential-free mounts, one input at a time."""
    directory.mkdir()
    required(source.is_file() and not source.is_symlink() and source.stat().st_size <= 65536,
             'Missing/unsafe/oversized solution artifact')
    frozen = directory / 'solution.py'
    shutil.copyfile(source, frozen)
    rows = []
    for value, expected in fixture.CHECKS:
        process = fixture.bounded(fixture.command(frozen), json.dumps(value).encode())
        save(directory / f'case-{len(rows)}.json', process)
        required(process['status'] == 'completed' and process['exit_code'] == 0, 'Grading process failed')
        actual = json.loads(process['stdout'])
        rows.append({'passed': fixture.same_answer(actual, expected)})
    return {'passed': sum(r['passed'] for r in rows), 'total': len(rows)}


def inventory(out):
    return {p.relative_to(out).as_posix(): digest(p) for p in sorted(out.rglob('*'))
            if p.is_file() and not p.is_symlink() and p.relative_to(out).parts[0] != 'boundary'
            and p.name != 'native-result.json' and 'work' not in p.relative_to(out).parts
            and 'host' not in p.relative_to(out).parts}


def approved_environment(prepared):
    required(prepared == plan(prepared['state'], prepared['instructions'], prepared['disabled_mcp']),
             'Approved plan or security pins changed; stop and review a new plan')
    return prepared['configuration']


def run(plan_path, approval, out):
    required(plan_path and approval, 'Provide the reviewed plan and its explicitly approved SHA256; no approval inferred')
    prepared = json.loads(Path(plan_path).read_text())
    required(fixture.sha(fixture.encode(prepared)) == approval, 'Approval does not match exact plan')
    required(prepared == plan(prepared['state'], prepared['instructions'], prepared['disabled_mcp']),
             'Plan or security pins changed; prepare and review a new plan')
    out = Path(out).absolute()
    required(not out.is_symlink() and all(not p.is_symlink() for p in out.parents), 'Run symlink refused')
    out.mkdir(parents=True, exist_ok=False)
    # A plan is a single-use authorization, including stopped/uncertain attempts.
    reservation = Path(prepared['state']) / ('approval-' + approval + '.json')
    with reservation.open('x') as stream:
        stream.write(json.dumps({'plan_sha256': approval, 'run': str(out), 'status': 'consumed'}) + '\n')
    save(out / 'native-plan.json', prepared)
    save(out / 'native-result.json', {'status': 'incomplete', 'completed': [], 'inventory': {}})
    result = {'status': 'incomplete', 'completed': [], 'failure': None}
    try:
        boundary = environment.validate_boundary(prepared['state'], out / 'boundary')
        required(boundary['status'] == 'pass', 'Fresh native boundary failed; inspect boundary evidence')
        result['boundary_evidence'] = boundary_hashes(out)
        controls = offline_acceptance.run(out / 'controls')
        required(controls['status'] == 'pass', 'Offline controls failed')
        config = approved_environment(prepared)
        preflight = codex_host.preflight(out / 'preflight-host', binary=Path(config['codex']),
                    allowed_instruction_sources=prepared['instructions'], disabled_mcp_servers=prepared['disabled_mcp'])
        save(out / 'preflight.json', preflight)
        required(preflight['status'] == 'ready' and preflight['auth_type'] == 'chatgpt', 'Native subscription preflight failed')
        required(any(prepared['model'] in (row.get('id'), row.get('model')) for row in preflight['models']),
                 'Approved model unavailable in native subscription catalog')
        for name, skill, readonly in SCHEDULE:
            approved_environment(prepared)
            directory = out / name
            directory.mkdir()
            work = directory / 'work'
            work.mkdir()
            prompt = prompt_for(skill, readonly)
            save(directory / 'request.json', {'prompt': prompt, 'model': prepared['model']})
            raw = codex_host.run_host(work, directory / 'tools', None, directory / 'host', prompt,
                prepared['model'], binary=Path(config['codex']), runtime=Path(config['runtime']),
                wall=90 if readonly else 300, max_calls=16, token_limit=8000 if readonly else 32000,
                system_prompt=SYSTEM, allowed_instruction_sources=prepared['instructions'],
                disabled_mcp_servers=prepared['disabled_mcp'])
            save(directory / 'host.json', raw)
            host = validate_host(raw, prepared, directory, skill, readonly)
            # Reuse existing durable tool receipt reconciliation, including rejected requests.
            import lab
            receipts = lab.tool_receipts(directory / 'tools', host['calls'])
            required(all(r['outcome']['status'] == 'completed' for r in receipts), 'Rejected or failed tool request')
            if readonly:
                passed = not host['calls'] and json.loads(host['answer']) == {'operation': 'display_verbatim', 'output': ' Alice '}
                required(not list(work.iterdir()), 'Read-only control modified workspace')
                scored = {'passed': int(passed), 'total': 1}
            else:
                required(host['calls'], 'Missing activation evidence')
                scored = grade(work / 'solution.py', directory / 'grading')
            save(directory / 'grade.json', scored)
            result['completed'].append(name)
        result['status'] = 'completed'
    except (OSError, ValueError, KeyError, TypeError, KeyboardInterrupt) as error:
        result['failure'] = type(error).__name__ + ': ' + str(error)
    finally:
        result['inventory'] = inventory(out)
        save(out / 'native-result.json', result)
    return result


def observed_usage(out, bindings):
    """Collect all scheduled receipts before grading gates, including stopped attempts."""
    from experiment_report import native_usage
    bindings = bindings if isinstance(bindings, dict) else {}
    fields = ('input_tokens', 'output_tokens', 'cached_input_tokens', 'total_tokens', 'model_responses', 'tool_calls')
    attempts = {}
    for name, _, _ in SCHEDULE:
        path = Path(out) / name / 'host.json'
        if not path.exists():
            continue
        row = {key: None for key in fields}
        row['evidence'] = 'unverified'
        try:
            required(digest(path) == bindings.get(name + '/host.json'), 'Changed or unbound host receipt')
            raw = json.loads(path.read_text())
            required(isinstance(raw, dict) and isinstance(raw.get('raw_events'), list), 'Malformed native events')
            observed = native_usage(raw, 'codex')
            for key in fields:
                value = observed.get(key)
                row[key] = value if type(value) is int and value >= 0 else None
            # Cached tokens are a subset of input, never an additional charge.
            if (row['cached_input_tokens'] is not None and row['input_tokens'] is not None
                    and row['cached_input_tokens'] > row['input_tokens']):
                row['cached_input_tokens'] = None
            row['evidence'] = 'hash_bound_native_receipt'
            row['host_completed'] = raw.get('status') == 'completed'
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        attempts[name] = row
    totals = {}
    for key in fields:
        values = [row[key] for row in attempts.values()]
        totals[key] = sum(values) if values and all(value is not None for value in values) else None
    return {'attempts': attempts, 'totals': totals, 'recorded_attempts': len(attempts),
            'planned_conditions_without_receipt': len(SCHEDULE) - len(attempts),
            'scope': 'Recorded scheduled attempts, including failures; missing telemetry is unknown, not zero cost.'}


def display(value):
    return 'unknown' if value is None else str(value)


def report(out):
    """Readback never executes source or trusts self-reported candidate success."""
    from experiment_report import clean
    out = Path(out)
    lines, scores, outcomes = [], [], {}
    status, error, result = 'incomplete', None, {}
    # Usage has a separate evidence path: a failed grader or boundary readback
    # must not erase usage already recorded by a stopped native attempt.
    try:
        result = json.loads((out / 'native-result.json').read_text())
    except (OSError, ValueError, TypeError):
        pass
    result = result if isinstance(result, dict) else {}
    usage = observed_usage(out, result.get('inventory', {}))
    try:
        frozen = json.loads((out / 'native-plan.json').read_text())
        result = json.loads((out / 'native-result.json').read_text())
        required(frozen == plan(frozen['state'], frozen['instructions'], frozen['disabled_mcp']), 'Current pins differ from frozen plan')
        required(result['inventory'] and inventory(out) == result['inventory'], 'Missing or changed native evidence')
        required(result.get('boundary_evidence') == boundary_hashes(out), 'Missing or changed fresh boundary evidence')
        verified_controls = offline_acceptance.verify(out / 'controls')
        required(json.loads((out / 'controls/report.json').read_text()) == verified_controls, 'Changed controls report')
        preflight = json.loads((out / 'preflight.json').read_text())
        required(preflight.get('status') == 'ready' and preflight.get('auth_type') == 'chatgpt',
                 'Missing native subscription preflight')
        import codex_boundary
        config = environment.load(frozen['state'])
        codex_boundary.verify_proof(out / 'boundary/native/report.json', runtime=Path(config['runtime']),
                                    binary=Path(config['codex']), model=frozen['model'])
        for name, skill, readonly in SCHEDULE:
            directory = out / name
            if not (directory / 'host.json').exists():
                continue
            raw = json.loads((directory / 'host.json').read_text())
            host = validate_host(raw, frozen, directory, skill, readonly)
            import lab
            receipts = lab.tool_receipts(directory / 'tools', host['calls'])
            required(all(r['outcome']['status'] == 'completed' for r in receipts), 'Failed or rejected tool')
            if readonly:
                required(not list((directory / 'work').iterdir()), 'Read-only workspace changed')
                passed = int(not host['calls'] and json.loads(host['answer']) == {'operation': 'display_verbatim', 'output': ' Alice '})
                total = 1
            else:
                required(host['calls'], 'Missing activation evidence')
                digest(directory / 'grading/solution.py')
                processes = [json.loads((directory / f'grading/case-{i}.json').read_text()) for i in range(len(fixture.CHECKS))]
                required(all(p['status'] == 'completed' and p['exit_code'] == 0 for p in processes), 'Incomplete grading')
                passed = sum(fixture.same_answer(json.loads(p['stdout']), expected)
                             for p, (_, expected) in zip(processes, fixture.CHECKS))
                total = len(fixture.CHECKS)
            required(json.loads((directory / 'grade.json').read_text()) == {'passed': passed, 'total': total}, 'Forged grade')
            scores.append(passed == total)
            outcomes[name] = {'passed': passed, 'total': total}
            lines.append(f'{name}: {passed}/{total}; model responses {host["model_responses"]}; tools {len(host["calls"])}.')
        required(result['status'] == 'completed' and result['completed'] == [r[0] for r in SCHEDULE] and len(lines) == 4,
                 result.get('failure') or 'Missing scheduled conditions')
        status = 'pass' if all(scores) else 'fail'
    except (OSError, ValueError, KeyError, TypeError) as exc:
        error = str(exc)
    pair_complete = all(name in outcomes for name in ('baseline', 'skill'))
    gain = (outcomes['skill']['passed'] / outcomes['skill']['total']
            - outcomes['baseline']['passed'] / outcomes['baseline']['total']) if pair_complete else None
    pair = {'complete': pair_complete, 'baseline': outcomes.get('baseline'), 'skill': outcomes.get('skill'),
            'score_gain': gain, 'repetitions': 1}
    decision = {'schema': 'skill-lab-preview-decision/1', 'status': status, 'evidence_class': 'native_codex_smoke',
                'adoption': 'inconclusive', 'safe_to_install': False,
                'completed_conditions': len(lines), 'planned_conditions': 4,
                'active_pair': pair, 'usage': usage}
    text = f'**Skill Lab Codex demo: {status}. Adoption: inconclusive.**\n\n' + '\n\n'.join(lines)
    if pair_complete:
        text += (f'\n\nActive pair: baseline {pair["baseline"]["passed"]}/{pair["baseline"]["total"]}; '
                 f'skill {pair["skill"]["passed"]}/{pair["skill"]["total"]}; score gain {gain:+.3f}. '
                 'One public smoke pair cannot establish general benefit.')
    else:
        text += '\n\nActive pair: incomplete; no baseline-versus-skill effect estimate.'
    if error:
        text += '\n\nStopped/unverified: ' + clean(error)
    totals = usage['totals']
    text += (f'\n\nObserved model responses (including the stopped attempt when available): {display(totals["model_responses"])}. '
             f'Recorded attempt receipts: {usage["recorded_attempts"]}/4. '
             f'Observed tokens across those attempts: input {display(totals["input_tokens"])}, '
             f'output {display(totals["output_tokens"])}, total {display(totals["total_tokens"])}, '
             f'cached input {display(totals["cached_input_tokens"])} (already included in input). '
             'Missing receipts or counters are unknown, never zero-cost success. '
             'Subscription quota/dollar cost unknown; separately billed API allowance $0, no fallback. '
             'Public smoke, one pair and explicit activation only; no general model-performance or safe-install claim. '
             'No automatic retry. Review local raw host/tool/grading receipts before further approval.\n')
    return decision, text
