#!/usr/bin/env python3
"""Verify one saved experiment and render an adoption-oriented report; never run a model."""
import argparse
import html
import json
import math
from pathlib import Path
import re
import shlex
import statistics
import subprocess
import sys
from urllib.parse import quote

import lab
try:
    import project_study
except ModuleNotFoundError as error:
    if error.name != 'project_study':
        raise
    # Private project benchmarks are deliberately excluded from portable releases.
    project_study = None


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def clean(value):
    text = html.escape(re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', str(value)), quote=False)
    return re.sub(r'([\\`*_{}\[\]()#+!|>])', r'\\\1', text)


def link(label, path):
    return f'[{label}](<{quote(str(Path(path).resolve()), safe="/")}>)'


def schedule(manifest):
    if manifest['target'] == 'ub-project':
        readonly = {t['id']: t['readonly'] for t in manifest['tasks']}
        return [dict(row, readonly=readonly[row['task_id']]) for row in manifest['schedule']]
    return [{'name': name, 'task_id': 'explanation' if readonly else manifest['target'], 'trial': 0,
             'condition': 'skill' if skill else 'baseline', 'readonly': readonly}
            for name, skill, readonly in lab.SCHEDULE]


def native_usage(raw, agent):
    """Read actual native events, including a host attempt that never reached grading."""
    if agent == 'codex':
        events = raw.get('raw_events', [])
        responses = {event['params']['responseId'] for event in events
                     if event.get('method') == 'rawResponse/completed'}
        counters = [event['params']['tokenUsage'] for event in events
                    if event.get('method') == 'thread/tokenUsage/updated']
        totals = counters[-1].get('total', {}) if counters else {}
        result = {'model_responses': len(responses),
                  'tool_calls': sum(event.get('method') == 'item/tool/call' for event in events),
                  'input_tokens': totals.get('inputTokens'), 'output_tokens': totals.get('outputTokens'),
                  'cached_input_tokens': totals.get('cachedInputTokens'),
                  'wall_seconds': raw.get('wall_seconds'), 'subscription_units': None, 'usd': None}
        incoming, outgoing = result['input_tokens'], result['output_tokens']
        # Cached input is already part of Codex input_tokens; do not add it again.
        result['total_tokens'] = incoming + outgoing if number(incoming) and number(outgoing) else None
        return result
    events = []
    for line in raw.get('stdout', '').splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    ids = {e.get('message', {}).get('id') for e in events if e.get('type') == 'assistant'} - {None, ''}
    return {'model_responses': len(ids), 'input_tokens': None, 'output_tokens': None,
            'cached_input_tokens': None, 'total_tokens': None, 'wall_seconds': raw.get('wall_seconds'),
            'tool_calls': sum(b.get('type') == 'tool_use' for e in events if e.get('type') == 'assistant'
                              for b in e.get('message', {}).get('content', []))}


def outcome(row, readonly):
    if row.get('status') != 'completed':
        return None, 'not completed'
    if readonly:
        return float(row.get('grade') == 'pass'), row.get('grade', 'unknown')
    functional = row.get('functionality', {})
    passed, total = functional.get('passed'), functional.get('total')
    if (functional.get('status') != 'valid' or type(passed) is not int or type(total) is not int
            or not 0 <= passed <= total or total == 0 or functional.get('skipped', 0)
            or functional.get('expected_cases', total) != total):
        return None, 'not graded / invalid'
    return passed / total, f'{passed}/{total} checks'


def assess(run, manifest, report):
    planned = schedule(manifest)
    results, attempts, observations = report.get('results', {}), {}, []
    if manifest['target'] != 'ub-project':
        task_id = results.get('baseline', {}).get('functionality', {}).get('task_id', manifest['target'])
        for item in planned:
            if not item['readonly']:
                item['task_id'] = task_id
    for item in planned:
        name = item['name']
        path = run / name / 'host.json'
        if path.is_file():
            raw = lab.read(path)
            attempts[name] = dict(native_usage(raw, manifest.get('agent', 'codex')),
                                  host_status=raw.get('status'), graded=name in results and results[name].get('status') == 'completed')
        row = results.get(name, {})
        score, label = outcome(row, item['readonly'])
        observations.append({**item, 'score': score, 'outcome': label,
                             'tool_calls': attempts.get(name, {}).get('tool_calls'),
                             'workflow': row.get('workflow', {}).get('status', 'not assessed')})
    pairs, gains = [], []
    for task_id, trial in dict.fromkeys((r['task_id'], r['trial']) for r in planned):
        arms = {r['condition']: r for r in observations if (r['task_id'], r['trial']) == (task_id, trial)}
        both = all(c in arms and arms[c]['score'] is not None for c in ('baseline', 'skill'))
        gain = arms['skill']['score'] - arms['baseline']['score'] if both else None
        readonly = next(iter(arms.values()))['readonly']
        pairs.append({'task_id': task_id, 'trial': trial, 'readonly': readonly, 'arms': arms, 'gain': gain})
        if both and not readonly:
            gains.append(gain)
    controls = report.get('fixture_controls', {})
    controls_pass = controls.get('status') in {'pass', 'valid'} and controls.get('grade', 'pass') == 'pass'
    fixture_only = report.get('synthetic') is True or ('fixture' in report.get('classification', '').lower()
                    and 'real' not in report.get('classification', '').lower())
    complete = (bool(planned) and not fixture_only and controls_pass and not report.get('failure')
                and report.get('status') in {'pass', 'fail', 'completed'}
                and all(r['score'] is not None and attempts.get(r['name'], {}).get('model_responses', 0) > 0
                        and attempts[r['name']].get('host_status') == 'completed'
                        for r in observations))
    scope = [r for r in observations if r['readonly']]
    scope_pass = bool(scope) and all(r['score'] == 1 and r['tool_calls'] == 0 for r in scope)
    regressions = sum(g < 0 for g in gains)
    mean = statistics.mean(gains) if gains else None
    constraints = all(row.get('workspace_audit', {}).get('status') == 'pass'
                      for name, row in results.items() if any(r['name'] == name and not r['readonly'] for r in planned))
    if not complete:
        decision, recommendation = 'inconclusive', 'Inconclusive: this run does not establish whether to adopt the skill.'
    elif regressions or not scope_pass or not constraints:
        decision, recommendation = 'do_not_adopt', 'Do not adopt for these tested tasks yet: regressions or scope/preservation failures occurred.'
    elif mean is not None and mean <= 0:
        decision, recommendation = 'benefit_not_demonstrated', 'Benefit not demonstrated: the skill did not improve the tested functional outcomes.'
    elif report.get('decision') == 'promising_on_these_tasks_confirm_on_broader_project_work':
        decision, recommendation = 'promising_limited_scope', 'Promising on these tested components only; confirm on broader project work before adoption.'
    elif report.get('decision') == 'benefit_not_demonstrated_on_these_tasks':
        decision, recommendation = 'benefit_not_demonstrated', 'Benefit not demonstrated at this study’s declared usefulness and resource thresholds.'
    else:
        decision, recommendation = 'inconclusive', 'Inconclusive: these results do not support a verified adoption recommendation.'
    usage = {}
    for condition in ('baseline', 'skill'):
        selected = [attempts[r['name']] for r in planned if r['condition'] == condition and r['name'] in attempts]
        usage[condition] = {'attempts': len(selected), 'ungraded_attempts': sum(not r['graded'] for r in selected)}
        for key in ('model_responses', 'tool_calls', 'input_tokens', 'output_tokens', 'cached_input_tokens', 'total_tokens', 'wall_seconds'):
            vals = [r.get(key) for r in selected]
            usage[condition][key] = sum(vals) if vals and all(number(v) for v in vals) else None
    return {'verified': True, 'complete': complete, 'decision': decision, 'recommendation': recommendation,
            'fixture_controls_pass': controls_pass, 'fixture_only': fixture_only, 'pairs': pairs,
            'active_mean_paired_gain': mean, 'active_regressions': regressions, 'scope_controls_pass': scope_pass,
            'conditions_completed': sum(r['score'] is not None for r in observations), 'conditions_expected': len(planned),
            'usage': usage, 'failure': report.get('failure'), 'subscription_cost_usd': None, 'subscription_quota_used': None}


def security_assessment(run, manifest, report):
    try:
        import skill_safety
        result = skill_safety.assess_run(run, manifest, report)
        lab.require(result.get('status') in {'blocked', 'unknown', 'isolated_only'}, 'Unknown security verdict')
        return result
    except (ImportError, OSError, ValueError, KeyError, TypeError) as error:
        return {'status': 'unknown', 'verdict': 'Security evidence unavailable (' + type(error).__name__ + ').',
                'flags': [], 'evidence': {}, 'limits': ['Laptop installation safety has not been assessed.']}


def render(run, manifest, report, decision):
    command = shlex.join(['python3', str(Path(__file__).resolve()), str(run)])
    lines = [f"**{decision['recommendation']}**", '',
             f"Run: {clean(run.name)}. Verification: {'passed' if decision['verified'] else 'failed'}. "
             f"Completed conditions: {decision.get('conditions_completed', 'unknown')}/{decision.get('conditions_expected', 'unknown')}."]
    if decision.get('failure'):
        lines += ['', 'Stopped because: ' + clean(decision['failure'])[:400]]
    safety = decision['security']
    lines += ['', f"Security verdict: **{clean(safety['status'])}** — {clean(safety['verdict'])}",
              'Functional success and a clean heuristic scan do not establish safe installation on your laptop.']
    flags = safety.get('flags', [])
    if flags:
        lines += ['', f'Security findings: {len(flags)}. Capability mentions require review; they are not proof of malicious behavior.', '']
        for flag in flags[:12]:
            location = clean(flag.get('path', 'location unavailable'))
            if type(flag.get('line')) is int:
                location += ':' + str(flag['line'])
            lines.append('- ' + clean(flag.get('category', 'review required')) + ' — ' + location)
        if len(flags) > 12:
            lines.append(f'- {len(flags) - 12} additional locations are recorded in decision.json.')
    evidence = safety.get('evidence', {})
    boundary = evidence.get('boundary', {})
    if evidence:
        rejected = evidence.get('rejected_attempts')
        count = len(rejected) if isinstance(rejected, (list, dict)) else rejected if type(rejected) is int else 'unknown'
        controls = boundary.get('controls')
        control_count = len(controls) if isinstance(controls, (list, dict)) else 'unknown'
        lines += ['', f"Security evidence: rejected attempts {count}; boundary verified {boundary.get('verified') is True}; tested boundary controls {control_count}."]
        attempted, planned = evidence.get('native_attempts'), evidence.get('planned_conditions')
        if type(attempted) is int and type(planned) is int:
            lines.append(f'Native conditions inspected: {attempted}/{planned}; this is security coverage, not completed performance trials.')
        if isinstance(boundary.get('path'), str):
            lines.append(link('boundary evidence', boundary['path']))
    if safety.get('limits'):
        lines.append('')
    for limit in safety.get('limits', []):
        lines.append('- ' + clean(limit))
    if decision['verified']:
        lines += ['', f"Skill: {clean(manifest.get('skill_source', manifest['target']))}. Model: {clean(manifest.get('model', 'unknown'))}.",
                  f"Skill SHA-256: {clean(manifest.get('skill_sha256', 'unknown'))}. Task SHA-256: {clean(manifest.get('task_sha256', 'unknown'))}.",
                  '', 'Functional outcomes (independent checks; incomplete pairs do not establish an effect):', '',
                  '| Task / repetition | Baseline | Skill | Paired gain |', '|---|---|---|---|']
        for pair in decision['pairs']:
            if pair['readonly']:
                continue
            arms = pair['arms']
            gain = 'not available' if pair['gain'] is None else f"{pair['gain']:+.1%}"
            lines.append(f"| {clean(pair['task_id'])} / {pair['trial'] + 1} | {arms.get('baseline', {}).get('outcome', 'missing')} | {arms.get('skill', {}).get('outcome', 'missing')} | {gain} |")
        lines += ['', 'Activation and workflow diagnostics (separate from functional quality):', '',
                  '| Task / repetition | Baseline tools / workflow | Skill tools / workflow |', '|---|---|---|']
        for pair in decision['pairs']:
            cells = []
            for condition in ('baseline', 'skill'):
                row = pair['arms'].get(condition, {})
                detail = (row.get('outcome', 'missing') + '; expected no tools') if pair['readonly'] else row.get('workflow', 'not assessed')
                cells.append(f"{row.get('tool_calls') if row.get('tool_calls') is not None else 'unknown'} / {clean(detail)}")
            lines.append(f"| {clean(pair['task_id'])} / {pair['trial'] + 1} | {' | '.join(cells)} |")
        lines += ['', 'Tool use shows explicit activation or restraint; automatic installed-skill discovery was not tested.',
                  f"Fixture controls: {'passed' if decision['fixture_controls_pass'] else 'not passed'}; fixture validation is not model-performance evidence.",
                  '', 'Observed candidate usage, including failed/ungraded scheduled attempts:', '',
                  '| Arm | Attempts (ungraded) | Actual responses | Input | Output | Cached input* | Total tokens |',
                  '|---|---|---|---|---|---|---|']
        for condition, row in decision['usage'].items():
            fmt = lambda key: f"{row[key]:,}" if row[key] is not None else 'unknown'
            lines.append(f"| {condition} | {row['attempts']} ({row['ungraded_attempts']}) | {fmt('model_responses')} | {fmt('input_tokens')} | {fmt('output_tokens')} | {fmt('cached_input_tokens')} | {fmt('total_tokens')} |")
        lines += ['', '*Codex cached input is included in input tokens; total = input + output. No extra cache charge is inferred.',
                  'Subscription charge, quota consumed, and full end-to-end cost: unknown. Candidate counts exclude authoring/review. No postmortem or other-run measurements are combined.',
                  '', 'Scope: ' + clean(manifest.get('scope', 'See frozen manifest.'))]
        if manifest['target'] == 'ub-project' and manifest.get('trials') == 1:
            order = '; '.join(clean(pair['task_id']) + ': ' + ' → '.join(
                r['condition'] for r in manifest['schedule'] if r['task_id'] == pair['task_id'])
                for pair in decision['pairs'])
            lines += ['One paired repetition per task; no repeated-performance estimate. Frozen order: ' + order + '.',
                      'A single pair cannot establish a general token-overhead ratio; previous attempts remain separate.']
        if manifest['target'] == 'ub-project':
            lines += ['Quality tools scan protected support source, notably evaluate.py. Whole-snapshot metrics do not isolate whether editable task code met UB quality targets. This benchmark coverage limitation is separate from functional correctness and must not determine adoption by itself.']
        if manifest['target'] == 'frontend':
            lines += ['Visual judgment is separate: this report verifies functional evidence and does not authenticate a separate visual review.']
        lines.append('')
        for limitation in dict.fromkeys(report.get('limitations', manifest.get('limitations', []))):
            lines.append('- ' + clean(limitation))
    next_action = ('Resolve the recorded failure or incomplete pairs, then prepare a fresh bounded run; obtain approval for changed limits before dispatch.'
                   if not decision.get('complete') else 'Use the task-level results to select a broader independent project trial before making a general adoption claim.')
    if manifest.get('target') == 'ub-project' and not decision.get('complete'):
        next_action = 'Hold default adoption for this configuration. Review task scope, protected-code metric coverage and context use before proposing another bounded study; no automatic retry or cap increase.'
    fence = '`' * max(3, max((len(s) + 1 for s in re.findall(r'`+', command)), default=3))
    lines += ['', 'Next action: ' + next_action, '', 'Evidence: ' + link('saved report', run / 'report.json') + ' · ' + link('frozen manifest', run / 'manifest.json'),
              '', 'Exact read-only report command:', '', fence + 'sh', command, fence, '']
    return '\n'.join(lines)


def build(run):
    run = Path(run).expanduser().resolve()
    manifest, report = {}, {}
    try:
        try:
            manifest = lab.read(run / 'manifest.json')
        except (OSError, ValueError):
            manifest = {}
        if manifest.get('target') == 'ub-project':
            lab.require(project_study is not None,
                        'Private project benchmark verifier is not distributed; verify with the original local project and evidence.')
            verifier = project_study.verify
        else:
            verifier = lab.verify
        report = verifier(run)
        manifest = lab.read(run / 'manifest.json')
        lab.require(manifest.get('target') in {'ub', 'frontend', 'ub-project'}, 'Unsupported saved experiment')
        decision = assess(run, manifest, report)
        decision['functional_decision'] = decision['decision']
        decision['security'] = security_assessment(run, manifest, report)
        if decision['security']['status'] == 'blocked':
            decision.update(decision='do_not_adopt', recommendation='Do not adopt: the independent security assessment blocked this skill or run.')
        elif decision['security']['status'] == 'unknown' and decision['decision'] == 'promising_limited_scope':
            decision.update(decision='inconclusive', recommendation='Inconclusive for adoption: functional results look promising, but security readiness is unknown.')
    except (OSError, ValueError, KeyError, TypeError, StopIteration, ImportError, subprocess.SubprocessError) as error:
        decision = {'verified': False, 'complete': False, 'decision': 'inconclusive',
                    'recommendation': 'Inconclusive: the saved evidence could not be verified; do not use it to justify adoption.',
                    'failure': str(error), 'subscription_cost_usd': None, 'subscription_quota_used': None,
                    'security': {'status': 'unknown', 'verdict': 'Run verification failed; security evidence is not established.', 'limits': []}}
    decision.update(schema='skill-lab-experiment-report/1', run=str(run),
                    source_hashes={name: lab.sha((run / name).read_bytes()) for name in ['manifest.json', 'report.json']
                                   if (run / name).is_file()}, reporter_sha256=lab.sha(Path(__file__).read_bytes()))
    return render(run, manifest, report, decision), decision


def write_report(output, run, markdown, decision):
    output, run = Path(output).expanduser().resolve(), Path(run).expanduser().resolve()
    lab.require(not output.is_relative_to(run) and not output.is_relative_to(lab.ROOT / 'runs'),
                'Report output must be outside immutable run directories')
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'REPORT.md').open('x') as stream:
        stream.write(markdown)
    lab.save(output / 'decision.json', dict(decision, markdown_sha256=lab.sha(markdown.encode())))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    markdown, decision = build(args.run)
    print(markdown, end='')
    try:
        if args.out:
            write_report(args.out, args.run, markdown, decision)
    except (OSError, ValueError) as error:
        print('Report output refused: ' + str(error), file=sys.stderr)
        return 2
    return 0 if decision['verified'] and decision['complete'] and decision['security']['status'] != 'blocked' else 2


if __name__ == '__main__':
    raise SystemExit(main())
