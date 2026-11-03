import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skill-evaluator/scripts'))
import preview_environment as environment
import ub_runtime


class PreviewEnvironmentTests(unittest.TestCase):
    def test_setup_is_offline_without_codex_and_never_replaces_pins(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'state'
            with patch.object(environment, 'discover_codex', side_effect=ValueError('not installed')):
                receipt = environment.setup(state)
            self.assertEqual(receipt['status'], 'configured')
            config = environment.load(state)
            self.assertIsNone(config['codex'])
            self.assertEqual(config['api_allowance_usd'], 0)
            self.assertFalse(config['downloads'])
            with self.assertRaisesRegex(ValueError, 'fresh state'):
                environment.setup(state)
            with (state / 'runtime/python/bin/python3').open('a') as stream:
                stream.write('# changed\n')
            with self.assertRaisesRegex(ValueError, 'changed'):
                environment.load(state)

    def test_runtime_has_no_copied_host_tree_or_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp) / 'runtime'
            environment.runtime_create(runtime)
            data = ub_runtime.validate(runtime)
            self.assertEqual(set(data['files']), {'python/bin/python', 'python/bin/python3'})
            self.assertEqual(data['resources'], [])
            command = ub_runtime.sandbox_command(temp, ['python3', '-c', 'print(1)'], runtime=runtime)
            self.assertIn('--unshare-all', command)
            self.assertNotIn(str(Path.home()), command)

    def test_symlink_state_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'link').symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'Symlink'):
                environment.setup(root / 'link/state')

    def test_explicit_missing_codex_fails_without_partial_state(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'state'
            with self.assertRaisesRegex(ValueError, 'missing'):
                environment.setup(state, codex=Path(temp) / 'missing')
            self.assertFalse(state.exists())

    def test_launcher_cannot_masquerade_as_native_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'codex'
            path.write_text('#!/bin/sh\nexit 0\n')
            path.chmod(0o755)
            with self.assertRaisesRegex(ValueError, 'native Linux ELF'):
                environment.discover_codex(path)

    def test_changed_helper_pin_fails_and_doctor_retains_action(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'state'
            with patch.object(environment, 'discover_codex', side_effect=ValueError('not installed')):
                environment.setup(state)
            with patch.object(environment, 'pins', return_value={'changed': 'digest'}):
                report = environment.doctor(state)
            self.assertFalse(report['offline_ready'])
            self.assertFalse(report['live_ready'])
            self.assertIn('automatic repinning is forbidden', report['error'])

    def test_namespace_failure_does_not_become_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'state'
            with patch.object(environment, 'discover_codex', side_effect=ValueError('not installed')):
                environment.setup(state)
            with patch('fixture_runner.bounded', return_value={'status': 'process_error', 'stdout': '', 'stderr': 'namespace denied'}):
                report = environment.doctor(state)
            self.assertEqual(report['status'], 'incomplete')
            self.assertEqual(report['namespace']['stderr'], 'namespace denied')
            self.assertIn('do not bypass isolation', report['error'])


@unittest.skipUnless(os.environ.get('SKILL_EVAL_ISOLATION_TESTS') == '1', 'requires enabled namespace integration')
class PreviewEnvironmentIsolationTests(unittest.TestCase):
    def test_minimal_runtime_actual_security_controls(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / 'state'
            with patch.object(environment, 'discover_codex', side_effect=ValueError('not installed')):
                environment.setup(state)
            self.assertEqual(environment.doctor(state)['status'], 'pass')
            report = environment.validate_boundary(state, Path(temp) / 'boundary')
            self.assertEqual(report['status'], 'offline_only')
            self.assertEqual(report['security'], 'pass')
            controls = json.loads((Path(temp) / 'boundary/security/report.json').read_text())
            self.assertTrue(controls['hostile']['passed'])
            self.assertTrue(controls['known_unsafe']['actual_host_read'])
            self.assertFalse(controls['known_unsafe']['assessment_passed'])
