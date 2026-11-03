import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('platform_launcher', ROOT / 'skill-lab/scripts/launcher.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class PlatformLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.skill = Path(self.temporary.name).resolve() / 'skill-lab'
        (self.skill / 'scripts').mkdir(parents=True)
        (self.skill / 'installation.json').write_text(json.dumps({'schema': 'skill-lab-installation/2', 'project_root': 'kit'}))
        scripts = self.skill / 'kit/skill-evaluator/scripts'
        scripts.mkdir(parents=True)
        (scripts / 'preview.py').write_text('raise RuntimeError("Linux imports must not load")')
        (scripts / 'preview_intake.py').write_text('print("native intake")')
        self.script = scripts / 'preview_intake.py'
        self.patcher = patch.object(launcher, '__file__', str(self.skill / 'scripts/launcher.py'))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_windows_and_macos_intake_stays_native_before_guest_route(self):
        (self.skill / 'platform.json').write_text('bad config ignored for local intake')
        for host in ('Windows', 'Darwin'):
            for operation in ('prepare', 'example'):
                with self.subTest(host=host, operation=operation):
                    with patch.object(launcher.platform, 'system', return_value=host):
                        with patch.object(launcher.subprocess, 'call', return_value=0) as call:
                            self.assertEqual(launcher.main([operation, '--out', 'selected-local-output']), 0)
                            self.assertEqual(call.call_args.args[0], [sys.executable, str(self.script), operation, '--out', 'selected-local-output'])

    def test_unconfigured_nonlinux_execution_stops_before_backend(self):
        for host in ('Windows', 'Darwin'):
            with patch.object(launcher.platform, 'system', return_value=host), patch.object(launcher, 'backend') as backend:
                with contextlib.redirect_stderr(io.StringIO()) as output:
                    self.assertEqual(launcher.main(['run', 'offline', '--out', '/tmp/test']), 2)
                backend.assert_not_called()
                self.assertIn('existing WSL2/Linux or Lima/Linux guest', output.getvalue())

    def test_explicit_route_preserves_failure_code_without_loading_linux_backend(self):
        (self.skill / 'platform.json').write_text('{}')
        fake = SimpleNamespace(load=lambda path: {'route': 'selected'}, dispatch=lambda config, arguments: 17)
        with patch.dict(sys.modules, {'platform_bridge': fake}), patch.object(launcher.platform, 'system', return_value='Darwin'):
            with patch.object(launcher, 'backend') as backend:
                self.assertEqual(launcher.main(['report', '/guest/run']), 17)
                backend.assert_not_called()

    def test_missing_native_intake_is_actionable(self):
        self.script.unlink()
        with patch.object(launcher.platform, 'system', return_value='Windows'):
            with contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(launcher.main(['example', '--out', 'tmp']), 2)
            self.assertIn('Portable intake is missing', output.getvalue())
