#!/usr/bin/env python3
"""Create a fresh public source tree from explicit release/test/document allowlists."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = 'PUBLIC-MANIFEST.json'


def release_module(root):
    # Import only this project's trusted packaging code, never selected skill code.
    spec = importlib.util.spec_from_file_location('skill_lab_public_release', Path(__file__).with_name('release.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload(root=ROOT):
    root = Path(root)
    release = release_module(root)
    files = release.payload(root)
    del files[release.MANIFEST]
    value = json.loads(release.safe_file(root, 'public-files.json').read_text())
    if value.get('schema') != 'skill-lab-public-source/1':
        raise ValueError('Unsupported public source allowlist')
    copies, templates = value.get('copy'), value.get('templates')
    if not isinstance(copies, list) or not copies or len(copies) != len(set(copies)) or not isinstance(templates, dict):
        raise ValueError('Invalid public source allowlist')
    for name in copies:
        parts = release.relative(name).parts
        if not (name in {'public-files.json', 'skill-lab/scripts/public_export.py'} or
                len(parts) == 2 and parts[0] == 'tests' and parts[1].startswith('test_') and parts[1].endswith('.py')):
            raise ValueError('Unexpected public source path: ' + name)
        if name in files:
            raise ValueError('Public source collision: ' + name)
        files[name] = release.safe_file(root, name).read_bytes()
    for target, source in templates.items():
        target_parts, source_parts = release.relative(target).parts, release.relative(source).parts
        if (not source.startswith('public-template/') or len(source_parts) < 2 or
                target_parts[0] in {'runs', 'evidence', 'private', 'pilots', '.git', '.codex'} or
                target in files or source in files):
            raise ValueError('Unsafe public template mapping')
        raw = release.safe_file(root, source).read_bytes()
        files[target] = raw
        # Retain the templates so the public source can reproduce this export.
        files[source] = raw
    if 'LICENSE' in value:
        raise ValueError('Do not select a license through exporter config; owner must add and allowlist a reviewed LICENSE')
    # LICENSE becomes part of the reviewed normal release allowlist once selected.
    metadata = {'schema': 'skill-lab-public-source/1', 'contains_private_experiment_evidence': False,
                'boundary_proof': 'not distributed; every installation must validate locally',
                'license_present': 'LICENSE' in files,
                'files': {name: {'sha256': release.digest(raw), 'bytes': len(raw)}
                          for name, raw in sorted(files.items())}}
    files[MANIFEST] = release.encoded(metadata)
    return files


def export(out, root=ROOT):
    release = release_module(root)
    files = payload(root)
    out = Path(out).absolute()
    if any(p.is_symlink() for p in (out, *out.parents)):
        raise ValueError('Symlink export destination refused')
    out.mkdir(parents=True, exist_ok=False)
    for name, raw in sorted(files.items()):
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    receipt = verify(out)
    return {**receipt, 'destination': str(out), 'published': False,
            'license_status': 'reviewed LICENSE present' if 'LICENSE' in files else 'owner decision required before redistribution'}


def verify(root):
    root = Path(root)
    release = release_module(root)
    original = json.loads(release.safe_file(root, MANIFEST).read_text())
    if original.get('schema') != 'skill-lab-public-source/1' or not original.get('files'):
        raise ValueError('Missing/nonempty public content manifest required')
    actual = set()
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if relative.parts[0] == '.git':
            continue
        if path.is_symlink():
            raise ValueError('Public tree symlink refused: ' + relative.as_posix())
        if path.is_file():
            actual.add(relative.as_posix())
    expected = set(original['files']) | {MANIFEST}
    if actual != expected:
        raise ValueError('Unlisted or missing public source files')
    for name, row in original['files'].items():
        raw = release.safe_file(root, name).read_bytes()
        if row != {'sha256': release.digest(raw), 'bytes': len(raw)}:
            raise ValueError('Public source changed: ' + name)
    return {'schema': 'skill-lab-public-verification/1', 'status': 'pass', 'files': len(actual),
            'manifest_sha256': release.digest((root / MANIFEST).read_bytes())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('export')
    build.add_argument('--out', type=Path, required=True)
    check = commands.add_parser('verify')
    check.add_argument('directory', type=Path)
    args = parser.parse_args(argv)
    try:
        result = export(args.out) if args.command == 'export' else verify(args.directory)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, 'Public export failed: ' + str(error) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
