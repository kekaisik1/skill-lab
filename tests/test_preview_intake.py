import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skill-evaluator/scripts'))
spec = importlib.util.spec_from_file_location('preview_intake', ROOT / 'skill-evaluator/scripts/preview_intake.py')
intake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(intake)


class PreviewIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.skill = self.root / 'skill'
        self.skill.mkdir()
        (self.skill / 'SKILL.md').write_text('Repair selected code; do not activate for explanations.\n')
        self.selected = self.root / 'selected.txt'
        self.selected.write_text('Explicit public selected example.\n')
        self.out = self.root / 'output'

    def prepare(self, **kwargs):
        return intake.prepare(self.skill, 'Repair selected Python code', [self.selected], self.out, **kwargs)

    def test_inert_intake_is_bound_to_only_explicit_examples(self):
        marker = self.root / 'executed'
        (self.skill / 'candidate.py').write_text(f'open({str(marker)!r}, "w").write("ran")\n')
        (self.root / 'unselected.txt').write_text('PRIVATE_UNSELECTED_SENTINEL')
        result = self.prepare()
        self.assertEqual(result['status'], 'needs_authoring')
        self.assertFalse(result['candidate_execution'])
        self.assertFalse(result['safe_to_install'])
        self.assertFalse(marker.exists())
        all_output = ''.join(p.read_text() for p in self.out.rglob('*') if p.is_file())
        self.assertNotIn('PRIVATE_UNSELECTED_SENTINEL', all_output)
        self.assertEqual(intake.validate(self.out), result)
        history = json.loads((self.out / 'private/history.json').read_text())
        self.assertFalse(history['entries'][0]['shareable'])

    def test_examples_optional_for_inert_intake_but_required_for_draft(self):
        result = intake.prepare(self.skill, 'Repair selected Python code', [], self.out)
        self.assertEqual(result['status'], 'needs_authoring')
        self.assertEqual(result['examples'], 0)
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(intake.validate(self.out), result)
        draft = ROOT / 'examples/portable/draft.json'
        with self.assertRaisesRegex(ValueError, 'requires explicit example'):
            intake.prepare(self.skill, 'Repair selected Python code', [], self.root / 'draft-out', draft=draft)
        self.assertFalse((self.root / 'draft-out').exists())

    def test_public_example_builds_and_oracle_controls_execute(self):
        result = intake.example(self.out)
        self.assertEqual(result['benchmark']['tasks'], 2)
        self.assertEqual(result['controls']['status'], 'pass')
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(result['independent_review'], 'required')
        self.assertEqual(len(result['controls']['controls']), 2)
        proposal = json.loads((self.out / 'proposal.json').read_text())
        self.assertEqual({task['should_activate'] for task in proposal['tasks']}, {True, False})
        for packet in (self.out / 'benchmark/candidate').rglob('*.json'):
            value = json.loads(packet.read_text())
            self.assertNotIn('expected', json.dumps(value['cases']))
            self.assertNotIn('acceptance', value)
            self.assertNotIn('history', value)
        self.assertEqual(intake.validate(self.out)['status'], 'prepared')

    def test_source_drift_new_file_and_proposal_tampering_rejected(self):
        self.prepare()
        path = self.out / 'private/skill/SKILL.md'
        original = path.read_bytes()
        path.write_text('Changed')
        with self.assertRaisesRegex(ValueError, 'Changed selection'):
            intake.validate(self.out)
        path.write_bytes(original)
        extra = self.out / 'private/skill/extra.txt'
        extra.write_text('Added')
        with self.assertRaisesRegex(ValueError, 'Changed selected skill inventory'):
            intake.validate(self.out)
        extra.unlink()
        (self.out / 'proposal.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Changed selection'):
            intake.validate(self.out)

    def test_selected_paths_refuse_directories_links_empty_and_duplicates(self):
        link = self.root / 'link'
        link.symlink_to(self.selected)
        empty = self.root / 'empty'
        empty.write_text('')
        for examples in [[self.root], [link], [empty], [self.selected, self.selected]]:
            with self.subTest(examples=examples), self.assertRaises(ValueError):
                intake.prepare(self.skill, 'Repair', examples, self.out)
            self.assertFalse(self.out.exists())
        linked_parent = self.root / 'linked'
        linked_parent.symlink_to(self.skill, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlinks'):
            intake.prepare(linked_parent, 'Repair', [self.selected], self.out)

    def test_missing_bad_draft_and_invalid_quote_leave_no_output(self):
        bad = self.root / 'draft.json'
        value = json.loads((ROOT / 'examples/portable/draft.json').read_text())
        bad.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'exact skill quotes'):
            self.prepare(draft=bad)
        self.assertFalse(self.out.exists())

    def test_existing_output_preserved_and_nested_output_refused(self):
        self.prepare()
        original = (self.out / 'proposal.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'fresh'):
            self.prepare()
        self.assertEqual((self.out / 'proposal.json').read_bytes(), original)
        with self.assertRaisesRegex(ValueError, 'inside selected skill'):
            intake.prepare(self.skill, 'Repair', [self.selected], self.skill / 'out')

    def test_missing_artifacts_and_unexpected_file_rejected(self):
        self.prepare()
        extra = self.out / 'extra.txt'
        extra.write_text('Unexpected')
        with self.assertRaisesRegex(ValueError, 'unexpected'):
            intake.validate(self.out)
        extra.unlink()
        (self.out / 'private/examples/example-1.txt').unlink()
        with self.assertRaisesRegex(ValueError, 'regular file'):
            intake.validate(self.out)


if __name__ == '__main__':
    unittest.main()
