"""Contract of the adapters of `test-run` (180-A5): the tool behind wrappers, reachability of the adapters, the counters of Karma.

Written before the code (red until the 180-A5 implementation): the table of `detect_tool`, the Karma counter that takes the LAST `Executed N of M`.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillsync_workflow import quality, test_adapters, test_run  # noqa: E402
from test_skillsync_test_run import Base, PASSING  # noqa: E402

TABLE = (
    ('dotnet test', 'dotnet'),
    ('cargo test', 'cargo'),
    ('timeout -k 5 100 cargo test', 'cargo'),
    ('timeout --kill-after=5 --signal=KILL 100 cargo test', 'cargo'),
    ('env A=1 B=2 cargo test', 'cargo'),
    ('env -i A=1 cargo test', 'cargo'),
    ('time -p nice -n 5 cargo test', 'cargo'),
    ('python -m pytest -q', 'pytest'),
    ('python3 -m unittest -v', 'unittest'),
    ('uv run pytest', 'pytest'),
    ('uv run python -m unittest -v', 'unittest'),
    ('pytest', 'pytest'),
    ('py.test', 'pytest'),
    ('npx playwright test', 'playwright'),
    ('npx --yes playwright test', 'playwright'),
    ('npm exec -- playwright test', 'playwright'),
    ('pnpm exec playwright test', 'playwright'),
    ('pnpm dlx playwright test', 'playwright'),
    ('yarn playwright test', 'playwright'),
    ('npx ng test', 'angular'),
    ('ng test', 'angular'),
    ('yarn ng test', 'angular'),
    ('npx vitest run', 'vitest'),
    ('npx jest', 'jest'),
    ('npx cypress run', 'cypress'),
    ('timeout 600 npx playwright test', 'playwright'),
    ('nice -10 npx playwright test', 'playwright'),
    ('nice -n10 npx playwright test', 'playwright'),
    ('time -v npx playwright test', 'playwright'),
    ('time -f %e -o out.txt npx jest', 'jest'),
    ('npx playwright@latest test', 'playwright'),
    ('npx jest@29', 'jest'),
    ('C:\\\\tools\\\\npx.CMD -y jest', 'jest'),
    ('"C:\\\\tools\\\\npx.cmd" jest', 'jest'),
    ('systemd-run --user --scope -p MemoryMax=3G -- cargo test', 'cargo'),
    ('systemd-run -p MemoryMax=3G cargo test', 'cargo'),
    # opaque scripts: no guessing
    ('npm test', 'npm'),
    ('npm run test:e2e', 'npm'),
    ('make test', 'unknown'),
    # an unknown wrapper or an unknown flag of a known one: as before (the window of the first three words, then unknown)
    ('timeout --unknown-flag 5 cargo test', 'unknown'),
    ('unknown-wrapper cargo test', 'cargo'),   # as before: cargo is within the first three words
    # no command behind the wrapper: no exception
    ('env', 'unknown'),
    ('timeout', 'unknown'),
    ('npx', 'npx'),
    # more layers than the limit (6)
    ('env env env env env env env cargo test', 'unknown'),
)


class DetectToolTable(unittest.TestCase):
    def test_every_row_of_the_table(self):
        for command, expected in TABLE:
            with self.subTest(command=command):
                self.assertEqual(test_run.detect_tool(command.split()), expected)

    def test_the_string_form_gives_the_same_answer(self):
        for command, expected in TABLE:
            with self.subTest(command=command):
                self.assertEqual(test_run.detect_tool(command), expected)


class Reachability(unittest.TestCase):
    def test_no_test_double_stays_registered_between_modules(self):
        test_adapters.discover()
        foreign = sorted(name for name, cls in test_adapters.REGISTRY.items() if not cls.__module__.startswith('skillsync_workflow.test_adapters.'))
        self.assertEqual(foreign, [], 'a module registered an adapter at import or did not take it back: other modules of the same process see it')

    def test_a_test_double_registered_by_another_module_is_ignored(self):
        double = type('Double', (test_adapters.Adapter,), {'name': 'zz-double', 'tools': ('no-such-tool',)})
        test_adapters.REGISTRY['zz-double'] = double
        self.addCleanup(test_adapters.REGISTRY.pop, 'zz-double', None)
        self.test_every_tool_of_every_adapter_is_a_value_of_the_table()

    def test_every_tool_of_every_adapter_is_a_value_of_the_table(self):
        known = {tool for _, tool in test_run.TOOLS} | {'unittest', 'pytest'}
        test_adapters.discover()
        product = [name for name, cls in test_adapters.REGISTRY.items() if cls.__module__.startswith('skillsync_workflow')]
        self.assertTrue({'pytest', 'unittest', 'dotnet'} <= set(product), product)   # the filter below must not hide the adapters of the product
        for name, cls in sorted(test_adapters.REGISTRY.items()):
            if not cls.__module__.startswith('skillsync_workflow.test_adapters.'):
                continue   # the adapters SHIPPED in the package; a test double registered by another module in the same process is not the subject
            with self.subTest(adapter=name):
                self.assertTrue(cls.tools, f'{name} declares no tools: it is unreachable in auto')
                for tool in cls.tools:
                    self.assertIn(tool, known, f'{name}: tool {tool!r} is not a value of TOOLS, so `auto` never selects it')


class KarmaCounter(unittest.TestCase):
    def test_the_last_executed_line_wins(self):
        output = 'Executed 0 of 5 SUCCESS (0 secs / 0 secs)\nExecuted 3 of 5 SUCCESS\nExecuted 5 of 5 SUCCESS (0.1 secs / 0.05 secs)\nTOTAL: 5 SUCCESS\n'
        self.assertEqual(quality._test_counts(output), (5, 0))

    def test_a_progress_start_alone_is_not_a_green_run_of_zero(self):
        run, skipped = quality._test_counts('Executed 0 of 5 SUCCESS\nExecuted 5 of 5 SUCCESS\n')
        self.assertEqual(run, 5)
        self.assertEqual(skipped, 0)

    def test_skipped_comes_from_the_last_line(self):
        run, skipped = quality._test_counts('Executed 0 of 5 SUCCESS\nExecuted 3 of 5 (skipped 2) SUCCESS\n')
        self.assertEqual((run, skipped), (3, 2))


class Unwrapping(unittest.TestCase):
    def test_the_argv_behind_the_wrappers(self):
        rows = (
            ('npx playwright test', ['playwright', 'test']),
            ('timeout -k 5 100 cargo test --lib', ['cargo', 'test', '--lib']),
            ('env A=1 timeout 10 npx --yes vitest run', ['vitest', 'run']),
            ('pnpm dlx cypress run', ['cypress', 'run']),
            ('yarn ng test', ['ng', 'test']),
            ('yarn test', ['yarn', 'test']),
            ('cargo test', ['cargo', 'test']),
            ('python3 manage.py test', ['python3', 'manage.py', 'test']),   # no -m: a script, not a module
            ('python3 -u -m pytest -q', ['pytest', '-q']),
            ('python -m unittest -v', ['unittest', '-v']),
            ('timeout --unknown-flag 5 cargo test', ['timeout', '--unknown-flag', '5', 'cargo', 'test']),
        )
        for command, expected in rows:
            with self.subTest(command=command):
                self.assertEqual(test_run.unwrap_command(command), expected)

    def test_more_layers_than_the_limit_return_the_original_argv(self):
        argv = ['env'] * 7 + ['cargo', 'test']
        self.assertEqual(test_run.unwrap_command(argv), argv)

    def test_an_unclosed_quote_is_no_exception(self):
        self.assertEqual(test_run.detect_tool('echo "abc'), 'unknown')
        self.assertEqual(test_run.unwrap_command('echo "abc'), [])

    def test_nothing_behind_the_wrapper_is_no_exception(self):
        for command in ('env', 'timeout', 'timeout 5', 'npx', 'systemd-run --user', ''):
            with self.subTest(command=command):
                self.assertIsInstance(test_run.unwrap_command(command), list)

    def test_the_context_carries_the_tool_and_the_core(self):
        ctx = test_adapters.AdapterContext(['npx', 'playwright', 'test'], '.', '.', mock.Mock(), tool='playwright', argv_core=['playwright', 'test'])
        self.assertEqual((ctx.tool, ctx.argv_core, ctx.argv), ('playwright', ['playwright', 'test'], ['npx', 'playwright', 'test']))
        old = test_adapters.AdapterContext(['cargo', 'test'], '.', '.', mock.Mock())   # the old four-argument form still works
        self.assertEqual((old.tool, old.argv_core), ('unknown', ['cargo', 'test']))


class Notice(unittest.TestCase):
    def notice(self, fmt, command):
        tool = test_run.detect_tool(command)
        return test_run.adapter_notice(fmt, command, tool, test_adapters.resolve(fmt, command, tool))

    def test_the_chosen_adapter_is_named(self):
        self.assertEqual(self.notice('auto', ['cargo', 'test']), 'test-run: adapter cargo-test (live completion)')

    def test_no_adapter_for_the_tool_names_the_way_out(self):
        text = self.notice('auto', ['make', 'test'])
        self.assertIn('no adapter for unknown', text)
        self.assertIn('--format <name>', text)
        self.assertIn('--format none', text)
        self.assertIn('cargo-test', text)   # the list of the known names

    def test_an_adapter_that_refused_the_arguments_is_said_to_be_unused(self):
        text = self.notice('auto', ['python', '-m', 'unittest'])   # unittest without -v prints dots: nothing to adapt
        self.assertIn('adapter unittest not used', text)
        self.assertIn('docs/test-adapters.md', text)

    def test_no_format_means_auto(self):
        self.assertIn('no adapter for unknown', test_run.adapter_notice(None, ['make'], 'unknown', None))

    def test_an_explicit_choice_is_silent(self):
        self.assertIsNone(self.notice('none', ['cargo', 'test']))
        self.assertIsNone(self.notice('cargo-test', ['cargo', 'test']))


class RecorderCounters(unittest.TestCase):
    def recorder(self, adapter):
        recorder = test_run.TestRecorder('.', 'R1', 'F1.1', 'flag', adapter=adapter)
        recorder.writer = mock.Mock()
        return recorder

    def test_a_selected_adapter_owns_the_counters(self):
        recorder = self.recorder(mock.Mock())
        recorder.line({}, 'out', 'Executed 3 of 5 (skipped 2) SUCCESS')
        recorder.writer.counts.assert_not_called()

    def test_without_an_adapter_the_built_in_reader_works(self):
        recorder = self.recorder(None)
        recorder.line({}, 'out', 'Executed 3 of 5 (skipped 2) SUCCESS')
        recorder.writer.counts.assert_called_once_with(passed=1, failed=0, skipped=2, running=0, total=5)

    def test_both_parentheses_are_read(self):
        recorder = self.recorder(None)
        recorder.line({}, 'out', 'Executed 5 of 5 (2 FAILED) (skipped 2)')
        recorder.writer.counts.assert_called_once_with(passed=1, failed=2, skipped=2, running=0, total=5)
        recorder = self.recorder(None)
        recorder.line({}, 'out', 'Executed 5 of 5 (skipped 2) (2 FAILED)')
        recorder.writer.counts.assert_called_once_with(passed=1, failed=2, skipped=2, running=0, total=5)


class WindowsShims(unittest.TestCase):
    def test_argv0_becomes_what_which_finds_on_windows(self):
        which = mock.Mock(return_value='C:\\tools\\npx.CMD')
        self.assertEqual(test_run.resolve_executable(['npx', 'playwright', 'test'], which=which, nt=True), ['C:\\tools\\npx.CMD', 'playwright', 'test'])
        which.assert_called_once_with('npx')

    def test_other_systems_and_unfound_commands_are_unchanged(self):
        which = mock.Mock(return_value='/usr/bin/npx')
        self.assertEqual(test_run.resolve_executable(['npx', 'x'], which=which, nt=False), ['npx', 'x'])
        which.assert_not_called()
        self.assertEqual(test_run.resolve_executable(['nothing'], which=mock.Mock(return_value=None), nt=True), ['nothing'])
        self.assertEqual(test_run.resolve_executable([], which=which, nt=True), [])

    def test_the_command_inside_a_wrapper_is_left_to_the_wrapper(self):
        which = mock.Mock(return_value='C:\\tools\\npx.CMD')
        result = test_run.resolve_executable(['npx', 'ng', 'test'], which=which, nt=True)
        self.assertEqual(result[1:], ['ng', 'test'])
        self.assertEqual(which.call_count, 1)


class NoticeEndToEnd(Base):
    def test_the_line_is_on_stderr_before_the_command_and_the_output_is_unchanged(self):
        done = self.run_cli('--allow-no-count', 'x', '--', sys.executable, '-c', PASSING)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, 'Ran 2 tests in 0.01s\nOK\n')
        lines = done.stderr.splitlines()
        self.assertTrue(lines[0].startswith('test-run: no adapter for unknown (known: '), lines)
        self.assertTrue(any(line.startswith('test-run: step ') for line in lines[1:]), lines)   # the summary line at the end is as before

    def test_none_and_a_name_are_silent(self):
        for fmt in ('none', 'cargo-test'):
            with self.subTest(fmt=fmt):
                done = self.run_cli('--format', fmt, '--allow-no-count', 'x', '--', sys.executable, '-c', PASSING)
                self.assertNotIn('no adapter', done.stderr)
                self.assertNotIn('test-run: adapter', done.stderr)


class WindowsShimsInTheCommand(Base):
    def test_the_command_started_by_test_run_has_the_resolved_argv0(self):
        import argparse
        seen = []

        def fake_run(root, checks, observer=None):
            seen.extend(checks)
            return {'results': [{'exit_code': 0, 'status': 'PASS'}]}

        args = argparse.Namespace(path=str(self.root), run='R1', step='F1.1', junit=None, allow_no_count='x', allow_skips=False, format='none', import_file=None,
                                  source_sha=None, timeout=60, manifest=None, command=['--', 'npx', 'playwright', 'test'])
        with mock.patch.object(test_run, 'run_validation', fake_run), mock.patch.object(test_run, 'resolve_executable', lambda argv: ['RESOLVED'] + argv[1:]):
            test_run._command(args)
        self.assertEqual(seen[0]['argv'], ['RESOLVED', 'playwright', 'test'])


class TheTableIsData(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        # the tests of this class swap the file and the cache of the table: nothing of it may stay for the modules that run later in the same process
        assert test_run.TOOLS_FILE == ROOT / 'skillsync_workflow' / 'data' / 'test_tools.json', test_run.TOOLS_FILE
        assert test_run.detect_tool('bun test') == 'unknown', 'the table of a test leaked'

    def setUp(self):
        self.addCleanup(setattr, test_run, '_TABLE', test_run._TABLE)
        self.addCleanup(setattr, test_run, 'TOOLS_FILE', test_run.TOOLS_FILE)

    def use(self, mutate):
        data = json.loads(test_run.TOOLS_FILE.read_text(encoding='utf-8'))
        mutate(data)
        tmp = tempfile.TemporaryDirectory(prefix='test-tools-')
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'test_tools.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        test_run.TOOLS_FILE, test_run._TABLE = path, None

    def test_the_file_is_in_the_flat_data_directory_the_wheel_allows(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        try:
            import check_wheel
        finally:
            sys.path.pop(0)
        self.assertEqual(test_run.TOOLS_FILE, ROOT / 'skillsync_workflow' / 'data' / 'test_tools.json')
        self.assertTrue(test_run.TOOLS_FILE.is_file())
        self.assertTrue(check_wheel.allowed('skillsync_workflow/data/test_tools.json'))

    def test_a_new_entry_changes_the_answer_without_a_change_of_code(self):
        self.assertEqual(test_run.detect_tool('bun test'), 'unknown')
        self.use(lambda data: data['tools'].append({'word': 'bun', 'tool': 'bun'}))
        self.assertEqual(test_run.detect_tool('bun test'), 'bun')
        self.assertEqual(test_run.detect_tool('timeout 5 bun test'), 'bun')

    def test_a_new_wrapper_is_data_too(self):
        self.use(lambda data: data['wrappers'].append({'word': 'nohup', 'boolean': []}))
        self.assertEqual(test_run.detect_tool('nohup cargo test'), 'cargo')
        self.assertEqual(test_run.unwrap_command('nohup nohup npx jest'), ['jest'])

    def test_the_order_of_the_entries_is_significant(self):
        self.use(lambda data: data['tools'].insert(0, {'word': 'cargo', 'tool': 'first'}))
        self.assertEqual(test_run.detect_tool('cargo test'), 'first')

    def test_the_known_commands_give_the_answer_of_the_file(self):
        table = test_run.tool_table()
        for word, tool in table['tools']:
            with self.subTest(word=word):
                self.assertEqual(test_run.detect_tool([word, 'x']), tool if word not in table['modules'] else table['modules'][word])

    def test_another_schema_is_a_loud_error_even_when_the_rest_is_valid(self):
        self.use(lambda data: data.update(schema=2))
        with self.assertRaises(ValueError):
            test_run.detect_tool('cargo test')

    def test_a_missing_or_broken_file_is_a_loud_error(self):
        for content in (None, '{not json', '{"schema": 2}', '{"schema": 1}'):
            with self.subTest(content=content):
                tmp = tempfile.TemporaryDirectory(prefix='test-tools-')
                self.addCleanup(tmp.cleanup)
                path = Path(tmp.name) / 'test_tools.json'
                if content is not None:
                    path.write_text(content, encoding='utf-8')
                test_run.TOOLS_FILE, test_run._TABLE = path, None
                with self.assertRaises(ValueError):
                    test_run.detect_tool('cargo test')


if __name__ == '__main__':
    unittest.main()
