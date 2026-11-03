#!/usr/bin/env python3
"""Freeze agent-authored skill/history benchmarks. No model dispatch or candidate execution."""
import argparse
from pathlib import Path
import re
import sys

import evaluate as evaluator

ORIGIN = 'explicit local skill snapshot for benchmark preparation'
BOUNDARY = ('Private directories are not OS isolation. A runner must exclude private/ from candidate '
            'mounts. Authored cases are not scientifically unseen holdouts. No other-user history is '
            'collected or published; shareable is consent metadata, not an export operation.')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def workspace(task):
    if 'files' not in task and 'check_command' not in task:
        return {}
    files, command = task.get('files'), task.get('check_command')
    require(isinstance(files, dict) and files, 'Workspace files must be a nonempty map')
    require(isinstance(command, list) and command and all(text(arg) and '\x00' not in arg for arg in command),
            'Workspace check_command must be a nonempty argv list')
    reserved = {'AGENTS.md', 'SKILL.md', 'CLAUDE.md', '.git', '.codex', '.agents'}
    for name, content in files.items():
        require(text(name) and '\\' not in name and '\x00' not in name and ':' not in name
                and all(part not in {'', '.', '..'} for part in name.split('/')),
                'Workspace paths must be safe relative paths')
        require(not any(part in reserved for part in name.split('/')), 'Reserved workspace path')
        require(isinstance(content, str), 'Workspace contents must be text, not file links')
        require(not any(other.startswith(name + '/') for other in files), 'Workspace file/directory collision')
    return {'files': files, 'check_command': command}


def compile_artifacts(source, pin, history, draft):
    profile = evaluator.intake(source, ORIGIN, pin)
    require(isinstance(history, dict) and isinstance(draft, dict), 'History and draft must be objects')
    require(history.get('schema') == 1 and draft.get('schema') == 1, 'Expected schema 1')
    entries = history.get('entries')
    require(isinstance(entries, list) and entries, 'Explicit sanitized history entries required')
    ids = set()
    for entry in entries:
        require(isinstance(entry, dict), 'History entries must be objects')
        require(set(entry) == {'id', 'text', 'shareable', 'provenance'}, 'History entry schema mismatch')
        require(all(text(entry[key]) for key in ['id', 'text', 'provenance']), 'History text and provenance required')
        require(entry['id'] not in ids and type(entry['shareable']) is bool, 'Duplicate history id or invalid consent')
        ids.add(entry['id'])
    skill_files = [source / name for name in ['SKILL.md', 'SKILL.md.review.txt'] if (source / name).is_file()]
    require(len(skill_files) == 1, 'Provide exactly one SKILL.md or SKILL.md.review.txt')
    skill_text = skill_files[0].read_text()
    require(text(draft.get('candidate')) and text(draft.get('intervention')), 'Candidate and intervention labels required')
    tasks = draft.get('tasks')
    require(isinstance(tasks, list) and tasks, 'Nonempty task draft required')
    checks, manifests, packets = {}, [], {}
    for task in tasks:
        require(isinstance(task, dict), 'Tasks must be objects')
        task_id = task.get('id')
        require(isinstance(task_id, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', task_id), 'Unsafe task id')
        require(task_id not in checks, 'Duplicate task id')
        quotes, history_ids = task.get('skill_quotes'), task.get('history_ids')
        require(isinstance(quotes, list) and quotes and all(text(q) and q in skill_text for q in quotes),
                f'{task_id}: exact skill quotes required')
        require(isinstance(history_ids, list) and history_ids and all(isinstance(i, str) and i in ids for i in history_ids)
                and len(set(history_ids)) == len(history_ids), f'{task_id}: valid distinct history ids required')
        active, negative = task.get('should_activate'), task.get('negative_control')
        require(type(active) is bool and type(negative) is bool and active != negative,
                f'{task_id}: explicit consistent activation/control flags required')
        checks[task_id] = task['checks']
        manifests.append({key: value for key, value in task.items() if key != 'checks'})
        # These packets are the only files a future runner should supply to a candidate.
        packet = {'task_id': task_id, 'prompt': task['prompt'],
                  'cases': [{'id': f'case-{i+1}', 'input': check['input']}
                            for i, check in enumerate(task['checks'])], **workspace(task)}
        packets[f'candidate/{task_id}/baseline.json'] = packet
        packets[f'candidate/{task_id}/skill.json'] = {**packet, 'skill': skill_text}
    require({t['should_activate'] for t in tasks} == {False, True}, 'Activation and nonactivation cases required')
    purpose = {**draft['purpose'], 'reviewer': 'benchmark author; independent semantic review pending',
               'source_sha256': profile['inventory_sha256']}
    manifest = {'schema': 1, 'candidate': draft['candidate'], 'intervention': draft['intervention'],
                'study_scope': draft.get('study_scope', 'complete'),
                'purpose': purpose, 'tasks': manifests, 'trials': 2, 'seed': 19,
                'thresholds': {'min_score_gain': 0.1, 'max_token_ratio': 1.5, 'max_regressions': 0},
                'matched_controls': {'condition_delta': 'exact frozen skill text only',
                                     'environment': 'REQUIRED isolated runner before dispatch'}}
    plan = evaluator.plan(profile, manifest)
    frozen = evaluator.freeze(plan, checks)
    values = {'private/history.json': history, 'private/draft.json': draft,
              'private/profile.json': profile, 'private/manifest.json': manifest,
              'private/plan.json': plan, 'private/frozen.json': frozen, **packets}
    artifacts = {name: evaluator.encode(value) for name, value in values.items()}
    for item in profile['files']:
        raw = (source / item['path']).read_bytes()
        require(evaluator.digest(raw) == item['sha256'], 'Source changed during snapshot')
        artifacts['private/source/' + item['path']] = raw
    return artifacts


def metadata(artifacts, pin):
    return {'schema': 1, 'pin': pin, 'classification': 'agent-authored benchmark preparation',
            'semantic_review': 'independent review required', 'boundary': BOUNDARY,
            'model_calls': 0, 'external_execution': 'disabled',
            'builder_sha256': evaluator.digest(Path(__file__).read_bytes()),
            'evaluator_sha256': evaluator.digest(Path(evaluator.__file__).read_bytes()),
            'files': {name: evaluator.digest(raw) for name, raw in sorted(artifacts.items())}}


def build(skill, pin, history_path, draft_path, out):
    skill, out = Path(skill), Path(out)
    require(not skill.is_symlink(), 'Symlink skill directory refused')
    require(not out.is_symlink(), 'Symlink output directory refused')
    source, destination = skill.resolve(), out.resolve()
    require(not destination.is_relative_to(source), 'Output cannot be inside the skill snapshot')
    for path in [history_path, draft_path]:
        require(not Path(path).resolve().is_relative_to(destination), 'Output cannot contain an input')
    artifacts = compile_artifacts(source, pin, evaluator.read(history_path), evaluator.read(draft_path))
    destination.mkdir(parents=True, exist_ok=False)
    for name, raw in artifacts.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as handle:
            handle.write(raw)
    evaluator.write(destination / 'bundle.json', metadata(artifacts, pin))
    return validate(destination)


def validate(out):
    out = Path(out)
    require(out.is_dir() and not out.is_symlink(), 'Regular benchmark directory required')
    found = set()
    for path in out.rglob('*'):
        require(not path.is_symlink(), 'Bundle symlinks refused')
        if path.is_file():
            found.add(path.relative_to(out).as_posix())
    bundle = evaluator.read(out / 'bundle.json')
    artifacts = compile_artifacts(out / 'private/source', bundle['pin'],
                                  evaluator.read(out / 'private/history.json'),
                                  evaluator.read(out / 'private/draft.json'))
    require(bundle == metadata(artifacts, bundle['pin']), 'Changed source, draft, history, or tool version')
    require(found == set(artifacts) | {'bundle.json'}, 'Missing or unexpected bundle artifact')
    for name, raw in artifacts.items():
        require((out / name).read_bytes() == raw, f'Changed artifact: {name}')
    return {'status': 'prepared', 'model_calls': 0, 'external_execution': 'disabled',
            'bundle_sha256': evaluator.digest((out / 'bundle.json').read_bytes()),
            'tasks': len(evaluator.read(out / 'private/manifest.json')['tasks']),
            'semantic_review': 'independent review required', 'boundary': BOUNDARY}


def selftest(out):
    validation = validate(out)
    frozen = evaluator.read(Path(out) / 'private/frozen.json')
    controls = []
    for task_id, checks in frozen['checks'].items():
        good = {'task_id': task_id, 'answers': [c['expected'] for c in checks]}
        bad = {'task_id': task_id, 'answers': [None if c['expected'] is not None else False for c in checks]}
        accepted, rejected = evaluator.grade(frozen, good), evaluator.grade(frozen, bad)
        require(accepted['score'] == 1 and rejected['score'] == 0, 'Grader failed known-good/bad control')
        try:
            evaluator.grade(frozen, {'task_id': task_id, 'answers': []})
        except ValueError:
            pass
        else:
            raise ValueError('Grader accepted missing answers')
        controls.append({'task_id': task_id, 'cases': len(checks), 'known_good': accepted,
                         'known_bad': rejected, 'missing_answers': 'rejected'})
    require(controls, 'Empty selftest forbidden')
    return {'status': 'pass', 'classification': 'authored oracle replay only; no skill/model executed',
            'model_calls': 0, 'bundle_sha256': validation['bundle_sha256'], 'controls': controls}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prepare = sub.add_parser('build')
    for name in ['skill', 'pin', 'history', 'draft', 'out']:
        prepare.add_argument('--' + name, required=True)
    for name in ['validate', 'selftest']:
        sub.add_parser(name).add_argument('--out', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'build':
            result = build(args.skill, args.pin, args.history, args.draft, args.out)
        else:
            result = (validate if args.command == 'validate' else selftest)(args.out)
        print(evaluator.encode(result).decode(), end='')
        return 0
    except (OSError, ValueError, KeyError, TypeError, UnicodeError) as error:
        print(f'Benchmark preparation failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
