"""The local state of the feedback drafts for the status page (CORE-1142-FB-BUTTON, S3), against a fake `az` that FAILS on every call (no network).

`feedback.drafts_state(session, *, full=True, now=None)` -> `{enabled, total, drafts}` from local files only (drafts, previews, `approvals.jsonl`, result
records, the machine config, the `feedback` section of the layers). States, checked in this order: `sent`, `stale`, `blocked`, `after` (a valid unused
consent), `waiting`. `skillsync feedback wait F<n>` only reads and answers PASS (a valid consent) or NOT_RUN (the timeout). The status of the page
carries the key `feedback`: the full elements for the consent role, only `{draft, state}` for the read-only socket.
Names are neutral examples; secret-shaped texts are not needed here.
"""
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import yaml

from tests.test_skillsync_feedback import DRAFT
from tests.test_skillsync_feedback_dashboard import DashboardCase
from tests.test_skillsync_dashboard_ports import two_ports
from tests.test_skillsync_workflow import CLI
from skillsync_workflow import dashboard as engine
from skillsync_workflow import dashboard_http as srv
from skillsync_workflow import dashboard_ports as ports
from skillsync_workflow import feedback

TOKEN = 'unit-test-token-0123456789abcdef'
ITEM_KEYS = {'draft', 'sha', 'sha12', 'state', 'reason', 'given', 'generic'}


def request(port, path):
    import http.client
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        conn.request('GET', path)
        response = conn.getresponse()
        return response.status, json.loads(response.read() or b'{}')
    finally:
        conn.close()


class StateCase(DashboardCase):
    """A project with a trusted target and a working fake `az` (the preview needs it for the search of an open item). The code under test must not call it:
    every state read counts the calls of the fake before and after (`no_tracker`), and `feedback._call` is a trap while the state is computed."""

    @contextlib.contextmanager
    def no_tracker(self):
        before = len(self.calls())
        with patch.object(feedback, '_call', side_effect=AssertionError('the state must not call the tracker')):
            yield
        self.assertEqual(len(self.calls()), before, 'the tracker (az) was called')

    @property
    def store_dir(self):
        return self.root / '.skillsync/feedback'

    def dstate(self, **kwargs):
        with self.no_tracker():
            return feedback.drafts_state(self.open(), **kwargs)

    def one(self, draft_id='F1', **kwargs):
        rows = [row for row in self.dstate(**kwargs)['drafts'] if row['draft'] == draft_id]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def snapshot(self):
        base = self.store_dir
        return {str(p.relative_to(base)): (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(base.rglob('*')) if p.is_file()} if base.is_dir() else None

    def write_sent(self, name, **fields):
        (self.store_dir / name).write_text(json.dumps(fields), encoding='utf-8')

    def fill_the_limit(self, count=feedback.SEND_LIMIT):
        for number in range(count):
            self.write_sent(f'F{900 + number}.sent.json', at=feedback._stamp(feedback._now()), sha='%064x' % (number + 1), id=number)

    def previewed_draft(self):
        draft_id, _ = self.previewed()
        return draft_id


class ShapeTest(StateCase):
    def test_no_store_gives_a_quiet_empty_state_and_creates_nothing(self):
        self.assertFalse(self.store_dir.exists())
        state = self.dstate()
        self.assertEqual((state['enabled'], state['total'], state['drafts']), (True, 0, []))   # the layer has a feedback section
        self.assertEqual(sorted(state), ['drafts', 'enabled', 'total'])
        self.assertFalse(self.store_dir.exists())

    def test_without_drafts_and_without_a_feedback_section_the_feature_is_disabled(self):
        self.use_layers()   # the core layer only: no `feedback:` section anywhere
        self.assertEqual(self.dstate(), {'enabled': False, 'total': 0, 'drafts': []})

    def test_a_draft_enables_the_feature_even_without_a_section(self):
        self.new()
        self.use_layers()
        self.assertTrue(self.dstate()['enabled'])

    def test_a_waiting_draft_has_the_full_element(self):
        draft_id = self.previewed_draft()
        preview = self.read_preview()
        state = self.dstate()
        self.assertEqual((state['enabled'], state['total']), (True, 1))
        row = state['drafts'][0]
        self.assertEqual(set(row), ITEM_KEYS | {'view'})
        self.assertEqual((row['draft'], row['state'], row['reason'], row['given']), (draft_id, 'waiting', None, None))
        self.assertEqual((row['sha'], row['sha12']), (preview['sha'], preview['sha'][:12]))
        self.assertEqual(row['view'], preview['view'])
        self.assertIs(row['generic'], True)   # no identity files in the fixture

    def test_generic_follows_the_coverage_of_the_view(self):
        draft_id = self.previewed_draft()
        preview = self.read_preview()
        preview['view']['scan']['coverage'] = 'full'
        self.write_preview(preview)
        self.assertIs(self.one(draft_id)['generic'], False)

    def test_the_state_is_computed_without_any_write(self):
        self.approved()
        before = self.snapshot()
        self.dstate()
        self.dstate(full=False)
        self.assertEqual(self.snapshot(), before)


class DraftStatesTest(StateCase):
    def test_a_consent_makes_the_draft_after_and_names_who_gave_it(self):
        draft_id = self.previewed_draft()
        feedback.approve_from_preview(self.open(), draft_id, source='dashboard')
        row = self.one(draft_id)
        self.assertEqual((row['state'], row['reason']), ('after', None))
        self.assertEqual(row['given']['source'], 'dashboard')
        self.assertTrue(row['given']['at'])
        self.assertEqual(sorted(row['given']), ['at', 'source'])
        self.assertIn('view', row)

    def test_every_source_is_reported_as_recorded(self):
        for source in ('cli', 'ide', 'dashboard'):
            with self.subTest(source):
                draft_id = self.previewed_draft()
                feedback.approve_from_preview(self.open(), draft_id, source=source)
                self.assertEqual(self.one(draft_id)['given']['source'], source)

    def test_a_consent_older_than_a_new_preview_does_not_count(self):
        draft_id = self.previewed_draft()
        feedback.approve_from_preview(self.open(), draft_id)
        self.fb('draft', draft_id)
        row = self.one(draft_id)
        self.assertEqual((row['state'], row['given']), ('waiting', None))

    def test_a_consent_for_another_draft_does_not_count(self):
        first, second = self.previewed_draft(), self.previewed_draft()
        feedback.approve_from_preview(self.open(), first)
        self.assertEqual((self.one(first)['state'], self.one(second)['state']), ('after', 'waiting'))

    def test_a_consent_over_24_hours_old_does_not_count(self):
        draft_id = self.previewed_draft()
        feedback.approve_from_preview(self.open(), draft_id)
        self.assertEqual(self.one(draft_id, now=feedback._now() + timedelta(hours=23, minutes=30))['state'], 'after')
        self.assertNotEqual(self.one(draft_id, now=feedback._now() + timedelta(hours=25))['state'], 'after')

    def test_a_sent_record_with_the_sha_of_the_preview_means_sent(self):
        draft_id = self.previewed_draft()
        self.write_sent('F1.sent.json', at=feedback._stamp(feedback._now()), sha=self.full_sha(), id=7, mode='create')
        row = self.one(draft_id)
        self.assertEqual(row['state'], 'sent')
        self.assertNotIn('view', row)

    def test_a_sent_record_of_other_content_does_not_make_the_draft_sent(self):
        draft_id = self.previewed_draft()
        self.write_sent('F1.sent.json', at=feedback._stamp(feedback._now()), sha='f' * 64, id=7, mode='create')
        self.assertEqual(self.one(draft_id)['state'], 'waiting')

    def test_a_used_consent_for_this_sha_means_sent(self):
        draft_id = self.previewed_draft()
        entry = feedback.approve_from_preview(self.open(), draft_id)
        self.write_sent('F1.sending.json', approval=entry['approval'], sha=self.full_sha(), mode='create', item=None)
        self.assertEqual(self.one(draft_id)['state'], 'sent')

    def test_sent_wins_over_stale(self):
        draft_id = self.previewed_draft()
        self.write_sent('F1.sent.json', at=feedback._stamp(feedback._now()), sha=self.full_sha(), id=7, mode='create')
        feedback.save_draft(self.store_dir, {**DRAFT, 'layer': 'org', 'proposal': 'Changed after the send.'}, draft_id)
        self.assertEqual(self.one(draft_id)['state'], 'sent')

    def test_a_new_preview_of_changed_content_after_a_send_is_waiting_again(self):
        draft_id = self.previewed_draft()
        self.write_sent('F1.sent.json', at=feedback._stamp(feedback._now()), sha=self.full_sha(), id=7, mode='create')
        feedback.save_draft(self.store_dir, {**DRAFT, 'layer': 'org', 'cause_key': 'new-cause', 'title': 'A second request'}, draft_id)
        self.fb('draft', draft_id)
        self.assertEqual(self.one(draft_id)['state'], 'waiting')


class StaleTest(StateCase):
    def assert_stale(self, row, reason):
        self.assertEqual(set(row), {'draft', 'state', 'reason', 'sha12'}, row)   # no content of the preview for a stale one
        self.assertEqual(row['state'], 'stale')
        if reason is not None:
            self.assertEqual(row['reason'], reason)
        self.assertTrue(row['reason'])

    def test_a_draft_without_a_preview_is_stale_with_no_preview(self):
        draft_id = self.new()
        row = self.one(draft_id)
        self.assertEqual((row['draft'], row['state'], row['reason']), (draft_id, 'stale', 'no preview'))
        self.assertNotIn('view', row)

    def test_a_draft_changed_after_the_preview_is_stale_without_content(self):
        draft_id = self.previewed_draft()
        sha12 = self.read_preview()['sha'][:12]
        feedback.save_draft(self.store_dir, {**DRAFT, 'layer': 'org', 'proposal': 'Something else entirely.'}, draft_id)
        row = self.one(draft_id)
        self.assert_stale(row, None)
        self.assertEqual(row['sha12'], sha12)
        self.assertNotIn('Something else', json.dumps(row))

    def test_a_target_that_is_no_longer_trusted_is_stale(self):
        draft_id = self.previewed_draft()
        self.trust_target(False)
        self.assert_stale(self.one(draft_id), None)

    def test_a_preview_over_24_hours_old_is_stale_and_younger_is_not(self):
        draft_id = self.previewed_draft()
        self.write_preview({**self.read_preview(), 'at': feedback._stamp(feedback._now() - timedelta(hours=25))})
        self.assert_stale(self.one(draft_id), None)
        self.write_preview({**self.read_preview(), 'at': feedback._stamp(feedback._now() - timedelta(hours=23))})
        self.assertEqual(self.one(draft_id)['state'], 'waiting')

    def test_the_now_argument_moves_the_clock(self):
        draft_id = self.previewed_draft()
        self.assertEqual(self.one(draft_id)['state'], 'waiting')
        self.assert_stale(self.one(draft_id, now=feedback._now() + timedelta(hours=25)), None)

    def test_a_preview_without_the_view_is_stale_with_the_hint_to_run_draft(self):
        draft_id = self.previewed_draft()
        preview = self.read_preview()
        preview.pop('view')
        self.write_preview(preview)
        self.assert_stale(self.one(draft_id), 'no view: run feedback draft')

    def test_a_stale_draft_in_the_read_only_form_is_only_draft_and_state(self):
        draft_id = self.new()
        self.assertEqual(self.one(draft_id, full=False), {'draft': draft_id, 'state': 'stale'})


class BlockedTest(StateCase):
    def test_a_preview_whose_scan_did_not_pass_is_blocked_with_a_reason(self):
        draft_id = self.previewed_draft()
        preview = self.read_preview()
        preview['view']['scan']['status'] = 'FAIL'
        self.write_preview(preview)
        row = self.one(draft_id)
        self.assertEqual(row['state'], 'blocked')
        self.assertTrue(row['reason'])
        self.assertEqual(row['view']['scan']['status'], 'FAIL')

    def test_the_limit_of_sends_in_24_hours_blocks_a_waiting_draft(self):
        draft_id = self.previewed_draft()
        self.fill_the_limit(feedback.SEND_LIMIT - 1)
        self.assertEqual(self.one(draft_id)['state'], 'waiting')
        self.fill_the_limit()
        row = self.one(draft_id)
        self.assertEqual(row['state'], 'blocked')
        self.assertTrue(row['reason'])

    def test_sends_older_than_24_hours_do_not_count_to_the_limit(self):
        draft_id = self.previewed_draft()
        for number in range(feedback.SEND_LIMIT):
            self.write_sent(f'F{900 + number}.sent.json', at=feedback._stamp(feedback._now() - timedelta(hours=25)), sha='%064x' % (number + 1))
        self.assertEqual(self.one(draft_id)['state'], 'waiting')

    def test_blocked_wins_over_after(self):
        draft_id = self.previewed_draft()
        feedback.approve_from_preview(self.open(), draft_id)
        self.fill_the_limit()
        self.assertEqual(self.one(draft_id)['state'], 'blocked')

    def test_stale_wins_over_blocked(self):
        draft_id = self.new()
        self.fill_the_limit()
        self.assertEqual(self.one(draft_id)['state'], 'stale')


BAD_TIMES = ('0001-01-01T00:00:00+05:00', '9999-12-31T23:59:59-05:00')   # `astimezone(utc)` overflows on both (year 0 / year 10000)


class OverflowingTimeTest(StateCase):
    """A time that parses but cannot be moved to UTC (OverflowError) is data of a damaged or hostile file: the element is skipped or stale, the rest works."""

    def append_approval_line(self, at, draft='F1'):
        row = {'id': 'bad' + at[:4], 'draft': draft, 'sha': 'a' * 64, 'at': at, 'source': 'cli'}
        with (self.store_dir / 'approvals.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row) + '\n')

    def test_read_approvals_skips_the_row_and_keeps_the_valid_ones(self):
        draft_id = self.previewed_draft()
        feedback.approve_from_preview(self.open(), draft_id, source='dashboard')
        for at in BAD_TIMES:
            self.append_approval_line(at, draft_id)
        for at in BAD_TIMES:
            with self.subTest(at=at):
                entries = feedback.read_approvals(self.store_dir)
                self.assertEqual([(item['draft'], item['source']) for item in entries], [(draft_id, 'dashboard')])

    def test_sends_in_window_ignores_a_record_with_such_a_time(self):
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.write_sent('F900.sent.json', at=feedback._stamp(feedback._now()), sha='1' * 64)
        for number, at in enumerate(BAD_TIMES):
            self.write_sent(f'F{901 + number}.sent.json', at=at, sha=f'{number + 2}' * 64)
        self.assertEqual(feedback.sends_in_window(self.store_dir), 1)

    def test_a_preview_with_such_a_time_makes_only_that_draft_stale(self):
        for at in BAD_TIMES:
            with self.subTest(at=at):
                bad, good = self.previewed_draft(), self.previewed_draft()
                self.write_preview({**self.read_preview(bad), 'at': at}, bad)
                state = self.dstate()
                rows = {row['draft']: row for row in state['drafts']}
                self.assertEqual(rows[bad]['state'], 'stale', rows[bad])
                self.assertNotIn('view', rows[bad])
                self.assertEqual(rows[good]['state'], 'waiting')
                self.assertEqual({row['draft']: row['state'] for row in self.dstate(full=False)['drafts']}[bad], 'stale')

    def test_an_approvals_row_or_a_sent_record_with_such_a_time_does_not_break_the_state(self):
        draft_id = self.previewed_draft()
        for number, at in enumerate(BAD_TIMES):
            self.append_approval_line(at, draft_id)
            self.write_sent(f'F{950 + number}.sent.json', at=at, sha=f'{number + 3}' * 64)
        self.assertEqual(self.one(draft_id)['state'], 'waiting')

    def test_approving_a_preview_with_such_a_time_is_refused_as_stale_not_as_a_crash(self):
        draft_id = self.previewed_draft()
        for at in BAD_TIMES:
            with self.subTest(at=at):
                self.write_preview({**self.read_preview(draft_id), 'at': at}, draft_id)
                with self.assertRaises(feedback.FeedbackError) as caught:
                    feedback.approve_from_preview(self.open(), draft_id)
                self.assertEqual(caught.exception.code, 'stale')

    def test_inspecting_a_preview_with_such_a_time_gives_stale_not_a_crash(self):
        draft_id = self.previewed_draft()
        self.write_preview({**self.read_preview(draft_id), 'at': BAD_TIMES[0]}, draft_id)
        info = feedback._inspect(self.open(), draft_id, feedback._shared_reads(self.open(), feedback._now()))
        self.assertEqual(info['state'], 'stale')


class ViewMatchTest(StateCase):
    """F4: `view` is only for showing, yet the page shows it and the user consents to it: a `view` that differs from what the server computes for the draft
    (a hand-edited preview with the sha left alone) makes the draft stale, with no content."""
    MARK = 'TAMPERED-VIEW-MARK'

    def tampered(self, how):
        draft_id = self.previewed_draft()
        preview = self.read_preview(draft_id)
        view = preview['view']
        how(view)
        self.write_preview(preview, draft_id)
        return draft_id

    CASES = {
        'body': lambda v: v.update(body=v['body'] + 'TAMPERED-VIEW-MARK'),
        'title': lambda v: v.update(title='TAMPERED-VIEW-MARK ' + v['title']),
        'tags': lambda v: v.update(tags=[*v['tags'], 'TAMPERED-VIEW-MARK']),
        'target organization': lambda v: v['target'].update(organization='TAMPERED-VIEW-MARK'),
        'target project': lambda v: v['target'].update(project='TAMPERED-VIEW-MARK'),
        'target type': lambda v: v['target'].update(type='TAMPERED-VIEW-MARK'),
        'missing body': lambda v: v.pop('body'),
        'body of another type': lambda v: v.update(body=['not', 'text']),
    }

    def test_a_view_that_differs_from_the_computed_one_is_stale_without_content(self):
        for name, how in self.CASES.items():
            with self.subTest(field=name):
                draft_id = self.tampered(how)
                for full in (True, False):
                    row = self.one(draft_id, full=full)
                    self.assertEqual(row['state'], 'stale', row)
                    if full:
                        self.assertIn('view', row['reason'].lower())
                        self.assertEqual(set(row), {'draft', 'state', 'reason', 'sha12'})
                    self.assertNotIn(self.MARK, json.dumps(row))

    def test_an_untouched_view_stays_waiting_and_a_reserialized_one_too(self):
        draft_id = self.previewed_draft()
        self.assertEqual(self.one(draft_id)['state'], 'waiting', 'control: the comparison does not reject a good view')
        preview = self.read_preview(draft_id)
        self.write_preview(json.loads(json.dumps(preview, ensure_ascii=False)), draft_id)
        self.assertEqual(self.one(draft_id)['state'], 'waiting')

    def test_a_view_that_differs_in_the_item_or_the_scan_only_is_not_what_is_compared(self):
        """What is compared is what goes to the tracker (title, body, tags, target); the scan and the quote are display data of the preview."""
        draft_id = self.previewed_draft()
        preview = self.read_preview(draft_id)
        preview['view']['item_title'] = 'another quotation'
        self.write_preview(preview, draft_id)
        self.assertEqual(self.one(draft_id)['state'], 'waiting')


class ListTest(StateCase):
    def many(self, count):
        for number in range(1, count + 1):
            feedback.save_draft(self.store_dir, {**DRAFT, 'layer': 'org'}, f'F{number}')

    def test_the_newest_draft_comes_first_by_number_not_by_text(self):
        self.many(12)
        self.assertEqual([row['draft'] for row in self.dstate()['drafts']], [f'F{n}' for n in range(12, 0, -1)])

    def test_at_most_50_elements_and_total_counts_all(self):
        self.many(55)
        for full in (True, False):
            with self.subTest(full=full):
                state = self.dstate(full=full)
                self.assertEqual((state['total'], len(state['drafts'])), (55, 50))
                self.assertEqual((state['drafts'][0]['draft'], state['drafts'][-1]['draft']), ('F55', 'F6'))


class ReadOnlyFormTest(StateCase):
    def test_every_element_is_only_draft_and_state(self):
        waiting = self.previewed_draft()
        approved = self.previewed_draft()
        feedback.approve_from_preview(self.open(), approved)
        stale = self.new()
        state = self.dstate(full=False)
        self.assertEqual((state['enabled'], state['total']), (True, 3))
        self.assertEqual({row['draft']: row for row in state['drafts']},
                         {waiting: {'draft': waiting, 'state': 'waiting'}, approved: {'draft': approved, 'state': 'after'}, stale: {'draft': stale, 'state': 'stale'}})
        self.assertEqual(sorted(state), ['drafts', 'enabled', 'total'])

    def test_the_read_only_form_leaks_no_content_hash_or_target(self):
        draft_id = self.previewed_draft()
        preview = self.read_preview()
        feedback.approve_from_preview(self.open(), draft_id)
        text = json.dumps(self.dstate(full=False))
        for secret in (preview['sha'], preview['sha'][:12], preview['view']['title'], 'example-org', 'ExampleProject'):
            self.assertNotIn(secret, text)

    def test_the_full_form_is_the_default(self):
        self.previewed_draft()
        self.assertEqual(self.dstate(), self.dstate(full=True))
        self.assertIn('view', self.dstate()['drafts'][0])


class DamagedFilesTest(StateCase):
    def survives(self, draft_id):
        state = self.dstate()   # no exception
        rows = [row for row in state['drafts'] if row['draft'] == draft_id]
        self.assertLessEqual(len(rows), 1)
        if rows:   # either skipped or reported as unusable, never as waiting for a consent
            self.assertEqual((rows[0]['state'], rows[0]['reason']), ('stale', 'unreadable'))
        return state

    def test_an_unreadable_draft_file_is_skipped_or_stale_unreadable(self):
        draft_id = self.previewed_draft()
        (self.store_dir / f'{draft_id}.json').write_text('{ not json', encoding='utf-8')
        self.survives(draft_id)

    def test_an_oversized_draft_file_is_skipped_or_stale_unreadable(self):
        draft_id = self.previewed_draft()
        (self.store_dir / f'{draft_id}.json').write_text(json.dumps({'junk': 'x' * 400_000}), encoding='utf-8')
        self.survives(draft_id)

    def test_a_damaged_preview_makes_the_draft_stale_not_an_error(self):
        draft_id = self.previewed_draft()
        for junk in ('not json', '[]', '{"sha": 5}', 'x' * 400_000):
            with self.subTest(junk=junk[:10]):
                self.preview_file().write_text(junk, encoding='utf-8')
                state = self.dstate()
                rows = [row for row in state['drafts'] if row['draft'] == draft_id]
                self.assertTrue(not rows or rows[0]['state'] == 'stale', rows)

    def test_a_damaged_approvals_file_does_not_break_the_state(self):
        draft_id = self.previewed_draft()
        (self.store_dir / 'approvals.jsonl').write_text('not json\n{"id": 1}\n[]\n', encoding='utf-8')
        self.assertEqual(self.one(draft_id)['state'], 'waiting')

    def test_an_oversized_approvals_file_does_not_break_the_state(self):
        draft_id = self.previewed_draft()
        (self.store_dir / 'approvals.jsonl').write_text('x' * (feedback.MAX_APPROVALS_BYTES + 10), encoding='utf-8')
        state = self.dstate()
        self.assertEqual(state['total'], 1)
        self.assertNotEqual(self.one(draft_id)['state'], 'after')

    def test_a_damaged_result_record_does_not_break_the_state(self):
        draft_id = self.previewed_draft()
        self.write_sent('F7.sent.json')
        (self.store_dir / 'F8.sent.json').write_text('garbage', encoding='utf-8')
        self.assertEqual(self.one(draft_id)['state'], 'waiting')

    def test_a_symlinked_store_file_is_not_followed(self):
        draft_id = self.previewed_draft()
        outside = self.base / 'outside.json'
        outside.write_text(json.dumps({'sha': 'a' * 64}), encoding='utf-8')
        self.preview_file().unlink()
        self.preview_file().symlink_to(outside)
        rows = [row for row in self.dstate()['drafts'] if row['draft'] == draft_id]
        self.assertTrue(not rows or rows[0]['state'] == 'stale', rows)


class WaitCase(StateCase):
    def wait(self, draft_id, *flags, code=0, json_flag=False, env=None):
        argv = [sys.executable, str(CLI), 'feedback', 'wait', draft_id, str(self.root), *flags, *(['--json'] if json_flag else [])]
        started, calls = time.monotonic(), len(self.calls())
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=self.root, env={**os.environ, **(env or {})}, stdin=subprocess.DEVNULL)
        self.elapsed = time.monotonic() - started
        self.assertEqual(len(self.calls()), calls, 'the tracker (az) was called by `wait`')
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return json.loads(result.stdout)


class WaitTest(WaitCase):
    def test_a_valid_consent_answers_pass_at_once_with_its_id_sha12_and_source(self):
        draft_id = self.previewed_draft()
        entry = feedback.approve_from_preview(self.open(), draft_id, source='dashboard')
        for json_flag in (False, True):
            with self.subTest(json=json_flag):
                result = self.wait(draft_id, '--timeout', '0', json_flag=json_flag)
                self.assertEqual((result['status'], result['approval'], result['sha12'], result['source']),
                                 ('PASS', entry['approval'], self.read_preview()['sha'][:12], 'dashboard'))

    def test_without_a_consent_the_timeout_answers_not_run_with_code_3(self):
        draft_id = self.previewed_draft()
        for json_flag in (False, True):
            with self.subTest(json=json_flag):
                result = self.wait(draft_id, '--timeout', '0', code=3, json_flag=json_flag)
                self.assertEqual(result['status'], 'NOT_RUN')
                self.assertNotIn('approval', result)

    def test_a_short_timeout_with_a_short_interval_returns_soon_with_not_run(self):
        draft_id = self.previewed_draft()
        result = self.wait(draft_id, '--timeout', '0.3', '--interval', '0.05', code=3)
        self.assertEqual(result['status'], 'NOT_RUN')
        self.assertLess(self.elapsed, 20)

    def test_it_reads_once_with_timeout_zero_and_writes_nothing(self):
        draft_id = self.previewed_draft()
        feedback.approve_from_preview(self.open(), draft_id)
        before = self.snapshot()
        self.wait(draft_id, '--timeout', '0')
        self.wait(draft_id, '--timeout', '0.2', '--interval', '0.05')
        self.assertEqual(self.snapshot(), before)

    def test_a_consent_of_an_older_preview_does_not_count(self):
        draft_id = self.previewed_draft()
        feedback.approve_from_preview(self.open(), draft_id)
        self.fb('draft', draft_id)
        self.assertEqual(self.wait(draft_id, '--timeout', '0', code=3)['status'], 'NOT_RUN')

    def test_a_consent_of_another_draft_does_not_count(self):
        first, second = self.previewed_draft(), self.previewed_draft()
        feedback.approve_from_preview(self.open(), first)
        self.assertEqual(self.wait(second, '--timeout', '0', code=3)['status'], 'NOT_RUN')
        self.assertEqual(self.wait(first, '--timeout', '0')['status'], 'PASS')

    def test_a_consent_given_while_waiting_ends_the_wait_with_pass(self):
        draft_id = self.previewed_draft()
        argv = [sys.executable, str(CLI), 'feedback', 'wait', draft_id, str(self.root), '--timeout', '30', '--interval', '0.05']
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=self.root, stdin=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        time.sleep(0.5)
        self.assertIsNone(proc.poll(), 'the wait must keep waiting while there is no consent')
        entry = feedback.approve_from_preview(self.open(), draft_id, source='ide')
        out, err = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 0, out + err)
        result = json.loads(out)
        self.assertEqual((result['status'], result['approval'], result['source']), ('PASS', entry['approval'], 'ide'))


class WaitFreshnessTest(WaitCase):
    """`wait` says it makes "the same check as `send`": a valid consent for the sha of the CURRENT content, given after the preview and at most 24 hours old.
    `send` needs neither a `view` in the preview nor a young preview (only `approve_from_preview` and the status page do), so `wait` must not block on them."""

    def consented(self):
        draft_id = self.previewed_draft()
        feedback.approve_from_preview(self.open(), draft_id)
        return draft_id

    def send_would_accept(self, draft_id):
        preview = self.read_preview(draft_id)
        return feedback.find_approval(self.store_dir, draft_id, preview['sha'], feedback._utc(preview['at'])) is not None

    def test_a_preview_without_a_view_does_not_block_wait_when_the_consent_is_valid(self):
        draft_id = self.consented()
        preview = self.read_preview(draft_id)
        preview.pop('view')
        self.write_preview(preview, draft_id)
        self.assertTrue(self.send_would_accept(draft_id), 'control: this is the criterion of `send`')
        self.assertEqual(self.wait(draft_id, '--timeout', '0')['status'], 'PASS')

    def test_a_preview_over_24_hours_old_does_not_block_wait_when_the_consent_is_valid(self):
        draft_id = self.consented()
        self.write_preview({**self.read_preview(draft_id), 'at': feedback._stamp(feedback._now() - timedelta(hours=25))}, draft_id)
        self.assertTrue(self.send_would_accept(draft_id), 'control: this is the criterion of `send`')
        self.assertEqual(self.wait(draft_id, '--timeout', '0')['status'], 'PASS')

    def test_control_a_changed_draft_still_blocks_wait(self):
        draft_id = self.consented()
        feedback.save_draft(self.store_dir, {**DRAFT, 'layer': 'org', 'proposal': 'Something else entirely.'}, draft_id)
        self.assertEqual(self.wait(draft_id, '--timeout', '0', code=2)['status'], 'BLOCKED')


class WaitUsageTest(WaitCase):
    def test_a_bad_draft_id_is_a_usage_error_with_code_2(self):
        for bad in ('X1', 'F0', 'f1', 'F01'):
            with self.subTest(bad=bad):
                self.assertNotEqual(self.wait(bad, '--timeout', '0', code=2)['status'], 'PASS')

    def test_an_unknown_draft_is_a_usage_error(self):
        self.new()
        self.assertEqual(self.wait('F99', '--timeout', '0', code=2)['status'], 'BLOCKED')

    def test_a_draft_without_a_preview_is_a_usage_error(self):
        draft_id = self.new()
        result = self.wait(draft_id, '--timeout', '0', code=2)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('draft', result['reason'])

    def test_a_stale_draft_is_a_usage_error_also_in_json(self):
        draft_id = self.previewed_draft()
        feedback.save_draft(self.store_dir, {**DRAFT, 'layer': 'org', 'proposal': 'Something else entirely.'}, draft_id)
        for json_flag in (False, True):
            with self.subTest(json=json_flag):
                self.assertEqual(self.wait(draft_id, '--timeout', '0', code=2, json_flag=json_flag)['status'], 'BLOCKED')

    def test_the_usage_errors_write_nothing_and_leave_the_tracker_alone(self):
        draft_id = self.new()
        before = self.snapshot()
        self.wait(draft_id, '--timeout', '0', code=2)
        self.assertEqual(self.snapshot(), before)


# --------------------------------------------------------------------------- the status page

class StatusKeyTest(StateCase):
    def test_a_project_without_drafts_and_sections_has_the_empty_disabled_key(self):
        self.use_layers()
        with self.no_tracker():
            self.assertEqual(engine.build_status(self.root)['feedback'], {'enabled': False, 'total': 0, 'drafts': []})

    def test_the_key_carries_the_full_state_with_its_fields(self):
        draft_id = self.previewed_draft()
        feedback.approve_from_preview(self.open(), draft_id, source='dashboard')
        with self.no_tracker():
            block = engine.build_status(self.root)['feedback']
        self.assertEqual(sorted(block), ['drafts', 'enabled', 'total'])
        self.assertEqual((block['enabled'], block['total']), (True, 1))
        self.assertEqual(set(block['drafts'][0]), ITEM_KEYS | {'view'})
        self.assertEqual((block['drafts'][0]['draft'], block['drafts'][0]['state'], block['drafts'][0]['given']['source']), (draft_id, 'after', 'dashboard'))

    def test_building_the_status_does_not_call_the_tracker_or_write_the_store(self):
        self.approved()
        before = self.snapshot()
        with self.no_tracker():
            engine.build_status(self.root)
        self.assertEqual(self.snapshot(), before)


class StatusRolesTest(StateCase):
    """One `Board`, a read socket and an approve socket (as `RoleFromTheSocket`): the role is the socket."""

    def setUp(self):
        super().setUp()
        self.waiting = self.previewed_draft()
        self.approved_id = self.previewed_draft()
        feedback.approve_from_preview(self.open(), self.approved_id)
        self.preview = self.read_preview(self.waiting)
        self.board = srv.Board(self.root, poll=0.2, heartbeat=0.5, prototype_port=1, approve_token=TOKEN)
        self.board.prototype_acceptance = lambda slug: None
        self.read_port, self.approve_port = two_ports()   # ports of the test slice only
        self.read = srv.make_server(self.board, '127.0.0.1', self.read_port, role=ports.ROLE_READ, peer_port=self.approve_port)
        self.approve = srv.make_server(self.board, '127.0.0.1', self.approve_port, role=ports.ROLE_APPROVE, peer_port=self.read_port)
        for server in (self.read, self.approve):
            threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)

    def test_the_approve_socket_serves_the_full_elements(self):
        with self.no_tracker():
            status, data = request(self.approve_port, '/api/status')
        self.assertEqual(status, 200)
        block = data['feedback']
        self.assertEqual((block['enabled'], block['total']), (True, 2))
        rows = {row['draft']: row for row in block['drafts']}
        self.assertEqual(set(rows[self.waiting]), ITEM_KEYS | {'view'})
        self.assertEqual((rows[self.waiting]['state'], rows[self.approved_id]['state']), ('waiting', 'after'))
        self.assertEqual(rows[self.waiting]['sha'], self.preview['sha'])

    def test_the_read_socket_serves_only_draft_and_state(self):
        with self.no_tracker():
            status, data = request(self.read_port, '/api/status')
        self.assertEqual(status, 200)
        block = data['feedback']
        self.assertEqual((block['enabled'], block['total']), (True, 2))
        self.assertEqual({row['draft']: row for row in block['drafts']},
                         {self.waiting: {'draft': self.waiting, 'state': 'waiting'}, self.approved_id: {'draft': self.approved_id, 'state': 'after'}})

    def test_the_read_socket_shows_no_hash_title_or_target_of_a_draft_anywhere(self):
        status, data = request(self.read_port, '/api/status')
        self.assertEqual(status, 200)
        text = json.dumps(data, ensure_ascii=False)
        for secret in (self.preview['sha'], self.preview['view']['title'], self.preview['view']['body'][:40]):
            self.assertNotIn(secret, text)

    def test_the_approve_socket_does_not_leak_into_the_read_socket_through_a_cache(self):
        request(self.approve_port, '/api/status')
        status, data = request(self.read_port, '/api/status')
        self.assertEqual(set(data['feedback']['drafts'][0]), {'draft', 'state'})
        status, data = request(self.approve_port, '/api/status')
        self.assertIn('view', data['feedback']['drafts'][0] if data['feedback']['drafts'][0]['state'] != 'stale' else {'view': 1})


    def corrupt_times(self):
        self.write_preview({**self.preview, 'at': BAD_TIMES[0]}, self.waiting)
        with (self.store_dir / 'approvals.jsonl').open('a', encoding='utf-8') as handle:
            for at in BAD_TIMES:
                handle.write(json.dumps({'id': 'x' + at[:4], 'draft': self.waiting, 'sha': 'b' * 64, 'at': at, 'source': 'cli'}) + '\n')
        for number, at in enumerate(BAD_TIMES):
            self.write_sent(f'F{970 + number}.sent.json', at=at, sha=f'{number + 4}' * 64)

    def test_a_time_that_overflows_in_the_files_does_not_make_the_status_a_500(self):
        self.corrupt_times()
        for port in (self.approve_port, self.read_port):
            with self.subTest(port='approve' if port == self.approve_port else 'read'):
                status, data = request(port, '/api/status')
                self.assertEqual(status, 200, data)
                rows = {row['draft']: row['state'] for row in data['feedback']['drafts']}
                self.assertEqual((rows[self.waiting], rows[self.approved_id]), ('stale', 'after'))
        with self.no_tracker():
            self.assertEqual(engine.build_status(self.root)['feedback']['total'], 2)


if __name__ == '__main__':
    unittest.main()
