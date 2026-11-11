"""Klucz `workflow.ceremony` (full | demo): walidator, pole `route`, kontrola `doctor` (K1, K2)."""
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow.ceremony import ceremony_info, validate_ceremony  # noqa: E402
from tests.test_skillsync_workflow import WorkflowFixture  # noqa: E402

BAD_VALUES = ('', None, 'Demo', 'DEMO', 'Full', 123, True, ['demo'], {'demo': 1}, 'nieznana', ' demo')


def manifest_with(**workflow):
    return {'workflow': dict(workflow)}


class CeremonyInfoTests(unittest.TestCase):
    def test_missing_key_is_the_full_default(self):
        for manifest in ({}, None, {'workflow': {}}, {'workflow': None}, {'workflow': 'x'}, {'workflow': ['ceremony']}):
            self.assertEqual(ceremony_info(manifest), {'value': 'full', 'source': 'default'}, manifest)
            self.assertEqual(validate_ceremony(manifest), ([], []), manifest)

    def test_valid_values_come_from_the_manifest(self):
        for value in ('full', 'demo'):
            manifest = manifest_with(ceremony=value)
            self.assertEqual(ceremony_info(manifest), {'value': value, 'source': 'manifest'})
            self.assertEqual(validate_ceremony(manifest), ([], []))

    def test_invalid_values(self):
        for value in BAD_VALUES:
            manifest = manifest_with(ceremony=value)
            info = ceremony_info(manifest)
            self.assertEqual(info['source'], 'invalid', repr(value))
            self.assertIsNone(info['value'], repr(value))
            self.assertTrue(info['error'].startswith('workflow.ceremony'), repr(value))
            errors, warnings = validate_ceremony(manifest)
            self.assertEqual((len(errors), warnings), (1, []), repr(value))
            self.assertEqual(errors[0], info['error'])

    def test_error_key_only_when_invalid(self):
        self.assertNotIn('error', ceremony_info({}))
        self.assertNotIn('error', ceremony_info(manifest_with(ceremony='demo')))

    def test_error_never_contains_the_value(self):
        self.assertNotIn('sekretna', ceremony_info(manifest_with(ceremony='sekretna'))['error'])


class RouteCeremonyTests(WorkflowFixture):
    def route(self):
        return self.report('route', '--topics', 'x')

    def ceremony_findings(self, report):
        return [(f['level'], f['reason']) for f in report['findings'] if 'ceremony' in f['reason']]

    def test_default_without_the_key(self):
        self.assertEqual(self.route()['ceremony'], {'value': 'full', 'source': 'default'})

    def test_manifest_values(self):
        for value in ('demo', 'full'):
            self.manifest['workflow']['ceremony'] = value
            self.save()
            self.assertEqual(self.route()['ceremony'], {'value': value, 'source': 'manifest'})

    def test_invalid_value_does_not_block_the_rest(self):
        baseline = self.route()
        self.manifest['workflow']['ceremony'] = 'Demo'
        self.save()
        result = self.route()
        self.assertEqual(result['ceremony']['source'], 'invalid')
        self.assertIsNone(result['ceremony']['value'])
        self.assertIn('error', result['ceremony'])
        self.assertEqual({k: v for k, v in result.items() if k != 'ceremony'},
                         {k: v for k, v in baseline.items() if k != 'ceremony'})

    def test_delivery_field_is_unchanged(self):
        before = self.route()['delivery']
        self.manifest['workflow']['ceremony'] = 'demo'
        self.save()
        self.assertEqual(self.route()['delivery'], before)

    def test_doctor_error_for_bad_values(self):
        for value in BAD_VALUES:
            self.manifest['workflow']['ceremony'] = value
            self.save()
            report = self.report('doctor', code=1)
            self.assertEqual(report['status'], 'FAIL', repr(value))
            self.assertEqual([level for level, _ in self.ceremony_findings(report)], ['error'], repr(value))

    def test_doctor_silent_for_valid_value_and_missing_key(self):
        self.assertEqual(self.ceremony_findings(self.report('doctor')), [])
        for value in ('full', 'demo'):
            self.manifest['workflow']['ceremony'] = value
            self.save()
            report = self.report('doctor')
            self.assertEqual(report['status'], 'PASS')
            self.assertEqual(self.ceremony_findings(report), [])

    def test_library_defaults_and_project_file_are_ignored(self):
        self.manifest['project'] = {'name': 'Team/Service'}
        directory = self.library / 'workflow/projects'
        directory.mkdir(parents=True)
        (directory.parent / 'defaults.yaml').write_text(yaml.safe_dump({'ceremony': 'demo'}))
        (directory / 'Team-Service.yaml').write_text(yaml.safe_dump({'workflow': {'ceremony': 'bogus'}}))
        self.save()
        self.assertEqual(self.route()['ceremony'], {'value': 'full', 'source': 'default'})
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.ceremony_findings(report), [])


if __name__ == '__main__':
    unittest.main()
