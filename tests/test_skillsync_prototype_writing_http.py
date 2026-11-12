"""Krok P2: blokada akceptacji przy markerze zapisu (`.writing.json`) po stronie serwera HTTP.

Kontrakt: spec zadania (K3, K4, K5, K7, K12).  Serwer działa na porcie 0, zegar sklepu i serwera
jest wstrzykiwany (`FakeClock`, sekundy); marker ma czasy w milisekundach.  Każdy test negatywny ma przypadek kontrolny
(to samo żądanie bez markera albo po jego wygaśnięciu przechodzi).
"""
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import prototype as pt  # noqa: E402
from skillsync_workflow import prototype_server as ps  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu)
from tests.test_skillsync_prototype_server import (  # noqa: E402
    RestoreServerCase, uid, write)
from tests.test_skillsync_prototype_accept import (  # noqa: E402
    PROJECT_ACCEPT_URL, PROJECT_URL, ProjectCase, file_operations)

WRITING_KEYS = {'since', 'expiresAt'}
BY = 'zz-writer'   # wyróżnialny autor markera: nie wolno go znaleźć w żadnej odpowiedzi


def put_marker(directory, clock, ttl=900, since_shift=0, window=None, **extra):
    """Zapisuje marker; `clock` w sekundach (zegar serwera), marker w ms."""
    now = int(clock() * 1000) + since_shift
    value = {'version': 1, 'since': now, 'expiresAt': now + (window if window is not None else ttl * 1000), 'by': BY}
    value.update(extra)
    with open(os.path.join(directory, ps.WRITING_MARKER), 'w', encoding='utf-8') as handle:
        json.dump(value, handle)
    return value


class WritingCase(ProjectCase):
    def mark(self, slug, **kwargs):
        return put_marker(self.directories[slug], self.clock, **kwargs)

    def accepts_of(self, slug):
        return [item for item in file_operations(self.directories[slug]) if item['type'] == 'accept']


class OperationsAcceptWritingTests(WritingCase):
    def test_valid_marker_is_409_with_slug_since_expires_and_no_by(self):
        marker = self.mark('demo')
        before = self.journal()
        response = self.check(self.accept(self.acc), 409, 'writing_in_progress')
        body = response.json
        self.assertEqual((body['slug'], body['since'], body['expiresAt']), ('demo', marker['since'], marker['expiresAt']))
        self.assertNotIn('by', body)
        self.assertNotIn(BY, response.text)
        self.assertEqual(self.journal(), before, 'odmowa nic nie zapisuje')

    def test_control_without_the_marker_the_same_request_is_201(self):
        self.check(self.accept(self.acc), 201)

    def test_check_comes_before_stale_view(self):
        payload = self.screen_payload()
        self.mark('demo')
        stale = dict(payload, version='0' * 64)
        self.check(self.acc.op('demo', 'accept', None, stale), 409, 'writing_in_progress')
        os.unlink(os.path.join(self.demo, ps.WRITING_MARKER))
        self.check(self.acc.op('demo', 'accept', None, stale), 409, 'stale_view')   # kontrola: bez markera stale_view

    def test_shape_errors_stay_400_with_a_marker(self):
        self.mark('demo')
        payload = self.screen_payload()
        self.check(self.acc.op('demo', 'accept', None, dict(payload, via='ui')), 400, 'field_not_allowed')
        self.check(self.acc.op('demo', 'accept', None, dict(payload, scope='all')), 400, 'invalid_scope')
        self.check(self.acc.op('demo', 'accept', None, {'scope': 'screen', 'screens': {}, 'version': payload['version']}), 400)

    def test_repeated_cid_is_not_blocked(self):
        cid = uid('c')
        self.check(self.accept(self.acc, cid=cid), 201)
        self.mark('demo')
        again = self.check(self.accept(self.acc, cid=cid), 200)
        self.assertEqual(len(self.accepts_of('demo')), 1)
        self.assertEqual(again.json['operation']['cid'], cid)
        self.check(self.accept(self.acc, screen='s3'), 409, 'writing_in_progress')   # nowy cid jest blokowany

    def test_ttl_expiry_on_the_injected_clock_unblocks_without_a_write_by_the_server(self):
        self.mark('demo', ttl=900)
        self.check(self.accept(self.acc), 409, 'writing_in_progress')
        self.clock.advance(899)
        self.check(self.accept(self.acc, screen='s3'), 409, 'writing_in_progress')
        self.clock.advance(2)
        self.check(self.accept(self.acc), 201)
        self.assertTrue(os.path.exists(os.path.join(self.demo, ps.WRITING_MARKER)), 'odczyt niczego nie usuwa')

    def test_markers_that_are_not_valid_do_not_block(self):
        cases = {'odległa przyszłość': dict(window=2 * 3600 * 1000),
                 'since z przyszłości': dict(since_shift=10 * 60 * 1000),
                 'zły schemat': dict(version=2)}
        for name, kwargs in cases.items():
            with self.subTest(name):
                self.mark('demo', **kwargs)
                self.check(self.accept(self.acc, screen='s2' if name != 'zły schemat' else 's3'), 201)
        write(os.path.join(self.demo, ps.WRITING_MARKER), '{nie json')
        self.check(self.accept(self.acc, screen='s2'), 201)
        os.unlink(os.path.join(self.demo, ps.WRITING_MARKER))
        os.mkdir(os.path.join(self.demo, ps.WRITING_MARKER))
        self.check(self.accept(self.acc, screen='s3'), 201)

    def test_unaccept_is_not_blocked(self):
        self.check(self.accept(self.acc), 201)
        self.mark('demo')
        self.check(self.accept(self.acc, screen='s3'), 409, 'writing_in_progress')   # kontrola: accept jest blokowany
        self.check(self.unaccept(self.acc, 's2'), 201)   # unaccept bez zmian

    def test_marker_does_not_change_hashes_and_the_marker_file_is_not_served(self):
        before = self.listing()
        self.mark('demo')
        after = self.listing()
        self.assertEqual((before['version'], before['screens']), (after['version'], after['screens']))
        self.check(self.anon.get('/demo/.writing.json'), 404)
        self.check(self.anon.get('/demo/%2ewriting.json'), 404)
        self.check(self.anon.get('/demo/index.html'), 200)   # kontrola: zwykły plik jest serwowany


class WritingFieldTests(WritingCase):
    def test_operations_get_has_writing_null_or_without_by(self):
        self.assertIsNone(self.listing()['writing'])
        marker = self.mark('demo')
        raw = self.check(self.anon.operations('demo'), 200).text
        self.assertNotIn(BY, raw)
        field = self.listing()['writing']
        self.assertEqual(set(field), WRITING_KEYS)
        self.assertEqual(field, {'since': marker['since'], 'expiresAt': marker['expiresAt']})
        self.clock.advance(901)
        self.assertIsNone(self.listing()['writing'])

    def test_project_listing_has_writing_per_prototype(self):
        marker = self.mark('beta')
        self.assertNotIn(BY, self.check(self.acc.get(PROJECT_URL), 200).text)
        by = dict((item['slug'], item) for item in self.project())
        self.assertEqual(by['beta']['writing'], {'since': marker['since'], 'expiresAt': marker['expiresAt']})
        for slug in ('alpha', 'demo', 'empty', 'old'):
            self.assertIsNone(by[slug]['writing'], slug)

    def test_one_helper_gives_the_shape(self):
        marker = self.mark('alpha')
        now = int(self.clock() * 1000)
        self.assertEqual(ps.public_writing(self.directories['alpha'], now), {'since': marker['since'], 'expiresAt': marker['expiresAt']})
        self.assertIsNone(ps.public_writing(self.directories['beta'], now))
        self.assertIsNone(ps.public_writing(self.directories['alpha'], marker['expiresAt']))


class ProjectAcceptWritingTests(WritingCase):
    def test_marker_refuses_the_whole_request_before_any_write_with_a_list(self):
        before = self.snapshot()
        marker = self.mark('beta')
        response = self.check(self.post(), 409, 'writing_in_progress')
        self.assertEqual(response.json['writing'], [{'slug': 'beta', 'since': marker['since'], 'expiresAt': marker['expiresAt']}])
        self.assertNotIn('by', response.json['writing'][0])
        self.assertNotIn(BY, response.text)
        self.assertEqual(self.snapshot(), before)
        os.unlink(os.path.join(self.directories['beta'], ps.WRITING_MARKER))
        self.check(self.post(), 201)   # kontrola

    def test_list_holds_every_marked_prototype_in_slug_order(self):
        self.mark('demo')
        self.mark('alpha')
        response = self.check(self.post(), 409, 'writing_in_progress')
        self.assertEqual([item['slug'] for item in response.json['writing']], ['alpha', 'demo'])

    def test_preflight_comes_before_stale_view(self):
        versions = self.versions()
        self.mark('alpha')
        self.edit_screen('beta')
        self.check(self.post(versions=versions), 409, 'writing_in_progress')
        os.unlink(os.path.join(self.directories['alpha'], ps.WRITING_MARKER))
        response = self.check(self.post(versions=versions), 409, 'stale_view')
        self.assertEqual(response.json['stale'], ['beta'])

    def test_empty_and_done_prototypes_are_left_out(self):
        cid = uid('p')
        self.mark('empty')
        self.check(self.post(cid=cid), 201)    # pusty prototyp z markerem nie blokuje
        os.unlink(os.path.join(self.directories['empty'], ps.WRITING_MARKER))
        self.mark('alpha')
        again = self.check(self.post(cid=cid), 200)   # ten sam cid: alpha jest done, marker nie blokuje
        self.assertEqual(again.json['accepted'], ['alpha', 'beta', 'demo'])
        self.assertEqual(len(self.accepts('alpha')), 1)

    def test_a_marker_that_appears_after_the_preflight_is_partial_with_writing(self):
        cid = uid('p')
        versions = self.versions()
        original = ps.CommentsStore.transaction
        fired = []

        def wrapper(store, build, *args, **kwargs):
            if store.slug == 'beta' and not fired:
                fired.extend([self.mark('beta'), self.mark('demo')])
            return original(store, build, *args, **kwargs)

        with mock.patch.object(ps.CommentsStore, 'transaction', wrapper):
            response = self.check(self.post(cid=cid, versions=versions), 409, 'partial')
        beta, demo = fired
        self.assertEqual(response.json['accepted'], ['alpha'])
        self.assertEqual(response.json['pending'], ['beta', 'demo'])
        self.assertEqual(response.json['writing'], [
            {'slug': 'beta', 'since': beta['since'], 'expiresAt': beta['expiresAt']},
            {'slug': 'demo', 'since': demo['since'], 'expiresAt': demo['expiresAt']}])
        self.assertNotIn(BY, response.text)
        self.assertEqual((len(self.accepts('alpha')), len(self.accepts('beta'))), (1, 0))
        self.check(self.post(cid=cid), 409, 'writing_in_progress')   # ponowienie: preflight (alpha done, beta i demo z markerem)
        for slug in ('beta', 'demo'):
            os.unlink(os.path.join(self.directories[slug], ps.WRITING_MARKER))
        done = self.check(self.post(cid=cid), 201).json
        self.assertEqual(done['accepted'], ['alpha', 'beta', 'demo'])
        self.assertEqual(len(self.accepts('alpha')), 1)

    def test_the_detected_slug_is_listed_even_when_its_marker_vanishes_before_the_list_is_read(self):
        original_transaction, original_entries = ps.CommentsStore.transaction, ps.writing_entries
        fired, calls = [], []

        def transaction(store, build, *args, **kwargs):
            if store.slug == 'alpha' and not fired:
                fired.append(self.mark('alpha'))
            return original_transaction(store, build, *args, **kwargs)

        def entries(items, now_ms):
            calls.append(1)
            if len(calls) == 2:   # the list of `partial`: the marker is gone by now
                os.unlink(os.path.join(self.directories['alpha'], ps.WRITING_MARKER))
            return original_entries(items, now_ms)

        with mock.patch.object(ps.CommentsStore, 'transaction', transaction), mock.patch.object(ps, 'writing_entries', entries):
            response = self.check(self.post(), 409, 'partial')
        self.assertEqual(len(calls), 2)
        self.assertEqual(response.json['accepted'], [])
        marker = fired[0]
        self.assertEqual(response.json['writing'], [{'slug': 'alpha', 'since': marker['since'], 'expiresAt': marker['expiresAt']}])


class RestoreWritingTests(RestoreServerCase):
    def mark(self, **kwargs):
        return put_marker(self.demo, self.clock, **kwargs)

    def test_valid_marker_is_409_and_writes_nothing(self):
        before = self.files()
        marker = self.mark()
        response = self.check(self.restore(self.acc), 409, 'writing_in_progress')
        self.assertEqual((response.json['slug'], response.json['since'], response.json['expiresAt']),
                         ('demo', marker['since'], marker['expiresAt']))
        self.assertNotIn('by', response.json)
        self.assert_untouched(before)
        os.unlink(os.path.join(self.demo, ps.WRITING_MARKER))
        self.check(self.restore(self.acc), 201)   # kontrola

    def test_check_comes_before_stale_view_and_after_validation(self):
        self.mark()
        self.check(self.restore(self.acc, version='0' * 64), 409, 'writing_in_progress')
        self.check(self.restore(self.acc, screen='s9'), 409, 'writing_in_progress')   # plan_restore po kontroli
        self.check(self.restore(self.com), 403, 'forbidden')   # rola przed markerem
        self.check(self.restore(self.acc, body={'cid': uid('r'), 'screen': 's2', 'reason': 'x', 'junk': 1}), 400)
        os.unlink(os.path.join(self.demo, ps.WRITING_MARKER))
        self.check(self.restore(self.acc, version='0' * 64), 409, 'stale_view')

    def test_repeated_cid_is_not_blocked_and_expiry_unblocks(self):
        cid = uid('r')
        self.check(self.restore(self.acc, cid=cid), 201)
        self.mark()
        self.check(self.restore(self.acc, body=self.body(cid=cid)), 200)
        self.check(self.restore(self.acc), 409, 'writing_in_progress')
        self.clock.advance(901)
        self.check(self.restore(self.acc), 200)   # wygasł: kontrola przechodzi (nic do zmiany albo kolejny zapis)

    def test_the_check_runs_after_the_recovery_of_a_pending_restore(self):
        order = []
        recover, check = ps.recover_prototype, ps.read_writing

        def recovering(*args, **kwargs):
            order.append('recovery')
            return recover(*args, **kwargs)

        def reading(*args, **kwargs):
            order.append('marker')
            return check(*args, **kwargs)

        self.mark()
        body = self.body()
        with mock.patch.object(ps, 'recover_prototype', recovering), mock.patch.object(ps, 'read_writing', reading):
            self.check(self.restore(self.acc, body=body), 409, 'writing_in_progress')
        self.assertEqual(order, ['recovery', 'marker'])


class GalleryWritingTests(WritingCase):
    def setUp(self):
        super().setUp()
        for slug in ('alpha', 'beta', 'demo'):
            meta = {'slug': slug, 'title': slug.title(), 'requirements': 'r', 'created': '2026-11-05T09:00:00Z',
                    'generated_by': 'test'}
            write(os.path.join(self.directories[slug], 'prototype.json'), json.dumps(meta) + '\n')
        self.start_server(gallery=lambda: pt.gallery_html(self.root, self.root, clock=self.clock))

    def test_live_gallery_marks_only_prototypes_with_a_valid_marker(self):
        self.assertNotIn('data-writing', self.check(self.anon.get('/'), 200).text)
        self.mark('beta')
        html = self.check(self.anon.get('/'), 200).text
        self.assertEqual(html.count('data-writing="true"'), 1)
        card = html.split('<li class="card">')
        beta = [part for part in card if 'href="beta/index.html"' in part][0]
        self.assertIn('data-writing="true"', beta)
        self.assertNotIn('data-writing', [part for part in card if 'href="alpha/index.html"' in part][0])
        self.assertNotIn(BY, html)   # bez `by`
        self.clock.advance(901)
        self.assertNotIn('data-writing', self.check(self.anon.get('/'), 200).text)

    def mark(self, slug, **kwargs):
        return put_marker(self.directories[slug], self.clock, **kwargs)

    def test_static_gallery_never_renders_the_mark(self):
        self.mark('beta')
        entries, _ = pt.gallery_entries(Path(self.root), Path(self.root))
        self.assertNotIn('writing', entries[0])
        static = pt.render_gallery(entries)
        self.assertNotIn('data-writing', static)
        self.assertNotIn('zapisu', static)
        live_entries, _ = pt.gallery_entries(Path(self.root), Path(self.root), int(self.clock() * 1000))
        self.assertIn('data-writing="true"', pt.render_gallery(live_entries))   # kontrola: ta sama funkcja z flagą

    def test_serve_wires_the_injected_clock_of_the_server_into_the_gallery(self):
        """`serve_prototypes` buduje galerię zegarem konfiguracji serwera (nie `time.time`)."""
        seen = []
        with mock.patch.object(pt, 'gallery_html', lambda root, repo, clock=None: seen.append(clock) or '<html></html>'), \
                mock.patch.object(pt.server, 'make_server', side_effect=RuntimeError('stop')), \
                mock.patch.object(pt.server, 'ServerConfig') as config:
            config.return_value.clock = self.clock
            args = mock.Mock(no_source_writes=True, users=None, host='127.0.0.1', port=0, secure_cookies=False,
                             trust_proxy=False, login_to_view=False)
            with mock.patch.object(pt, '_settings', return_value={'root': Path(self.root), 'warnings': []}), \
                    mock.patch.object(pt, 'users_file_path', return_value='x'), \
                    mock.patch.object(pt.server, 'link_run_dir_of', return_value=None):
                with self.assertRaises(RuntimeError):
                    pt.serve_prototypes(self.root, {}, args)
            gallery = config.call_args.kwargs['gallery']
            gallery()
        self.assertEqual(seen, [self.clock])

    def test_live_mark_adds_no_script(self):
        self.mark('beta')
        self.assertNotIn('<script', self.check(self.anon.get('/'), 200).text)


class WritingRaceTests(WritingCase):
    """K12: `accept` i prawdziwe `writing_prototype(..., 'start')` pod tą samą blokadą prototypu; blokada trzymana
    w wątku testu; w każdej kolejności dokładnie jeden wynik."""

    def setUp(self):
        super().setUp()
        store = ps.CommentsStore(self.demo, 'demo', clock=self.clock, lock_timeout=5.0)
        patcher = mock.patch.object(pt, '_prototype_context', lambda root, manifest, slug: (
            Path(self.root), {'warnings': []}, Path(self.demo), store))
        patcher.start()
        self.addCleanup(patcher.stop)

    def hold(self):
        return ps.CommentsStore(self.demo, 'demo', clock=self.clock).lock()

    def run_accept(self, results):
        payload = self.screen_payload()
        thread = threading.Thread(target=lambda: results.append(self.acc.op('demo', 'accept', None, payload)))
        thread.start()
        return thread

    def run_start(self, results):
        thread = threading.Thread(target=lambda: results.append(pt.writing_prototype(self.root, {}, 'demo', 'start')))
        thread.start()
        return thread

    def marker_exists(self):
        return os.path.exists(os.path.join(self.demo, ps.WRITING_MARKER))

    def test_start_takes_the_lock_first_then_accept_is_refused(self):
        starts, accepts = [], []
        with self.hold():
            thread = self.run_start(starts)
            thread.join(0.3)
            self.assertTrue(thread.is_alive(), 'start czeka na blokadę')
            self.assertFalse(self.marker_exists())
        thread.join(10)
        self.assertTrue(starts, 'wątek start nie zwrócił wyniku')
        self.assertEqual(starts[0]['status'], 'PASS')
        self.assertTrue(self.marker_exists())
        self.run_accept(accepts).join(10)
        self.assertTrue(accepts, 'wątek accept nie zwrócił wyniku')
        self.assertEqual((accepts[0].status, accepts[0].json['error']), (409, 'writing_in_progress'), accepts[0].text)
        self.assertEqual(self.accepts_of('demo'), [])

    def test_accept_takes_the_lock_first_then_it_is_kept_and_the_next_one_is_refused(self):
        accepts, starts = [], []
        with self.hold():
            thread = self.run_accept(accepts)
            thread.join(0.3)
            self.assertTrue(thread.is_alive(), 'akceptacja czeka na blokadę')
        thread.join(10)
        self.assertTrue(accepts, 'wątek accept nie zwrócił wyniku')
        self.assertEqual(accepts[0].status, 201, accepts[0].text)
        self.run_start(starts).join(10)
        self.assertTrue(starts, 'wątek start nie zwrócił wyniku')
        self.assertEqual(starts[0]['status'], 'PASS')
        self.assertEqual(len(self.accepts_of('demo')), 1, 'akceptacja sprzed markera zostaje')
        self.check(self.accept(self.acc, screen='s3'), 409, 'writing_in_progress')
        self.assertEqual(len(self.accepts_of('demo')), 1)

    def test_marker_written_under_the_lock_refuses_the_waiting_accept(self):
        accepts = []
        with self.hold():
            thread = self.run_accept(accepts)
            thread.join(0.3)
            self.assertTrue(thread.is_alive())
            ps.write_writing(self.demo, int(self.clock() * 1000), 900)
        thread.join(10)
        self.assertTrue(accepts, 'wątek accept nie zwrócił wyniku')
        self.assertEqual((accepts[0].status, accepts[0].json['error']), (409, 'writing_in_progress'), accepts[0].text)
        self.assertEqual(self.accepts_of('demo'), [])


if __name__ == '__main__':
    unittest.main()
