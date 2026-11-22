"""Trust model of the feedback target (CORE-1140-FEEDBACK, F-2): `feedback_targets` in the machine config, the states, `trust_target`."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import LayersCase, generator_version, make_layer  # noqa: E402
from skillsync_workflow import feedback, layers  # noqa: E402
from tmp_paths import child  # noqa: E402

SECTION = {'kind': 'azure-boards', 'organization': 'example-org', 'project': 'ExampleProject', 'area': 'Platform', 'type': 'Task', 'labels': []}
ENTRY = {'layer': 'org', 'kind': 'azure-boards', 'organization': 'example-org', 'project': 'ExampleProject'}


class ParsingTest(LayersCase):
    def config(self, targets, **extra):
        base = child(self.work, 'trust-layers')
        core = make_layer(child(base, 'core'), 'core', 'core', version=generator_version())
        org = make_layer(child(base, 'org'), 'org', 'org', extra_meta={'feedback': SECTION})
        self.write_config([('core', 'core', core), ('org', 'org', org)], [(['**'], ['core', 'org'])], **({'feedback_targets': targets} if targets is not None else {}), **extra)
        return layers.load_machine_config(self.config_file)

    def test_a_valid_list_is_read(self):
        self.assertEqual(self.config([ENTRY]).feedback_targets, [ENTRY])

    def test_without_the_key_the_list_is_empty(self):
        self.assertEqual(self.config(None).feedback_targets, [])

    def test_bad_lists_are_refused(self):
        bad = {
            'not a list': {'layer': 'org'},
            'not a mapping': ['org'],
            'unknown layer': [{**ENTRY, 'layer': 'other'}],
            'twice': [ENTRY, ENTRY],
            'other kind': [{**ENTRY, 'kind': 'jira'}],
            'url organization': [{**ENTRY, 'organization': 'https://dev.azure.com/example-org'}],
            'missing project': [{k: v for k, v in ENTRY.items() if k != 'project'}],
            'apostrophe project': [{**ENTRY, 'project': "Ex'ample"}],
            'extra key': [{**ENTRY, 'token': 'x'}],
        }
        for name, targets in bad.items():
            with self.subTest(name):
                with self.assertRaises(layers.LayerError):
                    self.config(targets)


class StateTest(LayersCase):
    def config(self, targets):
        base = child(self.work, 'state-layers')
        core = make_layer(child(base, 'core'), 'core', 'core', version=generator_version())
        org = make_layer(child(base, 'org'), 'org', 'org')
        self.write_config([('core', 'core', core), ('org', 'org', org)], [(['**'], ['core', 'org'])], **({'feedback_targets': targets} if targets is not None else {}))
        return layers.load_machine_config(self.config_file)

    def test_no_section_means_the_layer_takes_no_requests(self):
        self.assertEqual(feedback.target_state(self.config([ENTRY]), 'org', None)['state'], feedback.NO_SECTION)

    def test_a_proposal_without_an_approval_is_not_trusted(self):
        self.assertEqual(feedback.target_state(self.config([]), 'org', SECTION), {'state': feedback.NOT_TRUSTED, 'entry': None})

    def test_the_approved_target_is_trusted_and_area_and_type_may_change(self):
        config = self.config([ENTRY])
        self.assertEqual(feedback.target_state(config, 'org', SECTION)['state'], feedback.TRUSTED)
        self.assertEqual(feedback.target_state(config, 'org', {**SECTION, 'area': 'Other/Team', 'type': 'Bug'})['state'], feedback.TRUSTED)

    def test_a_changed_organization_or_project_cancels_the_approval(self):
        config = self.config([ENTRY])
        for key, value in (('organization', 'other-org'), ('project', 'OtherProject')):
            with self.subTest(key):
                self.assertEqual(feedback.target_state(config, 'org', {**SECTION, key: value})['state'], feedback.CHANGED)

    def test_an_approval_of_another_layer_does_not_count(self):
        base = child(self.work, 'state-layers2')
        core = make_layer(child(base, 'core'), 'core', 'core', version=generator_version())
        org = make_layer(child(base, 'org'), 'org', 'org')
        other = make_layer(child(base, 'other'), 'other', 'org')
        self.write_config([('core', 'core', core), ('org', 'org', org), ('other', 'org', other)], [(['**'], ['core', 'org'])],
                          feedback_targets=[{**ENTRY, 'layer': 'other'}])
        config = layers.load_machine_config(self.config_file)
        self.assertEqual(feedback.target_state(config, 'org', SECTION)['state'], feedback.NOT_TRUSTED)


class TrustWriteTest(StateTest):
    def test_trust_adds_the_entry_and_keeps_comments(self):
        config = self.config(None)
        text = self.config_file.read_text(encoding='utf-8')
        self.config_file.write_text('# my machine\n' + text, encoding='utf-8')
        config = layers.load_machine_config(self.config_file)
        result = feedback.trust_target(config, 'org', SECTION)
        self.assertTrue(result['changed'])
        self.assertFalse(result['rewritten'])
        written = self.config_file.read_text(encoding='utf-8')
        self.assertTrue(written.startswith('# my machine'))
        self.assertEqual(layers.load_machine_config(self.config_file).feedback_targets, [ENTRY])

    def test_trust_replaces_the_entry_of_the_same_layer(self):
        config = self.config([{**ENTRY, 'project': 'OldProject'}])
        feedback.trust_target(config, 'org', SECTION)
        self.assertEqual(layers.load_machine_config(self.config_file).feedback_targets, [ENTRY])

    def test_trust_is_idempotent(self):
        config = self.config([ENTRY])
        self.assertFalse(feedback.trust_target(config, 'org', SECTION)['changed'])

    def test_trust_refuses_without_a_section_or_for_an_unknown_layer(self):
        config = self.config(None)
        with self.assertRaises(layers.LayerError):
            feedback.trust_target(config, 'org', None)
        with self.assertRaises(layers.LayerError):
            feedback.trust_target(config, 'nope', SECTION)

    def test_dry_run_writes_nothing(self):
        config = self.config(None)
        before = self.config_file.read_text(encoding='utf-8')
        feedback.trust_target(config, 'org', SECTION, apply=False)
        self.assertEqual(self.config_file.read_text(encoding='utf-8'), before)


if __name__ == '__main__':
    unittest.main()
