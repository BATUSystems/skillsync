"""`expertQuestions` of the dashboard contract: open questions to a domain expert counted across ALL runs of a project."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child, descend  # noqa: E402
from skillsync_workflow import dashboard, dashboard_http, prototype_server  # noqa: E402


def ask(op_id, qid, blocking=True, at='2026-01-11T09:00:00Z', origin=None):
    op = {'id': op_id, 'type': 'ask', 'questionId': qid, 'at': at, 'by': 'agent', 'text': 'Pytanie?', 'blocking': blocking,
          'assumed': None if blocking else 'tak', 'addressees': [], 'refs': [], 'options': []}
    if origin is not None:
        op['origin'] = origin
    return op


def reply(op_id, qid, kind, at='2026-01-11T10:00:00Z'):
    return {'id': op_id, 'type': kind, 'questionId': qid, 'at': at, 'by': 'expert'}


def document(operations):
    return json.dumps({'questionsFormat': 1, 'operations': operations}, ensure_ascii=False)


class ExpertQuestions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-expert-questions-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()   # macOS: /var/... is a link to /private/var/..., the board resolves its root

    def run_dir(self, slug, text=None, operations=None, prototype=True):
        directory = child(self.root / 'docs' / 'prototypes', slug)
        directory.mkdir(parents=True)
        if prototype:
            (directory / 'prototype.json').write_text('{}\n', encoding='utf-8')
        if operations is not None:
            text = document(operations)
        if text is not None:
            (directory / 'questions.json').write_text(text, encoding='utf-8')
        return directory

    def summary(self):
        return dashboard.build_expert_questions(self.root)

    def test_no_prototypes_means_zero(self):
        self.assertEqual(self.summary(), {'open': 0, 'blocking': 0, 'runs': [], 'items': []})

    def test_sums_every_run_and_lists_only_runs_with_open_questions(self):
        self.run_dir('run-a', operations=[ask('o1', 'qn-000001'), ask('o2', 'qn-000002', blocking=False), ask('o3', 'qn-000003'),
                                          reply('o4', 'qn-000003', 'answer')])
        self.run_dir('run-b', operations=[ask('o5', 'qn-000004'), ask('o6', 'qn-000005'), reply('o7', 'qn-000005', 'withdraw')])
        self.run_dir('run-c', operations=[ask('o8', 'qn-000006'), reply('o9', 'qn-000006', 'answer')])
        self.run_dir('run-d', operations=[])
        got = self.summary()
        self.assertEqual(len(got.pop('items')), 6)   # every question of every run, open or closed (newest first)
        self.assertEqual(got, {'open': 3, 'blocking': 2, 'runs': [
            {'slug': 'run-a', 'open': 2, 'blocking': 1, 'url': '/_experts/run-a', 'addressees': 'all'},
            {'slug': 'run-b', 'open': 1, 'blocking': 1, 'url': '/_experts/run-b', 'addressees': 'all'}]})

    def test_counts_match_the_prototype_server_fold(self):
        operations = [ask('o1', 'qn-000001'), ask('o2', 'qn-000002', blocking=False), ask('o3', 'qn-000003'), ask('o4', 'qn-000004'),
                      ask('o5', 'qn-000004'),  # the same question asked twice: one question
                      reply('o6', 'qn-000003', 'answer', '2026-01-11T10:00:00Z'), reply('o7', 'qn-000003', 'withdraw', '2026-01-11T10:05:00Z'),
                      reply('o8', 'qn-000004', 'withdraw', '2026-01-11T10:00:00Z'), reply('o9', 'qn-000004', 'answer', '2026-01-11T10:05:00Z'),
                      reply('o10', 'qn-unknown', 'answer'), reply('o11', 'qn-000001', 'comment'), reply('o12', 'qn-000001', 'recorded'),
                      {'type': 'ask', 'questionId': 'qn-000009', 'at': '2026-01-11T09:00:00Z'}, 'junk', 7,
                      reply('o13', 'qn-000002', 'answer', '2026-01-11T08:00:00Z')]  # answer stamped before its ask still closes it
        self.run_dir('run-a', operations=operations)
        fold = prototype_server.fold_questions([op for op in operations if isinstance(op, dict)])
        server_open = [q for q in fold['questions'] if q['state'] == 'open']
        got = self.summary()
        self.assertEqual((got['open'], got['blocking']), (len(server_open), sum(1 for q in server_open if q['blocking'])))
        self.assertEqual((got['open'], got['blocking']), (1, 1))

    def test_edge_cases_follow_the_server_fold(self):
        operations = [
            ask('o2', 'qn-000001', blocking=False, at='2026-01-11T09:05:00Z'), ask('o1', 'qn-000001', blocking=True, at='2026-01-11T09:00:00Z'),  # earlier ask wins
            ask('o3', 'qn-000002'), reply('o3', 'qn-000002', 'answer')]  # an answer with the id of the ask is skipped
        self.run_dir('run-a', operations=operations)
        fold = prototype_server.fold_questions(operations)
        server_open = [q for q in fold['questions'] if q['state'] == 'open']
        got = self.summary()
        self.assertEqual((got['open'], got['blocking']), (len(server_open), sum(1 for q in server_open if q['blocking'])))
        self.assertEqual((got['open'], got['blocking']), (2, 2))

    def test_format_marker_must_be_the_integer_one(self):
        for index, marker in enumerate(('true', '1.0', '"1"')):
            with self.subTest(marker=marker):
                directory = self.run_dir('run-fmt-%d' % index, text='{"questionsFormat":%s,"operations":%s}' % (marker, json.dumps([ask('o1', 'qn-000001')])))
                self.assertNotIn(directory.name, [run['slug'] for run in self.summary()['runs']])

    def test_unreadable_or_unsafe_runs_are_skipped(self):
        self.run_dir('run-ok', operations=[ask('o1', 'qn-000001')])
        self.run_dir('run-broken', text='{not json')
        self.run_dir('run-format', text=json.dumps({'questionsFormat': 2, 'operations': [ask('o2', 'qn-000002')]}))
        self.run_dir('run-ops', text=json.dumps({'questionsFormat': 1, 'operations': {}}))
        self.run_dir('run-no-prototype', operations=[ask('o3', 'qn-000003')], prototype=False)
        self.run_dir('Bad_Slug', operations=[ask('o4', 'qn-000004')])
        big = self.run_dir('run-big', operations=[])
        (big / 'questions.json').write_text(document([ask('o5', 'qn-000005')]) + ' ' * (dashboard.EXPERT_QUESTIONS_MAX_BYTES + 1), encoding='utf-8')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'questions.json').write_text(document([ask('o6', 'qn-000006')]), encoding='utf-8')
        (outside / 'prototype.json').write_text('{}\n', encoding='utf-8')
        os.symlink(outside, self.root / 'docs' / 'prototypes' / 'run-link')
        linked = self.run_dir('run-linked-file', operations=[])
        (linked / 'questions.json').unlink()
        os.symlink(outside / 'questions.json', linked / 'questions.json')
        self.assertEqual([run['slug'] for run in self.summary()['runs']], ['run-ok'])

    def test_byte_order_mark_is_accepted(self):
        directory = self.run_dir('run-a', operations=[])
        (directory / 'questions.json').write_bytes(b'\xef\xbb\xbf' + document([ask('o1', 'qn-000001')]).encode('utf-8'))
        self.assertEqual(self.summary()['open'], 1)

    def test_status_carries_the_field_independent_of_the_visible_spec(self):
        self.run_dir('run-a', operations=[ask('o1', 'qn-000001'), ask('o2', 'qn-000002', blocking=False)])
        specs = self.root / 'docs' / 'specs'
        specs.mkdir(parents=True)
        (specs / '2026-01-12-other.md').write_text('# Inny spec\n\nStatus: draft\n\n## Pytania otwarte\n\n- Q1: coś?\n', encoding='utf-8')
        (self.root / '.github').mkdir()
        (self.root / '.github' / '.skillsync.yaml').write_text('skills: [dashboard]\n', encoding='utf-8')
        status = dashboard.build_status(self.root, approvals=False)
        expert = dict(status['expertQuestions'])
        self.assertIsNone(expert.pop('current'))   # the spec binds no prototype and no question
        self.assertEqual([item['id'] for item in expert.pop('items')], ['qn-000002', 'qn-000001'])
        self.assertEqual(expert, {'open': 2, 'blocking': 1, 'runs': [
            {'slug': 'run-a', 'open': 2, 'blocking': 1, 'url': '/_experts/run-a', 'addressees': 'all'}]})
        self.assertEqual(status['blockingQuestions']['count'], 1)  # the spec's own list is a separate field

    def test_watcher_follows_questions_files(self):
        directory = self.run_dir('run-a', operations=[ask('o1', 'qn-000001')])
        board = dashboard_http.Board(self.root, poll=0.2, heartbeat=0.5, prototype_port=1)
        self.assertIn(directory / 'questions.json', board.watched_paths())

    def test_watcher_resolves_a_root_given_through_a_symbolic_link(self):
        """The board resolves its root on purpose; a project opened through a link (macOS temp directories) lists resolved paths."""
        directory = self.run_dir('run-a', operations=[ask('o1', 'qn-000001')])
        link = self.root.parent / (self.root.name + '-link')
        try:
            os.symlink(self.root, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('brak dowiązań symbolicznych')
        self.addCleanup(lambda: os.path.lexists(link) and os.unlink(link))
        board = dashboard_http.Board(link, poll=0.2, heartbeat=0.5, prototype_port=1)
        paths = board.watched_paths()
        self.assertIn(directory / 'questions.json', paths)
        self.assertNotIn(link / 'docs' / 'prototypes' / 'run-a' / 'questions.json', paths)


SPEC = 'docs/specs/2026-02-01-demo.md'
OTHER_SPEC = 'docs/specs/2026-02-02-other.md'


def of(spec, label='Q1'):
    return {'spec': spec, 'q': label}


class CurrentQuestions(unittest.TestCase):
    """`expertQuestions.current`: prototypes and questions of the run of the visible spec, bound by the spec path (`requirements`, `origin.spec`)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-current-questions-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / '.github').mkdir()
        (self.root / '.github' / '.skillsync.yaml').write_text('skills: [dashboard]\n', encoding='utf-8')

    def spec(self, header='', rel=SPEC):
        path = descend(self.root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# Spec: demo\n\nStatus: draft\n' + header + '\n## Problem\n\nTekst.\n', encoding='utf-8')

    def prototype(self, slug, requirements=None, operations=None, text=None, meta=True):
        directory = child(self.root / 'docs' / 'prototypes', slug)
        directory.mkdir(parents=True, exist_ok=True)
        if meta:
            data = {'schema': 'prototype/1', 'slug': slug}
            if requirements is not None:
                data['requirements'] = requirements
            (directory / 'prototype.json').write_text(json.dumps(data), encoding='utf-8')
        if operations is not None:
            text = document(operations)
        if text is not None:
            (directory / 'questions.json').write_text(text, encoding='utf-8')
        return directory

    def status(self):
        return dashboard.build_status(self.root, approvals=False)

    def current(self):
        status = self.status()
        self.assertIn('current', status['expertQuestions'])
        return status['expertQuestions']['current']

    def ids(self, current):
        return [item['id'] for item in current['items']]

    def test_no_spec_of_the_run_means_null(self):
        self.prototype('run-a', requirements=SPEC, operations=[ask('o1', 'qn-000001', origin=of(SPEC))])
        status = self.status()   # no spec, no plan: mode none
        self.assertEqual(status['mode'], 'none')
        self.assertIsNone(status['expertQuestions']['current'])
        self.assertEqual(status['expertQuestions']['open'], 1)   # the project view is untouched

    def test_no_prototypes_directory_means_null(self):
        self.spec()
        self.assertIsNone(self.current())

    def test_a_prototypes_directory_that_is_a_link_means_null(self):
        self.spec()
        outside = self.root / 'outside'
        (outside / 'run-a').mkdir(parents=True)
        (outside / 'run-a' / 'prototype.json').write_text(json.dumps({'requirements': SPEC}), encoding='utf-8')
        (self.root / 'docs').mkdir(exist_ok=True)
        os.symlink(outside, self.root / 'docs' / 'prototypes', target_is_directory=True)
        self.assertIsNone(self.current())

    def test_spec_without_prototypes_and_questions_means_null(self):
        self.spec()
        self.prototype('run-other', requirements=OTHER_SPEC, operations=[ask('o1', 'qn-000001', origin=of(OTHER_SPEC))])
        self.prototype('run-none', requirements=OTHER_SPEC)
        self.assertIsNone(self.current())

    def test_a_plan_whose_spec_file_is_missing_means_null(self):
        plans = self.root / 'docs' / 'plans'
        plans.mkdir(parents=True)
        (plans / '2026-02-01-demo.md').write_text('# Plan: demo\n\nStatus: in_progress\nSpec: docs/specs/missing.md\n\n## Progress\n\n'
                                                  '### Faza 1: A\n- [ ] F1.1 Krok\n', encoding='utf-8')
        self.prototype('run-a', requirements='docs/specs/missing.md', operations=[ask('o1', 'qn-000001', origin=of('docs/specs/missing.md'))])
        status = self.status()
        self.assertEqual((status['mode'], status['spec']['exists']), ('plan', False))
        self.assertIsNone(status['expertQuestions']['current'])

    def test_a_prototype_bound_by_requirements_is_current_even_without_questions(self):
        self.spec()
        self.prototype('run-a', requirements=SPEC)
        self.prototype('run-b', requirements=OTHER_SPEC)
        current = self.current()
        self.assertEqual(set(current), {'spec', 'prototypes', 'open', 'blocking', 'items'})
        self.assertEqual((current['spec'], current['open'], current['blocking'], current['items']), (SPEC, 0, 0, []))
        self.assertEqual([row['slug'] for row in current['prototypes']], ['run-a'])
        self.assertEqual(set(current['prototypes'][0]), {'slug', 'iteration', 'screens', 'accepted', 'stale', 'threads', 'commentsAfterAcceptance', 'writing'})

    def test_a_prototype_named_in_the_header_is_current(self):
        self.spec('Prototyp: run-named\n')
        self.prototype('run-named', requirements=OTHER_SPEC, operations=[ask('o1', 'qn-000001', origin=of(OTHER_SPEC))])
        self.prototype('run-b', requirements=OTHER_SPEC)
        current = self.current()
        self.assertEqual([row['slug'] for row in current['prototypes']], ['run-named'])
        self.assertEqual(current['items'], [])   # a question of another spec in the shared prototype is not the run's

    def test_a_header_name_outside_the_slug_pattern_or_without_a_directory_is_skipped(self):
        self.prototype('run-a', requirements=SPEC)
        for name in ('nie dotyczy', 'Bad_Slug', 'run-missing'):
            with self.subTest(name=name):
                self.spec('Prototyp: %s\n' % name)
                status = self.status()
                self.assertEqual([row['slug'] for row in status['expertQuestions']['current']['prototypes']], ['run-a'])

    def test_several_prototypes_of_one_spec_are_all_current(self):
        self.spec()
        self.prototype('run-b', requirements=SPEC, operations=[ask('o1', 'qn-000001', at='2026-01-11T09:00:00Z', origin=of(SPEC, 'Q1'))])
        self.prototype('run-a', requirements=SPEC, operations=[ask('o2', 'qn-000002', at='2026-01-11T09:05:00Z', blocking=False, origin=of(SPEC, 'Q2'))])
        current = self.current()
        self.assertEqual([row['slug'] for row in current['prototypes']], ['run-a', 'run-b'])
        self.assertEqual(self.ids(current), ['qn-000002', 'qn-000001'])   # newest first
        self.assertEqual((current['open'], current['blocking']), (2, 1))

    def test_a_question_of_the_spec_saved_in_a_foreign_prototype_is_current(self):
        self.spec()
        self.prototype('run-shared', requirements=OTHER_SPEC, operations=[
            ask('o1', 'qn-000001', origin=of(SPEC)), ask('o2', 'qn-000002', origin=of(OTHER_SPEC))])
        current = self.current()
        self.assertEqual(self.ids(current), ['qn-000001'])
        self.assertEqual(current['items'][0]['runSlug'], 'run-shared')
        self.assertEqual(current['items'][0]['url'], '/_experts/run-shared')
        self.assertEqual(current['prototypes'], [])   # the prototype is not bound to the spec
        self.assertEqual((current['open'], current['blocking']), (1, 1))

    def test_a_foreign_question_in_a_shared_prototype_does_not_count(self):
        self.spec()
        self.prototype('run-a', requirements=SPEC, operations=[
            ask('o1', 'qn-000001', origin=of(SPEC)), ask('o2', 'qn-000002', origin=of(OTHER_SPEC)),
            ask('o3', 'qn-000003', origin=of(OTHER_SPEC), blocking=False)])
        current = self.current()
        self.assertEqual(self.ids(current), ['qn-000001'])
        self.assertEqual((current['open'], current['blocking']), (1, 1))
        expert = self.status()['expertQuestions']
        self.assertEqual((expert['open'], expert['blocking']), (3, 2))   # the project view still counts all of them

    def test_an_ask_without_origin_stays_in_the_project_view_only(self):
        self.spec()
        self.prototype('run-a', requirements=SPEC, operations=[ask('o1', 'qn-000001'), ask('o2', 'qn-000002', origin={}),
                                                              ask('o3', 'qn-000003', origin={'spec': 7}), ask('o4', 'qn-000004', origin='x')])
        current = self.current()
        self.assertEqual((current['items'], current['open']), ([], 0))
        self.assertEqual(self.status()['expertQuestions']['open'], 4)

    def test_origin_spec_is_compared_as_a_normalized_relative_path(self):
        self.spec()
        self.prototype('run-a', requirements=SPEC, operations=[
            ask('o1', 'qn-000001', origin=of('./' + SPEC)), ask('o2', 'qn-000002', origin=of('docs//specs/../specs/2026-02-01-demo.md')),
            ask('o3', 'qn-000003', origin=of('docs/specs/2026-02-01-demo.md/')), ask('o4', 'qn-000004', origin=of('/' + SPEC)),
            ask('o5', 'qn-000005', origin=of('docs/specs/2026-02-01-DEMO.md')), ask('o6', 'qn-000006', origin=of(SPEC))])
        self.assertEqual(sorted(self.ids(self.current())), ['qn-000001', 'qn-000002', 'qn-000003', 'qn-000006'])

    def test_requirements_and_origin_are_normalized_the_same_way(self):
        self.spec()
        self.prototype('run-a', requirements='./' + SPEC, operations=[ask('o1', 'qn-000001', origin=of(SPEC.replace('/', '\\')))])
        current = self.current()
        self.assertEqual([row['slug'] for row in current['prototypes']], ['run-a'])
        self.assertEqual(self.ids(current), ['qn-000001'])

    def test_a_deeply_nested_prototype_json_does_not_break_the_status(self):
        self.spec()
        self.prototype('run-a', requirements=SPEC)
        deep = self.prototype('run-deep', meta=False)
        (deep / 'prototype.json').write_text('[' * 30000, encoding='utf-8')
        self.assertEqual([row['slug'] for row in self.current()['prototypes']], ['run-a'])

    def test_closed_questions_are_items_but_not_open(self):
        self.spec()
        self.prototype('run-a', requirements=SPEC, operations=[
            ask('o1', 'qn-000001', origin=of(SPEC)), ask('o2', 'qn-000002', origin=of(SPEC)), ask('o3', 'qn-000003', origin=of(SPEC)),
            reply('o4', 'qn-000002', 'answer'), reply('o5', 'qn-000003', 'withdraw')])
        current = self.current()
        self.assertEqual({item['id']: item['state'] for item in current['items']},
                         {'qn-000001': 'open', 'qn-000002': 'answered', 'qn-000003': 'withdrawn'})
        self.assertEqual((current['open'], current['blocking']), (1, 1))

    def test_origin_does_not_leave_the_status_and_items_have_the_project_shape(self):
        self.spec()
        self.prototype('run-a', requirements=SPEC, operations=[ask('o1', 'qn-000001', origin=of(SPEC))])
        status = self.status()
        self.assertNotIn('origin', json.dumps(status['expertQuestions']))
        self.assertEqual(status['expertQuestions']['current']['items'], status['expertQuestions']['items'])
        self.assertEqual(set(status['expertQuestions']['current']['items'][0]),
                         {'id', 'runSlug', 'text', 'blocking', 'addressees', 'state', 'at', 'url'})

    def test_a_broken_or_oversized_file_is_skipped_and_the_rest_is_counted(self):
        self.spec()
        self.prototype('run-ok', requirements=SPEC, operations=[ask('o1', 'qn-000001', origin=of(SPEC))])
        self.prototype('run-broken', requirements=SPEC, text='{not json')
        big = self.prototype('run-big', requirements=SPEC, operations=[])
        (big / 'questions.json').write_text(document([ask('o2', 'qn-000002', origin=of(SPEC))]) + ' ' * (dashboard.EXPERT_QUESTIONS_MAX_BYTES + 1),
                                            encoding='utf-8')
        current = self.current()
        self.assertEqual(self.ids(current), ['qn-000001'])
        self.assertEqual([row['slug'] for row in current['prototypes']], ['run-big', 'run-broken', 'run-ok'])   # the prototypes stay bound

    def test_links_are_not_followed(self):
        self.spec()
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'prototype.json').write_text(json.dumps({'requirements': SPEC}), encoding='utf-8')
        (outside / 'questions.json').write_text(document([ask('o1', 'qn-000001', origin=of(SPEC))]), encoding='utf-8')
        (self.root / 'docs' / 'prototypes').mkdir(parents=True)
        os.symlink(outside, self.root / 'docs' / 'prototypes' / 'run-link')
        linked = self.prototype('run-linked-file', requirements=SPEC, operations=[])
        (linked / 'questions.json').unlink()
        os.symlink(outside / 'questions.json', linked / 'questions.json')
        meta = self.prototype('run-linked-meta', operations=[ask('o2', 'qn-000002', origin=of(SPEC))], meta=False)
        (meta / 'prototype.json').symlink_to(outside / 'prototype.json')
        self.prototype('Bad_Slug', requirements=SPEC, operations=[ask('o3', 'qn-000003', origin=of(SPEC))])
        current = self.current()
        self.assertEqual([row['slug'] for row in current['prototypes']], ['run-linked-file'])   # only the plain directory with a plain meta
        self.assertEqual(current['items'], [])

    def test_more_than_fifty_prototypes_and_questions_in_the_project(self):
        self.spec()
        for index in range(55):   # the project view keeps the first 50 slugs; the run's rows are counted without that limit
            self.prototype('p%02d' % index, requirements=OTHER_SPEC if index < 52 else SPEC)
        self.prototype('z-mine', requirements=SPEC, operations=[
            ask('m%03d' % index, 'qn-%06d' % index, at='2026-01-11T09:%02d:00Z' % (index % 60), origin=of(SPEC if index % 2 else OTHER_SPEC))
            for index in range(1, 111)])
        status = self.status()
        self.assertEqual(len(status['prototypes']), 50)
        self.assertNotIn('z-mine', [row['slug'] for row in status['prototypes']])
        current = status['expertQuestions']['current']
        self.assertEqual([row['slug'] for row in current['prototypes']], ['p52', 'p53', 'p54', 'z-mine'])
        self.assertEqual((len(current['items']), current['open']), (50, 55))   # items are limited to 50, the counters are not
        self.assertEqual(len(status['expertQuestions']['items']), 50)

    def test_the_rows_of_the_run_are_limited_to_fifty(self):
        self.spec()
        for index in range(53):
            self.prototype('p%02d' % index, requirements=SPEC)
        current = self.current()
        self.assertEqual(len(current['prototypes']), 50)
        self.assertEqual(current['prototypes'][0]['slug'], 'p00')

    def test_each_questions_file_is_read_once_per_status(self):
        self.spec()
        self.prototype('run-a', requirements=SPEC, operations=[ask('o1', 'qn-000001', origin=of(SPEC))])
        self.prototype('run-b', requirements=OTHER_SPEC, operations=[ask('o2', 'qn-000002', origin=of(SPEC))])
        real = dashboard._read_questions
        seen = []

        def spy(path):
            seen.append(Path(path).parent.name)
            return real(path)

        with mock.patch.object(dashboard, '_read_questions', spy):
            current = self.status()['expertQuestions']['current']
        self.assertEqual(sorted(seen), ['run-a', 'run-b'])
        self.assertEqual(self.ids(current), ['qn-000002', 'qn-000001'])

    def test_the_board_serves_the_same_current_as_show(self):
        self.spec()
        self.prototype('run-a', requirements=SPEC, operations=[ask('o1', 'qn-000001', origin=of(SPEC))])
        board = dashboard_http.Board(self.root, poll=0.2, heartbeat=0.5, prototype_port=1)
        shown = self.status()['expertQuestions']
        served = dashboard_http.add_inline_html(json.loads(json.dumps(board.status())))['expertQuestions']
        self.assertEqual(shown['current'], served['current'])   # plain text: no `textHtml` is added to either
        self.assertEqual(self.ids(served['current']), ['qn-000001'])

    def test_the_public_project_function_keeps_its_result(self):
        self.spec()
        self.prototype('run-a', requirements=SPEC, operations=[ask('o1', 'qn-000001', origin=of(SPEC))])
        self.assertEqual(set(dashboard.build_expert_questions(self.root)), {'open', 'blocking', 'runs', 'items'})


if __name__ == '__main__':
    unittest.main()
