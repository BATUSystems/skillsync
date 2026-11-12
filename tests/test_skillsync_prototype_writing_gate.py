"""CLI prototypu a marker zapisu ekranów: `accept`, `gate`, `wait`, `list`, `iterate` (K7, K8, K12 część CLI).

Kontrakt: spec zadania (K5, K7, K8, K12, „Kontrakty -> gate/wait/accept CLI").  Wywołania w procesie
testu (`pt.accept_prototype`, `pt.prototype_gate`, `pt.wait_prototype`, `pt.writing_prototype`) z zegarem sklepu
wstrzykiwanym przez `pt._store`; `getpass` i TTY są atrapami (dowód: odmowa przed pytaniem o hasło = brak wywołania).
Marker testowy ma wyróżnialne `by`, które nie może trafić do żadnego wyniku.  Każdy test negatywny ma kontrolę.
Uruchamianie: `python3 -m unittest discover -s tests -p test_skillsync_prototype_writing_gate.py`.
Zgodność ze składnią Pythona 3.8.
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
from tests.test_skillsync_prototype import PrototypeFixture  # noqa: E402
from tests.test_skillsync_prototype_accept import CliProject, SECRET_PW, make_project_users  # noqa: E402
from tests.test_skillsync_prototype_server import setUpModule, tearDownModule  # noqa: E402,F401  (strażnik katalogu)

BY = 'zz-writer'
NOW = 1_800_000_000.0   # sekundy; zegar testu


class Clock(object):
    def __init__(self, value=NOW):
        self.value = value

    def __call__(self):
        return self.value

    @property
    def ms(self):
        return int(self.value * 1000)


class WritingGateCase(CliProject):
    """alpha i beta (v2, ekrany s2 i s3), empty (bez ekranów), old (v1); zegar i `_store` wstrzykiwane w procesie."""

    def setUp(self):
        super().setUp()
        self.clock = Clock()
        self.users_path = make_project_users(self.root, [('celina@example.com', 'acceptor', SECRET_PW)])
        self.project = pt.read_project(str(self.root))
        patcher = mock.patch.object(pt, '_store', lambda directory, slug: ps.CommentsStore(
            str(directory), slug, clock=self.clock, lock_timeout=0.5))
        patcher.start()
        self.addCleanup(patcher.stop)

    def mark(self, slug, ttl=900, by=BY, at=None):
        directory = self.proto_dir(slug)
        ps.write_writing(str(directory), self.clock.ms if at is None else at, ttl, by)
        return directory / ps.WRITING_MARKER

    def unmark(self, slug):
        os.unlink(str(self.proto_dir(slug) / ps.WRITING_MARKER))

    def no_by(self, report):
        self.assertNotIn(BY, json.dumps(report), 'by of the marker leaked into the report')

    def accepted(self, slug):
        return len(self.accepts(slug))


class AcceptWritingTests(WritingGateCase):
    def accept(self, *args, **kwargs):
        """`accept_prototype` w procesie: TTY i hasło to atrapy; zwraca (raport, atrapa getpass)."""
        root, manifest = self.project
        with mock.patch.object(pt, '_stdin_is_tty', return_value=True), \
                mock.patch.object(pt.getpass, 'getpass', return_value=SECRET_PW) as password:
            report = pt.accept_prototype(root, manifest, *args, by='celina@example.com', **kwargs)
        self.no_by(report)
        return report, password

    def test_single_is_refused_before_the_password_prompt(self):
        self.mark('alpha')
        report, password = self.accept('alpha', all_screens=True)
        self.assertEqual((report['status'], report['error']), ('BLOCKED', 'writing_in_progress'))
        self.assertEqual((report['slug'], report['since'], report['expiresAt']), ('alpha', self.clock.ms, self.clock.ms + 900_000))
        self.assertNotIn('by', report)
        password.assert_not_called()
        self.assertEqual(self.accepted('alpha'), 0)
        # kontrola: ten sam prototyp bez markera pyta o hasło i akceptuje
        self.unmark('alpha')
        report, password = self.accept('alpha', all_screens=True)
        self.assertEqual(report['status'], 'PASS', report)
        password.assert_called_once()
        self.assertEqual(self.accepted('alpha'), 1)

    def test_expired_marker_does_not_block(self):
        self.mark('alpha', ttl=60)
        self.clock.value += 61
        report, password = self.accept('alpha', all_screens=True)
        self.assertEqual(report['status'], 'PASS', report)
        password.assert_called_once()

    def test_marker_of_another_prototype_does_not_block_single(self):
        self.mark('beta')
        report, _ = self.accept('alpha', all_screens=True)
        self.assertEqual(report['status'], 'PASS', report)
        self.assertEqual(self.accepted('beta'), 0)

    def test_single_v1_and_empty_prototypes_do_not_refuse_before_the_password(self):
        ps.write_writing(str(self.proto_dir('old')), self.clock.ms, 900, BY)
        report, password = self.accept('old', all_screens=True)
        self.assertEqual(report.get('error'), 'needs_migration', report)
        password.assert_called_once()      # v1: the usual needs_migration after the prompt, not writing_in_progress
        self.mark('empty')
        report, password = self.accept('empty', all_screens=True)
        self.assertNotEqual(report.get('error'), 'writing_in_progress', report)
        password.assert_called_once()      # no screens: nothing to refuse

    def test_project_list_after_a_refusal_under_the_lock_skips_empty_and_v1(self):
        root, manifest = self.project

        def type_password(_prompt):
            self.mark('beta')
            self.mark('empty')
            ps.write_writing(str(self.proto_dir('old')), self.clock.ms, 900, BY)
            return SECRET_PW
        with mock.patch.object(pt, '_stdin_is_tty', return_value=True), \
                mock.patch.object(pt.getpass, 'getpass', side_effect=type_password):
            report = pt.accept_prototype(root, manifest, project=True, by='celina@example.com')
        self.assertEqual(report['error'], 'writing_in_progress')
        self.assertEqual([item['slug'] for item in report['writing']], ['beta'])

    def test_refusal_of_a_screen_scope_too(self):
        self.mark('alpha')
        report, password = self.accept('alpha', screens=['s2'])
        self.assertEqual(report['error'], 'writing_in_progress')
        password.assert_not_called()

    def test_marker_appearing_after_the_prompt_is_refused_under_the_lock(self):
        root, manifest = self.project

        def type_password(_prompt):
            self.mark('alpha')      # agent zaczyna pisać, gdy człowiek wpisuje hasło
            return SECRET_PW
        with mock.patch.object(pt, '_stdin_is_tty', return_value=True), \
                mock.patch.object(pt.getpass, 'getpass', side_effect=type_password) as password:
            report = pt.accept_prototype(root, manifest, 'alpha', all_screens=True, by='celina@example.com')
        self.no_by(report)
        password.assert_called_once()
        self.assertEqual((report['status'], report['error'], report['slug']), ('BLOCKED', 'writing_in_progress', 'alpha'))
        self.assertEqual(self.accepted('alpha'), 0)

    def test_project_preflight_lists_every_marked_prototype_and_writes_nothing(self):
        self.mark('alpha')
        self.mark('beta', ttl=1200)
        before = {slug: self.journal(slug) for slug in ('alpha', 'beta', 'empty')}
        report, password = self.accept(project=True)
        self.assertEqual((report['status'], report['error']), ('BLOCKED', 'writing_in_progress'))
        self.assertEqual(report['writing'], [
            {'slug': 'alpha', 'since': self.clock.ms, 'expiresAt': self.clock.ms + 900_000},
            {'slug': 'beta', 'since': self.clock.ms, 'expiresAt': self.clock.ms + 1_200_000}])
        self.assertEqual(report['accepted'], [])
        password.assert_not_called()
        self.assertEqual({slug: self.journal(slug) for slug in before}, before, 'no journal was written')

    def test_project_preflight_skips_prototypes_without_screens_and_v1(self):
        self.mark('empty')
        ps.write_writing(str(self.proto_dir('old')), self.clock.ms, 900, BY)
        report, password = self.accept(project=True)
        self.assertEqual(report['status'], 'PASS', report)
        password.assert_called_once()
        self.assertEqual(sorted(item['slug'] for item in report['accepted']), ['alpha', 'beta'])
        self.assertEqual(self.accepted('alpha'), 1)

    def test_project_marker_after_the_prompt_leaves_the_earlier_ones_and_lists_the_pending(self):
        root, manifest = self.project

        def type_password(_prompt):
            self.mark('beta')
            return SECRET_PW
        with mock.patch.object(pt, '_stdin_is_tty', return_value=True), \
                mock.patch.object(pt.getpass, 'getpass', side_effect=type_password):
            report = pt.accept_prototype(root, manifest, project=True, by='celina@example.com')
        self.no_by(report)
        self.assertEqual((report['status'], report['error']), ('BLOCKED', 'writing_in_progress'))
        self.assertEqual([item['slug'] for item in report['accepted']], ['alpha'])
        self.assertEqual(report['pending'], ['beta', 'empty', 'old'])
        self.assertEqual(report['writing'], [{'slug': 'beta', 'since': self.clock.ms, 'expiresAt': self.clock.ms + 900_000}])
        self.assertNotIn('slug', report)
        self.assertEqual((self.accepted('alpha'), self.accepted('beta')), (1, 0))

    def test_all_scope_is_refused_like_a_screen(self):
        self.mark('beta')
        report, _ = self.accept('beta', all_screens=True)
        self.assertEqual(report['error'], 'writing_in_progress')


class GateWritingTests(WritingGateCase):
    def gate(self, *slugs, plan=None):
        root, manifest = self.project
        report = pt.prototype_gate(root, manifest, slugs=list(slugs) or None, plan=plan)
        self.no_by(report)
        return report

    def accept_all(self, slug):
        self.accept_ops(slug, 's2', 's3')

    def test_valid_marker_fails_even_when_every_screen_is_accepted(self):
        self.accept_all('alpha')
        self.assertEqual(self.gate('alpha')['status'], 'PASS', 'control: no marker, all accepted')
        self.mark('alpha')
        report = self.gate('alpha')
        self.assertEqual(report['status'], 'FAIL')
        self.assertEqual(report['missing'], [{'slug': 'alpha', 'path': 'docs/prototypes/alpha', 'screen': None,
                                              'reason': 'writing'}])
        self.assertEqual([item['screen'] for item in report['acceptances']], ['s2', 's3'], 'acceptances stay listed')

    def test_marker_adds_one_entry_per_prototype_next_to_the_screen_reasons(self):
        self.mark('beta')
        report = self.gate('alpha', 'beta')
        self.assertEqual([(item['slug'], item['screen'], item['reason']) for item in report['missing']], [
            ('alpha', 's2', 'brak'), ('alpha', 's3', 'brak'),
            ('beta', 's2', 'brak'), ('beta', 's3', 'brak'), ('beta', None, 'writing')])
        for item in report['missing']:
            self.assertEqual(set(item), {'slug', 'path', 'screen', 'reason'}, 'the shape read by quality.py')

    def test_expired_or_invalid_marker_is_ignored(self):
        self.accept_all('alpha')
        self.mark('alpha', ttl=60)
        self.clock.value += 61
        self.assertEqual(self.gate('alpha')['status'], 'PASS')
        path = self.mark('alpha')
        path.write_text('{"version": 1, "since": 1, "expiresAt": "x"}', encoding='utf-8')
        self.assertEqual(self.gate('alpha')['status'], 'PASS', 'a broken marker is fail-open')

    def test_done_removes_the_reason(self):
        self.accept_all('alpha')
        self.mark('alpha')
        self.assertEqual(self.gate('alpha')['status'], 'FAIL')
        root, manifest = self.project
        self.assertEqual(pt.writing_prototype(root, manifest, 'alpha', 'done')['status'], 'PASS')
        self.assertEqual(self.gate('alpha')['status'], 'PASS')

    def test_bypass_line_still_passes_and_adds_no_marker_reason_of_its_own(self):
        self.mark('alpha')
        plan = self.root / 'docs' / 'plans' / 'plan.md'
        plan.parent.mkdir(parents=True, exist_ok=True)
        header = 'Prototyp: docs/prototypes/alpha\n'
        plan.write_text('# Plan\n\n' + header + '\n## Etap 1\nTreść.\n', encoding='utf-8')
        self.assertEqual(self.gate(plan='docs/plans/plan.md')['status'], 'FAIL', 'control: no bypass')
        plan.write_text('# Plan\n\n' + header + 'Bramka prototypu: pominięta — powód testu — Ktoś, 2026-11-12\n\n## Etap 1\nTreść.\n',
                        encoding='utf-8')
        report = self.gate(plan='docs/plans/plan.md')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['bypassed']['reason'], 'powód testu')
        self.assertNotIn('writing', report['bypassed'])

    def test_v1_prototype_keeps_needs_migration_only(self):
        ps.write_writing(str(self.proto_dir('old')), self.clock.ms, 900, BY)
        report = self.gate('old')
        self.assertEqual([item['reason'] for item in report['missing']], ['needs_migration'])

    def test_cli_exit_code_and_reason(self):
        self.accept_all('alpha')
        self.proto('gate', 'alpha')    # kontrola: exit 0
        ps.write_writing(str(self.proto_dir('alpha')), int(time.time() * 1000), 900, BY)
        report = self.proto('gate', 'alpha', code=1)
        self.assertEqual([item['reason'] for item in report['missing']], ['writing'])
        self.no_by(report)


class WaitWritingTests(WritingGateCase):
    def wait(self, timeout=30.0, on_sleep=None):
        root, manifest = self.project
        calls = []

        def sleep(seconds):
            calls.append(seconds)
            self.assertLess(len(calls), 6, 'wait does not end')
            self.clock.value += seconds
            if on_sleep is not None:
                on_sleep(len(calls))
        with mock.patch.object(pt.time, 'sleep', sleep):
            report = pt.wait_prototype(root, manifest, 'alpha', timeout=timeout, interval=1.0)
        self.no_by(report)
        return report, calls

    def test_accepted_is_not_returned_while_the_marker_is_valid(self):
        self.accept_ops('alpha', 's2', 's3')
        report, _ = self.wait(timeout=30.0)          # kontrola: bez markera od razu `accepted`
        self.assertEqual((report['status'], report['event']), ('PASS', 'accepted'))
        self.mark('alpha')
        report, calls = self.wait(timeout=0.0)
        self.assertEqual(report['status'], 'TIMEOUT', report)
        self.assertEqual([item['reason'] for item in report['missing']], ['writing'])
        self.assertEqual(calls, [], 'the deadline was already reached')

    def test_timeout_reason_mentions_the_writing_marker_only_for_it(self):
        self.mark('alpha')
        report, _ = self.wait(timeout=0.0)
        self.assertIn('still writing screens', report['reason'])
        self.unmark('alpha')
        report, _ = self.wait(timeout=0.0)      # kontrola: bez markera, brak akceptacji
        self.assertEqual(report['status'], 'TIMEOUT')
        self.assertNotIn('writing', report['reason'])

    def test_returns_accepted_after_done(self):
        self.accept_ops('alpha', 's2', 's3')
        self.mark('alpha')
        report, calls = self.wait(on_sleep=lambda count: self.unmark('alpha'))
        self.assertEqual((report['status'], report['event']), ('PASS', 'accepted'))
        self.assertEqual(len(calls), 1, 'one poll with the marker, then the normal result')

    def test_returns_accepted_after_the_marker_expires(self):
        self.accept_ops('alpha', 's2', 's3')
        self.mark('alpha', ttl=60)
        report, calls = self.wait(on_sleep=lambda count: setattr(self.clock, 'value', NOW + 61))
        self.assertEqual((report['status'], report['event']), ('PASS', 'accepted'))
        self.assertEqual(len(calls), 1)


class ListWritingTests(WritingGateCase):
    def listing(self):
        root, manifest = self.project
        report = pt.list_prototypes(root, manifest, clock=self.clock)
        self.no_by(report)
        return {item['slug']: item for item in report['prototypes']}

    def test_writing_field_per_prototype(self):
        before = self.listing()
        self.assertTrue(all(item['writing'] is None for item in before.values()))
        self.mark('beta', ttl=1200)
        after = self.listing()
        self.assertEqual(after['beta']['writing'], {'since': self.clock.ms, 'expiresAt': self.clock.ms + 1_200_000})
        self.assertIsNone(after['alpha']['writing'])
        for slug, item in before.items():
            self.assertEqual({key: value for key, value in after[slug].items() if key != 'writing'},
                             {key: value for key, value in item.items() if key != 'writing'}, 'old fields unchanged')

    def test_expired_marker_is_null(self):
        self.mark('beta', ttl=60)
        self.clock.value += 60
        self.assertIsNone(self.listing()['beta']['writing'])

    def test_cli_list_has_the_field_without_by(self):
        ps.write_writing(str(self.proto_dir('alpha')), int(time.time() * 1000), 900, BY)
        report = self.proto('list')
        self.no_by(report)
        entries = {item['slug']: item for item in report['prototypes']}
        self.assertEqual(set(entries['alpha']['writing']), {'since', 'expiresAt'})
        self.assertIsNone(entries['beta']['writing'])

    def test_static_index_has_no_mark_but_the_live_gallery_has(self):
        real = Clock(time.time())
        ps.write_writing(str(self.proto_dir('alpha')), real.ms, 900, BY)   # marker zegarem rzeczywistym, jak `index`
        live = pt.gallery_html(self.protos, self.root, clock=real)
        self.assertIn('data-writing', live, 'control: the live gallery marks the prototype')
        self.assertNotIn(BY, live)
        self.proto('index')
        html = (self.protos / 'index.html').read_text(encoding='utf-8')
        self.assertNotIn('data-writing', html)


class IterateWritingTests(PrototypeFixture):
    def setUp(self):
        super().setUp()
        self.init('demo')

    def test_iterate_warns_but_runs(self):
        plain = self.proto('iterate', 'demo')
        self.assertFalse([item for item in plain.get('warnings', []) if 'writing marker' in item], 'control: no warning')
        ps.write_writing(str(self.proto_dir('demo')), int(time.time() * 1000), 900, BY)
        report = self.proto('iterate', 'demo')
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(report['iteration'], plain['iteration'] + 1)
        self.assertTrue([item for item in report['warnings'] if 'writing marker' in item], report)
        self.assertNotIn(BY, json.dumps(report))


class AcceptRaceTests(WritingGateCase):
    """K12 część CLI: `accept` i `writing start` pod tą samą blokadą; blokada trzymana w wątku testu.

    Ograniczenie: `FileLock` nie gwarantuje kolejności wybudzeń czekających, więc test nie kolejkuje dwóch czekających na
    raz; każdy porządek buduje się z jednym czekającym (drugi wątek rusza po zwolnieniu blokady).  Wątek sygnalizuje
    zdarzeniem, że doszedł do miejsca tuż przed blokadą (po preflight i haśle / po kontekście); samo oczekiwanie na
    blokadę to krótki `join`, bo wynik nie zależy od tego, czy wątek już czeka.  Odmowa w `build` pod blokadą ma
    osobne testy (`AcceptWritingTests`, marker zapisany po hasle) oraz trzeci test tej klasy."""

    def setUp(self):
        super().setUp()
        self.reached = threading.Event()
        store = ps.CommentsStore(str(self.proto_dir('alpha')), 'alpha', clock=self.clock, lock_timeout=5.0)

        def context(root, manifest, slug):
            self.reached.set()
            return Path(self.root), {'warnings': []}, self.proto_dir('alpha'), store
        patcher = mock.patch.object(pt, '_prototype_context', context)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(pt, '_stdin_is_tty', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        def type_password(_prompt):
            self.reached.set()
            return SECRET_PW
        patcher = mock.patch.object(pt.getpass, 'getpass', side_effect=type_password)
        patcher.start()
        self.addCleanup(patcher.stop)
        # `_store` z lock_timeout 5 s dla wątku accept
        patcher = mock.patch.object(pt, '_store', lambda directory, slug: ps.CommentsStore(
            str(directory), slug, clock=self.clock, lock_timeout=5.0))
        patcher.start()
        self.addCleanup(patcher.stop)

    def hold(self):
        return ps.CommentsStore(str(self.proto_dir('alpha')), 'alpha', clock=self.clock).lock()

    def run_accept(self, results):
        root, manifest = self.project
        thread = threading.Thread(target=lambda: results.append(pt.accept_prototype(
            root, manifest, 'alpha', all_screens=True, by='celina@example.com')))
        thread.start()
        return thread

    def run_start(self, results):
        root, manifest = self.project
        thread = threading.Thread(target=lambda: results.append(pt.writing_prototype(root, manifest, 'alpha', 'start')))
        thread.start()
        return thread

    def exists(self):
        return (self.proto_dir('alpha') / ps.WRITING_MARKER).exists()

    def test_start_first_then_accept_is_refused(self):
        starts, accepts = [], []
        with self.hold():
            thread = self.run_start(starts)
            self.assertTrue(self.reached.wait(10), 'start did not reach the lock')
            thread.join(0.2)
            self.assertTrue(thread.is_alive(), 'start waits for the lock')
            self.assertFalse(self.exists())
        thread.join(10)
        self.assertTrue(starts, 'the start thread returned nothing')
        self.assertEqual(starts[0]['status'], 'PASS')
        self.run_accept(accepts).join(10)
        self.assertTrue(accepts, 'the accept thread returned nothing')
        self.assertEqual((accepts[0]['status'], accepts[0]['error']), ('BLOCKED', 'writing_in_progress'))
        self.assertEqual(self.accepted('alpha'), 0)

    def test_accept_first_then_it_is_kept_and_the_next_is_refused(self):
        starts, accepts, later = [], [], []
        with self.hold():
            thread = self.run_accept(accepts)
            self.assertTrue(self.reached.wait(10), 'accept did not reach the lock')
            thread.join(0.2)
            self.assertTrue(thread.is_alive(), 'accept waits for the lock')
        thread.join(10)
        self.assertTrue(accepts, 'the accept thread returned nothing')
        self.assertEqual(accepts[0]['status'], 'PASS', accepts[0])
        self.run_start(starts).join(10)
        self.assertTrue(starts)
        self.assertEqual(starts[0]['status'], 'PASS')
        self.assertEqual(self.accepted('alpha'), 1, 'the acceptance from before the marker stays')
        self.run_accept(later).join(10)
        self.assertEqual((later[0]['status'], later[0]['error']), ('BLOCKED', 'writing_in_progress'))
        self.assertEqual(self.accepted('alpha'), 1)

    def test_marker_written_under_the_lock_refuses_the_waiting_accept(self):
        accepts = []
        with self.hold():
            thread = self.run_accept(accepts)
            self.assertTrue(self.reached.wait(10), 'accept did not reach the lock')
            thread.join(0.2)
            self.assertTrue(thread.is_alive())
            ps.write_writing(str(self.proto_dir('alpha')), self.clock.ms, 900, BY)
        thread.join(10)
        self.assertTrue(accepts)
        self.assertEqual((accepts[0]['status'], accepts[0]['error']), ('BLOCKED', 'writing_in_progress'))
        self.no_by(accepts[0])
        self.assertEqual(self.accepted('alpha'), 0)


if __name__ == '__main__':
    unittest.main()
