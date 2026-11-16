"""180-J2: the classifier of the test commands of the guard hook (`library/hooks/guard-tests.py`): verdict per command, the table is the data of `test_tools.json`,
the interpreter of the hook agrees with `test_run.detect_tool`/`unwrap_command`, and the classifier never raises."""
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skillsync_workflow import test_run  # noqa: E402

HOOK = ROOT / 'library' / 'hooks' / 'guard-tests.py'
TABLE_FILE = ROOT / 'skillsync_workflow' / 'data' / 'test_tools.json'


def load_module():
    spec = importlib.util.spec_from_file_location('guard_tests_under_test', HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


G = load_module()
TABLE = G.load_table(TABLE_FILE)

# command -> verdict (wide warns; point/exempt/wrapper/none do not)
VERDICTS = [
    # unittest
    ('python3 -m unittest discover', 'wide'), ('python3 -m unittest discover -s tests -p "test_*.py"', 'wide'), ('python -m unittest', 'wide'),
    ('python3 -m unittest tests.test_a tests.test_b tests.test_c', 'point'), ('python3 -m unittest tests.test_a.Case.test_x', 'point'),
    ('python3 -m unittest -v discover -k name', 'point'), ('python3 -m unittest --help', 'exempt'),
    # pytest
    ('pytest', 'wide'), ('python -m pytest', 'wide'), ('python3 -m pytest tests', 'wide'), ('pytest -q', 'wide'), ('py.test', 'wide'),
    ('pytest tests/test_a.py', 'point'), ('pytest tests/test_a.py::TestX::test_y', 'point'), ('pytest -k login', 'point'), ('pytest --collect-only', 'exempt'),
    ('pytest --co -q', 'exempt'), ('pytest -p no:cacheprovider tests/test_a.py', 'point'), ('pytest -p no:cacheprovider', 'wide'),
    # dotnet
    ('dotnet test', 'wide'), ('dotnet test Solution.sln', 'wide'), ('dotnet vstest a.dll', 'wide'), ('dotnet test --filter "Name~x"', 'point'),
    ('dotnet test --list-tests', 'exempt'), ('dotnet test --no-build --filter Category=Fast', 'point'), ('dotnet build', 'none'), ('dotnet run', 'none'),
    # angular, vitest, jest
    ('ng test', 'wide'), ('npx ng test', 'wide'), ('ng test --include src/app/a.spec.ts', 'point'), ('ng build', 'none'),
    ('vitest', 'wide'), ('vitest run', 'wide'), ('npx vitest run', 'wide'), ('vitest run src/a.test.ts', 'point'), ('npx vitest run -t "name"', 'point'),
    ('jest', 'wide'), ('npx jest src/a.test.ts', 'point'), ('npx jest -t name', 'point'), ('jest --listTests', 'exempt'),
    # cargo
    ('cargo test', 'wide'), ('cargo test --workspace', 'wide'), ('cargo test parser', 'point'), ('cargo test -p core', 'point'), ('cargo test --test api', 'point'),
    ('cargo test -- --list', 'exempt'), ('cargo +nightly test', 'wide'), ('cargo nextest run', 'wide'), ('cargo nextest run parser', 'point'), ('cargo nextest list', 'exempt'),
    ('cargo build', 'none'),
    # playwright, cypress
    ('playwright test', 'wide'), ('npx playwright test', 'wide'), ('npx playwright test e2e/login.spec.ts', 'point'), ('npx playwright test -g "login"', 'point'),
    ('npx playwright test --project=chromium', 'point'), ('playwright test --list', 'exempt'), ('cypress run', 'wide'), ('npx cypress run --spec cypress/e2e/a.cy.ts', 'point'),
    # go, make, npm
    ('go test ./...', 'wide'), ('go test ./pkg/parser', 'point'), ('go test -run TestX ./...', 'point'), ('go test', 'point'), ('go build ./...', 'none'),
    ('make test', 'wide'), ('make check', 'wide'), ('make build', 'none'), ('npm test', 'wide'), ('npm run test', 'wide'), ('pnpm test', 'wide'), ('yarn test', 'wide'),
    ('npm run build', 'none'), ('npm install', 'none'),
    # wrappers and opaque wrappers
    ('timeout 600 python3 -m unittest discover', 'wide'), ('env X=1 pytest', 'wide'), ('nice -n 10 pytest tests/test_a.py', 'point'),
    ('skillsync test-run . -- python3 -m unittest discover', 'wrapper'), ('skillsync test-run . --step F1.1 -- dotnet test', 'wrapper'),
    ('skillsync validate . --run r1', 'wrapper'), ('skillsync smart-test . --run r1', 'wrapper'),
    ('flock /run/user/1000/heavy-run.lock systemd-run --user --scope python3 -m unittest discover', 'wrapper'),
    ('systemd-run --user --scope pytest', 'wrapper'),
    # not tests at all
    ('ls -la', 'none'), ('git status', 'none'), ('python3 scripts/check_skips.py run.log', 'none'), ('python3 -m py_compile a.py', 'none'), ('echo pytest', 'none'),
    ('grep -rn unittest docs', 'none'),
]


class Verdicts(unittest.TestCase):
    def test_the_table_of_commands(self):
        import shlex
        for command, expected in VERDICTS:
            with self.subTest(command=command):
                self.assertEqual(G.classify(shlex.split(command), TABLE)['verdict'], expected)

    def test_the_wide_threshold_of_unittest_modules(self):
        modules = [f'tests.test_m{i}' for i in range(30)]
        self.assertEqual(G.classify(['python3', '-m', 'unittest', *modules], TABLE)['verdict'], 'wide')
        self.assertEqual(G.classify(['python3', '-m', 'unittest', *modules[:24]], TABLE)['verdict'], 'point')
        self.assertEqual(G.classify(['python3', '-m', 'unittest', *modules[:24]], TABLE, wide_modules=10)['verdict'], 'wide')

    def test_a_wrapper_of_the_manifest_is_a_wrapper(self):
        words = ['my-runner', 'run', 'pytest']
        self.assertEqual(G.classify(words, TABLE)['verdict'], 'none')
        self.assertEqual(G.classify(words, TABLE, extra_wrappers=[('my-runner', 'run')])['verdict'], 'wrapper')

    def test_npm_and_make_are_probable_not_certain(self):
        self.assertTrue(G.classify(['npm', 'test'], TABLE)['probably'])
        self.assertTrue(G.classify(['make', 'test'], TABLE)['probably'])
        self.assertFalse(G.classify(['pytest'], TABLE)['probably'])

    def test_windows_names_and_paths(self):
        self.assertEqual(G.classify([r'C:\Python\python.exe', '-m', 'pytest'], TABLE)['verdict'], 'wide')
        self.assertEqual(G.classify(['dotnet.exe', 'test'], TABLE)['verdict'], 'wide')
        self.assertEqual(G.classify(['npx.cmd', 'vitest', 'run'], TABLE)['verdict'], 'wide')

    def test_the_warning_is_plain_sentences_about_facts(self):
        text = G.warning_text('pytest', G.classify(['pytest'], TABLE), '/work/proj')
        self.assertIn('skillsync test-run /work/proj -- <the same command>', text)
        self.assertIn('not evidence', text)
        self.assertIn('after the user agrees', text)
        self.assertEqual(text, text.encode('ascii', 'ignore').decode(), 'ASCII only')
        self.assertLess(len(text), 10000)
        self.assertFalse(any(word.isupper() and len(word) > 3 and word.isalpha() for word in text.split()), 'no shouting')
        self.assertNotIn('Barci', text)


class AgreesWithTheCore(unittest.TestCase):
    COMMANDS = [command for command, _ in VERDICTS] + [
        'npx --yes vitest run', 'pnpm exec jest', 'yarn playwright test', 'uv run pytest', 'python3 -m pytest -x', 'time pytest', 'timeout -k 5 100 cargo test',
        'env FOO=1 BAR=2 dotnet test', 'systemd-run --user --scope go test ./...', 'nice -10 jest', 'npm exec -- vitest', 'a b c d e f g h i', '']

    def test_the_interpreter_of_the_hook_is_the_interpreter_of_the_core(self):
        import shlex
        for command in self.COMMANDS:
            words = shlex.split(command)
            with self.subTest(command=command):
                self.assertEqual(G.tool_of(words, TABLE), test_run.detect_tool(words))
                self.assertEqual(G.unwrap(words, TABLE), test_run.unwrap_command(words) if words else words)

    def test_the_table_is_read_from_the_same_file(self):
        data = json.loads(TABLE_FILE.read_text(encoding='utf-8'))
        self.assertEqual(len(TABLE['tools']), len(data['tools']))
        self.assertEqual(set(TABLE['wrappers']), {item['word'] for item in data['wrappers']})
        self.assertEqual(TABLE['layers'], data['max_layers'])
        self.assertEqual(TABLE['tools'], test_run.tool_table()['tools'])

    def test_every_tool_that_the_hook_has_a_rule_for_is_in_the_table(self):
        known = {tool for _, tool in TABLE['tools']} | set(TABLE['modules'].values())
        self.assertFalse(set(G.RULES) - known, 'a rule for a tool that the table does not know can never fire')


class NeverRaises(unittest.TestCase):
    def test_a_broken_or_missing_table_gives_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test-tools.json'
            self.assertIsNone(G.load_table(path))
            for text in ('', '{', '[]', '{"schema": 2}', '{"schema": 1}', '{"schema": 1, "tools": 3}', json.dumps({'schema': 1, 'tools': [{'word': 'x'}], 'module_words': {}, 'wrappers': [], 'max_layers': 6})):
                path.write_text(text, encoding='utf-8')
                self.assertIsNone(G.load_table(path), text)

    def test_odd_input_is_no_warning_and_no_exception(self):
        for words in ([], [''], ['--'], [None], [1, 2], ['pytest', None], ['"'], ['python3', '-m'], ['env'], ['timeout'], ['npx'], ['x'] * 50):
            with self.subTest(words=words):
                self.assertIn(G.classify(words, TABLE)['verdict'], ('none', 'wide', 'point', 'exempt', 'wrapper'))
        self.assertEqual(G.classify(['pytest'], None)['verdict'], 'none')   # no table: the hook of tests is silent

    def test_it_is_fast(self):
        start = time.perf_counter()
        for _ in range(500):
            G.classify(['npx', 'vitest', 'run', 'src/a.test.ts'], TABLE)
            G.classify(['python3', '-m', 'unittest', 'discover'], TABLE)
        self.assertLess((time.perf_counter() - start) / 1000, 0.002, 'a classification takes about a millisecond or less')


if __name__ == '__main__':
    unittest.main()
