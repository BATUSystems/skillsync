"""Windows shims in `test-run`: the `start` event shows the command of the user (not the resolved absolute path), and a warning about cmd.exe
metacharacters precedes the start of a `.cmd`/`.bat` command."""
import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from skillsync_workflow import test_run  # noqa: E402
from test_skillsync_test_run import Base  # noqa: E402

RESOLVED = 'C:\\tools\\npx.CMD'


class StartEventCommand(Base):
    def start_record(self, check):
        recorder = test_run.TestRecorder(self.root, 'R1', 'F1.1', 'flag')
        recorder.started(check)
        self.addCleanup(recorder.writer.close)
        path = self.root / '.skillsync/runs/R1/tests/F1.1/1.jsonl'
        records = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        return next(r for r in records if r.get('t') == 'start'), path.read_text(encoding='utf-8')

    def test_display_replaces_the_resolved_path_in_the_command(self):
        check = {'id': 'test-run', 'argv': [RESOLVED, 'playwright', 'test'], 'display': ['npx', 'playwright', 'test'], 'cwd': str(self.root)}
        record, raw = self.start_record(check)
        self.assertEqual(record['command'], 'npx playwright test')
        self.assertNotIn('npx.CMD', raw)
        self.assertNotIn('C:', raw)

    def test_display_keeps_the_quoting_of_the_user_command(self):
        check = {'id': 'test-run', 'argv': ['C:\\tools\\npx.exe', '--grep', 'a b'], 'display': ['npx', '--grep', 'a b'], 'cwd': str(self.root)}
        record, raw = self.start_record(check)
        self.assertEqual(record['command'], "npx --grep 'a b'")
        self.assertNotIn('.exe', raw)

    def test_without_display_the_command_is_as_before(self):
        check = {'id': 'test-run', 'argv': ['npx', 'playwright', 'test'], 'cwd': str(self.root)}
        record, _ = self.start_record(check)
        self.assertEqual(record['command'], 'npx playwright test')


class DisplayInTheCheck(Base):
    def test_main_flow_puts_the_user_command_in_display_and_the_resolved_in_argv(self):
        seen = []
        stderr = io.StringIO()

        def fake_run(root, checks, observer=None):
            seen.extend(checks)
            self.assertIn('cmd.exe', stderr.getvalue())   # the warning is printed BEFORE the command starts
            return {'results': [{'exit_code': 0, 'status': 'PASS'}]}

        args = argparse.Namespace(path=str(self.root), run='R1', step='F1.1', junit=None, allow_no_count='x', allow_skips=False, format='none', import_file=None,
                                  source_sha=None, timeout=60, manifest=None, command=['--', 'npx', 'test', '--grep', 'xx|yy'])
        which = mock.Mock(return_value=RESOLVED)
        real, real_notice = test_run.resolve_executable, test_run.cmd_metachar_notice
        with mock.patch.object(test_run, 'run_validation', fake_run), \
                mock.patch.object(test_run, 'resolve_executable', lambda argv: real(argv, which=which, nt=True)), \
                mock.patch.object(test_run, 'cmd_metachar_notice', lambda argv: real_notice(argv, nt=True)), \
                contextlib.redirect_stderr(stderr):
            test_run._command(args)
        self.assertEqual(seen[0]['argv'], [RESOLVED, 'test', '--grep', 'xx|yy'])
        self.assertEqual(seen[0]['display'], ['npx', 'test', '--grep', 'xx|yy'])
        self.assertNotIn('command', seen[0])   # `command` would make `run_validation` use a shell
        self.assertIn('warning', stderr.getvalue())
        self.assertNotIn('xx|yy', stderr.getvalue())


class CmdMetacharNotice(unittest.TestCase):
    def notice(self, argv, nt=True):
        return test_run.cmd_metachar_notice(argv, nt=nt)

    def test_a_pipe_in_an_argument_of_a_cmd_shim_warns_without_quoting_the_argument(self):
        text = self.notice(['C:\\tools\\npx.cmd', 'playwright', 'test', '--grep', 'xx|yy'])
        self.assertIsNotNone(text)
        self.assertTrue(text.startswith('test-run: '))
        self.assertIn('cmd.exe', text)
        self.assertNotIn('xx|yy', text)

    def test_every_metacharacter_and_the_quote(self):
        for char in '&|<>^%"':
            with self.subTest(char=char):
                self.assertIsNotNone(self.notice(['C:\\t\\npx.cmd', f'x{char}y']))
        self.assertIsNotNone(self.notice(['C:\\t\\npx.cmd', '%PATH%']))

    def test_bat_and_the_case_of_the_extension(self):
        for target in ('C:\\t\\x.bat', 'C:\\t\\x.BAT', 'C:\\t\\x.CMD', 'C:\\t\\x.Cmd'):
            with self.subTest(target=target):
                self.assertIsNotNone(self.notice([target, 'a&b']))

    def test_no_warning_without_a_reason(self):
        self.assertIsNone(self.notice(['C:\\t\\npx.exe', 'xx|yy']))
        self.assertIsNone(self.notice(['C:\\t\\npx.cmd', 'playwright', 'test', '--grep', 'plain']))
        self.assertIsNone(self.notice(['C:\\t\\npx.cmd']))
        self.assertIsNone(self.notice([]))
        self.assertIsNone(self.notice(['npx', 'xx|yy']))   # not resolved to a shim

    def test_only_the_arguments_count_not_the_program(self):
        self.assertIsNone(self.notice(['C:\\Tools & Co\\npx.cmd', 'test']))

    def test_other_systems_are_silent(self):
        self.assertIsNone(self.notice(['C:\\t\\npx.cmd', 'xx|yy'], nt=False))


if __name__ == '__main__':
    unittest.main()
