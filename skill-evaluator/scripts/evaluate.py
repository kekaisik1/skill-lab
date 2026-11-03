#!/usr/bin/env python3
"""Offline evaluation scaffolding. Never executes candidate content or external runners."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import sys

VERSION = '0.2.0'
MAX_FILE = 2_000_000
MAX_TOTAL = 10_000_000
RULES = {
 'installers': r'\b(?:pip|npm|pnpm|brew|apt|uv)\s+(?:install|add|sync)|setup\.py',
 'downloads': r'https?://|\bcurl\b|\bwget\b|urlopen|requests\.',
 'credentials': r'\.ssh|\.aws|\.env\b|credential|api[_ -]?key|token|secret|environ',
 'transmission': r'\b(?:upload|POST|send|socket|fetch)\b|requests\.',
 'hooks': r'\bhooks?\b|pre-commit|postinstall|startup|cron',
 'external_execution': r'subprocess|os\.system|\bexec\b|\beval\b|\bnpx\b|\b(?:run|execute)\b.*(?:command|script|binary)|\b(?:crapper|dryer|mutator)\b',
 'destructive_changes': r'\brm\b|\bunlink\b|rmtree|git\s+(?:reset|clean|restore|push)|\b(?:delete|cleanup|reclaim|push)\b',
 'permissions': r'\bsudo\b|chmod|chown|privileged|permission|sandbox',
 'dependencies': r'dependenc|requirements|package\.json|pyproject|lockfile|companion',
 'instruction_override': r'ignore.*(?:instruction|previous)|system prompt|disable.*(?:safety|check)|do not report',
}

def encode(value):
 return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()

def digest(data):
 return hashlib.sha256(data).hexdigest()

def read(path):
 p = Path(path)
 if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_TOTAL:
  raise ValueError('Expected bounded regular JSON file')
 def unique_object(pairs):
  result = {}
  for key,value in pairs:
   if key in result:
    raise ValueError('Duplicate JSON key: '+key)
   result[key] = value
  return result
 def invalid_constant(value):
  raise ValueError('Nonfinite JSON number: '+value)
 return json.loads(p.read_text(),object_pairs_hook=unique_object,parse_constant=invalid_constant)

def write(path, value):
 p = Path(path)
 p.parent.mkdir(parents=True, exist_ok=True)
 # Never overwrite existing evidence, even through a symlink.
 with p.open('xb') as f:
  f.write(encode(value))

def is_link(path):
 # Windows junctions and other reparse points can escape a selected tree too.
 path = Path(path)
 try:
  return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
 except FileNotFoundError:
  return False

def intake(source, origin, pin):
 root = Path(source)
 if any(is_link(p) for p in (root, *root.parents)) or not root.is_dir():
  raise ValueError('Source must be a local directory, not a symlink')
 if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', pin):
  raise ValueError('Require immutable 40/64 lowercase hex revision/content pin')
 files, findings, total = [], [], 0
 def walk_error(error):
  raise error
 for parent, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
  for name in sorted(dirs + names):
   p = Path(parent)/name
   if is_link(p):
    raise ValueError('Symlinks are refused: '+str(p.relative_to(root)))
  for name in sorted(names):
   p = Path(parent)/name
   if not p.is_file():
    raise ValueError('Special files refused')
   if p.stat().st_size > MAX_FILE:
    raise ValueError('File size limit exceeded')
   data = p.read_bytes()
   total += len(data)
   if total > MAX_TOTAL or len(files) >= 1000:
    raise ValueError('Intake size/count limit exceeded')
   rel = p.relative_to(root).as_posix()
   files.append({'path':rel, 'bytes':len(data), 'sha256':digest(data)})
   try:
    text = data.decode('utf-8')
   except UnicodeDecodeError:
    findings.append({'path':rel, 'line':None, 'category':'uninspected_binary'})
    continue
   for i, line in enumerate(text.splitlines(), 1):
    for category, pattern in RULES.items():
     if re.search(pattern, line, re.I):
      findings.append({'path':rel, 'line':i, 'category':category})
 if not files:
  raise ValueError('Empty inventory is incomplete evidence')
 files.sort(key=lambda x:x['path'])
 return {'schema':1, 'tool_version':VERSION, 'origin':origin, 'declared_pin':pin,
  'pin_verification':'unverified declaration; compare inventory with trusted revision independently',
  'inventory_sha256':digest(encode(files)), 'files':files, 'findings':findings,
  'states':{'source_reviewed':False,'runtime_tested':False,'functionality_tested':False},
  'gate':'manual_review_required', 'unknown':['semantic instruction review','transitive dependencies','runtime behavior'],
  'notice':'Heuristics are triage, never a safety verdict; clean scans still require review.'}

def finite_nonnegative(value):
 return type(value) in (int,float) and math.isfinite(value) and value >= 0

def validate_manifest(manifest):
 tasks = manifest['tasks']
 scope = manifest.get('study_scope','complete')
 if scope not in {'complete','pilot_subset'}:
  raise ValueError('Unknown study scope')
 if not isinstance(tasks,list) or not tasks:
  raise ValueError('Tasks must be a nonempty list')
 ids = [t['id'] for t in tasks]
 if any(not isinstance(i,str) or not i for i in ids) or len(ids) != len(set(ids)):
  raise ValueError('Task IDs must be unique nonempty strings')
 for t in tasks:
  if t.get('difficulty') not in {'easy','medium','complex'}:
   raise ValueError('Unknown task difficulty')
  if any(not isinstance(t.get(k),str) or not t[k].strip() for k in ['prompt','capability','acceptance']):
   raise ValueError('Each task needs prompt, capability, acceptance')
  if type(t.get('negative_control',False)) is not bool:
   raise ValueError('Negative control marker must be boolean')
 difficulties = {t['difficulty'] for t in tasks if not t.get('negative_control',False)}
 if scope == 'complete' and difficulties != {'easy','medium','complex'}:
  raise ValueError('In-scope tasks must cover easy, medium, complex')
 if not any(t.get('negative_control',False) for t in tasks):
  raise ValueError('At least one explicit negative control is required')
 thresholds = manifest['thresholds']
 if any(not finite_nonnegative(thresholds[k]) for k in ['min_score_gain','max_token_ratio','max_regressions']):
  raise ValueError('Thresholds must be finite nonnegative numbers')
 if thresholds['min_score_gain'] > 1 or type(thresholds['max_regressions']) is not int:
  raise ValueError('Invalid score-gain or regression threshold')
 # This is a reviewer-authored semantic mapping, never scanner-inferred truth.
 purpose = manifest.get('purpose')
 gaps = []
 if scope == 'pilot_subset':
  missing = sorted({'easy','medium','complex'} - difficulties)
  gaps.append('Pilot subset: no general performance claim; missing difficulty coverage: '+(', '.join(missing) or 'none; task selection remains limited'))
 if purpose is None:
  gaps.append('Reviewer-authored purpose and capability mapping missing')
 else:
  if any(not isinstance(purpose.get(k),str) or not purpose[k].strip() for k in ['claim','reviewer','source_sha256']):
   raise ValueError('Purpose requires claim, reviewer and source_sha256')
  if not re.fullmatch(r'[0-9a-f]{64}',purpose['source_sha256']):
   raise ValueError('Purpose source must use a SHA-256 inventory pin')
  capabilities = purpose.get('capabilities')
  if not isinstance(capabilities,list) or not capabilities or any(not isinstance(c,str) or not c for c in capabilities):
   raise ValueError('Purpose requires named capabilities')
  covered = {t['capability'] for t in tasks if not t.get('negative_control',False)}
  if covered != set(capabilities):
   raise ValueError('Every intended capability must map to an in-scope task and vice versa')
  for t in tasks:
   if not isinstance(t.get('adaptation_rationale'),str) or not t['adaptation_rationale'].strip():
    raise ValueError('Adapted tasks require a rationale, including negative controls')
 controls = manifest.get('matched_controls',{})
 for key in ['model','model_version','reasoning_budget','context_limit','base_commit','toolchain_digest','budget_per_trial','environment','condition_delta']:
  value = controls.get(key)
  if value is None or (isinstance(value,str) and (not value.strip() or 'REQUIRED' in value)):
   gaps.append('Unresolved matched control: '+key)
 return gaps

def plan(profile, manifest):
 gaps = validate_manifest(manifest)
 purpose = manifest.get('purpose')
 if purpose is not None and purpose['source_sha256'] != profile['inventory_sha256']:
  raise ValueError('Purpose mapping does not match this candidate inventory')
 tasks = manifest['tasks']
 trials = manifest.get('trials', 5)
 if type(trials) is not int or not 2 <= trials <= 100:
  raise ValueError('Use 2..100 repeated matched trials')
 schedule = []
 rng = random.Random(manifest.get('seed', 19))
 for task in tasks:
  first = rng.randrange(2)
  for trial in range(trials):
   conditions = ['baseline','skill'] if (trial+first)%2 == 0 else ['skill','baseline']
   for position, condition in enumerate(conditions):
    schedule.append({'task_id':task['id'], 'trial':trial, 'condition':condition,
                     'order':position, 'pair_seed':trial, 'fresh_workspace':True})
 result = {'schema':2,'profile_sha256':digest(encode(profile)),
  'manifest_sha256':digest(encode(manifest)), 'manifest':manifest,'schedule':schedule,
  'adaptation':{'method':'reviewer-authored capability mapping','readiness_gaps':gaps,
                'verified_semantically':False},
  'external_execution':'disabled', 'isolation':'not provisioned; prompt separation is not OS isolation',
  'gate':'blocked_pending_reviewed_adapter_and_explicit_action_approval'}
 return result

def freeze(plan_value, checks):
 task_ids = {t['id'] for t in plan_value['manifest']['tasks']}
 if set(checks) != task_ids or any(not v for v in checks.values()):
  raise ValueError('Checks must cover every task exactly')
 for task_checks in checks.values():
  if not isinstance(task_checks,list):
   raise ValueError('Task checks must be lists')
  for c in task_checks:
   if set(c) != {'input','expected','critical'} or type(c['critical']) is not bool:
    raise ValueError('Invalid check schema')
 return {'schema':1,'plan_sha256':digest(encode(plan_value)),
         'checks_sha256':digest(encode(checks)), 'checks':checks,
         'classification':'PUBLIC DEMO checks; replace with private independently authored holdouts for real trials'}

def grade(frozen, submission):
 # Answers are data, not callable code. No candidate-provided evaluator or shell commands.
 if frozen['checks_sha256'] != digest(encode(frozen['checks'])):
  raise ValueError('Frozen check hash mismatch')
 task = submission['task_id']
 checks = frozen['checks'][task]
 answers = submission['answers']
 if not isinstance(checks,list) or not checks or not isinstance(answers,list) or len(answers) != len(checks):
  raise ValueError('Missing/extra answers are invalid, not passing')
 passes = [encode(a) == encode(c['expected']) for a,c in zip(answers, checks)]
 return {'artifact_id':digest(encode(submission)), 'task_id':task,
  'checks_sha256':frozen['checks_sha256'], 'score':sum(passes)/len(passes),
  'critical_failures':sum(not p and c['critical'] for p,c in zip(passes,checks)),
  'grader':'deterministic condition-blind data oracle; no model grader',
  'runtime_tested':False}

def demo_answer(case, improved):
 # Authored simulation of weak vs request-linked logic; NOT UB or model behavior.
 if case['kind'] == 'scope':
  return {'mutations':0,'external_calls':0}
 if case['kind'] == 'normalize':
  return case['value'].strip().lower() if improved else case['value'].lower()
 if case['kind'] == 'booking':
  if improved:
   return 'confirmed' if case['commit_request'] == case['request'] and case['committed'] else 'unknown'
  return 'confirmed' if case['staff_match'] else 'unknown'
 if case['kind'] == 'retry':
  return len(set(case['requests'])) if improved else len(case['requests'])
 raise ValueError('Unknown authored fixture')

def summary(plan_value, records):
 validate_manifest(plan_value['manifest'])
 schedule = plan_value['schedule']
 def key(row):
  if type(row['trial']) is not int or row['trial'] < 0:
   raise ValueError('Trial must be a nonnegative integer')
  return row['task_id'],row['trial'],row['condition']
 expected = {key(r) for r in schedule}
 trials = plan_value['manifest'].get('trials',5)
 planned = {(t['id'],n,c) for t in plan_value['manifest']['tasks'] for n in range(trials) for c in ['baseline','skill']}
 if len(expected) != len(schedule) or expected != planned:
  raise ValueError('Schedule differs from the frozen manifest')
 if plan_value['manifest_sha256'] != digest(encode(plan_value['manifest'])):
  raise ValueError('Manifest hash mismatch')
 pairs = sorted({(t,n) for t,n,c in expected})
 if any((t,n,c) not in expected for t,n in pairs for c in ['baseline','skill']):
  raise ValueError('Schedule must contain both matched conditions')
 actual = [key(r) for r in records]
 if len(actual) != len(set(actual)) or not set(actual) <= expected:
  raise ValueError('Duplicate or foreign matched trial')
 allowed_status = {'completed','infrastructure_failure','timeout','cancelled','resource_limit','safety_failure'}
 for r in records:
  status = r.get('status','completed')
  if status not in allowed_status:
   raise ValueError('Unknown trial status')
  if status == 'completed' and (not finite_nonnegative(r['score']) or r['score'] > 1 or type(r['critical_failures']) is not int or r['critical_failures'] < 0):
   raise ValueError('Invalid grade')
  if not isinstance(r.get('safety_concerns',[]),list) or any(not isinstance(x,str) for x in r.get('safety_concerns',[])):
   raise ValueError('Safety concerns must be a list of strings')
  if type(r.get('synthetic',True)) is not bool:
   raise ValueError('Synthetic marker must be boolean')
  if not isinstance(r.get('usage',{}),dict):
   raise ValueError('Usage must be an object')
 by_key = {key(r):r for r in records}
 complete_pairs = [(t,n) for t,n in pairs if all((t,n,c) in by_key and by_key[(t,n,c)].get('status','completed') == 'completed' for c in ['baseline','skill'])]
 deltas = [by_key[(t,n,'skill')]['score']-by_key[(t,n,'baseline')]['score'] for t,n in complete_pairs]
 def mean_or_none(values):
  return statistics.mean(values) if values else None
 def group_summary(ids):
  selected = [(t,n) for t,n in complete_pairs if t in ids]
  changes = [by_key[(t,n,'skill')]['score']-by_key[(t,n,'baseline')]['score'] for t,n in selected]
  return {'paired_trials':len(selected), 'mean_paired_gain':mean_or_none(changes),
          'regressions':sum(d < 0 for d in changes),
          **{c:mean_or_none([by_key[(t,n,c)]['score'] for t,n in selected]) for c in ['baseline','skill']}}
 tasks = plan_value['manifest']['tasks']
 difficulties = {d:group_summary({t['id'] for t in tasks if t['difficulty']==d and not t.get('negative_control',False)}) for d in ['easy','medium','complex']}
 negatives = group_summary({t['id'] for t in tasks if t.get('negative_control',False)})
 telemetry, coverage, totals = {}, {}, {}
 for metric in ['input_tokens','output_tokens','cached_input_tokens','peak_context_tokens','context_limit_tokens',
             'compactions','tool_calls','wall_seconds','cpu_seconds','peak_rss_bytes','disk_bytes',
             'setup_wall_seconds','setup_cpu_seconds','setup_tokens','setup_disk_bytes',
             'validation_tokens','end_to_end_tokens','usd','subscription_units']:
  telemetry[metric],coverage[metric],totals[metric] = {},{},{}
  for condition in ['baseline','skill']:
   vals = [r.get('usage',{}).get(metric) for r in records if r['condition']==condition]
   if any(v is not None and not finite_nonnegative(v) for v in vals):
    raise ValueError('Invalid telemetry: '+metric)
   known = [v for v in vals if finite_nonnegative(v)]
   coverage[metric][condition] = {'measured':len(known),'observed_trials':len(vals)}
   # All attempted records contribute, including failed attempts; never silently drop unknowns.
   telemetry[metric][condition] = statistics.mean(known) if vals and len(known)==len(vals) else None
   totals[metric][condition] = sum(known) if vals and len(known)==len(vals) and metric not in {'peak_context_tokens','context_limit_tokens','peak_rss_bytes'} else None
 token_means = {c: None if any(telemetry[k][c] is None for k in ['input_tokens','output_tokens']) else sum(telemetry[k][c] for k in ['input_tokens','output_tokens']) for c in ['baseline','skill']}
 ratio = token_means['skill']/token_means['baseline'] if token_means['baseline'] and token_means['skill'] is not None else None
 end_means = telemetry['end_to_end_tokens']
 end_ratio = end_means['skill']/end_means['baseline'] if end_means['baseline'] and end_means['skill'] is not None else None
 thresholds = plan_value['manifest']['thresholds']
 mean = mean_or_none(deltas)
 regressions = sum(d<0 for d in deltas)
 critical = sum(r['critical_failures'] for r in records if r['condition']=='skill' and r.get('status','completed')=='completed')
 synthetic = not records or any(r.get('synthetic',True) for r in records)
 missing = [{'task_id':t,'trial':n,'condition':c} for t,n,c in sorted(expected-set(actual))]
 infrastructure = [{k:r[k] for k in ['task_id','trial','condition','status']} for r in records if r.get('status','completed') not in {'completed','safety_failure'}]
 safety = [{'task_id':r['task_id'],'trial':r['trial'],'condition':r['condition'],'status':r.get('status','completed'),'concerns':r.get('safety_concerns',[])} for r in records if r.get('status')=='safety_failure' or r.get('safety_concerns')]
 complete = len(complete_pairs)==len(pairs)
 eligible = complete and not safety and not synthetic and end_ratio is not None and mean is not None and mean >= thresholds['min_score_gain'] and end_ratio <= thresholds['max_token_ratio'] and regressions <= thresholds['max_regressions'] and critical==0
 decision = 'inconclusive_unverified_evidence' if eligible else 'inconclusive_or_below_threshold'
 if not complete: decision = 'inconclusive_incomplete_trials'
 if safety: decision = 'inconclusive_safety_concerns'
 return {'schema':2,'plan_sha256':digest(encode(plan_value)), 'records_sha256':digest(encode(records)),
  'synthetic':synthetic,'paired_trials':len(deltas),'planned_pairs':len(pairs),'mean_paired_gain':mean,
  'paired_gain_range':[min(deltas),max(deltas)] if deltas else None, 'regressions':regressions,
  'skill_critical_failures':critical,'by_difficulty':difficulties,'negative_controls':negatives,
  'by_task':{t['id']:group_summary({t['id']}) for t in tasks},
  'attrition':{'missing_trials':missing,'infrastructure_failures':infrastructure,'excluded_pairs':len(pairs)-len(complete_pairs)},
  'safety_concerns':safety,'usage_means':telemetry,'usage_totals':totals,'usage_coverage':coverage,
  'total_token_ratio':ratio,'end_to_end_token_ratio':end_ratio,'thresholds':thresholds,
  'numeric_screen_met':eligible,'evidence_verification':'unverified local records; no authenticated external runner',
  'decision':decision,
  'limits':['Only complete matched pairs enter quality effects; attrition can bias these descriptive effects.',
            'No statistical generalization from repeated deterministic fixtures.',
            'Telemetry includes failed attempts; null means unavailable, not zero. Peak values are per-trial, not aggregate peaks.',
            'Setup metrics are separately measured allocations per trial; shared setup must not be duplicated across records. Token totals do not automatically include setup or validation.',
            'Subscription permission is not paid API permission.',
            'Scores do not establish sandboxing, safety, or stock UB usefulness.',
            'Imported runner evidence is not authenticated by this local scaffold.']}

def demo(plan_value, frozen, out):
 if frozen['plan_sha256'] != digest(encode(plan_value)) or frozen['checks_sha256'] != digest(encode(frozen['checks'])):
  raise ValueError('Frozen evidence mismatch')
 records=[]
 out=Path(out)
 out.mkdir(parents=True,exist_ok=False)
 for row in plan_value['schedule']:
  submission={'task_id':row['task_id'], 'answers':[demo_answer(c['input'],row['condition']=='skill') for c in frozen['checks'][row['task_id']]]}
  grade_value=grade(frozen,submission)
  artifact_id=grade_value['artifact_id']
  if not (out/'artifacts'/f'{artifact_id}.json').exists():
   write(out/'artifacts'/f'{artifact_id}.json',submission)
  records.append({**row, **grade_value, 'synthetic':True,'usage':{},
   'provenance':{'runner':'authored in-process data simulator','model':None,'billing':'none','source':'demo only'}})
 write(out/'records.json',records)
 report=summary(plan_value, records)
 write(out/'report.json',report)
 (out/'report.md').write_text('# Synthetic demonstration only\n\nNo model or candidate skill ran. These scores demonstrate the evaluation plumbing, not UB effectiveness.\n\n'
  +f"Matched pairs: {report['paired_trials']}; mean synthetic gain: {report['mean_paired_gain']:.3f}; skill critical failures: {report['skill_critical_failures']}.\n\n"
  +'Token, context, latency and cost telemetry: unknown. No spending incurred by this demo.\n\nDecision: inconclusive. Runtime and functionality of UB remain untested.\n')
 return report

def main():
 p=argparse.ArgumentParser(description=__doc__)
 sub=p.add_subparsers(dest='cmd',required=True)
 s=sub.add_parser('intake'); s.add_argument('source'); s.add_argument('--origin',required=True); s.add_argument('--pin',required=True); s.add_argument('--out',required=True)
 s=sub.add_parser('plan'); s.add_argument('profile'); s.add_argument('manifest'); s.add_argument('--out',required=True)
 s=sub.add_parser('freeze'); s.add_argument('plan'); s.add_argument('checks'); s.add_argument('--out',required=True)
 s=sub.add_parser('demo'); s.add_argument('plan'); s.add_argument('frozen'); s.add_argument('--out',required=True)
 s=sub.add_parser('report'); s.add_argument('plan'); s.add_argument('records'); s.add_argument('--out',required=True)
 a=p.parse_args()
 if a.cmd=='intake': result=intake(a.source,a.origin,a.pin)
 elif a.cmd=='plan': result=plan(read(a.profile),read(a.manifest))
 elif a.cmd=='freeze': result=freeze(read(a.plan),read(a.checks))
 elif a.cmd=='demo':
  result=demo(read(a.plan),read(a.frozen),a.out)
  print(json.dumps({'decision':result['decision'],'synthetic':True})); return
 else: result=summary(read(a.plan),read(a.records))
 write(a.out,result)
 print(a.out)

if __name__=='__main__':
 try: main()
 except (ValueError,KeyError,TypeError,OSError) as e:
  print('Refused: '+str(e),file=sys.stderr); sys.exit(2)
