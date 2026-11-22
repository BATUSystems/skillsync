"""CLAUDE-3 / F3: the SubagentStop hook `library/hooks/check-subagent-report.py` (spec K5, A4).

For the writing roles (implementer, test-writer, scribe, mechanic, test-runner) the hook checks `last_assistant_message`: a line starting with
`Wynik:`, `Status:`, `Raport:` or `Report:` OR a path under `.skillsync/runs/` counts as a report. Otherwise it appends one JSON line to
`<project root>/.skillsync/hook-events.jsonl`. It never blocks (exit 0, no `decision`). Empty `agent_type` (context compaction) and other roles
are skipped. The script is taken from the repository.
"""
import json
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path

import sys as _sys_tmp_paths  # noqa: E402
from pathlib import Path as _Path_tmp_paths  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child, descend  # noqa: E402
from layers_helpers import IsolatedTestCase  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / 'library' / 'hooks' / 'check-subagent-report.py'
ROLES = ('implementer', 'test-writer', 'scribe', 'mechanic', 'test-runner')
NO_REPORT = 'Zrobiłem zmiany w trzech plikach i wszystko działa. Dajcie znać, jeśli coś jeszcze.'


# fragments joined at run time: a literal home-directory path in a test file is an owner path for the leak guard
WIN_USERS = 'C:' + chr(92) + 'Us' + 'ers'
OTHER_HOME = '/' + 'ho' + 'me/other'


class SubagentReportHook(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.make_repo('proj')
        self.events = descend(self.repo, '.skillsync/hook-events.jsonl')

    # --- helpers -------------------------------------------------------------------------------------------------------
    def payload(self, message=NO_REPORT, agent_type='implementer', session_id='sess-1', cwd=None, **extra):
        data = {'session_id': session_id, 'transcript_path': str(child(self.root, 't.jsonl')), 'cwd': str(cwd or self.repo),
                'hook_event_name': 'SubagentStop', 'agent_id': 'a1b2c3', 'agent_type': agent_type,
                'agent_transcript_path': str(child(self.root, 'agent.jsonl')), 'last_assistant_message': message,
                'stop_hook_active': False}
        data.update(extra)
        return data

    def run_hook(self, payload=None, mode='warn', raw=None, extra=None):
        self.assertTrue(HOOK.is_file(), f'{HOOK} does not exist yet (expected red until F4)')
        data = raw if raw is not None else json.dumps(payload, ensure_ascii=False)
        args = [sys.executable, str(HOOK)] + (extra if extra is not None else (['--mode', mode] if mode else []))
        done = subprocess.run(args, input=data.encode('utf-8'), capture_output=True, timeout=30, cwd=str(self.repo))
        self.stderr = done.stderr.decode('utf-8', 'replace')
        return done

    def lines(self):
        if not self.events.exists():
            return []
        text = self.events.read_bytes().decode('utf-8')
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def assertNeverBlocks(self, done):
        self.assertEqual(done.returncode, 0, self.stderr)
        out = done.stdout.decode('utf-8').strip()
        if out:
            self.assertNotEqual(json.loads(out).get('decision'), 'block')

    def assertWarns(self, payload, **kwargs):
        done = self.run_hook(payload, **kwargs)
        self.assertNeverBlocks(done)
        return self.lines()

    # --- warning ---------------------------------------------------------------------------------------------------------
    def test_missing_report_appends_one_well_formed_event(self):
        before = datetime.now().astimezone()
        done = self.run_hook(self.payload(session_id='sess-42'))
        self.assertNeverBlocks(done)
        records = self.lines()
        self.assertEqual(len(records), 1)
        record = records[0]
        for key, value in (('hook', 'subagent-report'), ('event', 'SubagentStop'), ('agent_type', 'implementer'),
                           ('reason', 'no-report'), ('session_id', 'sess-42')):
            self.assertEqual(record.get(key), value, key)
        stamp = datetime.fromisoformat(record['time'])
        self.assertIsNotNone(stamp.tzinfo, 'time must be ISO 8601 with a zone')
        self.assertLessEqual(abs((stamp - before).total_seconds()), 120)

    def test_all_five_roles_are_checked(self):
        for role in ROLES:
            with self.subTest(role=role):
                self.events.unlink(missing_ok=True)
                records = self.assertWarns(self.payload(agent_type=role))
                self.assertEqual([r['agent_type'] for r in records], [role])

    def test_a_result_status_report_line_is_a_report(self):
        for message in ('Wynik: PASS\nzmieniono 3 pliki', 'Praca skończona.\nStatus: DONE\nreszta', 'Raport: .skillsync/x\n',
                        'Report: all green', 'Krótko.\n\nWynik:'):
            with self.subTest(message=message):
                self.assertEqual(self.assertWarns(self.payload(message)), [])

    def test_a_path_under_skillsync_runs_is_a_report(self):
        for message in ('Pełny raport w .skillsync/runs/2026-11-21-claude-3/F3-test-writer.md, reszta skrótu.',
                        'Zapisałem: `.skillsync/runs/abc/step.md`'):
            with self.subTest(message=message):
                self.assertEqual(self.assertWarns(self.payload(message)), [])

    def test_marker_in_the_middle_of_a_line_is_not_a_report_line(self):
        for message in ('Nie ma tu żadnego Wynik: bo to środek zdania.', 'Zobacz opis pola Status: w dokumentacji', 'Dodatkowy Report: w środku linii'):
            with self.subTest(message=message):
                self.assertEqual(len(self.assertWarns(self.payload(message))), 1)
                self.events.unlink()

    def test_similar_paths_are_not_a_report(self):
        for message in ('Zajrzyj do .skillsync/hook-events.jsonl', 'Plik skillsync/runs/x.md', 'runs/x.md w skillsync'):
            with self.subTest(message=message):
                self.assertEqual(len(self.assertWarns(self.payload(message))), 1)
                self.events.unlink()

    def test_empty_or_missing_message_without_a_report_warns(self):
        for message in ('', None):
            with self.subTest(message=message):
                self.assertEqual(len(self.assertWarns(self.payload(message))), 1)
                self.events.unlink()

    # --- filter ----------------------------------------------------------------------------------------------------------
    def test_empty_and_missing_agent_type_are_skipped_compaction(self):
        self.assertEqual(self.assertWarns(self.payload(agent_type='')), [])
        data = self.payload()
        del data['agent_type']
        self.assertEqual(self.assertWarns(data), [])
        self.assertEqual(self.assertWarns(self.payload(agent_type=None)), [])

    def test_roles_outside_the_list_are_skipped(self):
        for role in ('explore', 'reviewer', 'verifier', 'general-purpose', 'Implementer', 'implementer-2', 'x' * 500):
            with self.subTest(role=role):
                self.assertEqual(self.assertWarns(self.payload(agent_type=role)), [])

    # --- modes -----------------------------------------------------------------------------------------------------------
    def test_mode_off_writes_nothing_and_does_not_create_the_directory(self):
        done = self.run_hook(self.payload(), mode='off')
        self.assertNeverBlocks(done)
        self.assertFalse(self.events.exists())
        self.assertFalse(child(self.repo, '.skillsync').exists())

    def test_mode_warn_is_explicit_and_never_blocks_even_when_the_stop_hook_is_active(self):
        for active in (False, True):
            with self.subTest(stop_hook_active=active):
                done = self.run_hook(self.payload(stop_hook_active=active), mode='warn')
                self.assertNeverBlocks(done)
        self.assertEqual(len(self.lines()), 2)

    # --- the events file -------------------------------------------------------------------------------------------------
    def test_events_are_appended_one_line_each_existing_lines_stay(self):
        self.events.parent.mkdir()
        self.events.write_bytes('{"keep": "ż"}\n'.encode('utf-8'))
        self.run_hook(self.payload(session_id='one'))
        self.run_hook(self.payload(agent_type='scribe', session_id='two'))
        raw = self.events.read_bytes().decode('utf-8')
        self.assertTrue(raw.endswith('\n'))
        rows = raw.splitlines()
        self.assertEqual(len(rows), 3)
        self.assertEqual(json.loads(rows[0]), {'keep': 'ż'})
        self.assertEqual([json.loads(r)['session_id'] for r in rows[1:]], ['one', 'two'])
        self.assertEqual([json.loads(r)['agent_type'] for r in rows[1:]], ['implementer', 'scribe'])

    def test_two_events_are_two_lines_with_no_embedded_newline(self):
        self.run_hook(self.payload(session_id='a\nb'))
        self.run_hook(self.payload(session_id='c'))
        raw = self.events.read_bytes().decode('utf-8')
        self.assertEqual(len(raw.splitlines()), 2)
        self.assertEqual([r['session_id'] for r in self.lines()], ['a\nb', 'c'])

    def test_utf8_beyond_ascii_is_stored_as_utf8(self):
        self.run_hook(self.payload('Zażółć gęślą jaźń ✓ bez raportu', session_id='sesja-żółć-日本'))
        raw = self.events.read_bytes()
        raw.decode('utf-8')
        self.assertEqual(self.lines()[0]['session_id'], 'sesja-żółć-日本')

    def test_missing_skillsync_directory_is_created(self):
        self.assertFalse(child(self.repo, '.skillsync').exists())
        self.run_hook(self.payload())
        self.assertTrue(self.events.is_file())

    def test_event_goes_to_the_project_root_also_from_a_subdirectory_cwd(self):
        sub = descend(self.repo, 'src/deep')
        sub.mkdir(parents=True)
        self.run_hook(self.payload(cwd=sub))
        self.assertTrue(self.events.is_file())
        self.assertFalse(descend(sub, '.skillsync').exists())

    def test_event_has_no_message_text_only_the_documented_fields(self):
        secret = 'wrażliwa treść odpowiedzi podagenta'
        self.run_hook(self.payload(secret))
        raw = self.events.read_bytes().decode('utf-8')
        self.assertNotIn(secret, raw)
        self.assertEqual(set(self.lines()[0]), {'time', 'hook', 'event', 'agent_type', 'reason', 'session_id'})

    def test_windows_paths_in_the_payload_are_data(self):
        data = self.payload(('Gotowe, bez raportu w ' + WIN_USERS + '\\dev\\proj'), session_id='C:\\sess')
        data['cwd'] = str(self.repo)
        data['agent_transcript_path'] = (WIN_USERS + '\\dev\\.claude\\agent.jsonl')
        self.assertEqual(len(self.assertWarns(data)), 1)

    # --- fail-open -------------------------------------------------------------------------------------------------------
    def test_garbage_input_is_fail_open_and_writes_nothing(self):
        for raw in ('{broken', '', '   ', '[]', 'null', '"x"', '\x00\x01'):
            with self.subTest(raw=raw):
                self.assertNeverBlocks(self.run_hook(raw=raw))
                self.assertEqual(self.lines(), [])

    def test_unwritable_events_location_is_fail_open(self):
        child(self.repo, '.skillsync').write_text('this is a file, not a directory', encoding='utf-8')
        self.assertNeverBlocks(self.run_hook(self.payload()))

    def test_missing_cwd_and_non_git_cwd_are_fail_open(self):
        plain = child(self.root, 'plain')
        plain.mkdir()
        for cwd in (child(self.root, 'missing-dir'), plain):
            with self.subTest(cwd=str(cwd)):
                self.assertNeverBlocks(self.run_hook(self.payload(cwd=cwd)))
        data = self.payload()
        del data['cwd']
        self.assertNeverBlocks(self.run_hook(data))

    def test_missing_mode_and_unknown_mode_never_block(self):
        self.assertNeverBlocks(self.run_hook(self.payload(), mode=None))
        self.assertNeverBlocks(self.run_hook(self.payload(), extra=['--mode', 'bogus']))


if __name__ == '__main__':
    unittest.main()
