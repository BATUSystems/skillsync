"""Contract of the adapters of `test-run` (180-A5): the tool behind wrappers, reachability of the adapters, the counters of Karma.

Written before the code (red until the 180-A5 implementation): the table of `detect_tool`, the Karma counter that takes the LAST `Executed N of M`.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import quality, test_adapters, test_run  # noqa: E402

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
    ('systemd-run --user --scope -p MemoryMax=3G -- cargo test', 'cargo'),
    ('systemd-run -p MemoryMax=3G cargo test', 'cargo'),
    # opaque scripts: no guessing
    ('npm test', 'npm'),
    ('npm run test:e2e', 'npm'),
    ('make test', 'unknown'),
    # an unknown wrapper or an unknown flag of a known one: as before (the window of three words, then unknown)
    ('timeout --unknown-flag 5 cargo test', 'unknown'),
    ('unknown-wrapper cargo test', 'unknown'),
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
    def test_every_tool_of_every_adapter_is_a_value_of_the_table(self):
        known = {tool for _, tool in test_run.TOOLS} | {'unittest', 'pytest'}
        test_adapters.discover()
        for name, cls in sorted(test_adapters.REGISTRY.items()):
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


if __name__ == '__main__':
    unittest.main()
