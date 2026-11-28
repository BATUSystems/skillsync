"""The route `POST /api/feedback-approve` of the status page (CORE-1142-FB-BUTTON, S4), against a fake `az` that is never called.

Contract: JSON `{draft: "F<n>", sha: "<64 hex>"}`, the same guards as `/api/approve` (CORE-2000-UNIFIED U-2, `_consent_gate`: Host, local/TLS context,
`X-Status-Board`, JSON content type, exact `Origin`, `Sec-Fetch-Site`, a session with the role `approver`, CSRF; no token), the read socket has no write route,
`--no-approvals` refuses with 403. The logic is `Board.feedback_approve(draft, sha, via)`
= `feedback.approve_from_preview(session, draft, sha=sha, source='dashboard')`: 200 `{ok, draft, sha12, approval, already}` and exactly one row with
`source: dashboard` in `.skillsync/feedback/approvals.jsonl` of the main checkout; refusals carry the `code` of the FeedbackError and a text from the
catalogue (`srv.feedback.<code>`), never a title, a body or the full sha; the log carries only the draft, the sha12 and the `via`.
Names are neutral examples; the passwords of the test accounts are built at run time.
"""
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from tests.test_skillsync_feedback_dashboard import DashboardCase
from tests.test_skillsync_dashboard_ports import request as raw_request, two_ports
from tests.test_skillsync_dashboard_server import SESSION_PW, SessionKit, session_request, split_post
from tests.test_skillsync_workflow import CLI
from skillsync_workflow import dashboard_http as srv
from skillsync_workflow import dashboard_ports as ports
from skillsync_workflow import feedback
from skillsync_workflow import i18n
from skillsync_workflow import prototype_server as proto_server

TOKEN = 'unit-test-token-0123456789abcdef'   # the stand-in value of the removed header `X-Approve-Token`: the server ignores it
ROUTE = '/api/feedback-approve'
CODES = ('card', 'no_preview', 'content_changed', 'stale', 'sent', 'scan')
STATUS_OF_CODE = {'card': 403}   # every other refusal of the consent path is a 409


class RouteCase(DashboardCase):
    APPROVALS = True

    def setUp(self):
        super().setUp()
        self.logged = []
        patcher = patch.object(srv, 'log', side_effect=lambda message: self.logged.append(str(message)))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.board = srv.Board(self.root, poll=0.2, heartbeat=0.5, prototype_port=1, approvals=self.APPROVALS)
        self.board.prototype_acceptance = lambda slug: None
        self.kit = SessionKit(self, base=self.base / 'route-session')
        self.read_port, self.approve_port = two_ports()   # ports of the test slice only
        self.read = srv.make_server(self.board, '127.0.0.1', self.read_port, role=ports.ROLE_READ, peer_port=self.approve_port, proto_app=self.kit.app)
        self.approve = srv.make_server(self.board, '127.0.0.1', self.approve_port, role=ports.ROLE_APPROVE, peer_port=self.read_port, proto_app=self.kit.app)
        self.kit.attach(self.approve)
        for server in (self.read, self.approve):
            threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.session = self.kit.login(self.approve_port)
        self.ok = self.session.headers(self.approve_port)   # a consent of the signed-in approver

    # --- helpers
    def post(self, data, headers=None, port=None, raw=None):
        body = raw if raw is not None else json.dumps(data).encode('utf-8')
        status, answer, _ = raw_request(port or self.approve_port, 'POST', ROUTE, self.ok if headers is None else headers, body)
        return status, answer

    def click(self, draft_id, sha=None, **kwargs):
        return self.post({'draft': draft_id, 'sha': sha or self.full_sha(draft_id)}, **kwargs)

    def status_of(self, port=None):
        # kontrakt U-2-3 (W1): what a consent recorded (`given`) is shown to a signed-in approver, not to an anonymous request
        status, data, _ = raw_request(port or self.approve_port, 'GET', '/api/status', {'Cookie': self.session.pair}, b'')
        self.assertEqual(status, 200)
        return data

    def message(self, code):
        return i18n.t(self.board.lang(), self.board.FEEDBACK_TEXT_KEYS[code])

    def refused_click(self, code, draft_id, sha=None, status=None):
        before = self.approvals()
        got, body = self.click(draft_id, sha)
        self.assertEqual((got, body.get('code')), (status or STATUS_OF_CODE.get(code, 409), code), body)
        self.assertEqual(body['error'], self.message(code))
        self.assertEqual(self.approvals(), before)
        return body

    def assert_no_tracker_call(self, before):
        self.assertEqual(len(self.calls()), before, 'the tracker (az) was called')


class HappyPathTest(RouteCase):
    def test_the_click_records_exactly_one_dashboard_row_with_the_full_sha(self):
        draft_id, preview = self.previewed()
        before = len(self.calls())
        with patch.object(feedback, '_call', side_effect=AssertionError('the route must not call the tracker')):
            status, body = self.click(draft_id)
        self.assertEqual(status, 200, body)
        self.assertEqual((body['ok'], body['draft'], body['sha12'], body['already']), (True, draft_id, preview['sha12'], False))
        rows = self.approvals()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0]['draft'], rows[0]['sha'], rows[0]['source'], rows[0]['id']), (draft_id, self.full_sha(draft_id), 'dashboard', body['approval']))
        self.assert_no_tracker_call(before)
        self.assertFalse((self.root / '.skillsync/runs').exists() and list((self.root / '.skillsync/runs').rglob('approvals.jsonl')), 'a run trail was written')

    def test_the_second_click_is_ok_and_already_without_a_second_row(self):
        draft_id, _ = self.previewed()
        first = self.click(draft_id)
        second = self.click(draft_id)
        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 200, second)
        self.assertTrue(second[1]['already'])
        self.assertEqual(second[1]['approval'], first[1]['approval'])
        self.assertEqual(len(self.approvals()), 1)

    def test_the_consent_of_one_draft_does_not_record_another(self):
        first, _ = self.previewed()
        second, _ = self.previewed()
        self.assertEqual(self.click(first)[0], 200)
        self.assertEqual([row['draft'] for row in self.approvals()], [first])
        self.assertNotIn(second, [row['draft'] for row in self.approvals()])

    def test_after_the_click_the_state_shows_after_given_by_the_dashboard_and_wait_returns_zero(self):
        draft_id, preview = self.previewed()
        self.assertEqual(self.click(draft_id)[0], 200)
        rows = {row['draft']: row for row in self.status_of()['feedback']['drafts']}
        self.assertEqual(rows[draft_id]['state'], 'after')
        self.assertEqual(rows[draft_id]['given']['source'], 'dashboard')
        result = subprocess.run([sys.executable, str(CLI), 'feedback', 'wait', draft_id, str(self.root), '--timeout', '10', '--interval', '0.05'],
                                capture_output=True, text=True, timeout=60, cwd=self.root, stdin=subprocess.DEVNULL)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual((report['status'], report['sha12'], report['source']), ('PASS', preview['sha12'], 'dashboard'))

    def test_board_feedback_approve_is_the_logic_behind_the_route(self):
        draft_id, preview = self.previewed()
        code, data = self.board.feedback_approve(draft_id, self.full_sha(draft_id), None)
        self.assertEqual((code, data['ok'], data['sha12'], data['already']), (200, True, preview['sha12'], False), data)
        code, data = self.board.feedback_approve(draft_id, self.full_sha(draft_id), None)
        self.assertEqual((code, data['already']), (200, True))
        self.assertEqual(len(self.approvals()), 1)

    def test_the_cli_approve_still_records_its_own_source_and_the_dashboard_one_is_idempotent_against_it(self):
        draft_id, preview = self.previewed()
        self.fb('approve', draft_id, '--word', f'WYŚLIJ {preview["sha12"]}')
        status, body = self.click(draft_id)
        self.assertEqual((status, body['already']), (200, True), body)
        self.assertEqual([row['source'] for row in self.approvals()], ['cli'])


class LiveRefreshTest(RouteCase):
    """F2: the page is refreshed by the watcher (`Board.check_once` compares a signature of `watched_paths`): a new preview, a consent from the CLI or another
    card, a send record, and the click itself must change it, or an open page (SSE) stays on the old state until something else moves."""

    @property
    def fbdir(self):
        return self.root.resolve() / '.skillsync/feedback'   # the board resolves its root: on Windows a temp dir under an 8.3 name (PAWEL~1) must be compared in its long form

    def settle(self):
        self.board.check_once()
        self.assertFalse(self.board.check_once(), 'control: without a change the signature is stable (a detector that always says "changed" proves nothing)')

    def watched_in_store(self):
        base = str(self.fbdir)
        return {str(path) for path in self.board.watched_paths() if str(path).startswith(base + os.sep)}

    def assert_changes(self, name, mutate):
        self.settle()
        before = self.board.version
        mutate()
        self.assertTrue(self.board.check_once(), f'{name}: the signature did not change')
        self.assertEqual(self.board.version, before + 1, name)

    def test_the_feedback_files_are_watched_in_the_main_checkout(self):
        draft_id, _ = self.previewed()
        feedback.approve_from_preview(self.open(), draft_id)
        for name in (f'{draft_id}.sent.json', f'{draft_id}.sent.20260101T000000-abc123.json', f'{draft_id}.sending.json'):
            (self.fbdir / name).write_text('{}', encoding='utf-8')
        watched = self.watched_in_store()
        for name in ('approvals.jsonl', f'{draft_id}.json', f'{draft_id}.preview.json', f'{draft_id}.sent.json',
                     f'{draft_id}.sent.20260101T000000-abc123.json', f'{draft_id}.sending.json'):
            with self.subTest(file=name):
                self.assertIn(str(self.fbdir / name), watched)

    def test_the_approvals_file_is_watched_before_it_exists_and_a_missing_store_is_no_error(self):
        self.assertFalse(self.fbdir.exists())
        paths = {str(path) for path in self.board.watched_paths()}
        self.assertIn(str(self.fbdir / 'approvals.jsonl'), paths, 'the first consent creates the file: it must already be on the list')
        self.assertFalse(self.board.check_once())

    def test_the_number_of_watched_feedback_files_is_limited(self):
        self.fbdir.mkdir(parents=True)
        for number in range(1, 261):
            (self.fbdir / f'F{number}.json').write_text('{}', encoding='utf-8')
        watched = self.watched_in_store()
        self.assertLessEqual(len(watched), 210, 'a store with many drafts must not make every poll stat every file')
        self.assertIn(str(self.fbdir / 'approvals.jsonl'), watched)
        self.assertIn(str(self.fbdir / 'F260.json'), watched, 'the newest draft is the one being worked on')

    def test_every_kind_of_feedback_change_moves_the_version(self):
        self.fbdir.mkdir(parents=True)
        draft_id = self.new()
        self.assert_changes('new preview', lambda: self.draft_in_process(draft_id))
        self.assert_changes('preview rewritten', lambda: self.write_preview({**self.read_preview(draft_id), 'note': 'x' * 40}, draft_id))
        self.assert_changes('consent given outside the page', lambda: feedback.approve_from_preview(self.open(), draft_id, source='cli'))
        self.assert_changes('sending record', lambda: (self.fbdir / f'{draft_id}.sending.json').write_text('{"sha": "' + 'a' * 64 + '"}', encoding='utf-8'))
        self.assert_changes('sent record', lambda: (self.fbdir / f'{draft_id}.sent.json').write_text('{"sha": "' + 'a' * 64 + '", "at": "x"}', encoding='utf-8'))
        self.assert_changes('a second draft', lambda: self.new())

    def test_the_running_watcher_notices_a_consent_given_outside_the_page(self):
        draft_id, _ = self.previewed()
        self.board.check_once()
        threading.Thread(target=self.board.watch_loop, daemon=True).start()
        self.addCleanup(self.board.stop.set)
        before = self.board.version
        feedback.approve_from_preview(self.open(), draft_id, source='ide')
        deadline = time.monotonic() + 10
        while self.board.version == before and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreater(self.board.version, before, 'the page would not learn about the consent')

    def test_the_click_wakes_the_watcher_check_after_the_consent_is_written(self):
        draft_id, _ = self.previewed()
        seen = []
        with patch.object(self.board, 'check_once', side_effect=lambda: seen.append(len(self.approvals())) or False):
            code, data = self.board.feedback_approve(draft_id, self.full_sha(draft_id), None)
        self.assertEqual(code, 200, data)
        self.assertEqual(seen, [1], 'feedback_approve must call check_once once, after the row is written (as approve does)')

    def test_the_click_moves_the_version_at_once(self):
        draft_id, _ = self.previewed()
        self.settle()
        before = self.board.version
        self.assertEqual(self.board.feedback_approve(draft_id, self.full_sha(draft_id), None)[0], 200)
        self.assertEqual(self.board.version, before + 1)


class ConcurrencyTest(RouteCase):
    def test_eight_parallel_clicks_record_one_row_and_all_answer_ok(self):
        draft_id, _ = self.previewed()
        sha = self.full_sha(draft_id)
        answers = []
        gate = threading.Barrier(8)

        def go():
            gate.wait(10)
            answers.append(self.post({'draft': draft_id, 'sha': sha}))

        threads = [threading.Thread(target=go) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(len(answers), 8)
        self.assertEqual([status for status, _ in answers], [200] * 8, answers)
        self.assertEqual(len(self.approvals()), 1, self.approvals())
        self.assertEqual(sorted(body['already'] for _, body in answers), [False] + [True] * 7)
        self.assertEqual(len({body['approval'] for _, body in answers}), 1)


class RefusalTest(RouteCase):
    def test_a_sha_that_differs_from_the_preview_is_409_content_changed(self):
        draft_id, _ = self.previewed()
        self.refused_click('content_changed', draft_id, sha='a' * 64)

    def test_a_draft_changed_after_the_preview_is_409_content_changed(self):
        draft_id, _ = self.previewed()
        sha = self.full_sha(draft_id)
        feedback.save_draft(self.root / '.skillsync/feedback', {**feedback_draft(), 'proposal': 'Something else entirely.'}, draft_id)
        self.refused_click('content_changed', draft_id, sha=sha)

    def test_a_target_that_is_no_longer_trusted_is_409_stale(self):
        draft_id, _ = self.previewed()
        sha = self.full_sha(draft_id)
        self.trust_target(False)
        self.refused_click('stale', draft_id, sha=sha)

    def test_a_preview_older_than_24_hours_is_409_stale(self):
        from datetime import timedelta
        draft_id, _ = self.previewed()
        preview = self.read_preview(draft_id)
        self.write_preview({**preview, 'at': feedback._stamp(feedback._now() - timedelta(hours=25))}, draft_id)
        self.refused_click('stale', draft_id, sha=preview['sha'])

    def test_a_sent_draft_is_409_sent(self):
        draft_id, _ = self.approved()
        sha = self.full_sha(draft_id)
        self.assertEqual(self.fb('send', draft_id)['status'], 'PASS')
        self.refused_click('sent', draft_id, sha=sha)

    def test_a_used_consent_is_409_sent_and_no_second_row_appears(self):
        draft_id, _ = self.approved()
        sha = self.full_sha(draft_id)
        self.fb('send', draft_id)
        before = len(self.approvals())
        self.refused_click('sent', draft_id, sha=sha)
        self.assertEqual(len(self.approvals()), before)

    def test_a_draft_without_a_preview_is_409_no_preview(self):
        draft_id = self.new()
        self.refused_click('no_preview', draft_id, sha='b' * 64)

    def test_an_unknown_draft_is_409_no_preview_and_writes_nothing(self):
        self.refused_click('no_preview', 'F99', sha='c' * 64)
        self.assertFalse((self.root / '.skillsync/feedback/F99.json').exists())

    def test_a_preview_whose_scan_did_not_pass_is_409_scan(self):
        draft_id, _ = self.previewed()
        preview = self.read_preview(draft_id)
        preview['view']['scan']['status'] = 'FAIL'
        self.write_preview(preview, draft_id)
        self.refused_click('scan', draft_id, sha=preview['sha'])

    def test_a_card_session_is_403_card(self):
        draft_id, _ = self.previewed()
        with patch.dict(os.environ, {feedback.CARD_ENV: 'Card-1'}):
            self.refused_click('card', draft_id, status=403)

    def marker(self, text='Card-1\n'):
        path = self.root / '.skillsync' / 'card-alias'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_a_card_marker_in_the_server_directory_is_403_card_and_removing_it_allows_the_click(self):
        draft_id, _ = self.previewed()
        marker = self.marker()
        self.refused_click('card', draft_id, status=403)
        marker.unlink()
        self.assertEqual(self.click(draft_id)[0], 200)

    def test_an_empty_or_oversized_marker_is_403_card(self):
        draft_id, _ = self.previewed()
        marker = self.marker('')
        self.refused_click('card', draft_id, status=403)
        marker.write_text('X' * 500, encoding='utf-8')
        self.refused_click('card', draft_id, status=403)

    @unittest.skipIf(os.name == 'nt', 'symlinks unavailable (Windows without Developer Mode)')
    def test_a_marker_that_is_a_link_is_403_card(self):
        draft_id, _ = self.previewed()
        target = self.root / 'elsewhere.txt'
        target.write_text('X\n', encoding='utf-8')
        marker = self.root / '.skillsync' / 'card-alias'
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.symlink_to(target)
        self.refused_click('card', draft_id, status=403)

    def test_the_card_answer_gives_the_way_out_in_full_and_a_marker_wins_over_a_missing_preview(self):
        draft_id = self.new()                    # no preview: the card refusal still comes first
        self.marker()
        body = self.refused_click('card', draft_id, sha='e' * 64, status=403)
        self.assertIn('.skillsync/card-alias', body['error'])
        self.assertIn('SKILLSYNC_CARD_ALIAS', body['error'])
        self.assertTrue(body['error'].rstrip().endswith(('.', ')', ').')), body['error'])
        with patch.dict(os.environ, {feedback.CARD_ENV: 'Card-2'}):   # the variable of the server process counts too, and the page says how to leave it
            self.assertIn('SKILLSYNC_CARD_ALIAS', self.click(draft_id, sha='e' * 64)[1]['error'])

    def test_the_other_draft_gets_no_row_when_one_is_refused(self):
        good, _ = self.previewed()
        bad = self.new()
        self.refused_click('no_preview', bad, sha='d' * 64)
        self.assertEqual(self.click(good)[0], 200)
        self.assertEqual([row['draft'] for row in self.approvals()], [good])


def feedback_draft():
    from tests.test_skillsync_feedback import DRAFT
    return {**DRAFT, 'layer': 'org'}


class ViewTamperedTest(RouteCase):
    def test_a_view_edited_by_hand_is_refused_with_content_changed_and_no_row(self):
        draft_id, _ = self.previewed()
        preview = self.read_preview(draft_id)
        preview['view']['body'] = preview['view']['body'] + 'TAMPERED'
        self.write_preview(preview, draft_id)
        self.refused_click('content_changed', draft_id)


class MessagesAndLogTest(RouteCase):
    def test_every_refusal_code_has_a_text_in_pl_and_en(self):
        for lang in ('pl', 'en'):
            for code in CODES:
                key = srv.Board.FEEDBACK_TEXT_KEYS[code]
                with self.subTest(lang=lang, key=key):
                    text = i18n.t(lang, key)
                    self.assertNotEqual(text, key, 'missing key')
                    self.assertTrue(text.strip())
        for code in CODES:
            self.assertNotEqual(i18n.t('pl', srv.Board.FEEDBACK_TEXT_KEYS[code]), i18n.t('en', srv.Board.FEEDBACK_TEXT_KEYS[code]))

    def test_the_catalogue_files_carry_the_keys(self):
        base = os.path.join(os.path.dirname(srv.__file__), 'data', 'i18n')
        for lang in ('pl', 'en'):
            with open(os.path.join(base, f'{lang}.json'), encoding='utf-8') as handle:
                catalogue = json.load(handle)
            for code in CODES:
                self.assertIn(srv.Board.FEEDBACK_TEXT_KEYS[code], catalogue)

    def test_a_refusal_does_not_quote_the_draft_or_the_sha(self):
        draft_id, _ = self.previewed()
        preview = self.read_preview(draft_id)
        view = preview['view']
        sha = preview['sha']
        self.trust_target(False)
        status, body = self.click(draft_id, sha)
        self.assertEqual(status, 409)
        text = json.dumps(body, ensure_ascii=False)
        for secret in (view['title'], view['body'][:30], sha, sha[:12], 'ExampleProject', 'example-org'):
            self.assertNotIn(secret, text)
        self.assertEqual(set(body), {'error', 'code'})

    def test_the_log_has_only_the_draft_the_sha12_and_the_via_and_never_the_content(self):
        draft_id, preview = self.previewed()
        view = self.read_preview(draft_id)['view']
        self.click(draft_id)
        self.click(draft_id, 'e' * 64)   # a refusal is logged without the content too
        text = '\n'.join(self.logged)
        for secret in (view['title'], view['body'][:30], self.full_sha(draft_id), 'ExampleProject', 'example-org', TOKEN, SESSION_PW, self.session.csrf,
                       self.session.pair.split('=', 1)[1]):
            self.assertNotIn(secret, text)
        self.assertTrue(any(draft_id in line and preview['sha12'] in line for line in self.logged), self.logged)


class GuardsTest(RouteCase):
    """The guards of `/api/approve`, the same function. Nothing is written by any refused request."""

    def setUp(self):
        super().setUp()
        self.draft_id, _ = self.previewed()
        self.data = {'draft': self.draft_id, 'sha': self.full_sha(self.draft_id)}

    def refused(self, headers, expect=403, code=None, data=None, raw=None, port=None):
        status, body = self.post(self.data if data is None else data, headers, port=port, raw=raw)
        self.assertEqual(status, expect, body)
        if code:
            self.assertEqual(body.get('code'), code)
        self.assertEqual(self.approvals(), [])
        return body

    def test_a_foreign_host_is_403(self):
        self.refused({**self.ok, 'Host': 'evil.example'})

    def test_a_foreign_origin_is_403(self):
        self.refused({**self.ok, 'Origin': 'http://evil.example'})

    def test_a_missing_status_board_header_is_403(self):
        self.refused({key: value for key, value in self.ok.items() if key != 'X-Status-Board'})

    def test_a_missing_session_a_stale_one_or_a_wrong_csrf_is_refused(self):
        without = lambda *names: {key: value for key, value in self.ok.items() if key not in names}   # noqa: E731
        self.refused(without('Cookie'), expect=401, code='login_required')
        self.refused({**self.ok, 'Cookie': self.session.stale_pair}, expect=401, code='session_expired')
        self.refused({**self.ok, 'X-Skillsync-CSRF': 'wrong'}, code='consent_refused')
        self.refused(without('X-Skillsync-CSRF'), code='consent_refused')
        self.refused({**without('Cookie', 'X-Skillsync-CSRF'), 'X-Approve-Token': TOKEN}, expect=401, code='login_required')   # the old token opens nothing
        self.assertEqual(self.click(self.draft_id)[0], 200)   # control

    def test_a_content_type_that_is_not_json_is_refused(self):
        status, body = self.post(self.data, {**self.ok, 'Content-Type': 'text/plain'})
        self.assertIn(status, (400, 403), body)
        self.assertEqual(self.approvals(), [])

    def test_the_read_socket_has_no_write_route(self):
        for port in (self.read_port,):
            status, body = self.post(self.data, port=port)
            self.assertEqual((status, body), (404, {'error': i18n.t(self.board.lang(), 'srv.http.noPage')}), body)
        self.assertEqual(self.approvals(), [])

    def test_a_bad_body_is_400_and_writes_nothing(self):
        cases = {'not an object': [1, 2], 'a string': 'F1', 'no fields': {}, 'no sha': {'draft': self.draft_id}, 'no draft': {'sha': self.data['sha']},
                 'draft is not F<n>': {'draft': '../F1', 'sha': self.data['sha']}, 'draft lower case': {'draft': 'f1', 'sha': self.data['sha']},
                 'draft F0': {'draft': 'F0', 'sha': self.data['sha']}, 'draft number': {'draft': 1, 'sha': self.data['sha']},
                 'sha short': {'draft': self.draft_id, 'sha': self.data['sha'][:12]}, 'sha upper': {'draft': self.draft_id, 'sha': self.data['sha'].upper()},
                 'sha not hex': {'draft': self.draft_id, 'sha': 'g' * 64}, 'sha too long': {'draft': self.draft_id, 'sha': self.data['sha'] + '0'},
                 'sha number': {'draft': self.draft_id, 'sha': 5}, 'draft too long': {'draft': 'F' + '1' * 40, 'sha': self.data['sha']},
                 'draft list': {'draft': [self.draft_id], 'sha': self.data['sha']}}
        for name, data in cases.items():
            with self.subTest(name):
                self.refused(self.ok, expect=400, data=data)
        for name, raw in (('not json', b'{nope'), ('empty', b''), ('huge', b'{"draft":"F1","pad":"' + b'x' * 100000 + b'"}')):
            with self.subTest(name):
                self.refused(self.ok, expect=400, raw=raw)

    def test_a_refused_request_leaves_the_draft_unconsented_in_the_state(self):
        self.refused({**self.ok, 'X-Skillsync-CSRF': 'wrong'}, code='consent_refused')
        row = next(row for row in self.status_of()['feedback']['drafts'] if row['draft'] == self.draft_id)
        self.assertEqual((row['state'], row['given']), ('waiting', None))


class SessionAndAuditBeforeTheWriteTest(RouteCase):
    """Kontrakt U-2-3: the consent of a feedback draft is checked again right before its row is written (B1) and needs its audit line (D1)."""

    def setUp(self):
        super().setUp()
        self.draft_id, self.preview = self.previewed()
        self.body = json.dumps({'draft': self.draft_id, 'sha': self.full_sha(self.draft_id)}).encode('utf-8')

    def logout(self):
        status, data, _ = session_request(self.approve_port, 'POST', '/_api/v1/logout', {}, self.ok)
        self.assertLess(status, 300, data)

    def test_a_logout_while_the_body_arrives_is_401_and_records_nothing(self):
        status, answer = split_post(self.approve_port, ROUTE, self.ok, self.body, self.logout)
        self.assertEqual((status, answer.get('code')), (401, 'session_expired'), answer)
        self.assertEqual(self.approvals(), [])

    def test_control_the_two_part_request_with_nothing_in_between_records_the_row(self):
        status, answer = split_post(self.approve_port, ROUTE, self.ok, self.body)
        self.assertEqual(status, 200, answer)
        self.assertEqual(len(self.approvals()), 1)

    def test_an_expiry_while_the_body_arrives_is_401_and_records_nothing(self):
        real = self.kit.app.sessions.clock
        status, answer = split_post(self.approve_port, ROUTE, self.ok, self.body, lambda: setattr(self.kit.app.sessions, 'clock', lambda: real() + 13 * 3600))
        self.assertEqual((status, answer.get('code')), (401, 'session_expired'), answer)
        self.assertEqual(self.approvals(), [])

    def test_the_audit_line_names_the_draft_and_without_it_nothing_is_recorded(self):
        with patch.object(proto_server, 'link_audit_append', side_effect=OSError('disk')):
            status, answer = self.click(self.draft_id)
        self.assertEqual((status, answer.get('code')), (503, 'audit_unavailable'), answer)
        self.assertEqual(self.approvals(), [])
        self.assertEqual(self.click(self.draft_id)[0], 200)   # control
        line = [line for line in self.kit.audit() if line['event'] == 'consent'][-1]
        self.assertEqual((line['word'], line['draft'], line['sha'], line['route'], line['account']),
                         ('feedback', self.draft_id, self.full_sha(self.draft_id), ROUTE, self.kit.account('ap@example.com')))


class SingleSocketAndOffTest(RouteCase):
    def test_a_foreign_host_on_the_single_port_server_is_403(self):
        from tests.serve_start import bind_free
        from tests.test_skillsync_dashboard_server import free_port
        server, port = bind_free(lambda p: srv.make_server(self.board, '127.0.0.1', p, proto_app=self.kit.app), free_port)
        threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        draft_id, _ = self.previewed()
        headers = self.session.headers(port)
        body = json.dumps({'draft': draft_id, 'sha': self.full_sha(draft_id)}).encode('utf-8')
        status, _, _ = raw_request(port, 'POST', ROUTE, {**headers, 'Host': 'evil.example'}, body)
        self.assertEqual(status, 403)
        status, _, _ = raw_request(port, 'POST', ROUTE, {**headers, 'Origin': 'http://evil.example'}, body)
        self.assertEqual(status, 403)
        self.assertEqual(self.approvals(), [])
        status, data, _ = raw_request(port, 'POST', ROUTE, {**headers, 'Origin': f'http://127.0.0.1:{port}'}, body)
        self.assertEqual(status, 200, data)
        self.assertEqual(len(self.approvals()), 1)


class NoApprovalsTest(RouteCase):
    APPROVALS = False

    def test_no_approvals_is_403_like_for_api_approve_and_writes_nothing(self):
        draft_id, _ = self.previewed()
        status, body = self.click(draft_id)
        self.assertEqual(status, 403, body)
        self.assertEqual(body['error'], i18n.t(self.board.lang(), 'srv.http.approvalsOff'))
        self.assertIn('--no-approvals', body['error'])
        self.assertEqual(self.approvals(), [])


if __name__ == '__main__':
    unittest.main()
