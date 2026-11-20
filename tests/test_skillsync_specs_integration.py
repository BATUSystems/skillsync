"""K7 (CORE-SPECSTORE): a project with `/docs/specs/` and `/docs/plans/` in `.gitignore` and `workflow.specs.tracked: false` keeps the consumers
of a spec and a plan working without a change of code: `readiness`, `status`, `dashboard show`, `spec approve`, `plan approve`, `approvals wait`.
The spec and the plan are untracked AND ignored, so the tests prove that no consumer needs them in git (index, HEAD or history)."""
import json
import os
import shutil
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import needs_sh  # noqa: E402
from test_skillsync_workflow import CLI, WorkflowFixture  # noqa: E402

SPEC = 'docs/specs/2026-11-20-demo.md'
PLAN = 'docs/plans/2026-11-20-demo.md'
HISTORY = {'show', 'log', 'cat-file', 'blame', 'rev-list'}
SECTIONS = '\n## contracts\nK1 x\n\n## risks\nnone known\n\n## evidence\ntests\n'


def spec_text(status='draft'):
    return f'# Spec: demo\n\nStatus: {status}\n{SECTIONS}'


def plan_text(spec_sha, status='draft', checked='[ ]', run='RUN-1'):
    return (f'# Plan: demo\n\nStatus: {status}\nSpec: {SPEC}\nSpec-sha: {spec_sha}\nPrzebieg: {run}\n{SECTIONS}\n'
            f'## Progress\n- {checked} A1 Code\n')


@needs_sh   # the spy `git` is a POSIX shell script
class IgnoredSpecStore(WorkflowFixture):
    def setUp(self):
        super().setUp()
        for name, value in (('HOME', str(self.base)), ('USERPROFILE', str(self.base)), ('XDG_CONFIG_HOME', str(self.base / 'xdg-config')), ('GIT_CONFIG_GLOBAL', os.devnull),
                            ('GIT_CONFIG_NOSYSTEM', '1')):
            patcher = mock.patch.dict(os.environ, {name: value})
            patcher.start()
            self.addCleanup(patcher.stop)
        self.manifest['workflow'] = {'specs': {'tracked': False}}
        self.save()
        self.write('README.md', 'fixture\n')
        self.write('.gitignore', '.skillsync/\n/docs/specs/\n/docs/plans/\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Contract Tests')
        self.git('config', 'user.email', 'tests@' + 'example.invalid')   # composed at run time (leak guard)
        self.git('add', '-A')
        self.git('commit', '-qm', 'fixture baseline')
        self.write(SPEC, spec_text())
        self.write(PLAN, plan_text(self.blob(SPEC)))
        self.assertEqual(self.git('status', '--porcelain'), '')   # both documents are ignored: git does not see them
        self.assertEqual(self.git('ls-files', 'docs'), '')

    def run_cli(self, *words, timeout=30):
        """`words` = the command words (`spec approve`), then the arguments; the project path goes after the command words."""
        count = 2 if words[0] in ('spec', 'plan', 'approvals', 'dashboard') else 1
        return subprocess.run([sys.executable, str(CLI), *words[:count], str(self.root), *words[count:], '--json'],
                              capture_output=True, text=True, timeout=timeout, cwd=self.root)

    def report(self, *words, code=0):
        result = self.run_cli(*words)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return json.loads(result.stdout)

    def blob(self, name):
        return self.git('hash-object', name)

    def calls_of_git(self):
        """A `git` in front of PATH that records every call (then runs the real one): the proof that no consumer reads the documents from history."""
        real = shutil.which('git')
        bin_dir = self.base / 'git-spy'
        bin_dir.mkdir()
        self.calls = self.base / 'git-calls.log'
        script = bin_dir / 'git'
        script.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{self.calls}"\nexec "{real}" "$@"\n', encoding='utf-8')
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        patcher = mock.patch.dict(os.environ, {'PATH': str(bin_dir) + os.pathsep + os.environ['PATH']})
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_no_history_of_documents(self):
        lines = self.calls.read_text(encoding='utf-8').splitlines() if self.calls.exists() else []
        for line in lines:
            if HISTORY & set(line.split()):
                self.assertNotRegex(line, r'docs/(specs|plans)', 'a consumer reads the document from git history: ' + line)

    # --- spec approve / plan approve / approvals wait ---------------------------------------------------------------------------------------------

    def test_spec_and_plan_approve_work_and_report_the_blob_sha_of_the_ignored_file(self):
        self.calls_of_git()
        report = self.report('spec', 'approve', SPEC)
        self.assertEqual((report['from'], report['to']), ('draft', 'approved'))
        self.assertEqual(report['sha'], self.blob(SPEC))
        self.assertIn('Status: approved', (self.root / SPEC).read_text(encoding='utf-8'))
        report = self.report('plan', 'approve', PLAN)
        self.assertEqual((report['from'], report['to']), ('draft', 'approved'))
        self.assertEqual(report['sha'], self.blob(PLAN))
        self.assertEqual(self.git('status', '--porcelain'), '')   # still invisible to git
        self.assert_no_history_of_documents()

    def test_the_expected_sha_guards_an_ignored_spec_too(self):
        stale = self.blob(SPEC)
        self.write(SPEC, spec_text() + 'edited\n')
        result = self.run_cli('spec', 'approve', SPEC, '--expect-sha', stale)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('sha mismatch', result.stdout + result.stderr)
        self.report('spec', 'approve', SPEC, '--expect-sha', self.blob(SPEC))

    def test_approvals_wait_ends_on_the_approved_status_of_an_ignored_document(self):
        self.calls_of_git()
        self.assertEqual(self.run_cli('approvals', 'wait', '--word', 'GO plan', '--file', SPEC, '--timeout', '1', '--interval', '1').returncode, 3)
        self.report('spec', 'approve', SPEC)
        found = self.report('approvals', 'wait', '--word', 'GO plan', '--file', SPEC, '--timeout', '5', '--interval', '1')['approval']
        self.assertEqual((found['kind'], found['status'], found['sha']), ('status', 'approved', self.blob(SPEC)))
        self.report('plan', 'approve', PLAN)
        found = self.report('approvals', 'wait', '--word', 'GO implement', '--file', PLAN, '--timeout', '5', '--interval', '1')['approval']
        self.assertEqual((found['status'], found['sha']), ('approved', self.blob(PLAN)))
        self.assert_no_history_of_documents()

    # --- status / dashboard show / readiness ------------------------------------------------------------------------------------------------------

    def test_status_lists_the_ignored_plan_and_spec_with_their_statuses_and_the_binding(self):
        self.calls_of_git()
        self.report('spec', 'approve', SPEC)
        self.write(PLAN, plan_text(self.blob(SPEC)))   # the plan binds the approved version of the spec
        self.report('plan', 'approve', PLAN)
        data = self.report('status')
        (plan,) = data['plans']
        (spec,) = data['specs']
        self.assertEqual((plan['path'], plan['status'], plan['spec']), (PLAN, 'approved', SPEC))
        self.assertEqual((spec['path'], spec['status']), (SPEC, 'approved'))
        self.assertEqual(data['findings'], [])
        self.assert_no_history_of_documents()

    def test_status_reports_the_implemented_spec_and_the_done_plan_without_history(self):
        self.calls_of_git()
        self.write(SPEC, spec_text('implemented'))
        self.write(PLAN, plan_text(self.blob(SPEC), status='done', checked='[x]'))
        data = self.report('status')
        self.assertEqual(data['specs'][0]['status'], 'implemented')
        self.assertEqual(data['plans'][0]['status'], 'done')
        self.assertEqual(data['plans'][0]['spec'], SPEC)
        self.assert_no_history_of_documents()

    def test_dashboard_show_reads_the_ignored_plan_and_the_spec_sha(self):
        self.calls_of_git()
        self.report('spec', 'approve', SPEC)
        spec_sha = self.blob(SPEC)
        self.write(PLAN, plan_text(spec_sha))
        view = self.report('dashboard', 'show', '--plan', PLAN)
        self.assertEqual((view['plan']['path'], view['plan']['fileSha']), (PLAN, self.blob(PLAN)))
        self.assertEqual((view['spec']['path'], view['spec']['exists'], view['spec']['status'], view['spec']['sha']),
                         (SPEC, True, 'approved', spec_sha))
        self.assertEqual(view['plans'][0]['path'], PLAN)
        self.assert_no_history_of_documents()

    def test_readiness_binds_the_ignored_spec_to_the_ignored_plan(self):
        self.calls_of_git()
        report = self.report('readiness', '--files', 'README.md', '--plan', PLAN, '--risk', 'low')
        self.assertEqual((report['status'], report['spec'], report['issues']), ('PASS', SPEC, []))
        self.assertEqual(sorted(report['sections']), ['contracts', 'evidence', 'risks'])
        (root_file,) = report['files']
        self.assertTrue(root_file['exists'])
        (self.root / SPEC).unlink()
        broken = self.report('readiness', '--files', 'README.md', '--plan', PLAN, '--risk', 'low', code=2)
        self.assertTrue(any('Spec rejected: not found' in issue for issue in broken['issues']), broken)
        self.assert_no_history_of_documents()

    def test_the_documents_stay_invisible_to_git_after_every_command(self):
        for command in (('status',), ('dashboard', 'show'), ('readiness', '--files', 'README.md', '--plan', PLAN, '--risk', 'low')):
            self.report(*command)
        self.report('spec', 'approve', SPEC)
        self.report('plan', 'approve', PLAN)
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.git('diff', '--cached', '--name-only'), '')


if __name__ == '__main__':
    unittest.main()
