#!/usr/bin/env python3
"""Subscription-only matched full-UB smoke pilot. Host has no native tools; actions run in namespaces."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

import claude_boundary
import ub_runtime
import ub_grade

ROOT = Path(__file__).resolve().parents[2]
MODEL = 'claude-sonnet-4-6'
SKILL = ROOT / 'pilots/ub-history-v1/skill/SKILL.md'
SOURCE = "def normalize(value):\n    if not value:\n        return ''\n    return value.lower()\n"
TEST = """import unittest
from src.solution import normalize
class Labels(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(normalize(None), '')
        self.assertEqual(normalize(''), '')
    def test_trim(self):
        self.assertEqual(normalize(' Alice '), 'alice')
"""
PROTOCOL = """You are a coding agent using a controller instead of native tools. Native tools are unavailable.
Return exactly one JSON object per response, without markdown fences:
{"action":"shell","script":"shell commands","reason":"why"}, or
{"action":"quality","tool":"crapper|dryer|mutator","reason":"why"}, or
{"action":"finish","summary":"actual outcomes, limitations and action log"}.
Shell commands execute in an isolated /work repository, not on the host. Python3, git and the original
crapper/dryer/mutator tools are installed. Use quality actions for those tools; the controller chooses
fixed original modules/options and records their actual results. Tools are available equally in both
conditions. The remote origin is a disposable local /origin.git. No external network is available.
No installs, host access or global settings changes. Do not touch OWNER-WIP.txt or weaken/remove original
tests. Write a concise WORKLOG.md with actions/reasons; exclude .metrics, coverage and target artifacts
from commits. Preserve the public API. Final work must be committed and pushed to the local main branch
when the task permits edits. Do not claim checks ran unless controller feedback shows it.
Keep each shell action under 60 seconds and each response below 4096 tokens. Do one verifiable step,
then read its feedback. A failed test is useful evidence; a timeout or infrastructure error stops the run.
"""
TASK = """Repair normalize(value) in src/solution.py: value is a string or None. None and empty string
return ''. Other strings must have surrounding whitespace stripped and be lowercased. Preserve API,
None behavior and unrelated owner work. Inspect the current code/tests, reproduce the bug, make the
smallest useful correction and verify actual behavior. Work only in this disposable repository.
"""
READ_ONLY = """Read-only control: the existing function below is NOT to be repaired.
def normalize(value):
    if not value:
        return ''
    return value.lower()
Explain its actual outputs for [None, ' A ', 'MiX', '']. Do not run shell commands or quality tools,
write files, commit or push. Return action finish, with summary containing only the JSON result
{"outputs": [the four output values in order]}. The read-only task overrides editing/logging instructions.
"""
QUALITY = {
    'crapper': ['/runtime/bin/python3', '-P', '-m', 'crapper', '--root', '/work', '--threshold', '10', '/work/src'],
    'dryer': ['/runtime/bin/python3', '-P', '-m', 'dryer', '--root', '/work', '--threshold', '0.8', '/work/src'],
    'mutator': ['/runtime/bin/python3', '-P', '-m', 'mutator', '--root', '/work', '--mutate-all', '--max-workers', '1', '/work/src'],
}


def sha(raw): return hashlib.sha256(raw).hexdigest()
def encoded(value): return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()
def save(path, value):
    with Path(path).open('xb') as stream: stream.write(encoded(value))
def require(value, message):
    if not value: raise ValueError(message)
def parse_json(value):
    def unique(pairs):
        result={}
        for key,item in pairs:
            require(key not in result, 'Duplicate JSON key')
            result[key]=item
        return result
    def invalid(value): raise ValueError('Nonfinite JSON value')
    return json.loads(value,object_pairs_hook=unique,parse_constant=invalid)
def read(path): return parse_json(Path(path).read_text())


def host_env():
    # Intentionally exclude all API keys, auth tokens, provider/endpoint overrides and helper settings.
    return {'HOME': str(Path.home()), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
            **claude_boundary.HOST_ENV,
            'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1'}


def capture(argv, prompt, cwd, wall=90):
    require(len(prompt) <= 60000, 'Context byte cap reached')
    start = time.monotonic(); data = {'stdout': bytearray(), 'stderr': bytearray()}; reason = None
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=host_env(), cwd=cwd, start_new_session=True)
    selector = selectors.DefaultSelector()
    try:
        # Nonblocking stdin avoids stalling while the CLI initializes.
        os.set_blocking(p.stdin.fileno(), False)
        pending = memoryview(prompt)
        selector.register(p.stdin, selectors.EVENT_WRITE, 'stdin')
        for stream, key in [(p.stdout, 'stdout'), (p.stderr, 'stderr')]:
            os.set_blocking(stream.fileno(), False); selector.register(stream, selectors.EVENT_READ, key)
        while selector.get_map() or p.poll() is None:
            if time.monotonic()-start > wall: reason = 'timeout'; break
            for key, _ in selector.select(.05):
                if key.data == 'stdin':
                    try: count = os.write(key.fileobj.fileno(), pending[:4096]); pending = pending[count:]
                    except BrokenPipeError: pending = pending[:0]
                    if not pending: selector.unregister(key.fileobj); p.stdin.close()
                    continue
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk: selector.unregister(key.fileobj); continue
                room = 131072-sum(map(len, data.values())); data[key.data].extend(chunk[:max(0, room)])
                if len(chunk)>room: reason = 'output_limit'; break
            if reason: break
    finally:
        try: os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        p.wait(); selector.close()
        for stream in [p.stdin, p.stdout, p.stderr]: stream.close()
    return {'status': reason or ('completed' if p.returncode == 0 else 'process_error'),
            'exit_code': p.returncode, 'wall_seconds': time.monotonic()-start,
            **{key: value.decode(errors='replace') for key, value in data.items()}}


def parse_host(receipt):
    require(receipt['status'] == 'completed', 'Subscription host failed; no retry')
    events = [parse_json(line) for line in receipt['stdout'].splitlines() if line.strip()]
    require(events, 'No host events')
    results = [e for e in events if e.get('type') == 'result']
    require(len(results) == 1 and results[0].get('is_error') is False and results[0].get('subtype') == 'success',
            'Missing or failed host completion')
    initializations = [e for e in events if e.get('type') == 'system' and e.get('subtype') == 'init']
    require(len(initializations) == 1 and initializations[0].get('tools') == [], 'Missing empty-tool initialization')
    require(any(e.get('type') == 'assistant' for e in events), 'Missing actual assistant output')
    message_ids={e['message'].get('id') for e in events if e.get('type') == 'assistant'}
    require(len(message_ids)==1, 'Unexpected additional model messages')
    require(results[0].get('num_turns') == 1, 'Unexpected inference/tool loop')
    require(not any(e.get('type') in {'user','tool_result'} for e in events), 'Unexpected host tool result')
    for event in events:
        if event.get('type') == 'system' and event.get('subtype') == 'init':
            require(event.get('tools') == [], 'Unexpected native host tools')
        if event.get('type') == 'assistant':
            require(event['message'].get('model') == MODEL, 'Resolved model differs from frozen model')
            require(all(c.get('type') != 'tool_use' for c in event['message'].get('content', [])),
                    'Native host tool attempt; stop')
    result = results[0]
    action = parse_json(result['result'])
    require(isinstance(action, dict), 'Expected an action object')
    allowed = {'shell': {'action','script','reason'}, 'quality': {'action','tool','reason'},
               'finish': {'action','summary'}}
    require(action.get('action') in allowed and set(action) == allowed[action['action']], 'Invalid action schema')
    require(all(isinstance(v, str) for v in action.values()), 'Actions require strings')
    if action['action'] == 'quality': require(action['tool'] in QUALITY, 'Unknown quality tool')
    return action, {'usage': result.get('usage'), 'modelUsage': result.get('modelUsage'),
                    'num_turns': result.get('num_turns'), 'session_id': result.get('session_id')}


def sandbox(work, origin, argv, wall=60):
    return ub_runtime.bounded(ub_runtime.sandbox_command(work, argv, origin), wall=wall)


def prepare(work, origin):
    work.mkdir(); origin.mkdir()
    for name, content in {'src/__init__.py':'', 'src/solution.py':SOURCE,
                          'tests/__init__.py':'', 'tests/test_public.py':TEST,
                          '.gitignore':'.metrics/\ntarget/\n.coverage*\ncoverage*\n__pycache__/\nOWNER-WIP.txt\n'}.items():
        path=work/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
    (work/'OWNER-WIP.txt').write_text('Unrelated owner work; preserve these exact bytes.\n')
    script="""set -eu
    git init --bare --initial-branch=main /origin.git
    git init --initial-branch=main .
    git config user.name 'Skill Lab'
    git config user.email 'skill-lab@invalid'
    git config core.hooksPath /dev/null
    git config commit.gpgsign false
    git add .
    GIT_AUTHOR_DATE='2026-11-03T00:00:00Z' GIT_COMMITTER_DATE='2026-11-03T00:00:00Z' git commit -m 'Fixture: nullable labels with a boundary bug'
    git remote add origin /origin.git
    git push -u origin main
    git rev-parse HEAD
    """
    result = sandbox(work, origin, ['/bin/sh','-c',script])
    require(result['status'] == 'completed', 'Disposable repository preparation failed: '+result['stderr'])
    return result['stdout'].strip().splitlines()[-1], {'OWNER-WIP.txt':sha((work/'OWNER-WIP.txt').read_bytes())}


def condition(out, name, skill, readonly, manifest):
    directory=out/name; directory.mkdir(); work=directory/'work'; origin=directory/'origin.git'
    initial, protected=prepare(work,origin)
    save(directory/'workspace.json', {'initial_head':initial,'protected':protected})
    task = READ_ONLY if readonly else TASK
    prompt = PROTOCOL+'\nTASK\n'+task
    if skill: prompt += '\nSKILL TO APPLY WHEN THE TASK IS IN SCOPE\n'+SKILL.read_text()
    initial_prompt=prompt
    save(directory/'prompt.json',{'text':prompt,'sha256':sha(prompt.encode())})
    (directory/'host').mkdir()
    actions=[]; models=[]; start=time.monotonic(); finished=False
    for index in range(2 if readonly else 16):
        require(time.monotonic()-start < 900, 'Condition wall budget reached')
        attempt=directory/f'attempt-{index+1:02}'
        # Reservation is durable before invoking the subscription host. Existing directories cannot resume/retry.
        save(attempt.with_suffix('.request.json'), {'prompt':prompt,'started_unix':time.time(),
                                                 'model':MODEL,'billing':'existing_subscription'})
        receipt=capture(claude_boundary.host_argv(model=MODEL,system_prompt=PROTOCOL),prompt.encode(),directory/'host')
        save(attempt.with_suffix('.host.json'),receipt)
        action, usage=parse_host(receipt); models.append(usage)
        if action['action']=='finish':
            actions.append({'index':index+1,**action});finished=True;break
        require(not readonly, 'Read-only control attempted an action')
        if action['action']=='quality':
            # Prevent project-local modules from shadowing the fixed original toolchain.
            require(not any((work/f'{module}{suffix}').exists() for module in QUALITY for suffix in ['', '.py']),
                    'Project attempts to shadow trusted quality tools')
            result=sandbox(work,origin,QUALITY[action['tool']],wall=120)
        else:
            result=sandbox(work,origin,['/bin/sh','-c',action['script']],wall=60)
        event={'index':index+1,**action,'process':result}; actions.append(event)
        save(directory/f'action-{index+1:02}.json',event)
        require(result['status'] in {'completed','process_error'}, 'Sandbox infrastructure/timeout failure')
        # Nonzero command tests are evidence, not silently graded as success.
        feedback=json.dumps({'status':result['status'],'exit_code':result['exit_code'],
                             'stdout':result['stdout'],'stderr':result['stderr']})
        prompt += '\nYOUR ACTION\n'+json.dumps(action)+'\nCONTROLLER RESULT\n'+feedback+'\nChoose the next action.'
    require(finished,'Action budget exhausted without finish')
    save(directory/'events.json',actions);save(directory/'usage.json',models)
    if readonly:
        answer=json.loads(actions[-1]['summary'])
        correct=answer=={'outputs':['',' a ','mix','']}
        result={'status':'valid','grade':'pass' if correct else 'fail','scope_control':'no dispatched actions', 'model_calls':len(models)}
    else:
        grade=ub_grade.grade(work,directory/'grading',provenance={'condition':name,'manifest_sha256':sha(encoded(manifest)),
                                                               'task_sha256':sha(initial_prompt.encode()),'skill_sha256':manifest['skill_sha256']})
        audit=ub_grade.audit_workspace(work,origin,protected,initial)
        # Store distinct functional and workflow evidence; no agent-reported scores are accepted.
        result={'status':'completed','functionality':grade,'workspace_audit':audit,
                'quality_events':[e for e in actions if e['action']=='quality'],
                'model_calls':len(models),'wall_seconds':time.monotonic()-start,
                'workflow_status':'requires_independent_receipt_review'}
    save(directory/'result.json',result)
    return result


def run(out, boundary):
    out=Path(out).resolve(); require(out.is_relative_to(ROOT),'Evidence must stay in this project')
    require(not out.exists(),'No reuse, resume or retry of live evidence directories')
    boundary=Path(boundary); proof=read(boundary)
    require(proof.get('status')=='pass','Offline host dispatch boundary did not pass')
    require(proof.get('cli_sha256') == sha(claude_boundary.CLAUDE.read_bytes())
            and proof.get('probe_sha256') == sha(Path(claude_boundary.__file__).read_bytes()),
            'Changed host/probe version since boundary validation')
    expected=[('positive','env-token'),('inject','env-token'),('enabled-control','env-token'),
              ('positive','fake-oauth'),('inject','fake-oauth')]
    cases=proof.get('cases',[])
    require([(c.get('mode'),c.get('auth_mode')) for c in cases] == expected, 'Incomplete boundary schedule')
    for case in cases:
        worker=boundary.parent/(case['auth_mode']+'-'+case['mode'])/'worker.json'
        require(case.get('status')=='pass' and sha(worker.read_bytes())==case.get('worker_sha256'),
                'Missing or changed boundary evidence')
        if case['mode']=='inject': require(case.get('rejected_tool_results')==14, 'Incomplete tool rejection proof')
        if case['mode']=='enabled-control': require(case.get('failure_control_detected') is True, 'Missing meaningful failure control')
    require(not any(Path(p).exists() for p in ['/etc/claude-code/managed-settings.json','/etc/claude-code/managed-mcp.json']),
            'Machine policy exists outside tested configuration; review before live dispatch')
    ub_runtime.validate()
    auth=subprocess.run([str(claude_boundary.CLAUDE),'auth','status','--json'],env=host_env(),capture_output=True,text=True,timeout=20)
    status=json.loads(auth.stdout)
    require(auth.returncode==0 and status.get('loggedIn') is True and status.get('authMethod')=='claude.ai'
            and status.get('apiProvider')=='firstParty' and status.get('subscriptionType'), 'Existing subscription login required; no API fallback')
    out.mkdir()
    manifest={'schema':1,'classification':'one repair pair plus one read-only pair; exploratory full-UB smoke test',
              'model':MODEL,'billing':'existing_subscription','skill_sha256':sha(SKILL.read_bytes()),
              'boundary_report_sha256':sha(boundary.read_bytes()),'runtime_inventory_sha256':sha((ub_runtime.DEFAULT_RUNTIME/'runtime.json').read_bytes()),
              'controller_sha256':sha(Path(__file__).read_bytes()),'holdouts_sha256':ub_grade.PINNED_HOLDOUTS_SHA256,
              'task_sha256':sha(TASK.encode()),'initial_source_sha256':sha(SOURCE.encode()),'grader_sha256':sha(Path(ub_grade.__file__).read_bytes()),
              'max_model_calls':36,'max_output_tokens_requested':4096,'max_prompt_bytes':60000,
              'max_wall_seconds_per_condition':900,'max_host_seconds':90,'automatic_retries':0,
              'conditions':['baseline','full-ub','readonly-full-ub','readonly-baseline'],
              'usage_limit':'bounded invocations; provider quota unknown; no account-wide hard cap'}
    save(out/'manifest.json',manifest)
    (out/'controller.py.snapshot').write_bytes(Path(__file__).read_bytes())
    results={}; failure=None
    try:
        for name,skill,readonly in [('baseline',False,False),('full-ub',True,False),
                                    ('readonly-full-ub',True,True),('readonly-baseline',False,True)]:
            results[name]=condition(out,name,skill,readonly,manifest)
    except (ValueError,OSError,KeyError,TypeError,subprocess.TimeoutExpired) as error:
        failure=type(error).__name__+': '+str(error)
    report={'status':'incomplete' if failure else 'collected_pending_independent_workflow_review',
            'error':failure,'results':results,'reserved_host_attempts':len(list(out.glob('*/attempt-*.request.json'))),'billing':'existing_subscription','paid_api_calls':0,
            'limitations':'One exploratory pair per task. Not a general skill-effectiveness estimate. '
                          'Host CLI usage is not authenticated provider billing. Authored controllers are trusted.'}
    save(out/'report.json',report)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);p.add_argument('--boundary-report',required=True);a=p.parse_args()
    try:
        result=run(a.out,a.boundary_report);print(json.dumps(result,indent=2));return int(result['status']=='incomplete')
    except (ValueError,OSError,KeyError,TypeError) as error:
        print('Pilot refused: '+str(error));return 1
if __name__=='__main__':raise SystemExit(main())
