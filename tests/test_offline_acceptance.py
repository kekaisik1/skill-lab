import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skill-evaluator/scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('offline_acceptance', SCRIPTS / 'offline_acceptance.py')
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
r = a.runner


def mock_receipt(name, directory):
    """Authored receipt for verifier unit tests; never evidence of an executed candidate."""
    directory = Path(directory)
    directory.mkdir()
    task = r.task_for(name)
    (directory / 'SKILL.md').write_bytes(r.SKILL.read_bytes())
    r.dump(directory / 'task.json', task)
    source = r.CANDIDATES[name].encode()
    (directory / 'candidate.py').write_bytes(source)
    completed = {'status': 'completed', 'exit_code': 0, 'stdout': '', 'stderr': ''}
    receipt = {
        'synthetic': True, 'fixture': name, 'task_id': task['id'],
        'skill_sha256': a.file_hash(r.SKILL),
        'runner_sha256': a.file_hash(Path(r.__file__)),
        'task_sha256': r.sha(r.encode(task)),
        'candidate_sha256': r.sha(source), 'executed_candidate_sha256': r.sha(source),
        'oracle_sha256': r.sha(r.encode(task['checks'])),
        'status': a.EXPECTED[name][0], 'grade': a.EXPECTED[name][1],
        'processes': [{**completed, 'phase': 'isolation_preflight', 'stdout': 'READY\n'},
                      {**completed, 'phase': 'candidate'}],
        'cases': [],
    }
    if name != 'forged':
        (directory / 'solution.py').write_text('# Unit test artifact; not an executed result.\n')
        receipt['artifact_sha256'] = a.file_hash(directory / 'solution.py')
        for check in task['checks']:
            value = check['input']
            if name.startswith('activation-'):
                active = (name == 'activation-always' or
                          name == 'activation-good' and value['operation'] == 'normalize_label')
                actual = {'activated': active,
                          'output': value['text'].strip().lower() if active else value['text']}
            else:
                actual = value.lower() if name == 'negative' else value.strip().lower()
            stdout = json.dumps(actual) + '\n'
            passed = r.same_answer(actual, check['expected'])
            receipt['cases'].append({'id': check['id'], 'actual': actual, 'passed': passed,
                                     'stdout_sha256': r.sha(stdout.encode())})
            receipt['processes'].append({**completed, 'phase': 'grading_candidate_execution',
                                         'case_id': check['id'], 'stdout': stdout})
        receipt['passed'] = sum(case['passed'] for case in receipt['cases'])
        receipt['total'] = len(receipt['cases'])
    if name == 'adversarial':
        receipt['isolation_probes'] = {key: 'blocked' for key in
                                      ['holdouts', 'home', 'project', 'source_write', 'network']}
    r.dump(directory / 'receipt.json', receipt)
    return receipt


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / 'run'
        with patch.object(r, 'run', side_effect=mock_receipt):
            self.report = a.run(self.out)
        self.assertEqual(self.report['status'], 'pass')

    def change_receipt(self, name, change):
        path = self.out / name / 'receipt.json'
        receipt = a.read(path)
        change(receipt)
        r.dump(path, receipt)

    def test_known_good_bad_and_activation_controls(self):
        rows = {row['fixture']: row for row in a.verify(self.out)['results']}
        self.assertEqual(rows['positive']['grade'], 'pass')
        self.assertEqual(rows['negative']['grade'], 'fail')
        self.assertEqual(rows['activation-good']['passed'], 2)
        for name in ['activation-always', 'activation-never']:
            self.assertEqual((rows[name]['grade'], rows[name]['passed']), ('fail', 1))
        self.assertEqual(rows['forged']['status'], 'expected_rejection')
        self.assertEqual(self.report['model_calls'], 0)

    def test_missing_fixture_is_rejected(self):
        (self.out / 'negative/receipt.json').unlink()
        with self.assertRaises(ValueError):
            a.verify(self.out)

    def test_missing_output_is_rejected(self):
        (self.out / 'positive/solution.py').unlink()
        with self.assertRaises(ValueError):
            a.verify(self.out)

    def test_changed_output_is_rejected(self):
        (self.out / 'positive/solution.py').write_text('changed')
        with self.assertRaises(ValueError):
            a.verify(self.out)

    def test_symlink_output_is_rejected(self):
        path = self.out / 'positive/solution.py'
        original = path.read_bytes()
        path.unlink()
        target = Path(self.temp.name) / 'copy.py'
        target.write_bytes(original)
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            a.verify(self.out)

    def test_skipped_case_is_rejected_even_with_success_grade(self):
        self.change_receipt('positive', lambda receipt: receipt['cases'].pop())
        with self.assertRaises(ValueError):
            a.verify(self.out)

    def test_missing_execution_is_rejected(self):
        self.change_receipt('positive', lambda receipt: receipt['processes'].pop())
        with self.assertRaises(ValueError):
            a.verify(self.out)

    def test_false_completion_is_rejected(self):
        self.change_receipt('positive', lambda receipt: receipt['processes'][2].update(exit_code=1))
        with self.assertRaises(ValueError):
            a.verify(self.out)

    def test_tampered_actual_answer_is_rejected(self):
        self.change_receipt('positive', lambda receipt: receipt['cases'][0].update(actual='forged'))
        with self.assertRaises(ValueError):
            a.verify(self.out)

    def test_tampered_stdout_is_rejected(self):
        self.change_receipt('positive', lambda receipt: receipt['processes'][2].update(stdout='"forged"\n'))
        with self.assertRaises(ValueError):
            a.verify(self.out)

    def test_forged_pass_count_is_rejected(self):
        self.change_receipt('negative', lambda receipt: receipt.update(grade='pass', passed=3))
        with self.assertRaises(ValueError):
            a.verify(self.out)

    def test_version_drift_is_rejected(self):
        frozen = a.read(self.out / 'plan.json')
        frozen['skill_sha256'] = '0' * 64
        r.dump(self.out / 'plan.json', frozen)
        with self.assertRaises(ValueError):
            a.verify(self.out)

    def test_copied_skill_drift_is_rejected(self):
        (self.out / 'positive/SKILL.md').write_text('changed instructions')
        with self.assertRaises(ValueError):
            a.verify(self.out)

    def test_empty_schedule_is_rejected(self):
        with patch.dict(a.EXPECTED, {}, clear=True), patch.object(r, 'run') as run:
            result = a.run(Path(self.temp.name) / 'empty')
        self.assertEqual(result['status'], 'fail')
        run.assert_not_called()

    def test_empty_checks_cannot_pass_runner(self):
        with patch.object(r, 'CHECKS', []), patch.object(r, 'bounded') as execute:
            result = r.run('positive', Path(self.temp.name) / 'empty-checks')
        self.assertEqual((result['status'], result['grade']), ('grader_error', 'inconclusive'))
        execute.assert_not_called()

    def test_infrastructure_failure_stops_and_persists_failure(self):
        target = Path(self.temp.name) / 'infra'
        with patch.object(r, 'bounded', return_value={
                'status': 'process_error', 'exit_code': 1, 'stdout': '', 'stderr': 'namespace denied'}):
            result = a.run(target)
        self.assertEqual(result['status'], 'fail')
        receipt = a.read(target / 'positive/receipt.json')
        self.assertEqual(receipt['status'], 'infrastructure_error')
        self.assertEqual(receipt['processes'][0]['stderr'], 'namespace denied')
        self.assertFalse((target / 'negative').exists())
        self.assertEqual(a.read(target / 'report.json'), result)

    def test_existing_directory_refused_without_overwriting(self):
        before = (self.out / 'report.json').read_bytes()
        with self.assertRaises(FileExistsError), patch.object(r, 'run') as run:
            a.run(self.out)
        run.assert_not_called()
        self.assertEqual((self.out / 'report.json').read_bytes(), before)

    def test_cli_rejects_stale_saved_success(self):
        r.dump(self.out / 'report.json', {'status': 'pass'})
        with patch.object(sys, 'argv', ['offline_acceptance.py', 'verify', '--out', str(self.out)]), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(a.main(), 1)

    def test_runner_cli_nonpass_is_nonzero(self):
        with patch.object(sys, 'argv', ['fixture_runner.py', 'positive', '--out', str(self.out)]), \
                patch.object(r, 'run', return_value={'status': 'infrastructure_error', 'grade': 'inconclusive'}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(r.main(), 1)


@unittest.skipUnless(os.environ.get('SKILL_EVAL_ISOLATION_TESTS') == '1',
                     'requires explicitly enabled bubblewrap execution')
class IsolatedAcceptanceTests(unittest.TestCase):
    def test_actual_clean_journey_and_independent_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'run'
            report = a.run(out)
            self.assertEqual(report['status'], 'pass', report)
            self.assertEqual(a.verify(out), report)
            rows = {row['fixture']: row for row in report['results']}
            self.assertEqual((rows['positive']['grade'], rows['negative']['grade']), ('pass', 'fail'))
            self.assertEqual(rows['activation-good']['passed'], 2)
            self.assertEqual(rows['activation-always']['passed'], 1)
            self.assertEqual(rows['activation-never']['passed'], 1)


if __name__ == '__main__':
    unittest.main()
