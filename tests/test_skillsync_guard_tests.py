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
    ('flock /tmp/x.lock pytest', 'none'), ('skillsync validate .', 'none'), ('skillsync smart-test . --run=r1', 'wrapper'),
    ('npm test -- one.test.js', 'point'), ('npm --prefix web test', 'wide'), ('make -C build test', 'wide'), ('make -j 4 check', 'wide'), ('make -j 4', 'none'),
    ('jest --testPathPattern src/a', 'point'), ('pytest --cov tests/a.py', 'point'), ('go test -coverpkg ./... ./pkg', 'point'), ('cargo test --message-format json parser', 'point'),
    # not tests at all
    ('ls -la', 'none'), ('git status', 'none'), ('python3 scripts/check_skips.py run.log', 'none'), ('python3 -m py_compile a.py', 'none'), ('echo pytest', 'none'),
    ('grep -rn unittest docs', 'none'),
    # `-m` behind a Python host, the new wrappers, `env -S`
    ('python -W error -m unittest tests', 'point'), ('python -W error -m unittest', 'wide'), ('python3.12 -m pytest', 'wide'), ('python3.12 -m pytest tests/test_a.py', 'point'),
    ('poetry run pytest', 'wide'), ('pdm run pytest tests/test_a.py', 'point'), ('pipenv run pytest', 'wide'), ('poetry run python -m unittest discover', 'wide'),
    ("env -S 'CI=1 npx jest'", 'wide'), ("env -S 'CI=1 npx jest src/a.test.ts'", 'point'), ("env -S 'echo $HOME'", 'none'), ('python -m coverage run -m pytest', 'wide'),
    ('env CI=1 python -S -m pytest', 'wide'), ('env A=1 npx jest -Sx', 'wide'), ('git commit -m pytest', 'none'), ('echo python -m pytest', 'none'), ('nohup python -m unittest', 'none'), ('tox -e py311', 'none'),
    ('git commit -m unittest', 'none'), ('mypy -m pytest', 'none'),
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

    def test_a_prefix_of_the_manifest_is_normalised_like_the_command(self):
        words = ['/opt/Tools/Runner.EXE', 'run', 'pytest']
        self.assertEqual(G.is_wrapper(words, [('/opt/Tools/Runner.exe', 'run')]), True)
        self.assertEqual(G.is_wrapper(['devbox', 'run', './scripts/test.sh'], [('devbox', 'run', './scripts/test.sh')]), True)

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


class AgreesWithTheCore(unittest.TestCase):
    COMMANDS = [command for command, _ in VERDICTS] + [
        'npx --yes vitest run', 'pnpm exec jest', 'yarn playwright test', 'uv run pytest', 'python3 -m pytest -x', 'time pytest', 'timeout -k 5 100 cargo test',
        'env FOO=1 BAR=2 dotnet test', 'systemd-run --user --scope go test ./...', 'nice -10 jest', 'npm exec -- vitest', 'a b c d e f g h i', '',
        'go test ./unittest', 'echo unittest', 'pytest -m unittest', 'git commit -m unittest', 'git commit -m pytest', 'python -W error -m unittest', 'python3.12 -m pytest',
        'uv run python -m pytest', 'nohup python -m unittest', 'python -m coverage run -m pytest', 'coverage run -m unittest', 'uv run --extra test pytest',
        'uv run --group dev pytest', 'poetry -C sub run pytest', 'docker compose run --rm app pytest', 'sudo -u ci pytest', 'nohup timeout 600 pytest',
        'poetry run pytest -k x', 'pdm run pytest', 'pipenv run pytest', 'poetry run python -m unittest', 'poetry --unknown-flag run pytest', 'tox -e py311',
        "env -S 'CI=1 npx jest'", "env -S'CI=1 npx jest'", "env --split-string='CI=1 npx jest'", "env -S 'CI=1 npx' jest", "env -i -S 'CI=1 npx jest'",
        "env -S 'echo $HOME'", "env -S 'echo \"abc'", 'env -S', 'env CI=1 python -S -m pytest', 'env A=1 npx jest -Sx', 'env A=1 npx jest -S x', 'pypy3 -m pytest', 'py -3 -m pytest', 'mypy -m pytest']

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


class PreviousReleaseTable(unittest.TestCase):
    """The new interpreter of the hook reads the table of the previous release (the library may be newer than a project's installed core)."""
    OLD = G.load_table(ROOT / 'tests' / 'fixtures' / 'test_tools_previous_release.json')
    NEW_WRAPPERS = ('poetry', 'pdm', 'pipenv', 'tox', 'env -S')

    def test_the_same_verdicts_as_with_the_current_table(self):
        import shlex
        self.assertIsNotNone(self.OLD)
        for command, expected in VERDICTS:
            if any(word in command for word in self.NEW_WRAPPERS):
                continue
            with self.subTest(command=command):
                self.assertEqual(G.classify(shlex.split(command), self.OLD)['verdict'], expected)
                self.assertEqual(G.tool_of(shlex.split(command), self.OLD), G.tool_of(shlex.split(command), TABLE))

    def test_new_wrappers_do_not_raise_and_give_the_old_answer(self):
        import shlex
        for command in ('poetry run pytest', 'pdm run pytest', 'pipenv run pytest', 'tox -e py311', "env -S 'CI=1 npx jest'", 'poetry --unknown-flag run pytest'):
            with self.subTest(command=command):
                words = shlex.split(command)
                self.assertEqual(G.classify(words, self.OLD)['verdict'], 'none')
                G.tool_of(words, self.OLD)
                G.unwrap(words, self.OLD)


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
