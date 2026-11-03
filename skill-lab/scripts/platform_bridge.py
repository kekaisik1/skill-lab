#!/usr/bin/env python3
"""Explicit Windows/macOS routing to an existing Linux guest installation.

No host paths, credentials or environment are copied into the guest. This bridge
is transport, not containment: the guest's doctor/boundary gates remain required.
"""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys

SCHEMA = 'skill-lab-platform-bridge/1'
PROVIDERS = {'wsl': ('Windows', 'wsl.exe'), 'lima': ('Darwin', 'limactl')}
OPERATIONS = {'list', 'setup', 'doctor', 'plan', 'run', 'report', 'prepare', 'example', 'boundary'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def guest_path(value, label):
    require(isinstance(value, str) and value.startswith('/') and not value.startswith('//') and '\\' not in value
            and all(ord(c) >= 32 for c in value) and len(value) <= 4096,
            label + ' must be an absolute Linux guest path, not a host path')
    path = PurePosixPath(value)
    require('..' not in path.parts and str(path) == value, label + ' must be normalized without traversal')
    return value


def validate(config):
    require(isinstance(config, dict) and set(config) == {
        'schema', 'provider', 'guest', 'guest_python', 'guest_launcher', 'guest_state'},
        'Unexpected bridge configuration fields')
    require(config['schema'] == SCHEMA and config['provider'] in PROVIDERS, 'Unsupported bridge schema/provider')
    require(isinstance(config['guest'], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', config['guest']),
            'Guest must be an explicit simple instance/distribution name')
    guest_path(config['guest_python'], 'guest_python')
    require(re.fullmatch(r'python3(?:\.\d+)?', PurePosixPath(config['guest_python']).name),
            'guest_python must name a Python 3 executable')
    guest_path(config['guest_launcher'], 'guest_launcher')
    require(PurePosixPath(config['guest_launcher']).name == 'launcher.py', 'Select the installed guest launcher.py')
    guest_path(config['guest_state'], 'guest_state')
    return config


def load(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384,
            'Bridge configuration must be a bounded regular file')
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate bridge configuration key')
            result[key] = value
        return result
    return validate(json.loads(path.read_text(), object_pairs_hook=unique))


def configure(path, provider, guest, guest_launcher, guest_state, guest_python='/usr/bin/python3'):
    config = validate({'schema': SCHEMA, 'provider': provider, 'guest': guest,
                       'guest_python': guest_python, 'guest_launcher': guest_launcher,
                       'guest_state': guest_state})
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'Symlink configuration destination refused')
    # No replace/update option: selecting a different guest is an explicit local edit.
    with path.open('x') as handle:
        json.dump(config, handle, sort_keys=True, indent=2)
        handle.write('\n')
    return {'status': 'configured', 'config': str(path), 'guest': guest,
            'platform_execution': 'untested; run bridge doctor',
            'auth': 'Log into native Codex inside this guest; never copy host credentials'}


def command_plan(config, arguments, *, host_platform=None, executable=None):
    config = validate(config)
    expected_platform, command = PROVIDERS[config['provider']]
    actual_platform = host_platform or platform.system()
    require(actual_platform == expected_platform,
            f'{config["provider"]} bridge requires {expected_platform}; this host is {actual_platform}')
    require(isinstance(arguments, (list, tuple)) and arguments and arguments[0] in OPERATIONS,
            'Bridge requires an explicit Skill Lab operation')
    require(all(isinstance(a, str) and '\x00' not in a and len(a) <= 32768 for a in arguments),
            'Invalid operation argument')
    require(sum(len(a) for a in arguments) <= 65536, 'Operation arguments exceed size bound')
    executable = executable or shutil.which(command)
    require(executable, f'{command} unavailable. Install/configure the guest tool yourself; no automatic download or fallback.')
    # An explicit guest workdir prevents implicit host-current-directory mapping.
    if config['provider'] == 'wsl':
        prefix = [str(executable), '--distribution', config['guest'], '--cd', '/', '--exec']
    else:
        prefix = [str(executable), 'shell', '--workdir=/', config['guest'], '--']
    return {'schema': 'skill-lab-platform-command/1', 'provider': config['provider'],
            'guest': config['guest'], 'argv': [*prefix, config['guest_python'],
                                              config['guest_launcher'], *arguments],
            'shell': False, 'host_paths_mounted': [], 'credentials_copied': False,
            'platform_claim': 'transport route only; guest containment requires actual local boundary controls'}


def transport_env(config):
    # WSLENV is the opt-in host-to-guest environment bridge. Do not forward host
    # credentials or other caller-selected variables through it.
    return dict(os.environ, WSLENV='') if config['provider'] == 'wsl' else dict(os.environ)


def running_precheck(config, planned):
    if config['provider'] != 'wsl':
        return
    # Best-effort protection against accidental startup. A guest can stop after
    # this read-only query; WSL may then start it when dispatching the command.
    query = [planned['argv'][0], '--list', '--running', '--quiet']
    process = subprocess.run(query, shell=False, capture_output=True, timeout=15,
                             env=transport_env(config))
    require(process.returncode == 0, 'Cannot inspect running WSL guests; no command dispatched')
    raw = process.stdout
    names = raw.decode('utf-16-le' if b'\x00' in raw else 'utf-8').lstrip('\ufeff')
    require(config['guest'] in {line.strip() for line in names.splitlines()},
            'Selected WSL guest is not running. Start it explicitly before using Skill Lab.')


def dispatch(config, arguments):
    planned = command_plan(config, arguments)
    running_precheck(config, planned)
    # CLI output flows directly to the calling chat/terminal; preserve failure codes.
    return subprocess.call(planned['argv'], shell=False, env=transport_env(config))


def doctor(config):
    result = {'schema': 'skill-lab-platform-doctor/1', 'status': 'incomplete',
              'real_model_calls': 0, 'live_ready': False, 'host_platform': platform.system(),
              'limitations': ['Routing to Linux does not establish native Windows/macOS containment.',
                             'Guest doctor is not native authentication or fresh boundary validation.',
                             'This bridge never installs a VM, copies credentials or changes mounts.',
                             'WSL running-state precheck is best effort; a stop/dispatch race can start the named guest.']}
    try:
        config = validate(config)
        planned = command_plan(config, ['doctor', '--state', config['guest_state']])
        result['command'] = planned
        running_precheck(config, planned)
        process = subprocess.run(planned['argv'], shell=False, capture_output=True, text=True, timeout=60,
                                 env=transport_env(config))
        result.update(exit_code=process.returncode, stdout=process.stdout, stderr=process.stderr)
        require(process.returncode == 0, 'Guest doctor failed; inspect stdout/stderr and fix guest prerequisites')
        observed = json.loads(process.stdout)
        require(observed.get('schema') == 'skill-lab-preview-doctor/1' and observed.get('status') == 'pass'
                and observed.get('offline_ready') is True and observed.get('real_model_calls') == 0,
                'Guest doctor did not return verified offline readiness')
        result.update(status='pass', offline_ready=True, guest_doctor=observed)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, subprocess.SubprocessError) as error:
        result.update(error=str(error), offline_ready=False)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    setup = sub.add_parser('configure')
    setup.add_argument('--out', type=Path, required=True)
    setup.add_argument('--provider', choices=sorted(PROVIDERS), required=True)
    setup.add_argument('--guest', required=True)
    setup.add_argument('--guest-launcher', required=True)
    setup.add_argument('--guest-state', required=True)
    setup.add_argument('--guest-python', default='/usr/bin/python3')
    for name in ('doctor', 'command', 'exec'):
        item = sub.add_parser(name)
        item.add_argument('--config', type=Path, required=True)
        if name != 'doctor':
            item.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command == 'configure':
            result = configure(args.out, args.provider, args.guest, args.guest_launcher,
                               args.guest_state, args.guest_python)
        else:
            config = load(args.config)
            if args.command == 'doctor':
                result = doctor(config)
            else:
                arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
                if args.command == 'exec':
                    return dispatch(config, arguments)
                result = command_plan(config, arguments)
        print(json.dumps(result, indent=2))
        return 0 if result.get('status') in {None, 'pass', 'configured'} else 2
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, subprocess.SubprocessError) as error:
        print('Skill Lab platform route unavailable: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
