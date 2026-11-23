"""`linkage_status`, its summary and the summary error of `doctor` (CORE-1150-UNLINKED): are the skills of the manifest linked?"""
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import linkage  # noqa: E402
from tests.test_skillsync_dispatch import write_skill  # noqa: E402
from tests.test_skillsync_workflow import WorkflowFixture  # noqa: E402


def symlinks_work():
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        try:
            os.symlink(folder, Path(folder) / 'probe')
        except (OSError, NotImplementedError):
            return False
    return True


class LinkageTests(WorkflowFixture):
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
            (self.root / host / name).mkdir(parents=True, exist_ok=True)
            (self.root / host / name / 'SKILL.md').write_text('x', encoding='utf-8')

    def install_all(self):
        for host in ('.github/skills', '.claude/skills'):
            self.install(host, 'alpha', 'beta')

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

    @unittest.skipUnless(symlinks_work(), 'needs os.symlink')
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
            (self.root / host).mkdir(parents=True, exist_ok=True)
            for index in range(40):
                (self.root / host / f'many{index}').write_text('plain', encoding='utf-8')
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


def report_code(case):
    result = case.cli('doctor')
    return result.returncode


if __name__ == '__main__':
    unittest.main()
