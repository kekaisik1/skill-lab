#!/usr/bin/env python3
"""Small MCP stdio server: trusted configuration, namespace-only candidate tools."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import time

import ub_runtime

SERVER_NAME = 'skill_lab'
HOST_PYTHON = Path('/usr/bin/python3')
MAX_FRAME = 131072
PROTOCOL_VERSION = '2024-11-05'
TOOLS = [
    {'name': 'shell', 'description': 'Run a shell command in the isolated candidate workspace /work. No network or host home is mounted.',
     'inputSchema': {'type': 'object', 'properties': {'script': {'type': 'string', 'minLength': 1, 'maxLength': 8192},
                                                    'reason': {'type': 'string', 'minLength': 1, 'maxLength': 2048}},
                     'required': ['script', 'reason'], 'additionalProperties': False}},
    {'name': 'quality', 'description': 'Run the fixed unittest command (tests) or an original pinned UB companion against /work/src. The controller fixes its arguments and records the actual output.',
     'inputSchema': {'type': 'object', 'properties': {'tool': {'type': 'string', 'enum': ['tests', 'crapper', 'dryer', 'mutator']},
                                                    'reason': {'type': 'string', 'minLength': 1, 'maxLength': 2048}},
                     'required': ['tool', 'reason'], 'additionalProperties': False}},
    {'name': 'read_file', 'description': 'Read a UTF-8 file relative to /work. Absolute paths, traversal and symlinks are rejected.',
     'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'maxLength': 1024}},
                     'required': ['path'], 'additionalProperties': False}},
    {'name': 'write_file', 'description': 'Write a UTF-8 file relative to /work. Absolute paths, traversal and symlinks are rejected.',
     'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string', 'minLength': 1, 'maxLength': 1024},
                                                    'content': {'type': 'string', 'maxLength': 32768}},
                     'required': ['path', 'content'], 'additionalProperties': False}},
]
TOOL_NAMES = [f'mcp__{SERVER_NAME}__{tool["name"]}' for tool in TOOLS]
QUALITY = {
    'tests': ['discover', '-s', '/work/tests', '-t', '/work', '-v'],
    'crapper': ['--root', '/work', '--threshold', '10', '/work/src'],
    'dryer': ['--root', '/work', '--threshold', '0.8', '/work/src'],
    'mutator': ['--root', '/work', '--mutate-all', '--max-workers', '1', '/work/src'],
}
# This script executes inside the namespace. Directory descriptors prevent a
# symlink planted by candidate code from escaping the intended relative path.
FILE_PROGRAM = '''import json,os,sys
p=json.load(sys.stdin)
parts=p['path'].split('/')
fd=os.open('/work',os.O_RDONLY|os.O_DIRECTORY)
try:
 for part in parts[:-1]:
  if p['operation']=='write_file':
   try:os.mkdir(part,dir_fd=fd)
   except FileExistsError:pass
  child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
  os.close(fd);fd=child
 flags=os.O_NOFOLLOW|(os.O_RDONLY if p['operation']=='read_file' else os.O_WRONLY|os.O_CREAT|os.O_TRUNC)
 target=os.open(parts[-1],flags,0o600,dir_fd=fd)
 with os.fdopen(target,'r' if p['operation']=='read_file' else 'w',encoding='utf-8') as stream:
  if p['operation']=='read_file':
   value=stream.read(32769)
   if len(value)>32768:raise ValueError('File exceeds read bound')
   print(value,end='')
  else:
   stream.write(p['content']);stream.flush();os.fsync(stream.fileno())
   print(json.dumps({'path':p['path'],'bytes_written':len(p['content'].encode())}))
finally:os.close(fd)
'''
SNAPSHOT_PROGRAM = '''import hashlib,json,os,stat
files={};total=0
for root in ('src','tests'):
 if os.path.islink(root):raise ValueError('Snapshot root is a symlink')
 for directory,dirs,names in os.walk(root,followlinks=False):
  dirs.sort();names.sort()
  for name in names+list(dirs):
   path=os.path.join(directory,name);info=os.lstat(path)
   if stat.S_ISLNK(info.st_mode):
    files[path]={'kind':'symlink'}
   elif stat.S_ISREG(info.st_mode):
    if info.st_size>262144:raise ValueError('Snapshot file exceeds 256KiB')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:data=stream.read(262145)
    total+=len(data)
    if len(data)>262144 or total>1048576:raise ValueError('Snapshot exceeds 1MiB')
    files[path]={'kind':'file','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
   elif not stat.S_ISDIR(info.st_mode):raise ValueError('Snapshot contains a special file')
   if len(files)>128:raise ValueError('Snapshot exceeds 128 entries')
print(json.dumps(files,sort_keys=True))
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def write_json(path, value):
    with Path(path).open('xb') as stream:
        stream.write(encoded(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())


def server_argv(work, receipts, origin=None, runtime=ub_runtime.DEFAULT_RUNTIME, max_calls=32, wall=120):
    command = [str(HOST_PYTHON), '-E', '-S', '-B', str(Path(__file__).resolve()), '--work', str(Path(work).resolve()),
               '--receipts', str(Path(receipts).resolve()), '--runtime', str(Path(runtime).resolve()),
               '--max-calls', str(max_calls), '--wall', str(wall)]
    if origin is not None:
        command += ['--origin', str(Path(origin).resolve())]
    return command


def mcp_config(work, receipts, origin=None, runtime=ub_runtime.DEFAULT_RUNTIME, max_calls=32, wall=120):
    argv = server_argv(work, receipts, origin, runtime, max_calls, wall)
    return {'mcpServers': {SERVER_NAME: {'command': argv[0], 'args': argv[1:]}}}


def host_argv(binary, config, model='claude-sonnet-4-6', system_prompt='Use the isolated skill lab tools to complete the task.', max_turns=12):
    """Explicit MCP only; safe-mode is intentionally omitted because it hides MCP."""
    if type(max_turns) is not int or not 1 <= max_turns <= 64:
        raise ValueError('Model turn limit must be an integer within 1..64')
    return [str(binary), '--print', '--tools', '', '--strict-mcp-config', '--mcp-config', json.dumps(config),
            '--restricted', '--disable-slash-commands', '--no-chrome', '--no-session-persistence',
            '--permission-mode', 'dontAsk', '--permission-prompts', 'none', '--allowedTools', ','.join(TOOL_NAMES),
            '--output-format', 'stream-json', '--verbose', '--setting-sources', '',
            '--settings', '{"disableAllHooks":true,"enableAllProjectMcpServers":false,"autoMemoryEnabled":false,"claudeMdExcludes":["/**"]}',
            '--max-turns', str(max_turns), '--model', model, '--system-prompt', system_prompt]


class Server:
    def __init__(self, work, receipts, origin=None, runtime=ub_runtime.DEFAULT_RUNTIME, max_calls=32, wall=120):
        self.work, self.receipts, self.runtime = (Path(p).resolve() for p in (work, receipts, runtime))
        self.origin = Path(origin).resolve() if origin is not None else None
        if not self.work.is_dir() or not 1 <= max_calls <= 128 or not 0 < wall <= 300:
            raise ValueError('Invalid fixed workspace or resource limits')
        paths = [self.work, self.receipts, self.runtime] + ([self.origin] if self.origin else [])
        for i, path in enumerate(paths):
            for other in paths[i + 1:]:
                if path == other or path in other.parents or other in path.parents:
                    raise ValueError('Workspace, receipts, runtime and origin must not overlap')
        if self.origin and not self.origin.is_dir():
            raise ValueError('Missing controller origin')
        self.max_calls, self.wall, self.calls, self.initialized = max_calls, wall, 0, False
        self.receipts.mkdir(parents=True, exist_ok=True)
        self.audit = (self.receipts / 'audit.jsonl').open('xb')
        self.record({'event': 'startup', 'server_sha256': sha(Path(__file__).read_bytes()),
                     'runtime_manifest_sha256': sha((self.runtime / 'runtime.json').read_bytes()),
                     'work': str(self.work), 'origin': str(self.origin) if self.origin else None,
                     'runtime': str(self.runtime), 'max_calls': max_calls, 'wall': wall,
                     'limits': ub_runtime.LIMITS, 'tools': TOOL_NAMES})
        ub_runtime.validate(self.runtime)
        self.record({'event': 'runtime_validated'})

    def record(self, value):
        self.audit.write(encoded({**value, 'time_ns': time.time_ns()}) + b'\n')
        self.audit.flush()
        os.fsync(self.audit.fileno())

    def close(self):
        self.audit.close()

    def arguments(self, name, args):
        tool = next((item for item in TOOLS if item['name'] == name), None)
        if not tool or not isinstance(args, dict):
            raise ValueError('Unknown tool or arguments not an object')
        schema = tool['inputSchema']
        if set(args) != set(schema['required']):
            raise ValueError('Missing or unexpected tool arguments')
        for key, value in args.items():
            rule = schema['properties'][key]
            if not isinstance(value, str) or '\x00' in value:
                raise ValueError('Tool arguments must be strings without NUL')
            if len(value) < rule.get('minLength', 0) or len(value) > rule.get('maxLength', 32768):
                raise ValueError('Tool argument exceeds length bounds')
            if 'enum' in rule and value not in rule['enum']:
                raise ValueError('Unknown pinned quality tool')
        if 'path' in args:
            path = args['path']
            if PurePosixPath(path).is_absolute() or any(p in ('', '.', '..') for p in path.split('/')) or '\\' in path:
                raise ValueError('Expected a relative workspace path without traversal')

    def call(self, name, args):
        self.calls += 1
        index = self.calls
        self.record({'event': 'attempt', 'index': index, 'tool': name, 'arguments': args})
        before, after = None, None
        try:
            if index > self.max_calls:
                raise ValueError('Tool call limit exhausted')
            self.arguments(name, args)
            stdin = b''
            if name == 'shell':
                command = ['/usr/bin/bash', '-euo', 'pipefail', '-c', args['script']]
            elif name == 'quality':
                # -P prevents current-directory module substitution. Runtime
                # code and dependencies are read-only; candidate code is not.
                module = 'unittest' if args['tool'] == 'tests' else args['tool']
                command = ['/runtime/bin/python3', '-P', '-m', module, *QUALITY[args['tool']]]
            else:
                command = ['/runtime/bin/python3', '-P', '-c', FILE_PROGRAM]
                stdin = encoded({'operation': name, **args})
            before = self.snapshot()
            if before['status'] != 'completed':
                raise ValueError('Before snapshot failed; candidate execution refused')
            outcome = ub_runtime.bounded(ub_runtime.sandbox_command(self.work, command, self.origin, self.runtime),
                                         wall=self.wall, stdin=stdin)
            if name == 'quality' and args['tool'] == 'tests' and outcome['status'] in {'completed', 'process_error'}:
                counts = re.findall(r'^Ran ([0-9]+) tests? in ', outcome['stderr'], re.MULTILINE)
                skipped = re.search(r'\bskipped=[1-9][0-9]*\b', outcome['stderr'])
                if len(counts) != 1 or int(counts[0]) == 0 or skipped:
                    outcome = {**outcome, 'execution_status': outcome['status'], 'status': 'invalid_test_run',
                               'validation_error': 'Expected one nonempty unittest run with no skipped cases'}
            after = self.snapshot()
            if after['status'] != 'completed':
                outcome = {**outcome, 'execution_status': outcome['status'], 'status': 'snapshot_error'}
        except (ValueError, OSError) as error:
            outcome = {'status': 'rejected', 'exit_code': None, 'stdout': '', 'stderr': str(error)}
        receipt = {'index': index, 'tool': name, 'arguments': args, 'before': before, 'after': after, 'outcome': outcome}
        known = isinstance(name, str) and name in {t['name'] for t in TOOLS}
        path = self.receipts / f'{index:04d}-{name if known else "unknown"}.json'
        write_json(path, receipt)
        digest = sha(path.read_bytes())
        self.record({'event': 'result', 'index': index, 'status': outcome['status'], 'receipt': path.name, 'sha256': digest})
        summary = {'receipt': path.name, 'sha256': digest,
                   **{key: value for key, value in outcome.items() if key != 'command'}}
        for key in ('stdout', 'stderr'):
            if len(summary.get(key, '')) > 16000:
                summary[key] = summary[key][:16000]
                summary[key + '_truncated_in_tool_response'] = True
        return {'content': [{'type': 'text', 'text': json.dumps(summary, sort_keys=True)}],
                'isError': outcome['status'] != 'completed' or outcome['exit_code'] != 0}

    def snapshot(self):
        """Hash bounded source/tests inside the same namespace as execution."""
        outcome = ub_runtime.bounded(ub_runtime.sandbox_command(
            self.work, ['/runtime/bin/python3', '-P', '-c', SNAPSHOT_PROGRAM], self.origin, self.runtime), wall=min(self.wall, 15))
        if outcome['status'] != 'completed' or outcome['exit_code'] != 0:
            return {'status': 'snapshot_error', 'outcome': outcome}
        try:
            return {'status': 'completed', 'files': json.loads(outcome['stdout'])}
        except ValueError:
            return {'status': 'snapshot_error', 'outcome': outcome}

    def dispatch(self, request):
        if not isinstance(request, dict) or request.get('jsonrpc') != '2.0' or not isinstance(request.get('method'), str):
            return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid request'}}
        method, request_id = request['method'], request.get('id')
        if 'id' not in request:
            if method == 'notifications/initialized':
                self.initialized = True
            return None
        try:
            params = request.get('params', {})
            if not isinstance(params, dict):
                raise ValueError('Expected object params')
            if method == 'initialize':
                result = {'protocolVersion': PROTOCOL_VERSION, 'capabilities': {'tools': {'listChanged': False}},
                          'serverInfo': {'name': SERVER_NAME, 'version': '1.0.0'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': TOOLS}
            elif method == 'tools/call':
                result = self.call(params.get('name'), params.get('arguments', {}))
            else:
                return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32601, 'message': 'Method not found'}}
            return {'jsonrpc': '2.0', 'id': request_id, 'result': result}
        except ValueError as error:
            return {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32602, 'message': str(error)}}


def strict_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key] = item
    return value


def serve(server, reader, writer):
    while True:
        line = reader.readline(MAX_FRAME + 1)
        if not line:
            return
        if len(line) > MAX_FRAME:
            raise ValueError('MCP frame exceeds bound')
        try:
            request = json.loads(line, object_pairs_hook=strict_pairs,
                                 parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
            response = server.dispatch(request)
        except (ValueError, UnicodeDecodeError):
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        if response is not None:
            writer.write(encoded(response) + b'\n')
            writer.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True)
    parser.add_argument('--receipts', required=True)
    parser.add_argument('--origin')
    parser.add_argument('--runtime', default=str(ub_runtime.DEFAULT_RUNTIME))
    parser.add_argument('--max-calls', type=int, default=32)
    parser.add_argument('--wall', type=float, default=120)
    args = parser.parse_args()
    server = Server(**vars(args))
    try:
        serve(server, sys.stdin.buffer, sys.stdout.buffer)
    finally:
        server.close()


if __name__ == '__main__':
    main()
