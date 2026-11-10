"""`delivery:` manifest section: validator, `doctor` findings and the `route` field `delivery`."""
import copy
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillsync_workflow.delivery_mode import delivery_info, validate_delivery_mode  # noqa: E402
from tests.test_skillsync_workflow import WorkflowFixture  # noqa: E402

MISSING_MODE = 'delivery.mode: brak, domyślnie pr'


def check(delivery=..., **extra):
    manifest = dict(extra)
    if delivery is not ...:
        manifest['delivery'] = delivery
    return validate_delivery_mode(manifest)


class ValidateDeliveryModeTests(unittest.TestCase):
    def test_valid_modes_with_and_without_base_have_no_findings(self):
        self.assertEqual(check({'mode': 'pr'}), ([], []))
        self.assertEqual(check({'mode': 'pr', 'base': 'main'}), ([], []))
        self.assertEqual(check({'mode': 'direct', 'base': 'release/1.x'}), ([], []))

    def test_missing_section_or_mode_is_a_warning_only(self):
        self.assertEqual(check(), ([], [MISSING_MODE]))
        self.assertEqual(check({}), ([], [MISSING_MODE]))
        self.assertEqual(check({'base': 'main'}), ([], [MISSING_MODE]))
        self.assertEqual(validate_delivery_mode(None), ([], [MISSING_MODE]))

    def test_present_key_without_value_is_an_error(self):
        errors, warnings = check(None)
        self.assertEqual(errors, ['delivery: must be a mapping'])
        self.assertEqual(warnings, [])
        errors, _ = check({'mode': None})
        self.assertEqual([e.split(':')[0] for e in errors], ['delivery.mode'])

    def test_section_that_is_not_a_mapping_is_an_error(self):
        for value in ('direct', ['pr'], 3, True):
            self.assertEqual(check(value), (['delivery: must be a mapping'], []), value)

    def test_mode_outside_the_set_or_not_a_string_is_an_error(self):
        for value in ('PR', 'merge', '', ' pr', 1, True, ['pr'], {'a': 1}):
            errors, _ = check({'mode': value})
            self.assertEqual([e.split(':')[0] for e in errors], ['delivery.mode'], value)

    def test_error_messages_never_contain_the_offending_value(self):
        errors, _ = check({'mode': 'sekret-tryb', 'base': 'zły sekret'})
        self.assertEqual(len(errors), 2)
        self.assertFalse(any('sekret' in e for e in errors), errors)

    def test_valid_base_names(self):
        for base in ('main', 'master', 'develop', 'feature/x-1', 'release/1.0', 'a.b', 'ąę', 'v1.0.0', 'a@b', 'x.locked'):
            self.assertEqual(check({'mode': 'pr', 'base': base}), ([], []), base)

    def test_invalid_base_values_are_errors(self):
        bad = ['', '   ', '-x', '--force', 'a..b', 'a b', 'a\tb', 'a\nb', 'a~1', 'a^', 'a:b', 'a?', 'a*', 'a[0]',
               'a\\b', 'a@{1}', 'a/', '/a', 'a//b', 'x.lock', 'a/.hidden', '.hidden', 'end.', '@', 'a\x7f', 'a\x00b', 'a\u00a0b', 'release.lock/x', 'a./b',
               1, True, None, ['main'], {'x': 1}]
        for base in bad:
            errors, _ = check({'mode': 'pr', 'base': base})
            self.assertEqual([e.split(':')[0] for e in errors], ['delivery.base'], repr(base))

    def test_direct_without_base_warns_but_pr_does_not(self):
        errors, warnings = check({'mode': 'direct'})
        self.assertEqual(errors, [])
        self.assertEqual([w.split(':')[0] for w in warnings], ['delivery.base'])
        self.assertEqual(check({'mode': 'pr'}), ([], []))

    def test_invalid_mode_with_direct_text_does_not_warn_about_base(self):
        _, warnings = check({'mode': 'bad'})
        self.assertEqual(warnings, [])

    def test_unknown_key_is_a_warning(self):
        errors, warnings = check({'mode': 'pr', 'colour': 'red'})
        self.assertEqual(errors, [])
        self.assertEqual([w.split(':')[0] for w in warnings], ['delivery.colour'])

    def test_validator_does_not_modify_the_manifest(self):
        manifest = {'delivery': {'mode': 'direct', 'base': 'main', 'x': 1}}
        before = copy.deepcopy(manifest)
        validate_delivery_mode(manifest)
        delivery_info(manifest)
        self.assertEqual(manifest, before)


class DeliveryInfoTests(unittest.TestCase):
    def test_manifest_default_and_invalid_sources(self):
        self.assertEqual(delivery_info({'delivery': {'mode': 'direct', 'base': 'main'}}),
                         {'mode': 'direct', 'base': 'main', 'source': 'manifest'})
        self.assertEqual(delivery_info({'delivery': {'mode': 'pr'}}), {'mode': 'pr', 'base': None, 'source': 'manifest'})
        self.assertEqual(delivery_info({}), {'mode': 'pr', 'base': None, 'source': 'default'})
        self.assertEqual(delivery_info({'delivery': {'base': 'main'}}), {'mode': 'pr', 'base': 'main', 'source': 'default'})

    def test_invalid_sections_give_no_mode_and_a_value_free_error(self):
        for section in ({'mode': None}, {'mode': 'tajne'}, {'mode': 'pr', 'base': '-tajne'}, 'tajne', None):
            info = delivery_info({'delivery': section})
            self.assertEqual((info['mode'], info['base'], info['source']), (None, None, 'invalid'), section)
            self.assertNotIn('tajne', info['error'])


class DoctorDeliveryTests(WorkflowFixture):
    def delivery_findings(self, report):
        return [(f['level'], f['reason'].split(':')[0]) for f in report['findings'] if f['reason'].startswith('delivery')]

    def test_missing_section_gives_a_warning_and_keeps_the_exit_code(self):
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.delivery_findings(report), [('warning', 'delivery.mode')])

    def test_valid_section_has_no_delivery_finding(self):
        self.manifest['delivery'] = {'mode': 'direct', 'base': 'main'}
        self.save()
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.delivery_findings(report), [])

    def test_the_error_flips_the_exit_code_and_the_warning_does_not(self):
        without = self.report('doctor')
        self.manifest['delivery'] = {'mode': 'direct'}
        self.save()
        warned = self.report('doctor')
        self.assertEqual(warned['status'], without['status'])
        self.assertEqual(self.delivery_findings(warned), [('warning', 'delivery.base')])
        self.manifest['delivery'] = {'mode': 'merge', 'base': '-x'}
        self.save()
        broken = self.report('doctor', code=1)
        self.assertEqual(broken['status'], 'FAIL')
        self.assertEqual(self.delivery_findings(broken), [('error', 'delivery.mode'), ('error', 'delivery.base')])

    def test_null_and_non_mapping_section_are_errors(self):
        for value in (None, 'direct'):
            self.manifest['delivery'] = value
            self.save()
            report = self.report('doctor', code=1)
            self.assertEqual(self.delivery_findings(report), [('error', 'delivery')], value)

    def test_workflow_delivery_is_independent_of_the_section(self):
        self.manifest['workflow'] = {'delivery': {'provider': 'github'}}
        self.save()
        report = self.report('doctor')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(self.delivery_findings(report), [('warning', 'delivery.mode')])


class RouteDeliveryTests(WorkflowFixture):
    def route(self):
        return self.report('route', '--topics', 'x')

    def test_default_source_without_the_key(self):
        self.assertEqual(self.route()['delivery'], {'mode': 'pr', 'base': None, 'source': 'default'})

    def test_manifest_source(self):
        self.manifest['delivery'] = {'mode': 'direct', 'base': 'main'}
        self.save()
        self.assertEqual(self.route()['delivery'], {'mode': 'direct', 'base': 'main', 'source': 'manifest'})

    def test_invalid_section_does_not_block_the_rest_of_the_result(self):
        baseline = self.route()
        self.manifest['delivery'] = {'mode': None}
        self.save()
        result = self.route()
        self.assertEqual(result['delivery']['source'], 'invalid')
        self.assertIsNone(result['delivery']['mode'])
        self.assertIn('error', result['delivery'])
        self.assertEqual({k: v for k, v in result.items() if k != 'delivery'},
                         {k: v for k, v in baseline.items() if k != 'delivery'})

    def test_existing_route_fields_are_unchanged(self):
        result = self.route()
        for key in ('status', 'files', 'topics', 'routes', 'skipped', 'lessons', 'domain', 'note'):
            self.assertIn(key, result)


LIBRARY = Path(__file__).resolve().parents[1] / 'library'

# Zdania sprzed trybu dostarczania: bezwarunkowe „commit/push tylko na polecenie” (K5).
STALE_PHRASES = [
    r'commit tylko na polecenie',
    r'Commit, push i deploy tylko na wyraźne polecenie użytkownika',
    r'commit/push/deploy \(tylko na polecenie użytkownika\)',
    r'Bez jawnego wywołania skill nie startuje',
    r'Nie działa bez /pr-run',
    r'Commit/push/deploy\s+wymagają polecenia użytkownika; samo',
]
# Pliki, w których takie zdanie ma odrębny powód (pierwszeństwo klucza zgłoszenia, retrospektywa,
# zewnętrzne repo lekcji, zamrożony wyjątek prototypów).
STALE_PHRASE_ALLOWED = {
    'skills/feature/references/pr-after-approval.md',
    'skills/feature/references/retro-quality.md',
    'skills/workflow/references/knowledge.md',
    'skills/prototype/references/decisions.md',
    'CHANGELOG.md',
}
# Pliki, w których polecenia merge/complete PR muszą być zakazane (K6).
PR_FORBIDDEN_SCOPE = ('instructions/_base.md', 'skills/feature/', 'skills/pr-run/', 'skills/dispatch/')
PR_ACTION = re.compile(r'gh pr merge|--auto-complete|--status completed|merge i complete|merge/complete|complete PR', re.I)
NEGATION = re.compile(r'nigdy|zakaz|nie |poza zakresem|tylko człowiek|scala człowiek', re.I)
BASE_REQUIRED = ['READY', 'READY!', 'CP!', 'CPD!', 'delivery.mode', 'route', '`pr`', '`direct`']


def library_texts(root=LIBRARY):
    return {p.relative_to(root).as_posix(): p.read_text(encoding='utf-8') for p in sorted(root.rglob('*.md'))}


def stale_phrase_hits(texts):
    return [f'{name}: {m.group(0)}'
            for name, text in texts.items() if name not in STALE_PHRASE_ALLOWED
            for pattern in STALE_PHRASES for m in re.finditer(pattern, text)]


def unguarded_pr_actions(texts):
    hits = []
    for name, text in texts.items():
        if not name.startswith(PR_FORBIDDEN_SCOPE):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if PR_ACTION.search(line) and not NEGATION.search(line):
                hits.append(f'{name}:{number}')
    return hits


def missing_in_base(texts):
    base = texts.get('instructions/_base.md', '')
    return [token for token in BASE_REQUIRED if token not in base]


class LibraryDeliveryConsistencyTests(unittest.TestCase):
    """Strażnik spójności biblioteki z trybem dostarczania (K5, K6)."""

    @classmethod
    def setUpClass(cls):
        cls.texts = library_texts()

    def test_library_is_readable(self):
        self.assertIn('instructions/_base.md', self.texts)
        self.assertIn('skills/pr-run/SKILL.md', self.texts)

    def test_no_unconditional_commit_only_on_command_phrases(self):
        self.assertEqual(stale_phrase_hits(self.texts), [])

    def test_base_instruction_defines_modes_and_ready_words(self):
        self.assertEqual(missing_in_base(self.texts), [])

    def test_mode_aware_skills_mention_ready_or_modes(self):
        for name in ('skills/pr-run/SKILL.md', 'skills/feature/SKILL.md', 'skills/dispatch/SKILL.md',
                     'skills/feature/references/worker-mode.md', 'skills/feature/references/multi-session.md'):
            self.assertRegex(self.texts[name], r'READY|`pr`', name)

    def test_no_instruction_allows_merge_or_complete_of_a_pr(self):
        self.assertEqual(unguarded_pr_actions(self.texts), [])

    def test_guard_detects_a_restored_pre_mode_sentence(self):
        mutated = dict(self.texts)
        mutated['skills/feature/SKILL.md'] += '\nCommit, push i deploy tylko na wyraźne polecenie użytkownika.\n'
        self.assertEqual(len(stale_phrase_hits(mutated)), len(stale_phrase_hits(self.texts)) + 1)
        mutated = dict(self.texts)
        mutated['skills/pr-run/SKILL.md'] += '\nPo PASS wykonaj gh pr merge.\n'
        self.assertEqual(len(unguarded_pr_actions(mutated)), len(unguarded_pr_actions(self.texts)) + 1)
        mutated = dict(self.texts)
        mutated['instructions/_base.md'] = mutated['instructions/_base.md'].replace('READY!', 'RDY')
        self.assertEqual(missing_in_base(mutated), ['READY!'])


class CpWordSemantics(unittest.TestCase):
    """CP pushes the working branch; handing over to the base needs an explicit word or the plan's `Oddanie:` line."""

    def test_core_defines_cp_as_the_working_branch_push(self):
        base = (Path(__file__).resolve().parents[1] / 'library/instructions/_base.md').read_text(encoding='utf-8')
        rule = ' '.join(base[base.index('Słowa zgody: `CP`'):base.index('`CPD` —')].split())
        self.assertIn('push bieżącej gałęzi roboczej na jej zdalny odpowiednik', rule)
        self.assertIn('nigdy na `delivery.base`, `main`, `master` ani `develop`', rule)
        self.assertIn('`CP <gałąź>`', rule)
        self.assertIn('`Oddanie: <gałąź>`', rule)
        self.assertIn('bez odpowiedzi nie pushujesz', rule)
        self.assertNotIn('`CP` — w `direct` push `robocza:base`', base)


if __name__ == '__main__':
    unittest.main()
