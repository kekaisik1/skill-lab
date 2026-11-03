#!/usr/bin/env python3
"""Local preview setup and fresh boundary gates. No download, login or model call."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def discover_codex(selected=None):
    """Locate an explicitly selected native binary or PATH npm installation."""
    value = selected or os.environ.get('SKILL_LAB_CODEX') or shutil.which('codex')
    if not value:
        raise ValueError('Codex not found: install the supported native Codex CLI, then pass --codex /path/to/native/bin/codex.')
    path = Path(value).expanduser().resolve()
    if path.name == 'codex.js':
        package = path.parent.parent
        matches = sorted(package.glob('node_modules/@openai/codex-*/vendor/*/bin/codex'))
        if len(matches) != 1:
            raise ValueError('Cannot uniquely resolve native Codex from its npm launcher; pass --codex /path/to/native/bin/codex.')
        path = matches[0]
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError('Codex native binary is missing or not executable: ' + str(path))
    with path.open('rb') as handle:
        if handle.read(4) != b'\x7fELF':
            raise ValueError('The boundary needs the native Linux ELF binary, not a shell/Node launcher: ' + str(path))
    return path.resolve()


def native_files(binary):
    binary = Path(binary)
    return {'codex': binary, 'code_mode_host': binary.with_name('codex-code-mode-host'),
            'codex_bwrap': binary.parent.parent / 'codex-resources/bwrap'}


def reject_links(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink state path refused: ' + str(path))
    return path


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')


def runtime_create(runtime):
    """Minimal authored shell shim; system Python/stdlib is mounted read-only."""
    runtime = Path(runtime)
    for name in ('python/bin', 'deps', 'companions', 'parser-cache'):
        (runtime / name).mkdir(parents=True, exist_ok=False)
    for name in ('python', 'python3'):
        path = runtime / 'python/bin' / name
        path.write_text('#!/bin/sh\nunset PYTHONHOME\n'
                        'if [ "$1" = "-P" ]; then shift; exec /usr/bin/python3 -I -S "$@"; fi\n'
                        'exec /usr/bin/python3 -S "$@"\n')
        path.chmod(0o755)
    files = {p.relative_to(runtime).as_posix(): digest(p) for p in (runtime / 'python').rglob('*') if p.is_file()}
    write(runtime / 'runtime.json', {'schema': 1, 'classification': 'portable system Python; no UB companions',
        'files': files, 'resources': [], 'supported_adapters': ['public-label-fixtures'],
        'unsupported': ['ub-companions', 'frontend-browser', 'arbitrary-project-grading']})


def pins(config):
    scripts = Path(__file__).resolve().parent
    paths = {'python': Path('/usr/bin/python3'), 'bwrap': Path('/usr/bin/bwrap'),
             'runtime_manifest': Path(config['runtime']) / 'runtime.json'}
    if config.get('codex'):
        paths.update(native_files(config['codex']))
    paths.update({name: scripts / name for name in ('preview_environment.py', 'preview.py', 'preview_native.py', 'preview_intake.py', 'benchmark_builder.py',
                                                   'skill_intake.py', 'evaluate.py', 'experiment_report.py', 'codex_host.py', 'codex_boundary.py',
                                                   'lab_mcp.py', 'ub_runtime.py', 'skill_safety.py', 'fixture_runner.py')})
    return {key: digest(path) for key, path in paths.items()}


def prerequisites():
    problems = []
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        problems.append('This preview supports Linux x86_64 only.')
    if sys.version_info < (3, 10):
        problems.append('Python 3.10 or newer is required.')
    for path in ('/usr/bin/python3', '/usr/bin/bwrap', '/usr', '/lib', '/lib64'):
        if not Path(path).exists():
            problems.append('Missing prerequisite ' + path + '; install the distribution Python/bubblewrap packages.')
    return problems


def setup(state, codex=None):
    state = reject_links(state)
    if state.exists():
        raise ValueError('Setup requires a fresh state directory. Existing pins are never refreshed; review changes and choose a new --state.')
    problems = prerequisites()
    if problems:
        raise ValueError('; '.join(problems))
    binary, native_error = None, None
    try:
        binary = discover_codex(codex)
        for path in native_files(binary).values():
            if not path.is_file():
                raise ValueError('Required native companion missing: ' + str(path))
    except (OSError, ValueError) as error:
        if codex is not None:
            raise
        native_error = str(error)
        binary = None
    state.mkdir(parents=True, mode=0o700)
    runtime_create(state / 'runtime')
    config = {'schema': 'skill-lab-preview-environment/1', 'runtime': str(state / 'runtime'),
              'codex': str(binary) if binary else None, 'native_discovery_error': native_error,
              'authentication': 'existing native Codex ChatGPT login; never copied or mounted into candidate',
              'api_allowance_usd': 0, 'telemetry': False, 'downloads': False}
    config['pins'] = pins(config)
    write(state / 'environment.json', config)
    return {'status': 'configured', 'state': str(state), 'runtime': config['runtime'],
            'codex': config['codex'], 'native_discovery_error': native_error,
            'boundary': 'untested; run doctor and boundary before any native comparison'}


def load(state):
    state = reject_links(state)
    config = json.loads((state / 'environment.json').read_text())
    if config.get('schema') != 'skill-lab-preview-environment/1':
        raise ValueError('Unsupported environment configuration; create a fresh reviewed state.')
    if Path(config['runtime']) != state / 'runtime':
        raise ValueError('Runtime must belong to this state directory; copied configuration is not local boundary proof.')
    import ub_runtime
    ub_runtime.validate(config['runtime'])
    if pins(config) != config.get('pins'):
        raise ValueError('Security pins changed. Review changed binaries/helpers/runtime, then explicitly set up a NEW state and rerun boundary; automatic repinning is forbidden.')
    return config


def doctor(state):
    result = {'schema': 'skill-lab-preview-doctor/1', 'status': 'incomplete', 'real_model_calls': 0,
              'real_credentials_accessed': False, 'checks': [], 'live_ready': False}
    try:
        problems = prerequisites()
        if problems:
            raise ValueError('; '.join(problems))
        config = load(state)
        import fixture_runner
        import tempfile
        with tempfile.TemporaryDirectory(prefix='skill-lab-doctor-') as directory:
            source = Path(directory) / 'probe.py'
            source.write_text("print('SKILL_LAB_NAMESPACE_READY')\n")
            receipt = fixture_runner.bounded(fixture_runner.command(source))
        result['namespace'] = receipt
        if receipt['status'] != 'completed' or receipt['stdout'].strip() != 'SKILL_LAB_NAMESPACE_READY':
            raise ValueError('Namespace preflight failed. Enable unprivileged user/PID/network namespaces and bubblewrap on this Linux host; do not bypass isolation. See namespace.stderr.')
        result.update(status='pass', checks=['prerequisites', 'unchanged_security_pins', 'actual_namespace_execution'],
                      offline_ready=True, native_binary_found=bool(config['codex']),
                      native_boundary='Run boundary into a fresh output directory; doctor does not reuse historical proof.',
                      authentication='untested; doctor never reads native login')
    except (OSError, ValueError, KeyError, TypeError) as error:
        result.update(error=str(error), offline_ready=False)
    return result


def validate_boundary(state, out):
    """Fresh local proof. Fake profiles only; never consult the real auth profile."""
    config = load(state)
    out = reject_links(out)
    out.mkdir(parents=True, exist_ok=False)
    import skill_safety
    import codex_boundary
    result = {'schema': 'skill-lab-preview-boundary/1', 'status': 'incomplete',
              'real_model_calls': 0, 'real_credentials_accessed': False,
              'pins': config['pins'], 'native': 'untested', 'safe_to_install': False}
    try:
        security = skill_safety.selftest(out / 'security', runtime=config['runtime'], portable=True)
        result['security'] = security['status']
        if security['status'] != 'pass':
            raise ValueError('Fake-secret containment controls failed; inspect security/report.json and process receipts.')
        if config['codex']:
            native = codex_boundary.run(out / 'native', runtime=config['runtime'], binary=Path(config['codex']))
            result['native'] = native['status']
            if native['status'] != 'pass':
                raise ValueError('Native dispatcher boundary failed; inspect native/report.json. This Codex build is not supported for live runs.')
            codex_boundary.verify_proof(out / 'native/report.json', runtime=config['runtime'], binary=Path(config['codex']))
        else:
            result['native_reason'] = config['native_discovery_error']
        load(state)
        result['status'] = 'pass' if result['native'] == 'pass' else 'offline_only'
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['error'] = str(error)
    write(out / 'report.json', result)
    return result
