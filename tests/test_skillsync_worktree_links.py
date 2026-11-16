"""161-I: a project that TRACKS the skill links of `sync.method: symlink` in git. After `link` such an entry differs from the commit (another
target), and `readiness` / `review` take the changed files from git: a link that lies in the project is not an escape from it. A fresh
`git worktree` of such a project has the links of the commit (the target can be gone): `doctor` says what to do, `link` repairs it, `checkpoint`
and `readiness` work in the worktree before and after. The skill is composed of two layers (core + org), as in a real project."""
import json
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layers_helpers import LayersCase, commit, git, needs_os_symlinks, write  # noqa: E402
from skillsync_workflow.common import checked_out_link  # noqa: E402
from skillsync_workflow.fsutil import rmtree_force  # noqa: E402   (git objects are read-only on Windows)


@needs_os_symlinks
class TrackedLinksFixture(LayersCase):
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

    def repoint(self, also_a_file=True):
        """What `link` does after a layer changed: the tracked link points to another composed target."""
        other = self.committed_target.parent / 'next'
        shutil.copytree(self.committed_target, other, symlinks=True)
        self.link.unlink()
        os.symlink(other, self.link)
        if also_a_file:
            write(self.proj / 'a.txt', 'changed\n')

    def run_cli(self, cwd, *args):
        process = self.cli(*args, cwd=cwd)
        try:
            return process.returncode, json.loads(process.stdout)
        except ValueError:
            return process.returncode, {'text': (process.stdout + process.stderr).strip()}



@needs_os_symlinks
class TrackedSkillLinks(TrackedLinksFixture):
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
        rmtree_force(self.committed_target)   # the target of the commit is gone (another machine, a cleaned cache)
        wt = self.worktree()
        link = wt / '.github/skills/foo'
        dangling, degraded = link.is_symlink() and not link.exists(), checked_out_link(link)
        self.assertTrue(dangling or degraded, 'git recreated the link (its target is gone) or wrote the target as a file (core.symlinks off)')
        code, report = self.run_cli(wt, 'doctor', str(wt))
        self.assertEqual((code, report['status']), (1, 'FAIL'), report)
        errors = [item['reason'] for item in report['findings'] if item['level'] == 'error']
        self.assertTrue(errors and all('skillsync link' in reason for reason in errors), errors)
        self.assertEqual(self.run_cli(wt, 'link', str(wt))[0], 0)
        self.assertTrue((wt / '.github/skills/foo').exists())
        code, report = self.run_cli(wt, 'doctor', str(wt))
        self.assertEqual((code, report['status']), (0, 'PASS'), report)

    def test_a_worktree_made_with_core_symlinks_off_has_files_and_doctor_names_the_repair(self):
        wt = self.work / 'wt-nolinks'
        git(self.proj, '-c', 'core.symlinks=false', 'worktree', 'add', '-q', '-b', 'work-nolinks', str(wt))   # as a Windows checkout without links
        link = wt / '.github/skills/foo'
        self.assertTrue(link.is_file() and not link.is_symlink(), 'git wrote the target as a regular file')
        self.assertTrue(checked_out_link(link))
        code, report = self.run_cli(wt, 'doctor', str(wt))
        errors = [item['reason'] for item in report['findings'] if item['level'] == 'error']
        self.assertEqual((code, report['status']), (1, 'FAIL'), report)
        self.assertTrue(errors and all('skillsync link' in reason for reason in errors), errors)
        self.assertEqual(self.run_cli(wt, 'link', str(wt))[0], 0)
        self.assertFalse(link.is_file() and not link.is_symlink(), 'link replaced the file')
        self.assertEqual(self.run_cli(wt, 'doctor', str(wt))[1]['status'], 'PASS')

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
        self.assertIn('rm --cached --', warnings[0]['reason'])
        self.assertIn('.github/skills/foo', warnings[0]['reason'])
        self.assertNotIn('.skillsync.yaml', warnings[0]['reason'])   # the manifest may be tracked on purpose
        git(self.proj, 'rm', '-q', '--cached', '-r', '.github/skills', '.claude/skills')
        code, report = self.run_cli(self.proj, 'doctor', str(self.proj))
        self.assertEqual([item for item in report['findings'] if 'tracked in git' in item['reason']], [])

    def test_the_untrack_command_of_the_warning_runs_as_it_stands(self):
        code, report = self.run_cli(self.proj, 'doctor', str(self.proj))
        warning = next(item['reason'] for item in report['findings'] if 'tracked in git' in item['reason'])
        commands = [line for line in warning.splitlines() if line.startswith('git ')]
        self.assertTrue(commands and '...' not in warning, warning)
        for line in commands:
            self.assertEqual(subprocess.run(line, shell=True, cwd=str(self.work), capture_output=True, text=True).returncode, 0, line)
        tracked = [name for name in git(self.proj, 'ls-files', '-ci', '--exclude-standard').stdout.split() if name.startswith(('.github/skills/', '.claude/skills/'))]
        self.assertEqual(tracked, [])
        self.assertTrue(self.link.is_symlink(), 'the files stay on disk')

    def test_a_tracked_instructions_file_that_the_project_does_not_ignore_is_not_reported(self):
        write(self.proj / 'CLAUDE.md', '# own instructions\n')
        git(self.proj, 'add', 'CLAUDE.md')
        commit(self.proj, 'own CLAUDE.md')
        code, report = self.run_cli(self.proj, 'doctor', str(self.proj))
        warning = next(item['reason'] for item in report['findings'] if 'tracked in git' in item['reason'])
        self.assertNotIn('CLAUDE.md', warning)
        self.assertEqual(git(self.proj, 'check-ignore', 'CLAUDE.md', check=False).returncode, 1)   # `ls-files -ci` lists ignored files only

    def test_a_tracked_instructions_file_that_the_project_ignores_is_reported_with_a_condition(self):
        write(self.proj / 'CLAUDE.md', '# generated\n')
        git(self.proj, 'add', '-f', 'CLAUDE.md')
        write(self.proj / '.gitignore', (self.proj / '.gitignore').read_text(encoding='utf-8') + 'CLAUDE.md\n')
        commit(self.proj, 'tracked ignored CLAUDE.md')
        code, report = self.run_cli(self.proj, 'doctor', str(self.proj))
        warning = next(item['reason'] for item in report['findings'] if 'tracked in git' in item['reason'])
        self.assertIn('CLAUDE.md', warning)
        self.assertIn('only if `skillsync link` generates them', warning)

    def test_doctor_outside_git_has_no_tracking_warning(self):
        rmtree_force(self.proj / '.git')
        code, report = self.run_cli(self.proj, 'doctor', str(self.proj))
        self.assertEqual([item for item in report['findings'] if 'tracked in git' in item['reason']], [])


@needs_os_symlinks
class GuardAndSelectionWithTrackedLinks(TrackedLinksFixture):
    """`guard`, `smart-test` and `validate` with a tracked link that was repointed: the link is classified by its own path, never opened."""
    MARK = 'FORBIDDEN-MARK'

    def setUp(self):
        super().setUp()
        manifest = self.proj / '.github' / '.skillsync.yaml'
        manifest.write_text(manifest.read_text(encoding='utf-8') + 'workflow:\n  guardrails:\n    rules:\n'
                            f'      - {{id: no-mark, paths: ["**"], forbidden: ["{self.MARK}"]}}\n', encoding='utf-8')
        self.target = self.tmp_file(f'content with {self.MARK}\n')   # outside the project: the guard must never read it
        os.symlink(self.target, self.proj / 'linked.txt')
        os.symlink(self.target.parent, self.proj / 'outdir')
        write(self.proj / 'plain.txt', 'ok\n')
        git(self.proj, 'add', '-f', '-A')
        commit(self.proj, 'guard fixtures')   # from here on `git status` shows only what a test changes

    def tmp_file(self, text):
        path = self.work / 'outside' / 'target.txt'
        write(path, text)
        return path

    def guard(self, *files):
        return self.run_cli(self.proj, 'guard', str(self.proj), '--files', *files, '--run')

    def test_the_guard_skips_a_changed_link_without_reading_its_target(self):
        code, report = self.guard('linked.txt')
        self.assertEqual((code, report['status']), (0, 'PASS'), report)   # every covered file is a link: PASS, with the reason and the skip in the report
        self.assertEqual(report['scanned'], [])
        self.assertIn('No content was checked', report['reason'])
        self.assertEqual(report['skipped'], [{'file': 'linked.txt', 'reason': 'symlink'}])
        self.assertNotIn(self.MARK, json.dumps(report))
        self.assertEqual(report['findings'], [])

    def test_a_skipped_link_does_not_hide_a_finding_in_another_file(self):
        write(self.proj / 'bad.txt', f'{self.MARK}\n')
        code, report = self.guard('linked.txt', 'bad.txt', 'plain.txt')
        self.assertEqual((code, report['status']), (1, 'FAIL'), report)
        self.assertEqual([item['file'] for item in report['findings']], ['bad.txt'])
        self.assertEqual(report['scanned'], ['bad.txt', 'plain.txt'])
        self.assertEqual(report['skipped'], [{'file': 'linked.txt', 'reason': 'symlink'}])

    def test_a_clean_file_next_to_a_skipped_link_is_pass_with_the_skip_visible(self):
        code, report = self.guard('linked.txt', 'plain.txt')
        self.assertEqual((code, report['status'], report['scanned']), (0, 'PASS', ['plain.txt']), report)
        self.assertEqual(report['skipped'], [{'file': 'linked.txt', 'reason': 'symlink'}])

    def test_a_report_without_links_has_no_skipped_key(self):
        code, report = self.guard('plain.txt')
        self.assertNotIn('skipped', report)

    def test_a_link_in_the_middle_of_the_path_is_still_refused(self):
        code, report = self.guard('outdir/target.txt')
        self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
        self.assertIn('symlinks are forbidden', report['reason'])

    def test_smart_test_classifies_a_repointed_tracked_link_by_its_own_path(self):
        self.repoint()
        code, report = self.run_cli(self.proj, 'smart-test', str(self.proj))
        self.assertNotIn('przez dowiązanie', json.dumps(report.get('reasons', []), ensure_ascii=False), report)
        self.assertIn('.github/skills/foo', report.get('files', []), report)
        code, report = self.run_cli(self.proj, 'smart-test', str(self.proj), '--files', '.github/skills/foo')
        self.assertNotIn('przez dowiązanie', json.dumps(report.get('reasons', []), ensure_ascii=False), report)

    def test_smart_test_still_refuses_a_link_directory_in_the_middle(self):
        code, report = self.run_cli(self.proj, 'smart-test', str(self.proj), '--files', 'outdir/target.txt')
        self.assertEqual((code, report['status']), (2, 'BLOCKED'), report)
        self.assertIn('przez dowiązanie', json.dumps(report['reasons'], ensure_ascii=False))

    def test_validate_is_not_blocked_when_only_repointed_links_changed(self):
        self.repoint(also_a_file=False)
        self.assertEqual([line for line in git(self.proj, 'status', '--short').stdout.splitlines() if line.startswith(' M')], [' M .github/skills/foo'])
        code, report = self.run_cli(self.proj, 'validate', str(self.proj), '--profile', 'quick', '--run')
        self.assertNotIn('Required success evidence missing', json.dumps(report), report)
        self.assertNotEqual(report.get('status'), 'BLOCKED', report)

    def test_a_plain_file_next_to_repointed_links_is_still_checked_by_validate(self):
        self.repoint(also_a_file=False)
        write(self.proj / 'bad.txt', f'{self.MARK}\n')
        code, report = self.run_cli(self.proj, 'validate', str(self.proj), '--profile', 'quick', '--run')
        self.assertEqual(report['status'], 'FAIL', report)

    def test_the_guard_refuses_a_link_in_the_middle_of_the_path_also_without_run(self):
        process = self.cli('guard', str(self.proj), '--files', 'outdir/target.txt', cwd=self.proj)
        self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
        self.assertIn('symlinks are forbidden', process.stdout)

    def test_the_guard_works_when_the_project_root_is_a_link(self):
        alias = self.work / 'root-alias'
        os.symlink(self.proj, alias, target_is_directory=True)
        code, report = self.run_cli(self.work, 'guard', str(alias), '--files', 'linked.txt', 'plain.txt', '--run')
        self.assertEqual((code, report['status'], report['scanned']), (0, 'PASS', ['plain.txt']), report)
        self.assertEqual(report['skipped'], [{'file': 'linked.txt', 'reason': 'symlink'}])

    def test_validate_with_guardrails_does_not_block_on_a_repointed_tracked_link(self):
        self.repoint()
        code, report = self.run_cli(self.proj, 'validate', str(self.proj), '--profile', 'quick', '--run')
        self.assertNotIn('Workflow artifact symlinks are forbidden', json.dumps(report), report)
        self.assertNotEqual(report.get('status'), 'BLOCKED', report)


class DegradedLinks(LayersCase):
    """The state of a project that tracks its skill links after a clone or a `git worktree add` on a system without `core.symlinks`: git tracks
    the entry as a link (mode 120000) but the working tree has a regular FILE with the target as text. No link has to be creatable here: the
    state is made with the index, so the tests run on every system."""
    PATHS = ('.github/skills/foo', '.claude/skills/foo')

    def setUp(self):
        super().setUp()
        core = self.layer_dir('core', 'core', {'skills/foo/SKILL.md': '---\nname: foo\ndescription: d\n---\nfoo from core\n'})
        self.write_config([('core', 'core', core)], [(['**'], ['core'])])
        self.proj = self.project('clients/acme/app')
        write(self.proj / '.github' / '.skillsync.yaml', 'skills: [foo]\n')
        git(self.proj, 'init', '-q', '-b', 'main')
        git(self.proj, 'config', 'user.email', 'dev@example.test')
        git(self.proj, 'config', 'user.name', 'Dev')
        write(self.proj / 'a.txt', 'x\n')
        git(self.proj, 'add', '-A')
        commit(self.proj, 'base')
        for rel in self.PATHS:   # no earlier `link`: a copy-mode state of an earlier run would make the files a conflict, not a degraded link
            self.degrade(rel, 120000)
        commit(self.proj, 'tracked skill links')

    def degrade(self, rel, mode):
        """Replace the installed skill by a regular file with a target text, tracked in the index with `mode` (120000: a link)."""
        path = self.proj / rel
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            rmtree_force(path)
        text = '../../../elsewhere/skills/foo'
        write(path, text)
        blob = git(self.proj, 'hash-object', '-w', '--stdin', input=text).stdout.strip()
        git(self.proj, 'update-index', '--add', '--cacheinfo', f'{mode},{blob},{rel}')

    def doctor(self):
        process = self.cli('doctor', str(self.proj), '--json', cwd=self.proj)
        report = json.loads(process.stdout)
        return process.returncode, report, {item['path'].replace('\\', '/').split('clients/acme/app/')[-1]: item['reason']
                                            for item in report['findings'] if item['level'] == 'error'}

    def test_doctor_names_the_repair_for_a_link_that_git_checked_out_as_a_file(self):
        code, report, errors = self.doctor()
        self.assertEqual((code, report['status']), (1, 'FAIL'), report)
        for rel in self.PATHS:
            self.assertIn('A regular file stands where the skill link should be', errors[rel])
            self.assertIn('skillsync link', errors[rel])

    def test_link_replaces_the_degraded_links_and_doctor_passes(self):
        process = self.cli('link', str(self.proj), cwd=self.proj)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertNotIn('not managed by skillsync', process.stdout + process.stderr)
        for rel in self.PATHS:
            path = self.proj / rel
            self.assertTrue(path.is_symlink() or path.is_dir(), f'{rel} is still a plain file')
        code, report, errors = self.doctor()
        self.assertEqual((code, report['status'], errors), (0, 'PASS', {}), report)

    def test_a_tracked_link_that_the_user_replaced_by_an_own_file_is_left_alone(self):
        path = self.proj / '.claude/skills/foo'
        path.write_text('my own notes, not the target of the link\n', encoding='utf-8')   # the index still says: a link with another blob
        self.assertFalse(checked_out_link(path))
        process = self.cli('link', str(self.proj), cwd=self.proj)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn('not managed by skillsync', process.stdout + process.stderr)
        self.assertEqual(path.read_text(encoding='utf-8'), 'my own notes, not the target of the link\n')
        code, report, errors = self.doctor()
        self.assertIn('Invalid installed skill copy', errors['.claude/skills/foo'])

    def test_dry_run_changes_nothing_and_says_what_it_would_do(self):
        before = {rel: (self.proj / rel).read_text(encoding='utf-8') for rel in self.PATHS}
        process = self.cli('link', str(self.proj), '--dry-run', cwd=self.proj)
        out = process.stdout + process.stderr
        self.assertEqual(process.returncode, 0, out)
        for rel in self.PATHS:
            self.assertIn(f'[dry-run] Would replace: {self.proj / rel}', out)
            path = self.proj / rel
            self.assertTrue(path.is_file() and not path.is_symlink() and path.read_text(encoding='utf-8') == before[rel], rel)

    def git_calls(self, *args):
        trace = self.work / 'git-trace.txt'
        trace.write_text('', encoding='utf-8')
        process = self.cli(*args, cwd=self.proj, env={'GIT_TRACE': str(trace)})
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        text = trace.read_text(encoding='utf-8', errors='replace')
        return text.count('ls-files -s'), text.count('hash-object')

    def test_the_check_costs_git_processes_only_for_a_file_that_is_in_the_way(self):
        # degraded: the host that skillsync does not own (.claude) has ONE plain file in the way: one ls-files and one hash-object
        self.assertEqual(self.git_calls('link', str(self.proj)), (1, 1))
        # a normal project (links or copies in place): no process at all
        self.assertEqual(self.git_calls('link', str(self.proj)), (0, 0))
        code, report, errors = self.doctor()
        self.assertEqual((code, errors), (0, {}))

    def test_a_file_of_the_user_that_git_does_not_track_as_a_link_is_left_alone(self):
        self.degrade('.claude/skills/foo', 100644)   # an ordinary tracked file
        commit(self.proj, 'own file')
        before = (self.proj / '.claude/skills/foo').read_text(encoding='utf-8')
        process = self.cli('link', str(self.proj), cwd=self.proj)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertIn('not managed by skillsync', process.stdout + process.stderr)
        self.assertEqual((self.proj / '.claude/skills/foo').read_text(encoding='utf-8'), before)
        self.assertFalse(checked_out_link(self.proj / '.claude/skills/foo'))
        code, report, errors = self.doctor()
        self.assertIn('Invalid installed skill copy', errors['.claude/skills/foo'])


class UntrackMessage(unittest.TestCase):
    """The pure function runs on every system: the quoting is the one of the other command hints (`layers._quote_arg`)."""

    def test_every_path_is_listed_in_lines_and_quoted(self):
        from skillsync_workflow.quality import untrack_message
        paths = [f'.claude/skills/skill {number}' for number in range(30)] + ['CLAUDE.md']
        message = untrack_message('/a project/dir', paths)
        lines = [line for line in message.splitlines() if line.startswith('git ')]
        self.assertEqual(len(lines), 4)
        self.assertNotIn('...', message)
        for path in paths:
            self.assertIn(path, message)
        if os.name != 'nt':   # POSIX: the line round-trips through a shell; Windows quotes with double quotes (cmd.exe has no apostrophes)
            listed = []
            for line in lines:
                words = shlex.split(line)
                self.assertEqual(words[:6], ['git', '-C', '/a project/dir', 'rm', '--cached', '--'])
                listed += words[6:]
            self.assertEqual(listed, paths)
        else:
            self.assertIn('"', lines[0])
        self.assertIn('only if `skillsync link` generates them', message)
        self.assertNotIn('only if `skillsync link` generates them', untrack_message('/p', ['.github/skills/foo']))
        self.assertIn('and 40 more', untrack_message('/p', [f'.claude/skills/s{n}' for n in range(200)]))


if __name__ == '__main__':
    unittest.main()
