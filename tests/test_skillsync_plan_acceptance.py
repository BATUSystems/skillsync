"""`needsHuman: acceptance` of `skillsync status` (S33): a closed plan with the human acceptance step waits for the closing consent word
of its delivery mode (`READY` in `pr`, `CP` in `direct`), recorded in the run's `approvals.jsonl` (its sha is a commit where the plan is `done`). Every plan of an active run (its run directory exists)
waits on its own; the plan text never ends the wait."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import status  # noqa: E402

GIT_ENV = {'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@example.test', 'GIT_COMMITTER_NAME': 'T',
           'GIT_COMMITTER_EMAIL': 't@example.test', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}


def plan(status_word='done', steps=None, run='ACC-1', extra=''):
    steps = steps if steps is not None else ['- [x] F1.1 Kod', '- [x] F4.3 Odbiór przez człowieka (lista kontrolna, pokrycie)', '- [x] F4.4 Zamknięcie przebiegu']
    return f'# Plan: demo\n\nStatus: {status_word}\nPrzebieg: {run}\n\n## Cel\nx\n{extra}\n## Progress\n' + '\n'.join(steps) + '\n'


class AcceptanceSignal(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'docs/plans').mkdir(parents=True)
        self.git('init', '-q', '-b', 'work')
        self.commit('first')

    def git(self, *args):
        result = subprocess.run(['git', *args], cwd=self.root, capture_output=True, text=True, env={**os.environ, **GIT_ENV})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, name):
        (self.root / f'{name}.txt').write_text(name, encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-q', '-m', name)
        return self.git('rev-parse', 'HEAD')

    def write_plan(self, text, name='2026-11-15-demo.md', run='ACC-1'):
        (self.root / 'docs/plans' / name).write_text(text, encoding='utf-8')
        if run:
            (self.root / '.skillsync/runs' / run).mkdir(parents=True, exist_ok=True)   # the run is active in this worktree
        self.git('add', 'docs')
        self.git('commit', '-q', '--allow-empty', '-m', f'plan {name}')   # an entry counts only for a commit that holds the plan as done

    def approve(self, word, sha, run='ACC-1'):
        directory = self.root / '.skillsync/runs' / run
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'approvals.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'schema': 1, 'at': '2026-11-15T16:00:00Z', 'word': word, 'sha': sha, 'by': 'x', 'source': 'cli'}) + '\n')

    def needs(self, mode=None):
        manifest = {'delivery': {'mode': mode, 'base': 'main'}} if mode else {}
        (record,) = status.project_status(self.root, manifest)['plans']
        return record['needsHuman']

    def test_a_closed_plan_with_the_acceptance_step_waits_for_the_closing_word_in_both_modes(self):
        self.write_plan(plan())
        for mode in ('pr', 'direct', None):   # no `delivery:` = `pr`
            with self.subTest(mode=mode):
                self.assertEqual(self.needs(mode), ['acceptance'])

    def test_only_the_closing_word_of_the_mode_ends_the_wait(self):
        self.write_plan(plan())
        head = self.git('rev-parse', 'HEAD')
        self.approve('CP', head)
        self.assertEqual(self.needs('pr'), ['acceptance'])       # CP is not the closing word of `pr`
        self.assertEqual(self.needs('direct'), [])
        self.approve('READY', head)
        self.assertEqual(self.needs('pr'), [])
        self.assertEqual(self.needs(), [])

    def test_a_new_head_does_not_light_a_finished_plan_again(self):
        self.write_plan(plan())
        self.approve('READY', self.git('rev-parse', 'HEAD'))
        self.assertEqual(self.needs('pr'), [])
        self.commit('later')
        self.assertEqual(self.needs('pr'), [])

    def test_direct_cp_given_before_the_plan_was_closed_closes_nothing(self):
        self.write_plan(plan('in_progress', steps=['- [x] F1.1 Kod', '- [ ] F4.3 Odbiór', '- [ ] F4.4 Zamknięcie']))
        self.approve('CP', self.git('rev-parse', 'HEAD'))                  # CP is said many times during a run
        self.write_plan(plan())                                            # the plan is closed later
        self.assertEqual(self.needs('direct'), ['acceptance'])

    def test_direct_cp_on_the_commit_where_the_plan_is_done_closes_it_and_a_later_commit_does_not_light_it(self):
        self.write_plan(plan('in_progress', steps=['- [x] F1.1 Kod', '- [ ] F4.3 Odbiór', '- [ ] F4.4 Zamknięcie']))
        self.write_plan(plan())
        self.approve('CP', self.git('rev-parse', 'HEAD'))
        self.assertEqual(self.needs('direct'), [])
        self.commit('later')
        self.assertEqual(self.needs('direct'), [])

    def test_a_plan_reopened_and_closed_again_after_the_word_waits_for_a_new_word(self):
        self.write_plan(plan())
        self.approve('CP', self.git('rev-parse', 'HEAD'))
        self.assertEqual(self.needs('direct'), [])
        self.write_plan(plan('in_progress', steps=['- [x] F1.1 Kod', '- [ ] F4.3 Odbiór', '- [ ] F4.4 Zamknięcie']))   # notes from the acceptance
        self.write_plan(plan())                                                                                    # closed the second time
        self.assertEqual(self.needs('direct'), ['acceptance'])
        self.approve('CP', self.git('rev-parse', 'HEAD'))                                                           # a new word closes it again
        self.assertEqual(self.needs('direct'), [])

    def test_a_reopening_in_the_history_between_two_commits_is_noticed(self):
        self.write_plan(plan())
        self.approve('READY', self.git('rev-parse', 'HEAD'))
        self.write_plan(plan('in_progress'))
        self.write_plan(plan('done'))
        self.commit('later')
        self.assertEqual(self.needs('pr'), ['acceptance'])

    def test_the_word_status_in_the_body_and_an_edited_header_text_do_not_reopen_the_plan(self):
        self.write_plan(plan(extra='The status of the work is described below; status quo stays.\n'))
        self.approve('READY', self.git('rev-parse', 'HEAD'))
        self.write_plan(plan('done (edited note)', extra='The status of the work changed its wording; the header word stays done.\n'))
        self.write_plan(plan(extra='A third wording of the status paragraph.\n'))
        self.assertEqual(self.needs('pr'), [])

    def test_a_failing_history_check_closes_nothing(self):
        self.write_plan(plan())
        self.approve('READY', self.git('rev-parse', 'HEAD'))
        real = subprocess.run

        def fail_log(command, *args, **kwargs):
            if 'log' in command:
                raise OSError('git is gone')
            return real(command, *args, **kwargs)
        with mock.patch.object(status.subprocess, 'run', fail_log):
            self.assertEqual(self.needs('pr'), ['acceptance'])

    def test_at_most_two_git_calls_per_plan(self):
        self.write_plan(plan())
        self.approve('READY', self.git('rev-parse', 'HEAD'))
        real, calls = subprocess.run, []

        def counting(command, *args, **kwargs):
            calls.append(command)
            return real(command, *args, **kwargs)
        with mock.patch.object(status.subprocess, 'run', counting):
            self.assertEqual(self.needs('pr'), [])
        self.assertEqual(len([c for c in calls if c[0] == 'git']), 2)

    def test_a_status_line_in_the_body_never_reopens_the_plan(self):
        self.write_plan(plan())
        self.approve('READY', self.git('rev-parse', 'HEAD'))
        self.write_plan(plan(extra='\n' * 40 + 'Status: PASS\n- Status: BLOCKED\n> Status: draft\n'))
        self.assertEqual(self.needs('pr'), [])
        self.write_plan(plan(extra='\n' * 40))                                     # removing such lines is no reopening either
        self.assertEqual(self.needs('pr'), [])

    def test_other_header_forms_of_a_reopening_are_noticed(self):
        for reopened in ('**Status:** in_progress', '- Status: draft', '> Status: in_progress'):
            with self.subTest(reopened=reopened):
                self.write_plan(plan())
                self.approve('READY', self.git('rev-parse', 'HEAD'))
                self.write_plan(plan().replace('Status: done', reopened, 1))
                self.write_plan(plan())
                self.assertEqual(self.needs('pr'), ['acceptance'])
                self.write_plan(plan('draft'))     # leave the file reopened for the next form; the entry stays stale
                self.write_plan(plan())
                self.git('commit', '-q', '--allow-empty', '-m', 'x')

    def test_a_failing_git_log_with_a_nonzero_exit_closes_nothing(self):
        self.write_plan(plan())
        self.approve('READY', self.git('rev-parse', 'HEAD'))
        real = subprocess.run

        def broken(command, *args, **kwargs):
            if 'log' in command:
                return subprocess.CompletedProcess(command, 128, b'', b'fatal')
            return real(command, *args, **kwargs)
        with mock.patch.object(status.subprocess, 'run', broken):
            self.assertEqual(self.needs('pr'), ['acceptance'])

    def test_an_entry_on_a_commit_outside_the_history_of_head_closes_nothing(self):
        self.write_plan(plan('in_progress'))
        self.write_plan(plan())
        sha = self.git('rev-parse', 'HEAD')
        self.git('reset', '-q', '--hard', 'HEAD~1')
        self.commit('other')                                                       # another history (a rebuilt commit would get the same sha)
        self.write_plan(plan())                                                    # the same plan rebuilt on top of it
        self.approve('READY', sha)
        self.assertEqual(self.needs('pr'), ['acceptance'])

    def test_several_lines_of_the_same_word_in_the_run_still_cost_two_git_calls(self):
        self.write_plan(plan())
        sha = self.git('rev-parse', 'HEAD')
        for _ in range(5):
            self.approve('CP', sha)
        real, calls = subprocess.run, []

        def counting(command, *args, **kwargs):
            calls.append(command)
            return real(command, *args, **kwargs)
        with mock.patch.object(status.subprocess, 'run', counting):
            self.assertEqual(self.needs('direct'), [])
        self.assertEqual(len([c for c in calls if c[0] == 'git']), 2)

    def test_a_sha_outside_the_repo_or_junk_closes_nothing(self):
        self.write_plan(plan())
        for sha in ('f' * 40, 'abcdef1', 'not-a-sha', '', '--help'):
            with self.subTest(sha=sha):
                self.approve('READY', sha)
                self.assertEqual(self.needs('pr'), ['acceptance'])

    def test_pr_ready_on_the_done_commit_still_closes(self):
        self.write_plan(plan())
        self.approve('READY', self.git('rev-parse', 'HEAD'))
        self.assertEqual(self.needs('pr'), [])

    def test_an_entry_of_another_run_does_not_count(self):
        self.write_plan(plan())
        self.approve('READY', self.git('rev-parse', 'HEAD'), run='OTHER')
        self.assertEqual(self.needs('pr'), ['acceptance'])

    def test_the_checkbox_and_a_line_in_the_plan_never_end_the_wait(self):
        for steps in (['- [x] F4.3 Odbiór'], ['- [ ] F4.3 Odbiór']):
            with self.subTest(steps=steps):
                self.write_plan(plan(steps=steps, extra='\nODBIÓR OK\n- ODBIÓR OK\n'))
                self.assertEqual(self.needs('pr'), ['acceptance'])

    def test_plans_without_the_step_or_not_closed_have_no_signal(self):
        self.write_plan(plan(steps=['- [x] F1.1 Kod', '- [x] F1.2 Zamknięcie']))
        self.assertEqual(self.needs('pr'), [])                    # no acceptance step: as before (K12)
        for word in ('draft', 'approved', 'archived'):
            self.write_plan(plan(word))
            self.assertNotIn('acceptance', self.needs('pr'), word)
        self.write_plan(plan('in_progress'))
        self.assertEqual(self.needs('pr'), ['closure'])           # all steps checked, not closed: `closure`, never both

    def by_name(self, mode=None):
        manifest = {'delivery': {'mode': mode, 'base': 'main'}} if mode else {}
        return {Path(item['path']).name: item['needsHuman'] for item in status.project_status(self.root, manifest)['plans']}

    def test_every_plan_of_an_active_run_waits_on_its_own_even_with_the_same_date(self):
        self.write_plan(plan(run='A-1'), '2026-11-15-a.md', 'A-1')
        self.write_plan(plan(run='B-1'), '2026-11-15-b.md', 'B-1')
        self.assertEqual(self.by_name('pr'), {'2026-11-15-a.md': ['acceptance'], '2026-11-15-b.md': ['acceptance']})
        self.approve('READY', self.git('rev-parse', 'HEAD'), run='B-1')
        self.assertEqual(self.by_name('pr'), {'2026-11-15-a.md': ['acceptance'], '2026-11-15-b.md': []})

    def test_a_plan_without_a_date_in_the_name_is_judged_by_its_own_run(self):
        self.write_plan(plan(run='A-1'), 'bez-daty.md', 'A-1')
        self.write_plan(plan(run='B-1'), '2026-11-15-b.md', 'B-1')
        self.approve('READY', self.git('rev-parse', 'HEAD'), run='B-1')
        self.assertEqual(self.by_name('pr'), {'bez-daty.md': ['acceptance'], '2026-11-15-b.md': []})

    def test_an_older_finished_plan_stays_quiet_next_to_newer_work(self):
        self.write_plan(plan(run='OLD-1'), '2026-10-01-old.md', 'OLD-1')
        self.approve('READY', self.git('rev-parse', 'HEAD'), run='OLD-1')
        self.write_plan(plan('in_progress', steps=['- [x] F1.1 Kod', '- [ ] F1.2 Dalej'], run='NEW-1'), '2026-11-15-new.md', 'NEW-1')
        self.commit('new work')
        self.assertEqual(self.by_name('pr'), {'2026-10-01-old.md': [], '2026-11-15-new.md': []})
        self.write_plan(plan(steps=['- [x] F1.1 Kod', '- [x] F1.2 Zamknięcie'], run='NEW-1'), '2026-11-15-new.md', 'NEW-1')
        self.commit('new work 2')
        self.assertEqual(self.by_name('pr'), {'2026-10-01-old.md': [], '2026-11-15-new.md': []})   # no acceptance step: no signal

    def test_a_plan_whose_run_directory_is_gone_is_history(self):
        self.write_plan(plan(run='GONE-1'), '2026-10-01-gone.md', None)
        self.assertEqual(self.by_name('pr'), {'2026-10-01-gone.md': []})

    def test_an_archived_plan_has_no_signal(self):
        self.write_plan(plan('archived', run='ARC-1'), '2026-09-01-arc.md', 'ARC-1')
        self.assertEqual(self.by_name('pr'), {'2026-09-01-arc.md': []})

    def test_an_invalid_delivery_mode_or_a_missing_run_line_gives_no_signal(self):
        self.write_plan(plan())
        self.assertEqual(self.needs('nonsense'), [])
        self.write_plan(plan().replace('Przebieg: ACC-1\n', ''))
        self.assertEqual(self.needs('pr'), [])


if __name__ == '__main__':
    unittest.main()
