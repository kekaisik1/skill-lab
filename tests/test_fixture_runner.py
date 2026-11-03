import importlib.util
import json
import os
from pathlib import Path
import tempfile
import signal
import time
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('fixture_runner',ROOT/'skill-evaluator/scripts/fixture_runner.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class ControllerTests(unittest.TestCase):
 def test_arbitrary_candidate_rejected(self):
  with self.assertRaises(ValueError):r.run('/tmp/arbitrary.py','/tmp/never-created')
 def test_preflight_fails_closed(self):
  with tempfile.TemporaryDirectory() as d,patch.object(r,'bounded',return_value={'status':'process_error','stdout':'','stderr':'namespace denied'}):
   result=r.run('positive',Path(d,'run'))
   self.assertEqual(result['status'],'infrastructure_error')
   self.assertEqual(len(result['processes']),1)
   self.assertEqual(result['grade'],'inconclusive')
 def test_controller_errors_are_not_task_failures(self):
  with tempfile.TemporaryDirectory() as d,patch.object(r,'bounded',side_effect=OSError('fixture controller error')):
   result=r.run('positive',Path(d,'run'))
   self.assertEqual(result['status'],'grader_error')
   self.assertEqual(result['grade'],'inconclusive')
 def test_failed_isolation_probe_stops_grading(self):
  def fake(cmd,*args,**kwargs):
   if '--bind' in cmd:
    work=Path(cmd[cmd.index('--bind')+1]);(work/'probe.json').write_text(json.dumps({'home':'accessible'}))
    return {'status':'completed','stdout':'','stderr':''}
   return {'status':'completed','stdout':'READY','stderr':''}
  with tempfile.TemporaryDirectory() as d,patch.object(r,'bounded',side_effect=fake):
   result=r.run('adversarial',Path(d,'run'))
   self.assertEqual(result['status'],'isolation_failure')
   self.assertEqual(result['grade'],'inconclusive')
   self.assertEqual(len(result['processes']),2)
 def test_interruption_receipt(self):
  with tempfile.TemporaryDirectory() as d,patch.object(r,'bounded',side_effect=KeyboardInterrupt()):
   with self.assertRaises(KeyboardInterrupt):r.run('positive',Path(d,'run'))
   receipt=json.loads(Path(d,'run/receipt.json').read_text())
   self.assertEqual(receipt['status'],'interrupted')
 def test_missing_runtime_receipt(self):
  with tempfile.TemporaryDirectory() as d,patch.object(r,'BWRAP','/nonexistent/runtime'):
   result=r.run('positive',Path(d,'run'))
   self.assertEqual(result['status'],'infrastructure_error')
   self.assertTrue(Path(d,'run/receipt.json').is_file())
 def test_no_host_project_or_hidden_mount(self):
  cmd=r.command(Path('/tmp/public.py'))
  self.assertNotIn('/home',cmd);self.assertNotIn('/evaluator',cmd)
  self.assertIn('--unshare-all',cmd);self.assertIn('--clearenv',cmd)

@unittest.skipUnless(os.environ.get('SKILL_EVAL_ISOLATION_TESTS')=='1','requires explicitly approved bubblewrap execution')
class IsolatedIntegrationTests(unittest.TestCase):
 def run_fixture(self,name):
  with tempfile.TemporaryDirectory() as d:return r.run(name,Path(d,'run'))
 def test_interruption_cleanup(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);work=root/'work';work.mkdir();src=root/'candidate.py'
   src.write_text("import os,time\nif os.fork()==0:\n time.sleep(.6);open('/work/late','w').write('bad')\nelse:time.sleep(60)\n")
   def interrupt(*args):raise KeyboardInterrupt()
   previous=signal.signal(signal.SIGALRM,interrupt)
   try:
    signal.setitimer(signal.ITIMER_REAL,.2)
    with self.assertRaises(KeyboardInterrupt) as caught:r.bounded(r.command(src,work))
    self.assertEqual(caught.exception.receipt['status'],'interrupted')
   finally:
    signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
   time.sleep(.8);self.assertFalse((work/'late').exists())
 def test_positive(self):
  x=self.run_fixture('positive');self.assertEqual((x['status'],x['grade']),('valid','pass'))
 def test_negative(self):
  x=self.run_fixture('negative');self.assertEqual((x['status'],x['grade']),('valid','fail'))
 def test_forged_stdout_cannot_grade(self):
  x=self.run_fixture('forged');self.assertEqual(x['status'],'invalid_artifact')
 def test_invalid_protocol(self):self.assertEqual(self.run_fixture('invalid')['status'],'invalid_protocol')
 def test_candidate_timeout(self):self.assertEqual(self.run_fixture('timeout')['status'],'candidate_timeout')
 def test_grading_timeout(self):self.assertEqual(self.run_fixture('grader-timeout')['status'],'grading_timeout')
 def test_output_limit(self):self.assertEqual(self.run_fixture('overflow')['status'],'candidate_output_limit')
 def test_symlink_artifact_refused(self):self.assertEqual(self.run_fixture('symlink')['status'],'invalid_artifact')
 def test_child_cleanup(self):
  x=self.run_fixture('cleanup');self.assertEqual(x['status'],'candidate_timeout');self.assertTrue(x['late_child_effect_absent'])
 def test_isolation_and_artifact(self):
  x=self.run_fixture('adversarial');self.assertEqual(x['grade'],'pass')
  self.assertEqual(set(x['isolation_probes'].values()),{'blocked'})

if __name__=='__main__':unittest.main()
