"""The shared consent path and the preview snapshot of the feedback channel (CORE-1142-FB-BUTTON, S1 and S2), against a fake `az` (no network).

S1: `feedback.approve_from_preview(session, draft_id, *, sha=None, sha12=None, source='cli')` is the one place where a consent is recorded (the CLI `approve`
after the word, and the status page route later), `feedback.open_session(path, layers=None)` builds the session outside the CLI, `FeedbackError.code` names
the refusal. S2: `draft` adds `view` (a snapshot to show) to `F<n>.preview.json`, only for a passing scan and within `PREVIEW_MAX_BYTES`.
Names are neutral examples; the credential-shaped text is assembled at run time.
"""
import argparse
import json
import os
import unittest
from datetime import timedelta
from unittest.mock import patch

from tests.test_skillsync_feedback import DRAFT
from tests.test_skillsync_feedback_command import CommandCase
from skillsync_workflow import feedback


class DashboardCase(CommandCase):
    def open(self):
        return feedback.open_session(self.root)

    def draft_in_process(self, draft_id):
        return feedback.cmd_draft(argparse.Namespace(path=str(self.root), layers=None, draft=draft_id, json=False))

    def preview_file(self, draft_id='F1'):
        return feedback.preview_path(self.root / '.skillsync/feedback', draft_id)

    def read_preview(self, draft_id='F1'):
        return json.loads(self.preview_file(draft_id).read_text(encoding='utf-8'))

    def write_preview(self, value, draft_id='F1'):
        self.preview_file(draft_id).write_text(json.dumps(value), encoding='utf-8')

    def approvals(self):
        path = self.root / '.skillsync/feedback/approvals.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()] if path.exists() else []

    def full_sha(self, draft_id='F1'):
        return self.read_preview(draft_id)['sha']

    def refused(self, code, *args, **kwargs):
        """approve_from_preview refuses with the exact `code` and records nothing new."""
        before = self.approvals()
        with self.assertRaises(feedback.FeedbackError) as caught:
            feedback.approve_from_preview(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        self.assertEqual(self.approvals(), before)
        return caught.exception

    def open_comment_item(self, title='An open item of the same cause'):
        tag = feedback.dedup_key(feedback.validate_draft({**DRAFT, 'layer': 'org'}))
        self.state.write_text(json.dumps({'items': [{'id': 100, 'url': 'https://example.invalid/items/100',
                                                      'fields': {'System.Title': title, 'System.Tags': f'feedback; {tag}', 'System.State': 'Proposed'}}],
                                          'comments': [], 'calls': []}), encoding='utf-8')


class SessionAndErrorTest(DashboardCase):
    def test_open_session_gives_the_project_store_config_and_library(self):
        session = self.open()
        self.assertEqual(session.store, feedback.store_root(self.root))
        self.assertTrue(session.library.layers)
        self.assertIsNotNone(session.config)
        self.assertIsNotNone(session.layer('org'))

    def test_the_error_code_defaults_to_none_and_keeps_the_message(self):
        error = feedback.FeedbackError('a message')
        self.assertIsNone(error.code)
        self.assertEqual(str(error), 'a message')
        self.assertIsNone(feedback.FeedbackError('draft id must look like F<n>').code)


class RefusalCodeTest(DashboardCase):
    def test_a_card_session_is_refused_with_code_card(self):
        draft_id, _ = self.previewed()
        session = self.open()
        with patch.dict(os.environ, {feedback.CARD_ENV: 'Card-1'}):
            self.refused('card', session, draft_id)

    def test_a_draft_without_a_preview_is_refused_with_code_no_preview(self):
        draft_id = self.new()
        self.refused('no_preview', self.open(), draft_id)

    def test_a_full_sha_that_differs_from_the_preview_is_refused_with_code_content_changed(self):
        draft_id, _ = self.previewed()
        self.refused('content_changed', self.open(), draft_id, sha='a' * 64)

    def test_a_sha12_that_differs_from_the_preview_is_refused_with_code_content_changed(self):
        draft_id, _ = self.previewed()
        self.refused('content_changed', self.open(), draft_id, sha12='0123456789ab')

    def test_a_draft_changed_after_the_preview_is_refused_with_code_content_changed(self):
        draft_id, preview = self.previewed()
        feedback.save_draft(self.root / '.skillsync/feedback', {**DRAFT, 'layer': 'org', 'proposal': 'Something else entirely.'}, draft_id)
        self.refused('content_changed', self.open(), draft_id, sha=self.full_sha(), sha12=preview['sha12'])

    def test_a_target_that_is_no_longer_trusted_is_refused_with_code_stale(self):
        draft_id, _ = self.previewed()
        self.trust_target(False)
        self.refused('stale', self.open(), draft_id)

    def test_a_target_whose_project_changed_is_refused_with_code_stale(self):
        draft_id, _ = self.previewed()
        self.use_layers(('core', 'core', self.library), ('org', 'org', self.org),
                        feedback_targets=[{**feedback_trusted(), 'project': 'OtherProject'}])
        self.refused('stale', self.open(), draft_id)

    def test_a_preview_older_than_24_hours_is_refused_with_code_stale(self):
        draft_id, _ = self.previewed()
        preview = self.read_preview()
        self.write_preview({**preview, 'at': feedback._stamp(feedback._now() - timedelta(hours=25))})
        self.refused('stale', self.open(), draft_id)

    def test_a_preview_younger_than_24_hours_is_still_accepted(self):
        draft_id, _ = self.previewed()
        preview = self.read_preview()
        self.write_preview({**preview, 'at': feedback._stamp(feedback._now() - timedelta(hours=23))})
        self.assertEqual(feedback.approve_from_preview(self.open(), draft_id)['status'], 'PASS')

    def test_a_preview_whose_scan_did_not_pass_is_refused(self):
        draft_id, _ = self.previewed()
        preview = self.read_preview()
        self.assertEqual(preview['view']['scan']['status'], 'PASS')
        preview['view']['scan']['status'] = 'FAIL'
        self.write_preview(preview)
        before = self.approvals()
        with self.assertRaises(feedback.FeedbackError) as caught:
            feedback.approve_from_preview(self.open(), draft_id)
        self.assertIn(caught.exception.code, ('stale', 'scan'))
        self.assertEqual(self.approvals(), before)

    def test_a_draft_that_was_already_sent_is_refused_with_code_sent(self):
        draft_id, _ = self.approved()
        self.assertEqual(self.fb('send', draft_id)['status'], 'PASS')
        self.refused('sent', self.open(), draft_id)

    def test_a_changed_draft_after_a_send_still_goes_through_the_replace_flow(self):
        # the sent record belongs to the old content: a new preview of new content may be approved again (`send --replace` needs that consent)
        draft_id, _ = self.approved()
        self.fb('send', draft_id)
        feedback.save_draft(self.root / '.skillsync/feedback', {**DRAFT, 'layer': 'org', 'cause_key': 'new-cause', 'title': 'A second request'}, draft_id)
        self.fb('draft', draft_id)
        self.assertEqual(feedback.approve_from_preview(self.open(), draft_id)['status'], 'PASS')


class ApprovalTest(DashboardCase):
    def test_a_good_consent_is_recorded_and_reported(self):
        draft_id, preview = self.previewed()
        result = feedback.approve_from_preview(self.open(), draft_id, sha=self.full_sha(), sha12=preview['sha12'])
        self.assertEqual((result['status'], result['draft'], result['sha12'], result['already']), ('PASS', draft_id, preview['sha12'], False))
        rows = self.approvals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(result['approval'], rows[0]['id'])

    def test_the_entry_keeps_the_format_id_draft_sha_at_source(self):
        draft_id, _ = self.previewed()
        feedback.approve_from_preview(self.open(), draft_id)
        row = self.approvals()[0]
        self.assertEqual(sorted(row), ['at', 'draft', 'id', 'sha', 'source'])
        self.assertEqual((row['draft'], row['sha'], row['source']), (draft_id, self.full_sha(), 'cli'))
        self.assertEqual(feedback.read_approvals(self.root / '.skillsync/feedback')[0]['id'], row['id'])

    def test_every_allowed_source_is_recorded_as_given(self):
        for source in ('cli', 'ide', 'dashboard'):
            with self.subTest(source):
                draft_id, _ = self.previewed()
                feedback.approve_from_preview(self.open(), draft_id, source=source)
                self.assertEqual([row['source'] for row in self.approvals() if row['draft'] == draft_id], [source])

    def test_an_unknown_source_is_refused_and_records_nothing(self):
        draft_id, _ = self.previewed()
        for source in ('web', 'status', '', None):
            with self.subTest(source), self.assertRaises(feedback.FeedbackError):
                feedback.approve_from_preview(self.open(), draft_id, source=source)
        self.assertEqual(self.approvals(), [])

    def test_a_matching_full_sha_and_sha12_are_accepted(self):
        draft_id, preview = self.previewed()
        self.assertEqual(feedback.approve_from_preview(self.open(), draft_id, sha=self.full_sha())['status'], 'PASS')
        other, _ = self.previewed()
        self.assertEqual(feedback.approve_from_preview(self.open(), other, sha12=preview['sha12'])['status'], 'PASS')

    def test_the_consent_works_for_the_send_and_is_used_once(self):
        draft_id, _ = self.previewed()
        result = feedback.approve_from_preview(self.open(), draft_id, source='dashboard')
        sent = self.fb('send', draft_id)
        self.assertEqual(sent['status'], 'PASS')
        record = json.loads((self.root / '.skillsync/feedback/F1.sent.json').read_text(encoding='utf-8'))
        self.assertEqual(record['approval'], result['approval'])
        self.assertIn(result['approval'], feedback.consumed_approvals(self.root / '.skillsync/feedback'))


class IdempotenceTest(DashboardCase):
    def test_a_second_call_returns_the_same_approval_and_adds_no_row(self):
        draft_id, _ = self.previewed()
        first = feedback.approve_from_preview(self.open(), draft_id, source='dashboard')
        second = feedback.approve_from_preview(self.open(), draft_id, source='dashboard')
        self.assertEqual((first['already'], second['already']), (False, True))
        self.assertEqual(second['approval'], first['approval'])
        self.assertEqual(len(self.approvals()), 1)

    def test_a_second_call_from_another_source_does_not_add_a_row_either(self):
        draft_id, _ = self.previewed()
        first = feedback.approve_from_preview(self.open(), draft_id, source='cli')
        second = feedback.approve_from_preview(self.open(), draft_id, source='dashboard')
        self.assertEqual((second['already'], second['approval']), (True, first['approval']))
        self.assertEqual([row['source'] for row in self.approvals()], ['cli'])

    def test_after_the_send_used_the_consent_a_new_call_is_refused_with_code_sent(self):
        draft_id, _ = self.previewed()
        feedback.approve_from_preview(self.open(), draft_id)
        self.fb('send', draft_id)
        self.refused('sent', self.open(), draft_id)

    def test_a_consent_older_than_a_new_preview_of_the_same_content_does_not_count_so_a_new_one_is_written(self):
        draft_id, _ = self.previewed()
        first = feedback.approve_from_preview(self.open(), draft_id)
        self.fb('draft', draft_id)   # a new preview: the old consent is older than it (find_approval)
        second = feedback.approve_from_preview(self.open(), draft_id)
        self.assertFalse(second['already'])
        self.assertNotEqual(second['approval'], first['approval'])
        self.assertEqual(len(self.approvals()), 2)

    def test_the_consent_of_another_draft_with_the_same_content_is_not_reused(self):
        first_id, _ = self.previewed()
        second_id, _ = self.previewed()
        feedback.approve_from_preview(self.open(), first_id)
        result = feedback.approve_from_preview(self.open(), second_id)
        self.assertFalse(result['already'])
        self.assertEqual(sorted(row['draft'] for row in self.approvals()), [first_id, second_id])


class CliApproveTest(DashboardCase):
    def test_the_exact_word_records_one_consent_with_the_cli_source(self):
        draft_id, preview = self.previewed()
        result = self.fb('approve', draft_id, '--word', 'WYŚLIJ ' + preview['sha12'])
        self.assertEqual((result['status'], result['draft'], result['sha12']), ('PASS', draft_id, preview['sha12']))
        rows = self.approvals()
        self.assertEqual([(row['draft'], row['sha'], row['source'], row['id']) for row in rows], [(draft_id, self.full_sha(), 'cli', result['approval'])])

    def test_the_ide_source_is_kept(self):
        draft_id, preview = self.previewed()
        self.fb('approve', draft_id, '--word', 'WYŚLIJ ' + preview['sha12'], '--source', 'ide')
        self.assertEqual([row['source'] for row in self.approvals()], ['ide'])

    def test_a_wrong_sha12_in_the_word_is_refused_and_records_nothing(self):
        draft_id, _ = self.previewed()
        result = self.fb('approve', draft_id, '--word', 'WYŚLIJ 0123456789ab', code=2)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(self.approvals(), [])

    def test_the_same_word_twice_records_one_row_and_names_the_same_approval(self):
        draft_id, preview = self.previewed()
        first = self.fb('approve', draft_id, '--word', 'WYŚLIJ ' + preview['sha12'])
        second = self.fb('approve', draft_id, '--word', 'WYSLIJ ' + preview['sha12'])
        self.assertEqual((second['status'], second['approval']), ('PASS', first['approval']))
        self.assertEqual(len(self.approvals()), 1)

    def test_a_card_session_still_records_nothing(self):
        draft_id, preview = self.previewed()
        result = self.fb('approve', draft_id, '--word', 'WYŚLIJ ' + preview['sha12'], code=2, env={feedback.CARD_ENV: 'Card-1'})
        self.assertIn('card session', result['reason'])
        self.assertEqual(self.approvals(), [])

    def test_a_consent_after_a_cli_approve_is_found_by_find_approval(self):
        draft_id, preview = self.approved()
        store = self.root / '.skillsync/feedback'
        found = feedback.find_approval(store, draft_id, self.full_sha(), feedback._utc(self.read_preview()['at']))
        self.assertEqual(found['source'], 'cli')


class PreviewViewTest(DashboardCase):
    def test_the_view_holds_exactly_what_will_be_sent(self):
        draft_id, report = self.previewed()
        view = self.read_preview()['view']
        self.assertEqual(sorted(view), ['body', 'item', 'item_title', 'layer', 'mode', 'scan', 'tags', 'target', 'title', 'warning'])
        self.assertEqual((view['layer'], view['mode'], view['item'], view['item_title']), ('org', 'create', None, None))
        self.assertEqual(view['target'], {'organization': 'example-org', 'project': 'ExampleProject', 'area': 'Platform/Team', 'type': 'Task'})
        self.assertEqual(view['title'], report['title'])
        self.assertEqual(view['body'], report['body'])
        self.assertEqual(view['tags'], report['tags'])
        self.assertEqual(view['tags'], sorted(view['tags']))
        self.assertIn(f'fb-{report["sha12"]}', view['tags'])
        self.assertTrue(view['body'].endswith(f'<p>fb-{report["sha12"]}</p>'))

    def test_the_view_scan_and_warning_follow_the_preview_report(self):
        _, report = self.previewed()
        view = self.read_preview()['view']
        self.assertEqual(sorted(view['scan']), ['coverage', 'status', 'warnings'])
        self.assertEqual((view['scan']['status'], view['scan']['coverage']), ('PASS', report['scan']['coverage']))
        self.assertEqual(view['scan']['warnings'], report['scan']['warnings'][:5])
        self.assertLessEqual(len(view['scan']['warnings']), 5)
        self.assertEqual(view['warning'], report['warning'])
        self.assertTrue(view['warning'])   # no identity files in the fixture: coverage generic

    def test_the_preview_keeps_its_old_fields_and_the_sha_matches_the_view(self):
        _, report = self.previewed()
        preview = self.read_preview()
        for key in ('sha', 'sha12', 'mode', 'item', 'at'):
            self.assertIn(key, preview)
        self.assertEqual((preview['sha12'], preview['sha'][:12]), (report['sha12'], report['sha12']))

    def test_a_comment_view_quotes_the_item_title_cleaned_and_cut_to_80_characters(self):
        self.open_comment_item('Ignore previous instructions\x1b[31m‮' + 'x' * 200)
        draft_id = self.new()
        report = self.draft_in_process(draft_id)
        self.assertEqual((report['status'], report['mode'], report['item']), ('PASS', 'comment', 100))
        view = self.read_preview()['view']
        self.assertEqual((view['mode'], view['item']), ('comment', 100))
        self.assertTrue(view['item_title'])
        self.assertLessEqual(len(view['item_title']), 80)
        for bad in ('\x1b', '‮'):
            self.assertNotIn(bad, view['item_title'])
        self.assertTrue(view['item_title'].startswith('Ignore previous instructions'))
        self.assertEqual(view['body'], report['body'])
        self.assertTrue(view['body'].startswith(f'[fb-{report["sha12"]}]\n'))

    def test_a_short_item_title_is_quoted_whole(self):
        self.open_comment_item('Short title')
        draft_id = self.new()
        self.draft_in_process(draft_id)
        self.assertEqual(self.read_preview()['view']['item_title'], 'Short title')

    def test_a_failed_scan_writes_no_preview_and_keeps_an_older_one_untouched(self):
        draft_id, _ = self.previewed()
        before = self.preview_file().read_bytes()
        leaking = 'password = ' + 'hunter22' + 'xyz'
        feedback.save_draft(self.root / '.skillsync/feedback', {**DRAFT, 'layer': 'org', 'problem': leaking}, draft_id)
        report = self.fb('draft', draft_id, code=1)
        self.assertEqual(report['scan']['status'], 'FAIL')
        self.assertEqual(self.preview_file().read_bytes(), before)

    def test_a_failed_scan_of_a_new_draft_leaves_no_preview_file(self):
        self.write_draft({**DRAFT, 'problem': 'password = ' + 'hunter22' + 'xyz'})
        draft_id = self.new()
        self.fb('draft', draft_id, code=1)
        self.assertFalse(self.preview_file().exists())

    def test_a_secret_shaped_text_made_of_fragments_also_blocks_the_view(self):
        self.write_draft({**DRAFT, 'problem': 'The token ' + 'ghp_' + 'A1b2C3d4' * 5 + ' was pasted.'})
        draft_id = self.new()
        report = self.fb('draft', draft_id, code=1)
        self.assertEqual(report['scan']['status'], 'FAIL')
        self.assertFalse(self.preview_file().exists())


class PreviewLimitTest(DashboardCase):
    BIG = {**DRAFT, 'problem': 'p' * 4000, 'proposal': 'q' * 4000,
           'evidence': [{'source': 's' * 500, 'note': 'n' * 3900}], 'acceptance': [{'input': 'i' * 500, 'expected': 'e' * 3900}]}

    def test_the_default_limit_is_128_kib(self):
        self.assertEqual(feedback.PREVIEW_MAX_BYTES, 131072)

    def test_a_big_draft_within_the_default_limit_keeps_its_view(self):
        self.write_draft(self.BIG)
        draft_id = self.new()
        report = self.draft_in_process(draft_id)
        self.assertEqual(report['status'], 'PASS')
        preview = self.read_preview()
        self.assertEqual(preview['view']['body'], report['body'])
        self.assertLessEqual(self.preview_file().stat().st_size, feedback.PREVIEW_MAX_BYTES)

    def test_a_snapshot_over_the_limit_is_saved_without_the_view_and_is_no_error(self):
        self.write_draft(self.BIG)
        draft_id = self.new()
        with patch.object(feedback, 'PREVIEW_MAX_BYTES', 2000):
            report = self.draft_in_process(draft_id)
        self.assertEqual(report['status'], 'PASS')
        preview = self.read_preview()
        self.assertNotIn('view', preview)
        self.assertEqual((preview['sha'][:12], preview['sha12']), (report['sha12'], report['sha12']))
        self.assertEqual(preview['mode'], 'create')
        self.assertLessEqual(self.preview_file().stat().st_size, 2000)

    def test_a_preview_without_the_view_still_allows_the_consent_and_the_send(self):
        self.write_draft(self.BIG)
        draft_id = self.new()
        with patch.object(feedback, 'PREVIEW_MAX_BYTES', 2000):
            report = self.draft_in_process(draft_id)
        result = feedback.approve_from_preview(self.open(), draft_id, sha12=report['sha12'])
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(self.fb('send', draft_id)['status'], 'PASS')


class OldPreviewTest(DashboardCase):
    def strip_view(self, draft_id='F1'):
        preview = self.read_preview(draft_id)
        preview.pop('view', None)
        self.write_preview(preview, draft_id)

    def test_approve_from_preview_accepts_an_old_preview_without_the_view(self):
        draft_id, _ = self.previewed()
        self.strip_view()
        self.assertEqual(feedback.approve_from_preview(self.open(), draft_id)['status'], 'PASS')
        self.assertEqual(len(self.approvals()), 1)

    def test_the_cli_approve_and_the_send_work_on_an_old_preview(self):
        draft_id, preview = self.previewed()
        self.strip_view()
        self.fb('approve', draft_id, '--word', 'WYŚLIJ ' + preview['sha12'])
        self.assertEqual(self.fb('send', draft_id)['status'], 'PASS')

    def test_a_send_without_a_consent_stays_refused_on_an_old_preview(self):
        draft_id, _ = self.previewed()
        self.strip_view()
        self.fb('send', draft_id, code=2)
        self.assertEqual(self.approvals(), [])


def feedback_trusted():
    from tests.test_skillsync_feedback_command import TRUSTED_ENTRY
    return TRUSTED_ENTRY


if __name__ == '__main__':
    unittest.main()
