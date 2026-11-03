#!/usr/bin/env python3
"""Trusted controller for allowlisted authored fixtures only. Requires working bubblewrap."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import selectors
import shutil
import signal
import subprocess
import tempfile
import time

BWRAP='/usr/bin/bwrap'
PYTHON='/usr/bin/python3'
LIMITS={'wall_seconds':2,'cpu_seconds':1,'memory_bytes':256*1024*1024,'output_bytes':16384,'file_bytes':65536,'processes':256}
# Public regression fixtures; runtime-private files are never mounted into candidates.
# These examples are NOT scientifically unseen holdouts.
CHECKS=[(' Alice ','alice'),('BOB','bob'),('','')]
SKILL=Path(__file__).resolve().parents[2]/'fixtures/benign/SKILL.md'
ACTIVATION_CHECKS=[
 ({'operation':'normalize_label','text':' Alice '},{'activated':True,'output':'alice'}),
 ({'operation':'display_verbatim','text':' Alice '},{'activated':False,'output':' Alice '}),
]
ROUTER="""import json,sys
v=json.load(sys.stdin)
active=v['operation']=='normalize_label'
print(json.dumps({'activated':active,'output':v['text'].strip().lower() if active else v['text']}))
"""
SOLUTION="import json,sys\nv=json.load(sys.stdin)\nprint(json.dumps(v.strip().lower()))\n"
CANDIDATES={
 'positive':"from pathlib import Path\nPath('/work/solution.py').write_text("+repr(SOLUTION)+")\n",
 'negative':"from pathlib import Path\nPath('/work/solution.py').write_text(\"import json,sys; print(json.dumps(json.load(sys.stdin).lower()))\")\n",
 'forged':"print('{\"score\":1,\"passed\":true}')\n",
 'invalid':"from pathlib import Path\nPath('/work/solution.py').write_text(\"print('NOT JSON')\")\n",
 'timeout':"import time\ntime.sleep(60)\n",
 'grader-timeout':"from pathlib import Path\nPath('/work/solution.py').write_text(\"import time; time.sleep(60)\")\n",
 'overflow':"import os\nwhile True: os.write(1,b'x'*8192)\n",
 'cleanup':"import os,time\nif os.fork()==0:\n time.sleep(1); open('/work/late-child','w').write('escaped'); time.sleep(60)\nelse: time.sleep(60)\n",
 'adversarial':"""import json, pathlib, socket
results={}
for label,path in [('holdouts','@HOLDOUT@'),('home','/home/skill-lab-fake'),('project','/host-project')]:
 try: pathlib.Path(path).stat(); results[label]='accessible'
 except OSError: results[label]='blocked'
try: pathlib.Path('/candidate.py').write_text('tampered'); results['source_write']='allowed'
except OSError: results['source_write']='blocked'
try:
 s=socket.socket(); s.settimeout(.1); s.connect(('192.0.2.1',80)); results['network']='connected'
except OSError: results['network']='blocked'
pathlib.Path('/work/probe.json').write_text(json.dumps(results))
print('{"score":1,"passed":true}')
"""+"from pathlib import Path\nPath('/work/solution.py').write_text("+repr(SOLUTION)+")\n",
 'symlink':"import os\nos.symlink('/etc/passwd','/work/solution.py')\n",
}
for name,source in [('activation-good',ROUTER),
                    ('activation-always',ROUTER.replace("v['operation']=='normalize_label'",'True')),
                    ('activation-never',ROUTER.replace("v['operation']=='normalize_label'",'False'))]:
 CANDIDATES[name]="from pathlib import Path\nPath('/work/solution.py').write_text("+repr(source)+")\n"

def task_for(name):
 activation=name.startswith('activation-')
 return {'id':'label-activation-v1' if activation else 'normalize-labels-v1',
         'prompt':('Normalize labels only for normalize_label; display_verbatim must preserve text without activation.'
                   if activation else 'Trim surrounding whitespace and lowercase labels.'),
         'checks':[{'id':f'case-{i+1}','input':value,'expected':expected}
                   for i,(value,expected) in enumerate(ACTIVATION_CHECKS if activation else CHECKS)],
         'visibility':'public authored regression cases; runtime private, not scientifically unseen'}

def encode(value):return (json.dumps(value,sort_keys=True,indent=2)+'\n').encode()

def same_answer(actual,expected):
 # JSON booleans must not compare equal to integers, including in nested answers.
 return json.dumps(actual,sort_keys=True)==json.dumps(expected,sort_keys=True)

def sha(data):return hashlib.sha256(data).hexdigest()
def dump(path,value):Path(path).write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')
def limits():
 resource.setrlimit(resource.RLIMIT_CPU,(LIMITS['cpu_seconds'],LIMITS['cpu_seconds']))
 resource.setrlimit(resource.RLIMIT_AS,(LIMITS['memory_bytes'],LIMITS['memory_bytes']))
 resource.setrlimit(resource.RLIMIT_FSIZE,(LIMITS['file_bytes'],LIMITS['file_bytes']))
 resource.setrlimit(resource.RLIMIT_NOFILE,(64,64))
 resource.setrlimit(resource.RLIMIT_CORE,(0,0))

def command(source,work=None,skill=None):
 cmd=[BWRAP,'--unshare-all','--die-with-parent','--new-session','--cap-drop','ALL',
      '--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64',
      '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--clearenv','--setenv','PATH','/usr/bin',
      '--ro-bind',str(source),'/candidate.py']
 if work:cmd+=['--bind',str(work),'/work','--chdir','/work']
 else:cmd+=['--tmpfs','/work','--chdir','/work']
 if skill:cmd+=['--ro-bind',str(skill),'/skill.md']
 bootstrap="import resource,runpy; resource.setrlimit(resource.RLIMIT_NPROC,(256,256)); runpy.run_path('/candidate.py',run_name='__main__')"
 return cmd+['--',PYTHON,'-I','-S','-c',bootstrap]

def bounded(cmd, stdin=b'', wall=None, cancel=None):
 started=time.monotonic(); data={'stdout':bytearray(),'stderr':bytearray()}; reason=None; interrupted=None
 p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    env={},start_new_session=True,preexec_fn=limits)
 sel=selectors.DefaultSelector()
 try:
  p.stdin.write(stdin);p.stdin.close()
  for stream,label in [(p.stdout,'stdout'),(p.stderr,'stderr')]:
   os.set_blocking(stream.fileno(),False);sel.register(stream,selectors.EVENT_READ,label)
  while sel.get_map() or p.poll() is None:
   if cancel is not None and cancel.is_set():reason='cancelled';break
   if time.monotonic()-started>(wall or LIMITS['wall_seconds']):reason='timeout';break
   for key,_ in sel.select(.05):
    chunk=os.read(key.fileobj.fileno(),4096)
    if not chunk:sel.unregister(key.fileobj);continue
    room=LIMITS['output_bytes']-sum(len(x) for x in data.values())
    data[key.data].extend(chunk[:max(room,0)])
    if len(chunk)>room:reason='output_limit';break
   if reason:break
 except KeyboardInterrupt as error:
  interrupted=error;reason='interrupted'
 finally:
  # Covers timeouts, output overflow, exceptions and KeyboardInterrupt.
  try:os.killpg(p.pid,signal.SIGKILL)
  except ProcessLookupError:pass
  p.wait();sel.close();p.stdout.close();p.stderr.close()
 result={'command':cmd,'pid':p.pid,'exit_code':p.returncode,'status':reason or ('completed' if p.returncode==0 else 'process_error'),
         'wall_seconds':time.monotonic()-started,'stdout':data['stdout'].decode(errors='replace'),
         'stderr':data['stderr'].decode(errors='replace'),'cleanup':'process group killed and leader reaped; PID namespace dies with its init'}
 if interrupted is not None:
  interrupted.receipt=result
  raise interrupted
 return result

def run(name,out):
 if name not in CANDIDATES:raise ValueError('Only bundled authored fixtures are supported')
 out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
 receipt={'fixture':name,'synthetic':True,'limits':LIMITS,'candidate_sha256':sha(CANDIDATES[name].encode()),
          'tool_hashes':{},'started_unix':time.time(),
          'claim':'OS namespace/mount isolation for authored fixtures, not hardened against kernel exploits',
          'grade':'inconclusive','processes':[]}
 try:
  task=task_for(name)
  if not task['checks']:raise ValueError('Task must have nonempty checks')
  skill=SKILL.read_bytes()
  (out/'SKILL.md').write_bytes(skill);dump(out/'task.json',task)
  receipt.update(task_id=task['id'],task_sha256=sha(encode(task)),skill_sha256=sha(skill),
                 runner_sha256=sha(Path(__file__).read_bytes()),cases=[])
  try:
   receipt['tool_hashes']={p:sha(Path(p).read_bytes()) for p in [BWRAP,PYTHON]}
  except OSError as error:
   receipt.update(status='infrastructure_error',error=str(error));return receipt
  with tempfile.TemporaryDirectory(prefix='skill-eval-run-') as tmp:
   root=Path(tmp);work=root/'work';work.mkdir();src=root/'candidate.py';src.write_text(CANDIDATES[name])
   hidden=root/'evaluator';hidden.mkdir();dump(hidden/'holdouts.json',task['checks'])
   skill_snapshot=root/'SKILL.md';skill_snapshot.write_bytes(skill)
   receipt['oracle_sha256']=sha((hidden/'holdouts.json').read_bytes())
   src.write_text(CANDIDATES[name].replace('@HOLDOUT@',str(hidden/'holdouts.json')))
   receipt['executed_candidate_sha256']=sha(src.read_bytes())
   probe=root/'probe.py';probe.write_text("print('READY')")
   pre=bounded(command(probe));receipt['processes'].append({'phase':'isolation_preflight',**pre})
   if pre['status']!='completed' or pre['stdout'].strip()!='READY':
    receipt['status']='infrastructure_error';return receipt
   (out/'candidate.py').write_bytes(src.read_bytes())
   result=bounded(command(src,work,skill_snapshot),wall=.35 if name=='cleanup' else None)
   receipt['processes'].append({'phase':'candidate',**result})
   if name=='cleanup':
    time.sleep(1.2);receipt['late_child_effect_absent']=not (work/'late-child').exists()
   if result['status']!='completed':receipt['status']='candidate_'+result['status'];return receipt
   if name=='adversarial':
    receipt['isolation_probes']=json.loads((work/'probe.json').read_text())
    expected={'holdouts','home','project','source_write','network'}
    receipt['isolation_verdict']='pass' if set(receipt['isolation_probes'])==expected and set(receipt['isolation_probes'].values())=={'blocked'} else 'fail'
    if receipt['isolation_verdict']!='pass':
     receipt.update(status='isolation_failure');return receipt
   artifact=work/'solution.py'
   if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size>LIMITS['file_bytes']:
    receipt.update(status='invalid_artifact',grade='invalid');return receipt
   # Candidate namespace has exited; freeze an evaluator-owned copy. Only this copy is mounted for grading.
   snapshot=root/'frozen-solution.py';snapshot.write_bytes(artifact.read_bytes())
   receipt['artifact_sha256']=sha(snapshot.read_bytes());(out/'solution.py').write_bytes(snapshot.read_bytes())
   passes=[]
   for case in task['checks']:
    value,expected=case['input'],case['expected']
    r=bounded(command(snapshot),json.dumps(value).encode());receipt['processes'].append({'phase':'grading_candidate_execution','case_id':case['id'],**r})
    if r['status']!='completed':receipt['status']='grading_'+r['status'];return receipt
    try:answer=json.loads(r['stdout'])
    except (ValueError,TypeError):receipt.update(status='invalid_protocol',grade='invalid');return receipt
    passed=same_answer(answer,expected);passes.append(passed)
    receipt['cases'].append({'id':case['id'],'actual':answer,'passed':passed,
                             'stdout_sha256':sha(r['stdout'].encode())})
   receipt.update(status='valid',grade='pass' if all(passes) else 'fail',passed=sum(passes),total=len(passes))
   return receipt
 except KeyboardInterrupt as error:
  receipt['status']='interrupted'
  if hasattr(error,'receipt'):receipt['processes'].append({'phase':'interrupted_process',**error.receipt})
  raise
 except (OSError,ValueError) as error:
  receipt.update(status='grader_error',error=type(error).__name__+': '+str(error));return receipt
 finally:
  dump(out/'receipt.json',receipt)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('fixture',choices=sorted(CANDIDATES));p.add_argument('--out',required=True);a=p.parse_args()
 r=run(a.fixture,a.out);print(json.dumps({'status':r['status'],'grade':r['grade'],'receipt':str(Path(a.out)/'receipt.json')}))
 return 0 if r['status']=='valid' and r['grade']=='pass' else 1
if __name__=='__main__':raise SystemExit(main())
