#!/usr/bin/env python3
"""Persist a chat's selected decision without discovering files or executing skills."""
import argparse
import json
from pathlib import Path
import sys

import evaluate
import preview_intake

FIELDS = {
    'intended_work': 'What work should this skill improve?',
    'improvement': 'What observable improvement would make it worth using?',
    'max_regression': 'What correctness regression, if any, is acceptable (fraction 0 to 1)?',
    'max_token_overhead': 'What additional token use is acceptable (fraction, e.g. 0.5)?',
    'max_time_overhead': 'What additional elapsed time is acceptable (fraction, e.g. 0.5)?',
    'budget': 'Choose quick, balanced, or deeper; depth does not guarantee a decisive answer.',
}


def capture(skill, pin, answers, examples, out):
    """Answers come from the current conversation, never inferred from a skill."""
    if not isinstance(answers, dict):
        raise ValueError('Answers must be a JSON object')
    for key in ('max_regression', 'max_token_overhead', 'max_time_overhead'):
        value = answers.get(key)
        if value is not None and (type(value) not in (int, float) or not 0 <= value <= 100):
            raise ValueError('Invalid finite allowance: ' + key)
    if answers.get('max_regression') is not None and answers['max_regression'] > 1:
        raise ValueError('Regression allowance must be a score fraction <= 1')
    if answers.get('budget') not in (None, 'quick', 'balanced', 'deeper'):
        raise ValueError('Choose quick, balanced or deeper')
    out = preview_intake.no_links(out)
    out.mkdir(parents=True, exist_ok=False)
    selected = preview_intake.prepare(skill, answers.get('intended_work') or 'Awaiting intended-work answer',
                                      examples, out / 'selection', pin=pin)
    missing = [{'field': key, 'question': question} for key, question in FIELDS.items()
               if answers.get(key) is None or answers.get(key) == '']
    value = {'schema': 'skill-lab-adoption-intake/1', 'answers': answers,
             'selection': selected, 'questions': missing,
             'status': 'needs_answers' if missing else 'needs_benchmark_authoring',
             'constraints': answers.get('constraints', []), 'selected_examples_only': True,
             'next': 'Author a claim-to-task proposal from this selection; independent semantic and executable-grader review precede any approved live plan.',
             'adapter_status': 'Custom selections are authoring assistance until an executable adapter is registered for exact source/task pins.',
             'model_calls': 0, 'separately_billed_api_allowance_usd': 0}
    (out / 'decision.json').write_bytes(evaluate.encode(value))
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skill', required=True)
    parser.add_argument('--pin', required=True)
    parser.add_argument('--answers', required=True, help='JSON answers already supplied in chat; empty object asks only missing questions')
    parser.add_argument('--example', action='append', default=[])
    parser.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(capture(args.skill, args.pin, json.loads(preview_intake.selected_file(args.answers)),
                                 args.example, args.out), indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('Adoption intake unavailable: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
