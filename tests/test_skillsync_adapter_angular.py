"""180-A3: `ng test` runs Vitest or Karma, chosen by the project: the `angular` adapter reads the lines and lets whichever tool prints emit (NOT_VERIFIED on a real
`ng test`: the two engines were recorded directly)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay as ar  # noqa: E402
from adapter_replay import replay_fixture, replay, load, chunks_from_text  # noqa: E402

from skillsync_workflow import test_adapters  # noqa: E402

ADAPTER_FILE = Path(test_adapters.__file__).parent / 'angular.py'


class Selection(unittest.TestCase):
    def test_ng_test_selects_the_angular_adapter_and_the_others_do_not_take_it(self):
        adapter = test_adapters.resolve('auto', ['ng', 'test', '--no-watch'], 'angular')
        self.assertEqual((adapter.name, adapter.live), ('angular', 'counts'))
        for name in ('vitest', 'karma', 'cypress', 'playwright'):
            self.assertFalse(test_adapters.REGISTRY[name]().accepts(['ng', 'test']))
        self.assertTrue(test_adapters.REGISTRY['angular']().accepts(['npx', 'ng', 'test']))   # `accepts` looks for the command, whatever wraps it
        self.assertFalse(test_adapters.REGISTRY['angular']().accepts(['ng', 'build']))

    def test_npx_and_the_short_alias_and_the_wrong_subcommands(self):
        self.assertEqual(test_adapters.resolve('angular', ['npx', 'ng', 'test'], 'npx').name, 'angular')   # `--format angular` works under any wrapper
        cls = test_adapters.REGISTRY['angular']
        for argv, expected in ((['ng', 't'], True), (['ng', 'generate', 'component', 'test'], False), (['ng.cmd', 'test'], True), (['npx', 'vitest', 'test'], False), (['ng'], False)):
            with self.subTest(argv=argv):
                self.assertEqual(cls().accepts(argv), expected)

    def test_format_forces_one_of_the_engines(self):
        self.assertEqual(test_adapters.resolve('vitest', ['ng', 'test'], 'angular').name, 'vitest')
        self.assertEqual(test_adapters.resolve('karma', ['ng', 'test'], 'angular').name, 'karma')

    def test_the_module_only_reads_text_and_emits(self):
        self.assertEqual(ar.audit(ADAPTER_FILE), [])


class Engines(unittest.TestCase):
    def test_a_vitest_run_gives_the_tests_and_the_report_is_not_needed(self):
        recorder, _, _, complete = replay_fixture('angular', 'vitest-verbose.jsonl', argv=['ng', 'test'], tool='angular')
        direct = replay_fixture('vitest', 'vitest-verbose.jsonl')[0]
        self.assertEqual(recorder.calls, direct.calls)
        self.assertTrue(complete)

    def test_a_karma_run_gives_the_counters(self):
        recorder, _, _, complete = replay_fixture('angular', 'karma-progress.jsonl', argv=['ng', 'test'], tool='angular')
        direct = replay_fixture('karma', 'karma-progress.jsonl')[0]
        self.assertEqual(recorder.calls, direct.calls)
        self.assertFalse(complete)

    def test_an_unknown_reporter_emits_nothing(self):
        self.assertEqual(replay('angular', chunks_from_text('Application bundle generation complete.\nsome other reporter 3 of 4\n'), argv=['ng', 'test'], tool='angular')[0].calls, [])


if __name__ == '__main__':
    unittest.main()
