#!/usr/bin/env python3
"""Controller-private functional grading for the frozen full-UB boundary-repair pilot."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

import fixture_runner as runner

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = ROOT / 'runs/ub-runtime-v1'
DEFAULT_HOLDOUTS = ROOT / 'pilots/ub-full-v1/holdouts.json'
PINNED_HOLDOUTS_SHA256 = 'fa10705012e498199f0db7585b6abf62b4338d9ef9e4bd092140504f4efb2099'
MAX_SOURCE_BYTES = 1024 * 1024
WRAPPER = """import importlib,json,sys
sys.dont_write_bytecode=True
sys.path.insert(0,'/artifact')
module=importlib.import_module('src.solution')
value=json.load(sys.stdin)
answer=module.normalize(value)
print(json.dumps(answer,ensure_ascii=False,allow_nan=False))
"""
CONTROLS = {
    'good': "def normalize(value):\n    if not value:\n        return ''\n    return value.strip().lower()\n",
    'missing-trim': "def normalize(value):\n    if not value:\n        return ''\n    return value.lower()\n",
    'returns-nothing': "def normalize(value):\n    return None\n",
    'casefold-mutant': "def normalize(value):\n    if not value:\n        return ''\n    return value.strip().casefold()\n",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def save(path, value):
    Path(path).write_bytes(encode(value))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_SOURCE_BYTES,
            'Expected bounded regular JSON file')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'Duplicate JSON key')
            result[key] = value
        return result
    return json.loads(path.read_text(), object_pairs_hook=unique)


def checks(path=DEFAULT_HOLDOUTS, expected_sha256=PINNED_HOLDOUTS_SHA256):
    value = read(path)
    require(sha(Path(path).read_bytes()) == expected_sha256, 'Frozen holdouts changed')
    require(value.get('schema') == 1 and value.get('task_id') == 'boundary-repair'
            and value.get('entrypoint') == 'solution.normalize', 'Wrong grading task/schema')
    cases = value.get('cases')
    require(isinstance(cases, list) and cases, 'Nonempty private cases required')
    ids = set()
    for case in cases:
        require(isinstance(case, dict) and set(case) == {'id', 'input', 'expected'}, 'Case schema mismatch')
        require(isinstance(case['id'], str) and case['id'] and case['id'] not in ids, 'Duplicate/empty case id')
        require((case['input'] is None or type(case['input']) is str) and type(case['expected']) is str,
                'String/null input and string output required')
        ids.add(case['id'])
    return cases


def snapshot_source(workspace, destination):
    source = Path(workspace) / 'src'
    require(source.is_dir() and not source.is_symlink(), 'Missing/unsafe src artifact')
    files, size = [], 0
    for path in sorted(source.rglob('*')):
        require(not path.is_symlink(), 'Source symlinks refused')
        if path.is_dir():
            continue
        require(path.is_file(), 'Special source file refused')
        require(size + path.stat().st_size <= MAX_SOURCE_BYTES and len(files) < 100, 'Source inventory exceeds limit')
        raw = path.read_bytes()
        size += len(raw)
        require(size <= MAX_SOURCE_BYTES, 'Source changed during snapshot')
        name = path.relative_to(source).as_posix()
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        files.append({'path': name, 'sha256': sha(raw), 'bytes': len(raw)})
    require(any(f['path'] == 'solution.py' for f in files), 'Missing src/solution.py')
    return files


def runtime_identity(runtime=DEFAULT_RUNTIME):
    runtime = Path(runtime)
    manifest = read(runtime / 'runtime.json')
    expected = {name: digest for name, digest in manifest['files'].items() if name.startswith('python/')}
    actual = {p.relative_to(runtime).as_posix() for p in (runtime / 'python').rglob('*') if p.is_file()}
    require(expected and set(expected) == actual, 'Missing or changed Python runtime inventory')
    for name, digest in expected.items():
        path = runtime / name
        require(not path.is_symlink() and sha(path.read_bytes()) == digest, 'Python runtime drift: ' + name)
    return {'manifest_sha256': sha((runtime / 'runtime.json').read_bytes()),
            'python_sha256': sha((runtime / 'python/bin/python3.12').read_bytes())}


def grading_command(wrapper, source=None, runtime=DEFAULT_RUNTIME):
    # Same copied Python 3.12 as the task; no companion packages, expected answers,
    # task workspace, home, private directory or Git mount enters artifact execution.
    command = runner.command(wrapper)
    position = command.index('--')
    mounts = ['--ro-bind', str(Path(runtime).resolve() / 'python'), '/runtime',
              '--setenv', 'PYTHONHOME', '/runtime']
    if source is not None:
        mounts += ['--ro-bind', str(source), '/artifact/src']
    command[position + 1] = '/runtime/bin/python3.12'
    return command[:position] + mounts + command[position:]


def grade(workspace, out, *, holdouts_path=DEFAULT_HOLDOUTS,
          holdouts_sha256=PINNED_HOLDOUTS_SHA256, provenance=None, runtime=DEFAULT_RUNTIME):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    receipt = {'schema': 1, 'task_id': 'boundary-repair', 'task_version': 'nullable-v1', 'status': 'incomplete',
               'grade': 'inconclusive', 'provenance': provenance,
               'holdouts_sha256': holdouts_sha256, 'grader_sha256': sha(Path(__file__).read_bytes()),
               'processes': [], 'cases': [], 'model_calls_by_grader': 0,
               'limitations': 'One small authored task; local hashes are not authenticated evidence. '
                              'Functionality grade does not establish full UB workflow execution.'}
    try:
        case_list = checks(holdouts_path, holdouts_sha256)
        receipt['expected_cases'] = len(case_list)
        artifact = out / 'artifact'
        artifact.mkdir()
        try:
            inventory = snapshot_source(workspace, artifact)
        except (OSError, ValueError) as error:
            receipt.update(status='invalid_artifact', grade='invalid', error=str(error))
            return receipt
        receipt['artifact_inventory'] = inventory
        receipt['artifact_sha256'] = sha(encode(inventory))
        try:
            receipt['runtime_identity'] = runtime_identity(runtime)
            receipt['bwrap_sha256'] = sha(Path(runner.BWRAP).read_bytes())
        except (OSError, ValueError, KeyError) as error:
            receipt.update(status='infrastructure_error', error=str(error))
            return receipt
        with tempfile.TemporaryDirectory(prefix='ub-grade-') as directory:
            wrapper = Path(directory) / 'wrapper.py'
            wrapper.write_text(WRAPPER)
            probe = Path(directory) / 'probe.py'
            probe.write_text("print('READY')\n")
            result = runner.bounded(grading_command(probe, runtime=runtime))
            receipt['processes'].append({'phase': 'preflight', **result})
            if result['status'] != 'completed' or result['stdout'].strip() != 'READY':
                receipt['status'] = 'infrastructure_error'
                return receipt
            for case in case_list:
                result = runner.bounded(grading_command(wrapper, artifact, runtime),
                                        json.dumps(case['input']).encode())
                receipt['processes'].append({'phase': 'case', 'case_id': case['id'], **result})
                if result['status'] != 'completed':
                    receipt['status'] = 'execution_' + result['status']
                    return receipt
                try:
                    answer = json.loads(result['stdout'])
                except (ValueError, TypeError):
                    receipt.update(status='invalid_protocol', grade='invalid')
                    return receipt
                passed = type(answer) is str and answer == case['expected']
                receipt['cases'].append({'id': case['id'], 'actual': answer, 'passed': passed,
                                         'input_sha256': sha(encode(case['input'])),
                                         'stdout_sha256': sha(result['stdout'].encode())})
        require(len(receipt['cases']) == len(case_list) > 0, 'Missing case execution')
        count = sum(row['passed'] for row in receipt['cases'])
        receipt.update(status='valid', grade='pass' if count == len(case_list) else 'fail',
                       passed=count, total=len(case_list))
        return receipt
    except KeyboardInterrupt:
        receipt['status'] = 'interrupted'
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        receipt.update(status='grader_error', error=str(error))
        return receipt
    finally:
        save(out / 'receipt.json', receipt)


def git_read(workspace, args, origin=None):
    workspace = Path(workspace).resolve()
    require((workspace / '.git').is_dir() and not (workspace / '.git').is_symlink(), 'Expected local Git directory')
    # Git metadata is candidate-controlled: inspect without host paths, credentials,
    # helpers, hooks or external network; both repositories are read-only.
    git_dir = '/origin.git' if origin is not None else '/work/.git'
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0',
           'GIT_DIR': git_dir, 'GIT_WORK_TREE': '/work'}
    argv = ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
            '-c', 'diff.external=', *args]
    with tempfile.TemporaryDirectory(prefix='ub-git-audit-') as directory:
        wrapper = Path(directory) / 'git.py'
        wrapper.write_text('import subprocess,sys\n'
                           + 'r=subprocess.run(' + repr(argv) + ',env=' + repr(env) + ',capture_output=True)\n'
                           + 'sys.stdout.buffer.write(r.stdout);sys.stderr.buffer.write(r.stderr)\n'
                           + 'raise SystemExit(r.returncode)\n')
        command = runner.command(wrapper)
        mounts = ['--ro-bind', str(workspace), '/work']
        if origin is not None:
            mounts += ['--ro-bind', str(Path(origin).resolve()), '/origin.git']
        position = command.index('--')
        result = runner.bounded(command[:position] + mounts + command[position:])
        require(result['status'] == 'completed', 'Git audit failed: ' + result['status'] + ': ' + result['stderr'])
        return result['stdout'].strip()


def audit_workspace(workspace, origin, protected_hashes, initial_head):
    """Read actual artifact/Git state; reports each observation without trusting agent logs."""
    workspace, origin = Path(workspace).resolve(), Path(origin).resolve()
    result = {'status': 'incomplete', 'protected': {}, 'limitations':
              'Git observations establish final state, not TDD ordering or quality-tool execution.'}
    try:
        require(isinstance(protected_hashes, dict) and protected_hashes, 'At least one protected file required')
        for name, expected in protected_hashes.items():
            path = Path(name)
            require(not path.is_absolute() and '..' not in path.parts, 'Unsafe protected path')
            target = workspace / path
            safe = (target.is_file() and not target.is_symlink() and target.resolve().is_relative_to(workspace)
                    and target.stat().st_size <= MAX_SOURCE_BYTES)
            actual = sha(target.read_bytes()) if safe else None
            result['protected'][name] = {'expected': expected, 'actual': actual, 'unchanged': actual == expected}
        head = git_read(workspace, ['rev-parse', '--verify', 'HEAD'])
        branch = git_read(workspace, ['symbolic-ref', '--quiet', '--short', 'HEAD'])
        result.update(head=head, initial_head=initial_head, branch=branch, commit_changed=head != initial_head,
                      working_tree_clean=not git_read(workspace, ['status', '--porcelain', '--untracked-files=all']))
        require(origin.is_dir() and not origin.is_symlink(), 'Expected local bare origin')
        remote_head = git_read(workspace, ['rev-parse', '--verify', 'refs/heads/' + branch], origin=origin)
        result.update(origin_head=remote_head, push_matches_head=head == remote_head)
        passed = all(v['unchanged'] for v in result['protected'].values()) and all(
            result[k] for k in ['commit_changed', 'working_tree_clean', 'push_matches_head'])
        result['status'] = 'pass' if passed else 'fail'
    except (OSError, ValueError) as error:
        result.update(status='invalid', error=str(error))
    return result


def selftest(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    summary = {'status': 'incomplete', 'classification': 'authored grader controls; no model/skill run', 'controls': []}
    try:
        for name, source in CONTROLS.items():
            workspace = out / (name + '-input') / 'src'
            workspace.mkdir(parents=True)
            (workspace / 'solution.py').write_text(source)
            result = grade(workspace.parent, out / name, provenance={'kind': 'authored_grader_control'})
            expected = 'pass' if name == 'good' else 'fail'
            summary['controls'].append({'name': name, 'status': result['status'], 'grade': result['grade'],
                                        'expected_grade': expected, 'passed': result.get('passed'),
                                        'total': result.get('total'), 'receipt': str(out / name / 'receipt.json')})
            require(result['status'] == 'valid' and result['grade'] == expected, 'Known control did not behave as expected')
        missing = grade(out / 'missing-input', out / 'missing')
        require(missing['status'] == 'invalid_artifact', 'Missing artifact accepted')
        summary['controls'].append({'name': 'missing', 'status': missing['status'], 'grade': missing['grade']})
        summary['status'] = 'pass'
    except (OSError, ValueError) as error:
        summary.update(status='fail', error=str(error))
    finally:
        save(out / 'selftest.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('grade')
    command.add_argument('--workspace', required=True)
    command.add_argument('--out', required=True)
    sub.add_parser('selftest').add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        result = selftest(args.out) if args.command == 'selftest' else grade(args.workspace, args.out)
        print(encode(result).decode(), end='')
        return 0 if result['status'] in {'valid', 'pass'} and result.get('grade', 'pass') == 'pass' else 1
    except (OSError, ValueError) as error:
        print(str(error))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
