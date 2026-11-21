"""Charakteryzacja `route` i `doctor` sprzed wprowadzenia `workflow.specs` (CORE-SPECSTORE, F2.1).

Przypina istniejące zachowanie: zestaw kluczy `route`, `delivery`/`ceremony`, wynik `doctor` na czystym
projekcie i błędy dla błędnego manifestu. Celowo NIE sprawdza braku klucza `specs` (dodaje go F2)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_skillsync_workflow import WorkflowFixture  # noqa: E402

LEGACY_KEYS = {'status', 'files', 'topics', 'routes', 'skipped', 'lessons', 'domain', 'delivery', 'ceremony', 'note'}


def legacy(report):
    """Widok `route` bez kluczy dodawanych przez F2."""
    return {k: v for k, v in report.items() if k != 'specs'}


class RouteBaselineTests(WorkflowFixture):
    def route(self, *args):
        return self.report('route', '--topics', 'x', *args)

    def test_full_key_set_and_values_for_a_plain_project(self):
        result = legacy(self.route())
        self.assertEqual(set(result), LEGACY_KEYS)
        self.assertEqual(result['status'], 'NOT_RUN')
        self.assertEqual(result['topics'], ['x'])
        for key in ('files', 'routes', 'skipped', 'lessons', 'domain'):
            self.assertEqual(result[key], [], key)
        self.assertEqual(result['delivery'], {'mode': 'pr', 'base': None, 'source': 'default'})
        self.assertEqual(result['ceremony'], {'value': 'full', 'source': 'default'})
        self.assertIn('not executed', result['note'])

    def test_files_argument_is_echoed(self):
        result = legacy(self.report('route', '--topics', 'x', '--files', 'a/b.py'))
        self.assertEqual(set(result), LEGACY_KEYS)
        self.assertEqual(result['files'], ['a/b.py'])

    def test_delivery_and_ceremony_come_from_the_manifest_without_workflow_specs(self):
        self.manifest['delivery'] = {'mode': 'direct', 'base': 'develop'}
        self.manifest['workflow']['ceremony'] = 'demo'
        self.save()
        self.assertNotIn('specs', self.manifest['workflow'])
        result = legacy(self.route())
        self.assertEqual(set(result), LEGACY_KEYS)
        self.assertEqual(result['delivery'], {'mode': 'direct', 'base': 'develop', 'source': 'manifest'})
        self.assertEqual(result['ceremony'], {'value': 'demo', 'source': 'manifest'})

    def test_invalid_delivery_is_reported_without_blocking_route(self):
        self.manifest['delivery'] = {'mode': 'bogus'}
        self.save()
        result = legacy(self.route())
        self.assertEqual(set(result), LEGACY_KEYS)
        self.assertEqual(result['delivery']['source'], 'invalid')
        self.assertEqual(result['ceremony'], {'value': 'full', 'source': 'default'})


class DoctorBaselineTests(WorkflowFixture):
    @staticmethod
    def levels(report, level):
        return [f for f in report['findings'] if f['level'] == level]

    def test_clean_project_passes_with_no_errors(self):
        self.manifest['delivery'] = {'mode': 'pr'}
        self.save()
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.levels(report, 'error'), [])
        self.assertIn('budgets', report)
        self.assertTrue(all({'level', 'path', 'reason'} <= set(f) for f in report['findings']))

    def test_missing_delivery_mode_is_only_a_warning(self):
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.levels(report, 'error'), [])
        self.assertIn('delivery.mode: brak, domyślnie pr', [f['reason'] for f in report['findings']])

    def test_invalid_delivery_mode_is_an_error(self):
        self.manifest['delivery'] = {'mode': 'bogus'}
        self.save()
        report = self.report('doctor', code=1)
        self.assertEqual(report['status'], 'FAIL')
        errors = self.levels(report, 'error')
        self.assertEqual([e['reason'] for e in errors], ['delivery.mode: must be one of pr, direct'])
        self.assertEqual(errors[0]['path'], '.github/.skillsync.yaml')

    def test_invalid_ceremony_is_an_error(self):
        self.manifest['workflow']['ceremony'] = 'Demo'
        self.save()
        report = self.report('doctor', code=1)
        self.assertEqual(report['status'], 'FAIL')
        errors = self.levels(report, 'error')
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0]['reason'].startswith('workflow.ceremony'))
        self.assertNotIn('Demo', errors[0]['reason'])

    def test_both_invalid_sections_give_both_errors(self):
        self.manifest['delivery'] = {'mode': 'bogus'}
        self.manifest['workflow']['ceremony'] = 'nieznana'
        self.save()
        report = self.report('doctor', code=1)
        reasons = [e['reason'] for e in self.levels(report, 'error')]
        self.assertEqual(len(reasons), 2, reasons)
        self.assertTrue(any(r.startswith('delivery.mode') for r in reasons))
        self.assertTrue(any(r.startswith('workflow.ceremony') for r in reasons))


if __name__ == '__main__':
    unittest.main()
