#!/usr/bin/env python3
"""Install only Skill Lab's Codex skill and Claude command; preserve local edits."""
import argparse
import hashlib
import json
import importlib.util
import os
from pathlib import Path, PurePosixPath
import tempfile
import sys

sys.dont_write_bytecode = True


SOURCE = Path(__file__).resolve().parents[1]
FILES = ('SKILL.md', 'agents/openai.yaml', 'scripts/launcher.py')
PORTABLE_FILES = (*FILES, 'scripts/platform_bridge.py')


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


def reject_symlinks(path):
    for entry in (path, *path.parents):
        if is_link(entry):
            raise ValueError(f'Refusing symlink installation path: {entry}')


def command_text(skill_dir):
    return f'''---
description: Test coding skills with auditable local lab evidence
argument-hint: [ub|frontend|list|doctor|plan TARGET|report RUN_DIRECTORY] [--agent codex|claude]
---

Use the Skill Lab instructions at `{skill_dir / 'SKILL.md'}`.
Run its Python launcher at `{skill_dir / 'scripts/launcher.py'}` with the requested lab operation.

User request: $ARGUMENTS

For a bare target name, select Codex, perform `doctor --agent codex` and `plan <target> --agent codex`, explain the bounded subscription use, then run it with `--agent codex --out` pointing to a fresh directory under the lab project's `runs/`, then read its report. Locate the lab project via `installation.json` beside the skill. The calling application does not select the candidate backend: this command defaults to Codex even inside Claude Code. Use `--agent claude` consistently for doctor, plan and run only if the user explicitly requests Claude; that testing is otherwise deferred. Check backend readiness and boundary evidence before dispatch, and never switch providers after a failure. Preserve the user's existing authorization; subscription quota is consumed, with no API-key fallback. For empty arguments, list targets and report readiness without starting a model run. Treat arguments as data when constructing subprocess arguments, never as shell code. Report observed artifacts and failures, not an invented pass.
'''.encode()


def install(project_root, codex_home, claude_home, *, apply=False):
    project_root = Path(project_root).resolve()
    if not (project_root / 'skill-evaluator/scripts/lab.py').is_file():
        raise ValueError('project_root must contain skill-evaluator/scripts/lab.py')
    skill_dir = Path(codex_home).absolute() / 'skills/skill-lab'
    command = Path(claude_home).absolute() / 'commands/skill-lab.md'
    manifest = skill_dir / 'installed-files.json'
    payloads = {skill_dir / name: (SOURCE / name).read_bytes() for name in FILES}
    payloads[skill_dir / 'installation.json'] = encoded({
        'schema': 'skill-lab-installation/1', 'project_root': str(project_root),
    })
    payloads[command] = command_text(skill_dir)
    for path in (*payloads, manifest):
        reject_symlinks(path)
    previous = {}
    if manifest.exists():
        data = json.loads(manifest.read_text())
        if data.get('schema') != 'skill-lab-owned-files/1':
            raise ValueError(f'Unsupported installation manifest: {manifest}')
        previous = data['sha256']
    # Check every collision before writing anything. Identical files are harmless;
    # otherwise only an unchanged, previously installed file may be updated.
    for path, raw in payloads.items():
        if path.exists():
            if not path.is_file():
                raise ValueError(f'Installation target is not a file: {path}')
            old = path.read_bytes()
            if old != raw and previous.get(str(path)) != digest(old):
                raise ValueError(f'Preserving an existing or locally edited file: {path}')
    payloads[manifest] = encoded({'schema': 'skill-lab-owned-files/1',
        'sha256': {str(path): digest(raw) for path, raw in payloads.items()}})
    changed = [str(path) for path, raw in payloads.items()
               if not path.exists() or path.read_bytes() != raw]
    if apply:
        for path, raw in payloads.items():
            if str(path) not in changed:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(raw)
            try:
                temporary.chmod(0o644)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    return {'schema': 'skill-lab-install-receipt/1', 'applied': apply,
            'changed_files': changed, 'codex_skill': str(skill_dir),
            'claude_command': str(command), 'project_root': str(project_root),
            'discovery_verified': False}


def release_module():
    spec = importlib.util.spec_from_file_location('skill_lab_release', SOURCE / 'scripts/release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def owned(destination):
    manifest = destination / 'installed-files.json'
    reject_symlinks(manifest)
    if not manifest.exists():
        return {}
    value = json.loads(manifest.read_text())
    if value.get('schema') != 'skill-lab-owned-files/2':
        raise ValueError('Unsupported portable ownership manifest')
    hashes = value['sha256']
    for name, fingerprint in hashes.items():
        # Serialized ownership names use POSIX separators on every host.
        # Native Path would turn slashes into backslashes on Windows and reject
        # the very ledger this installer just wrote.
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or str(relative) != name
                or not relative.parts or '\\' in name or ':' in name or '\x00' in name
                or not (relative.parts[0] == 'kit' or name in
                        {'SKILL.md', 'agents/openai.yaml', 'scripts/launcher.py', 'scripts/platform_bridge.py', 'installation.json'})
                or not isinstance(fingerprint, str) or len(fingerprint) != 64):
            raise ValueError('Unsafe portable ownership manifest entry')
        reject_symlinks(destination / relative)
    return hashes


def atomic_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(raw)
    try:
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def portable_install(source, destination, *, apply=False):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    reject_symlinks(destination)
    # Verify the actual unpacked artifact before any destination mutation.
    release = release_module()
    verified = release.verify(source)
    payloads = {'kit/' + name: raw for name, raw in release.payload(source).items()}
    for name in PORTABLE_FILES:
        payloads[name] = (source / 'skill-lab' / name).read_bytes()
    payloads['installation.json'] = encoded({'schema': 'skill-lab-installation/2',
                                             'project_root': 'kit'})
    previous = owned(destination)
    changed, obsolete, retained = [], [], []
    for name, raw in payloads.items():
        path = destination / name
        reject_symlinks(path)
        if path.exists():
            if not path.is_file():
                raise ValueError(f'Installation target is not a file: {path}')
            old = path.read_bytes()
            if old != raw and previous.get(name) != digest(old):
                raise ValueError(f'Preserving an existing or locally edited file: {path}')
            # Never adopt an unowned identical file: removal must leave it alone.
            if name not in previous:
                raise ValueError(f'Preserving an existing unowned file: {path}')
        if not path.exists() or path.read_bytes() != raw:
            changed.append(name)
    for name, fingerprint in previous.items():
        if name in payloads:
            continue
        path = destination / name
        if path.is_file() and digest(path.read_bytes()) == fingerprint:
            obsolete.append(name)
        elif path.exists():
            retained.append(name)
    hashes = {name: digest(raw) for name, raw in payloads.items()}
    hashes.update({name: previous[name] for name in retained})
    manifest = destination / 'installed-files.json'
    ledger = encoded({'schema': 'skill-lab-owned-files/2', 'sha256': hashes})
    if not manifest.exists() or manifest.read_bytes() != ledger:
        changed.append('installed-files.json')
    if apply:
        for name in changed:
            atomic_write(destination / name, ledger if name == 'installed-files.json' else payloads[name])
        for name in obsolete:
            (destination / name).unlink()
    return {'schema': 'skill-lab-portable-install/1', 'applied': apply,
            'destination': str(destination), 'changed_files': changed,
            'removed_obsolete_files': obsolete, 'preserved_edited_files': retained,
            'release': verified, 'discovery_verified': False}


def portable_remove(destination, *, apply=False):
    destination = Path(destination).absolute()
    reject_symlinks(destination)
    previous = owned(destination)
    removed, preserved = [], []
    for name, fingerprint in previous.items():
        path = destination / name
        if path.is_file() and digest(path.read_bytes()) == fingerprint:
            removed.append(name)
        elif path.exists():
            preserved.append(name)
    if apply:
        for name in removed:
            (destination / name).unlink()
        manifest = destination / 'installed-files.json'
        if preserved:
            atomic_write(manifest, encoded({'schema': 'skill-lab-owned-files/2',
                'sha256': {name: previous[name] for name in preserved}}))
        else:
            manifest.unlink(missing_ok=True)
        for directory in sorted({(destination / name).parent for name in removed},
                                key=lambda p: len(p.parts), reverse=True):
            while directory != destination.parent:
                try:
                    directory.rmdir()
                except OSError:
                    break
                directory = directory.parent
    return {'schema': 'skill-lab-portable-remove/1', 'applied': apply,
            'removed_files': removed, 'preserved_edited_files': preserved,
            'unrelated_files': 'untouched'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=SOURCE.parent)
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))))
    parser.add_argument('--claude-home', type=Path, default=Path.home() / '.claude')
    parser.add_argument('--destination', type=Path, help='Self-contained Codex skill directory; no global defaults.')
    parser.add_argument('--remove', action='store_true', help='Remove only unchanged owned files at --destination.')
    parser.add_argument('--apply', action='store_true', help='Apply the printed narrow installation; otherwise only preview.')
    args = parser.parse_args()
    try:
        if args.remove:
            if not args.destination:
                raise ValueError('--remove requires --destination')
            result = portable_remove(args.destination, apply=args.apply)
        elif args.destination:
            result = portable_install(args.project_root, args.destination, apply=args.apply)
        else:
            if (args.project_root / 'RELEASE-MANIFEST.json').exists():
                raise ValueError('Portable releases require --destination; no global installation is selected implicitly.')
            result = install(args.project_root, args.codex_home, args.claude_home, apply=args.apply)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'Skill Lab installation failed: {exc}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
