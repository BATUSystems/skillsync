"""CLAUDE-7 F4.1 (tests written first, RED until the code exists): the response style `karta` and its installation by `link` (spec K7, K8, K9; plan step F4).

Contract under test (the spec of CLAUDE-7, `Kontrakty` and `Decyzje o stylu karta`):
 * `library/output-styles/karta.md`: YAML frontmatter (`name: karta`, `description`, `keep-coding-instructions: true`) that parses the way `doctor` parses
   frontmatter (no unquoted colon), a short body about the FORM of a report only (STATUS/REPORT, brevity), no voice, no personas, no secrets, no
   attribution of AI tools.
 * `claude.output_styles: true`: `link` puts a COPY (never a symlink) into `.claude/output-styles/karta.md` and adds the line `output-styles/karta.md` to
   `.claude/.gitignore`; the second `link` changes nothing (bytes, mtime); the setting `outputStyle` is NEVER written by the generator (the user picks the
   style with `/output-style karta`); a foreign file of that name (also a tracked one, also a symlink) is not overwritten (a `[warn]`), and is not put into
   `.claude/.gitignore`; a locally edited managed copy stays.
 * the key off, absent, or `claude: false` / `{enabled: false}`: our unchanged copy is removed, a foreign or edited file stays.
 * without the keys NOTHING changes (K8): no directory, no file, no `.gitignore` line, no finding of `doctor`.
 * the style is independent of the status line (each key switches only its own artefacts).
 * layers (K9): `output-styles/` is `core-only` (the org and personal layers neither add nor override), and the ban on AI attribution scans it.

Decisions the spec leaves open (see the report of F4.1): the `[warn]` of a foreign file names the file `karta.md` and starts a line with `[warn]`; the
line of a local edit is any of `[info]`, `[warn]`, `[conflict]` naming `karta.md` (the wording of the other managed copies); the `.gitignore` line is
`output-styles/karta.md` (per file, like `agents/<role>.md` and `hooks/statusline.py`); a foreign file is not git-ignored by us.
Negative assertions have a proof that they can fail: `HarnessSelfChecks` plants the forbidden state by hand and expects the helper to refuse it.
Temporary paths come from `tmp_paths.child`/`descend`; HOME/XDG are isolated by `IsolatedTestCase`; nothing touches the real `~/.claude`."""
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from pathlib import Path as _Path_tmp_paths
import sys as _sys_tmp_paths
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE / 'fixtures' / 'hooksync'))
from layers_helpers import IsolatedTestCase, LayersCase, SKILL_TEXT, generator_version, git, make_layer, needs_os_symlinks, tree_snapshot  # noqa: E402
_sys_tmp_paths.path.insert(0, str(_Path_tmp_paths(__file__).resolve().parent))  # noqa: E402
from tmp_paths import child, descend  # noqa: E402
import cases  # noqa: E402
from skillsync_workflow import attribution, layers  # noqa: E402

CLI = REPO / 'skillsync'
LOCAL = cases.LOCAL
STYLE = 'output-styles/karta.md'
DEST = '.claude/output-styles/karta.md'
GITIGNORE = '.claude/.gitignore'
SOURCE = REPO / 'library' / STYLE
FOREIGN_STYLE = '---\nname: karta\ndescription: "moj wlasny styl"\n---\nPisz po swojemu, ą ę.\n'
FOREIGN_BAR = {'type': 'command', 'command': 'echo moj-pasek', 'padding': 2}
STYLE_WORDS = re.compile(r'output[-_ ]?styles?|outputStyle|karta\.md', re.IGNORECASE)
# a text of the banned AI attribution, composed at run time so that this file does not match its own patterns
BANNED = 'Gener' + 'ated with [Cla' + 'ude Code](https://cla' + 'ude.com/cla' + 'ude-code)'


def doctor_mentions_style(finding):
    return STYLE_WORDS.search(f"{finding.get('path', '')} {finding.get('reason', '')}") is not None


def frontmatter(text):
    """The way `quality.doctor` reads the frontmatter of a record: `---`, the YAML up to the next `---`; (metadata, body)."""
    if not text.startswith('---\n'):
        raise AssertionError('no YAML frontmatter')
    _, head, body = text.split('---', 2)
    return yaml.safe_load(head), body


# --------------------------------------------------------------------------------------------------------------
# 1. the file of the library
# --------------------------------------------------------------------------------------------------------------
class StyleFile(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SOURCE.is_file() and not SOURCE.is_symlink(), f'{STYLE} is missing in the library')
        self.raw = SOURCE.read_bytes()
        self.text = self.raw.decode('utf-8')

    def test_frontmatter_parses_like_doctor_and_has_the_contracted_fields(self):
        metadata, _body = frontmatter(self.text)
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata.get('name'), 'karta')
        self.assertIsInstance(metadata.get('description'), str)
        self.assertTrue(metadata['description'].strip())
        self.assertIs(metadata.get('keep-coding-instructions'), True, 'the style must keep the coding instructions of the host')
        self.assertLessEqual(set(metadata), {'name', 'description', 'keep-coding-instructions'}, 'only the fields the host knows')

    def test_text_is_lf_utf8_and_ends_with_a_newline(self):
        self.assertNotIn(b'\r', self.raw)
        self.assertTrue(self.raw.endswith(b'\n'))

    def test_body_is_about_the_form_of_a_report_only(self):
        _metadata, body = frontmatter(self.text)
        self.assertTrue(body.strip())
        self.assertIn('STATUS', body)
        self.assertIn('REPORT', body)
        self.assertLessEqual(len(body.encode('utf-8')), 4096, 'a style is read in every turn: brevity is the point of it')

    def test_body_points_to_the_message_grammar_of_the_cards_and_does_not_invent_its_own_format(self):
        _metadata, body = frontmatter(self.text)
        self.assertRegex(body, r'(?m)^.*`date:.*', 'the first line `date:` is required by the protocol')
        self.assertIn('STATUS <paczka>', body)
        self.assertRegex(body, r'REPORT DONE\|BLOCKED')
        self.assertNotRegex(body, r'(?m)^\s*(STATUS|REPORT):', 'no own format of the report (a colon after the keyword)')
        self.assertIn('multi-session', body, 'a pointer to the grammar')
        self.assertIn('NOT_RUN', body)

    def test_body_has_no_voice_persona_secret_or_machine_path(self):
        _metadata, body = frontmatter(self.text)
        lowered = body.lower()
        for word in ('skip' + 'py', 'barc' + 'iś', 'persona', 'osobowo', '/ho' + 'me/', '~/', 'password', 'api key', 'secret'):
            with self.subTest(word):
                self.assertNotIn(word, lowered)

    def test_text_passes_the_leak_check_of_the_core(self):
        done = subprocess.run([sys.executable, str(CLI), 'leakcheck', '--target', 'core', '--stdin'], input=self.raw, capture_output=True,
                              timeout=120, env=dict(os.environ, SKILLSYNC_NO_COMPLETION='1'))
        self.assertEqual(done.returncode, 0, done.stdout.decode('utf-8', 'replace') + done.stderr.decode('utf-8', 'replace'))

    def test_text_has_no_attribution_of_ai_tools(self):
        self.assertEqual(attribution.scan_text(self.text), [])

    def test_the_scanner_would_flag_a_footer_in_the_same_place(self):
        # the proof that the scan above can fail: the same text with a footer appended is flagged
        self.assertTrue(attribution.scan_text(self.text + '\n' + BANNED + '\n'))


# --------------------------------------------------------------------------------------------------------------
# 2. layers: the merge rule and the scan of attribution (K9)
# --------------------------------------------------------------------------------------------------------------
class LayerRules(LayersCase):
    sets = 0

    def build(self, *specs):
        """specs: (id, kind, {rel: text}); one rule `**` using the layers in the given order."""
        LayerRules.sets += 1
        base = child(self.work, f'set{LayerRules.sets}')
        entries = []
        for layer_id, kind, files in specs:
            options = {'version': generator_version()} if kind == 'core' else {}
            entries.append((layer_id, kind, make_layer(child(base, layer_id), layer_id, kind, files, **options)))
        self.write_config(entries, [(['**'], [spec[0] for spec in specs])])
        return layers.resolve_layers(self.project(f'proj{LayerRules.sets}'))

    def three(self, core=None, acme=None, me=None):
        return self.build(('core', 'core', core or {}), ('acme', 'org', acme or {}), ('me', 'personal', me or {}))

    def findings(self, layerset):
        return layers.check_layers(layerset, hooks=False)

    def by_code(self, layerset, code):
        return [item for item in self.findings(layerset) if item['code'] == code]

    def test_the_directory_is_core_only(self):
        for rel in (STYLE, 'output-styles/dyspozytor.md'):
            with self.subTest(rel):
                self.assertEqual(layers.merge_rule(rel), 'core-only')

    def test_only_the_core_layer_provides_the_style(self):
        layerset = self.three(core={STYLE: 'core\n'}, acme={STYLE: 'org\n'}, me={STYLE: 'mine\n'})
        owner, path = layerset.resolve(STYLE)
        self.assertEqual((owner.id, path.read_text(encoding='utf-8')), ('core', 'core\n'))

    def test_a_style_of_the_org_or_personal_layer_alone_is_ignored_with_a_warning(self):
        layerset = self.three(core={}, acme={STYLE: 'org\n'}, me={'output-styles/moj.md': 'mine\n'})
        self.assertIsNone(layerset.resolve(STYLE), 'nothing from core: no result at all')
        self.assertIsNone(layerset.resolve('output-styles/moj.md'))
        found = self.by_code(layerset, 'core-only-ignored')
        self.assertEqual(sorted(item['layer'] for item in found), ['acme', 'me'])
        self.assertTrue(all(item['severity'] == 'warning' for item in found))

    def test_a_clean_core_has_no_ignored_style(self):
        layerset = self.three(core={STYLE: 'core\n'})
        self.assertEqual(self.by_code(layerset, 'core-only-ignored'), [])

    def test_the_scan_of_attribution_covers_the_directory(self):
        layerset = self.three(core={STYLE: 'Form of a report.\n' + BANNED + '\n'})
        hits = self.by_code(layerset, 'attribution-text')
        self.assertTrue(hits, 'a footer in output-styles/ must be reported')
        self.assertTrue(all(hit['severity'] == 'error' for hit in hits))
        self.assertTrue(any(f'{STYLE}:2' in hit['message'] for hit in hits), hits)
        self.assertFalse(any('Gener' + 'ated' in hit['message'] for hit in hits), 'the message never repeats the matched text')

    def test_a_clean_style_is_not_reported(self):
        layerset = self.three(core={STYLE: 'Form of a report.\n'})
        self.assertEqual(self.by_code(layerset, 'attribution-text'), [])

    def test_the_library_of_the_repository_passes_the_scan(self):
        self.assertTrue(SOURCE.is_file(), f'{STYLE} is missing in the library')
        layerset = self.build(('core', 'core', {STYLE: SOURCE.read_text(encoding='utf-8')}))
        self.assertEqual(self.by_code(layerset, 'attribution-text'), [])

    def test_link_takes_the_style_from_the_core_layer_whatever_the_org_and_personal_layers_say(self):
        base = child(self.work, 'composed')
        core = make_layer(child(base, 'core'), 'core', 'core', {STYLE: 'core style\n', 'skills/solo/SKILL.md': SKILL_TEXT.format('solo')},
                          version=generator_version())
        org = make_layer(child(base, 'org'), 'org', 'org', {STYLE: 'org style\n'})
        mine = make_layer(child(base, 'mine'), 'mine', 'personal', {STYLE: 'my style\n'})
        self.write_config([('core', 'core', core), ('org', 'org', org), ('mine', 'personal', mine)], [(['**'], ['core', 'org', 'mine'])])
        project = child(self.work, 'proj-composed')
        manifest = {'version': 1, 'skills': ['solo'], 'claude': {'output_styles': True}, 'mcp': False,
                    'instructions': {'merge': ['_base'], 'template': 'proj'}}
        target = descend(project, '.github/.skillsync.yaml')
        target.parent.mkdir(parents=True)
        target.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding='utf-8')
        done = self.cli('link', str(project), env={'SKILLSYNC_NO_COMPLETION': '1'})
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        copy = descend(project, DEST)
        self.assertTrue(copy.is_file() and not copy.is_symlink())
        self.assertEqual(copy.read_text(encoding='utf-8'), 'core style\n')


# --------------------------------------------------------------------------------------------------------------
# 3. `link` (black box, subprocess, isolated HOME/XDG/SKILLSYNC_CONFIG)
# --------------------------------------------------------------------------------------------------------------
class LinkBase(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {'SKILLSYNC_NO_COMPLETION': '1'})
        patcher.start()
        self.addCleanup(patcher.stop)
        cases.write_machine_config(self.env['SKILLSYNC_CONFIG'])
        self.count = 0
        self.home = Path(self.env['HOME'])
        self.library_bytes = SOURCE.read_bytes() if SOURCE.is_file() else None

    # --- building and running -------------------------------------------------------------------------------
    def project(self, claude=None, bare=False, name=None, method=None):
        """A fresh plain project (the hooks of CLAUDE-3 switched off). `claude`: the value of the manifest key (`None`: output styles on);
        `bare=True`: no `claude` key at all; `method`: `sync.method`."""
        self.count += 1
        project = child(self.work, name or f'p{self.count}')
        project.mkdir()
        self.manifest = {'version': 1, 'skills': [], 'hooks': dict(cases.NEW_HOOK_KEYS_OFF)}
        if method:
            self.manifest['sync'] = {'method': method}
        if not bare:
            self.manifest['claude'] = {'output_styles': True} if claude is None else claude
        self.write_manifest(project)
        return project

    def write_manifest(self, project):
        target = descend(project, '.github/.skillsync.yaml')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml.safe_dump(self.manifest, sort_keys=False), encoding='utf-8')

    def set_claude(self, project, value):
        if value is None:
            self.manifest.pop('claude', None)
        else:
            self.manifest['claude'] = value
        self.write_manifest(project)

    def link(self, project, *args):
        return cases.run_link(project, *args)

    def lines(self, result):
        return result.stdout.splitlines()

    def warns(self, result):
        return [line for line in self.lines(result) if line.startswith('[warn]')]

    def style_lines(self, result, prefixes=('[warn]', '[info]', '[conflict]')):
        return [line for line in self.lines(result) if line.startswith(prefixes) and 'karta.md' in line]

    def dest(self, project):
        return Path(project).joinpath('.claude', 'output-styles', 'karta.md')   # not `descend`: it refuses a path that resolves out of the project (a planted link)

    def settings(self, project):
        return json.loads(descend(project, LOCAL).read_bytes().decode('utf-8'))

    def write_settings(self, project, data):
        target = descend(project, LOCAL)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(cases.json_bytes(data))
        return target

    def write_user_settings(self, data):
        target = child(self.home, '.claude')
        target.mkdir(exist_ok=True)
        path = child(target, 'settings.json')
        path.write_bytes(cases.json_bytes(data))
        return path

    def put_style(self, project, text=FOREIGN_STYLE, name='karta.md'):
        target = descend(project, f'.claude/output-styles/{name}')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8', newline='\n')
        return target

    def gitignore_lines(self, project):
        path = descend(project, GITIGNORE)
        return path.read_text(encoding='utf-8').splitlines() if path.is_file() else []

    def doctor(self, project):
        done = subprocess.run([sys.executable, str(CLI), 'doctor', str(project), '--json'], capture_output=True, text=True, timeout=120,
                              cwd=str(project), env=dict(os.environ))
        self.assertNotIn('Traceback', done.stderr, done.stderr)
        return json.loads(done.stdout)['findings']

    def init_git(self, project):
        git(project, 'init', '-q', '-b', 'main')
        git(project, 'config', 'user.email', 'dev@example.test')
        git(project, 'config', 'user.name', 'Dev Example')
        git(project, 'config', 'commit.gpgsign', 'false')

    # --- assertions -----------------------------------------------------------------------------------------
    def assert_no_style(self, project, message=''):
        """No file (not even a link) at the place of the style and no line of it in `.claude/.gitignore` (the negative assertion; proven to fail in
        `HarnessSelfChecks`)."""
        target = self.dest(project)
        self.assertFalse(target.exists() or target.is_symlink(), f'the style is there {message}')
        self.assertFalse(any('output-styles/karta.md' in line for line in self.gitignore_lines(project)), f'.claude/.gitignore names the style {message}')

    def assert_copy_of_library(self, project, message='', expected=None):
        """A regular file (not a link) equal to the library byte for byte (`expected`: the bytes to compare with; for the self-checks)."""
        if expected is None:
            self.assertTrue(SOURCE.is_file(), f'{STYLE} is missing in the library')
            expected = SOURCE.read_bytes()
        target = self.dest(project)
        self.assertTrue(target.is_file() and not target.is_symlink(), f'not a regular copy at {DEST} {message}')
        self.assertEqual(target.read_bytes(), expected, f'the copy differs from the library {message}')

    def assert_no_output_style_setting(self, project):
        for name in ('settings.local.json', 'settings.json'):
            path = descend(project, f'.claude/{name}')
            if path.is_file():
                self.assertNotIn('outputStyle', json.loads(path.read_bytes().decode('utf-8')), f'`outputStyle` written to {name}')


class Installation(LinkBase):
    def test_true_installs_a_copy_equal_to_the_library(self):
        project = self.project()
        self.link(project)
        self.assert_copy_of_library(project)

    def test_the_copy_is_a_file_and_not_a_symlink_in_the_default_symlink_mode_and_in_the_copy_mode(self):
        for method in (None, 'symlink', 'copy'):
            with self.subTest(method=method):
                project = self.project(method=method)
                self.link(project)
                self.assert_copy_of_library(project)
                directory = descend(project, '.claude/output-styles')
                self.assertTrue(directory.is_dir() and not directory.is_symlink(), 'the directory is real too')

    def test_gitignore_lists_the_style_per_file_once(self):
        project = self.project()
        self.link(project)
        self.assertEqual(self.gitignore_lines(project).count('output-styles/karta.md'), 1)
        self.link(project)
        self.assertEqual(self.gitignore_lines(project).count('output-styles/karta.md'), 1)

    def test_the_second_link_changes_nothing_not_the_bytes_not_the_mtime(self):
        project = self.project()
        self.link(project)
        self.assert_copy_of_library(project)
        target = self.dest(project)
        os.utime(target, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
        before = (target.read_bytes(), target.stat().st_mtime_ns, descend(project, GITIGNORE).read_bytes())
        second = self.link(project)
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns, descend(project, GITIGNORE).read_bytes()), before)
        self.assertEqual(self.warns(second), [])
        self.assertEqual(self.style_lines(second), [])
        self.assertFalse([line for line in self.lines(second) if line.startswith('Updated:') and 'output-styles' in line], second.stdout)

    def test_dry_run_writes_nothing_and_says_what_it_would_do(self):
        project = self.project()
        result = self.link(project, '--dry-run')
        self.assert_no_style(project)
        self.assertFalse(descend(project, '.claude/output-styles').exists(), 'no directory either')
        self.assertTrue([line for line in self.lines(result) if line.startswith('[dry-run]') and 'karta.md' in line], result.stdout)

    def test_the_generator_never_sets_the_output_style(self):
        project = self.project()
        self.link(project)
        self.assert_no_output_style_setting(project)

    def test_a_style_chosen_by_the_user_stays_when_link_installs_and_when_it_switches_off(self):
        user = {'outputStyle': 'karta', 'env': {'ZMIENNA': 'wartość-ą'}, 'permissions': {'allow': ['Bash(ls:*)']}}
        project = self.project()
        self.write_settings(project, user)
        self.link(project)
        data = self.settings(project)
        self.assertEqual(data['outputStyle'], 'karta')
        self.assertEqual((data['env'], data['permissions']), (user['env'], user['permissions']))
        self.set_claude(project, {'output_styles': False})
        self.link(project)
        self.assertEqual(self.settings(project)['outputStyle'], 'karta', 'switching the key off does not unset the choice of the user')

    def test_a_different_style_of_the_user_stays_too(self):
        project = self.project()
        self.write_settings(project, {'outputStyle': 'Explanatory'})
        self.link(project)
        self.assertEqual(self.settings(project)['outputStyle'], 'Explanatory')

    def test_the_user_and_the_shared_settings_files_are_not_written(self):
        user_path = self.write_user_settings({'outputStyle': 'Learning'})
        before = user_path.read_bytes()
        project = self.project()
        shared = descend(project, '.claude/settings.json')
        shared.parent.mkdir(parents=True)
        shared.write_bytes(cases.json_bytes({'env': {'A': '1'}}))
        shared_before = shared.read_bytes()
        self.link(project)
        self.assertEqual(user_path.read_bytes(), before)
        self.assertEqual(shared.read_bytes(), shared_before)

    def test_other_styles_in_the_directory_are_not_touched(self):
        project = self.project()
        other = self.put_style(project, '---\nname: moj\ndescription: moj\n---\nTreść.\n', name='moj.md')
        stamp = (other.read_bytes(), other.stat().st_mtime_ns)
        self.link(project)
        self.assertEqual((other.read_bytes(), other.stat().st_mtime_ns), stamp)
        self.assert_copy_of_library(project)

    def test_a_locally_edited_managed_copy_stays_and_is_reported_like_other_managed_copies(self):
        project = self.project()
        self.link(project)
        self.assert_copy_of_library(project)
        target = self.dest(project)
        target.write_text(target.read_text(encoding='utf-8') + '\nMoja dopiska.\n', encoding='utf-8', newline='\n')
        edited = target.read_bytes()
        result = self.link(project)
        self.assertEqual(target.read_bytes(), edited)
        self.assertTrue(self.style_lines(result), f'a line naming karta.md is expected: {result.stdout}')
        self.assertFalse([line for line in self.style_lines(result) if 'push' in line.lower()], 'push does not handle styles: no pointer to it')

    def test_doctor_gives_an_info_about_the_available_style_never_a_warning(self):
        project = self.project()
        self.link(project)
        findings = [f for f in self.doctor(project) if doctor_mentions_style(f)]
        self.assertEqual([f['level'] for f in findings], ['info'], findings)
        self.assertIn('/output-style karta', findings[0]['reason'])

    def test_doctor_reports_no_error_about_the_style_after_the_install(self):
        project = self.project()
        self.link(project)
        findings = [f for f in self.doctor(project) if doctor_mentions_style(f)]
        self.assertEqual([f for f in findings if f['level'] in ('error', 'warning')], [], findings)


class ForeignFile(LinkBase):
    def test_a_foreign_file_of_that_name_is_not_overwritten_and_gets_a_warning(self):
        project = self.project()
        target = self.put_style(project)
        before = target.read_bytes()
        result = self.link(project)
        self.assertEqual(target.read_bytes(), before)
        self.assertTrue([line for line in self.warns(result) if 'karta.md' in line], result.stdout)

    def test_a_foreign_file_stays_the_same_after_every_link(self):
        project = self.project()
        target = self.put_style(project)
        before = target.read_bytes()
        for _ in range(2):
            self.link(project)
            self.assertEqual(target.read_bytes(), before)
            self.assertFalse(target.is_symlink())

    def test_a_foreign_file_is_not_hidden_from_git_by_our_gitignore(self):
        project = self.project()
        self.put_style(project)
        self.link(project)
        self.assertFalse(any('output-styles/karta.md' in line for line in self.gitignore_lines(project)))

    def test_a_file_tracked_by_git_is_not_overwritten_and_the_work_tree_stays_clean(self):
        project = self.project()
        self.init_git(project)
        target = self.put_style(project)
        git(project, 'add', '-f', DEST)
        git(project, 'commit', '-q', '-m', 'wlasny styl')
        before = target.read_bytes()
        result = self.link(project)
        self.assertEqual(target.read_bytes(), before)
        self.assertTrue([line for line in self.warns(result) if 'karta.md' in line], result.stdout)
        self.assertEqual(git(project, 'diff', '--name-only', '--', DEST).stdout.strip(), '', 'the tracked file is unchanged for git')

    @needs_os_symlinks
    def test_a_symlink_in_the_directory_never_makes_us_write_through_it(self):
        project = self.project()
        outside = child(self.work, 'poza-projektem.md')
        outside.write_text(FOREIGN_STYLE, encoding='utf-8', newline='\n')
        before = outside.read_bytes()
        link = self.dest(project)
        link.parent.mkdir(parents=True)
        os.symlink(outside, link)
        result = self.link(project)
        self.assertEqual(outside.read_bytes(), before, 'the target of a foreign symlink is not written')
        self.assertTrue(link.is_symlink(), 'a foreign symlink is not silently replaced')
        self.assertTrue([line for line in self.warns(result) if 'karta.md' in line], result.stdout)
        self.assertFalse(any('output-styles/karta.md' in line for line in self.gitignore_lines(project)))


class SwitchingOff(LinkBase):
    def installed(self, claude=None):
        project = self.project(claude=claude)
        self.link(project)
        self.assert_copy_of_library(project, 'after the install')
        return project

    def test_key_false_removes_our_unchanged_copy(self):
        project = self.installed()
        self.set_claude(project, {'output_styles': False})
        self.link(project)
        self.assertFalse(self.dest(project).exists() or self.dest(project).is_symlink())

    def test_key_absent_removes_our_unchanged_copy(self):
        project = self.installed()
        self.set_claude(project, {})
        self.link(project)
        self.assertFalse(self.dest(project).exists())

    def test_the_whole_claude_key_removed_removes_our_unchanged_copy(self):
        project = self.installed()
        self.set_claude(project, None)
        self.link(project)
        self.assertFalse(self.dest(project).exists())

    def test_claude_false_removes_our_copy(self):
        project = self.installed()
        self.set_claude(project, False)
        self.link(project)
        self.assertFalse(self.dest(project).exists())

    def test_claude_enabled_false_wins_over_the_style_key(self):
        project = self.installed()
        self.set_claude(project, {'enabled': False, 'output_styles': True})
        self.link(project)
        self.assertFalse(self.dest(project).exists())

    def test_a_locally_edited_copy_stays_when_the_key_goes_off(self):
        project = self.installed()
        target = self.dest(project)
        target.write_text(target.read_text(encoding='utf-8') + '\nMoja dopiska.\n', encoding='utf-8', newline='\n')
        edited = target.read_bytes()
        for value in ({'output_styles': False}, False):
            self.set_claude(project, value)
            self.link(project)
            self.assertEqual(target.read_bytes(), edited, f'claude: {value!r}')

    def test_a_foreign_file_stays_when_the_key_is_off_and_when_claude_is_false(self):
        for value in ({'output_styles': False}, {}, False):
            with self.subTest(claude=value):
                project = self.project(claude=value)
                target = self.put_style(project)
                before = target.read_bytes()
                self.link(project)
                self.assertEqual(target.read_bytes(), before)

    @needs_os_symlinks
    def test_a_foreign_link_into_a_layer_stays_without_the_key_and_with_claude_false(self):
        for value in ({'output_styles': False}, {}, False):
            with self.subTest(claude=value):
                project = self.project(claude=value)
                link = Path(project).joinpath('.claude', 'output-styles', 'inny.md')
                link.parent.mkdir(parents=True)
                os.symlink(SOURCE, link)
                self.link(project)
                same = os.path.normcase(os.path.realpath(link)) == os.path.normcase(os.path.realpath(SOURCE))   # on Windows readlink may carry a long-path prefix
                self.assertTrue(link.is_symlink() and same, f'claude: {value!r}')

    def test_a_copy_equal_to_the_library_but_tracked_by_git_stays_without_the_key(self):
        project = self.project(claude={'output_styles': False})
        self.init_git(project)
        target = self.put_style(project, SOURCE.read_text(encoding='utf-8'))
        git(project, 'add', '-f', DEST)
        git(project, 'commit', '-q', '-m', 'styl')
        self.link(project)
        self.assertEqual(target.read_bytes(), SOURCE.read_bytes())

    def test_an_untracked_copy_equal_to_the_library_without_state_is_removed_without_the_key(self):
        project = self.project(claude={'output_styles': False})
        self.put_style(project, SOURCE.read_text(encoding='utf-8'))
        self.link(project)
        self.assertFalse(self.dest(project).exists())

    def test_a_foreign_file_stays_when_we_never_installed_and_the_key_is_off_without_a_word_about_it(self):
        project = self.project(claude={'output_styles': False})
        target = self.put_style(project)
        result = self.link(project)
        self.assertEqual(target.read_bytes(), FOREIGN_STYLE.encode('utf-8'))
        self.assertEqual(self.style_lines(result), [], 'nothing is opted in: no warning about a file we have nothing to do with')

    def test_other_styles_stay_when_ours_is_removed(self):
        project = self.installed()
        other = self.put_style(project, '---\nname: moj\ndescription: moj\n---\nTreść.\n', name='moj.md')
        before = other.read_bytes()
        self.set_claude(project, {'output_styles': False})
        self.link(project)
        self.assertEqual(other.read_bytes(), before)
        self.assertFalse(self.dest(project).exists())

    def test_switching_off_twice_and_on_again_is_stable(self):
        project = self.installed()
        self.set_claude(project, {'output_styles': False})
        self.link(project)
        again = self.link(project)
        self.assertEqual(self.warns(again), [])
        self.assertFalse(self.dest(project).exists())
        self.set_claude(project, {'output_styles': True})
        self.link(project)
        self.assert_copy_of_library(project, 'after switching on again')

    def test_the_settings_of_the_user_survive_the_way_back(self):
        project = self.project()
        self.write_settings(project, {'outputStyle': 'karta', 'env': {'A': '1'}})
        self.link(project)
        self.set_claude(project, False)
        self.link(project)
        data = self.settings(project)
        self.assertEqual((data['outputStyle'], data['env']), ('karta', {'A': '1'}))


class IndependenceFromTheStatusLine(LinkBase):
    def bar_files(self, project):
        data = self.settings(project) if descend(project, LOCAL).is_file() else {}
        return 'statusLine' in data, descend(project, '.claude/hooks/statusline.py').exists()

    def test_the_bar_alone_installs_no_style(self):
        project = self.project(claude={'statusline': True})
        self.link(project)
        self.assert_no_style(project)
        self.assertEqual(self.bar_files(project), (True, True), 'the bar itself is installed')

    def test_the_style_alone_installs_no_bar(self):
        project = self.project(claude={'output_styles': True})
        self.link(project)
        self.assert_copy_of_library(project)
        self.assertEqual(self.bar_files(project), (False, False))

    def test_both_keys_install_both(self):
        project = self.project(claude={'statusline': True, 'output_styles': True})
        self.link(project)
        self.assert_copy_of_library(project)
        self.assertEqual(self.bar_files(project), (True, True))
        self.assert_no_output_style_setting(project)

    def test_switching_the_bar_off_leaves_the_style_and_the_other_way_round(self):
        project = self.project(claude={'statusline': True, 'output_styles': True})
        self.link(project)
        self.set_claude(project, {'statusline': False, 'output_styles': True})
        self.link(project)
        self.assert_copy_of_library(project, 'after the bar went off')
        self.assertEqual(self.bar_files(project), (False, False))
        self.set_claude(project, {'statusline': True, 'output_styles': True})
        self.link(project)
        self.set_claude(project, {'statusline': True, 'output_styles': False})
        self.link(project)
        self.assertFalse(self.dest(project).exists())
        self.assertEqual(self.bar_files(project), (True, True), 'the bar stays when the style goes off')

    def test_a_foreign_bar_or_disable_all_hooks_does_not_stop_the_style(self):
        self.write_user_settings({'statusLine': FOREIGN_BAR, 'disableAllHooks': True})
        project = self.project(claude={'statusline': True, 'output_styles': True})
        self.link(project)
        self.assert_copy_of_library(project)
        self.assertFalse(self.bar_files(project)[0], 'the foreign bar is respected: no entry of ours')

    def test_a_foreign_file_of_the_style_does_not_stop_the_bar(self):
        project = self.project(claude={'statusline': True, 'output_styles': True})
        self.put_style(project)
        self.link(project)
        self.assertEqual(self.bar_files(project), (True, True))
        self.assertEqual(self.dest(project).read_bytes(), FOREIGN_STYLE.encode('utf-8'))


class NothingChangesWithoutTheKeys(LinkBase):
    def claude_tree(self, project):
        tree = tree_snapshot(descend(project, '.claude'), mtimes=False)
        return {rel: value for rel, value in tree.items() if value[0] != 'dir' or rel.startswith('output-styles')}

    def test_the_same_tree_with_no_key_with_false_keys_and_with_an_empty_section(self):
        trees = []
        for claude in (None, {'output_styles': False, 'statusline': False}, {}):
            project = self.project(bare=claude is None, claude=claude)
            self.link(project)
            self.assert_no_style(project)
            self.assertFalse(descend(project, '.claude/output-styles').exists(), 'not even the directory')
            trees.append(self.claude_tree(project))
        self.assertEqual(trees[0], trees[1])
        self.assertEqual(trees[0], trees[2])

    def test_the_bar_alone_leaves_no_trace_of_styles(self):
        project = self.project(claude={'statusline': True})
        self.link(project)
        self.assertFalse(descend(project, '.claude/output-styles').exists())

    def test_no_output_style_is_written_to_the_settings(self):
        project = self.project(bare=True)
        self.link(project)
        self.assert_no_output_style_setting(project)

    def test_a_foreign_file_of_that_name_is_not_even_mentioned_without_the_keys(self):
        project = self.project(bare=True)
        target = self.put_style(project)
        stamp = (target.read_bytes(), target.stat().st_mtime_ns)
        result = self.link(project)
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), stamp)
        self.assertEqual(self.style_lines(result), [])
        self.assertFalse(any('output-styles/karta.md' in line for line in self.gitignore_lines(project)))

    def test_doctor_adds_nothing_about_styles_without_the_keys(self):
        project = self.project(bare=True)
        self.link(project)
        self.assertEqual([f for f in self.doctor(project) if doctor_mentions_style(f)], [])

    def test_doctor_adds_nothing_about_styles_when_the_key_is_false(self):
        project = self.project(claude={'output_styles': False})
        self.link(project)
        self.assertEqual([f for f in self.doctor(project) if doctor_mentions_style(f)], [])


# --------------------------------------------------------------------------------------------------------------
# 4. the proof that the negative assertions can fail
# --------------------------------------------------------------------------------------------------------------
class HarnessSelfChecks(LinkBase):
    def test_a_planted_file_is_refused_by_assert_no_style(self):
        project = self.project()
        self.put_style(project)
        with self.assertRaises(AssertionError):
            self.assert_no_style(project)

    def test_a_planted_gitignore_line_is_refused_by_assert_no_style(self):
        project = self.project()
        target = descend(project, GITIGNORE)
        target.parent.mkdir(parents=True)
        target.write_text('output-styles/karta.md\n', encoding='utf-8')
        with self.assertRaises(AssertionError):
            self.assert_no_style(project)

    @needs_os_symlinks
    def test_a_planted_symlink_is_refused_by_both_helpers(self):
        project = self.project()
        outside = child(self.work, 'cel.md')
        outside.write_bytes(b'reference\n')
        link = self.dest(project)
        link.parent.mkdir(parents=True)
        os.symlink(outside, link)
        with self.assertRaises(AssertionError):
            self.assert_copy_of_library(project, expected=b'reference\n')
        with self.assertRaises(AssertionError):
            self.assert_no_style(project)

    def test_a_copy_equal_to_the_reference_is_accepted_and_an_edited_one_is_refused(self):
        project = self.project()
        target = self.put_style(project, 'reference\n')
        self.assert_copy_of_library(project, expected=b'reference\n')
        target.write_text('reference\nedit\n', encoding='utf-8', newline='\n')
        with self.assertRaises(AssertionError):
            self.assert_copy_of_library(project, expected=b'reference\n')

    def test_a_finding_about_the_style_is_recognised(self):
        self.assertTrue(doctor_mentions_style({'path': '.claude/output-styles/karta.md', 'reason': 'x'}))
        self.assertTrue(doctor_mentions_style({'path': 'x', 'reason': 'Available output styles: karta'}))
        self.assertFalse(doctor_mentions_style({'path': 'x', 'reason': 'the status line'}))


if __name__ == '__main__':
    unittest.main()
