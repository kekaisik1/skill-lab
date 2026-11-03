import importlib.util
import json
from pathlib import Path, PureWindowsPath
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location('portable_' + name, ROOT / 'skill-lab/scripts' / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


installer = module('install')
release = module('release')


class PortablePackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        files = {'skill-lab/SKILL.md': b'original skill',
                 'skill-lab/agents/openai.yaml': b'interface: {}',
                 'skill-lab/scripts/launcher.py': (ROOT / 'skill-lab/scripts/launcher.py').read_bytes(),
                 'skill-lab/scripts/platform_bridge.py': (ROOT / 'skill-lab/scripts/platform_bridge.py').read_bytes(),
                 'skill-evaluator/scripts/preview.py': b'import sys\nprint("portable backend")\nraise SystemExit(7 if "fail" in sys.argv else 0)\n',
                 'skill-evaluator/scripts/preview_intake.py': b'import sys\nprint("portable backend")\nraise SystemExit(7 if "fail" in sys.argv else 0)\n'}
        files['release-files.json'] = release.encoded({'schema': 'skill-lab-release-files/1',
                                                     'files': sorted([*files, 'release-files.json'])})
        for name, raw in files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.refresh()
        self.destination = self.root / 'other location' / '.codex/skills/skill-lab'

    def refresh(self):
        (self.source / release.MANIFEST).write_bytes(release.payload(self.source)[release.MANIFEST])

    def install(self, apply=True):
        return installer.portable_install(self.source, self.destination, apply=apply)

    def test_reproducible_archive_exact_manifest_and_no_extra_files(self):
        a, b = self.root / 'a.tar.gz', self.root / 'b.tar.gz'
        release.build(a, self.source)
        release.build(b, self.source)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        with tarfile.open(a) as archive:
            self.assertEqual(set(archive.getnames()), {'skill-lab-preview/' + n for n in release.payload(self.source)})
        (self.source / 'private.txt').write_text('private')
        with self.assertRaisesRegex(ValueError, 'unlisted'):
            release.verify(self.source)

    def test_artifact_tampering_refuses_install_without_destination_writes(self):
        (self.source / 'skill-lab/SKILL.md').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.install()
        self.assertFalse(self.destination.exists())

    def test_preview_then_install_runs_without_original_source(self):
        self.install(False)
        self.assertFalse(self.destination.exists())
        self.install()
        self.source.rename(self.root / 'unavailable source')
        script = self.destination / 'scripts/launcher.py'
        result = subprocess.run([sys.executable, str(script), 'example', 'fail'], cwd=self.root,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn('portable backend', result.stdout)
        self.assertEqual(json.loads((self.destination / 'installation.json').read_text())['project_root'], 'kit')

    def test_noop_update_and_unowned_collision_never_adopted(self):
        self.install()
        self.assertEqual(self.install()['changed_files'], [])
        ledger = self.destination / 'installed-files.json'
        data = json.loads(ledger.read_text())
        del data['sha256']['SKILL.md']
        ledger.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'unowned'):
            self.install()

    def test_ownership_ledger_uses_posix_names_on_windows_hosts(self):
        self.install()
        expected = json.loads((self.destination / 'installed-files.json').read_text())['sha256']
        with patch.object(installer, 'Path', side_effect=lambda value: PureWindowsPath(value)
                          if isinstance(value, str) else Path(value)):
            self.assertEqual(installer.owned(self.destination), expected)
        for name in ('C:/outside', 'kit/file:stream', 'kit/../outside', 'kit\\outside'):
            ledger = self.destination / 'installed-files.json'
            ledger.write_text(json.dumps({'schema': 'skill-lab-owned-files/2',
                                         'sha256': {name: 'a' * 64}}))
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unsafe'):
                installer.owned(self.destination)

    def test_update_preserves_edits_and_preflights_every_collision(self):
        self.install()
        (self.destination / 'SKILL.md').write_text('owner edit')
        before = (self.destination / 'kit/skill-lab/SKILL.md').read_bytes()
        (self.source / 'skill-lab/SKILL.md').write_text('new version')
        self.refresh()
        with self.assertRaisesRegex(ValueError, 'locally edited'):
            self.install()
        self.assertEqual((self.destination / 'SKILL.md').read_text(), 'owner edit')
        self.assertEqual((self.destination / 'kit/skill-lab/SKILL.md').read_bytes(), before)

    def test_update_unchanged_owned_files_and_remove_preserve_unrelated_and_edited(self):
        self.install()
        (self.source / 'skill-lab/SKILL.md').write_text('new version')
        self.refresh()
        self.install()
        self.assertEqual((self.destination / 'SKILL.md').read_text(), 'new version')
        (self.destination / 'SKILL.md').write_text('owner version')
        note = self.destination / 'owner-note.md'
        note.write_text('unrelated')
        dry = installer.portable_remove(self.destination)
        self.assertIn('SKILL.md', dry['preserved_edited_files'])
        self.assertTrue((self.destination / 'scripts/launcher.py').exists())
        installer.portable_remove(self.destination, apply=True)
        self.assertEqual(note.read_text(), 'unrelated')
        self.assertEqual((self.destination / 'SKILL.md').read_text(), 'owner version')
        self.assertFalse((self.destination / 'scripts/launcher.py').exists())

    def test_symlink_and_traversal_ownership_refused(self):
        self.install()
        outside = self.root / 'outside'
        outside.write_text('untouched')
        ledger = self.destination / 'installed-files.json'
        data = json.loads(ledger.read_text())
        data['sha256']['../../outside'] = installer.digest(outside.read_bytes())
        ledger.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            installer.portable_remove(self.destination, apply=True)
        self.assertEqual(outside.read_text(), 'untouched')
        del data['sha256']['../../outside']
        ledger.write_text(json.dumps(data))
        target = self.destination / 'SKILL.md'
        target.unlink()
        target.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            installer.portable_remove(self.destination, apply=True)
        self.assertEqual(outside.read_text(), 'untouched')

    def test_release_refuses_sensitive_roots_and_symlinks(self):
        allow = self.source / 'release-files.json'
        data = json.loads(allow.read_text())
        data['files'].append('runs/secret.json')
        allow.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'forbidden'):
            release.payload(self.source)
        data['files'].remove('runs/secret.json')
        allow.write_text(json.dumps(data))
        target = self.source / 'skill-lab/SKILL.md'
        target.unlink()
        target.symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            release.payload(self.source)


if __name__ == '__main__':
    unittest.main()
