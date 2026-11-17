"""180-A3: which adapter `skillsync test-run` picks for a command of a web tool, with and without `--format`.

Every adapter declares its OWN tool (never a wrapper: `playwright`, `cypress`, `vitest`, `karma`, `angular`, all values of `test_run.TOOLS`) and its `accepts` looks for the
command anywhere in the argv, so `--format <name>` works under any wrapper now. `auto` through a wrapper (`npx playwright test`, `yarn playwright test`, `timeout 60 ...`) works since `detect_tool` peels the wrappers (180-A5). A script of a package
manager (`npm test`, `npm run ...`, `yarn test`, `make`) is opaque and never recognised: `--format` is the way."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adapter_replay  # noqa: E402,F401

from skillsync_workflow import test_adapters, test_run  # noqa: E402

DIRECT = (
    (['vitest', 'run', '--reporter=verbose'], 'vitest'), (['./node_modules/.bin/vitest', 'run'], 'vitest'), (['playwright', 'test'], 'playwright'),
    (['./node_modules/.bin/playwright', 'test', '--project=x'], 'playwright'), (['cypress', 'run'], 'cypress'), (['karma', 'start', 'karma.conf.js'], 'karma'),
    (['ng', 'test'], 'angular'), (['ng', 't'], 'angular'),
)
WRAPPED = (
    (['npx', 'vitest', 'run'], 'vitest'), (['npx', 'playwright', 'test'], 'playwright'), (['npx', 'cypress', 'run'], 'cypress'), (['npx', 'karma', 'start'], 'karma'),
    (['npx', 'ng', 'test'], 'angular'), (['pnpm', 'exec', 'vitest', 'run'], 'vitest'), (['timeout', '60', 'playwright', 'test'], 'playwright'), (['yarn', 'playwright', 'test'], 'playwright'),
)
OPAQUE = (['npm', 'test'], ['npm', 'run', 'test:unit'], ['yarn', 'test'], ['make', 'test'], ['pnpm', 'test'])
NAMES = ('vitest', 'karma', 'playwright', 'cypress', 'angular')


def auto(argv):
    adapter = test_adapters.resolve('auto', argv, test_run.detect_tool(argv))
    return adapter.name if adapter else None


class Declarations(unittest.TestCase):
    def test_every_tool_is_a_value_of_the_tools_table_and_is_not_a_wrapper(self):
        known = {tool for _, tool in test_run.TOOLS}
        for name in NAMES:
            with self.subTest(adapter=name):
                tools = test_adapters.REGISTRY[name].tools
                self.assertTrue(tools and set(tools) <= known, tools)
                self.assertFalse(set(tools) & {'npx', 'npm', 'node'}, tools)


class Auto(unittest.TestCase):
    def test_direct_commands(self):
        for argv, want in DIRECT:
            with self.subTest(argv=argv):
                self.assertEqual(auto(argv), want)

    def test_opaque_scripts_are_never_recognised(self):
        for argv in OPAQUE:
            with self.subTest(argv=argv):
                self.assertIsNone(auto(argv))

    def test_wrapped_commands_after_the_wrappers_are_peeled(self):
        for argv, want in WRAPPED:
            with self.subTest(argv=argv):
                self.assertEqual(auto(argv), want)


class Notice(unittest.TestCase):
    def notice(self, argv):
        tool = test_run.detect_tool(argv)
        return test_run.adapter_notice('auto', argv, tool, test_adapters.resolve('auto', argv, tool))

    def test_vitest_without_a_reporter_says_how_to_get_the_list(self):
        for argv, reporter in ((['vitest', 'run'], '--reporter=verbose'), (['npx', 'ng', 'test', '--no-watch'], '--reporters=verbose')):
            with self.subTest(argv=argv):
                text = self.notice(argv)
                self.assertIn('test list', text)
                self.assertIn(reporter, text)

    def test_a_chosen_reporter_or_another_tool_gets_no_advice(self):
        for argv in (['vitest', 'run', '--reporter=verbose'], ['playwright', 'test'], ['cypress', 'run']):
            with self.subTest(argv=argv):
                self.assertNotIn('test list', self.notice(argv))
        self.assertNotIn('Vitest', self.notice(['ng', 'test', '--reporters=verbose']))   # only the Karma advice remains: the junit report
        self.assertIn('junit reporter', self.notice(['karma', 'start']))


class Forced(unittest.TestCase):
    def test_format_works_under_any_wrapper_and_for_opaque_scripts(self):
        for argv, want in DIRECT + WRAPPED:
            with self.subTest(argv=argv):
                self.assertEqual(test_adapters.resolve(want, argv, test_run.detect_tool(argv)).name, want)
        for argv in OPAQUE:
            for name in NAMES:
                with self.subTest(argv=argv, name=name):
                    self.assertEqual(test_adapters.resolve(name, argv, 'npm').name, name)

    def test_accepts_finds_the_command_behind_a_wrapper(self):
        for argv, want in WRAPPED:
            with self.subTest(argv=argv):
                self.assertTrue(test_adapters.REGISTRY[want]().accepts(argv))

    def test_no_other_adapter_takes_a_command_of_a_web_tool(self):
        for argv, want in DIRECT + WRAPPED:
            for name in NAMES:
                if name != want:
                    with self.subTest(argv=argv, other=name):
                        self.assertFalse(test_adapters.REGISTRY[name]().accepts(argv))


if __name__ == '__main__':
    unittest.main()
