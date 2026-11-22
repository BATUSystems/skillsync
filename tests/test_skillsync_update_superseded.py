"""`layers update` and `pull` (CORE-1140-SUP-TOOLS, SUP-3): one line and the JSON field `superseded` about entries a layer declared in this very update.

The count comes from the versions in layer.yaml before and after the update (the report has commits): an entry counts when its `since` is in
(version before, version after]. No entry, no line and no field; nothing is run and no project is scanned.
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from layers_helpers import LayersCase, generator_version, write  # noqa: E402
from skillsync_workflow import cli, layers  # noqa: E402

LINE = 'superseded tools: {} newly declared by updated layers; run skillsync doctor in each project'


def entry(entry_id, since):
    return {'id': entry_id, 'since': since, 'replacement': 'skill x', 'matches': ['tools/*.sh']}


class SupersededUpdateTest(LayersCase):
    def setUp(self):
        super().setUp()
        self.set_git_identity('jan@acme.example')
        self.bare, self.src = self.source_repo('acme', 'acme', 'org', {'skills/s/SKILL.md': 'v1\n'})
        self.assertEqual(self.cli('layers', 'add', 'acme', '--kind', 'org', '--source', str(self.bare)).returncode, 0)

    def release(self, version, *entries):
        data = {'schema': 1, 'id': 'acme', 'kind': 'org', 'version': version, 'core_min': generator_version()}
        if entries:
            data['supersedes'] = list(entries)
        return self.push_commit(self.src, 'layer.yaml', yaml.safe_dump(data, sort_keys=False), message=f'release {version}')

    def update(self, *extra):
        return self.cli_json('layers', 'update', 'acme', *extra)

    def test_an_entry_declared_by_the_update_is_counted_in_the_line_and_the_field(self):
        self.release('1.1.0', entry('a', '1.1.0'), entry('b', '1.1.0'))
        code, report = self.update()
        self.assertEqual((code, report['status']), (0, 'PASS'))
        self.assertEqual(report['superseded'], {'acme': 2})
        self.assertEqual(report['layers'][0]['superseded'], 2)
        self.assertEqual(self.release('1.2.0', entry('a', '1.1.0'), entry('b', '1.1.0')) and self.update()[1].get('superseded'), None)   # nothing new this time

    def test_the_text_line_is_printed_once_and_json_stays_clean(self):
        self.release('1.1.0', entry('a', '1.1.0'))
        process = self.cli('layers', 'update', 'acme')
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.stdout.count(LINE.format(1)), 1, process.stdout)

    def test_no_new_entry_no_line_and_no_field(self):
        self.release('1.1.0')
        process = self.cli('layers', 'update', 'acme')
        self.assertNotIn('superseded tools', process.stdout + process.stderr)
        self.release('1.2.0')
        self.assertNotIn('superseded', self.update()[1])

    def test_an_old_entry_or_an_entry_added_without_a_version_bump_is_not_counted(self):
        self.release('1.1.0', entry('a', '1.0.0'))                         # since <= the version before
        self.assertNotIn('superseded', self.update()[1])
        self.release('1.1.0', entry('a', '1.0.0'), entry('late', '1.1.0'))   # a new entry, the same version
        self.assertNotIn('superseded', self.update()[1])
        self.release('1.2.0', entry('future', '1.5.0'))                    # since above the version after
        self.assertNotIn('superseded', self.update()[1])

    def test_a_bad_layer_yaml_after_the_update_never_breaks_it(self):
        self.push_commit(self.src, 'layer.yaml', 'schema: [unclosed\n', message='broken')
        code, report = self.update()
        self.assertEqual(report['layers'][0]['status'], 'PASS')
        self.assertNotIn('superseded', report)

    def test_pull_repeats_the_line(self):
        report = {'status': 'PASS', 'layers': [], 'superseded': {'acme': 3}}
        out = io.StringIO()
        with mock.patch.object(layers, 'layers_update', return_value=report), mock.patch.object(cli, 'cmd_link', return_value=0), \
                contextlib.redirect_stdout(out):
            cli.cmd_pull(mock.Mock(path='.'))
        self.assertEqual(out.getvalue().count(LINE.format(3)), 1, out.getvalue())
        out = io.StringIO()
        with mock.patch.object(layers, 'layers_update', return_value={'status': 'PASS', 'layers': []}), mock.patch.object(cli, 'cmd_link', return_value=0), \
                contextlib.redirect_stdout(out):
            cli.cmd_pull(mock.Mock(path='.'))
        self.assertNotIn('superseded tools', out.getvalue())

    def test_the_summary_helper_ignores_junk(self):
        self.assertIsNone(layers.superseded_summary({'superseded': {'x': 'many', 'y': 0}}))
        self.assertIsNone(layers.superseded_summary(None))
        self.assertEqual(layers.superseded_summary({'superseded': {'x': 1, 'y': 2}}), LINE.format(3))


if __name__ == '__main__':
    unittest.main()
