import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('platform_bridge', ROOT / 'skill-lab/scripts/platform_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def config(provider='wsl'):
    return {'schema': bridge.SCHEMA, 'provider': provider, 'guest': 'skill-lab',
            'guest_python': '/usr/bin/python3', 'guest_launcher': '/home/lab/skill-lab/scripts/launcher.py',
            'guest_state': '/home/lab/state'}


class PlatformBridgeTests(unittest.TestCase):
    def test_wsl_exact_argv_preserves_metacharacters_as_data(self):
        args = ['report', '/home/lab/my report;$(touch SHOULD_NOT_EXIST)']
        result = bridge.command_plan(config(), args, host_platform='Windows', executable='wsl.exe')
        self.assertEqual(result['argv'], ['wsl.exe', '--distribution', 'skill-lab', '--cd', '/', '--exec',
                         '/usr/bin/python3', '/home/lab/skill-lab/scripts/launcher.py', *args])
        self.assertIs(result['shell'], False)
        self.assertEqual(result['host_paths_mounted'], [])
        self.assertIs(result['credentials_copied'], False)

    def test_lima_uses_explicit_guest_and_guest_workdir(self):
        result = bridge.command_plan(config('lima'), ['list'], host_platform='Darwin', executable='/usr/local/bin/limactl')
        self.assertEqual(result['argv'], ['/usr/local/bin/limactl', 'shell', '--workdir=/', 'skill-lab', '--',
                         '/usr/bin/python3', '/home/lab/skill-lab/scripts/launcher.py', 'list'])

    def test_wrong_os_missing_tool_and_arbitrary_operations_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'requires Windows'):
            bridge.command_plan(config(), ['list'], host_platform='Linux', executable='wsl.exe')
        with patch.object(bridge.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                bridge.command_plan(config(), ['list'], host_platform='Windows')
        with self.assertRaisesRegex(ValueError, 'explicit Skill Lab operation'):
            bridge.command_plan(config(), ['sh', '-c', 'echo BAD'], host_platform='Windows', executable='wsl.exe')

    def test_invalid_names_paths_extra_fields_and_shell_interpreter_rejected(self):
        mutations = [('guest', '--evil'), ('guest', 'name;touch bad'),
                     ('guest_python', '/bin/sh'), ('guest_launcher', 'C:\\host\\launcher.py'),
                     ('guest_launcher', '/home/lab/../launcher.py'), ('guest_state', '/tmp/../state'),
                     ('guest_state', '/tmp/state\nBAD'), ('credentials', 'copy')]
        for key, value in mutations:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                bridge.validate({**config(), key: value})

    def test_configure_is_fresh_and_load_rejects_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / 'platform.json'
            bridge.configure(path, 'wsl', 'skill-lab', '/home/lab/scripts/launcher.py', '/home/lab/state')
            self.assertEqual(bridge.load(path)['provider'], 'wsl')
            with self.assertRaises(FileExistsError):
                bridge.configure(path, 'wsl', 'another', '/home/lab/scripts/launcher.py', '/home/lab/state')
            path.write_text('{"schema":"a", "schema":"b"}')
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                bridge.load(path)

    def test_dispatch_preserves_guest_exit_status_and_never_uses_shell(self):
        with patch.object(bridge.platform, 'system', return_value='Windows'), patch.object(bridge.shutil, 'which', return_value='wsl.exe'):
            with patch.object(bridge, 'running_precheck'), patch.object(bridge.subprocess, 'call', return_value=7) as call:
                self.assertEqual(bridge.dispatch(config(), ['report', '/tmp/a']), 7)
                self.assertFalse(call.call_args.kwargs['shell'])
                self.assertEqual(call.call_args.args[0][-2:], ['report', '/tmp/a'])

    def test_doctor_needs_actual_guest_readiness_not_exit_zero_claim(self):
        good = {'schema': 'skill-lab-preview-doctor/1', 'status': 'pass', 'offline_ready': True, 'real_model_calls': 0}
        with patch.object(bridge.platform, 'system', return_value='Windows'), patch.object(bridge.shutil, 'which', return_value='wsl.exe'):
            for body, expected in [(good, 'pass'), ({'success': True}, 'incomplete'), ({**good, 'offline_ready': False}, 'incomplete')]:
                with patch.object(bridge, 'running_precheck'), patch.object(bridge.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(body), '')) as run:
                    result = bridge.doctor(config())
                    self.assertEqual(result['status'], expected)
                    self.assertFalse(result['live_ready'])
                    self.assertFalse(run.call_args.kwargs['shell'])

    def test_doctor_infrastructure_failure_is_not_platform_support(self):
        with patch.object(bridge.platform, 'system', return_value='Darwin'), patch.object(bridge.shutil, 'which', return_value='limactl'):
            with patch.object(bridge.subprocess, 'run', side_effect=OSError('VM unavailable')):
                result = bridge.doctor(config('lima'))
                self.assertEqual(result['status'], 'incomplete')
                self.assertIn('VM unavailable', result['error'])
                self.assertFalse(result['offline_ready'])

    def test_wsl_stopped_guest_refused_before_dispatch_and_environment_not_forwarded(self):
        planned = bridge.command_plan(config(), ['list'], host_platform='Windows', executable='wsl.exe')
        with patch.object(bridge.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'other\n', b'')) as run:
            with self.assertRaisesRegex(ValueError, 'not running'):
                bridge.running_precheck(config(), planned)
            self.assertEqual(run.call_args.kwargs['env']['WSLENV'], '')
        for names in (b'skill-lab\n', 'skill-lab\r\n'.encode('utf-16-le')):
            with patch.object(bridge.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, names, b'')):
                bridge.running_precheck(config(), planned)

    def test_bridge_imports_without_unix_resource(self):
        with patch.dict('sys.modules', {'resource': None}):
            isolated_spec = importlib.util.spec_from_file_location('bridge_without_resource', ROOT / 'skill-lab/scripts/platform_bridge.py')
            isolated = importlib.util.module_from_spec(isolated_spec)
            isolated_spec.loader.exec_module(isolated)
            self.assertEqual(isolated.SCHEMA, bridge.SCHEMA)
