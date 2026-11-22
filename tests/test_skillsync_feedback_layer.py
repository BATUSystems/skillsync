"""Section `feedback:` of `layer.yaml` (CORE-1140-FEEDBACK, F-1): validation per section, the `feedback-invalid` warning, backward compatibility.

The section only proposes where change requests go; a bad one is skipped with a warning and never blocks the layer. Names are neutral examples."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import LayersCase, generator_version, make_layer  # noqa: E402
from skillsync_workflow import layers  # noqa: E402
from tmp_paths import child  # noqa: E402

GOOD = {'kind': 'azure-boards', 'organization': 'example-org', 'project': 'Example Project', 'area': 'Platform/Team', 'type': 'Task',
        'labels': ['extra', 'core:candidate']}


def parse(**extra):
    return layers.parse_layer_yaml({'schema': 1, 'id': 'acme', 'kind': 'org', **extra})


class ParsingTest(unittest.TestCase):
    def test_a_valid_section_is_read_as_given(self):
        meta = parse(feedback=GOOD)
        self.assertEqual(meta['feedback_errors'], [])
        self.assertEqual(meta['feedback'], GOOD)

    def test_without_the_key_there_is_nothing_and_no_error(self):
        meta = parse()
        self.assertEqual((meta['feedback'], meta['feedback_errors']), (None, []))

    def test_defaults(self):
        meta = parse(feedback={'kind': 'azure-boards', 'organization': 'example-org', 'project': 'ExampleProject'})
        self.assertEqual((meta['feedback']['type'], meta['feedback']['area'], meta['feedback']['labels']), ('Task', None, []))

    def test_bad_sections_are_skipped_and_reported(self):
        bad = {
            'not a mapping': ['x'],
            'other kind': {**GOOD, 'kind': 'jira'},
            'no kind': {k: v for k, v in GOOD.items() if k != 'kind'},
            'url organization': {**GOOD, 'organization': 'https://dev.azure.com/example-org'},
            'organization with slash': {**GOOD, 'organization': 'a/b'},
            'no organization': {k: v for k, v in GOOD.items() if k != 'organization'},
            'apostrophe project': {**GOOD, 'project': "Ex'ample"},
            'project not text': {**GOOD, 'project': 5},
            'control character': {**GOOD, 'project': 'Ex\nample'},
            'padded project': {**GOOD, 'project': ' Example'},
            'area with ..': {**GOOD, 'area': 'Platform/../Other'},
            'area with backslash': {**GOOD, 'area': 'Platform\\Team'},
            'area leading slash': {**GOOD, 'area': '/Platform'},
            'area empty segment': {**GOOD, 'area': 'Platform//Team'},
            'area too long': {**GOOD, 'area': '/'.join(['a'] * 300)},
            'type with digit start': {**GOOD, 'type': '1Task'},
            'too many labels': {**GOOD, 'labels': ['a', 'b', 'c', 'd', 'e', 'f']},
            'bad label': {**GOOD, 'labels': ['Upper']},
            'reserved fb label': {**GOOD, 'labels': ['fb-0123456789ab']},
            'reserved fbk label': {**GOOD, 'labels': ['fbk-0123456789ab']},
            'reserved layer label': {**GOOD, 'labels': ['layer:other']},
            'reserved feedback label': {**GOOD, 'labels': ['feedback']},
            'reserved core-candidate label': {**GOOD, 'labels': ['core-candidate']},
            'labels not a list': {**GOOD, 'labels': 'feedback'},
            'unknown key': {**GOOD, 'token': 'x'},
        }
        for name, section in bad.items():
            with self.subTest(name):
                meta = parse(feedback=section)
                self.assertIsNone(meta['feedback'])
                self.assertEqual(len(meta['feedback_errors']), 1)
                self.assertTrue(meta['feedback_errors'][0].startswith('feedback'))

    def test_the_rest_of_the_layer_stays_readable(self):
        meta = parse(feedback=['x'], version='1.0.0')
        self.assertEqual(meta['version'], '1.0.0')

    def test_duplicate_labels_collapse(self):
        meta = parse(feedback={**GOOD, 'labels': ['extra', 'extra']})
        self.assertEqual(meta['feedback']['labels'], ['extra'])


class FindingsTest(LayersCase):
    def build(self, section):
        base = child(self.work, 'fb-layers')
        core = make_layer(child(base, 'core'), 'core', 'core', {'skills/a/SKILL.md': 'x\n'}, version=generator_version())
        org = make_layer(child(base, 'org'), 'org', 'org', {'skills/b/SKILL.md': 'y\n'}, extra_meta={'feedback': section})
        self.write_config([('core', 'core', core), ('org', 'org', org)], [(['**'], ['core', 'org'])])
        return layers.resolve_layers(self.project('fb-project'))

    def findings(self, section):
        return layers.check_layers(self.build(section), hooks=False, orphans=False)

    def test_a_bad_section_is_a_warning_and_the_layer_stays_usable(self):
        layerset = self.build({**GOOD, 'organization': 'https://x'})
        self.assertIsNotNone(layerset.layers[1].meta)
        found = [f for f in layers.check_layers(layerset, hooks=False, orphans=False) if f['code'] == 'feedback-invalid']
        self.assertEqual([(f['severity'], f['layer']) for f in found], [('warning', 'org')])
        self.assertEqual(layers.findings_status(layers.check_layers(layerset, hooks=False, orphans=False)), 'PASS')

    def test_a_good_section_has_no_finding(self):
        self.assertNotIn('feedback-invalid', {f['code'] for f in self.findings(GOOD)})


if __name__ == '__main__':
    unittest.main()
