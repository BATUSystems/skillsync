"""Leak scan of the feedback payload (CORE-1140-FEEDBACK, F-4): the scan covers the exact bytes (title, tags, body), takes the destination layer as the
target, shows the coverage and never quotes the text. Client terms are synthetic and composed at run time."""
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import LayersCase, generator_version, make_layer  # noqa: E402
from skillsync_workflow import feedback, layers  # noqa: E402
from tmp_paths import child  # noqa: E402
from test_skillsync_feedback import DRAFT, SECTION, draft  # noqa: E402

CLIENT = 'Brenk' + 'or'   # a client name of ANOTHER layer (synthetic)
CLIENT_APOSTROPHE = "O'Bren" + 'kor'   # a name with an apostrophe: HTML escaping turns it into `O&#x27;Brenkor`


class LeakScanTest(LayersCase):
    def config(self, *, identity=True, with_target=True):
        base = child(self.work, 'leak-layers')
        core = make_layer(child(base, 'core'), 'core', 'core', version=generator_version())
        org = make_layer(child(base, 'org'), 'org', 'org')
        other = self.git_layer('other', 'org', identity_terms=[{'term': CLIENT, 'kind': 'org'}, {'term': CLIENT_APOSTROPHE, 'kind': 'org'}] if identity else None)
        extra = {'leakguard': {'targets': {'org': {'scope': 'private', 'kind': 'org'}, 'other': {'scope': 'private', 'kind': 'org'}}}} if with_target else {}
        self.write_config([('core', 'core', core), ('org', 'org', org), ('other', 'org', other)], [(['**'], ['core', 'org'])], **extra)
        return layers.load_machine_config(self.config_file)

    def scan(self, config, **changes):
        rendered = feedback.render(feedback.validate_draft(draft(**changes)), SECTION)
        return feedback.leak_scan(config, 'org', feedback.final_payload(rendered), directory=self.work)

    def test_a_clean_payload_passes_with_the_coverage_of_the_leak_guard(self):
        report = self.scan(self.config())
        self.assertEqual((report['status'], report['findings']), ('PASS', []))
        self.assertEqual(report['coverage'], 'full')

    def test_a_client_name_in_each_surface_fails_without_quoting_it(self):
        config = self.config()
        cases = {'title': {'title': f'Fix for {CLIENT}'}, 'body': {'problem': f'{CLIENT} reported it'},
                 'evidence': {'evidence': [{'source': 'run', 'note': f'seen at {CLIENT}'}]}, 'target': {'target': f'skills/{CLIENT.lower()}/SKILL.md'}}
        for name, changes in cases.items():
            with self.subTest(name):
                report = self.scan(config, **changes)
                self.assertEqual(report['status'], 'FAIL')
                self.assertTrue(report['findings'])
                self.assertNotIn(CLIENT.lower(), repr(report).lower())

    def test_a_client_name_in_a_label_is_caught_as_part_of_the_tags(self):
        config = self.config()
        section = {**SECTION, 'labels': [CLIENT.lower()]}
        rendered = feedback.render(feedback.validate_draft(DRAFT), section)
        report = feedback.leak_scan(config, 'org', feedback.final_payload(rendered), directory=self.work)
        self.assertEqual(report['status'], 'FAIL')
        self.assertIn('tags', {item['label'] for item in report['findings']})

    def test_a_name_that_html_escaping_would_split_is_caught(self):
        report = self.scan(self.config(), problem=f'reported by {CLIENT_APOSTROPHE} last week')
        self.assertEqual(report['status'], 'FAIL')

    def test_a_comment_payload_is_scanned_too(self):
        config = self.config()
        rendered = feedback.render(feedback.validate_draft(draft(problem=f'{CLIENT} again')), SECTION, mode='comment', item=4)
        self.assertEqual(feedback.leak_scan(config, 'org', feedback.final_payload(rendered), directory=self.work)['status'], 'FAIL')

    def test_a_secret_shaped_text_fails(self):
        token = 'ghp_' + 'A1b2C3d4' * 5
        self.assertEqual(self.scan(self.config(), problem=f'the token is {token}')['status'], 'FAIL')

    def test_other_secret_shapes_fail_and_ordinary_text_does_not(self):
        config = self.config()
        for text in ('Authorization: Bearer ' + 'abcdefghij' * 3, 'password = ' + 'hunter22' + 'xyz', '-----BEGIN RSA PRIVATE ' + 'KEY-----',
                     'https://user:' + 'pw1234@host.example', 'a' * 52):
            with self.subTest(text[:12]):
                self.assertEqual(self.scan(config, problem=text)['status'], 'FAIL')
        for text in ('client_secret=' + 'abcd1234efgh', 'access_token: ' + 'abcd1234efgh', 'AZURE_DEVOPS_EXT_PAT=' + 'abcd1234efgh',
                     '"token": "' + 'abcd1234efgh' + '"', 'x-api-key: ' + 'abcd1234efgh', 'glpat-' + 'a' * 20, 'AccountKey=' + 'A' * 20, 'ASIA' + 'A' * 16,
                     'sk-ant-' + 'a' * 24, 'Authorization: Basic ' + 'dXNlcjpwYXNz' * 3):
            with self.subTest(text[:14]):
                self.assertEqual(self.scan(config, problem=text)['status'], 'FAIL')
        self.assertEqual(self.scan(config, problem='The token field is described in the docs; the password policy is unchanged.')['status'], 'PASS')

    def test_a_text_that_the_status_output_would_mask_is_a_finding(self):
        # `redact` masks the values of secret-named variables of the environment: the preview would show `[REDACTED]` while the send writes the value
        value = 'plain-value-' + 'without-a-key-9f3a'
        with unittest.mock.patch.dict(os.environ, {'EXAMPLE_API_TOKEN': value}):
            report = self.scan(self.config(), problem=f'the value was {value} in the log')
        self.assertEqual(report['status'], 'FAIL')
        self.assertNotIn(value, repr(report))

    def test_without_identity_files_the_coverage_is_generic_and_says_so(self):
        report = self.scan(self.config(identity=False))
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['coverage'], 'generic')

    def test_without_a_declared_target_the_coverage_is_generic(self):
        report = self.scan(self.config(with_target=False))
        self.assertEqual(report['coverage'], 'generic')

    def test_the_target_is_the_destination_layer_not_a_choice_of_the_repository(self):
        config = self.config()
        payload = feedback.final_payload(feedback.render(feedback.validate_draft(DRAFT), SECTION))
        self.assertEqual(feedback.leak_scan(config, 'org', payload, directory=self.work)['status'], 'PASS')
        # the identity of the destination layer itself is not a forbidden term for it: the text of layer `other` may name its own client
        rendered = feedback.render(feedback.validate_draft(draft(problem=f'{CLIENT} again')), SECTION)
        self.assertEqual(feedback.leak_scan(config, 'other', feedback.final_payload(rendered), directory=self.work)['status'], 'PASS')

    def test_bad_input_is_blocked_not_passed(self):
        config = self.config()
        payload = feedback.final_payload(feedback.render(feedback.validate_draft(DRAFT), SECTION))
        report = feedback.leak_scan(config, 'org', payload, directory=child(self.work, 'missing-dir'))
        self.assertEqual(report['status'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
