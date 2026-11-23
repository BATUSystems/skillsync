"""`linkage_status`, its summary and the summary error of `doctor` (CORE-1150-UNLINKED): are the skills of the manifest linked?"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillsync_workflow import linkage  # noqa: E402
from tests.test_skillsync_dispatch import write_skill  # noqa: E402
from tmp_paths import child, descend  # noqa: E402
from layers_helpers import needs_os_symlinks  # noqa: E402
from tests.test_skillsync_workflow import CLI, WorkflowFixture  # noqa: E402


class ProjectBase(WorkflowFixture):
    """A project whose manifest declares two library skills and links none of them."""
    def setUp(self):
        super().setUp()
        write_skill(self.library, 'alpha')
        write_skill(self.library, 'beta')
        self.manifest['skills'] = ['alpha', 'beta']
        self.save()

    def status(self):
        return linkage.linkage_status(self.root, self.manifest, self.layerset())

    def install(self, host, *names):
        for name in names:
            folder = child(descend(self.root, host), name)
            folder.mkdir(parents=True, exist_ok=True)
            child(folder, 'SKILL.md').write_text('x', encoding='utf-8')

    def install_all(self):
        for host in ('.github/skills', '.claude/skills'):
            self.install(host, 'alpha', 'beta')



class LinkageTests(ProjectBase):
    def test_nothing_linked_is_unlinked_for_every_enabled_host_and_names_the_fix(self):
        result = self.status()
        self.assertEqual(result['state'], 'unlinked')
        self.assertEqual(set(result['hosts']), {'copilot', 'claude'})   # codex is off without the workflow skill
        for info in result['hosts'].values():
            self.assertEqual(info['missing'], ['alpha', 'beta'])
        self.assertTrue(result['fix'].startswith('skillsync link '))
        lines = linkage.summary(result)
        self.assertEqual([directory for directory, _line in lines], ['.github/skills', '.claude/skills'])
        self.assertIn('Host claude: 2 of 2 skills from the manifest are not linked (alpha, beta); run `skillsync link ', lines[1][1])

    def test_a_project_skill_alone_in_the_host_directory_is_still_unlinked(self):
        self.install('.claude/skills', 'own-project-skill')
        self.assertEqual(self.status()['hosts']['claude']['missing'], ['alpha', 'beta'])

    def test_partial_and_linked(self):
        self.install('.claude/skills', 'alpha')
        self.assertEqual(self.status()['state'], 'partial')
        self.install_all()
        result = self.status()
        self.assertEqual(result['state'], 'linked')
        self.assertEqual(linkage.summary(result), [])

    @needs_os_symlinks
    def test_a_dangling_symlink_counts_as_not_linked_and_a_working_one_as_linked(self):
        self.install_all()
        gone = self.root / '.claude' / 'skills' / 'alpha'
        for item in gone.iterdir():
            item.unlink()
        gone.rmdir()
        os.symlink(self.base / 'does-not-exist', gone)
        self.assertEqual(self.status()['hosts']['claude']['missing'], ['alpha'])
        gone.unlink()
        os.symlink(self.root / '.claude' / 'skills' / 'beta', gone)
        self.assertEqual(self.status()['state'], 'linked')

    def test_a_copy_that_differs_from_the_source_counts_as_linked(self):
        self.install_all()
        (self.root / '.claude' / 'skills' / 'alpha' / 'SKILL.md').write_text('edited by hand', encoding='utf-8')
        self.assertEqual(self.status()['state'], 'linked')   # currency is `doctor`'s business

    def test_a_regular_file_tracked_as_a_link_counts_as_not_linked(self):
        self.install_all()
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        path = self.root / '.claude' / 'skills' / 'alpha'
        for item in path.iterdir():
            item.unlink()
        path.rmdir()
        path.write_bytes(b'../../somewhere/alpha')
        blob = subprocess.run(['git', 'hash-object', '-w', '--no-filters', str(path)], cwd=self.root, capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(['git', 'update-index', '--add', '--cacheinfo', f'120000,{blob},.claude/skills/alpha'], cwd=self.root, check=True)
        self.assertEqual(self.status()['hosts']['claude']['missing'], ['alpha'])
        self.assertEqual(self.status()['hosts']['copilot']['missing'], [])

    def test_a_skill_without_a_source_in_the_layers_is_not_expected(self):
        self.manifest['skills'] = ['alpha', 'ghost']
        self.save()
        self.assertEqual(self.status()['hosts']['claude'], {'dir': '.claude/skills', 'expected': 1, 'missing': ['alpha']})

    def test_claude_off_removes_the_host(self):
        self.manifest['claude'] = False
        self.save()
        self.assertEqual(set(self.status()['hosts']), {'copilot'})

    def test_codex_exclusion_from_the_manifest_and_from_a_layer_file(self):
        self.manifest['codex'] = {'enabled': True, 'exclude_skills': ['alpha']}
        self.save()
        self.assertEqual(self.status()['hosts']['codex'], {'dir': '.agents/skills', 'expected': 1, 'missing': ['beta']})
        self.manifest['codex'] = {'enabled': True}
        self.save()
        (self.library / 'codex.yaml').write_text(yaml.safe_dump({'exclude_skills': ['beta']}), encoding='utf-8')
        self.assertEqual(self.status()['hosts']['codex'], {'dir': '.agents/skills', 'expected': 1, 'missing': ['alpha']})

    def test_codex_is_on_by_default_only_with_the_workflow_skill(self):
        self.assertNotIn('codex', self.status()['hosts'])
        write_skill(self.library, 'feature', adapters=True)
        self.manifest['skills'] = ['feature']
        self.save()
        self.assertIn('codex', self.status()['hosts'])

    def test_unknown_without_a_manifest_and_on_a_layer_error(self):
        self.assertIsNone(linkage.linkage_status(self.root, {}, self.layerset()))
        with mock.patch.object(linkage, 'expand_project_skills', side_effect=linkage._layers.LayerError('x')):
            self.assertIsNone(self.status())

    def test_plain_files_cost_one_git_call_per_host_not_one_per_file(self):
        for index in range(40):
            write_skill(self.library, f'many{index}')
        self.manifest['skills'] = [f'many{index}' for index in range(40)]
        self.save()
        for host in ('.github/skills', '.claude/skills'):
            descend(self.root, host).mkdir(parents=True, exist_ok=True)
            for index in range(40):
                child(descend(self.root, host), f'many{index}').write_text('plain', encoding='utf-8')
        layerset = self.layerset()   # resolving the layers is not what is measured
        real = subprocess.run
        calls = []
        with mock.patch.object(linkage.subprocess, 'run', side_effect=lambda *a, **k: (calls.append(a), real(*a, **k))[1]):
            result = linkage.linkage_status(self.root, self.manifest, layerset)
        self.assertEqual(result['state'], 'linked')
        self.assertLessEqual(len(calls), 2)   # one `git ls-files -s` per host; no `checked_out_link` for files git does not track as links

    def test_route_and_status_carry_the_fields_only_when_something_is_not_linked(self):
        for command in ('route', 'status'):
            report = self.report(command)
            self.assertEqual(report['linkage']['state'], 'unlinked', command)
            self.assertIn('Skills from the manifest are not linked (copilot 2/2, claude 2/2). Run: skillsync link ', report['linkageNote'], command)
        self.install_all()
        for command in ('route', 'status'):
            report = self.report(command)
            self.assertNotIn('linkage', report, command)
            self.assertNotIn('linkageNote', report, command)

    def test_status_stays_pass_without_the_field_when_the_layers_cannot_be_resolved(self):
        self.machine_config.write_text('layers: [', encoding='utf-8')
        report = self.report('status')
        self.assertEqual(report['status'], 'PASS')
        self.assertNotIn('linkage', report)

    @needs_os_symlinks
    def test_the_missing_set_equals_the_per_skill_errors_of_doctor_for_every_not_linked_state(self):
        write_skill(self.library, 'gamma')
        self.manifest['skills'] = ['alpha', 'beta', 'gamma']
        self.save()
        self.install('.github/skills', 'alpha', 'beta', 'gamma')
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        folder = descend(self.root, '.claude/skills')
        folder.mkdir(parents=True, exist_ok=True)
        child(folder, 'alpha').write_bytes(b'../../elsewhere/alpha')   # a link checked out as text
        blob = subprocess.run(['git', 'hash-object', '-w', '--no-filters', str(child(folder, 'alpha'))], cwd=self.root, capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(['git', 'update-index', '--add', '--cacheinfo', f'120000,{blob},.claude/skills/alpha'], cwd=self.root, check=True)
        os.symlink(self.base / 'does-not-exist', child(folder, 'beta'))   # dangling; gamma is absent
        missing = set(self.status()['hosts']['claude']['missing'])
        self.assertEqual(missing, {'alpha', 'beta', 'gamma'})
        report = self.report('doctor', code=1)
        per_skill = {Path(f['path']).name for f in report['findings']
                     if '.claude' in f['path'].replace('\\', '/')
                     and f['reason'].startswith(('Missing installed skill', 'Installed skill symlink points to a target that does not exist',
                                                 'A regular file stands where the skill link should be'))}
        self.assertEqual(per_skill, missing)

    def test_the_fast_path_of_the_notice_counts_only_what_exists_and_ignores_names_that_are_not_plain(self):
        present = linkage._declared_skills_present
        self.assertIs(present(self.root), False)
        self.install_all()
        self.assertIs(present(self.root), True)
        self.manifest['skills'] = ['agentflow', 'go-jira', '@backend', 'angular-*']
        self.save()
        self.assertIsNone(present(self.root))   # nothing plain to check: no notice from the fast path, `doctor` and `route` still say it
        self.manifest['skills'] = ['alpha', '@backend']
        self.save()
        self.assertIs(present(self.root), True)

    @needs_os_symlinks
    def test_a_dangling_link_does_not_count_as_present_in_the_fast_path(self):
        folder = descend(self.root, '.github/skills')
        folder.mkdir(parents=True, exist_ok=True)
        os.symlink(self.base / 'does-not-exist', child(folder, 'alpha'))
        self.manifest['skills'] = ['alpha']
        self.save()
        self.assertIs(linkage._declared_skills_present(self.root), False)

    def test_a_closed_or_missing_stderr_does_not_stop_the_command(self):
        with mock.patch.object(linkage, 'notice', return_value='skillsync: x'), mock.patch.object(linkage.sys, 'stderr', None):
            linkage.print_notice('layers', None, [])   # must not raise
        broken = mock.Mock()
        broken.write.side_effect = BrokenPipeError()
        with mock.patch.object(linkage, 'notice', return_value='skillsync: x'), mock.patch.object(linkage.sys, 'stderr', broken):
            linkage.print_notice('layers', None, [])

    def test_doctor_puts_one_summary_per_host_before_the_per_skill_errors(self):
        report = self.report('doctor', code=1)
        reasons = [f['reason'] for f in report['findings']]
        first = next(i for i, text in enumerate(reasons) if text.startswith('Host '))
        per_skill = next(i for i, text in enumerate(reasons) if text.startswith('Missing installed skill'))
        self.assertLess(first, per_skill)
        summaries = [f for f in report['findings'] if f['reason'].startswith('Host ')]
        self.assertEqual([f['path'] for f in summaries], ['.github/skills', '.claude/skills'])
        self.assertTrue(all(f['level'] == 'error' for f in summaries))
        # the summary lists exactly the skills that have a per-skill "Missing installed skill" error on that host
        missing = {Path(f['path']).name for f in report['findings'] if f['reason'].startswith('Missing installed skill') and '.claude' in f['path'].replace('\\', '/')}
        self.assertEqual(missing, {'alpha', 'beta'})

    def test_doctor_has_no_summary_when_everything_is_linked(self):
        self.install_all()
        report = self.report('doctor', code=report_code(self))
        self.assertFalse([f for f in report['findings'] if f['reason'].startswith('Host ')])


class NoticeTests(ProjectBase):
    """The one stderr line of any other command in a project whose skills are not linked (U-3b)."""
    NOTICE = 'skillsync: Skills from the manifest are not linked'

    def run_cli(self, *args, env=None, cwd=None):
        environment = {**os.environ, **(env or {})}
        return subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, timeout=60, cwd=cwd or self.root, env=environment)

    def test_the_line_goes_to_stderr_once_and_stdout_and_the_exit_code_are_unchanged(self):
        noisy = self.run_cli('layers', 'check')
        quiet = self.run_cli('layers', 'check', env={'SKILLSYNC_NO_LINK_NOTICE': '1'})
        self.assertEqual(noisy.stderr.count(self.NOTICE), 1, noisy.stderr)
        self.assertIn('Run: skillsync link ', noisy.stderr)
        self.assertNotIn(self.NOTICE, noisy.stdout)
        self.assertEqual((noisy.returncode, noisy.stdout), (quiet.returncode, quiet.stdout))
        self.assertNotIn(self.NOTICE, quiet.stderr)

    def test_a_path_argument_selects_the_project_not_the_working_directory(self):
        result = self.run_cli('instructions', self.root, '--dry-run', cwd=self.base)
        self.assertEqual(result.stderr.count(self.NOTICE), 1, result.stderr)

    def test_no_line_for_json_for_commands_that_say_it_themselves_and_when_linked(self):
        self.assertNotIn(self.NOTICE, self.run_cli('layers', 'check', '--json').stderr)
        for command in ('doctor', 'route', 'status'):
            result = self.run_cli(command, self.root)
            self.assertNotIn(self.NOTICE, result.stderr, command)
        self.install_all()
        self.assertNotIn(self.NOTICE, self.run_cli('layers', 'check').stderr)

    def test_no_line_outside_a_project_and_json_stdout_stays_parseable_for_pipes(self):
        self.assertNotIn(self.NOTICE, self.run_cli('layers', 'check', cwd=self.base).stderr)
        report = self.run_cli('doctor', self.root, '--json')
        json.loads(report.stdout)
        self.assertNotIn(self.NOTICE, report.stdout + report.stderr)


def report_code(case):
    result = case.cli('doctor')
    return result.returncode


if __name__ == '__main__':
    unittest.main()
