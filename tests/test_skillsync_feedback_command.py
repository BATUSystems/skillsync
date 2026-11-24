"""`skillsync feedback` (CORE-1140-FEEDBACK, F-6): the consent gate, the refusals, the limits and the manifest key, against a fake `az` (no network).

The gate: `send` needs a consent entry for the exact content (full sha), written after the user's word `WYŚLIJ <sha12>` and after a preview, unused and fresh.
"""
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_skillsync_workflow import CLI
from tests.test_skillsync_feedback import DRAFT
from tests.test_skillsync_feedback_azure import AzureFeedbackCase, draft
from skillsync_workflow import feedback, winacl
from layers_helpers import make_layer

ORG_SECTION = {'kind': 'azure-boards', 'organization': 'example-org', 'project': 'ExampleProject', 'area': 'Platform/Team', 'type': 'Task'}
TRUSTED_ENTRY = {'layer': 'org', 'kind': 'azure-boards', 'organization': 'example-org', 'project': 'ExampleProject'}


class CommandCase(AzureFeedbackCase):
    def setUp(self):
        super().setUp()
        self.org = self.base / 'org-layer'
        make_layer(self.org, 'org', 'org', extra_meta={'feedback': ORG_SECTION})
        self.trust_target(True)
        self.draft_file = self.base / 'draft.json'
        self.write_draft(DRAFT)

    def trust_target(self, trusted):
        extra = {'feedback_targets': [TRUSTED_ENTRY]} if trusted else {}
        self.use_layers(('core', 'core', self.library), ('org', 'org', self.org), **extra)
        self.context = feedback.Context(*feedback.load_layer_project(self.root))

    def write_draft(self, value):
        self.draft_file.write_text(json.dumps({**value, 'layer': 'org'}), encoding='utf-8')

    def fb(self, *args, code=0, env=None):
        action, *rest = args
        positional = rest[:1] if action in ('draft', 'approve', 'send', 'trust') else []
        flags = rest[1:] if positional else rest
        argv = [sys.executable, str(CLI), 'feedback', action, *positional, str(self.root), *flags]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=self.root, env={**os.environ, **(env or {})}, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        return json.loads(result.stdout)

    def new(self):
        return self.fb('new', '--file', str(self.draft_file))['draft']

    def previewed(self):
        draft_id = self.new()
        preview = self.fb('draft', draft_id)
        return draft_id, preview

    def approved(self):
        draft_id, preview = self.previewed()
        self.fb('approve', draft_id, '--word', f'WYŚLIJ {preview["sha12"]}')
        return draft_id, preview

    def sent_files(self):
        return sorted(p.name for p in (self.root / '.skillsync/feedback').glob('F*.sent*.json'))


class HappyPathTest(CommandCase):
    def test_new_draft_approve_send(self):
        draft_id, preview = self.previewed()
        self.assertEqual((preview['mode'], preview['target']['organization'], preview['scan']['status']), ('create', 'example-org', 'PASS'))
        self.assertEqual(preview['warning'][:12], 'client names')   # no identity files: the coverage is generic and says so
        self.assertIn('WYŚLIJ ' + preview['sha12'], preview['consent'])
        approval = self.fb('approve', draft_id, '--word', 'WYŚLIJ ' + preview['sha12'])
        self.assertEqual(approval['status'], 'PASS')
        sent = self.fb('send', draft_id)
        self.assertEqual((sent['status'], sent['mode'], sent['id']), ('PASS', 'create', 100))
        record = json.loads((self.root / '.skillsync/feedback/F1.sent.json').read_text())
        self.assertEqual(record['approval'], approval['approval'])
        self.assertEqual(self.fb('list')['drafts'], [{'draft': 'F1', 'state': 'sent', 'id': 100, 'mode': 'create'}])

    def test_an_interrupted_send_is_resumed_by_the_normal_command_without_a_second_element(self):
        draft_id, preview = self.approved()
        result = self.fb('send', draft_id, code=2, env={'FAKE_AZ_MODE': 'readback-empty'})
        self.assertEqual((result['status'], result['mutation_completed']), ('BLOCKED', True))
        self.assertEqual(len(self.data()['items']), 1)
        again = self.fb('send', draft_id)   # the planning would now see its own open element (a comment): the intent keeps mode `create`
        self.assertEqual((again['status'], again['mode']), ('PASS', 'create'))
        self.assertEqual(len(self.data()['items']), 1)
        self.assertEqual(self.data()['comments'], [])

    def test_the_retro_link_is_kept_in_the_result_file_and_the_list(self):
        self.write_draft({**DRAFT, 'retro': 'r-1/P2'})
        draft_id, preview = self.approved()
        self.fb('send', draft_id)
        self.assertEqual(json.loads((self.root / '.skillsync/feedback/F1.sent.json').read_text())['retro'], 'r-1/P2')
        self.assertEqual(self.fb('list')['drafts'], [{'draft': 'F1', 'state': 'sent', 'id': 100, 'mode': 'create', 'retro': 'r-1/P2'}])

    def test_the_ascii_word_works(self):
        draft_id, preview = self.previewed()
        self.fb('approve', draft_id, '--word', 'WYSLIJ ' + preview['sha12'])
        self.assertEqual(self.fb('send', draft_id)['status'], 'PASS')


class GateTest(CommandCase):
    def test_without_a_consent_nothing_is_written(self):
        draft_id, preview = self.previewed()
        before = len(self.calls())
        result = self.fb('send', draft_id, code=2)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn(preview['sha12'], result['consent'])
        self.assertEqual([c for c in self.calls()[before:] if '--http-method' in c and 'POST' in c], [])
        self.assertEqual(self.data()['items'], [])

    def test_without_a_preview_there_is_no_consent_to_record_and_no_send(self):
        draft_id = self.new()
        self.fb('approve', draft_id, '--word', 'WYŚLIJ 0123456789ab', code=2)
        self.assertFalse((self.root / '.skillsync/feedback/approvals.jsonl').exists())
        self.fb('send', draft_id, code=2)

    def test_a_wrong_or_malformed_word_is_refused_and_records_nothing(self):
        draft_id, preview = self.previewed()
        for word in ('WYŚLIJ 000000000000', 'WYŚLIJ', 'SEND ' + preview['sha12'], 'WYŚLIJ ' + preview['sha12'][:8], 'WYŚLIJ ' + preview['sha12'] + ' x'):
            with self.subTest(word):
                self.fb('approve', draft_id, '--word', word, code=2)
        self.assertFalse((self.root / '.skillsync/feedback/approvals.jsonl').exists())

    def test_a_changed_draft_after_the_consent_is_refused(self):
        draft_id, _ = self.approved()
        self.write_draft({**DRAFT, 'proposal': 'Something else entirely.'})
        feedback.save_draft(self.root / '.skillsync/feedback', json.loads(self.draft_file.read_text()), draft_id)
        result = self.fb('send', draft_id, code=2)
        self.assertIn('no preview of this content', result['reason'])
        self.assertEqual(self.data()['items'], [])

    def test_back_to_the_first_content_needs_a_new_preview_and_a_new_consent(self):
        draft_id, preview = self.approved()
        store = self.root / '.skillsync/feedback'
        feedback.save_draft(store, {**DRAFT, 'layer': 'org', 'proposal': 'B'}, draft_id)
        self.fb('draft', draft_id)                       # a preview of content B
        feedback.save_draft(store, {**DRAFT, 'layer': 'org'}, draft_id)
        again = self.fb('draft', draft_id)               # content A previewed again: the old consent for A is older than this preview
        self.assertEqual(again['sha12'], preview['sha12'])
        self.fb('send', draft_id, code=2)
        self.fb('approve', draft_id, '--word', 'WYŚLIJ ' + again['sha12'])
        self.assertEqual(self.fb('send', draft_id)['status'], 'PASS')

    def test_a_consent_for_one_draft_does_not_serve_a_copy_with_the_same_content(self):
        draft_id, preview = self.approved()
        copy_id = self.new()
        self.assertNotEqual(copy_id, draft_id)
        self.fb('draft', copy_id)
        self.fb('send', copy_id, code=2)

    def test_the_consent_is_used_once_and_resending_needs_a_new_one(self):
        draft_id, preview = self.approved()
        self.fb('send', draft_id)
        self.fb('send', draft_id, code=2)                 # already sent
        result = self.fb('send', draft_id, '--replace', code=2)   # the consent is used
        self.assertIn('no preview', result['reason'])
        self.assertEqual(len(self.data()['items']), 1)

    def test_a_message_in_the_approvals_file_of_the_runs_is_not_a_consent(self):
        draft_id, preview = self.previewed()
        run = self.root / '.skillsync/runs/r1'
        run.mkdir(parents=True)
        (run / 'approvals.jsonl').write_text(json.dumps({'word': 'CP', 'at': '2099-01-01T00:00:00Z', 'sha': 'a' * 40, 'source': 'cli'}) + '\n', encoding='utf-8')
        self.fb('send', draft_id, code=2)

    def test_the_status_command_cannot_record_this_consent(self):
        draft_id, preview = self.previewed()
        result = subprocess.run([sys.executable, str(CLI), 'approvals', 'record', str(self.root), '--word', 'feedback-send', '--run-id', 'r1', '--sha', 'a' * 40],
                                capture_output=True, text=True, timeout=30, cwd=self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / '.skillsync/feedback/approvals.jsonl').exists())


class RefusalTest(CommandCase):
    def test_an_untrusted_or_changed_target_stops_draft_and_send(self):
        draft_id = self.new()
        self.trust_target(False)
        self.assertIn('not approved', self.fb('draft', draft_id, code=2)['reason'])
        self.use_layers(('core', 'core', self.library), ('org', 'org', self.org),
                        feedback_targets=[{**TRUSTED_ENTRY, 'project': 'OtherProject'}])
        self.assertIn('changed', self.fb('draft', draft_id, code=2)['reason'])
        self.fb('send', draft_id, code=2)

    def test_a_card_session_never_sends(self):
        draft_id, _ = self.approved()
        result = self.fb('send', draft_id, code=2, env={feedback.CARD_ENV: 'Card-1'})
        self.assertIn('card session', result['reason'])
        self.assertEqual(self.data()['items'], [])

    def test_a_card_session_does_not_record_a_consent_either(self):
        draft_id, preview = self.previewed()
        result = self.fb('approve', draft_id, '--word', 'WYŚLIJ ' + preview['sha12'], code=2, env={feedback.CARD_ENV: 'Card-1'})
        self.assertIn('card session', result['reason'])
        self.assertFalse((self.root / '.skillsync/feedback/approvals.jsonl').exists())

    def marker(self, text='Card-1\n'):
        path = self.root / '.skillsync' / 'card-alias'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_the_card_marker_file_stops_send_and_approve_without_the_variable(self):
        draft_id, preview = self.previewed()
        marker = self.marker()
        result = self.fb('approve', draft_id, '--word', 'WYŚLIJ ' + preview['sha12'], code=2)
        self.assertIn('card session (Card-1)', result['reason'])
        self.assertFalse((self.root / '.skillsync/feedback/approvals.jsonl').exists())
        marker.unlink()
        self.fb('approve', draft_id, '--word', 'WYŚLIJ ' + preview['sha12'])        # a main session of the same directory: allowed again
        self.marker()
        result = self.fb('send', draft_id, code=2)
        self.assertIn('card session (Card-1)', result['reason'])
        self.assertEqual(self.data()['items'], [])
        marker.unlink()
        self.assertEqual(self.fb('send', draft_id)['status'], 'PASS')

    def test_a_card_marker_refuses_approve_with_a_malformed_word_too_and_the_message_is_not_cut(self):
        draft_id, _ = self.previewed()
        self.marker('A' * 40 + '\n')
        result = self.fb('approve', draft_id, '--word', 'zle slowo', code=2)
        self.assertIn('card session (', result['reason'])
        self.assertTrue(result['reason'].endswith('(or unset SKILLSYNC_CARD_ALIAS).'), result['reason'])

    def test_an_empty_or_odd_marker_still_counts_and_the_alias_is_cleaned(self):
        draft_id, _ = self.approved()
        marker = self.marker('')
        self.assertIn('card session (card)', self.fb('send', draft_id, code=2)['reason'])
        marker.write_text('Alias\x1b[31m-2\nsecond line\n', encoding='utf-8')
        reason = self.fb('send', draft_id, code=2)['reason']
        self.assertIn('Alias[31m-2', reason)
        self.assertNotIn('\x1b', reason)
        self.assertNotIn('second line', reason)

    @unittest.skipIf(os.name == 'nt', 'symlinks unavailable (Windows without Developer Mode)')
    def test_a_marker_that_is_a_link_counts(self):
        draft_id, _ = self.approved()
        target = self.base / 'elsewhere.txt'
        target.write_text('X\n', encoding='utf-8')
        marker = self.root / '.skillsync' / 'card-alias'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.symlink_to(target)
        self.assertIn('card session', self.fb('send', draft_id, code=2)['reason'])

    def test_targets_lists_the_layers_with_the_state_of_their_target(self):
        rows = self.fb('targets')['targets']
        self.assertEqual([(row['layer'], row['state']) for row in rows], [('org', 'trusted')])
        self.trust_target(False)
        self.assertEqual(self.fb('targets')['targets'][0]['state'], 'not-trusted')

    def test_send_with_the_tracker_down_writes_nothing(self):
        draft_id, preview = self.approved()
        result = self.fb('send', draft_id, code=2, env={'FAKE_AZ_STATE': str(self.base / 'missing-dir' / 'state.json')})
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(self.data()['items'], [])
        self.assertFalse((self.root / '.skillsync/feedback/F1.sent.json').exists())

    def test_a_change_of_the_dedup_result_between_the_preview_and_the_send_is_refused(self):
        draft_id, preview = self.approved()
        # somebody else opened an item with the same cause meanwhile: the mode becomes COMMENT, the sha changes, the consent no longer fits
        state = self.data()
        tags = '; '.join(['feedback', 'layer:org', feedback.dedup_key(feedback.validate_draft({**DRAFT, 'layer': 'org'}))])
        state['items'].append({'id': 777, 'url': 'x', 'fields': {'System.Title': 'Other', 'System.Tags': tags, 'System.State': 'Proposed'}})
        self.state.write_text(json.dumps(state), encoding='utf-8')
        result = self.fb('send', draft_id, code=2)
        self.assertIn('no preview of this content', result['reason'])
        self.assertEqual(self.data()['comments'], [])
        self.assertEqual(len(self.data()['items']), 1)

    def test_replace_needs_a_new_preview_and_a_new_consent(self):
        draft_id, preview = self.approved()
        self.fb('send', draft_id)
        self.write_draft({**DRAFT, 'cause_key': 'new-cause', 'title': 'A second request'})
        feedback.save_draft(self.root / '.skillsync/feedback', json.loads(self.draft_file.read_text()), draft_id)
        self.fb('send', draft_id, '--replace', code=2)                       # no preview of the new content
        again = self.fb('draft', draft_id)
        self.fb('send', draft_id, '--replace', code=2)                       # a preview, but no consent for it
        self.fb('approve', draft_id, '--word', 'WYŚLIJ ' + again['sha12'])
        self.assertEqual(self.fb('send', draft_id, '--replace')['status'], 'PASS')
        self.assertEqual(len(self.data()['items']), 2)
        archives = sorted(p.name for p in (self.root / '.skillsync/feedback').glob('F1.sent.*.json'))
        self.assertEqual(len(archives), 1)

    def test_a_worktree_never_sends(self):
        subprocess.run(['git', '-C', str(self.root), 'init', '-q'], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.email=t@example.org', '-c', 'user.name=t', 'commit', '-q', '--allow-empty', '-m', 'x'], check=True)
        linked = self.base / 'linked'
        subprocess.run(['git', '-C', str(self.root), 'worktree', 'add', '-q', str(linked), '-b', 'side'], check=True)
        (linked / '.github').mkdir()
        (linked / '.github/.skillsync.yaml').write_text((self.root / '.github/.skillsync.yaml').read_text(), encoding='utf-8')
        result = subprocess.run([sys.executable, str(CLI), 'feedback', 'send', 'F1', str(linked)], capture_output=True, text=True, timeout=60, cwd=linked,
                                stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('worktree', json.loads(result.stdout)['reason'])

    def test_a_card_in_a_worktree_is_refused_by_its_own_marker_for_approve_and_the_message_says_how_to_clear_it(self):
        draft_id, preview = self.previewed()
        subprocess.run(['git', '-C', str(self.root), 'init', '-q'], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.email=t@example.org', '-c', 'user.name=t', 'commit', '-q', '--allow-empty', '-m', 'x'], check=True)
        linked = self.base / 'card-linked'
        subprocess.run(['git', '-C', str(self.root), 'worktree', 'add', '-q', str(linked), '-b', 'card-side'], check=True)
        (linked / '.github').mkdir()
        (linked / '.github/.skillsync.yaml').write_text((self.root / '.github/.skillsync.yaml').read_text(), encoding='utf-8')
        (linked / '.skillsync').mkdir()
        (linked / '.skillsync' / 'card-alias').write_text('Card-7\n', encoding='utf-8')
        word = 'WYŚLIJ ' + preview['sha12']
        result = subprocess.run([sys.executable, str(CLI), 'feedback', 'approve', draft_id, str(linked), '--word', word], capture_output=True, text=True, timeout=60,
                                cwd=linked, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        reason = json.loads(result.stdout)['reason']
        self.assertIn('card session (Card-7)', reason)
        self.assertIn('.skillsync/card-alias', reason)       # the way out for a marker that was left behind
        self.assertFalse((self.root / '.skillsync/feedback/approvals.jsonl').exists())

    def test_a_leak_in_the_draft_stops_the_preview_and_the_send(self):
        self.write_draft({**DRAFT, 'problem': 'password = ' + 'hunter22' + 'xyz'})
        draft_id = self.new()
        result = self.fb('draft', draft_id, code=1)
        self.assertEqual(result['scan']['status'], 'FAIL')
        self.assertFalse((self.root / '.skillsync/feedback/F1.preview.json').exists())
        self.assertNotIn('hunter22', json.dumps(result['scan']))

    def test_the_tracker_being_down_means_no_preview(self):
        draft_id = self.new()
        result = self.fb('draft', draft_id, code=2, env={'FAKE_AZ_STATE': str(self.base / 'missing-dir' / 'state.json')})
        self.assertEqual(result['dedup'], 'unknown')
        self.assertFalse((self.root / '.skillsync/feedback/F1.preview.json').exists())

    def test_the_limit_of_sends_per_day(self):
        store = self.root / '.skillsync/feedback'
        store.mkdir(parents=True)
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        for number in range(2, 7):
            (store / f'F{number}.sent.json').write_text(json.dumps({'at': now, 'id': number}), encoding='utf-8')
        draft_id = self.new()
        self.assertEqual(draft_id, 'F7')
        self.assertIn('limit', self.fb('send', draft_id, code=2)['reason'])


class CardAliasTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_no_marker_and_no_variable_is_a_main_session(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(feedback.CARD_ENV, None)
            self.assertIsNone(feedback.card_alias(self.root))

    def test_the_variable_wins_and_is_cut(self):
        with patch.dict(os.environ, {feedback.CARD_ENV: 'A' * 100}):
            self.assertEqual(feedback.card_alias(self.root), 'A' * 40)

    def test_the_marker_gives_the_first_line_and_a_big_file_still_counts(self):
        (self.root / '.skillsync').mkdir()
        marker = self.root / '.skillsync' / 'card-alias'
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(feedback.CARD_ENV, None)
            marker.write_text('Card-9\n', encoding='utf-8')
            self.assertEqual(feedback.card_alias(self.root), 'Card-9')
            marker.write_text('x' * 1000, encoding='utf-8')
            self.assertEqual(feedback.card_alias(self.root), 'card')    # too big to read: counted, shown with the neutral name


class ApprovalRulesTest(unittest.TestCase):
    """`find_approval`: freshness, the future, the preview, the draft and the use of a consent (the clock is injected)."""
    SHA = 'a' * 64

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name) / 'store'
        self.store.mkdir()
        self.now = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
        self.preview_at = self.now - timedelta(hours=1)

    def record(self, at, draft_id='F1', sha=None):
        return feedback.record_approval(self.store, draft_id, sha or self.SHA, now=at)

    def find(self, draft_id='F1', sha=None):
        return feedback.find_approval(self.store, draft_id, sha or self.SHA, self.preview_at, now=self.now)

    def test_a_fresh_consent_after_the_preview_is_found(self):
        entry = self.record(self.now - timedelta(minutes=5))
        self.assertEqual(self.find()['id'], entry['id'])

    def test_older_than_the_preview_or_a_day_old_or_from_the_future_is_not(self):
        self.record(self.preview_at - timedelta(seconds=1))
        self.assertIsNone(self.find())
        self.record(self.now - timedelta(hours=25))
        self.assertIsNone(self.find())
        self.record(self.now + timedelta(minutes=5))
        self.assertIsNone(self.find())

    def test_the_boundaries(self):
        self.record(self.preview_at)                                   # exactly at the preview: allowed
        self.assertIsNotNone(self.find())

    def test_another_draft_or_another_sha_is_not(self):
        self.record(self.now - timedelta(minutes=5), draft_id='F2')
        self.record(self.now - timedelta(minutes=5), sha='b' * 64)
        self.assertIsNone(self.find())

    def test_a_used_consent_is_not_found_again(self):
        entry = self.record(self.now - timedelta(minutes=5))
        (self.store / 'F1.sent.json').write_text(json.dumps({'approval': entry['id']}), encoding='utf-8')
        self.assertIsNone(self.find())
        other = self.record(self.now - timedelta(minutes=4))
        self.assertEqual(self.find()['id'], other['id'])

    def test_an_intent_file_also_uses_the_consent(self):
        entry = self.record(self.now - timedelta(minutes=5))
        (self.store / 'F9.sending.json').write_text(json.dumps({'approval': entry['id']}), encoding='utf-8')
        self.assertIsNone(self.find())

    def test_bad_lines_are_skipped_and_a_huge_file_is_refused(self):
        entry = self.record(self.now - timedelta(minutes=5))
        with (self.store / 'approvals.jsonl').open('a', encoding='utf-8') as handle:
            handle.write('not json\n{"id": 1}\n[]\n' + json.dumps({'id': 'x', 'draft': 'F1', 'sha': 'zz', 'at': 'x', 'source': 'cli'}) + '\n')
        self.assertEqual(self.find()['id'], entry['id'])
        (self.store / 'approvals.jsonl').write_bytes(b'x' * (feedback.MAX_APPROVALS_BYTES + 1))
        with self.assertRaises(feedback.FeedbackError):
            self.find()

    def test_a_record_needs_a_full_sha_and_a_known_source(self):
        for sha, source in (('abc', 'cli'), (self.SHA, 'chat'), ('G' * 64, 'cli')):
            with self.assertRaises(feedback.FeedbackError):
                feedback.record_approval(self.store, 'F1', sha, source=source)

    def test_the_file_is_private(self):
        self.record(self.now)
        self.assertTrue(winacl.is_owner_only(self.store / 'approvals.jsonl'))   # POSIX: no group or other bits; Windows: the ACL (the mode means nothing there)
        if os.name != 'nt':
            self.assertEqual(os.stat(self.store / 'approvals.jsonl').st_mode & 0o777, 0o600)


class ManifestKeyTest(unittest.TestCase):
    def test_validation(self):
        v = feedback.validate_feedback_manifest
        self.assertEqual(v({}), ([], []))
        self.assertEqual(v({'feedback': {'channel': 'ask'}}), ([], []))
        self.assertEqual(v({'feedback': {'channel': 'tracker'}}), ([], []))
        self.assertTrue(v({'feedback': {'channel': 'mail'}})[0])
        self.assertTrue(v({'feedback': 'ask'})[0])
        self.assertTrue(v({'feedback': None})[0])
        self.assertEqual(v({'feedback': {'channel': 'ask', 'x': 1}})[0], [])
        self.assertTrue(v({'feedback': {'channel': 'ask', 'x': 1}})[1])
        self.assertNotIn('mail', json.dumps(v({'feedback': {'channel': 'mail'}})))


@unittest.skipIf(os.name == 'nt', 'POSIX only: needs a pseudo-terminal')
class TrustCommandTest(CommandCase):
    def test_trust_needs_a_terminal(self):
        self.trust_target(False)
        result = self.fb('trust', 'org', code=2)
        self.assertIn('terminal', result['reason'])
        self.assertEqual(feedback.layers.load_machine_config(self.machine_config).feedback_targets, [])

    def run_in_pty(self, answer):
        import pty
        import select
        master, slave = pty.openpty()
        process = subprocess.Popen([sys.executable, str(CLI), 'feedback', 'trust', 'org', str(self.root)], stdin=slave, stdout=slave, stderr=subprocess.PIPE,
                                   cwd=self.root, close_fds=True)
        os.close(slave)
        output = b''
        os.write(master, (answer + '\n').encode())
        while True:
            ready, _, _ = select.select([master], [], [], 20)
            if not ready:
                break
            try:
                data = os.read(master, 4096)
            except OSError:
                break
            if not data:
                break
            output += data
        process.wait(timeout=20)
        process.stderr.close()
        os.close(master)
        return process.returncode, output.decode('utf-8', 'replace')

    def test_trust_with_the_right_answer_writes_the_machine_config(self):
        self.trust_target(False)
        code, output = self.run_in_pty('example-org')
        self.assertEqual(code, 0, output)
        self.assertEqual(feedback.layers.load_machine_config(self.machine_config).feedback_targets, [TRUSTED_ENTRY])

    def test_trust_with_a_wrong_answer_changes_nothing(self):
        self.trust_target(False)
        code, output = self.run_in_pty('yes')
        self.assertEqual(code, 2, output)
        self.assertEqual(feedback.layers.load_machine_config(self.machine_config).feedback_targets, [])


class GuardHookTest(unittest.TestCase):
    HOOK = Path(__file__).resolve().parent.parent / 'library' / 'hooks' / 'guard-commands.py'

    def run_hook(self, command, *args):
        payload = {'tool_name': 'Bash', 'tool_input': {'command': command}, 'cwd': '/tmp'}
        return subprocess.run([sys.executable, str(self.HOOK), *args], input=json.dumps(payload), capture_output=True, text=True, timeout=30)

    def test_the_internal_provider_is_refused_in_every_mode(self):
        for command in ('skillsync tracker --provider azure-feedback --operation workitem-create --run --allow-write',
                        "python3 skillsync tracker . --params '{}' --provider azure-feedback --run",
                        'az devops invoke --area wit --resource workItems # azure-feedback'):
            for args in ((), ('--mode', 'warn')):
                with self.subTest((command[:40], args)):
                    result = self.run_hook(command, *args)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn('skillsync feedback send', result.stderr)

    def test_the_feedback_command_and_other_providers_pass(self):
        for command in ('skillsync feedback send F1', 'skillsync feedback draft F1', 'skillsync tracker --provider azure --operation read',
                        'echo azure-feedback', 'grep -rn azure-feedback docs', 'git commit -m "azure-feedback"\nskillsync tracker --provider azure --operation read'):
            with self.subTest(command):
                self.assertEqual(self.run_hook(command).returncode, 0)


if __name__ == '__main__':
    unittest.main()
