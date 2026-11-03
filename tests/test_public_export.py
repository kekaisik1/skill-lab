"""Public source packaging tests use authored temporary trees, never private runs."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_export_test_module', ROOT / 'skill-lab/scripts/public_export.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class PublicExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.out = self.root / 'public'
        self.write('release-files.json', json.dumps({'schema': 'skill-lab-release-files/1',
                                                   'files': ['release-files.json', 'source.py']}))
        self.write('source.py', '# authored public source\n')
        self.write('public-files.json', json.dumps({'schema': 'skill-lab-public-source/1',
            'copy': ['public-files.json', 'skill-lab/scripts/public_export.py', 'tests/test_fixture.py'],
            'templates': {'README.md': 'public-template/README.md'}}))
        self.write('skill-lab/scripts/public_export.py', '# trusted exporter fixture\n')
        self.write('tests/test_fixture.py', '# authored fixture tests\n')
        self.write('public-template/README.md', 'Public instructions\n')
        self.write('runs/private.json', 'PRIVATE_UNSELECTED_SENTINEL')
        self.write('evidence/report.txt', 'PRIVATE_UNSELECTED_SENTINEL')
        self.write('.env', 'PRIVATE_UNSELECTED_SENTINEL')

    def write(self, name, text):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_exact_export_ignores_local_private_files_and_is_reproducible(self):
        result = exporter.export(self.out, self.source)
        self.assertFalse(result['published'])
        self.assertIn('owner decision', result['license_status'])
        files = [p for p in self.out.rglob('*') if p.is_file()]
        self.assertFalse(any(b'PRIVATE_UNSELECTED_SENTINEL' in p.read_bytes() for p in files))
        again = self.root / 'again'
        exporter.export(again, self.source)
        self.assertEqual((self.out / exporter.MANIFEST).read_bytes(), (again / exporter.MANIFEST).read_bytes())
        self.assertEqual(exporter.verify(self.out)['status'], 'pass')
        self.assertEqual((self.out / 'README.md').read_bytes(), (self.out / 'public-template/README.md').read_bytes())

    def test_changed_missing_or_added_source_is_rejected(self):
        exporter.export(self.out, self.source)
        file = self.out / 'source.py'
        original = file.read_bytes()
        file.write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'changed'):
            exporter.verify(self.out)
        file.write_bytes(original)
        (self.out / 'extra.txt').write_text('not allowlisted')
        with self.assertRaisesRegex(ValueError, 'Unlisted'):
            exporter.verify(self.out)
        (self.out / 'extra.txt').unlink()
        file.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            exporter.verify(self.out)

    def test_export_never_overwrites_and_rejects_symlinks(self):
        exporter.export(self.out, self.source)
        with self.assertRaises(FileExistsError):
            exporter.export(self.out, self.source)
        linked = self.root / 'linked'
        linked.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            exporter.export(linked / 'export', self.source)

    def test_private_paths_cannot_enter_via_extra_copy_list(self):
        value = json.loads((self.source / 'public-files.json').read_text())
        value['copy'].append('runs/private.json')
        self.write('public-files.json', json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'Unexpected public source'):
            exporter.export(self.out, self.source)
        self.assertFalse(self.out.exists())

    def test_git_metadata_is_not_public_content(self):
        exporter.export(self.out, self.source)
        (self.out / '.git').mkdir()
        (self.out / '.git/config').write_text('local git metadata')
        self.assertEqual(exporter.verify(self.out)['status'], 'pass')


if __name__ == '__main__':
    unittest.main()
