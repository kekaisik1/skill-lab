#!/usr/bin/env python3
"""Check the public source; --isolated executes credential-free Linux controls."""
import argparse
import ast
import os
from pathlib import Path
import platform
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--isolated', action='store_true')
    parser.add_argument('--host', action='store_true', help='Only portable packaging/bridge checks; no guest containment claim')
    args = parser.parse_args()
    if args.host and args.isolated:
        parser.error('--host and --isolated are distinct checks')
    if platform.system() != 'Linux' and not args.host:
        parser.error('Run --host here; full evaluator/isolation checks run inside the selected Linux guest')
    root = Path(__file__).resolve().parent
    paths = [root / 'check.py', root / 'acceptance.py']
    for directory in ['skill-evaluator/scripts', 'skill-lab/scripts', 'tests']:
        paths.extend(sorted((root / directory).glob('*.py')))
    for path in paths:
        ast.parse(path.read_bytes(), filename=str(path.relative_to(root)))
    print(f'Syntax checked {len(paths)} Python files.', flush=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               SKILL_EVAL_ISOLATION_TESTS='1' if args.isolated else '0')
    if args.host:
        patterns = ['test_portable_package.py', 'test_platform_bridge.py', 'test_platform_launcher.py', 'test_portable_intake.py']
        for pattern in patterns:
            if not (root / 'tests' / pattern).is_file():
                print('Required host test module unavailable: ' + pattern, file=sys.stderr)
                return 2
            result = subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', pattern, '-v'], cwd=root, env=env)
            if result:
                return result
        print('Native intake/compiler, host route and packaging checks; WSL/Lima guest execution and native login are not validated here.')
        return 0
    return subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=root, env=env)


if __name__ == '__main__':
    raise SystemExit(main())
