#!/usr/bin/env python3
"""Execute the release in a fake home with original checkout/home absent from mounts."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def worker(root):
    records = []
    env = dict(os.environ, HOME=str(root / 'home'), CODEX_HOME=str(root / 'home/.codex'), PYTHONDONTWRITEBYTECODE='1')
    (root / 'home').mkdir()
    (root / 'work').mkdir()
    release = root / 'unpacked/skill-lab-preview'
    destination = root / 'home/.codex/skills/skill-lab'
    install = release / 'skill-lab/scripts/install.py'
    launcher = destination / 'scripts/launcher.py'

    def command(label, args, expected=0, prefix=None):
        process = subprocess.run([*(prefix or [sys.executable]), *map(str, args)], cwd=root / 'work', env=env,
                                 capture_output=True, text=True, timeout=240)
        record = {'label': label, 'argv': list(map(str, args)), 'exit_code': process.returncode,
                  'stdout': process.stdout, 'stderr': process.stderr}
        records.append(record)
        (root / 'commands.json').write_text(json.dumps(records, indent=2))
        if process.returncode != expected:
            raise ValueError(f'{label}: expected exit {expected}, got {process.returncode}; inspect commands.json')
        return process

    command('install', [install, '--project-root', release, '--destination', destination, '--apply'])
    command('idempotent-update', [install, '--project-root', release, '--destination', destination, '--apply'])
    assert not Path('/home').exists(), 'Real home must not be visible'
    parked = root / 'parked-release'
    release.rename(parked)
    command('installed-list-with-source-absent', [launcher, 'list'])
    setup = [launcher, 'setup', '--state', root / 'state']
    if Path('/opt/codex/bin/codex').exists():
        setup += ['--codex', '/opt/codex/bin/codex']
    command('setup', setup)
    command('doctor', [launcher, 'doctor', '--state', root / 'state'])
    command('offline-plan', [launcher, 'plan', 'offline'])
    command('offline-run', [launcher, 'run', 'offline', '--out', root / 'offline'])
    command('success-report', [launcher, 'report', root / 'offline', '--export', root / 'export'])
    command('actual-infrastructure-failure', [launcher, 'run', 'offline', '--out', root / 'actual-infrastructure'], expected=2,
            prefix=['/usr/bin/bwrap', '--ro-bind', '/', '/', '--bind', str(root), str(root),
                    '--ro-bind', '/dev/null', '/usr/bin/bwrap', '--', sys.executable])
    command('actual-infrastructure-report', [launcher, 'report', root / 'actual-infrastructure'], expected=2)
    command('extension-example', [launcher, 'example', '--out', root / 'example'])
    command('fresh-boundary', [launcher, 'boundary', '--state', root / 'state', '--out', root / 'boundary'])
    # Inject faults into copied evidence; original successful evidence remains untouched.
    for label in ('missing', 'empty', 'skipped', 'tampered', 'infrastructure', 'interrupted'):
        copied = root / label
        shutil.copytree(root / 'offline', copied)
        receipt = copied / 'positive/receipt.json'
        data = json.loads(receipt.read_text())
        if label == 'missing':
            (copied / 'positive/solution.py').unlink()
        elif label == 'empty':
            (copied / 'positive/solution.py').write_text('')
        elif label == 'skipped':
            data['cases'] = data['cases'][:-1]
        elif label == 'tampered':
            data['passed'] = 999
        elif label == 'infrastructure':
            data['processes'][0]['status'] = 'infrastructure_error'
        else:
            shutil.rmtree(copied / 'activation-good')
            (copied / 'report.json').write_text('{"status":"incomplete"}')
        receipt.write_text(json.dumps(data))
        command(label + '-report-rejected', [launcher, 'report', copied], expected=2)
    # Actually interrupt a running fixture journey; report retained partial execution.
    p = subprocess.Popen([sys.executable, str(launcher), 'run', 'offline', '--out', str(root / 'actual-interrupted')],
                         cwd=root / 'work', env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    import signal
    import time
    deadline = time.monotonic() + 10
    while not (root / 'actual-interrupted/plan.json').exists() and p.poll() is None and time.monotonic() < deadline:
        time.sleep(.01)
    if p.poll() is None:
        os.killpg(p.pid, signal.SIGINT)
    stdout, stderr = p.communicate(timeout=15)
    (root / 'interrupt.json').write_text(json.dumps({'exit_code': p.returncode, 'stdout': stdout, 'stderr': stderr}))
    command('actual-interrupted-report', [launcher, 'report', root / 'actual-interrupted'], expected=2)
    # Exercise adoption through the installed artifact with the source parked.
    # These are authored software controls and consume no model budget.
    answers = root / 'decision-answers.json'
    answers.write_text(json.dumps({'intended_work': 'Python maintenance', 'improvement': 'Prevent defects',
        'max_regression': 0, 'max_token_overhead': .5, 'max_time_overhead': .5, 'budget': 'deeper'}))
    command('adoption-chat-intake', [launcher, 'adoption', 'intake', '--skill', destination / 'kit/fixtures/benign',
            '--pin', 'a' * 40, '--answers', answers, '--out', root / 'decision-intake'])
    assert not json.loads((root / 'decision-intake/decision.json').read_text())['questions']
    adoption = root / 'adoption'
    command('adoption-example', [launcher, 'adoption', 'example', '--out', adoption])
    proposal = adoption / 'proposal.json'
    command('adoption-adequacy-review', [launcher, 'adoption', 'review', '--proposal', proposal])
    for budget in ('quick', 'balanced', 'deeper'):
        command('adoption-budget-' + budget,
                [launcher, 'adoption', 'plan', '--proposal', proposal, '--budget', budget,
                 '--out', root / ('adoption-' + budget + '-plan.json')])
    plans = [json.loads((root / ('adoption-' + name + '-plan.json')).read_text())
             for name in ('quick', 'balanced', 'deeper')]
    assert [len(plan['schedule']) for plan in plans] == [6, 10, 14]
    assert all(plan['total_token_threshold'] == 0 for plan in plans)
    command('adoption-offline-run', [launcher, 'adoption', 'run',
            '--plan', root / 'adoption-quick-plan.json', '--out', root / 'adoption-run'])
    command('adoption-verified-report', [launcher, 'adoption', 'report', root / 'adoption-run'])
    command('adoption-executed-scenarios', [launcher, 'adoption', 'exercise',
            '--out', root / 'adoption-exercise'])
    exercise = json.loads((root / 'adoption-exercise/exercise.json').read_text())
    assert exercise.get('schema') == 'skill-lab-adoption-exercise/1'
    assert exercise.get('status') == 'pass' and exercise.get('scenarios'), 'Adoption scenarios must execute'
    assert {row['scenario'] for row in exercise['scenarios']} == {
        'improvement', 'regression', 'equal', 'overhead', 'workflow',
        'missing', 'skipped', 'tampered', 'interrupted', 'infrastructure'}
    executed = json.loads((root / 'adoption-run/execution.json').read_text())
    assert executed['status'] == 'completed' and len(executed['attempts']) == 6
    assert (root / 'adoption-run/adoption-plan.json').is_file()
    assert (root / 'adoption-run/REPORT.md').read_text().strip(), 'Adoption report must be generated'
    # Runtime drift must never silently re-pin.
    config = root / 'state/runtime/python/bin/python'
    config.write_text(config.read_text() + '# changed\n')
    command('changed-pin-doctor', [launcher, 'doctor', '--state', root / 'state'], expected=2)
    parked.rename(release)
    note = destination / 'owner-note.txt'
    note.write_text('preserve unrelated')
    owner_skill = destination / 'SKILL.md'
    owner_skill.write_text('preserve owner edit')
    command('edited-update-refused', [install, '--project-root', release, '--destination', destination, '--apply'], expected=2)
    command('owned-removal', [install, '--remove', '--destination', destination, '--apply'])
    assert owner_skill.read_text() == 'preserve owner edit'
    assert note.read_text() == 'preserve unrelated'
    assert not launcher.exists()
    result = {'status': 'pass', 'evidence_class': 'executed_release_software_and_fake_security_controls',
              'real_model_calls': 0, 'checks': len(records), 'original_home_checkout_visible': False,
              'native_boundary': json.loads((root / 'boundary/report.json').read_text())['native'],
              'adoption': {'status': exercise['status'], 'evidence_class': 'authored_software_controls',
                           'budget_choices': ['quick', 'balanced', 'deeper']},
              'limits': 'Injected evidence faults are regression tests, not real model performance. Native auth/live comparison untested.'}
    (root / 'acceptance.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--codex', type=Path)
    parser.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args._worker:
        worker(args.out)
        return 0
    out = args.out.absolute()
    out.mkdir(parents=True, exist_ok=False)
    with tarfile.open(args.artifact, 'r:gz') as archive:
        seen, total = set(), 0
        for item in archive.getmembers():
            total += item.size
            if item.name in seen or len(seen) >= 500 or total > 20 * 1024**2:
                raise ValueError('Duplicate or oversized release archive')
            seen.add(item.name)
            path = Path(item.name)
            if (path.is_absolute() or '..' in path.parts or not item.isfile() or item.size > 5 * 1024**2
                    or not path.parts or path.parts[0] != 'skill-lab-preview'):
                raise ValueError('Unsafe release archive member')
            target = out / 'unpacked' / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(item).read())
    command = ['/usr/bin/bwrap', '--unshare-all', '--die-with-parent', '--new-session', '--cap-drop', 'ALL',
               '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64',
               '--symlink', 'usr/bin', '/bin', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
               '--bind', str(out), '/test', '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
               '--setenv', 'PYTHONDONTWRITEBYTECODE', '1']
    if args.codex:
        # Only public executable distribution, never its real profile or home.
        command += ['--ro-bind', str(args.codex.resolve().parent.parent), '/opt/codex']
    command += ['--', '/usr/bin/python3', '/test/unpacked/skill-lab-preview/acceptance.py', '--_worker', '--out', '/test']
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    (out / 'outer.json').write_text(json.dumps({'argv': command, 'exit_code': result.returncode,
                                               'stdout': result.stdout, 'stderr': result.stderr}, indent=2))
    print(result.stdout)
    print(result.stderr, file=sys.stderr)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
