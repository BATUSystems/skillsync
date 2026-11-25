"""N16: the `agents` part of the status page: `build_agents` (limits, noise filter, ETA fields, orphaned events), the single path of `show` and
`GET /api/status` (`dashboard_tests.add_to_status`), the watcher and `signature()`. The journal fixtures are written by hand (no code of the
`agent-run` command)."""
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from layers_helpers import guard_real_state  # noqa: E402
from tmp_paths import child  # noqa: E402

from skillsync_workflow import dashboard as engine  # noqa: E402
from skillsync_workflow import dashboard_agents as agents  # noqa: E402
from skillsync_workflow import dashboard_http as srv  # noqa: E402
from skillsync_workflow import dashboard_tests as dt  # noqa: E402

FIXTURE = ROOT / 'tests' / 'fixtures' / 'dashboard' / 'spec-act' / 'input'
RUN_ID = '2026-01-11-demo-onboarding'


def line(ev, agent, t, **fields):
    return json.dumps({'v': 1, 't': t, 'ev': ev, 'agent': agent, **fields}, ensure_ascii=False)


def start(agent, t, role='reviewer', kind='review', profile='spec', step=None, round=1, eta=None):
    return line('start', agent, t, role=role, kind=kind, profile=profile if kind == 'review' else None, step=step, round=round, eta=eta)


class Journal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dash-agents-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, *lines, raw=None):
        (self.dir / 'agents.jsonl').write_bytes(raw if raw is not None else ('\n'.join(lines) + '\n').encode('utf-8'))

    def build(self, files=()):
        return agents.build_agents(self.dir, 'R1', '2026-11-23T13:00:00+01:00', files)


class BuildAgents(Journal):
    def test_no_journal_gives_no_field(self):
        self.assertIsNone(self.build())

    def test_the_documented_shape_with_a_running_and_a_finished_round(self):
        self.write(start('a-2', '2026-11-23T13:10:00+01:00', eta='13:40'),
                   line('end', 'a-2', '2026-11-23T13:41:20+01:00', result='CHANGES_REQUIRED', report='spec-review-1.md'),
                   start('a-3', '2026-11-23T14:05:12+01:00', round=2, eta='14:35'),
                   line('eta', 'a-3', '2026-11-23T14:30:00+01:00', eta='15:05'))
        got = self.build(files=['spec-review-1.md'])
        self.assertEqual(got['running'], [{'agent': 'a-3', 'role': 'reviewer', 'kind': 'review', 'profile': 'spec', 'step': None, 'round': 2,
                                          'startedAt': '2026-11-23T14:05:12+01:00', 'eta': '15:05', 'etaFirst': '14:35',
                                          'etaIso': '2026-11-23T15:05:00+01:00', 'etaShifts': 1}])
        self.assertEqual(got['recent'], [{'agent': 'a-2', 'role': 'reviewer', 'kind': 'review', 'profile': 'spec', 'step': None, 'round': 1,
                                         'startedAt': '2026-11-23T13:10:00+01:00', 'endedAt': '2026-11-23T13:41:20+01:00', 'seconds': 1880,
                                         'etaIso': '2026-11-23T13:40:00+01:00', 'result': 'CHANGES_REQUIRED', 'report': 'spec-review-1.md'}])
        self.assertEqual(got['omitted'], {'running': 0, 'recent': 0})

    def test_report_is_a_link_only_for_a_file_of_the_run(self):
        self.write(start('a-1', '2026-11-23T13:00:00+01:00'), line('end', 'a-1', '2026-11-23T13:20:00+01:00', result='PASS', report='x.log'))
        self.assertIsNone(self.build(files=['spec-review-1.md'])['recent'][0]['report'])
        self.assertEqual(self.build(files=['x.log'])['recent'][0]['report'], 'x.log')

    def test_eta_without_a_first_deadline_counts_as_a_shift(self):
        self.write(start('a-1', '2026-11-23T13:00:00+01:00'), line('eta', 'a-1', '2026-11-23T13:20:00+01:00', eta='13:50'))
        run = self.build()['running'][0]
        self.assertEqual((run['etaFirst'], run['etaShifts'], run['eta']), (None, 1, '13:50'))

    def test_a_deadline_across_midnight_is_the_next_day(self):
        self.write(start('a-1', '2026-11-23T23:50:00+01:00', eta='00:20'))
        self.assertEqual(self.build()['running'][0]['etaIso'], '2026-11-24T00:20:00+01:00')

    def test_an_overrun_deadline_of_the_same_day_stays_in_the_past(self):
        self.write(start('a-1', '2026-11-23T14:00:00+01:00', eta='13:00'))   # an hour BEFORE the event: not tomorrow
        self.assertEqual(self.build()['running'][0]['etaIso'], '2026-11-23T13:00:00+01:00')

    def test_a_naive_time_is_read_in_the_zone_of_the_run_start(self):
        self.write(start('a-1', '2026-11-23T14:00:00', eta='14:30'))
        self.assertEqual(self.build()['running'][0]['startedAt'], '2026-11-23T14:00:00+01:00')

    def test_orphaned_end_and_eta_are_ignored_and_corrupted_lines_skipped(self):
        self.write(line('end', 'a-9', '2026-11-23T13:00:00+01:00', result='PASS'),
                   line('eta', 'a-9', '2026-11-23T13:00:00+01:00', eta='13:30'),
                   '{not json', '[]', '', start('a-1', '2026-11-23T13:01:00+01:00'),
                   line('start', 'a-2', '2026-11-23T13:02:00+01:00', role='hacker', kind='review'),   # a role outside the closed list
                   line('end', 'a-1', '2026-11-23T13:30:00+01:00', result='MAYBE'),                   # a result outside the closed list
                   line('eta', 'a-1', '2026-11-23T13:03:00+01:00', eta='25:99'))
        got = self.build()
        self.assertEqual([r['agent'] for r in got['running']], ['a-1'])
        self.assertEqual(got['running'][0]['etaShifts'], 0)
        self.assertEqual(got['recent'], [])

    def test_free_text_and_wrong_types_are_refused(self):
        self.write(line('start', 'a-1', '2026-11-23T13:00:00+01:00', role='reviewer', kind='review', profile='spec', step='x y', round=1),
                   line('start', 'a-2', '2026-11-23T13:00:00+01:00', role='reviewer', kind='review', profile='spec', step=None, round=100),
                   line('start', 'a-3', '2026-11-23T13:00:00+01:00', role='reviewer', kind='review', profile='spec', step=None, round=True),
                   line('start', 'bad', '2026-11-23T13:00:00+01:00', role='reviewer', kind='review'),
                   '{"v":2,"ev":"start","agent":"a-4","t":"2026-11-23T13:00:00+01:00","role":"reviewer","kind":"review"}')
        self.assertEqual(self.build()['running'], [])

    def test_running_is_limited_to_six_and_the_oldest_are_omitted(self):
        self.write(*[start(f'a-{n}', f'2026-11-23T13:0{n}:00+01:00', role='implementer', kind='run', profile=None, round=None) for n in range(1, 9)])
        got = self.build()
        self.assertEqual([r['agent'] for r in got['running']], [f'a-{n}' for n in range(3, 9)])
        self.assertEqual(got['omitted']['running'], 2)

    def test_recent_filters_noise_and_is_limited_to_eight_newest_first(self):
        rows = [start('a-1', '2026-11-23T10:00:00+01:00', role='implementer', kind='run', profile=None, round=None),
                line('end', 'a-1', '2026-11-23T10:30:00+01:00', result='DONE'),   # a long run without an ETA: noise
                start('a-2', '2026-11-23T10:00:00+01:00', role='test-runner', kind='run', profile=None, round=None, eta='10:30'),
                line('end', 'a-2', '2026-11-23T10:01:00+01:00', result='PASS'),   # shorter than 2 min: noise
                start('a-3', '2026-11-23T10:00:00+01:00', round=1),
                line('end', 'a-3', '2026-11-23T10:00:05+01:00', result='BLOCKED')]   # a round of review is always shown, however short
        for n in range(4, 15):
            rows += [start(f'a-{n}', f'2026-11-23T11:{n:02d}:00+01:00', role='implementer', kind='run', profile=None, round=None, eta='12:30'),
                     line('end', f'a-{n}', f'2026-11-23T11:{n:02d}:00+01:00'.replace('T11:', 'T12:'), result='DONE')]
        self.write(*rows)
        got = self.build()
        ids = [r['agent'] for r in got['recent']]
        self.assertEqual(len(ids), 8)
        self.assertEqual(ids[0], 'a-14')
        self.assertNotIn('a-1', ids)
        self.assertNotIn('a-2', ids)
        self.assertEqual(got['omitted']['recent'], 12 - 8 - 0)   # 11 long runs + the round, minus the 8 shown; filtered noise is NOT counted

    def test_a_restarted_agent_id_does_not_reopen_a_finished_run(self):
        self.write(start('a-1', '2026-11-23T13:00:00+01:00'), line('end', 'a-1', '2026-11-23T13:30:00+01:00', result='PASS'),
                   line('eta', 'a-1', '2026-11-23T13:40:00+01:00', eta='14:00'))
        got = self.build()
        self.assertEqual((got['running'], len(got['recent'])), ([], 1))

    def test_two_parallel_runs_of_one_step_and_a_dispatch_run_id(self):
        self.write(start('a-1', '2026-11-23T13:00:00+01:00', step='F2.1', profile='code'), start('a-2', '2026-11-23T13:00:01+01:00', step='F2.1', profile='code'))
        got = agents.build_agents(self.dir, 'run.pack-1', None, ())
        self.assertEqual([r['agent'] for r in got['running']], ['a-1', 'a-2'])

    def test_the_tail_only_is_read_and_a_cut_first_line_is_dropped(self):
        old = [start(f'a-{n}', '2026-11-23T10:00:00+01:00', role='implementer', kind='run', profile=None, round=None) for n in range(1, 701)]
        self.write(*old)
        got = self.build()
        self.assertEqual(len(got['running']) + got['omitted']['running'], agents.MAX_LINES)   # the last 500 lines only (their orphans' `start`s are in the tail)
        self.assertEqual(got['running'][-1]['agent'], 'a-700')

    def test_the_byte_limit_reads_one_open_and_a_partial_first_line_is_skipped(self):
        pad = line('start', 'a-1', '2026-11-23T13:00:00+01:00', role='implementer', kind='run', step='x' * 20, round=None, eta=None)
        body = (pad + '\n') * 30 + start('a-99', '2026-11-23T14:00:00+01:00') + '\n'
        with mock.patch.object(agents, 'MAX_BYTES', 700):
            self.write(raw=body.encode('utf-8'))
            with mock.patch('builtins.open', wraps=open) as opened:
                got = self.build()
        self.assertEqual(sum(1 for call in opened.call_args_list if str(call.args[0]).endswith('agents.jsonl')), 1)
        self.assertEqual(got['running'][-1]['agent'], 'a-99')

    def test_extreme_dates_and_a_boolean_version_never_break_the_fold(self):
        self.write(line('start', 'a-1', '0001-01-01T00:00:00', role='reviewer', kind='review', profile='spec', step=None, round=1, eta=None),
                   line('start', 'a-2', '9999-12-31T23:59:00+00:00', role='reviewer', kind='review', profile='spec', step=None, round=1, eta='00:05'),
                   json.dumps({'v': True, 't': '2026-11-23T13:00:00+01:00', 'ev': 'start', 'agent': 'a-3', 'role': 'reviewer', 'kind': 'review'}))
        got = self.build()
        self.assertNotIn('a-3', [r['agent'] for r in got['running']], 'True is not the version 1')
        self.assertIsInstance(agents.metrics_rows(self.dir, '2026-11-23T13:00:00+01:00'), list)

    def test_a_symlinked_journal_is_not_read(self):
        target = self.dir / 'other.jsonl'
        target.write_text(start('a-1', '2026-11-23T13:00:00+01:00') + '\n', encoding='utf-8')
        (self.dir / 'agents.jsonl').symlink_to(target)
        self.assertIsNone(self.build())

    def test_the_journal_has_no_session_text(self):
        self.write(start('a-1', '2026-11-23T13:00:00+01:00'), line('end', 'a-1', '2026-11-23T13:20:00+01:00', result='PASS', report='r.md'))
        text = json.dumps(self.build(files=['r.md']))
        for key in ('prompt', 'message', 'transcript', 'summary'):
            self.assertNotIn(key, text)


class Project(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dash-agents-proj-')
        guard_real_state(self)
        self.addCleanup(self.tmp.cleanup)
        self.root = child(Path(self.tmp.name), 'demo')
        shutil.copytree(FIXTURE, self.root)
        self.run_dir = next(self.root.glob('.skillsync/runs/*'))

    def status(self):
        return engine.build_status(self.root, prototype_port=1, acceptance=lambda slug: None, writing=lambda slug: None, ahead=lambda *a: {})

    def journal(self, *lines):
        (self.run_dir / 'agents.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')


class FrozenClock(datetime):
    """`generatedAt` has a one-second resolution: two status reads on a second boundary differed (Python 3.12 on Windows, 3.11 earlier)."""
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 23, 13, 5, 0, tzinfo=timezone.utc).astimezone(tz)


class StatusField(Project):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(engine, 'datetime', FrozenClock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_run_without_a_journal_has_no_agents_and_the_status_is_unchanged(self):
        with_hook = self.status()
        with mock.patch.object(dt, '_add_agents', lambda *args: None):
            without = self.status()
        self.assertNotIn('agents', with_hook)
        self.assertEqual(json.dumps(with_hook, sort_keys=True), json.dumps(without, sort_keys=True))

    def test_the_journal_adds_agents_and_leaves_silence_alone(self):
        self.check_the_journal_adds_agents()

    def check_the_journal_adds_agents(self):
        before = self.status()
        self.journal(start('a-1', '2026-11-23T13:00:00+01:00', eta='13:30'))
        got = self.status()
        self.assertEqual([r['agent'] for r in got['agents']['running']], ['a-1'])
        self.assertEqual(got['silence'], before['silence'])   # the server does not touch `silence`: the renderer silences it
        without = {k: v for k, v in got.items() if k != 'agents'}
        self.assertEqual(json.dumps(without, sort_keys=True), json.dumps(before, sort_keys=True))

    def test_a_broken_builder_never_takes_the_status_down(self):
        self.journal(start('a-1', '2026-11-23T13:00:00+01:00'))
        with mock.patch.object(agents, 'build_agents', side_effect=RuntimeError('boom')):
            self.assertNotIn('agents', self.status())

    def test_the_report_link_follows_the_files_of_the_run(self):
        (self.run_dir / 'spec-review-1.md').write_text('Wynik: PASS\n', encoding='utf-8')
        self.journal(start('a-1', '2026-11-23T13:00:00+01:00'), line('end', 'a-1', '2026-11-23T13:20:00+01:00', result='PASS', report='spec-review-1.md'),
                     start('a-2', '2026-11-23T13:30:00+01:00'), line('end', 'a-2', '2026-11-23T13:40:00+01:00', result='PASS', report='nope.md'))
        recent = {r['agent']: r['report'] for r in self.status()['agents']['recent']}
        self.assertEqual(recent, {'a-1': 'spec-review-1.md', 'a-2': None})

    def test_a_symlinked_run_directory_gives_no_field(self):
        self.journal(start('a-1', '2026-11-23T13:00:00+01:00'))
        real = self.run_dir.with_name('real-dir')
        self.run_dir.rename(real)
        self.run_dir.symlink_to(real)
        self.assertNotIn('agents', self.status())

    def test_the_masking_covers_the_field(self):
        self.journal(start('a-1', '2026-11-23T13:00:00+01:00'))
        self.assertIn('agents', dt.finalize_status(self.status()))


class Watcher(Project):
    def test_the_journal_is_watched_and_a_new_line_changes_the_signature(self):
        board = srv.Board(self.root, prototype_port=1)
        self.assertIn(self.run_dir.resolve() / 'agents.jsonl', board.watched_paths())   # the board resolves its root (Windows 8.3 temp names)
        before = board.signature()
        self.journal(start('a-1', '2026-11-23T13:00:00+01:00'))
        created = board.signature()
        self.assertNotEqual(before, created)
        with open(self.run_dir / 'agents.jsonl', 'a', encoding='utf-8') as handle:
            handle.write(line('end', 'a-1', '2026-11-23T13:20:00+01:00', result='PASS') + '\n')
        self.assertNotEqual(created, board.signature())


class MetricsAndContract(Project):
    def metrics(self):
        return engine.build_metrics(self.root, RUN_ID)

    def test_metrics_without_a_journal_have_no_agents_key(self):
        self.assertNotIn('agents', self.metrics())

    def test_metrics_give_the_raw_rows_of_every_run_with_the_first_deadline_and_every_move(self):
        self.journal(start('a-1', '2026-11-23T13:00:00+01:00', eta='13:30'), line('eta', 'a-1', '2026-11-23T13:29:00+01:00', eta='14:00'),
                     line('eta', 'a-1', '2026-11-23T13:58:00+01:00', eta='14:30'), line('end', 'a-1', '2026-11-23T14:20:00+01:00', result='CHANGES_REQUIRED', report='spec-review-1.md'),
                     start('a-2', '2026-11-23T14:21:00+01:00', role='implementer', kind='run', profile=None, step='F2.1', round=None),   # short and without a deadline: noise on the page, a row here
                     line('end', 'a-2', '2026-11-23T14:21:30+01:00', result='DONE'),
                     start('a-3', '2026-11-23T14:30:00+01:00', round=2))
        rows = self.metrics()['agents']
        self.assertEqual([r['agent'] for r in rows], ['a-1', 'a-2', 'a-3'])
        first = rows[0]
        self.assertEqual((first['etaFirst'], first['etaShifts'], first['seconds'], first['result']), ('13:30', 2, 4800, 'CHANGES_REQUIRED'))
        self.assertEqual([m['eta'] for m in first['etaMoves']], ['14:00', '14:30'])
        self.assertEqual(rows[1]['seconds'], 30)
        self.assertEqual((rows[2]['endedAt'], rows[2]['seconds'], rows[2]['result'], rows[2]['round']), (None, None, None, 2))
        self.assertEqual(set(first), {'agent', 'role', 'kind', 'profile', 'step', 'round', 'startedAt', 'etaFirst', 'etaShifts', 'etaMoves', 'endedAt', 'seconds', 'result'})

    def test_metrics_read_the_whole_journal_not_the_page_limits(self):
        self.journal(*[start(f'a-{n}', '2026-11-23T13:00:00+01:00', role='implementer', kind='run', profile=None, round=None) for n in range(1, 701)])
        self.assertEqual(len(self.metrics()['agents']), 700)
        self.assertLessEqual(len(self.status()['agents']['running']), 6)

    def test_show_and_the_server_status_carry_the_same_agents(self):
        self.journal(start('a-1', '2026-11-23T13:00:00+01:00', eta='13:30'), line('end', 'a-1', '2026-11-23T13:20:00+01:00', result='PASS', report='x.md'),
                     start('a-2', '2026-11-23T13:30:00+01:00'))
        shown = self.status()['agents']
        served = srv.Board(self.root, prototype_port=1).status()['agents']
        self.assertEqual(shown, served)

    def test_the_contract_describes_the_field_the_stale_rule_and_the_metrics_rows(self):
        doc = (ROOT / 'docs' / 'dashboard-contract.md').read_text(encoding='utf-8')
        for needle in ('### `agents`', '`agents.jsonl`', 'max(startedAt + 45 min, etaIso + 15 min)', 'Serwer nie czyta zegara i nie ma pola `stale`', '`agents[]`',
                       'zielon', 'ciszę gasi **renderer**', 'wygrywa bieg z dziennika'):
            self.assertIn(needle, doc)


if __name__ == '__main__':
    unittest.main()
