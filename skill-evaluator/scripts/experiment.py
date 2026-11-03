#!/usr/bin/env python3
"""Run the existing bounded experiment, then emit its verified decision report."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def inside(value, root):
    if root.resolve() != root.absolute():
        raise ValueError('Output root must not use symlinks')
    path = Path(value)
    path = path if path.is_absolute() else ROOT / path
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        raise ValueError('Output must be a new child of ' + str(root))
    if path.exists() or path.is_symlink():
        raise ValueError('Existing output refused: ' + str(path))
    if any(parent.is_symlink() for parent in path.parents):
        raise ValueError('Output parent symlink refused')
    return resolved


def run(target, output, *, agent='codex', trials=1, active_token_limit=None, report_out=None):
    # Check both fresh destinations BEFORE inference. An existing successful report
    # must not be accidentally printed after a refused/reused run directory.
    out = inside(output, ROOT / 'runs')
    report_dir = inside(report_out or ROOT / 'evidence/experiment-reports' / out.name, ROOT / 'evidence')
    if target != 'ub-project' and (active_token_limit is not None or trials != 1):
        raise ValueError('Repetitions and token overrides are only supported for ub-project')
    if target == 'ub-project' and agent != 'codex':
        raise ValueError('Project experiments currently support Codex only')
    runner = ROOT / 'skill-evaluator/scripts/lab.py'
    reporter = ROOT / 'skill-evaluator/scripts/experiment_report.py'
    if not runner.is_file() or not reporter.is_file():
        raise ValueError('Missing experiment runner or report formatter')
    command = [sys.executable, str(runner), 'run', target, '--agent', agent, '--out', str(out)]
    if target == 'ub-project':
        command += ['--trials', str(trials)]
        if active_token_limit is not None:
            command += ['--active-token-limit', str(active_token_limit)]
    status = subprocess.call(command, cwd=ROOT)
    # Even a token-limit/infrastructure stop gets a truthful report when the
    # underlying runner preserved one. No retry or account/model substitution.
    if not (out / 'report.json').is_file():
        print('Experiment incomplete: no result artifact was produced; no adoption conclusion.', file=sys.stderr)
        return status or 2
    reported = subprocess.call([sys.executable, str(reporter), str(out), '--out', str(report_dir)], cwd=ROOT)
    return status or reported


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    execute = commands.add_parser('run')
    execute.add_argument('target', choices=['ub', 'frontend', 'ub-project'])
    execute.add_argument('--out', required=True)
    execute.add_argument('--agent', choices=['codex', 'claude'], default='codex')
    execute.add_argument('--trials', choices=[1, 2], type=int, default=1)
    execute.add_argument('--active-token-limit', choices=[128000, 256000, 512000], type=int)
    execute.add_argument('--report-out')
    report = commands.add_parser('report')
    report.add_argument('run_directory')
    report.add_argument('--out')
    args = parser.parse_args(argv)
    try:
        if args.command == 'run':
            return run(args.target, args.out, agent=args.agent, trials=args.trials,
                       active_token_limit=args.active_token_limit, report_out=args.report_out)
        command = [sys.executable, str(ROOT / 'skill-evaluator/scripts/experiment_report.py'), args.run_directory]
        if args.out:
            command += ['--out', args.out]
        return subprocess.call(command, cwd=ROOT)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Experiment unavailable: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
