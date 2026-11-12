"""`skillsync prototype` F2: iterate, restore (with interruptions), commit before the iteration, agent CLI, merge-comments.

Contract: spec zadania (poza rdzeniem) (K23, K29, K32, K35, K37, K40; "Przywrócenie:
kolejność i odtwarzanie").  The tests observe the CLI reports and the files of a temporary project; crashes are real
process deaths (`os._exit` at a chosen step in a child process, no `finally` runs), not exceptions.  Every negative
test has a control that passes (the same call with correct input).  Git tests use a temporary repository with its own
identity and never touch the working tree of this repository.
"""
import hashlib
import json
import time
import threading
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype import CLI, PrototypeFixture, tree  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu tymczasowego)

PW = 'Haslo-testowe-1234'
ACCEPTOR = 'ala.kot@example.com'
DEVELOPER = 'dev.one@example.com'
COMMENTER = 'kom.ent@example.com'

HEAD = ('<!doctype html>\n<html lang="pl" data-prototype-id="demo">\n<head>\n<meta charset="utf-8">\n'
        '<title>Demo</title>\n<link rel="stylesheet" href="screens.css">\n</head>\n<body>\n')
TAIL = '<script src="prototype.js"></script>\n</body>\n</html>\n'
S1 = '<section class="screen" id="s1" data-prototype-example>\n  <div class="frame">Przykład</div>\n</section>\n'
S2_V1 = ('<section class="screen" id="s2">\n  <div class="frame"><h2 data-proto-id="s2-h">Tydzień</h2>'
         '<button type="button" data-proto-id="s2-btn-a">A</button></div>\n</section>\n')
S2_V2 = S2_V1.replace('<button type="button" data-proto-id="s2-btn-a">A</button>',
                      '<button type="button" data-proto-id="s2-btn-b">B</button>')
S3_V1 = ('<section class="screen" id="s3">\n  <div class="frame"><h2 data-proto-id="s3-h">Inny</h2></div>\n'
         '</section>\n')
S3_V2 = S3_V1.replace('Inny', 'Inny zmieniony')


def page(s2=S2_V1, s3=S3_V1, extra=''):
    return HEAD + S1 + s2 + s3 + extra + TAIL


CRASH_SCRIPT = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
from skillsync_workflow import prototype_server as ps
directory, step, mode = sys.argv[2], sys.argv[3], sys.argv[4]
real = ps.atomic_write_bytes
def hook(path, data, *args, **kwargs):
    name = os.path.basename(path)
    nested = os.sep + 'iterations' + os.sep in path
    if step == 'before_marker' and name == '.restore-pending.tmp':
        os._exit(137)   # F06: nothing of the restore has happened yet
    if step == 'before_journal' and name == 'comments.json':
        os._exit(137)   # step (2) done, step (3) not started
    if step == 'before_index' and name == 'index.html' and not nested:
        os._exit(137)   # steps (2) and (3) done, step (4) not started
    return real(path, data, *args, **kwargs)
ps.atomic_write_bytes = hook
if step == 'before_freeze':
    def no_freeze(*args, **kwargs):
        os._exit(137)   # F06: marker written, step (2) not started
    ps.freeze_iteration = no_freeze
if step == 'before_unmark':
    def no_unmark(*args, **kwargs):
        os._exit(137)   # F06: step (4) done, the marker not removed yet
    ps.remove_restore_marker = no_unmark
store = ps.CommentsStore(directory, 'demo')
if mode == 'iterate':
    ps.perform_iterate(store, directory)
else:
    ps.perform_restore(store, directory, 's2', 1, 'powód', by='agent', who=sys.argv[5])
os._exit(0)   # the crash point was never reached
'''


def journal(directory):
    path = Path(directory) / 'comments.json'
    return json.loads(path.read_text(encoding='utf-8'))['operations'] if path.exists() else []


class RestoreFixture(PrototypeFixture):
    """Prototype `demo` with screens s1 (example), s2, s3 and users with the three roles."""

    def setUp(self):
        super().setUp()
        self.init('demo')
        self.dir = self.proto_dir()
        self.write_page(page())
        users = ps.UsersFile(str(self.root / '.skillsync' / 'prototype' / 'users.json'), iterations=1000)
        ps.add_user(users, ACCEPTOR, role='acceptor', password=PW)
        ps.add_user(users, DEVELOPER, role='developer', password=PW)
        ps.add_user(users, COMMENTER, role='commenter', password=PW)

    # -- files ----------------------------------------------------------------
    def write_page(self, text):
        (self.dir / 'index.html').write_text(text, encoding='utf-8', newline='')

    def html(self):
        return (self.dir / 'index.html').read_text(encoding='utf-8')

    def frozen(self, number):
        return (self.dir / 'iterations' / str(number) / 'index.html').read_text(encoding='utf-8')

    def iteration_dirs(self):
        parent = self.dir / 'iterations'
        return sorted(os.listdir(parent)) if parent.is_dir() else []

    def ops(self, kind=None):
        return [op for op in journal(self.dir) if kind is None or op['type'] == kind]

    def store(self):
        return ps.CommentsStore(str(self.dir), 'demo')

    def thread(self, thread_id, message_id, screen, proto_id, by=ACCEPTOR):
        anchor = ({'kind': 'element', 'protoId': proto_id, 'x': 0.5, 'y': 0.5, 'iteration': 1} if proto_id
                  else {'kind': 'screen', 'x': 0.1, 'y': 0.1, 'iteration': 1})
        self.store().append([{'type': 'create', 'threadId': thread_id, 'by': by,
                              'payload': {'message': {'id': message_id, 'text': 'uwaga ' + thread_id},
                                          'screen': screen, 'anchor': anchor}}])

    def snapshot(self):
        """The prototype directory as bytes, without the lock file (an empty, git-ignored by-product of every lock)."""
        result = tree(self.dir)
        result.pop('comments.json.lock', None)
        return result

    # -- CLI -----------------------------------------------------------------
    def iterate(self, code=0, **kwargs):
        return self.proto('iterate', 'demo', code=code, **kwargs)

    def restore(self, screen='s2', source=1, who=ACCEPTOR, reason='wróć', code=0, extra=(), **kwargs):
        args = ['demo', '--screen', screen, '--from', str(source), '--reason', reason, '--who', who, *extra]
        return self.proto('restore', *args, code=code, **kwargs)

    def refused(self, error=None, **kwargs):
        """`restore` that must end BLOCKED without touching the prototype directory."""
        before = self.snapshot()
        report = self.restore(code=2, **kwargs)
        self.assertEqual(self.snapshot(), before, 'a refused restore must write nothing')
        if error is not None:
            self.assertEqual(report.get('error'), error, report)
        return report

    def crash(self, step, mode, who=ACCEPTOR):
        result = subprocess.run([sys.executable, '-c', CRASH_SCRIPT, str(ROOT), str(self.dir), step, mode, who],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 137, 'the child must die at the crash point: ' + result.stderr)

    def sk(self, *argv, cwd=None, env=None):
        """Raw CLI call (arguments as given); returns (exit code, JSON report)."""
        environment = dict(os.environ)
        environment.update(env or {})
        result = subprocess.run([sys.executable, str(CLI), *map(str, argv)], capture_output=True, text=True,
                                timeout=60, cwd=str(cwd or self.base), env=environment)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result.returncode, json.loads(result.stdout)


# ---------------------------------------------------------------------------
# K23: iterate
# ---------------------------------------------------------------------------
class IterateTests(RestoreFixture):
    def test_iterate_freezes_the_current_version_and_records_the_operation(self):
        (self.dir / 'comments.json.lock').write_text('', encoding='utf-8')
        before_index = (self.dir / 'index.html').read_bytes()
        report = self.iterate()
        self.assertEqual((report['iteration'], report['current'], report['recovered']), (1, 2, False))
        self.assertEqual(report['path'], 'docs/prototypes/demo/iterations/1/')
        self.assertIsNone(report['commit'])
        frozen = self.dir / 'iterations' / '1'
        names = set(os.listdir(frozen))
        self.assertIn('index.html', names)
        self.assertIn('prototype.js', names)  # control: ordinary files are copied
        for excluded in ('comments.json', 'comments.json.lock', 'iterations', '.gitignore', '.gitattributes'):
            self.assertNotIn(excluded, names, excluded)
        self.assertFalse(any('.tmp-' in name for name in names))
        text = (frozen / 'index.html').read_text(encoding='utf-8')
        self.assertIn('data-prototype-iteration="1" data-prototype-frozen', text)
        self.assertNotIn('data-prototype-frozen', self.html())
        self.assertEqual((self.dir / 'index.html').read_bytes(), before_index, 'the source page is not touched')
        operation = self.ops('iteration')[0]
        self.assertEqual(operation['by'], 'agent')
        self.assertEqual((operation['payload']['number'], operation['payload']['path']), (1, 'iterations/1/'))
        self.assertRegex(operation['payload']['hash'], r'^[0-9a-f]{64}$')
        self.assertEqual(operation['payload']['hash'], report['hash'])

    def test_iterations_are_numbered_upwards_and_frozen_ones_never_change(self):
        self.iterate()
        first = (self.dir / 'iterations' / '1' / 'index.html').read_bytes()
        self.write_page(page(S2_V2))
        second = self.iterate()
        self.assertEqual((second['iteration'], second['current']), (2, 3))
        self.assertEqual((self.dir / 'iterations' / '1' / 'index.html').read_bytes(), first)
        self.assertIn('s2-btn-b', self.frozen(2))
        self.assertNotIn('s2-btn-b', self.frozen(1))
        self.assertEqual([op['payload']['number'] for op in self.ops('iteration')], [1, 2])
        self.assertEqual(self.proto('list')['prototypes'][0]['iteration'], 3, 'K32: list follows the iterations')

    def test_the_frozen_copy_has_no_nested_copy_of_earlier_iterations(self):
        self.iterate()
        self.iterate()
        self.assertEqual(self.iteration_dirs(), ['1', '2'])
        self.assertNotIn('iterations', os.listdir(self.dir / 'iterations' / '2'))

    def test_missing_ids_fail_with_exit_1_and_write_nothing(self):
        self.write_page(page(S2_V1.replace(' data-proto-id="s2-btn-a"', '')))
        before = self.snapshot()
        report = self.iterate(code=1)
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual([item['tag'] for item in report['missing']], ['button'])
        self.assertEqual(self.snapshot(), before)
        self.write_page(page())  # control: the same prototype with the handle passes
        self.assertEqual(self.iterate()['iteration'], 1)

    def test_duplicated_ids_fail_with_exit_1(self):
        self.write_page(page(S2_V1.replace('s2-h', 's2-btn-a')))
        report = self.iterate(code=1)
        self.assertEqual(report['duplicates'], ['s2-btn-a'])
        self.assertEqual(self.iteration_dirs(), [])

    def test_unknown_slug_and_v1_prototype_are_blocked(self):
        unknown = self.proto('iterate', 'nie-ma', code=2)
        self.assertTrue(unknown['reason'])
        self.set_comments('demo', 'window.__SKILLSYNC_PROTOTYPE_COMMENTS__ = {"version":1,"prototypeId":"demo","operations":[]};\n')
        report = self.iterate(code=2)
        self.assertIn('migrate', report['reason'])
        self.assertEqual(self.iteration_dirs(), [])


# ---------------------------------------------------------------------------
# K37: recovery of an interrupted iterate
# ---------------------------------------------------------------------------
class IterateRecoveryTests(RestoreFixture):
    def test_crash_after_the_freeze_is_completed_without_a_second_freeze(self):
        self.crash('before_journal', 'iterate')
        self.assertEqual(self.iteration_dirs(), ['1'])
        self.assertEqual(self.ops('iteration'), [], 'the operation was not written before the crash')
        report = self.iterate()
        self.assertTrue(report['recovered'])
        self.assertEqual((report['iteration'], report['current']), (1, 2))
        self.assertEqual(self.iteration_dirs(), ['1'], 'no second freeze of the same content')
        operations = self.ops('iteration')
        self.assertEqual([op['payload']['number'] for op in operations], [1])
        self.assertEqual(operations[0]['by'], 'agent')
        frozen_hash = operations[0]['payload']['hash']
        self.assertEqual(frozen_hash, report['hash'])
        follow = self.iterate()  # control: the next call is an ordinary freeze of iteration 2
        self.assertEqual((follow['iteration'], follow['recovered']), (2, False))

    def test_an_orphan_directory_that_differs_from_the_current_version_is_incomplete_restore(self):
        self.crash('before_journal', 'iterate')
        self.write_page(page(S2_V2))  # the page changed after the crash
        before = self.snapshot()
        report = self.iterate(code=2)
        self.assertEqual(report['error'], 'incomplete_restore')
        self.assertIn('iterations/1/', report['reason'])
        self.assertEqual(self.snapshot(), before, 'refusal writes nothing')
        shutil.rmtree(self.dir / 'iterations' / '1')  # control: after the removal the iteration works
        self.assertEqual(self.iterate()['iteration'], 1)

    def test_an_orphan_with_a_foreign_number_is_incomplete_restore(self):
        self.iterate()
        shutil.copytree(self.dir / 'iterations' / '1', self.dir / 'iterations' / '7')
        report = self.iterate(code=2)
        self.assertEqual(report['error'], 'incomplete_restore')
        self.assertEqual(self.iteration_dirs(), ['1', '7'])
        shutil.rmtree(self.dir / 'iterations' / '7')
        self.assertEqual(self.iterate()['iteration'], 2)

    def test_staging_directories_are_removed_and_reported(self):
        staging = self.dir / 'iterations' / '1.tmp-abc123'
        staging.mkdir(parents=True)
        (staging / 'index.html').write_text('<html>half</html>', encoding='utf-8')
        report = self.iterate()
        self.assertEqual(report['removed'], ['iterations/1.tmp-abc123/'])
        self.assertEqual(self.iteration_dirs(), ['1'])
        self.assertEqual(report['iteration'], 1)


# ---------------------------------------------------------------------------
# K37: restore
# ---------------------------------------------------------------------------
class RestoreTests(RestoreFixture):
    def prepare(self):
        """Iteration 1 = S2_V1/S3_V1; the current page has S2_V2 and a changed S3."""
        self.iterate()
        self.write_page(page(S2_V2, S3_V2))

    def test_restore_replaces_only_the_section_of_the_screen(self):
        self.prepare()
        head_before = self.html().split('<body>')[0]
        report = self.restore()
        self.assertEqual((report['frozen'], report['current']), (2, 3))
        now = self.html()
        self.assertIn(S2_V1, now)
        self.assertNotIn(S2_V2, now)
        self.assertIn(S3_V2, now, 'the other screen stays as it was')
        self.assertEqual(now.split('<body>')[0], head_before, 'the head stays current')
        self.assertIn(S2_V2, self.frozen(2), 'iteration 2 holds the version from before the restore')
        self.assertIn('data-prototype-iteration="2" data-prototype-frozen', self.frozen(2))
        self.assertEqual(self.iteration_dirs(), ['1', '2'], 'nothing is removed')
        tail = self.ops()[-2:]
        self.assertEqual([op['type'] for op in tail], ['iteration', 'restore'])
        payload = tail[1]['payload']
        self.assertEqual((payload['screen'], payload['from'], payload['frozen'], payload['reason'], payload['who']),
                         ('s2', 1, 2, 'wróć', ACCEPTOR))
        self.assertNotEqual(payload['hashBefore'], payload['hashAfter'])
        self.assertEqual(tail[1]['by'], 'agent')
        self.assertEqual(self.proto('list')['prototypes'][0]['iteration'], 3)

    def test_restore_can_be_undone_by_a_restore_from_the_freeze(self):
        self.prepare()
        self.restore()
        self.assertIn(S2_V1, self.html())
        undone = self.restore(source=2, who=DEVELOPER, reason='cofnij')
        self.assertEqual((undone['frozen'], undone['current']), (3, 4))
        self.assertIn(S2_V2, self.html())
        self.assertNotIn(S2_V1, self.html())
        self.assertEqual(self.iteration_dirs(), ['1', '2', '3'])

    def test_dry_run_reports_the_effects_and_writes_nothing(self):
        self.thread('th-a', 'm-a', 's2', 's2-btn-a')   # handle comes back after the restore
        self.thread('th-b', 'm-b', 's2', 's2-btn-b')   # handle is lost after the restore
        self.thread('th-c', 'm-c', 's3', 's3-h')       # other screen: not counted
        self.thread('th-d', 'm-d', 's2', None)         # screen anchor: not counted
        self.prepare()
        before = self.snapshot()
        dry = self.restore(extra=['--dry-run'])
        self.assertTrue(dry['dryRun'])
        self.assertEqual((dry['threadsBack'], dry['threadsLost'], dry['acceptanceLost']), (1, 1, 0))
        self.assertEqual((dry['frozen'], dry['current']), (2, 3))
        self.assertEqual(self.snapshot(), before)
        real = self.restore()  # control: the real restore reports the same numbers
        self.assertEqual((real['threadsBack'], real['threadsLost'], real['acceptanceLost']), (1, 1, 0))
        self.assertNotEqual(self.snapshot(), before)

    def test_only_visible_unfinished_threads_are_counted(self):
        self.thread('th-open', 'm-1', 's2', 's2-btn-a')
        self.thread('th-closed', 'm-2', 's2', 's2-btn-a')
        self.thread('th-deleted', 'm-3', 's2', 's2-btn-a')
        self.store().append([
            {'type': 'resolve', 'threadId': 'th-closed', 'by': ACCEPTOR, 'payload': {'reason': 'done'}},
            {'type': 'delete', 'threadId': 'th-deleted', 'by': DEVELOPER, 'payload': {}}])
        self.prepare()
        self.assertEqual(self.restore(extra=['--dry-run'])['threadsBack'], 1)

    def test_restore_refused_for_a_missing_iteration(self):
        self.prepare()
        report = self.refused('iteration_not_found', source=7)
        self.assertIn('iterations/7/', report['reason'])
        self.assertEqual(self.restore()['frozen'], 2)  # control

    def test_restore_refused_when_the_screen_is_missing_in_the_current_version_or_in_the_iteration(self):
        self.prepare()
        self.refused('screen_missing', screen='s9')
        extra = '<section class="screen" id="s4">\n  <div class="frame"><h2 data-proto-id="s4-h">Nowy</h2></div>\n</section>\n'
        self.write_page(page(S2_V2, S3_V2, extra))
        report = self.refused('screen_missing', screen='s4')
        self.assertIn('iteration 1', report['reason'], 's4 exists now but not in iteration 1')
        self.write_page(page(S2_V2, S3_V2))
        self.assertEqual(self.restore()['frozen'], 2)  # control

    def test_restore_refused_for_an_ambiguous_section(self):
        self.prepare()
        nested = page(S2_V2, S3_V2.replace('</section>', S2_V2.replace('s2-', 's2x-').replace('id="s2"', 'id="s5"')
                                           + '</section>'))
        self.write_page(nested)
        self.refused('ambiguous_section', screen='s3')
        self.write_page(page(S2_V2, S3_V2))
        self.assertEqual(self.restore()['frozen'], 2)  # control

    def test_restore_refused_when_the_result_fails_ids_check(self):
        self.prepare()
        duplicate = S3_V2.replace('s3-h', 's2-btn-a')   # s2-btn-a is in iteration 1 section s2
        self.write_page(page(S2_V2, duplicate))
        report = self.refused('ids_check_failed')
        self.assertEqual(report['duplicates'], ['s2-btn-a'])
        self.write_page(page(S2_V2, S3_V2))
        # a handle missing in the frozen section also fails (the frozen copy is edited by hand)
        frozen = self.dir / 'iterations' / '1' / 'index.html'
        original = frozen.read_text(encoding='utf-8')
        frozen.write_text(original.replace(' data-proto-id="s2-btn-a"', ''), encoding='utf-8')
        report = self.refused('ids_check_failed')
        self.assertEqual([item['tag'] for item in report['missing']], ['button'])
        frozen.write_text(original, encoding='utf-8')
        self.assertEqual(self.restore()['frozen'], 2)  # control

    def test_restore_needs_a_privileged_existing_user(self):
        self.prepare()
        report = self.refused(who=COMMENTER)
        self.assertIn('forbidden', report['reason'])
        self.refused(who='nikt@example.com')
        self.refused(who='to nie jest adres')
        users = self.root / '.skillsync' / 'prototype' / 'users.json'
        users.rename(users.with_name('users.off'))
        self.assertIn('users_file_missing', self.restore(code=2)['reason'])
        users.with_name('users.off').rename(users)
        self.assertEqual(self.restore(who=DEVELOPER)['frozen'], 2)  # control: developer is allowed

    def test_restore_argument_validation(self):
        self.prepare()
        for kwargs in ({'reason': '   '}, {'reason': 'x' * 501}, {'source': 0}, {'screen': 'ekran'}):
            with self.subTest(kwargs=kwargs):
                self.refused(**kwargs)
        missing = self.proto('restore', 'demo', '--screen', 's2', code=2)
        self.assertIn('--from', missing['reason'])
        self.assertEqual(self.restore(reason='x' * 500)['frozen'], 2)  # control: the limit is 500 characters

    def test_restore_of_a_screen_equal_to_the_current_one_is_pass_unchanged_without_a_write(self):
        """D27: the section of iteration 1 equals the current one: PASS `unchanged`, no freeze, no operation."""
        self.iterate()
        before = self.snapshot()
        report = self.restore()
        self.assertEqual((report['status'], report['result']), ('PASS', 'unchanged'))
        self.assertRegex(report['version'], r'^[0-9a-f]{64}$')
        self.assertEqual(self.snapshot(), before, 'nothing is written')
        self.assertEqual(self.iteration_dirs(), ['1'], 'nothing is frozen')
        self.assertEqual((len(self.ops('iteration')), self.ops('restore')), (1, []), 'no operation is logged')
        self.write_page(page(S2_V2))
        control = self.restore()  # control: a screen that differs is restored
        self.assertEqual((control['result'], control['frozen']), ('restored', 2))

    def test_dry_run_reports_whether_the_screen_is_unchanged(self):
        self.iterate()
        before = self.snapshot()
        self.assertIs(self.restore(extra=['--dry-run'])['unchanged'], True)
        self.assertEqual(self.snapshot(), before)
        self.write_page(page(S2_V2))
        self.assertIs(self.restore(extra=['--dry-run'])['unchanged'], False, 'control: s2 differs now')


# ---------------------------------------------------------------------------
# K37: interruption after each step and recovery
# ---------------------------------------------------------------------------
class RestoreInterruptionTests(RestoreFixture):
    def setUp(self):
        super().setUp()
        self.iterate()
        self.write_page(page(S2_V2, S3_V2))

    def test_crash_after_the_freeze_before_the_journal_changes_nothing_but_the_directory(self):
        self.crash('before_journal', 'restore')
        self.assertEqual(self.iteration_dirs(), ['1', '2'])
        self.assertEqual([op['type'] for op in self.ops()], ['iteration'], 'only iteration 1 is in the journal')
        self.assertIn(S2_V2, self.html(), 'the page was not changed')
        report = self.restore()  # the same restore is repeated: the orphan is registered, then the restore runs
        self.assertEqual(report['status'], 'PASS')
        self.assertIn(S2_V1, self.html())
        self.assertEqual([op['type'] for op in self.ops()], ['iteration', 'iteration', 'iteration', 'restore'])
        self.assertEqual([op['payload']['number'] for op in self.ops('iteration')], [1, 2, 3])
        self.assertEqual([op['by'] for op in self.ops('iteration')], ['agent'] * 3)
        self.assertEqual(self.iteration_dirs(), ['1', '2', '3'])
        self.assertEqual(report['frozen'], 3)

    def test_crash_before_the_page_is_written_is_finished_automatically_while_restore_is_last(self):
        self.crash('before_index', 'restore')
        self.assertEqual([op['type'] for op in self.ops()], ['iteration', 'iteration', 'restore'])
        self.assertIn(S2_V2, self.html(), 'step (4) did not happen')
        report = self.iterate()  # recovery runs before any iterate/restore
        self.assertIn(S2_V1, self.html(), 'step (4) was repeated')
        self.assertNotIn(S2_V2, self.html())
        self.assertEqual(report['iteration'], 3)
        self.assertEqual([op['type'] for op in self.ops()], ['iteration', 'iteration', 'restore', 'iteration'])

    def test_resume_redoes_the_page_write_and_then_reports_complete(self):
        self.crash('before_index', 'restore')
        redo = self.proto('restore', 'demo', '--resume')
        self.assertEqual(redo['resume'], 'redo')
        self.assertIsNotNone(redo['redone'])
        self.assertIn(S2_V1, self.html())
        after = self.html()
        again = self.proto('restore', 'demo', '--resume')
        self.assertEqual(again['resume'], 'complete')
        self.assertIsNone(again['redone'])
        self.assertEqual(self.html(), after)

    def test_restore_that_is_not_the_last_operation_is_not_redone_automatically(self):
        self.restore()
        restored = self.html()
        self.write_page(restored.replace(S2_V1, S2_V2))   # like `git checkout -- index.html`
        self.thread('th-x', 'm-x', 's3', 's3-h')          # the restore is no longer the last operation
        self.iterate()
        self.assertIn(S2_V2, self.html(), 'recovery must not repeat a restore that someone undid')
        self.assertNotIn(S2_V1, self.html())

    def test_resume_redoes_an_undone_restore_that_is_not_last_only_when_asked(self):
        self.restore()
        self.write_page(self.html().replace(S2_V1, S2_V2))
        self.thread('th-x', 'm-x', 's3', 's3-h')
        redo = self.proto('restore', 'demo', '--resume')
        self.assertEqual(redo['resume'], 'redo')
        self.assertIn(S2_V1, self.html())
        self.assertEqual(self.proto('restore', 'demo', '--resume')['resume'], 'complete')

    def test_a_later_edit_of_the_screen_is_never_overwritten(self):
        self.restore()
        edited = self.html().replace('>A</button>', '>A2</button>')
        self.write_page(edited)
        report = self.proto('restore', 'demo', '--resume')
        self.assertEqual(report['resume'], 'changed')
        self.assertEqual(self.html(), edited)
        self.iterate()
        self.assertEqual(self.html(), edited)

    def test_resume_without_any_restore_is_a_noop(self):
        before = self.snapshot()
        report = self.proto('restore', 'demo', '--resume')
        self.assertEqual(report['resume'], 'none')
        self.assertEqual(self.snapshot(), before)

    def test_resume_cannot_be_combined_with_dry_run(self):
        result = subprocess.run([sys.executable, str(CLI), 'prototype', 'restore', str(self.root), 'demo', '--resume',
                                 '--dry-run', '--json'], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Traceback', result.stderr)


class RestoreMarkerTests(RestoreFixture):
    """F06: the marker `.restore-pending.tmp` of an unfinished restore.  Real order of a restore: marker → (2) freeze
    → (3) journal → (4) index.html → marker removed; a crash at every point, then recovery (before iterate)."""

    MARKER = '.restore-pending.tmp'

    def setUp(self):
        super().setUp()
        self.iterate()
        self.write_page(page(S2_V2, S3_V2))

    def marker(self):
        return (self.dir / self.MARKER).exists()

    def warnings(self, report):
        return ' | '.join(report.get('warnings', []))

    def test_crash_before_the_marker_leaves_nothing_to_recover(self):
        self.crash('before_marker', 'restore')
        self.assertFalse(self.marker())
        self.assertEqual((self.iteration_dirs(), len(self.ops())), (['1'], 1))
        self.assertIn(S2_V2, self.html())
        report = self.restore()  # control: the same restore now runs normally
        self.assertEqual((report['result'], report['frozen']), ('restored', 2))
        self.assertNotIn('recovery', self.warnings(report))

    def test_crash_after_the_marker_before_the_freeze_is_reported_and_nothing_is_restored(self):
        self.crash('before_freeze', 'restore')
        self.assertTrue(self.marker(), 'the marker is written before the first step')
        self.assertEqual(self.iteration_dirs(), ['1'])
        report = self.iterate()
        self.assertIn('never reached the log', self.warnings(report))
        self.assertFalse(self.marker(), 'the stale marker is removed')
        self.assertIn(S2_V2, self.html(), 'nothing is restored from a stale marker')
        self.assertEqual((report['iteration'], self.ops('restore')), (2, []))

    def test_crash_between_the_freeze_and_the_journal_registers_the_freeze_and_reports_the_marker(self):
        self.crash('before_journal', 'restore')
        self.assertTrue(self.marker())
        report = self.iterate()
        text = self.warnings(report)
        self.assertIn('registered the interrupted freeze iterations/2/', text)
        self.assertIn('never reached the log', text)
        self.assertTrue(report['recovered'])
        self.assertFalse(self.marker())
        self.assertIn(S2_V2, self.html())

    def test_crash_between_the_journal_and_the_page_is_finished_and_said_so(self):
        self.crash('before_index', 'restore')
        self.assertTrue(self.marker())
        report = self.iterate()
        self.assertIn('finished the interrupted restore of s2', self.warnings(report))
        self.assertIn(S2_V1, self.html())
        self.assertFalse(self.marker())

    def test_crash_after_the_page_before_the_marker_is_removed_only_removes_the_marker(self):
        self.crash('before_unmark', 'restore')
        self.assertTrue(self.marker())
        self.assertIn(S2_V1, self.html(), 'step (4) was done')
        before = self.html()
        report = self.iterate()
        self.assertFalse(self.marker())
        self.assertEqual(self.html(), before)
        self.assertNotIn('finished the interrupted restore', self.warnings(report))
        self.assertNotIn('never reached the log', self.warnings(report))

    def test_undoing_a_restore_by_hand_is_never_reverted_by_iterate_or_restore(self):
        self.restore()
        self.assertFalse(self.marker(), 'a finished restore leaves no marker')
        self.write_page(self.html().replace(S2_V1, S2_V2))   # like `git checkout -- index.html`; restore stays last
        report = self.iterate()
        self.assertIn(S2_V2, self.html(), 'iterate must not repeat a finished restore')
        self.assertNotIn('finished the interrupted restore', self.warnings(report))
        other = self.restore(screen='s3', source=1)
        self.assertEqual(other['result'], 'restored')
        self.assertIn(S2_V2, self.html(), 'a restore of another screen must not revert s2')
        self.assertIn(S3_V1, self.html())

    def test_the_marker_is_ignored_by_git_through_the_prototype_gitignore(self):
        subprocess.run(['git', 'init', '-q', str(self.base)], check=True)
        (self.dir / self.MARKER).write_text('{}', encoding='utf-8')
        ignored = subprocess.run(['git', '-C', str(self.base), 'check-ignore', '-q', str(self.dir / self.MARKER)])
        self.assertEqual(ignored.returncode, 0, 'the `*.tmp` line of the prototype .gitignore covers the marker')
        control = subprocess.run(['git', '-C', str(self.base), 'check-ignore', '-q', str(self.dir / 'index.html')])
        self.assertEqual(control.returncode, 1, 'control: index.html is not ignored')


class ReviewFixesTests(RestoreFixture):
    """Fixes after code-review-2.6-srv-1 (S1-1 … S1-6)."""

    MARKER = '.restore-pending.tmp'

    def setUp(self):
        super().setUp()
        self.iterate()
        self.write_page(page(S2_V2, S3_V2))

    def marker(self):
        return (self.dir / self.MARKER).exists()

    def test_s1_1_a_page_changed_by_the_commit_hook_is_never_dropped(self):
        edited = page(S2_V2, S3_V2.replace('Inny zmieniony', 'Zmiana z hooka'))

        def hook(number):
            self.write_page(edited)   # like a formatter or autosave run by a pre-commit hook
            return None

        with self.assertRaises(ps.PrototypeServerError) as caught:
            ps.perform_restore(self.store(), str(self.dir), 's2', 1, 'powód', by='agent', who=ACCEPTOR,
                               before_freeze=hook)
        self.assertEqual(caught.exception.code, 'stale_view')
        self.assertEqual(self.html(), edited, 'the change of the hook stays')
        self.assertEqual(self.iteration_dirs(), ['1'], 'the freeze of the refused restore is removed')
        self.assertEqual(self.ops('restore'), [])
        self.assertFalse(self.marker())
        result = ps.perform_restore(self.store(), str(self.dir), 's2', 1, 'powód', by='agent', who=ACCEPTOR,
                                    before_freeze=lambda number: None)   # control: a hook that changes nothing
        self.assertEqual(result['current'], 3)
        self.assertIn('Zmiana z hooka', self.html())
        self.assertIn(S2_V1, self.html())

    def test_s2_1_a_css_change_by_the_hook_is_never_dropped(self):
        css = self.dir / 'screens.css'
        changed = css.read_text(encoding='utf-8') + '\n.hook { color: red }\n'

        def hook(number):
            css.write_text(changed, encoding='utf-8')   # index.html unchanged: only the hashed files differ
            return None

        with self.assertRaises(ps.PrototypeServerError) as caught:
            ps.perform_restore(self.store(), str(self.dir), 's2', 1, 'powód', by='agent', who=ACCEPTOR,
                               before_freeze=hook)
        self.assertEqual(caught.exception.code, 'stale_view')
        self.assertEqual(css.read_text(encoding='utf-8'), changed)
        self.assertIn(S2_V2, self.html())
        self.assertEqual((self.iteration_dirs(), self.ops('restore'), self.marker()), (['1'], [], False))
        self.assertNotIn('leftover', caught.exception.details)

    def test_s2_5_a_freeze_that_cannot_be_removed_is_named(self):
        def hook(number):
            self.write_page(page(S2_V2, S3_V1))
            return None

        with mock.patch.object(ps.shutil, 'rmtree', lambda *args, **kwargs: None):
            with self.assertRaises(ps.PrototypeServerError) as caught:
                ps.perform_restore(self.store(), str(self.dir), 's2', 1, 'powód', by='agent', who=ACCEPTOR,
                                   before_freeze=hook)
        self.assertEqual(caught.exception.details.get('leftover'), 'iterations/2/')
        self.assertIn('iterations/2/ could not be removed', caught.exception.message)

    def test_s2_1_recovery_is_in_the_fail_report_of_iterate(self):
        (self.dir / 'iterations' / '2.tmp-ab12').mkdir()
        self.write_page(page(S2_V2, S3_V2.replace('</h2>', '</h2><button type="button">bez id</button>')))
        report = self.iterate(code=1)
        self.assertEqual(report['status'], 'FAIL')
        self.assertTrue(any('removed the unfinished freeze iterations/2.tmp-ab12/' in note
                            for note in report.get('recovery', [])), report)
        self.assertNotIn('recovery', self.iterate(code=1), 'control: nothing recovered the second time')

    @unittest.skipIf(os.name == 'nt' or (hasattr(os, 'geteuid') and os.geteuid() == 0), 'POSIX modes, not root')
    def test_s2_2_ids_fix_without_changes_takes_no_lock(self):
        lock = self.dir / 'comments.json.lock'
        if lock.exists():
            lock.unlink()
        mode = self.dir.stat().st_mode
        os.chmod(self.dir, 0o555)
        try:
            report = self.proto('ids', 'demo', '--fix')
            self.assertEqual(report['added'], [])
            self.assertFalse(lock.exists(), 'no lock file without changes')
        finally:
            os.chmod(self.dir, mode)
        self.write_page(page(S2_V2, S3_V2.replace('</h2>', '</h2><button type="button">x</button>')))
        self.assertTrue(self.proto('ids', 'demo', '--fix')['added'])   # control: a change takes the lock and writes
        self.assertTrue(lock.exists())

    def test_s1_2_recovery_notes_are_reported_when_iterate_or_restore_fails_afterwards(self):
        self.crash('before_index', 'restore')
        tiny = ps.CommentsStore(str(self.dir), 'demo', max_bytes=10)   # 507 right after recovery
        with self.assertRaises(ps.FileTooLarge) as caught:
            ps.perform_iterate(tiny, str(self.dir))
        notes = caught.exception.details.get('recovery', [])
        self.assertTrue(any('finished the interrupted restore of s2' in note for note in notes), notes)
        self.assertEqual(getattr(caught.exception, 'recovery_notes', None), notes)
        self.assertIn(S2_V1, self.html())
        self.write_page(page(S2_V2, S3_V2))
        self.crash('before_index', 'restore')
        refused = self.restore(source=99, code=2)
        self.assertEqual(refused['error'], 'iteration_not_found')
        self.assertTrue(any('finished the interrupted restore' in note for note in refused.get('recovery', [])), refused)
        plain = self.restore(source=98, code=2)   # control: nothing recovered, no `recovery`
        self.assertNotIn('recovery', plain)

    def test_s1_3_an_interrupt_after_the_journal_keeps_the_marker_for_recovery(self):
        store = self.store()
        real = store.append_locked

        def interrupted(partials, create=True):
            result = real(partials, create)
            raise KeyboardInterrupt()   # Ctrl+C right after os.replace of comments.json

        store.append_locked = interrupted
        with self.assertRaises(KeyboardInterrupt):
            ps.perform_restore(store, str(self.dir), 's2', 1, 'powód', by='agent', who=ACCEPTOR)
        self.assertTrue(self.marker(), 'step (3) is in the log: the marker must stay')
        self.assertIn(S2_V2, self.html())
        report = self.iterate()
        self.assertIn(S2_V1, self.html(), 'recovery finished step (4)')
        self.assertTrue(any('finished the interrupted restore' in item for item in report.get('warnings', [])))

    def test_s1_3_control_an_error_before_the_journal_removes_the_marker(self):
        store = self.store()

        def failing(partials, create=True):
            raise ps.FileTooLarge(1)

        store.append_locked = failing
        with self.assertRaises(ps.FileTooLarge):
            ps.perform_restore(store, str(self.dir), 's2', 1, 'powód', by='agent', who=ACCEPTOR)
        self.assertFalse(self.marker())

    def test_s1_4_a_restore_that_cannot_be_finished_names_the_marker(self):
        self.crash('before_index', 'restore')
        frozen = self.dir / 'iterations' / '1' / 'index.html'
        frozen.write_text(frozen.read_text(encoding='utf-8').replace('Tydzień', 'Tydzień inny'), encoding='utf-8')
        report = self.iterate(code=2)
        self.assertEqual(report['error'], 'incomplete_restore')
        self.assertIn(self.MARKER, report['reason'])
        self.assertEqual(report.get('marker'), self.MARKER)
        (self.dir / self.MARKER).unlink()   # the way out named in the message
        self.assertEqual(self.iterate()['status'], 'PASS')

    def test_s1_5_ids_fix_waits_for_the_prototype_lock(self):
        self.write_page(page(S2_V2, S3_V2.replace('</h2>', '</h2><button type="button">x</button>')))
        before = self.html()
        with self.store().lock():
            report = self.proto('ids', 'demo', '--fix', code=2)
            self.assertIn('lock', report['reason'])
            self.assertEqual(self.html(), before, 'nothing is written while a restore holds the lock')
        self.assertTrue(self.proto('ids', 'demo', '--fix')['added'])  # control: free lock

    def test_s1_6_preview_retries_once_under_the_lock(self):
        store = self.store()
        real_lock, real_actions = store.lock, ps.recovery_actions
        state = {'calls': 0, 'locked': False}

        def lock():
            state['locked'] = True
            return real_lock()

        def actions(*args, **kwargs):
            state['calls'] += 1
            if state['calls'] == 1:
                raise ps.PrototypeServerError(409, 'incomplete_restore', 'a restore runs in parallel')
            return real_actions(*args, **kwargs)

        store.lock = lock
        ps.recovery_actions = actions
        try:
            preview = ps.preview_restore(store, str(self.dir), 's2', 1)
        finally:
            ps.recovery_actions = real_actions
        self.assertEqual((preview['threadsBack'], state['calls'], state['locked']), (0, 2, True))
        ps.recovery_actions = lambda *a, **k: (_ for _ in ()).throw(
            ps.PrototypeServerError(409, 'incomplete_restore', 'lasting'))
        try:
            with self.assertRaises(ps.PrototypeServerError):   # control: a lasting state keeps the 409
                ps.preview_restore(self.store(), str(self.dir), 's2', 1)
        finally:
            ps.recovery_actions = real_actions


# ---------------------------------------------------------------------------
# K40: commit before the iteration (temporary git repository)
# ---------------------------------------------------------------------------
class CommitBeforeIterationTests(RestoreFixture):
    """The git repository is `self.base`; the project (`self.root`) is its subdirectory."""

    def setUp(self):
        super().setUp()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test Autor')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'commit.gpgsign', 'false')
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'init')
        self.prefix = 'project/docs/prototypes/demo/'
        self.write_page(page(S2_V1, S3_V2))  # an uncommitted change: K40 commits only when something changed

    def git(self, *args, check=True, env=None):
        environment = dict(os.environ)
        environment.update(env or {})
        result = subprocess.run(['git', '-C', str(self.base), *args], capture_output=True, text=True, timeout=60,
                                env=environment)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def flag(self, value=True):
        self.set_prototype({'commit_before_iteration': value})

    def head(self):
        return self.git('rev-parse', 'HEAD')

    def last_files(self):
        return self.git('show', '--name-only', '--format=', 'HEAD').split()

    def test_only_the_prototype_directory_is_committed_even_with_other_staged_files(self):
        self.flag()
        (self.base / 'other.txt').write_text('x', encoding='utf-8')
        self.git('add', 'other.txt')
        before = self.head()
        report = self.iterate()
        self.assertEqual(report['commit'], self.head())
        self.assertNotEqual(self.head(), before)
        files = self.last_files()
        self.assertTrue(files, 'the commit has files')
        self.assertTrue(all(name.startswith(self.prefix) for name in files), files)
        self.assertNotIn(self.prefix + 'comments.json.lock', files, 'the lock file is ignored')
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'other.txt', 'the other staged file stays staged')
        self.assertEqual(self.git('log', '-1', '--format=%s'), 'prototyp demo: stan przed iteracją 1')
        self.assertEqual(self.ops('iteration')[0]['payload']['commit'], report['commit'])
        self.assertEqual(self.git('log', '-1', '--format=%an'), 'Test Autor')
        self.assertEqual(self.git('remote'), '', 'no push target exists and none is used')

    def test_the_next_commit_picks_up_the_previous_freeze_and_the_journal(self):
        self.flag()
        self.iterate()
        self.write_page(page(S2_V2))
        self.iterate()
        files = self.last_files()
        self.assertIn(self.prefix + 'iterations/1/index.html', files)
        self.assertIn(self.prefix + 'comments.json', files)
        self.assertEqual(self.git('log', '-1', '--format=%s'), 'prototyp demo: stan przed iteracją 2')

    def test_no_changes_means_no_commit(self):
        self.flag()
        self.iterate()
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'ręcznie')
        head = self.head()
        report = self.iterate()
        self.assertIsNone(report['commit'])
        self.assertEqual(self.head(), head)
        self.assertEqual(report['iteration'], 2, 'the freeze still happens')

    def test_flag_off_or_missing_means_no_commit(self):
        head = self.head()
        self.assertIsNone(self.iterate()['commit'], 'no key')
        self.flag(False)
        self.assertIsNone(self.iterate()['commit'], 'false')
        self.assertEqual(self.head(), head)
        self.flag(True)  # control: the same call commits when the flag is on
        self.assertTrue(self.iterate()['commit'])

    def test_outside_a_git_working_copy_is_a_warning_without_a_commit(self):
        # Background `git maintenance` may delete its own lock mid-walk (seen on macOS): retry the rest.
        for _ in range(5):
            try:
                shutil.rmtree(self.base / '.git')
                break
            except FileNotFoundError:
                continue
        self.assertFalse((self.base / '.git').exists())
        self.flag()
        report = self.iterate(env={'GIT_CEILING_DIRECTORIES': str(self.base)})
        self.assertIsNone(report['commit'])
        self.assertTrue(any('not in a git working copy' in warning for warning in report['warnings']), report)
        self.assertEqual(report['iteration'], 1)

    def test_merge_or_rebase_in_progress_blocks_before_the_freeze(self):
        self.flag()
        head = self.head()
        gitdir = self.base / '.git'
        for marker, is_dir in (('MERGE_HEAD', False), ('rebase-merge', True), ('CHERRY_PICK_HEAD', False)):
            with self.subTest(marker=marker):
                target = gitdir / marker
                target.mkdir() if is_dir else target.write_text(head + '\n', encoding='utf-8')
                before = self.snapshot()
                report = self.iterate(code=2)
                self.assertIn(marker, report['reason'])
                self.assertEqual(self.snapshot(), before, 'nothing frozen, nothing written')
                self.assertEqual(self.head(), head)
                shutil.rmtree(target) if is_dir else target.unlink()
        self.assertTrue(self.iterate()['commit'])  # control: markers removed

    def test_s2_1_a_blocked_commit_after_recovery_reports_the_recovery(self):
        self.flag()
        (self.dir / 'iterations').mkdir(exist_ok=True)
        (self.dir / 'iterations' / '1.tmp-ab12').mkdir()
        merge = self.base / '.git' / 'MERGE_HEAD'
        merge.write_text(self.head() + '\n', encoding='utf-8')
        report = self.iterate(code=2)
        self.assertIn('MERGE_HEAD', report['reason'])
        self.assertTrue(any('removed the unfinished freeze iterations/1.tmp-ab12/' in note
                            for note in report.get('recovery', [])), report)
        merge.unlink()
        self.assertNotIn('recovery', self.iterate(), 'control: nothing left to recover')

    def test_unresolved_conflict_blocks_before_the_freeze(self):
        self.flag()
        (self.base / 'c.txt').write_text('a\n', encoding='utf-8')
        self.git('add', 'c.txt')
        self.git('commit', '-q', '-m', 'c', '--', 'c.txt')
        main = self.git('rev-parse', '--abbrev-ref', 'HEAD')
        self.git('checkout', '-q', '-b', 'side')
        (self.base / 'c.txt').write_text('side\n', encoding='utf-8')
        self.git('commit', '-q', '-m', 'side', '--', 'c.txt')
        self.git('checkout', '-q', main)
        (self.base / 'c.txt').write_text('main\n', encoding='utf-8')
        self.git('commit', '-q', '-m', 'main', '--', 'c.txt')
        self.git('merge', 'side', check=False)
        self.assertIn('c.txt', self.git('diff', '--name-only', '--diff-filter=U'))
        before = self.snapshot()
        report = self.iterate(code=2)
        self.assertTrue(report['reason'])
        self.assertEqual(self.snapshot(), before)
        self.git('merge', '--abort')
        self.assertTrue(self.iterate()['commit'])  # control

    def test_failing_hook_blocks_the_iteration_without_a_freeze(self):
        self.flag()
        hook = self.base / '.git' / 'hooks' / 'pre-commit'
        hook.write_text('#!/bin/sh\necho hook says no >&2\nexit 1\n', encoding='utf-8')
        hook.chmod(0o755)
        before = self.snapshot()
        report = self.iterate(code=2)
        self.assertIn('hook says no', report['reason'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.iteration_dirs(), [])
        hook.unlink()
        self.assertTrue(self.iterate()['commit'])  # control

    def test_missing_git_identity_blocks_the_iteration_without_a_freeze(self):
        self.flag()
        self.git('config', '--unset', 'user.name')
        self.git('config', '--unset', 'user.email')
        self.git('config', 'user.useConfigOnly', 'true')
        isolated = {'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1', 'HOME': str(self.base), 'EMAIL': ''}
        before = self.snapshot()
        report = self.iterate(code=2, env=isolated)
        self.assertIn('git commit failed', report['reason'])
        self.assertEqual(self.snapshot(), before)
        self.git('config', 'user.name', 'T')
        self.git('config', 'user.email', 't@example.com')
        self.assertTrue(self.iterate(env=isolated)['commit'])  # control

    def test_non_boolean_flag_is_blocked(self):
        for value in ('yes', 1, None):
            with self.subTest(value=value):
                self.set_prototype({'commit_before_iteration': value})
                report = self.iterate(code=2)
                self.assertIn('prototype', report['reason'])
                self.assertTrue(any('commit_before_iteration' in error for error in report['errors']), report)
                self.assertEqual(self.iteration_dirs(), [])
        self.flag(True)
        self.assertEqual(self.iterate()['iteration'], 1)  # control

    def test_restore_commits_with_the_number_of_the_frozen_iteration(self):
        self.flag()
        self.iterate()
        self.write_page(page(S2_V2))
        report = self.restore()
        self.assertEqual(report['commit'], self.head())
        self.assertEqual(self.git('log', '-1', '--format=%s'), 'prototyp demo: stan przed iteracją %d' % report['frozen'])
        self.assertEqual(self.ops('iteration')[-1]['payload']['commit'], report['commit'])

    def test_an_unchanged_restore_never_commits(self):
        """D27 with K40: `unchanged` ends before the commit, so the uncommitted freeze 1 stays uncommitted."""
        self.flag()
        self.iterate()
        head = self.head()
        report = self.restore()
        self.assertEqual(report['result'], 'unchanged')
        self.assertEqual(self.head(), head)
        self.assertEqual(self.iteration_dirs(), ['1'])
        self.write_page(page(S2_V2, S3_V2))
        control = self.restore()  # control: a real restore commits
        self.assertEqual(control['commit'], self.head())
        self.assertNotEqual(self.head(), head)

    def test_dry_run_and_resume_never_commit(self):
        self.flag()
        self.iterate()
        self.write_page(page(S2_V2))
        head = self.head()
        self.restore(extra=['--dry-run'])
        self.proto('restore', 'demo', '--resume')
        self.assertEqual(self.head(), head)
        self.assertIsNotNone(self.restore()['commit'], 'control: the real restore commits')


# ---------------------------------------------------------------------------
# K35: agent CLI (comments, triage, decide, resolve)
# ---------------------------------------------------------------------------
class AgentCommandsTests(RestoreFixture):
    def setUp(self):
        super().setUp()
        self.thread('th-a', 'm-a', 's2', 's2-btn-a')
        self.thread('th-b', 'm-b', 's2', 's2-btn-a')
        self.store().append([{'type': 'vote', 'threadId': 'th-a', 'by': COMMENTER,
                              'payload': {'messageId': 'm-a', 'value': 'down'}}])

    def test_s1_9_a_thread_number_must_be_ascii_digits(self):
        before = (self.dir / 'comments.json').read_bytes()
        report = self.proto('triage', 'demo', '\u0661', '--class', 'clear', code=2)  # ARABIC-INDIC DIGIT ONE
        self.assertEqual(report.get('error'), 'thread_not_found', report)
        self.assertEqual((self.dir / 'comments.json').read_bytes(), before)
        self.assertEqual(self.proto('triage', 'demo', '1', '--class', 'clear')['threadId'], 'th-a')  # control

    def refused_without_write(self, *args, **kwargs):
        before = (self.dir / 'comments.json').read_bytes()
        report = self.proto(*args, code=2, **kwargs)
        self.assertEqual((self.dir / 'comments.json').read_bytes(), before)
        return report

    def test_comments_returns_thread_ids_numbers_and_votes_with_names(self):
        report = self.proto('comments', 'demo')
        threads = {item['threadId']: item for item in report['threads']}
        self.assertEqual(set(threads), {'th-a', 'th-b'})
        self.assertEqual(sorted(item['number'] for item in threads.values()), [1, 2])
        self.assertEqual(threads['th-a']['messages'][0]['votes']['down'], [{'login': COMMENTER, 'name': 'Kom'}])

    def test_threads_are_addressed_by_id_or_by_number(self):
        by_id = self.proto('triage', 'demo', 'th-a', '--class', 'clear')
        self.assertEqual((by_id['threadId'], by_id['number']), ('th-a', 1))
        by_number = self.proto('triage', 'demo', '2', '--class', 'disputed', '--note', 'n')
        self.assertEqual((by_number['threadId'], by_number['threadStatus']), ('th-b', 'awaiting'))
        self.assertEqual([op['type'] for op in self.ops()[-2:]], ['triage', 'triage'])
        self.assertEqual({op['by'] for op in self.ops('triage')}, {'agent'})

    def test_missing_or_deleted_threads_are_blocked_without_a_write(self):
        for reference in ('th-nie-ma', '99'):
            with self.subTest(reference=reference):
                self.refused_without_write('resolve', 'demo', reference, '--reason', 'done')
        self.store().append([{'type': 'delete', 'threadId': 'th-b', 'by': DEVELOPER, 'payload': {}}])
        report = self.refused_without_write('triage', 'demo', 'th-b', '--class', 'clear')
        self.assertEqual(report['state'], 'deleted')
        self.assertEqual(self.proto('resolve', 'demo', 'th-a', '--reason', 'done')['threadStatus'], 'closed')  # control

    def test_a_human_decision_needs_a_developer_or_acceptor_account(self):
        refused = self.refused_without_write('decide', 'demo', 'th-a', '--kind', 'developer', '--who', COMMENTER,
                                             '--outcome', 'apply', '--text', 't')
        self.assertIn('developer or acceptor', refused['reason'])
        for who, thread in ((DEVELOPER, 'th-a'), (ACCEPTOR, 'th-b')):
            with self.subTest(who=who):
                done = self.proto('decide', 'demo', thread, '--kind', 'developer', '--who', who, '--outcome', 'apply',
                                  '--text', 't')
                self.assertEqual(done['threadStatus'], 'decided')

    def test_triage_on_a_decided_thread_is_blocked(self):
        self.proto('decide', 'demo', 'th-a', '--kind', 'agent', '--outcome', 'apply', '--text', 't')
        report = self.refused_without_write('triage', 'demo', 'th-a', '--class', 'clear')
        self.assertEqual(report['error'], 'thread_decided')


# ---------------------------------------------------------------------------
# K29: merge-comments
# ---------------------------------------------------------------------------
def op(number, kind='create', thread=None, at=None, payload=None, by=ACCEPTOR):
    result = {'id': 'op-%026d' % number, 'type': kind, 'at': at or '2026-11-05T09:00:%02d.000Z' % number, 'by': by}
    if thread:
        result['threadId'] = thread
    result['payload'] = payload if payload is not None else {}
    return result


def create_op(number, thread, text='uwaga'):
    return op(number, 'create', thread, payload={
        'message': {'id': 'm-%d' % number, 'text': text}, 'screen': 's2',
        'anchor': {'kind': 'screen', 'x': 0.5, 'y': 0.5, 'iteration': 1}})


def reply_op(number, thread, text='odpowiedź'):
    return op(number, 'reply', thread, payload={'message': {'id': 'm-%d' % number, 'text': text}})


def iteration_op(number, digit, at, ident=None):
    return op(80 + (ident or number), 'iteration', at=at, by='agent',
              payload={'number': number, 'path': 'iterations/%d/' % number, 'hash': digit * 64})


class AgentReplyAndWaitTests(RestoreFixture):
    """`prototype reply` (wpis agenta w wątku) i `prototype wait` (komentarz osoby albo akceptacja wszystkich ekranów)."""
    def setUp(self):
        super().setUp()
        self.thread('th-a', 'm-a', 's2', 's2-btn-a')

    def run_wait(self, *args, timeout='0'):
        argv = [sys.executable, str(CLI), 'prototype', 'wait', str(self.root), 'demo', '--timeout', timeout,
                '--interval', '0.2', '--json', *args]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=self.base)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def accept_all(self):
        hashes = ps.screen_hashes(str(self.dir))['screens']
        self.store().append([{'type': 'accept', 'by': ACCEPTOR,
                              'payload': {'scope': 'screen', 'screens': hashes, 'iteration': 1, 'via': 'ui'}}])

    def test_reply_is_an_agent_message_in_the_thread(self):
        report = self.proto('reply', 'demo', '1', '--text', 'Czy chodzi o (a) kolor, (b) rozmiar? Rekomendacja: a.')
        self.assertEqual((report['threadId'], report['operation']['type'], report['operation']['by']), ('th-a', 'reply', 'agent'))
        thread = self.store().snapshot().fold['byId']['th-a']
        self.assertEqual([(m['by'], m['text'][:8]) for m in thread['messages']], [(ACCEPTOR, 'uwaga th'), ('agent', 'Czy chod')])
        self.assertRegex(thread['messages'][1]['id'], r'^m-agent-')
        before = (self.dir / 'comments.json').read_bytes()
        for args in (('99', '--text', 'x'), ('1', '--text', '   ')):
            with self.subTest(args=args):
                self.proto('reply', 'demo', *args, code=2)
        self.assertEqual((self.dir / 'comments.json').read_bytes(), before)

    def test_wait_reports_a_new_comment_by_a_person_but_not_by_the_agent(self):
        code, report = self.run_wait()
        self.assertEqual((code, report['status']), (3, 'TIMEOUT'))  # wątek sprzed startu się nie liczy
        last = self.ops()[-1]['id']
        self.proto('reply', 'demo', '1', '--text', 'odpowiedź agenta')
        code, report = self.run_wait('--after', last)
        self.assertEqual(code, 3, report)  # wpis agenta nie budzi
        self.store().append([{'type': 'reply', 'threadId': 'th-a', 'by': COMMENTER,
                              'payload': {'message': {'id': 'm-odp', 'text': 'Chodzi o kolor.'}}}])
        code, report = self.run_wait('--after', last)
        self.assertEqual((code, report['event']), (0, 'comment'), report)
        self.assertEqual({k: report['comment'][k] for k in ('threadId', 'number', 'screen', 'author', 'text')},
                         {'threadId': 'th-a', 'number': 1, 'screen': 's2', 'author': COMMENTER, 'text': 'Chodzi o kolor.'})
        after = report['comment']['opId']
        code, report = self.run_wait('--after', after)
        self.assertEqual(code, 3, report)

    def test_wait_ends_when_every_screen_is_accepted(self):
        code, report = self.run_wait()
        self.assertEqual(code, 3)
        self.assertTrue(report['missing'])
        self.accept_all()
        code, report = self.run_wait()
        self.assertEqual((code, report['event'], report['gate']['status']), (0, 'accepted', 'PASS'), report)

    def test_wait_wakes_up_during_the_wait(self):
        writer = threading.Timer(1.0, lambda: self.store().append([
            {'type': 'create', 'threadId': 'th-n', 'by': COMMENTER,
             'payload': {'message': {'id': 'm-n', 'text': 'nowa uwaga'}, 'screen': 's3',
                         'anchor': {'kind': 'screen', 'x': 0.1, 'y': 0.1, 'iteration': 1}}}]))
        writer.start()
        try:
            code, report = self.run_wait(timeout='20')
        finally:
            writer.cancel()
        self.assertEqual((code, report['comment']['threadId'], report['comment']['type']), (0, 'th-n', 'create'), report)

    def comment_by(self, who, thread_id, text='uwaga'):
        self.store().append([{'type': 'create', 'threadId': thread_id, 'by': who,
                              'payload': {'message': {'id': 'm-' + thread_id, 'text': text}, 'screen': 's3',
                                          'anchor': {'kind': 'screen', 'x': 0.1, 'y': 0.1, 'iteration': 1}}}])

    def test_wait_reports_author_role_decision_and_reaction(self):
        last = self.ops()[-1]['id']  # punkt odniesienia z dziennika, nie z zegara (ta sama milisekunda = niestabilny test)
        self.comment_by(COMMENTER, 'th-k', 'propozycja')
        code, report = self.run_wait('--after', last)
        c = report['comment']
        self.assertEqual((c['authorRole'], c['decision'], c['reaction']), ('commenter', None, 'reply'), report)
        self.comment_by(DEVELOPER, 'th-d', 'zmień kolor na zielony')
        code, report = self.run_wait('--after', c['opId'])
        self.assertEqual((report['comment']['authorRole'], report['comment']['reaction']), ('developer', 'order?'))
        # decyzja „Wprowadź” akceptującego zmienia propozycję komentującego w realizację
        self.proto('decide', 'demo', 'th-k', '--kind', 'developer', '--who', ACCEPTOR, '--outcome', 'apply', '--text', 'tak')
        self.store().append([{'type': 'reply', 'threadId': 'th-k', 'by': COMMENTER,
                              'payload': {'message': {'id': 'm-k2', 'text': 'dzięki'}}}])
        code, report = self.run_wait('--after', report['comment']['opId'])
        c = report['comment']
        self.assertEqual((c['threadId'], c['reaction'], c['decision']['outcome'], c['decision']['whoRole']),
                         ('th-k', 'implement', 'apply', 'acceptor'), report)
        # „Wariant” albo decyzja agenta = tylko odpowiedź
        self.proto('decide', 'demo', 'th-d', '--kind', 'agent', '--outcome', 'apply', '--text', 'agent')
        self.store().append([{'type': 'reply', 'threadId': 'th-d', 'by': COMMENTER,
                              'payload': {'message': {'id': 'm-d2', 'text': '?'}}}])
        code, report = self.run_wait('--after', c['opId'])
        self.assertEqual(report['comment']['reaction'], 'reply')

    def test_wait_rejects_since_and_after_together_and_bad_times(self):
        self.assertEqual(self.run_wait('--since', '2026-01-01T00:00:00Z', '--after', 'x')[0], 2)
        self.assertEqual(self.run_wait('--since', 'wczoraj')[0], 2)


class EngineAssetsAndAcceptanceTests(RestoreFixture):
    """Otoczka silnika (`prototype.css`) nie wchodzi do hasha ekranu; akceptacje z dawnego wzoru zostają ważne."""

    def legacy_accept(self):
        """Akceptacja zapisana dawnym wzorem (hash z `prototype.css`), jak przed tą zmianą."""
        css = (self.dir / 'prototype.css').read_bytes()
        digest = hashlib.sha256(ps.normalize_newline_bytes(css)).hexdigest()
        old_assets = ps.assets_hash(str(self.dir), engine_digest=digest)
        info = ps.screen_hashes(str(self.dir))
        html = (self.dir / 'index.html').read_text(encoding='utf-8')
        spans = ps.section_spans(html)
        screens = {sid: ps.screen_hash(old_assets, info['head'], html[r['start']:r['end']])
                   for sid, r in spans['screens'].items() if sid in info['screens']}
        self.assertNotEqual(screens, info['screens'])  # dawny wzór naprawdę różni się od bieżącego
        self.store().append([{'type': 'accept', 'by': ACCEPTOR,
                              'payload': {'scope': 'screen', 'screens': screens, 'iteration': 1, 'via': 'ui'}}])

    def gate(self, code=0):
        return self.proto('gate', 'demo', code=code)

    def test_engine_css_change_keeps_legacy_acceptances_when_recorded(self):
        self.legacy_accept()
        self.assertEqual(self.gate()['status'], 'PASS')  # alias: bieżący prototype.css
        css = self.dir / 'prototype.css'
        old = css.read_bytes()
        from skillsync_workflow import prototype as proto
        proto._remember_engine_digest(self.dir, old)  # to robi migrate przed podmianą
        css.write_bytes(old + b'\n.anno-ai-badge { font-weight: 700; }\n')
        self.assertEqual(self.gate()['status'], 'PASS')
        # zmiana ekranu nadal unieważnia akceptację
        self.write_page(page(s2=S2_V2))
        self.assertEqual(self.gate(code=1)['status'], 'FAIL')

    def test_without_history_an_engine_change_still_counts_as_stale_for_legacy_entries(self):
        self.legacy_accept()
        css = self.dir / 'prototype.css'
        css.write_bytes(css.read_bytes() + b'\n/* inny silnik */\n')
        self.assertEqual(self.gate(code=1)['status'], 'FAIL')
        # nowa akceptacja (bieżący wzór) nie zależy od prototype.css
        hashes = ps.screen_hashes(str(self.dir))['screens']
        self.store().append([{'type': 'accept', 'by': ACCEPTOR,
                              'payload': {'scope': 'screen', 'screens': hashes, 'iteration': 1, 'via': 'ui'}}])
        css.write_bytes(css.read_bytes() + b'\n/* kolejny */\n')
        self.assertEqual(self.gate()['status'], 'PASS')

    def test_migrate_reports_no_lost_acceptance_for_an_engine_only_change(self):
        self.proto('migrate', 'demo', '--assets', str(self.assets))  # strona w bieżącym formacie (head jak w projektach)
        css = self.dir / 'prototype.css'
        css.write_bytes(css.read_bytes() + b'\n/* stary silnik */\n')
        self.legacy_accept()
        report = self.proto('migrate', 'demo', '--assets', str(self.assets))
        self.assertEqual(report['acceptance']['lost'], 0, report)
        meta = json.loads((self.dir / 'prototype.json').read_text(encoding='utf-8'))
        self.assertEqual(len(meta[ps.ENGINE_HISTORY_KEY]), 1)
        self.assertEqual(self.gate()['status'], 'PASS')


class MergeCommentsTests(RestoreFixture):
    def setUp(self):
        super().setUp()
        self.base_ops = [create_op(1, 'th-1'), create_op(2, 'th-2'), reply_op(3, 'th-1')]
        self.set_journal(self.dir / 'comments.json', self.base_ops)

    def set_journal(self, path, operations):
        Path(path).write_text(ps.serialize_document('demo', operations), encoding='utf-8')

    def other(self, operations, name='other.json', slug='demo'):
        (self.root / name).write_text(ps.serialize_document(slug, operations), encoding='utf-8')
        return name

    def merge(self, name):
        return self.sk('prototype', 'merge-comments', self.root, 'demo', name, '--json')

    def lines(self):
        text = (self.dir / 'comments.json').read_text(encoding='utf-8')
        return text.splitlines()

    def test_missing_operations_are_appended_after_the_untouched_prefix(self):
        prefix = self.lines()[:-1]  # header and the three operations, without the closing `]}` line
        extra = [reply_op(4, 'th-2', 'z innej gałęzi'), create_op(5, 'th-5')]
        name = self.other(self.base_ops[:2] + extra)
        code, report = self.merge(name)
        self.assertEqual((code, report['added'], report['total']), (0, 2, 5))
        after = self.lines()
        # K2: the lines of the old operations stay byte for byte (only the comma after the last one is added)
        self.assertEqual(after[:len(prefix) - 1], prefix[:-1])
        self.assertEqual(after[len(prefix) - 1].rstrip(','), prefix[-1].rstrip(','))
        self.assertEqual([item['id'] for item in journal(self.dir)], [item['id'] for item in self.base_ops + extra])
        code, again = self.merge(name)  # idempotent
        self.assertEqual((code, again['added']), (0, 0))

    def test_the_order_of_the_other_file_is_kept_for_the_added_operations(self):
        extra = [create_op(9, 'th-9'), create_op(5, 'th-5'), create_op(7, 'th-7')]
        self.merge(self.other(self.base_ops + extra))
        self.assertEqual([item['id'] for item in journal(self.dir)][3:], [item['id'] for item in extra])

    def test_same_id_with_other_content_is_a_conflict_without_a_write(self):
        before = (self.dir / 'comments.json').read_bytes()
        changed = dict(self.base_ops[0], at='2020-01-01T00:00:00.000Z')
        code, report = self.merge(self.other([changed, create_op(5, 'th-5')]))
        self.assertEqual(code, 2)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertEqual(report['error'], 'merge_conflict')
        self.assertEqual([item['id'] for item in report['conflicts']], [self.base_ops[0]['id']])
        self.assertEqual((self.dir / 'comments.json').read_bytes(), before)
        code, ok = self.merge(self.other([self.base_ops[0], create_op(5, 'th-5')]))  # control: same id and content
        self.assertEqual((code, ok['added']), (0, 1))

    def test_a_new_iteration_with_a_number_and_another_hash_is_a_conflict(self):
        self.set_journal(self.dir / 'comments.json', self.base_ops + [iteration_op(1, 'a', '2026-11-05T10:00:00.000Z')])
        before = (self.dir / 'comments.json').read_bytes()
        other = self.other([iteration_op(1, 'b', '2026-11-05T10:00:01.000Z', ident=2)])
        code, report = self.merge(other)
        self.assertEqual(code, 2)
        self.assertIn('iteration 1', json.dumps(report))
        self.assertEqual((self.dir / 'comments.json').read_bytes(), before)
        same = self.other([iteration_op(1, 'a', '2026-11-05T10:00:00.000Z', ident=3)], name='same.json')  # control: same hash
        self.assertEqual(self.merge(same)[0], 0)

    def test_other_prototype_and_unreadable_files_are_blocked(self):
        before = (self.dir / 'comments.json').read_bytes()
        self.assertEqual(self.merge(self.other([create_op(5, 'th-5')], slug='inny'))[0], 2)
        (self.root / 'broken.json').write_text('{"nie":"dziennik"', encoding='utf-8')
        self.assertEqual(self.merge('broken.json')[0], 2)
        self.assertEqual(self.merge('nie-ma.json')[0], 2)
        self.assertEqual((self.dir / 'comments.json').read_bytes(), before)
        self.assertEqual(self.merge(self.other([create_op(5, 'th-5')]))[0], 0)  # control

    def test_the_report_lists_iterations_without_a_directory(self):
        other = self.other([iteration_op(40, 'c', '2026-11-05T11:00:00.000Z')])
        code, report = self.merge(other)
        self.assertEqual(code, 0)
        self.assertEqual([(item['type'], item['number']) for item in report['missingIterations']], [('iteration', 40)])
        self.assertTrue(any('iterations/<n>/' in warning for warning in report['warnings']))

    def test_the_git_driver_form_writes_the_union_to_the_current_file(self):
        extra_a, extra_b = reply_op(4, 'th-2', 'a'), reply_op(5, 'th-1', 'b')
        ancestor, current, theirs = (self.root / 'O.json', self.root / 'A.json', self.root / 'B.json')
        self.set_journal(ancestor, self.base_ops[:1])
        self.set_journal(current, self.base_ops + [extra_a])
        self.set_journal(theirs, self.base_ops + [extra_b])
        code, report = self.sk('prototype', 'merge-comments', '--driver', ancestor, current, theirs, '--json',
                               cwd=self.root)
        self.assertEqual((code, report['added']), (0, 1))
        merged = ps.parse_document(current.read_text(encoding='utf-8')).operations
        self.assertEqual([item['id'] for item in merged], [item['id'] for item in self.base_ops + [extra_a, extra_b]])

    def test_the_merged_state_does_not_depend_on_the_order_of_the_arguments(self):
        extra_a, extra_b = reply_op(4, 'th-2', 'a'), reply_op(5, 'th-1', 'b')
        results = []
        for first, second in (((self.base_ops + [extra_a]), (self.base_ops + [extra_b])),
                              ((self.base_ops + [extra_b]), (self.base_ops + [extra_a]))):
            mine, theirs, ancestor = self.root / 'A.json', self.root / 'B.json', self.root / 'O.json'
            self.set_journal(mine, first)
            self.set_journal(theirs, second)
            self.set_journal(ancestor, self.base_ops[:1])
            code, _report = self.sk('prototype', 'merge-comments', '--driver', ancestor, mine, theirs, '--json',
                                    cwd=self.root)
            self.assertEqual(code, 0)
            folded = ps.fold_operations(ps.parse_document(mine.read_text(encoding='utf-8')).operations)
            results.append(json.dumps({thread['id']: (thread['status'], sorted(m['id'] for m in thread['messages']))
                                       for thread in folded['threads']}, sort_keys=True))
        self.assertEqual(results[0], results[1])
        self.assertIn('m-4', results[0])  # control: the result holds both sides' operations
        self.assertIn('m-5', results[0])

    def test_the_driver_form_leaves_the_current_file_on_a_conflict(self):
        ancestor, current, theirs = (self.root / 'O.json', self.root / 'A.json', self.root / 'B.json')
        self.set_journal(ancestor, self.base_ops[:1])
        self.set_journal(current, self.base_ops)
        self.set_journal(theirs, [dict(self.base_ops[0], by='x@example.com')])
        before = current.read_bytes()
        code, report = self.sk('prototype', 'merge-comments', '--driver', ancestor, current, theirs, '--json',
                               cwd=self.root)
        self.assertEqual((code, report['error']), (2, 'merge_conflict'))
        self.assertEqual(current.read_bytes(), before)

    def test_merge_needs_a_v2_prototype(self):
        self.set_comments('demo', 'window.__SKILLSYNC_PROTOTYPE_COMMENTS__ = {"version":1,"prototypeId":"demo","operations":[]};\n')
        code, report = self.merge(self.other([create_op(5, 'th-5')]))
        self.assertEqual(code, 2)
        self.assertIn('migrate', report['reason'])


if __name__ == '__main__':
    unittest.main()
