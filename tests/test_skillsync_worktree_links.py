"""161-I: a project that TRACKS the skill links of `sync.method: symlink` in git. After `link` such an entry differs from the commit (another
target), and `readiness` / `review` take the changed files from git: a link that lies in the project is not an escape from it. A fresh
`git worktree` of such a project has the links of the commit (the target can be gone): `doctor` says what to do, `link` repairs it, `checkpoint`
and `readiness` work in the worktree before and after. The skill is composed of two layers (core + org), as in a real project."""
import json
import os
import shutil
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layers_helpers import LayersCase, commit, git, needs_os_symlinks, write  # noqa: E402


@needs_os_symlinks
class TrackedSkillLinks(LayersCase):
    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ)   # the tests assert the symlink output: pinned, so SKILLSYNC_NO_SYMLINK=1 of a copy-mode run does not change it
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop('SKILLSYNC_NO_SYMLINK', None)
        core = self.layer_dir('core', 'core', {'skills/foo/SKILL.md': '---\nname: foo\ndescription: d\n---\nfoo from core\n'})
        org = self.layer_dir('acme', 'org', {'skills/foo/extra.md': 'org part\n'})
        self.write_config([('core', 'core', core), ('acme', 'org', org)], [(['**'], ['core', 'acme'])])
        self.proj = self.project('clients/acme/app')
        write(self.proj / '.github' / '.skillsync.yaml', 'skills: [foo]\nsync: {method: symlink}\n')
        git(self.proj, 'init', '-q', '-b', 'main')
        git(self.proj, 'config', 'user.email', 'dev@example.test')
        git(self.proj, 'config', 'user.name', 'Dev')
        write(self.proj / 'a.txt', 'x\n')
        git(self.proj, 'add', '-A')
        commit(self.proj, 'base')
        self.assertEqual(self.cli('link', str(self.proj)).returncode, 0)
        self.link = self.proj / '.github' / 'skills' / 'foo'
        self.assertTrue(self.link.is_symlink())
        git(self.proj, 'add', '-f', '.github', '.claude')   # the generated links are tracked, whatever the project's ignore file says
        commit(self.proj, 'track the skill links')
        self.committed_target = self.link.resolve()

    def repoint(self):
        """What `link` does after a layer changed: the tracked link points to another composed target."""
        other = self.committed_target.parent / 'next'
        shutil.copytree(self.committed_target, other, symlinks=True)
        self.link.unlink()
        os.symlink(other, self.link)
        write(self.proj / 'a.txt', 'changed\n')

    def run_cli(self, cwd, *args):
        process = self.cli(*args, cwd=cwd)
        try:
            return process.returncode, json.loads(process.stdout)
        except ValueError:
            return process.returncode, {'text': (process.stdout + process.stderr).strip()}

    def test_the_fixture_tracks_a_link_that_points_outside_the_project(self):
        self.assertIn('120000', git(self.proj, 'ls-files', '-s', '.github/skills').stdout)
        self.assertFalse(self.committed_target.is_relative_to(self.proj.resolve()))

    def test_readiness_and_review_accept_a_tracked_link_that_was_repointed(self):
        self.repoint()
        self.assertIn(' M .github/skills/foo', git(self.proj, 'status', '--short').stdout)
        for command in ('readiness', 'review'):
            with self.subTest(command=command):
                code, report = self.run_cli(self.proj, command, str(self.proj), '--risk', 'low')
                self.assertNotEqual(report.get('reason'), 'Path escapes project: .github/skills/foo', report)
                self.assertIn(report['status'], ('PASS', 'NOT_RUN'), report)

    def test_the_link_path_given_explicitly_is_a_file_of_the_project(self):
        code, report = self.run_cli(self.proj, 'readiness', str(self.proj), '--risk', 'low', '--files', '.github/skills/foo', 'a.txt')
        self.assertEqual((code, report['status']), (0, 'PASS'), report)

    def test_a_directory_link_to_the_outside_in_the_middle_is_still_an_escape(self):
        os.symlink(self.committed_target.parent, self.proj / 'outside')
        code, report = self.run_cli(self.proj, 'readiness', str(self.proj), '--risk', 'low', '--files', 'outside/foo/SKILL.md')
        self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
        self.assertIn('Path escapes project', report['reason'])

    def worktree(self):
        path = self.work / 'wt'
        git(self.proj, 'worktree', 'add', '-q', '-b', 'work', str(path))
        return path

    def test_a_fresh_worktree_doctor_fails_with_the_command_to_run_and_link_repairs_it(self):
        shutil.rmtree(self.committed_target)   # the target of the commit is gone (another machine, a cleaned cache)
        wt = self.worktree()
        self.assertTrue((wt / '.github/skills/foo').is_symlink() and not (wt / '.github/skills/foo').exists())
        code, report = self.run_cli(wt, 'doctor', str(wt))
        self.assertEqual((code, report['status']), (1, 'FAIL'), report)
        errors = [item['reason'] for item in report['findings'] if item['level'] == 'error']
        self.assertTrue(errors and all('skillsync link' in reason for reason in errors), errors)
        self.assertEqual(self.run_cli(wt, 'link', str(wt))[0], 0)
        self.assertTrue((wt / '.github/skills/foo').exists())
        code, report = self.run_cli(wt, 'doctor', str(wt))
        self.assertEqual((code, report['status']), (0, 'PASS'), report)

    def test_readiness_and_checkpoint_work_in_a_worktree_before_and_after_link(self):
        wt = self.worktree()
        write(wt / 'a.txt', 'z\n')
        write(wt / 'ev.txt', 'log\n')
        for stage in ('before link', 'after link'):
            with self.subTest(stage=stage):
                self.assertEqual(self.run_cli(wt, 'readiness', str(wt), '--risk', 'low')[1]['status'], 'PASS')
                code, report = self.run_cli(wt, 'checkpoint', str(wt), '--run-id', 'r1', '--step', 'F1', '--status', 'PASS',
                                            '--evidence', 'ev.txt', '--next', 'x')
                self.assertEqual((code, report['status']), (0, 'PASS'), report)
            self.assertEqual(self.run_cli(wt, 'link', str(wt))[0], 0)

    def test_a_plan_in_another_tree_is_refused_with_a_message_that_names_the_cause(self):
        wt = self.worktree()
        write(wt / 'docs/plans/p.md', '# P\n\nStatus: draft\n')
        code, report = self.run_cli(self.proj, 'readiness', str(self.proj), '--plan', str(wt / 'docs/plans/p.md'), '--risk', 'high')
        self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
        self.assertIn('The plan lies outside the project', report['reason'])
        self.assertIn('run the command from the directory of the tree that holds the plan', report['reason'])


    def test_the_evidence_must_be_a_file_of_the_project_and_the_message_says_so(self):
        for evidence, expected in (('opis tekstowy', 'Brak pliku: opis tekstowy'), ('/etc/hostname', 'Ścieżka wychodzi poza projekt')):
            with self.subTest(evidence=evidence):
                process = self.cli('checkpoint', str(self.proj), '--run-id', 'r1', '--step', 'F1', '--status', 'PASS', '--evidence', evidence,
                                   '--next', 'x', cwd=self.proj)
                self.assertEqual(process.returncode, 2)
                self.assertIn(expected, process.stderr + process.stdout)
                self.assertIn('--evidence oczekuje ścieżki pliku w projekcie', process.stderr + process.stdout)
        self.assertIn('ścieżki plików z dowodem', self.cli('checkpoint', '--help').stdout)

    def test_doctor_warns_about_tracked_generated_files_with_the_command_to_untrack_them(self):
        code, report = self.run_cli(self.proj, 'doctor', str(self.proj))
        warnings = [item for item in report['findings'] if item['level'] == 'warning' and 'tracked in git' in item['reason']]
        self.assertEqual(len(warnings), 1, report)
        self.assertIn('git rm --cached', warnings[0]['reason'])
        self.assertIn('.github/skills/foo', warnings[0]['reason'])
        self.assertNotIn('.skillsync.yaml', warnings[0]['reason'])   # the manifest may be tracked on purpose
        git(self.proj, 'rm', '-q', '--cached', '-r', '.github/skills', '.claude/skills')
        code, report = self.run_cli(self.proj, 'doctor', str(self.proj))
        self.assertEqual([item for item in report['findings'] if 'tracked in git' in item['reason']], [])

    def test_doctor_outside_git_has_no_tracking_warning(self):
        shutil.rmtree(self.proj / '.git')
        code, report = self.run_cli(self.proj, 'doctor', str(self.proj))
        self.assertEqual([item for item in report['findings'] if 'tracked in git' in item['reason']], [])


if __name__ == '__main__':
    unittest.main()
