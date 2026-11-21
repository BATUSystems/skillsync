"""CLAUDE-7 / F2.1: the status line script `library/hooks/statusline.py` (spec K1-K4).

The script is started as a subprocess (`sys.executable <script>`) with a JSON payload on stdin (UTF-8 bytes) and a controlled
environment (HOME in a temporary directory, NO_COLOR, COLUMNS). It must print exactly one UTF-8 line, always exit 0 and never
start a process or open a socket. The script is taken from `$SKILLSYNC_TEST_LIBRARY/hooks/statusline.py` (default: `library/`).

What the spec fixes is asserted; what it leaves open (separators, order of sections, which plan name is shown) is not.
The negative assertions (no processes, no sockets, files not opened) go through `fixtures/statusline/guard_wrapper.py`; its own
tests below show that it records a blocked call and an open, so a green run is not an empty one.
"""
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tmp_paths import child, descend  # noqa: E402
from layers_helpers import IsolatedTestCase, git  # noqa: E402
from skillsync_workflow import status as plan_status  # noqa: E402
from skillsync_workflow.common import decode_text  # noqa: E402

LIBRARY = Path(os.environ.get('SKILLSYNC_TEST_LIBRARY') or ROOT / 'library')
SCRIPT = LIBRARY / 'hooks' / 'statusline.py'
WRAPPER = Path(__file__).resolve().parent / 'fixtures' / 'statusline' / 'guard_wrapper.py'
SGR = re.compile(r'\x1b\[[0-9;]*m')
STEPS = re.compile(r'(?<![\w/])(\d+)/(\d+)(?![\w/])')
MAX_PLAN_BYTES = 512 * 1024
SHA = 'a1b2c3d4e5f60718293a4b5c6d7e8f9012345678'

PROGRESS_2_OF_5 = ('### Faza 1\n- [x] F1.1 pierwszy\n- [X] F1.2 drugi\n### Faza 2\n- [ ] F2.1 trzeci\n- [ ] F2.2 czwarty\n- [ ] F2.3 piaty\n')
PROGRESS_1_OF_3 = '- [x] A1 jeden\n- [ ] A2 dwa\n- [ ] A3 trzy\n'
PROGRESS_4_OF_6 = '- [x] B1 a\n- [x] B2 b\n- [x] B3 c\n- [x] B4 d\n- [ ] B5 e\n- [ ] B6 f\n'


def plan_text(status_line='Status: in_progress', progress=PROGRESS_2_OF_5):
    """A plan: header with the status line, a checklist OUTSIDE `## Progress` (never counted), the Progress section, a later section with a checklist."""
    text = f'# Plan: probny\n\n{status_line}\nSpec: docs/specs/x.md\n\n## Kryteria\n\n- [x] kryterium poza Progress\n- [ ] drugie poza Progress\n\n'
    if progress is not None:
        text += f'## Progress\nopis pola\n\n{progress}\n'
    return text + '\n## Wynik\n\n- [ ] punkt po Progress\n'


def visible(text):
    return SGR.sub('', text)


def forbidden_characters(text):
    """Control (Cc), format (Cf, bidi marks included) and line/paragraph separator characters."""
    return [c for c in text if unicodedata.category(c) in ('Cc', 'Cf', 'Zl', 'Zp')]


class Result:
    def __init__(self, process, log):
        self.rc = process.returncode
        self.out = process.stdout
        self.err = process.stderr
        self.log = log
        self.text = process.stdout.decode('utf-8')   # strict: invalid UTF-8 on stdout is a failure of the test
        self.line = self.text[:-1] if self.text.endswith('\n') else self.text


class StatuslineCase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.home = child(self.work, 'home')
        self.home.mkdir()
        self.neutral = child(self.work, 'neutral')
        self.neutral.mkdir()

    # --- helpers -----------------------------------------------------------------------------------------------------
    def child_env(self, **extra):
        base = {name: os.environ[name] for name in ('PATH', 'SYSTEMROOT', 'TEMP', 'TMP') if name in os.environ}
        base.update({'HOME': str(self.home), 'USERPROFILE': str(self.home)})
        base.update({name: value for name, value in extra.items() if value is not None})
        return base

    def run_script(self, payload, cwd=None, wrapped=False, fixture=None, **env):
        """Run the script; `payload` is a dict (JSON, UTF-8) or raw bytes. `wrapped` runs it through the guard wrapper."""
        self.assertTrue(SCRIPT.is_file(), f'the status line script is missing: {SCRIPT}')
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        command = [sys.executable, str(SCRIPT)]
        extra = {}
        log_path = child(self.work, 'wrapper-log.json')
        if wrapped:
            command = [sys.executable, str(WRAPPER), str(SCRIPT)]
            extra = {'SL_ROOT': str(fixture), 'SL_LOG': str(log_path)}
        process = subprocess.run(command, input=data, capture_output=True, cwd=str(cwd or self.neutral),
                                 env=self.child_env(**extra, **env), timeout=60)
        log = json.loads(log_path.read_text(encoding='utf-8')) if wrapped and log_path.exists() else None
        return Result(process, log)

    def put(self, base, relative, data, mtime=None):
        path = descend(base, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def project(self, name='proj', head='ref: refs/heads/main', mode=None, manifest=None):
        """A project directory with a plain `.git` directory (HEAD set), optionally a manifest with `delivery.mode`."""
        base = child(self.work, name)
        self.put(base, '.git/HEAD', head + '\n')
        if manifest is not None:
            self.put(base, '.github/.skillsync.yaml', manifest)
        elif mode is not None:
            self.put(base, '.github/.skillsync.yaml', f'project: {name}\ndelivery:\n  mode: {mode}\n')
        return base

    def linked_worktree(self, name='wtlink', gitdir_name=None, head='ref: refs/heads/feat/linked', relative=False):
        """A `git worktree add` layout built by hand: <main>/.git/worktrees/<gitdir_name> and <name>/.git with `gitdir: ...`."""
        main = child(self.work, f'{name}-main')
        gitdir_name = gitdir_name or name
        admin = Path(os.path.join(str(main), '.git', 'worktrees', gitdir_name))   # the name may be hostile on purpose (raises OSError where the system refuses it)
        admin.mkdir(parents=True)
        (admin / 'HEAD').write_bytes((head + '\n').encode('utf-8'))
        (admin / 'commondir').write_bytes(b'../..\n')
        worktree = child(self.work, name)
        target = os.path.relpath(admin, worktree) if relative else str(admin)
        self.put(worktree, '.git', f'gitdir: {target}\n')
        return worktree

    def assert_one_line(self, result):
        self.assertEqual(result.rc, 0, result.err)
        self.assertLessEqual(result.text.count('\n'), 1, result.text)
        if result.text.count('\n') == 1:
            self.assertTrue(result.text.endswith('\n'), result.text)
        self.assertEqual(len(result.line.splitlines()), 1 if result.line else 0)

    def assert_clean(self, result, colour=False):
        """One line, no control/format/separator characters; ANSI colour (SGR) only when `colour` allows it."""
        self.assert_one_line(result)
        text = visible(result.line) if colour else result.line
        self.assertEqual(forbidden_characters(text), [], repr(result.line))


class FailOpen(StatuslineCase):
    """K1: every error ends with exit 0 and an empty line."""

    def test_script_exists(self):
        self.assertTrue(SCRIPT.is_file(), f'the status line script is missing: {SCRIPT}')

    def test_bad_input_gives_exit_zero_and_empty_line(self):
        cases = {'empty stdin': b'', 'not json': b'not json at all', 'truncated json': b'{"session_name": "x"',
                 'json list': b'[]', 'json null': b'null', 'json string': b'"text"', 'json number': b'42',
                 'invalid utf-8': b'\xff\xfe\xfa{"x": 1}', 'only a BOM and a brace': b'\xef\xbb\xbf{',
                 'deep nesting': b'[' * 200000, 'no fields': b'{}', 'NUL bytes': b'\x00\x00\x00'}
        for label, payload in cases.items():
            with self.subTest(label):
                result = self.run_script(payload)
                self.assertEqual(result.rc, 0, result.err)
                self.assertEqual(result.line.strip(), '', result.text)
                self.assertLessEqual(result.text.count('\n'), 1)

    def test_wrong_field_types_do_not_crash(self):
        payloads = [{'session_name': 5}, {'session_name': ['a']}, {'session_name': None}, {'session_name': {'a': 1}},
                    {'workspace': 'text'}, {'workspace': ['a']}, {'workspace': {'project_dir': 5, 'git_worktree': {'x': 1}}},
                    {'worktree': 'text'}, {'worktree': {'branch': 5, 'name': ['n']}}, {'cwd': 7}, {'cwd': None},
                    {'cwd': ['a']}, {'session_name': True, 'workspace': {'project_dir': False}}]
        for payload in payloads:
            with self.subTest(payload):
                result = self.run_script(payload)
                self.assert_one_line(result)

    def test_missing_paths_do_not_crash(self):
        missing = str(child(self.work, 'does-not-exist'))
        a_file = self.put(self.work, 'a-file.txt', 'x')
        for payload in ({'cwd': missing}, {'workspace': {'project_dir': missing}}, {'cwd': str(a_file)},
                        {'workspace': {'project_dir': str(a_file)}}):
            with self.subTest(payload):
                result = self.run_script(payload)
                self.assert_one_line(result)

    def test_hostile_filesystem_does_not_crash(self):
        """HEAD, the manifest, a plan and `docs/plans` that are directories or garbage: exit 0, still one line."""
        base = child(self.work, 'proj-hostile')
        (base / '.git' / 'HEAD').mkdir(parents=True)
        (base / '.github' / '.skillsync.yaml').mkdir(parents=True)
        (base / 'docs' / 'plans' / 'dir.md').mkdir(parents=True)
        self.put(base, 'docs/plans/binary.md', b'\xff\xfe\x00\x01Status: in_progress\n\x80\x81')
        self.put(base, 'docs/plans/empty.md', '')
        result = self.run_script({'session_name': 'Alfa-1', 'workspace': {'project_dir': str(base)}})
        self.assert_one_line(result)
        self.assertIn('sesja: Alfa-1', result.line)
        garbage = child(self.work, 'proj-garbage')
        self.put(garbage, '.git', b'\x00\x01\x02 not a gitdir line')
        self.put(garbage, '.github/.skillsync.yaml', b'\xff\xfe: : :\n\x00')
        self.assert_one_line(self.run_script({'workspace': {'project_dir': str(garbage)}}))


class OutputShape(StatuslineCase):
    """K1: one UTF-8 line, at most min(120, COLUMNS) characters, no ANSI under NO_COLOR."""

    def rich(self, session='Alfa-1'):
        base = self.project('proj', head='ref: refs/heads/feat/x', mode='direct')
        self.put(base, 'docs/plans/2026-11-21-a.md', plan_text())
        return base, {'session_name': session, 'workspace': {'project_dir': str(base)}, 'cwd': str(base)}

    def test_one_utf8_line_whatever_the_locale(self):
        """The output is UTF-8 bytes even when Python's stdio encoding is Latin-1 (the script must use the binary streams)."""
        base, payload = self.rich(session='Żółw-ę 日本語')
        result = self.run_script(payload, PYTHONIOENCODING='latin-1', NO_COLOR='1')
        self.assert_one_line(result)
        self.assertIn('sesja: Żółw-ę 日本語', result.line)
        self.assertIn('Żółw-ę 日本語'.encode('utf-8'), result.out)

    def test_rich_line_has_all_sections(self):
        base, payload = self.rich()
        result = self.run_script(payload, NO_COLOR='1')
        self.assert_one_line(result)
        line = result.line
        self.assertIn('sesja: Alfa-1', line)
        self.assertIn('proj', line)
        self.assertIn('feat/x', line)
        self.assertRegex(line, r'(?<![\w/-])direct(?![\w/-])')
        self.assertRegex(line, r'(?<![\w/])2/5(?![\w/])')
        self.assertLessEqual(len(line), 120)

    def test_no_color_means_no_escape_characters(self):
        base, payload = self.rich()
        result = self.run_script(payload, NO_COLOR='1')
        self.assertNotIn('\x1b', result.text)
        self.assertEqual(forbidden_characters(result.line), [])

    def test_colour_if_any_is_plain_sgr(self):
        """Without NO_COLOR the script may colour (K1 allows it); nothing but SGR sequences may reach the terminal."""
        base, payload = self.rich()
        result = self.run_script(payload)
        self.assert_clean(result, colour=True)
        self.assertIn('sesja: Alfa-1', visible(result.line))

    def test_line_never_longer_than_120_without_columns(self):
        base, payload = self.rich(session='S' * 500)
        base2 = self.project('p' * 200, head='ref: refs/heads/' + 'b' * 300)
        for label, data in (('long session', payload),
                            ('long project and branch', {'session_name': 'ok', 'workspace': {'project_dir': str(base2)}}),
                            ('long worktree fields', {'session_name': 'n' * 300, 'worktree': {'name': 'w' * 300, 'branch': 'b' * 300},
                                                      'workspace': {'project_dir': str(base)}})):
            with self.subTest(label):
                result = self.run_script(data, NO_COLOR='1')
                self.assert_one_line(result)
                self.assertTrue(result.line)
                self.assertLessEqual(len(result.line), 120)

    def test_columns_limits_the_line(self):
        base, payload = self.rich(session='S' * 200)
        for columns, limit in (('30', 30), ('80', 80), ('119', 119), ('120', 120), ('500', 120), ('10000', 120)):
            with self.subTest(columns):
                result = self.run_script(payload, NO_COLOR='1', COLUMNS=columns)
                self.assert_one_line(result)
                self.assertTrue(result.line)
                self.assertLessEqual(len(result.line), limit)

    def test_invalid_columns_fall_back_to_120(self):
        base, payload = self.rich(session='S' * 200)
        for columns in ('abc', '', '0', '-5', '1e3', ' '):
            with self.subTest(columns):
                result = self.run_script(payload, NO_COLOR='1', COLUMNS=columns)
                self.assert_one_line(result)
                self.assertLessEqual(len(result.line), 120)

    def test_truncation_never_splits_a_character(self):
        """Wide non-ASCII text cut to COLUMNS is still valid UTF-8 and has no half character or stray surrogate."""
        payload = {'session_name': 'żółć' * 100}
        result = self.run_script(payload, NO_COLOR='1', COLUMNS='25')
        self.assert_one_line(result)
        self.assertLessEqual(len(result.line), 25)
        self.assertEqual(result.out.decode('utf-8'), result.text)

    def test_colour_does_not_count_against_the_limit_for_plain_text(self):
        """The visible width (SGR removed) stays within COLUMNS even when the script colours."""
        base, payload = self.rich(session='S' * 200)
        result = self.run_script(payload, COLUMNS='40')
        self.assert_clean(result, colour=True)
        self.assertLessEqual(len(visible(result.line)), 40)


class Sections(StatuslineCase):
    """K2: session, project directory, branch and worktree, delivery mode."""

    def test_session_section(self):
        for name, expected in (('Alfa-1', 'sesja: Alfa-1'), ('  spacje  ', 'sesja: spacje')):
            with self.subTest(name):
                result = self.run_script({'session_name': name}, NO_COLOR='1')
                self.assertIn(expected, result.line)
        for label, payload in (('absent', {}), ('null', {'session_name': None}), ('empty', {'session_name': ''}),
                               ('blank', {'session_name': '   '})):
            with self.subTest(label):
                result = self.run_script(payload, NO_COLOR='1')
                self.assertNotIn('sesja:', result.line)

    def test_session_label_makes_no_identity_claim(self):
        result = self.run_script({'session_name': 'Beta-2'}, NO_COLOR='1')
        self.assertIn('sesja: Beta-2', result.line)
        for word in ('karta', 'alias', 'tozsamosc'):
            self.assertNotIn(word, result.line)

    def test_project_dir_from_workspace(self):
        base = self.project('projalpha')
        result = self.run_script({'workspace': {'project_dir': str(base)}}, NO_COLOR='1')
        self.assertIn('projalpha', result.line)

    def test_project_dir_wins_over_cwd(self):
        alpha = self.project('projalpha')
        beta = self.project('projbeta')
        result = self.run_script({'workspace': {'project_dir': str(alpha)}, 'cwd': str(beta)}, NO_COLOR='1')
        self.assertIn('projalpha', result.line)
        self.assertNotIn('projbeta', result.line)

    def test_project_found_upwards_from_a_subdirectory(self):
        base = self.project('projup', head='ref: refs/heads/feat/up', mode='pr')
        self.put(base, 'docs/plans/p.md', plan_text(progress=PROGRESS_4_OF_6))
        deep = descend(base, 'src/pkg/inner')
        deep.mkdir(parents=True)
        result = self.run_script({'cwd': str(deep)}, NO_COLOR='1')
        self.assert_one_line(result)
        self.assertIn('projup', result.line)
        self.assertNotIn('inner', result.line)
        self.assertIn('feat/up', result.line)
        self.assertRegex(result.line, r'(?<![\w/-])pr(?![\w/-])')
        self.assertRegex(result.line, r'(?<![\w/])4/6(?![\w/])')

    def test_branch_from_plain_repository_head(self):
        base = self.project('projbr', head='ref: refs/heads/feat/plain')
        result = self.run_script({'workspace': {'project_dir': str(base)}}, NO_COLOR='1')
        self.assertIn('feat/plain', result.line)

    def test_branch_and_worktree_from_stdin_fields_win_over_files(self):
        base = self.project('projwt', head='ref: refs/heads/from-files')
        payload = {'workspace': {'project_dir': str(base)},
                   'worktree': {'name': 'wt-stdin', 'path': str(base), 'branch': 'feat/stdin',
                                'original_cwd': str(base), 'original_branch': 'main'}}
        result = self.run_script(payload, NO_COLOR='1')
        self.assertIn('feat/stdin', result.line)
        self.assertIn('wt-stdin', result.line)
        self.assertNotIn('from-files', result.line)

    def test_linked_worktree_with_dot_git_file(self):
        """A worktree made by `git worktree add`: `.git` is a FILE `gitdir: <main>/.git/worktrees/<name>`; HEAD lives in that directory."""
        worktree = self.linked_worktree('wtlinked', head='ref: refs/heads/feat/linked')
        result = self.run_script({'workspace': {'project_dir': str(worktree)}}, NO_COLOR='1')
        self.assert_one_line(result)
        self.assertIn('feat/linked', result.line)
        self.assertIn('wtlinked', result.line)

    def test_worktree_name_comes_from_the_gitdir_directory_not_the_checkout_directory(self):
        worktree = self.linked_worktree('checkoutdir', gitdir_name='admin-name', head='ref: refs/heads/feat/names')
        result = self.run_script({'workspace': {'project_dir': str(worktree)}}, NO_COLOR='1')
        self.assertIn('admin-name', result.line)
        self.assertIn('feat/names', result.line)

    def test_git_worktree_field_names_the_worktree(self):
        worktree = self.linked_worktree('checkoutdir2', gitdir_name='admin-two', head='ref: refs/heads/feat/two')
        payload = {'workspace': {'project_dir': str(worktree), 'git_worktree': 'from-claude'}}
        result = self.run_script(payload, NO_COLOR='1')
        self.assertIn('from-claude', result.line)
        self.assertIn('feat/two', result.line)

    def test_linked_worktree_with_relative_gitdir(self):
        worktree = self.linked_worktree('wtrel', head='ref: refs/heads/feat/rel', relative=True)
        result = self.run_script({'workspace': {'project_dir': str(worktree)}}, NO_COLOR='1')
        self.assertIn('feat/rel', result.line)
        self.assertIn('wtrel', result.line)

    def test_linked_worktree_found_from_a_subdirectory(self):
        worktree = self.linked_worktree('wtsub', head='ref: refs/heads/feat/sub')
        deep = descend(worktree, 'a/b')
        deep.mkdir(parents=True)
        result = self.run_script({'cwd': str(deep)}, NO_COLOR='1')
        self.assertIn('feat/sub', result.line)
        self.assertIn('wtsub', result.line)

    def test_real_git_worktree(self):
        """The same with a repository and a worktree made by git itself (git is used by the fixture, never by the script)."""
        repo = self.make_repo('realmain')
        git(repo, 'commit', '--allow-empty', '-m', 'init')
        other = child(self.work, 'wt-real')
        git(repo, 'worktree', 'add', '-b', 'feat/real', str(other))
        wrapped = self.run_script({'workspace': {'project_dir': str(other)}}, wrapped=True, fixture=self.work, NO_COLOR='1')
        self.assertEqual(wrapped.log['blocked'], [])
        self.assertIn('feat/real', wrapped.line)
        self.assertIn('wt-real', wrapped.line)
        main = self.run_script({'workspace': {'project_dir': str(repo)}}, NO_COLOR='1')
        self.assertRegex(main.line, r'\bmain\b')

    def test_detached_head_shows_short_sha(self):
        base = self.project('projdet', head=SHA)
        result = self.run_script({'workspace': {'project_dir': str(base)}}, NO_COLOR='1')
        self.assert_one_line(result)
        self.assertIn(SHA[:7], result.line)
        self.assertNotIn(SHA, result.line)

    def test_detached_head_in_a_linked_worktree(self):
        worktree = self.linked_worktree('wtdet', head=SHA)
        result = self.run_script({'workspace': {'project_dir': str(worktree)}}, NO_COLOR='1')
        self.assertIn(SHA[:7], result.line)
        self.assertNotIn(SHA, result.line)
        self.assertIn('wtdet', result.line)

    def test_unreadable_git_data_only_drops_the_section(self):
        for label, head in (('empty HEAD', ''), ('garbage HEAD', '\x00\x01 garbage'), ('ref without branch', 'ref: ')):
            with self.subTest(label):
                base = self.project(f'projgit', head=head)
                result = self.run_script({'session_name': 'Alfa-1', 'workspace': {'project_dir': str(base)}}, NO_COLOR='1')
                self.assert_one_line(result)
                self.assertIn('sesja: Alfa-1', result.line)
        missing = self.linked_worktree('wtmissing')
        os.remove(next(Path(str(self.work)).glob('wtmissing-main/.git/worktrees/*/HEAD')))
        result = self.run_script({'session_name': 'Alfa-1', 'workspace': {'project_dir': str(missing)}}, NO_COLOR='1')
        self.assert_one_line(result)
        self.assertIn('sesja: Alfa-1', result.line)

    # --- delivery mode -----------------------------------------------------------------------------------------------
    def mode_in(self, manifest, **kwargs):
        base = self.project(f'projmode', manifest=manifest, **kwargs)
        return self.run_script({'workspace': {'project_dir': str(base)}}, NO_COLOR='1')

    def assert_mode(self, result, shown, hidden):
        self.assert_one_line(result)
        self.assertRegex(result.line, rf'(?<![\w/-]){shown}(?![\w/-])')
        self.assertNotRegex(result.line, rf'(?<![\w/-]){hidden}(?![\w/-])')

    def test_delivery_mode_values(self):
        cases = {'direct': 'project: x\ndelivery:\n  mode: direct\n',
                 'pr': 'project: x\ndelivery:\n  mode: pr\n  base: main\n',
                 'quoted': 'delivery:\n  mode: "direct"\n',
                 'single quoted': "delivery:\n  mode: 'direct'\n",
                 'comment': 'delivery:\n  mode: direct   # trailing comment\n',
                 'crlf': 'project: x\r\ndelivery:\r\n  mode: direct\r\n',
                 'after other keys': 'project: x\nlayers: [a]\nclaude: false\ndelivery:\n  base: main\n  mode: direct\n'}
        for label, manifest in cases.items():
            with self.subTest(label):
                shown = 'pr' if label == 'pr' else 'direct'
                self.assert_mode(self.mode_in(manifest), shown, 'pr' if shown == 'direct' else 'direct')

    def test_mode_of_another_section_is_not_the_delivery_mode(self):
        manifest = 'project: x\nother:\n  mode: direct\ndelivery:\n  mode: pr\n'
        self.assert_mode(self.mode_in(manifest), 'pr', 'direct')

    def test_no_delivery_mode_means_no_section(self):
        for label, manifest in (('no delivery', 'project: x\n'), ('delivery without mode', 'delivery:\n  base: main\n'),
                                ('mode outside delivery', 'mode: direct\nproject: x\n'), ('empty', ''),
                                ('unknown value', 'delivery:\n  mode: yolo\n')):
            with self.subTest(label):
                result = self.mode_in(manifest)
                self.assert_one_line(result)
                self.assertNotRegex(result.line, r'(?<![\w/-])(direct|pr|yolo)(?![\w/-])')

    def test_no_manifest_means_no_mode(self):
        base = self.project('projnomanifest')
        result = self.run_script({'workspace': {'project_dir': str(base)}}, NO_COLOR='1')
        self.assertNotRegex(result.line, r'(?<![\w/-])(direct|pr)(?![\w/-])')

    def test_mode_is_read_without_pyyaml(self):
        """The script must not import PyYAML (it runs under any interpreter): a blocking import hook must not change the output."""
        base = self.project('projyaml', mode='direct')
        blocker = self.put(self.work, 'blockyaml/sitecustomize.py',
                           'import sys\nsys.modules["yaml"] = None\n')
        result = self.run_script({'workspace': {'project_dir': str(base)}}, NO_COLOR='1', PYTHONPATH=str(blocker.parent))
        self.assert_mode(result, 'direct', 'pr')


class Sanitizing(StatuslineCase):
    """K1: every field, from stdin and from files, is stripped of control, format and terminal sequences."""
    HOSTILE = {'ansi': 'A\x1b[31mB\x1b[0mC', 'osc bel': 'X\x1b]8;;evil.example\x07link\x1b]8;;\x07Y',
               'c1': 'A\x9b31mB\x85C\x9dD\x90E', 'bidi': 'A‮B⁦C⁩D‎E﻿F​G‪H',
               'breaks': 'A\nB\r\nC D E\x0bF\x0cG', 'controls': 'A\x00B\tC\x7fD\x08E\x07F'}

    def check(self, result):
        self.assert_clean(result, colour=True)
        self.assertNotIn('evil', result.line)

    def test_session_name(self):
        for label, value in {**self.HOSTILE, 'osc st': 'X\x1b]0;evil-title\x1b\\Y'}.items():
            for colour in (True, False):
                with self.subTest(label=label, no_color=not colour):
                    result = self.run_script({'session_name': value}, **({} if colour else {'NO_COLOR': '1'}))
                    self.check(result)
                    if not colour:
                        self.assertNotIn('\x1b', result.text)
                    self.assertIn('sesja:', result.line)

    def test_removed_sequences_leave_the_text_around_them(self):
        result = self.run_script({'session_name': self.HOSTILE['ansi']}, NO_COLOR='1')
        self.assertIn('sesja: ABC', result.line)
        self.assertNotIn('[31m', result.line)
        osc = self.run_script({'session_name': self.HOSTILE['osc bel']}, NO_COLOR='1')
        self.assertTrue(osc.line.startswith('sesja: X'))
        self.assertIn('Y', osc.line)
        self.assertNotIn(']8;;', osc.line)

    def test_other_stdin_fields(self):
        base = self.project('projsan')
        for label, value in self.HOSTILE.items():
            fields = {'project_dir': {'workspace': {'project_dir': value}}, 'cwd': {'cwd': value},
                      'git_worktree': {'workspace': {'project_dir': str(base), 'git_worktree': value}},
                      'worktree.branch': {'worktree': {'branch': value, 'name': 'n'}, 'workspace': {'project_dir': str(base)}},
                      'worktree.name': {'worktree': {'branch': 'b', 'name': value}, 'workspace': {'project_dir': str(base)}},
                      'original_branch': {'worktree': {'original_branch': value, 'original_cwd': value, 'path': value}}}
            for field, payload in fields.items():
                with self.subTest(label=label, field=field):
                    self.check(self.run_script(payload, NO_COLOR='1'))

    def test_all_fields_at_once_under_no_color_have_no_escape(self):
        hostile = self.HOSTILE['osc bel'] + self.HOSTILE['ansi']
        payload = {'session_name': hostile, 'cwd': hostile, 'workspace': {'project_dir': hostile, 'git_worktree': hostile},
                   'worktree': {'name': hostile, 'branch': hostile, 'path': hostile}}
        result = self.run_script(payload, NO_COLOR='1')
        self.check(result)
        self.assertNotIn('\x1b', result.text)

    def test_branch_read_from_head_file(self):
        for label, value in self.HOSTILE.items():
            with self.subTest(label):
                base = self.project('projheadsan', head='ref: refs/heads/feat/' + value.replace('\n', '').replace('\r', ''))
                result = self.run_script({'workspace': {'project_dir': str(base)}}, NO_COLOR='1')
                self.check(result)

    def test_worktree_directory_name_read_from_gitdir(self):
        for label, value in (('ansi', 'a\x1b[31mb'), ('osc', 'a\x1b]0;evil.example\x07b'), ('bidi', 'a‮b')):
            with self.subTest(label):
                try:
                    worktree = self.linked_worktree('wtsan' + label.replace(' ', ''), gitdir_name=value, head='ref: refs/heads/feat/san')
                except OSError:
                    self.skipTest('this file system refuses such a directory name')
                self.check(self.run_script({'workspace': {'project_dir': str(worktree)}}, NO_COLOR='1'))

    def test_hostile_manifest_value(self):
        for value in ('pr\x1b[31m', 'direct\x1b]0;evil.example\x07', '\x9b31mpr'):
            with self.subTest(repr(value)):
                base = self.project('projmanisan', manifest=f'delivery:\n  mode: {value}\n')
                self.check(self.run_script({'workspace': {'project_dir': str(base)}}, NO_COLOR='1'))


class PlanFixtures(StatuslineCase):
    """K3: which plan is chosen and how its steps are counted; parity with the tolerant parser of `status.py`."""

    def plans_project(self, plans, name='projplans'):
        """`plans` = {file name: (text, mtime offset in seconds)}; later offsets are newer."""
        base = self.project(name)
        start = 1_700_000_000
        for file, (text, offset) in plans.items():
            self.put(base, f'docs/plans/{file}', text, mtime=start + offset)
        return base

    def reference(self, base):
        """What `status.py` would pick: the newest `in_progress` plan (its own status and step parsers); the plan record or None."""
        best = None
        for path in sorted((base / 'docs' / 'plans').glob('*.md')):
            if path.is_symlink() or not path.is_file() or path.name.lower() == 'readme.md' or path.stat().st_size > plan_status.MAX_PLAN_BYTES:
                continue
            text = decode_text(path.read_bytes())
            status, _ = plan_status._status_fields(text, plan_status.PLAN_STATUSES)
            if status == 'in_progress' and (best is None or path.stat().st_mtime > best[0]):
                best = (path.stat().st_mtime, plan_status._progress(text))
        return best

    def shown_steps(self, base, **env):
        result = self.run_script({'workspace': {'project_dir': str(base)}}, NO_COLOR='1', **env)
        self.assert_one_line(result)
        return result, STEPS.findall(result.line)

    def assert_parity(self, base, expect=None):
        chosen = self.reference(base)
        result, found = self.shown_steps(base)
        if chosen is not None and chosen[1] is not None:
            counts = (str(chosen[1]['done']), str(chosen[1]['total']))
            self.assertIn(counts, found, f'status.py counts {counts}, the line shows {result.line!r}')
            self.assertEqual(len(found), 1, result.line)
        else:
            self.assertEqual(found, [], result.line)
        if expect is not None:
            self.assertEqual(found, [expect] if expect else [], result.line)
        return result

    def test_one_in_progress_plan(self):
        base = self.plans_project({'2026-11-21-a.md': (plan_text(), 0)})
        self.assert_parity(base, expect=('2', '5'))

    def test_several_plans_newest_in_progress_wins(self):
        base = self.plans_project({'2026-11-01-old.md': (plan_text(progress=PROGRESS_1_OF_3), 100),
                                   '2026-11-10-new.md': (plan_text(progress=PROGRESS_4_OF_6), 300),
                                   '2026-11-15-done.md': (plan_text('Status: done', PROGRESS_2_OF_5), 500),
                                   '2026-11-16-draft.md': (plan_text('Status: draft', PROGRESS_2_OF_5), 600),
                                   '2026-11-17-archived.md': (plan_text('Status: archived', PROGRESS_2_OF_5), 700)})
        self.assert_parity(base, expect=('4', '6'))

    def test_modification_time_decides_not_the_file_name(self):
        """The alphabetically LAST plan is the OLDER one here."""
        base = self.plans_project({'2026-11-30-zz-older.md': (plan_text(progress=PROGRESS_1_OF_3), 100),
                                   '2026-11-01-aa-newer.md': (plan_text(progress=PROGRESS_4_OF_6), 900)})
        self.assert_parity(base, expect=('4', '6'))

    def test_plan_without_progress_section_gives_no_steps(self):
        base = self.plans_project({'2026-11-21-a.md': (plan_text(progress=None), 0)})
        self.assert_parity(base, expect=None)
        result, found = self.shown_steps(base)
        self.assertEqual(found, [])

    def test_only_closed_plans_give_no_steps(self):
        base = self.plans_project({'2026-11-21-a.md': (plan_text('Status: done'), 0), '2026-11-22-b.md': (plan_text('Status: draft'), 10),
                                   '2026-11-23-c.md': (plan_text('Status: approved'), 20)})
        self.assert_parity(base, expect=None)

    def test_status_variants_like_status_py(self):
        for label, line in {'parenthesis': 'Status: in_progress (czeka na GO implement)', 'bold label': '**Status:** in_progress',
                            'bold value': 'Status: **in_progress**', 'backticks': 'Status: `in_progress`', 'list item': '- Status: in_progress',
                            'quote': '> Status: in_progress', 'upper case word': 'Status: In_Progress', 'trailing text': 'Status: in_progress, krok F3'}.items():
            with self.subTest(label):
                base = self.plans_project({'2026-11-21-a.md': (plan_text(line, PROGRESS_4_OF_6), 0)}, name='projstatus')
                self.assert_parity(base, expect=('4', '6'))

    def test_not_in_progress_variants_are_ignored(self):
        for label, line in {'done with note': 'Status: done (in_progress wczesniej)', 'draft': 'Status: draft (czeka na GO plan)',
                            'unknown word': 'Status: wip', 'no status line': ''}.items():
            with self.subTest(label):
                base = self.plans_project({'2026-11-21-a.md': (plan_text(line, PROGRESS_4_OF_6), 0)}, name='projnotprog')
                self.assert_parity(base, expect=None)

    def test_step_counting_rules(self):
        progress = ('### Faza 0: Bramka\n- [ ] F0.1 a\n- [x] F0.2 b\n### Faza 1\n- [X] F1.1 c\n'
                    '  - [x] zagniezdony punkt\nzwykly akapit - [x] nie punkt\n- [ ] F1.2 d\n')
        base = self.plans_project({'2026-11-21-a.md': (plan_text(progress=progress), 0)})
        self.assert_parity(base)

    def test_crlf_plan(self):
        text = plan_text(progress=PROGRESS_4_OF_6).replace('\n', '\r\n')
        base = self.plans_project({'2026-11-21-a.md': (text, 0)})
        self.assert_parity(base, expect=('4', '6'))

    def test_all_steps_done_and_none_done(self):
        for label, progress, expect in (('all', '- [x] a\n- [X] b\n', ('2', '2')), ('none', '- [ ] a\n- [ ] b\n- [ ] c\n', ('0', '3'))):
            with self.subTest(label):
                base = self.plans_project({'2026-11-21-a.md': (plan_text(progress=progress), 0)}, name='projcount')
                self.assert_parity(base, expect=expect)

    def test_readme_in_plans_directory_is_not_a_plan(self):
        base = self.plans_project({'2026-11-01-a.md': (plan_text(progress=PROGRESS_1_OF_3), 100),
                                   'README.md': (plan_text(progress=PROGRESS_4_OF_6), 900)})
        self.assert_parity(base, expect=('1', '3'))

    def test_non_plan_files_are_ignored(self):
        base = self.plans_project({'2026-11-01-a.md': (plan_text(progress=PROGRESS_1_OF_3), 100),
                                   'notes.txt': (plan_text(progress=PROGRESS_4_OF_6), 900),
                                   'archive.md.bak': (plan_text(progress=PROGRESS_4_OF_6), 900)})
        (base / 'docs' / 'plans' / 'nested.md').mkdir()
        self.put(base, 'docs/plans/nested.md/inner.md', plan_text(progress=PROGRESS_4_OF_6), mtime=1_700_000_900)
        self.assert_parity(base, expect=('1', '3'))

    def test_plan_found_from_subdirectory_of_a_worktree(self):
        worktree = self.linked_worktree('wtplans', head='ref: refs/heads/feat/p')
        self.put(worktree, 'docs/plans/p.md', plan_text(progress=PROGRESS_4_OF_6))
        deep = descend(worktree, 'src')
        deep.mkdir()
        result = self.run_script({'cwd': str(deep)}, NO_COLOR='1')
        self.assertIn(('4', '6'), STEPS.findall(result.line))

    # --- limits ------------------------------------------------------------------------------------------------------
    def padded(self, size):
        """An `in_progress` plan of exactly `size` bytes (padding after the Progress section)."""
        text = plan_text(progress=PROGRESS_4_OF_6)
        data = text.encode('utf-8')
        return data + b'\n' + b'x' * (size - len(data) - 1)

    def test_plan_of_exactly_512_kib_is_read_and_one_byte_more_is_skipped(self):
        for size, shown in ((MAX_PLAN_BYTES, True), (MAX_PLAN_BYTES + 1, False)):
            with self.subTest(size=size):
                base = self.plans_project({'2026-11-21-big.md': (self.padded(size), 0)}, name='projbig')
                self.assertEqual(os.path.getsize(base / 'docs' / 'plans' / '2026-11-21-big.md'), size)
                _, found = self.shown_steps(base)
                self.assertEqual(found, [('4', '6')] if shown else [])

    def test_oversized_newest_plan_is_skipped_for_the_next_one(self):
        base = self.plans_project({'2026-11-01-small.md': (plan_text(progress=PROGRESS_1_OF_3), 100),
                                   '2026-11-02-big.md': (self.padded(MAX_PLAN_BYTES + 1), 900)})
        _, found = self.shown_steps(base)
        self.assertEqual(found, [('1', '3')])

    def test_symlinked_plan_is_skipped_and_never_opened(self):
        base = self.plans_project({'2026-11-01-real.md': (plan_text(progress=PROGRESS_1_OF_3), 100)})
        outside = self.put(self.work, 'outside/plan.md', plan_text(progress=PROGRESS_4_OF_6), mtime=1_700_000_900)
        link = base / 'docs' / 'plans' / '2026-11-09-link.md'
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links are not available on this system')
        result = self.run_script({'workspace': {'project_dir': str(base)}}, wrapped=True, fixture=base, NO_COLOR='1')
        self.assertEqual(result.rc, 0, result.err)
        self.assertEqual(STEPS.findall(result.line), [('1', '3')])
        self.assertEqual([path for path in result.log['opened'] if path.endswith('2026-11-09-link.md')], [])


class NoProcessesAndBoundedReads(StatuslineCase):
    """K1 (no processes, no sockets) and K4 (bounded reads, time)."""

    def many_plans(self, count, name='projmany', in_progress_every=50):
        base = self.project(name, mode='direct')
        for index in range(count):
            status = 'Status: in_progress' if index % in_progress_every == 0 else 'Status: done'
            self.put(base, f'docs/plans/2026-01-01-plan-{index:04d}.md', plan_text(status, PROGRESS_2_OF_5), mtime=1_700_000_000 + index)
        return base

    # -- the guard wrapper is itself checked, so that "nothing was blocked" can fail -------------------------------------
    def probe(self, body, base):
        script = self.put(self.work, 'probe.py', body)
        log = child(self.work, 'probe-log.json')
        process = subprocess.run([sys.executable, str(WRAPPER), str(script)], input=b'{}', capture_output=True, timeout=60,
                                 env=self.child_env(SL_ROOT=str(base), SL_LOG=str(log)))
        return process, json.loads(log.read_text(encoding='utf-8'))

    def test_wrapper_records_blocked_calls_even_when_the_script_swallows_errors(self):
        body = ('import os, socket, subprocess\n'
                'for call in (lambda: subprocess.run(["true"]), lambda: subprocess.Popen(["true"]), lambda: os.system("true"),\n'
                '             lambda: os.popen("true"), lambda: socket.socket(), lambda: socket.create_connection(("127.0.0.1", 9))):\n'
                '    try:\n        call()\n    except BaseException:\n        pass\n')
        process, log = self.probe(body, self.project('probe-root'))
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(sorted(log['blocked']), sorted(['subprocess.run', 'subprocess.Popen', 'os.system', 'os.popen', 'socket.socket',
                                                          'socket.create_connection']))

    def test_wrapper_records_opens_under_the_fixture_only(self):
        base = self.project('probe-root')
        body = ('import pathlib\nopen(%r).read()\npathlib.Path(%r).read_text()\npathlib.Path(%r).read_bytes()\nopen(%r).read()\n'
                % (str(base / '.git' / 'HEAD'), str(base / '.git' / 'HEAD'), str(base / '.git' / 'HEAD'), str(self.work / 'probe.py')))
        process, log = self.probe(body, base)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(log['opened'], [str(base / '.git' / 'HEAD')] * 3)

    # -- the script ----------------------------------------------------------------------------------------------------
    def test_no_processes_no_sockets(self):
        base = self.many_plans(5)
        worktree = self.linked_worktree('wtnoproc')
        self.put(worktree, 'docs/plans/p.md', plan_text())
        for label, payload in (('plain repo', {'session_name': 'Alfa-1', 'workspace': {'project_dir': str(base)}}),
                               ('linked worktree', {'session_name': 'Alfa-1', 'workspace': {'project_dir': str(worktree)}}),
                               ('from a subdirectory', {'cwd': str(descend(worktree, 'docs'))}),
                               ('no fields', {}), ('hostile', {'session_name': '\x1b]0;x\x07' * 50, 'cwd': '\x00'})):
            with self.subTest(label):
                result = self.run_script(payload, wrapped=True, fixture=self.work, NO_COLOR='1')
                self.assertEqual(result.rc, 0, result.err)
                self.assertEqual(result.log['blocked'], [])
        rich = self.run_script({'session_name': 'Alfa-1', 'workspace': {'project_dir': str(base)}}, wrapped=True, fixture=self.work, NO_COLOR='1')
        self.assertIn('sesja: Alfa-1', rich.line)   # the wrapper did not break the script: the run above was a real one

    def test_number_of_file_opens_is_bounded(self):
        """300 plans on disk: at most the first 200 are read (the limit of K3) plus a handful of project files (manifest, `.git`, HEAD)."""
        base = self.many_plans(300)
        result = self.run_script({'session_name': 'Alfa-1', 'workspace': {'project_dir': str(base)}}, wrapped=True, fixture=base, NO_COLOR='1')
        self.assertEqual(result.rc, 0, result.err)
        opened = result.log['opened']
        plans = [path for path in opened if f'{os.sep}docs{os.sep}plans{os.sep}' in path]
        others = [path for path in opened if path not in plans]
        self.assertLessEqual(len(plans), 200, 'plan limit (K3: up to 200 files)')
        self.assertLessEqual(len(others), 10, others)
        self.assertEqual(len(plans), len(set(plans)), 'a plan is read at most once')
        self.assertIn('sesja: Alfa-1', result.line)

    def test_files_outside_the_documented_set_are_not_opened(self):
        base = self.project('projscope', mode='direct')
        self.put(base, 'docs/plans/p.md', plan_text())
        self.put(base, 'docs/plans/notes.txt', 'x')
        self.put(base, 'docs/specs/s.md', 'Status: approved\n')
        self.put(base, 'src/code.py', 'print(1)\n')
        self.put(base, 'README.md', '# x\n')
        result = self.run_script({'workspace': {'project_dir': str(base)}}, wrapped=True, fixture=base, NO_COLOR='1')
        names = {os.path.relpath(path, base).replace(os.sep, '/') for path in result.log['opened']}
        self.assertLessEqual(names, {'.git/HEAD', '.git', '.github/.skillsync.yaml', 'docs/plans/p.md'}, names)
        self.assertIn('docs/plans/p.md', names)

    def test_two_hundred_plans_fit_in_one_second(self):
        """A broad bound, not a benchmark: 200 small plans in a worktree repository, best of three process runs (start included)."""
        worktree = self.linked_worktree('wtperf', head='ref: refs/heads/feat/perf')
        self.put(worktree, '.github/.skillsync.yaml', 'delivery:\n  mode: pr\n')
        for index in range(200):
            status = 'Status: in_progress' if index == 123 else 'Status: done'
            self.put(worktree, f'docs/plans/2026-02-02-plan-{index:04d}.md', plan_text(status, PROGRESS_4_OF_6) + '\nTekst. ' * 400,
                     mtime=1_700_000_000 + index)
        payload = {'session_name': 'Alfa-1', 'workspace': {'project_dir': str(worktree)}}
        best, last = None, None
        for _ in range(3):
            started = time.perf_counter()
            last = self.run_script(payload, NO_COLOR='1')
            elapsed = time.perf_counter() - started
            best = elapsed if best is None else min(best, elapsed)
        self.assertEqual(last.rc, 0, last.err)
        self.assertIn(('4', '6'), STEPS.findall(last.line))   # the run did the real work
        self.assertIn('feat/perf', last.line)
        self.assertLess(best, 1.0, f'best of three: {best:.3f}s')


if __name__ == '__main__':
    unittest.main()
