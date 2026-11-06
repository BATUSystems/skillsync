"""Contract for `skillsync dispatch` (schema 1): read-only view of `.skillsync/dispatch/*.yaml` that never leaks free text."""
import json
import os
from pathlib import Path
import time
import unittest

import yaml

from tests.test_skillsync_workflow import WorkflowFixture

needs_fifo = unittest.skipUnless(hasattr(os, 'mkfifo'), 'mkfifo unavailable')
needs_symlinks = unittest.skipUnless(hasattr(os, 'symlink') and os.name != 'nt', 'symlinks unavailable')

SENT = 'ZZSENTINELZZ'
RUN = '2026-10-31-test-run'
HASH = '1a2b3c4d5e6f'


def state(**over):
    """Shape reproduced from real state files (skeletons only): fields outside the contract carry a sentinel."""
    data = {
        'version': 1, 'run_id': RUN, 'goal': f'{SENT} goal', 'repo': f'/home/user/{SENT}', 'base_branch': 'main', 'base_commit': HASH,
        'status': 'running', 'model_gate': {'required': SENT, 'confirmed': SENT}, 'user_delegation': f'{SENT} delegacja',
        'parallel_limit': 2, 'guards': {'load': 0.5, 'note': SENT},
        'cards': [{'alias': 'Dyspozytor', 'session_id': SENT, 'session_name': SENT, 'state': 'dispatcher'},
                  {'alias': 'karta-1', 'session_id': SENT, 'session_name': SENT, 'state': 'working', 'package': 'P1',
                   'branch': 'dispatch/p1', 'worktree': f'../wt/{SENT}', 'last_message': SENT}],
        'packages': [{'id': 'P1', 'card': 'karta-1', 'status': 'in_progress', 'branch': 'dispatch/p1', 'worktree': f'../wt/{SENT}',
                      'input_commit': HASH, 'result_commit': None, 'evidence': ['a.log', 'b.log'], 'note': SENT, 'debt': [SENT],
                      'claim': {'task_id': 'P1', 'run_id': f'{RUN}.P1', 'ttl': 14400}, 'future_field': SENT}],
        'leases': [{'resource': 'smoke', 'holder': 'P1', 'card': 'karta-1', 'granted_at': '2026-10-31T09:00:00Z',
                    'released_at': None, 'release': {'exit': 0, 'log': f'/x/{SENT}.log', 'verified': True}, 'queue': ['P2']}],
        'merge_queue': ['P1'], 'push_slot': None, 'master_head': HASH,
        'decisions': [{'id': 'D1', 'question': SENT}], 'open_for_user': [SENT], 'debt': [{'item': SENT}],
        'events': [{'at': 'x', 'msg': SENT}], 'cleanup_pending': [{'worktree': SENT, 'note': SENT}], 'watch': {'note': SENT},
        'followups': [{'what': SENT}], 'open_decisions': [{'question': SENT}], 'unknown_top': SENT,
    }
    data.update(over)
    return data


class DispatchState(WorkflowFixture):
    def put(self, name, data, raw=None):
        path = self.root / '.skillsync' / 'dispatch' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(raw if raw is not None else yaml.safe_dump(data, sort_keys=False), encoding='utf-8')
        return path

    def dispatch(self, *args, code=0):
        return self.report('dispatch', *args, code=code)

    def only(self, report):
        self.assertEqual(len(report['dispatches']), 1, report)
        return report['dispatches'][0]

    # --- no free text, no paths -------------------------------------------------------------------------------
    def test_sentinel_in_every_excluded_and_unknown_field_never_leaves(self):
        self.put(f'{RUN}.yaml', state())
        result = self.cli('dispatch')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SENT, result.stdout)
        self.assertNotIn('/home/user', result.stdout)
        entry = self.only(json.loads(result.stdout))
        self.assertEqual(entry['file'], f'{RUN}.yaml')
        self.assertNotIn(str(self.root), json.dumps(entry))

    def test_whitelisted_fields_come_through(self):
        self.put(f'{RUN}.yaml', state())
        report = self.dispatch()
        self.assertEqual((report['schema'], report['status'], report['authoritative']), (1, 'PASS', False))
        entry = self.only(report)
        self.assertEqual((entry['runId'], entry['status'], entry['baseBranch'], entry['baseCommit'], entry['masterHead']),
                         (RUN, 'running', 'main', HASH, HASH))
        card = {c['alias']: c for c in entry['cards']}['karta-1']
        self.assertEqual((card['state'], card['package'], card['branch']), ('working', 'P1', 'dispatch/p1'))
        package = entry['packages'][0]
        self.assertEqual((package['id'], package['card'], package['status'], package['branch'], package['inputCommit'],
                          package['resultCommit'], package['evidenceCount']), ('P1', 'karta-1', 'in_progress', 'dispatch/p1', HASH, None, 2))
        self.assertEqual(package['claim'], {'taskId': 'P1', 'runId': f'{RUN}.P1', 'ttl': 14400})
        self.assertNotIn('worktree', json.dumps(entry))
        self.assertEqual(entry['leases'][0]['resource'], 'smoke')
        self.assertEqual(entry['mergeQueue'], [{'package': 'P1', 'state': None}])
        self.assertIsNone(entry['pushSlot'])

    # --- statuses ---------------------------------------------------------------------------------------------
    def test_status_aliases_and_unknown_values(self):
        self.put('a.yaml', state(run_id='2026-10-31-a', status='active'))
        self.put('b.yaml', state(run_id='2026-10-31-b', status='weird-status'))
        rows = {d['runId']: d for d in self.dispatch()['dispatches']}
        self.assertEqual(rows['2026-10-31-a']['status'], 'running')
        self.assertEqual(rows['2026-10-31-b']['status'], 'unknown')
        self.assertNotIn('weird-status', json.dumps(rows))

    def test_package_statuses_go_known_others_unknown(self):
        packages = [{'id': f'P{i}', 'status': s} for i, s in enumerate(['go', 'push_granted', 'phase6', 'closing', 'done', None])]
        self.put(f'{RUN}.yaml', state(packages=packages))
        statuses = [p['status'] for p in self.only(self.dispatch())['packages']]
        self.assertEqual(statuses, ['go', 'unknown', 'unknown', 'unknown', 'done', 'unknown'])
        self.assertNotIn('push_granted', json.dumps(self.dispatch()))

    # --- shapes -----------------------------------------------------------------------------------------------
    def test_packages_as_single_mapping_and_garbage(self):
        self.put('a.yaml', state(run_id='2026-10-31-a', packages={'id': 'P9', 'status': 'done'}))
        self.put('b.yaml', state(run_id='2026-10-31-b', packages='nonsense'))
        rows = {d['runId']: d for d in self.dispatch()['dispatches']}
        self.assertEqual([p['id'] for p in rows['2026-10-31-a']['packages']], ['P9'])
        self.assertEqual(rows['2026-10-31-b']['packages'], [])
        self.assertTrue(any('packages' in f for f in self.dispatch()['findings']))

    def test_merge_queue_strings_or_mappings_and_master_forms(self):
        self.put('a.yaml', state(run_id='2026-10-31-a', merge_queue=[{'package': 'P1', 'state': 'waiting'}, 'P2'], master_head=None,
                                 master={'head': HASH, 'red': None}))
        entry = self.only(self.dispatch())
        self.assertEqual(entry['mergeQueue'], [{'package': 'P1', 'state': 'waiting'}, {'package': 'P2', 'state': None}])
        self.assertEqual(entry['masterHead'], HASH)

    def test_evidence_string_or_list(self):
        self.put(f'{RUN}.yaml', state(packages=[{'id': 'A', 'evidence': 'one.log'}, {'id': 'B', 'evidence': []}, {'id': 'C', 'evidence': 5}]))
        counts = [p['evidenceCount'] for p in self.only(self.dispatch())['packages']]
        self.assertEqual(counts, [1, 0, None])

    def test_bad_values_become_null_with_finding_without_the_value(self):
        bad = f'bad branch\n{SENT}'
        self.put(f'{RUN}.yaml', state(packages=[{'id': 'P1', 'branch': bad, 'input_commit': f'nothex-{SENT}', 'status': 'done'}],
                                      base_branch=bad))
        result = self.cli('dispatch')
        self.assertNotIn(SENT, result.stdout)
        entry = self.only(json.loads(result.stdout))
        self.assertIsNone(entry['packages'][0]['branch'])
        self.assertIsNone(entry['packages'][0]['inputCommit'])
        self.assertIsNone(entry['baseBranch'])
        self.assertTrue(json.loads(result.stdout)['findings'])

    # --- unreadable, limits, absent ---------------------------------------------------------------------------
    def test_unreadable_files_are_entries_not_errors(self):
        self.put('bad.yaml', None, raw='a: [unclosed\n')
        self.put('list.yaml', None, raw='- 1\n- 2\n')
        self.put('deep.yaml', None, raw='[' * 20000 + ']' * 20000)
        self.put('big.yaml', None, raw='k: ' + 'x' * (300 * 1024))
        self.put('ok.yaml', state(run_id='2026-10-31-ok'))
        report = self.dispatch()
        by_file = {d['file']: d for d in report['dispatches']}
        for name in ('bad.yaml', 'list.yaml', 'deep.yaml', 'big.yaml'):
            self.assertEqual(by_file[name]['state'], 'unreadable', name)
            self.assertNotIn('packages', by_file[name])
        self.assertEqual(by_file['ok.yaml']['state'], 'ok')
        self.assertEqual(report['status'], 'PASS')

    def test_filename_with_free_text_and_bad_bytes_never_leaves(self):
        self.put(f'hasło {SENT} incydent.yaml', state(run_id='2026-10-31-a'))
        self.put(f'{SENT[:8]}\nnewline.yaml', None, raw='a: [broken')
        raw = self.root / '.skillsync' / 'dispatch'
        try:
            (raw / b'\xff.yaml'.decode('utf-8', 'surrogateescape')).write_text('x: 1', encoding='utf-8')
        except OSError:
            pass
        result = self.cli('dispatch')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SENT, result.stdout)
        self.assertNotIn('hasło', result.stdout)
        report = json.loads(result.stdout)
        self.assertTrue(all(d['file'] is None for d in report['dispatches']))
        self.assertEqual(report['status'], 'PASS')

    def test_broken_yaml_content_and_error_text_do_not_leak(self):
        self.put('bad.yaml', None, raw=f'a: [{SENT}\nb: "{SENT}')
        result = self.cli('dispatch')
        self.assertNotIn(SENT, result.stdout)
        self.assertEqual(json.loads(result.stdout)['dispatches'][0]['state'], 'unreadable')

    def test_sentinel_in_whitelisted_fields_with_bad_shape_is_dropped(self):
        data = state(master={'head': f'{SENT}-nothex'}, master_head=None, push_slot=SENT * 4,
                     leases=[{'resource': 'smoke', 'holder': 'P1', 'granted_at': f'{SENT} when'}],
                     cards=[{'alias': 'karta-1', 'state': f'{SENT}-state'}],
                     packages=[{'id': 'P1', 'claim': {'task_id': SENT * 3, 'run_id': 'r', 'ttl': SENT}, 'status': [SENT], 'card': {'x': SENT}}])
        self.put(f'{RUN}.yaml', data)
        result = self.cli('dispatch')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SENT, result.stdout)
        entry = self.only(json.loads(result.stdout))
        self.assertEqual(entry['packages'][0]['status'], 'unknown')
        self.assertEqual(entry['cards'][0]['state'], 'unknown')

    def test_full_length_commit_hash_in_result_commit_is_kept(self):
        full = 'a1b2c3d4e5' * 4
        self.put(f'{RUN}.yaml', state(packages=[{'id': 'P1', 'result_commit': full}]))
        self.assertEqual(self.only(self.dispatch())['packages'][0]['resultCommit'], full)

    def test_credential_shaped_values_are_rejected(self):
        token = 'ghp_' + 'A1b2C3d4' * 5
        self.put(f'{RUN}.yaml', state(packages=[{'id': token, 'branch': 'sk-ant-api03-' + 'x' * 60, 'status': 'done'}]))
        result = self.cli('dispatch')
        self.assertNotIn(token, result.stdout)
        self.assertNotIn('sk-ant', result.stdout)
        package = self.only(json.loads(result.stdout))['packages'][0]
        self.assertEqual((package['id'], package['branch']), (None, None))

    @needs_fifo
    def test_fifo_in_claims_does_not_hang(self):
        self.put(f'{RUN}.yaml', state())
        claims = self.root / '.skillsync' / 'claims'
        claims.mkdir(parents=True)
        os.mkfifo(claims / 'x.json')
        self.assertEqual(self.dispatch()['status'], 'PASS')

    @needs_symlinks
    def test_symlinked_runs_directory_is_ignored_not_followed(self):
        other = self.base / 'other-runs'
        other.mkdir()
        (other / f'{RUN}.P1.json').write_text(json.dumps({'schema_version': 1, 'checkpoints': [self.checkpoint('a', 9.0)]}), encoding='utf-8')
        (self.root / '.skillsync').mkdir(exist_ok=True)
        (self.root / '.skillsync' / 'runs').symlink_to(other)
        self.put(f'{RUN}.yaml', state(packages=[{'id': 'P1'}]))
        package = self.only(self.dispatch())['packages'][0]
        self.assertEqual(package['cost']['state'], 'unknown')
        self.assertIsNone(package['run'])

    def test_non_finite_and_huge_costs_never_produce_invalid_json(self):
        self.put(f'{RUN}.yaml', state(packages=[{'id': 'P1'}]))
        self.run_file('P1', [self.checkpoint('a', 1e308), self.checkpoint('b', 1e308)])
        result = self.cli('dispatch')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('Infinity', result.stdout)
        self.assertEqual(self.only(json.loads(result.stdout))['packages'][0]['cost']['state'], 'unknown')

    def test_sentinel_in_run_record_free_text_does_not_leak(self):
        self.put(f'{RUN}.yaml', state(packages=[{'id': 'P1'}]))
        record = self.checkpoint('a', 0.1)
        record.update(next=f'{SENT} next', cause=f'{SENT} cause')
        self.run_file('P1', [record])
        self.assertNotIn(SENT, self.cli('dispatch').stdout)

    def test_alias_bomb_is_bounded(self):
        lines = ['a0: &a0 [x, x]'] + [f'a{i}: &a{i} [*a{i-1}, *a{i-1}]' for i in range(1, 40)] + ['status: running']
        self.put('bomb.yaml', None, raw='\n'.join(lines))
        start = time.time()
        report = self.dispatch()
        self.assertLess(time.time() - start, 5)
        self.assertEqual(report['status'], 'PASS')

    def test_no_directory_or_empty_is_not_run(self):
        report = self.dispatch()
        self.assertEqual((report['status'], report['dispatches']), ('NOT_RUN', []))
        (self.root / '.skillsync' / 'dispatch').mkdir(parents=True)
        self.assertEqual(self.dispatch()['status'], 'NOT_RUN')

    @needs_symlinks
    def test_symlinked_directory_blocks_symlinked_file_skipped(self):
        real = self.base / 'elsewhere'
        real.mkdir()
        (real / 'x.yaml').write_text(yaml.safe_dump(state()), encoding='utf-8')
        (self.root / '.skillsync').mkdir()
        (self.root / '.skillsync' / 'dispatch').symlink_to(real)
        report = self.dispatch(code=2)
        self.assertEqual(report['status'], 'BLOCKED')
        (self.root / '.skillsync' / 'dispatch').unlink()
        self.put('ok.yaml', state())
        (self.root / '.skillsync' / 'dispatch' / 'link.yaml').symlink_to(real / 'x.yaml')
        self.assertEqual([d['file'] for d in self.dispatch()['dispatches']], ['ok.yaml'])

    def test_run_id_filter_and_invalid_run_id(self):
        self.put('a.yaml', state(run_id='2026-10-31-a'))
        self.put('b.yaml', state(run_id='2026-10-31-b'))
        self.assertEqual([d['runId'] for d in self.dispatch('--run-id', '2026-10-31-b')['dispatches']], ['2026-10-31-b'])
        bad = self.dispatch('--run-id', '../x', code=2)
        self.assertEqual(bad['status'], 'BLOCKED')

    def test_newest_twenty_files(self):
        for i in range(25):
            path = self.put(f'2026-10-31-r{i:02d}.yaml', state(run_id=f'2026-10-31-r{i:02d}'))
            os.utime(path, (1_700_000_000 + i, 1_700_000_000 + i))
        runs = [d['runId'] for d in self.dispatch()['dispatches']]
        self.assertEqual(len(runs), 20)
        self.assertEqual(runs[0], '2026-10-31-r24')

    # --- freshness and claims -----------------------------------------------------------------------------------
    def test_stale_only_for_active_dispatches_older_than_six_hours(self):
        old = time.time() - 7 * 3600
        for name, status in (('run.yaml', 'running'), ('done.yaml', 'done')):
            path = self.put(name, state(run_id='2026-10-31-' + name[:-5], status=status))
            os.utime(path, (old, old))
        rows = {d['file']: d for d in self.dispatch()['dispatches']}
        self.assertTrue(rows['run.yaml']['freshness']['stale'])
        self.assertFalse(rows['done.yaml']['freshness']['stale'])
        self.assertGreaterEqual(rows['run.yaml']['freshness']['ageSeconds'], 7 * 3600 - 5)

    def test_claim_active_from_claim_file_not_yaml_ttl(self):
        self.put(f'{RUN}.yaml', state(packages=[{'id': 'P1', 'claim': {'task_id': 'P1', 'run_id': f'{RUN}.P1', 'ttl': 14400}},
                                                {'id': 'P2', 'claim': {'task_id': 'P2', 'run_id': f'{RUN}.P2', 'ttl': 10}},
                                                {'id': 'P3', 'claim': {'task_id': 'P3', 'run_id': f'{RUN}.P3', 'ttl': 10}}]))
        now = time.time()
        for task, expires in (('P1', now + 600), ('P2', now - 600)):
            self.write(f'.skillsync/claims/{task}.json', json.dumps({'version': 1, 'task_id': task, 'run_id': f'{RUN}.{task}',
                                                                      'acquired_at': now - 1000, 'heartbeat_at': now - 900, 'expires_at': expires}))
        active = {p['id']: p['claimActive'] for p in self.only(self.dispatch())['packages']}
        self.assertEqual(active, {'P1': True, 'P2': False, 'P3': None})

    # --- cost ---------------------------------------------------------------------------------------------------
    def checkpoint(self, step, cost, unit='USD'):
        return {'step': step, 'status': 'FAIL', 'next': 'x', 'recorded_at': '2026-10-31T09:00:00+00:00', 'provenance': 'test',
                'attempt': 1, 'cost': cost, 'duration_seconds': None, 'cost_unit': unit, 'fingerprint': {'digest': 'a' * 64, 'head': None},
                'evidence': []}

    def run_file(self, package, records):
        self.write(f'.skillsync/runs/{RUN}.{package}.json', json.dumps({'schema_version': 1, 'checkpoints': records}))

    def test_cost_known_partial_unknown_and_zero_is_never_guessed(self):
        self.put(f'{RUN}.yaml', state(packages=[{'id': k} for k in ('K', 'PA', 'U', 'Z', 'EUR', 'NONE')]))
        self.run_file('K', [self.checkpoint('a', 0.25), self.checkpoint('b', 0.5)])
        self.run_file('PA', [self.checkpoint('a', 0.25), self.checkpoint('b', None)])
        self.run_file('U', [self.checkpoint('a', None)])
        self.run_file('Z', [self.checkpoint('a', 0)])
        self.run_file('EUR', [self.checkpoint('a', 1.0, 'EUR')])
        costs = {p['id']: p['cost'] for p in self.only(self.dispatch())['packages']}
        self.assertEqual(costs['K'], {'usd': 0.75, 'partial': False, 'state': 'known'})
        self.assertEqual(costs['PA'], {'usd': 0.25, 'partial': True, 'state': 'partial'})
        self.assertEqual(costs['U'], {'usd': None, 'partial': False, 'state': 'unknown'})
        self.assertEqual(costs['Z'], {'usd': 0, 'partial': False, 'state': 'known'})
        self.assertEqual(costs['EUR'], {'usd': None, 'partial': False, 'state': 'unknown'})
        self.assertEqual(costs['NONE'], {'usd': None, 'partial': False, 'state': 'unknown'})
        runs = {p['id']: p['run'] for p in self.only(self.dispatch())['packages']}
        self.assertEqual((runs['K']['exists'], runs['K']['steps'], runs['NONE']['exists']), (True, 2, False))

    def test_unreadable_run_file_does_not_break_the_report(self):
        self.put(f'{RUN}.yaml', state(packages=[{'id': 'P1'}]))
        self.write(f'.skillsync/runs/{RUN}.P1.json', '{broken')
        package = self.only(self.dispatch())['packages'][0]
        self.assertEqual(package['cost']['state'], 'unknown')
        self.assertTrue(package['run']['exists'])


if __name__ == '__main__':
    unittest.main()
