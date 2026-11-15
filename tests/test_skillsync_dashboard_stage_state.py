"""Stage state (`stages[].state`, `trwa:`), `linkWarning`, `waiting[].who` and `progress.activePercent` of the dashboard
contract (STATUS-160-A; additive fields of `schema: 1`)."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from skillsync_workflow import dashboard  # noqa: E402

FIXTURES = ROOT / 'tests' / 'fixtures' / 'dashboard'
CASES = sorted(path.name for path in FIXTURES.iterdir() if path.is_dir())


class Project(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-stage-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def project(self, case):
        self.count = getattr(self, 'count', 0) + 1
        root = self.base / f'{case}-{self.count}' / 'demo'
        shutil.copytree(FIXTURES / case / 'input', root)
        return root

    def status(self, root):
        return dashboard.build_status(root, prototype_port=1, acceptance=lambda slug: None, writing=lambda slug: None,
                                      ahead=lambda *args: {})

    def checkpoints(self, root):
        return next(root.glob('.skillsync/runs/*/checkpoints.md'))

    def add_checkpoint(self, root, line):
        path = self.checkpoints(root)
        path.write_text(path.read_text(encoding='utf-8') + line + '\n', encoding='utf-8')


class StageState(Project):
    def test_state_matches_done_in_every_fixture(self):
        for case in CASES:
            with self.subTest(case=case):
                for act in self.status(self.project(case))['acts']:
                    for stage in act['stages']:
                        self.assertIn(stage['state'], ('done', 'active', 'planned'))
                        self.assertEqual(stage['done'], stage['state'] == 'done')

    def test_trwa_marks_the_named_stage_active_from_the_newest_checkpoint(self):
        root = self.project('spec-act')
        self.add_checkpoint(root, '- 08:20 trwa: spec_review, Zatwierdzony')
        stages = {stage['key']: stage for stage in self.status(root)['acts'][0]['stages']}
        self.assertEqual((stages['stage_spec_review']['state'], stages['stage_spec_review']['since']), ('active', '08:20'))
        self.assertEqual(stages['stage_spec_approved']['state'], 'active')
        self.assertEqual(stages['stage_spec_draft']['state'], 'done')

    def test_since_is_the_start_of_the_unbroken_run_of_checkpoints(self):
        root = self.project('spec-act')
        self.add_checkpoint(root, '- 08:20 trwa: spec_review')
        self.add_checkpoint(root, '- 08:40 trwa: spec_review; zrobione: nic')
        stage = next(s for s in self.status(root)['acts'][0]['stages'] if s['key'] == 'stage_spec_review')
        self.assertEqual(stage['since'], '08:20')

    def test_a_newer_checkpoint_without_the_segment_or_with_a_wait_clears_it(self):
        for later in ('- 08:30 w toku: spec', '- 08:30 czeka: GO plan'):
            with self.subTest(later=later):
                root = self.project('spec-act')
                self.add_checkpoint(root, '- 08:20 trwa: spec_review')
                self.add_checkpoint(root, later)
                stage = next(s for s in self.status(root)['acts'][0]['stages'] if s['key'] == 'stage_spec_review')
                self.assertNotEqual(stage.get('state'), 'active')

    def test_wait_in_the_same_line_wins_over_trwa(self):
        root = self.project('spec-act')
        self.add_checkpoint(root, '- 08:20 trwa: spec_review; czeka: GO plan')
        stage = next(s for s in self.status(root)['acts'][0]['stages'] if s['key'] == 'stage_spec_review')
        self.assertNotEqual(stage['state'], 'active')

    def test_a_stage_that_is_done_stays_done_whatever_trwa_says(self):
        root = self.project('plan-in-progress')
        self.add_checkpoint(root, '- 10:00 trwa: plan')
        stage = next(s for s in self.status(root)['acts'][1]['stages'] if s['key'] == 'stage_plan')
        self.assertEqual((stage['done'], stage['state']), (True, 'done'))

    def test_unknown_key_changes_nothing(self):
        root = self.project('spec-act')
        before = self.status(root)['acts'][0]['stages']
        self.add_checkpoint(root, '- 08:20 trwa: nie-ma-takiego-etapu')
        after = self.status(root)['acts'][0]['stages']
        self.assertEqual([s['state'] for s in after], [s['state'] for s in before])

    def test_spec_being_prepared_marks_the_draft_stage_active(self):
        stage = self.status(self.project('run-mode'))['acts'][0]['stages'][0]
        self.assertEqual((stage['key'], stage['state']), ('stage_spec_draft', 'active'))


class LinkWarning(Project):
    def test_complete_link_has_no_warning(self):
        for case in ('plan-in-progress', 'spec-act'):
            with self.subTest(case=case):
                self.assertIsNone(self.status(self.project(case))['acts'][0]['linkWarning'])

    def test_missing_run_directory_is_named(self):
        root = self.project('spec-act')
        shutil.rmtree(next(root.glob('.skillsync/runs/*')))
        warning = self.status(root)['acts'][0]['linkWarning']
        self.assertIsNotNone(warning)
        self.assertIn('przebieg', warning.lower())

    def test_spec_pointing_to_a_missing_prototype_is_named(self):
        root = self.project('plan-in-progress')
        spec = next(root.glob('docs/specs/*.md'))
        spec.write_text(spec.read_text(encoding='utf-8').replace('\n\n', '\nPrototyp: docs/prototypes/nie-ma\n\n', 1), encoding='utf-8')
        self.assertIn('prototyp', (self.status(root)['acts'][0]['linkWarning'] or '').lower())


class WaitingWho(Project):
    def test_suffix_gives_role_and_login_and_is_stripped_from_the_text(self):
        root = self.project('spec-act')
        self.add_checkpoint(root, '- 08:30 czeka: GO plan (kto: akceptujący jan.kowalski)')
        item = self.status(root)['waiting'][0]
        self.assertEqual(item['what'], 'GO plan')
        self.assertEqual(item['who'], {'role': 'acceptor', 'login': 'jan.kowalski', 'label': 'akceptujący'})

    def test_unknown_role_keeps_the_text_without_an_error(self):
        root = self.project('spec-act')
        self.add_checkpoint(root, '- 08:30 czeka: pytanie do zespołu (kto: kosmita)')
        who = self.status(root)['waiting'][0]['who']
        self.assertEqual((who['role'], who['label']), (None, 'kosmita'))

    def test_without_a_suffix_the_source_decides(self):
        who = self.status(self.project('spec-act'))['waiting'][0].get('who')
        self.assertEqual(who['role'], 'acceptor')
        self.assertEqual(dashboard.default_who({'source': 'writing'})['role'], 'agent')
        self.assertEqual(dashboard.default_who({'source': 'expert'})['role'], 'expert')
        self.assertEqual(dashboard.default_who({'source': 'acceptance'})['label'], 'odbiór')
        self.assertIsNone(dashboard.default_who({'source': 'checkpoint', 'what': 'inne'}))


class ActivePercent(Project):
    def test_three_segments_never_pass_one_hundred_and_percent_is_unchanged(self):
        for case in CASES:
            with self.subTest(case=case):
                progress = self.status(self.project(case))['progress']
                self.assertLessEqual(progress['percent'] + progress['pendingPercent'] + progress['activePercent'], 100)
                self.assertEqual(progress['percent'], round(100 * progress['done'] / progress['total']) if progress['total'] else 0)

    def test_a_step_in_progress_counts_as_half_a_step(self):
        self.assertEqual(dashboard._active_percent(0, 2, 4), 25)
        self.assertEqual(dashboard._active_percent(2, 1, 5), 10)
        self.assertEqual(dashboard._active_percent(3, 2, 4), 25)
        self.assertEqual(dashboard._active_percent(0, 0, 4), 0)
        self.assertEqual(dashboard._active_percent(0, 1, 0), 0)


class WritingScreens(Project):
    """`prototype.writing.screensPlanned/screensDone`, the waiting item "agent pisze ekrany (N z M, …)" and the active prototype tile."""

    def add_prototype(self, root):
        spec = next(root.glob('docs/specs/*.md')).relative_to(root).as_posix()
        directory = root / 'docs' / 'prototypes' / 'demo'
        directory.mkdir(parents=True)
        (directory / 'prototype.json').write_text('{"slug": "demo", "requirements": "%s"}\n' % spec, encoding='utf-8')

    def status_with(self, root, planned, screens):
        mark = {'since': 1_700_000_000_000, 'expiresAt': 1_700_000_900_000, 'renewedAt': 1_700_000_000_000}
        if planned is not None:
            mark['screensPlanned'] = planned
        return dashboard.build_status(root, prototype_port=1, writing=lambda slug: dict(mark),
                                      acceptance=lambda slug: {'screens': screens, 'accepted': 0, 'stale': 0}, ahead=lambda *args: {})

    def test_screens_done_is_the_acceptance_screen_count_and_planned_is_passed_through(self):
        root = self.project('spec-act')
        self.add_prototype(root)
        writing = self.status_with(root, 12, 5)['prototype']['writing']
        self.assertEqual((writing['screensPlanned'], writing['screensDone']), (12, 5))

    def test_without_a_plan_the_item_has_no_counts_and_the_marker_no_planned_field(self):
        root = self.project('spec-act')
        self.add_prototype(root)
        status = self.status_with(root, None, 3)
        self.assertNotIn('screensPlanned', status['prototype']['writing'])
        item = next(w for w in status['waiting'] if w['source'] == 'writing')
        self.assertNotIn(' z ', item['what'].split('(')[1].split(',')[0])

    def test_unreadable_prototype_leaves_screens_done_out(self):
        root = self.project('spec-act')
        self.add_prototype(root)
        mark = {'since': 1_700_000_000_000, 'expiresAt': 1_700_000_900_000, 'renewedAt': 1_700_000_000_000, 'screensPlanned': 12}
        status = dashboard.build_status(root, prototype_port=1, writing=lambda slug: dict(mark), acceptance=lambda slug: None,
                                        ahead=lambda *args: {})
        writing = status['prototype']['writing']
        self.assertNotIn('screensDone', writing)
        self.assertEqual(writing['screensPlanned'], 12)
        item = next(w for w in status['waiting'] if w['source'] == 'writing')
        self.assertNotIn(' z ', item['what'].split('(')[1].split(',')[0])

    def test_waiting_item_names_done_of_planned(self):
        root = self.project('spec-act')
        self.add_prototype(root)
        item = next(w for w in self.status_with(root, 12, 5)['waiting'] if w['source'] == 'writing')
        self.assertIn('(5 z 12, do ', item['what'])
        self.assertEqual(item['who']['role'], 'agent')

    def test_the_prototype_stage_is_active_while_the_marker_is_valid(self):
        root = self.project('spec-act')
        self.add_prototype(root)
        stages = {s['key']: s for s in self.status_with(root, 12, 5)['acts'][0]['stages']}
        self.assertEqual(stages['stage_prototype']['state'], 'active')
        self.assertTrue(stages['stage_prototype']['since'])


if __name__ == '__main__':
    unittest.main()
