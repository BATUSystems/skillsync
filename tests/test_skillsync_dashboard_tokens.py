"""I18N-1 poprawki części A: tokeny protokołu w parserze dashboardu i statusu (mapa tokenów v1+v2).

Para PL/EN daje ten sam wynik, przypadek mieszany też; polskie dokumenty z angielskimi słowami nie zmieniają wyniku."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from skillsync_workflow import dashboard, i18n, status  # noqa: E402
from tests.test_skillsync_dashboard_i18n import ProjectCase  # noqa: E402


class HeaderTokens(unittest.TestCase):
    def test_run_line_both_languages_and_mixed(self):
        for text in ('# P\n\nPrzebieg: r1\n', '# P\n\nRun: r1\n', '# P\n\nRun: `r1`\n'):
            self.assertEqual(dashboard.parse_header(text)[1]['przebieg'], 'r1', text)
        self.assertEqual(dashboard.parse_header('# P\n\nRun: second\nPrzebieg: first\n')[1]['przebieg'], 'second')
        self.assertEqual(dashboard.parse_header('# P\n\nPrzebieg: first\nRun: second\n')[1]['przebieg'], 'first')

    def test_first_run_line_wins_also_with_a_note(self):
        text = '# P\n\nPrzebieg: a (uwaga)\nPrzebieg: b\n'
        self.assertEqual(dashboard.parse_header(text)[1]['przebieg'], 'a (uwaga)')

    def test_run_line_with_a_note_reads_like_the_polish_one(self):
        for key in ('Przebieg', 'Run'):
            self.assertEqual(dashboard.parse_header(f'# P\n\n{key}: r1 (uwaga)\n')[1]['przebieg'], 'r1 (uwaga)', key)

    def test_english_run_line_outside_the_header_is_not_read(self):
        self.assertNotIn('przebieg', dashboard.parse_header('# P\n\n## A\n\nRun: x\n')[1])

    def test_handoff_replaces_handover(self):
        _, fields = dashboard.parse_header('# P\n\nHandoff: dev\nPrototype: p1\n')
        self.assertEqual((fields['oddanie'], fields['prototyp']), ('dev', 'p1'))
        _, old = dashboard.parse_header('# P\n\nHandover: dev\n')
        self.assertNotIn('oddanie', old)
        self.assertEqual(dashboard.parse_header('# P\n\nOddanie: main\n')[1]['oddanie'], 'main')

    def test_default_phase_name_comes_from_the_catalog(self):
        plan = '# P\n\n## Progress\n\n- [ ] S1 One\n'
        self.assertEqual(dashboard.parse_progress(plan)[0]['name'], 'Kroki')
        self.assertEqual(dashboard.parse_progress(plan, lang='en')[0]['name'], 'Steps')


class KeywordAnchors(unittest.TestCase):
    """English `done:` / `in progress:` count only at the start of a segment; the Polish keywords win everywhere (as before the English aliases were added)."""

    def test_polish_keyword_wins_over_an_english_word_in_an_earlier_segment(self):
        self.assertEqual(dashboard.ids_reported_done('review done: 0 uwag; zrobione: S3'), ['S3'])
        self.assertEqual(dashboard.ids_in_progress('testy in progress: 2/5; w toku: S3'), ['S3'])

    def test_english_word_inside_a_segment_is_not_a_keyword(self):
        self.assertEqual(dashboard.ids_reported_done('review done: S3'), [])
        self.assertIsNone(dashboard.ids_in_progress('plugin progress: S3'))
        self.assertIsNone(dashboard.ids_in_progress('plug-in progress: S3'))

    def test_english_keyword_at_the_start_of_a_segment(self):
        self.assertEqual(dashboard.ids_reported_done('done: S3'), ['S3'])
        self.assertEqual(dashboard.ids_in_progress('in progress: S3'), ['S3'])
        self.assertEqual(dashboard.ids_reported_done('spec ready; Done: S3'), ['S3'])
        self.assertEqual(dashboard.ids_in_progress('spec ready; in progress: S3, S4'), ['S3', 'S4'])
        self.assertEqual(dashboard.report_warnings('spec ready; in progress: S3'), [])


class PlanStateForRun(unittest.TestCase):
    def state(self, run_line):
        import tempfile
        import tmp_paths
        with tempfile.TemporaryDirectory() as tmp:
            plans = tmp_paths.child(tmp, 'docs')
            plans.mkdir()
            plans = tmp_paths.child(plans, 'plans')
            plans.mkdir()
            tmp_paths.child(plans, 'p.md').write_text(f'# Plan: x\n\nStatus: in_progress\n{run_line}\n\n## Progress\n', encoding='utf-8')
            return status.plan_state_for_run(tmp, 'r1')

    def test_polish_run_line(self):
        self.assertEqual(self.state('Przebieg: r1'), 'in_progress')

    # Zależność: paczka CORE-1130-I18N-ALIASES (S2) przenosi quality._PLAN_RUN i test_gate._RUN_LINE na protocol_tokens.RUN_LINE;
    # po jej scaleniu zdjąć expectedFailure.
    @unittest.expectedFailure
    def test_english_run_line(self):
        self.assertEqual(self.state('Run: r1'), 'in_progress')


class SegmentTokens(unittest.TestCase):
    def test_yes_no_retro_item_is_the_same_in_both_languages(self):
        for lang in ('pl', 'en'):
            pl = dashboard.wait_item('TAK P1 / NIE P2', 'checkpoint', None, None, lang)
            en = dashboard.wait_item('YES P1 / NO P2', 'checkpoint', None, None, lang)
            mixed = dashboard.wait_item('TAK P1 / NO P2', 'checkpoint', None, None, lang)
            self.assertEqual(pl['action'].replace('TAK P1 / NIE P2', 'X'), en['action'].replace('YES P1 / NO P2', 'X'), lang)
            self.assertEqual(pl['action'].replace('TAK P1 / NIE P2', 'X'), mixed['action'].replace('TAK P1 / NO P2', 'X'), lang)
            self.assertNotEqual(pl['action'], i18n.t(lang, 'srv.wait.generic', what='TAK P1 / NIE P2'), lang)
        self.assertEqual(dashboard.wait_item('please say YES', 'checkpoint')['action'],
                         i18n.t('pl', 'srv.wait.generic', what='please say YES'))

    def test_running_and_ongoing_stage_markers(self):
        self.assertEqual(dashboard.trwa_marker('running: review, prototype'), ['review', 'prototype'])
        self.assertEqual(dashboard.trwa_marker('ongoing: review'), ['review'])
        self.assertEqual(dashboard.trwa_marker('trwa: review'), ['review'])
        self.assertIsNone(dashboard.trwa_marker('running: none'))

    def test_assumed_marker(self):
        for marker in ('przyjęte', 'assumed', 'accepted'):
            text = f'# S\n\n## Pytania otwarte\n\n- Q1 [ZAŁOŻENIE]: format — {marker}: CSV\n'
            self.assertEqual(dashboard.parse_questions(text)[0], [{'id': 'Q1', 'question': 'format', 'accepted': 'CSV'}], marker)
        english = '# S\n\n## Open questions\n\n- Q1 [ASSUMPTION]: format - assumed: CSV\n- Q2 [RESOLVED 2026-01-01]: x\n- Q3: y?\n'
        assumptions, blocking = dashboard.parse_questions(english)
        self.assertEqual((assumptions[0]['accepted'], [q['id'] for q in blocking]), ('CSV', ['Q3']))

    def test_readiness_date_line(self):
        for label in ('Data', 'Date'):
            self.assertEqual(dashboard.READINESS_DATE_RE.search(f'Readiness: PASS\n{label}: 2026-01-01\n').group('val'), '2026-01-01')

    def test_english_words_inside_polish_text_change_nothing(self):
        self.assertIsNone(dashboard.ids_in_progress('Plugin progress: S3'))
        self.assertEqual(dashboard.ids_reported_done('abandoned: S1'), [])
        self.assertEqual(dashboard.report_warnings('Plugin progress: ok; zrobione: S1'), [])
        self.assertEqual(dashboard.ids_in_progress('in progress: S3'), ['S3'])
        self.assertEqual(dashboard.ids_in_progress('(w toku: S2)'), ['S2'])
        self.assertEqual(dashboard.risky_sections('# S\n\n## Migration notes\n\nThe export API changes.\n'), [])
        self.assertEqual(status.pre_implement_sections('# S\n\n## Migration notes\n\nThe export API changes.\n'), [])

    def test_english_risk_sections_are_not_aliases_yet(self):
        for heading in ('Contracts', 'Migration', 'Data model'):
            self.assertEqual(dashboard.risky_sections(f'# S\n\n## {heading}\n\nThe export API changes.\n'), [], heading)
            self.assertEqual(status.pre_implement_sections(f'# S\n\n## {heading}\n\nThe export API changes.\n'), [], heading)
        self.assertEqual(dashboard.risky_sections('# S\n\n## Kontrakty\n\nZmiana API.\n'), ['Kontrakty'])
        self.assertEqual(dashboard.risky_sections('# S\n\n## Dane\n\nNot applicable.\n'), [])

    def test_result_fallback(self):
        info = {'status': 'done', 'path': 'docs/plans/x.md'}
        self.assertEqual(dashboard.build_result('# P\n\n## Result\n\nAll good\n', info)['text'], 'All good')
        self.assertEqual(dashboard.build_result('# P\n\n## Wynik\n\nOk\n## Result\n\nNo\n', info)['text'], 'Ok')


class StageTokens(unittest.TestCase):
    KEYS = {'stage_spec_draft': ('szkic', 'draft'), 'stage_prototype': ('prototyp', 'prototype'),
            'stage_spec_approved': ('zatwierdzony', 'approved'), 'code_review': ('review kodu', 'code review'),
            'closure': ('zamknięcie', 'closure')}

    def acts(self, lang, token, exists=True):
        trwa = {dashboard._stage_token(token): '09:05'}
        spec = {'exists': exists, 'status': 'draft', 'path': 'docs/specs/x.md', 'prototype': 'p1'}
        acts = dashboard.compute_acts(spec, None, {}, [], {'trwa': trwa}, lang)
        return {stage['key']: stage for act in acts for stage in act['stages']}

    def test_trwa_matches_the_key_and_the_labels_of_both_languages(self):
        for lang in ('pl', 'en'):
            for key, tokens in self.KEYS.items():
                for token in tokens + (key.replace('stage_', ''),):
                    stage = self.acts(lang, token, key != 'stage_spec_draft')[key]
                    self.assertEqual((stage['state'], stage.get('since')), ('active', '09:05'), f'{lang} {token}')

    def test_other_stage_is_not_activated(self):
        stages = self.acts('en', 'prototyp')
        self.assertEqual(stages['stage_spec_draft']['state'], 'done')
        self.assertNotEqual(stages['closure']['state'], 'active')


class StatusTokens(ProjectCase):
    PLAN = '# P\n\nStatus: done\n{run}\n\n## Progress\n\n- [x] S1 Build\n- [ ] S2 {step}\n'

    def context(self):
        return {'mode': 'direct', 'approvals': [], 'runs': {'r1'}, 'root': self.root}

    def test_run_line_in_status(self):
        for run, step in (('Przebieg: r1', 'Odbiór'), ('Run: r1', 'Acceptance'), ('Run: r1', 'Odbiór')):
            plan = self.PLAN.format(run=run, step=step)
            self.assertTrue(status.acceptance_pending('done', plan, self.context(), 'docs/plans/x.md'), run + step)
        late = self.PLAN.replace('## Progress', '## Progress\n\nRun: r1').format(run='', step='Acceptance')
        self.assertFalse(status.acceptance_pending('done', late, self.context(), 'docs/plans/x.md'))

    def test_acceptance_only_as_a_plan_step(self):
        prose = '# P\n\nStatus: done\n\n## Progress\n\nAcceptance criteria were checked.\n\n- [x] S1 Build\n'
        self.assertFalse(status.has_acceptance_step('done', prose))
        self.assertFalse(status.has_acceptance_step('done', '# P\n\n## Notes\n\n- [ ] S2 Acceptance\n'))

    def test_summary_for_humans(self):
        self.assertEqual(status.plan_summary('# P\n\n## Summary for humans\n\nShort.\n'), 'Short.')
        self.assertIsNone(status.plan_summary('# P\n\n## Human summary\n\nShort.\n'))

    def test_pre_implement_reads_the_english_run_line(self):
        self.put('docs/specs/x.md', '# Spec\n\n## Kontrakty\n\nThe export API.\n')
        self.put('.skillsync/runs/r1/pre-implement.md', 'Verdict: BLOCKED\n')
        plan = '# Plan\n\nStatus: draft\nRun: r1\nSpec: docs/specs/x.md\n'
        self.assertEqual(status.pre_implement_state(self.root, 'docs/specs/x.md', plan, 'draft', lang='en')['verdict'], 'BLOCKED')


if __name__ == '__main__':
    unittest.main()
