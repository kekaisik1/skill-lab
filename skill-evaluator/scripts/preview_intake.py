#!/usr/bin/env python3
"""Explicit, inert local selections and source-bound benchmark preparation."""
import argparse
from pathlib import Path
import sys

import benchmark_builder
import evaluate
import skill_intake as skill_safety

ROOT = Path(__file__).resolve().parents[2]
MAX_EXAMPLES = 20
REQUIREMENTS = [
    'Quote exact selected skill claims and link each task to selected example IDs.',
    'State intended outcomes and independent acceptance criteria before candidate execution.',
    'Include activation and nonactivation cases and honest difficulty coverage.',
    'Independent semantic review and a trusted executable task adapter are required before live use.',
    'Keep grader, private references, expected answers and other cases outside candidate mounts.',
    'Public example cases are regression fixtures, not scientifically unseen holdouts.',
]


def require(ok, message):
    if not ok:
        raise ValueError(message)


def no_links(path):
    path = Path(path).absolute()
    require(not any(evaluate.is_link(part) for part in (path, *path.parents)),
            'Symlinks in selected or output paths are refused')
    return path


def selected_file(path):
    path = no_links(path)
    require(path.is_file() and path.stat().st_size <= evaluate.MAX_FILE,
            'Select a bounded regular file, not a directory or special file')
    raw = path.read_bytes()
    require(len(raw) <= evaluate.MAX_FILE, 'Selected file grew beyond size limit')
    return raw


def prepare(skill, intended_use, examples, out, *, pin=None, draft=None):
    """Never import or execute selected content. Only a supplied draft is compiled."""
    require(isinstance(intended_use, str) and intended_use.strip(), 'Intended use is required')
    require(len(intended_use) <= 10000, 'Intended use exceeds size limit')
    require(len(examples) <= MAX_EXAMPLES, 'Select at most 20 individual example files')
    require(draft is None or examples, 'A benchmark draft requires explicit example files for its history mappings')
    source, destination = no_links(skill), no_links(out)
    require(not destination.exists(), 'Choose a fresh output directory')
    require(not destination.is_relative_to(source), 'Output cannot be inside selected skill')
    require(not source.is_relative_to(destination), 'Output cannot contain selected skill')
    profile = skill_safety.inspect_skill(source, pin or '0' * 64)
    if pin is None:
        pin = profile['inventory_sha256']
        profile = skill_safety.inspect_skill(source, pin)
    artifacts = {}
    for row in profile['files']:
        raw = selected_file(source / row['path'])
        require(evaluate.digest(raw) == row['sha256'], 'Skill changed during selection')
        artifacts['private/skill/' + row['path']] = raw
    history, selections, seen, total = [], [], set(), 0
    for index, path in enumerate(examples, 1):
        path = no_links(path)
        require(path not in seen, 'Duplicate selected example')
        require(not path.is_relative_to(destination), 'Output cannot contain selected examples')
        seen.add(path)
        raw = selected_file(path)
        total += len(raw)
        require(total <= evaluate.MAX_TOTAL, 'Selected examples exceed total size limit')
        content = raw.decode('utf-8')
        require(content.strip(), 'Empty example is incomplete input')
        identifier = f'example-{index}'
        artifacts[f'private/examples/{identifier}.txt'] = raw
        selections.append({'id': identifier, 'name': path.name, 'sha256': evaluate.digest(raw), 'bytes': len(raw)})
        history.append({'id': identifier, 'text': content, 'shareable': False,
                        'provenance': 'Explicit local file selected by owner; hash recorded in proposal'})
    history_value = {'schema': 1, 'entries': history}
    artifacts['private/history.json'] = evaluate.encode(history_value)
    artifacts['private/security.json'] = evaluate.encode(profile)
    draft_value = None
    if draft is not None:
        selected_file(draft)
        draft_value = evaluate.read(draft)
        # Validate all claims and task mappings before leaving any output.
        benchmark_builder.compile_artifacts(source, pin, history_value, draft_value)
        artifacts['private/draft.json'] = evaluate.encode(draft_value)
    proposal = {
        'schema': 'skill-lab-intake/1', 'status': 'prepared' if draft else 'needs_authoring',
        'intended_use': intended_use.strip(), 'declared_pin': pin,
        'inventory_sha256': profile['inventory_sha256'],
        'pin_verification': 'Exact local bytes inventoried; publisher identity and declared revision unverified',
        'selected_examples': selections, 'selection_policy': 'Only explicitly selected files; no history discovery',
        'model_calls': 0, 'candidate_execution': False, 'uploads': False,
        'safe_to_install': False, 'independent_review': 'required',
        'adapter': 'not provisioned; benchmark compiler and oracle replay only',
        'requirements': REQUIREMENTS,
        'tasks': [] if draft is None else [{key: task[key] for key in
            ['id', 'capability', 'skill_quotes', 'history_ids', 'should_activate', 'negative_control',
             'acceptance', 'adaptation_rationale']} for task in draft_value['tasks']],
        'sharing': 'Local preparation includes selected source and expected answers. Do not share this directory implicitly.',
    }
    artifacts['proposal.json'] = evaluate.encode(proposal)
    destination.mkdir(parents=True)
    for name, raw in artifacts.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as handle:
            handle.write(raw)
    if draft is not None:
        benchmark_builder.build(destination / 'private/skill', pin, destination / 'private/history.json',
                                destination / 'private/draft.json', destination / 'benchmark')
    evaluate.write(destination / 'selection-manifest.json', {
        'schema': 'skill-lab-selection/1',
        'files': {name: evaluate.digest(raw) for name, raw in sorted(artifacts.items())},
        'benchmark': draft is not None,
        'intake_sha256': evaluate.digest(Path(__file__).read_bytes()),
    })
    return validate(destination)


def validate(out):
    out = no_links(out)
    manifest = evaluate.read(out / 'selection-manifest.json')
    require(manifest.get('schema') == 'skill-lab-selection/1', 'Unsupported selection schema')
    require(manifest['intake_sha256'] == evaluate.digest(Path(__file__).read_bytes()), 'Changed intake version')
    require(isinstance(manifest.get('files'), dict) and manifest['files'], 'Missing selection artifacts')
    for name, expected in manifest['files'].items():
        require(not skill_safety.unsafe_relative(name), 'Unsafe selection artifact path')
        require(evaluate.digest(selected_file(out / name)) == expected, 'Changed selection artifact: ' + name)
    proposal = evaluate.read(out / 'proposal.json')
    security = skill_safety.inspect_skill(out / 'private/skill', proposal['declared_pin'])
    require(security['inventory_sha256'] == proposal['inventory_sha256'], 'Changed selected skill inventory')
    found = set()
    for path in out.rglob('*'):
        no_links(path)
        require(path.is_file() or path.is_dir(), 'Special selection artifact refused')
        if path.is_file() and not path.is_relative_to(out / 'benchmark'):
            found.add(path.relative_to(out).as_posix())
    require(found == set(manifest['files']) | {'selection-manifest.json'}, 'Missing or unexpected selection artifact')
    benchmark = benchmark_builder.validate(out / 'benchmark') if manifest['benchmark'] else None
    require(manifest['benchmark'] or not (out / 'benchmark').exists(), 'Unexpected benchmark')
    return {'status': proposal['status'], 'model_calls': 0, 'independent_review': 'required',
            'safe_to_install': False, 'candidate_execution': False,
            'inventory_sha256': proposal['inventory_sha256'], 'examples': len(proposal['selected_examples']),
            'proposal_sha256': evaluate.digest((out / 'proposal.json').read_bytes()), 'benchmark': benchmark}


def example(out):
    source = ROOT / 'examples/portable'
    result = prepare(source / 'skill',
                     'Repair Python normalization; explain without activating on read-only requests.',
                     [source / 'selected/request.txt'], out, draft=source / 'draft.json')
    return {**result, 'controls': benchmark_builder.selftest(Path(out) / 'benchmark'),
            'classification': 'Original public regression fixtures; no candidate or model executed'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare')
    prepare_parser.add_argument('--skill', required=True)
    prepare_parser.add_argument('--intended-use', required=True)
    prepare_parser.add_argument('--example', action='append', default=[])
    prepare_parser.add_argument('--pin')
    prepare_parser.add_argument('--draft')
    prepare_parser.add_argument('--out', required=True)
    for name in ['validate', 'example']:
        commands.add_parser(name).add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare(args.skill, args.intended_use, args.example, args.out, pin=args.pin, draft=args.draft)
        else:
            result = (validate if args.command == 'validate' else example)(args.out)
        print(evaluate.encode(result).decode(), end='')
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        print(f'Skill Lab intake failed: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
