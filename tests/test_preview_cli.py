import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skill-evaluator/scripts'))
import preview


class PreviewCommandTests(unittest.TestCase):
    def test_reused_run_cannot_print_old_success(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(preview, 'report') as report:
            with self.assertRaisesRegex(ValueError, 'fresh destination'):
                preview.run_offline(tmp)
            report.assert_not_called()

    def test_missing_and_interrupted_reports_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            for path in (Path(tmp) / 'absent', Path(tmp)):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(preview.report(path), 2)
                self.assertIn('incomplete', output.getvalue())
                self.assertIn('Model calls: 0', output.getvalue())

    def test_export_excludes_raw_error_or_private_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'summary'
            with patch.object(preview.offline_acceptance, 'verify', side_effect=ValueError('PRIVATE_SOURCE_SECRET')):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(preview.report(Path(tmp), out), 2)
            combined = ''.join(p.read_text() for p in out.iterdir())
            self.assertNotIn('PRIVATE_SOURCE_SECRET', combined)
            self.assertNotIn(str(tmp), combined)
            self.assertEqual(set(p.name for p in out.iterdir()), {'REPORT.md', 'decision.json'})

    def test_successful_setup_exit_zero(self):
        with patch.dict(sys.modules, {'preview_environment': __import__('preview_environment')}):
            import preview_environment
            with patch.object(preview_environment, 'setup', return_value={'status': 'configured'}):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(preview.main(['setup', '--state', '/fake-selected-state']), 0)

    def test_failed_execution_still_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'run'
            def failure(path):
                Path(path).mkdir()
                raise OSError('authored infrastructure failure')
            with patch.object(preview.offline_acceptance, 'run', side_effect=failure):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(preview.run_offline(out), 2)
