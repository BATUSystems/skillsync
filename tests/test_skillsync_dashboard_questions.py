"""`expertQuestions` of the dashboard contract: open questions to a domain expert counted across ALL runs of a project."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import dashboard, dashboard_http, prototype_server  # noqa: E402


def ask(op_id, qid, blocking=True, at='2026-01-11T09:00:00Z'):
    return {'id': op_id, 'type': 'ask', 'questionId': qid, 'at': at, 'by': 'agent', 'text': 'Pytanie?', 'blocking': blocking,
            'assumed': None if blocking else 'tak', 'addressees': [], 'refs': [], 'options': []}


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
        directory = self.root / 'docs' / 'prototypes' / slug
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
        self.assertEqual(self.summary(), {'open': 0, 'blocking': 0, 'runs': []})

    def test_sums_every_run_and_lists_only_runs_with_open_questions(self):
        self.run_dir('run-a', operations=[ask('o1', 'qn-000001'), ask('o2', 'qn-000002', blocking=False), ask('o3', 'qn-000003'),
                                          reply('o4', 'qn-000003', 'answer')])
        self.run_dir('run-b', operations=[ask('o5', 'qn-000004'), ask('o6', 'qn-000005'), reply('o7', 'qn-000005', 'withdraw')])
        self.run_dir('run-c', operations=[ask('o8', 'qn-000006'), reply('o9', 'qn-000006', 'answer')])
        self.run_dir('run-d', operations=[])
        self.assertEqual(self.summary(), {'open': 3, 'blocking': 2, 'runs': [
            {'slug': 'run-a', 'open': 2, 'blocking': 1, 'url': '/_experts/run-a'},
            {'slug': 'run-b', 'open': 1, 'blocking': 1, 'url': '/_experts/run-b'}]})

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
        self.assertEqual(status['expertQuestions'], {'open': 2, 'blocking': 1, 'runs': [
            {'slug': 'run-a', 'open': 2, 'blocking': 1, 'url': '/_experts/run-a'}]})
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


if __name__ == '__main__':
    unittest.main()
