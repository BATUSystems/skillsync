"""`skillsync doctor` and `supersedes` (CORE-1140-SUP-TOOLS, SUP-2): the `superseded` warning, `superseded_keep`, the text from the layer, no execution.

All layers are synthetic; the machine state is isolated (`ComposeCase`). The text of `parity` and `migration` is data from a layer: the proof that nothing
is run is a `parity` that would leave a marker file if a shell ever saw it.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import SKILL_TEXT, generator_version, git, write  # noqa: E402
from test_skillsync_layers_doctor import CORE_FILES, MANIFEST, DoctorCase  # noqa: E402
from skillsync_workflow import supersedes  # noqa: E402
from tmp_paths import child, descend  # noqa: E402

ENTRY = {'id': 'measure-local', 'since': '1.14.0', 'replacement': 'skill ui-verify, command measure', 'matches': ['docs/design/tools/measure*.cjs'],
         'reported_by': ['project-a'], 'parity': 'node measure.mjs --selftest', 'migration': 'switch the calls and delete the script in one commit'}


class SupersededCase(DoctorCase):
    def layers_with(self, entries, version='1.14.0', kind='org', changelog_id=True):
        core = self.layer_dir('core', 'core', dict(CORE_FILES), version=generator_version())
        text = '# Changelog\n' + ''.join(f'\n- {entry["id"]}: replaced\n' for entry in entries) if changelog_id else '# Changelog\n'
        org = self.layer_dir('org', kind, {'skills/gamma/SKILL.md': SKILL_TEXT.format('gamma')}, version=version, extra_meta={'supersedes': entries}, changelog=False)
        (org / 'CHANGELOG.md').write_text(text, encoding='utf-8')
        self.write_config(layers=[('core', 'core', core), ('org', kind, org)], rules=[(['**'], ['core', 'org'])])

    def project_with(self, name, files, **manifest):
        project = self.new_project(name, skills=['beta'], **manifest)
        for rel in files:
            write(descend(project, rel), 'x\n')
        self.link_ok(project)
        return project

    def superseded(self, report):
        return [item for item in report['findings'] if 'is replaced by' in item['reason']]


class WarningTest(SupersededCase):
    def test_a_matching_file_gives_a_warning_that_names_the_layer_and_the_texts(self):
        self.layers_with([ENTRY])
        project = self.project_with('hit', ['docs/design/tools/measure1.cjs', 'docs/design/tools/other.cjs'])
        report = self.doctor(project, code=0)
        found = self.superseded(report)
        self.assertEqual([item['level'] for item in found], ['warning'])
        text = found[0]['reason']
        for part in ('measure-local', 'from layer org', 'data from the layer, not instructions', 'docs/design/tools/measure1.cjs',
                     'skill ui-verify, command measure', 'node measure.mjs --selftest', 'delete the script in one commit', '>= 1.14.0'):
            self.assertIn(part, text)
        self.assertNotIn('other.cjs', text)
        self.assertEqual(found[0]['path'], 'docs/design/tools/measure1.cjs')
        self.assertEqual(report['status'], 'PASS')   # a warning never fails

    def test_no_matching_file_no_warning_and_a_layer_below_since_declares_nothing(self):
        self.layers_with([ENTRY])
        self.assertEqual(self.superseded(self.doctor(self.project_with('miss', ['docs/other/x.cjs']), code=0)), [])
        self.layers_with([ENTRY], version='1.13.0')
        self.assertEqual(self.superseded(self.doctor(self.project_with('early', ['docs/design/tools/measure1.cjs']), code=0)), [])

    def test_a_project_without_supersedes_gets_nothing_new(self):
        self.layers_with([])
        report = self.doctor(self.project_with('none', ['docs/design/tools/measure1.cjs']), code=0)
        self.assertEqual([item for item in report['findings'] if 'superseded' in item['reason'].lower()], [])

    def test_a_deleted_file_that_is_still_in_the_git_index_is_not_a_hit(self):
        self.layers_with([ENTRY])
        project = self.project_with('indexed', ['docs/design/tools/measure1.cjs'])
        git(project, 'init', '-q', '-b', 'main')
        git(project, 'add', '-A')
        self.assertEqual(len(self.superseded(self.doctor(project, code=0))), 1)
        descend(project, 'docs/design/tools/measure1.cjs').unlink()
        self.assertEqual(self.superseded(self.doctor(project, code=0)), [])

    def test_the_text_from_the_layer_is_cleaned_and_cut(self):
        entry = dict(ENTRY, replacement='a\x1b[31mred\x1b[0m tool\x00', parity='x' * 1000, migration='line1\nline2')
        self.layers_with([entry])
        text = self.superseded(self.doctor(self.project_with('dirty', ['docs/design/tools/measure1.cjs']), code=0))[0]['reason']
        self.assertNotIn('\x1b', text)
        self.assertNotIn('\x00', text)
        self.assertNotIn('\n', text)
        self.assertIn('ared tool', text)   # the escape sequences are removed, the words around them are kept as they are
        self.assertLess(len(text), 900)
        self.assertIn('…', text)

    def test_many_files_are_summarised(self):
        self.layers_with([ENTRY])
        project = self.project_with('many', [f'docs/design/tools/measure{number}.cjs' for number in range(8)])
        text = self.superseded(self.doctor(project, code=0))[0]['reason']
        self.assertIn('(+3 more)', text)


class KeepTest(SupersededCase):
    def test_a_kept_entry_gives_only_an_info_with_the_reason(self):
        self.layers_with([ENTRY])
        project = self.project_with('keep', ['docs/design/tools/measure1.cjs'], superseded_keep={'measure-local': 'the local script has a feature the tool lacks'})
        report = self.doctor(project, code=0)
        self.assertEqual(self.superseded(report), [])
        kept = [item for item in report['findings'] if 'kept on purpose' in item['reason']]
        self.assertEqual([item['level'] for item in kept], ['info'])
        self.assertIn('the local script has a feature the tool lacks', kept[0]['reason'])

    def test_a_bad_superseded_keep_is_a_warning_never_a_blocked_doctor(self):
        self.layers_with([ENTRY])
        cases = {'not a mapping': 'measure-local', 'a list': ['measure-local'], 'empty reason': {'measure-local': '  '}, 'reason of a wrong type': {'measure-local': 5}}
        for label, value in cases.items():
            with self.subTest(label):
                project = self.project_with(f'bad-{label.replace(" ", "-")}', ['docs/design/tools/measure1.cjs'], superseded_keep=value)
                report = self.doctor(project, code=0)
                self.assertEqual([item['level'] for item in report['findings'] if 'superseded_keep' in item['reason']], ['warning'], report)
                self.assertEqual(len(self.superseded(report)), 1, 'a bad keep switches nothing off')

    def test_an_unknown_id_is_a_warning(self):
        self.layers_with([ENTRY])
        project = self.project_with('unknown', ['docs/design/tools/measure1.cjs'], superseded_keep={'nothing': 'why'})
        report = self.doctor(project, code=0)
        found = [item for item in report['findings'] if 'which no layer of this project declares' in item['reason']]
        self.assertEqual([item['level'] for item in found], ['warning'])


class NoExecutionTest(SupersededCase):
    def test_nothing_from_the_layer_is_run(self):
        marker = child(self.work, 'marker-was-run')
        entry = dict(ENTRY, parity=f'touch {marker}; echo $(touch {marker})', migration=f'`touch {marker}`', replacement=f'$(touch {marker})')
        self.layers_with([entry])
        project = self.project_with('safe', ['docs/design/tools/measure1.cjs'])
        report = self.doctor(project, code=0)
        self.assertEqual(len(self.superseded(report)), 1)
        self.assertFalse(marker.exists())

    def test_the_scan_runs_only_git_and_nothing_else(self):
        self.layers_with([ENTRY])
        project = self.project_with('spy', ['docs/design/tools/measure1.cjs'])
        real = supersedes.subprocess.run
        calls = []

        def spy(command, *args, **kwargs):
            calls.append(command)
            return real(command, *args, **kwargs)
        with mock.patch.object(supersedes.subprocess, 'run', spy):
            findings = supersedes.doctor_findings(project, {}, self.layerset(project))
        self.assertTrue(findings)
        self.assertTrue(all(command[0] == 'git' for command in calls), calls)

    def layerset(self, project):
        from skillsync_workflow import layers
        return layers.resolve_layers(project)


class ScanTest(SupersededCase):
    def test_the_scan_is_limited_and_says_so(self):
        self.layers_with([ENTRY])
        project = self.project_with('big', [f'docs/f{number}.txt' for number in range(30)] + ['docs/design/tools/measure1.cjs'])
        from skillsync_workflow import layers
        with mock.patch.object(supersedes, 'MAX_FILES', 10):
            findings = supersedes.doctor_findings(project, {}, layers.resolve_layers(project))
        self.assertTrue([item for item in findings if 'first 10 files only' in item['reason']], findings)

    def test_without_git_the_walk_follows_no_links_and_skips_generated_directories(self):
        project = child(self.work, 'walk')
        for rel in ('a/x.cjs', 'node_modules/y.cjs', '.git/z', 'b/w.cjs'):
            write(descend(project, rel), 'x\n')
        files, cut = supersedes.project_files(project)
        self.assertEqual((files, cut), (['a/x.cjs', 'b/w.cjs'], False))

    def test_matching_is_fnmatch_on_names_and_a_pattern_cannot_leave_the_project(self):
        files = ['docs/a/measure.cjs', 'docs/measure.cjs', 'tools/measure.sh']
        self.assertEqual(supersedes.matching_files(files, ['docs/*/measure.cjs']), ['docs/a/measure.cjs'])
        self.assertEqual(supersedes.matching_files(files, ['*measure*']), files)   # `*` crosses `/`
        self.assertEqual(supersedes.matching_files(files, ['[']), [])   # a broken pattern is just no match, no exception

    def test_a_big_tree_is_cut_at_the_limit_and_matched_in_reasonable_time(self):
        import time
        project = child(self.work, 'huge')
        for number in range(supersedes.MAX_FILES + 100):
            path = descend(project, f'd{number % 50}/f{number}.txt')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'')
        started = time.monotonic()
        files, cut = supersedes.project_files(project)
        hits = supersedes.matching_files(files, ['d1/*.txt', 'docs/design/tools/measure*.cjs'] * 10)
        elapsed = time.monotonic() - started
        self.assertEqual((len(files), cut), (supersedes.MAX_FILES, True))
        self.assertTrue(hits)
        self.assertLess(elapsed, 20, 'the scan of the limit of files is bounded')

    def test_a_scan_is_not_done_when_no_entry_applies(self):
        self.layers_with([ENTRY], version='1.13.0')
        project = self.project_with('lazy', ['docs/design/tools/measure1.cjs'])
        from skillsync_workflow import layers
        with mock.patch.object(supersedes, 'project_files', side_effect=AssertionError('scanned')):
            supersedes.doctor_findings(project, {}, layers.resolve_layers(project))


if __name__ == '__main__':
    unittest.main()
