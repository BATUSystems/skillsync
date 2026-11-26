"""CORE-CLOSURE-TRACKED B-4a (aneks speca, Z5/Z6): plan porzucony i `plan abandon`.

Porzucony = `approved`, żaden krok Progress nie odhaczony, brak lokalnego przebiegu i ostatnia zmiana pliku (data commita; dla zmiany
niezacommitowanej `mtime`) starsza niż 14 dni. `plan abandon`: `approved` -> `archived` + linia `Porzucony: <data UTC>`, audyt, ręcznie;
`unarchive` planu z linią wraca do `approved` i usuwa linię. Decyzja implementera utrwalona tu: `archive` i `unarchive` usuwają każdą poprawną
linię porzucenia z nagłówka (stara linia nie może skierować późniejszego `unarchive` na `approved`).
"""
import json
import os
import stat
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import needs_mode_enforcement, needs_os_symlinks, needs_posix  # noqa: E402
from test_skillsync_workflow import CLI, WorkflowFixture  # noqa: E402
from tmp_paths import descend  # noqa: E402

from skillsync_workflow import dashboard_history  # noqa: E402
from skillsync_workflow import protocol_tokens as tokens  # noqa: E402
from skillsync_workflow import status as status_core  # noqa: E402

DAY = 86400
NAME = 'docs/plans/2026-11-01-demo.md'


def plan(status='approved', run='abc', extra='', steps=('[ ] A-1 jeden', '[ ] A-2 dwa'), eol='\n'):
    text = f'# Plan: demo\n\nStatus: {status}\n' + (f'Przebieg: {run}\n' if run else '') + extra
    if steps is not None:
        text += '\n## Progress\n\n' + ''.join(f'- {step}\n' for step in steps)
    return text.replace('\n', eol)


def today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


class AbandonCase(WorkflowFixture):
    def setUp(self):
        super().setUp()
        self.write('.gitignore', '.skillsync/\n')
        self.init_git()

    def put(self, text, name=NAME, age=None):
        path = descend(self.root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8') if isinstance(text, str) else text)
        if age is not None:
            self.age(path, age)
        return path

    def age(self, path, days):
        moment = time.time() - days * DAY
        os.utime(path, (moment, moment))

    def commit(self, days, *names):
        stamp = f'{int(time.time() - days * DAY)} +0000'
        env = {**os.environ, 'GIT_COMMITTER_DATE': stamp, 'GIT_AUTHOR_DATE': stamp}
        for args in (['add', '-f', *(names or (NAME,))], ['commit', '-qm', f'plan {days}']):
            subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, env=env, timeout=30)

    def run_cli(self, *args, code=0, env=None):
        done = subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, encoding='utf-8', timeout=60,
                              cwd=self.root, env={**os.environ, **(env or {})})
        self.assertNotIn('Traceback', done.stderr, done.stderr)
        self.assertEqual(done.returncode, code, done.stdout + done.stderr)
        return json.loads(done.stdout)

    def rows(self, env=None):
        return {row['path']: row for row in self.run_cli('status', self.root, '--json', env=env)['plans']}

    def abandoned(self, name=NAME, env=None):
        return self.rows(env)[name]['abandoned']

    def audit(self):
        path = self.root / '.skillsync' / 'audit' / 'plan-status.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []


class Definition(AbandonCase):
    def test_a_committed_plan_is_dated_by_its_commit_not_by_the_file_time(self):
        path = self.put(plan())
        self.commit(15)
        self.age(path, 0)   # touch, klon albo przełączenie gałęzi
        self.assertIs(self.abandoned(), True)
        self.put(plan(extra='Nota.\n'))
        self.commit(2)
        self.age(path, 90)
        self.assertIs(self.abandoned(), False)

    def test_an_uncommitted_change_is_dated_by_the_file_time(self):
        path = self.put(plan())
        self.commit(90)
        for label, staged in (('worktree', False), ('index', True)):
            with self.subTest(label):
                self.put(plan(extra=f'Zmiana {label}.\n'))
                if staged:
                    self.git('add', NAME)
                self.age(path, 1)
                self.assertIs(self.abandoned(), False)
                self.age(path, 15)
                self.assertIs(self.abandoned(), True)

    def test_untracked_and_ignored_plans_are_dated_by_the_file_time(self):
        self.write('.gitignore', '.skillsync/\n/docs/plans/ignored.md\n')
        for name in ('docs/plans/untracked.md', 'docs/plans/ignored.md'):
            with self.subTest(name):
                path = self.put(plan(), name, age=1)
                self.assertIs(self.abandoned(name), False)
                self.age(path, 15)
                self.assertIs(self.abandoned(name), True)

    def test_without_git_the_file_time_decides(self):
        import shutil
        shutil.rmtree(self.root / '.git', onerror=lambda func, target, _: (os.chmod(target, 0o700), func(target)))
        path = self.put(plan(), age=15)
        self.assertIs(self.abandoned(), True)
        self.age(path, 13)
        self.assertIs(self.abandoned(), False)

    def test_a_plan_changed_by_a_merge_commit_is_dated_by_that_commit(self):
        """Rozwiązanie konfliktu w commicie scalającym to ostatnia zmiana pliku w gicie (`git log -1 -- plan` wskazuje ten commit)."""
        def git(*args, days=0, check=True):
            stamp = f'{int(time.time() - days * DAY)} +0000'
            return subprocess.run(['git', '-C', str(self.root), *args], check=check, capture_output=True, text=True, timeout=30,
                                  env={**os.environ, 'GIT_COMMITTER_DATE': stamp, 'GIT_AUTHOR_DATE': stamp})
        self.put(plan(extra='baza\n'))
        self.commit(60)
        main = git('branch', '--show-current').stdout.strip()
        git('checkout', '-qb', 'side')
        self.put(plan(extra='bok\n'))
        git('commit', '-qam', 'side', days=40)
        git('checkout', '-q', main)
        self.put(plan(extra='glowna\n'))
        git('commit', '-qam', 'main', days=40)
        self.assertIs(self.abandoned(), True, 'kontrola: przed scaleniem plan leży od 40 dni')
        git('merge', 'side', check=False)
        self.put(plan(extra='rozstrzygniete dzisiaj\n'))
        git('add', NAME)
        git('commit', '-qm', 'merge', days=0)
        self.assertEqual(git('status', '--porcelain').stdout, '')
        self.assertIs(self.abandoned(), False)

    def dated_git(self, *args, days=0, check=True):
        stamp = f'{int(time.time() - days * DAY)} +0000'
        return subprocess.run(['git', '-C', str(self.root), *args], check=check, capture_output=True, text=True, timeout=30,
                              env={**os.environ, 'GIT_COMMITTER_DATE': stamp, 'GIT_AUTHOR_DATE': stamp})

    def merge_today(self, main_change, side_change):
        """Plan sprzed 60 dni; zmiany obu gałęzi sprzed 40 dni; scalenie dzisiaj bez konfliktu."""
        body = 'poczatek\n\n.\n.\n.\n.\n.\n\nkoniec\n'
        self.put(plan(extra=body))
        self.commit(60)
        main = self.dated_git('branch', '--show-current').stdout.strip()
        for branch, change in (('side', side_change), (main, main_change)):
            self.dated_git('checkout', '-q', *(['-b'] if branch == 'side' else []), branch)
            if change:
                self.put(plan(extra=body.replace(change, change + ' zmiana')))
            else:
                self.write(f'inny-{branch}.txt', branch)
            self.dated_git('add', '.')
            self.dated_git('commit', '-qm', branch, days=40)
        self.dated_git('merge', '--no-ff', '-qm', 'merge', 'side')
        self.assertEqual(self.dated_git('status', '--porcelain').stdout, '')
        self.assertEqual(len(self.dated_git('log', '-1', '--format=%P').stdout.split()), 2)

    def test_an_ordinary_merge_does_not_move_the_date(self):
        for label, main_change, side_change in (('side-only', None, 'koniec'), ('main-only', 'poczatek', None), ('neither', None, None)):
            with self.subTest(label):
                import shutil
                shutil.rmtree(self.root / '.git', onerror=lambda func, target, _: (os.chmod(target, 0o700), func(target)))
                for extra in self.root.glob('inny-*.txt'):
                    extra.unlink()
                (self.root / NAME).unlink(missing_ok=True)
                self.init_git()
                self.merge_today(main_change, side_change)
                self.age(self.root / NAME, 0)
                self.assertIs(self.abandoned(), True)

    def test_a_merge_that_combines_the_changes_of_both_sides_dates_the_plan_by_the_merge(self):
        """Wynik scalenia różni się od obu rodziców: ta wersja pliku powstała w commicie scalającym."""
        self.merge_today('poczatek', 'koniec')
        text = (self.root / NAME).read_text(encoding='utf-8')
        self.assertIn('poczatek zmiana', text)
        self.assertIn('koniec zmiana', text)
        self.assertIs(self.abandoned(), False)

    def test_an_inherited_git_dir_does_not_redirect_the_dating(self):
        self.put(plan())
        self.commit(15)
        other = self.base / 'other-repo'
        other.mkdir()
        subprocess.run(['git', 'init', '-q', str(other)], check=True, capture_output=True, timeout=30)
        self.assertIs(self.abandoned(env={'GIT_DIR': str(other / '.git'), 'GIT_WORK_TREE': str(other)}), True)

    def test_the_limit_of_fourteen_days_with_an_injected_clock(self):
        self.assertEqual(status_core.ABANDONED_DAYS, 14)
        path = self.put(plan(), age=1)
        text = path.read_text(encoding='utf-8')
        moment = path.stat().st_mtime
        for label, now, abandoned in (('13d', moment + 14 * DAY - 1, False), ('exactly', moment + 14 * DAY, False),
                                      ('over', moment + 14 * DAY + 1, True)):
            with self.subTest(label, source='mtime'):
                self.assertIs(status_core.plan_abandon_blocker(self.root, NAME, text, moment, now=now) is None, abandoned)
        self.commit(30)
        committed = int(subprocess.run(['git', '-C', str(self.root), 'log', '-1', '--format=%ct', '--', NAME], check=True,
                                       capture_output=True, text=True, timeout=30).stdout)
        for label, now, abandoned in (('exactly', committed + 14 * DAY, False), ('over', committed + 14 * DAY + 1, True)):
            with self.subTest(label, source='commit'):
                self.assertIs(status_core.plan_abandon_blocker(self.root, NAME, text, time.time(), now=now) is None, abandoned)

    def test_only_an_approved_plan_has_the_field_and_the_other_fields_stay(self):
        for status in ('draft', 'approved', 'in_progress', 'done', 'archived'):
            self.put(plan(status), f'docs/plans/{status}.md', age=60)
        rows = self.rows()
        self.assertEqual({name: row.get('abandoned', 'missing') for name, row in rows.items()},
                         {f'docs/plans/{status}.md': (True if status == 'approved' else 'missing')
                          for status in ('draft', 'approved', 'in_progress', 'done', 'archived')})
        old = rows['docs/plans/approved.md']
        self.put(plan('approved'), 'docs/plans/approved.md', age=1)
        fresh = self.rows()['docs/plans/approved.md']
        self.assertEqual({k: v for k, v in old.items() if k not in ('abandoned', 'updated')},
                         {k: v for k, v in fresh.items() if k not in ('abandoned', 'updated')}, 'pole jest dodatkiem: reszta bez zmian')

    def test_any_checked_step_means_progress(self):
        cases = {'lower': (('[x] A-1', '[ ] A-2'), False), 'upper': (('[ ] A-1', '[X] A-2'), False), 'all': (('[x] A-1',), False),
                 'none': (('[ ] A-1', '[ ] A-2'), True), 'no-progress': (None, True)}
        for label, (steps, expected) in cases.items():
            with self.subTest(label):
                self.put(plan(steps=steps), age=15)
                self.assertIs(self.abandoned(), expected)
        for heading in ('Kroki', 'Steps'):
            with self.subTest(heading):
                self.put(plan(steps=None) + f'\n## {heading}\n\n- [x] A-1\n', age=15)
                self.assertIs(self.abandoned(), False)

    def test_a_local_run_of_the_plan_protects_it(self):
        runs = self.root / '.skillsync' / 'runs'
        self.put(plan(), age=15)

        def directory():
            (runs / 'abc').mkdir(parents=True)

        def run_file():
            self.write('.skillsync/runs/abc.json', '{"checkpoints": []}')

        def attempt():
            self.write('.skillsync/runs/abc.2.json', '{"checkpoints": []}')

        def approvals():
            self.write('.skillsync/runs/abc/approvals.jsonl', '')

        import shutil
        for make in (directory, run_file, attempt, approvals):
            with self.subTest(make.__name__):
                make()
                self.assertIs(self.abandoned(), False)
                shutil.rmtree(self.root / '.skillsync')
                self.assertIs(self.abandoned(), True)
        self.write('.skillsync/runs/abcd.json', '{"checkpoints": []}')
        self.assertIs(self.abandoned(), True, 'cudzy przebieg')

    def test_a_plan_without_a_usable_run_line_has_no_run(self):
        self.write('.skillsync/runs/abc.json', '{"checkpoints": []}')
        for label, run in (('no-line', None), ('bad-id', '../x')):
            with self.subTest(label):
                self.put(plan(run=run), age=15)
                self.assertIs(self.abandoned(), True)

    def test_a_run_line_in_a_code_block_is_not_the_run_of_the_plan(self):
        self.write('.skillsync/runs/przyklad.json', '{"checkpoints": []}')
        example = '\n```\nPrzebieg: przyklad\n```\n'
        self.put(f'# P\n\nStatus: approved\n{example}Przebieg: abc\n\n## Progress\n\n- [ ] A-1\n', age=15)
        self.assertIs(self.abandoned(), True)
        self.write('.skillsync/runs/abc.json', '{"checkpoints": []}')
        self.assertIs(self.abandoned(), False)
        with self.subTest(control='the same line outside a block is the run'):
            (self.root / '.skillsync/runs/abc.json').unlink()
            self.put(plan(run='przyklad'), age=15)
            self.assertIs(self.abandoned(), False)

    def test_a_checked_step_only_inside_a_code_block_is_not_progress(self):
        for fence in ('```', '~~~'):
            for eol in ('\n', '\r\n'):
                with self.subTest(fence, eol=repr(eol)):
                    block = f'\n{fence}\n- [x] przyklad\n{fence}\n'.replace('\n', eol)
                    self.put(plan(eol=eol) + block, age=15)
                    self.assertIs(self.abandoned(), True)
                    self.put(plan(eol=eol) + f'{eol}- [x] prawdziwy{eol}', age=15)   # kontrola: ten sam wiersz poza blokiem
                    self.assertIs(self.abandoned(), False)
        with self.subTest('a real step after a closed block still counts'):
            self.put(plan() + '\n```\nx\n```\n- [x] po bloku\n', age=15)
            self.assertIs(self.abandoned(), False)

    def test_a_status_line_only_inside_a_code_block_makes_no_candidate(self):
        cases = {'header': '# P\n\n```\nStatus: approved\n```\n', 'tilde': '# P\n\n~~~\nStatus: approved\n~~~\n',
                 'unclosed': '# P\n\n```\nStatus: approved\n',
                 'example-approved-real-draft': '# P\n\n```\nStatus: approved\n```\nStatus: draft\n',
                 'example-done-before-real-approved': '# P\n\n```\nStatus: done\n```\nStatus: approved\n'}
        for label, text in cases.items():
            with self.subTest(label):
                path = self.put(text, age=15)
                self.assertIsNot(self.rows()[NAME].get('abandoned'), True)
                report = self.run_cli('plan', 'abandon', self.root, NAME, '--json', code=2)
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertEqual(path.read_bytes(), text.encode('utf-8'))
        self.assertEqual(self.audit(), [])
        self.put('# P\n\nStatus: approved\n\n```\nStatus: done\n```\n', age=15)
        self.assertIs(self.abandoned(), True, 'kontrola: własny status przed blokiem')

    def test_the_history_offers_no_abandon_for_a_plan_whose_status_is_read_from_a_code_block(self):
        """Wiersz „zakończony” (status z pierwszej linii `Status:`) nie może dostać akcji „Porzuć”, choć poza blokiem stoi `approved`."""
        def history_row():
            tab = dashboard_history.TabRoot('main', 'p', self.root)
            return next(item for item in dashboard_history.collect([tab])['items'] if item['path'] == NAME)
        self.put('# P\n\n```\nStatus: done\n```\nStatus: approved\n', age=15)
        row = history_row()
        self.assertEqual((row['status'], row['abandoned'], row['action']), ('done', False, 'archive'))
        self.put('# P\n\nStatus: approved\n', age=15)   # kontrola: porzucony plan jest na liście z akcją
        row = history_row()
        self.assertEqual((row['status'], row['abandoned'], row['action']), ('approved', True, 'abandon'))

    def test_a_plan_with_an_unclosed_code_block_is_never_abandoned(self):
        """W2 (przegląd B-9): po niezamkniętym bloku nie wiadomo, co jest postępem, więc plan nie jest porzucony."""
        cases = {'open': '\n```\nniezamkniety\n', 'checked-after': '\n```\n- [x] po otwarciu\n', 'tilde': '\n~~~\nx\n',
                 'closing-with-note': '\n```\nx\n``` koniec\n', 'shorter-closing': '\n````\nx\n```\n', 'other-mark': '\n```\nx\n~~~\n'}
        for label, tail in cases.items():
            for eol in ('\n', '\r\n'):
                with self.subTest(label, eol=repr(eol)):
                    path = self.put(plan(eol=eol) + tail.replace('\n', eol), age=15)
                    before = path.read_bytes()
                    self.assertIs(self.abandoned(), False)
                    self.assertEqual(self.run_cli('plan', 'abandon', self.root, NAME, '--json', code=2)['status'], 'BLOCKED')
                    self.assertEqual(path.read_bytes(), before)
                    tab = dashboard_history.TabRoot('main', 'p', self.root)
                    self.assertEqual([i for i in dashboard_history.collect([tab])['items'] if i['path'] == NAME], [])
        with self.subTest(control='the closed block does not protect'):
            self.put(plan() + '\n```\nx\n```\n', age=15)
            self.assertIs(self.abandoned(), True)

    @needs_os_symlinks
    def test_a_symlinked_store_protects_the_plan(self):
        outside = self.base / 'store'
        (outside / 'runs').mkdir(parents=True)
        (self.root / '.skillsync').symlink_to(outside, target_is_directory=True)
        self.put(plan(), age=15)
        self.assertIs(self.abandoned(), False)


class AbandonCommand(AbandonCase):
    def abandon(self, *extra, code=0, name=NAME):
        return self.run_cli('plan', 'abandon', self.root, name, *extra, '--json', code=code)

    def expected(self, text, eol='\n'):
        return text.replace(f'Status: approved{eol}', f'Status: archived{eol}Porzucony: {today()}{eol}', 1)

    def test_it_archives_adds_the_line_under_the_status_and_changes_nothing_else(self):
        for eol in ('\n', '\r\n'):
            with self.subTest(eol=repr(eol)):
                text = plan(eol=eol, extra='Zażółć gęślą jaźń   \n')
                path = self.put(text, age=15)
                before = today()
                report = self.abandon()
                self.assertEqual(before, today(), 'test przez północ UTC: powtórz')
                self.assertEqual((report['status'], report['from'], report['to'], report['warnings']), ('PASS', 'approved', 'archived', []))
                self.assertEqual(path.read_bytes(), self.expected(text, eol).encode('utf-8'))
                self.assertEqual(report['sha'], status_core.git_blob_sha(path.read_bytes()))
                self.assertEqual(report['plan'], NAME)
                self.assertEqual([p.name for p in path.parent.iterdir()], [path.name], 'bez plików tymczasowych')
                row = self.rows()[NAME]
                self.assertEqual((row['status'], row['needsHuman'], 'abandoned' in row), ('archived', [], False))

    def test_the_note_after_the_status_word_stays_and_a_file_ending_on_the_status_line_gets_no_final_newline(self):
        for text, expected in (('# P\n\nStatus: approved (po review)\n\nreszta', f'# P\n\nStatus: archived (po review)\nPorzucony: {today()}\n\nreszta'),
                               ('# P\n\nStatus: approved', f'# P\n\nStatus: archived\nPorzucony: {today()}'),
                               ('# P\r\n\r\nStatus: approved', f'# P\r\n\r\nStatus: archived\r\nPorzucony: {today()}')):
            with self.subTest(text=text):
                path = self.put(text, age=15)
                self.abandon()
                self.assertEqual(path.read_bytes(), expected.encode('utf-8'))
                self.run_cli('plan', 'unarchive', self.root, NAME, '--json')
                self.assertEqual(path.read_bytes(), text.encode('utf-8'), 'abandon i unarchive: bajt w bajt')

    @needs_posix
    def test_the_file_mode_is_kept(self):
        path = self.put(plan(), age=15)
        os.chmod(path, 0o640)
        self.abandon()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_the_audit_has_the_intent_and_the_result_of_the_change(self):
        path = self.put(plan(), age=15)
        old = status_core.git_blob_sha(path.read_bytes())
        self.abandon('--expect-sha', old)
        rows = self.audit()
        self.assertEqual([(r['phase'], r['from'], r['to'], r['path'], r['via'], r['tab']) for r in rows],
                         [('intent', 'approved', 'archived', NAME, 'cli', None), ('done', 'approved', 'archived', NAME, 'cli', None)])
        self.assertTrue(all(r['by'] and r['at'] for r in rows))
        self.assertEqual(list(self.root.glob('.skillsync/runs/*')), [], 'bez zgód i przebiegów')

    def assert_refused(self, path, fragment, *extra, name=NAME):
        before = path.read_bytes()
        report = self.abandon(*extra, code=2, name=name)
        self.assertEqual(report['status'], 'BLOCKED', report)
        self.assertIn(fragment, report['reason'])
        self.assertEqual(path.read_bytes(), before)
        return report

    def test_refusals_change_nothing_and_leave_no_audit(self):
        old_plan = lambda **kw: self.put(plan(**kw), age=15)   # noqa: E731
        with self.subTest('fresh'):
            self.assert_refused(self.put(plan(), age=1), '14 days')
        with self.subTest('checked step'):
            self.assert_refused(old_plan(steps=('[x] A-1',)), 'checked Progress steps')
        for status in ('draft', 'in_progress', 'done', 'archived'):
            with self.subTest(status):
                self.assert_refused(old_plan(status=status), "expected 'approved'")
        with self.subTest('local run'):
            self.write('.skillsync/runs/abc.json', '{"checkpoints": []}')
            self.assert_refused(old_plan(), 'local run')
            (self.root / '.skillsync/runs/abc.json').unlink()
        with self.subTest('sha mismatch'):
            self.assert_refused(old_plan(), 'sha mismatch', '--expect-sha', '0' * 40)
        with self.subTest('bad sha'):
            self.assert_refused(old_plan(), '40-character', '--expect-sha', 'HEAD')
        with self.subTest('busy lock'):
            path = old_plan()
            with status_core.plan_status_lock(self.root):
                self.assert_refused(path, 'locked by another writer')
        self.assertEqual(self.audit(), [])
        self.abandon()   # kontrola: ten sam plan bez przeszkód przechodzi

    def test_a_spec_or_a_file_outside_docs_plans_is_refused(self):
        for name in ('docs/specs/x.md', 'notes.md'):
            with self.subTest(name):
                path = self.put(plan(), name, age=15)
                self.assert_refused(path, 'docs', name=name)

    def test_a_status_line_that_could_not_get_a_readable_line_is_refused(self):
        text = '# P\n\n## Meta\n\nStatus: approved\n'
        self.assert_refused(self.put(text, age=15), 'would not be read')

    @needs_os_symlinks
    def test_without_the_audit_nothing_changes(self):
        outside = self.base / 'outside-audit'
        outside.mkdir()
        (self.root / '.skillsync').mkdir()
        (self.root / '.skillsync/audit').symlink_to(outside, target_is_directory=True)
        path = self.put(plan(), age=15)
        self.assert_refused(path, 'audit')
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_failed_replace_leaves_the_whole_old_plan_and_no_temporary_file(self):
        path = self.put(plan(), age=15)
        before = path.read_bytes()
        with mock.patch.object(status_core.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises((OSError, ValueError)):
                status_core.abandon_plan(self.root, NAME)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([p.name for p in path.parent.iterdir()], [path.name])
        self.assertEqual([r['phase'] for r in self.audit()], ['intent', 'failed'])

    def test_nothing_moves_a_plan_by_itself(self):
        path = self.put(plan(), age=400)
        before = path.read_bytes()
        self.rows()
        self.run_cli('status', self.root, '--json')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.audit(), [])


class OtherWorktrees(AbandonCase):
    """W3 (przegląd B-9): praca nad planem w innym worktree tego samego repozytorium chroni go przed porzuceniem."""

    def setUp(self):
        super().setUp()
        self.put(plan())
        self.commit(15)
        self.other = self.base / 'other-tree'
        self.git('worktree', 'add', '-q', '-b', 'side', str(self.other))
        self.copy = descend(self.other, NAME)

    def assert_protected(self, fragment):
        path = self.root / NAME
        before = path.read_bytes()
        self.assertIs(self.abandoned(), False)
        report = self.run_cli('plan', 'abandon', self.root, NAME, '--json', code=2)
        self.assertEqual(report['status'], 'BLOCKED', report)
        self.assertIn(fragment, report['reason'])
        self.assertEqual(path.read_bytes(), before)

    def test_control_an_idle_copy_or_no_copy_does_not_protect(self):
        self.assertTrue(self.copy.is_file())
        self.assertIs(self.abandoned(), True)
        self.copy.unlink()
        self.assertIs(self.abandoned(), True)
        self.run_cli('plan', 'abandon', self.root, NAME, '--json')

    def test_progress_or_an_unclosed_block_in_the_other_copy_protects(self):
        for label, text in (('checked', plan(steps=('[x] A-1 jeden', '[ ] A-2 dwa'))), ('unclosed', plan() + '\n```\nx\n')):
            with self.subTest(label):
                self.copy.write_text(text, encoding='utf-8', newline='')
                self.assert_protected('worktree')
        with self.subTest('a checked step only in a code block of the copy'):
            self.copy.write_text(plan() + '\n```\n- [x] przyklad\n```\n', encoding='utf-8', newline='')
            self.assertIs(self.abandoned(), True)

    def run_appears_and_goes(self, tree, run):
        import shutil
        (tree / '.skillsync' / 'runs').mkdir(parents=True, exist_ok=True)
        (tree / '.skillsync' / 'runs' / f'{run}.json').write_text('{"checkpoints": []}', encoding='utf-8')
        self.assert_protected(f'local run `{run}`')
        shutil.rmtree(tree / '.skillsync' / 'runs')
        self.assertIs(self.abandoned(), True)

    def test_a_run_of_the_plan_in_any_tree_protects(self):
        for label, tree in (('other', self.other), ('own', self.root)):
            with self.subTest(label):
                self.run_appears_and_goes(tree, 'abc')

    def test_the_run_of_another_copy_in_any_tree_protects(self):
        self.copy.write_text(plan(run='inny'), encoding='utf-8', newline='')
        self.assertIs(self.abandoned(), True, 'inna linia przebiegu bez śladu')
        for label, tree in (('store of the copy', self.other), ('own store', self.root)):
            with self.subTest(label):
                self.run_appears_and_goes(tree, 'inny')

    def test_the_linked_worktree_sees_the_work_in_the_main_tree(self):
        main, linked = self.root, self.other
        (main / NAME).write_text(plan(steps=('[x] A-1 jeden',)), encoding='utf-8', newline='')
        self.root = linked
        self.assertIs(self.abandoned(), False)
        (main / NAME).write_text(plan(), encoding='utf-8', newline='')
        self.assertIs(self.abandoned(), True)

    def test_an_oversized_copy_protects(self):
        self.copy.write_bytes(plan().encode('utf-8') + b'x' * (status_core.MAX_PLAN_BYTES + 1))
        self.assert_protected('cannot be read')

    @needs_os_symlinks
    def test_a_symlinked_copy_protects(self):
        target = self.base / 'idle.md'
        target.write_text(plan(), encoding='utf-8')
        self.copy.unlink()
        self.copy.symlink_to(target)
        self.assert_protected('cannot be read')

    @needs_mode_enforcement
    def test_an_unreadable_copy_protects(self):
        os.chmod(self.copy, 0)
        self.addCleanup(os.chmod, self.copy, 0o600)
        self.assert_protected('cannot be read')

    def test_a_removed_worktree_directory_does_not_protect(self):
        import shutil
        shutil.rmtree(self.other)
        self.assertIs(self.abandoned(), True)

    def test_a_project_in_a_subdirectory_is_matched_with_the_same_subdirectory(self):
        repo = self.base / 'mono'
        app = repo / 'app'
        (app / 'docs/plans').mkdir(parents=True)
        (app / NAME).write_text(plan(), encoding='utf-8')
        (app / '.github').mkdir()
        (app / '.github/.skillsync.yaml').write_bytes((self.root / '.github/.skillsync.yaml').read_bytes())
        stamp = f'{int(time.time() - 15 * DAY)} +0000'
        env = {**os.environ, 'GIT_COMMITTER_DATE': stamp, 'GIT_AUTHOR_DATE': stamp}
        side = self.base / 'mono-side'
        for args in (['init', '-q'], ['config', 'user.email', 'a@example.invalid'], ['config', 'user.name', 'a'], ['add', '.'],
                     ['commit', '-qm', 'x'], ['worktree', 'add', '-q', '-b', 'side2', str(side)]):
            subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, env=env, timeout=30)
        self.root = app
        self.assertIs(self.abandoned(), True)
        (side / 'app' / NAME).write_text(plan(steps=('[x] A-1',)), encoding='utf-8')
        self.assertIs(self.abandoned(), False)


class WriterOutsideTheLock(AbandonCase):
    """W1: zapis spoza blokady (edytor, inne narzędzie) między odczytem a podmianą nie jest nadpisywany."""

    def writer_in_the_gap(self, path, change):
        """`chmod` pliku tymczasowego leży między odczytem planu a podmianą."""
        real, done = os.chmod, []

        def chmod(target, mode, *args, **kwargs):
            if not done and str(target).endswith('.tmp') and path.name in str(target):
                done.append(1)
                change()
            return real(target, mode, *args, **kwargs)
        return mock.patch.object(status_core.os, 'chmod', chmod)

    def test_every_status_change_refuses_to_overwrite_a_concurrent_edit(self):
        for action, status in (('abandon', 'approved'), ('archive', 'done'), ('unarchive', 'archived'), ('approve', 'draft')):
            with self.subTest(action):
                path = self.put(plan(status), age=15)
                edited = plan(status, extra='Cudza zmiana.\n').encode('utf-8')
                with self.writer_in_the_gap(path, lambda: path.write_bytes(edited)):
                    with self.assertRaisesRegex(ValueError, 'sha mismatch'):
                        if action == 'abandon':
                            status_core.abandon_plan(self.root, NAME)
                        else:
                            status_core.set_doc_status(self.root, NAME, action)
                self.assertEqual(path.read_bytes(), edited)
                self.assertEqual([p.name for p in path.parent.iterdir()], [path.name])

    @needs_os_symlinks
    def test_a_plan_swapped_for_a_symlink_in_the_gap_is_not_replaced(self):
        path = self.put(plan('done'))
        target = self.base / 'target.md'
        target.write_bytes(path.read_bytes())

        def swap():
            path.unlink()
            path.symlink_to(target)
        with self.writer_in_the_gap(path, swap), self.assertRaisesRegex(ValueError, 'sha mismatch'):
            status_core.set_doc_status(self.root, NAME, 'archive')
        self.assertTrue(path.is_symlink())
        self.assertEqual(target.read_bytes(), plan('done').encode('utf-8'))


class AbandonmentLine(AbandonCase):
    def move(self, action, text, name=NAME):
        path = self.put(text, name)
        report = self.run_cli('plan', action, self.root, name, '--json')
        return path.read_bytes().decode('utf-8'), report['to']

    def test_unarchive_of_an_abandoned_plan_goes_back_to_approved(self):
        for label, line in (('pl', 'Porzucony: 2026-11-01'), ('en', 'Abandoned: 2026-11-01'),
                            ('two', 'Porzucony: 2026-11-01\nAbandoned: 2026-10-01')):
            for eol in ('\n', '\r\n'):
                with self.subTest(label, eol=repr(eol)):
                    text = f'# P\n\nStatus: archived\n{line}\nPrzebieg: abc\n\n## Progress\n'.replace('\n', eol)
                    written, to = self.move('unarchive', text)
                    self.assertEqual((written, to), ('# P\n\nStatus: approved\nPrzebieg: abc\n\n## Progress\n'.replace('\n', eol), 'approved'))

    def test_a_line_that_is_not_the_abandonment_line_means_done_and_stays(self):
        cases = {'bad-date': 'Porzucony: wczoraj\n', 'tail': 'Porzucony: 2026-11-01 bo tak\n', 'quote': '> Porzucony: 2026-11-01\n',
                 'fence': '```\nPorzucony: 2026-11-01\n```\n', 'body': '\n## Notatki\n\nPorzucony: 2026-11-01\n'}
        for label, extra in cases.items():
            with self.subTest(label):
                written, to = self.move('unarchive', f'# P\n\nStatus: archived\n{extra}')
                self.assertEqual((written, to), (f'# P\n\nStatus: done\n{extra}', 'done'))

    def test_archive_drops_a_stale_line_so_that_a_later_unarchive_goes_to_done(self):
        written, to = self.move('archive', '# P\n\nStatus: done\nPorzucony: 2026-11-01\n```\nPorzucony: 2026-01-01\n```\n')
        self.assertEqual((written, to), ('# P\n\nStatus: archived\n```\nPorzucony: 2026-01-01\n```\n', 'archived'))
        written, to = self.move('unarchive', written)
        self.assertEqual((written, to), ('# P\n\nStatus: done\n```\nPorzucony: 2026-01-01\n```\n', 'done'))

    def test_approve_does_not_touch_the_plan_apart_from_the_status(self):
        written, _ = self.move('approve', '# P\n\nStatus: draft\nPorzucony: 2026-11-01\n')
        self.assertEqual(written, '# P\n\nStatus: approved\nPorzucony: 2026-11-01\n')

    def test_the_grammar_of_the_line(self):
        self.assertEqual(tuple(tokens.names('abandoned')), ('Porzucony:', 'Abandoned:'))
        table = [('# P\nPorzucony: 2026-11-01\n', '2026-11-01'), ('# P\r\nAbandoned: 2026-11-01\r\n', '2026-11-01'),
                 ('# P\nPorzucony: 2026-11-01  \n', '2026-11-01'), ('# P\nPorzucony:\n2026-11-01\n', None),
                 ('# P\nPorzucony: 2026-1-1\n', None), ('# P\n  Porzucony: 2026-11-01\n', None),
                 ('# P\n~~~\nPorzucony: 2026-11-01\n~~~\n', None), ('# P\n## S\nPorzucony: 2026-11-01\n', None),
                 ('x' * 4000 + '\nPorzucony: 2026-11-01\n', None), ('', None)]
        for text, expected in table:
            with self.subTest(text=text[-60:]):
                self.assertEqual(tokens.abandoned_line(text), expected)


if __name__ == '__main__':
    unittest.main()
