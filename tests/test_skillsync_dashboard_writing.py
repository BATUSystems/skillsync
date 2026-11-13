"""Strona statusu a znacznik zapisu ekranów i pytania blokujące (WRITING-UX-140, K2, K3).

`prototype.writing` w kontrakcie, pozycja „sesja czeka”: `source: writing` zamiast wezwania do akceptacji,
`source: expert` przy pytaniach blokujących bez znacznika `czeka:` w najnowszym checkpoincie.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_dashboard_writing.py`.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skillsync_workflow import dashboard, prototype_server  # noqa: E402

NOW = 1_800_000_000_000
MARK = {'since': NOW - 600_000, 'expiresAt': NOW + 900_000, 'renewedAt': NOW}
ACC = {'screens': 2, 'accepted': 0, 'stale': 0}
SPEC = 'docs/specs/2026-01-11-demo.md'
PLAN = 'docs/plans/2026-01-11-demo.md'
EXPERT = {'open': 2, 'blocking': 1, 'runs': [{'slug': 'demo', 'open': 2, 'blocking': 1, 'url': '/_experts/demo'}]}


def hhmm(millis):
    return time.strftime('%H:%M', time.localtime(millis / 1000))


def run(checkpoint=None):
    return {'checkpoints': [{'time': '10:00', 'text': checkpoint}] if checkpoint else []}


def spec(acc=ACC):
    return {'path': SPEC, 'prototypeAcceptance': acc}


def plan(status='draft'):
    return {'path': PLAN, 'status': status, 'statusText': ''}


class WaitingItems(unittest.TestCase):
    def test_without_a_marker_the_acceptance_call_stays(self):
        items = dashboard.waiting_items(run(), spec(), plan(), 'demo')
        self.assertEqual([item['source'] for item in items], ['prototype'])
        self.assertIn('akceptacja prototypu demo (0/2)', items[0]['what'])

    def test_a_valid_marker_replaces_the_acceptance_call(self):
        items = dashboard.waiting_items(run(), spec(), plan(), 'demo', writing=MARK, now_ms=NOW)
        self.assertEqual([item['source'] for item in items], ['writing'])
        item = items[0]
        self.assertEqual(item['what'], 'agent pisze ekrany (do %s)' % hhmm(MARK['expiresAt']))
        self.assertEqual(item['link'], {'kind': 'prototype', 'slug': 'demo'})
        self.assertNotIn('zaakceptuj', item['action'])
        self.assertIn('akceptacja wróci', item['action'])
        self.assertTrue(item['since'])

    def test_the_marker_also_replaces_an_acceptance_item_from_the_checkpoint(self):
        items = dashboard.waiting_items(run('ryzyko: brak; czeka: akceptacja prototypu demo.'), spec(), plan(), 'demo',
                                        writing=MARK, now_ms=NOW)
        self.assertEqual([item['source'] for item in items], ['writing'])

    def test_other_checkpoint_waits_stay_next_to_the_writing_item(self):
        items = dashboard.waiting_items(run('czeka: GO plan.'), spec(), plan(), 'demo', writing=MARK, now_ms=NOW)
        self.assertEqual([item['source'] for item in items], ['writing', 'checkpoint'])

    def test_the_marker_shows_even_without_accepted_screens_to_wait_for(self):
        items = dashboard.waiting_items(run(), spec({'screens': 0, 'accepted': 0, 'stale': 0}), plan(), 'demo', writing=MARK, now_ms=NOW)
        self.assertEqual([item['source'] for item in items], ['writing'])

    def test_no_slug_no_writing_item(self):
        self.assertEqual(dashboard.waiting_items(run(), None, None, None, writing=MARK, now_ms=NOW), [])

    def test_blocking_questions_give_an_expert_item(self):
        items = dashboard.waiting_items(run(), spec(), plan('in_progress'), 'demo', expert=EXPERT)
        self.assertEqual([item['source'] for item in items], ['expert'])
        item = items[0]
        self.assertEqual(item['what'], 'odpowiedź eksperta (1 blokujące)')
        self.assertEqual(item['link'], {'kind': 'expert', 'slug': 'demo', 'url': '/_experts/demo'})
        self.assertIsNone(item['since'])

    def test_expert_link_without_a_single_run_goes_to_the_list(self):
        many = {'open': 3, 'blocking': 2, 'runs': [{'slug': 'a', 'open': 1, 'blocking': 1, 'url': '/_experts/a'},
                                                    {'slug': 'b', 'open': 2, 'blocking': 1, 'url': '/_experts/b'}]}
        item = dashboard.waiting_items(run(), None, None, None, expert=many)[0]
        self.assertEqual(item['what'], 'odpowiedź eksperta (2 blokujące)')
        self.assertEqual(item['link'], {'kind': 'expert', 'slug': None, 'url': '/_experts'})

    def test_only_non_blocking_questions_give_no_item(self):
        quiet = {'open': 2, 'blocking': 0, 'runs': [{'slug': 'demo', 'open': 2, 'blocking': 0, 'url': '/_experts/demo'}]}
        self.assertEqual(dashboard.waiting_items(run(), spec(), plan('in_progress'), 'demo', expert=quiet), [])
        self.assertEqual(dashboard.waiting_items(run(), spec(), plan('in_progress'), 'demo', expert={'open': 0, 'blocking': 0, 'runs': []}), [])

    def test_a_wait_marker_in_the_checkpoint_beats_the_expert_item(self):
        items = dashboard.waiting_items(run('czeka: GO implement.'), spec(), plan(), 'demo', expert=EXPERT)
        self.assertEqual([item['source'] for item in items], ['checkpoint'])

    def test_expert_item_comes_with_the_writing_item_when_both_hold(self):
        items = dashboard.waiting_items(run(), spec(), plan(), 'demo', writing=MARK, now_ms=NOW, expert=EXPERT)
        self.assertEqual([item['source'] for item in items], ['writing', 'expert'])

    def test_polish_count_forms(self):
        for blocking, word in ((1, 'blokujące'), (2, 'blokujące'), (5, 'blokujących'), (12, 'blokujących'), (22, 'blokujące')):
            with self.subTest(blocking=blocking):
                expert = {'open': blocking, 'blocking': blocking, 'runs': []}
                self.assertEqual(dashboard.waiting_items(run(), None, None, None, expert=expert)[0]['what'],
                                 'odpowiedź eksperta (%d %s)' % (blocking, word))


class PrototypeWriting(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-dashboard-writing-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'docs' / 'prototypes' / 'demo'
        self.directory.mkdir(parents=True)
        (self.directory / 'prototype.json').write_text('{"requirements": "%s"}\n' % SPEC, encoding='utf-8')

    def mark(self, now=NOW, ttl=900, by='agent'):
        return prototype_server.write_writing(str(self.directory), now, ttl, by)

    def test_valid_marker_is_public_without_by(self):
        self.mark(by='zz-writer')
        self.assertEqual(dashboard.prototype_writing(self.root, 'demo', NOW + 1),
                         {'since': NOW, 'expiresAt': NOW + 900_000, 'renewedAt': NOW})

    def test_no_marker_expired_corrupt_and_foreign_slugs_are_none(self):
        self.assertIsNone(dashboard.prototype_writing(self.root, 'demo', NOW))
        self.mark()
        self.assertIsNone(dashboard.prototype_writing(self.root, 'demo', NOW + 900_000))
        (self.directory / '.writing.json').write_text('{"version": 1, "since": "x"}', encoding='utf-8')
        self.assertIsNone(dashboard.prototype_writing(self.root, 'demo', NOW))
        for slug in (None, '', '..', 'a/b', 'nie-ma'):
            self.assertIsNone(dashboard.prototype_writing(self.root, slug, NOW), slug)

    def test_a_symlinked_prototype_directory_is_ignored(self):
        self.mark()
        link = self.root / 'docs' / 'prototypes' / 'link'
        try:
            os.symlink(self.directory, link)
        except (OSError, NotImplementedError):
            self.skipTest('brak dowiązań symbolicznych')
        self.assertIsNone(dashboard.prototype_writing(self.root, 'link', NOW + 1))

    def test_reading_writes_nothing(self):
        self.mark()
        before = sorted((p.name, p.stat().st_mtime_ns) for p in self.directory.iterdir())
        dashboard.prototype_writing(self.root, 'demo', NOW + 1)
        self.assertEqual(before, sorted((p.name, p.stat().st_mtime_ns) for p in self.directory.iterdir()))


class BuildStatus(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-dashboard-writing-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for rel, text in (
                (SPEC, '# Spec: demo\n\nStatus: draft\nPrototyp: docs/prototypes/demo\n'),
                (PLAN, '# Plan: demo\n\nStatus: draft\nPrzebieg: r1\nSpec: %s\n\n## Progress\n- [ ] S1 a\n' % SPEC),
                ('docs/prototypes/demo/prototype.json', '{"requirements": "%s"}\n' % SPEC)):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')

    def status(self, **kwargs):
        return dashboard.build_status(self.root, PLAN, manifest={}, acceptance=lambda slug: ACC, **kwargs)

    def test_contract_has_prototype_writing_null_by_default(self):
        data = self.status()
        self.assertEqual(sorted(data['prototype']), ['port', 'slug', 'up', 'writing'])
        self.assertIsNone(data['prototype']['writing'])
        self.assertEqual([item['source'] for item in data['waiting']], ['prototype'])

    def test_marker_shows_in_prototype_and_waiting(self):
        data = self.status(writing=lambda slug: MARK if slug == 'demo' else None, now_ms=NOW)
        self.assertEqual(data['prototype']['writing'], MARK)
        self.assertEqual([item['source'] for item in data['waiting']], ['writing'])

    def test_real_marker_file_is_read_with_the_injected_clock(self):
        prototype_server.write_writing(str(self.root / 'docs' / 'prototypes' / 'demo'), NOW, 900, 'zz-writer')
        data = self.status(now_ms=NOW + 1000)
        self.assertEqual(data['prototype']['writing'], {'since': NOW, 'expiresAt': NOW + 900_000, 'renewedAt': NOW})
        self.assertNotIn('zz-writer', str(data))
        self.assertEqual([item['source'] for item in data['waiting']], ['writing'])
        gone = self.status(now_ms=NOW + 900_000)
        self.assertIsNone(gone['prototype']['writing'])
        self.assertEqual([item['source'] for item in gone['waiting']], ['prototype'])

    def test_blocking_question_file_gives_the_expert_item(self):
        import json
        ops = [{'id': 'o1', 'type': 'ask', 'questionId': 'qn-000001', 'at': '2026-01-11T09:00:00Z', 'by': 'agent', 'text': 'Pytanie?',
                'blocking': True, 'assumed': None, 'addressees': [], 'refs': [], 'options': []}]
        (self.root / 'docs/prototypes/demo/questions.json').write_text(json.dumps({'questionsFormat': 1, 'operations': ops}), encoding='utf-8')
        data = self.status(writing=lambda slug: None, now_ms=NOW)
        sources = [item['source'] for item in data['waiting']]
        self.assertIn('expert', sources)
        expert = next(item for item in data['waiting'] if item['source'] == 'expert')
        self.assertEqual(expert['link'], {'kind': 'expert', 'slug': 'demo', 'url': '/_experts/demo'})


class Watcher(unittest.TestCase):
    def setUp(self):
        from skillsync_workflow import dashboard_http as srv
        self.temp = tempfile.TemporaryDirectory(prefix='skillsync-dashboard-writing-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'docs' / 'prototypes' / 'demo'
        self.directory.mkdir(parents=True)
        (self.directory / 'prototype.json').write_text('{}\n', encoding='utf-8')
        self.board = srv.Board(self.root, prototype_port=1)

    def test_marker_appearing_expiring_and_going_change_the_signature(self):
        empty = self.board.signature()
        now_ms = int(time.time() * 1000)
        prototype_server.write_writing(str(self.directory), now_ms, 60, 'agent')
        with_marker = self.board.signature()
        self.assertNotEqual(empty, with_marker)
        # the same file, but past its expiry: no file change, yet the signature differs (validity is part of it)
        prototype_server.write_writing(str(self.directory), now_ms - 120_000, 60, 'agent')
        expired = self.board.signature()
        self.assertNotEqual(with_marker, expired)
        (self.directory / '.writing.json').unlink()
        self.assertNotEqual(expired, self.board.signature())


if __name__ == '__main__':
    unittest.main()
