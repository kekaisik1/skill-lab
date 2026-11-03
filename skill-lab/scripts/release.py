#!/usr/bin/env python3
"""Build/verify a deterministic local preview archive from an exact file allowlist."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = 'RELEASE-MANIFEST.json'
ALLOWLIST = 'release-files.json'


def is_link(path):
    path = Path(path)
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
    except FileNotFoundError:
        return False


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def relative(name):
    path = PurePosixPath(name)
    if (not isinstance(name, str) or path.is_absolute() or str(path) != name
            or '..' in path.parts or '\\' in name or not path.parts):
        raise ValueError(f'Unsafe release path: {name}')
    return path


def safe_file(root, name):
    path = root / relative(name)
    if any(is_link(p) for p in (path, *path.parents)) or not path.is_file():
        raise ValueError(f'Missing or symlink release file: {name}')
    return path


def payload(root=ROOT):
    root = Path(root)
    allow = json.loads(safe_file(root, ALLOWLIST).read_text())
    if allow.get('schema') != 'skill-lab-release-files/1':
        raise ValueError('Unsupported release allowlist')
    names = allow['files']
    if not names or len(names) != len(set(names)) or ALLOWLIST not in names:
        raise ValueError('Empty/duplicate allowlist or missing allowlist self-entry')
    files = {}
    for name in sorted(names):
        parts = relative(name).parts
        if (any(p in {'runs', 'evidence', 'private', 'histories', '.git', '__pycache__',
                      '.codex', '.aws', 'node_modules', 'pilots'} for p in parts)
                or parts[-1] in {'auth.json', 'installation.json', 'installed-files.json', '.env',
                                 'config.toml', 'config.json', 'history.jsonl'}):
            raise ValueError(f'Private/local release path forbidden: {name}')
        files[name] = safe_file(root, name).read_bytes()
    manifest = {'schema': 'skill-lab-release/1', 'version': '0.2.0-dev',
                'evidence_class': 'software_only_no_local_boundary_proof',
                'files': {name: {'sha256': digest(raw), 'bytes': len(raw)}
                          for name, raw in files.items()}}
    files[MANIFEST] = encoded(manifest)
    return files


def verify(root):
    root = Path(root)
    manifest = json.loads(safe_file(root, MANIFEST).read_text())
    if manifest.get('schema') != 'skill-lab-release/1':
        raise ValueError('Unsupported release manifest')
    expected = payload(root)
    if safe_file(root, MANIFEST).read_bytes() != expected[MANIFEST]:
        raise ValueError('Release manifest differs from allowlist or file bytes')
    # The extracted distribution is exact; local installs store state outside kit.
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() or p.is_symlink()}
    if actual != set(expected):
        raise ValueError('Release contains unlisted or missing files')
    return {'schema': 'skill-lab-release-verification/1', 'status': 'pass',
            'files': len(expected), 'manifest_sha256': digest(expected[MANIFEST])}


def build(out, root=ROOT):
    out = Path(out)
    files = payload(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('xb') as handle:
        with gzip.GzipFile(filename='', mode='wb', fileobj=handle, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                for name, raw in sorted(files.items()):
                    item = tarfile.TarInfo('skill-lab-preview/' + name)
                    item.size = len(raw)
                    item.mode = 0o644
                    item.mtime = 0
                    archive.addfile(item, io.BytesIO(raw))
    return {'schema': 'skill-lab-release-build/1', 'artifact': str(out.absolute()),
            'sha256': digest(out.read_bytes()), 'files': len(files),
            'manifest': json.loads(files[MANIFEST])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    builder = commands.add_parser('build')
    builder.add_argument('--out', type=Path, required=True)
    checker = commands.add_parser('verify')
    checker.add_argument('directory', type=Path)
    args = parser.parse_args()
    try:
        result = build(args.out) if args.command == 'build' else verify(args.directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'Skill Lab release failed: {exc}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
