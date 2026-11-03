#!/usr/bin/env python3
"""Portable Skill Lab entry point. Default operations are offline; no telemetry."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

import offline_acceptance
import fixture_runner

ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    Path(path).write_bytes(fixture_runner.encode(value))


def offline_report(out):
    """Re-read underlying executions; never trust a saved success label."""
    out = Path(out)
    try:
        result = offline_acceptance.verify(out)
        if offline_acceptance.read(out / 'report.json') != result:
            raise ValueError('Saved report differs from independently verified receipts')
        status, detail = 'pass', 'All seven executed controls match their expected outcomes.'
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'results': []}
        status, detail = 'incomplete', str(error)
    from experiment_report import clean
    decision = {'schema': 'skill-lab-preview-decision/1', 'status': status,
                'evidence_class': 'authored_offline_fixtures', 'model_calls': 0,
                'separately_billed_api_usd': 0, 'adoption': 'inconclusive',
                'safe_to_install': False, 'completed_controls': len(result['results']),
                'planned_controls': 7}
    text = (f'**Skill Lab offline preview: {status}. Adoption: inconclusive.**\n\n'
            f'{clean(detail)}\n\nExecuted controls: {len(result["results"])}/7. '
            'Known-good acceptance, known-bad rejection, activation/nonactivation, missing-artifact '
            'rejection and namespace checks are authored fixtures.\n\n'
            'Model calls: 0. Separately billed API use: $0. No model-performance conclusion. '
            'A passing fixture is not a safe-install certificate or an unseen holdout.\n\n'
            'Next action: inspect local receipts; prepare and independently review a skill-specific benchmark.\n')
    return decision, text


def report(out, export=None):
    out = Path(out)
    if (out / 'native-plan.json').exists():
        import preview_native
        decision, text = preview_native.report(out)
    elif (out / 'manifest.json').exists() and not (out / 'plan.json').exists():
        # Compatibility: existing engine's human report, including stopped runs.
        return subprocess.call([sys.executable, str(ROOT / 'skill-evaluator/scripts/experiment_report.py'), str(out)])
    else:
        decision, text = offline_report(out)
    print(text)
    if export:
        destination = Path(export)
        if destination.is_symlink() or any(p.is_symlink() for p in destination.parents):
            raise ValueError('Export symlinks refused')
        destination.mkdir(parents=True, exist_ok=False)
        # Fixed field summary only: no sources, paths, error strings, raw output or private answers.
        save(destination / 'decision.json', decision)
        (destination / 'REPORT.md').write_text(
            f'Skill Lab: {decision["status"]}. Adoption: inconclusive.\n\n'
            f'Evidence: {decision["evidence_class"]}. Safe to install: not established.\n'
            'This summary exports no task source, histories, answers or raw execution receipts.\n')
    return 0 if decision['status'] == 'pass' else 2


def run_offline(out):
    destination = Path(out)
    if destination.exists() or destination.is_symlink() or any(p.is_symlink() for p in destination.parents):
        raise ValueError('Run requires a fresh destination without symlinks; existing results are not rerun')
    try:
        offline_acceptance.run(out)
    except (KeyboardInterrupt, OSError, ValueError) as error:
        if not Path(out).exists():
            raise
        print('Execution stopped; retained evidence follows: ' + str(error), file=sys.stderr)
    return report(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    for name in ('setup', 'doctor', 'boundary'):
        item = commands.add_parser(name)
        item.add_argument('--state', type=Path, required=True)
        if name == 'setup':
            item.add_argument('--codex')
        if name == 'boundary':
            item.add_argument('--out', required=True)
    p = commands.add_parser('plan')
    p.add_argument('target', choices=['offline', 'codex-demo'])
    p.add_argument('--state', type=Path)
    p.add_argument('--instruction', action='append', default=[])
    p.add_argument('--disable-mcp', action='append', default=[])
    p.add_argument('--out')
    p = commands.add_parser('run')
    p.add_argument('target', choices=['offline', 'codex-demo'])
    p.add_argument('--out', required=True)
    p.add_argument('--plan')
    p.add_argument('--approved-plan-sha256')
    p = commands.add_parser('report')
    p.add_argument('run_directory')
    p.add_argument('--export')
    p = commands.add_parser('prepare')
    p.add_argument('--skill', required=True)
    p.add_argument('--pin', required=True)
    p.add_argument('--intended-use', required=True)
    p.add_argument('--example', action='append', default=[])
    p.add_argument('--draft')
    p.add_argument('--out', required=True)
    p = commands.add_parser('example')
    p.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'list':
            value = {'offline': 'seven executed public regression fixtures; no model calls',
                     'codex-demo': 'original normalization matched smoke; approval and fresh native boundary required',
                     'custom_skills': 'prepare + independent review + a new task adapter; no arbitrary automatic grader',
                     'historical_ub_frontend': 'not bundled; third-party rights and private study material excluded',
                     'claude': 'deferred; no fallback'}
        elif args.command in {'setup', 'doctor', 'boundary'}:
            import preview_environment as environment
            value = (environment.setup(args.state, codex=args.codex) if args.command == 'setup' else
                     environment.doctor(args.state) if args.command == 'doctor' else
                     environment.validate_boundary(args.state, args.out))
            print(json.dumps(value, indent=2))
            return 0 if value.get('status') in {'configured', 'ready', 'pass', 'offline_only'} else 2
        elif args.command == 'plan':
            if args.target == 'offline':
                value = offline_acceptance.plan()
            else:
                import preview_native
                if not args.state:
                    raise ValueError('--state required for codex-demo')
                value = preview_native.plan(args.state, args.instruction, args.disable_mcp)
            if args.out:
                with Path(args.out).open('xb') as stream:
                    stream.write(fixture_runner.encode(value))
            print(json.dumps(value, indent=2))
            print('Plan SHA256: ' + fixture_runner.sha(fixture_runner.encode(value)))
            return 0
        elif args.command == 'run':
            if args.target == 'offline':
                return run_offline(args.out)
            import preview_native
            preview_native.run(args.plan, args.approved_plan_sha256, args.out)
            return report(args.out)
        elif args.command == 'report':
            return report(args.run_directory, args.export)
        elif args.command == 'prepare':
            import preview_intake
            value = preview_intake.prepare(args.skill, args.intended_use, args.example,
                                           args.out, pin=args.pin, draft=args.draft)
        else:
            import preview_intake
            value = preview_intake.example(args.out)
        print(json.dumps(value, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('Skill Lab unavailable: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
