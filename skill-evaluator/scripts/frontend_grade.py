#!/usr/bin/env python3
"""Controller-only browser grading and public previews of one frozen HTML artifact."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / 'pilots/frontend-v1'
PINNED_MANIFEST_SHA256 = '64984801bd3f4697ff6fa2a0db87cc3a112f431c19f8695c8bb308b881170990'
MAX_ARTIFACT_BYTES = 1024 * 1024
MAX_PROCESS_OUTPUT = 128 * 1024
URL = 'https://kana-fieldbook.invalid/'
VIEWPORTS = [{'name': 'desktop', 'width': 1440, 'height': 900},
             {'name': 'mobile', 'width': 390, 'height': 844}]
CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
       "img-src data:; font-src data:; connect-src 'none'; media-src 'none'; "
       "frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def frozen_bundle():
    raw = (BUNDLE / 'manifest.json').read_bytes()
    require(sha(raw) == PINNED_MANIFEST_SHA256, 'Frozen frontend manifest changed')
    manifest = json.loads(raw)
    for name, digest in manifest['files'].items():
        target = BUNDLE / name
        require(not target.is_symlink() and sha(target.read_bytes()) == digest,
                'Frozen frontend input changed: ' + name)
    cases = json.loads((BUNDLE / 'private/cases.json').read_bytes())
    require(cases['task_id'] == manifest['task_id'] == 'kana-fieldbook-v1', 'Wrong task')
    require(cases['viewports'] == VIEWPORTS, 'Frozen viewports changed')
    require(isinstance(cases['cases'], list) and cases['cases']
            and len(cases['cases']) == len(set(cases['cases'])), 'Empty or duplicate private cases')
    return manifest, cases


def snapshot(work, out, *, require_design=False):
    work = Path(work)
    require(work.is_dir() and not work.is_symlink(), 'Missing or symlink work directory')
    path = work / 'index.html'
    require(path.is_file() and not path.is_symlink(), 'Missing or unsafe index.html artifact')
    require(0 < path.stat().st_size <= MAX_ARTIFACT_BYTES, 'Empty or oversized index.html artifact')
    raw = path.read_bytes()
    require(0 < len(raw) <= MAX_ARTIFACT_BYTES and raw.strip(), 'Empty or changed artifact')
    raw.decode('utf-8')
    artifact = Path(out) / 'artifact'
    artifact.mkdir()
    (artifact / 'index.html').write_bytes(raw)
    inventory = [{'path': 'index.html', 'bytes': len(raw), 'sha256': sha(raw)}]
    design = work / 'DESIGN.md'
    if require_design:
        require(design.is_file() and not design.is_symlink()
                and 0 < design.stat().st_size <= MAX_ARTIFACT_BYTES, 'Missing/unsafe DESIGN.md artifact')
        require(design.read_bytes().strip(), 'Empty DESIGN.md artifact')
    if design.is_file() and not design.is_symlink() and design.stat().st_size <= MAX_ARTIFACT_BYTES:
        design_raw = design.read_bytes()
        (artifact / 'DESIGN.md').write_bytes(design_raw)
        inventory.append({'path': 'DESIGN.md', 'bytes': len(design_raw), 'sha256': sha(design_raw)})
    return artifact / 'index.html', inventory


def worker_process(artifact, out, mode, wall=100):
    """Bound browser startup/execution; never inherit credentials or a user profile."""
    out = Path(out)
    require(0 < wall <= 120, 'Worker wall limit must be in 0..120 seconds')
    with tempfile.TemporaryDirectory(prefix='frontend-browser-', dir=out) as directory:
        temp = Path(directory)
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(temp), 'TMPDIR': str(temp),
               'PYTHONDONTWRITEBYTECODE': '1', 'LANG': 'C.UTF-8',
               'PLAYWRIGHT_BROWSERS_PATH': os.environ.get('PLAYWRIGHT_BROWSERS_PATH',
                                                        str(Path.home() / '.cache/ms-playwright'))}
        command = [sys.executable, '-I', str(Path(__file__).resolve()), '--worker', mode,
                   '--work', str(artifact), '--out', str(out)]
        started = time.monotonic()
        with (out / 'worker.stdout').open('wb') as stdout, (out / 'worker.stderr').open('wb') as stderr:
            process = subprocess.Popen(command, env=env, stdout=stdout, stderr=stderr,
                                       stdin=subprocess.DEVNULL, start_new_session=True)
            reason = None
            try:
                while process.poll() is None:
                    if time.monotonic() - started > wall:
                        reason = 'timeout'
                        break
                    if stdout.tell() + stderr.tell() > MAX_PROCESS_OUTPUT:
                        reason = 'output_limit'
                        break
                    time.sleep(.05)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        return {'status': reason or ('completed' if process.returncode == 0 else 'process_error'),
                'exit_code': process.returncode, 'wall_seconds': time.monotonic() - started,
                'command': command, 'cleanup': 'worker process group killed and leader reaped',
                'stdout_path': 'worker.stdout', 'stderr_path': 'worker.stderr'}


def validate_result(result, expected_cases, out):
    require(expected_cases and len(expected_cases) == len(set(expected_cases)), 'Expected nonempty unique cases')
    require(result.get('status') == 'completed', 'Browser did not complete: ' + str(result.get('status')))
    require(result.get('chromium_sandbox') is True, 'Chromium sandbox was not enabled')
    require(result.get('browser_version') and result.get('browser_binary_sha256'), 'Missing browser identity')
    rows = result.get('cases')
    require(isinstance(rows, list) and len(rows) == len(expected_cases), 'Missing case execution')
    require([r.get('id') for r in rows] == expected_cases, 'Skipped, reordered or duplicated case')
    require(all(type(r.get('passed')) is bool for r in rows), 'Non-boolean case verdict')
    screenshots = result.get('screenshots', {})
    require(set(screenshots) == {'desktop', 'desktop-practice', 'desktop-review',
                                 'mobile', 'mobile-practice', 'mobile-review'}, 'Missing screenshots')
    for row in screenshots.values():
        filename = row.get('path', '')
        require(Path(filename).name == filename and filename.endswith('.png'), 'Unsafe screenshot path')
        path = Path(out) / filename
        require(path.is_file() and not path.is_symlink() and path.stat().st_size > 100,
                'Missing/empty screenshot artifact')
        require(sha(path.read_bytes()) == row.get('sha256'), 'Screenshot digest mismatch')


def grade(work, out, *, provenance=None):
    """Grade browser outcomes. The worker reads private checks, never the candidate."""
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    receipt = {'schema': 1, 'task_id': 'kana-fieldbook-v1', 'status': 'incomplete',
               'grade': 'inconclusive', 'provenance': provenance, 'cases': [],
               'model_calls_by_grader': 0, 'grader_sha256': sha(Path(__file__).read_bytes()),
               'visual_review': {'status': 'pending', 'automatic_visual_score': None},
               'limitations': 'Authored task and browser invariants. Functional pass is not visual quality '
                              'or general skill usefulness. Local hashes are not authenticated evidence.'}
    try:
        manifest, private = frozen_bundle()
        receipt.update(manifest_sha256=PINNED_MANIFEST_SHA256,
                       task_sha256=manifest['files']['task.md'],
                       skill_sha256=manifest['files']['skill/SKILL.md'],
                       private_cases_sha256=manifest['files']['private/cases.json'])
        expected = [v['name'] + '/' + name for v in VIEWPORTS for name in private['cases']]
        receipt['expected_cases'] = len(expected)
        try:
            artifact, inventory = snapshot(work, out, require_design=True)
        except (OSError, ValueError, UnicodeError) as error:
            receipt.update(status='invalid_artifact', grade='invalid', error=str(error))
            return receipt
        receipt.update(artifact_inventory=inventory, artifact_sha256=inventory[0]['sha256'])
        process = worker_process(artifact, out, 'grade')
        receipt['process'] = process
        if process['status'] != 'completed':
            receipt.update(status='infrastructure_error', error='Browser worker ' + process['status'])
            return receipt
        result = json.loads((out / 'browser-result.json').read_text())
        receipt['browser_result'] = result
        validate_result(result, expected, out)
        rows = result['cases']
        receipt.update(status='valid', grade='pass' if all(r['passed'] for r in rows) else 'fail',
                       passed=sum(r['passed'] for r in rows), total=len(rows), cases=rows,
                       screenshots=result['screenshots'])
        return receipt
    except (OSError, ValueError, KeyError, TypeError) as error:
        receipt.update(status='grader_error', error=str(error))
        return receipt
    finally:
        save(out / 'receipt.json', receipt)


def preview(work, out):
    """Public rendering only: no private cases, answers, assertions or score exposed."""
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    receipt = {'schema': 1, 'status': 'incomplete', 'grade': 'not_graded', 'screenshots': {},
               'model_calls': 0, 'purpose': 'public visual preview, not a functional evaluation'}
    try:
        artifact, inventory = snapshot(work, out)
        receipt['artifact_inventory'] = inventory
        receipt['process'] = worker_process(artifact, out, 'preview', wall=30)
        if receipt['process']['status'] != 'completed':
            receipt.update(status='infrastructure_error', error=receipt['process']['status'])
            return receipt
        result = json.loads((out / 'browser-result.json').read_text())
        receipt.update(result)
        require(result['status'] == 'completed' and set(result['screenshots']) == {'desktop', 'mobile'},
                'Incomplete browser preview')
        return receipt
    except (OSError, ValueError, KeyError, TypeError) as error:
        receipt.update(status='preview_error', error=str(error))
        return receipt
    finally:
        save(out / 'preview.json', receipt)


def browser_worker(artifact, out, mode):
    """Trusted Playwright actions. The page receives only HTML bytes and public input."""
    from playwright.sync_api import sync_playwright
    out = Path(out)
    raw = Path(artifact).read_bytes()
    report = {'status': 'incomplete', 'chromium_sandbox': True, 'cases': [], 'screenshots': {},
              'console_errors': [], 'blocked_requests': [], 'playwright_version': importlib.metadata.version('playwright')}

    def phase(name):
        save(out / 'browser-phase.json', {'phase': name, 'time_unix': time.time()})

    try:
        phase('starting_playwright')
        private = frozen_bundle()[1] if mode == 'grade' else None
        with sync_playwright() as playwright:
            phase('launching_chromium')
            browser = playwright.chromium.launch(headless=True, chromium_sandbox=True,
                                                timeout=15000, env={'PATH': '/usr/bin:/bin',
                                                                   'HOME': os.environ['HOME'],
                                                                   'TMPDIR': os.environ['TMPDIR']})
            report['browser_version'] = browser.version
            phase('chromium_launched')
            # launch(headless=True) uses headless_shell; record the exact shipped binary.
            engine = Path(playwright.chromium.executable_path)
            shell = engine.parents[1].parent / engine.parents[1].name.replace('chromium-', 'chromium_headless_shell-')
            candidates = list(shell.glob('*/chrome-headless-shell'))
            binary = candidates[0] if len(candidates) == 1 else engine
            report['browser_binary_sha256'] = sha(binary.read_bytes())
            report['browser_binary'] = str(binary)
            for viewport in VIEWPORTS:
                name = viewport['name']
                phase(name + ':opening_context')
                context = browser.new_context(viewport={k: viewport[k] for k in ('width', 'height')},
                                              service_workers='block', accept_downloads=False,
                                              permissions=[], reduced_motion='reduce')
                page = context.new_page()
                page.set_default_timeout(1200)
                errors, blocked = [], []
                page.on('pageerror', lambda error: errors.append(str(error)[:2000]) if len(errors) < 30 else None)
                page.on('console', lambda message: errors.append(message.text[:2000])
                        if message.type == 'error' and len(errors) < 30 else None)

                def route(request_route):
                    request = request_route.request
                    if request.url == URL and request.resource_type == 'document' and request.frame == page.main_frame:
                        request_route.fulfill(status=200, body=raw, headers={
                            'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': CSP,
                            'X-Content-Type-Options': 'nosniff'})
                    else:
                        if len(blocked) < 30:
                            blocked.append(request.url[:1000])
                        request_route.abort('blockedbyclient')

                context.route('**/*', route)

                def websocket(ws):
                    if len(blocked) < 30:
                        blocked.append(ws.url[:1000])
                    # No connect_to_server call: Playwright routes this socket to a
                    # local mock. Discard messages and close it with the context.
                    # Calling close synchronously in this handler deadlocks before
                    # Playwright completes its mock handshake in version 1.58.
                    ws.on_message(lambda message: None)

                context.route_web_socket('**/*', websocket)

                def shot(label):
                    path = out / (label + '.png')
                    page.screenshot(path=str(path), full_page=False, timeout=5000)
                    report['screenshots'][label] = {'path': path.name, 'sha256': sha(path.read_bytes()),
                                                    'width': viewport['width'], 'height': viewport['height']}

                phase(name + ':loading_artifact')
                page.goto(URL, wait_until='domcontentloaded', timeout=10000)
                phase(name + ':screenshot')
                shot(name)
                if mode == 'grade':
                    evaluate_page(page, private, name, report['cases'], shot, errors, blocked)
                report['console_errors'].append({'viewport': name, 'errors': errors})
                report['blocked_requests'].append({'viewport': name, 'requests': blocked})
                phase(name + ':closing_context')
                context.close()
            phase('closing_browser')
            browser.close()
        report['status'] = 'completed'
        phase('completed')
    except Exception as error:
        report.update(status='infrastructure_error', error=type(error).__name__ + ': ' + str(error)[:4000])
    finally:
        save(out / 'browser-result.json', report)
    return report


def evaluate_page(page, private, viewport_name, rows, shot, errors, blocked):
    pairs = private['pairs']
    layout = []
    captured = set()

    def capture(suffix):
        shot(viewport_name + '-' + suffix)
        captured.add(suffix)

    def check(condition, message):
        if not condition:
            raise AssertionError(message)

    def view(name):
        page.get_by_role('button', name=name, exact=True).click()
        for other in ('Learn', 'Practice', 'Review'):
            check(page.get_by_role('button', name=other, exact=True).get_attribute('aria-pressed')
                  == str(other == name).lower(), 'Active view not accurately announced: ' + other)
        width = page.evaluate('''({page:document.documentElement.scrollWidth, viewport:innerWidth,
          targets:[...document.querySelectorAll('button,input')].filter(e=>e.getClientRects().length)
            .map(e=>({label:e.textContent||e.getAttribute('aria-label')||e.id,
              x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y,
              width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height}))})''')
        layout.append({'view': name, **width})

    def progress(number):
        view('Review')
        actual = page.get_by_test_id('review-progress').inner_text().strip()
        check(actual == f'{number} of 5 learned', f'Progress: expected {number} of 5 learned, got {actual!r}')
        return actual

    def glyph(expected):
        actual = page.get_by_test_id('practice-kana').inner_text().strip()
        check(actual == expected, f'Expected current glyph {expected}, got {actual!r}')

    def answer(value, feedback):
        page.get_by_label('Your answer', exact=True).fill(value)
        page.get_by_role('button', name='Check answer', exact=True).click()
        actual = page.get_by_test_id('answer-feedback').inner_text().strip()
        check(feedback in actual, f'Expected {feedback!r} feedback, got {actual!r}')
        check(page.get_by_test_id('answer-feedback').get_attribute('role') == 'status', 'Feedback not announced')
        return actual

    def next_card(expected):
        page.get_by_role('button', name='Next card', exact=True).click()
        glyph(expected)
        check(page.get_by_label('Your answer', exact=True).input_value() == '', 'Next retained old input')
        check(page.get_by_test_id('answer-feedback').inner_text().strip() == '', 'Next retained old feedback')

    def reset():
        view('Review')
        page.get_by_role('button', name='Reset progress', exact=True).click()
        progress(0)
        view('Practice')
        glyph('あ')

    def learn():
        check(page.get_by_role('heading', name='Kana Fieldbook', exact=True).is_visible(), 'Missing visible heading')
        check(page.get_by_role('main').count() == 1, 'Expected semantic main landmark')
        check(page.get_by_role('button', name='Learn', exact=True).get_attribute('aria-pressed') == 'true', 'Learn not initially active')
        text = page.locator('body').inner_text()
        for kana, roman in pairs:
            check(kana in text and re.search(r'(?<![a-z])' + roman + r'(?![a-z])', text.lower()),
                  'Missing learning pair ' + kana)
        check(not page.get_by_label('Your answer', exact=True).is_visible(), 'Practice input visible in Learn')
        return {'visible_pairs': pairs}

    def keyboard():
        page.get_by_role('button', name='Learn', exact=True).focus()
        page.keyboard.press('Tab')
        check(page.get_by_role('button', name='Practice', exact=True).evaluate('(e)=>e===document.activeElement'), 'Practice not in keyboard order')
        page.keyboard.press('Enter')
        glyph('あ')
        check(not page.get_by_role('button', name='Reset progress', exact=True).is_visible(),
              'Inactive Review controls remain visible in Practice')
        page.get_by_label('Your answer', exact=True).focus()
        page.keyboard.press('Enter')
        check('Enter an answer' in page.get_by_test_id('answer-feedback').inner_text(), 'Enter did not submit')
        capture('practice')
        return {'tab_reached_practice': True, 'enter_submitted': True}

    def empty():
        view('Practice')
        feedback = answer('   ', 'Enter an answer')
        glyph('あ')
        progress(0)
        return {'feedback': feedback, 'learned': 0}

    def wrong():
        view('Practice')
        feedback = answer('xyz', 'Try again')
        glyph('あ')
        progress(0)
        return {'feedback': feedback, 'learned': 0}

    def normalized():
        view('Practice')
        feedback = answer(' A ', 'Correct')
        progress(1)
        page.reload(wait_until='domcontentloaded', timeout=5000)
        progress(1)
        return {'input': ' A ', 'feedback': feedback, 'learned': 1, 'learned_after_reload': 1}

    def duplicate():
        view('Practice')
        answer('a', 'Correct')
        progress(1)
        return {'learned_after_repeat': 1}

    def skip():
        view('Practice')
        next_card('い')
        next_card('う')
        progress(1)
        remaining = page.get_by_test_id('remaining-kana').inner_text()
        check('い' in remaining and 'う' in remaining and 'あ' not in remaining, 'Skipped cards received credit')
        return {'learned_after_skip': 1, 'remaining': remaining}

    def all_five():
        reset()
        for index, (kana, roman) in enumerate(pairs):
            glyph(kana)
            answer(roman, 'Correct')
            if index < len(pairs) - 1:
                next_card(pairs[index + 1][0])
        progress(5)
        remaining = page.get_by_test_id('remaining-kana').inner_text().strip()
        check(remaining and not any(p[0] in remaining for p in pairs), 'Completion still lists unlearned kana')
        capture('review')
        view('Practice')
        next_card('あ')
        answer('a', 'Correct')
        progress(5)
        return {'learned': 5, 'completion': remaining, 'wrap_to_first': True, 'learned_after_revisit': 5}

    def persisted():
        page.reload(wait_until='domcontentloaded', timeout=5000)
        progress(5)
        return {'learned_after_reload': 5}

    def cleared():
        reset()
        page.reload(wait_until='domcontentloaded', timeout=5000)
        progress(0)
        view('Practice')
        glyph('あ')
        return {'learned_after_reload': 0, 'first_card': 'あ'}

    def corrupt(value):
        page.evaluate('localStorage.removeItem("kana-fieldbook-progress")')
        page.reload(wait_until='domcontentloaded', timeout=5000)
        progress(0)
        before = page.locator('body').inner_text().lower()
        page.evaluate('(v)=>localStorage.setItem("kana-fieldbook-progress",v)', value)
        page.reload(wait_until='domcontentloaded', timeout=5000)
        progress(0)
        text = page.locator('body').inner_text().lower()
        check(text != before and any(word in text for word in ('saved', 'recover', 'reset', 'invalid', 'read', 'corrupt')),
              'No storage-recovery notice')
        return {'injected_storage': value, 'learned_after_recovery': 0}

    def runtime_errors():
        check(not errors, 'Browser errors: ' + repr(errors))
        return {'errors': list(errors)}

    def stale_and_write_failure():
        observed = corrupt('{"version":999,"learned":["あ","alien"]}')
        page.evaluate('localStorage.clear()')
        page.add_init_script('Storage.prototype.setItem=function(){throw new DOMException("Controller quota control","QuotaExceededError")}')
        page.reload(wait_until='domcontentloaded', timeout=5000)
        view('Practice')
        glyph('あ')
        answer('a', 'Correct')
        progress(1)
        observed['storage_write_failure_kept_practice_usable'] = True
        return observed

    def network():
        check(not blocked, 'Page attempted outbound requests: ' + repr(blocked))
        return {'blocked_requests': list(blocked), 'csp': CSP, 'service_workers': 'block'}

    def responsive():
        check(layout and all(x['page'] <= x['viewport'] + 1 for x in layout), 'Horizontal overflow: ' + repr(layout))
        for observation in layout:
            targets = observation['targets']
            for i, first in enumerate(targets):
                check(first['x'] >= -1 and first['x'] + first['width'] <= observation['viewport'] + 1,
                      'Control outside viewport: ' + first['label'])
                for second in targets[i + 1:]:
                    overlap_x = min(first['x'] + first['width'], second['x'] + second['width']) - max(first['x'], second['x'])
                    overlap_y = min(first['y'] + first['height'], second['y'] + second['height']) - max(first['y'], second['y'])
                    check(overlap_x <= 1 or overlap_y <= 1, 'Overlapping controls: ' + first['label'] + ', ' + second['label'])
        for name in ('Learn', 'Practice', 'Review'):
            button = page.get_by_role('button', name=name, exact=True)
            box = button.bounding_box()
            check(box is not None and box['width'] >= 24 and box['height'] >= 24, 'Navigation target too small')
        return {'observations': layout}

    actions = {'learn-content': learn, 'keyboard-navigation': keyboard, 'empty-answer': empty,
               'wrong-answer': wrong, 'correct-normalization': normalized, 'duplicate-credit': duplicate,
               'skip-no-credit': skip, 'all-five-recall': all_five, 'persisted-progress': persisted,
               'reset-progress': cleared, 'corrupt-storage': lambda: corrupt('{not-json'),
               'stale-storage': stale_and_write_failure,
               'runtime-errors': runtime_errors, 'network-denied': network, 'responsive-layout': responsive}
    for case_id in private['cases']:
        started = time.monotonic()
        row = {'id': viewport_name + '/' + case_id, 'passed': False}
        try:
            row['observed'] = actions[case_id]()
            row['passed'] = True
        except Exception as error:
            row['error'] = type(error).__name__ + ': ' + str(error)[:3000]
        row['wall_seconds'] = time.monotonic() - started
        rows.append(row)
    # Failed controls still produce all viewport evidence; missing views stay visible
    # as failures rather than making the screenshot requirement a skipped check.
    for suffix in ('practice', 'review'):
        if suffix not in captured:
            # Capture the actual resulting state, even when the interaction failed.
            capture(suffix)


def controls(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    result = {'schema': 1, 'classification': 'authored browser controls; not model-performance evidence',
              'status': 'incomplete', 'grade': 'inconclusive', 'model_calls': 0, 'controls': {}}
    try:
        frozen_bundle()
        for name in ('good', 'claim-only', 'wrong-credit', 'partial-persistence', 'missing', 'empty'):
            work = out / (name + '-work')
            work.mkdir()
            if name not in ('missing', 'empty'):
                (work / 'index.html').write_bytes((BUNDLE / 'private/controls' / (name + '.html')).read_bytes())
                (work / 'DESIGN.md').write_bytes((BUNDLE / 'private/controls/DESIGN.md').read_bytes())
            elif name == 'empty':
                (work / 'index.html').write_text('')
            receipt = grade(work, out / name, provenance={'kind': 'authored_control', 'name': name})
            result['controls'][name] = {'status': receipt['status'], 'grade': receipt['grade'],
                                        'receipt': name + '/receipt.json'}
        expected = {'good': ('valid', 'pass'), 'claim-only': ('valid', 'fail'),
                    'wrong-credit': ('valid', 'fail'), 'partial-persistence': ('valid', 'fail'),
                    'missing': ('invalid_artifact', 'invalid'),
                    'empty': ('invalid_artifact', 'invalid')}
        passed = all((result['controls'][k]['status'], result['controls'][k]['grade']) == v
                     for k, v in expected.items())
        result.update(status='valid', grade='pass' if passed else 'fail')
        return result
    except (OSError, ValueError, KeyError, TypeError) as error:
        result.update(status='control_error', error=str(error))
        return result
    finally:
        save(out / 'controls.json', result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work')
    parser.add_argument('--out', required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--controls', action='store_true')
    group.add_argument('--preview', action='store_true')
    group.add_argument('--worker', choices=['preview', 'grade'], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        result = browser_worker(args.work, args.out, args.worker)
        return 0 if result['status'] == 'completed' else 2
    if args.controls:
        result = controls(args.out)
    elif args.work:
        result = preview(args.work, args.out) if args.preview else grade(args.work, args.out)
    else:
        parser.error('--work is required unless --controls is used')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.preview:
        return 0 if result['status'] == 'completed' else 2
    return 0 if result['status'] == 'valid' and result['grade'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
