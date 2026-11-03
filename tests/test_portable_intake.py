"""Native preparation must work without Unix isolation imports or candidate execution."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PortableIntakeTests(unittest.TestCase):
    def test_public_example_runs_with_unix_runtime_imports_forbidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = '''import builtins,sys,json
from pathlib import Path
real_import=builtins.__import__
def guarded(name,*args,**kwargs):
    if name.split('.')[0] in {'resource','fixture_runner','codex_host','ub_runtime','lab','skill_safety'}:
        raise ImportError('Unix execution/runtime module is forbidden for native intake: '+name)
    return real_import(name,*args,**kwargs)
builtins.__import__=guarded
sys.path.insert(0,sys.argv[1])
import preview_intake
result=preview_intake.example(Path(sys.argv[2]))
print(json.dumps(result))
'''
            result = subprocess.run([sys.executable, '-B', '-c', code,
                                     str(ROOT / 'skill-evaluator/scripts'), str(Path(tmp).resolve() / 'example')],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report['candidate_execution'])
            self.assertEqual(report['model_calls'], 0)
            self.assertEqual(report['controls']['status'], 'pass')

    def test_windows_reparse_points_are_rejected_before_inventory(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        sys.path.insert(0, str(ROOT / 'skill-evaluator/scripts'))
        import evaluate
        with patch.object(Path, 'is_symlink', return_value=False):
            with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_file_attributes=0x400)):
                self.assertTrue(evaluate.is_link(Path('selected-junction')))
                with self.assertRaisesRegex(ValueError, 'not a symlink'):
                    evaluate.intake(Path('selected-junction'), 'explicit selection', 'a' * 40)

    def test_selected_skill_does_not_execute_hostile_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            skill = root / 'skill'
            skill.mkdir()
            (skill / 'SKILL.md').write_text('Review labels only. Never change verbatim text.\n')
            sentinel = root / 'not-created'
            (skill / 'attack.py').write_text('from pathlib import Path\nPath(' + repr(str(sentinel)) + ').write_text("executed")\n')
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'skill-evaluator/scripts/preview_intake.py'),
                                     'prepare', '--skill', str(skill), '--intended-use', 'review labels',
                                     '--out', str(root / 'proposal')], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(sentinel.exists())
            self.assertEqual(json.loads(result.stdout)['status'], 'needs_authoring')


if __name__ == '__main__':
    unittest.main()
