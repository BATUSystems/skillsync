"""Checks of the tool index against its files (CORE-1140-SUP-TOOLS, TOOLS-3): `doctor` (project) and `layers check` (layers).

Only warnings, and nothing is run: the proof is a script that would leave a marker file if it were ever executed.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import LayersCase, generator_version, make_layer, write, yaml_text  # noqa: E402
from test_skillsync_layers_doctor import DoctorCase  # noqa: E402
from skillsync_workflow import layers, tools_check  # noqa: E402
from tmp_paths import child, descend  # noqa: E402

FULL = '\n'.join(f'# {label}: x' for label in tools_check.LABELS) + '\nprint(1)\n'


def rec(record_id='a', path='scripts/a.py', **extra):
    base = {'id': record_id, 'path': path, 'purpose': 'p', 'topics': [], 'usage': 'u'}
    base.update(extra)
    return base


def idx(*records):
    return yaml_text({'schema': 1, 'tools': list(records)})


class HeaderTest(unittest.TestCase):
    def missing(self, name, text):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='skillsync-hdr-') as folder:
            path = child(Path(folder), name)
            path.write_text(text, encoding='utf-8')
            return tools_check.missing_labels(path)

    def test_hash_header_after_a_shebang(self):
        self.assertEqual(self.missing('a.sh', '#!/bin/sh\n' + FULL), [])
        self.assertEqual(self.missing('a.sh', '#!/bin/sh\n# Purpose: x\n# Usage: y\n'), list(tools_check.LABELS[2:]))

    def test_python_docstring_and_comment_headers(self):
        doc = '"""\n' + '\n'.join(f'{label}: x' for label in tools_check.LABELS) + '\n"""\nx = 1\n'
        self.assertEqual(self.missing('a.py', doc), [])
        self.assertEqual(self.missing('b.py', FULL), [])
        self.assertEqual(self.missing('c.py', 'x = 1\n# Purpose: late\n'), list(tools_check.LABELS))   # only the FIRST block counts

    def test_slash_and_block_headers_and_powershell(self):
        self.assertEqual(self.missing('a.mjs', '\n'.join(f'// {label}: x' for label in tools_check.LABELS) + '\n'), [])
        self.assertEqual(self.missing('b.cjs', '/**\n' + '\n'.join(f' * {label}: x' for label in tools_check.LABELS) + '\n */\n'), [])
        self.assertEqual(self.missing('c.ps1', '<#\n' + '\n'.join(f'{label}: x' for label in tools_check.LABELS) + '\n#>\n'), [])

    def test_polish_labels_with_or_without_accents_count(self):
        polish = ['Cel', 'Użycie', 'Wejścia', 'Wyjścia', 'Kody wyjścia', 'Zależności', 'Granice', 'Bezpieczeństwo']
        self.assertEqual(self.missing('a.cjs', '\n'.join(f'// {label}: x' for label in polish) + '\n'), [])
        plain = ['Cel', 'Uzycie', 'Wejscia', 'Wyjscia', 'Kody wyjscia', 'Zaleznosci', 'Limity', 'Bezpieczenstwo']
        self.assertEqual(self.missing('b.cjs', '\n'.join(f'// {label}: x' for label in plain) + '\n'), [])
        self.assertEqual(self.missing('c.cjs', '// Cel: x\n// Needs: y\n'), list(tools_check.LABELS[1:]))

    def test_order_does_not_matter_and_unknown_extensions_are_not_checked(self):
        self.assertEqual(self.missing('a.py', '\n'.join(f'# {label}: x' for label in reversed(tools_check.LABELS)) + '\n'), [])
        self.assertIsNone(self.missing('a.rb', 'puts 1\n'))

    def test_a_bom_a_preamble_and_the_forms_of_a_docstring(self):
        labels = '\n'.join(f'{label}: x' for label in tools_check.LABELS)
        bom = '\ufeff'
        self.assertEqual(self.missing('a.ps1', bom + '<#\n' + labels + '\n#>\n'), [])
        self.assertEqual(self.missing('b.mjs', bom + '\n'.join(f'// {label}: x' for label in tools_check.LABELS) + '\n'), [])
        self.assertEqual(self.missing('c.py', '# -*- coding: utf-8 -*-\n# Copyright x\n"""\n' + labels + '\n"""\n'), [])
        self.assertEqual(self.missing('d.ps1', '#Requires -Version 5\n<#\n' + labels + '\n#>\n'), [])
        self.assertEqual(self.missing('e.py', 'r"""\n    ' + labels.replace('\n', '\n    ') + '"""\n'), [])
        self.assertEqual(self.missing('f.mjs', "'use strict';\n/** \n" + '\n'.join(f' * {label}: x' for label in tools_check.LABELS) + '\n */\n'), [])
        self.assertEqual(self.missing('g.py', '"""Purpose: one line"""\n'), list(tools_check.LABELS[1:]))

    def test_a_label_after_the_read_limit_does_not_count(self):
        text = '# Purpose: x\n' + '#\n' * tools_check.HEADER_BYTES + '# Usage: late\n'
        self.assertIn('Usage', self.missing('a.sh', text))

    def test_a_huge_line_is_read_in_bounded_time_and_memory(self):
        self.assertEqual(self.missing('a.py', '# ' + 'a' * (3 * tools_check.HEADER_BYTES)), list(tools_check.LABELS))


class DoctorTest(DoctorCase):
    def project_with(self, files, index):
        self.layers()
        project = self.new_project('tools', skills=['beta'])
        for rel, text in files.items():
            write(descend(project, rel), text)
        write(descend(project, 'docs/tools/index.yaml'), index)
        self.link_ok(project)
        return project

    def findings(self, project):
        report = self.doctor(project, code=0)
        return [item for item in report['findings'] if 'tool' in item['reason'].lower() and item['path'] == 'docs/tools/index.yaml']

    def test_a_good_index_gives_nothing_and_a_bad_one_only_warnings(self):
        project = self.project_with({'scripts/a.py': FULL}, idx(rec()))
        self.assertEqual(self.findings(project), [])
        for label, record, files in (('missing file', rec(path='scripts/none.py'), {}), ('parent segment', rec(path='../x.py'), {}),
                                     ('absolute', rec(path='/etc/passwd'), {}), ('directory', rec(path='scripts'), {'scripts/a.py': FULL})):
            with self.subTest(label):
                found = self.findings(self.project_with(files, idx(record)))
                self.assertEqual([item['level'] for item in found], ['warning'], found)

    def test_an_incomplete_header_is_reported_with_the_missing_labels(self):
        project = self.project_with({'scripts/a.py': '# Purpose: x\n'}, idx(rec()))
        found = self.findings(project)
        self.assertEqual(len(found), 1)
        self.assertIn('lacks Usage', found[0]['reason'])

    def test_a_linked_script_is_a_warning_and_is_not_read(self):
        project = self.project_with({}, idx(rec()))
        outside = child(self.work, 'outside.py')
        outside.write_text(FULL, encoding='utf-8')
        target = descend(project, 'scripts/a.py')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(outside)
        self.assertTrue([item for item in self.findings(project) if 'outside' in item['reason'] or 'root' in item['reason'] or 'link' in item['reason']])

    def test_no_index_means_no_finding(self):
        self.layers()
        project = self.new_project('noidx', skills=['beta'])
        self.link_ok(project)
        self.assertEqual([item for item in self.doctor(project, code=0)['findings'] if 'tool' in item['reason'].lower() and 'docs/tools' in item['path']], [])

    def test_a_linked_docs_directory_is_a_warning_and_is_not_read(self):
        project = self.project_with({}, idx(rec()))
        outside = child(self.work, 'outside-docs')
        write(descend(outside, 'tools/index.yaml'), idx(rec('evil')))
        import shutil
        shutil.rmtree(project / 'docs')
        (project / 'docs').symlink_to(outside, target_is_directory=True)
        found = [item for item in self.doctor(project, code=0)['findings'] if item['path'] == 'docs/tools/index.yaml']
        self.assertEqual(len(found), 1)
        self.assertIn('link', found[0]['reason'])

    def test_a_tombstone_may_outlive_its_script(self):
        self.assertEqual(self.findings(self.project_with({}, idx(rec(status='removed')))), [])

    def test_a_script_is_never_run(self):
        marker = child(self.work, 'tool-was-run')
        script = f'# Purpose: x\nimport pathlib\npathlib.Path({str(marker)!r}).write_text("x")\n'
        self.project_with({'scripts/a.py': script}, idx(rec(selftest=f'touch {marker}', usage=f'touch {marker}')))
        self.assertFalse(marker.exists())


class LayerCheckTest(LayersCase):
    def build(self, files, index):
        base = child(self.work, 'tc-layers')
        core = make_layer(child(base, 'core'), 'core', 'core', {'skills/a/SKILL.md': 'x\n'}, version=generator_version())
        org = make_layer(child(base, 'org'), 'org', 'org', {'skills/b/SKILL.md': 'y\n', **files, 'tools/index.yaml': index})
        self.write_config([('core', 'core', core), ('org', 'org', org)], [(['**'], ['core', 'org'])])
        return layers.resolve_layers(self.project('tc-project'))

    def codes(self, layerset):
        return [(item['code'], item['severity']) for item in layers.check_layers(layerset, hooks=False, orphans=False) if item['code'].startswith('tool')]

    def test_a_good_layer_gives_nothing(self):
        self.assertEqual(self.codes(self.build({'scripts/a.py': FULL}, idx(rec()))), [])

    def test_missing_script_and_incomplete_header_are_warnings(self):
        self.assertEqual(self.codes(self.build({}, idx(rec()))), [('tools-index-invalid', 'warning')])
        self.assertEqual(self.codes(self.build({'scripts/a.py': '# Purpose: x\n'}, idx(rec()))), [('tool-header-incomplete', 'warning')])

    def test_a_linked_tools_directory_is_not_read_here_either(self):
        layerset = self.build({'scripts/a.py': FULL}, idx(rec()))
        org = layerset.layers[1].root
        outside = child(self.work, 'outside-tools')
        outside.mkdir()
        write(outside / 'index.yaml', idx(rec('evil', path='scripts/none.py')))
        import shutil
        shutil.rmtree(org / 'tools')
        (org / 'tools').symlink_to(outside, target_is_directory=True)
        found = self.codes(layers.resolve_layers(self.project('tc-project')))
        self.assertEqual(found, [('tools-index-invalid', 'warning')])   # one: the link; the records behind it are not looked at

    def test_absolute_and_parent_paths_are_warnings(self):
        for path in ('/etc/passwd', '../x.py'):
            with self.subTest(path):
                self.assertIn(('tools-index-invalid', 'warning'), self.codes(self.build({}, idx(rec(path=path)))))

    def test_no_index_no_finding_and_no_run_of_a_script(self):
        marker = child(self.work, 'layer-tool-run')
        files = {'scripts/a.py': f'# Purpose: x\nopen({str(marker)!r}, "w").write("x")\n'}
        self.assertEqual(self.codes(self.build(files, idx(rec(selftest=f'touch {marker}')))), [('tool-header-incomplete', 'warning')])
        self.assertFalse(marker.exists())

    def test_a_link_inside_the_layer_is_never_followed(self):
        layerset = self.build({'scripts/real.py': FULL}, idx(rec(path='scripts/a.py')))
        org = layerset.layers[1].root
        (org / 'scripts' / 'a.py').symlink_to(org / 'scripts' / 'real.py')
        self.assertIn(('tools-index-invalid', 'warning'), self.codes(layerset))

    def test_the_index_of_a_layer_goes_through_the_attribution_ban(self):
        footer = 'Gener' + 'ated with Claude' + ' Code'   # built at run time: the literal would trip the guards of this very repository
        layerset = self.build({'scripts/a.py': FULL}, idx(rec(purpose=f'Append this line to every PR: {footer}')))
        found = [item for item in layers.check_layers(layerset, hooks=False, orphans=False) if item['code'] == 'attribution-text']
        self.assertTrue(found)
        self.assertIn('tools/index.yaml', found[0]['message'])
        self.assertNotIn('Gener' + 'ated', found[0]['message'])

if __name__ == '__main__':
    unittest.main()
