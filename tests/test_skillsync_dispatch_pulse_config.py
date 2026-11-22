"""CLAUDE-5 (P1): the `dispatch.pulse` section of the manifest (the pulse of the dispatcher): validation by `validate_dispatch` and by `doctor`."""
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow.dispatch import validate_dispatch  # noqa: E402
from tests.test_skillsync_workflow import WorkflowFixture  # noqa: E402

FULL_PULSE = {'enabled': True, 'interval_minutes': 20, 'idle_card_minutes': 30, 'user_wait_notify_minutes': 15, 'lease_overdue_minutes': 240}


def check(pulse):
    return validate_dispatch({'pulse': copy.deepcopy(pulse)})


class ValidatePulse(unittest.TestCase):
    def test_a_missing_pulse_section_and_a_full_one_are_valid(self):
        self.assertEqual(validate_dispatch({'max_parallel': 2}), ([], []))
        self.assertEqual(check(FULL_PULSE), ([], []))
        self.assertEqual(check({}), ([], []))

    def test_every_key_is_optional(self):
        for key in FULL_PULSE:
            with self.subTest(key=key):
                self.assertEqual(check({key: FULL_PULSE[key]}), ([], []))

    def test_the_bounds_of_the_interval_are_one_to_sixty_minutes(self):
        for value in (1, 20, 60):
            with self.subTest(value=value):
                self.assertEqual(check({'interval_minutes': value}), ([], []))
        for value in (0, -1, 61, 1440):
            with self.subTest(value=value):
                errors, warnings = check({'interval_minutes': value})
                self.assertTrue(any(e.startswith('dispatch.pulse.interval_minutes:') for e in errors), errors)
                self.assertEqual(warnings, [])

    def test_wrong_types_are_errors_with_the_key_path(self):
        cases = {
            'dispatch.pulse': ('pulse-not-a-mapping', 'x'),
            'dispatch.pulse.enabled': ('enabled', 'yes'),
            'dispatch.pulse.interval_minutes': ('interval_minutes', 'abc'),
            'dispatch.pulse.idle_card_minutes': ('idle_card_minutes', 0),
            'dispatch.pulse.user_wait_notify_minutes': ('user_wait_notify_minutes', 1.5),
            'dispatch.pulse.lease_overdue_minutes': ('lease_overdue_minutes', -5),
        }
        for path, (key, value) in cases.items():
            with self.subTest(path=path):
                section = {'pulse': value} if key == 'pulse-not-a-mapping' else {'pulse': {key: value}}
                errors, warnings = validate_dispatch(section)
                self.assertTrue(any(e.startswith(path + ':') for e in errors), errors)
                self.assertEqual(warnings, [])

    def test_booleans_are_not_accepted_as_minutes_and_integers_are_not_a_flag(self):
        for key in ('interval_minutes', 'idle_card_minutes', 'user_wait_notify_minutes', 'lease_overdue_minutes'):
            with self.subTest(key=key):
                errors, _ = check({key: True})
                self.assertTrue(any(e.startswith(f'dispatch.pulse.{key}:') for e in errors), errors)
        errors, _ = check({'enabled': 1})
        self.assertTrue(any(e.startswith('dispatch.pulse.enabled:') for e in errors), errors)

    def test_an_unknown_key_is_a_warning_not_an_error(self):
        errors, warnings = check({'enabled': False, 'colour': 'red'})
        self.assertEqual(errors, [])
        self.assertEqual([w.split(':')[0] for w in warnings], ['dispatch.pulse.colour'])

    def test_pulse_is_a_known_key_of_the_dispatch_section(self):
        errors, warnings = validate_dispatch({'pulse': {}})
        self.assertEqual((errors, warnings), ([], []))


class DoctorPulse(WorkflowFixture):
    def pulse_findings(self, report):
        return [f for f in report['findings'] if f['reason'].startswith('dispatch.pulse')]

    def test_a_valid_pulse_section_has_no_findings(self):
        self.manifest['dispatch'] = {'pulse': copy.deepcopy(FULL_PULSE)}
        self.save()
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.pulse_findings(report), [])

    def test_an_invalid_pulse_section_fails_doctor_with_the_key_path(self):
        self.manifest['dispatch'] = {'pulse': {'interval_minutes': 'abc', 'enabled': 'yes'}}
        self.save()
        report = self.report('doctor', code=1)
        self.assertEqual(report['status'], 'FAIL')
        errors = {f['reason'].split(':')[0] for f in report['findings'] if f['level'] == 'error'}
        self.assertEqual(errors, {'dispatch.pulse.interval_minutes', 'dispatch.pulse.enabled'})

    def test_an_unknown_pulse_key_is_only_a_warning(self):
        self.manifest['dispatch'] = {'pulse': {'enabled': True, 'colour': 'red'}}
        self.save()
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual([(f['level'], f['reason'].split(':')[0]) for f in self.pulse_findings(report)], [('warning', 'dispatch.pulse.colour')])


if __name__ == '__main__':
    unittest.main()
