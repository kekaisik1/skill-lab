import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill-evaluator/scripts'))
import codex_host as host


def completed():
    return {'status': 'completed', 'model': 'fixture', 'thread': {'thread': {'id': 'thread'}},
            'raw_events': [
                {'id': 1, 'method': 'item/tool/call', 'params': {'namespace': 'skill_lab', 'tool': 'read_file',
                                                             'arguments': {'path': 'output.txt'}}},
                {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'actual output'}}},
                {'method': 'turn/completed', 'params': {'turn': {'id': 'turn', 'status': 'completed'}}}]}


class CodexHostTests(unittest.TestCase):
    def test_common_instructions_are_exact_and_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'AGENTS.md'
            path.write_text('Authored common context')
            allowed = {str(path): hashlib.sha256(path.read_bytes()).hexdigest()}
            host.verify_instructions([str(path)], allowed)
            with self.assertRaisesRegex(ValueError, 'Unselected'):
                host.verify_instructions([str(path), '/unselected/AGENTS.md'], allowed)
            path.write_text('Different context')
            with self.assertRaisesRegex(ValueError, 'changed'):
                host.verify_instructions([str(path)], allowed)

    def test_profile_response_contents_are_not_retained_in_events(self):
        program = ('import sys,json,time; q=json.loads(sys.stdin.readline()); '
                   'print(json.dumps({"id":q["id"],"result":{"account":{"type":"chatgpt",'
                   '"email":"AUTHORED_PRIVATE_FIELD"}}}),flush=True); time.sleep(5)')
        with tempfile.TemporaryDirectory() as directory:
            client = host.Client(['/usr/bin/python3', '-c', program], {}, directory, 1)
            try:
                response = client.request('account/read', {'refreshToken': False})
                self.assertEqual(response['account']['type'], 'chatgpt')
                self.assertNotIn('AUTHORED_PRIVATE_FIELD', json.dumps(client.events))
                self.assertEqual(client.events[-1], {'id': 1000, 'redacted': True})
            finally:
                client.close()

    def test_empty_failed_incomplete_and_self_claims_are_rejected(self):
        self.assertEqual(host.parse_host(completed(), 'fixture')['answer'], 'actual output')
        bad = [dict(completed(), status='infrastructure_error'), dict(completed(), raw_events=[]),
               dict(completed(), model='other')]
        for status in ['inProgress', 'interrupted', 'failed']:
            value = copy.deepcopy(completed())
            value['raw_events'][-1]['params']['turn']['status'] = status
            bad.append(value)
        value = copy.deepcopy(completed())
        value['raw_events'][1]['params']['item']['text'] = ' '
        bad.append(value)
        value = copy.deepcopy(completed())
        value['raw_events'] = value['raw_events'][:-1]
        bad.append(value)
        for receipt in bad:
            with self.subTest(receipt=receipt), self.assertRaises(ValueError):
                host.parse_host(receipt, 'fixture')

    def test_environmentless_explicit_tools_and_no_model_fallback(self):
        params = host.thread_params('/tmp/controller', 'fixture', system_prompt='selected task only')
        self.assertEqual(params['environments'], [])
        self.assertFalse(params['allowProviderModelFallback'])
        self.assertEqual(params['baseInstructions'], 'selected task only')
        self.assertEqual(params['selectedCapabilityRoots'], [])
        self.assertEqual([tool['name'] for tool in params['dynamicTools'][0]['tools']],
                         ['shell', 'quality', 'read_file', 'write_file'])
        self.assertNotIn('environments', host.thread_params('/tmp/controller', 'fixture', environmentless=False))

    def test_api_keys_and_overrides_removed_but_host_policy_preserved(self):
        with patch.dict(os.environ, {'HOME': '/native', 'OPENAI_API_KEY': 'do-not-copy',
                                     'OPENAI_BASE_URL': 'do-not-use', 'CODEX_SANDBOX_NETWORK_DISABLED': '1'}, clear=True):
            env = host.host_env('/tmp/host')
        self.assertEqual(env['CODEX_HOME'], '/native/.codex')
        self.assertEqual(env['CODEX_SANDBOX_NETWORK_DISABLED'], '1')
        self.assertNotIn('OPENAI_API_KEY', env)
        self.assertNotIn('OPENAI_BASE_URL', env)
        self.assertNotIn('CODEX_SQLITE_HOME', env)

    def test_fixture_provider_cannot_use_native_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'fake-profile'):
                host.run_host('/unused', '/unused', '/unused', directory, 'unused', 'fixture',
                              provider_url='http://127.0.0.1:1234/v1')
        with self.assertRaisesRegex(ValueError, 'loopback'):
            host.host_argv('/tmp/host', provider_url='https://example.invalid')

    def test_unresponsive_process_has_a_real_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            client = host.Client(['/usr/bin/python3', '-c', 'import time; time.sleep(10)'], {}, directory, 0.1)
            try:
                with self.assertRaises(TimeoutError):
                    client.receive()
            finally:
                client.close()
        self.assertIsNotNone(client.process.returncode)

    def test_blocked_stdin_has_a_real_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            client = host.Client(['/usr/bin/python3', '-c', 'import time; time.sleep(10)'], {}, directory, 0.1)
            try:
                with self.assertRaises(TimeoutError):
                    client.send({'payload': 'x' * 200000})
            finally:
                client.close()


if __name__ == '__main__':
    unittest.main()
