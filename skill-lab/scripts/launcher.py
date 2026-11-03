#!/usr/bin/env python3
"""Locate the installed Skill Lab checkout and preserve the CLI's exit status."""
import json
import os
import platform
from pathlib import Path
import subprocess
import sys


def backend(skill_dir=None):
    skill_dir = Path(skill_dir or Path(__file__).resolve().parents[1])
    config = skill_dir / 'installation.json'
    if config.exists():
        value = json.loads(config.read_text())
        if value.get('schema') not in ('skill-lab-installation/1', 'skill-lab-installation/2'):
            raise ValueError('Unsupported Skill Lab installation schema; reinstall from the project.')
        root = Path(value['project_root'])
        if value['schema'] == 'skill-lab-installation/2':
            if str(root) != 'kit':
                raise ValueError('Portable project_root must be the relative kit directory.')
            root = skill_dir / root
            script = root / 'skill-evaluator/scripts/preview.py'
            if not script.is_file():
                raise FileNotFoundError(f'Skill Lab backend is missing: {script}. Reinstall the release artifact.')
            return script.resolve()
        if not root.is_absolute():
            raise ValueError('Installed project_root must be absolute.')
    else:
        root = skill_dir.parent
    script = root / 'skill-evaluator/scripts/lab.py'
    if not script.is_file():
        raise FileNotFoundError(f'Skill Lab backend is missing: {script}. Restore the project or reinstall the skill.')
    return script.resolve()


def main(argv=None):
    try:
        arguments = sys.argv[1:] if argv is None else argv
        skill_dir = Path(__file__).resolve().parents[1]
        route = skill_dir / 'platform.json'
        installation = skill_dir / 'installation.json'
        if (platform.system() != 'Linux' and (arguments[:1] in (['prepare'], ['example']) or arguments[:2] == ['adoption', 'intake'])
                and installation.is_file()
                and json.loads(installation.read_text()).get('schema') == 'skill-lab-installation/2'):
            intake = arguments[:2] == ['adoption', 'intake']
            script = backend().with_name('adoption_intake.py' if intake else 'preview_intake.py')
            if intake:
                arguments = arguments[2:]
            if not script.is_file():
                raise ValueError('Portable intake is missing; reinstall the release artifact.')
            return subprocess.call([sys.executable, str(script), *arguments],
                                   env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        if route.exists():
            import platform_bridge
            return platform_bridge.dispatch(platform_bridge.load(route), arguments)
        if platform.system() != 'Linux':
            raise ValueError('Configure platform.json for an existing WSL2/Linux or Lima/Linux guest using scripts/platform_bridge.py before running the Linux evaluator. Native Windows/macOS execution is unavailable.')
        script = backend()
        return subprocess.call([sys.executable, str(script), *arguments],
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Skill Lab startup failed: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
