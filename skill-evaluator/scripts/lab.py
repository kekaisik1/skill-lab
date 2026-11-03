#!/usr/bin/env python3
"""Subscription skill lab: native tools, frozen cases, independent outcome checks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time

import claude_boundary
import full_ub_pilot
import lab_mcp
import ub_grade
import ub_runtime

ROOT = Path(__file__).resolve().parents[2]
MODEL = 'claude-sonnet-4-6'
BOUNDARY = ROOT / 'runs/lab-mcp-boundary-v6/report.json'
CODEX_BOUNDARY = ROOT / 'runs/codex-boundary-v2/report.json'
CODEX_CONTEXT = ROOT / 'pilots/codex-host-v2'
CODEX_DISABLED_MCP = ('Sanity', 'context7', 'filesystem', 'github', 'livekit-docs',
                      'nexfield-ops', 'node_repl', 'openaiDeveloperDocs', 'pencil', 'sequential-thinking')
SCHEDULE = [('baseline', False, False), ('skill', True, False),
            ('readonly-skill', True, True), ('readonly-baseline', False, True)]
SKILL_MARKER = '\n\nSKILL AVAILABLE: Apply the complete skill when this task is in scope.\n'
SYSTEM = '''You are a coding agent in a disposable skill evaluation workspace.
Use only the real skill_lab tools supplied by the controller. They execute in /work with Python3, git,
and the original crapper/dryer/mutator installed. The quality tool runs the fixed
original companion commands. Native host tools, network and package installs
are unavailable. Only /work and its disposable local /origin.git may be changed.
Use write_file for artifact contents (at most 32768 characters per file).
Shell scripts are limited to 8192 characters; do not put large files in shell heredocs.
Use quality with tool='tests' to run Python regression tests; this fixes the test
command so its actual red/green results can be independently audited.
Python test discovery requires an importable tests package: create tests/__init__.py
and at least one actual unittest case before invoking it. Empty/skipped discovery
and rejected tool requests invalidate this evaluation, even if later repaired.
Do not modify OWNER-WIP.txt or weaken existing tests. For implementation tasks,
preserve existing test files byte for byte; add new tests in separate files.
keep WORKLOG.md with actions and reasons, verify outcomes, commit and push to
local origin/main. Exclude generated metrics, coverage and cache files from git.
For explanation-only tasks, do not use tools or change anything. Task scope
overrides implementation workflow. Report only actions established by actual
tool results. Never invent tool output, commits or scores. Finish with a concise
summary of actual outcomes and limitations. A separate controller grades files.
'''
READONLY = '''This is explanation only; do not implement, redesign or test anything.
No tools, files, logs, commits or pushes. Read this existing Python function:
def normalize(value):
    if not value:
        return ''
    return value.lower()
Return only one JSON object with the outputs for [None, ' A ', 'MiX', '']:
{"outputs": [the four values in order]}
'''


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def save(path, value):
    with Path(path).open('xb') as stream:
        stream.write(encode(value))
        stream.flush()
        os.fsync(stream.fileno())


def read(path):
    return full_ub_pilot.parse_json(Path(path).read_text())


def profile(target):
    require(target in {'ub', 'frontend'}, 'Unsupported target; use list')
    skill = (full_ub_pilot.SKILL if target == 'ub' else ROOT / 'pilots/frontend-v1/skill/SKILL.md')
    task = full_ub_pilot.TASK if target == 'ub' else (ROOT / 'pilots/frontend-v1/task.md').read_text()
    return {'id': target, 'skill': str(skill), 'skill_sha256': sha(skill.read_bytes()),
            'task': task, 'task_sha256': sha(task.encode()),
            'source': ('UB upstream 57a8bdd28e45ed41900ace0bafa2ff76dd9ed70f' if target == 'ub'
                       else 'Installed frontend-design skill snapshot; plugin 1.1.1'),
            'classification': 'synthetic development smoke cases; not unseen or population-level evidence'}


def backend(agent, model=None):
    require(agent in {'codex', 'claude'}, 'Unsupported candidate backend')
    if agent == 'codex':
        import codex_host
        return {'model': model or codex_host.DEFAULT_MODEL, 'binary': codex_host.CODEX,
                'helper': Path(codex_host.__file__), 'billing': 'existing_chatgpt_subscription'}
    require(model in {None, MODEL}, 'Claude backend retains its validated fixed model')
    return {'model': MODEL, 'binary': claude_boundary.CLAUDE,
            'helper': Path(claude_boundary.__file__), 'billing': 'existing_claude_subscription'}


def codex_instructions():
    context = read(CODEX_CONTEXT / 'context.json')
    require(sha((CODEX_CONTEXT / 'AGENTS.md').read_bytes()) == context['sha256'],
            'Pinned common Codex instructions changed')
    return {context['source']: context['sha256']}


def plan(target, agent='codex', model=None):
    spec = profile(target)
    selected = backend(agent, model)
    grader = Path(ub_grade.__file__) if target == 'ub' else Path(__file__).with_name('frontend_grade.py')
    result = {'schema': 1, 'target': target, 'model': selected['model'], 'agent': agent,
            'billing': selected['billing'], 'api_fallback': False,
            'separately_billed_api_spending_limit_usd': 0,
            'subscription_quota_remaining': None, 'subscription_quota_cost': None,
            'max_sessions': 4, 'max_turns_per_active_session': 24,
            'max_turns_per_readonly_session': 3, 'max_tool_calls_per_session': 32,
            'max_output_tokens_per_turn_requested': 4096,
            'max_wall_seconds_per_active_session': 900, 'max_wall_seconds_per_readonly_session': 90,
            'automatic_retries': 0, 'stop_on': ['authentication error', 'rate limit', 'host failure',
                'infrastructure failure', 'missing tool receipt', 'boundary drift', 'wall or turn limit'],
            'conditions': [name for name, _, _ in SCHEDULE],
            'condition_delta': 'exact skill text and explicit in-scope invocation only',
            'skill_sha256': spec['skill_sha256'], 'skill_source': spec['source'],
            'task_sha256': spec['task_sha256'], 'readonly_task_sha256': sha(READONLY.encode()),
            'system_prompt_sha256': sha(SYSTEM.encode()),
            'grader_sha256': sha(grader.read_bytes()), 'controller_sha256': sha(Path(__file__).read_bytes()),
            'helper_sha256': {name: sha(Path(__file__).with_name(name).read_bytes()) for name in
                              ['full_ub_pilot.py', 'claude_boundary.py', 'ub_runtime.py', 'ub_grade.py']},
            'server_sha256': sha(Path(lab_mcp.__file__).read_bytes()),
            'cli_sha256': sha(Path(selected['binary']).read_bytes()),
            'host_helper_sha256': sha(selected['helper'].read_bytes()),
            'runtime_sha256': sha((ub_runtime.DEFAULT_RUNTIME / 'runtime.json').read_bytes()),
            'scope': spec['classification'],
            'limitations': ['One active case and one negative case per skill; fixed order, no statistical claim.',
                'Explicit skill invocation tests compliance, not automatic installed-skill discovery.',
                'Candidates have no browser tool; final frontend browser and visual review are controller-side.',
                'Bounds limit this run, not account-wide quota. No aggregate cgroup disk/memory quota.']}
    if agent == 'codex':
        # Codex app-server has no verified equivalent of Claude's max-turns/output option.
        import codex_host
        result.update(max_turns_per_active_session=None, max_turns_per_readonly_session=None,
                      max_output_tokens_per_turn_requested=None, max_agent_threads=1,
                      reasoning_effort='low', max_protocol_bytes=codex_host.MAX_PROTOCOL_BYTES,
                      observed_token_limit_active=128000, observed_token_limit_readonly=16000,
                      common_instruction_sha256=codex_instructions(),
                      disabled_mcp_servers=list(CODEX_DISABLED_MCP),
                      automatic_retries_scope='Controller session restarts; native transport retries are not separately capped',
                      tool_transport='Codex App Server client-executed dynamic tools')
        result['limitations'].append('Observed token limit can overshoot by one response; no provider token/response-count hard cap. Cancellation may leave an in-flight response.')
    else:
        result['tool_transport'] = 'Claude native MCP tools'
    return result


def authentication(agent='claude', host_dir=None):
    if agent == 'codex':
        import codex_host
        instructions = codex_instructions()
        if host_dir is not None:
            result = codex_host.preflight(host_dir, disabled_mcp_servers=CODEX_DISABLED_MCP,
                                          allowed_instruction_sources=instructions)
            save(Path(host_dir) / 'preflight.json', result)
        else:
            with tempfile.TemporaryDirectory(prefix='codex-lab-preflight-', dir=ROOT / 'runs') as directory:
                result = codex_host.preflight(Path(directory), disabled_mcp_servers=CODEX_DISABLED_MCP,
                                              allowed_instruction_sources=instructions)
        require(result.get('status') == 'ready' and result.get('auth_type') == 'chatgpt',
                'Codex ChatGPT subscription preflight failed at ' + str(result.get('stage', 'unknown stage'))
                + ': ' + str(result.get('error', result.get('status'))))
        return result
    result = subprocess.run([str(claude_boundary.CLAUDE), 'auth', 'status', '--json'],
                            env=full_ub_pilot.host_env(), capture_output=True, text=True, timeout=20)
    status = json.loads(result.stdout)
    require(result.returncode == 0 and status.get('loggedIn') is True
            and status.get('authMethod') == 'claude.ai' and status.get('apiProvider') == 'firstParty'
            and status.get('subscriptionType'), 'Existing Claude subscription login required; no API fallback')
    return {key: status.get(key) for key in ['loggedIn', 'authMethod', 'apiProvider', 'subscriptionType']}


def boundary(agent='claude', model=None):
    if agent == 'codex':
        import codex_boundary
        import codex_host
        result = codex_boundary.verify_proof(CODEX_BOUNDARY, model=model or codex_host.DEFAULT_MODEL)
        require(result is not False, 'Codex boundary proof invalid')
        return {'path': str(CODEX_BOUNDARY), 'sha256': sha(CODEX_BOUNDARY.read_bytes())}
    import lab_mcp_boundary
    require(not any(Path(name).exists() for name in ['/etc/claude-code/managed-settings.json',
            '/etc/claude-code/managed-mcp.json']), 'Managed policy differs from the tested host configuration')
    result = lab_mcp_boundary.verify_proof(BOUNDARY)
    require(result is not False, 'MCP boundary proof invalid')
    return {'path': str(BOUNDARY), 'sha256': sha(BOUNDARY.read_bytes())}


def doctor(agent='codex'):
    observations = {}
    for name, check in [('runtime', ub_runtime.validate), ('boundary', lambda: boundary(agent)),
                        ('subscription', lambda: authentication(agent))]:
        try:
            detail = check()
            if name == 'runtime':
                detail = {'verified_files': len(detail['files']),
                          'manifest_sha256': sha((ub_runtime.DEFAULT_RUNTIME / 'runtime.json').read_bytes())}
            observations[name] = {'status': 'pass', 'detail': detail}
        except (ValueError, OSError, KeyError, ImportError, subprocess.SubprocessError) as error:
            observations[name] = {'status': 'blocked', 'detail': str(error)}
    return {'status': 'ready' if all(v['status'] == 'pass' for v in observations.values()) else 'blocked',
            'checks': observations, 'model_calls': 0, 'targets': ['ub', 'frontend', 'ub-project'],
            'inference_verified': False,
            'auth_limit': 'Login status does not refresh expired credentials or prove provider availability.',
            'candidate_backend': agent, 'lab_root': str(ROOT)}


def capture(argv, prompt, cwd, wall, receipts_directory=None):
    require(len(prompt) <= 65536, 'Prompt byte cap exceeded')
    start, reason = time.monotonic(), None
    data = {'stdout': bytearray(), 'stderr': bytearray()}
    observed = set()
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=full_ub_pilot.host_env(), cwd=cwd, start_new_session=True)
    selector = selectors.DefaultSelector()
    try:
        for stream, name, event in [(process.stdin, 'stdin', selectors.EVENT_WRITE),
                                    (process.stdout, 'stdout', selectors.EVENT_READ),
                                    (process.stderr, 'stderr', selectors.EVENT_READ)]:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, event, name)
        pending = memoryview(prompt)
        while selector.get_map() or process.poll() is None:
            if time.monotonic() - start > wall:
                reason = 'timeout'
                break
            if receipts_directory is not None:
                for path in Path(receipts_directory).glob('[0-9]*.json'):
                    if path in observed:
                        continue
                    try:
                        outcome = read(path)['outcome']
                    except (OSError, ValueError, KeyError):
                        continue  # Writer may still be flushing this bounded receipt.
                    observed.add(path)
                    if outcome['status'] not in {'completed', 'process_error'} or re.search(
                            r'bwrap:|Operation not permitted|No space left on device', outcome.get('stderr', '')):
                        reason = 'tool_infrastructure_failure'
                        break
                if reason:
                    break
            for key, _ in selector.select(.05):
                if key.data == 'stdin':
                    try:
                        pending = pending[os.write(key.fileobj.fileno(), pending[:4096]):]
                    except BrokenPipeError:
                        pending = pending[:0]
                    if not pending:
                        selector.unregister(key.fileobj)
                        process.stdin.close()
                    continue
                chunk = os.read(key.fileobj.fileno(), 16384)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                room = 8 * 1024 * 1024 - sum(map(len, data.values()))
                data[key.data].extend(chunk[:max(0, room)])
                if len(chunk) > room:
                    reason = 'output_limit'
                    break
            if reason:
                break
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        selector.close()
        for stream in [process.stdin, process.stdout, process.stderr]:
            stream.close()
    return {'status': reason or ('completed' if process.returncode == 0 else 'process_error'),
            'exit_code': process.returncode, 'wall_seconds': time.monotonic() - start,
            **{key: value.decode(errors='replace') for key, value in data.items()}}


def host_result(receipt, agent='claude', model=None):
    if agent == 'codex':
        import codex_host
        require(receipt.get('status') == 'completed', 'Codex host failed: '
                + str(receipt.get('error', receipt.get('status', 'missing status'))))
        return codex_host.parse_host(receipt, model)
    if receipt['status'] != 'completed':
        detail = ''
        for line in receipt.get('stdout', '').splitlines():
            try:
                event = full_ub_pilot.parse_json(line)
            except ValueError:
                continue
            if event.get('type') == 'result' and event.get('is_error') is True:
                detail = str(event.get('result', ''))[:1200]
        raise ValueError('Subscription host failed: ' + receipt['status'] + (': ' + detail if detail else ''))
    events = [full_ub_pilot.parse_json(line) for line in receipt['stdout'].splitlines() if line.strip()]
    init = [e for e in events if e.get('type') == 'system' and e.get('subtype') == 'init']
    require(len(init) == 1 and set(init[0].get('tools', [])) == set(lab_mcp.TOOL_NAMES),
            'Unexpected native tools or missing MCP tools')
    models = [e['message'] for e in events if e.get('type') == 'assistant']
    require(models and all(e.get('model') == MODEL for e in models), 'Missing output or changed model')
    calls = [block for msg in models for block in msg.get('content', []) if block.get('type') == 'tool_use']
    require(all(c.get('name') in lab_mcp.TOOL_NAMES for c in calls), 'Attempted native host tool')
    results = [e for e in events if e.get('type') == 'result']
    require(len(results) == 1 and results[0].get('is_error') is False
            and results[0].get('subtype') == 'success', 'Missing/failed completion or exhausted turn limit')
    result = results[0]
    require(isinstance(result.get('result'), str) and result['result'].strip(), 'Empty actual answer')
    require(type(result.get('num_turns')) is int and result['num_turns'] > 0, 'Empty inference run')
    return {'answer': result['result'], 'calls': calls, 'num_turns': result['num_turns'],
            'usage': result.get('usage'), 'modelUsage': result.get('modelUsage'),
            'session_id': result.get('session_id')}


def tool_receipts(directory, calls):
    audit = [read_line(line) for line in (directory / 'audit.jsonl').read_text().splitlines()]
    require(sum(row['event'] == 'startup' for row in audit) == 1
            and sum(row['event'] == 'runtime_validated' for row in audit) == 1, 'MCP never initialized')
    attempts = [row for row in audit if row['event'] == 'attempt']
    results = [row for row in audit if row['event'] == 'result']
    require(len(attempts) == len(results) == len(calls), 'Missing/extra actual tool receipts')
    receipts = []
    # Parallel model calls can reach a serial server in a different order. Match exact input multisets.
    expected = sorted(encode({'tool': c['name'].split('__')[-1], 'arguments': c['input']}) for c in calls)
    actual = sorted(encode({'tool': c['tool'], 'arguments': c['arguments']}) for c in attempts)
    require(expected == actual, 'Model calls do not match actual dispatch')
    for index, row in enumerate(results, 1):
        require(row['index'] == index and Path(row['receipt']).name == row['receipt'], 'Invalid receipt sequence')
        path = directory / row['receipt']
        require(sha(path.read_bytes()) == row['sha256'], 'Changed tool receipt')
        receipt = read(path)
        outcome = receipt['outcome']
        require(outcome['status'] in {'completed', 'process_error'},
                f"Tool {index} ({receipt['tool']}) {outcome['status']}: " + outcome.get('stderr', '')[-800:])
        require(not re.search(r'bwrap:|Operation not permitted|No space left on device', outcome.get('stderr', '')),
                'Namespace/resource infrastructure failure')
        receipts.append(receipt)
    return receipts


def read_line(line):
    return full_ub_pilot.parse_json(line)


def test_count(receipt):
    outcome = receipt['outcome']
    text = outcome.get('stdout', '') + '\n' + outcome.get('stderr', '')
    counts = re.findall(r'Ran (\d+) tests? in ', text)
    return max([int(n) for n in counts] or [0]), text


def workflow(receipts, audit, work):
    quality = {}
    final_files = (receipts[-1].get('after') or {}).get('files') if receipts else None
    for name in ['crapper', 'dryer', 'mutator']:
        matches = [r for r in receipts if r['tool'] == 'quality' and r['arguments']['tool'] == name]
        last = matches[-1] if matches else None
        quality[name] = {'executed': bool(last), 'passed': False}
        current = bool(last and final_files and (last.get('after') or {}).get('files') == final_files)
        quality[name]['matches_final_source_and_tests'] = current
        if not last or last['outcome']['status'] != 'completed' or not current:
            continue
        count, text = test_count(last)
        if name == 'crapper':
            rows = re.findall(r'^\S+\s+\S+\s+\d+\s+([\d.]+)%\s+([\d.]+)\s*$', text, re.M)
            passed = count > 0 and bool(rows) and all(float(crap) < 10 for _, crap in rows)
            quality[name].update(tests=count, measured_functions=len(rows))
        elif name == 'dryer':
            passed = 'No duplicate candidates found.' in text
        else:
            row = re.search(r'^Total\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+)%', text, re.M)
            passed = bool(row) and count > 0 and int(row[1]) > 0 and int(row[2]) == int(row[3]) == 0 and int(row[4]) == int(row[1])
            if row:
                quality[name].update(killed=int(row[1]), survived=int(row[2]), uncovered=int(row[3]), sites=int(row[4]))
        quality[name]['passed'] = bool(passed)
    fixed_tests = [r for r in receipts if r['tool'] == 'quality' and r['arguments'].get('tool') == 'tests']
    red = [r['index'] for r in fixed_tests if test_count(r)[0] > 0
           and r['outcome']['exit_code'] != 0 and re.search(r'FAILED \(failures=\d+', test_count(r)[1])]
    green = [r['index'] for r in fixed_tests if test_count(r)[0] > 0 and r['outcome']['exit_code'] == 0
             and re.search(r'\bOK\b', test_count(r)[1])]
    edits = [r['index'] for r in receipts if (r.get('before') or {}).get('files', {}).get('src/solution.py')
             != (r.get('after') or {}).get('files', {}).get('src/solution.py')]
    tdd = bool(red and green and edits and min(red) < min(edits) < max(green))
    log = work / 'WORKLOG.md'
    has_log = log.is_file() and not log.is_symlink() and log.stat().st_size > 40
    passed = all(v['passed'] for v in quality.values()) and tdd and has_log and audit['status'] == 'pass'
    return {'status': 'pass' if passed else 'fail', 'quality': quality, 'red_test_steps': red,
            'source_change_steps': edits, 'green_test_steps': green, 'red_before_edit_then_green': tdd,
            'worklog_present': has_log, 'git_and_owner_audit': audit['status'],
            'scope': 'Observable repair workflow only; not every general UB principle applies to this tiny task.'}


def condition(target, out, name, skill, readonly, manifest):
    directory = out / name
    directory.mkdir()
    work, origin = directory / 'work', directory / 'origin.git'
    initial, protected = full_ub_pilot.prepare(work, origin)
    if target == 'ub':
        protected['tests/test_public.py'] = sha((work / 'tests/test_public.py').read_bytes())
        protected['tests/__init__.py'] = sha((work / 'tests/__init__.py').read_bytes())
    if target == 'frontend':
        prepared = full_ub_pilot.sandbox(work, origin, ['/bin/sh', '-ec',
            "git rm -r src tests; git commit -m 'Fixture: empty frontend workspace'; git push origin main; git rev-parse HEAD"])
        require(prepared['status'] == 'completed', 'Frontend clean workspace preparation failed')
        initial = prepared['stdout'].strip().splitlines()[-1]
    save(directory / 'workspace.json', {'initial_head': initial, 'protected': protected})
    spec = profile(target)
    task = READONLY if readonly else spec['task']
    prompt = task
    if skill:
        prompt += SKILL_MARKER + Path(spec['skill']).read_text()
    save(directory / 'prompt.json', {'text': prompt, 'sha256': sha(prompt.encode()), 'skill_included': skill})
    (directory / 'host').mkdir()
    agent, model = manifest.get('agent', 'claude'), manifest['model']
    argv = None
    if agent == 'claude':
        config = lab_mcp.mcp_config(work, directory / 'tools', origin)
        argv = lab_mcp.host_argv(claude_boundary.CLAUDE, config, model, SYSTEM, max_turns=3 if readonly else 24)
    save(directory / 'request.json', {'model': model, 'agent': agent, 'argv': argv, 'started_unix': time.time(),
                                      'billing': 'existing_subscription', 'manifest_sha256': sha(encode(manifest))})
    if agent == 'codex':
        import codex_host
        raw = codex_host.run_host(work, directory / 'tools', origin, directory / 'host', prompt, model,
                                 wall=90 if readonly else 900, max_calls=32, system_prompt=SYSTEM,
                                 token_limit=16000 if readonly else 128000,
                                 allowed_instruction_sources=manifest['common_instruction_sha256'],
                                 disabled_mcp_servers=CODEX_DISABLED_MCP)
    else:
        raw = capture(argv, prompt.encode(), directory / 'host', 90 if readonly else 900, directory / 'tools')
    save(directory / 'host.json', raw)
    host = host_result(raw, agent, model)
    if agent == 'codex':
        require(type(host.get('model_responses')) is int and host['model_responses'] > 0,
                'Missing actual Codex model response')
    save(directory / 'model.json', host)
    receipts = tool_receipts(directory / 'tools', host['calls'])
    if readonly:
        try:
            answer = full_ub_pilot.parse_json(host['answer'])
        except ValueError:
            answer = None
        passed = not receipts and answer == {'outputs': ['', ' a ', 'mix', '']}
        require(ub_grade.git_read(work, ['rev-parse', 'HEAD']) == initial, 'Read-only control changed git')
        result = {'status': 'completed', 'grade': 'pass' if passed else 'fail', 'tool_calls': len(receipts),
                  'scope': 'Actual model nonactivation control; no tool execution expected',
                  'correct_answer': answer == {'outputs': ['', ' a ', 'mix', '']}}
    else:
        require(receipts, 'Empty implementation run')
        provenance = {'target': target, 'condition': name, 'manifest_sha256': sha(encode(manifest)),
                      'skill_sha256': spec['skill_sha256'] if skill else None,
                      'task_sha256': spec['task_sha256'], 'prompt_sha256': sha(prompt.encode())}
        if target == 'ub':
            functional = ub_grade.grade(work, directory / 'grading', provenance=provenance)
            valid = functional['status'] == 'valid'
        else:
            import frontend_grade
            functional = frontend_grade.grade(work, directory / 'grading', provenance=provenance)
            valid = functional['status'] == 'valid'
        save(directory / 'provenance.json', provenance)
        require(valid, 'Independent grader did not execute all cases: ' + str(functional.get('status')))
        audit = ub_grade.audit_workspace(work, origin, protected, initial)
        wf = workflow(receipts, audit, work) if target == 'ub' else {'status': 'not_scored', 'git_audit': audit}
        passed = functional.get('grade') == 'pass' and audit['status'] == 'pass'
        if target == 'ub' and skill:
            passed = passed and wf['status'] == 'pass'
        result = {'status': 'completed', 'grade': 'pass' if passed else 'fail', 'functionality': functional,
                  'workspace_audit': audit, 'workflow': wf, 'actual_tool_calls': len(receipts),
                  'activation': 'observed implementation actions' if receipts else 'missing',
                  'visual_review': 'pending independent screenshot inspection' if target == 'frontend' else 'not_applicable'}
    result.update(model_turns=host['num_turns'] if agent == 'claude' else host['model_responses'],
                  agent_turns=host['num_turns'], wall_seconds=raw['wall_seconds'])
    save(directory / 'result.json', result)
    return result


def controls_pass(controls):
    return controls.get('status') in {'pass', 'valid'} and controls.get('grade', 'pass') == 'pass'


def summary(results, controls, failure=None):
    complete = set(results) == {name for name, _, _ in SCHEDULE} and all(
        row.get('status') == 'completed' for row in results.values())
    status = ('pass' if all(row.get('grade') == 'pass' for row in results.values()) else 'fail') if complete else 'incomplete'
    if failure or not controls_pass(controls):
        status = 'incomplete'
    return {'status': status, 'conditions_completed': sum(row.get('status') == 'completed' for row in results.values()),
            'conditions_expected': 4, 'results': results, 'fixture_controls': controls,
            'failure': failure, 'classification': 'real subscription model smoke test; fixture controls separately identified'}


def inventory(out):
    files = {}
    for path in sorted(out.rglob('*')):
        relative = path.relative_to(out)
        if any(part in {'work', 'origin.git', 'host'} for part in relative.parts) or relative.as_posix() == 'report.json':
            continue
        require(not path.is_symlink(), 'Evidence symlink refused')
        if path.is_file():
            require(path.stat().st_size <= 16 * 1024 * 1024, 'Evidence file too large')
            files[relative.as_posix()] = sha(path.read_bytes())
    return files


def host_attempts(out, manifest):
    """Count actual responses even when a condition never reached grading."""
    rows = {}
    for name, _, _ in SCHEDULE:
        path = out / name / 'host.json'
        if not path.exists():
            continue
        raw = read(path)
        row = {'host_status': raw['status'], 'wall_seconds': raw.get('wall_seconds'),
               'graded': (out / name / 'result.json').exists()}
        if manifest.get('agent') == 'codex':
            events = raw.get('raw_events', [])
            responses = {e['params']['responseId'] for e in events if e.get('method') == 'rawResponse/completed'}
            usage = [e['params']['tokenUsage'] for e in events if e.get('method') == 'thread/tokenUsage/updated']
            row.update(model_responses=len(responses), observed_token_usage=usage[-1] if usage else None,
                       actual_tool_calls=sum(e.get('method') == 'item/tool/call' for e in events))
        rows[name] = row
    return rows


def run(target, output, agent='codex', model=None):
    out = Path(output)
    if not out.is_absolute():
        out = ROOT / out
    out = out.resolve()
    require(out.is_relative_to(ROOT / 'runs') and out != ROOT / 'runs', 'Run evidence must be inside this lab runs/ directory')
    out.mkdir(parents=True, exist_ok=False)
    results, controls, failure = {}, {'status': 'not_run'}, None
    manifest = plan(target, agent, model)
    save(out / 'manifest.json', manifest)
    (out / 'controller.py.snapshot').write_bytes(Path(__file__).read_bytes())
    try:
        proof = boundary(agent, manifest['model'])
        ub_runtime.validate()
        auth = authentication(agent, out / 'preflight-host')
        save(out / 'preflight.json', {'boundary': proof, 'auth': auth, 'model_calls': 0})
        if agent == 'codex':
            listed = [row for row in auth['models'] if row.get('model') == manifest['model']]
            require(listed and any(e.get('reasoningEffort') == 'low' for e in listed[0].get('supportedReasoningEfforts', [])),
                    'Requested Codex model/low effort not present in the account catalog; no automatic fallback')
            (out / 'common-AGENTS.md.snapshot').write_bytes((CODEX_CONTEXT / 'AGENTS.md').read_bytes())
        if target == 'ub':
            controls = ub_grade.selftest(out / 'controls')
        else:
            import frontend_grade
            controls = frontend_grade.controls(out / 'controls')
        require(controls_pass(controls), 'Known-good/bad/missing-artifact grader controls failed')
        for name, skill, readonly in SCHEDULE:
            # Recheck code and tool identity before every separate subscribed session.
            require(plan(target, agent, model) == manifest, 'Task/skill/controller/runtime changed during run')
            boundary(agent, manifest['model'])
            print('Starting ' + target + '/' + name, flush=True)
            results[name] = condition(target, out, name, skill, readonly, manifest)
            print(name + ': ' + results[name]['grade'], flush=True)
    except (ValueError, OSError, KeyError, TypeError, ImportError, subprocess.SubprocessError) as error:
        failure = str(error)
    finally:
        for name, _, _ in SCHEDULE:
            if name not in results:
                results[name] = {'status': 'not_completed', 'grade': None}
        report = summary(results, controls, failure)
        report['host_attempts'] = host_attempts(out, manifest)
        report.update(target=target, manifest_sha256=sha(encode(manifest)), evidence=inventory(out))
        save(out / 'report.json', report)
    return report


def verify(output):
    out = Path(output)
    if not out.is_absolute():
        out = ROOT / out
    report = read(out / 'report.json')
    require(report['evidence'] == inventory(out), 'Missing, changed or extra evidence')
    require(report['manifest_sha256'] == sha((out / 'manifest.json').read_bytes()), 'Changed manifest')
    manifest = read(out / 'manifest.json')
    if 'host_attempts' in report:
        require(report['host_attempts'] == host_attempts(out, manifest), 'Changed actual host attempt accounting')
    if report['conditions_completed']:
        require(manifest.get('schema') == 1 and manifest.get('target') == report['target']
                and manifest.get('conditions') == [name for name, _, _ in SCHEDULE],
                'Report target/schedule differs from its frozen manifest')
    rebuilt = summary(report['results'], report['fixture_controls'], report['failure'])
    require(all(report[key] == value for key, value in rebuilt.items()), 'Report outcome does not match observations')
    for name, row in report['results'].items():
        if row['status'] == 'completed':
            require(row == read(out / name / 'result.json'), 'Report differs from condition receipt')
            verify_condition(out / name, name, report['target'], row, manifest)
    if report['conditions_completed']:
        verify_controls(out / 'controls', report['target'], report['fixture_controls'])
    return report


def verify_grade(directory, target):
    """Reconcile saved actual processes/artifacts; no model or candidate is launched."""
    receipt = read(directory / 'receipt.json')
    if receipt.get('status') == 'invalid_artifact':
        require(receipt.get('grade') == 'invalid', 'Invalid artifact reported as success')
        return receipt
    require(receipt.get('status') == 'valid', 'Grader did not complete')
    artifacts = receipt.get('artifact_inventory')
    require(isinstance(artifacts, list) and artifacts, 'Missing graded artifact inventory')
    for row in artifacts:
        path = Path(row['path'])
        require(not path.is_absolute() and '..' not in path.parts, 'Unsafe artifact path')
        file = directory / 'artifact' / path
        require(file.is_file() and not file.is_symlink() and sha(file.read_bytes()) == row['sha256'],
                'Missing or changed actual output artifact')
    if target == 'ub':
        cases = ub_grade.checks()
        processes = [p for p in receipt['processes'] if p.get('phase') == 'case']
        require(len(processes) == len(cases) > 0 and len(receipt['cases']) == len(cases), 'Missing private case execution')
        for case, process, row in zip(cases, processes, receipt['cases']):
            require(process.get('case_id') == row.get('id') == case['id'], 'Skipped or changed private case')
            require(process.get('status') == 'completed' and process.get('exit_code') == 0, 'Grader infrastructure failure')
            actual = full_ub_pilot.parse_json(process['stdout'])
            passed = type(actual) is str and actual == case['expected']
            require(row['actual'] == actual and row['passed'] is passed
                    and row['stdout_sha256'] == sha(process['stdout'].encode()), 'Grade differs from actual process output')
    else:
        import frontend_grade
        _, private = frontend_grade.frozen_bundle()
        expected = [v['name'] + '/' + name for v in frontend_grade.VIEWPORTS for name in private['cases']]
        browser = read(directory / 'browser-result.json')
        frontend_grade.validate_result(browser, expected, directory)
        require(receipt['process']['status'] == 'completed' and receipt['process']['exit_code'] == 0,
                'Browser infrastructure failure')
        require(browser == receipt['browser_result'] and browser['cases'] == receipt['cases'], 'Changed browser outcomes')
    rows = receipt['cases']
    require(rows and receipt['expected_cases'] == receipt['total'] == len(rows), 'Empty or skipped grading cases')
    passed = sum(row['passed'] is True for row in rows)
    require(passed == receipt['passed'] and receipt['grade'] == ('pass' if passed == len(rows) else 'fail'),
            'Grade aggregate differs from actual cases')
    return receipt


def verify_controls(directory, target, summary_receipt):
    stored = read(directory / ('selftest.json' if target == 'ub' else 'controls.json'))
    require(stored == summary_receipt and controls_pass(stored), 'Missing actual fixture controls')
    if target == 'ub':
        expected = {'good': 'pass', 'missing-trim': 'fail', 'returns-nothing': 'fail',
                    'casefold-mutant': 'fail', 'missing': 'invalid'}
        require({r['name'] for r in stored['controls']} == set(expected), 'Missing known-good/bad controls')
    else:
        expected = {'good': 'pass', 'claim-only': 'fail', 'wrong-credit': 'fail',
                    'partial-persistence': 'fail', 'missing': 'invalid', 'empty': 'invalid'}
        require(set(stored['controls']) == set(expected), 'Missing known-good/bad controls')
    for name, grade in expected.items():
        require(verify_grade(directory / name, target)['grade'] == grade, 'Control did not prove the expected outcome')


def verify_provenance(directory, name, target, manifest):
    """Bind evidence to the frozen task, skill version and actual condition."""
    require(manifest['target'] == target and isinstance(manifest['model'], str) and manifest['model'], 'Changed target or model')
    manifest_sha = sha(encode(manifest))
    request = read(directory / 'request.json')
    require(request['model'] == manifest['model'] and request['billing'] == 'existing_subscription'
            and request.get('agent', 'claude') == manifest.get('agent', 'claude')
            and request['manifest_sha256'] == manifest_sha, 'Request is not bound to this frozen run')
    prompt = read(directory / 'prompt.json')
    require(sha(prompt['text'].encode()) == prompt['sha256'], 'Changed task prompt')
    uses_skill = name in {'skill', 'readonly-skill'}
    require(prompt['skill_included'] is uses_skill, 'Wrong skill condition')
    task = prompt['text']
    if uses_skill:
        task, separator, skill = task.partition(SKILL_MARKER)
        require(separator and sha(skill.encode()) == manifest['skill_sha256'], 'Prompt skill version differs from frozen skill')
    task_sha = manifest['readonly_task_sha256' if name.startswith('readonly-') else 'task_sha256']
    require(sha(task.encode()) == task_sha, 'Prompt task differs from frozen task')
    return {'target': target, 'condition': name, 'manifest_sha256': manifest_sha,
            'skill_sha256': manifest['skill_sha256'] if uses_skill else None,
            'task_sha256': task_sha, 'prompt_sha256': prompt['sha256']}


def verify_condition(directory, name, target, row, manifest):
    provenance = verify_provenance(directory, name, target, manifest)
    agent = manifest.get('agent', 'claude')
    require(agent in {'codex', 'claude'}, 'Unknown candidate backend in manifest')
    host = host_result(read(directory / 'host.json'), agent, manifest['model'])
    if agent == 'codex':
        raw = read(directory / 'host.json')
        turns = [q.get('params', {}) for q in raw.get('requests', []) if q.get('method') == 'turn/start']
        threads = [q.get('params', {}) for q in raw.get('requests', []) if q.get('method') == 'thread/start']
        require(len(turns) == len(threads) == 1, 'Missing or duplicated actual Codex dispatch')
        require(turns[0].get('input') == [{'type': 'text', 'text': read(directory / 'prompt.json')['text']}]
                and turns[0].get('threadId') == host['session_id'], 'Actual Codex input differs from frozen task')
        require(threads[0].get('model') == manifest['model']
                and threads[0].get('allowProviderModelFallback') is False
                and turns[0].get('effort') == manifest['reasoning_effort'], 'Actual Codex model/settings changed')
        if 'system_prompt_sha256' in manifest:
            require(sha(threads[0].get('baseInstructions', '').encode()) == manifest['system_prompt_sha256'],
                    'Actual Codex system instructions differ from frozen instructions')
        require(raw.get('common_instruction_sha256') == manifest['common_instruction_sha256']
                and set(manifest['common_instruction_sha256'].values()) == {
                    sha((directory.parent / 'common-AGENTS.md.snapshot').read_bytes())},
                'Codex common context differs from its frozen manifest')
    require(host == read(directory / 'model.json'), 'Saved model result differs from actual host output')
    receipts = tool_receipts(directory / 'tools', host['calls'])
    expected = host['num_turns'] if agent == 'claude' else host.get('model_responses')
    require(type(expected) is int and expected > 0 and row['model_turns'] == expected
            and host['num_turns'] > 0, 'Missing actual model response or agent turn')
    if name.startswith('readonly-'):
        try:
            answer = full_ub_pilot.parse_json(host['answer'])
        except ValueError:
            answer = None
        passed = not receipts and answer == {'outputs': ['', ' a ', 'mix', '']}
        require(row['grade'] == ('pass' if passed else 'fail'), 'Incorrect nonactivation grade')
    else:
        require(receipts, 'Empty implementation run')
        receipt = verify_grade(directory / 'grading', target)
        require(receipt == row['functionality'], 'Functionality claim differs from grader evidence')
        require(receipt['provenance'] == read(directory / 'provenance.json') == provenance
                and receipt['grader_sha256'] == manifest['grader_sha256'],
                'Grading provenance differs from the frozen task/skill/output condition')
        require(row['actual_tool_calls'] == len(receipts), 'Incorrect tool count')
        audit = row['workspace_audit']
        if audit['status'] == 'pass':
            require(audit['head'] != audit['initial_head'] and audit['head'] == audit['origin_head']
                    and audit['working_tree_clean'] is True and audit['protected']
                    and all(p['unchanged'] is True and p['actual'] == p['expected'] for p in audit['protected'].values()),
                    'Incomplete git/owner audit')
        passed = receipt['grade'] == 'pass' and audit['status'] == 'pass'
        if target == 'ub' and name == 'skill':
            passed = passed and row['workflow']['status'] == 'pass'
        require(row['grade'] == ('pass' if passed else 'fail'), 'Condition grade differs from observed outcomes')


def display_report(result, output):
    path = Path(output)
    if not path.is_absolute():
        path = ROOT / path
    return {'status': result['status'], 'target': result['target'], 'report': str(path / 'report.json'),
            'conditions_completed': result['conditions_completed'],
            'conditions_expected': result['conditions_expected'], 'failure': result['failure'],
            'host_attempts': result.get('host_attempts', {}),
            'fixture_controls': {'passed': controls_pass(result['fixture_controls']), 'model_evidence': False},
            'conditions': {name: {'status': row['status'], 'grade': row['grade'],
                'functional_passed': row.get('functionality', {}).get('passed'),
                'functional_total': row.get('functionality', {}).get('total'),
                'workflow': row.get('workflow', {}).get('status'), 'model_turns': row.get('model_turns')}
                for name, row in result['results'].items()},
            'claim_limit': 'Small explicit-invocation smoke test, not general skill effectiveness or automatic discovery.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    doctor_parser = commands.add_parser('doctor')
    doctor_parser.add_argument('--agent', choices=['codex', 'claude'], default='codex')
    plan_parser = commands.add_parser('plan')
    plan_parser.add_argument('target', choices=['ub', 'frontend', 'ub-project'])
    plan_parser.add_argument('--agent', choices=['codex', 'claude'], default='codex')
    plan_parser.add_argument('--model')
    plan_parser.add_argument('--trials', type=int, choices=[1, 2], default=2,
                             help='Paired repetitions for ub-project only')
    plan_parser.add_argument('--active-token-limit', type=int, choices=[128000, 256000, 512000],
                             help='Explicit observed-token threshold for ub-project; default 256000')
    run_parser = commands.add_parser('run')
    run_parser.add_argument('target', choices=['ub', 'frontend', 'ub-project'])
    run_parser.add_argument('--out', required=True)
    run_parser.add_argument('--agent', choices=['codex', 'claude'], default='codex')
    run_parser.add_argument('--model')
    run_parser.add_argument('--trials', type=int, choices=[1, 2], default=2,
                            help='Paired repetitions for ub-project only')
    run_parser.add_argument('--active-token-limit', type=int, choices=[128000, 256000, 512000],
                            help='Explicit observed-token threshold for ub-project; default 256000')
    commands.add_parser('report').add_argument('output')
    args = parser.parse_args()
    try:
        if args.command == 'list':
            import project_study
            result = {'targets': [{k: v for k, v in profile(name).items() if k != 'task'}
                                  for name in ['ub', 'frontend']] + [project_study.profile()], 'lab_root': str(ROOT),
                      'default_backend': 'codex', 'backends': ['codex', 'claude'], 'api_fallback': False}
        elif args.command == 'doctor':
            result = doctor(args.agent)
        elif args.command == 'plan':
            if args.target == 'ub-project':
                import project_study
                require(args.agent == 'codex', 'Project study currently supports Codex only')
                result = project_study.plan(args.trials, args.model, args.active_token_limit or 256000)
            else:
                require(args.active_token_limit is None, 'Active token override supports ub-project only')
                result = plan(args.target, args.agent, args.model)
        elif args.command == 'run':
            if args.target == 'ub-project':
                import project_study
                require(args.agent == 'codex', 'Project study currently supports Codex only')
                result = project_study.display(project_study.run(args.out, args.trials, args.model,
                                               args.active_token_limit or 256000), args.out)
            else:
                require(args.active_token_limit is None, 'Active token override supports ub-project only')
                result = display_report(run(args.target, args.out, args.agent, args.model), args.out)
        else:
            output = Path(args.output)
            output = output if output.is_absolute() else ROOT / output
            if read(output / 'manifest.json').get('schema') == 'skill-lab-project-study/1':
                import project_study
                result = project_study.display(project_study.verify(args.output), args.output)
            else:
                result = display_report(verify(args.output), args.output)
        print(encode(result).decode(), end='')
        return 0 if result.get('status', 'pass') in {'pass', 'ready', 'completed'} else 1
    except (OSError, ValueError, KeyError, TypeError, ImportError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
