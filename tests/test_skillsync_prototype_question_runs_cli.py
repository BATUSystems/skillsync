"""CLI pytań do eksperta per przebieg (Q-PER-RUN, 1.2.1): `--slug`, wybór przebiegu ze speca, `withdraw`, notatki
o utworzonych plikach i osierocony lock, `purge`, `migrate`.

Kryteria: docs/specs/2026-11-12-q-per-run.md (K1, K4, D4, D6) i dodatki z testu 1.2.0 (withdraw -> stan, wypisanie
utworzonych plików, sprzątanie locka, purge). Fixture `QuestionCase`: repozytorium git, przebieg `demo` z `requirements`
równym SPEC. Migracja chodzi w procesie testu z podstawionym `_move_to_trash`, żeby test nie ruszał kosza użytkownika.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_question_runs_cli.py`.
"""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype as pt  # noqa: E402
from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests import test_skillsync_prototype_question_cli as C  # noqa: E402
from tests.test_skillsync_prototype_question_cli import (QA, QB, QC, RUN, SPEC, QN, QuestionCase,  # noqa: E402,F401
                                                         setUpModule, tearDownModule)

SECOND = 'etap-2'
SPEC2 = 'docs/specs/etap2.md'
QD = 'qn-dddddddddddd'
QE = 'qn-eeeeeeeeeeee'


def run_json(slug, spec):
    return json.dumps({'slug': slug, 'title': slug, 'requirements': spec, 'created': '2026-11-01T09:00:00Z',
                       'generated_by': 'skillsync prototype'})


class RunsCliCase(QuestionCase):
    def setUp(self):
        super().setUp()
        self.make_run(SECOND, SPEC2)

    def make_run(self, slug, spec):
        directory = self.qroot / slug
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'prototype.json').write_text(run_json(slug, spec), encoding='utf-8')

    def run_ops(self, slug):
        path = self.qroot / slug / ps.QUESTIONS_FILE
        return json.loads(path.read_text(encoding='utf-8'))['operations'] if path.exists() else []


class SlugSelectionTests(RunsCliCase):
    def test_add_with_a_slug_writes_to_that_runs_file_only(self):
        report = self.add('Pytanie etapu 2?', QA, '--slug', SECOND)
        self.assertEqual(report['slug'], SECOND)
        self.assertEqual([op['questionId'] for op in self.run_ops(SECOND)], [QA])
        self.assertEqual(self.run_ops(RUN), [], 'drugi przebieg bez pytania')

    def test_add_picks_the_run_whose_requirements_is_the_origin_spec(self):
        self.assertEqual(self.add('A?', QA, origin=SPEC + ':Q1')['slug'], RUN)
        self.assertEqual(self.add('B?', QB, origin=SPEC2 + ':Q1')['slug'], SECOND)

    def test_add_is_blocked_when_no_run_or_several_runs_match_the_spec(self):
        report = self.add('X?', QA, origin='docs/specs/obca.md:Q1', code=2)
        self.assertEqual(report['error'], 'slug_required')
        self.assertEqual(sorted(report['prototypes']), [RUN, SECOND])
        self.make_run('etap-3', SPEC)   # drugi przebieg z tym samym specem
        report = self.add('X?', QA, origin=SPEC + ':Q1', code=2)
        self.assertEqual((report['error'], report['candidates']), ('slug_ambiguous', [RUN, 'etap-3']))
        for slug in (RUN, SECOND, 'etap-3'):
            self.assertEqual(self.run_ops(slug), [], 'BLOCKED nic nie zapisało')
        self.assertEqual(self.add('X?', QA, '--slug', 'etap-3', origin=SPEC + ':Q1')['slug'], 'etap-3')   # jawny slug rozstrzyga

    def test_an_unknown_slug_is_blocked(self):
        for bad in ('nie-ma', 'X', '..', 'etap-2/x'):
            with self.subTest(slug=bad):
                report = self.q('add', '--text', 'Q?', '--assume', 'a', '--origin', SPEC + ':Q1', '--slug', bad, code=2)
                self.assertIn('--slug', report['reason'])

    def test_list_narrows_with_slug_and_joins_without_it(self):
        self.add('A?', QA)
        self.add('B?', QB, '--slug', SECOND)
        everything = self.q('list')
        self.assertEqual({(item['id'], item['slug']) for item in everything['questions']}, {(QA, RUN), (QB, SECOND)})
        self.assertEqual([run['slug'] for run in everything['runs']], [RUN, SECOND])
        narrowed = self.q('list', '--slug', SECOND)
        self.assertEqual([item['id'] for item in narrowed['questions']], [QB])
        self.assertEqual([item['slug'] for item in self.q('export', '--slug', RUN)['questions']], [RUN])

    def test_show_record_and_withdraw_find_the_question_in_any_run(self):
        self.add('B?', QB, '--slug', SECOND)
        self.assertEqual(self.q('show', QB)['questions'][0]['slug'], SECOND)
        withdrawn = self.q('withdraw', QB)
        self.assertEqual((withdrawn['state'], withdrawn['questions'][0]['state'], withdrawn['slug']), ('withdrawn', 'withdrawn', SECOND))
        self.add('C?', QC, '--slug', RUN)
        self.answer(QC)   # odpowiedź eksperta zapisana w pliku przebiegu demo
        recorded = self.q('record', QC, '--spec', SPEC, '--line', 4)
        self.assertEqual(recorded['slug'], RUN)
        self.q('show', 'qn-ffffffffffff', code=1)
        self.q('withdraw', 'qn-ffffffffffff', code=1)

    def test_wait_all_covers_every_run_and_slug_narrows_it(self):
        self.add('B?', QB, '--slug', SECOND)
        self.expert_write_in(SECOND, 'answer', QB, text='tak')
        report = self.q('wait', '--all', '--timeout', '0')
        self.assertEqual((report['questionId'], report['slug']), (QB, SECOND))
        self.q('wait', '--all', '--slug', RUN, '--timeout', '0', code=3)

    def expert_write_in(self, slug, kind, qid, by=C.EWA, **fields):
        store = ps.QuestionsStore(str(self.qroot / slug))
        partial = {'type': kind, 'questionId': qid, 'by': by, 'cid': '%s-%s' % (kind, qid)}
        partial.update(fields)
        store.transaction(lambda snapshot: partial)


class CreatedFilesTests(RunsCliCase):
    def test_first_add_says_which_files_it_created_including_the_lock(self):
        report = self.add()
        self.assertEqual(sorted(report['created']), sorted([
            'docs/prototypes/.gitattributes', 'docs/prototypes/.gitignore', 'docs/prototypes/demo/questions.json',
            'docs/prototypes/demo/questions.json.lock']))
        self.assertTrue(any('questions.json.lock' in note and 'ignored by git' in note for note in report['notes']), report['notes'])
        again = self.add('Druga?', QB)
        self.assertEqual(again['created'], [], 'drugi add niczego nie tworzy')

    def test_an_orphaned_lock_is_removed_by_the_next_add(self):
        self.add()
        data = self.qroot / QN
        data.unlink()   # ktoś usunął plik pytań, lock został
        lock = Path(str(data) + ps.LOCK_SUFFIX)
        self.assertTrue(lock.exists())
        report = self.add('Znów?', QB)
        self.assertTrue(any('orphaned lock' in note for note in report['notes']), report['notes'])
        self.assertEqual([op['questionId'] for op in self.ops()], [QB])
        self.assertIn('docs/prototypes/demo/questions.json.lock', report['created'], 'lock założony na nowo przy zapisie')

    def test_a_lock_beside_an_existing_questions_file_is_left_alone(self):
        self.add()
        lock = Path(str(self.qroot / QN) + ps.LOCK_SUFFIX)
        store = ps.QuestionsStore(str(self.qroot / RUN))
        self.assertFalse(store.sweep_orphan_lock())
        self.assertTrue(lock.exists())

    def test_a_held_orphan_lock_is_not_removed(self):
        self.add()
        data = self.qroot / QN
        data.unlink()
        store = ps.QuestionsStore(str(self.qroot / RUN), lock_timeout=0.2)
        with store.lock():
            self.assertFalse(ps.QuestionsStore(str(self.qroot / RUN)).sweep_orphan_lock())
        self.assertTrue(ps.QuestionsStore(str(self.qroot / RUN)).sweep_orphan_lock())


class PurgeTests(RunsCliCase):
    def seed(self):
        self.add('Bez odpowiedzi?', QA)
        self.add('Wycofane?', QB)
        self.q('withdraw', QB)
        self.add('Odpowiedziane?', QC)
        self.answer(QC)
        self.add('Z komentarzem?', QD)
        self.expert_write('comment', QD, C.EWA, text='uwaga')
        self.add('Inny przebieg?', QE, '--slug', SECOND)

    def test_without_yes_it_only_lists_and_changes_nothing(self):
        self.seed()
        before = self.file_bytes()
        report = self.q('purge', '--slug', RUN)
        self.assertTrue(report['dryRun'])
        self.assertEqual(sorted(item['id'] for item in report['removed']), [QA, QB])
        self.assertEqual(self.file_bytes(), before)

    def test_yes_removes_only_withdrawn_and_untouched_questions(self):
        self.seed()
        second_before = (self.qroot / SECOND / ps.QUESTIONS_FILE).read_bytes()
        report = self.q('purge', '--slug', RUN, '--yes')
        self.assertFalse(report['dryRun'])
        self.assertEqual({item['id']: item['reason'] for item in report['removed']},
                         {QA: 'no answer and no comment', QB: 'withdrawn'})
        self.assertEqual(report['kept'], 2)
        left = {op['questionId'] for op in self.ops()}
        self.assertEqual(left, {QC, QD})
        self.assertEqual({item['id']: item['state'] for item in self.q('list', '--slug', RUN, '--status', 'all')['questions']},
                         {QC: 'answered', QD: 'open'})
        self.assertEqual((self.qroot / SECOND / ps.QUESTIONS_FILE).read_bytes(), second_before, 'inny przebieg nietknięty')
        self.assertEqual(self.q('purge', '--slug', RUN, '--yes')['removed'], [], 'powtórka nic nie usuwa')

    def test_an_answered_or_commented_question_is_untouched_even_when_asked_to_purge(self):
        self.add('Odpowiedziane?', QC)
        self.answer(QC)
        self.add('Z komentarzem?', QD)
        self.expert_write('comment', QD, C.EWA, text='uwaga')
        before = self.file_bytes()
        report = self.q('purge', '--slug', RUN, '--yes')
        self.assertEqual(report['removed'], [])
        self.assertEqual(self.file_bytes(), before)

    def test_a_question_that_a_kept_one_refers_to_is_kept(self):
        self.add('Źródło sporu?', QA)
        self.add('Spór?', QB, '--refers-to', QA)
        self.expert_write('comment', QB, C.EWA, text='uwaga')   # QB zostaje (komentarz), więc QA też
        report = self.q('purge', '--slug', RUN, '--yes')
        self.assertEqual(report['removed'], [])
        self.assertEqual({op['questionId'] for op in self.ops()}, {QA, QB})

    def test_it_needs_a_slug_and_an_existing_run(self):
        self.seed()
        self.q('purge', code=2)
        self.q('purge', '--slug', 'nie-ma', '--yes', code=2)

    def test_it_runs_under_the_file_lock(self):
        self.seed()
        store = ps.QuestionsStore(str(self.qroot / RUN), lock_timeout=0.2)
        with store.lock():
            result = self.run_cli('purge', '--slug', RUN, '--yes', env={})
        self.assertNotIn('Traceback', result.stderr)
        # wstrzymane na locku do jego 5 s limitu albo odrzucone; w każdym razie plik nie został zmieniony w trakcie
        self.assertEqual({op['questionId'] for op in self.ops()}, {QA, QB, QC, QD})


class MigrateTests(RunsCliCase):
    def seed_legacy(self, ops):
        ps.QuestionsStore(str(self.qroot)).append(ops)

    def legacy_ask(self, qid, spec, to='all', **over):
        op = {'type': 'ask', 'questionId': qid, 'by': 'agent', 'cid': 'ask-' + qid, 'text': 'Pytanie ' + qid,
              'blocking': False, 'assumed': 'tak', 'addressees': to, 'refs': [], 'options': [],
              'origin': {'spec': spec, 'q': 'Q1'}}
        op.update(over)
        return op

    def migrate(self, *extra, dry=False, **kwargs):
        root, manifest = pt.read_project(str(self.root))[:2]
        with mock.patch.object(pt, '_move_to_trash', return_value='trashed') as trash:
            report = pt.migrate_questions(root, manifest, dry_run=dry, slug_for=list(extra) or None)
        return report, trash

    def test_splits_by_origin_spec_keeps_the_operations_of_each_question_with_it(self):
        self.seed_legacy([self.legacy_ask(QA, SPEC), self.legacy_ask(QB, SPEC2), {
            'type': 'answer', 'questionId': QB, 'by': C.EWA, 'cid': 'ans-b', 'text': 'tak'}])
        report, trash = self.migrate()
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['migrated'], {RUN: {'operations': 1, 'added': 1}, SECOND: {'operations': 2, 'added': 2}})
        self.assertEqual([op['type'] for op in self.run_ops(SECOND)], ['ask', 'answer'])
        self.assertEqual([op['questionId'] for op in self.run_ops(RUN)], [QA])
        trash.assert_called_once()
        self.assertEqual(Path(trash.call_args[0][0]).name, ps.QUESTIONS_FILE)

    def test_dry_run_writes_nothing_and_keeps_the_old_file(self):
        self.seed_legacy([self.legacy_ask(QA, SPEC)])
        report, trash = self.migrate(dry=True)
        self.assertEqual((report['status'], report['dryRun'], report['plan'][RUN]['added']), ('PASS', True, 1))
        self.assertEqual(self.run_ops(RUN), [])
        trash.assert_not_called()

    def test_ambiguous_or_unassigned_questions_block_everything_and_write_nothing(self):
        self.make_run('etap-3', SPEC)
        self.seed_legacy([self.legacy_ask(QA, SPEC), self.legacy_ask(QB, SPEC2), self.legacy_ask(QC, 'docs/specs/obca.md')])
        report, trash = self.migrate()
        self.assertEqual(report['status'], 'BLOCKED')
        by_id = {item['id']: item for item in report['unassigned']}
        self.assertEqual((by_id[QA]['error'], by_id[QA]['candidates']), ('ambiguous', [RUN, 'etap-3']))
        self.assertEqual(by_id[QC]['error'], 'no_run')
        self.assertNotIn(QB, by_id)
        for slug in (RUN, SECOND, 'etap-3'):
            self.assertEqual(self.run_ops(slug), [], 'bez zgadywania i bez zapisu częściowego')
        trash.assert_not_called()

    def test_slug_for_decides_a_spec_without_a_single_run(self):
        self.make_run('etap-3', SPEC)
        self.seed_legacy([self.legacy_ask(QA, SPEC)])
        report, _trash = self.migrate(SPEC + '=etap-3')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual([op['questionId'] for op in self.run_ops('etap-3')], [QA])
        with self.assertRaises(pt.PrototypeError):
            self.migrate('bez-rowna')
        with self.assertRaises(pt.PrototypeError):
            self.migrate(SPEC + '=nie-ma')

    def test_an_operation_without_its_ask_is_blocked(self):
        self.seed_legacy([self.legacy_ask(QA, SPEC)])
        path = self.qroot / ps.QUESTIONS_FILE
        data = json.loads(path.read_text(encoding='utf-8'))['operations']
        data.append({'id': 'op-' + '9' * 26, 'type': 'comment', 'questionId': QB, 'at': '2026-11-12T10:00:00.000Z',
                     'by': C.EWA, 'cid': 'c-sierota', 'text': 'x'})
        path.write_text(ps.serialize_questions(data), encoding='utf-8')
        report, trash = self.migrate()
        self.assertEqual((report['status'], report['unassigned'][0]['error']), ('BLOCKED', 'orphan_operation'))
        trash.assert_not_called()

    def test_it_is_idempotent_and_a_second_run_has_nothing_to_do(self):
        self.seed_legacy([self.legacy_ask(QA, SPEC)])
        first, _ = self.migrate()
        self.assertEqual(first['status'], 'PASS')
        path = self.qroot / ps.QUESTIONS_FILE
        # `_move_to_trash` jest podstawiony, więc plik leży dalej: drugi bieg nie dubluje operacji w celu
        second, _ = self.migrate()
        self.assertEqual(second['status'], 'PASS')
        self.assertEqual(second['migrated'][RUN]['added'], 0)
        self.assertEqual(len(self.run_ops(RUN)), 1)
        self.assertTrue(path.exists())

    def test_a_different_operation_with_the_same_id_in_the_target_is_a_conflict_and_the_old_file_stays(self):
        self.seed_legacy([self.legacy_ask(QA, SPEC)])
        old = json.loads((self.qroot / ps.QUESTIONS_FILE).read_text(encoding='utf-8'))['operations'][0]
        changed = dict(old, text='inna treść')
        (self.qroot / RUN / ps.QUESTIONS_FILE).write_text(ps.serialize_questions([changed]), encoding='utf-8')
        report, trash = self.migrate()
        self.assertEqual(report['status'], 'FAIL', report)
        trash.assert_not_called()
        self.assertTrue((self.qroot / ps.QUESTIONS_FILE).exists())

    def test_no_legacy_file_means_nothing_to_migrate(self):
        report, trash = self.migrate()
        self.assertEqual((report['status'], report['legacy']), ('PASS', 'absent'))
        trash.assert_not_called()

    def test_a_target_that_loses_an_operation_before_the_read_back_keeps_the_old_file(self):
        self.seed_legacy([self.legacy_ask(QA, SPEC)])
        real = ps.atomic_write_bytes

        def lossy(path, data, *args, **kwargs):
            if str(path).endswith(RUN + '/' + ps.QUESTIONS_FILE):
                data = ps.serialize_questions([]).encode('utf-8')
            return real(path, data, *args, **kwargs)

        with mock.patch.object(ps, 'atomic_write_bytes', lossy):
            report, trash = self.migrate()
        self.assertEqual((report['status'], report['error']), ('BLOCKED', 'verify_failed'))
        trash.assert_not_called()
        self.assertTrue((self.qroot / ps.QUESTIONS_FILE).exists())

    def test_without_a_trash_tool_the_old_file_is_renamed_never_deleted(self):
        path = self.qroot / ps.QUESTIONS_FILE
        path.write_text('{}', encoding='utf-8')
        with mock.patch('shutil.which', return_value=None):
            outcome = pt._move_to_trash(path)
        self.assertEqual(outcome, 'renamed:.questions.json.migrated')
        self.assertFalse(path.exists())
        self.assertTrue((self.qroot / '.questions.json.migrated').exists(), 'nazwa z kropką: serwer jej nie wyda')

    def test_the_cli_form_blocks_without_a_trash_side_effect_when_unassigned(self):
        self.seed_legacy([self.legacy_ask(QA, 'docs/specs/obca.md')])
        report = self.q('migrate', '--dry-run', code=2)
        self.assertEqual(report['error'], 'unassigned')
        self.assertTrue((self.qroot / ps.QUESTIONS_FILE).exists())

    def test_two_targets_with_a_conflict_in_the_second_write_nothing_to_the_first(self):
        self.seed_legacy([self.legacy_ask(QA, SPEC), self.legacy_ask(QB, SPEC2)])
        old = json.loads((self.qroot / ps.QUESTIONS_FILE).read_text(encoding='utf-8'))['operations']
        changed = dict([op for op in old if op['questionId'] == QB][0], text='inna treść')
        (self.qroot / SECOND / ps.QUESTIONS_FILE).write_text(ps.serialize_questions([changed]), encoding='utf-8')
        report, trash = self.migrate()
        self.assertEqual(report['status'], 'FAIL', report)
        self.assertEqual(self.run_ops(RUN), [], 'konflikt w drugim celu wykryty w planie, zanim cokolwiek zapisano')
        trash.assert_not_called()

    def test_an_interrupted_migration_lists_each_question_once(self):
        self.seed_legacy([self.legacy_ask(QA, SPEC), self.legacy_ask(QB, SPEC2)])
        old = json.loads((self.qroot / ps.QUESTIONS_FILE).read_text(encoding='utf-8'))['operations']
        (self.qroot / RUN / ps.QUESTIONS_FILE).write_text(ps.serialize_questions([old[0]]), encoding='utf-8')   # cel zapisany, stary plik został
        listed = self.q('list', '--status', 'all')
        self.assertEqual(sorted((item['id'], item['slug']) for item in listed['questions']), [(QA, RUN), (QB, None)])
        self.assertEqual(listed['legacy']['questions'], 1, 'w starym pliku zostało tylko niezmigrowane')


class DamagedFileCliTests(RunsCliCase):
    def test_a_damaged_run_file_blocks_the_joined_views_loudly_and_names_the_run(self):
        self.add('A?', QA)
        self.add('B?', QB, '--slug', SECOND)
        (self.qroot / SECOND / ps.QUESTIONS_FILE).write_bytes(b'{"operations": [ {"id": ')
        for args in (('list',), ('export',), ('show', QA), ('wait', '--all', '--timeout', '0')):
            with self.subTest(action=args[0]):
                report = self.q(*args, code=2)
                self.assertTrue(report['reason'].startswith('invalid_log'), report)
                self.assertIn(SECOND, report['reason'])
        self.assertEqual([item['id'] for item in self.q('list', '--slug', RUN)['questions']], [QA], 'zdrowy przebieg nadal da się czytać')
        self.assertEqual(self.q('withdraw', QA)['state'], 'withdrawn', 'pytanie znalezione w zdrowym pliku mimo uszkodzenia innego')
        report = self.q('withdraw', QB, code=2)
        self.assertIn(SECOND, report['reason'])


class WaitAfterAcrossRunsTests(RunsCliCase):
    def test_after_an_operation_of_one_run_still_reports_events_of_the_other(self):
        self.add('A?', QA)
        self.add('B?', QB, '--slug', SECOND)
        first = self.expert_write('comment', QA, C.EWA, text='uwaga w demo')   # jej id to punkt odniesienia
        time.sleep(0.01)
        store = ps.QuestionsStore(str(self.qroot / SECOND))
        store.transaction(lambda snapshot: {'type': 'comment', 'questionId': QB, 'by': C.EWA, 'cid': 'cm-b', 'text': 'uwaga w etap-2'})
        report = self.q('wait', '--all', '--after', first['id'], '--timeout', '0')
        self.assertEqual((report['event'], report['questionId'], report['slug']), ('comment', QB, SECOND))
        self.q('wait', '--all', '--after', 'op-' + '0' * 26, '--timeout', '0', code=1)


class GitAttributesPerRunTests(RunsCliCase):
    def test_the_root_attribute_line_covers_every_runs_file(self):
        self.add()
        self.add('B?', QB, '--slug', SECOND)
        for slug in (RUN, SECOND):
            attr = self.git('check-attr', 'merge', 'docs/prototypes/%s/questions.json' % slug).stdout
            self.assertIn('merge: skillsync-questions', attr)
            ignored = self.git('check-ignore', '-q', 'docs/prototypes/%s/questions.json.lock' % slug, check=False)
            self.assertEqual(ignored.returncode, 0, 'lock przebiegu %s jest ignorowany' % slug)
            tracked = self.git('check-ignore', '-q', 'docs/prototypes/%s/questions.json' % slug, check=False)
            self.assertEqual(tracked.returncode, 1, 'plik pytań jest śledzony')


if __name__ == '__main__':
    unittest.main()
