#!/usr/bin/env python3
"""Trusted Codex App Server controller; candidate tools run through lab_mcp.

The environmentless dispatch boundary must pass codex_boundary before live use.
No credentials are read, copied, returned, or passed to the candidate namespace.
"""
import json
import hashlib
import os
import re
from pathlib import Path
import selectors
import select
import signal
import subprocess
import time

import lab_mcp
import ub_runtime
from preview_environment import discover_codex

try:
    CODEX = discover_codex()
except ValueError:
    # Offline reports remain usable without an installed native CLI. Preflight
    # fails explicitly before dispatch; there is no alternate provider.
    CODEX = Path(os.environ.get('SKILL_LAB_CODEX', '/usr/bin/codex')).expanduser().absolute()
DEFAULT_MODEL = 'gpt-6.1-sol'
MAX_PROTOCOL_BYTES = 8 * 1024 * 1024
MAX_FRAME_BYTES = 1024 * 1024
SYSTEM = 'Use only the skill_lab tools to complete the task in the isolated /work workspace.'
DISABLED_FEATURES = ('hooks', 'apps', 'plugins', 'memories', 'multi_agent', 'multi_agent_v2',
                     'multi_agent_v2_dynamic_tools', 'code_mode', 'code_mode_only',
                     'code_mode_tool_search', 'shell_snapshot', 'skill_search',
                     'skill_mcp_dependency_install', 'plugin_discovery_prefetch', 'plugin_sharing',
                     'remote_plugin', 'shell_tool', 'unified_exec', 'view_image', 'browser_use',
                     'browser_use_external', 'browser_use_full_cdp_access', 'computer_use',
                     'sleep_tool', 'workspace_dependencies', 'unbounded_connection_retries',
                     'prevent_idle_sleep', 'request_permissions_tool', 'tool_suggest',
                     'enable_request_compression')


def host_env(host_dir, home=None, codex_home=None):
    """Keep native login in its profile; never inherit API/endpoint overrides."""
    home = Path(home or os.environ.get('HOME', str(Path.home())))
    result = {'HOME': str(home), 'CODEX_HOME': str(codex_home or os.environ.get('CODEX_HOME', home / '.codex')),
              'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'TMPDIR': str(Path(host_dir) / 'tmp')}
    # A new SQLite home with an existing profile triggers native history
    # backfill. Keep the native index; candidate tools have no host access.
    # Host security policy must not be weakened by environment cleaning.
    for name, value in os.environ.items():
        if name.startswith('CODEX_SANDBOX'):
            result[name] = value
    return result


def host_argv(host_dir, binary=CODEX, provider_url=None, disabled_mcp_servers=()):
    values = {**{'features.' + name: False for name in DISABLED_FEATURES},
              'features.code_mode_host': True,
              'features.skip_host_skill_discovery': True, 'features.omit_skill_descriptions': True,
              'project_doc_max_bytes': 0, 'agents.max_concurrent_threads_per_session': 1,
              'agents.enabled': False, 'agents.max_depth': 0,
              'shell_environment_policy.inherit': 'none', 'web_search': 'disabled',
              'approval_policy': 'never', 'sandbox_mode': 'read-only',
              'model_reasoning_effort': 'low', 'model_provider': 'openai',
              'developer_instructions': '', 'instructions': '', 'notify': []}
    for name in disabled_mcp_servers:
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', name):
            raise ValueError('Invalid configured MCP server name')
        values['mcp_servers.' + name + '.enabled'] = False
    if provider_url is not None:
        if not provider_url.startswith('http://127.0.0.1:'):
            raise ValueError('Offline test provider must be loopback HTTP')
        values.update({'model_provider': 'skill_lab_fixture',
                       'model_providers.skill_lab_fixture.name': 'Offline fixture',
                       'model_providers.skill_lab_fixture.base_url': provider_url,
                       'model_providers.skill_lab_fixture.wire_api': 'responses',
                       'model_providers.skill_lab_fixture.requires_openai_auth': False,
                       'model_providers.skill_lab_fixture.request_max_retries': 0,
                       'model_providers.skill_lab_fixture.stream_max_retries': 0})
    command = [str(binary), '--no-daemon']
    for key, value in values.items():
        command += ['-c', key + '=' + json.dumps(value, separators=(',', ':'))]
    return command + ['app-server', '--stdio', '--strict-config']


def thread_params(host_dir, model, environmentless=True, system_prompt=SYSTEM):
    result = {'cwd': str(Path(host_dir).resolve()), 'model': model, 'ephemeral': True,
              'approvalPolicy': 'never', 'approvalsReviewer': 'user', 'sandbox': 'read-only',
              'baseInstructions': system_prompt, 'developerInstructions': '',
              'runtimeWorkspaceRoots': [], 'selectedCapabilityRoots': [],
              'allowProviderModelFallback': False, 'experimentalRawEvents': True,
              'dynamicTools': [{'type': 'namespace', 'name': 'skill_lab',
                                'description': 'Tools in an isolated candidate workspace.',
                                'tools': [dict(tool, type='function', deferLoading=False) for tool in lab_mcp.TOOLS]}]}
    if environmentless:
        result['environments'] = []
    return result


class ProtocolError(ValueError):
    pass


class Client:
    def __init__(self, argv, env, host_dir, wall):
        self.deadline = time.monotonic() + wall
        self.events, self.sent, self.stderr, self.pending, self.buffer = [], [], bytearray(), [], bytearray()
        self.total, self.next_id = 0, 1000
        self.private_ids = set()
        self.stdout_closed = False
        self.process = subprocess.Popen(argv, cwd=host_dir, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ, 'stdout')
        self.selector.register(self.process.stderr, selectors.EVENT_READ, 'stderr')
        os.set_blocking(self.process.stdin.fileno(), False)

    def send(self, message):
        data = (json.dumps(message, allow_nan=False) + '\n').encode()
        if len(data) > MAX_FRAME_BYTES:
            raise ProtocolError('Outgoing frame exceeds bound')
        self.sent.append(message)
        while data:
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError('Codex protocol write wall limit exhausted')
            _, ready, _ = select.select([], [self.process.stdin], [], min(left, 1))
            if ready:
                try:
                    data = data[os.write(self.process.stdin.fileno(), data):]
                except BlockingIOError:
                    pass

    def receive(self):
        while True:
            if b'\n' in self.buffer:
                line, _, rest = self.buffer.partition(b'\n')
                self.buffer = bytearray(rest)
                if len(line) > MAX_FRAME_BYTES:
                    raise ProtocolError('Incoming frame exceeds bound')
                value = json.loads(line, object_pairs_hook=lab_mcp.strict_pairs)
                if not isinstance(value, dict):
                    raise ProtocolError('Expected a protocol object')
                self.events.append({'id': value['id'], 'redacted': True} if value.get('id') in self.private_ids and 'method' not in value else value)
                return value
            if self.stdout_closed:
                raise ProtocolError('Codex closed protocol before completion')
            left = self.deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError('Codex wall limit exhausted')
            ready = self.selector.select(min(left, 1))
            if not ready and self.process.poll() is not None:
                raise ProtocolError('Codex exited before required protocol completion')
            for key, _ in ready:
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    self.selector.unregister(key.fileobj)
                    if key.data == 'stdout':
                        self.stdout_closed = True
                    continue
                self.total += len(data)
                if self.total > MAX_PROTOCOL_BYTES:
                    raise ProtocolError('Codex protocol output exceeds bound')
                target = self.buffer if key.data == 'stdout' else self.stderr
                target.extend(data)
                if key.data == 'stdout' and b'\n' not in target and len(target) > MAX_FRAME_BYTES:
                    raise ProtocolError('Incoming frame exceeds bound')

    def request(self, method, params):
        request_id = self.next_id
        self.next_id += 1
        if method in {'config/read', 'account/read'}:
            self.private_ids.add(request_id)
        self.send({'id': request_id, 'method': method, 'params': params})
        while True:
            value = self.receive()
            if value.get('id') == request_id and 'method' not in value:
                if 'error' in value:
                    raise ProtocolError(method + ': ' + str(value['error']))
                return value['result']
            if 'id' in value:
                self.send({'id': value['id'], 'error': {'code': -32601, 'message': 'Request refused by lab controller'}})
                raise ProtocolError('Unexpected server request before turn')
            self.pending.append(value)

    def initialize(self):
        result = self.request('initialize', {'clientInfo': {'name': 'skill_lab', 'title': 'Skill Lab', 'version': '1.0.0'},
                                             'capabilities': {'experimentalApi': True}})
        self.send({'method': 'initialized', 'params': {}})
        return result

    def close(self):
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.process.wait(timeout=5)
        os.set_blocking(self.process.stderr.fileno(), False)
        while len(self.stderr) < MAX_PROTOCOL_BYTES:
            try:
                data = os.read(self.process.stderr.fileno(), min(65536, MAX_PROTOCOL_BYTES - len(self.stderr)))
            except BlockingIOError:
                break
            if not data:
                break
            self.stderr.extend(data)
        self.selector.close()
        for stream in [self.process.stdin, self.process.stdout, self.process.stderr]:
            stream.close()


def _prepare(host_dir, env):
    host_dir = Path(host_dir).resolve()
    host_dir.mkdir(parents=True, exist_ok=True)
    for name in ['tmp']:
        (host_dir / name).mkdir(exist_ok=True)
    return host_dir, env if env is not None else host_env(host_dir)


def preflight(host_dir, *, binary=CODEX, env=None, wall=30, disabled_mcp_servers=(), allowed_instruction_sources=None):
    """Read account type/model catalog through native auth; never start a turn."""
    started_at = time.monotonic()
    host_dir, env = _prepare(host_dir, env)
    client = None
    result = {'status': 'infrastructure_error', 'model_turns_started': 0, 'auth_type': None, 'models': [],
              'stage': 'startup', 'stages': []}
    def stage(name):
        result['stage'] = name
        result['stages'].append({'name': name, 'elapsed_seconds': round(time.monotonic() - started_at, 3)})
    try:
        verify_instructions(allowed_instruction_sources or {}, allowed_instruction_sources or {})
        client = Client(host_argv(host_dir, binary, disabled_mcp_servers=disabled_mcp_servers), env, host_dir, wall)
        stage('initialize')
        result['server'] = client.initialize()
        stage('configuration')
        result['configuration'] = inspect_config(client, host_dir)
        stage('account_read')
        account = client.request('account/read', {'refreshToken': False})
        result['auth_type'] = (account.get('account') or {}).get('type')
        result['requires_openai_auth'] = account.get('requiresOpenaiAuth')
        stage('model_list')
        cursor = None
        for _ in range(10):
            page = client.request('model/list', {'includeHidden': False, 'limit': 100, 'cursor': cursor})
            result['models'].extend({key: model.get(key) for key in ['id', 'model', 'displayName', 'supportedReasoningEfforts', 'defaultReasoningEffort', 'isDefault']} for model in page['data'])
            cursor = page.get('nextCursor')
            if cursor is None:
                break
        else:
            raise ProtocolError('Model catalog pagination exceeded bound')
        result['status'] = 'ready' if result['auth_type'] == 'chatgpt' and result['models'] else 'not_ready'
        stage('completed')
    except (OSError, ValueError, TimeoutError, KeyError) as error:
        result['error'] = str(error)
    finally:
        if client:
            # Account responses contain identifying information; deliberately do
            # not retain raw preflight events or account fields beyond type.
            result['protocol_events_count'] = len(client.events)
            result['process_exit_before_cleanup'] = client.process.poll()
            client.close()
            result['stderr'] = client.stderr.decode(errors='replace')
            result['process_exit_after_cleanup'] = client.process.returncode
        result['wall_seconds'] = round(time.monotonic() - started_at, 3)
    return result


def inspect_config(client, host_dir, provider_url=None):
    """Verify effective safety settings without persisting profile secrets."""
    config = client.request('config/read', {'includeLayers': False, 'cwd': str(host_dir)})['config']
    expected_provider = 'skill_lab_fixture' if provider_url else 'openai'
    if config.get('model_provider') != expected_provider:
        raise ProtocolError('Unexpected effective model provider')
    features = config.get('features', {})
    if any(features.get(name) is not False for name in DISABLED_FEATURES) or features.get('code_mode_host') is not True:
        raise ProtocolError('A required feature override was not applied')
    if (config.get('agents', {}).get('max_concurrent_threads_per_session') != 1
            or config.get('agents', {}).get('enabled') is not False or config.get('project_doc_max_bytes') != 0
            or config.get('approval_policy') != 'never' or config.get('sandbox_mode') != 'read-only'):
        safe = {key: config.get(key) for key in ['project_doc_max_bytes', 'approval_policy', 'sandbox_mode']}
        safe['max_threads'] = config.get('agents', {}).get('max_concurrent_threads_per_session')
        safe['agents_enabled'] = config.get('agents', {}).get('enabled')
        raise ProtocolError('Required host policy overrides were not applied: ' + json.dumps(safe))
    mcp_servers = config.get('mcp_servers') or {}
    if any(not isinstance(server, dict) or server.get('enabled') is not False for server in mcp_servers.values()):
        raise ProtocolError('Unselected MCP servers remain in effective configuration')
    if not provider_url and ((config.get('model_providers') or {}).get('openai')
                             or config.get('chatgpt_base_url') not in {None, 'https://chatgpt.com/backend-api/'}
                             or config.get('openai_base_url')):
        raise ProtocolError('Custom native provider endpoint settings are not supported')
    return {'provider': expected_provider, 'disabled_features': list(DISABLED_FEATURES),
            'disabled_mcp_servers': sorted(mcp_servers), 'project_doc_max_bytes': 0, 'max_threads': 1,
            'approval_policy': 'never', 'sandbox_mode': 'read-only'}


def verify_instructions(sources, allowed):
    """Only explicitly selected common instructions may enter both conditions."""
    actual = {str(Path(path).resolve()) for path in sources}
    if actual != set(allowed):
        raise ProtocolError('Unselected or missing instruction sources were loaded')
    for path, digest in allowed.items():
        raw = Path(path).read_bytes()
        if len(raw) > 32768 or hashlib.sha256(raw).hexdigest() != digest:
            raise ProtocolError('Pinned common instructions changed')


def run_host(work, receipts, origin, host_dir, prompt, model, wall=900, max_calls=32, *,
             binary=CODEX, env=None, provider_url=None, environmentless=True,
             runtime=ub_runtime.DEFAULT_RUNTIME, system_prompt=SYSTEM, token_limit=128000,
             allowed_instruction_sources=None, disabled_mcp_servers=()):
    started_at = time.monotonic()
    if (type(max_calls) is not int or not 1 <= max_calls <= 128 or type(token_limit) is not int or token_limit <= 0
            or not isinstance(wall, (int, float)) or not 0 < wall <= 3600):
        raise ValueError('Invalid bounded Codex run limits')
    host_dir, env = _prepare(host_dir, env)
    if provider_url is not None and (env.get('HOME') != '/home/probe' or env.get('CODEX_HOME') != '/home/probe/.codex'):
        raise ValueError('Fake provider requires the authored fake-profile namespace')
    result = {'status': 'infrastructure_error', 'exit_code': None, 'output_truncated': False,
              'raw_events': [], 'requests': [], 'model': model,
              'limits': {'wall_seconds': wall, 'dynamic_calls': max_calls, 'protocol_bytes': MAX_PROTOCOL_BYTES,
                         'observed_tokens': token_limit, 'max_threads': 1},
              'environmentless': environmentless}
    client = server = None
    try:
        allowed = allowed_instruction_sources or {}
        verify_instructions(allowed, allowed)
        client = Client(host_argv(host_dir, binary, provider_url, disabled_mcp_servers), env, host_dir, wall)
        result['server'] = client.initialize()
        result['configuration'] = inspect_config(client, host_dir, provider_url)
        started = client.request('thread/start', thread_params(host_dir, model, environmentless, system_prompt))
        result['thread'] = started
        verify_instructions(started.get('instructionSources', []), allowed)
        result['common_instruction_sha256'] = dict(allowed)
        if started.get('model') != model:
            raise ProtocolError('Codex changed the requested model')
        thread_id = started['thread']['id']
        server = lab_mcp.Server(work, receipts, origin, runtime, max_calls=max_calls, wall=min(120, wall))
        params = {'threadId': thread_id, 'input': [{'type': 'text', 'text': prompt}], 'effort': 'low'}
        if environmentless:
            params['environments'] = []
        turn = client.request('turn/start', params)['turn']
        turn_id = turn['id']
        while True:
            event = client.pending.pop(0) if client.pending else client.receive()
            method, params = event.get('method'), event.get('params', {})
            if 'id' in event:
                if method != 'item/tool/call' or params.get('namespace') != 'skill_lab' or params.get('threadId') != thread_id or params.get('turnId') != turn_id:
                    client.send({'id': event['id'], 'error': {'code': -32601, 'message': 'Request refused by lab controller'}})
                    raise ProtocolError('Unexpected server tool or approval request')
                if server.calls >= max_calls:
                    raise ProtocolError('Dynamic tool call limit exhausted')
                left = client.deadline - time.monotonic()
                if left <= 0.03:
                    raise TimeoutError('Codex wall limit exhausted before tool execution')
                # A call has before/after snapshots plus execution, each bounded
                # by Server.wall. Reserve time for all three phases.
                server.wall = min(120, left / 3)
                response = server.call(params.get('tool'), params.get('arguments'))
                client.send({'id': event['id'], 'result': {'success': not response['isError'],
                              'contentItems': [{'type': 'inputText', 'text': item['text']} for item in response['content']]}})
            elif method == 'thread/tokenUsage/updated':
                total = params.get('tokenUsage', {}).get('total', {}).get('totalTokens')
                if isinstance(total, int) and total > token_limit:
                    raise ProtocolError('Observed token limit exhausted; one response may overshoot')
            elif method == 'turn/completed' and params.get('threadId') == thread_id:
                if params['turn']['id'] != turn_id or params['turn']['status'] != 'completed':
                    raise ProtocolError('Native turn did not complete successfully: ' + str(params['turn'].get('error')))
                result['status'], result['exit_code'] = 'completed', 0
                break
    except (OSError, ValueError, TimeoutError, KeyError) as error:
        result['error'] = str(error)
    finally:
        if server:
            server.close()
        if client:
            client.close()
            result.update(raw_events=client.events, requests=client.sent, stderr=client.stderr.decode(errors='replace'),
                          process_exit_after_cleanup=client.process.returncode)
    result['wall_seconds'] = round(time.monotonic() - started_at, 3)
    if result['status'] == 'completed':
        try:
            result['normalized'] = parse_host(result, model)
        except ValueError as error:
            result['status'] = 'infrastructure_error'
            result['error'] = str(error)
    return result


def parse_host(receipt, model):
    """Normalize only completed native evidence; model claims cannot pass this."""
    if receipt.get('status') != 'completed' or receipt.get('model') != model:
        raise ValueError('Codex host did not complete the requested model run')
    events = receipt.get('raw_events', [])
    completions = [e['params']['turn'] for e in events if e.get('method') == 'turn/completed']
    messages = [e['params']['item'].get('text', '') for e in events if e.get('method') == 'item/completed'
                and e['params']['item'].get('type') == 'agentMessage']
    if len(completions) != 1 or completions[0].get('status') != 'completed' or not messages or not messages[-1].strip():
        raise ValueError('Missing actual completed turn and nonempty agent output')
    calls = [{'name': e['params']['tool'], 'input': e['params']['arguments']} for e in events
             if e.get('method') == 'item/tool/call' and e['params'].get('namespace') == 'skill_lab']
    usage = [e['params']['tokenUsage'] for e in events if e.get('method') == 'thread/tokenUsage/updated']
    responses = [e['params']['responseId'] for e in events if e.get('method') == 'rawResponse/completed']
    return {'answer': messages[-1], 'calls': calls, 'num_turns': len(completions),
            'model_responses': len(set(responses)),
            'usage': usage[-1] if usage else None,
            'session_id': receipt.get('thread', {}).get('thread', {}).get('id')}
