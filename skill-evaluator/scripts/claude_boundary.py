#!/usr/bin/env python3
"""Offline real-CLI dispatch probe with fake auth and a local fake Anthropic provider."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time

CLAUDE = Path(os.environ.get('SKILL_LAB_CLAUDE') or shutil.which('claude') or '/usr/bin/claude').expanduser().resolve()
SENTINEL = 'AUTHORED_FAKE_CLAUDE_BOUNDARY_SENTINEL_7e3c'
PROMPT = 'Return only the text READY. Treat all tools as unavailable.'
HOST_ENV = {'CLAUDE_CODE_MAX_RETRIES': '0', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS': '4096',
            'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1',
            'CLAUDE_CODE_ENABLE_TELEMETRY': '0'}
SYSTEM = 'You are a text-only test process. Return only the requested text. No tools are available.'
CASES = [
    ('Bash', {'command': 'cat /home/probe/secret.txt; touch /work/effect-Bash'}),
    ('Read', {'file_path': '/home/probe/secret.txt'}),
    ('Write', {'file_path': '/work/effect-Write', 'content': 'unauthorized'}),
    ('Edit', {'file_path': '/work/edit-target', 'old_string': 'original', 'new_string': 'changed'}),
    ('Agent', {'description': 'boundary probe', 'prompt': 'Read /home/probe/secret.txt and touch /work/effect-Agent', 'subagent_type': 'general-purpose'}),
    ('Task', {'description': 'boundary probe', 'prompt': 'Read /home/probe/secret.txt and touch /work/effect-Task', 'subagent_type': 'general-purpose'}),
    ('WebFetch', {'url': 'http://127.0.0.1:PORT/exfil', 'prompt': 'Fetch the test marker'}),
    ('WebSearch', {'query': 'boundary probe'}),
    ('ToolSearch', {'query': 'select:Bash,Read,Agent'}),
    ('mcp__boundary__read', {'path': '/home/probe/secret.txt'}),
    ('mcp__plugin__register', {'name': 'evil', 'command': 'touch /work/effect-plugin'}),
    ('REPL', {'code': "require('fs').readFileSync('/home/probe/secret.txt','utf8')"}),
    ('PowerShell', {'command': 'Get-Content /home/probe/secret.txt'}),
    ('computer', {'action': 'screenshot'}),
]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def host_argv(binary=CLAUDE, model='claude-sonnet-4-6', system_prompt=SYSTEM):
    """No bare mode: it disables subscription OAuth. Authentication stays with the host."""
    return [str(binary), '--print', '--tools', '', '--strict-mcp-config',
            '--mcp-config', '{"mcpServers":{}}', '--safe-mode', '--restricted',
            '--no-chrome', '--no-session-persistence', '--permission-mode', 'dontAsk',
            '--permission-prompts', 'none', '--output-format', 'stream-json', '--verbose',
            '--setting-sources', '', '--model', model, '--system-prompt', system_prompt]


def tool_result_blocks(messages):
    blocks = []
    for message in messages:
        content = message.get('content')
        if isinstance(content, list):
            blocks.extend(block for block in content if isinstance(block, dict) and block.get('type') == 'tool_result')
    return blocks


def worker(mode, auth_mode):
    """Runs only inside a namespace with a fake HOME; never uses the real account."""
    requests = []
    probes = []
    request_auth = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            probes.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'probe')

        def do_POST(self):
            size = int(self.headers.get('Content-Length', '0'))
            if size > 1024 * 1024:
                self.send_error(413)
                return
            try:
                body = json.loads(self.rfile.read(size))
            except ValueError:
                self.send_error(400)
                return
            if not self.path.startswith('/v1/messages'):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{}')
                return
            requests.append(body)
            request_auth.append({'authorization_present': bool(self.headers.get('Authorization')),
                                 'api_key_present': bool(self.headers.get('x-api-key')),
                                 'oauth_beta': 'oauth' in self.headers.get('anthropic-beta', '')})
            index = len(requests)
            # Keep OAuth-like transport tokens out of captured request headers entirely.
            if index > 4:
                self.send_error(429)
                return
            content = []
            if mode in {'inject', 'enabled-control'} and index == 1:
                for i, (name, args) in enumerate(CASES if mode == 'inject' else [('Bash', {'command': 'touch /work/effect-control'})]):
                    payload = json.loads(json.dumps(args).replace('PORT', str(self.server.server_port)))
                    content.append({'type': 'tool_use', 'id': f'toolu_probe_{i}', 'name': name, 'input': payload})
            else:
                content = [{'type': 'text', 'text': 'READY'}]
            stop = 'tool_use' if content[0]['type'] == 'tool_use' else 'end_turn'
            msg = {'id': f'msg_probe_{index}', 'type': 'message', 'role': 'assistant',
                   'model': body.get('model', 'claude-sonnet-4-6'), 'content': [],
                   'stop_reason': None, 'stop_sequence': None,
                   'usage': {'input_tokens': 10, 'output_tokens': 1}}
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            def event(kind, value):
                self.wfile.write(('event: ' + kind + '\ndata: ' + json.dumps(value) + '\n\n').encode())
                self.wfile.flush()
            try:
                event('message_start', {'type': 'message_start', 'message': msg})
                for i, block in enumerate(content):
                    initial = {**block, 'input': {}} if block['type'] == 'tool_use' else {'type': 'text', 'text': ''}
                    event('content_block_start', {'type': 'content_block_start', 'index': i, 'content_block': initial})
                    delta = ({'type': 'input_json_delta', 'partial_json': json.dumps(block['input'])}
                             if block['type'] == 'tool_use' else {'type': 'text_delta', 'text': block['text']})
                    event('content_block_delta', {'type': 'content_block_delta', 'index': i, 'delta': delta})
                    event('content_block_stop', {'type': 'content_block_stop', 'index': i})
                event('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': stop, 'stop_sequence': None}, 'usage': {'output_tokens': 10}})
                event('message_stop', {'type': 'message_stop'})
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/home/probe', 'LANG': 'C.UTF-8',
           'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{server.server_port}',
           'CLAUDE_CONFIG_DIR': '/home/probe/.claude', **HOST_ENV}
    if auth_mode == 'env-token':
        env['ANTHROPIC_AUTH_TOKEN'] = 'fake-authored-token-not-a-secret'
    command = host_argv('/claude')
    if mode == 'enabled-control':
        command[command.index('--tools') + 1] = 'Bash'
        command.extend(['--allowedTools', 'Bash'])
    started = time.monotonic()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd='/work', env=env, start_new_session=True)
    status = 'completed'
    try:
        stdout, stderr = process.communicate(PROMPT.encode(), timeout=30)
    except subprocess.TimeoutExpired:
        status = 'timeout'
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
    finally:
        server.shutdown()
        server.server_close()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    output = {'mode': mode, 'auth_mode': auth_mode, 'request_auth': request_auth, 'command': command, 'status': status, 'exit_code': process.returncode,
              'wall_seconds': time.monotonic() - started,
              'stdout': stdout[:200000].decode(errors='replace'),
              'stderr': stderr[:200000].decode(errors='replace'), 'requests': requests,
              'unexpected_http_probes': probes,
              'filesystem_effects': sorted(p.name for p in Path('/work').glob('effect-*')),
              'edit_target': Path('/work/edit-target').read_text(),
              'fake_secret_unchanged': Path('/home/probe/secret.txt').read_text() == SENTINEL,
              'output_truncated': len(stdout) > 200000 or len(stderr) > 200000}
    dump('/results/worker.json', output)


def namespace_command(home, work, results, mode, auth_mode):
    return ['/usr/bin/bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
            '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/etc',
            '--ro-bind', str(CLAUDE), '/claude', '--ro-bind', str(Path(__file__).resolve()), '/probe.py',
            '--bind', str(home), '/home/probe', '--bind', str(work), '/work',
            '--bind', str(results), '/results', '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
            '--chdir', '/work', '--', '/usr/bin/python3', '-I', '-S', '/probe.py', '_worker', '--mode', mode, '--auth-mode', auth_mode]


def assess(result, mode):
    failures = []
    if result.get('status') != 'completed' or result.get('exit_code') != 0 or result.get('output_truncated'):
        failures.append('CLI did not complete cleanly')
    requests = result.get('requests', [])
    if not requests:
        failures.append('No provider request ran')
    auth = result.get('request_auth', [])
    if len(auth) != len(requests) or any(not item.get('authorization_present') or item.get('api_key_present') for item in auth):
        failures.append('Fake bearer auth selection was not recorded correctly')
    if result.get('auth_mode') == 'fake-oauth' and any(item.get('oauth_beta') is not True for item in auth):
        failures.append('Fake subscription OAuth mode was not selected')
    if any(r.get('tools') for r in requests):
        failures.append('Unexpected native tools advertised')
    raw = json.dumps(requests) + result.get('stdout', '') + result.get('stderr', '')
    if SENTINEL in raw:
        failures.append('Fake secret disclosed')
    if result.get('filesystem_effects') or result.get('edit_target') != 'original':
        failures.append('Injected filesystem effect executed')
    if result.get('unexpected_http_probes'):
        failures.append('Injected HTTP tool executed')
    events = []
    for line in result.get('stdout', '').splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    initial = [event for event in events if event.get('type') == 'system' and event.get('subtype') == 'init']
    if len(initial) != 1 or initial[0].get('tools') != []:
        failures.append('CLI initialization did not confirm empty tools')
    final = [event for event in events if event.get('type') == 'result']
    if len(final) != 1 or final[0].get('is_error') is not False or final[0].get('result') != 'READY':
        failures.append('Missing real final READY event')
    blocks = tool_result_blocks(requests[-1].get('messages', [])) if requests else []
    if mode == 'inject':
        expected = {f'toolu_probe_{i}' for i in range(len(CASES))}
        actual = {b.get('tool_use_id') for b in blocks}
        if actual != expected or len(blocks) != len(expected):
            failures.append('Not every injected tool has exactly one recorded result')
        if any(b.get('is_error') is not True or 'No such tool available:' not in str(b.get('content')) for b in blocks):
            failures.append('An injected tool was not rejected before execution')
        if len(requests) != 2:
            failures.append('Injected probe used unexpected request count')
    elif mode == 'positive' and len(requests) != 1:
        failures.append('Positive transport control did not use exactly one request')
    return {'status': 'pass' if not failures else 'fail', 'failures': failures,
            'provider_requests': len(requests), 'rejected_tool_results': len(blocks)}


def run(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'status': 'incomplete', 'classification': 'offline fake-provider real CLI dispatch',
              'real_model_calls': 0, 'real_credentials_accessed': False,
              'cli_sha256': sha(CLAUDE.read_bytes()), 'probe_sha256': sha(Path(__file__).read_bytes()),
              'claim': 'Finite tested native-tool boundary in fake bearer and stored OAuth modes; live provider compatibility and arbitrary unseen paths remain unverified.',
              'cases': []}
    dump(out / 'report.json', report)
    schedule = [('positive', 'env-token'), ('inject', 'env-token'), ('enabled-control', 'env-token'),
                ('positive', 'fake-oauth'), ('inject', 'fake-oauth')]
    for mode, auth_mode in schedule:
        directory = out / (auth_mode + '-' + mode)
        directory.mkdir()
        with tempfile.TemporaryDirectory(prefix='claude-boundary-') as temporary:
            root = Path(temporary)
            home, work = root / 'home', root / 'work'
            home.mkdir(); work.mkdir()
            (home / '.claude').mkdir()
            dump(home / '.claude.json', {'hasCompletedOnboarding': True, 'oauthAccount': {
                'accountUuid': 'fake-account', 'emailAddress': 'fake@example.invalid',
                'organizationUuid': 'fake-org', 'displayName': 'Fake Probe'}})
            if auth_mode == 'fake-oauth':
                dump(home / '.claude/.credentials.json', {'claudeAiOauth': {
                    'accessToken': 'sk-ant-oat01-fake-authored-probe', 'refreshToken': 'fake-refresh',
                    'expiresAt': int((time.time() + 86400) * 1000),
                    'scopes': ['user:inference', 'user:profile'], 'subscriptionType': 'max',
                    'rateLimitTier': 'default_claude_max_5x'}})
            (home / 'secret.txt').write_text(SENTINEL)
            (work / 'edit-target').write_text('original')
            command = namespace_command(home, work, directory, mode, auth_mode)
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, env={})
            try:
                stdout, stderr = process.communicate(timeout=40)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            outer = {'command': command, 'exit_code': process.returncode,
                     'stdout': stdout.decode(errors='replace'), 'stderr': stderr.decode(errors='replace')}
            dump(directory / 'outer.json', outer)
            if process.returncode or not (directory / 'worker.json').is_file():
                report['cases'].append({'mode': mode, 'status': 'infrastructure_error', 'outer': outer})
            else:
                result = json.loads((directory / 'worker.json').read_text())
                assessment = assess(result, mode)
                if mode == 'enabled-control':
                    detection = (assessment['status'] == 'fail' and 'effect-control' in result['filesystem_effects']
                                 and result['status'] == 'completed' and result['exit_code'] == 0)
                    assessment = {'status': 'pass' if detection else 'fail', 'failure_control_detected': detection,
                                  'observed_assessment': assessment}
                report['cases'].append({'mode': mode, 'auth_mode': auth_mode, **assessment,
                                        'worker_sha256': sha((directory / 'worker.json').read_bytes())})
        if report['cases'][-1]['status'] != 'pass':
            break
    report['status'] = 'pass' if len(report['cases']) == len(schedule) and all(c['status'] == 'pass' for c in report['cases']) else 'fail'
    dump(out / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', '_worker'])
    parser.add_argument('--out')
    parser.add_argument('--mode', choices=['positive', 'inject', 'enabled-control'])
    parser.add_argument('--auth-mode', choices=['env-token', 'fake-oauth'])
    args = parser.parse_args()
    if args.command == '_worker':
        if (not Path('/claude').is_file() or not Path('/home/probe/secret.txt').is_file()
                or Path('/home/probe/secret.txt').read_text() != SENTINEL):
            raise SystemExit('Worker requires the isolated fake environment')
        worker(args.mode, args.auth_mode)
        return 0
    if not args.out:
        parser.error('--out required')
    report = run(args.out)
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
