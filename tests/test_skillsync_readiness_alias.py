"""CORE-1130 S4b: aliasy angielskie w readiness (pary sekcji), profilach review i czyszczeniu dopisku po `approve`.
Czytamy oba języki, zapisujemy po polsku; zachowanie dla dokumentów polskich bez zmian."""
import json
import subprocess
import sys
import unittest

from skillsync_workflow import quality, protocol_tokens
from skillsync_workflow.status import set_doc_status
from tests.test_skillsync_workflow import CLI, WorkflowFixture

PL_PLAN = ('# Plan\n## Cel\nc\n## Kryteria\nk\n## TESTY\nt\n## Wiedza\nw\n## Ryzyka\nr\n## Kontrakty\nk\n## Migracja\nm\n')
EN_PLAN = ('# Plan\n## Goal\nc\n## Criteria\nk\n## TESTS\nt\n## Knowledge\nw\n## Risks\nr\n## Contracts\nk\n## Migration\nm\n')
PL_NAMES = ['Cel', 'Kryteria', 'TESTY', 'Wiedza', 'Ryzyka', 'Kontrakty', 'Migracja']
EN_NAMES = ['Goal', 'Criteria', 'TESTS', 'Knowledge', 'Risks', 'Contracts', 'Migration']


class ReadinessAliasTests(WorkflowFixture):
    def configure(self, names):
        self.manifest['workflow'] = {'readiness': {'required_sections': list(names), 'risk_sections': []}}
        self.save()

    def run_readiness(self, plan_text, code):
        self.write('plan.md', plan_text)
        return self.report('readiness', '--files', 'helper.py', '--plan', 'plan.md', code=code)

    def test_pairs_and_tokens_exist(self):
        self.assertEqual(protocol_tokens.READINESS_PAIRS['goal'], ('Cel', 'Goal'))
        self.assertEqual(protocol_tokens.READINESS_PAIRS['tests'], ('TESTY', 'TESTS'))
        self.assertEqual(protocol_tokens.names('review_code'), ('kod', 'code'))
        self.assertEqual(protocol_tokens.names('review_prototype'), ('prototyp', 'prototype'))
        self.assertEqual(protocol_tokens.names('sketch'), ('szkic', 'sketch'))

    def test_polish_plan_with_polish_config_passes(self):  # zielony od razu: kontrola bez zmian
        self.configure(PL_NAMES)
        report = self.run_readiness(PL_PLAN, 0)
        self.assertEqual(report['status'], 'PASS', report)

    def test_english_plan_with_polish_config_passes(self):
        self.configure(PL_NAMES)
        report = self.run_readiness(EN_PLAN, 0)
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(sorted(report['sections']), sorted(PL_NAMES))

    def test_polish_plan_with_english_config_passes(self):
        self.configure(EN_NAMES)
        self.assertEqual(self.run_readiness(PL_PLAN, 0)['status'], 'PASS')

    def test_mixed_language_plan_passes(self):
        self.configure(PL_NAMES)
        mixed = '# Plan\n## Goal\nc\n## Kryteria\nk\n## TESTS\nt\n## Wiedza\nw\n## Risks\nr\n## Kontrakty\nk\n## Migration\nm\n'
        self.assertEqual(self.run_readiness(mixed, 0)['status'], 'PASS')

    def test_alias_header_is_case_insensitive_substring_like_polish(self):
        self.configure(['Cel'])
        self.assertEqual(self.run_readiness('# Plan\n## 1. goal of the work\nc\n', 0)['status'], 'PASS')

    def test_missing_and_empty_sections_stay_blocked_in_both_languages(self):
        self.configure(PL_NAMES)
        for label, text, expected in (
                ('pl missing', PL_PLAN.replace('## Wiedza\nw\n', ''), 'Missing plan section: Wiedza'),
                ('en missing', EN_PLAN.replace('## Knowledge\nw\n', ''), 'Missing plan section: Wiedza'),
                ('pl empty', PL_PLAN.replace('## Wiedza\nw\n', '## Wiedza\n\n'), 'Empty plan section: Wiedza'),
                ('en empty', EN_PLAN.replace('## Knowledge\nw\n', '## Knowledge\n\n'), 'Empty plan section: Wiedza')):
            with self.subTest(label):
                report = self.run_readiness(text, 2)
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertIn(expected, report['issues'])

    def test_english_spec_satisfies_polish_config(self):
        self.configure(PL_NAMES)
        self.write('docs/specs/x.md', EN_PLAN)
        report = self.run_readiness('# Plan\nSpec: docs/specs/x.md\nbody\n', 0)
        self.assertEqual((report['status'], report['spec']), ('PASS', 'docs/specs/x.md'))

    def test_custom_section_without_pair_is_unchanged(self):
        self.configure(['Dowody'])
        self.assertEqual(self.run_readiness('# Plan\n## Evidence\nx\n', 2)['status'], 'BLOCKED')
        self.assertEqual(self.run_readiness('# Plan\n## Dowody\nx\n', 0)['status'], 'PASS')

    def test_pair_members_do_not_leak_to_unrelated_names(self):
        self.configure(['Cel'])
        self.assertEqual(self.run_readiness('# Plan\n## Criteria\nx\n', 2)['status'], 'BLOCKED')

    def test_risk_sections_for_high_risk_read_english_headers(self):
        self.manifest['workflow'] = {'readiness': {'required_sections': ['Cel'], 'risk_sections': ['Kontrakty', 'Migracja']}}
        self.save()
        self.write('plan.md', '# Plan\n## Goal\nc\n## Contracts\nk\n## Migration\nm\n')
        report = self.report('readiness', '--files', 'helper.py', '--plan', 'plan.md', '--risk', 'high')
        self.assertEqual(report['status'], 'PASS', report)


class ReviewProfileAliasTests(WorkflowFixture):
    def review(self, files, *args):
        return self.report('review', '--files', *files, *args)

    def test_profile_aliases_equal_polish_values(self):
        for polish, english, files in (('kod', 'code', ['docs/cli.md']), ('prototyp', 'prototype', ['helper.py']), ('docs', 'docs', ['helper.py'])):
            with self.subTest(english):
                pl = self.review(files, '--profile', polish)
                en = self.review(files, '--profile', english)
                self.assertEqual(en, pl)
                self.assertEqual(en['profile'], polish)  # wartość kanoniczna jak dziś

    def test_default_profile_unchanged(self):
        self.assertEqual(self.review(['helper.py'])['profile'], 'kod')
        self.assertEqual(self.review(['docs/prototypes/d/index.html'])['profile'], 'prototyp')

    def test_unknown_profile_is_refused_by_cli(self):
        result = self.cli('review', '--files', 'helper.py', '--profile', 'nonsense')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_unknown_profile_value_error_lists_allowed_values(self):
        manifest = self.manifest
        for profile in ('nonsense', 'Code', ''):
            with self.subTest(profile):
                with self.assertRaises(ValueError) as caught:
                    quality.review(self.root, manifest, self.layerset(), ['helper.py'], profile=profile)
                message = str(caught.exception)
                for allowed in ('kod', 'docs', 'prototyp', 'code', 'prototype'):
                    self.assertIn(allowed, message)

    def test_in_process_aliases_equal_polish(self):
        for polish, english in (('kod', 'code'), ('prototyp', 'prototype')):
            with self.subTest(english):
                args = (self.root, self.manifest, self.layerset(), ['helper.py'])
                self.assertEqual(quality.review(*args, profile=english), quality.review(*args, profile=polish))


class ApproveAliasTests(WorkflowFixture):
    def doc(self, kind, text):
        path = self.write(f'docs/{kind}s/x.md', text)
        return path

    def approve(self, kind, text):
        path = self.doc(kind, text)
        set_doc_status(self.root, f'docs/{kind}s/x.md', 'approve', folder=f'{kind}s')
        return path.read_text(encoding='utf-8')

    def test_polish_note_removed_for_the_right_consent(self):  # zielony: regresja
        self.assertEqual(self.approve('spec', 'Status: draft (czeka na GO plan)\n'), 'Status: approved\n')
        self.assertEqual(self.approve('plan', 'Status: draft (czeka na GO implement)\nrest'), 'Status: approved\nrest')

    def test_english_note_removed_for_the_right_consent(self):
        cases = (
            ('spec', 'Status: draft (waiting for GO plan)\n', 'Status: approved\n'),
            ('spec', 'Status: **draft** (waiting for GO plan) — 2026-11-10\n', 'Status: **approved** — 2026-11-10\n'),
            ('spec', 'Status: draft (Q answered, waiting for again "GO plan")\n', 'Status: approved\n'),
            ('spec', 'Status: draft (WAITING FOR go plan)\n', 'Status: approved\n'),
            ('plan', 'Status: draft (waiting for GO implement)\nrest', 'Status: approved\nrest'),
            ('plan', 'Status: draft (Waiting For GO Implement)\n', 'Status: approved\n'),
        )
        for kind, before, after in cases:
            with self.subTest(kind=kind, before=before):
                self.assertEqual(self.approve(kind, before), after)

    def test_other_notes_stay_in_both_languages(self):
        cases = (
            ('spec', 'Status: draft (czeka na spec)\n', 'Status: approved (czeka na spec)\n'),
            ('spec', 'Status: draft (waiting for spec)\n', 'Status: approved (waiting for spec)\n'),
            ('plan', 'Status: draft (sketch)\n', 'Status: approved (sketch)\n'),
            ('plan', 'Status: draft (szkic)\n', 'Status: approved (szkic)\n'),
            ('plan', 'Status: draft (sketch — waiting for GO plan)\n', 'Status: approved (sketch — waiting for GO plan)\n'),
        )
        for kind, before, after in cases:
            with self.subTest(kind=kind, before=before):
                self.assertEqual(self.approve(kind, before), after)

    def test_multiple_parentheses_only_the_right_one_is_removed(self):
        for waiting in ('(waiting for GO plan)', '(czeka na GO plan)'):
            with self.subTest(waiting=waiting):
                out = self.approve('spec', f'Status: draft (note) {waiting}\n')
                self.assertEqual(out, 'Status: approved (note)\n')
                out = self.approve('spec', f'Status: draft {waiting} (note)\n')
                self.assertNotIn('GO plan', out)
                self.assertIn('(note)', out)
                self.assertTrue(out.startswith('Status: approved'), out)

    def test_crlf_line_endings(self):
        for before in ('Status: draft (waiting for GO plan)\r\nrest\r\n', 'Status: draft (czeka na GO plan)\r\nrest\r\n'):
            with self.subTest(before=before):
                path = self.doc('spec', '')
                path.write_bytes(before.encode('utf-8'))
                set_doc_status(self.root, 'docs/specs/x.md', 'approve', folder='specs')
                data = path.read_bytes().decode('utf-8')
                self.assertEqual(data, 'Status: approved\r\nrest\r\n')

    def test_wrong_consent_note_stays(self):
        # plan nie jest zgodą „GO plan”, spec nie jest zgodą „GO implement”
        self.assertEqual(self.approve('plan', 'Status: draft (waiting for GO plan)\n'), 'Status: approved (waiting for GO plan)\n')
        self.assertEqual(self.approve('spec', 'Status: draft (waiting for GO implement)\n'), 'Status: approved (waiting for GO implement)\n')

    def test_waiting_for_outside_status_parentheses_is_untouched(self):
        text = ('# Plan\n\nStatus: draft (czeka na spec)\n\nwaiting for GO implement\n(waiting for GO implement)\n'
                'Status: draft (waiting for GO implement)\n')
        out = self.approve('plan', text)
        self.assertEqual(out, text.replace('Status: draft (czeka', 'Status: approved (czeka', 1))
        for text in ('# Plan\n\nStatus: draft — waiting for GO implement\n', '# Spec\n\nStatus: draft waiting for GO plan\n'):
            kind = 'plan' if 'Plan' in text else 'spec'
            with self.subTest(text):
                self.assertEqual(self.approve(kind, text), text.replace('draft', 'approved', 1))

    def test_waiting_text_gives_no_consent_and_approve_refuses_non_draft(self):
        self.doc('plan', '# P\n\nStatus: draft\n\nwaiting for GO implement\n(waiting for GO implement)\n')
        result = subprocess.run([sys.executable, str(CLI), 'status', str(self.root), '--json'], capture_output=True, text=True,
                                timeout=30, cwd=self.root)
        row = {r['path']: r for r in json.loads(result.stdout)['plans']}['docs/plans/x.md']
        self.assertEqual(row['status'], 'draft')
        self.assertEqual(row['needsHuman'], ['approval'])
        for status in ('approved', 'in_progress', 'done'):
            self.doc('plan', f'# P\n\nStatus: {status} (waiting for GO implement)\n')
            with self.subTest(status), self.assertRaises(ValueError):
                set_doc_status(self.root, 'docs/plans/x.md', 'approve', folder='plans')
        text = '# P\n\nStatus: done (waiting for GO plan)\n'
        self.assertEqual(self.doc('plan', text).read_text(encoding='utf-8'), text)


if __name__ == '__main__':
    unittest.main()
