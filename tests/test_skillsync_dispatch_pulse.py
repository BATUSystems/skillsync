"""CLAUDE-5 (P2): `skillsync dispatch --pulse`: new whitelisted fields (`lastMessage`, `lastProbe`, `decisions`) and the computed findings of the pulse."""
import calendar
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillsync_workflow import dispatch_state  # noqa: E402
from tests.test_skillsync_workflow import WorkflowFixture  # noqa: E402
from tmp_paths import child  # noqa: E402

SENT = 'ZZSENTINELZZ'
RUN = '2026-11-21-pulse-run'
NOW = float(calendar.timegm((2026, 11, 21, 12, 0, 0)))   # the injected clock: 12:00:00Z
PLUS_ONE = timezone(timedelta(hours=1))


def ago(minutes, zone=PLUS_ONE):
    """An ISO time with an offset, `minutes` before NOW."""
    return (datetime.fromtimestamp(NOW - minutes * 60, timezone.utc).astimezone(zone)).isoformat()


def state(cards=(), leases=(), decisions=None, **over):
    data = {'version': 1, 'run_id': RUN, 'status': 'running', 'goal': f'{SENT} goal',
            'cards': [{'alias': 'Dyspozytor', 'state': 'dispatcher'}, *cards], 'packages': [], 'leases': list(leases)}
    if decisions is not None:
        data.update(decisions)
    data.update(over)
    return data


class Pulse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def put(self, data, name=f'{RUN}.yaml', age_hours=0.0, raw=None):
        path = child(child(child(self.root, '.skillsync'), 'dispatch'), name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw if raw is not None else yaml.safe_dump(data, sort_keys=False), encoding='utf-8')
        stamp = NOW - age_hours * 3600
        os.utime(path, (stamp, stamp))
        return path

    def report(self, manifest=None, pulse=True):
        return dispatch_state.dispatch_report(self.root, manifest or {}, None, now=NOW, pulse=pulse)

    def entry(self, **kw):
        report = self.report(**kw)
        self.assertEqual(len(report['dispatches']), 1, report)
        return report['dispatches'][0]

    def findings(self, **kw):
        return [(f['kind'], f['subject'], f['ageMinutes']) for f in self.entry(**kw)['pulse']['findings']]

    def card(self, alias='karta-1', state_='working', **extra):
        return {'alias': alias, 'state': state_, **extra}

    # --- the fields of the view ---------------------------------------------------------------------------------
    def test_the_new_fields_are_always_there_the_pulse_object_only_with_the_flag(self):
        self.put(state(cards=[self.card(last_message=ago(5), last_probe=ago(7))]))
        plain = self.entry(pulse=False)
        self.assertNotIn('pulse', plain)
        card = [c for c in plain['cards'] if c['alias'] == 'karta-1'][0]
        self.assertEqual(card['lastMessage'], ago(5))
        self.assertEqual(card['lastProbe'], ago(7))
        self.assertEqual(plain['decisions'], [])
        self.assertEqual(set(self.entry(pulse=True)['pulse']), {'now', 'intervalMinutes', 'thresholds', 'findings'})

    def test_a_time_without_a_zone_or_a_text_is_null(self):
        self.put(state(cards=[self.card(last_message='2026-11-21T10:00:00', last_probe=f'{SENT} soon')]))
        card = [c for c in self.entry()['cards'] if c['alias'] == 'karta-1'][0]
        self.assertIsNone(card['lastMessage'])
        self.assertIsNone(card['lastProbe'])
        self.assertNotIn(SENT, json.dumps(self.report()))

    def test_an_unquoted_yaml_timestamp_with_an_offset_is_read_and_one_without_is_not(self):
        raw = ('run_id: ' + RUN + '\nstatus: running\ncards:\n  - {alias: karta-1, state: working, last_message: 2026-11-21T10:00:00+01:00, '
               'last_probe: 2026-11-21T10:00:00}\n')
        self.put(None, raw=raw)
        card = [c for c in self.entry()['cards'] if c['alias'] == 'karta-1'][0]
        self.assertTrue(card['lastMessage'].startswith('2026-11-21T10:00:00'))
        self.assertIsNone(card['lastProbe'])

    def test_the_decisions_of_the_three_lists_are_read_with_their_default_state(self):
        data = state(decisions={'decisions': [{'id': 'D1', 'question': SENT, 'state': 'answered', 'asked': ago(90)}, {'id': 'D2', 'decision': SENT}],
                                'open_decisions': [{'id': 'D3', 'asked': ago(20)}], 'open_for_user': [{'question': SENT}]})
        self.put(data)
        decisions = {d['id']: d for d in self.entry()['decisions']}
        self.assertEqual(decisions['D1']['state'], 'answered')
        self.assertEqual(decisions['D2']['state'], 'unknown')
        self.assertEqual(decisions['D3']['state'], 'open')
        self.assertEqual(decisions['D3']['asked'], ago(20))
        self.assertEqual(sorted(d['state'] for d in self.entry()['decisions']), ['answered', 'open', 'open', 'unknown'])
        self.assertNotIn(SENT, json.dumps(self.report()))

    # --- the findings -------------------------------------------------------------------------------------------
    def test_a_working_card_silent_over_the_threshold_is_idle_and_one_below_is_not(self):
        self.put(state(cards=[self.card('karta-1', last_message=ago(45)), self.card('karta-2', last_message=ago(20))]))
        self.assertEqual(self.findings(), [('idle-card', 'karta-1', 45)])

    def test_the_age_is_counted_from_the_latest_of_the_message_and_the_probe(self):
        self.put(state(cards=[self.card(last_message=ago(120), last_probe=ago(10))]))
        self.assertEqual(self.findings(), [])
        self.put(state(cards=[self.card(last_message=ago(120), last_probe=ago(40))]))
        self.assertEqual(self.findings(), [('idle-card', 'karta-1', 40)])

    def test_a_busy_card_counts_like_a_working_one_and_other_states_never_idle(self):
        cards = [self.card('c-busy', 'busy', last_message=ago(60))]
        cards += [self.card(f'c-{name}', name, last_message=ago(600)) for name in ('free', 'released', 'unavailable', 'probed')]
        self.put(state(cards=cards))
        self.assertEqual(self.findings(), [('idle-card', 'c-busy', 60)])

    def test_a_card_without_any_time_is_age_unknown_never_idle(self):
        self.put(state(cards=[self.card('karta-1'), self.card('karta-2', 'waiting_user')]))
        self.assertEqual(sorted(self.findings()), [('age-unknown', 'karta-1', None), ('age-unknown', 'karta-2', None)])

    def test_a_card_waiting_for_the_user_is_user_wait_not_idle(self):
        self.put(state(cards=[self.card('karta-1', 'waiting_user', last_message=ago(20)), self.card('karta-2', 'waiting_user', last_message=ago(5))]))
        self.assertEqual(self.findings(), [('user-wait', 'karta-1', 20)])

    def test_an_open_decision_over_the_threshold_is_user_wait_an_answered_one_is_not(self):
        data = state(decisions={'open_decisions': [{'id': 'D1', 'asked': ago(30)}, {'id': 'D2', 'asked': ago(3)}, {'asked': ago(25)}],
                                'decisions': [{'id': 'D4', 'state': 'answered', 'asked': ago(500)}, {'id': 'D5', 'asked': ago(500)}]})
        self.put(data)
        # the number of an entry without an id counts the combined list: `decisions` (2 entries), then `open_decisions` (the third of them is the 5th)
        self.assertEqual(sorted(self.findings()), [('user-wait', 'D1', 30), ('user-wait', 'decision-5', 25)])

    def test_an_open_decision_without_a_time_is_not_a_finding(self):
        self.put(state(decisions={'open_decisions': [{'id': 'D1'}]}))
        self.assertEqual(self.findings(), [])

    def test_a_lease_not_released_over_the_limit_is_overdue(self):
        leases = [{'resource': 'e2e', 'granted_at': ago(300), 'released_at': None}, {'resource': 'smoke', 'granted_at': ago(300), 'released_at': ago(5)},
                  {'resource': 'db', 'granted_at': ago(100), 'released_at': None}]
        self.put(state(leases=leases))
        self.assertEqual(self.findings(), [('lease-overdue', 'e2e', 300)])

    def test_a_lease_without_a_usable_time_is_age_unknown(self):
        self.put(state(leases=[{'resource': 'e2e', 'granted_at': '2026-11-21T07:00:00', 'released_at': None}]))
        self.assertEqual(self.findings(), [('age-unknown', 'e2e', None)])

    def test_a_state_file_not_written_for_over_six_hours_is_stale(self):
        self.put(state(), age_hours=7)
        self.assertEqual(self.findings(), [('stale-state', RUN, 420)])
        self.put(state(), age_hours=5)
        self.assertEqual(self.findings(), [])

    def test_only_the_dispatches_that_run_have_findings(self):
        for status in ('planning', 'done', 'aborted'):
            with self.subTest(status=status):
                self.put(state(cards=[self.card(last_message=ago(900))], status=status), age_hours=9)
                self.assertEqual(self.findings(), [])
        self.put(state(cards=[self.card(last_message=ago(900))], status='draining'))
        self.assertEqual(self.findings(), [('idle-card', 'karta-1', 900)])

    def test_the_thresholds_come_from_the_manifest_and_an_invalid_one_falls_back_to_the_default(self):
        self.put(state(cards=[self.card(last_message=ago(15))]))
        self.assertEqual(self.findings(manifest={'dispatch': {'pulse': {'idle_card_minutes': 10}}}), [('idle-card', 'karta-1', 15)])
        self.assertEqual(self.findings(manifest={'dispatch': {'pulse': {'idle_card_minutes': 'abc'}}}), [])
        thresholds = self.entry(manifest={'dispatch': {'pulse': {'idle_card_minutes': 10, 'user_wait_notify_minutes': 0}}})['pulse']['thresholds']
        self.assertEqual(thresholds, {'idleCardMinutes': 10, 'userWaitNotifyMinutes': 15, 'leaseOverdueMinutes': 240})

    def test_a_value_equal_to_the_threshold_is_a_finding_and_one_minute_below_is_not(self):
        self.put(state(cards=[self.card('c-30', last_message=ago(30)), self.card('c-29', last_message=ago(29)),
                              self.card('w-15', 'waiting_user', last_message=ago(15)), self.card('w-14', 'waiting_user', last_message=ago(14))],
                       leases=[{'resource': 'l-240', 'granted_at': ago(240), 'released_at': None}, {'resource': 'l-239', 'granted_at': ago(239), 'released_at': None}],
                       decisions={'open_decisions': [{'id': 'D15', 'asked': ago(15)}, {'id': 'D14', 'asked': ago(14)}]}))
        self.assertEqual(sorted(self.findings()), [('idle-card', 'c-30', 30), ('lease-overdue', 'l-240', 240), ('user-wait', 'D15', 15), ('user-wait', 'w-15', 15)])

    def test_a_state_file_exactly_six_hours_old_is_not_yet_stale(self):
        self.put(state(), age_hours=6)
        self.assertEqual(self.findings(), [])

    def test_a_word_that_is_not_ours_is_an_unknown_state_and_never_a_finding(self):
        self.put(state(decisions={'open_decisions': [{'id': 'D1', 'state': 'closed', 'asked': ago(500)}, {'id': 'D2', 'state': None, 'asked': ago(500)}]}))
        states = {d['id']: d['state'] for d in self.entry()['decisions']}
        self.assertEqual(states, {'D1': 'unknown', 'D2': 'open'})
        self.assertEqual(self.findings(), [('user-wait', 'D2', 500)])

    def test_a_card_or_a_lease_without_a_valid_name_gets_a_numbered_subject(self):
        self.put(state(cards=[{'alias': f'{SENT} bad name', 'state': 'working'}, {'state': 'working'}],
                       leases=[{'granted_at': ago(300), 'released_at': None}]))
        self.assertEqual(sorted(self.findings()), [('age-unknown', 'card-2', None), ('age-unknown', 'card-3', None), ('lease-overdue', 'lease-1', 300)])

    def test_a_waiting_card_with_only_a_probe_time_is_age_unknown(self):
        self.put(state(cards=[self.card('karta-1', 'waiting_user', last_probe=ago(600))]))
        self.assertEqual(self.findings(), [('age-unknown', 'karta-1', None)])

    def test_a_boolean_threshold_falls_back_to_the_default(self):
        self.put(state(cards=[self.card(last_message=ago(15))]))
        thresholds = self.entry(manifest={'dispatch': {'pulse': {'idle_card_minutes': True}}})['pulse']['thresholds']
        self.assertEqual(thresholds['idleCardMinutes'], 30)

    def test_one_reading_of_the_clock_serves_the_whole_report(self):
        self.put(state(cards=[self.card(last_message=ago(45))]), age_hours=2)
        report = self.report()
        entry = report['dispatches'][0]
        self.assertEqual(report['generatedAt'], '2026-11-21T12:00:00Z')
        self.assertEqual(entry['pulse']['now'], report['generatedAt'])
        self.assertEqual(entry['freshness']['ageSeconds'], 2 * 3600)

    def test_the_base_interval_comes_from_the_manifest_with_the_default_and_the_bound(self):
        self.put(state())
        self.assertEqual(self.entry()['pulse']['intervalMinutes'], 20)
        self.assertEqual(self.entry(manifest={'dispatch': {'pulse': {'interval_minutes': 1}}})['pulse']['intervalMinutes'], 1)
        self.assertEqual(self.entry(manifest={'dispatch': {'pulse': {'interval_minutes': 60}}})['pulse']['intervalMinutes'], 60)
        for bad in (0, 61, True, 'abc', None):
            with self.subTest(bad=bad):
                self.assertEqual(self.entry(manifest={'dispatch': {'pulse': {'interval_minutes': bad}}})['pulse']['intervalMinutes'], 20)

    def test_the_clock_is_the_injected_one(self):
        self.put(state(cards=[self.card(last_message=ago(45))]))
        pulse = self.entry()['pulse']
        self.assertEqual(pulse['now'], '2026-11-21T12:00:00Z')

    def test_a_future_time_counts_as_zero_minutes_not_a_negative_one(self):
        self.put(state(cards=[self.card(last_message=ago(-30))]))
        self.assertEqual(self.findings(), [])

    def test_an_unreadable_dispatch_has_no_pulse_and_does_not_break_the_report(self):
        self.put(None, raw='{{ not yaml')
        self.put(state(cards=[self.card(last_message=ago(45))]), name='2026-11-21-ok.yaml')
        report = self.report()
        states = sorted(d['state'] for d in report['dispatches'])
        self.assertEqual(states, ['ok', 'unreadable'])
        self.assertTrue(all('pulse' not in d for d in report['dispatches'] if d['state'] == 'unreadable'))

    def test_no_free_text_leaves_in_the_pulse(self):
        self.put(state(cards=[self.card(last_message=ago(45), note=SENT, session_name=SENT)], goal=SENT,
                       decisions={'open_decisions': [{'id': 'D1', 'asked': ago(30), 'question': SENT, 'text': SENT}]}))
        self.assertNotIn(SENT, json.dumps(self.report()))


class PulseCli(WorkflowFixture):
    def test_the_flag_adds_the_pulse_object_and_without_it_there_is_none(self):
        path = self.root / '.skillsync' / 'dispatch' / f'{RUN}.yaml'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(state(cards=[{'alias': 'karta-1', 'state': 'working'}]), sort_keys=False), encoding='utf-8')
        with_flag = self.report('dispatch', '--pulse')
        entry = with_flag['dispatches'][0]
        self.assertEqual(set(entry['pulse']), {'now', 'intervalMinutes', 'thresholds', 'findings'})
        self.assertEqual([(f['kind'], f['subject']) for f in entry['pulse']['findings']], [('age-unknown', 'karta-1')])
        self.assertNotIn('pulse', self.report('dispatch')['dispatches'][0])


if __name__ == '__main__':
    unittest.main()
